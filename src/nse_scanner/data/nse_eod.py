"""NSE official EOD (End-of-Day) bhavcopy provider — the PRIMARY data path (PART 3 / PART 7).

NSE publishes a daily "Common Bhavcopy Final" (UDiFF format) archive for the Capital Market
(equity) segment. This module downloads and parses that file for a single trading session. It
is deliberately isolated behind the DataProvider interface (PART 8) so the rest of the system
never depends on NSE's specific URL scheme or column names.

**Resilience (PART 3):** NSE has changed this file's URL and column schema before (the classic
per-day "cm<DDMMMYYYY>bhav.csv" format was superseded by the UDiFF "BhavCopy_NSE_CM_..." format).
This module tries a primary URL template and falls back to a documented legacy template, and
raises a clear `DataProviderError` (never a silent empty result) if both fail or the response
does not match either expected schema — it does not guess at a third format.

**Sandbox note:** this build/CI environment's network egress allow-list does not include
nseindia.com, so the live HTTP path below could not be exercised while writing this repository
(see CODE_REVIEW.md and README "Known limitations"). The parsing/normalization logic IS unit
tested against saved sample bhavcopy fixtures (tests/fixtures/sample_udiff.csv). Before relying
on this provider in production: (1) confirm the current file naming convention against
https://www.nseindia.com/all-reports (Capital Market > Bhavcopy), (2) run
`pytest tests/integration/test_nse_parser.py -m live` once with network access.
"""

from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass, field
from datetime import date, datetime, timezone

import pandas as pd
import requests

from nse_scanner.data.base import DataProvider
from nse_scanner.exceptions import DataProviderError
from nse_scanner.logging_config import get_logger
from nse_scanner.models.market_data import NormalizedBarMeta

logger = get_logger(__name__)

NSE_HOMEPAGE = "https://www.nseindia.com"
NSE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 " "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "text/csv,application/csv,application/zip,text/plain,*/*",
    "Accept-Language": "en-US,en;q=0.9",
}

# UDiFF Common Bhavcopy Final (current primary format as of this repository's knowledge cutoff).
UDIFF_URL_TEMPLATE = "https://nsearchives.nseindia.com/content/cm/" "BhavCopy_NSE_CM_0_0_0_{yyyymmdd}_F_0000.csv.zip"
# Legacy per-day bhavcopy CSV (kept as a documented fallback — PART 3 resilience requirement).
LEGACY_URL_TEMPLATE = (
    "https://nsearchives.nseindia.com/content/historical/EQUITIES/" "{year}/{mon}/cm{ddmmmyyyy}bhav.csv.zip"
)

# UDiFF column names -> canonical OHLCV. NSE's UDiFF schema uses these field names as of this
# repository's knowledge cutoff; PART 3 requires adapting this map if NSE changes it.
UDIFF_COLUMN_MAP = {
    "TckrSymb": "NSE_Symbol",
    "ISIN": "ISIN",
    "SctySrs": "Series",
    "OpnPric": "Open",
    "HghPric": "High",
    "LwPric": "Low",
    "ClsPric": "Close",
    "TtlTradgVol": "Volume",
    "TtlTrfVal": "Turnover",
    "TradDt": "TradeDate",
}
LEGACY_COLUMN_MAP = {
    "SYMBOL": "NSE_Symbol",
    "SERIES": "Series",
    "OPEN": "Open",
    "HIGH": "High",
    "LOW": "Low",
    "CLOSE": "Close",
    "TOTTRDQTY": "Volume",
    "TOTTRDVAL": "Turnover",
    "TIMESTAMP": "TradeDate",
    "ISIN": "ISIN",
}


@dataclass
class NseBhavcopyResult:
    session_date: date
    frame: pd.DataFrame  # long-format: one row per security for this session
    source_url: str
    schema_version: str  # "UDIFF" | "LEGACY"
    retrieved_at: datetime
    file_hash: str
    row_count: int


def _warm_up_session(session: requests.Session, timeout: int) -> None:
    try:
        session.get(NSE_HOMEPAGE, timeout=timeout)
    except requests.RequestException as e:
        logger.warning("NSE homepage warm-up request failed (continuing anyway): %s: %s", type(e).__name__, e)


def _download_and_unzip_csv(session: requests.Session, url: str, timeout: int) -> str:
    resp = session.get(url, timeout=timeout)
    resp.raise_for_status()
    content = resp.content
    if url.endswith(".zip"):
        with zipfile.ZipFile(io.BytesIO(content)) as zf:
            names = [n for n in zf.namelist() if n.lower().endswith(".csv")]
            if not names:
                raise DataProviderError(f"no CSV found inside zip archive at {url}")
            with zf.open(names[0]) as f:
                return f.read().decode("utf-8")
    return content.decode("utf-8")


def fetch_bhavcopy(session_date: date, timeout: int = 20) -> NseBhavcopyResult:
    """Fetches and normalizes the CM bhavcopy for exactly one trading session.

    Raises DataProviderError if neither the UDiFF nor the legacy source can be retrieved and
    parsed — this is a systemic failure, not a per-symbol one, so it propagates rather than
    being swallowed (PART 66 Gate 2).
    """
    import hashlib

    session = requests.Session()
    session.headers.update(NSE_HEADERS)
    _warm_up_session(session, timeout)

    yyyymmdd = session_date.strftime("%Y%m%d")
    udiff_url = UDIFF_URL_TEMPLATE.format(yyyymmdd=yyyymmdd)
    attempts: list[str] = []

    try:
        text = _download_and_unzip_csv(session, udiff_url, timeout)
        df = pd.read_csv(io.StringIO(text))
        df = df.rename(columns={k: v for k, v in UDIFF_COLUMN_MAP.items() if k in df.columns})
        missing = [c for c in ("NSE_Symbol", "Open", "High", "Low", "Close", "Volume") if c not in df.columns]
        if missing:
            raise DataProviderError(f"UDiFF response missing expected columns: {missing}")
        file_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        return NseBhavcopyResult(
            session_date=session_date,
            frame=df,
            source_url=udiff_url,
            schema_version="UDIFF",
            retrieved_at=datetime.now(timezone.utc),
            file_hash=file_hash,
            row_count=len(df),
        )
    except Exception as e:  # noqa: BLE001 - deliberately broad: fall through to legacy format
        attempts.append(f"UDIFF[{udiff_url}] -> {type(e).__name__}: {e}")

    ddmmmyyyy = session_date.strftime("%d%b%Y").upper()
    mon = session_date.strftime("%b").upper()
    legacy_url = LEGACY_URL_TEMPLATE.format(year=session_date.year, mon=mon, ddmmmyyyy=ddmmmyyyy)
    try:
        text = _download_and_unzip_csv(session, legacy_url, timeout)
        df = pd.read_csv(io.StringIO(text))
        df = df.rename(columns={k: v for k, v in LEGACY_COLUMN_MAP.items() if k in df.columns})
        missing = [c for c in ("NSE_Symbol", "Open", "High", "Low", "Close", "Volume") if c not in df.columns]
        if missing:
            raise DataProviderError(f"legacy bhavcopy response missing expected columns: {missing}")
        file_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        return NseBhavcopyResult(
            session_date=session_date,
            frame=df,
            source_url=legacy_url,
            schema_version="LEGACY",
            retrieved_at=datetime.now(timezone.utc),
            file_hash=file_hash,
            row_count=len(df),
        )
    except Exception as e:  # noqa: BLE001
        attempts.append(f"LEGACY[{legacy_url}] -> {type(e).__name__}: {e}")

    raise DataProviderError(
        f"Could not retrieve NSE bhavcopy for {session_date.isoformat()} from any known URL "
        f"pattern. This can mean NSE changed the file naming/schema again (see PART 3) — check "
        f"https://www.nseindia.com/all-reports and update UDIFF_URL_TEMPLATE / "
        f"LEGACY_URL_TEMPLATE / *_COLUMN_MAP. Attempts:\n" + "\n".join(f"  - {a}" for a in attempts)
    )


@dataclass
class NSEDataProvider(DataProvider):
    """Adapts single-session bhavcopy fetches into the multi-day DataProvider interface by
    reading previously-ingested sessions out of local storage (see pipeline/ingestion.py,
    which is what actually calls `fetch_bhavcopy` day-by-day and appends to storage).

    This provider does NOT itself reconstruct a multi-year "period" history from bhavcopy files
    on demand — NSE's bhavcopy is a daily file, one HTTP request per session, so building 2
    years of history this way means ~500 sequential requests. That incremental accumulation is
    exactly the daily ingestion pipeline's job (PART 55), not something this class does inline.
    `fetch_history` therefore reads from the local Parquet/DuckDB store that ingestion has been
    populating, and reports a clear error if the store doesn't have enough history yet — it does
    NOT silently fall back to yfinance (that fallback, where desired, is an explicit choice made
    by the caller/pipeline, not hidden inside this class — PART 8).
    """

    name: str = "NSE"
    price_basis: str = "RAW"  # static fallback label; fetch_history reports the REAL per-symbol
    # basis (which can be BLENDED if bootstrap history was used) via
    # the returned NormalizedBarMeta.price_basis instead of this.
    store: object = field(default=None)  # nse_scanner.data.storage.MarketDataStore, injected

    def fetch_history(
        self, symbol: str, period: str, interval: str
    ) -> tuple[pd.DataFrame | None, NormalizedBarMeta | None, str | None]:
        if self.store is None:
            return None, None, "NSEDataProvider has no local store configured — run ingest_eod.py first"
        df = self.store.read_symbol_history(symbol)  # type: ignore[attr-defined]
        if df is None or df.empty:
            return None, None, "no locally-ingested NSE history for this symbol"
        actual_price_basis = self.store.price_basis_composition(symbol)  # type: ignore[attr-defined]
        meta = NormalizedBarMeta(
            isin=None,
            nse_symbol=symbol,
            source=self.name,
            price_basis=actual_price_basis,
            interval=interval,
            retrieved_at=datetime.now(timezone.utc),
            provider_version="bhavcopy",
        )
        return df, meta, None

    def fetch_history_batch(
        self, symbols: list[str], period: str, interval: str
    ) -> tuple[dict[str, pd.DataFrame], dict[str, str]]:
        results: dict[str, pd.DataFrame] = {}
        errors: dict[str, str] = {}
        for sym in symbols:
            df, _meta, err = self.fetch_history(sym, period, interval)
            if df is not None:
                results[sym] = df
            else:
                errors[sym] = err or "unknown error"
        return results, errors
