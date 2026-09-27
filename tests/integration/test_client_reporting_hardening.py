"""Regression tests for the client-reporting-hardening branch (Task 11).

Numbered comments below map directly to the 13 items listed under TASK 11 in the branch
instructions; test_version_consistency.py separately covers item 11 (version) and the existing
229 pre-branch tests (re-run unmodified in CI) cover item 13.
"""

from __future__ import annotations

import subprocess

import nse_scanner.pipeline.scan as scan_module
import pandas as pd
import pytest
from nse_scanner.config import ScannerConfig
from nse_scanner.data.calendar import DataStatus, SessionInfo
from nse_scanner.offline_fixture import InMemoryBenchmarkProvider, InMemoryDataProvider, InMemoryUniverseProvider
from nse_scanner.pipeline.scan import (
    REASON_CLOSE_NOT_ABOVE_UPPER_BB,
    REASON_DATA_VALIDATION_FAILURE,
    REASON_NOT_EVALUABLE_INSUFFICIENT_HISTORY,
    REASON_NOT_EVALUABLE_NO_DATA,
    REASON_SANITY_GATE_FAILURE,
    STAGE_SANITY_GATE,
    STRATEGY_STATUS_EVALUATED,
    STRATEGY_STATUS_NOT_EVALUABLE,
    run_scan,
)
from nse_scanner.reporting.run_manifest import build_run_manifest
from nse_scanner.reporting.summary import build_summary_sheet
from nse_scanner.testing.synthetic_market_data import generate_synthetic_index, generate_synthetic_ohlcv

UNIVERSE_SIZE = 200

# Deterministic role assignment across the 200-symbol synthetic universe, mirroring the real
# 2026-09-25 NIFTY_200 run's shape (a handful of signals, zero of most failure categories, a
# LIMITED-history example) rather than an arbitrary split.
N_BREAKOUT = 2  # engineered to satisfy the mandatory BB+HA condition -> Primary_Signal=True
N_INSUFFICIENT_HISTORY = 3  # far fewer rows than min_rows_primary -> NOT_EVALUABLE
N_NO_DATA = 1  # no history at all for this symbol -> NOT_EVALUABLE
N_DATA_VALIDATION_FAILURE = 1  # rows exist but a required OHLCV column is missing -> NOT_EVALUABLE
# (review-round-1: a genuine data-quality problem, distinct from a plain row-count shortfall)
N_LIMITED_HISTORY = 1  # >= min_rows_primary but < min_rows_sma200 -> EVALUATED, SMA200 NA
# everything else: sufficient history, no engineered breakout -> EVALUATED, FAIL


def _build_universe_scenario() -> tuple[pd.DataFrame, dict[str, pd.DataFrame], SessionInfo]:
    symbols = [f"SYM{i:03d}" for i in range(UNIVERSE_SIZE)]
    universe_df = pd.DataFrame(
        {
            "Symbol": symbols,
            "Company_Name": [f"{s} Ltd" for s in symbols],
            "Sector": ["Technology"] * UNIVERSE_SIZE,
        }
    )

    histories: dict[str, pd.DataFrame] = {}
    cursor = 0
    for _ in range(N_BREAKOUT):
        s = symbols[cursor]
        histories[s] = generate_synthetic_ohlcv(s, n_days=250, seed=1000 + cursor, engineer_breakout_on_last_day=True)
        cursor += 1
    for _ in range(N_INSUFFICIENT_HISTORY):
        s = symbols[cursor]
        histories[s] = generate_synthetic_ohlcv(s, n_days=10, seed=2000 + cursor)
        cursor += 1
    for _ in range(N_NO_DATA):
        # deliberately absent from `histories` -> InMemoryDataProvider returns (None, None, err)
        cursor += 1
    for _ in range(N_DATA_VALIDATION_FAILURE):
        s = symbols[cursor]
        df = generate_synthetic_ohlcv(s, n_days=250, seed=5000 + cursor)
        # Plenty of ROWS (250) -- this must NOT be classified as insufficient-history. Dropping a
        # required column makes validate_and_clean_ohlc raise with
        # reason=DataValidationError.REASON_MISSING_COLUMNS, a genuine data-quality problem.
        histories[s] = df.drop(columns=["Volume"])
        cursor += 1
    for _ in range(N_LIMITED_HISTORY):
        s = symbols[cursor]
        histories[s] = generate_synthetic_ohlcv(s, n_days=60, seed=3000 + cursor)  # >=30, <200 rows
        cursor += 1
    for i in range(cursor, UNIVERSE_SIZE):
        s = symbols[i]
        histories[s] = generate_synthetic_ohlcv(s, n_days=250, seed=4000 + i)

    last_date = max(df.index[-1] for df in histories.values())
    d = last_date.date()
    session = SessionInfo(as_of_date=d, expected_completed_session=d, signal_date=d, planned_entry_date=d)
    return universe_df, histories, session


@pytest.fixture(scope="module")
def scenario_result():
    universe_df, histories, session = _build_universe_scenario()
    cfg = ScannerConfig()
    result = run_scan(
        cfg,
        InMemoryUniverseProvider(universe_df),
        InMemoryDataProvider(histories),
        InMemoryBenchmarkProvider(generate_synthetic_index()),
        session,
        holidays=set(),
    )
    return cfg, session, result


NO_DATA_SYMBOL = f"SYM{N_BREAKOUT + N_INSUFFICIENT_HISTORY:03d}"
DATA_VALIDATION_FAILURE_SYMBOL = f"SYM{N_BREAKOUT + N_INSUFFICIENT_HISTORY + N_NO_DATA:03d}"
LIMITED_SYMBOL = f"SYM{N_BREAKOUT + N_INSUFFICIENT_HISTORY + N_NO_DATA + N_DATA_VALIDATION_FAILURE:03d}"
PLAIN_FAIL_SYMBOL = (
    f"SYM{N_BREAKOUT + N_INSUFFICIENT_HISTORY + N_NO_DATA + N_DATA_VALIDATION_FAILURE + N_LIMITED_HISTORY:03d}"
)
INSUFFICIENT_SYMBOL = f"SYM{N_BREAKOUT:03d}"
BREAKOUT_SYMBOL = "SYM000"


# --- 1. A 200-symbol fixture produces 200 Diagnostics rows ---
def test_200_symbol_universe_produces_200_diagnostics_rows(scenario_result):
    _, _, result = scenario_result
    assert result.constituent_count == UNIVERSE_SIZE
    assert len(result.sheets.diagnostics) == UNIVERSE_SIZE


# --- 2. Every successfully-evaluated row preserves its actual latest numeric feature snapshot ---
def test_evaluated_rows_preserve_actual_feature_values(scenario_result):
    _, _, result = scenario_result
    diag = result.sheets.diagnostics
    evaluated = diag[diag["Primary_Strategy_Status"] == STRATEGY_STATUS_EVALUATED]
    # every EVALUATED row (pass or fail) must have its core feature snapshot populated -- this is
    # exactly the bug this branch fixes (a real 200-symbol run previously had ~198 EVALUATED rows
    # with every feature field blank)
    for col in ("CMP", "BB_Upper", "BB_Middle", "BB_Lower", "HA_Body_Pct", "ATR14", "Volume"):
        assert evaluated[col].notna().all(), f"{col} has nulls among EVALUATED rows"
    # and the values are the SAME ones the strategy decision used, not recalculated: for a plain
    # FAIL row, Close must NOT be above BB_Upper (that's exactly why it failed) -- if this test
    # ever failed it would mean CMP/BB_Upper were sourced from something other than the row the
    # strategy itself evaluated
    fail_row = diag[diag["Symbol"] == PLAIN_FAIL_SYMBOL].iloc[0]
    assert fail_row["CMP"] <= fail_row["BB_Upper"]


# --- 3. Non-signal symbols get explicit Primary_Failure_Reason ---
def test_non_signal_symbols_get_explicit_failure_reason(scenario_result):
    _, _, result = scenario_result
    diag = result.sheets.diagnostics
    non_signals = diag[~diag["Primary_Signal"]]
    assert (non_signals["Primary_Failure_Reason"].notna()).all()
    fail_row = diag[diag["Symbol"] == PLAIN_FAIL_SYMBOL].iloc[0]
    assert fail_row["Primary_Strategy_Status"] == STRATEGY_STATUS_EVALUATED
    assert fail_row["Primary_Failure_Reason"] == REASON_CLOSE_NOT_ABOVE_UPPER_BB


# --- 4. Signal symbols still appear in the signal sheet unchanged ---
def test_signal_symbols_appear_in_signal_sheet(scenario_result):
    _, _, result = scenario_result
    live = result.sheets.live_signals
    assert BREAKOUT_SYMBOL in set(live["Symbol"])
    signal_row = live[live["Symbol"] == BREAKOUT_SYMBOL].iloc[0]
    diag_row = result.sheets.diagnostics[result.sheets.diagnostics["Symbol"] == BREAKOUT_SYMBOL].iloc[0]
    assert signal_row["Primary_Signal"] is True or signal_row["Primary_Signal"] == True  # noqa: E712
    # signal-sheet numbers must be IDENTICAL to the diagnostics row for the same symbol -- one
    # underlying computation, two views
    for col in ("CMP", "BB_Upper", "HA_Body_Pct", "BB_Overshoot_Pct"):
        assert signal_row[col] == diag_row[col]


# --- 5. No-data / insufficient-history symbols remain distinguishable from valid non-signals ---
def test_no_data_and_insufficient_history_distinguishable_from_valid_non_signals(scenario_result):
    _, _, result = scenario_result
    diag = result.sheets.diagnostics

    no_data_row = diag[diag["Symbol"] == NO_DATA_SYMBOL].iloc[0]
    assert no_data_row["Primary_Strategy_Status"] == STRATEGY_STATUS_NOT_EVALUABLE
    assert no_data_row["Primary_Failure_Reason"] == REASON_NOT_EVALUABLE_NO_DATA
    assert pd.isna(no_data_row["CMP"])

    insufficient_row = diag[diag["Symbol"] == INSUFFICIENT_SYMBOL].iloc[0]
    assert insufficient_row["Primary_Strategy_Status"] == STRATEGY_STATUS_NOT_EVALUABLE
    assert insufficient_row["Primary_Failure_Reason"] == REASON_NOT_EVALUABLE_INSUFFICIENT_HISTORY
    assert pd.isna(insufficient_row["CMP"])

    data_validation_row = diag[diag["Symbol"] == DATA_VALIDATION_FAILURE_SYMBOL].iloc[0]
    assert data_validation_row["Primary_Strategy_Status"] == STRATEGY_STATUS_NOT_EVALUABLE
    assert data_validation_row["Primary_Failure_Reason"] == REASON_DATA_VALIDATION_FAILURE
    assert pd.isna(data_validation_row["CMP"])

    valid_fail_row = diag[diag["Symbol"] == PLAIN_FAIL_SYMBOL].iloc[0]
    assert valid_fail_row["Primary_Strategy_Status"] == STRATEGY_STATUS_EVALUATED
    assert pd.notna(valid_fail_row["CMP"])

    # all four reasons pairwise distinct -- the whole point of Task 3's explicit vocabulary
    reasons = {
        no_data_row["Primary_Failure_Reason"],
        insufficient_row["Primary_Failure_Reason"],
        data_validation_row["Primary_Failure_Reason"],
        valid_fail_row["Primary_Failure_Reason"],
    }
    assert len(reasons) == 4


# --- Review-round-1 fix 1: counters must reflect their true, specific category, not the
# --- DataValidationError umbrella. insufficient_history_count must NOT be inflated by NO_DATA or
# --- DATA_VALIDATION_FAILURE symbols; data_validation_failures is the umbrella and covers all
# --- three, by design (see the existing "Failure_Category=data_validation_failure" contract).
def test_insufficient_history_counter_excludes_no_data_and_data_validation_failure(scenario_result):
    _, _, result = scenario_result

    # the umbrella count covers all three NOT_EVALUABLE-due-to-data categories
    assert result.data_validation_failures == N_INSUFFICIENT_HISTORY + N_NO_DATA + N_DATA_VALIDATION_FAILURE

    # but the specific insufficient-history count must reflect ONLY that one category
    assert result.insufficient_history_count == N_INSUFFICIENT_HISTORY

    # cross-check against Data_Health, which is built from the exact same counters, and against
    # a direct count of Primary_Failure_Reason in Diagnostics -- three independent views of the
    # same underlying numbers must all agree
    diag = result.sheets.diagnostics
    assert int((diag["Primary_Failure_Reason"] == REASON_NOT_EVALUABLE_INSUFFICIENT_HISTORY).sum()) == (
        N_INSUFFICIENT_HISTORY
    )
    assert int((diag["Primary_Failure_Reason"] == REASON_NOT_EVALUABLE_NO_DATA).sum()) == N_NO_DATA
    assert int((diag["Primary_Failure_Reason"] == REASON_DATA_VALIDATION_FAILURE).sum()) == (N_DATA_VALIDATION_FAILURE)
    data_health = result.sheets.data_health.iloc[0]
    assert int(data_health["Insufficient_History_Count"]) == result.insufficient_history_count
    assert int(data_health["Data_Validation_Failures"]) == result.data_validation_failures


# --- Review-round-1 fix 2: a sanity-gate failure occurs AFTER the mandatory BB+HA condition has
# --- already been evaluated (bb_breakout/bb_size_ok/ha_strength_ok are computed before either
# --- ordering check) -- so it must be reported as EVALUATED, never NOT_EVALUABLE. NOT_EVALUABLE
# --- means "never reached the strategy check at all", which is not what happened here.
def test_sanity_gate_failure_reports_evaluated_not_not_evaluable(monkeypatch):
    universe_df, histories, session = _build_universe_scenario()
    cfg = ScannerConfig()

    original_check = scan_module.check_bollinger_ordering
    call_count = {"n": 0}

    def _fail_once_then_delegate(*args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise scan_module.SignalMathError("synthetic forced sanity failure for regression test")
        return original_check(*args, **kwargs)

    monkeypatch.setattr(scan_module, "check_bollinger_ordering", _fail_once_then_delegate)

    result = scan_module.run_scan(
        cfg,
        InMemoryUniverseProvider(universe_df),
        InMemoryDataProvider(histories),
        InMemoryBenchmarkProvider(generate_synthetic_index()),
        session,
        holidays=set(),
    )

    diag = result.sheets.diagnostics
    tripped_row = diag[diag["Symbol"] == "SYM000"].iloc[0]
    assert tripped_row["Primary_Strategy_Status"] == STRATEGY_STATUS_EVALUATED
    assert tripped_row["Primary_Failure_Stage"] == STAGE_SANITY_GATE
    assert tripped_row["Primary_Failure_Reason"] == REASON_SANITY_GATE_FAILURE
    assert tripped_row["Primary_Signal"] == False  # noqa: E712 -- never emit a signal we can't trust
    # the feature snapshot must still be preserved for debugging even though the row is untrusted
    assert pd.notna(tripped_row["CMP"])
    assert pd.notna(tripped_row["BB_Upper"])
    assert result.security_scan_failures == 1


# --- 6. LIMITED history does not become a primary strategy failure when the primary threshold is met ---
def test_limited_history_is_not_a_primary_strategy_failure(scenario_result):
    _, _, result = scenario_result
    diag = result.sheets.diagnostics
    row = diag[diag["Symbol"] == LIMITED_SYMBOL].iloc[0]
    assert row["Rows"] >= ScannerConfig().history.min_rows_primary
    assert row["Rows"] < ScannerConfig().history.min_rows_sma200
    assert row["History_Quality"] == "LIMITED"
    # EVALUATED regardless of whether it happened to pass or fail the strategy condition --
    # LIMITED history quality is about secondary feature availability, not the baseline signal
    assert row["Primary_Strategy_Status"] == STRATEGY_STATUS_EVALUATED
    assert pd.isna(row["SMA200"])
    assert row["SMA200_Status"] != "OK"


# --- 7. Research sheets remain empty when research_mode=false ---
def test_research_sheets_empty_when_research_mode_false(scenario_result):
    cfg, _, result = scenario_result
    assert cfg.research.run_research_mode is False
    assert result.sheets.research_summary.empty
    assert result.sheets.forward_returns.empty
    assert result.sheets.sensitivity.empty


# --- 8. Scan_Log remains empty when no failures occurred ---
def test_scan_log_empty_when_no_unexpected_failures(scenario_result):
    _, _, result = scenario_result
    assert result.security_scan_failures == 0
    assert result.sheets.scan_log.empty


# --- 9. Summary sheet counts exactly agree with the underlying scan result ---
def test_summary_sheet_counts_match_scan_result(scenario_result):
    cfg, session, result = scenario_result
    manifest = build_run_manifest(
        run_id=result.run_id,
        cfg=cfg,
        universe_id=cfg.universe.universe_scope,
        universe_snapshot_date=session.as_of_date.isoformat(),
        universe_source=result.universe_source,
        constituent_count=result.constituent_count,
        data_provider="SYNTHETIC_FIXTURE",
        data_as_of=session.expected_completed_session.isoformat(),
        expected_session=session.expected_completed_session.isoformat(),
        signal_date=session.signal_date.isoformat(),
        status=result.run_health,
        price_basis=result.price_basis,
    )
    summary = build_summary_sheet(result, cfg, session, manifest)
    values = dict(zip(summary["Field"], summary["Value"], strict=True))

    assert values["Universe_Count"] == result.constituent_count == UNIVERSE_SIZE
    assert values["Stale_Count"] == result.signals_stale
    assert values["Data_Validation_Failures"] == result.data_validation_failures
    assert values["Security_Scan_Failures"] == result.security_scan_failures
    assert values["Insufficient_History_Count"] == result.insufficient_history_count
    assert values["Current_Signal_Count"] == result.signals_current
    assert result.signals_current >= N_BREAKOUT  # at least the engineered breakouts must show up
    assert values["Run_ID"] == result.run_id
    assert values["Run_Health"] == result.run_health
    assert values["Benchmark_Status"] == result.benchmark_status
    assert values["Data_Current_Count"] == int(result.sheets.data_health.iloc[0]["Data_Current"])


# --- 10. Summary strategy parameters exactly match configuration ---
def test_summary_sheet_strategy_parameters_match_config(scenario_result):
    cfg, session, result = scenario_result
    manifest = build_run_manifest(
        run_id=result.run_id,
        cfg=cfg,
        universe_id=cfg.universe.universe_scope,
        universe_snapshot_date=session.as_of_date.isoformat(),
        universe_source=result.universe_source,
        constituent_count=result.constituent_count,
        data_provider="SYNTHETIC_FIXTURE",
        data_as_of=session.expected_completed_session.isoformat(),
        expected_session=session.expected_completed_session.isoformat(),
        signal_date=session.signal_date.isoformat(),
        status=result.run_health,
        price_basis=result.price_basis,
    )
    summary = build_summary_sheet(result, cfg, session, manifest)
    values = dict(zip(summary["Field"], summary["Value"], strict=True))

    assert values["Strategy_ID"] == cfg.baseline.strategy_id == "bb_ha_v1_base"
    assert values["BB_Period"] == cfg.baseline.bb_period == 20
    assert values["BB_Std_Mult"] == cfg.baseline.bb_std_mult == 2.0
    assert values["BB_DDOF"] == cfg.baseline.bb_ddof == 1
    assert values["Max_BB_Overshoot_Pct"] == cfg.baseline.max_bb_overshoot_pct == 4.0
    assert values["Min_HA_Body_Pct"] == cfg.baseline.min_ha_body_pct == 1.0
    assert "EOD" in values["Notes"] and "not an intraday feed" in values["Notes"].lower()
    assert "probability" in values["Notes"].lower()


# --- 12. Manifest git_commit matches the exact tested commit ---
def test_manifest_git_commit_matches_actual_head(scenario_result):
    cfg, session, result = scenario_result
    manifest = build_run_manifest(
        run_id=result.run_id,
        cfg=cfg,
        universe_id=cfg.universe.universe_scope,
        universe_snapshot_date=session.as_of_date.isoformat(),
        universe_source=result.universe_source,
        constituent_count=result.constituent_count,
        data_provider="SYNTHETIC_FIXTURE",
        data_as_of=session.expected_completed_session.isoformat(),
        expected_session=session.expected_completed_session.isoformat(),
        signal_date=session.signal_date.isoformat(),
        status=result.run_health,
        price_basis=result.price_basis,
    )
    actual_head = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, timeout=5).stdout.strip()
    assert manifest.git_commit == actual_head


def test_data_status_field_present_for_no_data_symbol(scenario_result):
    """Guard against DataStatus.UNAVAILABLE silently regressing to a Python None/NaN in the
    Excel output for the two truly-unevaluable categories."""
    _, _, result = scenario_result
    diag = result.sheets.diagnostics
    row = diag[diag["Symbol"] == NO_DATA_SYMBOL].iloc[0]
    assert row["Data_Status"] == DataStatus.UNAVAILABLE
