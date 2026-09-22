"""Heikin-Ashi engine.

HA_Close[t] = (Open[t]+High[t]+Low[t]+Close[t])/4
HA_Open[0]  = (Open[0]+Close[0])/2                        (seed)
HA_Open[t]  = (HA_Open[t-1]+HA_Close[t-1])/2               for t>0 (explicit recursion — there
              is no correct closed-form vectorization of this because each step depends on the
              previous one; a naive vectorized shortcut is exactly the kind of bug this module
              exists to prevent)
HA_High[t]  = max(High[t], HA_Open[t], HA_Close[t])
HA_Low[t]   = min(Low[t],  HA_Open[t], HA_Close[t])

Heikin-Ashi is SYNTHETIC. It must never be used as a literal execution/order price anywhere in
this codebase — only the mandatory-signal body-strength calculation and diagnostics (PART 18).

A very short input history has a stronger dependence on the HA_Open seed value (the effect of
the seed decays geometrically, roughly halving each bar, but is never exactly zero) — callers
should treat History_Quality as VERY_LIMITED/LIMITED for HA-derived fields on short histories
(see indicators.history_quality).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

REQUIRED_COLUMNS = ("Open", "High", "Low", "Close")


def calculate_heikin_ashi(df: pd.DataFrame) -> pd.DataFrame:
    o = df["Open"].to_numpy(dtype=float)
    h = df["High"].to_numpy(dtype=float)
    lo = df["Low"].to_numpy(dtype=float)
    c = df["Close"].to_numpy(dtype=float)
    n = len(df)

    ha_close = (o + h + lo + c) / 4.0

    ha_open = np.empty(n, dtype=float)
    if n > 0:
        ha_open[0] = (o[0] + c[0]) / 2.0
        for t in range(1, n):
            ha_open[t] = (ha_open[t - 1] + ha_close[t - 1]) / 2.0

    ha = pd.DataFrame(index=df.index)
    ha["HA_Open"] = ha_open
    ha["HA_Close"] = ha_close
    ha["HA_High"] = np.maximum.reduce([h, ha_open, ha_close])
    ha["HA_Low"] = np.minimum.reduce([lo, ha_open, ha_close])

    ha_upper_body = np.maximum(ha["HA_Open"].to_numpy(), ha["HA_Close"].to_numpy())
    ha_lower_body = np.minimum(ha["HA_Open"].to_numpy(), ha["HA_Close"].to_numpy())
    ha_range = ha["HA_High"].to_numpy() - ha["HA_Low"].to_numpy()

    with np.errstate(divide="ignore", invalid="ignore"):
        ha["HA_Upper_Wick_Pct"] = (ha["HA_High"].to_numpy() - ha_upper_body) / ha["HA_Open"].to_numpy() * 100.0
        ha["HA_Lower_Wick_Pct"] = (ha_lower_body - ha["HA_Low"].to_numpy()) / ha["HA_Open"].to_numpy() * 100.0
        ha["HA_Body_to_Range"] = np.where(
            ha_range > 0, np.abs(ha["HA_Close"].to_numpy() - ha["HA_Open"].to_numpy()) / ha_range, np.nan
        )
    return ha


def ha_body_pct(ha_open: pd.Series, ha_close: pd.Series) -> pd.Series:
    """(HA_Close - HA_Open) / HA_Open * 100 — the exact mandatory-condition arithmetic."""
    return (ha_close - ha_open) / ha_open * 100.0


def seed_dependency_weight(bars_since_seed: int) -> float:
    """Approximate residual weight of the HA_Open[0] seed on HA_Open[t], t = bars_since_seed.

    Because HA_Open[t] = (HA_Open[t-1] + HA_Close[t-1]) / 2, the seed's coefficient halves each
    step: weight(t) = 0.5**t. Used only to label History_Quality — never to alter the HA
    arithmetic itself.
    """
    if bars_since_seed < 0:
        raise ValueError("bars_since_seed must be >= 0")
    return 0.5 ** bars_since_seed
