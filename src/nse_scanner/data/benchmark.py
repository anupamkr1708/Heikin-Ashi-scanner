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
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from nse_scanner.config import DataConfig
from nse_scanner.data.base import BenchmarkProvider
from nse_scanner.data.normalization import normalize_ohlcv_columns
from nse_scanner.logging_config import get_logger

logger = get_logger(__name__)


@dataclass
class YahooBenchmarkProvider(BenchmarkProvider):
    cfg: DataConfig

    def fetch_benchmark(self, ticker: str, period: str, interval: str
                         ) -> tuple[pd.DataFrame | None, str | None]:
        try:
            import yfinance as yf
        except ImportError as e:
            return None, f"yfinance not installed: {e}"

        try:
            raw = yf.download(
                tickers=ticker,
                period=period,
                interval=interval,
                auto_adjust=self.cfg.yfinance_auto_adjust,
                repair=self.cfg.yfinance_repair,
                keepna=self.cfg.yfinance_keepna,
                group_by=self.cfg.yfinance_group_by,
                threads=False,       # single symbol: no benefit to threading, keeps this path simple
                progress=False,
            )
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
        logger.info("Benchmark %s: %d rows retrieved", ticker, len(df))
        return df, None
