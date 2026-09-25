import numpy as np
import pandas as pd
import pytest
from nse_scanner.data.validation import validate_and_clean_ohlc
from nse_scanner.exceptions import DataValidationError


def _good_df(n=40):
    idx = pd.date_range("2025-01-01", periods=n)
    close = 100 + np.cumsum(np.random.default_rng(0).normal(0, 1, n))
    return pd.DataFrame(
        {
            "Open": close - 0.5,
            "High": close + 1,
            "Low": close - 1,
            "Close": close,
            "Volume": np.random.default_rng(1).integers(1000, 5000, n),
        },
        index=idx,
    )


def test_valid_data_passes():
    df = _good_df(40)
    cleaned, report = validate_and_clean_ohlc(df, min_rows=30)
    assert report.ok
    assert len(cleaned) == 40


def test_none_input_raises():
    with pytest.raises(DataValidationError):
        validate_and_clean_ohlc(None, min_rows=10)


def test_insufficient_rows_raises():
    df = _good_df(10)
    with pytest.raises(DataValidationError):
        validate_and_clean_ohlc(df, min_rows=30)


def test_non_positive_prices_dropped_not_fabricated():
    df = _good_df(40)
    df.iloc[5, df.columns.get_loc("Close")] = -5.0
    cleaned, report = validate_and_clean_ohlc(df, min_rows=30)
    assert len(cleaned) == 39
    assert any("non-positive" in i for i in report.issues)


def test_bad_ohlc_ordering_dropped():
    df = _good_df(40)
    # Make Low > High on one row — physically impossible bar
    df.iloc[10, df.columns.get_loc("Low")] = df.iloc[10]["High"] + 10
    cleaned, report = validate_and_clean_ohlc(df, min_rows=30)
    assert len(cleaned) == 39
    assert any("ordering" in i for i in report.issues)


def test_negative_volume_dropped():
    df = _good_df(40)
    df.iloc[3, df.columns.get_loc("Volume")] = -100
    cleaned, report = validate_and_clean_ohlc(df, min_rows=30)
    assert len(cleaned) == 39


def test_missing_columns_raises():
    df = _good_df(40).drop(columns=["Volume"])
    with pytest.raises(DataValidationError):
        validate_and_clean_ohlc(df, min_rows=30)
