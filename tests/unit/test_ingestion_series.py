from datetime import date

import pandas as pd
from nse_scanner.data.nse_eod import NseBhavcopyResult
from nse_scanner.pipeline import ingestion


def test_ingest_session_keeps_bz_and_excludes_sme(tmp_path, monkeypatch):
    frame = pd.DataFrame(
        [
            {
                "NSE_Symbol": "TESTBZ",
                "ISIN": "INE000B01001",
                "Series": "BZ",
                "Open": 10.0,
                "High": 11.0,
                "Low": 9.0,
                "Close": 10.5,
                "Volume": 1000,
                "Turnover": 10500.0,
            },
            {
                "NSE_Symbol": "TESTSM",
                "ISIN": "INE000S01001",
                "Series": "SM",
                "Open": 20.0,
                "High": 21.0,
                "Low": 19.0,
                "Close": 20.5,
                "Volume": 2000,
                "Turnover": 41000.0,
            },
        ]
    )

    result = NseBhavcopyResult(
        session_date=date(2026, 9, 29),
        frame=frame,
        source_url="https://example.test/bhavcopy.csv",
        schema_version="UDIFF",
        retrieved_at=pd.Timestamp("2026-09-30", tz="UTC").to_pydatetime(),
        file_hash="test-hash",
        row_count=len(frame),
    )

    captured: dict[str, pd.DataFrame] = {}

    class FakeStore:
        def append_eod_prices(self, df: pd.DataFrame) -> int:
            captured["df"] = df.copy()
            return len(df)

    monkeypatch.setattr(
        ingestion,
        "fetch_bhavcopy",
        lambda _session_date: result,
    )

    outcome = ingestion.ingest_session(
        date(2026, 9, 29),
        FakeStore(),
        tmp_path,
        check_availability_first=False,
    )

    assert outcome.status == ingestion.STATUS_INGESTED
    assert outcome.rows_ingested == 1

    stored = captured["df"]

    assert stored["nse_symbol"].tolist() == ["TESTBZ"]
    assert stored["isin"].tolist() == ["INE000B01001"]
    assert stored["source"].tolist() == ["NSE"]
    assert stored["price_basis"].tolist() == ["RAW"]
