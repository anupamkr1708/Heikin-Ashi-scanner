"""run_daily — the ONE-COMMAND daily entry point.

Normal usage:           python scripts/run_daily.py
Forced date:             python scripts/run_daily.py --date 2026-09-10
Offline fixture mode:    python scripts/run_daily.py --offline-fixture
                          (parses tests/fixtures/nse/*, no network — for CI/dev/debugging)

Workflow (continuation-prompt "CRITICAL DAILY EOD WORKFLOW"):
    1. determine current IST date/time
    2. determine the expected completed NSE trading session (calendar-based, never `today - 1`)
    3. check whether the final EOD report for that session is actually published yet
       (data-driven — never assumes 15:30 == available)
    4. if not available: print STATUS = WAITING_FOR_EOD_DATA and exit non-zero (no silent
       substitution of an older session — continuation-prompt "Do NOT use D-1 data silently")
    5. ingest (download, hash, parse, validate, store) — ONE session's worth, appended to
       whatever history already exists locally (see scripts/bootstrap_history.py for populating
       that history in the first place; run_daily.py does NOT do a multi-year historical
       download itself, by design — PART "Daily production... append only the new session")
    6. run the scan (universe -> features -> signal -> optional context)
    7. write the Excel report + run manifest
    8. print a concise health summary
"""

from __future__ import annotations

import argparse
import sys
import zoneinfo
from datetime import date, datetime
from pathlib import Path

from nse_scanner.config import load_config
from nse_scanner.data.benchmark import YahooBenchmarkProvider
from nse_scanner.data.calendar import resolve_holidays_for_run, resolve_session
from nse_scanner.data.nse_eod import NSEDataProvider
from nse_scanner.data.storage import MarketDataStore
from nse_scanner.exceptions import CalendarError, NseScannerError, UniverseIntegrityError
from nse_scanner.logging_config import configure_logging, get_logger
from nse_scanner.pipeline.ingestion import STATUS_INGESTED, STATUS_WAITING_FOR_EOD_DATA, ingest_session
from nse_scanner.pipeline.scan import run_scan
from nse_scanner.reporting.excel import write_report
from nse_scanner.reporting.run_manifest import build_run_manifest
from nse_scanner.universe.factory import get_universe_provider

IST = zoneinfo.ZoneInfo("Asia/Kolkata")

logger = get_logger(__name__)


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="NSE EOD/T-1 Technical Scanner — one-command daily run")
    p.add_argument("--date", type=str, default=None, help="Force a specific session date (YYYY-MM-DD)")
    p.add_argument(
        "--offline-fixture",
        action="store_true",
        help="Use tests/fixtures/nse/* instead of live NSE/yfinance — no network required",
    )
    p.add_argument("--universe", type=str, default=None, help="Override universe_scope (e.g. NIFTY_200)")
    p.add_argument("--config", type=str, default="config/default.yaml", help="Path to YAML config")
    p.add_argument("--skip-ingest", action="store_true", help="Skip EOD ingestion, scan existing local data only")
    p.add_argument(
        "--allow-missing-holidays",
        action="store_true",
        help="Degrade to a weekday-only calendar instead of blocking when "
        "config/nse_holidays.yaml is missing a required year (NOT recommended "
        "for a production run — see README)",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    configure_logging()
    args = build_arg_parser().parse_args(argv)

    cli_overrides = {}
    if args.universe:
        cli_overrides["universe"] = {"universe_scope": args.universe}
    cfg = load_config(args.config, cli_overrides)

    store = MarketDataStore(cfg.paths.processed_dir, cfg.paths.duckdb_path)

    now_ist = (
        datetime.now(IST)
        if args.date is None
        else datetime.combine(date.fromisoformat(args.date), datetime.min.time(), tzinfo=IST).replace(hour=16)
    )  # forced-date mode treats the date as already-closed for session purposes

    # Production default is STRICT: a missing required holiday year blocks the run rather than
    # silently falling back to a weekday-only calendar. --offline-fixture never touches the real
    # calendar at all (synthetic session dates), and --allow-missing-holidays is an explicit,
    # named escape hatch for someone who has accepted that risk.
    strict_calendar = not (args.offline_fixture or args.allow_missing_holidays)
    try:
        holidays = resolve_holidays_for_run("config/nse_holidays.yaml", now_ist.date(), strict=strict_calendar)
    except CalendarError as e:
        print("=" * 70)
        print("BLOCKED: NSE HOLIDAY CALENDAR INCOMPLETE")
        print("=" * 70)
        print(str(e))
        print()
        print(
            "Re-run with --allow-missing-holidays to proceed anyway with a weekday-only "
            "calendar (staleness detection will be less precise around holidays), or "
            "--offline-fixture to test without touching the real calendar at all."
        )
        return 5

    session = resolve_session(now_ist, holidays)

    print("=" * 70)
    print("NSE EOD/T-1 TECHNICAL SCANNER")
    print("=" * 70)
    print(f"RUN DATE (IST):              {session.as_of_date.isoformat()}")
    print(f"EXPECTED COMPLETED SESSION:  {session.expected_completed_session.isoformat()}")
    print(f"SIGNAL DATE:                 {session.signal_date.isoformat()}")
    print(f"PLANNED NEXT SESSION:        {session.planned_entry_date.isoformat()}")
    print("-" * 70)

    if args.offline_fixture:
        print("MODE: OFFLINE FIXTURE (no network) — see tests/fixtures/nse/")
        from nse_scanner.offline_fixture import run_offline_fixture_scan

        return run_offline_fixture_scan(cfg, session, store)

    if not args.skip_ingest:
        ingestion_result = ingest_session(session.expected_completed_session, store, cfg.paths.raw_dir)
        print(f"INGESTION STATUS:            {ingestion_result.status}")
        if ingestion_result.status == STATUS_WAITING_FOR_EOD_DATA:
            print(f"DETAIL: {ingestion_result.detail}")
            print("Refusing to scan using an older/incomplete session. Try again later.")
            return 2
        if ingestion_result.status != STATUS_INGESTED:
            print(f"DETAIL: {ingestion_result.detail}")
            print("Ingestion failed. See logs above. Refusing to produce a possibly-misleading report.")
            return 1
        print(f"ROWS INGESTED:                {ingestion_result.rows_ingested}")

    try:
        universe_provider = get_universe_provider(cfg)
    except NseScannerError as e:
        print(f"UNIVERSE CONFIGURATION ERROR: {e}")
        return 1

    data_provider = NSEDataProvider(store=store)
    # AS-OF binding (research-integrity fix): without this, the provider defaults to
    # as_of_date=None and fetches an unbounded, rolling `period=` window from yfinance — which can
    # include a same-day/in-progress index row (e.g. a 2026-09-24 ^NSEI row while the session this
    # run is actually scoring is 2026-09-23). Binding explicitly to the same
    # `session.expected_completed_session` that `run_replay.py` already uses activates the
    # provider's existing request+response-layer cutoff (see data/benchmark.py) for the daily path
    # too. No new mechanism — this reuses what run_replay.py already relies on.
    benchmark_provider = YahooBenchmarkProvider(cfg.data, as_of_date=session.expected_completed_session)

    try:
        result = run_scan(
            cfg,
            universe_provider,
            data_provider,
            benchmark_provider,
            session,
            holidays,
            symbol_override_path="config/symbol_overrides.yaml",
        )
    except UniverseIntegrityError as e:
        print("UNIVERSE INTEGRITY FAILURE — ABORTING RUN (no partial-universe fallback).")
        print(str(e))
        return 3
    except NseScannerError as e:
        print(f"SCAN FAILED: {type(e).__name__}: {e}")
        return 1

    report_path = Path(cfg.paths.reports_dir) / f"NSE_Technical_Scanner_{session.signal_date.isoformat()}.xlsx"
    write_report(result.sheets, report_path)

    manifest = build_run_manifest(
        run_id=result.run_id,
        cfg=cfg,
        universe_id=cfg.universe.universe_scope,
        universe_snapshot_date=session.as_of_date.isoformat(),
        universe_source=result.universe_source,
        constituent_count=result.constituent_count,
        data_provider=data_provider.name,
        data_as_of=session.expected_completed_session.isoformat(),
        expected_session=session.expected_completed_session.isoformat(),
        signal_date=session.signal_date.isoformat(),
        status=result.run_health,
        price_basis=result.price_basis,
        # PART 54 requires the manifest (the machine-readable record) to carry these — they were
        # already being computed (ScanRunResult / IngestionResult) and printed to the console
        # below, but previously silently dropped rather than written to run_manifest_*.json.
        # Found + fixed during the v1.3 research-integrity audit.
        extra={
            "data_file_hash": ingestion_result.file_hash if not args.skip_ingest else None,
            "data_file_path": ingestion_result.raw_file_path if not args.skip_ingest else None,
            "benchmark_status": result.benchmark_status,
            "data_validation_failures": result.data_validation_failures,
            "insufficient_history_count": result.insufficient_history_count,
            "security_scan_failures": result.security_scan_failures,
            "signals_current": result.signals_current,
            "signals_stale": result.signals_stale,
            "needs_bootstrap": result.needs_bootstrap,
        },
    )
    manifest_path = Path(cfg.paths.reports_dir) / f"run_manifest_{session.signal_date.isoformat()}.json"
    manifest.write(manifest_path)

    print("-" * 70)
    print(f"UNIVERSE:                    {result.constituent_count} ({result.universe_source})")
    print(f"PRICE BASIS:                 {result.price_basis}")
    print(f"BENCHMARK:                   {result.benchmark_status}")
    print(
        f"DATA VALIDATION FAILURES:    {result.data_validation_failures}"
        f" (of which insufficient-history: {result.insufficient_history_count})"
    )
    print(f"SECURITY SCAN FAILURES:      {result.security_scan_failures}")
    print(f"CURRENT SIGNALS:             {result.signals_current}")
    print(f"STALE CANDIDATES:            {result.signals_stale}")
    print(f"RUN_HEALTH:                  {result.run_health}")
    print(f"REPORT:                      {report_path}")
    print(f"MANIFEST:                    {manifest_path}")

    if result.needs_bootstrap:
        print("-" * 70)
        print("NOTICE: most of the universe has insufficient local history for the baseline")
        print(
            "strategy (< {} rows). This is expected on a freshly-installed database — the".format(
                cfg.history.min_rows_primary
            )
        )
        print("scanner does not claim to have current technical signals until enough history")
        print("has been populated. Run this once:")
        print()
        print(f"    python scripts/bootstrap_history.py --universe {cfg.universe.universe_scope} --period 2y")
        print()
        print("then re-run this script daily as normal.")

    print("=" * 70)

    return 0 if result.run_health != "RED" else 4


if __name__ == "__main__":
    sys.exit(main())
