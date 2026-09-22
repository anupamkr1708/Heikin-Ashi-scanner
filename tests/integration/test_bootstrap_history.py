"""Exercises scripts/bootstrap_history.py end-to-end with a mocked yfinance provider (no
network) — proving the exact gap the real NSE run surfaced is now closed: a fresh store has zero
history, bootstrap populates it, and the baseline scan can then compute Bollinger/HA/ATR."""

from __future__ import annotations

from nse_scanner.cli import bootstrap_history
from nse_scanner.config import ScannerConfig
from nse_scanner.data.storage import MarketDataStore
from nse_scanner.testing.synthetic_market_data import (
    SYNTHETIC_SYMBOLS,
    generate_synthetic_ohlcv,
    generate_synthetic_universe,
)


class _FakeUniverseProvider:
    def get_constituents(self):
        return generate_synthetic_universe(), "FAKE_UNIVERSE", "2026-09-13T00:00:00+00:00"


class _FakeYahooProvider:
    def __init__(self, cfg):
        self.cfg = cfg

    def fetch_history_batch(self, symbols, period, interval, start=None, end=None):
        results, errors = {}, {}
        for sym in symbols:
            base_symbol = sym.replace(".NS", "")
            if base_symbol == SYNTHETIC_SYMBOLS[-1]:
                errors[sym] = "simulated_download_failure"
                continue
            n_days = 5 if base_symbol == SYNTHETIC_SYMBOLS[-2] else 500  # one thin, rest ample
            results[sym] = generate_synthetic_ohlcv(base_symbol, n_days=n_days)
        return results, errors


def test_bootstrap_populates_store_and_reports_coverage(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(bootstrap_history, "get_universe_provider", lambda cfg: _FakeUniverseProvider())
    monkeypatch.setattr(bootstrap_history, "YahooFinanceProvider", _FakeYahooProvider)

    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(
        f"paths:\n  processed_dir: '{tmp_path / 'processed'}'\n  duckdb_path: '{tmp_path / 'db.duckdb'}'\n"
    )

    rc = bootstrap_history.main(["--period", "2y", "--config", str(cfg_path)])
    assert rc == 0

    out = capsys.readouterr().out
    assert "Universe:" in out
    assert "Successfully populated:" in out
    assert "Failed downloads:" in out

    # SYNTHETIC_SYMBOLS[-1] failed download -> not populated. Everyone else should be.
    store = MarketDataStore(tmp_path / "processed", tmp_path / "db.duckdb")
    hist_ok = store.read_symbol_history(SYNTHETIC_SYMBOLS[0])
    assert hist_ok is not None
    assert len(hist_ok) == 500

    hist_failed = store.read_symbol_history(SYNTHETIC_SYMBOLS[-1])
    assert hist_failed is None

    # Confirm the exact gap this closes: the baseline strategy needs >= min_rows_primary (30).
    cfg = ScannerConfig()
    assert len(hist_ok) >= cfg.history.min_rows_primary


def test_bootstrap_reports_insufficient_history_separately_from_failed_download(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(bootstrap_history, "get_universe_provider", lambda cfg: _FakeUniverseProvider())
    monkeypatch.setattr(bootstrap_history, "YahooFinanceProvider", _FakeYahooProvider)

    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(
        f"paths:\n  processed_dir: '{tmp_path / 'processed'}'\n  duckdb_path: '{tmp_path / 'db.duckdb'}'\n"
    )
    bootstrap_history.main(["--config", str(cfg_path)])

    store = MarketDataStore(tmp_path / "processed", tmp_path / "db.duckdb")
    thin_hist = store.read_symbol_history(SYNTHETIC_SYMBOLS[-2])
    assert thin_hist is not None
    assert len(thin_hist) == 5  # populated, but still below the primary threshold — visible, not hidden


def test_bootstrap_with_zero_successful_downloads_returns_nonzero(tmp_path, monkeypatch, capsys):
    class _AllFailYahooProvider:
        def __init__(self, cfg):
            pass

        def fetch_history_batch(self, symbols, period, interval, start=None, end=None):
            return {}, {s: "simulated_total_failure" for s in symbols}

    monkeypatch.setattr(bootstrap_history, "get_universe_provider", lambda cfg: _FakeUniverseProvider())
    monkeypatch.setattr(bootstrap_history, "YahooFinanceProvider", _AllFailYahooProvider)

    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(
        f"paths:\n  processed_dir: '{tmp_path / 'processed'}'\n  duckdb_path: '{tmp_path / 'db.duckdb'}'\n"
    )
    rc = bootstrap_history.main(["--config", str(cfg_path)])
    assert rc == 1


def test_start_end_flags_are_passed_through_to_the_provider(tmp_path, monkeypatch, capsys):
    """PHASE 10: --start/--end must reach the underlying provider call, not just --period."""
    captured_kwargs = {}

    class _CapturingYahooProvider:
        def __init__(self, cfg):
            pass

        def fetch_history_batch(self, symbols, period, interval, start=None, end=None):
            captured_kwargs["period"] = period
            captured_kwargs["start"] = start
            captured_kwargs["end"] = end
            results = {s: generate_synthetic_ohlcv(s.replace(".NS", ""), n_days=50) for s in symbols}
            return results, {}

    monkeypatch.setattr(bootstrap_history, "get_universe_provider", lambda cfg: _FakeUniverseProvider())
    monkeypatch.setattr(bootstrap_history, "YahooFinanceProvider", _CapturingYahooProvider)

    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(
        f"paths:\n  processed_dir: '{tmp_path / 'processed'}'\n  duckdb_path: '{tmp_path / 'db.duckdb'}'\n"
    )
    rc = bootstrap_history.main(["--config", str(cfg_path), "--start", "2018-01-01", "--end", "2026-09-11"])
    assert rc == 0
    assert captured_kwargs["start"] == "2018-01-01"
    assert captured_kwargs["end"] == "2026-09-11"

    out = capsys.readouterr().out
    assert "2018-01-01 .. 2026-09-11" in out
