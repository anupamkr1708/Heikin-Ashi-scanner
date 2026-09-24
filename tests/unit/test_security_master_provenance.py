"""Regression coverage for the second P1 forensic finding: `nse_reports.py::snapshot()` used to
set `snapshot_date=result.retrieved_at.date()` -- conflating the security FILE's own report/
effective date (embedded in its filename, e.g. `NSE_CM_security_23092026.csv.gz` -> 2026-09-23)
with the timestamp THIS process happened to download it (which can be a later calendar date,
e.g. 2026-09-24). These are separate concepts and must be recorded separately.

`DiscoveryResult`/`SecurityFileResult` now carry `source_date: date | None`, captured directly
from the date already known while constructing the winning dated URL (not re-parsed from the URL
string after the fact). `snapshot()` uses it for `UniverseSnapshot.snapshot_date` and raises
`DataProviderError` rather than silently falling back to `retrieved_at` when no date can be
honestly attributed (discovery matched the undated `EQUITY_L.csv` fallback template).
"""

from __future__ import annotations

import gzip
from datetime import date, datetime, timezone
from unittest.mock import patch

import pytest
from nse_scanner.data import nse_reports
from nse_scanner.exceptions import DataProviderError
from nse_scanner.testing.synthetic_market_data import generate_synthetic_security_file

from tests.unit.test_security_master_discovery import _mock_get_factory, _Resp


def _security_csv_bytes() -> bytes:
    df = generate_synthetic_security_file()
    return gzip.compress(df.to_csv(index=False).encode("utf-8"))


class _FixedDatetime(datetime):
    """Stands in for the module's `datetime` so `fetch_security_file`'s
    `retrieved_at=datetime.now(timezone.utc)` is deterministic in tests, independent of whatever
    the real sandbox wall clock happens to read. Subclasses the real `datetime` so nothing else
    that type-checks against it breaks."""

    FIXED = datetime(2026, 9, 24, 10, 0, tzinfo=timezone.utc)

    @classmethod
    def now(cls, tz=None):
        return cls.FIXED if tz is None else cls.FIXED.astimezone(tz)


def _discover_yesterdays_file(monkeypatch, discovery_today: date, source_filename_date: date):
    """Mocks discovery so that `discovery_today`'s own dated URL 404s but
    `source_filename_date`'s does not -- the exact real-world shape of the scenario this fix
    covers (report published for T-1, downloaded on T)."""
    good_url = (
        "https://nsearchives.nseindia.com/content/cm/"
        f"NSE_CM_security_{source_filename_date.strftime('%d%m%Y')}.csv.gz"
    )
    monkeypatch.setattr(nse_reports, "datetime", _FixedDatetime)
    with patch("requests.Session.get", _mock_get_factory({good_url: _Resp(200, _security_csv_bytes())})):
        return nse_reports.fetch_security_file(today=discovery_today), good_url


def test_source_date_is_parsed_from_the_discovered_filename_not_todays_scan_anchor(monkeypatch):
    """NSE_CM_security_23092026.csv.gz must resolve to source_date == 2026-09-23, even though
    discovery was anchored at (searching backward from) 2026-09-24."""
    result, url = _discover_yesterdays_file(
        monkeypatch, discovery_today=date(2026, 9, 24), source_filename_date=date(2026, 9, 23)
    )
    assert result.source_url == url
    assert result.source_date == date(2026, 9, 23)


def test_retrieval_timestamp_can_be_later_than_source_date_without_changing_it(monkeypatch):
    """The exact real scenario from the forensic report: a file dated 2026-09-23, retrieved
    2026-09-24. retrieved_at reflects the later date; source_date must NOT shift to match it."""
    result, _ = _discover_yesterdays_file(
        monkeypatch, discovery_today=date(2026, 9, 24), source_filename_date=date(2026, 9, 23)
    )

    assert result.retrieved_at.date() == date(2026, 9, 24)
    assert result.source_date == date(2026, 9, 23)
    assert result.retrieved_at.date() != result.source_date  # the two concepts genuinely differ here


def test_snapshot_keeps_source_date_and_retrieved_at_as_two_separate_fields(monkeypatch):
    """Part 2A item 3: the downstream UniverseSnapshot/manifest record must retain BOTH dates,
    not collapse one into the other. This is the direct regression test for the bug: before the
    fix, snapshot_date was `result.retrieved_at.date()` -- i.e. this assertion would have failed
    by construction (snapshot_date would equal retrieved_at.date(), not source_date)."""
    result, _ = _discover_yesterdays_file(
        monkeypatch, discovery_today=date(2026, 9, 24), source_filename_date=date(2026, 9, 23)
    )
    mainboard = nse_reports.derive_mainboard(result.frame)
    snap = nse_reports.snapshot(result, mainboard)

    assert snap.snapshot_date == date(2026, 9, 23)  # source/report date
    assert snap.retrieved_at == result.retrieved_at  # retrieval timestamp, untouched
    assert snap.retrieved_at.date() == date(2026, 9, 24)
    assert snap.snapshot_date != snap.retrieved_at.date()  # the fix: these must be free to differ

    as_dict = snap.to_dict()
    assert as_dict["snapshot_date"] == "2026-09-23"
    assert as_dict["retrieved_at"].startswith("2026-09-24")


def test_snapshot_fails_safely_when_source_date_is_unparseable(monkeypatch):
    """Part 2A item 4: when discovery only finds the undated EQUITY_L.csv fallback (no date can
    be embedded/parsed at all), snapshot() must raise -- NEVER silently substitute retrieved_at
    as if it were the source date. This is the specific failure mode the fix targets, not a
    hypothetical: the undated template exists precisely because dated ones can 404."""
    undated_url = "https://nsearchives.nseindia.com/content/equity/EQUITY_L.csv"
    plain_csv = generate_synthetic_security_file().to_csv(index=False).encode("utf-8")  # EQUITY_L.csv
    # is not gzipped -- fetch_security_file picks is_gzip purely from the URL's ".gz" suffix.
    monkeypatch.setattr(nse_reports, "datetime", _FixedDatetime)
    with patch("requests.Session.get", _mock_get_factory({undated_url: _Resp(200, plain_csv)})):
        result = nse_reports.fetch_security_file(today=date(2026, 9, 24), max_lookback_days=0)

    assert result.source_date is None  # honestly unknown, not guessed

    mainboard = nse_reports.derive_mainboard(result.frame)
    with pytest.raises(DataProviderError, match="source/report date"):
        nse_reports.snapshot(result, mainboard)


def test_discovery_result_source_date_is_none_when_discovery_fails_entirely():
    """No found_url -> no source_date either; nothing to parse a date out of."""
    with patch("requests.Session.get", _mock_get_factory({})):
        result = nse_reports.discover_report(max_lookback_days=1, today=date(2026, 9, 24))
    assert result.found_url is None
    assert result.source_date is None
