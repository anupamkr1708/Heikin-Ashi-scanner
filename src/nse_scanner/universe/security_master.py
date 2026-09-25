"""Builds the security master from a validated universe snapshot + symbol mapping.

Note on ISIN: NSE's public index-constituent CSVs (e.g. ind_nifty200list.csv) include an ISIN
column in practice, but this repository's sandbox could not fetch a live file to confirm the
exact current column name — `build_security_master` therefore accepts ISIN as optional and
never fabricates one (PART 4 says ISIN is the canonical identity, not that this repository may
invent ISINs when a source omits them). Records without a confirmed ISIN get
`mapping_status = PENDING` and `isin = None`, which reporting explicitly surfaces rather than
silently treating the NSE symbol as if it were the canonical identity.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd

from nse_scanner.models.security import SecurityRecord
from nse_scanner.universe.symbol_mapping import map_symbol_to_yfinance


def build_security_master(
    constituents_df: pd.DataFrame, override_table: dict[str, str], source: str, source_version: str | None
) -> list[SecurityRecord]:
    now = datetime.now(timezone.utc)
    records: list[SecurityRecord] = []
    for row in constituents_df.itertuples(index=False):
        mapping = map_symbol_to_yfinance(row.Symbol, override_table)
        isin = getattr(row, "ISIN", None)
        records.append(
            SecurityRecord(
                isin=isin if isin else f"UNKNOWN:{row.Symbol}",
                nse_symbol=row.Symbol,
                company_name=getattr(row, "Company_Name", row.Symbol),
                sector=getattr(row, "Sector", None),
                yf_symbol=mapping.yf_symbol,
                mapping_status=mapping.status,
                mapping_method=mapping.method,
                mapping_confidence=mapping.confidence,
                source=source,
                source_version=source_version,
                retrieved_at=now,
                updated_at=now,
            )
        )
    return records


def security_master_to_frame(records: list[SecurityRecord]) -> pd.DataFrame:
    return pd.DataFrame([r.model_dump() for r in records])
