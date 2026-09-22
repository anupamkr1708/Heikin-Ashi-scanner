"""Security master model.

Canonical identity is ISIN. ISIN != NSE symbol != Yahoo symbol — tickers change, companies
rename, symbols change; this model treats them as separate, explicitly-mapped attributes
(PART 4) rather than assuming a fixed transform between them.
"""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field


class MappingStatus:
    OK = "OK"
    PENDING = "PENDING"
    OVERRIDE = "OVERRIDE"
    ERROR = "ERROR"
    NOT_SCANNED = "NOT_SCANNED"


class SecurityRecord(BaseModel):
    """One row of the security master (PART 4). `isin` is the stable identity; everything else
    is a versioned attribute of that identity."""

    model_config = ConfigDict(frozen=True)

    isin: str
    nse_symbol: str
    company_name: str
    series: str = "EQ"
    security_code: str | None = None
    status: str = "ACTIVE"
    active: bool = True
    date_of_listing: date | None = None
    date_of_deactivation: date | None = None
    sector: str | None = None

    yf_symbol: str | None = None
    historical_symbol_aliases: tuple[str, ...] = Field(default_factory=tuple)
    symbol_valid_from: date | None = None
    symbol_valid_to: date | None = None

    mapping_status: str = MappingStatus.PENDING
    mapping_method: str | None = None
    mapping_confidence: float | None = None
    mapping_error: str | None = None

    source: str = ""
    source_version: str | None = None
    retrieved_at: datetime | None = None
    updated_at: datetime | None = None
