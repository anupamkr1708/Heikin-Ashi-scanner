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

    provider = NSEMainboardEquityUniverseProvider(min_count=1, max_count=100)
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

    provider = NSEMainboardEquityUniverseProvider(min_count=1, max_count=100)
    constituents, _source, _retrieved_at = provider.get_constituents()
    assert len(constituents) == len(SYNTHETIC_SYMBOLS) - 1


def test_mainboard_provider_hard_fails_on_download_error(monkeypatch):
    def _raise():
        raise DataProviderError("simulated network failure")

    monkeypatch.setattr("nse_scanner.universe.mainboard.fetch_security_file", _raise)

    provider = NSEMainboardEquityUniverseProvider()
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
    provider = NSEMainboardEquityUniverseProvider(min_count=500, max_count=4000)
    with pytest.raises(UniverseIntegrityError):
        provider.get_constituents()
