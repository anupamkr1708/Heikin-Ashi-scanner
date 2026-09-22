"""Normalized internal OHLCV schema.

The strategy engine consumes only this schema — it never knows whether a bar came from NSE or
Yahoo Finance (PART 8 / PART 65). Price basis (RAW vs ADJUSTED) is always explicit and is never
mixed within one DataFrame (PART 9).
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict

REQUIRED_OHLCV_COLUMNS = ("Open", "High", "Low", "Close", "Volume")


class PriceBasis:
    RAW = "RAW"
    ADJUSTED = "ADJUSTED"


class NormalizedBarMeta(BaseModel):
    """Metadata attached to a normalized OHLCV DataFrame (one per security-per-fetch, not
    per-row — PART 8's `NormalizedOHLCV` concept, implemented here as sidecar metadata next to
    a plain pandas DataFrame rather than one Pydantic object per row, for performance)."""

    model_config = ConfigDict(frozen=True)

    isin: str | None
    nse_symbol: str
    source: str                 # "NSE" | "YFINANCE"
    price_basis: str            # PriceBasis.RAW | PriceBasis.ADJUSTED
    interval: str = "1d"
    retrieved_at: datetime | None = None
    provider_version: str | None = None
