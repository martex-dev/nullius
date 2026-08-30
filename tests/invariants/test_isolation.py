"""The sandbox denies what an experiment has no business doing.

Every test here spawns a real subprocess, installs the real guard, and
attempts the real operation. Asserting against a mocked hook would prove
nothing about the thing that ships.

What this suite does **not** claim, and what ADR-0002 says in the same words:
this is not a security boundary. A determined adversary with code execution
defeats an in-process audit hook. It is defence in depth against the failure
that actually happens — an experiment that reaches for the network or writes
somewhere it shouldn't — and its real value is that such an attempt becomes
*visible* rather than silent.
"""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from nullius.build.compiler import compile_spec
from nullius.db.enums import RunStatus
from nullius.execute.sandbox import SandboxLimits, SubprocessSandbox
from tests.test_execution import SPEC

pytestmark = pytest.mark.isolation


def _attempt(code: str, workdir: Path) -> subprocess.CompletedProcess[str]:
    """Run ``code`` in a child with the guard installed, and report what happened."""
    workdir.mkdir(parents=True, exist_ok=True)
    script = textwrap.dedent(f"""
        from pathlib import Path
        from nullius.execute.guard import GuardViolation, install_guard, violations

        install_guard(Path({str(workdir)!r}))
        try:
{textwrap.indent(textwrap.dedent(code), " " * 12)}
        except GuardViolation as exc:
            print("DENIED:", exc)
            raise SystemExit(0)
        print("ALLOWED")
        raise SystemExit(1)
    """)
    return subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def test_opening_a_socket_is_denied(tmp_path: Path) -> None:
    result = _attempt(
        """
        import socket
        socket.create_connection(("example.com", 80), timeout=1)
        """,
        tmp_path / "w",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "DENIED" in result.stdout
    assert "network" in result.stdout


def test_resolving_a_hostname_is_denied(tmp_path: Path) -> None:
    """Blocked before the connection, so a lookup cannot leak a name either."""
    result = _attempt(
        """
        import socket
        socket.gethostbyname("example.com")
        """,
        tmp_path / "w",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "DENIED" in result.stdout


def test_spawning_a_subprocess_is_denied(tmp_path: Path) -> None:
    result = _attempt(
        """
        import subprocess
        subprocess.run(["echo", "hello"], check=False)
        """,
        tmp_path / "w",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "DENIED" in result.stdout


def test_writing_outside_the_workdir_is_denied(tmp_path: Path) -> None:
    workdir = tmp_path / "w"
    outside = tmp_path / "escaped.txt"
    result = _attempt(
        f"""
        with open({str(outside)!r}, "w") as handle:
            handle.write("should never be written")
        """,
        workdir,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "DENIED" in result.stdout
    assert "outside the workdir" in result.stdout
    assert not outside.exists(), "the write must not have happened"


def test_writing_inside_the_workdir_is_allowed(tmp_path: Path) -> None:
    """The guard must not be so blunt that a legitimate experiment cannot run."""
    workdir = tmp_path / "w"
    workdir.mkdir(parents=True)
    result = _attempt(
        f"""
        with open({str(workdir / "out.txt")!r}, "w") as handle:
            handle.write("expected")
        """,
        workdir,
    )
    assert result.returncode == 1, result.stdout + result.stderr
    assert "ALLOWED" in result.stdout
    assert (workdir / "out.txt").read_text() == "expected"


def test_reading_outside_the_workdir_is_allowed(tmp_path: Path) -> None:
    """Reads are not restricted — the guard bounds writes and egress.

    Confidentiality of the evaluation sample does not depend on this: the
    holdout is drawn by the Custodian from a seed the plan never contains, so
    there is no file here to read.
    """
    source = tmp_path / "readable.txt"
    source.write_text("data")
    result = _attempt(f"""print(open({str(source)!r}).read())""", tmp_path / "w")
    assert result.returncode == 1
    assert "ALLOWED" in result.stdout


def test_every_denial_is_recorded(tmp_path: Path) -> None:
    """Visibility is the point: a blocked attempt must leave a trace."""
    result = _attempt(
        """
        import socket
        try:
            socket.gethostbyname("example.com")
        except Exception:
            pass
        print("VIOLATIONS:", violations())
        raise GuardViolation("done")
        """,
        tmp_path / "w",
    )
    assert "VIOLATIONS:" in result.stdout
    assert "socket.gethostbyname" in result.stdout


# ---------------------------------------------------------------------------
# The sandbox around the guard
# ---------------------------------------------------------------------------


def test_a_failing_plan_is_a_scientific_failure_not_a_crash(tmp_path: Path) -> None:
    """A failed experiment is evidence about the design, not an error to retry."""
    plan = compile_spec(SPEC, seed=SPEC.seeds()[0])
    plan["dataset"]["generator"] = "no_such_generator"

    result = SubprocessSandbox().run(plan, tmp_path / "w", SandboxLimits(wall_seconds=60))

    assert not result.ok
    assert RunStatus(result.status) is RunStatus.SCIENTIFIC_FAILURE
    error = result.error()
    assert error is not None
    assert "no_such_generator" in error["message"]


def test_a_run_that_exceeds_its_wall_clock_is_killed(tmp_path: Path) -> None:
    plan = compile_spec(SPEC, seed=SPEC.seeds()[0])
    result = SubprocessSandbox().run(plan, tmp_path / "w", SandboxLimits(wall_seconds=0.01))

    assert result.timed_out
    assert RunStatus(result.status) is RunStatus.TIMEOUT
    assert not result.ok


def test_the_child_gets_no_ambient_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An API key in the parent's environment must not reach an experiment."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-should-never-be-visible")

    plan = compile_spec(SPEC, seed=SPEC.seeds()[0])
    plan["dataset"]["generator"] = "no_such_generator"  # fail fast; we want the env, not a run
    result = SubprocessSandbox().run(plan, tmp_path / "w", SandboxLimits(wall_seconds=60))

    combined = result.stdout + result.stderr + json.dumps(result.error() or {})
    assert "sk-should-never-be-visible" not in combined
