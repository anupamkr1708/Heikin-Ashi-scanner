"""python scripts/run_research.py --start YYYY-MM-DD --end YYYY-MM-DD [--universe NIFTY_200]

Event-study research over locally-stored EOD history (PART 28 MODE 2). Requires that
ingest_eod.py / run_daily.py has already built up multi-day history in the local store — this
script does not download data itself.

`--start`/`--end` restrict which SIGNAL dates are included in the study (PHASE 38/39: research
history is independent of the daily scanner's short live lookback) — they do not fabricate data
outside what's actually in the store. If the requested range extends beyond what a symbol
actually has, that symbol's usable range is silently narrowed to its real coverage, and the
printed DATA COVERAGE line reports the ACTUAL earliest/latest signal-eligible date used, not the
requested one, so a request for more history than exists is visible rather than silently granted.

Survivorship: uses today's universe applied backward (MODE A —
CURRENT_UNIVERSE_HISTORICAL_SIMULATION) and explicitly discloses that in the output, because this
repository does not have a verified point-in-time NIFTY 200 membership feed (PART 29 /
research/survivorship.py).
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

import pandas as pd

from nse_scanner.config import load_config
from nse_scanner.data.storage import MarketDataStore
from nse_scanner.logging_config import configure_logging
from nse_scanner.pipeline.features import build_feature_frame
from nse_scanner.research.events import extract_events
from nse_scanner.research.forward_returns import forward_returns_multi_horizon, resolve_entry
from nse_scanner.research.mfe_mae import calculate_mfe_mae, summarize_excursions
from nse_scanner.research.statistics import cluster_summary, describe_returns, sector_summary
from nse_scanner.research.survivorship import label_current_universe_mode


def main(argv: list[str] | None = None) -> int:
    configure_logging()
    p = argparse.ArgumentParser()
    p.add_argument("--config", default="config/default.yaml")
    p.add_argument("--horizon", type=int, default=None, help="Primary horizon in trading days (default from config)")
    p.add_argument("--start", default=None, help="Only include signals on/after this date (YYYY-MM-DD)")
    p.add_argument("--end", default=None, help="Only include signals on/before this date (YYYY-MM-DD)")
    args = p.parse_args(argv)

    cfg = load_config(args.config)
    horizon = args.horizon or cfg.research.primary_research_horizon
    range_start = date.fromisoformat(args.start) if args.start else None
    range_end = date.fromisoformat(args.end) if args.end else None
    store = MarketDataStore(cfg.paths.processed_dir, cfg.paths.duckdb_path)

    universe_df = store.read_parquet("security_master_latest")
    if universe_df is None or universe_df.empty:
        print("No local universe snapshot found — run scripts/update_universe.py first.", file=sys.stderr)
        return 1

    label = label_current_universe_mode()
    print(f"SURVIVORSHIP MODE: {label.mode}")
    print(label.disclosure)
    print("-" * 70)

    feature_frames: dict[str, pd.DataFrame] = {}
    sector_by_symbol: dict[str, object] = {}
    coverage_start: date | None = None
    coverage_end: date | None = None
    for row in universe_df.itertuples(index=False):
        symbol = row.Symbol
        hist = store.read_symbol_history(symbol)
        if hist is None or len(hist) < cfg.history.min_rows_primary:
            continue
        feature_frames[symbol] = build_feature_frame(hist, cfg)
        sector_by_symbol[symbol] = getattr(row, "Sector", None)
        sym_start, sym_end = hist.index[0].date(), hist.index[-1].date()
        coverage_start = sym_start if coverage_start is None else min(coverage_start, sym_start)
        coverage_end = sym_end if coverage_end is None else max(coverage_end, sym_end)

    if not feature_frames:
        print("No local history available for any universe symbol — run bootstrap_history.py / "
              "ingest_eod.py / run_daily.py for several sessions first.", file=sys.stderr)
        return 1

    print(f"DATA COVERAGE (actual, across universe): {coverage_start} .. {coverage_end}")
    if range_start and coverage_start and range_start < coverage_start:
        print(f"  NOTE: requested --start {range_start} is before the earliest available data "
              f"({coverage_start}) — narrowed to what actually exists, not fabricated.")
    if range_end and coverage_end and range_end > coverage_end:
        print(f"  NOTE: requested --end {range_end} is after the latest available data "
              f"({coverage_end}) — narrowed to what actually exists, not fabricated.")

    events = extract_events(feature_frames, cfg.baseline.max_bb_overshoot_pct, cfg.baseline.min_ha_body_pct,
                             cfg.research.independent_event_cooldown_days)

    all_signal_days = events.all_signal_days
    independent_events = events.independent_events
    if range_start or range_end:
        sig_dates = pd.to_datetime(independent_events["Signal_Date"]).dt.date
        mask = pd.Series(True, index=independent_events.index)
        if range_start:
            mask &= sig_dates >= range_start
        if range_end:
            mask &= sig_dates <= range_end
        independent_events = independent_events[mask]

    print(f"ALL_SIGNAL_DAYS:    {len(all_signal_days)}")
    print(f"INDEPENDENT_EVENTS: {len(independent_events)}"
          + (" (filtered to --start/--end range)" if (range_start or range_end) else ""))

    rows = []
    for ev in independent_events.itertuples(index=False):
        symbol = ev.Symbol
        price_df = feature_frames[symbol][["Open", "High", "Low", "Close"]]
        signal_pos = price_df.index.get_loc(ev.Signal_Date)
        if isinstance(signal_pos, slice):
            continue
        entry = resolve_entry(price_df, signal_pos, cfg.research.entry_price_method)
        if entry.entry_pos is None or entry.entry_price is None:
            continue
        fwd = forward_returns_multi_horizon(price_df, entry.entry_pos, cfg.research.forward_return_horizons)
        exc = calculate_mfe_mae(price_df, entry.entry_pos, entry.entry_price, horizon, cfg.research.entry_price_method)
        rows.append({
            "Symbol": symbol, "Sector": sector_by_symbol.get(symbol), "Signal_Date": ev.Signal_Date,
            "Entry_Date": entry.entry_date, "Entry_Price": entry.entry_price,
            "Gap_Pct": entry.gap_from_signal_to_entry_pct,
            "Breakout_Type": ev.Breakout_Type, **fwd, "MFE_Pct": exc.mfe_pct, "MAE_Pct": exc.mae_pct,
        })

    if not rows:
        print("No independent events had a resolvable entry (need at least one bar after the signal).")
        return 0

    results_df = pd.DataFrame(rows)
    primary_col = f"FwdRet_{horizon}D"
    stats = describe_returns(results_df[primary_col].to_numpy(), results_df["Symbol"].to_numpy(),
                              pd.to_datetime(results_df["Signal_Date"]).to_numpy())
    excursion_summary = summarize_excursions(results_df["MFE_Pct"].tolist(), results_df["MAE_Pct"].tolist())
    cluster = cluster_summary(results_df["Symbol"].to_numpy(), pd.to_datetime(results_df["Signal_Date"]).to_numpy())
    sectors_table = sector_summary(results_df["Sector"].to_numpy(), results_df[primary_col].to_numpy())

    print("-" * 70)
    print(f"PRIMARY HORIZON: {horizon}D forward return")
    print(f"N={stats.n}  unique_stocks={stats.unique_stocks}  unique_days={stats.unique_days}")
    print(f"mean={stats.mean:.2f}%  median={stats.median:.2f}%  win_rate={stats.win_rate:.2%}" if stats.n else "N=0")
    print(excursion_summary)
    print(cluster)
    print("Sector breakdown:")
    print(sectors_table.to_string(index=False))

    Path(cfg.paths.reports_dir).mkdir(parents=True, exist_ok=True)
    out_path = Path(cfg.paths.reports_dir) / "research_events.csv"
    results_df.to_csv(out_path, index=False)
    print(f"Event-level results written to {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
