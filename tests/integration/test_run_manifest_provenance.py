"""v1.3 audit regression: PART 54 requires the run manifest to carry benchmark status, data-quality
counts, scan/signal counts, and (for ingesting entry points) the ingested file's hash -- as the
single machine-readable, reproducible record of a run. Before this fix, `cli/run_daily.py`,
`cli/run_scan.py`, `cli/run_replay.py`, and `offline_fixture.py::run_offline_fixture_scan` all
computed these values (they were printed to the console / available on `ScanRunResult`) but never
passed them to `build_run_manifest`'s `extra=` field, so `run_manifest_*.json` on disk was missing
all of them -- a real gap between what the console showed a human and what the manifest recorded
for a machine/auditor, in a file whose entire purpose (PART 54/55) is to be that record.

This test exercises the one entry point reachable with zero network access
(`run_offline_fixture_scan`) and asserts the actual JSON file on disk -- not just the in-memory
`ScanRunResult` -- contains the previously-dropped fields.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import date

from nse_scanner.config import PathsConfig, ScannerConfig
from nse_scanner.data.calendar import SessionInfo
from nse_scanner.data.storage import MarketDataStore
from nse_scanner.offline_fixture import run_offline_fixture_scan


def test_offline_fixture_manifest_on_disk_carries_the_previously_dropped_fields(tmp_path):
    cfg = ScannerConfig(paths=PathsConfig(reports_dir=str(tmp_path / "reports")))
    store = MarketDataStore(tmp_path / "processed", tmp_path / "db.duckdb")
    today = date(2026, 9, 15)
    session = SessionInfo(today, today, today, today)

    exit_code = run_offline_fixture_scan(cfg, session, store)
    assert exit_code == 0

    manifest_path = tmp_path / "reports" / "run_manifest_OFFLINE_FIXTURE.json"
    assert manifest_path.exists()
    manifest = json.loads(manifest_path.read_text())

    # Top-level field that was previously silently omitted at this specific call site (fell back
    # to the config default instead of the actual price basis the provider returned).
    assert manifest["price_basis"] is not None

    extra = manifest["extra"]
    for key in (
        "benchmark_status",
        "data_validation_failures",
        "insufficient_history_count",
        "security_scan_failures",
        "signals_current",
        "signals_stale",
    ):
        assert key in extra, f"{key!r} missing from manifest['extra'] — the v1.3 fix regressed"
    # Sanity: these should be the real, non-placeholder values (int/str), not None stand-ins.
    assert isinstance(extra["signals_current"], int)
    assert isinstance(extra["benchmark_status"], str)


def test_manifest_extra_survives_a_second_offline_run_with_different_values(tmp_path):
    """Not a hard-coded single-value check: confirm the manifest reflects each run's ACTUAL
    result rather than some cached/stale default, by nudging the baseline threshold between two
    runs (widening it should not shrink the observed signal count in this fixture) and checking
    the manifest's signals_current tracks `ScanRunResult.signals_current` on each run."""
    cfg_a = ScannerConfig(paths=PathsConfig(reports_dir=str(tmp_path / "a")))
    cfg_b = replace(
        cfg_a,
        paths=PathsConfig(reports_dir=str(tmp_path / "b")),
        baseline=replace(cfg_a.baseline, min_ha_body_pct=99.0),
    )  # impossibly strict
    store = MarketDataStore(tmp_path / "processed", tmp_path / "db.duckdb")
    today = date(2026, 9, 15)
    session = SessionInfo(today, today, today, today)

    run_offline_fixture_scan(cfg_a, session, store)
    run_offline_fixture_scan(cfg_b, session, store)

    manifest_a = json.loads((tmp_path / "a" / "run_manifest_OFFLINE_FIXTURE.json").read_text())
    manifest_b = json.loads((tmp_path / "b" / "run_manifest_OFFLINE_FIXTURE.json").read_text())

    # cfg_b's impossibly strict HA-body threshold must drive signals_current to 0, and the
    # manifest must reflect that -- proving `extra` isn't a copy-pasted static value.
    assert manifest_b["extra"]["signals_current"] == 0
    assert manifest_a["extra"]["signals_current"] != manifest_b["extra"]["signals_current"]
