"""Regression test for Task 1 (client-reporting-hardening): before this fix, pyproject.toml
declared version 1.4.0 while src/nse_scanner/version.py separately hard-coded __version__ =
"1.3.1" — two independent, drifting sources of truth. A built package could therefore be 1.4.0
while every run manifest it produced claimed software_version=1.3.1 (a real provenance bug, not
cosmetic: run_manifest.py's `software_version` field is exactly what Task 13/Task 14's
reproducibility guarantee depends on).

This test proves there is now exactly ONE authoritative version source (pyproject.toml), which
nse_scanner.version.__version__ reads back via installed package metadata rather than
re-declaring — so the two are structurally incapable of disagreeing, not just coincidentally
equal today.
"""

from __future__ import annotations

import tomllib
from importlib.metadata import version as installed_version
from pathlib import Path

import pytest
from nse_scanner.reporting.run_manifest import RunManifest
from nse_scanner.version import __version__

REPO_ROOT = Path(__file__).resolve().parents[2]


def _pyproject_version() -> str:
    pyproject = REPO_ROOT / "pyproject.toml"
    with open(pyproject, "rb") as f:
        data = tomllib.load(f)
    return data["project"]["version"]


def test_version_matches_pyproject_declared_version() -> None:
    """The single authoritative source (pyproject.toml [project] version) must match what the
    package/runtime actually reports."""
    assert __version__ == _pyproject_version()


def test_version_matches_installed_package_metadata() -> None:
    """__version__ is DERIVED from installed package metadata, not a second hard-coded literal —
    confirm that derivation actually round-trips through importlib.metadata as designed, not by
    coincidence of two literals happening to agree."""
    assert __version__ == installed_version("nse-scanner")


def test_version_is_not_the_old_drifted_literal() -> None:
    """Guards specifically against reintroducing the exact historical drift this task fixed."""
    assert __version__ != "1.3.1" or _pyproject_version() == "1.3.1"


def test_run_manifest_default_software_version_matches_package_version() -> None:
    """RunManifest.software_version defaults from nse_scanner.version.__version__ (see
    reporting/run_manifest.py) — confirm that default is wired to the same single source, so a
    manifest written by this exact tested commit always records the true package version
    (Task 1's 'ensure the final real run manifest records the exact software version of the
    tested artifact', and Task 11.11)."""
    default_software_version = RunManifest.__dataclass_fields__["software_version"].default
    assert default_software_version == __version__
    assert default_software_version == _pyproject_version()


def test_version_import_fails_fast_when_package_metadata_unavailable(monkeypatch) -> None:
    """Review-round-1 fix: resolving __version__ must raise loudly and immediately if package
    metadata can't be found -- never silently fall back to a placeholder like
    "0.0.0+unknown-not-installed" that could end up in a client-facing run_manifest.json. This
    simulates "imported without being properly installed" by monkeypatching
    importlib.metadata.version to raise PackageNotFoundError, then reloading the module (which
    re-executes its top-level __version__ = ... assignment)."""
    import importlib
    from importlib.metadata import PackageNotFoundError

    import nse_scanner.version as version_module

    def _raise_not_found(name: str) -> str:
        raise PackageNotFoundError(name)

    monkeypatch.setattr("importlib.metadata.version", _raise_not_found)
    try:
        with pytest.raises(RuntimeError, match="could not resolve installed package metadata"):
            importlib.reload(version_module)
    finally:
        # Restore the real importlib.metadata.version BEFORE reloading again, so the module (and
        # BASELINE_STRATEGY_ID, __version__ used by every other test in this process) is put back
        # to its correct, real state regardless of pass/fail above.
        monkeypatch.undo()
        importlib.reload(version_module)

    assert version_module.__version__ == __version__
    assert version_module.BASELINE_STRATEGY_ID == "bb_ha_v1_base"
