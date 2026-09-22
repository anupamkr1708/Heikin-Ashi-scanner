"""python scripts/run_replay.py --universe NIFTY_200 --as-of 2026-09-08

Answers: "What would the baseline BB+HA strategy have detected on historical date T, using only
information actually available by the close of T?" (PHASE 1 — the highest-priority requirement
of this release).

This command is READ-ONLY: it never writes to the historical database, never ingests, never
appends. It only reads what's already locally stored, through the `AsOfDataProvider` cutoff
(`data/asof_provider.py`), which makes it structurally impossible for this command to use any
observation dated after `--as-of` — not a documentation promise, an enforced query filter.

**Universe caveat, stated up front, not buried:** this repository has no verified point-in-time
NIFTY-200-membership feed. This command uses TODAY's constituent list applied to the historical
date (`research/survivorship.py::label_current_universe_mode()`), and the report is labeled
`SURVIVORSHIP_BIAS_PRESENT = True` accordingly — it is never presented as if it were the actual
historical membership.
"""

from __future__ import annotations

import argparse
import sys
import zoneinfo
from datetime import date, datetime
from pathlib import Path

from nse_scanner.config import load_config
from nse_scanner.data.asof_provider import AsOfDataProvider
from nse_scanner.data.benchmark import YahooBenchmarkProvider
from nse_scanner.data.calendar import resolve_holidays_for_run, resolve_session
from nse_scanner.data.storage import PRICE_BASIS_BLENDED, MarketDataStore
from nse_scanner.exceptions import CalendarError, NseScannerError, UniverseIntegrityError
from nse_scanner.logging_config import configure_logging
from nse_scanner.pipeline.scan import run_scan
from nse_scanner.reporting.excel import write_report
from nse_scanner.reporting.run_manifest import build_run_manifest
from nse_scanner.research.survivorship import label_current_universe_mode
from nse_scanner.universe.factory import get_universe_provider

IST = zoneinfo.ZoneInfo("Asia/Kolkata")


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Read-only historical AS-OF replay: what the baseline strategy would have "
                    "detected on a past date, using only information available by that date's close."
    )
    p.add_argument("--as-of", required=True, help="Historical date to replay, YYYY-MM-DD")
    p.add_argument("--universe", default=None, help="Override universe_scope (e.g. NIFTY_200)")
    p.add_argument("--config", default="config/default.yaml")
    p.add_argument("--allow-missing-holidays", action="store_true")
    return p


def main(argv: list[str] | None = None) -> int:
    configure_logging()
    args = build_arg_parser().parse_args(argv)

    overrides = {"universe": {"universe_scope": args.universe}} if args.universe else {}
    cfg = load_config(args.config, overrides)

    as_of_date = date.fromisoformat(args.as_of)
    # Treat the requested date as already-closed for session-resolution purposes — same
    # convention run_daily.py uses for --date, so replaying "today" and forcing "today" as a live
    # run resolve identically.
    as_of_dt = datetime.combine(as_of_date, datetime.min.time(), tzinfo=IST).replace(hour=16)

    try:
        holidays = resolve_holidays_for_run("config/nse_holidays.yaml", as_of_date,
                                             strict=not args.allow_missing_holidays)
    except CalendarError as e:
        print(f"BLOCKED: {e}", file=sys.stderr)
        print("Re-run with --allow-missing-holidays to proceed with a weekday-only calendar.",
              file=sys.stderr)
        return 5

    session = resolve_session(as_of_dt, holidays)
    # For replay, the SIGNAL is dated at the resolved session itself (the actual historical
    # trading day on/before --as-of), not at "the next session after it" — there is no "next
    # session" relative to a historical replay in the way there is for a live run.

    print("=" * 70)
    print("HISTORICAL AS-OF REPLAY (read-only — the database is never modified by this command)")
    print("=" * 70)
    print(f"AS_OF_DATE (requested):      {as_of_date.isoformat()}")
    print(f"EXPECTED_SESSION (resolved): {session.expected_completed_session.isoformat()}")
    print(f"SIGNAL_DATE:                 {session.expected_completed_session.isoformat()}")
    print(f"UNIVERSE:                    {cfg.universe.universe_scope}")

    survivorship = label_current_universe_mode()
    print(f"UNIVERSE_MODE:                {survivorship.mode}")
    print("SURVIVORSHIP_BIAS_PRESENT:    True")
    print("-" * 70)

    try:
        universe_provider = get_universe_provider(cfg)
    except NseScannerError as e:
        print(f"UNIVERSE CONFIGURATION ERROR: {e}", file=sys.stderr)
        return 1

    store = MarketDataStore(cfg.paths.processed_dir, cfg.paths.duckdb_path)
    data_provider = AsOfDataProvider(as_of_date=session.expected_completed_session, store=store)
    benchmark_provider = YahooBenchmarkProvider(cfg.data)  # best-effort; replay works without it

    try:
        result = run_scan(cfg, universe_provider, data_provider, benchmark_provider, session, holidays,
                           symbol_override_path="config/symbol_overrides.yaml")
    except UniverseIntegrityError as e:
        print("UNIVERSE INTEGRITY FAILURE — ABORTING (no partial-universe fallback).", file=sys.stderr)
        print(str(e), file=sys.stderr)
        return 3
    except NseScannerError as e:
        print(f"REPLAY FAILED: {type(e).__name__}: {e}", file=sys.stderr)
        return 1

    price_basis_warning = result.price_basis == PRICE_BASIS_BLENDED

    print(f"DATA COVERAGE:                {result.constituent_count - result.data_validation_failures}/"
          f"{result.constituent_count} securities had sufficient history as of this date")
    price_basis_warning_str = (
        "  *** WARNING: mixed RAW/ADJUSTED history for at least one symbol — see Diagnostics ***"
        if price_basis_warning else ""
    )
    print(f"PRICE_BASIS:                  {result.price_basis}{price_basis_warning_str}")
    print(f"BASELINE_SIGNAL_COUNT:        {result.signals_current}")
    print(f"RUN_HEALTH:                   {result.run_health}")

    if result.constituent_count == 0 or result.data_validation_failures == result.constituent_count:
        print("BASELINE_SIGNAL_STATUS:       NOT_EVALUABLE (no security had usable history as of "
              "this date — this is different from '0 signals found')")
    else:
        print(f"BASELINE_SIGNAL_STATUS:       EVALUATED ({result.signals_current} signals found)")

    reports_dir = Path(cfg.paths.reports_dir)
    report_path = reports_dir / f"NSE_Technical_Replay_{session.expected_completed_session.isoformat()}.xlsx"
    write_report(result.sheets, report_path)

    manifest = build_run_manifest(
        run_id=result.run_id, cfg=cfg, universe_id=cfg.universe.universe_scope,
        universe_snapshot_date=as_of_date.isoformat(), universe_source=result.universe_source,
        constituent_count=result.constituent_count, data_provider=data_provider.name,
        data_as_of=session.expected_completed_session.isoformat(),
        expected_session=session.expected_completed_session.isoformat(),
        signal_date=session.expected_completed_session.isoformat(), status=result.run_health,
        price_basis=result.price_basis,
        extra={
            "mode": "HISTORICAL_AS_OF_REPLAY",
            "requested_as_of_date": as_of_date.isoformat(),
            "universe_mode": survivorship.mode,
            "survivorship_bias_present": True,
            "survivorship_disclosure": survivorship.disclosure,
            "price_basis_warning": price_basis_warning,
            "no_future_data_used": True,  # enforced by AsOfDataProvider's query-level cutoff, not
                                            # merely asserted — see data/asof_provider.py
        },
    )
    manifest_path = reports_dir / f"run_manifest_replay_{session.expected_completed_session.isoformat()}.json"
    manifest.write(manifest_path)

    print(f"REPORT:                       {report_path}")
    print(f"MANIFEST:                     {manifest_path}")
    print("=" * 70)
    print(survivorship.disclosure)
    print("=" * 70)

    return 0


if __name__ == "__main__":
    sys.exit(main())
