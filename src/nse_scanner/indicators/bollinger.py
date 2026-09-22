"""Bollinger Bands engine.

Middle = SMA(Close, period)
StdDev = rolling std of Close over `period`, explicit ddof (0=population, 1=sample)
Upper  = Middle + std_mult * StdDev
Lower  = Middle - std_mult * StdDev
BB_Width_Pct = (Upper - Lower) / Middle * 100
BB_PctB = (Close - Lower) / (Upper - Lower)   -- a RATIO, not a percentage (PART 17 / BUG 7).
    %B < 0 : below lower band | 0-1 : inside bands | %B > 1 : above upper band.

This module has no knowledge of NSE, yfinance, Excel, or the strategy engine (PART 65).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

REQUIRED_COLUMNS = ("Close",)


def calculate_bollinger_bands(df: pd.DataFrame, period: int = 20, std_mult: float = 2.0,
                               ddof: int = 1) -> pd.DataFrame:
    """Returns a DataFrame indexed like `df` with columns:
    BB_Middle, BB_StdDev, BB_Upper, BB_Lower, BB_Width_Pct, BB_PctB.

    Values are NaN wherever fewer than `period` valid closes are available (no forward-fill,
    no fabrication — PART 15 "Do not interpolate market prices").
    """
    close = df["Close"]
    bb = pd.DataFrame(index=df.index)
    bb["BB_Middle"] = close.rolling(window=period, min_periods=period).mean()
    bb["BB_StdDev"] = close.rolling(window=period, min_periods=period).std(ddof=ddof)
    bb["BB_Upper"] = bb["BB_Middle"] + std_mult * bb["BB_StdDev"]
    bb["BB_Lower"] = bb["BB_Middle"] - std_mult * bb["BB_StdDev"]
    bb["BB_Width_Pct"] = (bb["BB_Upper"] - bb["BB_Lower"]) / bb["BB_Middle"] * 100.0

    band_range = bb["BB_Upper"] - bb["BB_Lower"]
    bb["BB_PctB"] = np.where(band_range > 0, (close - bb["BB_Lower"]) / band_range, np.nan)
    return bb


def bb_overshoot_pct(close: pd.Series, upper: pd.Series) -> pd.Series:
    """(Close - Upper) / Upper * 100 — the exact mandatory-condition arithmetic. Never modify
    this formula; if a variant is needed, add it as a separately named function/strategy
    variant (PART 1 / PART 72), never by editing this one in place."""
    return (close - upper) / upper * 100.0
