"""Client-friendly EOD_Summary sheet (Task 5, client-reporting-hardening).

A NIFTY_200-scale Diagnostics sheet (200 rows x 50+ columns) is the right place for an analyst
to dig in, but it is NOT where a client should have to start. This module builds a single,
short, label/value sheet that answers "what run is this, what did it cover, is it trustworthy,
and what exact configuration/version produced it" in one glance — everything Task 5 asks for,
nothing invented or recalculated (every value here is read directly from the already-computed
ScanRunResult / ScannerConfig / SessionInfo / RunManifest, the same objects the rest of the
report and the run_manifest_*.json are built from).

Deliberately NOT included here: any per-security prediction, ranking, or "recommendation" —
this sheet is run-level metadata only (Task 14: "not a trading recommendation engine"). The
methodological statement at the bottom is Task 5's explicit requirement: EOD/not-intraday, and
Research_Heuristic_Score is not a probability.
"""

from __future__ import annotations

import pandas as pd

from nse_scanner.config import ScannerConfig
from nse_scanner.data.calendar import SessionInfo
from nse_scanner.pipeline.scan import ScanRunResult
from nse_scanner.reporting.run_manifest import RunManifest

METHODOLOGY_STATEMENT = (
    "This is an END-OF-DAY (EOD/T-1) technical screening report. It is NOT an intraday feed, NOT "
    "a live market data stream, and NOT connected to a live/real-time price source. All figures "
    "reflect the completed session identified below. Research_Heuristic_Score, where present, is "
    "a descriptive ranking heuristic only — it is NOT a probability, NOT an expected return, and "
    "is NOT statistically validated. Nothing in this report is a trade recommendation; it exists "
    "to support manual, human review ahead of the next trading session."
)


def _kv(section: str, field: str, value) -> dict:
    return {"Section": section, "Field": field, "Value": value}


def build_summary_sheet(
    result: ScanRunResult,
    cfg: ScannerConfig,
    session: SessionInfo,
    manifest: RunManifest,
) -> pd.DataFrame:
    """Every value below is read from an object the rest of the pipeline already produced —
    nothing is recomputed (Task 15 scope discipline: this is a reporting/aggregation step, not a
    new calculation). `manifest` should be the SAME RunManifest instance written to
    run_manifest_*.json for this run, so Summary and the manifest can never disagree."""
    # Data_Current is only reliably known from Data_Health, where pipeline/scan.py already
    # computed it once — read it back rather than re-deriving it here.
    data_current = (
        int(result.sheets.data_health.iloc[0]["Data_Current"]) if not result.sheets.data_health.empty else None
    )
    market_regime = (
        result.sheets.market_regime.iloc[0]["Market_Regime"] if not result.sheets.market_regime.empty else None
    )

    rows: list[dict] = [
        # --- Run identity / provenance ---
        _kv("Run Identity", "Run_ID", result.run_id),
        _kv("Run Identity", "Git_Commit", manifest.git_commit),
        _kv("Run Identity", "Software_Version", manifest.software_version),
        _kv("Run Identity", "Run_Health", result.run_health),
        _kv("Run Identity", "Run_Timestamp_UTC", manifest.run_timestamp),
        # --- Session / dates ---
        _kv("Session", "Report_Date", session.as_of_date.isoformat()),
        _kv("Session", "Expected_Completed_Session", session.expected_completed_session.isoformat()),
        _kv("Session", "Signal_Date", session.signal_date.isoformat()),
        _kv("Session", "Next_Planned_Session", session.planned_entry_date.isoformat()),
        # --- Universe & coverage ---
        _kv("Universe & Coverage", "Universe_ID", cfg.universe.universe_scope),
        _kv("Universe & Coverage", "Universe_Count", result.constituent_count),
        _kv("Universe & Coverage", "Universe_Source", result.universe_source),
        _kv("Universe & Coverage", "Data_Current_Count", data_current),
        _kv("Universe & Coverage", "Stale_Count", result.signals_stale),
        _kv("Universe & Coverage", "Data_Validation_Failures", result.data_validation_failures),
        _kv("Universe & Coverage", "Security_Scan_Failures", result.security_scan_failures),
        _kv("Universe & Coverage", "Insufficient_History_Count", result.insufficient_history_count),
        _kv("Universe & Coverage", "Current_Signal_Count", result.signals_current),
        # --- Benchmark / regime ---
        _kv("Market Context", "Benchmark_Status", result.benchmark_status),
        _kv("Market Context", "Market_Regime", market_regime),
        # --- Immutable strategy parameters (read straight from cfg -- never recalculated) ---
        _kv("Strategy Parameters (Immutable Baseline)", "Strategy_ID", cfg.baseline.strategy_id),
        _kv("Strategy Parameters (Immutable Baseline)", "BB_Period", cfg.baseline.bb_period),
        _kv("Strategy Parameters (Immutable Baseline)", "BB_Std_Mult", cfg.baseline.bb_std_mult),
        _kv("Strategy Parameters (Immutable Baseline)", "BB_DDOF", cfg.baseline.bb_ddof),
        _kv("Strategy Parameters (Immutable Baseline)", "Max_BB_Overshoot_Pct", cfg.baseline.max_bb_overshoot_pct),
        _kv("Strategy Parameters (Immutable Baseline)", "Min_HA_Body_Pct", cfg.baseline.min_ha_body_pct),
        _kv("Strategy Parameters (Immutable Baseline)", "Price_Basis", result.price_basis),
        # --- Methodology ---
        _kv("Methodology", "Notes", METHODOLOGY_STATEMENT),
    ]

    return pd.DataFrame(rows)
