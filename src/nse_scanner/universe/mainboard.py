"""NSE_MAINBOARD_EQ universe provider (PART 5 / continuation "make NIFTY_200 and
NSE_MAINBOARD_EQ really different"; extended mainboard-universe-integrity-v2, Tasks 3/6/7).

Derives the mainboard-equity universe from the official NSE security list
(`data/nse_reports.py::fetch_security_file` + `filter_mainboard_equity`), never from a hard-coded
list. Same hard-fail integrity policy as `universe/nifty200.py`: a failure to retrieve/validate
the PRIMARY security file raises `UniverseIntegrityError` and the run stops — it does not silently
substitute a smaller/stale list. See MAINBOARD_UNIVERSE_SPEC.md Part B for the full, evidence-cited
definition this module implements.

**mainboard-universe-integrity-v2 changes from the prior version of this module:**
  - `excluded_symbols`/`excluded_isins` are no longer permanently empty. By default (
    `auto_fetch_exclusion_lists=True`), `data/nse_instrument_lists.py`'s four real NSE reference
    lists (ETF/REIT/InvIT/SME) are fetched and merged in automatically. A category that fails to
    fetch is recorded as `UNAVAILABLE` in `last_diagnostics` and simply excludes nothing for that
    category this run (a deliberately different, less severe failure policy than the primary
    source — see `data/nse_instrument_lists.py`'s module docstring for why). Manually-supplied
    `excluded_symbols`/`excluded_isins` (e.g. for tests, or a known-bad symbol not on any official
    list) are still honored and merged with whatever was auto-fetched, never replaced by it.
  - Each excluded row is now attributed to a specific category (`ETF`/`REIT`/`InvIT`/`SME`/
    `manual_override`) rather than a single lumped "known non-equity" count — Task 7's
    "a future system should be able to say... with a reason/source" is answered by
    `last_diagnostics["excluded_by_category"]`.
  - `min_count`/`max_count` are re-derived from `EQUITY_L.csv`'s real, observed shape (2,585 rows,
    2026-09 snapshot) rather than the CM-MII master's previously-cited ~4,464 (a file this project
    has never successfully fetched) — see the field docstrings below for the exact reasoning.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from nse_scanner.data.base import UniverseProvider
from nse_scanner.data.nse_instrument_lists import CLASSIFICATION_RULES_VERSION, fetch_all_instrument_lists
from nse_scanner.data.nse_reports import (
    SecurityFileResult,
    compute_mainboard_diagnostics,
    deduplicate_mainboard,
    fetch_security_file,
    filter_mainboard_equity,
    snapshot,
)
from nse_scanner.exceptions import DataProviderError, UniverseIntegrityError
from nse_scanner.logging_config import get_logger
from nse_scanner.universe.validation import UniverseSnapshot

logger = get_logger(__name__)

# Task 14: an immutable identifier for the universe RULES this module implements, independent of
# software version and strategy_id. A future rule change (a different predicate, a different
# primary source, a different dedup precedence) must mint a new id here rather than silently
# changing this one's meaning — see MAINBOARD_UNIVERSE_SPEC.md.
UNIVERSE_DEFINITION_ID = "nse_mainboard_equity_v2"


@dataclass
class NSEMainboardEquityUniverseProvider(UniverseProvider):
    # Task 6: re-derived from EQUITY_L.csv's real, observed shape this session (2,585 total rows:
    # 2,317 EQ + 241 BE + 27 BZ, zero duplicate symbols) rather than the CM-MII master's
    # previously-cited ~4,464-after-dedup figure (a file never successfully fetched by this
    # project — see MAINBOARD_UNIVERSE_INTEGRITY_V2_AUDIT.md Part B.6). Bounds are a deliberately
    # generous +/- collar around that observed figure — wide enough to tolerate ordinary multi-year
    # listing/delisting drift without needing a code change, narrow enough to still catch a
    # catastrophic failure (a truncated download, or a schema change that silently lets SME/ETF
    # rows back in). This is NOT "whatever count happens to look plausible" (Task 6's explicit
    # warning) — it is bounded by this session's actual, cited, real-file evidence, and documented
    # as such rather than picked to make a number fall inside a range.
    min_count: int = 1500
    max_count: int = 3500
    # Mainboard-universe-semantics addition, wired to real data as of mainboard-universe-
    # integrity-v2 (was permanently empty from PR #5 through PR #6): a MANUAL, opt-in
    # supplement to the auto-fetched exclusion lists below — for a known-bad symbol not covered
    # by any official list, or for tests. Merged with (never replaces) whatever
    # auto_fetch_exclusion_lists fetches.
    excluded_symbols: frozenset[str] = field(default_factory=frozenset)
    excluded_isins: frozenset[str] = field(default_factory=frozenset)
    # Task 7: fetch data/nse_instrument_lists.py's real ETF/REIT/InvIT/SME cross-references and
    # merge them into the exclusion set automatically. Set False only for tests that want to
    # control exclusions purely via excluded_symbols/excluded_isins (avoids a live-network
    # dependency in unit tests — see tests/unit/test_mainboard_diagnostics.py).
    auto_fetch_exclusion_lists: bool = True
    exclusion_list_timeout: int = 20
    last_diagnostics: dict | None = field(default=None, init=False, repr=False, compare=False)
    # Retained so callers (cli/update_universe.py, cli/run_daily.py) can build a correct,
    # provenance-carrying snapshot/manifest entry — Task 1 finding A.7: this information used to be
    # computed and then dropped, leaving nse_reports.snapshot() unreachable from any real CLI.
    last_source_result: SecurityFileResult | None = field(default=None, init=False, repr=False, compare=False)
    last_mainboard_frame: pd.DataFrame | None = field(default=None, init=False, repr=False, compare=False)

    def build_universe_snapshot(self) -> UniverseSnapshot:
        """The provenance-correct snapshot for the run `get_constituents()` just completed. Raises
        if called before a successful `get_constituents()` — never fabricates one."""
        if self.last_source_result is None or self.last_mainboard_frame is None:
            raise UniverseIntegrityError("build_universe_snapshot() called before a successful get_constituents().")
        return snapshot(
            self.last_source_result,
            self.last_mainboard_frame,
            self.last_diagnostics,
            universe_definition_id=UNIVERSE_DEFINITION_ID,
            classification_rules_version=CLASSIFICATION_RULES_VERSION,
        )

    def get_constituents(self) -> tuple[pd.DataFrame, str, str]:
        try:
            result = fetch_security_file()
        except DataProviderError as e:
            raise UniverseIntegrityError(
                "Could not retrieve the NSE security list needed to derive NSE_MAINBOARD_EQ. "
                "This repository does NOT fall back to a hard-coded equity list (PART 6).\n"
                f"{e}"
            ) from e

        mainboard = filter_mainboard_equity(result.frame)
        eq_be_filtered = mainboard.copy()  # kept for diagnostics — pre-exclusion, pre-dedup

        excluded_symbols_by_category: dict[str, frozenset[str]] = {}
        excluded_isins_by_category: dict[str, frozenset[str]] = {}
        exclusion_list_status: dict[str, str] = {}
        if self.auto_fetch_exclusion_lists:
            fetched, exclusion_list_status = fetch_all_instrument_lists(timeout=self.exclusion_list_timeout)
            for category, list_result in fetched.items():
                excluded_symbols_by_category[category] = list_result.symbols
                excluded_isins_by_category[category] = list_result.isins
        if self.excluded_symbols or self.excluded_isins:
            excluded_symbols_by_category["manual_override"] = self.excluded_symbols
            excluded_isins_by_category["manual_override"] = self.excluded_isins
            exclusion_list_status["manual_override"] = "PROVIDED"

        # Task 7: attribute each excluded row to the FIRST category that matches it (a symbol
        # should not plausibly match more than one of these real, disjoint official lists — Audit
        # B.4 confirmed zero cross-overlap between them — but a deterministic first-match order
        # keeps the accounting well-defined even if that assumption is ever violated).
        excluded_mask = pd.Series(False, index=mainboard.index)
        excluded_category = pd.Series(pd.NA, index=mainboard.index, dtype="object")
        for category in (*excluded_symbols_by_category.keys(),):
            symbols = excluded_symbols_by_category.get(category, frozenset())
            isins = excluded_isins_by_category.get(category, frozenset())
            cat_mask = pd.Series(False, index=mainboard.index)
            if symbols:
                cat_mask |= mainboard["NSE_Symbol"].isin(symbols)
            if isins and "ISIN" in mainboard.columns:
                cat_mask |= mainboard["ISIN"].isin(isins)
            newly_matched = cat_mask & ~excluded_mask
            excluded_category = excluded_category.mask(newly_matched, category)
            excluded_mask |= cat_mask

        excluded = mainboard[excluded_mask].copy()
        excluded["Exclusion_Category"] = excluded_category[excluded_mask]
        mainboard = mainboard[~excluded_mask].copy()

        mainboard, dropped_by_dedup = deduplicate_mainboard(mainboard)
        mainboard = mainboard.reset_index(drop=True)

        self.last_diagnostics = compute_mainboard_diagnostics(
            result.frame,
            eq_be_filtered,
            mainboard,
            dropped_by_dedup,
            excluded_df=excluded,
        )
        self.last_diagnostics["universe_definition_id"] = UNIVERSE_DEFINITION_ID
        self.last_diagnostics["classification_rules_version"] = CLASSIFICATION_RULES_VERSION
        # Task 13 provenance block: source date is recorded as None (not the retrieval time) when
        # the source carries none — see nse_reports.snapshot().
        self.last_diagnostics["source_url"] = result.source_url
        self.last_diagnostics["source_file_hash"] = result.file_hash
        self.last_diagnostics["source_date"] = result.source_date.isoformat() if result.source_date else None
        self.last_diagnostics["retrieved_at"] = result.retrieved_at.isoformat()
        # Task 6 cardinality/identity diagnostics on the FINAL population.
        self.last_diagnostics["final_unique_symbols"] = int(mainboard["NSE_Symbol"].nunique())
        self.last_diagnostics["final_unique_isins"] = (
            int(mainboard["ISIN"].nunique()) if "ISIN" in mainboard.columns else None
        )
        self.last_diagnostics["final_series_breakdown"] = mainboard["Series"].value_counts().to_dict()
        self.last_diagnostics["raw_unique_instrument_ids"] = (
            int(result.frame["FinInstrmId"].nunique()) if "FinInstrmId" in result.frame.columns else None
        )
        self.last_diagnostics["exclusion_list_status"] = exclusion_list_status
        self.last_diagnostics["excluded_by_category"] = (
            excluded["Exclusion_Category"].value_counts().to_dict() if not excluded.empty else {}
        )
        logger.info(
            "NSE_MAINBOARD_EQ diagnostics: raw=%d eq_be_bz=%d excluded_known_non_equity=%d "
            "(by category: %s) dropped_by_dedup=%d final=%d exclusion_list_status=%s",
            self.last_diagnostics["raw_row_count"],
            self.last_diagnostics["eq_be_row_count"],
            self.last_diagnostics["excluded_known_non_equity_count"],
            self.last_diagnostics["excluded_by_category"],
            self.last_diagnostics["dropped_by_dedup_count"],
            self.last_diagnostics["final_constituent_count"],
            exclusion_list_status,
        )

        if not (self.min_count <= len(mainboard) <= self.max_count):
            raise UniverseIntegrityError(
                f"NSE_MAINBOARD_EQ derived from the security list has {len(mainboard)} "
                f"constituents (raw file had {result.row_count} rows before EQ/BE/BZ filtering), "
                f"outside the accepted sanity range [{self.min_count}, {self.max_count}]. "
                f"Refusing to treat this as a valid universe — the security-file schema may have "
                f"changed (see data/nse_reports.py::SECURITY_FILE_COLUMN_CANDIDATES), or the "
                f"wrong source file was fetched (see MAINBOARD_UNIVERSE_SPEC.md Part B)."
            )

        self.last_source_result = result
        self.last_mainboard_frame = mainboard

        out = pd.DataFrame(
            {
                "Symbol": mainboard["NSE_Symbol"],
                "Company_Name": mainboard["Company_Name"],
                "Sector": pd.NA,  # the NSE security master does not carry sector classification
                "ISIN": mainboard["ISIN"] if "ISIN" in mainboard.columns else pd.NA,
                "Series": mainboard["Series"],
            }
        )
        logger.info("NSE_MAINBOARD_EQ universe OK: %d constituents from %s", len(out), result.source_url)
        return out, result.source_url, result.retrieved_at.isoformat()
