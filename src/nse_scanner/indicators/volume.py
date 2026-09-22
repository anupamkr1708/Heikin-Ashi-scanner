"""Volume / liquidity diagnostics.

Diagnostic only, feeding the optional volume filter and the ranking score — never the mandatory
signal. More volume is not assumed to be automatically bullish (PART 22).
"""

from __future__ import annotations

import pandas as pd


def calculate_volume_liquidity(df: pd.DataFrame) -> pd.DataFrame:
    vl = pd.DataFrame(index=df.index)
    vl["Avg_Volume_20"] = df["Volume"].rolling(window=20, min_periods=20).mean()
    vl["Avg_Volume_50"] = df["Volume"].rolling(window=50, min_periods=50).mean()
    vl["Volume_Ratio_20"] = df["Volume"] / vl["Avg_Volume_20"]
    vl["Volume_Ratio_50"] = df["Volume"] / vl["Avg_Volume_50"]
    vl["Dollar_Volume"] = df["Volume"] * df["Close"]
    return vl
