"""python scripts/ingest_eod.py --date YYYY-MM-DD [--force]

Ingests exactly one session's NSE bhavcopy into the local store. Most users should prefer
`run_daily.py`, which determines the date automatically; this script is for backfilling specific
historical sessions.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date

from nse_scanner.config import load_config
from nse_scanner.data.storage import MarketDataStore
from nse_scanner.logging_config import configure_logging
from nse_scanner.pipeline.ingestion import STATUS_INGESTED, ingest_session


def main(argv: list[str] | None = None) -> int:
    configure_logging()
    p = argparse.ArgumentParser()
    p.add_argument("--date", required=True, help="Session date, YYYY-MM-DD")
    p.add_argument("--config", default="config/default.yaml")
    p.add_argument(
        "--no-availability-check",
        action="store_true",
        help="Skip the pre-flight availability check (retry immediately)",
    )
    args = p.parse_args(argv)

    cfg = load_config(args.config)
    store = MarketDataStore(cfg.paths.processed_dir, cfg.paths.duckdb_path)
    result = ingest_session(
        date.fromisoformat(args.date), store, cfg.paths.raw_dir, check_availability_first=not args.no_availability_check
    )

    print(f"STATUS: {result.status}")
    print(f"DETAIL: {result.detail}")
    if result.status == STATUS_INGESTED:
        print(f"ROWS:   {result.rows_ingested}")
        print(f"HASH:   {result.file_hash}")
        print(f"RAW:    {result.raw_file_path}")
    return 0 if result.status == STATUS_INGESTED else 1


if __name__ == "__main__":
    sys.exit(main())
