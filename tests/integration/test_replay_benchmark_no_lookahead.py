"""Benchmark-path extension of `test_replay_no_lookahead.py`'s definitive no-look-ahead test
(project spec PART 10 — the invariant must cover "regime context" and "benchmark data" explicitly,
not merely the final signal count).

**Why this file exists as a SEPARATE regression from `test_replay_no_lookahead.py`:** that file's
flagship `test_full_run_scan_pipeline_via_asof_provider_is_immutable_to_future_ingestion` always
calls `run_scan(..., benchmark_provider=None, ...)` — it never exercised the benchmark code path
at all, so it could not have caught (and did not catch) the gap this file is regression-testing:
`YahooBenchmarkProvider` used to call `yf.download(period=...)` with no upper date bound
regardless of caller, which meant `run_replay.py --as-of <past date>` fetched TODAY's live
benchmark data into a historical replay. The clearest concrete symptom (asserted directly below,
not just inferred): `pipeline/scan.py` builds the `Market_Regime` sheet from
`index_features.iloc[-1]` — the LAST row of whatever the benchmark provider returned. Without an
as-of cutoff on the benchmark fetch, that "last row" in a replay would silently be TODAY's index
bar, not the replayed date's — the Market_Regime sheet of a historical replay report would show
today's market conditions mislabeled as the replay date's.

`yfinance.download` is mocked to always return a "live" (i.e., extends past any as_of_date)
benchmark series, exactly mirroring the real-world situation where Yahoo always has data up to
today regardless of which historical date is being replayed.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd
from nse_scanner.config import ScannerConfig
from nse_scanner.data.asof_provider import AsOfDataProvider
from nse_scanner.data.benchmark import YahooBenchmarkProvider
from nse_scanner.data.calendar import SessionInfo
from nse_scanner.data.storage import MarketDataStore
from nse_scanner.offline_fixture import InMemoryUniverseProvider
from nse_scanner.pipeline.scan import run_scan

from tests.integration.test_replay_no_lookahead import _ingest_as_nse


def _synthetic_index(n_days: int, seed: int = 99) -> pd.DataFrame:
    """Deterministic index series with a KNOWN close at any position, so a test can assert an
    exact expected value rather than merely "differs from the live-fetched last row"."""
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range(end=pd.Timestamp.today().normalize() - pd.Timedelta(days=1), periods=n_days)
    closes = 20000.0 + np.cumsum(rng.normal(5.0, 40.0, size=n_days))
    return pd.DataFrame({
        "Open": closes - 10, "High": closes + 20, "Low": closes - 20, "Close": closes,
        "Volume": rng.integers(1_000_000, 5_000_000, size=n_days),
    }, index=dates)


def _run_replay_style_scan(tmp_path: Path, cutoff_idx: int, mocked_index_df: pd.DataFrame):
    """Builds a real on-disk store with one stock's history (so run_scan has a valid universe to
    iterate, matching the shape of a real replay), and a `YahooBenchmarkProvider` configured
    exactly the way `run_replay.py` now configures it (as_of_date = the replay cutoff), with
    `yfinance.download` mocked to return `mocked_index_df` regardless of what's asked for — this
    is the realistic case: a live provider always returns "up to today," not "up to as_of"."""
    from nse_scanner.testing.synthetic_market_data import generate_synthetic_ohlcv, generate_synthetic_universe

    store = MarketDataStore(tmp_path / "processed", tmp_path / "db.duckdb")
    history = generate_synthetic_ohlcv("REGIMETEST", n_days=100)
    _ingest_as_nse(store, "REGIMETEST", history)

    cutoff = history.index[cutoff_idx].date()
    universe_df = generate_synthetic_universe()
    universe_df = universe_df[universe_df["Symbol"] == "ALPHATEST"].copy()  # not ingested: harmless,
    # keeps the universe non-empty without needing this symbol's history for what this test checks
    # (the Market_Regime sheet is built before the per-symbol loop and does not depend on it).

    cfg = ScannerConfig()
    session = SessionInfo(cutoff, cutoff, cutoff, cutoff)
    data_provider = AsOfDataProvider(as_of_date=cutoff, store=store)
    benchmark_provider = YahooBenchmarkProvider(cfg.data, as_of_date=cutoff)

    with patch("yfinance.download", return_value=mocked_index_df.copy()):
        result = run_scan(cfg, InMemoryUniverseProvider(universe_df), data_provider,
                           benchmark_provider, session=session, holidays=set())
    return result, cutoff


def test_replay_market_regime_sheet_uses_the_as_of_date_not_todays_live_close(tmp_path):
    """Direct proof of the concrete symptom described in the module docstring: the Market_Regime
    sheet's Index_Close must equal the close ON THE REPLAY DATE, not the last row of whatever the
    (always-live) yfinance call returned."""
    index_df = _synthetic_index(n_days=100)
    cutoff_idx = 60
    result, cutoff = _run_replay_style_scan(tmp_path, cutoff_idx, index_df)

    assert not result.sheets.market_regime.empty
    reported_close = result.sheets.market_regime.iloc[0]["Index_Close"]
    expected_close_at_cutoff = index_df.loc[pd.Timestamp(cutoff), "Close"]
    todays_live_close = index_df["Close"].iloc[-1]

    assert reported_close == expected_close_at_cutoff
    # The regression this guards: before the fix, `reported_close` would have been
    # `todays_live_close` (the last row of the unbounded fetch) instead.
    assert reported_close != todays_live_close


def test_replay_market_regime_sheet_is_immutable_to_future_benchmark_sessions(tmp_path):
    """The definitive PART 10 invariant, applied to the benchmark path: replay the SAME historical
    as-of date twice, with the (mocked, always-live) benchmark feed extended by 40 MORE future
    sessions between the two runs — including a large engineered price move designed to tempt any
    future non-causal regime/RS calculation — and require byte-identical Market_Regime output."""
    index_before = _synthetic_index(n_days=100)
    cutoff_idx = 60

    result_before, cutoff = _run_replay_style_scan(tmp_path / "run1", cutoff_idx, index_before)
    regime_before = result_before.sheets.market_regime.copy()

    # Extend with 40 more sessions AFTER the original 100, including a violent engineered move
    # (a regime-flipping crash) that a leaking implementation would be very likely to expose.
    extra_dates = pd.bdate_range(
        start=index_before.index[-1] + pd.Timedelta(days=1), periods=40,
    )
    crash_close = index_before["Close"].iloc[-1] * np.linspace(1.0, 0.55, 40)  # -45% engineered crash
    index_after = pd.concat([
        index_before,
        pd.DataFrame({
            "Open": crash_close * 1.01, "High": crash_close * 1.02, "Low": crash_close * 0.97,
            "Close": crash_close, "Volume": np.full(40, 9_000_000),
        }, index=extra_dates),
    ])
    assert len(index_after) == len(index_before) + 40  # genuinely appended, nothing rewritten
    pd.testing.assert_frame_equal(index_after.iloc[:100], index_before)

    result_after, cutoff_after = _run_replay_style_scan(tmp_path / "run2", cutoff_idx, index_after)
    regime_after = result_after.sheets.market_regime.copy()

    assert cutoff_after == cutoff
    pd.testing.assert_frame_equal(regime_before, regime_after)
