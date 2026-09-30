"""NSE instrument-classification cross-reference lists (mainboard-universe-integrity-v2, Task 7).

`universe/mainboard.py`'s `excluded_symbols`/`excluded_isins` hook existed from PR #5 onward but
shipped permanently empty — "no authoritative list has been fetched/verified yet" (see that PR's
own docstring). This module is that authoritative source, evidenced in
MAINBOARD_UNIVERSE_SPEC.md Parts D–G: NSE's own official "Instrument type in Equity includes
fully paid equity shares/ETFs, units of REITs/INVITs" (fetched directly from nseindia.com's
"Legend of Series" / "Securities available for Trading" pages this session) confirms these
instruments are NOT distinguishable from ordinary equity by `Series` alone (ETFs share `Series=EQ`
with plain stocks) — cross-referencing against NSE's own separately-published lists is therefore
structurally necessary, not merely a defensive nicety.

**Confidence levels are NOT uniform across the four lists** — recorded explicitly per source,
never silently treated as equally certain:
  - ETF (`eq_etfseclist.csv`) and REIT (`REITS_L.csv`) URLs: STRONG INFERENCE by directory-pattern
    analogy with the two below (not independently content-verified this session).
  - InvIT (`INVITS_L.csv`) and SME (`SME_EQUITY_L.csv`) URLs: CONFIRMED this session via a direct,
    byte-identical content match between a live web search result and the real file supplied by
    the user in this conversation (same rows, same company names, same ISINs).

None of these four URLs have been fetched *live* by this project (this sandbox has no network
path to nseindia.com) — every parser below is built and tested against the REAL file content
supplied this session (see tests/fixtures/nse/), not fabricated data, but the live-fetch path
itself remains unverified end-to-end. See MAINBOARD_UNIVERSE_INTEGRITY_V2_AUDIT.md Part B.

**Failure policy — deliberately different from the primary security-master fetch:** these are
supplementary, defense-in-depth cross-references (Task 3 Part B already found zero symbol overlap
between EQUITY_L.csv and any of the four real files analyzed this session — today's primary
source does not actually need this exclusion to produce a correct universe). A failure to fetch
one of these lists is therefore logged as a WARNING and recorded explicitly in diagnostics as
`UNAVAILABLE` for that category — it does NOT fail the whole NSE_MAINBOARD_EQ run closed the way
a primary-source failure does (see `universe/mainboard.py`). This is a considered distinction, not
an oversight: Task 12 asks for fail-closed behavior on "the official source" (singular, primary);
treating every supplementary list with the identical severity would make the whole mainboard path
fragile to a transient failure of a file that, on today's evidence, changes nothing.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

import pandas as pd
import requests

from nse_scanner.data.nse_eod import NSE_HEADERS, NSE_HOMEPAGE
from nse_scanner.data.nse_reports import hash_raw, validate_artifact
from nse_scanner.exceptions import DataProviderError
from nse_scanner.logging_config import get_logger

logger = get_logger(__name__)


# Task 14: versions the ETF/REIT/InvIT/SME cross-reference LOGIC (which lists, matched on which
# fields, with what precedence) independently of software version, strategy_id and the universe
# definition id. Bump when that logic changes so an old snapshot's exclusions stay interpretable.
CLASSIFICATION_RULES_VERSION = "instrument_lists_v1"


class InstrumentListUnavailable(DataProviderError):
    """A supplementary (non-primary) instrument cross-reference list could not be fetched or
    parsed. Always caught by the caller (universe/mainboard.py) and treated as non-fatal — see
    module docstring."""


@dataclass(frozen=True)
class InstrumentListSource:
    category: str  # "ETF" | "REIT" | "InvIT" | "SME"
    url: str
    symbol_column_candidates: tuple[str, ...]
    isin_column_candidates: tuple[str, ...]
    confidence: str  # "CONFIRMED" | "STRONG_INFERENCE" — see module docstring
    has_footer_notes: bool  # REITS_L.csv/INVITS_L.csv carry a trailing free-text disclaimer row


# One entry per category (Task 3 Parts D-G). URLs and confidence levels per this session's
# forensic findings — see module docstring and MAINBOARD_UNIVERSE_SPEC.md.
INSTRUMENT_LIST_SOURCES: tuple[InstrumentListSource, ...] = (
    InstrumentListSource(
        category="ETF",
        url="https://nsearchives.nseindia.com/content/equities/eq_etfseclist.csv",
        symbol_column_candidates=("Symbol",),
        isin_column_candidates=("ISINNumber", "ISIN NUMBER", "ISIN"),
        confidence="STRONG_INFERENCE",
        has_footer_notes=False,
    ),
    InstrumentListSource(
        category="REIT",
        url="https://nsearchives.nseindia.com/content/equities/REITS_L.csv",
        symbol_column_candidates=("SYMBOL",),
        isin_column_candidates=("ISIN NUMBER", "ISINNumber", "ISIN"),
        confidence="STRONG_INFERENCE",
        has_footer_notes=True,
    ),
    InstrumentListSource(
        category="InvIT",
        url="https://nsearchives.nseindia.com/content/equities/INVITS_L.csv",
        symbol_column_candidates=("SYMBOL",),
        isin_column_candidates=("ISIN NUMBER", "ISINNumber", "ISIN"),
        confidence="CONFIRMED",
        has_footer_notes=True,
    ),
    InstrumentListSource(
        category="SME",
        url="https://nsearchives.nseindia.com/emerge/corporates/content/SME_EQUITY_L.csv",
        symbol_column_candidates=("SYMBOL",),
        isin_column_candidates=("ISIN_NUMBER", "ISIN NUMBER", "ISIN"),
        confidence="CONFIRMED",
        has_footer_notes=False,
    ),
)


@dataclass(frozen=True)
class InstrumentListResult:
    category: str
    symbols: frozenset[str]
    isins: frozenset[str]
    source_url: str
    retrieved_at: datetime
    row_count: int
    file_hash: str


def _looks_like_a_real_row(symbol_value: object) -> bool:
    """REITS_L.csv/INVITS_L.csv carry a trailing free-text disclaimer row (observed verbatim this
    session: 'Note : The Market lot is updated as on the date of listing...') in the same column
    as the symbol. A real NSE symbol is a short, space-free, alphanumeric token; a disclaimer
    sentence is neither. This is a content-shape check, not a hard-coded string match against the
    exact disclaimer text (which could reword itself), so it stays robust to that.
    """
    if symbol_value is None or (isinstance(symbol_value, float) and pd.isna(symbol_value)):
        return False
    s = str(symbol_value).strip()
    if not s or len(s) > 32 or " " in s:
        return False
    return True


def parse_instrument_list_csv(text: str, source: InstrumentListSource) -> tuple[frozenset[str], frozenset[str], int]:
    """Parses one real NSE instrument-classification CSV into (symbols, isins, row_count).
    Raises InstrumentListUnavailable (never returns a silently-wrong/partial result) if the
    expected symbol column cannot be found — schema drift must be visible, not silently ignored,
    even though a fetch failure for this category is itself non-fatal to the caller."""
    import io

    df = pd.read_csv(io.StringIO(text))
    df.columns = [c.strip() for c in df.columns]

    symbol_col = next((c for c in source.symbol_column_candidates if c in df.columns), None)
    if symbol_col is None:
        raise InstrumentListUnavailable(
            f"[{source.category}] symbol column not found in {source.url}. Columns present: "
            f"{list(df.columns)}. Update InstrumentListSource.symbol_column_candidates in "
            f"data/nse_instrument_lists.py to match the real file."
        )
    isin_col = next((c for c in source.isin_column_candidates if c in df.columns), None)

    if source.has_footer_notes:
        df = df[df[symbol_col].map(_looks_like_a_real_row)]

    symbols = frozenset(df[symbol_col].astype(str).str.strip().str.upper())
    isins = frozenset(df[isin_col].astype(str).str.strip()) if isin_col is not None else frozenset()
    return symbols, isins, len(df)


def fetch_instrument_list(source: InstrumentListSource, timeout: int = 20) -> InstrumentListResult:
    """The real, full fetch for one category. Raises InstrumentListUnavailable on any failure —
    network, validation, or parsing — for the caller to catch and treat as non-fatal (see module
    docstring). Never returns a partial or fabricated result."""
    session = requests.Session()
    session.headers.update(NSE_HEADERS)
    try:
        session.get(NSE_HOMEPAGE, timeout=timeout)
    except requests.RequestException as e:
        logger.warning("[%s] NSE homepage warm-up failed (continuing anyway): %s", source.category, e)

    try:
        resp = session.get(source.url, timeout=timeout)
        resp.raise_for_status()
        content = resp.content
    except requests.RequestException as e:
        raise InstrumentListUnavailable(f"[{source.category}] download failed for {source.url}: {e}") from e

    try:
        validate_artifact(content, source.url)
    except DataProviderError as e:
        raise InstrumentListUnavailable(f"[{source.category}] {e}") from e

    text = content.decode("utf-8")
    try:
        symbols, isins, row_count = parse_instrument_list_csv(text, source)
    except InstrumentListUnavailable:
        raise
    except Exception as e:  # noqa: BLE001 - any parse failure -> treated identically (non-fatal to caller)
        raise InstrumentListUnavailable(f"[{source.category}] parse failed for {source.url}: {e}") from e

    return InstrumentListResult(
        category=source.category,
        symbols=symbols,
        isins=isins,
        source_url=source.url,
        retrieved_at=datetime.now(timezone.utc),
        row_count=row_count,
        file_hash=hash_raw(content),
    )


def fetch_all_instrument_lists(
    timeout: int = 20,
) -> tuple[dict[str, InstrumentListResult], dict[str, str]]:
    """Fetches all four categories independently — one category's failure never blocks another's.
    Returns (results_by_category, status_by_category); a category missing from `results_by_category`
    has its failure reason in `status_by_category` instead (never silently dropped — see
    universe/mainboard.py for how this feeds Task 13's diagnostics)."""
    results: dict[str, InstrumentListResult] = {}
    statuses: dict[str, str] = {}
    for source in INSTRUMENT_LIST_SOURCES:
        try:
            results[source.category] = fetch_instrument_list(source, timeout=timeout)
            statuses[source.category] = "FETCHED"
        except InstrumentListUnavailable as e:
            logger.warning(
                "[%s] supplementary exclusion list unavailable this run -- proceeding WITHOUT "
                "this exclusion (non-fatal; see data/nse_instrument_lists.py module docstring "
                "for why this is a deliberate, non-fatal failure mode): %s",
                source.category,
                e,
            )
            statuses[source.category] = f"UNAVAILABLE: {e}"
    return results, statuses
