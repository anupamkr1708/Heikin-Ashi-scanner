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

FAILURE_DATA_VALIDATION = "data_validation_failure"  # routine: insufficient history, bad OHLC, no data
FAILURE_SECURITY_SCAN = "security_scan_failure"  # unexpected: sanity-gate trip or other bug

# --- client-reporting-hardening: explicit per-symbol diagnostic vocabulary (Tasks 2-4) ---
# `Failure_Category`/`Failure_Reason` above are the pre-existing coarse/free-text pair (kept
# byte-for-byte compatible — tests/integration/test_diagnostics_and_run_health.py depends on
# their exact values). `Primary_Strategy_Status` / `Primary_Failure_Stage` /
# `Primary_Failure_Reason` below are new, additive, structured fields for exactly WHY a symbol
# did or didn't produce a signal — distinct from "was there a data problem".
STRATEGY_STATUS_EVALUATED = "EVALUATED"  # the baseline BB+HA conditions were actually checked
STRATEGY_STATUS_NOT_EVALUABLE = "NOT_EVALUABLE"  # never reached the strategy check at all

STAGE_DATA_VALIDATION = "data_validation"
STAGE_STRATEGY_GATE = "strategy_gate"  # the immutable mandatory BB+HA condition itself
STAGE_SANITY_GATE = "sanity_gate"
STAGE_SCAN = "scan"

REASON_NOT_EVALUABLE_NO_DATA = "NOT_EVALUABLE_NO_DATA"
REASON_NOT_EVALUABLE_INSUFFICIENT_HISTORY = "NOT_EVALUABLE_INSUFFICIENT_HISTORY"
REASON_DATA_VALIDATION_FAILURE = "DATA_VALIDATION_FAILURE"  # rows existed but failed OHLC quality
# checks (bad ordering, non-positive prices, ...) for a reason OTHER than plain row-count
REASON_CLOSE_NOT_ABOVE_UPPER_BB = "CLOSE_NOT_ABOVE_UPPER_BB"
REASON_BB_OVERSHOOT_OUT_OF_RANGE = "BB_OVERSHOOT_OUT_OF_RANGE"
REASON_HA_BODY_TOO_SMALL = "HA_BODY_TOO_SMALL"
REASON_SANITY_GATE_FAILURE = "SANITY_GATE_FAILURE"
REASON_SECURITY_SCAN_FAILURE = "SECURITY_SCAN_FAILURE"


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
    """Canonical Diagnostics-row shape (Task 2). EVERY successfully-processed universe member
    gets exactly one row from this function, whatever happened to it — PASS, mandatory-condition
    FAIL, sanity-gate failure, or NOT_EVALUABLE (no/insufficient data). Every diagnostic/feature
    field below defaults to None; a caller overrides ONLY the fields it actually has a real,
    already-computed value for — nothing here is ever invented or recalculated (Task 2: 'preserve
    the exact values already used by the strategy calculation... do NOT recalculate with
    alternative formulas'). A field left at None therefore means exactly what Task 4 asks for:
    'this feature genuinely does not exist for this row', not a silently dropped value."""
    row = {
        "Symbol": symbol,
        "Company_Name": getattr(rec, "Company_Name", symbol),
        "Sector": getattr(rec, "Sector", None),
        "Rows": None,
        "History_Status": None,
        "Data_Status": DataStatus.UNAVAILABLE,
        "History_Quality": None,
        "Price_Basis": None,
        "Signal_Date": None,
        "Stock": None,
        "CMP": None,
        "BB_Middle": None,
        "BB_Upper": None,
        "BB_Lower": None,
        "BB_StdDev": None,
        "BB_Width_Pct": None,
        "BB_PctB": None,
        "BB_Overshoot_Pct": None,
        "HA_Open": None,
        "HA_Close": None,
        "HA_Body_Pct": None,
        "ATR14": None,
        "ATR_Pct": None,
        "BB_Overshoot_ATR": None,
        "Volume": None,
        "Volume_Ratio_20": None,
        "Dollar_Volume": None,
        "SMA20": None,
        "SMA50": None,
        "SMA200": None,
        "SMA20_Status": None,
        "SMA50_Status": None,
        "SMA200_Status": None,
        "Dist_SMA50_Pct": None,
        "Dist_SMA200_Pct": None,
        "RS_20D": None,
        "RS_60D": None,
        "RS_120D": None,
        "Breakout_Type": None,
        "Days_Above_Upper_BB": None,
        "Market_Regime": None,
        "Benchmark_Status": None,
        "Score_Status": None,
        "Research_Heuristic_Score": None,
        "BB_Breakout": None,
        "BB_Size_OK": None,
        "HA_Strength_OK": None,
        "Primary_Strategy_Status": STRATEGY_STATUS_NOT_EVALUABLE,
        "Primary_Signal": False,
        "Primary_Failure_Stage": None,
        "Primary_Failure_Reason": None,
        "Optional_Filter_Status": None,
        "Optional_Filters_Passed": None,
        "Failure_Category": None,
        "Failure_Reason": None,
        "Signal_Reason": None,
    }
    row.update(overrides)
    return row


def _feature_fields(
    symbol: str,
    row: pd.Series,
    last_bar_date_d,
    benchmark_status: str,
    score_result,
    atr_period: int,
) -> dict:
    """Every field that is a direct, unmodified read of the strategy's own already-computed
    feature row — the ONE place this happens, shared by the PASS path, the mandatory-condition
    FAIL path, and any sanity-gate-failure path that got far enough to have a feature row at all.
    Never recomputes anything (Task 2)."""
    return {
        "Signal_Date": last_bar_date_d.isoformat() if last_bar_date_d is not None else None,
        "Stock": symbol,
        "History_Quality": row.get("History_Quality"),
        "CMP": row["Close"],
        "BB_Middle": row["BB_Middle"],
        "BB_Upper": row["BB_Upper"],
        "BB_Lower": row["BB_Lower"],
        "BB_StdDev": row["BB_StdDev"],
        "BB_Width_Pct": row["BB_Width_Pct"],
        "BB_PctB": row["BB_PctB"],
        "BB_Overshoot_Pct": row["BB_Overshoot_Pct"],
        "HA_Open": row["HA_Open"],
        "HA_Close": row["HA_Close"],
        "HA_Body_Pct": row["HA_Body_Pct"],
        "ATR14": row.get(f"ATR{atr_period}"),
        "ATR_Pct": row["ATR_Pct"],
        "BB_Overshoot_ATR": row["BB_Overshoot_ATR"],
        "Volume": row["Volume"],
        "Volume_Ratio_20": row.get("Volume_Ratio_20"),
        "Dollar_Volume": row.get("Dollar_Volume"),
        "SMA20": row.get("SMA20"),
        "SMA50": row.get("SMA50"),
        "SMA200": row.get("SMA200"),
        "SMA20_Status": row.get("SMA20_Status"),
        "SMA50_Status": row.get("SMA50_Status"),
        "SMA200_Status": row.get("SMA200_Status"),
        "Dist_SMA50_Pct": row.get("Dist_SMA50_Pct"),
        "Dist_SMA200_Pct": row.get("Dist_SMA200_Pct"),
        "RS_20D": row.get("RS_20D"),
        "RS_60D": row.get("RS_60D"),
        "RS_120D": row.get("RS_120D"),
        "Breakout_Type": row.get("Breakout_Type"),
        "Days_Above_Upper_BB": row.get("Days_Above_Upper_BB"),
        "Market_Regime": row.get("Market_Regime"),
        "Benchmark_Status": benchmark_status,
        "Score_Status": score_result.status if score_result is not None else None,
        "Research_Heuristic_Score": score_result.score if score_result is not None else None,
    }


def _strategy_gate_failure_reason(bb_breakout: bool, bb_size_ok: bool, ha_strength_ok: bool) -> str:
    """Priority order mirrors the mandatory AND clause itself (Close > Upper_BB, then the
    overshoot bound, then the HA body threshold) so a row failing more than one condition at
    once still gets ONE deterministic, meaningful primary reason (Task 3) rather than an
    arbitrary one."""
    if not bb_breakout:
        return REASON_CLOSE_NOT_ABOVE_UPPER_BB
    if not bb_size_ok:
        return REASON_BB_OVERSHOOT_OUT_OF_RANGE
    return REASON_HA_BODY_TOO_SMALL


def run_scan(
    cfg: ScannerConfig,
    universe_provider: UniverseProvider,
    data_provider: DataProvider,
    benchmark_provider: BenchmarkProvider | None,
    session: SessionInfo,
    holidays: set,
    symbol_override_path: str | None = None,
) -> ScanRunResult:
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
            cfg.benchmark.index_ticker,
            period=cfg.research.live_history_period,
            interval=cfg.data.interval,
        )
        if idx_df is not None:
            index_features = calculate_index_features(
                idx_df,
                cfg.history.min_rows_sma20,
                cfg.history.min_rows_sma50,
                cfg.history.min_rows_sma200,
            )
            benchmark_status = "OK"
        else:
            logger.warning("Benchmark unavailable (baseline scan is unaffected): %s", err)
            scan_log_rows.append(
                {
                    "security": cfg.benchmark.index_ticker,
                    "stage": "benchmark",
                    "provider": type(benchmark_provider).__name__,
                    "failure_category": "benchmark_failure",
                    "exception_type": "BenchmarkError",
                    "exception_message": err or "unknown",
                    "timestamp": datetime.now().isoformat(),
                }
            )

    signal_rows: list[dict] = []
    diagnostics_rows: list[dict] = []
    data_validation_failures = 0
    security_scan_failures = 0
    insufficient_history_count = 0

    for rec in constituents.itertuples(index=False):
        symbol = rec.Symbol
        # Bound in the outer function scope before the try starts so that EVERY except branch
        # below can safely reference "however far we got" (Task 4: never hide partial feature
        # availability) instead of only being able to report a bare exception message.
        row = None
        prev_row = None
        df_clean = None
        data_status = None
        symbol_price_basis = None
        last_bar_date_d = None
        bb_breakout = None
        bb_size_ok = None
        ha_strength_ok = None
        filter_result = None
        score_result = None
        try:
            df_raw, meta, err = data_provider.fetch_history(symbol, cfg.research.live_history_period, cfg.data.interval)

            try:
                if df_raw is None:
                    raise DataValidationError(err or "no data returned", reason=DataValidationError.REASON_NO_DATA)
                symbol_price_basis = meta.price_basis if meta is not None else price_basis
                observed_price_bases.add(symbol_price_basis)
                df_clean, val_report = validate_and_clean_ohlc(df_raw, cfg.history.min_rows_primary)
            except DataValidationError as e:
                # Task 3: distinguish "no data at all", "rows existed but too few after
                # cleaning" (routine, especially pre-bootstrap), and "rows existed but failed a
                # genuine OHLC quality check" (missing columns, bad ordering, etc. — see
                # data/validation.py) — these used to be collapsed into one binary check.
                #
                # Review-round-1 fix: classification is now driven by DataValidationError.reason
                # -- a STRUCTURED category each raise site in data/validation.py sets explicitly
                # -- rather than substring-matching str(e), which was fragile to wording changes.
                # `rows_available == 0` is kept only as a defensive fallback for any future/
                # third-party raiser of DataValidationError that doesn't set `reason` at all
                # (e.g. a bare DataValidationError raised outside this codebase's control) — it
                # is a structural measurement, not text-matching, so it doesn't reintroduce the
                # brittleness being removed here.
                rows_available = len(df_raw) if df_raw is not None else 0
                reason = getattr(e, "reason", None)
                if reason == DataValidationError.REASON_NO_DATA or rows_available == 0:
                    history_status = HISTORY_NO_DATA
                    primary_failure_reason = REASON_NOT_EVALUABLE_NO_DATA
                elif reason == DataValidationError.REASON_INSUFFICIENT_ROWS:
                    history_status = HISTORY_INSUFFICIENT
                    primary_failure_reason = REASON_NOT_EVALUABLE_INSUFFICIENT_HISTORY
                    # Review-round-1 fix: this counter must reflect ONLY the true
                    # insufficient-history population, not every DataValidationError. A symbol
                    # with no data at all, or one that failed a genuine OHLC quality check
                    # (missing columns, bad ordering that drops every row), is NOT a member of
                    # "insufficient history" and must not inflate that count -- both are still
                    # counted in the data_validation_failures umbrella below, just not here.
                    insufficient_history_count += 1
                else:
                    # reason is REASON_MISSING_COLUMNS, or None (defensive fallback) -- a genuine
                    # data-quality problem distinct from a plain row-count shortfall.
                    history_status = HISTORY_INSUFFICIENT
                    primary_failure_reason = REASON_DATA_VALIDATION_FAILURE
                data_validation_failures += 1
                diagnostics_rows.append(
                    _diagnostic_row(
                        symbol,
                        rec,
                        Rows=rows_available,
                        History_Status=history_status,
                        Primary_Failure_Stage=STAGE_DATA_VALIDATION,
                        Primary_Failure_Reason=primary_failure_reason,
                        Failure_Category=FAILURE_DATA_VALIDATION,
                        Failure_Reason=str(e),
                    )
                )
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

            bb_breakout = bool(row["Close"] > row["BB_Upper"])
            bb_size_ok = bool(0 < row["BB_Overshoot_Pct"] <= cfg.baseline.max_bb_overshoot_pct)
            ha_strength_ok = bool(row["HA_Body_Pct"] >= cfg.baseline.min_ha_body_pct)
            mandatory_pass = bb_breakout and bb_size_ok and ha_strength_ok

            # Pure, side-effect-free reads of row/prev_row — computed here (before the sanity
            # gates below) rather than only inside the PASS branch, so (a) a sanity-gate failure
            # still has them available for its diagnostic row, and (b) mandatory-condition FAIL
            # rows get them too, per Task 2's explicit Diagnostics field list. This changes
            # nothing about VALUES for any row that used to compute them (identical inputs,
            # earlier call site) — only which rows now have them.
            filter_result = apply_optional_filters(row, prev_row, cfg.filters)
            score_result = calculate_research_heuristic_score(row, benchmark_available=(benchmark_status == "OK"))

            check_bollinger_ordering(row["BB_Upper"], row["BB_Middle"], row["BB_Lower"])
            check_heikin_ashi_ordering(row["HA_High"], row["HA_Low"], row["HA_Open"], row["HA_Close"])

            if not mandatory_pass:
                full_row = _diagnostic_row(
                    symbol,
                    rec,
                    Rows=len(df_clean),
                    History_Status=HISTORY_SUFFICIENT,
                    Data_Status=data_status,
                    Price_Basis=symbol_price_basis,
                    BB_Breakout=bb_breakout,
                    BB_Size_OK=bb_size_ok,
                    HA_Strength_OK=ha_strength_ok,
                    Primary_Strategy_Status=STRATEGY_STATUS_EVALUATED,
                    Primary_Signal=False,
                    Primary_Failure_Stage=STAGE_STRATEGY_GATE,
                    Primary_Failure_Reason=_strategy_gate_failure_reason(bb_breakout, bb_size_ok, ha_strength_ok),
                    Optional_Filter_Status=filter_result.optional_pass,
                    Optional_Filters_Passed=filter_result.optional_pass,
                    **_feature_fields(
                        symbol, row, last_bar_date_d, benchmark_status, score_result, cfg.history.atr_period
                    ),
                )
                diagnostics_rows.append(full_row)
                continue

            check_pass_row(
                row["Close"],
                row["BB_Upper"],
                row["BB_Overshoot_Pct"],
                row["HA_Body_Pct"],
                cfg.baseline.max_bb_overshoot_pct,
                cfg.baseline.min_ha_body_pct,
            )

            out_row = _diagnostic_row(
                symbol,
                rec,
                Rows=len(df_clean),
                History_Status=HISTORY_SUFFICIENT,
                Data_Status=data_status,
                Price_Basis=symbol_price_basis,
                BB_Breakout=bb_breakout,
                BB_Size_OK=bb_size_ok,
                HA_Strength_OK=ha_strength_ok,
                Primary_Strategy_Status=STRATEGY_STATUS_EVALUATED,
                Primary_Signal=True,
                Primary_Failure_Stage=None,
                Primary_Failure_Reason=None,
                Optional_Filter_Status=filter_result.optional_pass,
                Optional_Filters_Passed=filter_result.optional_pass,
                Signal_Reason=build_signal_reason(row),
                **_feature_fields(symbol, row, last_bar_date_d, benchmark_status, score_result, cfg.history.atr_period),
            )
            diagnostics_rows.append(out_row)
            if data_status == DataStatus.CURRENT and filter_result.optional_pass:
                signal_rows.append(out_row)

        except SignalMathError as e:
            # A sanity-gate trip is a genuine bug signal (indicator math and its own row
            # disagree), never routine — always logged loudly and bucketed separately from
            # ordinary data-validation failures. Whatever was already computed for this symbol
            # (possibly the full feature row, possibly nothing) is still preserved below rather
            # than discarded (Task 4) — a sanity-gate trip is a reason to distrust the SIGNAL
            # decision, not a reason to hide the numbers that tripped it.
            security_scan_failures += 1
            log_failure(logger, run_id=run_id, security=symbol, stage="sanity_gate", provider=data_provider.name, exc=e)
            extra_fields = (
                _feature_fields(symbol, row, last_bar_date_d, benchmark_status, score_result, cfg.history.atr_period)
                if row is not None
                else {}
            )
            diagnostics_rows.append(
                _diagnostic_row(
                    symbol,
                    rec,
                    Rows=len(df_clean) if df_clean is not None else None,
                    History_Status=HISTORY_SUFFICIENT if df_clean is not None else None,
                    Data_Status=data_status if data_status is not None else DataStatus.UNAVAILABLE,
                    Price_Basis=symbol_price_basis,
                    BB_Breakout=bb_breakout,
                    BB_Size_OK=bb_size_ok,
                    HA_Strength_OK=ha_strength_ok,
                    # Review-round-1 fix: bb_breakout/bb_size_ok/ha_strength_ok/mandatory_pass are
                    # computed BEFORE either sanity-gate call (check_bollinger_ordering,
                    # check_heikin_ashi_ordering, check_pass_row -- see the try block above), so
                    # a SignalMathError here ALWAYS means the baseline strategy condition WAS
                    # already evaluated; it must never be reported as NOT_EVALUABLE (that status
                    # means "never reached the strategy check at all"). The condition below is
                    # written defensively against `bb_breakout is not None` (rather than just
                    # asserting EVALUATED unconditionally) so this stays correct even if a future
                    # refactor changes call order.
                    Primary_Strategy_Status=(
                        STRATEGY_STATUS_EVALUATED if bb_breakout is not None else STRATEGY_STATUS_NOT_EVALUABLE
                    ),
                    Primary_Signal=False,
                    Primary_Failure_Stage=STAGE_SANITY_GATE,
                    Primary_Failure_Reason=REASON_SANITY_GATE_FAILURE,
                    Optional_Filter_Status=filter_result.optional_pass if filter_result is not None else None,
                    Optional_Filters_Passed=filter_result.optional_pass if filter_result is not None else None,
                    Failure_Category=FAILURE_SECURITY_SCAN,
                    Failure_Reason=str(e),
                    **extra_fields,
                )
            )
            scan_log_rows.append(
                {
                    "security": symbol,
                    "stage": "sanity_gate",
                    "provider": data_provider.name,
                    "failure_category": FAILURE_SECURITY_SCAN,
                    "exception_type": type(e).__name__,
                    "exception_message": str(e),
                    "timestamp": datetime.now().isoformat(),
                }
            )

        except Exception as e:  # noqa: BLE001 - per-symbol isolation is the point (PART 15)
            security_scan_failures += 1
            log_failure(logger, run_id=run_id, security=symbol, stage="scan", provider=data_provider.name, exc=e)
            extra_fields = (
                _feature_fields(symbol, row, last_bar_date_d, benchmark_status, score_result, cfg.history.atr_period)
                if row is not None
                else {}
            )
            diagnostics_rows.append(
                _diagnostic_row(
                    symbol,
                    rec,
                    Rows=len(df_clean) if df_clean is not None else None,
                    History_Status=HISTORY_SUFFICIENT if df_clean is not None else None,
                    Data_Status=data_status if data_status is not None else DataStatus.UNAVAILABLE,
                    Price_Basis=symbol_price_basis,
                    BB_Breakout=bb_breakout,
                    BB_Size_OK=bb_size_ok,
                    HA_Strength_OK=ha_strength_ok,
                    # Review-round-1 fix: an unexpected exception can occur at any point (data
                    # fetch, feature build, or after the strategy condition already ran) -- unlike
                    # the sanity-gate handler above, here bb_breakout genuinely MAY be None, so
                    # this conditional is load-bearing, not just defensive.
                    Primary_Strategy_Status=(
                        STRATEGY_STATUS_EVALUATED if bb_breakout is not None else STRATEGY_STATUS_NOT_EVALUABLE
                    ),
                    Primary_Signal=False,
                    Primary_Failure_Stage=STAGE_SCAN,
                    Primary_Failure_Reason=REASON_SECURITY_SCAN_FAILURE,
                    Optional_Filter_Status=filter_result.optional_pass if filter_result is not None else None,
                    Optional_Filters_Passed=filter_result.optional_pass if filter_result is not None else None,
                    Failure_Category=FAILURE_SECURITY_SCAN,
                    Failure_Reason=str(e),
                    **extra_fields,
                )
            )
            scan_log_rows.append(
                {
                    "security": symbol,
                    "stage": "scan",
                    "provider": data_provider.name,
                    "failure_category": FAILURE_SECURITY_SCAN,
                    "exception_type": type(e).__name__,
                    "exception_message": str(e),
                    "timestamp": datetime.now().isoformat(),
                }
            )

    if insufficient_history_count:
        logger.info(
            "%d/%d universe symbols skipped: insufficient local history (< %d rows). "
            "Run scripts/bootstrap_history.py if this is a freshly-installed database.",
            insufficient_history_count,
            len(constituents),
            cfg.history.min_rows_primary,
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
    n_stale = (
        int((diagnostics_df["Data_Status"].isin([DataStatus.STALE_1_SESSION, DataStatus.STALE_2_PLUS])).sum())
        if not diagnostics_df.empty
        else 0
    )

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
    data_health_df = pd.DataFrame(
        [
            {
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
            }
        ]
    )

    parameters_df = pd.DataFrame(
        [
            {
                "strategy_id": cfg.baseline.strategy_id,
                "bb_period": cfg.baseline.bb_period,
                "bb_std_mult": cfg.baseline.bb_std_mult,
                "bb_ddof": cfg.baseline.bb_ddof,
                "max_bb_overshoot_pct": cfg.baseline.max_bb_overshoot_pct,
                "min_ha_body_pct": cfg.baseline.min_ha_body_pct,
                "atr_period": cfg.history.atr_period,
                "universe_scope": cfg.universe.universe_scope,
                "entry_price_method": cfg.research.entry_price_method,
                "price_basis": price_basis,
                "data_provider": data_provider.name,
            }
        ]
    )

    universe_df = constituents.copy()
    scan_log_df = pd.DataFrame(scan_log_rows)

    market_regime_rows = []
    if index_features is not None and not index_features.empty:
        last_idx = index_features.iloc[-1]
        market_regime_rows.append(
            {
                "Index": cfg.benchmark.index_name,
                "Index_Close": last_idx["Index_Close"],
                "Index_SMA50": last_idx["Index_SMA50"],
                "Index_SMA200": last_idx["Index_SMA200"],
                "Market_Regime": last_idx["Market_Regime"],
            }
        )
    market_regime_df = pd.DataFrame(market_regime_rows)

    sheets = ReportSheets(
        live_signals=signals_df,
        stale_signals=stale_df,
        diagnostics=diagnostics_df,
        data_health=data_health_df,
        scan_log=scan_log_df,
        universe=universe_df,
        parameters=parameters_df,
        market_regime=market_regime_df,
        research_summary=pd.DataFrame(),
        forward_returns=pd.DataFrame(),
        sensitivity=pd.DataFrame(),
    )

    return ScanRunResult(
        run_id=run_id,
        sheets=sheets,
        session=session,
        universe_source=universe_source,
        universe_retrieved_at=retrieved_at,
        constituent_count=universe_count,
        benchmark_status=benchmark_status,
        price_basis=price_basis,
        signals_current=len(signals_df),
        signals_stale=n_stale,
        run_health=run_health,
        data_validation_failures=data_validation_failures,
        security_scan_failures=security_scan_failures,
        insufficient_history_count=insufficient_history_count,
        needs_bootstrap=needs_bootstrap,
        failures=scan_log_rows,
    )
