"""Corporate-action-aware price-discontinuity flagging (PART 14).

This module does NOT fabricate a corporate-action calendar (no network access to an official CA
source was available while building this repository — see CODE_REVIEW.md). What it DOES do
honestly:
  1. Flag statistically large single-day gaps (`large_gap_flag`) so a research consumer knows a
     jump might not be "real" market movement.
  2. Provide a `CorporateActionRecord` schema and a `CorporateActionStore` so that once a real CA
     feed (e.g. NSE's corporate-actions API/report) is wired up, splits/bonuses/rights can be
     attached to `corporate_action_flag` and reconciled against detected gaps.

Without a CA feed, `corporate_action_flag` stays False for every row and `large_gap_flag` is the
only signal — this repository never claims to distinguish a real corporate-action discontinuity
from a genuine large price move; it only flags the candidate for a human/CA-feed to resolve.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import pandas as pd


@dataclass(frozen=True)
class CorporateActionRecord:
    isin: str
    nse_symbol: str
    action_type: str         # SPLIT | BONUS | RIGHTS | DIVIDEND | MERGER | SYMBOL_CHANGE | OTHER
    ex_date: date
    ratio_or_amount: str | None
    source: str
    retrieved_at: str


def flag_large_gaps(df: pd.DataFrame, gap_threshold_pct: float = 10.0) -> pd.DataFrame:
    """Adds `large_gap_flag` and `price_discontinuity_flag` columns based on overnight
    open-vs-prior-close gap magnitude. `corporate_action_flag` is included, defaulted False,
    ready to be populated once a real CA feed is attached (see module docstring)."""
    out = df.copy()
    prev_close = out["Close"].shift(1)
    gap_pct = (out["Open"] - prev_close) / prev_close * 100.0
    out["Gap_Pct"] = gap_pct
    out["large_gap_flag"] = gap_pct.abs() >= gap_threshold_pct
    out["corporate_action_flag"] = False
    out["price_discontinuity_flag"] = out["large_gap_flag"] & ~out["corporate_action_flag"]
    return out


def reconcile_with_ca_calendar(gapped_df: pd.DataFrame, ca_records: list[CorporateActionRecord]
                                ) -> pd.DataFrame:
    """Marks `corporate_action_flag = True` on any date matching a known CA ex-date, and
    recomputes `price_discontinuity_flag` accordingly. Pure function — takes an explicit CA
    record list rather than fetching one, so it works identically in tests and in production
    once a real feed supplies `ca_records`."""
    out = gapped_df.copy()
    ca_dates = {r.ex_date for r in ca_records}
    dates = out.index.date if hasattr(out.index, "date") else out.index
    out["corporate_action_flag"] = pd.Series(list(dates), index=out.index).isin(ca_dates)
    out["price_discontinuity_flag"] = out["large_gap_flag"] & ~out["corporate_action_flag"]
    return out
