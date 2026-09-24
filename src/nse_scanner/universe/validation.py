"""Universe snapshot metadata (PART 6).

Every universe fetch produces a `UniverseSnapshot` record alongside the constituents DataFrame,
so a stored/cached universe is never reused without knowing what produced it, when, and whether
it passed validation.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

import pandas as pd


@dataclass(frozen=True)
class UniverseSnapshot:
    universe_id: str
    snapshot_date: date
    source: str
    source_version: str | None
    retrieved_at: datetime
    constituent_count: int
    symbol_count: int
    duplicate_count: int
    invalid_count: int
    validation_status: str   # "VALID" | "INVALID"
    # P1 provider-hardening additions (data/nse_reports.py's security-master pipeline) — optional,
    # default None so the existing NIFTY_200 caller (build_snapshot below) is unaffected.
    source_url: str | None = None
    file_hash: str | None = None
    schema_version: str | None = None
    raw_row_count: int | None = None    # rows in the raw file before any eligibility filtering
    eligible_count: int | None = None   # rows after eligibility filtering (== constituent_count
                                         # for this pipeline; kept as its own named field since the
                                         # distinction from raw_row_count is the point of recording it)
    definition: str | None = None       # human-readable universe-membership rule, e.g. "Series in {EQ,BE}"

    def to_dict(self) -> dict:
        return {
            "universe_id": self.universe_id,
            "snapshot_date": self.snapshot_date.isoformat(),
            "source": self.source,
            "source_version": self.source_version,
            "retrieved_at": self.retrieved_at.isoformat(),
            "constituent_count": self.constituent_count,
            "symbol_count": self.symbol_count,
            "duplicate_count": self.duplicate_count,
            "invalid_count": self.invalid_count,
            "validation_status": self.validation_status,
            "source_url": self.source_url,
            "file_hash": self.file_hash,
            "schema_version": self.schema_version,
            "raw_row_count": self.raw_row_count,
            "eligible_count": self.eligible_count,
            "definition": self.definition,
        }


def build_snapshot(universe_id: str, constituents_raw: pd.DataFrame, constituents_clean: pd.DataFrame,
                    source: str, source_version: str | None, retrieved_at: datetime) -> UniverseSnapshot:
    raw_count = len(constituents_raw)
    clean_count = len(constituents_clean)
    dupes = int(constituents_raw["Symbol"].duplicated().sum()) if "Symbol" in constituents_raw.columns else 0
    invalid = max(raw_count - clean_count - dupes, 0)
    return UniverseSnapshot(
        universe_id=universe_id,
        snapshot_date=retrieved_at.date(),
        source=source,
        source_version=source_version,
        retrieved_at=retrieved_at,
        constituent_count=clean_count,
        symbol_count=clean_count,
        duplicate_count=dupes,
        invalid_count=invalid,
        validation_status="VALID" if clean_count > 0 else "INVALID",
    )
