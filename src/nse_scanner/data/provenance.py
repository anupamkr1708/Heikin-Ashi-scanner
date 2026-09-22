"""Data provenance records (PART 13).

Every ingestion operation should produce one of these and persist it (e.g. alongside the
run_manifest, or in a `provenance` table) so a cached/stored dataset is never reused without
knowing what produced it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class ProvenanceRecord:
    source: str
    provider: str
    provider_version: str | None
    request_start: datetime
    request_end: datetime
    retrieved_at: datetime
    file_name: str | None
    file_hash: str | None
    row_count: int
    valid_row_count: int
    schema_version: str
    price_basis: str
    adjustment_mode: str
    interval: str
    universe_version: str | None
    software_version: str
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = {
            "source": self.source,
            "provider": self.provider,
            "provider_version": self.provider_version,
            "request_start": self.request_start.isoformat(),
            "request_end": self.request_end.isoformat(),
            "retrieved_at": self.retrieved_at.isoformat(),
            "file_name": self.file_name,
            "file_hash": self.file_hash,
            "row_count": self.row_count,
            "valid_row_count": self.valid_row_count,
            "schema_version": self.schema_version,
            "price_basis": self.price_basis,
            "adjustment_mode": self.adjustment_mode,
            "interval": self.interval,
            "universe_version": self.universe_version,
            "software_version": self.software_version,
        }
        d.update(self.extra)
        return d
