"""The in-process guard installed before an experiment runs.

A Python audit hook (PEP 578) that denies the things an experiment has no
business doing: opening sockets, spawning processes, and writing outside its
own working directory.

**This is not a security boundary.** A determined adversary with code
execution can defeat an in-process hook, and ADR-0002 says so in those words.
It is defence in depth against the failure that actually happens — an
experiment that accidentally reaches for the network, shells out, or writes
where it should not — and it is what makes those attempts *visible* rather
than silent, because every denial is recorded.

Two things this deliberately does not rely on:

- It does not protect the holdout split. That guarantee comes from the
  Custodian's process boundary: the test partition is never in this process's
  filesystem view at all, so there is nothing here to reach for.
- It does not replace ``DockerSandbox``. Code generation (M12) is gated in
  code on the container backend being active.

Audit hooks cannot be removed once installed, which is the one property that
makes this worth having at all.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

__all__ = ["GuardViolation", "install_guard", "violations"]

#: Audit events refused outright. Each is something no compiled experiment does.
FORBIDDEN_EVENTS: frozenset[str] = frozenset(
    {
        "socket.connect",
        "socket.bind",
        "socket.getaddrinfo",
        "socket.gethostbyname",
        "subprocess.Popen",
        "os.system",
        "os.exec",
        "os.posix_spawn",
        "os.fork",
        "os.forkpty",
        "ftplib.connect",
        "imaplib.open",
        "smtplib.connect",
        "urllib.Request",
        "webbrowser.open",
        "ctypes.dlopen",
        "ctypes.dlsym",
        "ctypes.call_function",
    }
)

_WRITE_MODES = frozenset({"w", "a", "x", "+"})

_violations: list[str] = []


class GuardViolation(RuntimeError):
    """An experiment attempted a denied operation."""


def violations() -> list[str]:
    """Everything the guard refused, in order. Harvested into the run record."""
    return list(_violations)


def _is_write(mode: object) -> bool:
    return isinstance(mode, str) and any(flag in mode for flag in _WRITE_MODES)


def install_guard(workdir: Path) -> None:
    """Install the audit hook. Call before importing or running any plan.

    Resolved once, up front: resolving paths inside the hook would itself
    trigger filesystem audit events and recurse.
    """
    allowed_root = workdir.resolve()

    def hook(event: str, args: tuple[Any, ...]) -> None:
        if event in FORBIDDEN_EVENTS:
            message = f"denied {event}"
            _violations.append(message)
            raise GuardViolation(
                f"{message}: an experiment may not use the network, spawn processes, "
                "or load native libraries"
            )

        if event == "open" and len(args) >= 2 and _is_write(args[1]):
            target = args[0]
            if not isinstance(target, (str, bytes, int)):
                return
            if isinstance(target, int):
                return  # already-open descriptor; the path check happened earlier
            path = Path(os.fsdecode(target))
            try:
                resolved = path if path.is_absolute() else Path.cwd() / path
                resolved = Path(os.path.normpath(resolved))
            except (OSError, ValueError):  # pragma: no cover - defensive
                return
            if allowed_root not in resolved.parents and resolved != allowed_root:
                message = f"denied write outside the workdir: {resolved}"
                _violations.append(message)
                raise GuardViolation(f"{message}. An experiment writes only under {allowed_root}.")

    sys.addaudithook(hook)
