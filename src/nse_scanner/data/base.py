"""Provider interfaces.

The strategy/indicator layers depend only on these abstractions, never on a concrete provider
(PART 8 / PART 65 — dependency inversion). Concrete implementations: data/nse_eod.py
(NSEDataProvider), data/yfinance_provider.py (YahooFinanceProvider), universe/nifty200.py
(NSEUniverseProvider), data/benchmark.py (benchmark providers).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date

import pandas as pd

from nse_scanner.models.market_data import NormalizedBarMeta


class DataProvider(ABC):
    """Fetches OHLCV history for one or more securities."""

    name: str

    @abstractmethod
    def fetch_history(self, symbol: str, period: str, interval: str
                       ) -> tuple[pd.DataFrame | None, NormalizedBarMeta | None, str | None]:
        """Returns (dataframe_or_None, meta_or_None, error_message_or_None). Never raises for an
        ordinary per-symbol failure (missing ticker, empty response) — that is reported via the
        error_message so one bad symbol never halts a batch (PART 15). Raises DataProviderError
        only for a systemic failure (e.g. the whole batch endpoint is unreachable)."""

    @abstractmethod
    def fetch_history_batch(self, symbols: list[str], period: str, interval: str
                             ) -> tuple[dict[str, pd.DataFrame], dict[str, str]]:
        """Returns (results: symbol->DataFrame for successes, errors: symbol->error message)."""


class UniverseProvider(ABC):
    """Fetches a validated universe constituent list."""

    @abstractmethod
    def get_constituents(self) -> tuple[pd.DataFrame, str, str]:
        """Returns (constituents_df, source_used, retrieval_timestamp_iso). Raises
        UniverseIntegrityError if no source can be validated — never silently falls back to a
        small hard-coded list (PART 6)."""


class BenchmarkProvider(ABC):
    """Fetches benchmark index history. Deliberately NOT the same code path as the per-stock
    downloader (PART 10 — 'Do NOT force benchmark data through a stock downloader that assumes
    identical schemas'), because a benchmark failure must never silently degrade into the
    per-stock scan failing too (BUG 3 / BUG 16)."""

    @abstractmethod
    def fetch_benchmark(self, ticker: str, period: str, interval: str
                         ) -> tuple[pd.DataFrame | None, str | None]:
        """Returns (dataframe_or_None, error_message_or_None)."""


def expected_session_date(today: date) -> date:
    """Deprecated placeholder retained only to flag the anti-pattern this repository fixes
    (BUG 1): callers must use data.calendar.resolve_session(...) instead of `today - 1 day`.
    Intentionally raises if anyone calls it."""
    raise NotImplementedError(
        "expected_session_date(today - 1 day) is exactly the bug this repository fixes. "
        "Use nse_scanner.data.calendar.resolve_session() instead."
    )
