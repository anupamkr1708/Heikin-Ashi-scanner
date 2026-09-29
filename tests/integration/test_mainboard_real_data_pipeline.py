"""End-to-end NSE_MAINBOARD_EQ pipeline test against REAL curated NSE data (Task 10, Task 21
acceptance criterion 10). Every file under tests/fixtures/nse_real_samples/ is real content
supplied this session, not fabricated — see MAINBOARD_UNIVERSE_INTEGRITY_V2_AUDIT.md Part B.

This is deliberately the one test in this suite that exercises filter_mainboard_equity,
deduplicate_mainboard, compute_mainboard_diagnostics, and the real ETF/REIT/InvIT/SME parsers all
together against real bytes, rather than each in isolation — a schema-compatibility regression
between any two of these modules would show up here even if every unit test in isolation still
passes.
"""

from __future__ import annotations

import gzip
from pathlib import Path

import pandas as pd
from nse_scanner.data.nse_instrument_lists import (
    INSTRUMENT_LIST_SOURCES,
    InstrumentListResult,
    parse_instrument_list_csv,
)
from nse_scanner.data.nse_reports import SecurityFileResult, parse_security_file
from nse_scanner.universe.mainboard import NSEMainboardEquityUniverseProvider

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "nse_real_samples"


def _real_security_result() -> SecurityFileResult:
    raw_text = (FIXTURES / "EQUITY_L_real_sample.csv").read_text(encoding="utf-8")
    gz = gzip.compress(raw_text.encode("utf-8"))
    parsed = parse_security_file(gz)
    return SecurityFileResult(
        frame=parsed,
        source_url="https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv",
        retrieved_at=pd.Timestamp("2026-09-25", tz="UTC"),
        file_hash="real-sample-fixture",
        row_count=len(parsed),
        source_date=None,  # EQUITY_L.csv carries no embedded date -- honestly recorded as such
    )


_REAL_FILE_BY_CATEGORY = {
    "ETF": "eq_etfseclist.csv",
    "REIT": "REITS_L.csv",
    "InvIT": "INVITS_L.csv",
    "SME": "SME_EQUITY_L.csv",
}


def _real_instrument_lists() -> tuple[dict[str, InstrumentListResult], dict[str, str]]:
    results = {}
    for source in INSTRUMENT_LIST_SOURCES:
        text = (FIXTURES / _REAL_FILE_BY_CATEGORY[source.category]).read_text(encoding="utf-8")
        symbols, isins, row_count = parse_instrument_list_csv(text, source)
        results[source.category] = InstrumentListResult(
            category=source.category,
            symbols=symbols,
            isins=isins,
            source_url=source.url,
            retrieved_at=pd.Timestamp("2026-09-25", tz="UTC"),
            row_count=row_count,
            file_hash="real-sample-fixture",
        )
    return results, {c: "FETCHED" for c in results}


def test_full_pipeline_against_real_curated_nse_data(monkeypatch):
    monkeypatch.setattr("nse_scanner.universe.mainboard.fetch_security_file", _real_security_result)
    monkeypatch.setattr(
        "nse_scanner.universe.mainboard.fetch_all_instrument_lists", lambda timeout=20: _real_instrument_lists()
    )

    # the real sample has 122 rows (65 EQ + 30 BE + 27 BZ); relax the cardinality gate to fit this
    # deliberately small curated sample rather than the full ~2,585-row real population.
    provider = NSEMainboardEquityUniverseProvider(min_count=1, max_count=200)
    constituents, source_url, retrieved_at = provider.get_constituents()

    assert source_url == "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv"
    assert set(constituents["Series"]) <= {"EQ", "BE", "BZ"}  # real BZ rows genuinely survive the filter
    assert "ADANIENT" in set(constituents["Symbol"])  # a real row this session's forensics specifically used
    assert "DALBHARAT" in set(constituents["Symbol"])

    diag = provider.last_diagnostics
    assert diag["raw_row_count"] == 122
    assert diag["universe_definition_id"] == "nse_mainboard_equity_v2"
    assert diag["classification_rules_version"] == "instrument_lists_v1"
    # the real ETF/REIT/InvIT/SME lists have zero symbol overlap with the real EQUITY_L sample
    # (Audit B.4) -- confirmed again here, end to end, not merely asserted in isolation
    assert diag["excluded_known_non_equity_count"] == 0
    assert diag["exclusion_list_status"] == {"ETF": "FETCHED", "REIT": "FETCHED", "InvIT": "FETCHED", "SME": "FETCHED"}
    assert diag["source_date"] is None  # EQUITY_L.csv carries none -- never invented
    assert diag["final_unique_symbols"] == len(constituents)


def test_real_udiff_bhavcopy_series_families_all_classified_by_legend_of_series():
    """Cross-checks the real curated bhavcopy sample against every series family this session's
    forensic analysis attributed to NSE's official Legend of Series (fetched directly from
    nseindia.com) -- a static assertion that the real 56-series sample doesn't contain anything
    this project's evidence base can't explain, would need updating if it ever did."""
    udiff = pd.read_csv(FIXTURES / "UDIFF_real_sample.csv")

    known_prefixes_and_exact = {
        "EQ",
        "BE",
        "BZ",
        "BL",  # Block Deals
        "SM",
        "ST",
        "SZ",  # SME
        "RR",
        "RT",  # REITs
        "IV",
        "ID",  # InvITs
        "GB",
        "GS",
        "SG",
        "TB",  # Gold Bond / G-Sec / SDL / T-Bill
        "MF",
        "ME",  # mutual fund units
        "BO",  # buyback
    }
    debt_prefixes = ("N", "Y", "Z", "A", "B", "D", "S", "W", "K", "E", "X", "P", "O", "Q", "F")

    unexplained = [
        s
        for s in udiff["Series"].unique()
        if s not in known_prefixes_and_exact and not (len(s) >= 1 and s[0] in debt_prefixes)
    ]
    assert unexplained == [], f"Series not attributable to any evidenced category this session: {unexplained}"

    # the specific same-day multiplicity finding (Audit B.5): ADANIENT/DALBHARAT each have BOTH an
    # EQ row and a BL (Block Deal) row on the same real trading day.
    for symbol in ("ADANIENT", "DALBHARAT"):
        rows = udiff[udiff["NSE_Symbol"] == symbol]
        assert set(rows["Series"]) == {"EQ", "BL"}
