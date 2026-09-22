"""Weekly OHLC construction with a hard as-of cutoff (PHASE 5).

**The current baseline strategy (`bb_ha_v1_base`) does not use weekly data at all** — this module
exists so that if a future strategy variant ever does, the look-ahead trap PHASE 5 warns about
(using a Friday candle that hasn't closed yet when evaluating a Tuesday signal) is closed by
construction, not by a comment asking a future developer to remember.

A week is only "complete" once its last trading day's session has closed. Given a daily OHLC
frame and an `as_of` date, `latest_completed_weekly_bar` returns the most recent weekly candle
whose ISO calendar week has fully elapsed by `as_of` (that week's Friday `<= as_of`) — a Tuesday
`as_of` can only ever see the PREVIOUS week's completed Friday bar, never the current
(still-forming) week's partial bar, and this holds even if the input DataFrame has itself been
truncated mid-week (the eligibility check is a calendar fact, not inferred from data presence).
"""

from __future__ import annotations

from datetime import date

import pandas as pd


def build_weekly_ohlc(daily_df: pd.DataFrame) -> pd.DataFrame:
    """Real weekly OHLC from a daily DataFrame (DatetimeIndex, columns Open/High/Low/Close/Volume).

    Each weekly bar is labeled with that ISO week's calendar FRIDAY — a fixed calendar fact
    computed from the ISO (year, week) group key, independent of how much daily data happens to
    be present for that week. This is deliberate: labeling by "the last trading day actually
    present in the data" would make a week that's merely TRUNCATED (e.g. a replay that only has
    Monday+Tuesday of a week so far) indistinguishable from a week that's genuinely COMPLETE with
    a holiday-shortened end — and `latest_completed_weekly_bar` relies on this label being a
    reliable calendar fact, not a data-presence artifact, for its as-of cutoff to be safe.
    """
    iso = daily_df.index.isocalendar()
    week_key = pd.Series(iso["year"].to_numpy() * 100 + iso["week"].to_numpy(), index=daily_df.index)

    grouped = daily_df.groupby(week_key)
    weekly = pd.DataFrame({
        "Open": grouped["Open"].first(),
        "High": grouped["High"].max(),
        "Low": grouped["Low"].min(),
        "Close": grouped["Close"].last(),
        "Volume": grouped["Volume"].sum(),
    })
    fridays = [date.fromisocalendar(int(k) // 100, int(k) % 100, 5) for k in weekly.index]
    weekly.index = pd.DatetimeIndex(fridays, name="week_ends_on")
    weekly = weekly.sort_index()
    return weekly


def latest_completed_weekly_bar(daily_df: pd.DataFrame, as_of: date) -> pd.Series | None:
    """The most recent weekly candle whose ISO calendar week has fully elapsed by `as_of` (that
    week's Friday `<= as_of`). Returns None if no week has completed yet.

    This is the enforcement point: a caller that wants "this week's Friday close" on a Tuesday
    physically cannot get it from this function, because that week's calendar Friday is
    `> as_of` and therefore excluded — they get the PRIOR week's bar instead. Eligibility is
    computed from the calendar, not from how much daily data happens to be present, so this
    remains safe even when `daily_df` itself is a truncated (mid-week) historical replay slice.
    """
    weekly = build_weekly_ohlc(daily_df)
    eligible = weekly[weekly.index.date <= as_of]
    if eligible.empty:
        return None
    return eligible.iloc[-1]
