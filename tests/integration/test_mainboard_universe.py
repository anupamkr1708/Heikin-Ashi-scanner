import pandas as pd
import pytest
from nse_scanner.data.nse_reports import SecurityFileResult
from nse_scanner.exceptions import DataProviderError, UniverseIntegrityError
from nse_scanner.testing.synthetic_market_data import SYNTHETIC_SYMBOLS, generate_synthetic_security_file
from nse_scanner.universe.mainboard import NSEMainboardEquityUniverseProvider


def _fake_security_result(frame: pd.DataFrame) -> SecurityFileResult:
    import pandas as pd

    return SecurityFileResult(
        frame=frame,
        source_url="https://fake/security_file.csv.gz",
        retrieved_at=pd.Timestamp("2026-09-13", tz="UTC"),
        file_hash="deadbeef",
        row_count=len(frame),
    )


def test_mainboard_provider_derives_universe_from_security_master(monkeypatch):
    raw = generate_synthetic_security_file()  # SYMBOL/NAME OF COMPANY/SERIES/ISIN NUMBER columns
    import gzip

    from nse_scanner.data.nse_reports import parse_security_file

    parsed = parse_security_file(gzip.compress(raw.to_csv(index=False).encode("utf-8")))

    monkeypatch.setattr("nse_scanner.universe.mainboard.fetch_security_file", lambda: _fake_security_result(parsed))

    provider = NSEMainboardEquityUniverseProvider(min_count=1, max_count=100, auto_fetch_exclusion_lists=False)
    constituents, source, retrieved_at = provider.get_constituents()

    assert set(constituents["Symbol"]) == set(SYNTHETIC_SYMBOLS)
    assert "Company_Name" in constituents.columns
    assert source == "https://fake/security_file.csv.gz"


def test_mainboard_provider_excludes_non_eq_be_series(monkeypatch):
    raw = generate_synthetic_security_file()
    raw.loc[0, "SERIES"] = "SM"  # inject one SME-series row
    import gzip

    from nse_scanner.data.nse_reports import parse_security_file

    parsed = parse_security_file(gzip.compress(raw.to_csv(index=False).encode("utf-8")))

    monkeypatch.setattr("nse_scanner.universe.mainboard.fetch_security_file", lambda: _fake_security_result(parsed))

    provider = NSEMainboardEquityUniverseProvider(min_count=1, max_count=100, auto_fetch_exclusion_lists=False)
    constituents, _source, _retrieved_at = provider.get_constituents()
    assert len(constituents) == len(SYNTHETIC_SYMBOLS) - 1


def test_mainboard_provider_hard_fails_on_download_error(monkeypatch):
    def _raise():
        raise DataProviderError("simulated network failure")

    monkeypatch.setattr("nse_scanner.universe.mainboard.fetch_security_file", _raise)

    provider = NSEMainboardEquityUniverseProvider(auto_fetch_exclusion_lists=False)
    with pytest.raises(UniverseIntegrityError):
        provider.get_constituents()


def test_mainboard_provider_hard_fails_on_implausible_count(monkeypatch):
    raw = generate_synthetic_security_file()
    import gzip

    from nse_scanner.data.nse_reports import parse_security_file

    parsed = parse_security_file(gzip.compress(raw.to_csv(index=False).encode("utf-8")))

    monkeypatch.setattr("nse_scanner.universe.mainboard.fetch_security_file", lambda: _fake_security_result(parsed))

    # Sanity bounds set far above what the tiny synthetic fixture can satisfy -> must hard-fail,
    # never silently accept an implausibly small "mainboard" universe.
    provider = NSEMainboardEquityUniverseProvider(min_count=500, max_count=4000, auto_fetch_exclusion_lists=False)
    with pytest.raises(UniverseIntegrityError):
        provider.get_constituents()


def test_mainboard_provider_default_cardinality_gate_matches_equity_l_evidence():
    """mainboard-universe-integrity-v2 (Task 6): the default [min_count, max_count] must be the
    evidenced range derived from EQUITY_L.csv's real observed shape this session (2,585 rows), not
    the prior [500, 4000] guessed against an unverified CM-MII master figure."""
    provider = NSEMainboardEquityUniverseProvider()
    assert provider.min_count == 1500
    assert provider.max_count == 3500
    # the real, evidenced count this session must fall inside the gate
    assert provider.min_count <= 2585 <= provider.max_count


def test_mainboard_provider_merges_auto_fetched_exclusions_with_manual_overrides(monkeypatch):
    """The auto-fetch wiring (Task 7), exercised WITHOUT any live network call: monkeypatches
    fetch_all_instrument_lists itself (not the network underneath it) so this stays a fast,
    offline unit test while still proving the real merge/attribution logic in
    universe/mainboard.py works — manual overrides and auto-fetched categories both apply, and
    each excluded row is attributed to the correct category."""
    import gzip

    from nse_scanner.data.nse_instrument_lists import InstrumentListResult
    from nse_scanner.data.nse_reports import parse_security_file

    raw = generate_synthetic_security_file()
    parsed = parse_security_file(gzip.compress(raw.to_csv(index=False).encode("utf-8")))
    monkeypatch.setattr("nse_scanner.universe.mainboard.fetch_security_file", lambda: _fake_security_result(parsed))

    etf_symbol = SYNTHETIC_SYMBOLS[0]
    manual_symbol = SYNTHETIC_SYMBOLS[1]

    def _fake_fetch_all(timeout=20):
        results = {
            "ETF": InstrumentListResult(
                category="ETF",
                symbols=frozenset({etf_symbol}),
                isins=frozenset(),
                source_url="https://fake/eq_etfseclist.csv",
                retrieved_at=pd.Timestamp("2026-09-13", tz="UTC"),
                row_count=1,
                file_hash="deadbeef",
            )
        }
        statuses = {"ETF": "FETCHED", "REIT": "UNAVAILABLE: simulated", "InvIT": "FETCHED", "SME": "FETCHED"}
        return results, statuses

    monkeypatch.setattr("nse_scanner.universe.mainboard.fetch_all_instrument_lists", _fake_fetch_all)

    provider = NSEMainboardEquityUniverseProvider(
        min_count=1, max_count=100, excluded_symbols=frozenset({manual_symbol})
    )
    constituents, _source, _retrieved_at = provider.get_constituents()

    symbols = set(constituents["Symbol"])
    assert etf_symbol not in symbols
    assert manual_symbol not in symbols
    assert len(constituents) == len(SYNTHETIC_SYMBOLS) - 2

    assert provider.last_diagnostics["excluded_by_category"] == {"ETF": 1, "manual_override": 1}
    assert provider.last_diagnostics["exclusion_list_status"]["ETF"] == "FETCHED"
    assert provider.last_diagnostics["exclusion_list_status"]["REIT"] == "UNAVAILABLE: simulated"
    assert provider.last_diagnostics["exclusion_list_status"]["manual_override"] == "PROVIDED"
    assert provider.last_diagnostics["universe_definition_id"] == "nse_mainboard_equity_v2"


def test_mainboard_provider_proceeds_without_exclusion_when_all_lists_unavailable(monkeypatch):
    """Task 7's non-fatal failure policy for supplementary exclusion lists (see
    data/nse_instrument_lists.py module docstring): every category failing to fetch must NOT fail
    the whole mainboard run -- it must simply exclude nothing for those categories, with the
    failure explicitly visible in diagnostics rather than silent."""
    import gzip

    from nse_scanner.data.nse_reports import parse_security_file

    raw = generate_synthetic_security_file()
    parsed = parse_security_file(gzip.compress(raw.to_csv(index=False).encode("utf-8")))
    monkeypatch.setattr("nse_scanner.universe.mainboard.fetch_security_file", lambda: _fake_security_result(parsed))
    monkeypatch.setattr(
        "nse_scanner.universe.mainboard.fetch_all_instrument_lists",
        lambda timeout=20: ({}, {c: "UNAVAILABLE: simulated" for c in ("ETF", "REIT", "InvIT", "SME")}),
    )

    provider = NSEMainboardEquityUniverseProvider(min_count=1, max_count=100)
    constituents, _source, _retrieved_at = provider.get_constituents()

    assert set(constituents["Symbol"]) == set(SYNTHETIC_SYMBOLS)  # nothing excluded
    assert all(v.startswith("UNAVAILABLE") for v in provider.last_diagnostics["exclusion_list_status"].values())
