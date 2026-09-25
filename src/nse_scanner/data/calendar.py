"""NSE trading-session calendar (PART 7 / BUG 1).

**Audit note (BUG 1):** the legacy notebook flagged staleness using *calendar* days
(``STALE_DATA_MAX_DAYS = 5`` calendar days). That silently misbehaves around weekends and
multi-day holiday clusters (a Friday close is only 3 calendar days old on Monday, so a
genuinely-missing Tuesday+Wednesday update could still read as "not stale"; conversely a single
placeholder that is stale by only 2 *trading* sessions could read as fresh if a long weekend is
in between). This module replaces that with an explicit trading-SESSION model.

Holiday data: NSE's trading holidays include several lunar-calendar festivals (Diwali, Holi,
Eid, etc.) whose Gregorian dates are gazetted per year and are **not** derivable from a fixed
rule. This module never fabricates those dates. It loads them from an explicit, versioned
holiday list (``config/nse_holidays.yaml``) that must be populated/refreshed from NSE's official
trading-holiday circular (https://www.nseindia.com/resources/exchange-communication-holidays)
before a production run for a given year — see README "Data freshness" section and
CODE_REVIEW.md. If a year is missing from the holiday file, this module does NOT silently assume
"no holidays that year" for session-count purposes where that would matter for correctness;
callers get an explicit ``CalendarError`` instead (see `assert_year_covered`).

**2026 note (found during the v1.3 research-integrity audit):** the official annual circular is
not the whole story even for a year it fully covers — NSE issued an ad-hoc modification circular
on 2026-01-12 adding 2026-01-15 as a trading holiday (Maharashtra municipal elections), on top of
the 15 dates in the original December 2025 annual circular. `config/nse_holidays.yaml` documents
both the primary circular and this ad-hoc addition with separate provenance for each. This is
exactly why this module treats "year covered" and "year complete" as different claims: covering a
year does not guarantee every ad-hoc addition issued during that year has been re-verified.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from datetime import time as dtime
from pathlib import Path

import yaml

from nse_scanner.exceptions import CalendarError

NSE_CLOSE_TIME = dtime(15, 30)


@dataclass(frozen=True)
class SessionInfo:
    as_of_date: date
    expected_completed_session: date
    signal_date: date
    planned_entry_date: date


class DataStatus:
    CURRENT = "CURRENT"
    STALE_1_SESSION = "STALE_1_SESSION"
    STALE_2_PLUS = "STALE_2_PLUS"
    MISSING = "MISSING"
    INVALID = "INVALID"
    UNAVAILABLE = "UNAVAILABLE"


def load_holiday_set(path: str | Path, years: set[int] | None = None) -> set[date]:
    """Loads explicit holiday dates from a YAML file shaped like:

        holidays:
          2025: ["2025-01-26", "2025-03-14", ...]
          2026: ["2026-01-26", ...]

    Returns the union across the requested years (or all years in the file if `years` is None).
    """
    p = Path(path)
    if not p.exists():
        return set()
    with open(p, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    holidays_by_year = raw.get("holidays", {})
    out: set[date] = set()
    for yr, dates in holidays_by_year.items():
        if years is not None and int(yr) not in years:
            continue
        for d in dates:
            out.add(d if isinstance(d, date) else datetime.strptime(d, "%Y-%m-%d").date())
    return out


def covered_holiday_years(path: str | Path) -> set[int]:
    """Which years actually have an entry in the holiday YAML — independent of whether that
    year's list happens to be empty. Used (instead of deriving years from the loaded dates
    themselves) so a year that is present but genuinely has zero remaining holidays is never
    mistaken for a year that was never populated at all."""
    p = Path(path)
    if not p.exists():
        return set()
    with open(p, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    return {int(y) for y in (raw.get("holidays", {}) or {}).keys()}


def required_holiday_years(as_of: date, lookback_days: int = 35, lookahead_days: int = 35) -> set[int]:
    """Which calendar year(s) session resolution around `as_of` can actually touch.

    Bug this fixes: a prior version unconditionally required both `as_of.year` AND
    `as_of.year - 1`, so a September run demanded holiday data for the PREVIOUS January even
    though nothing in `resolve_session`'s bounded lookback/lookahead (`previous_trading_day` /
    `next_trading_day`, each capped well under `lookback_days`/`lookahead_days` here) could ever
    reach that far back. Only the year(s) the lookback/lookahead window actually spans are
    required; December/January boundary runs correctly still pull in the adjacent year.
    """
    start = as_of - timedelta(days=lookback_days)
    end = as_of + timedelta(days=lookahead_days)
    return set(range(start.year, end.year + 1))


def assert_year_covered(holiday_set_years: set[int], year: int) -> None:
    if year not in holiday_set_years:
        raise CalendarError(
            f"No NSE holiday data loaded for {year}. Populate config/nse_holidays.yaml from the "
            f"official NSE trading-holiday circular before running a production scan/ingestion "
            f"for {year}. Refusing to guess (PART 6 / PART 15 — no fabricated calendar data)."
        )


def resolve_holidays_for_run(holiday_yaml_path: str | Path, as_of: date, strict: bool = True) -> set[date]:
    """Single, shared entry point every CLI script uses to load holidays for a run — replaces
    each script duplicating its own year-selection/validation logic.

    Loads only the year(s) `required_holiday_years(as_of)` actually spans (fixes the
    unrelated-year bug above). In `strict` mode (the production default, used by
    `run_daily.py`'s live path and `run_scan.py`) a missing required year **raises**
    `CalendarError` rather than silently degrading — per the explicit requirement that a
    production scan must not run on unverified calendar data. In non-strict mode
    (`--offline-fixture`, tests) a missing year logs a warning and degrades to a weekday-only
    calendar for that year instead of blocking.
    """
    needed_years = required_holiday_years(as_of)
    covered = covered_holiday_years(holiday_yaml_path)
    missing = needed_years - covered
    if missing:
        msg = (
            f"NSE holiday data missing for required year(s) {sorted(missing)} (needed to resolve "
            f"sessions around {as_of.isoformat()}). Populate config/nse_holidays.yaml from the "
            f"official NSE trading-holiday circular before running a production scan/ingestion."
        )
        if strict:
            raise CalendarError(msg)
        import logging

        logging.getLogger(__name__).warning("%s (proceeding with weekday-only calendar for %s)", msg, sorted(missing))
    return load_holiday_set(holiday_yaml_path, needed_years)


def is_weekday(d: date) -> bool:
    return d.weekday() < 5  # Mon=0 .. Fri=4


def is_trading_day(d: date, holidays: set[date]) -> bool:
    return is_weekday(d) and d not in holidays


def previous_trading_day(d: date, holidays: set[date], max_lookback_days: int = 30) -> date:
    cur = d - timedelta(days=1)
    for _ in range(max_lookback_days):
        if is_trading_day(cur, holidays):
            return cur
        cur -= timedelta(days=1)
    raise CalendarError(f"Could not find a trading day before {d} within {max_lookback_days} days")


def next_trading_day(d: date, holidays: set[date], max_lookahead_days: int = 30) -> date:
    cur = d + timedelta(days=1)
    for _ in range(max_lookahead_days):
        if is_trading_day(cur, holidays):
            return cur
        cur += timedelta(days=1)
    raise CalendarError(f"Could not find a trading day after {d} within {max_lookahead_days} days")


def sessions_between(start_exclusive: date, end_inclusive: date, holidays: set[date]) -> int:
    """Number of trading sessions strictly after `start_exclusive` through `end_inclusive`."""
    if end_inclusive < start_exclusive:
        return -sessions_between(end_inclusive, start_exclusive, holidays)
    count = 0
    cur = start_exclusive + timedelta(days=1)
    while cur <= end_inclusive:
        if is_trading_day(cur, holidays):
            count += 1
        cur += timedelta(days=1)
    return count


def resolve_session(now: datetime, holidays: set[date]) -> SessionInfo:
    """Determines AS_OF_DATE / EXPECTED_COMPLETED_SESSION / SIGNAL_DATE / PLANNED_ENTRY_DATE
    (PART 7) from a timezone-aware "now" (expected to already be in IST).

    If `now` falls on/after NSE close on a trading day, that day itself is the expected
    completed session (T). Otherwise the expected completed session is the previous trading day.
    Signal date == expected completed session (the scanner is EOD/T-1 by design — PART 7).
    Planned entry date is the NEXT trading day after the signal date.
    """
    as_of_date = now.date()

    if is_trading_day(as_of_date, holidays) and now.time() >= NSE_CLOSE_TIME:
        expected_completed = as_of_date
    else:
        expected_completed = previous_trading_day(as_of_date, holidays)

    signal_date = expected_completed
    planned_entry = next_trading_day(signal_date, holidays)

    return SessionInfo(
        as_of_date=as_of_date,
        expected_completed_session=expected_completed,
        signal_date=signal_date,
        planned_entry_date=planned_entry,
    )


def classify_data_status(last_bar_date: date | None, expected_completed_session: date, holidays: set[date]) -> str:
    """Session-based staleness classification (replaces BUG 1's calendar-day heuristic)."""
    if last_bar_date is None:
        return DataStatus.MISSING
    if last_bar_date > expected_completed_session:
        return DataStatus.INVALID  # a bar dated after the expected completed session is impossible
    lag_sessions = sessions_between(last_bar_date, expected_completed_session, holidays)
    if lag_sessions == 0:
        return DataStatus.CURRENT
    if lag_sessions == 1:
        return DataStatus.STALE_1_SESSION
    return DataStatus.STALE_2_PLUS


def trim_incomplete_candle(last_bar_date: date, now: datetime, holidays: set[date]) -> bool:
    """True if the last bar belongs to *today's* still-open session and should be dropped before
    computing signals (regular-session close not yet reached)."""
    return last_bar_date == now.date() and is_trading_day(now.date(), holidays) and now.time() < NSE_CLOSE_TIME
