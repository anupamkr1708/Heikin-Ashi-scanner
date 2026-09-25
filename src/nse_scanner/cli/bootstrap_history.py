"""python scripts/bootstrap_history.py --universe NIFTY_200 --period 2y

**This is what was missing.** A real run against live NSE data showed daily EOD ingestion working
correctly (one session's bhavcopy, ~2,885 securities) but every stock then failing the baseline
scan with "insufficient history for PRIMARY calculation: 1 valid rows (need >= 30)" — because
daily ingestion appends exactly ONE session at a time by design (PART 55: "Do not redownload
years of history every evening"), and nothing had ever populated the OTHER 29+ sessions the
strategy needs. This script is that one-time (or occasional) backfill step.

    ONE-TIME / OCCASIONAL:  python scripts/bootstrap_history.py --universe NIFTY_200 --period 2y
    DAILY, forever after:   python scripts/run_daily.py

Provider: yfinance ONLY (PART "Provider: yfinance" for this workflow). This does NOT replace NSE
as the primary DAILY EOD source (data/nse_eod.py) — it exists purely to give the daily-appending
NSE pipeline enough history to compute a 20-day Bollinger Band on day one, and is never invoked
by run_daily.py itself.

Workflow: universe (via universe/factory.py) -> NSE-symbol -> Yahoo-symbol mapping
(universe/symbol_mapping.py + config/symbol_overrides.yaml) -> batch Yahoo historical download
(data/yfinance_provider.py, which already implements batching/pause/retry) -> normalize ->
validate -> store (data/storage.py, tagged source=YFINANCE, price_basis=ADJUSTED) -> coverage
report.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from nse_scanner.config import load_config
from nse_scanner.data.storage import MarketDataStore
from nse_scanner.data.validation import DataValidationError, validate_and_clean_ohlc
from nse_scanner.data.yfinance_provider import YahooFinanceProvider
from nse_scanner.exceptions import NseScannerError
from nse_scanner.logging_config import configure_logging, get_logger
from nse_scanner.models.market_data import PriceBasis
from nse_scanner.universe.factory import get_universe_provider
from nse_scanner.universe.symbol_mapping import load_override_table, map_symbol_to_yfinance

logger = get_logger(__name__)

VALID_PERIODS = ("1y", "2y", "5y", "10y", "max")


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="One-time/occasional historical bootstrap via yfinance")
    p.add_argument("--universe", default=None, help="Override universe_scope (e.g. NIFTY_200, NSE_MAINBOARD_EQ)")
    p.add_argument(
        "--provider",
        default="yfinance",
        choices=["yfinance"],
        help="Historical bootstrap provider (only yfinance is supported — PART: "
        "'yfinance: SECONDARY historical bootstrap')",
    )
    p.add_argument(
        "--period",
        default="2y",
        choices=VALID_PERIODS,
        help="How much history to backfill (ignored if --start/--end are given)",
    )
    p.add_argument("--start", default=None, help="Explicit start date YYYY-MM-DD (overrides --period)")
    p.add_argument("--end", default=None, help="Explicit end date YYYY-MM-DD (defaults to today if --start is given)")
    p.add_argument("--config", default="config/default.yaml")
    p.add_argument("--symbol-overrides", default="config/symbol_overrides.yaml")
    return p


def main(argv: list[str] | None = None) -> int:
    configure_logging()
    args = build_arg_parser().parse_args(argv)

    overrides = {"universe": {"universe_scope": args.universe}} if args.universe else {}
    cfg = load_config(args.config, overrides)

    use_date_range = args.start is not None
    history_desc = f"{args.start} .. {args.end or 'today'}" if use_date_range else args.period

    print("=" * 70)
    print("HISTORICAL BOOTSTRAP (one-time/occasional — NOT the daily data path)")
    print("=" * 70)
    print(f"UNIVERSE:            {cfg.universe.universe_scope}")
    print(f"PROVIDER:             {args.provider}")
    print(f"REQUESTED HISTORY:    {history_desc}")
    print("-" * 70)

    try:
        universe_provider = get_universe_provider(cfg)
        constituents, source, _retrieved_at = universe_provider.get_constituents()
    except NseScannerError as e:
        print(f"UNIVERSE FAILURE — ABORTING (no partial-universe fallback): {e}", file=sys.stderr)
        return 1

    print(f"Universe retrieved: {len(constituents)} constituents from {source}")

    override_table = load_override_table(args.symbol_overrides)
    nse_to_yf: dict[str, str] = {}
    for sym in constituents["Symbol"]:
        mapping = map_symbol_to_yfinance(sym, override_table)
        nse_to_yf[sym] = mapping.yf_symbol
    yf_to_nse = {v: k for k, v in nse_to_yf.items()}

    yf_provider = YahooFinanceProvider(cfg.data)
    print(
        f"Downloading {len(nse_to_yf)} symbols from Yahoo Finance in batches of {cfg.data.batch_size} "
        f"(this can take a while for large universes)..."
    )
    results, download_errors = yf_provider.fetch_history_batch(
        list(nse_to_yf.values()),
        period=args.period,
        interval=cfg.data.interval,
        start=args.start,
        end=args.end,
    )

    store = MarketDataStore(cfg.paths.processed_dir, cfg.paths.duckdb_path)

    populated_rows: list[int] = []
    insufficient_symbols: list[str] = []
    populated_symbols: list[str] = []
    validation_failed: dict[str, str] = {}
    now = datetime.now(timezone.utc)
    all_normalized: list[pd.DataFrame] = []

    for yf_symbol, raw_df in results.items():
        nse_symbol = yf_to_nse[yf_symbol]
        try:
            # min_rows=1 here: bootstrap should store whatever valid history exists rather than
            # rejecting a newly-listed security outright — the tiered PRIMARY threshold is
            # enforced later, at SCAN time (config.HistoryTierConfig), not at bootstrap time.
            clean_df, _report = validate_and_clean_ohlc(raw_df, min_rows=1)
        except DataValidationError as e:
            validation_failed[nse_symbol] = str(e)
            continue

        n_rows = len(clean_df)
        populated_rows.append(n_rows)
        populated_symbols.append(nse_symbol)
        if n_rows < cfg.history.min_rows_primary:
            insufficient_symbols.append(nse_symbol)

        normalized = pd.DataFrame(
            {
                "nse_symbol": nse_symbol,
                "isin": None,
                "trade_date": clean_df.index,
                "open": clean_df["Open"].to_numpy(),
                "high": clean_df["High"].to_numpy(),
                "low": clean_df["Low"].to_numpy(),
                "close": clean_df["Close"].to_numpy(),
                "volume": clean_df["Volume"].to_numpy(),
                "turnover": None,
                "source": "YFINANCE",
                "price_basis": PriceBasis.ADJUSTED if cfg.data.yfinance_auto_adjust else PriceBasis.RAW,
                "schema_version": "yfinance_bootstrap",
                "ingested_at": now,
            }
        )
        all_normalized.append(normalized)

    if all_normalized:
        combined = pd.concat(all_normalized, ignore_index=True)
        total_rows = store.append_eod_prices(combined)
        print(
            f"Stored bootstrap history for {len(populated_symbols)} symbols "
            f"({len(combined)} new rows; store now has {total_rows} total rows across all symbols/sessions)."
        )
    else:
        print("No symbols were successfully downloaded — nothing stored.", file=sys.stderr)

    failed_download_symbols = [yf_to_nse[s] for s in download_errors if s in yf_to_nse]

    print("-" * 70)
    print("COVERAGE REPORT")
    print("-" * 70)
    print(f"Universe:                  {len(constituents)}")
    print(f"Requested history:         {history_desc}")
    print(f"Successfully populated:    {len(populated_symbols)}")
    print(
        f"  of which insufficient" f" for PRIMARY (< {cfg.history.min_rows_primary} rows): {len(insufficient_symbols)}"
    )
    print(f"Failed downloads:          {len(failed_download_symbols)}")
    print(f"Failed validation:         {len(validation_failed)}")
    if populated_rows:
        print(f"Median rows per symbol:    {int(np.median(populated_rows))}")
        print(f"Min rows:                  {min(populated_rows)}")
        print(f"Max rows:                  {max(populated_rows)}")
    print(f"Database location:         {store.eod_prices_dir}")

    if failed_download_symbols:
        preview = ", ".join(failed_download_symbols[:15])
        more = f" (+{len(failed_download_symbols) - 15} more)" if len(failed_download_symbols) > 15 else ""
        print(f"\nFailed download symbols: {preview}{more}")
        print(
            "(Check config/symbol_overrides.yaml if these are known-renamed tickers — a wrong "
            "NSE->Yahoo mapping shows up here as a download failure, since this script does not "
            "pre-validate a mapping's existence before attempting the download.)"
        )

    print("=" * 70)
    print("Next step: python scripts/run_daily.py")
    print("=" * 70)

    return 0 if populated_symbols else 1


if __name__ == "__main__":
    sys.exit(main())
