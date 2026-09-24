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
**P1 provider-hardening note (this revision):** a REAL production run hit persistent HTTP 404s on
all three candidate URLs for TODAY's exact date. Investigation (web search + direct fetch against
the live nseindia.com "All Reports" page, since this repository's sandbox itself still has no
direct network path to nseindia.com) found:
  - The `NSE_CM_security_ddmmyyyy.csv.gz` filename IS the current, official pattern — confirmed
    directly from NSE circular NSE/MSD/60315 (a primary source, not a third-party aggregator),
    which documents it as the CM-MII Security File's naming convention, effective since
    2024-02-05, alongside the analogous FO/CD/CO "MII" files.
  - This report is NOT discontinued. The "Discontinued... switch to CM-UDiFF" notice that
    appears in the live page's text is attached specifically to the legacy "CM - Bhavcopy(csv)"
    and "CM - Common Bhavcopy (csv)" entries (superseded by UDiFF per circular 62424) — NOT to
    either "CM - MII - Security File (.gz)" entry, which the live page still lists as a normal,
    active report as of this investigation.
  - The most plausible explanation for the 404 on TODAY's exact date: unlike the bhavcopy (which
    is genuinely published every single trading session), a security/instrument MASTER file is a
    natural candidate for being republished only when something changes (a listing, delisting, or
    series change) or on some periodic cadence — not necessarily on a session with no such change.
    This repository's sandbox could not confirm this against the live endpoint directly, so it is
    a well-reasoned hypothesis, not a verified fact — see `discover_report`'s docstring.
  - A real, separately-confirmed NSE-specific failure mode this now defends against: NSE's
    anti-bot layer can return HTTP 200 with an HTML block/captcha page instead of the requested
    file. A bare `resp.raise_for_status()` (the pre-fix behavior) would NOT catch this — a 200 is
    a 200 — silently corrupting everything downstream. `validate_artifact` below exists
    specifically to catch this class of failure before it reaches the parser.

The fix: `discover_report` now scans backward across a bounded window of recent calendar dates
(not just today) for each dated template, and every attempt (template, date, outcome) is recorded
and surfaced in the final error if nothing is found — never a bare "tried 3 URLs, all 404". This
is a genuine, evidence-based improvement, not a guess at a wholly different URL scheme — but it
was not possible to confirm from this sandbox that it resolves the real production 404, since
this sandbox cannot reach nseindia.com at all (see RESEARCH_INTEGRITY_V13_NOTES / the P1 forensic
report for the live smoke-test results this DOES require the real network to confirm).
"""

from __future__ import annotations

import gzip
import io
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

import pandas as pd
import requests

from nse_scanner.data.nse_eod import NSE_HEADERS, NSE_HOMEPAGE, UDIFF_URL_TEMPLATE
from nse_scanner.exceptions import DataProviderError
from nse_scanner.logging_config import get_logger
from nse_scanner.universe.validation import UniverseSnapshot

logger = get_logger(__name__)


class ReportType:
    CM_UDIFF_BHAVCOPY = "CM_UDIFF_BHAVCOPY"
    CM_SECURITY_FILE = "CM_SECURITY_FILE"


@dataclass(frozen=True)
class SecurityFileUrlTemplate:
    template: str
    dated: bool   # False = a single static URL, not worth retrying across multiple dates


# Candidate URL templates for the CM-MII security file, tried in order at each candidate date
# (PART 3 resilience). The first template's filename fragment is confirmed correct against the
# live NSE circular (see module docstring) — the folder/date-availability assumption is the part
# that was NOT confirmable from this sandbox.
SECURITY_FILE_URL_TEMPLATES: tuple[SecurityFileUrlTemplate, ...] = (
    SecurityFileUrlTemplate(
        "https://nsearchives.nseindia.com/content/cm/NSE_CM_security_{ddmmyyyy}.csv.gz", True),
    SecurityFileUrlTemplate(
        "https://nsearchives.nseindia.com/content/equity_bhavcopy/security_{ddmmyyyy}.csv.gz", True),
    SecurityFileUrlTemplate("https://nsearchives.nseindia.com/content/equity/EQUITY_L.csv", False),
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

SECURITY_FILE_SCHEMA_VERSION = "SECURITY_FILE_COLUMN_CANDIDATES_v1"  # bump if the map above changes


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


# ==================================================================================================
# Security-master discovery pipeline (P1 provider-hardening):
#     discover_report -> resolve_download -> download_raw -> hash_raw -> validate_artifact
#         -> decompress -> parse -> validate_schema -> derive_mainboard -> snapshot
# Each stage is a small, separately-testable function; fetch_security_file() at the bottom of this
# file is the orchestrator that chains them, matching the pre-existing public API/return shape so
# universe/mainboard.py does not need to change.
# ==================================================================================================

@dataclass(frozen=True)
class DiscoveryAttempt:
    """One (template, date) combination that was tried, and exactly what happened — the fix for
    the pre-existing "tried 3 URLs, all failed" diagnostic, which gave no way to tell a genuine
    404 apart from a network failure apart from an anti-bot block page."""
    url: str
    outcome: str   # "AVAILABLE" | "NOT_FOUND" | "UNREACHABLE" | "INVALID_CONTENT"
    detail: str | None = None


@dataclass(frozen=True)
class DiscoveryResult:
    found_url: str | None
    attempts: list[DiscoveryAttempt] = field(default_factory=list)

    @property
    def summary(self) -> str:
        return "\n".join(f"  - {a.url} -> {a.outcome}" + (f" ({a.detail})" if a.detail else "")
                          for a in self.attempts)


def discover_report(max_lookback_days: int = 10, timeout: int = 15,
                     today: date | None = None) -> DiscoveryResult:
    """Finds the most recent date for which the CM-MII security file is actually available.

    **Why this scans backward across dates rather than only checking today** (the pre-fix
    behavior): unlike the bhavcopy, which is genuinely published every trading session, a
    security/instrument MASTER file plausibly only gets republished when its contents actually
    change (a listing, delisting, or series change) — see module docstring for the full reasoning
    and its confidence level (a well-reasoned hypothesis this sandbox could not directly confirm
    against the live endpoint). Requesting exactly today's date and giving up on a 404 conflates
    "the file for today doesn't exist because nothing changed" with "the discovery mechanism is
    broken" — this function tells those apart by trying a bounded window of recent dates before
    concluding discovery genuinely failed.

    Undated templates (`SecurityFileUrlTemplate.dated=False`) are tried once regardless of the
    date window, not repeated per date. A lightweight ranged GET (first ~512 bytes) is used per
    attempt rather than a full download, so scanning several dates stays cheap — `download_raw`
    does the real full fetch afterward, only once, against whichever URL this function returns.

    Returns a `DiscoveryResult` with `found_url=None` and every attempt logged if nothing was
    found in the window — callers must fail closed on that (see `fetch_security_file`), never
    substitute a fallback.
    """
    today = today or datetime.now(timezone.utc).date()
    session = requests.Session()
    session.headers.update(NSE_HEADERS)
    try:
        session.get(NSE_HOMEPAGE, timeout=timeout)
    except requests.RequestException as e:
        logger.warning("NSE homepage warm-up failed (continuing anyway): %s", e)

    attempts: list[DiscoveryAttempt] = []
    tried_undated: set[str] = set()

    for offset in range(max_lookback_days + 1):
        candidate_date = today - timedelta(days=offset)
        for tpl in SECURITY_FILE_URL_TEMPLATES:
            if not tpl.dated:
                if tpl.template in tried_undated:
                    continue
                tried_undated.add(tpl.template)
                url = tpl.template
            else:
                url = tpl.template.format(ddmmyyyy=candidate_date.strftime("%d%m%Y"))

            try:
                resp = session.get(url, timeout=timeout, headers={"Range": "bytes=0-511"})
            except requests.RequestException as e:
                attempts.append(DiscoveryAttempt(url, "UNREACHABLE", f"{type(e).__name__}: {e}"))
                continue

            if resp.status_code == 404:
                attempts.append(DiscoveryAttempt(url, "NOT_FOUND", "HTTP 404"))
                continue
            if resp.status_code not in (200, 206):
                attempts.append(DiscoveryAttempt(url, "UNREACHABLE", f"HTTP {resp.status_code}"))
                continue

            try:
                _validate_artifact_bytes(resp.content, url)
            except DataProviderError as e:
                # A 200/206 that isn't actually the file (e.g. an HTML anti-bot block page) — a
                # real, distinct NSE failure mode a bare status-code check would miss entirely.
                attempts.append(DiscoveryAttempt(url, "INVALID_CONTENT", str(e)))
                continue

            attempts.append(DiscoveryAttempt(url, "AVAILABLE"))
            return DiscoveryResult(found_url=url, attempts=attempts)

    return DiscoveryResult(found_url=None, attempts=attempts)


def resolve_download(discovery: DiscoveryResult) -> str:
    """Turns a successful `discover_report` result into the concrete URL to download. Kept as its
    own pipeline stage (rather than folded into `discover_report`) so a future requirement — e.g.
    preferring one AVAILABLE candidate over another by some priority rule beyond "first found," or
    resolving a signed/temporary URL — has an obvious place to live without restructuring
    discovery itself. Raises `DataProviderError` (never returns a fallback URL) if discovery
    failed; the message includes the full attempt log."""
    if discovery.found_url is None:
        raise DataProviderError(
            "Could not discover a current CM-MII security file (PART 3 — NSE may have changed "
            "the file naming/schema/publication cadence again, or genuinely has not republished "
            "it in the lookback window). Attempts:\n" + discovery.summary
        )
    return discovery.found_url


def download_raw(url: str, timeout: int = 20) -> bytes:
    """The real, full fetch — only ever called once, against the URL `resolve_download` returned."""
    session = requests.Session()
    session.headers.update(NSE_HEADERS)
    try:
        session.get(NSE_HOMEPAGE, timeout=timeout)
    except requests.RequestException as e:
        logger.warning("NSE homepage warm-up failed (continuing anyway): %s", e)
    try:
        resp = session.get(url, timeout=timeout)
        resp.raise_for_status()
        return resp.content
    except requests.RequestException as e:
        raise DataProviderError(f"Download failed for {url}: {type(e).__name__}: {e}") from e


def hash_raw(raw_bytes: bytes) -> str:
    import hashlib
    return hashlib.sha256(raw_bytes).hexdigest()


def _validate_artifact_bytes(raw_bytes: bytes, url: str) -> None:
    """Shared by both the cheap discovery-time probe (first ~512 bytes) and the full
    post-download validation — same checks apply to a content prefix or the whole artifact."""
    if not raw_bytes:
        raise DataProviderError(f"Empty response body from {url}")
    stripped = raw_bytes.lstrip()
    if stripped[:1] in (b"<",) or b"<html" in raw_bytes[:512].lower() or b"<!doctype" in raw_bytes[:512].lower():
        raise DataProviderError(
            f"Response from {url} looks like an HTML page, not the expected data file "
            f"(a known NSE anti-bot failure mode: HTTP 200 with a block/captcha page instead of "
            f"the file). First bytes: {raw_bytes[:80]!r}"
        )
    if url.endswith(".gz") and raw_bytes[:2] != b"\x1f\x8b":
        raise DataProviderError(
            f"Response from {url} does not start with gzip magic bytes (got {raw_bytes[:2]!r}) "
            f"despite a .gz URL — not a valid gzip artifact."
        )


def validate_artifact(raw_bytes: bytes, url: str) -> None:
    """Full post-download validation — see `_validate_artifact_bytes`. Raises `DataProviderError`
    (never silently proceeds to parse something that isn't actually the expected file)."""
    _validate_artifact_bytes(raw_bytes, url)


@dataclass(frozen=True)
class SecurityFileResult:
    frame: pd.DataFrame
    source_url: str
    retrieved_at: "datetime"
    file_hash: str
    row_count: int
    discovery_attempts: list[DiscoveryAttempt] = field(default_factory=list)


def fetch_security_file(timeout: int = 20, max_lookback_days: int = 10,
                         today: date | None = None) -> SecurityFileResult:
    """Orchestrates the full pipeline: discover_report -> resolve_download -> download_raw ->
    hash_raw -> validate_artifact -> decompress -> parse -> validate_schema (the last four are
    combined in `parse_security_file`, unchanged from before this revision — see that function's
    own docstring). Raises `DataProviderError` at whichever stage genuinely fails; never returns a
    partial or substituted result. See module docstring for what changed in this revision and why.
    """
    discovery = discover_report(max_lookback_days=max_lookback_days, timeout=timeout, today=today)
    url = resolve_download(discovery)

    content = download_raw(url, timeout=timeout)
    validate_artifact(content, url)

    is_gzip = url.endswith(".gz")
    frame = parse_security_file(content, is_gzip=is_gzip)
    raw_for_hash = gzip.decompress(content) if is_gzip else content
    file_hash = hash_raw(raw_for_hash)

    logger.info("Security file source OK: %s (%d rows)", url, len(frame))
    return SecurityFileResult(
        frame=frame, source_url=url, retrieved_at=datetime.now(timezone.utc),
        file_hash=file_hash, row_count=len(frame), discovery_attempts=discovery.attempts,
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
    documented as such rather than silently guessing at other series codes.

    This is the pipeline's `derive_mainboard` stage — kept under its original name (rather than
    renamed) since `universe/mainboard.py` already imports it directly and renaming would be
    churn with no behavior change; `derive_mainboard` below is a thin alias for pipeline-naming
    consistency with the rest of this module."""
    return security_df[security_df["Series"].isin(("EQ", "BE"))].copy()


derive_mainboard = filter_mainboard_equity  # pipeline-stage alias, see docstring above


def snapshot(result: SecurityFileResult, mainboard_df: pd.DataFrame) -> UniverseSnapshot:
    """The pipeline's final `snapshot` stage: turns a successful `fetch_security_file` result plus
    its EQ/BE-filtered mainboard frame into a `UniverseSnapshot` record carrying full provenance
    (source URL, file hash, schema version, raw vs eligible counts) — the same record type
    `universe/validation.py::build_snapshot` produces for NIFTY_200, reused here rather than
    inventing a parallel type."""
    has_symbol = "NSE_Symbol" in mainboard_df.columns
    return UniverseSnapshot(
        universe_id="NSE_MAINBOARD_EQ",
        snapshot_date=result.retrieved_at.date(),
        source=result.source_url,
        source_version=None,
        retrieved_at=result.retrieved_at,
        constituent_count=len(mainboard_df),
        symbol_count=mainboard_df["NSE_Symbol"].nunique() if has_symbol else len(mainboard_df),
        duplicate_count=int(mainboard_df["NSE_Symbol"].duplicated().sum()) if has_symbol else 0,
        invalid_count=0,
        validation_status="VALID" if len(mainboard_df) > 0 else "INVALID",
        source_url=result.source_url,
        file_hash=result.file_hash,
        schema_version=SECURITY_FILE_SCHEMA_VERSION,
        raw_row_count=result.row_count,
        eligible_count=len(mainboard_df),
        definition="NSE_MAINBOARD_EQ = CM-MII security file rows with Series in {EQ, BE}",
    )
