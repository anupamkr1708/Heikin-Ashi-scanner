"""python scripts/update_universe.py [--universe NIFTY_200|NSE_MAINBOARD_EQ] [--override-csv PATH]

Fetches and validates the current universe constituent list (via universe/factory.py, so this
picks the right provider for whichever universe_scope is configured — NIFTY_200 or
NSE_MAINBOARD_EQ), writes a snapshot + the security-master-equivalent constituent list to
data/processed/, and prints the resulting UniverseSnapshot. Raises (non-zero exit) on any
integrity failure — never falls back to a partial list (PART 6).
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime

import pandas as pd

from nse_scanner.config import load_config
from nse_scanner.data.base import UniverseProvider
from nse_scanner.data.storage import MarketDataStore
from nse_scanner.exceptions import UniverseIntegrityError
from nse_scanner.logging_config import configure_logging
from nse_scanner.universe.factory import get_universe_provider
from nse_scanner.universe.nifty200 import NSENifty200UniverseProvider
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

    snapshot = build_snapshot(
        universe_id=cfg.universe.universe_scope,
        constituents_raw=constituents,
        constituents_clean=constituents,
        source=source,
        source_version=None,
        retrieved_at=datetime.fromisoformat(retrieved_at_iso),
    )

    store = MarketDataStore(cfg.paths.processed_dir, cfg.paths.duckdb_path)
    store.write_parquet("universe_snapshots", pd.DataFrame([snapshot.to_dict()]))
    store.write_parquet("security_master_latest", constituents)

    print(
        f"Universe snapshot OK: {snapshot.constituent_count} constituents from {source} "
        f"(universe_scope={cfg.universe.universe_scope})"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
