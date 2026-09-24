"""Tests for data/nse_reports.py's security-master discovery pipeline
(discover_report -> resolve_download -> download_raw -> hash_raw -> validate_artifact ->
parse -> derive_mainboard -> snapshot), added during the P1 provider-hardening checkpoint.

Before this file, the discovery/download/HTTP layer had ZERO direct tests — only the parsing
layer (tests/integration/test_nse_parser.py) and the mainboard-filter layer
(tests/integration/test_mainboard_universe.py) were covered, both by stubbing
`fetch_security_file` itself rather than exercising what it actually does over HTTP.

`requests.Session.get` is mocked throughout (PART 57 — offline unit tests must not depend on
live providers); this file tests this module's own discovery/validation logic, not the network.
"""

from __future__ import annotations

import gzip
from datetime import date
from unittest.mock import patch

import pandas as pd
import pytest
from nse_scanner.data import nse_reports
from nse_scanner.data.nse_reports import (
    DiscoveryResult,
    SecurityFileUrlTemplate,
    discover_report,
    download_raw,
    fetch_security_file,
    hash_raw,
    resolve_download,
    validate_artifact,
)
from nse_scanner.exceptions import DataProviderError
from nse_scanner.testing.synthetic_market_data import generate_synthetic_security_file


class _Resp:
    def __init__(self, status_code: int, content: bytes = b""):
        self.status_code = status_code
        self.content = content

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests
            raise requests.HTTPError(f"HTTP {self.status_code}")


def _security_csv_bytes(gzip_it: bool = True) -> bytes:
    df = generate_synthetic_security_file()
    raw = df.to_csv(index=False).encode("utf-8")
    return gzip.compress(raw) if gzip_it else raw


def _mock_get_factory(responses_by_url: dict[str, _Resp], homepage_ok: bool = True):
    """`responses_by_url` maps an exact URL to the _Resp it should return; the NSE homepage
    warm-up GET (no Range header, different URL) always succeeds unless homepage_ok=False."""
    def _get(self, url, timeout=None, headers=None):
        if url == "https://www.nseindia.com":
            if not homepage_ok:
                import requests
                raise requests.ConnectionError("homepage unreachable")
            return _Resp(200, b"<html>homepage</html>")
        if url in responses_by_url:
            return responses_by_url[url]
        return _Resp(404)
    return _get


# --- discover_report: date-backward scanning ----------------------------------------------------

def test_discover_finds_todays_file_on_first_try():
    today = date(2026, 9, 22)
    good_url = f"https://nsearchives.nseindia.com/content/cm/NSE_CM_security_{today.strftime('%d%m%Y')}.csv.gz"
    with patch("requests.Session.get", _mock_get_factory({good_url: _Resp(200, _security_csv_bytes())})):
        result = discover_report(max_lookback_days=5, today=today)
    assert result.found_url == good_url
    assert result.attempts[-1].outcome == "AVAILABLE"


def test_discover_scans_backward_when_todays_date_404s():
    """The core P1 fix: a security master file that isn't published every session must not be
    treated as 'discovery is broken' just because TODAY's exact date 404s."""
    today = date(2026, 9, 22)
    three_days_ago = date(2026, 9, 19)
    good_url = (f"https://nsearchives.nseindia.com/content/cm/"
                f"NSE_CM_security_{three_days_ago.strftime('%d%m%Y')}.csv.gz")
    with patch("requests.Session.get", _mock_get_factory({good_url: _Resp(200, _security_csv_bytes())})):
        result = discover_report(max_lookback_days=5, today=today)
    assert result.found_url == good_url
    # Every intermediate date's attempt must be recorded, not silently skipped.
    not_found = [a for a in result.attempts if a.outcome == "NOT_FOUND"]
    assert len(not_found) >= 3  # today, today-1, today-2 all missed before today-3 succeeded


def test_discover_records_every_attempt_when_nothing_found():
    today = date(2026, 9, 22)
    with patch("requests.Session.get", _mock_get_factory({})):
        result = discover_report(max_lookback_days=3, today=today)
    assert result.found_url is None
    assert len(result.attempts) > 0
    assert all(a.outcome == "NOT_FOUND" for a in result.attempts)


def test_discover_picks_the_correct_dates_file_not_a_neighboring_one():
    """Date-mismatch regression: with TWO different dates both serving content, the result must
    correspond to the date actually requested in that URL, not a coincidentally-similar one."""
    today = date(2026, 9, 22)
    url_today = "https://nsearchives.nseindia.com/content/cm/NSE_CM_security_22092026.csv.gz"
    url_yesterday = "https://nsearchives.nseindia.com/content/cm/NSE_CM_security_21092026.csv.gz"
    with patch("requests.Session.get", _mock_get_factory({
        url_today: _Resp(200, _security_csv_bytes()),
        url_yesterday: _Resp(200, _security_csv_bytes()),
    })):
        result = discover_report(max_lookback_days=5, today=today)
    assert result.found_url == url_today  # today's own date wins, not yesterday's


def test_discover_treats_network_exception_as_unreachable_not_not_found():
    today = date(2026, 9, 22)

    def _get(self, url, timeout=None, headers=None):
        if url == "https://www.nseindia.com":
            return _Resp(200)
        import requests
        raise requests.Timeout("simulated timeout")

    with patch("requests.Session.get", _get):
        result = discover_report(max_lookback_days=1, today=today)
    assert result.found_url is None
    assert all(a.outcome == "UNREACHABLE" for a in result.attempts)


def test_discover_rejects_html_block_page_despite_200_status():
    """Real NSE anti-bot failure mode this pipeline now defends against: HTTP 200 with an HTML
    page instead of the file. A bare status-code check would have accepted this."""
    today = date(2026, 9, 22)
    url = "https://nsearchives.nseindia.com/content/cm/NSE_CM_security_22092026.csv.gz"
    html_block_page = b"<html><body>Access Denied - please enable JavaScript</body></html>"
    with patch("requests.Session.get", _mock_get_factory({url: _Resp(200, html_block_page)})):
        result = discover_report(max_lookback_days=1, today=today)
    assert result.found_url is None
    assert any(a.outcome == "INVALID_CONTENT" for a in result.attempts)


def test_undated_template_tried_only_once_across_the_date_window():
    today = date(2026, 9, 22)
    calls: list[str] = []

    def _get(self, url, timeout=None, headers=None):
        calls.append(url)
        if url == "https://www.nseindia.com":
            return _Resp(200)
        return _Resp(404)

    with patch("requests.Session.get", _get):
        discover_report(max_lookback_days=3, today=today)

    undated_url = "https://nsearchives.nseindia.com/content/equity/EQUITY_L.csv"
    assert calls.count(undated_url) == 1


# --- resolve_download / DataProviderError with full attempt log ---------------------------------

def test_resolve_download_returns_the_found_url():
    result = DiscoveryResult(found_url="https://example.com/f.csv.gz", attempts=[])
    assert resolve_download(result) == "https://example.com/f.csv.gz"


def test_resolve_download_raises_with_full_attempt_log_on_failure():
    today = date(2026, 9, 22)
    with patch("requests.Session.get", _mock_get_factory({})):
        discovery = discover_report(max_lookback_days=2, today=today)
    with pytest.raises(DataProviderError) as exc_info:
        resolve_download(discovery)
    msg = str(exc_info.value)
    assert "NSE_CM_security" in msg  # the actual attempted URLs are in the error, not just a count
    assert msg.count("->") >= 2      # multiple distinct attempts logged, not collapsed to one


# --- download_raw / hash_raw / validate_artifact -------------------------------------------------

def test_download_raw_returns_full_content():
    url = "https://nsearchives.nseindia.com/content/cm/NSE_CM_security_22092026.csv.gz"
    content = _security_csv_bytes()
    with patch("requests.Session.get", _mock_get_factory({url: _Resp(200, content)})):
        out = download_raw(url)
    assert out == content


def test_download_raw_raises_on_http_error():
    url = "https://nsearchives.nseindia.com/content/cm/NSE_CM_security_22092026.csv.gz"
    with patch("requests.Session.get", _mock_get_factory({url: _Resp(500)})):
        with pytest.raises(DataProviderError):
            download_raw(url)


def test_hash_raw_is_a_real_sha256():
    import hashlib
    content = b"hello world"
    assert hash_raw(content) == hashlib.sha256(content).hexdigest()


def test_validate_artifact_accepts_real_gzip():
    validate_artifact(_security_csv_bytes(), "https://example.com/f.csv.gz")  # must not raise


def test_validate_artifact_rejects_empty():
    with pytest.raises(DataProviderError):
        validate_artifact(b"", "https://example.com/f.csv.gz")


def test_validate_artifact_rejects_html_for_gz_url():
    with pytest.raises(DataProviderError, match="HTML"):
        validate_artifact(b"<html>blocked</html>", "https://example.com/f.csv.gz")


def test_validate_artifact_rejects_non_gzip_bytes_for_gz_url():
    with pytest.raises(DataProviderError, match="gzip"):
        validate_artifact(b"not actually gzip data at all", "https://example.com/f.csv.gz")


# --- fetch_security_file: full orchestration -----------------------------------------------------

def test_fetch_security_file_end_to_end_records_correct_hash_and_url():
    today = date(2026, 9, 22)
    url = "https://nsearchives.nseindia.com/content/cm/NSE_CM_security_22092026.csv.gz"
    content = _security_csv_bytes()
    with patch("requests.Session.get", _mock_get_factory({url: _Resp(200, content)})):
        result = fetch_security_file(today=today)

    assert result.source_url == url
    assert result.file_hash == hash_raw(gzip.decompress(content))
    assert result.row_count == len(result.frame)
    assert len(result.discovery_attempts) >= 1


def test_fetch_security_file_raises_cleanly_with_no_fallback_when_all_sources_fail():
    """No fallback (PART 6): a total discovery failure must raise, never return a substituted or
    partial result — proven here at the nse_reports.py level (universe/mainboard.py's OWN
    no-fallback contract is separately tested in test_mainboard_universe.py)."""
    today = date(2026, 9, 22)
    with patch("requests.Session.get", _mock_get_factory({})):
        with pytest.raises(DataProviderError):
            fetch_security_file(max_lookback_days=2, today=today)


def test_fetch_security_file_derives_mainboard_and_snapshot_with_full_provenance():
    today = date(2026, 9, 22)
    url = "https://nsearchives.nseindia.com/content/cm/NSE_CM_security_22092026.csv.gz"
    content = _security_csv_bytes()
    with patch("requests.Session.get", _mock_get_factory({url: _Resp(200, content)})):
        result = fetch_security_file(today=today)

    mainboard = nse_reports.derive_mainboard(result.frame)
    snap = nse_reports.snapshot(result, mainboard)

    assert snap.universe_id == "NSE_MAINBOARD_EQ"
    assert snap.source_url == url
    assert snap.file_hash == result.file_hash
    assert snap.schema_version == nse_reports.SECURITY_FILE_SCHEMA_VERSION
    assert snap.raw_row_count == result.row_count
    assert snap.eligible_count == len(mainboard)
    assert snap.eligible_count <= snap.raw_row_count
    assert snap.definition is not None and "EQ" in snap.definition and "BE" in snap.definition
    assert snap.validation_status == "VALID"


def test_snapshot_marks_invalid_when_mainboard_is_empty():
    empty = pd.DataFrame({"NSE_Symbol": [], "Company_Name": [], "Series": []})
    result = nse_reports.SecurityFileResult(
        frame=empty, source_url="https://example.com/f.csv.gz",
        retrieved_at=pd.Timestamp("2026-09-22", tz="UTC"), file_hash="deadbeef", row_count=0,
    )
    snap = nse_reports.snapshot(result, empty)
    assert snap.validation_status == "INVALID"


def test_security_file_url_template_dataclass_fields():
    tpl = SecurityFileUrlTemplate("https://example.com/{ddmmyyyy}.csv.gz", True)
    assert tpl.dated is True
    assert "{ddmmyyyy}" in tpl.template
