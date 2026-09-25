"""python scripts/run_walk_forward.py [--train-years 3] [--test-months 6]

Genuine rolling walk-forward (fixes legacy BUG 11 — a single static split is NOT walk-forward).
Uses `research/walk_forward.py::generate_walk_forward_windows` to build a SEQUENCE of
train/test windows and evaluates each independently; window-level results are stored, never
averaged into one number that would hide instability across windows.

**Because the baseline strategy (`bb_ha_v1_base`) is immutable, there is no parameter to select
or optimize on the TRAIN period in this release** — TRAIN here is used only to report descriptive
context (how many independent events occurred, what the baseline's characteristics looked like)
before evaluating the SAME fixed strategy out-of-sample on TEST. This trivially satisfies "no
test-period information may influence parameter selection" (there is no selection step at all),
and the framework is structured so that a future strategy VARIANT with an actual tunable
parameter can slot into the TRAIN step without changing this script's window-generation or
reporting logic.
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
from nse_scanner.research.statistics import describe_returns
from nse_scanner.research.survivorship import label_current_universe_mode
from nse_scanner.research.walk_forward import generate_walk_forward_windows


def _events_and_forward_returns_in_window(
    feature_frames: dict[str, pd.DataFrame], cfg, window_start: date, window_end: date
) -> pd.DataFrame:
    """Restricts each symbol's already-computed feature frame to [window_start, window_end]
    BEFORE event extraction, so a window's results can only reflect signals whose date actually
    falls inside that window — the train/test date fencing this whole script exists to enforce."""
    windowed = {}
    for symbol, feat in feature_frames.items():
        sliced = feat[(feat.index.date >= window_start) & (feat.index.date <= window_end)]
        if not sliced.empty:
            windowed[symbol] = feat.loc[: sliced.index[-1]]  # keep full history UP TO window_end
            # for correct rolling-indicator values,
            # but events are only extracted where
            # the signal date itself falls in-window

    events = extract_events(
        windowed,
        cfg.baseline.max_bb_overshoot_pct,
        cfg.baseline.min_ha_body_pct,
        cfg.research.independent_event_cooldown_days,
    )
    if events.independent_events.empty:
        return pd.DataFrame()

    in_window = events.independent_events[
        (pd.to_datetime(events.independent_events["Signal_Date"]).dt.date >= window_start)
        & (pd.to_datetime(events.independent_events["Signal_Date"]).dt.date <= window_end)
    ]

    rows = []
    for ev in in_window.itertuples(index=False):
        symbol = ev.Symbol
        price_df = feature_frames[symbol][["Open", "High", "Low", "Close"]]
        signal_pos = price_df.index.get_loc(ev.Signal_Date)
        if isinstance(signal_pos, slice):
            continue
        entry = resolve_entry(price_df, signal_pos, cfg.research.entry_price_method)
        if entry.entry_pos is None or entry.entry_price is None:
            continue
        fwd = forward_returns_multi_horizon(price_df, entry.entry_pos, cfg.research.forward_return_horizons)
        rows.append({"Symbol": symbol, "Signal_Date": ev.Signal_Date, **fwd})

    return pd.DataFrame(rows)


def main(argv: list[str] | None = None) -> int:
    configure_logging()
    p = argparse.ArgumentParser(description="Genuine rolling walk-forward evaluation of the baseline strategy")
    p.add_argument("--config", default="config/default.yaml")
    p.add_argument("--train-years", type=float, default=None)
    p.add_argument("--test-months", type=float, default=None)
    p.add_argument("--horizon", type=int, default=None)
    args = p.parse_args(argv)

    cfg = load_config(args.config)
    train_years = args.train_years or cfg.research.walk_forward_train_years
    test_months = args.test_months or cfg.research.walk_forward_test_months
    horizon = args.horizon or cfg.research.primary_research_horizon
    primary_col = f"FwdRet_{horizon}D"

    store = MarketDataStore(cfg.paths.processed_dir, cfg.paths.duckdb_path)
    universe_df = store.read_parquet("security_master_latest")
    if universe_df is None or universe_df.empty:
        print("No local universe snapshot found — run scripts/update_universe.py first.", file=sys.stderr)
        return 1

    label = label_current_universe_mode()
    print(f"SURVIVORSHIP MODE: {label.mode}")
    print("-" * 70)

    feature_frames: dict[str, pd.DataFrame] = {}
    data_start: date | None = None
    data_end: date | None = None
    for symbol in universe_df["Symbol"]:
        hist = store.read_symbol_history(symbol)
        if hist is None or len(hist) < cfg.history.min_rows_primary:
            continue
        feature_frames[symbol] = build_feature_frame(hist, cfg)
        sym_start, sym_end = hist.index[0].date(), hist.index[-1].date()
        data_start = sym_start if data_start is None else min(data_start, sym_start)
        data_end = sym_end if data_end is None else max(data_end, sym_end)

    if not feature_frames or data_start is None or data_end is None:
        print("No local history available — run bootstrap_history.py / run_daily.py first.", file=sys.stderr)
        return 1

    windows = generate_walk_forward_windows(data_start, data_end, train_years, test_months)
    if not windows:
        print(
            f"No complete walk-forward window fits in the available data range "
            f"({data_start} .. {data_end}) with train_years={train_years}, test_months={test_months}. "
            f"Need more history (bootstrap_history.py --period 5y or more).",
            file=sys.stderr,
        )
        return 1

    print(f"DATA RANGE:        {data_start} .. {data_end}")
    print(f"WINDOW CONFIG:     {train_years}y train / {test_months}mo test")
    print(f"WINDOWS GENERATED: {len(windows)}")
    print("-" * 70)

    window_rows = []
    for w in windows:
        train_df = _events_and_forward_returns_in_window(feature_frames, cfg, w.train_start, w.train_end)
        test_df = _events_and_forward_returns_in_window(feature_frames, cfg, w.test_start, w.test_end)

        def _stats_or_none(df: pd.DataFrame):
            if df.empty or primary_col not in df:
                return None
            return describe_returns(df[primary_col].to_numpy())

        train_stats = _stats_or_none(train_df)
        test_stats = _stats_or_none(test_df)

        row = {
            "window_id": w.window_id,
            "train_start": w.train_start,
            "train_end": w.train_end,
            "test_start": w.test_start,
            "test_end": w.test_end,
            "strategy_id": cfg.baseline.strategy_id,
            "train_n": train_stats.n if train_stats else 0,
            "train_mean_return_pct": train_stats.mean if train_stats else None,
            "train_win_rate": train_stats.win_rate if train_stats else None,
            "test_n": test_stats.n if test_stats else 0,
            "test_mean_return_pct": test_stats.mean if test_stats else None,
            "test_win_rate": test_stats.win_rate if test_stats else None,
        }
        window_rows.append(row)
        print(
            f"Window {w.window_id}: train[{w.train_start}..{w.train_end}] N={row['train_n']} "
            f"-> test[{w.test_start}..{w.test_end}] N={row['test_n']}"
            + (f" mean={row['test_mean_return_pct']:.2f}%" if row["test_mean_return_pct"] is not None else "")
        )

    windows_df = pd.DataFrame(window_rows)
    Path(cfg.paths.reports_dir).mkdir(parents=True, exist_ok=True)
    out_path = Path(cfg.paths.reports_dir) / "walk_forward_windows.csv"
    windows_df.to_csv(out_path, index=False)

    print("-" * 70)
    n_windows_with_test_events = int((windows_df["test_n"] > 0).sum())
    print(f"Windows with >=1 OOS event: {n_windows_with_test_events}/{len(windows_df)}")
    print(
        "Per-window results (not a single pooled number — look for consistency/instability "
        "across windows, per PART 27/54's explicit instruction not to search for one magic result)."
    )
    print(f"Results written to {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
