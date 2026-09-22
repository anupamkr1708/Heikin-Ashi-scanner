from datetime import date

import pytest
from nse_scanner.data.calendar import (
    covered_holiday_years,
    required_holiday_years,
    resolve_holidays_for_run,
)
from nse_scanner.exceptions import CalendarError


def test_required_years_september_run_needs_only_current_year():
    """The exact bug reported: a September 2026 run should NOT require 2025 holiday data."""
    years = required_holiday_years(date(2026, 9, 13))
    assert years == {2026}


def test_required_years_early_january_needs_previous_year_too():
    years = required_holiday_years(date(2026, 1, 3), lookback_days=35, lookahead_days=35)
    assert 2025 in years
    assert 2026 in years


def test_required_years_late_december_needs_next_year_too():
    years = required_holiday_years(date(2026, 12, 29), lookback_days=35, lookahead_days=35)
    assert 2026 in years
    assert 2027 in years


def test_covered_holiday_years_reads_yaml_keys_not_derived_dates(tmp_path):
    p = tmp_path / "holidays.yaml"
    p.write_text("holidays:\n  2026: []\n  2027: [\"2027-01-26\"]\n")
    # 2026 has an EMPTY list but is still "covered" (explicitly configured, just no remaining
    # holidays) — must not be indistinguishable from "never configured".
    assert covered_holiday_years(p) == {2026, 2027}


def test_resolve_holidays_for_run_strict_raises_on_missing_year(tmp_path):
    p = tmp_path / "holidays.yaml"
    p.write_text("holidays: {}\n")
    with pytest.raises(CalendarError):
        resolve_holidays_for_run(p, date(2026, 9, 13), strict=True)


def test_resolve_holidays_for_run_lenient_degrades_instead_of_raising(tmp_path):
    p = tmp_path / "holidays.yaml"
    p.write_text("holidays: {}\n")
    holidays = resolve_holidays_for_run(p, date(2026, 9, 13), strict=False)
    assert holidays == set()  # degrades to weekday-only, does not raise


def test_resolve_holidays_for_run_succeeds_when_year_covered(tmp_path):
    p = tmp_path / "holidays.yaml"
    p.write_text("holidays:\n  2026: [\"2026-01-26\", \"2026-08-15\"]\n")
    holidays = resolve_holidays_for_run(p, date(2026, 9, 13), strict=True)
    assert date(2026, 1, 26) in holidays
    assert date(2026, 8, 15) in holidays
