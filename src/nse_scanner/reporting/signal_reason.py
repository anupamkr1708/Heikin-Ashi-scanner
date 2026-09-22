"""Programmatic, factual signal-reason text (PART 46).

Every passing signal gets a plain-language explanation built ONLY from that row's own computed
fields. Never uses language implying an outcome ("high probability", "guaranteed", "strong win",
"expected to profit") — this module physically cannot produce those phrases because it only
formats numbers that are already on the row.
"""

from __future__ import annotations

import pandas as pd


def build_signal_reason(row: pd.Series) -> str:
    parts = [
        f"{row.get('Breakout_Type', 'BREAKOUT').replace('_', ' ').title()}.",
        f"Close is {row['BB_Overshoot_Pct']:.2f}% above Upper BB.",
        f"HA body is {row['HA_Body_Pct']:.2f}%.",
    ]

    atr_overshoot = row.get("BB_Overshoot_ATR")
    if pd.notna(atr_overshoot):
        parts.append(f"ATR-normalized overshoot is {atr_overshoot:.2f} ATR.")

    vol_ratio = row.get("Volume_Ratio_20")
    if pd.notna(vol_ratio):
        parts.append(f"Volume ratio is {vol_ratio:.2f}x.")

    dist50 = row.get("Dist_SMA50_Pct")
    dist200 = row.get("Dist_SMA200_Pct")
    if pd.notna(dist50) and pd.notna(dist200):
        parts.append(f"Price is {dist50:.1f}% above SMA50 and {dist200:.1f}% above SMA200.")
    elif pd.notna(dist50):
        parts.append(f"Price is {dist50:.1f}% above SMA50 (SMA200 unavailable — limited history).")

    return " ".join(parts)
