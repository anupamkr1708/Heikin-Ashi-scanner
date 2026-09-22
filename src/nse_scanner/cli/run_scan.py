"""python scripts/run_scan.py --universe NIFTY_200 [--as-of YYYY-MM-DD]

Runs the scan against ALREADY-INGESTED local data (does not fetch a new EOD session — use
ingest_eod.py or run_daily.py for that). Useful for re-running the scan/report step alone, e.g.
after changing config, without re-downloading data.
"""

from __future__ import annotations

import argparse
import sys
import zoneinfo
from datetime import datetime
from pathlib import Path

from nse_scanner.config import load_config
from nse_scanner.data.benchmark import YahooBenchmarkProvider
from nse_scanner.data.calendar import resolve_holidays_for_run, resolve_session
from nse_scanner.data.nse_eod import NSEDataProvider
from nse_scanner.data.storage import MarketDataStore
from nse_scanner.exceptions import CalendarError, NseScannerError, UniverseIntegrityError
from nse_scanner.logging_config import configure_logging
from nse_scanner.pipeline.scan import run_scan as run_scan_pipeline
from nse_scanner.reporting.excel import write_report
from nse_scanner.reporting.run_manifest import build_run_manifest
from nse_scanner.universe.factory import get_universe_provider

IST = zoneinfo.ZoneInfo("Asia/Kolkata")


def main(argv: list[str] | None = None) -> int:
    configure_logging()
    p = argparse.ArgumentParser()
    p.add_argument("--universe", default=None)
    p.add_argument("--as-of", default=None)
    p.add_argument("--config", default="config/default.yaml")
    p.add_argument("--allow-missing-holidays", action="store_true")
    args = p.parse_args(argv)

    overrides = {"universe": {"universe_scope": args.universe}} if args.universe else {}
    cfg = load_config(args.config, overrides)

    now_ist = datetime.now(IST) if not args.as_of else datetime.combine(
        datetime.fromisoformat(args.as_of).date(), datetime.min.time(), tzinfo=IST
    ).replace(hour=16)

    try:
        holidays = resolve_holidays_for_run("config/nse_holidays.yaml", now_ist.date(),
                                             strict=not args.allow_missing_holidays)
    except CalendarError as e:
        print(f"BLOCKED: {e}", file=sys.stderr)
        print("Re-run with --allow-missing-holidays to proceed with a weekday-only calendar.",
              file=sys.stderr)
        return 5

    session = resolve_session(now_ist, holidays)

    store = MarketDataStore(cfg.paths.processed_dir, cfg.paths.duckdb_path)
    try:
        universe_provider = get_universe_provider(cfg)
    except NseScannerError as e:
        print(f"UNIVERSE CONFIGURATION ERROR: {e}", file=sys.stderr)
        return 1

    data_provider = NSEDataProvider(store=store)
    benchmark_provider = YahooBenchmarkProvider(cfg.data)

    try:
        result = run_scan_pipeline(cfg, universe_provider, data_provider, benchmark_provider, session,
                                    holidays, symbol_override_path="config/symbol_overrides.yaml")
    except (UniverseIntegrityError, NseScannerError) as e:
        print(f"SCAN FAILED: {type(e).__name__}: {e}", file=sys.stderr)
        return 1

    report_path = Path(cfg.paths.reports_dir) / f"NSE_Technical_Scanner_{session.signal_date.isoformat()}.xlsx"
    write_report(result.sheets, report_path)
    manifest = build_run_manifest(
        run_id=result.run_id, cfg=cfg, universe_id=cfg.universe.universe_scope,
        universe_snapshot_date=session.as_of_date.isoformat(), universe_source=result.universe_source,
        constituent_count=result.constituent_count, data_provider=data_provider.name,
        data_as_of=session.expected_completed_session.isoformat(),
        expected_session=session.expected_completed_session.isoformat(),
        signal_date=session.signal_date.isoformat(), status=result.run_health,
        price_basis=result.price_basis,
        # See run_daily.py for why these are here (v1.3 audit finding: computed but previously
        # dropped before reaching the manifest). No ingestion happens in this entry point, so no
        # data_file_hash field here — that's specific to run_daily.py's ingest step.
        extra={
            "benchmark_status": result.benchmark_status,
            "data_validation_failures": result.data_validation_failures,
            "insufficient_history_count": result.insufficient_history_count,
            "security_scan_failures": result.security_scan_failures,
            "signals_current": result.signals_current,
            "signals_stale": result.signals_stale,
            "needs_bootstrap": result.needs_bootstrap,
        },
    )
    manifest.write(Path(cfg.paths.reports_dir) / f"run_manifest_{session.signal_date.isoformat()}.json")

    print(f"Signals: {result.signals_current} current, {result.signals_stale} stale. "
          f"RUN_HEALTH={result.run_health}. Price basis: {result.price_basis}. Report: {report_path}")
    if result.needs_bootstrap:
        print(f"NOTICE: most of the universe lacks sufficient history — run "
              f"scripts/bootstrap_history.py --universe {cfg.universe.universe_scope} --period 2y")
    return 0


if __name__ == "__main__":
    sys.exit(main())
