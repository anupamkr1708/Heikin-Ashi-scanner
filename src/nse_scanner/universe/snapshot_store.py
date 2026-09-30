"""Append-only universe snapshot history (mainboard-universe-integrity-v2, Tasks 8 & 10).

Until now the only persisted universe artifacts were `security_master_latest.parquet` and
`universe_snapshots.parquet`, both **overwritten on every run** (`MarketDataStore.write_parquet`
is `df.to_parquet(path)`), so no history existed and no drift could be computed — see
MAINBOARD_UNIVERSE_INTEGRITY_V2_AUDIT.md A.8. Those two files are still written unchanged (the
research CLIs read `security_master_latest`); this module adds a history *alongside* them:

    <processed_dir>/universe_history/<universe_id>/<stamp>_members.parquet
    <processed_dir>/universe_history/<universe_id>/<stamp>_snapshot.parquet

`<stamp>` is the snapshot's own `retrieved_at` in UTC (`YYYYMMDDTHHMMSSZ`) — deliberately the
*observation* time, because for an undated source (EQUITY_L.csv) no source date exists to key on;
the source date, when there is one, lives inside the snapshot record (`source_date`), never in the
filename.

**What this is and is not.** It is an append-only record of what this project *observed* on the
days it ran `update_universe`, each entry carrying the source hash so the exact bytes can be
identified. It is NOT point-in-time reconstruction of NSE's membership on an arbitrary past date:
a date on which no snapshot was taken cannot be recovered, and an undated source cannot be
back-dated. Do not describe it as PIT support.

Writes refuse to overwrite an existing stamp (append-only is the point).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from nse_scanner.universe.validation import UniverseSnapshot

_STAMP_FORMAT = "%Y%m%dT%H%M%SZ"


def snapshot_stamp(retrieved_at: datetime) -> str:
    """UTC stamp for a retrieval time. A naive datetime is interpreted as UTC (never local time)."""
    if retrieved_at.tzinfo is None:
        retrieved_at = retrieved_at.replace(tzinfo=timezone.utc)
    return retrieved_at.astimezone(timezone.utc).strftime(_STAMP_FORMAT)


def _history_dir(processed_dir: str | Path, universe_id: str) -> Path:
    return Path(processed_dir) / "universe_history" / universe_id


def write_universe_snapshot(
    processed_dir: str | Path, snapshot: UniverseSnapshot, members: pd.DataFrame
) -> tuple[Path, Path]:
    """Persist one run's members + provenance record. Raises FileExistsError rather than
    overwriting an existing stamp."""
    d = _history_dir(processed_dir, snapshot.universe_id)
    d.mkdir(parents=True, exist_ok=True)
    stamp = snapshot_stamp(snapshot.retrieved_at)
    members_path = d / f"{stamp}_members.parquet"
    snapshot_path = d / f"{stamp}_snapshot.parquet"
    if members_path.exists() or snapshot_path.exists():
        raise FileExistsError(
            f"universe snapshot {stamp} already exists for {snapshot.universe_id}; refusing to overwrite"
        )
    row = snapshot.to_dict()
    # Nested diagnostics (int-keyed series breakdowns, possibly-empty dicts) are not reliably
    # parquet-serialisable; JSON text round-trips exactly and stays human-readable.
    row["diagnostics"] = (
        json.dumps(row["diagnostics"], sort_keys=True, default=str) if row["diagnostics"] is not None else None
    )
    members.to_parquet(members_path, index=False)
    pd.DataFrame([row]).to_parquet(snapshot_path, index=False)
    return members_path, snapshot_path


def list_snapshot_stamps(processed_dir: str | Path, universe_id: str) -> list[str]:
    d = _history_dir(processed_dir, universe_id)
    if not d.exists():
        return []
    return sorted(p.name.removesuffix("_snapshot.parquet") for p in d.glob("*_snapshot.parquet"))


def _resolve_stamp(processed_dir: str | Path, universe_id: str, stamp: str | None) -> str | None:
    stamps = list_snapshot_stamps(processed_dir, universe_id)
    if stamp is None:
        return stamps[-1] if stamps else None
    return stamp if stamp in stamps else None


def read_universe_members(processed_dir: str | Path, universe_id: str, stamp: str | None = None) -> pd.DataFrame | None:
    """Members for `stamp`, or the most recent snapshot if `stamp` is None. None if unavailable —
    never a guess at the nearest date."""
    s = _resolve_stamp(processed_dir, universe_id, stamp)
    if s is None:
        return None
    return pd.read_parquet(_history_dir(processed_dir, universe_id) / f"{s}_members.parquet")


def read_universe_snapshot_record(processed_dir: str | Path, universe_id: str, stamp: str | None = None) -> dict | None:
    s = _resolve_stamp(processed_dir, universe_id, stamp)
    if s is None:
        return None
    row = pd.read_parquet(_history_dir(processed_dir, universe_id) / f"{s}_snapshot.parquet").iloc[0].to_dict()
    if isinstance(row.get("diagnostics"), str):
        row["diagnostics"] = json.loads(row["diagnostics"])
    return row
