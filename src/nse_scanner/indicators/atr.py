"""True Range and Wilder ATR.

**Audit note (BUG 5):** the legacy notebook's ``calculate_atr`` computed a *simple rolling mean*
of True Range and its docstring called this "Wilder's ATR" (a documented approximation, but
still mislabeled — the values it produced are NOT what Wilder's smoothing produces). This module
implements the actual recursive Wilder formulation and is unit-tested against a hand-computed
reference series (see tests/unit/test_atr.py).

True Range:
    TR_t = max(High_t - Low_t, |High_t - Close_{t-1}|, |Low_t - Close_{t-1}|)

Wilder ATR (recursive, NOT a simple moving average of TR):
    ATR_first = mean(TR_1..TR_n)                       (simple average seed, standard Wilder init)
    ATR_t     = ((ATR_{t-1} * (n - 1)) + TR_t) / n      for t after the seed
"""

from __future__ import annotations

import numpy as np
import pandas as pd

REQUIRED_COLUMNS = ("High", "Low", "Close")


def true_range(df: pd.DataFrame) -> pd.Series:
    """max(High-Low, |High-PrevClose|, |Low-PrevClose|). The very first bar has no previous
    close, so `PrevClose` terms are NaN there and `.max()` (skipna=True by default) falls back
    to plain `High-Low` for that single bar — the standard convention, not a gap in the data.
    `wilder_atr`'s seed window deliberately still starts one bar later (see its docstring) so the
    seed only uses TR values computed WITH a genuine previous close.
    """
    prev_close = df["Close"].shift(1)
    tr = pd.concat([
        df["High"] - df["Low"],
        (df["High"] - prev_close).abs(),
        (df["Low"] - prev_close).abs(),
    ], axis=1).max(axis=1)
    tr.name = "TR"
    return tr


def wilder_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Genuine recursive Wilder ATR. The first `period` values are NaN (no seed exists yet);
    the seed at index `period - 1` (0-based) is the simple mean of the first `period` TR values;
    every value after that follows the recursive formula. Deterministic and history-order
    dependent — this is exactly why the anti-look-ahead test (tests/unit/test_lookahead.py)
    matters for this indicator.
    """
    tr = true_range(df)
    n = len(tr)
    atr = np.full(n, np.nan, dtype=float)

    if n < period:
        return pd.Series(atr, index=df.index, name="ATR14" if period == 14 else f"ATR{period}")

    tr_vals = tr.to_numpy()
    # tr_vals[0] has no prior close, so true_range() falls back to High-Low for that single bar
    # (the standard convention) rather than NaN — but the Wilder seed window deliberately still
    # starts at index 1, using exactly `period` TRUE RANGE values computed WITH a prior close
    # (tr_vals[1:period+1]), which is the conventional Wilder seed definition.
    seed = np.nanmean(tr_vals[1:period + 1])
    atr[period] = seed  # seed lands on the bar AFTER the period-th TR value, matching a
                          # `period`-bar warm-up plus the first TR (which itself needs a prior close)

    prev = seed
    for t in range(period + 1, n):
        cur_tr = tr_vals[t]
        if np.isnan(cur_tr):
            atr[t] = np.nan
            continue
        prev = ((prev * (period - 1)) + cur_tr) / period
        atr[t] = prev

    return pd.Series(atr, index=df.index, name=f"ATR{period}")


def atr_pct(atr: pd.Series, close: pd.Series) -> pd.Series:
    return atr / close * 100.0


def bb_overshoot_atr(close: pd.Series, bb_upper: pd.Series, atr: pd.Series) -> pd.Series:
    """ATR-normalized overshoot: (Close - Upper_BB) / ATR14. Diagnostic only (PART 37 #1)."""
    return np.where(atr > 0, (close - bb_upper) / atr, np.nan)
