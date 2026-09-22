from datetime import date, datetime

from nse_scanner.data.calendar import (
    DataStatus,
    classify_data_status,
    is_trading_day,
    next_trading_day,
    previous_trading_day,
    resolve_session,
    sessions_between,
)

HOLIDAYS = {date(2026, 1, 26)}  # Republic Day, Monday-adjacent test fixture


def test_weekend_is_not_a_trading_day():
    saturday = date(2026, 9, 12)
    sunday = date(2026, 9, 13)
    assert not is_trading_day(saturday, HOLIDAYS)
    assert not is_trading_day(sunday, HOLIDAYS)


def test_holiday_is_not_a_trading_day():
    assert not is_trading_day(date(2026, 1, 26), HOLIDAYS)


def test_previous_trading_day_skips_weekend():
    monday = date(2026, 9, 14)  # a Monday
    prev = previous_trading_day(monday, HOLIDAYS)
    assert prev == date(2026, 9, 11)  # the preceding Friday


def test_next_trading_day_skips_weekend():
    friday = date(2026, 9, 11)
    nxt = next_trading_day(friday, HOLIDAYS)
    assert nxt == date(2026, 9, 14)  # the following Monday


def test_sessions_between_counts_only_trading_days():
    # Friday to the following Monday should be exactly 1 trading session apart.
    assert sessions_between(date(2026, 9, 11), date(2026, 9, 14), HOLIDAYS) == 1
    # Same day: 0 sessions.
    assert sessions_between(date(2026, 9, 11), date(2026, 9, 11), HOLIDAYS) == 0


def test_resolve_session_before_close_uses_previous_day():
    # Tuesday 10:00 IST -> expected completed session is Monday (market not yet closed today)
    now = datetime(2026, 9, 15, 10, 0)  # Tuesday
    info = resolve_session(now, HOLIDAYS)
    assert info.expected_completed_session == date(2026, 9, 14)  # Monday
    assert info.signal_date == date(2026, 9, 14)
    assert info.planned_entry_date == date(2026, 9, 15)  # Tuesday


def test_resolve_session_after_close_uses_today():
    now = datetime(2026, 9, 15, 16, 0)  # Tuesday, after 15:30 close
    info = resolve_session(now, HOLIDAYS)
    assert info.expected_completed_session == date(2026, 9, 15)
    assert info.planned_entry_date == date(2026, 9, 16)  # Wednesday


def test_classify_data_status_session_based_not_calendar_day_based():
    expected = date(2026, 9, 14)  # Monday
    # Data from the immediately preceding trading session (Friday) — exactly 1 session stale,
    # even though it's 3 CALENDAR days old. The old calendar-day bug (BUG 1) would have judged
    # this using raw day counts and could misclassify it around a weekend.
    status = classify_data_status(date(2026, 9, 11), expected, HOLIDAYS)
    assert status == DataStatus.STALE_1_SESSION

    status_current = classify_data_status(expected, expected, HOLIDAYS)
    assert status_current == DataStatus.CURRENT

    status_missing = classify_data_status(None, expected, HOLIDAYS)
    assert status_missing == DataStatus.MISSING

    status_invalid = classify_data_status(date(2026, 9, 16), expected, HOLIDAYS)  # after expected
    assert status_invalid == DataStatus.INVALID


def test_classify_data_status_two_plus_stale():
    expected = date(2026, 9, 15)  # Tuesday
    status = classify_data_status(date(2026, 9, 11), expected, HOLIDAYS)  # Friday: 2 sessions back
    assert status == DataStatus.STALE_2_PLUS


def test_2026_09_11_next_session_skips_the_09_14_holiday():
    """PHASE 44 regression test, using the real (non-hard-coded) holiday dataset in
    config/nse_holidays.yaml: 2026-09-11 is a Friday; 2026-09-14 (Monday) is a real NSE holiday
    per that file. The next trading session must resolve to 2026-09-15, not 2026-09-14 — and this
    must come from the actual loaded calendar dataset, not a one-off hard-coded date check."""
    from nse_scanner.data.calendar import load_holiday_set

    holidays_2026 = load_holiday_set("config/nse_holidays.yaml", {2026})
    assert date(2026, 9, 14) in holidays_2026  # the calendar file actually has this entry

    friday = date(2026, 9, 11)
    next_session = next_trading_day(friday, holidays_2026)
    assert next_session == date(2026, 9, 15)
    assert next_session != date(2026, 9, 14)
