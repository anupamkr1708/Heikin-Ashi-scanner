"""Optional confirmation filters (PART 26 / PART 73).

Distinction maintained throughout this package:
    SIGNAL  -> strategy/bb_ha.py mandatory conditions (never touched by this module)
    FILTER  -> an optional condition that can remove a signal, evaluated strictly AFTER the
               mandatory check. Enabling a filter never changes what the mandatory check itself
               evaluates to.
    CONTEXT -> informational fields (breakout state, regime, RS) used to understand the setup.
    RANKING -> ordering mechanism (see reporting / the Research_Heuristic_Score).

Every filter here defaults to disabled (config.OptionalFilterConfig) and each one degrades to
"not applied" rather than silently failing closed or open when its input is NaN/unavailable.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from nse_scanner.config import OptionalFilterConfig
from nse_scanner.strategy.breakout import FRESH_BREAKOUT


@dataclass(frozen=True)
class FilterResult:
    checks: dict[str, bool]
    optional_pass: bool


def apply_optional_filters(row: pd.Series, prev_row: pd.Series | None, cfg: OptionalFilterConfig) -> FilterResult:
    checks: dict[str, bool] = {}
    optional_pass = True

    def _apply(name: str, enabled: bool, ok: bool) -> None:
        nonlocal optional_pass
        if enabled:
            checks[name] = bool(ok)
            optional_pass = optional_pass and bool(ok)

    _apply("trend_sma50", cfg.use_trend_filter, pd.notna(row.get("SMA50")) and row["Close"] > row["SMA50"])
    _apply("long_trend_sma200", cfg.use_long_trend_filter, pd.notna(row.get("SMA200")) and row["Close"] > row["SMA200"])
    _apply(
        "volume_confirmation",
        cfg.use_volume_filter,
        pd.notna(row.get("Volume_Ratio_20")) and row["Volume_Ratio_20"] > 1.0,
    )

    bandwidth_ok = False
    if prev_row is not None and pd.notna(row.get("BB_Width_Pct")) and pd.notna(prev_row.get("BB_Width_Pct")):
        bandwidth_ok = row["BB_Width_Pct"] > prev_row["BB_Width_Pct"]
    _apply("bandwidth_expansion", cfg.use_bandwidth_filter, bandwidth_ok)

    _apply(
        "atr_normalized_overshoot",
        cfg.use_atr_filter,
        pd.notna(row.get("BB_Overshoot_ATR")) and row["BB_Overshoot_ATR"] <= cfg.atr_overshoot_max,
    )
    _apply(
        "candle_quality_clv",
        cfg.use_candle_quality_filter,
        pd.notna(row.get("Close_Location_Value")) and row["Close_Location_Value"] >= cfg.candle_quality_min_clv,
    )
    _apply("relative_strength", cfg.use_relative_strength_filter, pd.notna(row.get("RS_20D")) and row["RS_20D"] > 0)
    _apply("market_regime_bull", cfg.use_market_regime_filter, row.get("Market_Regime") == "BULL")
    _apply("fresh_breakout_only", cfg.use_fresh_breakout_only, row.get("Breakout_Type") == FRESH_BREAKOUT)

    return FilterResult(checks=checks, optional_pass=optional_pass)
