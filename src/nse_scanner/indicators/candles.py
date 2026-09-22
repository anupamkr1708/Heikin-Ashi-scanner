"""Regular-candle quality diagnostics (as distinct from the Heikin-Ashi engine).

Close_Location_Value (CLV) = ((Close - Low) - (High - Close)) / (High - Low), guarded for
High == Low. Range in [-1, 1]: +1 = close at the high, -1 = close at the low.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def calculate_candle_quality(df: pd.DataFrame) -> pd.DataFrame:
    cq = pd.DataFrame(index=df.index)
    o, h, lo, c = df["Open"], df["High"], df["Low"], df["Close"]
    rng = h - lo

    cq["Candle_Body_Pct"] = (c - o) / o * 100.0
    cq["Upper_Wick_Pct"] = (h - pd.concat([o, c], axis=1).max(axis=1)) / o * 100.0
    cq["Lower_Wick_Pct"] = (pd.concat([o, c], axis=1).min(axis=1) - lo) / o * 100.0
    cq["Range_Pct"] = rng / o * 100.0
    cq["Close_Location_Value"] = np.where(rng > 0, ((c - lo) - (h - c)) / rng, np.nan)
    return cq
