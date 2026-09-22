"""Dedicated benchmark-index data path (PART 10, fixes BUG 3).

**Audit note (BUG 3):** the legacy notebook fetched the benchmark index through the same batch
downloader used for stocks, which assumes a MultiIndex/`group_by="ticker"` shape. A single-symbol
yfinance request (`^NSEI`) does not return that shape, which is exactly the kind of
single-vs-multi mismatch data/normalization.py exists to handle — but more importantly, a
benchmark fetch is architecturally a *different concern* from a stock fetch (PART 10: "Do NOT
force benchmark data through a stock downloader that assumes identical schemas"), so it gets its
own function/provider here rather than reusing YahooFinanceProvider.fetch_history_batch.

A benchmark failure must never silently degrade into the per-stock scan failing, nor into a
numeric score that pretends benchmark data existed (BUG 4 / BUG 16) — callers receive an explicit
`None` + error message and must propagate "Market_Regime = UNAVAILABLE_NO_BENCHMARK_DATA" /
"Benchmark_Status = UNAVAILABLE" rather than defaulting to a neutral-looking number.

**AS-OF correctness (found during the v1.3 research-integrity audit — previously a real gap, not
just a documentation omission):** every call site (`run_daily.py`, `run_scan.py`, `run_replay.py`)
constructed this provider identically and it always called `yf.download(period=...)` with no upper
date bound. For live runs that's correct — "whatever Yahoo has right now" IS the live benchmark.
For `run_replay.py --as-of <historical date>`, that was wrong: the SAME unbounded call ran, so a
historical replay's `Market_Regime` / `RS_20D` / `RS_60D` / `RS_120D` were computed from
TODAY's benchmark data, not from what was actually available by the close of the replayed date —
the exact violation the AS_OF replay mechanism (`data/asof_provider.py`) exists to prevent for
stock data. `tests/integration/test_replay_no_lookahead.py`'s flagship pipeline-level test didn't
catch this because it always passed `benchmark_provider=None`, so the benchmark code path was
never exercised by the no-look-ahead regression at all.

`as_of_date`, when set (only `run_replay.py` sets it), truncates the benchmark series at TWO
independent layers so no single layer has to be trusted alone:
  1. request layer — pass `end=as_of_date + 1 day` to `yf.download` so future rows are not even
     requested (bounds network cost the same way the SQL cutoff bounds a store query).
  2. response layer — unconditionally drop any returned row with `date > as_of_date` regardless of
     what the request layer actually honored. This mirrors `MarketDataStore.read_symbol_history`'s
     `trade_date <= ?` SQL clause: don't rely on the upstream API's `end` semantics being correct
     or inclusive/exclusive in the way this code assumes.

**Residual limitation, stated rather than hidden:** date-truncation does not protect against a
provider silently REVISING an already-published adjusted-close value for a date <= as_of_date
(e.g. yfinance retroactively re-adjusting history for a dividend declared after that date). Once a
date is ingested into the local store (stock path), it is frozen and immune to this — but this
benchmark path re-fetches from the live network on every single run, live or replay, and does not
cache/freeze historical benchmark bars locally. This means two AS-OF replays of the same historical
date, run months apart, are not guaranteed byte-identical for benchmark-derived columns even though
they are guaranteed identical for the baseline BB+HA signal itself (which never depends on the
benchmark). This is a known, disclosed gap — see CODE_REVIEW.md — not a claim that the benchmark
path is fully point-in-time; only that it no longer leaks entire FUTURE SESSIONS into a replay.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

import pandas as pd

from nse_scanner.config import DataConfig
from nse_scanner.data.base import BenchmarkProvider
from nse_scanner.data.normalization import normalize_ohlcv_columns
from nse_scanner.logging_config import get_logger

logger = get_logger(__name__)


@dataclass
class YahooBenchmarkProvider(BenchmarkProvider):
    cfg: DataConfig
    # Historical-replay cutoff (PHASE 1/3 parity with AsOfDataProvider). None (the default) means
    # "live" — no cutoff — which is correct for run_daily.py / run_scan.py. run_replay.py is the
    # only caller that sets this, to session.expected_completed_session.
    as_of_date: date | None = None

    def fetch_benchmark(self, ticker: str, period: str, interval: str
                         ) -> tuple[pd.DataFrame | None, str | None]:
        try:
            import yfinance as yf
        except ImportError as e:
            return None, f"yfinance not installed: {e}"

        download_kwargs: dict = dict(
            tickers=ticker,
            interval=interval,
            auto_adjust=self.cfg.yfinance_auto_adjust,
            repair=self.cfg.yfinance_repair,
            keepna=self.cfg.yfinance_keepna,
            group_by=self.cfg.yfinance_group_by,
            threads=False,       # single symbol: no benefit to threading, keeps this path simple
            progress=False,
        )
        if self.as_of_date is not None:
            # Request layer: never even ask Yahoo for sessions after the replay cutoff. `end` is
            # exclusive in yfinance, so add one day to include as_of_date itself.
            download_kwargs["end"] = (self.as_of_date + timedelta(days=1)).isoformat()
            # A wide, fixed start keeps this bounded and deterministic rather than relying on
            # `period` (which is always relative to *today*, not to as_of_date, and would request
            # an as_of-irrelevant window for an old replay).
            download_kwargs["start"] = "2000-01-01"
        else:
            download_kwargs["period"] = period

        try:
            raw = yf.download(**download_kwargs)
        except Exception as e:  # noqa: BLE001 - network/provider call
            return None, f"{type(e).__name__}: {e}"

        if raw is None or raw.empty:
            return None, "no_data_returned"

        df = raw.copy()
        if isinstance(df.columns, pd.MultiIndex):
            # A single-ticker call can still come back with a (field, ticker) MultiIndex
            # depending on yfinance version/group_by — collapse to the field level explicitly,
            # never assume it matches the stock-downloader shape (BUG 3).
            df.columns = df.columns.get_level_values(0)
        try:
            df = normalize_ohlcv_columns(df)
        except KeyError as e:
            return None, f"unexpected benchmark schema, missing column: {e}"

        if df.empty:
            return None, "no_data_returned"

        if self.as_of_date is not None:
            # Response layer (defense in depth — see module docstring): unconditionally drop any
            # row after the cutoff regardless of what the request layer actually honored. Never
            # trust a single layer alone for a no-look-ahead guarantee.
            before = len(df)
            cutoff_ts = pd.Timestamp(self.as_of_date)
            df = df[df.index.normalize() <= cutoff_ts]
            dropped = before - len(df)
            if dropped:
                logger.warning(
                    "Benchmark %s: dropped %d row(s) after as_of=%s that the request layer "
                    "should not have returned (response-layer cutoff caught it)",
                    ticker, dropped, self.as_of_date,
                )
            if df.empty:
                return None, f"no benchmark history on or before as_of={self.as_of_date}"

        logger.info("Benchmark %s: %d rows retrieved%s", ticker, len(df),
                    f" (as_of={self.as_of_date})" if self.as_of_date is not None else "")
        return df, None
