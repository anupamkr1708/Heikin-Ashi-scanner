import numpy as np
import pandas as pd
from nse_scanner.data.normalization import extract_symbol_frame, normalize_ohlcv_columns


def _ohlcv_index(n=5):
    return pd.date_range("2025-01-01", periods=n)


def test_single_ticker_plain_columns():
    df = pd.DataFrame(
        {
            "Open": [1, 2, 3],
            "High": [1.1, 2.1, 3.1],
            "Low": [0.9, 1.9, 2.9],
            "Close": [1.05, 2.05, 3.05],
            "Volume": [100, 200, 300],
        },
        index=_ohlcv_index(3),
    )
    out = extract_symbol_frame(df, "FOO", batch_size=1)
    assert out is not None
    assert {"Open", "High", "Low", "Close", "Volume"}.issubset(set(out.columns))


def test_multi_ticker_multiindex_group_by_ticker():
    idx = _ohlcv_index(3)
    fields = ["Open", "High", "Low", "Close", "Volume"]
    tickers = ["FOO", "BAR"]
    cols = pd.MultiIndex.from_product([tickers, fields])
    data = np.random.default_rng(0).uniform(1, 100, size=(3, len(tickers) * len(fields)))
    df = pd.DataFrame(data, index=idx, columns=cols)

    out_foo = extract_symbol_frame(df, "FOO", batch_size=2)
    out_bar = extract_symbol_frame(df, "BAR", batch_size=2)
    assert out_foo is not None and out_bar is not None
    assert not out_foo.equals(out_bar)


def test_multi_ticker_multiindex_group_by_column():
    idx = _ohlcv_index(3)
    fields = ["Open", "High", "Low", "Close", "Volume"]
    tickers = ["FOO", "BAR"]
    cols = pd.MultiIndex.from_product([fields, tickers])  # field-first ordering
    data = np.random.default_rng(1).uniform(1, 100, size=(3, len(tickers) * len(fields)))
    df = pd.DataFrame(data, index=idx, columns=cols)

    out_foo = extract_symbol_frame(df, "FOO", batch_size=2)
    assert out_foo is not None
    assert set(["Open", "High", "Low", "Close", "Volume"]).issubset(set(out_foo.columns))


def test_missing_ticker_returns_none():
    idx = _ohlcv_index(3)
    fields = ["Open", "High", "Low", "Close", "Volume"]
    tickers = ["FOO", "BAR"]
    cols = pd.MultiIndex.from_product([tickers, fields])
    data = np.random.default_rng(2).uniform(1, 100, size=(3, len(tickers) * len(fields)))
    df = pd.DataFrame(data, index=idx, columns=cols)

    out = extract_symbol_frame(df, "NOT_THERE", batch_size=2)
    assert out is None


def test_empty_response_returns_none():
    assert extract_symbol_frame(None, "FOO", batch_size=1) is None
    assert extract_symbol_frame(pd.DataFrame(), "FOO", batch_size=1) is None


def test_all_nan_response_treated_as_empty():
    idx = _ohlcv_index(3)
    df = pd.DataFrame(
        {
            "Open": [np.nan] * 3,
            "High": [np.nan] * 3,
            "Low": [np.nan] * 3,
            "Close": [np.nan] * 3,
            "Volume": [np.nan] * 3,
        },
        index=idx,
    )
    out = extract_symbol_frame(df, "FOO", batch_size=1)
    assert out is None


def test_normalize_ohlcv_columns_coerces_numeric_and_drops_dupe_index():
    idx = pd.DatetimeIndex(["2025-01-01", "2025-01-01", "2025-01-02"])
    df = pd.DataFrame(
        {
            "Open": ["1", "1.5", "2"],
            "High": ["1.1", "1.6", "2.1"],
            "Low": ["0.9", "1.4", "1.9"],
            "Close": ["1.05", "1.55", "2.05"],
            "Volume": ["100", "150", "200"],
        },
        index=idx,
    )
    out = normalize_ohlcv_columns(df)
    assert len(out) == 2  # duplicate date collapsed, keep last
    assert out["Close"].dtype.kind == "f"
    assert out.index.is_monotonic_increasing
