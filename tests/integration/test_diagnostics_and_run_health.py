"""Covers the exact bugs the user's real NSE run surfaced:

- Diagnostics previously only contained PASS rows.
- Mapping_OK = universe_count - len(scan_log) could go negative / was meaningless.
- Benchmark failure alone pushed RUN_HEALTH to RED even though the baseline scan was healthy.
"""

import pandas as pd
from nse_scanner.config import ScannerConfig
from nse_scanner.data.calendar import SessionInfo
from nse_scanner.offline_fixture import InMemoryBenchmarkProvider, InMemoryDataProvider, InMemoryUniverseProvider
from nse_scanner.pipeline.scan import run_scan
from nse_scanner.testing.synthetic_market_data import (
    SYNTHETIC_SYMBOLS,
    generate_synthetic_index,
    generate_synthetic_ohlcv,
    generate_synthetic_universe,
)


def _session_for(histories: dict[str, pd.DataFrame]) -> SessionInfo:
    last_date = max(df.index[-1] for df in histories.values())
    d = last_date.date()
    return SessionInfo(as_of_date=d, expected_completed_session=d, signal_date=d, planned_entry_date=d)


def test_diagnostics_contains_every_universe_member_regardless_of_outcome():
    cfg = ScannerConfig()
    universe_df = generate_synthetic_universe()
    histories = {
        sym: generate_synthetic_ohlcv(sym, n_days=80, engineer_breakout_on_last_day=(sym == SYNTHETIC_SYMBOLS[0]))
        for sym in SYNTHETIC_SYMBOLS
    }
    session = _session_for(histories)

    result = run_scan(
        cfg,
        InMemoryUniverseProvider(universe_df),
        InMemoryDataProvider(histories),
        InMemoryBenchmarkProvider(generate_synthetic_index()),
        session,
        holidays=set(),
    )

    diagnostics = result.sheets.diagnostics
    assert len(diagnostics) == len(SYNTHETIC_SYMBOLS)  # every symbol, not just the one PASS
    assert set(diagnostics["Symbol"]) == set(SYNTHETIC_SYMBOLS)
    # At least one PASS (the engineered breakout) and at least one non-PASS (everyone else).
    assert diagnostics["Primary_Signal"].sum() >= 1
    assert (~diagnostics["Primary_Signal"]).sum() >= 1


def test_diagnostics_includes_insufficient_history_symbols_with_reason():
    cfg = ScannerConfig()
    universe_df = generate_synthetic_universe()
    # One symbol has almost no history at all.
    histories = {sym: generate_synthetic_ohlcv(sym, n_days=80) for sym in SYNTHETIC_SYMBOLS[1:]}
    histories[SYNTHETIC_SYMBOLS[0]] = generate_synthetic_ohlcv(SYNTHETIC_SYMBOLS[0], n_days=1)
    session = _session_for(histories)

    result = run_scan(
        cfg,
        InMemoryUniverseProvider(universe_df),
        InMemoryDataProvider(histories),
        InMemoryBenchmarkProvider(generate_synthetic_index()),
        session,
        holidays=set(),
    )

    diagnostics = result.sheets.diagnostics
    assert len(diagnostics) == len(SYNTHETIC_SYMBOLS)
    row = diagnostics[diagnostics["Symbol"] == SYNTHETIC_SYMBOLS[0]].iloc[0]
    assert row["History_Status"] == "INSUFFICIENT_HISTORY"
    assert row["Failure_Category"] == "data_validation_failure"
    assert row["Failure_Reason"] is not None
    assert result.data_validation_failures == 1
    assert result.insufficient_history_count == 1


def test_benchmark_failure_alone_does_not_force_red():
    """The exact bug: baseline healthy + benchmark unavailable must be YELLOW, not RED."""
    cfg = ScannerConfig()
    universe_df = generate_synthetic_universe()
    histories = {sym: generate_synthetic_ohlcv(sym, n_days=80) for sym in SYNTHETIC_SYMBOLS}
    session = _session_for(histories)

    # No benchmark provider at all -> benchmark_status stays UNAVAILABLE, baseline scan is fine.
    result = run_scan(
        cfg,
        InMemoryUniverseProvider(universe_df),
        InMemoryDataProvider(histories),
        benchmark_provider=None,
        session=session,
        holidays=set(),
    )

    assert result.benchmark_status == "UNAVAILABLE"
    assert result.data_validation_failures == 0
    assert result.security_scan_failures == 0
    assert result.run_health == "YELLOW"  # capped at YELLOW, never RED, for benchmark alone


def test_high_baseline_failure_rate_is_red_regardless_of_benchmark():
    cfg = ScannerConfig()
    universe_df = generate_synthetic_universe()
    # Cold-start scenario: everyone has only 1 row (matches the user's real-world report).
    histories = {sym: generate_synthetic_ohlcv(sym, n_days=1) for sym in SYNTHETIC_SYMBOLS}
    session = _session_for(histories)

    result = run_scan(
        cfg,
        InMemoryUniverseProvider(universe_df),
        InMemoryDataProvider(histories),
        InMemoryBenchmarkProvider(generate_synthetic_index()),
        session,
        holidays=set(),
    )

    assert result.run_health == "RED"
    assert result.needs_bootstrap is True


def test_healthy_baseline_and_healthy_benchmark_is_green():
    cfg = ScannerConfig()
    universe_df = generate_synthetic_universe()
    histories = {sym: generate_synthetic_ohlcv(sym, n_days=80) for sym in SYNTHETIC_SYMBOLS}
    session = _session_for(histories)

    result = run_scan(
        cfg,
        InMemoryUniverseProvider(universe_df),
        InMemoryDataProvider(histories),
        InMemoryBenchmarkProvider(generate_synthetic_index()),
        session,
        holidays=set(),
    )

    assert result.run_health == "GREEN"
    assert result.needs_bootstrap is False


def test_security_scan_failure_is_bucketed_separately_from_data_validation_failure(monkeypatch):
    """A genuine bug (sanity-gate trip) must never be conflated with routine insufficient-history
    failures — they are different categories with different meanings."""
    import nse_scanner.pipeline.scan as scan_module

    cfg = ScannerConfig()
    universe_df = generate_synthetic_universe()
    histories = {sym: generate_synthetic_ohlcv(sym, n_days=80) for sym in SYNTHETIC_SYMBOLS}
    session = _session_for(histories)

    def _boom(*args, **kwargs):
        raise scan_module.SignalMathError("synthetic forced sanity failure")

    monkeypatch.setattr(scan_module, "check_bollinger_ordering", _boom)

    result = run_scan(
        cfg,
        InMemoryUniverseProvider(universe_df),
        InMemoryDataProvider(histories),
        InMemoryBenchmarkProvider(generate_synthetic_index()),
        session,
        holidays=set(),
    )

    assert result.security_scan_failures == len(SYNTHETIC_SYMBOLS)
    assert result.data_validation_failures == 0
    assert result.run_health == "RED"  # 100% security-scan-failure rate is a real bug, not routine
