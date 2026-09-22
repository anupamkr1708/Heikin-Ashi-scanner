"""NSE report discovery layer.

Isolates "which URL corresponds to which (report_type, date)" so that if NSE changes its public
file layout again, only this module (plus the two parsers it feeds — data/nse_eod.py for
Bhavcopy, this module's `parse_security_file` for the security master) needs to change; nothing
else in the codebase references an NSE URL directly.

Two report types are modeled, matching NSE's current (as of this repository's knowledge cutoff)
"All Reports" page for the Capital Market segment:

    ReportType.CM_UDIFF_BHAVCOPY  -> daily OHLCV for every traded security (data/nse_eod.py)
    ReportType.CM_SECURITY_FILE   -> the security master / list of securities available for
                                      trading (parsed here)

**Honesty note:** this repository's build sandbox has no network path to nseindia.com, and the
user's own attached "real NSE fixture files" did not actually arrive in this conversation's
uploaded-files list (only the original legacy notebook did — see README "Known limitations").
The exact current column names for the CM Security File below are therefore this module's best
documented guess, not a verified schema. `parse_security_file` is written defensively (tries
several known/likely column-name variants, fails loudly and lists the columns it actually saw
if none match) specifically because of that uncertainty — see PART 3's resilience requirement.
Before production use, run it once against a real downloaded file and adjust
`SECURITY_FILE_COLUMN_CANDIDATES` if needed; that is a data/config change, not a redesign.
"""

from __future__ import annotations

import gzip
import io
from dataclasses import dataclass
from datetime import date, datetime, timezone

import pandas as pd
import requests

from nse_scanner.data.nse_eod import NSE_HEADERS, NSE_HOMEPAGE, UDIFF_URL_TEMPLATE
from nse_scanner.exceptions import DataProviderError
from nse_scanner.logging_config import get_logger

logger = get_logger(__name__)


class ReportType:
    CM_UDIFF_BHAVCOPY = "CM_UDIFF_BHAVCOPY"
    CM_SECURITY_FILE = "CM_SECURITY_FILE"


# Candidate URL templates for the CM-MII security file. NSE has published this under a couple of
# naming conventions historically; tried in order, first success wins (PART 3 resilience).
SECURITY_FILE_URL_TEMPLATES = (
    "https://nsearchives.nseindia.com/content/cm/NSE_CM_security_{ddmmyyyy}.csv.gz",
    "https://nsearchives.nseindia.com/content/equity_bhavcopy/security_{ddmmyyyy}.csv.gz",
    "https://nsearchives.nseindia.com/content/equity/EQUITY_L.csv",  # last resort: undated master list
)

# Column-name variants this parser will accept for the security master file, mapped to the
# canonical name used internally. Extend this if a real file uses a variant not listed.
SECURITY_FILE_COLUMN_CANDIDATES: dict[str, tuple[str, ...]] = {
    "NSE_Symbol": ("SYMBOL", "TckrSymb", "Symbol"),
    "Company_Name": ("NAME OF COMPANY", "NAME_OF_COMPANY", "FinInstrmNm", "CompanyName"),
    "Series": ("SERIES", "SctySrs", "Series"),
    "ISIN": ("ISIN NUMBER", "ISIN_NUMBER", "ISIN", "ISINCode"),
    "Date_of_Listing": ("DATE OF LISTING", "DATE_OF_LISTING", "ListingDate"),
    "Face_Value": ("FACE VALUE", "FACE_VALUE", "FaceVal"),
    "Status": ("STATUS", "Status"),
}


@dataclass(frozen=True)
class ReportAvailability:
    available: bool
    url_checked: str | None
    status: str  # "AVAILABLE" | "NOT_YET_PUBLISHED" | "UNREACHABLE"
    detail: str | None = None


def check_bhavcopy_availability(session_date: date, timeout: int = 10) -> ReportAvailability:
    """HEAD-checks (falling back to a light GET if HEAD is disallowed) whether the UDiFF bhavcopy
    for `session_date` exists yet, WITHOUT downloading the full file. Used by run_daily.py to
    decide WAITING_FOR_EOD_DATA vs proceeding (continuation-prompt requirement: EOD availability
    must be data-driven, not `15:30 == available`)."""
    url = UDIFF_URL_TEMPLATE.format(yyyymmdd=session_date.strftime("%Y%m%d"))
    session = requests.Session()
    session.headers.update(NSE_HEADERS)
    try:
        session.get(NSE_HOMEPAGE, timeout=timeout)
    except requests.RequestException as e:
        logger.warning("NSE homepage warm-up failed: %s", e)

    try:
        resp = session.head(url, timeout=timeout, allow_redirects=True)
        if resp.status_code == 200:
            return ReportAvailability(True, url, "AVAILABLE")
        if resp.status_code in (403, 405):  # some NSE endpoints reject HEAD; try a ranged GET
            resp = session.get(url, timeout=timeout, headers={"Range": "bytes=0-0"})
            if resp.status_code in (200, 206):
                return ReportAvailability(True, url, "AVAILABLE")
        if resp.status_code == 404:
            return ReportAvailability(False, url, "NOT_YET_PUBLISHED")
        return ReportAvailability(False, url, "UNREACHABLE", detail=f"HTTP {resp.status_code}")
    except requests.RequestException as e:
        return ReportAvailability(False, url, "UNREACHABLE", detail=f"{type(e).__name__}: {e}")


@dataclass(frozen=True)
class SecurityFileResult:
    frame: pd.DataFrame
    source_url: str
    retrieved_at: "datetime"
    file_hash: str
    row_count: int


def fetch_security_file(timeout: int = 20) -> SecurityFileResult:
    """Downloads and parses the current CM-MII security master file, trying each candidate URL
    in `SECURITY_FILE_URL_TEMPLATES` in order (PART 3 resilience — first success wins).

    Unlike the bhavcopy, the security master is not naturally keyed to a single trading session,
    so this fetches "the current file" rather than one for a specific date; templates that
    embed a date substitute today's date (IST-naive here — callers doing date-sensitive
    provenance should record their own retrieval timestamp, which this result also returns).
    Raises DataProviderError if every candidate URL fails.
    """
    import gzip as _gzip
    import hashlib

    session = requests.Session()
    session.headers.update(NSE_HEADERS)
    try:
        session.get(NSE_HOMEPAGE, timeout=timeout)
    except requests.RequestException as e:
        logger.warning("NSE homepage warm-up failed (continuing anyway): %s", e)

    today = date.today()
    attempts: list[str] = []
    for template in SECURITY_FILE_URL_TEMPLATES:
        url = template.format(ddmmyyyy=today.strftime("%d%m%Y"))
        try:
            resp = session.get(url, timeout=timeout)
            resp.raise_for_status()
            content = resp.content
            is_gzip = url.endswith(".gz")
            frame = parse_security_file(content, is_gzip=is_gzip)
            raw_for_hash = _gzip.decompress(content) if is_gzip else content
            file_hash = hashlib.sha256(raw_for_hash).hexdigest()
            logger.info("Security file source OK: %s (%d rows)", url, len(frame))
            return SecurityFileResult(
                frame=frame, source_url=url, retrieved_at=datetime.now(timezone.utc),
                file_hash=file_hash, row_count=len(frame),
            )
        except Exception as e:  # noqa: BLE001 - one URL failing tries the next
            attempts.append(f"{url} -> {type(e).__name__}: {e}")
            logger.warning("Security file source failed [%s]: %s: %s", url, type(e).__name__, e)

    raise DataProviderError(
        "Could not retrieve the NSE CM security master file from any known URL pattern "
        "(PART 3 — NSE may have changed the file naming/schema again). "
        "Attempts:\n" + "\n".join(f"  - {a}" for a in attempts)
    )


def parse_security_file(raw_bytes: bytes, is_gzip: bool = True) -> pd.DataFrame:
    """Parses the CM-MII security file into a DataFrame with canonical column names.

    Raises DataProviderError (never returns a silently-wrong/partial frame) if none of the known
    column-name variants for a REQUIRED field (NSE_Symbol, Company_Name, Series) are found —
    see module docstring on schema uncertainty.
    """
    text = gzip.decompress(raw_bytes).decode("utf-8") if is_gzip else raw_bytes.decode("utf-8")
    df = pd.read_csv(io.StringIO(text))
    df.columns = [c.strip() for c in df.columns]

    rename_map: dict[str, str] = {}
    for canonical, candidates in SECURITY_FILE_COLUMN_CANDIDATES.items():
        for cand in candidates:
            if cand in df.columns:
                rename_map[cand] = canonical
                break
    df = df.rename(columns=rename_map)

    required = ("NSE_Symbol", "Company_Name", "Series")
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise DataProviderError(
            f"Security file is missing required field(s) {missing} after applying known column-name "
            f"variants. Columns actually present: {list(df.columns)}. Update "
            f"SECURITY_FILE_COLUMN_CANDIDATES in data/nse_reports.py to match the real file."
        )

    df["NSE_Symbol"] = df["NSE_Symbol"].astype(str).str.strip().str.upper()
    df["Series"] = df["Series"].astype(str).str.strip().str.upper()
    return df


def filter_mainboard_equity(security_df: pd.DataFrame) -> pd.DataFrame:
    """NSE_MAINBOARD_EQ universe filter (PART 5 / continuation universe definitions): keeps only
    the standard equity series, excluding ETFs/SME/debt/etc. 'EQ' and 'BE' are the standard
    mainboard equity series; SME uses 'SM'/'ST', ETFs use 'EQ' too in some feeds but are
    typically separately flagged — this filter is intentionally conservative (EQ/BE only) and
    documented as such rather than silently guessing at other series codes."""
    return security_df[security_df["Series"].isin(("EQ", "BE"))].copy()
