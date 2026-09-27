"""Version identifiers.

The application/system version and the strategy version are versioned independently
(PART 82 of the spec) — a system release can ship without touching the strategy definition,
and a new strategy variant does not require a system version bump.

**Client-reporting-hardening fix (Task 1):** `__version__` used to be a second, hand-maintained
copy of the version already declared in `pyproject.toml`'s `[project] version`. The two drifted
(pyproject.toml said 1.4.0, this file said 1.3.1), so a wheel built as 1.4.0 could write a run
manifest claiming software_version=1.3.1 — a real client-facing provenance bug, not a cosmetic
one. There is now exactly ONE authoritative version: `pyproject.toml`. This module no longer
declares a literal; it reads the installed package's metadata (which setuptools populates FROM
pyproject.toml at build/install time — `pip install -e .` included) via `importlib.metadata`, so
it is structurally impossible for this value to diverge from the package version again.
`tests/unit/test_version_consistency.py` is a regression test asserting the two stay equal.

**Review-round-1 fix (fail-fast):** the first version of this fix caught `PackageNotFoundError`
and fell back to a placeholder (`"0.0.0+unknown-not-installed"`) so import never failed. For a
reproducibility-focused production scanner that is the wrong trade-off: a client-facing run
manifest with an invented/placeholder `software_version` is worse than an import that fails
loudly and immediately, because the placeholder can silently ship in a report before anyone
notices the installation was wrong. This module now RAISES if package metadata can't be
resolved, rather than degrading silently.
"""

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _installed_version

_DISTRIBUTION_NAME = "nse-scanner"  # must match pyproject.toml's [project] name

try:
    __version__ = _installed_version(_DISTRIBUTION_NAME)
except PackageNotFoundError as exc:
    raise RuntimeError(
        f"nse_scanner could not resolve installed package metadata for {_DISTRIBUTION_NAME!r} "
        "(importlib.metadata.version() raised PackageNotFoundError). This package must be "
        "installed properly before use -- run `pip install -e .` (see CONTRIBUTING.md / "
        "Makefile) -- rather than imported from a raw source checkout via sys.path "
        "manipulation. Failing here, loudly and at import time, is deliberate: silently "
        "falling back to a placeholder version would let a run produce a client-facing "
        "run_manifest.json with an invented/unusable software_version, which is a worse outcome "
        "than refusing to run at all."
    ) from exc

BASELINE_STRATEGY_ID = "bb_ha_v1_base"  # immutable baseline strategy identifier
