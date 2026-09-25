"""Forward returns with explicit entry timing (PART 30 / PART 31).

Default entry model: signal detected at completed close T; entry at T+1 session's OPEN (never
the signal-day close — PART 30 "Never pretend signal-day close is executable"). next_close entry
is also supported and must be doubly explicit about which session's close.

Horizons are measured in trading rows *from the entry row*, not from the signal row — so
"5D forward return" means the close 5 trading sessions after entry, not after the signal.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

NEXT_OPEN = "next_open"
NEXT_CLOSE = "next_close"


@dataclass(frozen=True)
class EntryResult:
    entry_date: pd.Timestamp | None
    entry_price: float | None
    entry_pos: int | None  # integer row position of the entry bar within `price_df`
    gap_from_signal_to_entry_pct: float | None


def resolve_entry(price_df: pd.DataFrame, signal_pos: int, method: str = NEXT_OPEN) -> EntryResult:
    """`price_df` is the FULL OHLC history (DatetimeIndex, position-ordered) for one security.
    `signal_pos` is the integer row position of the signal bar (T). Returns None fields if there
    is no T+1 bar yet (signal occurred on the last available row)."""
    n = len(price_df)
    entry_pos = signal_pos + 1
    if entry_pos >= n:
        return EntryResult(None, None, None, None)

    signal_close = float(price_df["Close"].iloc[signal_pos])
    if method == NEXT_OPEN:
        entry_price = float(price_df["Open"].iloc[entry_pos])
    elif method == NEXT_CLOSE:
        entry_price = float(price_df["Close"].iloc[entry_pos])
    else:
        raise ValueError(f"unknown entry method: {method}")

    entry_date = price_df.index[entry_pos]
    gap_pct = (entry_price - signal_close) / signal_close * 100.0
    return EntryResult(entry_date, entry_price, entry_pos, gap_pct)


def forward_return(price_df: pd.DataFrame, entry_pos: int, horizon_days: int) -> float | None:
    """Close-to-close forward return, `horizon_days` trading sessions AFTER entry (i.e. measured
    from the entry bar's close to the close `horizon_days` sessions later)."""
    target_pos = entry_pos + horizon_days
    if target_pos >= len(price_df):
        return None
    entry_close = float(price_df["Close"].iloc[entry_pos])
    target_close = float(price_df["Close"].iloc[target_pos])
    return (target_close - entry_close) / entry_close * 100.0


def forward_returns_multi_horizon(
    price_df: pd.DataFrame, entry_pos: int, horizons: tuple[int, ...] = (1, 3, 5, 10, 20, 40)
) -> dict[str, float | None]:
    return {f"FwdRet_{h}D": forward_return(price_df, entry_pos, h) for h in horizons}
