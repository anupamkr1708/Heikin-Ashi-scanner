"""Scan pipeline: universe -> data -> features -> signal -> report (PART 55 / PART 66 gates).

One bad symbol never halts the run (Gate 4 in PART 66) — every per-symbol failure is caught,
logged, and recorded; only a UNIVERSE-level failure (Gate 1) or a systemic data-provider failure
propagates and stops the run.

**Audit note (this revision):** a real run against live NSE data (single-session bootstrap only)
surfaced several bugs fixed here:
  - `Diagnostics` previously only contained rows that PASSED the mandatory signal. Every
    successfully-processed universe member now gets a diagnostic row regardless of outcome
    (PASS / FAIL / INSUFFICIENT_HISTORY / STALE / MISSING / INVALID).
  - Failure accounting is now split into `data_validation_failures` (insufficient history, bad
    OHLC, no data — expected/routine, especially before historical bootstrap has run) vs.
    `security_scan_failures` (an unexpected exception or a sanity-gate failure — a real bug
    signal, should be rare). The previous `Mapping_OK = universe_count - len(scan_log)`
    computation was invalid (conflated failure types and could go negative) and is removed.
  - `RUN_HEALTH` no longer goes RED purely because the benchmark is unavailable — the baseline
    BB+HA signal does not need a benchmark. RED is now reserved for the baseline scan itself
    being untrustworthy (universe empty, or too high a baseline failure rate); a missing
    benchmark alone caps the run at YELLOW.
  - The reported `price_basis` now reflects what the data provider actually returned
    (`data_provider.price_basis`), not a blindly-copied config default — the NSE bhavcopy path
    is RAW, not ADJUSTED, and the manifest/Parameters sheet must say so.
  - Per-symbol "insufficient history" failures (the expected, routine case on a freshly
    bootstrapped or not-yet-bootstrapped machine) log at DEBUG, not WARNING — one aggregate
    summary line covers them so 200 near-identical WARNING lines don't bury a real problem.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime

import pandas as pd

from nse_scanner.config import ScannerConfig
from nse_scanner.data.base import BenchmarkProvider, DataProvider, UniverseProvider
from nse_scanner.data.calendar import DataStatus, SessionInfo, classify_data_status
from nse_scanner.data.validation import DataValidationError, validate_and_clean_ohlc
from nse_scanner.exceptions import SignalMathError
from nse_scanner.indicators.relative_strength import calculate_index_features
from nse_scanner.indicators.sanity import check_bollinger_ordering, check_heikin_ashi_ordering, check_pass_row
from nse_scanner.logging_config import get_logger, log_failure
from nse_scanner.pipeline.features import build_feature_frame
from nse_scanner.reporting.excel import ReportSheets
from nse_scanner.reporting.signal_reason import build_signal_reason
from nse_scanner.strategy.filters import apply_optional_filters
from nse_scanner.strategy.ranking import calculate_research_heuristic_score

logger = get_logger(__name__)

HISTORY_SUFFICIENT = "SUFFICIENT"
HISTORY_INSUFFICIENT = "INSUFFICIENT_HISTORY"
HISTORY_NO_DATA = "NO_DATA"

FAILURE_DATA_VALIDATION = "data_validation_failure"   # routine: insufficient history, bad OHLC, no data
FAILURE_SECURITY_SCAN = "security_scan_failure"        # unexpected: sanity-gate trip or other bug


@dataclass
class ScanRunResult:
    run_id: str
    sheets: ReportSheets
    session: SessionInfo
    universe_source: str
    universe_retrieved_at: str
    constituent_count: int
    benchmark_status: str
    price_basis: str
    signals_current: int
    signals_stale: int
    run_health: str
    data_validation_failures: int = 0
    security_scan_failures: int = 0
    insufficient_history_count: int = 0
    needs_bootstrap: bool = False
    failures: list[dict] = field(default_factory=list)


def _diagnostic_row(symbol: str, rec, **overrides) -> dict:
    row = {
        "Symbol": symbol,
        "Company_Name": getattr(rec, "Company_Name", symbol),
        "Sector": getattr(rec, "Sector", None),
        "Rows": None,
        "History_Status": None,
        "Data_Status": DataStatus.UNAVAILABLE,
        "History_Quality": None,
        "Price_Basis": None,
        "BB_Breakout": None,
        "BB_Size_OK": None,
        "HA_Strength_OK": None,
        "Primary_Signal": False,
        "Optional_Filter_Status": None,
        "Failure_Category": None,
        "Failure_Reason": None,
        "Signal_Reason": None,
    }
    row.update(overrides)
    return row


def run_scan(cfg: ScannerConfig, universe_provider: UniverseProvider, data_provider: DataProvider,
             benchmark_provider: BenchmarkProvider | None, session: SessionInfo,
             holidays: set, symbol_override_path: str | None = None) -> ScanRunResult:
    run_id = str(uuid.uuid4())
    scan_log_rows: list[dict] = []

    # --- Gate 1: universe ---
    constituents, universe_source, retrieved_at = universe_provider.get_constituents()  # raises on failure

    # symbol_override_path is accepted for forward-compatibility with a yfinance-fallback data
    # path (PART 4/10) that would need NSE->Yahoo symbol mapping; the current scan pipeline talks
    # to NSEDataProvider using NSE symbols directly, so no mapping is applied here yet.

    price_basis = getattr(data_provider, "price_basis", cfg.data.price_basis)
    observed_price_bases: set[str] = set()

    # --- benchmark (isolated path — BUG 3/16). Benchmark failure is recorded separately from
    # baseline scan failures and never counted toward RUN_HEALTH's RED threshold (see below). ---
    index_features = None
    benchmark_status = "UNAVAILABLE"
    if benchmark_provider is not None:
        idx_df, err = benchmark_provider.fetch_benchmark(
            cfg.benchmark.index_ticker, period=cfg.research.live_history_period, interval=cfg.data.interval,
        )
        if idx_df is not None:
            index_features = calculate_index_features(
                idx_df, cfg.history.min_rows_sma20, cfg.history.min_rows_sma50, cfg.history.min_rows_sma200,
            )
            benchmark_status = "OK"
        else:
            logger.warning("Benchmark unavailable (baseline scan is unaffected): %s", err)
            scan_log_rows.append({"security": cfg.benchmark.index_ticker, "stage": "benchmark",
                                   "provider": type(benchmark_provider).__name__,
                                   "failure_category": "benchmark_failure", "exception_type": "BenchmarkError",
                                   "exception_message": err or "unknown", "timestamp": datetime.now().isoformat()})

    signal_rows: list[dict] = []
    diagnostics_rows: list[dict] = []
    data_validation_failures = 0
    security_scan_failures = 0
    insufficient_history_count = 0

    for rec in constituents.itertuples(index=False):
        symbol = rec.Symbol
        try:
            df_raw, meta, err = data_provider.fetch_history(
                symbol, cfg.research.live_history_period, cfg.data.interval
            )
            if df_raw is None:
                raise DataValidationError(err or "no data returned")
            symbol_price_basis = meta.price_basis if meta is not None else price_basis
            observed_price_bases.add(symbol_price_basis)

            try:
                df_clean, val_report = validate_and_clean_ohlc(df_raw, cfg.history.min_rows_primary)
            except DataValidationError as e:
                rows_available = len(df_raw) if df_raw is not None else 0
                history_status = HISTORY_INSUFFICIENT if rows_available > 0 else HISTORY_NO_DATA
                insufficient_history_count += 1
                data_validation_failures += 1
                diagnostics_rows.append(_diagnostic_row(
                    symbol, rec, Rows=rows_available, History_Status=history_status,
                    Failure_Category=FAILURE_DATA_VALIDATION, Failure_Reason=str(e),
                ))
                # Routine/expected (especially pre-bootstrap) — DEBUG only, not WARNING. See
                # the aggregate summary line logged after the loop.
                logger.debug("security=%s stage=validation rows=%d reason=%s", symbol, rows_available, e)
                continue

            last_bar_date = df_clean.index[-1]
            last_bar_date_d = last_bar_date.date() if hasattr(last_bar_date, "date") else last_bar_date
            data_status = classify_data_status(last_bar_date_d, session.expected_completed_session, holidays)

            feat = build_feature_frame(df_clean, cfg, index_features)
            row = feat.iloc[-1]
            prev_row = feat.iloc[-2] if len(feat) >= 2 else None

            check_bollinger_ordering(row["BB_Upper"], row["BB_Middle"], row["BB_Lower"])
            check_heikin_ashi_ordering(row["HA_High"], row["HA_Low"], row["HA_Open"], row["HA_Close"])

            bb_breakout = bool(row["Close"] > row["BB_Upper"])
            bb_size_ok = bool(0 < row["BB_Overshoot_Pct"] <= cfg.baseline.max_bb_overshoot_pct)
            ha_strength_ok = bool(row["HA_Body_Pct"] >= cfg.baseline.min_ha_body_pct)
            mandatory_pass = bb_breakout and bb_size_ok and ha_strength_ok

            if not mandatory_pass:
                diagnostics_rows.append(_diagnostic_row(
                    symbol, rec, Rows=len(df_clean), History_Status=HISTORY_SUFFICIENT,
                    Data_Status=data_status, History_Quality=row.get("History_Quality"),
                    BB_Breakout=bb_breakout, BB_Size_OK=bb_size_ok, HA_Strength_OK=ha_strength_ok,
                    Primary_Signal=False, Price_Basis=symbol_price_basis,
                ))
                continue

            check_pass_row(row["Close"], row["BB_Upper"], row["BB_Overshoot_Pct"], row["HA_Body_Pct"],
                            cfg.baseline.max_bb_overshoot_pct, cfg.baseline.min_ha_body_pct)

            filter_result = apply_optional_filters(row, prev_row, cfg.filters)
            score_result = calculate_research_heuristic_score(row, benchmark_available=(benchmark_status == "OK"))

            out_row = {
                "Signal_Date": last_bar_date_d.isoformat(),
                "Symbol": symbol, "Stock": symbol,
                "Company_Name": getattr(rec, "Company_Name", symbol),
                "Sector": getattr(rec, "Sector", None),
                "CMP": row["Close"],
                "BB_Middle": row["BB_Middle"], "BB_Upper": row["BB_Upper"], "BB_Lower": row["BB_Lower"],
                "BB_StdDev": row["BB_StdDev"], "BB_Width_Pct": row["BB_Width_Pct"], "BB_PctB": row["BB_PctB"],
                "BB_Overshoot_Pct": row["BB_Overshoot_Pct"],
                "HA_Open": row["HA_Open"], "HA_Close": row["HA_Close"], "HA_Body_Pct": row["HA_Body_Pct"],
                "ATR14": row.get(f"ATR{cfg.history.atr_period}"), "ATR_Pct": row["ATR_Pct"],
                "BB_Overshoot_ATR": row["BB_Overshoot_ATR"],
                "Volume": row["Volume"], "Volume_Ratio_20": row.get("Volume_Ratio_20"),
                "Dollar_Volume": row.get("Dollar_Volume"),
                "SMA20": row.get("SMA20"), "SMA50": row.get("SMA50"), "SMA200": row.get("SMA200"),
                "Dist_SMA50_Pct": row.get("Dist_SMA50_Pct"), "Dist_SMA200_Pct": row.get("Dist_SMA200_Pct"),
                "RS_20D": row.get("RS_20D"), "RS_60D": row.get("RS_60D"), "RS_120D": row.get("RS_120D"),
                "Breakout_Type": row.get("Breakout_Type"), "Days_Above_Upper_BB": row.get("Days_Above_Upper_BB"),
                "Market_Regime": row.get("Market_Regime"),
                "Rows": len(df_clean), "History_Status": HISTORY_SUFFICIENT,
                "Data_Status": data_status, "History_Quality": row.get("History_Quality"),
                "Benchmark_Status": benchmark_status, "Score_Status": score_result.status,
                "Research_Heuristic_Score": score_result.score,
                "Price_Basis": symbol_price_basis,
                "BB_Breakout": bb_breakout, "BB_Size_OK": bb_size_ok, "HA_Strength_OK": ha_strength_ok,
                "Primary_Signal": True, "Optional_Filter_Status": filter_result.optional_pass,
                "Optional_Filters_Passed": filter_result.optional_pass,
                "Failure_Category": None, "Failure_Reason": None,
                "Signal_Reason": build_signal_reason(row),
            }
            diagnostics_rows.append(out_row)
            if data_status == DataStatus.CURRENT and filter_result.optional_pass:
                signal_rows.append(out_row)

        except SignalMathError as e:
            # A sanity-gate trip is a genuine bug signal (indicator math and its own row
            # disagree), never routine — always logged loudly and bucketed separately from
            # ordinary data-validation failures.
            security_scan_failures += 1
            log_failure(logger, run_id=run_id, security=symbol, stage="sanity_gate",
                        provider=data_provider.name, exc=e)
            diagnostics_rows.append(_diagnostic_row(
                symbol, rec, Failure_Category=FAILURE_SECURITY_SCAN, Failure_Reason=str(e),
            ))
            scan_log_rows.append({"security": symbol, "stage": "sanity_gate", "provider": data_provider.name,
                                   "failure_category": FAILURE_SECURITY_SCAN,
                                   "exception_type": type(e).__name__, "exception_message": str(e),
                                   "timestamp": datetime.now().isoformat()})

        except Exception as e:  # noqa: BLE001 - per-symbol isolation is the point (PART 15)
            security_scan_failures += 1
            log_failure(logger, run_id=run_id, security=symbol, stage="scan", provider=data_provider.name, exc=e)
            diagnostics_rows.append(_diagnostic_row(
                symbol, rec, Failure_Category=FAILURE_SECURITY_SCAN, Failure_Reason=str(e),
            ))
            scan_log_rows.append({"security": symbol, "stage": "scan", "provider": data_provider.name,
                                   "failure_category": FAILURE_SECURITY_SCAN,
                                   "exception_type": type(e).__name__, "exception_message": str(e),
                                   "timestamp": datetime.now().isoformat()})

    if insufficient_history_count:
        logger.info(
            "%d/%d universe symbols skipped: insufficient local history (< %d rows). "
            "Run scripts/bootstrap_history.py if this is a freshly-installed database.",
            insufficient_history_count, len(constituents), cfg.history.min_rows_primary,
        )

    # Summarize the price basis actually observed across the universe (PART: "price basis
    # metadata matches reality") rather than reporting the provider's static default label.
    if len(observed_price_bases) == 1:
        price_basis = next(iter(observed_price_bases))
    elif len(observed_price_bases) > 1:
        price_basis = "MIXED_SEE_DIAGNOSTICS"
    # else: no symbol was successfully fetched at all — keep the provider's static default.

    diagnostics_df = pd.DataFrame(diagnostics_rows)
    signals_df = pd.DataFrame(signal_rows)
    if not signals_df.empty:
        signals_df = signals_df.sort_values("Research_Heuristic_Score", ascending=False, na_position="last")
        signals_df.insert(0, "Rank", range(1, len(signals_df) + 1))

    stale_df = pd.DataFrame()
    if not diagnostics_df.empty:
        stale_df = diagnostics_df[
            diagnostics_df["Data_Status"].isin([DataStatus.STALE_1_SESSION, DataStatus.STALE_2_PLUS])
            & diagnostics_df["Primary_Signal"]
        ].copy()

    universe_count = len(constituents)
    n_current = int((diagnostics_df["Data_Status"] == DataStatus.CURRENT).sum()) if not diagnostics_df.empty else 0
    n_stale = int((diagnostics_df["Data_Status"].isin(
        [DataStatus.STALE_1_SESSION, DataStatus.STALE_2_PLUS])).sum()) if not diagnostics_df.empty else 0

    # RUN_HEALTH is driven ONLY by whether the baseline BB+HA scan itself is trustworthy — the
    # baseline does not require a benchmark. Benchmark unavailability can only cap the result at
    # YELLOW, never push it to RED on its own.
    baseline_failures = data_validation_failures + security_scan_failures
    baseline_failure_rate = baseline_failures / max(universe_count, 1)
    needs_bootstrap = insufficient_history_count >= 0.5 * max(universe_count, 1)

    if universe_count == 0 or baseline_failure_rate > 0.25:
        run_health = "RED"
    elif benchmark_status != "OK" or baseline_failure_rate > 0.05 or security_scan_failures > 0:
        run_health = "YELLOW"
    else:
        run_health = "GREEN"

    expected_session_str = (
        session.expected_completed_session.isoformat() if session.expected_completed_session else None
    )
    data_health_df = pd.DataFrame([{
        "Run_Date": session.as_of_date.isoformat(),
        "Expected_Session": expected_session_str,
        "Universe_Status": "VALID",
        "Universe_Count": universe_count,
        "Data_Current": n_current,
        "Data_Stale": n_stale,
        "Data_Validation_Failures": data_validation_failures,
        "Security_Scan_Failures": security_scan_failures,
        "Insufficient_History_Count": insufficient_history_count,
        "Needs_Bootstrap": needs_bootstrap,
        "Benchmark_Status": benchmark_status,
        "Price_Basis": price_basis,
        "Signal_Count": len(signals_df),
        "RUN_HEALTH": run_health,
    }])

    parameters_df = pd.DataFrame([{
        "strategy_id": cfg.baseline.strategy_id, "bb_period": cfg.baseline.bb_period,
        "bb_std_mult": cfg.baseline.bb_std_mult, "bb_ddof": cfg.baseline.bb_ddof,
        "max_bb_overshoot_pct": cfg.baseline.max_bb_overshoot_pct, "min_ha_body_pct": cfg.baseline.min_ha_body_pct,
        "atr_period": cfg.history.atr_period, "universe_scope": cfg.universe.universe_scope,
        "entry_price_method": cfg.research.entry_price_method, "price_basis": price_basis,
        "data_provider": data_provider.name,
    }])

    universe_df = constituents.copy()
    scan_log_df = pd.DataFrame(scan_log_rows)

    market_regime_rows = []
    if index_features is not None and not index_features.empty:
        last_idx = index_features.iloc[-1]
        market_regime_rows.append({
            "Index": cfg.benchmark.index_name, "Index_Close": last_idx["Index_Close"],
            "Index_SMA50": last_idx["Index_SMA50"], "Index_SMA200": last_idx["Index_SMA200"],
            "Market_Regime": last_idx["Market_Regime"],
        })
    market_regime_df = pd.DataFrame(market_regime_rows)

    sheets = ReportSheets(
        live_signals=signals_df, stale_signals=stale_df, diagnostics=diagnostics_df,
        data_health=data_health_df, scan_log=scan_log_df, universe=universe_df, parameters=parameters_df,
        market_regime=market_regime_df, research_summary=pd.DataFrame(), forward_returns=pd.DataFrame(),
        sensitivity=pd.DataFrame(),
    )

    return ScanRunResult(
        run_id=run_id, sheets=sheets, session=session, universe_source=universe_source,
        universe_retrieved_at=retrieved_at, constituent_count=universe_count, benchmark_status=benchmark_status,
        price_basis=price_basis, signals_current=len(signals_df), signals_stale=n_stale, run_health=run_health,
        data_validation_failures=data_validation_failures, security_scan_failures=security_scan_failures,
        insufficient_history_count=insufficient_history_count, needs_bootstrap=needs_bootstrap,
        failures=scan_log_rows,
    )
