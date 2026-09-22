"""Trend structure: SMA20/50/200, distance-from-SMA, slopes.

Tiered availability (PART 16 / BUG 6): a security is never rejected merely because it lacks
enough history for SMA200. Each moving average reports its own status; the primary BB+HA signal
does not depend on any of these.
"""

from __future__ import annotations

import pandas as pd

UNAVAILABLE = "UNAVAILABLE_INSUFFICIENT_HISTORY"
OK = "OK"


def tiered_sma(close: pd.Series, window: int, min_rows_needed: int) -> tuple[pd.Series, str]:
    sma = close.rolling(window=window, min_periods=window).mean()
    status = OK if len(close) >= min_rows_needed else UNAVAILABLE
    return sma, status


def calculate_trend_structure(df: pd.DataFrame, min_rows_sma20: int = 20, min_rows_sma50: int = 50,
                               min_rows_sma200: int = 200) -> pd.DataFrame:
    close = df["Close"]
    ts = pd.DataFrame(index=df.index)

    ts["SMA20"], sma20_status = tiered_sma(close, 20, min_rows_sma20)
    ts["SMA50"], sma50_status = tiered_sma(close, 50, min_rows_sma50)
    ts["SMA200"], sma200_status = tiered_sma(close, 200, min_rows_sma200)
    ts["SMA20_Status"] = sma20_status
    ts["SMA50_Status"] = sma50_status
    ts["SMA200_Status"] = sma200_status

    ts["Dist_SMA50_Pct"] = (close - ts["SMA50"]) / ts["SMA50"] * 100.0
    ts["Dist_SMA200_Pct"] = (close - ts["SMA200"]) / ts["SMA200"] * 100.0

    ts["SMA20_Slope_10D"] = (ts["SMA20"] - ts["SMA20"].shift(10)) / ts["SMA20"].shift(10) * 100.0
    ts["SMA50_Slope_20D"] = (ts["SMA50"] - ts["SMA50"].shift(20)) / ts["SMA50"].shift(20) * 100.0
    ts["SMA200_Slope_20D"] = (ts["SMA200"] - ts["SMA200"].shift(20)) / ts["SMA200"].shift(20) * 100.0

    return ts


def history_quality(n_rows: int, min_rows_sma200: int = 200, min_rows_sma50: int = 50) -> str:
    """VERY_LIMITED / LIMITED / FULL — used to caveat HA-seed dependence and SMA availability
    (PART 16). This never gates the primary signal; it is a reporting label only."""
    if n_rows < min_rows_sma50:
        return "VERY_LIMITED"
    if n_rows < min_rows_sma200:
        return "LIMITED"
    return "FULL"
