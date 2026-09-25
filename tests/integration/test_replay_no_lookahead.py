"""The central requirement of this release: "What would the strategy have detected on historical
date T, using only information available by the close of T?" — and NEVER using anything dated
after T, even if the local database has since been extended far past T by continued daily
ingestion.

This test builds a real `MarketDataStore` (not an in-memory provider), ingests OHLCV data as if
NSE daily ingestion had been running for 80 sessions, replays at an EARLY as-of date using
`AsOfDataProvider`, then ingests 20 MORE future sessions into the same store and replays the SAME
early as-of date again — the result must be byte-identical. This is the provider/storage-level
analogue of `tests/unit/test_lookahead.py`'s pure-function tests: those prove the indicator math
is look-ahead-safe in isolation, this proves the STORAGE QUERY LAYER that feeds that math is too.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd
from nse_scanner.data.asof_provider import AsOfDataProvider
from nse_scanner.data.storage import MarketDataStore
from nse_scanner.testing.synthetic_market_data import generate_synthetic_ohlcv


def _ingest_as_nse(store: MarketDataStore, symbol: str, ohlcv: pd.DataFrame) -> None:
    """Mimics pipeline/ingestion.py's normalization, without needing a real bhavcopy file."""
    now = datetime.now(timezone.utc)
    normalized = pd.DataFrame(
        {
            "nse_symbol": symbol,
            "isin": None,
            "trade_date": ohlcv.index,
            "open": ohlcv["Open"].to_numpy(),
            "high": ohlcv["High"].to_numpy(),
            "low": ohlcv["Low"].to_numpy(),
            "close": ohlcv["Close"].to_numpy(),
            "volume": ohlcv["Volume"].to_numpy(),
            "turnover": None,
            "source": "NSE",
            "price_basis": "RAW",
            "schema_version": "UDIFF",
            "ingested_at": now,
        }
    )
    store.append_eod_prices(normalized)


def test_asof_provider_cannot_see_data_ingested_after_the_cutoff(tmp_path):
    store = MarketDataStore(tmp_path / "processed", tmp_path / "db.duckdb")
    full_history = generate_synthetic_ohlcv("REPLAYTEST", n_days=100)

    # Simulate 80 days of prior daily ingestion.
    early_slice = full_history.iloc[:80]
    _ingest_as_nse(store, "REPLAYTEST", early_slice)

    as_of_date = early_slice.index[60].date()  # well within the ingested range
    provider = AsOfDataProvider(as_of_date=as_of_date, store=store)
    df_before, meta_before, err = provider.fetch_history("REPLAYTEST", period="2y", interval="1d")

    assert df_before is not None
    assert df_before.index[-1].date() == as_of_date
    result_before = df_before.copy()

    # Now simulate 20 MORE days of daily ingestion happening AFTER this replay's as-of date —
    # exactly what would happen if run_daily.py kept running daily in the real world.
    later_slice = full_history.iloc[80:]
    _ingest_as_nse(store, "REPLAYTEST", later_slice)

    # Re-run the SAME historical replay. The result must be identical — the newly-ingested future
    # data must be invisible to a replay whose as-of date predates it.
    df_after, meta_after, err2 = provider.fetch_history("REPLAYTEST", period="2y", interval="1d")
    assert df_after is not None
    assert df_after.index[-1].date() == as_of_date  # still the same cutoff, not the new max date

    pd.testing.assert_frame_equal(result_before, df_after)


def test_asof_provider_excludes_data_strictly_after_cutoff_date():
    """A more surgical check: the cutoff boundary itself is inclusive of as_of but not beyond."""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        from pathlib import Path

        store = MarketDataStore(Path(tmp) / "processed", Path(tmp) / "db.duckdb")
        history = generate_synthetic_ohlcv("BOUNDARYTEST", n_days=40)
        _ingest_as_nse(store, "BOUNDARYTEST", history)

        cutoff = history.index[20].date()
        provider = AsOfDataProvider(as_of_date=cutoff, store=store)
        df, meta, err = provider.fetch_history("BOUNDARYTEST", period="2y", interval="1d")

        assert df is not None
        assert (df.index.date <= cutoff).all()
        assert df.index[-1].date() == cutoff  # inclusive: the as-of day's own bar IS visible


def test_replay_feature_calculation_matches_a_manually_truncated_calculation():
    """End-to-end: features computed via AsOfDataProvider's cutoff must equal features computed
    by manually slicing the DataFrame to the same date — proving the cutoff mechanism produces
    the same answer as "just don't have the future data in the first place"."""
    import tempfile
    from pathlib import Path

    from nse_scanner.config import ScannerConfig
    from nse_scanner.pipeline.features import build_feature_frame

    with tempfile.TemporaryDirectory() as tmp:
        store = MarketDataStore(Path(tmp) / "processed", Path(tmp) / "db.duckdb")
        full_history = generate_synthetic_ohlcv("FEATTEST", n_days=100)
        _ingest_as_nse(store, "FEATTEST", full_history)

        cutoff = full_history.index[70].date()
        provider = AsOfDataProvider(as_of_date=cutoff, store=store)
        df_asof, _meta, _err = provider.fetch_history("FEATTEST", period="2y", interval="1d")

        manually_truncated = full_history[full_history.index.date <= cutoff]

        cfg = ScannerConfig()
        feat_asof = build_feature_frame(df_asof, cfg)
        feat_manual = build_feature_frame(manually_truncated, cfg)

        assert feat_asof["BB_Upper"].iloc[-1] == feat_manual["BB_Upper"].iloc[-1]
        assert feat_asof["HA_Close"].iloc[-1] == feat_manual["HA_Close"].iloc[-1]
        atr_col = f"ATR{cfg.history.atr_period}"
        assert feat_asof[atr_col].iloc[-1] == feat_manual[atr_col].iloc[-1]


def test_full_run_scan_pipeline_via_asof_provider_is_immutable_to_future_ingestion():
    """The strongest version of this test: exercises the SAME `pipeline.scan.run_scan()` entry
    point the real `run_replay.py` CLI calls (not just the provider in isolation), with a real
    on-disk store, a genuine append-only future-data simulation (new dates added, existing dates
    never rewritten — unlike a careless manual test that regenerates a whole series with a
    different length and silently shifts which calendar date each price lands on), and asserts
    the entire Live_Signals output row is byte-identical before and after."""
    import tempfile
    from pathlib import Path

    import pandas as pd
    from nse_scanner.config import ScannerConfig
    from nse_scanner.data.calendar import SessionInfo
    from nse_scanner.offline_fixture import InMemoryUniverseProvider
    from nse_scanner.pipeline.scan import run_scan
    from nse_scanner.testing.synthetic_market_data import generate_synthetic_universe

    with tempfile.TemporaryDirectory() as tmp:
        store = MarketDataStore(Path(tmp) / "processed", Path(tmp) / "db.duckdb")

        # Generate ONE full history per symbol up front, then ingest it in two slices — this is
        # the correct way to simulate "more data arrives later" (append-only, same underlying
        # series) as opposed to calling the generator twice with different lengths, which
        # reassigns which calendar date each price lands on and is NOT a valid test of anything.
        full_histories = {
            sym: generate_synthetic_ohlcv(sym, n_days=100, engineer_breakout_on_last_day=(sym == "ALPHATEST"))
            for sym in ["ALPHATEST", "BETATEST", "GAMMATEST"]
        }
        for sym, full_hist in full_histories.items():
            _ingest_as_nse(store, sym, full_hist.iloc[:70])  # only the first 70 sessions exist so far

        cutoff = full_histories["ALPHATEST"].index[50].date()
        universe_df = generate_synthetic_universe()
        universe_df = universe_df[universe_df["Symbol"].isin(full_histories.keys())].reset_index(drop=True)

        cfg = ScannerConfig()
        session = SessionInfo(cutoff, cutoff, cutoff, cutoff)
        provider_before = AsOfDataProvider(as_of_date=cutoff, store=store)
        result_before = run_scan(
            cfg,
            InMemoryUniverseProvider(universe_df),
            provider_before,
            benchmark_provider=None,
            session=session,
            holidays=set(),
        )
        live_before = result_before.sheets.live_signals.copy()
        diag_before = result_before.sheets.diagnostics.copy()

        # Now append the REMAINING 30 sessions (including the engineered future breakout on the
        # very last day) — genuinely new dates, nothing already-ingested gets rewritten.
        for sym, full_hist in full_histories.items():
            _ingest_as_nse(store, sym, full_hist.iloc[70:])

        provider_after = AsOfDataProvider(as_of_date=cutoff, store=store)
        result_after = run_scan(
            cfg,
            InMemoryUniverseProvider(universe_df),
            provider_after,
            benchmark_provider=None,
            session=session,
            holidays=set(),
        )
        live_after = result_after.sheets.live_signals.copy()
        diag_after = result_after.sheets.diagnostics.copy()

        pd.testing.assert_frame_equal(live_before, live_after)
        pd.testing.assert_frame_equal(diag_before, diag_after)
