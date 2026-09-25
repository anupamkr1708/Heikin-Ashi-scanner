from datetime import date

import numpy as np
import pandas as pd
from nse_scanner.indicators.weekly import build_weekly_ohlc, latest_completed_weekly_bar


def _daily_df_two_weeks():
    # Week 1: Mon 2026-08-31 .. Fri 2026-09-04 (5 sessions)
    # Week 2: Mon 2026-09-07 .. Fri 2026-09-11 (5 sessions)
    dates = pd.bdate_range("2026-08-31", "2026-09-11")
    n = len(dates)
    closes = np.linspace(100, 100 + n - 1, n)
    return pd.DataFrame(
        {
            "Open": closes - 0.5,
            "High": closes + 1,
            "Low": closes - 1,
            "Close": closes,
            "Volume": [1000] * n,
        },
        index=dates,
    )


def test_build_weekly_ohlc_aggregates_correctly():
    df = _daily_df_two_weeks()
    weekly = build_weekly_ohlc(df)
    assert len(weekly) == 2

    week1 = weekly.iloc[0]
    week1_days = df.loc["2026-08-31":"2026-09-04"]
    assert week1["Open"] == week1_days["Open"].iloc[0]
    assert week1["Close"] == week1_days["Close"].iloc[-1]
    assert week1["High"] == week1_days["High"].max()
    assert week1["Low"] == week1_days["Low"].min()
    assert week1["Volume"] == week1_days["Volume"].sum()

    # Weekly bar is labeled by its LAST constituent trading day.
    assert weekly.index[0].date() == date(2026, 9, 4)  # Friday of week 1
    assert weekly.index[1].date() == date(2026, 9, 11)  # Friday of week 2


def test_tuesday_as_of_cannot_see_the_still_forming_week():
    """The core PHASE 5 guarantee: a Tuesday signal must see only the PRIOR completed Friday,
    never the current week's in-progress bar (which hasn't closed yet)."""
    df = _daily_df_two_weeks()
    tuesday_in_week2 = date(2026, 9, 8)  # Tuesday of week 2 — week 2 has NOT completed yet

    bar = latest_completed_weekly_bar(df, tuesday_in_week2)
    assert bar is not None
    # Must be week 1's bar (completed Friday 2026-09-04), NOT week 2's partial bar.
    assert bar.name.date() == date(2026, 9, 4)


def test_friday_as_of_sees_that_same_week_once_it_has_closed():
    df = _daily_df_two_weeks()
    friday_week2 = date(2026, 9, 11)  # the week HAS completed by this date

    bar = latest_completed_weekly_bar(df, friday_week2)
    assert bar is not None
    assert bar.name.date() == date(2026, 9, 11)


def test_as_of_before_any_week_completes_returns_none():
    df = _daily_df_two_weeks()
    bar = latest_completed_weekly_bar(df, date(2026, 8, 31))  # Monday of week 1 — nothing done yet
    assert bar is None


def test_appending_the_rest_of_the_week_does_not_change_a_tuesday_replay():
    """Direct analogue of the daily-bar look-ahead test, for weekly data: once Tuesday's replay
    has been computed, adding Wednesday/Thursday/Friday's bars to the DataFrame must not change
    what Tuesday's replay sees."""
    df = _daily_df_two_weeks()
    tuesday_in_week2 = date(2026, 9, 8)

    truncated = df.loc[:"2026-09-08"]  # only through Tuesday
    bar_from_truncated = latest_completed_weekly_bar(truncated, tuesday_in_week2)

    bar_from_full = latest_completed_weekly_bar(df, tuesday_in_week2)  # full week 2 data available

    assert bar_from_truncated is not None
    assert bar_from_full is not None
    pd.testing.assert_series_equal(bar_from_truncated, bar_from_full)
