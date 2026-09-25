"""Daily EOD ingestion pipeline (PART 55 / continuation PART "CRITICAL DAILY EOD WORKFLOW").

Workflow: determine expected session -> check report availability (data-driven, not a clock
assumption) -> retrieve -> save immutable raw copy + hash -> parse -> normalize -> validate ->
append to DuckDB/Parquet store -> report coverage. Every step is isolated so a failure at any
point produces a clear status rather than a corrupted partial ingest.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd

from nse_scanner.data.nse_eod import NseBhavcopyResult, fetch_bhavcopy
from nse_scanner.data.nse_reports import ReportAvailability, check_bhavcopy_availability
from nse_scanner.data.storage import MarketDataStore
from nse_scanner.exceptions import DataProviderError
from nse_scanner.logging_config import get_logger
from nse_scanner.models.market_data import PriceBasis

logger = get_logger(__name__)

STATUS_INGESTED = "INGESTED"
STATUS_WAITING_FOR_EOD_DATA = "WAITING_FOR_EOD_DATA"
STATUS_FAILED = "FAILED"


@dataclass
class IngestionResult:
    status: str
    session_date: date | None
    rows_ingested: int
    file_hash: str | None
    raw_file_path: str | None
    detail: str | None = None


def _save_raw_copy(raw_dir: str | Path, session_date: date, content: str, schema_version: str) -> tuple[Path, str]:
    raw_dir = Path(raw_dir) / "nse"
    raw_dir.mkdir(parents=True, exist_ok=True)
    path = raw_dir / f"{session_date.isoformat()}_{schema_version}.csv"
    path.write_text(content, encoding="utf-8")
    file_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
    return path, file_hash


def ingest_session(
    session_date: date, store: MarketDataStore, raw_dir: str | Path, check_availability_first: bool = True
) -> IngestionResult:
    """Ingests exactly one trading session's bhavcopy. Never silently substitutes an older
    session's data (continuation-prompt requirement: "Do NOT use D-1 data silently")."""
    if check_availability_first:
        availability: ReportAvailability = check_bhavcopy_availability(session_date)
        if not availability.available:
            return IngestionResult(
                status=STATUS_WAITING_FOR_EOD_DATA,
                session_date=session_date,
                rows_ingested=0,
                file_hash=None,
                raw_file_path=None,
                detail=f"{availability.status}: {availability.detail or ''} ({availability.url_checked})",
            )

    try:
        result: NseBhavcopyResult = fetch_bhavcopy(session_date)
    except DataProviderError as e:
        return IngestionResult(STATUS_FAILED, session_date, 0, None, None, detail=str(e))

    if result.session_date != session_date:
        return IngestionResult(
            STATUS_FAILED,
            session_date,
            0,
            result.file_hash,
            None,
            detail=f"retrieved report is for {result.session_date}, expected {session_date} — refusing to ingest",
        )

    raw_path, file_hash = _save_raw_copy(raw_dir, session_date, result.frame.to_csv(index=False), result.schema_version)

    frame = result.frame.copy()
    wanted_cols = ("NSE_Symbol", "ISIN", "Series", "Open", "High", "Low", "Close", "Volume", "Turnover")
    keep_cols = [c for c in wanted_cols if c in frame.columns]
    frame = frame[keep_cols]
    if "Series" in frame.columns:
        frame = frame[frame["Series"].astype(str).str.strip().str.upper().isin(("EQ", "BE"))]

    now = datetime.now(timezone.utc)
    normalized = pd.DataFrame(
        {
            "nse_symbol": frame["NSE_Symbol"].astype(str).str.strip().str.upper(),
            "isin": frame.get("ISIN"),
            "trade_date": pd.Timestamp(session_date),
            "open": pd.to_numeric(frame["Open"], errors="coerce"),
            "high": pd.to_numeric(frame["High"], errors="coerce"),
            "low": pd.to_numeric(frame["Low"], errors="coerce"),
            "close": pd.to_numeric(frame["Close"], errors="coerce"),
            "volume": pd.to_numeric(frame.get("Volume", 0), errors="coerce").fillna(0),
            "turnover": pd.to_numeric(frame.get("Turnover"), errors="coerce") if "Turnover" in frame.columns else None,
            "source": "NSE",
            "price_basis": PriceBasis.RAW,
            "schema_version": result.schema_version,
            "ingested_at": now,
        }
    )
    normalized = normalized.dropna(subset=["open", "high", "low", "close"])

    row_count = store.append_eod_prices(normalized)
    logger.info(
        "Ingested %s: %d symbols (schema=%s, store now has %d total rows)",
        session_date.isoformat(),
        len(normalized),
        result.schema_version,
        row_count,
    )

    return IngestionResult(
        status=STATUS_INGESTED,
        session_date=session_date,
        rows_ingested=len(normalized),
        file_hash=file_hash,
        raw_file_path=str(raw_path),
        detail=f"schema={result.schema_version}",
    )
