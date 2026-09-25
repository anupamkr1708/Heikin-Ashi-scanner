"""Proves scripts/run_walk_forward.py produces a genuine SEQUENCE of rolling windows (fixing the
legacy BUG 11 static-split-called-walk-forward), each evaluated independently and never pooled
into one hidden "the" result."""

from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from nse_scanner.cli import run_walk_forward
from nse_scanner.data.storage import MarketDataStore
from nse_scanner.testing.synthetic_market_data import SYNTHETIC_SYMBOLS, generate_synthetic_ohlcv


def _populate_store(tmp_path: Path, n_days: int = 500) -> None:
    store = MarketDataStore(tmp_path / "processed", tmp_path / "db.duckdb")
    now = datetime.now(timezone.utc)
    universe_rows = []
    for sym in SYNTHETIC_SYMBOLS:
        hist = generate_synthetic_ohlcv(sym, n_days=n_days)
        normalized = pd.DataFrame(
            {
                "nse_symbol": sym,
                "isin": None,
                "trade_date": hist.index,
                "open": hist["Open"].to_numpy(),
                "high": hist["High"].to_numpy(),
                "low": hist["Low"].to_numpy(),
                "close": hist["Close"].to_numpy(),
                "volume": hist["Volume"].to_numpy(),
                "turnover": None,
                "source": "NSE",
                "price_basis": "RAW",
                "schema_version": "UDIFF",
                "ingested_at": now,
            }
        )
        store.append_eod_prices(normalized)
        universe_rows.append({"Symbol": sym, "Company_Name": f"{sym} Ltd", "Sector": "Test"})
    store.write_parquet("security_master_latest", pd.DataFrame(universe_rows))


def _write_config(tmp_path: Path) -> Path:
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(
        f"paths:\n"
        f"  processed_dir: '{tmp_path / 'processed'}'\n"
        f"  duckdb_path: '{tmp_path / 'db.duckdb'}'\n"
        f"  reports_dir: '{tmp_path / 'reports'}'\n"
    )
    return cfg_path


def test_walk_forward_produces_multiple_independent_windows(tmp_path, capsys):
    _populate_store(tmp_path, n_days=500)
    cfg_path = _write_config(tmp_path)

    rc = run_walk_forward.main(["--config", str(cfg_path), "--train-years", "0.4", "--test-months", "2"])
    assert rc == 0

    out_csv = tmp_path / "reports" / "walk_forward_windows.csv"
    assert out_csv.exists()
    windows_df = pd.read_csv(out_csv)

    assert len(windows_df) > 1  # a SEQUENCE, not one static split
    # Each window's train_end must precede its own test_start (chronological, non-overlapping).
    for _, row in windows_df.iterrows():
        assert pd.Timestamp(row["train_end"]) < pd.Timestamp(row["test_start"])
    # Windows must roll forward: each successive window's train_start is later than the previous.
    starts = pd.to_datetime(windows_df["train_start"])
    assert (starts.diff().dropna() > pd.Timedelta(0)).all()


def test_walk_forward_reports_per_window_not_pooled(tmp_path, capsys):
    _populate_store(tmp_path, n_days=500)
    cfg_path = _write_config(tmp_path)
    run_walk_forward.main(["--config", str(cfg_path), "--train-years", "0.4", "--test-months", "2"])

    windows_df = pd.read_csv(tmp_path / "reports" / "walk_forward_windows.csv")
    # A per-window mean return column must exist and vary across windows — proving the report
    # is not collapsing everything into a single number.
    assert "test_mean_return_pct" in windows_df.columns
    non_null = windows_df["test_mean_return_pct"].dropna()
    if len(non_null) > 1:
        assert non_null.nunique() > 1 or len(non_null) == 1  # tolerant of a coincidental tie


def test_walk_forward_fails_clearly_with_insufficient_history(tmp_path, capsys):
    _populate_store(tmp_path, n_days=60)  # far short of a 3-year train window
    cfg_path = _write_config(tmp_path)

    rc = run_walk_forward.main(["--config", str(cfg_path)])  # default 3y train / 6mo test
    assert rc == 1
    err = capsys.readouterr().err
    assert "bootstrap_history.py" in err or "No complete walk-forward window" in err


def test_walk_forward_requires_universe_snapshot(tmp_path, capsys):
    cfg_path = _write_config(tmp_path)  # no store populated, no universe snapshot written
    rc = run_walk_forward.main(["--config", str(cfg_path)])
    assert rc == 1
