"""NSE_MAINBOARD_EQ universe provider (PART 5 / continuation "make NIFTY_200 and
NSE_MAINBOARD_EQ really different").

Derives the mainboard-equity universe from the official NSE CM-MII security master file
(`data/nse_reports.py::fetch_security_file` + `filter_mainboard_equity`), never from a hard-coded
list. Same hard-fail integrity policy as `universe/nifty200.py`: a failure to retrieve/validate
the security file raises `UniverseIntegrityError` and the run stops — it does not silently
substitute a smaller/stale list.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from nse_scanner.data.base import UniverseProvider
from nse_scanner.data.nse_reports import fetch_security_file, filter_mainboard_equity
from nse_scanner.exceptions import DataProviderError, UniverseIntegrityError
from nse_scanner.logging_config import get_logger

logger = get_logger(__name__)


@dataclass
class NSEMainboardEquityUniverseProvider(UniverseProvider):
    min_count: int = 500  # sanity bounds — NSE mainboard equity count is in the low thousands
    max_count: int = 4000

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
        mainboard = mainboard.drop_duplicates(subset="NSE_Symbol").reset_index(drop=True)

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
