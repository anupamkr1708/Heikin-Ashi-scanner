"""Yahoo Finance provider — secondary/fallback historical data source (PART 3 / PART 10).

Every yfinance parameter that matters is passed explicitly, never left at library defaults
(PART 10): interval, auto_adjust, repair, keepna, group_by, threads. The tested version is
recorded in config.DataConfig.yfinance_version_tested and in every run manifest.

**Sandbox note:** this repository's build/CI network sandbox does not have outbound access to
Yahoo Finance's endpoints, so this provider's live network behavior could not be exercised in
this environment (see CODE_REVIEW.md). The MultiIndex/shape-normalization logic it depends on
(data/normalization.py) IS unit-tested against synthetic DataFrames shaped exactly like real
yfinance responses (single ticker, multi-ticker MultiIndex, missing ticker, empty response).
Before relying on this provider in production, run `pytest tests/integration/test_yfinance_live.py
-m live` once with network access to confirm the installed yfinance version's response shape
still matches what normalization.py expects.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone

import pandas as pd

from nse_scanner.config import DataConfig
from nse_scanner.data.base import DataProvider
from nse_scanner.data.normalization import extract_symbol_frame
from nse_scanner.exceptions import DataProviderError
from nse_scanner.logging_config import get_logger
from nse_scanner.models.market_data import NormalizedBarMeta, PriceBasis

logger = get_logger(__name__)


@dataclass
class YahooFinanceProvider(DataProvider):
    cfg: DataConfig
    name: str = "YFINANCE"

    @property
    def price_basis(self) -> str:
        return PriceBasis.ADJUSTED if self.cfg.yfinance_auto_adjust else PriceBasis.RAW

    def _import_yfinance(self):
        try:
            import yfinance as yf
        except ImportError as e:  # pragma: no cover - environment issue, not logic
            raise DataProviderError("yfinance is not installed. Install the pinned version from pyproject.toml.") from e
        return yf

    def fetch_history(
        self, symbol: str, period: str, interval: str
    ) -> tuple[pd.DataFrame | None, NormalizedBarMeta | None, str | None]:
        results, errors = self.fetch_history_batch([symbol], period, interval)
        if symbol in results:
            df = results[symbol]
            meta = NormalizedBarMeta(
                isin=None,
                nse_symbol=symbol,
                source=self.name,
                price_basis=PriceBasis.ADJUSTED if self.cfg.yfinance_auto_adjust else PriceBasis.RAW,
                interval=interval,
                retrieved_at=datetime.now(timezone.utc),
                provider_version=self.cfg.yfinance_version_tested,
            )
            return df, meta, None
        return None, None, errors.get(symbol, "unknown error")

    def fetch_history_batch(
        self, symbols: list[str], period: str, interval: str, start: str | None = None, end: str | None = None
    ) -> tuple[dict[str, pd.DataFrame], dict[str, str]]:
        """`start`/`end` (YYYY-MM-DD), when given, are passed to yfinance INSTEAD of `period` —
        yfinance's own API treats these as mutually exclusive-ish (an explicit date range takes
        priority), used by `bootstrap_history.py --start ... --end ...` for a precise historical
        window rather than a relative period."""
        yf = self._import_yfinance()
        results: dict[str, pd.DataFrame] = {}
        errors: dict[str, str] = {}
        use_date_range = start is not None or end is not None

        batch_size = self.cfg.batch_size
        for i in range(0, len(symbols), batch_size):
            chunk = symbols[i : i + batch_size]
            data = None
            last_err: Exception | None = None
            for attempt in range(self.cfg.max_download_retries + 1):
                try:
                    download_kwargs = dict(
                        tickers=chunk,
                        interval=interval,
                        auto_adjust=self.cfg.yfinance_auto_adjust,
                        repair=self.cfg.yfinance_repair,
                        keepna=self.cfg.yfinance_keepna,
                        group_by=self.cfg.yfinance_group_by,
                        threads=self.cfg.yfinance_threads,
                        progress=False,
                    )
                    if use_date_range:
                        download_kwargs["start"] = start
                        download_kwargs["end"] = end
                    else:
                        download_kwargs["period"] = period
                    data = yf.download(**download_kwargs)
                    break
                except Exception as e:  # noqa: BLE001 - provider call, converted to structured error below
                    last_err = e
                    if attempt < self.cfg.max_download_retries:
                        time.sleep(self.cfg.retry_backoff_sec)

            if data is None:
                msg = f"batch_download_failed_after_retries: {type(last_err).__name__}: {last_err}"
                for sym in chunk:
                    errors[sym] = msg
                time.sleep(self.cfg.batch_pause_sec)
                continue

            for sym in chunk:
                frame = extract_symbol_frame(data, sym, len(chunk))
                if frame is None:
                    errors[sym] = "no_data_returned"
                else:
                    results[sym] = frame
            time.sleep(self.cfg.batch_pause_sec)

        return results, errors
