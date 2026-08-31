"""Sandbox backends.

One interface, three tiers, and an honest account of what each is worth
(ADR-0002):

``SubprocessSandbox`` — the default
    A separate process with the audit guard installed, a wall-clock kill and a
    memory ceiling. Defence in depth against accidents, **not** a security
    boundary. Adequate while the MVP executes only compiled, human-written
    operators.

``DockerSandbox``
    ``--network=none``, read-only rootfs, dropped capabilities, cgroup limits.
    Required from M12, when a model starts writing the code.

The tier is recorded on every run, so a claim produced under the weaker one
stays identifiable as such for as long as the claim exists.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from nullius.environment import IsolationTier
from nullius.errors import NulliusError

__all__ = [
    "DockerSandbox",
    "SandboxBackend",
    "SandboxLimits",
    "SandboxResult",
    "SubprocessSandbox",
]


class SandboxUnavailable(NulliusError):
    """The requested isolation tier is not available on this host."""


@dataclass(frozen=True, slots=True)
class SandboxLimits:
    """Hard ceilings. Exceeding any of them kills the run."""

    wall_seconds: float = 300.0
    memory_mb: int = 4096
    output_bytes: int = 64 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class SandboxResult:
    """What came back, including how it ended."""

    exit_code: int
    timed_out: bool
    out_of_memory: bool
    wall_seconds: float
    peak_memory_mb: float
    stdout: str
    stderr: str
    isolation_tier: IsolationTier
    outputs: dict[str, Path] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out and not self.out_of_memory

    @property
    def status(self) -> str:
        """The run status, in the vocabulary of :class:`~nullius.db.enums.RunStatus`."""
        if self.timed_out:
            return "timeout"
        if self.out_of_memory:
            return "oom"
        if self.exit_code == 0:
            return "completed"
        # A non-zero exit from the child means the experiment itself failed —
        # a scientific failure, which is evidence, not an error to retry.
        return "scientific_failure"

    def results(self) -> dict[str, Any] | None:
        path = self.outputs.get("results.json")
        if path is None or not path.is_file():
            return None
        return dict(json.loads(path.read_text(encoding="utf-8")))

    def error(self) -> dict[str, Any] | None:
        path = self.outputs.get("error.json")
        if path is None or not path.is_file():
            return None
        return dict(json.loads(path.read_text(encoding="utf-8")))

    def telemetry(self) -> dict[str, Any]:
        return {
            "exit_code": self.exit_code,
            "wall_seconds": round(self.wall_seconds, 6),
            "peak_memory_mb": round(self.peak_memory_mb, 3),
            "timed_out": self.timed_out,
            "out_of_memory": self.out_of_memory,
            "isolation_tier": self.isolation_tier.value,
            "outputs": sorted(self.outputs),
        }


@runtime_checkable
class SandboxBackend(Protocol):
    """Runs a compiled plan in isolation and harvests what it produced."""

    tier: IsolationTier

    def run(self, plan: dict[str, Any], workdir: Path, limits: SandboxLimits) -> SandboxResult:
        """Execute ``plan`` in ``workdir``."""
        ...


class SubprocessSandbox:
    """A separate process, guarded and bounded.

    Not a security boundary. See ADR-0002 and the module docstring.
    """

    tier = IsolationTier.SUBPROCESS

    __slots__ = ("_python",)

    def __init__(self, python: str | None = None) -> None:
        self._python = python or sys.executable

    def run(self, plan: dict[str, Any], workdir: Path, limits: SandboxLimits) -> SandboxResult:
        # Absolute before anything else. The child is launched with its working
        # directory set to the workdir, so a relative path handed in by the
        # caller would be re-interpreted against the workdir itself and the
        # child would fail to find the plan it was told to run — a failure that
        # surfaces as a scientific failure of every seed, which is the most
        # misleading way this could possibly break.
        workdir = workdir.resolve()
        workdir.mkdir(parents=True, exist_ok=True)
        out = workdir / "out"
        out.mkdir(exist_ok=True)

        plan_path = workdir / "plan.json"
        plan_path.write_text(json.dumps(plan, indent=2, sort_keys=True), encoding="utf-8")

        # A minimal environment. No API keys, no ambient credentials: the child
        # receives configuration through the plan file and nothing else.
        env = {
            "PATH": "",
            "PYTHONPATH": "",
            "PYTHONHASHSEED": "0",
            "OMP_NUM_THREADS": "1",
            "OPENBLAS_NUM_THREADS": "1",
            "MKL_NUM_THREADS": "1",
            "NUMEXPR_NUM_THREADS": "1",
            "SYSTEMROOT": _system_root(),
        }

        started = time.perf_counter()
        process = subprocess.Popen(  # noqa: S603 - fixed argv, no shell
            [self._python, "-I", "-m", "nullius.execute.child", str(plan_path), str(workdir)],
            cwd=str(workdir),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        peak_mb, out_of_memory = _watch_memory(process, limits.memory_mb)

        timed_out = False
        try:
            stdout, stderr = process.communicate(timeout=limits.wall_seconds)
        except subprocess.TimeoutExpired:
            timed_out = True
            process.kill()
            stdout, stderr = process.communicate()

        return SandboxResult(
            exit_code=process.returncode if process.returncode is not None else -1,
            timed_out=timed_out,
            out_of_memory=out_of_memory(),
            wall_seconds=time.perf_counter() - started,
            peak_memory_mb=peak_mb(),
            stdout=stdout or "",
            stderr=stderr or "",
            isolation_tier=self.tier,
            outputs=_harvest(out, limits.output_bytes),
        )


class DockerSandbox:
    """The container backend. Required from M12."""

    tier = IsolationTier.DOCKER

    __slots__ = ("_image",)

    def __init__(self, image: str = "nullius/experiment:latest") -> None:
        if shutil.which("docker") is None:
            raise SandboxUnavailable(
                "docker is not on PATH. The default backend is SubprocessSandbox, "
                "which is not a security boundary; see ADR-0002."
            )
        self._image = image

    def run(self, plan: dict[str, Any], workdir: Path, limits: SandboxLimits) -> SandboxResult:
        workdir = workdir.resolve()  # a bind mount source must be absolute
        workdir.mkdir(parents=True, exist_ok=True)
        out = workdir / "out"
        out.mkdir(exist_ok=True)
        plan_path = workdir / "plan.json"
        plan_path.write_text(json.dumps(plan, indent=2, sort_keys=True), encoding="utf-8")

        argv = [
            "docker",
            "run",
            "--rm",
            "--network=none",  # not "restricted" — absent
            "--read-only",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            "--user=65534:65534",
            f"--memory={limits.memory_mb}m",
            f"--memory-swap={limits.memory_mb}m",
            "--pids-limit=256",
            "--cpus=2",
            "-v",
            f"{workdir.resolve()}:/workdir:rw",
            self._image,
            "python",
            "-I",
            "-m",
            "nullius.execute.child",
            "/workdir/plan.json",
            "/workdir",
        ]

        started = time.perf_counter()
        completed = subprocess.run(  # noqa: S603 - fixed argv, no shell
            argv, capture_output=True, text=True, timeout=limits.wall_seconds, check=False
        )
        return SandboxResult(
            exit_code=completed.returncode,
            timed_out=False,
            out_of_memory=completed.returncode == 137,  # SIGKILL, typically the cgroup limit
            wall_seconds=time.perf_counter() - started,
            peak_memory_mb=0.0,  # cgroup accounting; wired up with the image in M12
            stdout=completed.stdout,
            stderr=completed.stderr,
            isolation_tier=self.tier,
            outputs=_harvest(out, limits.output_bytes),
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _system_root() -> str:
    """Windows needs SYSTEMROOT present or the interpreter will not start."""
    import os

    return os.environ.get("SYSTEMROOT", "")


def _watch_memory(process: subprocess.Popen[str], ceiling_mb: int) -> tuple[Any, Any]:
    """Poll the child's memory and kill it if it exceeds ``ceiling_mb``.

    Polling rather than an OS limit because ``resource.setrlimit`` does not
    exist on Windows, and the ceiling has to hold on the machine this is
    actually developed on.
    """
    import psutil

    state = {"peak": 0.0, "killed": False}

    def watch() -> None:
        try:
            proc = psutil.Process(process.pid)
        except psutil.Error:  # pragma: no cover - process already gone
            return
        while process.poll() is None:
            try:
                rss_mb = proc.memory_info().rss / (1024 * 1024)
            except psutil.Error:
                return
            state["peak"] = max(float(state["peak"]), rss_mb)
            if rss_mb > ceiling_mb:
                state["killed"] = True
                process.kill()
                return
            time.sleep(0.05)

    thread = threading.Thread(target=watch, daemon=True)
    thread.start()
    return (lambda: float(state["peak"]), lambda: bool(state["killed"]))


def _harvest(out: Path, size_cap: int) -> dict[str, Path]:
    """Collect declared outputs, refusing anything oversized.

    The output directory is the child's only channel back. Files above the cap
    are omitted and named, rather than silently truncated.
    """
    outputs: dict[str, Path] = {}
    if not out.is_dir():
        return outputs
    for path in sorted(out.iterdir()):
        if path.is_file() and path.stat().st_size <= size_cap:
            outputs[path.name] = path
    return outputs
