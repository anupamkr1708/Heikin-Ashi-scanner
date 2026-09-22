import numpy as np
import pandas as pd
import pytest
from nse_scanner.indicators.bollinger import bb_overshoot_pct, calculate_bollinger_bands


def _df(closes):
    return pd.DataFrame({"Close": closes}, index=pd.RangeIndex(len(closes)))


def test_bollinger_matches_hand_calculation():
    closes = [10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 19, 18, 17, 16, 15, 14, 13, 12, 11]
    df = _df(closes)
    bb = calculate_bollinger_bands(df, period=20, std_mult=2.0, ddof=1)

    expected_mean = np.mean(closes)
    expected_std = np.std(closes, ddof=1)

    assert bb["BB_Middle"].iloc[-1] == pytest.approx(expected_mean)
    assert bb["BB_StdDev"].iloc[-1] == pytest.approx(expected_std)
    assert bb["BB_Upper"].iloc[-1] == pytest.approx(expected_mean + 2 * expected_std)
    assert bb["BB_Lower"].iloc[-1] == pytest.approx(expected_mean - 2 * expected_std)


def test_bollinger_nan_before_period():
    df = _df(list(range(10)))
    bb = calculate_bollinger_bands(df, period=20)
    assert bb["BB_Middle"].isna().all()


def test_ddof_changes_stddev():
    closes = [10, 12, 9, 15, 11, 13, 8, 14, 10, 12, 16, 9, 11, 13, 10, 12, 14, 9, 11, 13]
    df = _df(closes)
    bb0 = calculate_bollinger_bands(df, period=20, ddof=0)
    bb1 = calculate_bollinger_bands(df, period=20, ddof=1)
    assert bb0["BB_StdDev"].iloc[-1] < bb1["BB_StdDev"].iloc[-1]


def test_pctb_is_ratio_not_percentage():
    # Close exactly at the upper band => PctB == 1.0 (a ratio), never "100" or "1.00%"
    closes = [100.0] * 19 + [100.0]
    df = _df(closes)
    bb = calculate_bollinger_bands(df, period=20, ddof=1)
    # constant series -> stddev 0 -> upper == lower == middle -> band_range 0 -> PctB is NaN by guard
    assert np.isnan(bb["BB_PctB"].iloc[-1])

    # A series with real variance: check PctB is within a sane ratio range, not a percentage scale
    closes2 = list(np.linspace(90, 130, 20))
    df2 = _df(closes2)
    bb2 = calculate_bollinger_bands(df2, period=20, ddof=1)
    pctb = bb2["BB_PctB"].iloc[-1]
    assert -3 < pctb < 3  # ratio scale, not "0..400" percentage scale


def test_overshoot_formula_exact():
    close = pd.Series([104.0])
    upper = pd.Series([100.0])
    result = bb_overshoot_pct(close, upper)
    assert result.iloc[0] == pytest.approx(4.0)

