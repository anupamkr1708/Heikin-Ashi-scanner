"""Maximum Favorable/Adverse Excursion, calculated strictly from the entry bar forward (PART 32).

**Correctness rule this module exists to enforce:** MFE/MAE must never include any intraday
range from BEFORE actual entry. For a next-open entry, the entry bar's own high/low IS eligible
(the position exists intraday on that bar, from the open). For a next-close entry, the entry
bar's high/low is NOT eligible — the position didn't exist until that bar's close, so only bars
strictly after the entry bar count. tests/unit/test_mfe_mae.py asserts both cases explicitly.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from nse_scanner.research.forward_returns import NEXT_OPEN


@dataclass(frozen=True)
class ExcursionResult:
    mfe_pct: float | None
    mae_pct: float | None
    time_to_mfe: int | None
    time_to_mae: int | None


def calculate_mfe_mae(
    price_df: pd.DataFrame, entry_pos: int, entry_price: float, horizon_days: int, entry_method: str
) -> ExcursionResult:
    """`horizon_days` bounds the excursion window to `horizon_days` sessions after entry."""
    n = len(price_df)
    window_end = min(entry_pos + horizon_days, n - 1)

    # For next_open entry, the entry bar's own intraday range is includable (position existed
    # intraday from the open). For next_close entry, the entry bar's range predates the fill.
    window_start = entry_pos if entry_method == NEXT_OPEN else entry_pos + 1
    if window_start > window_end:
        return ExcursionResult(None, None, None, None)

    highs = price_df["High"].iloc[window_start : window_end + 1]
    lows = price_df["Low"].iloc[window_start : window_end + 1]
    if highs.empty:
        return ExcursionResult(None, None, None, None)

    mfe_price = highs.max()
    mae_price = lows.min()
    mfe_pct = (mfe_price - entry_price) / entry_price * 100.0
    mae_pct = (mae_price - entry_price) / entry_price * 100.0

    time_to_mfe = int(highs.values.argmax() + (window_start - entry_pos))
    time_to_mae = int(lows.values.argmin() + (window_start - entry_pos))

    return ExcursionResult(mfe_pct=mfe_pct, mae_pct=mae_pct, time_to_mfe=time_to_mfe, time_to_mae=time_to_mae)


def summarize_excursions(mfe_values: list[float], mae_values: list[float]) -> dict[str, float]:
    """Correctly-labeled aggregates (BUG 9): never call an average an 'MFE'."""
    mfe = np.array([v for v in mfe_values if v is not None], dtype=float)
    mae = np.array([v for v in mae_values if v is not None], dtype=float)
    out: dict[str, float] = {}
    if mfe.size:
        out["Mean_MFE"] = float(np.mean(mfe))
        out["Median_MFE"] = float(np.median(mfe))
        out["P10_MFE"] = float(np.percentile(mfe, 10))
        out["P90_MFE"] = float(np.percentile(mfe, 90))
    if mae.size:
        out["Mean_MAE"] = float(np.mean(mae))
        out["Median_MAE"] = float(np.median(mae))
        out["P10_MAE"] = float(np.percentile(mae, 10))
        out["P90_MAE"] = float(np.percentile(mae, 90))
    return out
