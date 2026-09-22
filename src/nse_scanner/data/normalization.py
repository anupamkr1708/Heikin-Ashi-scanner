"""Normalizes raw yfinance output into the internal OHLCV schema.

**Audit note (BUG 3):** the legacy notebook's benchmark download and per-stock download shared
one code path that assumed a consistent MultiIndex shape. yfinance's `download()` with multiple
tickers returns a `MultiIndex` (whose level order depends on `group_by`), but a *single*-ticker
request returns plain columns — and an empty/failed response can come back as `None`, an empty
DataFrame, or a DataFrame that is all-NaN. This module handles each case explicitly and is
covered by tests/unit/test_yfinance_normalization.py for: single ticker, multiple tickers,
missing ticker, partial response, empty response, MultiIndex, ordinary columns.
"""

from __future__ import annotations

import pandas as pd

REQUIRED_COLUMNS = ("Open", "High", "Low", "Close", "Volume")


def extract_symbol_frame(batch_result: pd.DataFrame | None, symbol: str, batch_size: int) -> pd.DataFrame | None:
    """Extracts a single symbol's OHLCV frame out of a yfinance batch-download result.

    `batch_size` is the number of tickers originally requested in this call — NOT
    `len(batch_result.columns)`, which is unreliable once some tickers silently return no data.
    """
    if batch_result is None:
        return None

    if batch_size == 1:
        # Single-ticker downloads never carry a MultiIndex, regardless of group_by.
        df = batch_result.copy()
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        return _none_if_empty(df)

    if isinstance(batch_result.columns, pd.MultiIndex):
        level0_values = set(batch_result.columns.get_level_values(0))
        level1_values = set(batch_result.columns.get_level_values(1)) if batch_result.columns.nlevels > 1 else set()
        if symbol in level0_values:
            df = batch_result[symbol].copy()
        elif symbol in level1_values:
            # group_by="column" puts the field first and the ticker second.
            df = batch_result.xs(symbol, axis=1, level=1, drop_level=True).copy()
        else:
            return None
        return _none_if_empty(df)

    # Ordinary (non-MultiIndex) columns with more than one ticker requested is unexpected but
    # can happen if yfinance silently collapsed to a single successful ticker — only trust it
    # when the requested symbol is genuinely the only one that could be in scope.
    return None


def _none_if_empty(df: pd.DataFrame) -> pd.DataFrame | None:
    df = df.dropna(how="all")
    if df.empty:
        return None
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        return None
    return df


def normalize_ohlcv_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Coerces to the canonical column set/order and numeric dtypes. Does not validate ranges —
    see data/validation.py for that."""
    out = df.copy()
    if isinstance(out.columns, pd.MultiIndex):
        out.columns = out.columns.get_level_values(0)
    out = out[list(REQUIRED_COLUMNS)]
    for c in REQUIRED_COLUMNS:
        out[c] = pd.to_numeric(out[c], errors="coerce")
    out = out[~out.index.duplicated(keep="last")].sort_index()
    return out
