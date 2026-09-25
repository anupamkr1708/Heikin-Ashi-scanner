"""Regression coverage for NSEMainboardEquityUniverseProvider's `excluded_symbols`/`excluded_isins`
hook. This is explicitly a MECHANISM test, not an "ETFs are excluded" claim: no authoritative
ETF/REIT/InvIT list is wired in anywhere in this codebase (see the mainboard-universe-semantics
forensic report -- the exact field/file for that remains unresolved). These tests prove two
separate things:
  1. With the default empty exclusion sets, today's universe is BYTE-IDENTICAL to before this
     hook existed -- adding the mechanism must not itself change any live behavior.
  2. IF a list is supplied (here, synthetic test-only symbols -- never a real ETF/REIT list),
     exclusion and diagnostics behave correctly. This is what lets a future patch wire in a real,
     authoritative list with confidence, without re-deriving this logic from scratch.
"""

from __future__ import annotations

import pandas as pd
from nse_scanner.data.nse_reports import SecurityFileResult
from nse_scanner.testing.synthetic_market_data import generate_mainboard_semantics_fixture_cases
from nse_scanner.universe.mainboard import NSEMainboardEquityUniverseProvider


def _fake_security_result(frame: pd.DataFrame) -> SecurityFileResult:
    return SecurityFileResult(
        frame=frame,
        source_url="https://fake/security_file.csv.gz",
        retrieved_at=pd.Timestamp("2026-09-24", tz="UTC"),
        file_hash="deadbeef",
        row_count=len(frame),
        source_date=pd.Timestamp("2026-09-23").date(),
    )


def _provider_with_fixture_cases(monkeypatch, **kwargs):
    import gzip as _gzip

    from nse_scanner.data.nse_reports import parse_security_file

    raw = generate_mainboard_semantics_fixture_cases()
    parsed = parse_security_file(_gzip.compress(raw.to_csv(index=False).encode("utf-8")))
    monkeypatch.setattr(
        "nse_scanner.universe.mainboard.fetch_security_file",
        lambda: _fake_security_result(parsed),
    )
    return NSEMainboardEquityUniverseProvider(min_count=1, max_count=100, **kwargs)


def test_default_empty_exclusion_sets_change_nothing(monkeypatch):
    """The regression that matters most: adding this hook must not alter today's universe."""
    provider_before = _provider_with_fixture_cases(monkeypatch)  # no excluded_symbols/isins passed
    constituents, _, _ = provider_before.get_constituents()

    assert provider_before.excluded_symbols == frozenset()
    assert provider_before.excluded_isins == frozenset()
    assert provider_before.last_diagnostics["excluded_known_non_equity_count"] == 0
    # Same 8 constituents the diagnostics test file independently computes for this fixture.
    assert len(constituents) == 8
    assert "ETFCASE" in set(constituents["Symbol"])  # included -- no authoritative exclusion exists


def test_populated_symbol_exclusion_list_excludes_and_is_diagnosed(monkeypatch):
    provider = _provider_with_fixture_cases(
        monkeypatch, excluded_symbols=frozenset({"ETFCASE", "REITCASE", "INVITCASE"})
    )
    constituents, _, _ = provider.get_constituents()

    symbols = set(constituents["Symbol"])
    assert "ETFCASE" not in symbols
    assert "REITCASE" not in symbols
    assert "INVITCASE" not in symbols
    assert "NORMALEQ" in symbols  # unrelated rows unaffected
    assert provider.last_diagnostics["excluded_known_non_equity_count"] == 3
    assert provider.last_diagnostics["final_constituent_count"] == 5


def test_populated_isin_exclusion_list_excludes_by_isin(monkeypatch):
    raw = generate_mainboard_semantics_fixture_cases()
    normaleq_isin = raw.loc[raw["TckrSymb"] == "NORMALEQ", "ISIN"].iloc[0]

    provider = _provider_with_fixture_cases(monkeypatch, excluded_isins=frozenset({normaleq_isin}))
    constituents, _, _ = provider.get_constituents()

    # excludes ALL THREE rows sharing that ISIN pre-dedup (NORMALEQ's EQ row, its own BE row, and
    # DUPEISIN_SAMESERIES) -- ISIN-based exclusion applies before symbol-keyed dedup, unlike
    # identity/dedup itself, and correctly catches the BE row too since it's the same company/ISIN.
    assert "NORMALEQ" not in set(constituents["Symbol"])
    assert "DUPEISIN_SAMESERIES" not in set(constituents["Symbol"])
    assert provider.last_diagnostics["excluded_known_non_equity_count"] == 3


def test_last_diagnostics_is_none_before_get_constituents_is_called():
    provider = NSEMainboardEquityUniverseProvider()
    assert provider.last_diagnostics is None
