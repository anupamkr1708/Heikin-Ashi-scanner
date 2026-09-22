"""Proves the whole wired-together pipeline works with zero network access: universe -> data ->
features -> baseline signal -> optional context -> Excel -> run manifest. This is what PART 89's
"no broken imports / no circular dependencies / no undefined functions" acceptance criterion
actually gets tested by.
"""

from pathlib import Path

from nse_scanner.config import ScannerConfig
from nse_scanner.data.calendar import SessionInfo
from nse_scanner.offline_fixture import (
    InMemoryBenchmarkProvider,
    InMemoryDataProvider,
    InMemoryUniverseProvider,
)
from nse_scanner.pipeline.scan import run_scan
from nse_scanner.testing.synthetic_market_data import (
    SYNTHETIC_SYMBOLS,
    generate_synthetic_index,
    generate_synthetic_ohlcv,
    generate_synthetic_universe,
)


def test_full_offline_scan_pipeline_produces_a_signal_and_a_valid_report(tmp_path):
    cfg = ScannerConfig()

    universe_df = generate_synthetic_universe()
    histories = {
        sym: generate_synthetic_ohlcv(sym, n_days=80, engineer_breakout_on_last_day=(sym == SYNTHETIC_SYMBOLS[0]))
        for sym in SYNTHETIC_SYMBOLS
    }
    index_df = generate_synthetic_index()

    universe_provider = InMemoryUniverseProvider(universe_df)
    data_provider = InMemoryDataProvider(histories)
    benchmark_provider = InMemoryBenchmarkProvider(index_df)

    last_date = max(df.index[-1] for df in histories.values())
    session = SessionInfo(
        as_of_date=last_date.date(), expected_completed_session=last_date.date(),
        signal_date=last_date.date(), planned_entry_date=last_date.date(),
    )

    result = run_scan(cfg, universe_provider, data_provider, benchmark_provider, session, holidays=set())

    assert result.constituent_count == len(SYNTHETIC_SYMBOLS)
    assert result.benchmark_status == "OK"
    assert result.signals_current >= 1  # the engineered breakout must be found
    assert result.run_health in ("GREEN", "YELLOW")

    live = result.sheets.live_signals
    assert not live.empty
    row = live.iloc[0]
    assert row["Stock"] in SYNTHETIC_SYMBOLS
    assert 0 < row["BB_Overshoot_Pct"] <= cfg.baseline.max_bb_overshoot_pct
    assert row["HA_Body_Pct"] >= cfg.baseline.min_ha_body_pct
    assert row["Data_Status"] == "CURRENT"

    from nse_scanner.reporting.excel import write_report
    out_path = Path(tmp_path) / "report.xlsx"
    written = write_report(result.sheets, out_path)
    assert written.exists()
    assert written.stat().st_size > 0


def test_scan_survives_a_broken_symbol_without_halting(tmp_path):
    """One bad symbol must never halt the whole scan (PART 15)."""
    cfg = ScannerConfig()
    universe_df = generate_synthetic_universe()
    histories = {
        sym: generate_synthetic_ohlcv(sym, n_days=80) for sym in SYNTHETIC_SYMBOLS[1:]
    }
    # SYNTHETIC_SYMBOLS[0] has NO history at all -> must be logged as a failure, not crash the run
    index_df = generate_synthetic_index()

    universe_provider = InMemoryUniverseProvider(universe_df)
    data_provider = InMemoryDataProvider(histories)
    benchmark_provider = InMemoryBenchmarkProvider(index_df)

    last_date = max(df.index[-1] for df in histories.values())
    session = SessionInfo(last_date.date(), last_date.date(), last_date.date(), last_date.date())

    result = run_scan(cfg, universe_provider, data_provider, benchmark_provider, session, holidays=set())
    assert any(f["security"] == SYNTHETIC_SYMBOLS[0] for f in result.failures)
    assert result.constituent_count == len(SYNTHETIC_SYMBOLS)  # scan still covered everyone else
