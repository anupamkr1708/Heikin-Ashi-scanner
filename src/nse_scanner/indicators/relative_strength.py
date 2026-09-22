"""Relative strength vs. a benchmark index, and benchmark-derived market regime.

Benchmark failures never silently degrade into a numeric score (BUG 4 / BUG 16): if no benchmark
data is available this run, RS_* and Market_Regime are explicitly "UNAVAILABLE", never NaN
silently treated as zero and never a fabricated regime label.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from nse_scanner.indicators.moving_averages import tiered_sma

UNAVAILABLE_NO_BENCHMARK = "UNAVAILABLE_NO_BENCHMARK_DATA"
UNAVAILABLE_INSUFFICIENT_HISTORY = "UNAVAILABLE_INSUFFICIENT_INDEX_HISTORY"


def calculate_index_features(index_df_clean: pd.DataFrame, min_rows_sma20: int = 20,
                              min_rows_sma50: int = 50, min_rows_sma200: int = 200) -> pd.DataFrame:
    """Benchmark-index diagnostics used for both Market Regime and Relative Strength.

    Regime rule (explicit, documented as a RESEARCH DEFINITION — PART 24 — not an objectively
    correct market regime):
        BULL    : Index_Close > Index_SMA200 AND Index_SMA50 > Index_SMA200
        BEAR    : Index_Close < Index_SMA200 AND Index_SMA50 < Index_SMA200
        NEUTRAL : anything else (once SMA200 is available)
    """
    idx = pd.DataFrame(index=index_df_clean.index)
    close = index_df_clean["Close"]
    idx["Index_Close"] = close
    idx["Index_SMA20"], _ = tiered_sma(close, 20, min_rows_sma20)
    idx["Index_SMA50"], _ = tiered_sma(close, 50, min_rows_sma50)
    idx["Index_SMA200"], _ = tiered_sma(close, 200, min_rows_sma200)
    idx["Index_Return_20D"] = (close / close.shift(20) - 1) * 100.0
    idx["Index_Return_60D"] = (close / close.shift(60) - 1) * 100.0
    idx["Index_Return_120D"] = (close / close.shift(120) - 1) * 100.0

    def _classify(row: pd.Series) -> str:
        if pd.isna(row["Index_SMA200"]):
            return UNAVAILABLE_INSUFFICIENT_HISTORY
        if row["Index_Close"] > row["Index_SMA200"] and row["Index_SMA50"] > row["Index_SMA200"]:
            return "BULL"
        if row["Index_Close"] < row["Index_SMA200"] and row["Index_SMA50"] < row["Index_SMA200"]:
            return "BEAR"
        return "NEUTRAL"

    idx["Market_Regime"] = idx.apply(_classify, axis=1)
    return idx


def calculate_relative_strength(df: pd.DataFrame, index_feat: pd.DataFrame | None) -> pd.DataFrame:
    """RS_ND = Stock_Return_ND - Index_Return_ND. NaN + explicit status when unavailable —
    never silently zero-filled (BUG 4/16)."""
    rs = pd.DataFrame(index=df.index)
    close = df["Close"]
    rs["Stock_Return_20D"] = (close / close.shift(20) - 1) * 100.0
    rs["Stock_Return_60D"] = (close / close.shift(60) - 1) * 100.0
    rs["Stock_Return_120D"] = (close / close.shift(120) - 1) * 100.0

    if index_feat is None:
        rs["Index_Return_20D"] = np.nan
        rs["Index_Return_60D"] = np.nan
        rs["Index_Return_120D"] = np.nan
        rs["RS_20D"] = np.nan
        rs["RS_60D"] = np.nan
        rs["RS_120D"] = np.nan
        rs["Market_Regime"] = UNAVAILABLE_NO_BENCHMARK
        return rs

    aligned = index_feat.reindex(df.index)
    rs["Index_Return_20D"] = aligned["Index_Return_20D"]
    rs["Index_Return_60D"] = aligned["Index_Return_60D"]
    rs["Index_Return_120D"] = aligned["Index_Return_120D"]
    rs["RS_20D"] = rs["Stock_Return_20D"] - rs["Index_Return_20D"]
    rs["RS_60D"] = rs["Stock_Return_60D"] - rs["Index_Return_60D"]
    rs["RS_120D"] = rs["Stock_Return_120D"] - rs["Index_Return_120D"]
    rs["Market_Regime"] = aligned["Market_Regime"]
    return rs
