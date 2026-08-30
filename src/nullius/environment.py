"""Capability probing.

Nullius runs at different *enforcement tiers* depending on what the host
provides: whether row-level security is available to isolate the Replicator,
whether a container runtime is available to isolate execution, whether a live
model provider is reachable.

A claim produced under a weaker tier must be identifiable as such forever, so
this module produces a structured, hashable record that is stored in the
provenance of every run. See ADR-0001 and ADR-0002.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass
from enum import StrEnum
from importlib.util import find_spec


class IsolationTier(StrEnum):
    """How strongly experiment execution is isolated from the host."""

    DOCKER = "docker"
    """Container with no network, read-only rootfs, dropped capabilities."""

    SUBPROCESS = "subprocess"
    """Separate process with an audit hook and resource caps. Defence in depth
    against accident, *not* a boundary against a determined adversary."""

    NONE = "none"
    """In-process. Test fixtures only; refused for real runs."""


class VisibilityTier(StrEnum):
    """How role-scoped information asymmetry (e.g. Replicator blindness) is enforced."""

    DATABASE = "database"
    """Postgres row-level security: the database refuses."""

    APPLICATION = "application"
    """Role-scoped repository layer plus a query audit log: our code refuses."""


@dataclass(frozen=True, slots=True)
class Capabilities:
    """A hashable snapshot of what this host can enforce."""

    python_version: str
    platform: str
    machine: str
    cpu_count: int
    isolation_tier: IsolationTier
    visibility_tier: VisibilityTier
    docker_version: str | None
    postgres_available: bool
    live_provider: str | None
    git_commit: str | None

    def digest(self) -> str:
        """Stable hash, suitable for storing alongside a run's provenance."""
        payload = json.dumps(asdict(self), sort_keys=True, default=str)
        return hashlib.sha256(payload.encode()).hexdigest()

    @property
    def warnings(self) -> list[str]:
        """Statements that must be surfaced rather than discovered later."""
        out: list[str] = []
        if self.isolation_tier is IsolationTier.SUBPROCESS:
            out.append(
                "Isolation tier is 'subprocess'. This is defence in depth against "
                "accidental misbehaviour, not a security boundary. Do not execute "
                "untrusted code. Code generation (M12) requires the docker tier."
            )
        if self.visibility_tier is VisibilityTier.APPLICATION:
            out.append(
                "Visibility tier is 'application'. Replicator blindness is enforced "
                "by the repository layer and proven by the query audit log, not by "
                "the database refusing. See ADR-0001."
            )
        if self.live_provider is None:
            out.append(
                "No live model provider configured. Mock and replay providers are "
                "available; a live run needs ANTHROPIC_API_KEY."
            )
        return out


def _tool_version(executable: str, *args: str) -> str | None:
    path = shutil.which(executable)
    if path is None:
        return None
    try:
        completed = subprocess.run(  # noqa: S603 - fixed argv, resolved absolute path
            [path, *args],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout.strip() or None


def _git_commit() -> str | None:
    return _tool_version("git", "rev-parse", "HEAD")


def detect_live_provider() -> str | None:
    """Return the name of a usable live model provider, or ``None``.

    Presence of a key is necessary but not sufficient: the SDK must be
    installed too, so that a misconfiguration surfaces here rather than
    halfway through a research program.
    """
    if os.environ.get("ANTHROPIC_API_KEY") and find_spec("anthropic") is not None:
        return "anthropic"
    return None


def detect() -> Capabilities:
    """Probe the host. Cheap enough to call on every CLI invocation."""
    docker_version = _tool_version("docker", "--version")
    postgres_available = find_spec("psycopg") is not None

    return Capabilities(
        python_version=sys.version.split()[0],
        platform=f"{platform.system()} {platform.release()}",
        machine=platform.machine(),
        cpu_count=os.cpu_count() or 1,
        isolation_tier=(IsolationTier.DOCKER if docker_version else IsolationTier.SUBPROCESS),
        visibility_tier=(
            VisibilityTier.DATABASE if postgres_available else VisibilityTier.APPLICATION
        ),
        docker_version=docker_version,
        postgres_available=postgres_available,
        live_provider=detect_live_provider(),
        git_commit=_git_commit(),
    )
