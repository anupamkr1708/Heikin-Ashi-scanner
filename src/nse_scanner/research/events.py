"""Historical signal-day / independent-event extraction (PART 33, fixes BUG 8).

**Audit note (BUG 8):** the legacy notebook's `extract_historical_events` scanned every row
where the mandatory BB+HA conditions held and treated each one as its own event. A stock that
stays extended above the upper band for five consecutive sessions produced five "independent"
observations in the forward-return study, inflating the effective sample size and correlating
adjacent observations' forward returns almost perfectly (they overlap in time).

This module reports BOTH:
    ALL_SIGNAL_DAYS    — every row where the mandatory condition holds (matches legacy behavior,
                          kept for continuity/diagnostics, NEVER used alone to claim a sample size)
    INDEPENDENT_EVENTS — a de-correlated subset, built from FRESH_BREAKOUT transitions with a
                          configurable cooldown (`independent_event_cooldown_days`) before the
                          next signal day can start a new independent event

The independent-event definition is explicit and swappable (a different definition is a
different, separately-tested function) — never silently baked into "the" event count.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from nse_scanner.strategy.bb_ha import evaluate_mandatory_vectorized
from nse_scanner.strategy.breakout import FRESH_BREAKOUT, calculate_breakout_state


@dataclass(frozen=True)
class EventExtractionResult:
    all_signal_days: pd.DataFrame  # every row with mandatory_pass == True
    independent_events: pd.DataFrame  # de-correlated subset


def extract_signal_days(
    feature_df: pd.DataFrame, max_bb_overshoot_pct: float = 4.0, min_ha_body_pct: float = 1.0
) -> pd.DataFrame:
    """`feature_df` must already contain Close, BB_Upper, BB_Overshoot_Pct, HA_Body_Pct (and
    ideally Breakout_Type — computed here if absent). Returns all rows where the mandatory
    baseline condition holds, tagged with Breakout_Type."""
    mand = evaluate_mandatory_vectorized(
        feature_df["Close"],
        feature_df["BB_Upper"],
        feature_df["BB_Overshoot_Pct"],
        feature_df["HA_Body_Pct"],
        max_bb_overshoot_pct,
        min_ha_body_pct,
    )
    df = feature_df.copy()
    df["mandatory_pass"] = mand["mandatory_pass"]

    if "Breakout_Type" not in df.columns:
        bo = calculate_breakout_state(df["Close"], df["BB_Upper"], max_bb_overshoot_pct)
        df["Breakout_Type"] = bo["Breakout_Type"]
        df["Days_Above_Upper_BB"] = bo["Days_Above_Upper_BB"]

    return df[df["mandatory_pass"]].copy()


def extract_independent_events(signal_days: pd.DataFrame, cooldown_days: int = 5) -> pd.DataFrame:
    """Builds a de-correlated event subset from `signal_days` (as returned by
    `extract_signal_days`), per security.

    Definition (explicit, configurable — PART 33): a new independent event starts at a
    FRESH_BREAKOUT row, OR at any signal-day row that is at least `cooldown_days` trading rows
    (within that security's own row index, i.e. row-count based, not calendar-day based) after
    the previously selected independent event's row for the same security. This prevents a long
    CONTINUATION streak from either being fully excluded (if we required FRESH_BREAKOUT only,
    a stock that reclaims and re-extends would never re-qualify) or fully included (inflating N).

    Requires a 'Symbol' column to group by; if absent, treats the whole frame as one security.
    """
    if signal_days.empty:
        return signal_days.copy()

    df = signal_days.copy()
    group_col = "Symbol" if "Symbol" in df.columns else None
    groups = df.groupby(group_col, sort=False) if group_col else [(None, df)]

    selected_index_values: list = []
    for _, g in groups:
        g = g.sort_index()
        last_selected_pos: int | None = None
        for i, (idx, row) in enumerate(g.iterrows()):
            is_fresh = row.get("Breakout_Type") == FRESH_BREAKOUT
            cooldown_elapsed = last_selected_pos is None or (i - last_selected_pos) >= cooldown_days
            if is_fresh or cooldown_elapsed:
                selected_index_values.append(idx)
                last_selected_pos = i

    # NOTE: when grouping by Symbol, the (date) index is not guaranteed unique across groups, so
    # selection is done via a stable row id rather than by (potentially duplicated) index label.
    if group_col:
        df = df.reset_index(drop=False)
        id_col = df.columns[0]
        selected_ids = set(selected_index_values)
        mask = df[id_col].isin(selected_ids)
        return df[mask].set_index(id_col)
    return df.loc[df.index.isin(selected_index_values)].copy()


def extract_events(
    feature_df_by_symbol: dict[str, pd.DataFrame],
    max_bb_overshoot_pct: float = 4.0,
    min_ha_body_pct: float = 1.0,
    cooldown_days: int = 5,
) -> EventExtractionResult:
    """Convenience wrapper over multiple securities' feature frames."""
    all_frames = []
    for symbol, fdf in feature_df_by_symbol.items():
        sd = extract_signal_days(fdf, max_bb_overshoot_pct, min_ha_body_pct)
        if sd.empty:
            continue
        sd = sd.copy()
        sd["Symbol"] = symbol
        sd["Signal_Date"] = sd.index
        all_frames.append(sd)

    if not all_frames:
        empty = pd.DataFrame()
        return EventExtractionResult(all_signal_days=empty, independent_events=empty)

    all_signal_days = pd.concat(all_frames).reset_index(drop=True)
    # extract_independent_events groups by Symbol and needs a DatetimeIndex per group for the
    # row-count based cooldown; re-split, apply per symbol, and recombine.
    independent_frames = []
    for _symbol, g in all_signal_days.groupby("Symbol", sort=False):
        g2 = g.set_index("Signal_Date").sort_index()
        ind = extract_independent_events(g2, cooldown_days)
        ind = ind.reset_index()
        independent_frames.append(ind)
    if independent_frames:
        independent_events = pd.concat(independent_frames).reset_index(drop=True)
    else:
        independent_events = all_signal_days.iloc[0:0]

    return EventExtractionResult(all_signal_days=all_signal_days, independent_events=independent_events)
