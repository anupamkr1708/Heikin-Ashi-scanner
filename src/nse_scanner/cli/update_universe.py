"""python scripts/update_universe.py [--universe NIFTY_200|NSE_MAINBOARD_EQ] [--override-csv PATH]

Fetches and validates the current universe constituent list (via universe/factory.py, so this
picks the right provider for whichever universe_scope is configured — NIFTY_200 or
NSE_MAINBOARD_EQ), writes a snapshot + the security-master-equivalent constituent list to
data/processed/, and prints the resulting UniverseSnapshot. Raises (non-zero exit) on any
integrity failure — never falls back to a partial list (PART 6).
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from datetime import datetime

import pandas as pd

from nse_scanner.config import load_config
from nse_scanner.data.base import UniverseProvider
from nse_scanner.data.storage import MarketDataStore
from nse_scanner.exceptions import UniverseIntegrityError
from nse_scanner.logging_config import configure_logging
from nse_scanner.universe.drift import compute_universe_drift
from nse_scanner.universe.factory import get_universe_provider
from nse_scanner.universe.mainboard import NSEMainboardEquityUniverseProvider
from nse_scanner.universe.nifty200 import NSENifty200UniverseProvider
from nse_scanner.universe.snapshot_store import (
    read_universe_members,
    read_universe_snapshot_record,
    write_universe_snapshot,
)
from nse_scanner.universe.validation import build_snapshot


def main(argv: list[str] | None = None) -> int:
    configure_logging()
    p = argparse.ArgumentParser()
    p.add_argument("--universe", default=None, help="Override universe_scope (e.g. NIFTY_200, NSE_MAINBOARD_EQ)")
    p.add_argument("--config", default="config/default.yaml")
    p.add_argument("--override-csv", default=None, help="NIFTY_200 only: path to a manual constituent CSV")
    args = p.parse_args(argv)

    overrides = {"universe": {"universe_scope": args.universe}} if args.universe else {}
    cfg = load_config(args.config, overrides)

    provider: UniverseProvider
    if args.override_csv and cfg.universe.universe_scope == "NIFTY_200":
        provider = NSENifty200UniverseProvider(
            min_count=cfg.universe.nifty200_min_count,
            max_count=cfg.universe.nifty200_max_count,
            override_csv_path=args.override_csv,
        )
    else:
        try:
            provider = get_universe_provider(cfg)
        except Exception as e:  # noqa: BLE001 - surfaced as a clean CLI error below
            print(f"UNIVERSE CONFIGURATION ERROR: {e}", file=sys.stderr)
            return 1

    try:
        constituents, source, retrieved_at_iso = provider.get_constituents()
    except UniverseIntegrityError as e:
        print(f"UNIVERSE INTEGRITY FAILURE:\n{e}", file=sys.stderr)
        return 1

    if isinstance(provider, NSEMainboardEquityUniverseProvider):
        # Task 1 finding A.7: the provenance-correct snapshot (real source date / explicit
        # "undated" basis, file hash, funnel diagnostics) already existed but was never used here.
        snapshot = provider.build_universe_snapshot()
    else:
        snapshot = build_snapshot(  # NIFTY_200: construction unchanged
            universe_id=cfg.universe.universe_scope,
            constituents_raw=constituents,
            constituents_clean=constituents,
            source=source,
            source_version=None,
            retrieved_at=datetime.fromisoformat(retrieved_at_iso),
        )

    # Task 9: drift vs the PREVIOUS snapshot in the append-only history (read BEFORE writing this
    # run's entry). Report-only — never aborts on a membership change.
    previous_members = read_universe_members(cfg.paths.processed_dir, snapshot.universe_id)
    previous_record = read_universe_snapshot_record(cfg.paths.processed_dir, snapshot.universe_id)
    drift = compute_universe_drift(
        previous_members,
        constituents,
        previous_diagnostics=(previous_record or {}).get("diagnostics"),
        current_diagnostics=snapshot.diagnostics,
    )
    snapshot = dataclasses.replace(snapshot, diagnostics={**(snapshot.diagnostics or {}), "drift": drift})

    store = MarketDataStore(cfg.paths.processed_dir, cfg.paths.duckdb_path)
    legacy_row = snapshot.to_dict()
    # Nested dict diagnostics are not reliably parquet-serialisable (empty dicts, mixed types) —
    # store as JSON text, same as the history store.
    legacy_row["diagnostics"] = json.dumps(legacy_row["diagnostics"], sort_keys=True, default=str)
    store.write_parquet("universe_snapshots", pd.DataFrame([legacy_row]))  # legacy: latest only
    store.write_parquet("security_master_latest", constituents)  # legacy: read by research CLIs
    write_universe_snapshot(cfg.paths.processed_dir, snapshot, constituents)  # append-only history

    print(
        f"Universe snapshot OK: {snapshot.constituent_count} constituents from {source} "
        f"(universe_scope={cfg.universe.universe_scope})"
    )
    print(
        f"  source_date={snapshot.source_date.isoformat() if snapshot.source_date else 'UNAVAILABLE'} "
        f"(snapshot_date_basis={snapshot.snapshot_date_basis or 'n/a'}) "
        f"universe_definition_id={snapshot.universe_definition_id or 'n/a'} drift={drift['status']}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
