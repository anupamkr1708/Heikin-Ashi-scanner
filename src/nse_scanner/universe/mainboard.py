"""NSE_MAINBOARD_EQ universe provider (PART 5 / continuation "make NIFTY_200 and
NSE_MAINBOARD_EQ really different").

Derives the mainboard-equity universe from the official NSE CM-MII security master file
(`data/nse_reports.py::fetch_security_file` + `filter_mainboard_equity`), never from a hard-coded
list. Same hard-fail integrity policy as `universe/nifty200.py`: a failure to retrieve/validate
the security file raises `UniverseIntegrityError` and the run stops — it does not silently
substitute a smaller/stale list.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from nse_scanner.data.base import UniverseProvider
from nse_scanner.data.nse_reports import (
    compute_mainboard_diagnostics,
    deduplicate_mainboard,
    fetch_security_file,
    filter_mainboard_equity,
)
from nse_scanner.exceptions import DataProviderError, UniverseIntegrityError
from nse_scanner.logging_config import get_logger

logger = get_logger(__name__)


@dataclass
class NSEMainboardEquityUniverseProvider(UniverseProvider):
    min_count: int = 500  # sanity bounds — NSE mainboard equity count is in the low thousands
    max_count: int = 4000
    # Mainboard-universe-semantics addition: an explicit, OPT-IN cross-reference of symbols/ISINs
    # to exclude from NSE_MAINBOARD_EQ despite having Series in {EQ, BE} — intended for a future
    # authoritative ETF/REIT/InvIT list (candidate mechanism identified during forensic research:
    # NSE separately publishes an ETF security list, e.g. eq_etfseclist.csv, cross-referenced by
    # symbol/ISIN against the security master — NOT a field inside the security master itself).
    # Deliberately EMPTY by default: no authoritative list has been fetched/verified yet, so today's
    # universe composition is byte-identical to before this field existed. Do not populate this
    # from a symbol-name heuristic (e.g. "ends with ETF") — see the forensic report's explicit
    # instruction against that. See test_mainboard_known_non_equity_exclusion.py for how this
    # activates once a real list is wired in.
    excluded_symbols: frozenset[str] = field(default_factory=frozenset)
    excluded_isins: frozenset[str] = field(default_factory=frozenset)
    last_diagnostics: dict | None = field(default=None, init=False, repr=False, compare=False)

    def get_constituents(self) -> tuple[pd.DataFrame, str, str]:
        try:
            result = fetch_security_file()
        except DataProviderError as e:
            raise UniverseIntegrityError(
                "Could not retrieve the NSE security master needed to derive NSE_MAINBOARD_EQ. "
                "This repository does NOT fall back to a hard-coded equity list (PART 6).\n"
                f"{e}"
            ) from e

        mainboard = filter_mainboard_equity(result.frame)
        eq_be_filtered = mainboard.copy()  # kept for diagnostics — pre-exclusion, pre-dedup

        excluded_mask = pd.Series(False, index=mainboard.index)
        if self.excluded_symbols:
            excluded_mask |= mainboard["NSE_Symbol"].isin(self.excluded_symbols)
        if self.excluded_isins and "ISIN" in mainboard.columns:
            excluded_mask |= mainboard["ISIN"].isin(self.excluded_isins)
        excluded = mainboard[excluded_mask].copy()
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
        logger.info(
            "NSE_MAINBOARD_EQ diagnostics: raw=%d eq_be=%d excluded_known_non_equity=%d "
            "dropped_by_dedup=%d final=%d",
            self.last_diagnostics["raw_row_count"],
            self.last_diagnostics["eq_be_row_count"],
            self.last_diagnostics["excluded_known_non_equity_count"],
            self.last_diagnostics["dropped_by_dedup_count"],
            self.last_diagnostics["final_constituent_count"],
        )

        if not (self.min_count <= len(mainboard) <= self.max_count):
            raise UniverseIntegrityError(
                f"NSE_MAINBOARD_EQ derived from the security master has {len(mainboard)} "
                f"constituents (raw file had {result.row_count} rows before EQ/BE filtering), "
                f"outside the accepted sanity range [{self.min_count}, {self.max_count}]. "
                f"Refusing to treat this as a valid universe — the security-file schema may have "
                f"changed (see data/nse_reports.py::SECURITY_FILE_COLUMN_CANDIDATES)."
            )

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
