"""The environment manifest.

``environment_hash`` answers "would this run today be the same run?" It covers
the interpreter, the platform, the versions of every library whose numerics
could move a metric, the isolation tier, and the plan itself.

Deliberately *not* covered: wall-clock time, hostname, working directory,
process id. Including them would make every hash unique, which is the same as
having no hash — the manifest exists to be *comparable*, so it holds only the
things whose change could change a result.
"""

from __future__ import annotations

import platform
import sys
from importlib.metadata import PackageNotFoundError, version
from typing import Any

from nullius.util.canonical import sha256_of

__all__ = ["NUMERIC_DEPENDENCIES", "environment_manifest"]

NUMERIC_DEPENDENCIES: tuple[str, ...] = ("numpy", "scipy", "scikit-learn", "pandas")
"""Libraries whose version can move a metric. A change here is a change of result."""


def _versions() -> dict[str, str]:
    out: dict[str, str] = {}
    for name in NUMERIC_DEPENDENCIES:
        try:
            out[name] = version(name)
        except PackageNotFoundError:  # pragma: no cover - all are hard dependencies
            out[name] = "absent"
    return out


def environment_manifest(*, plan: dict[str, Any], isolation_tier: str) -> dict[str, Any]:
    """A complete, comparable description of the conditions of a run."""
    return {
        "python": sys.version.split()[0],
        "implementation": platform.python_implementation(),
        "platform": platform.system(),
        "machine": platform.machine(),
        "isolation_tier": isolation_tier,
        "packages": _versions(),
        "plan_hash": sha256_of(plan),
        # Pinned to one thread in the child: multi-threaded BLAS reductions sum
        # in nondeterministic order, so this is part of what makes a rerun
        # comparable rather than merely similar.
        "blas_threads": 1,
    }


def environment_hash(*, plan: dict[str, Any], isolation_tier: str) -> str:
    """Content address of the manifest. Stored on every run."""
    return sha256_of(environment_manifest(plan=plan, isolation_tier=isolation_tier))
