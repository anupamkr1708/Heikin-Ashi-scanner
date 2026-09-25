"""AS-OF data provider — the core mechanism behind true historical replay (PHASE 1/3).

`AsOfDataProvider` wraps a `MarketDataStore` and a fixed `as_of_date`. Every `fetch_history` call
is truncated at the store level (`MarketDataStore.read_symbol_history(..., as_of=...)`) so that
data ingested AFTER `as_of_date` — including data ingested by a `run_daily.py` that has kept
running daily since this replay's `as_of_date` — is structurally invisible to this provider. This
is what distinguishes a genuine historical replay from "whatever's in the store right now, dated
retroactively" (see `tests/integration/test_replay_no_lookahead.py`).

This provider deliberately does NOT know how to fetch NEW data from NSE/yfinance — it only reads
what's already in the local store, exactly like `NSEDataProvider`, but with the added cutoff.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone

import pandas as pd

from nse_scanner.data.base import DataProvider
from nse_scanner.models.market_data import NormalizedBarMeta


@dataclass
class AsOfDataProvider(DataProvider):
    as_of_date: date
    name: str = "NSE_ASOF_REPLAY"
    price_basis: str = "RAW"  # static fallback label; see fetch_history for the real per-symbol value
    store: object = field(default=None)  # nse_scanner.data.storage.MarketDataStore, injected

    def fetch_history(
        self, symbol: str, period: str, interval: str
    ) -> tuple[pd.DataFrame | None, NormalizedBarMeta | None, str | None]:
        if self.store is None:
            return None, None, "AsOfDataProvider has no local store configured"
        df = self.store.read_symbol_history(symbol, as_of=self.as_of_date)  # type: ignore[attr-defined]
        if df is None or df.empty:
            return None, None, f"no locally-ingested history for this symbol on or before {self.as_of_date}"
        actual_price_basis = self.store.price_basis_composition(symbol, as_of=self.as_of_date)  # type: ignore[attr-defined]
        meta = NormalizedBarMeta(
            isin=None,
            nse_symbol=symbol,
            source=self.name,
            price_basis=actual_price_basis,
            interval=interval,
            retrieved_at=datetime.now(timezone.utc),
            provider_version="asof_replay",
        )
        return df, meta, None

    def fetch_history_batch(
        self, symbols: list[str], period: str, interval: str
    ) -> tuple[dict[str, pd.DataFrame], dict[str, str]]:
        results: dict[str, pd.DataFrame] = {}
        errors: dict[str, str] = {}
        for sym in symbols:
            df, _meta, err = self.fetch_history(sym, period, interval)
            if df is not None:
                results[sym] = df
            else:
                errors[sym] = err or "unknown error"
        return results, errors
