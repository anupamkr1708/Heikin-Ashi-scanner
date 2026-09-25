from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from nse_scanner.cli import run_research
from nse_scanner.data.storage import MarketDataStore
from nse_scanner.testing.synthetic_market_data import SYNTHETIC_SYMBOLS, generate_synthetic_ohlcv


def _populate_store(tmp_path: Path, n_days: int = 300) -> None:
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


def test_start_end_narrows_independent_events_without_fabricating(tmp_path, capsys):
    _populate_store(tmp_path)
    cfg_path = _write_config(tmp_path)

    rc_unfiltered = run_research.main(["--config", str(cfg_path)])
    out_unfiltered = capsys.readouterr().out
    unfiltered_n = int([line for line in out_unfiltered.splitlines() if "INDEPENDENT_EVENTS" in line][0].split()[1])

    rc_filtered = run_research.main(["--config", str(cfg_path), "--start", "2026-06-01", "--end", "2026-08-01"])
    out_filtered = capsys.readouterr().out

    assert rc_unfiltered in (0, 1)
    assert rc_filtered in (0, 1)
    assert "filtered to --start/--end range" in out_filtered
    filtered_line = [line for line in out_filtered.splitlines() if "INDEPENDENT_EVENTS" in line][0]
    filtered_n = int(filtered_line.split()[1])
    assert filtered_n <= unfiltered_n  # narrowing, never inflating, the event count


def test_out_of_range_request_is_reported_not_silently_granted(tmp_path, capsys):
    _populate_store(tmp_path, n_days=100)
    cfg_path = _write_config(tmp_path)

    run_research.main(["--config", str(cfg_path), "--start", "2000-01-01"])
    out = capsys.readouterr().out
    assert "narrowed to what actually exists, not fabricated" in out
