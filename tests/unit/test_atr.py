import numpy as np
import pandas as pd
import pytest
from nse_scanner.indicators.atr import true_range, wilder_atr


def test_true_range_hand_calculated():
    df = pd.DataFrame({
        "High": [110, 115, 108],
        "Low": [100, 105, 95],
        "Close": [105, 112, 100],
    })
    tr = true_range(df)
    # Bar 0 has no previous close, so true_range() falls back to plain High-Low for that single
    # bar (the standard convention) rather than NaN.
    assert tr.iloc[0] == pytest.approx(10.0)  # High-Low = 110-100
    # bar 1: max(115-105, |115-105|, |105-105|) = max(10, 10, 0) = 10
    assert tr.iloc[1] == pytest.approx(10.0)
    # bar 2: max(108-95, |108-112|, |95-112|) = max(13, 4, 17) = 17
    assert tr.iloc[2] == pytest.approx(17.0)


def test_wilder_atr_matches_hand_computed_recursion():
    # 6 bars of hand-picked True Range values (bypassing OHLC by constructing High/Low/Close
    # such that TR is exactly [nan, 2, 4, 3, 5, 1] for period=3).
    highs =  [100, 102, 106, 108, 112, 111]
    lows =   [ 98, 100, 102, 105, 107, 110]
    closes = [ 99, 100, 104, 106, 110, 110.5]
    df = pd.DataFrame({"High": highs, "Low": lows, "Close": closes})

    tr = true_range(df).to_numpy()
    period = 3
    atr = wilder_atr(df, period=period)

    # Seed = mean of TR[1:period+1] (skipping the NaN first TR)
    seed = np.nanmean(tr[1:period + 1])
    assert atr.iloc[period] == pytest.approx(seed)

    # Next value follows the Wilder recursion, not a simple rolling mean of the last `period` TRs
    manual_next = ((seed * (period - 1)) + tr[period + 1]) / period
    assert atr.iloc[period + 1] == pytest.approx(manual_next)

    # Confirm this is NOT the same as a simple rolling mean (the bug this module fixes)
    simple_rolling_mean = pd.Series(tr).rolling(period).mean().iloc[period + 1]
    assert atr.iloc[period + 1] != pytest.approx(simple_rolling_mean)


def test_wilder_atr_nan_before_seed():
    df = pd.DataFrame({"High": [10, 11, 12], "Low": [9, 10, 11], "Close": [9.5, 10.5, 11.5]})
    atr = wilder_atr(df, period=14)
    assert atr.isna().all()
