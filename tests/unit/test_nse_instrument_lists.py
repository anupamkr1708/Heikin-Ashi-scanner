"""Tests for data/nse_instrument_lists.py.

Parsing tests run against the REAL files supplied this session
(tests/fixtures/nse_real_samples/), not fabricated data — Task 10's "strong offline fixtures
based on real observed schemas". Fetch-failure tests use requests-mocking, never live network.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from nse_scanner.data.nse_instrument_lists import (
    INSTRUMENT_LIST_SOURCES,
    InstrumentListUnavailable,
    fetch_all_instrument_lists,
    fetch_instrument_list,
    parse_instrument_list_csv,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "nse_real_samples"

_REAL_FILE_BY_CATEGORY = {
    "ETF": "eq_etfseclist.csv",
    "REIT": "REITS_L.csv",
    "InvIT": "INVITS_L.csv",
    "SME": "SME_EQUITY_L.csv",
}

# ground truth, independently verified this session by direct pandas analysis of the real files
_EXPECTED = {
    "ETF": {"row_count": 351, "sample_symbol": "ABGSEC"},
    "REIT": {"row_count": 3, "sample_symbol": "EMBASSY"},
    "InvIT": {"row_count": 5, "sample_symbol": "INDIGRID"},
    "SME": {"row_count": 572, "sample_symbol": None},
}


def _source(category: str):
    return next(s for s in INSTRUMENT_LIST_SOURCES if s.category == category)


@pytest.mark.parametrize("category", ["ETF", "REIT", "InvIT", "SME"])
def test_parses_real_file_to_expected_row_count(category):
    text = (FIXTURES / _REAL_FILE_BY_CATEGORY[category]).read_text(encoding="utf-8")
    symbols, isins, row_count = parse_instrument_list_csv(text, _source(category))
    assert row_count == _EXPECTED[category]["row_count"]
    assert len(symbols) == row_count  # every real file has zero duplicate symbols (Audit B.1/B.3/B.4)
    assert len(isins) == row_count
    sample = _EXPECTED[category]["sample_symbol"]
    if sample:
        assert sample in symbols


def test_reit_footer_disclaimer_row_is_stripped_not_treated_as_a_symbol():
    text = (FIXTURES / "REITS_L.csv").read_text(encoding="utf-8")
    symbols, _isins, row_count = parse_instrument_list_csv(text, _source("REIT"))
    assert row_count == 3
    assert not any(len(s) > 32 or " " in s for s in symbols)  # no disclaimer sentence slipped through
    assert symbols == frozenset({"BIRET", "MINDSPACE", "EMBASSY"})


def test_invit_footer_disclaimer_row_is_stripped():
    text = (FIXTURES / "INVITS_L.csv").read_text(encoding="utf-8")
    symbols, _isins, row_count = parse_instrument_list_csv(text, _source("InvIT"))
    assert row_count == 5
    assert symbols == frozenset({"OSEINTRUST", "INDINFR", "INDIGRID", "IRBINVIT", "PGINVIT"})


def test_missing_symbol_column_raises_instrument_list_unavailable():
    bad_source = _source("ETF")
    with pytest.raises(InstrumentListUnavailable, match="symbol column not found"):
        parse_instrument_list_csv("A,B,C\n1,2,3\n", bad_source)


def test_fetch_instrument_list_download_failure_raises_instrument_list_unavailable():
    import requests

    source = _source("ETF")
    with patch("requests.Session.get", side_effect=requests.ConnectionError("simulated: no route to host")):
        with pytest.raises(InstrumentListUnavailable, match="download failed"):
            fetch_instrument_list(source, timeout=1)


def test_fetch_all_instrument_lists_one_category_failing_does_not_block_others(monkeypatch):
    """Task 7's non-fatal-per-category failure policy, exercised at the fetch_all level."""
    from nse_scanner.data import nse_instrument_lists as mod

    real_symbols = frozenset({"FAKESYM"})
    real_isins = frozenset({"INE000A00000"})

    def _fake_fetch(source, timeout=20):
        if source.category == "REIT":
            raise InstrumentListUnavailable("[REIT] simulated failure")
        return mod.InstrumentListResult(
            category=source.category,
            symbols=real_symbols,
            isins=real_isins,
            source_url=source.url,
            retrieved_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
            row_count=1,
            file_hash="deadbeef",
        )

    monkeypatch.setattr(mod, "fetch_instrument_list", _fake_fetch)
    results, statuses = fetch_all_instrument_lists()

    assert set(results.keys()) == {"ETF", "InvIT", "SME"}  # REIT missing, not silently substituted
    assert statuses["ETF"] == "FETCHED"
    assert statuses["REIT"].startswith("UNAVAILABLE")
    assert statuses["InvIT"] == "FETCHED"
    assert statuses["SME"] == "FETCHED"


def test_all_four_source_urls_use_https_nseindia_domain():
    """A cheap, permanent guard against ever pointing one of these at something other than the
    real NSE archive domain, whatever the exact path turns out to be."""
    for source in INSTRUMENT_LIST_SOURCES:
        assert source.url.startswith("https://nsearchives.nseindia.com/")


def test_confidence_levels_are_recorded_and_not_uniform():
    """Task 2's discipline: confidence must be explicit and per-source, not a single blanket
    claim. ETF/REIT are STRONG_INFERENCE (directory-pattern analogy); InvIT/SME are CONFIRMED
    (byte-identical content match found this session) -- see module docstring."""
    by_category = {s.category: s.confidence for s in INSTRUMENT_LIST_SOURCES}
    assert by_category["ETF"] == "STRONG_INFERENCE"
    assert by_category["REIT"] == "STRONG_INFERENCE"
    assert by_category["InvIT"] == "CONFIRMED"
    assert by_category["SME"] == "CONFIRMED"
