"""M0 smoke tests: the capability probe and the CLI it feeds."""

from __future__ import annotations

import dataclasses

import pytest
from typer.testing import CliRunner

from nullius import __version__
from nullius.cli import app
from nullius.environment import Capabilities, IsolationTier, VisibilityTier, detect

runner = CliRunner()


def test_detect_returns_a_complete_snapshot() -> None:
    caps = detect()
    assert caps.python_version.startswith("3.")
    assert caps.cpu_count >= 1
    assert caps.isolation_tier in set(IsolationTier)
    assert caps.visibility_tier in set(VisibilityTier)


def test_digest_is_stable_and_sensitive() -> None:
    caps = detect()
    assert caps.digest() == detect().digest()

    weaker = dataclasses.replace(caps, isolation_tier=IsolationTier.NONE)
    assert weaker.digest() != caps.digest(), "the tier must change the provenance digest"


@pytest.mark.parametrize(
    ("tier", "expected_fragment"),
    [
        (IsolationTier.SUBPROCESS, "not a security boundary"),
        (IsolationTier.DOCKER, None),
    ],
)
def test_weak_isolation_always_warns(tier: IsolationTier, expected_fragment: str | None) -> None:
    caps = dataclasses.replace(
        detect(), isolation_tier=tier, docker_version="docker version 99", live_provider="anthropic"
    )
    joined = " ".join(caps.warnings)
    if expected_fragment is None:
        assert "security boundary" not in joined
    else:
        assert expected_fragment in joined


def test_capabilities_are_frozen() -> None:
    caps = detect()
    with pytest.raises(dataclasses.FrozenInstanceError):
        caps.cpu_count = 999  # type: ignore[misc]


def test_version_command() -> None:
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert __version__ in result.stdout


def test_doctor_reports_tiers() -> None:
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0
    assert "isolation tier" in result.stdout
    assert "visibility tier" in result.stdout


def test_doctor_surfaces_warnings_rather_than_hiding_them() -> None:
    """A weak tier must never be silent: the whole point of ADR-0002."""
    caps = Capabilities(
        python_version="3.12.0",
        platform="Test",
        machine="x86_64",
        cpu_count=1,
        isolation_tier=IsolationTier.SUBPROCESS,
        visibility_tier=VisibilityTier.APPLICATION,
        docker_version=None,
        postgres_available=False,
        live_provider=None,
        git_commit=None,
    )
    assert len(caps.warnings) == 3
