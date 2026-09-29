"""Regression coverage for NSEMainboardEquityUniverseProvider's `excluded_symbols`/`excluded_isins`
manual-override hook. As of mainboard-universe-integrity-v2, this hook is no longer the only
exclusion mechanism — `auto_fetch_exclusion_lists=True` (the production default) additionally
fetches real ETF/REIT/InvIT/SME lists via `data/nse_instrument_lists.py`. Every test in this file
passes `auto_fetch_exclusion_lists=False` explicitly, so these remain controlled, offline,
network-free tests of the MANUAL override mechanism specifically (never a live-network
dependency in CI) — real-list auto-fetch behavior is covered separately in
tests/unit/test_nse_instrument_lists.py (parsing, against the real files supplied this session)
and tests/integration/test_mainboard_universe.py (the merge-with-auto-fetch-disabled-but-present
wiring, via monkeypatched fetch_all_instrument_lists).
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
    kwargs.setdefault("auto_fetch_exclusion_lists", False)
    return NSEMainboardEquityUniverseProvider(min_count=1, max_count=100, **kwargs)


def test_default_empty_exclusion_sets_change_nothing(monkeypatch):
    """With auto-fetch disabled and no manual exclusions, adding both hooks must not alter the
    universe a bare filter_mainboard_equity + deduplicate_mainboard would produce."""
    provider_before = _provider_with_fixture_cases(monkeypatch)  # no excluded_symbols/isins passed
    constituents, _, _ = provider_before.get_constituents()

    assert provider_before.excluded_symbols == frozenset()
    assert provider_before.excluded_isins == frozenset()
    assert provider_before.last_diagnostics["excluded_known_non_equity_count"] == 0
    # mainboard-universe-integrity-v2: 9 now (was 8) -- SERIESCHANGED (BZ) is correctly included
    # now that filter_mainboard_equity's predicate covers BZ (see test_mainboard_diagnostics.py's
    # cardinality-funnel test for the full row-by-row accounting).
    assert len(constituents) == 9
    assert "ETFCASE" in set(constituents["Symbol"])  # included -- no exclusion source active here


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
    # mainboard-universe-integrity-v2: 6 now (was 5) -- base count is 9 now, not 8 (SERIESCHANGED/
    # BZ correctly included; see test_default_empty_exclusion_sets_change_nothing above).
    assert provider.last_diagnostics["final_constituent_count"] == 6
    assert provider.last_diagnostics["excluded_by_category"] == {"manual_override": 3}


def test_populated_isin_exclusion_list_excludes_by_isin(monkeypatch):
    raw = generate_mainboard_semantics_fixture_cases()
    normaleq_isin = raw.loc[raw["TckrSymb"] == "NORMALEQ", "ISIN"].iloc[0]

    provider = _provider_with_fixture_cases(monkeypatch, excluded_isins=frozenset({normaleq_isin}))
    constituents, _, _ = provider.get_constituents()

    # excludes ALL FOUR rows sharing that ISIN pre-dedup (NORMALEQ's EQ row, its own BE row,
    # DUPEISIN_SAMESERIES, and -- mainboard-universe-integrity-v2 -- SERIESCHANGED, whose BZ series
    # is now included by filter_mainboard_equity's predicate; was 3 before BZ was included).
    # ISIN-based exclusion applies before symbol-keyed dedup, unlike identity/dedup itself, and
    # correctly catches the BE row too since it's the same company/ISIN.
    assert "NORMALEQ" not in set(constituents["Symbol"])
    assert "DUPEISIN_SAMESERIES" not in set(constituents["Symbol"])
    assert "SERIESCHANGED" not in set(constituents["Symbol"])
    assert provider.last_diagnostics["excluded_known_non_equity_count"] == 4


def test_last_diagnostics_is_none_before_get_constituents_is_called():
    provider = NSEMainboardEquityUniverseProvider(auto_fetch_exclusion_lists=False)
    assert provider.last_diagnostics is None
