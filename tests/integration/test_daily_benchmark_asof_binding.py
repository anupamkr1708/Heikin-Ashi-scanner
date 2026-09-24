"""Regression coverage for the research-integrity gap found during the P1 forensic audit:
`cli/run_daily.py` and `cli/run_scan.py` constructed `YahooBenchmarkProvider(cfg.data)` with no
`as_of_date`, while `cli/run_replay.py` already correctly bound it to
`session.expected_completed_session`. That meant the daily/non-replay paths could ingest a
same-day/in-progress `^NSEI` row (e.g. a 2026-09-24 bar while the run is scoring the completed
2026-09-23 session) with no cutoff at all — the response-layer filter in `data/benchmark.py` is
only reached when `as_of_date is not None`.

These tests exercise the REAL `cli.run_daily.main()` / `cli.run_scan.main()` entry points (not a
hand-rolled mimic of the provider construction) so the assertion is about the actual call site,
per the explicit instruction not to rely solely on a provider-level unit test
(`tests/unit/test_benchmark_provider.py` already covers the provider itself).

Both tests fail on the pre-fix code (provider built with `as_of_date=None`, so the 2026-09-24 row
reaches `Market_Regime`) and pass after the one-line call-site fix in each CLI module.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest
import yaml
from nse_scanner.data.calendar import SessionInfo
from nse_scanner.offline_fixture import InMemoryUniverseProvider
from nse_scanner.testing.synthetic_market_data import generate_synthetic_universe

# Fixed, deterministic session standing in for the project's own worked example:
#   RUN DATE (IST) = 2026-09-24, EXPECTED COMPLETED SESSION = SIGNAL DATE = 2026-09-23.
# Session resolution itself (weekday/holiday/close-time arithmetic) is already covered by
# tests/unit/test_calendar*.py — this file is only about whether the CLI, GIVEN a session, binds
# the benchmark provider to it. Patching `resolve_session` to return this fixed value keeps the
# test independent of the real holiday calendar and of `--date`/`--as-of`'s "force to 16:00 IST"
# semantics (which would make the forced date ITSELF the completed session, not what this
# scenario needs).
FIXED_SESSION = SessionInfo(
    as_of_date=date(2026, 9, 24),
    expected_completed_session=date(2026, 9, 23),
    signal_date=date(2026, 9, 23),
    planned_entry_date=date(2026, 9, 24),
)


def _benchmark_frame_spanning_the_cutoff() -> pd.DataFrame:
    """A single-column-set OHLCV benchmark frame with rows on 2026-09-22, -23, and -24 — the
    exact shape `normalize_ticker_frame` produces after normalization. `close_by_date` lets each
    test assert on a known, distinguishable value per day rather than an opaque number."""
    dates = pd.to_datetime(["2026-09-22", "2026-09-23", "2026-09-24"])
    closes = [24000.0, 24100.0, 99999.0]  # 24-09 deliberately implausible/large -> unmissable leak
    return pd.DataFrame(
        {"Open": closes, "High": closes, "Low": closes, "Close": closes, "Volume": [123456, 234567, 345678]},
        index=dates,
    )


def _write_temp_config(tmp_path: Path) -> Path:
    """Every path the CLI touches (DuckDB/parquet store, reports dir) is redirected under
    tmp_path so this test can never read or write the real repository's data/reports directories."""
    cfg_path = tmp_path / "test_config.yaml"
    cfg_path.write_text(
        yaml.safe_dump(
            {
                "paths": {
                    "processed_dir": str(tmp_path / "processed"),
                    "duckdb_path": str(tmp_path / "db.duckdb"),
                    "reports_dir": str(tmp_path / "reports"),
                    "raw_dir": str(tmp_path / "raw"),
                },
            }
        )
    )
    return cfg_path


def _run_cli_and_capture_sheets(
    monkeypatch, tmp_path: Path, cli_module, argv: list[str], benchmark_frame: pd.DataFrame
):
    """Runs the real CLI `main()` with: a fixed session (see FIXED_SESSION), a synthetic
    single-symbol universe (no local price history needed — the Market_Regime sheet this test
    checks is built before the per-symbol loop, same fact `test_replay_benchmark_no_lookahead.py`
    relies on), a mocked `yfinance.download` returning `benchmark_frame` regardless of what's
    asked for (the realistic case: Yahoo always has "up to today", not "up to as_of"), and
    `write_report` intercepted so the produced sheets can be inspected without parsing an xlsx
    file. Returns (exit_code, captured_sheets)."""
    cfg_path = _write_temp_config(tmp_path)

    universe_df = generate_synthetic_universe().iloc[[0]].copy()  # one symbol, not ingested
    monkeypatch.setattr(cli_module, "get_universe_provider", lambda cfg: InMemoryUniverseProvider(universe_df))
    monkeypatch.setattr(cli_module, "resolve_session", lambda now, holidays: FIXED_SESSION)
    monkeypatch.setattr(cli_module, "resolve_holidays_for_run", lambda *a, **k: set())

    captured: dict = {}
    monkeypatch.setattr(
        cli_module, "write_report", lambda sheets, path: captured.setdefault("sheets", sheets) or Path(path)
    )

    full_argv = argv + ["--config", str(cfg_path), "--allow-missing-holidays"]
    with patch("yfinance.download", return_value=benchmark_frame.copy()):
        exit_code = cli_module.main(full_argv)

    return exit_code, captured.get("sheets")


@pytest.mark.parametrize("cli_name", ["run_daily", "run_scan"])
def test_cli_entry_point_binds_benchmark_provider_to_expected_completed_session(monkeypatch, tmp_path, cli_name):
    """Call-site binding proof (Part 1A item 3): spy on the REAL `YahooBenchmarkProvider` class as
    imported into each CLI module and assert the actual constructor call carries
    `as_of_date=session.expected_completed_session` — not a re-implementation of the same check,
    the production call site itself."""
    import importlib

    cli_module = importlib.import_module(f"nse_scanner.cli.{cli_name}")

    captured_kwargs: dict = {}
    real_provider_cls = cli_module.YahooBenchmarkProvider

    class _SpyBenchmarkProvider(real_provider_cls):
        def __init__(self, cfg, as_of_date=None):
            captured_kwargs["as_of_date"] = as_of_date
            super().__init__(cfg, as_of_date=as_of_date)

    monkeypatch.setattr(cli_module, "YahooBenchmarkProvider", _SpyBenchmarkProvider)

    argv = ["--skip-ingest"] if cli_name == "run_daily" else []
    exit_code, sheets = _run_cli_and_capture_sheets(
        monkeypatch, tmp_path, cli_module, argv, _benchmark_frame_spanning_the_cutoff()
    )

    assert (
        "as_of_date" in captured_kwargs
    ), f"{cli_name}.main() never constructed YahooBenchmarkProvider — cannot assert its as_of_date"
    assert captured_kwargs["as_of_date"] == FIXED_SESSION.expected_completed_session, (
        f"{cli_name}.py must bind the benchmark provider to session.expected_completed_session "
        f"(2026-09-23), not {captured_kwargs['as_of_date']!r} — this is exactly the P1 gap: the "
        f"call site building YahooBenchmarkProvider(cfg.data) with no as_of_date at all."
    )
    # exit_code is deliberately not asserted here: with a single non-ingested synthetic universe
    # member the run correctly reports RUN_HEALTH=RED (a baseline-scan-quality signal, unrelated
    # to this test). What matters is that main() reached write_report at all, proving the run
    # actually got past the benchmark-provider construction this test is inspecting.
    assert sheets is not None


@pytest.mark.parametrize("cli_name", ["run_daily", "run_scan"])
def test_cli_entry_point_market_regime_excludes_benchmark_rows_after_completed_session(monkeypatch, tmp_path, cli_name):
    """Part 1A items 1 and 2 (daily-path / run_scan-path test): with a mocked benchmark feed
    containing 2026-09-22/23/24 and expected_completed_session=2026-09-23, the 2026-09-24
    observation must never reach the Market_Regime sheet — the same concrete symptom
    `test_replay_benchmark_no_lookahead.py` checks for replay, proven here for the two
    non-replay entry points instead."""
    import importlib

    cli_module = importlib.import_module(f"nse_scanner.cli.{cli_name}")

    benchmark_frame = _benchmark_frame_spanning_the_cutoff()
    argv = ["--skip-ingest"] if cli_name == "run_daily" else []
    exit_code, sheets = _run_cli_and_capture_sheets(monkeypatch, tmp_path, cli_module, argv, benchmark_frame)

    # exit_code not asserted — see note in the sibling test above (RED here is a baseline-scan
    # -quality signal from the single non-ingested synthetic symbol, orthogonal to this check).
    assert sheets is not None and not sheets.market_regime.empty
    reported_close = sheets.market_regime.iloc[0]["Index_Close"]

    # The regression this guards against: pre-fix, as_of_date=None means no response-layer
    # cutoff runs at all, so the LAST row of the (always-live) mocked feed -- the 2026-09-24
    # row -- would be reported instead.
    leaking_close = benchmark_frame.loc["2026-09-24", "Close"].item()
    correct_close = benchmark_frame.loc["2026-09-23", "Close"].item()

    assert reported_close != leaking_close, (
        f"{cli_name}.py leaked the 2026-09-24 benchmark row (Close={leaking_close}) into "
        f"Market_Regime for a run whose expected completed session is 2026-09-23."
    )
    assert reported_close == correct_close
