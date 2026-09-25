"""Research-immutability / anti-look-ahead tests (PART 68).

For a historical date T: calculate features/signal, save the result, append FUTURE observations,
recalculate, and assert the historical result at T is byte-for-byte identical. This is checked
for every indicator that has any recursive or rolling-window dependency: Bollinger, Heikin-Ashi,
Wilder ATR, and the breakout state machine.
"""

import numpy as np
import pandas as pd
from nse_scanner.indicators.atr import wilder_atr
from nse_scanner.indicators.bollinger import calculate_bollinger_bands
from nse_scanner.indicators.heikin_ashi import calculate_heikin_ashi
from nse_scanner.strategy.breakout import calculate_breakout_state


def _synthetic_ohlc(n, seed=1):
    rng = np.random.default_rng(seed)
    opens = rng.uniform(90, 110, n)
    closes = opens + rng.normal(0, 2, n)
    highs = np.maximum(opens, closes) + rng.uniform(0, 3, n)
    lows = np.minimum(opens, closes) - rng.uniform(0, 3, n)
    return pd.DataFrame({"Open": opens, "High": highs, "Low": lows, "Close": closes})


def test_bollinger_is_immutable_to_future_data():
    full = _synthetic_ohlc(60)
    T = 40  # historical cutoff index

    bb_at_T_from_truncated = calculate_bollinger_bands(full.iloc[: T + 1], period=20)
    bb_full = calculate_bollinger_bands(full, period=20)

    assert bb_at_T_from_truncated["BB_Upper"].iloc[T] == bb_full["BB_Upper"].iloc[T]
    assert bb_at_T_from_truncated["BB_Middle"].iloc[T] == bb_full["BB_Middle"].iloc[T]
    assert bb_at_T_from_truncated["BB_Lower"].iloc[T] == bb_full["BB_Lower"].iloc[T]


def test_heikin_ashi_is_immutable_to_future_data():
    full = _synthetic_ohlc(60)
    T = 40

    ha_truncated = calculate_heikin_ashi(full.iloc[: T + 1])
    ha_full = calculate_heikin_ashi(full)

    # HA_Open at T only depends on bars 0..T, so it must be bit-identical regardless of what
    # comes after T — this is exactly the property a naive "vectorize the recursion" bug could
    # violate if it peeked at a later window.
    assert ha_truncated["HA_Open"].iloc[T] == ha_full["HA_Open"].iloc[T]
    assert ha_truncated["HA_Close"].iloc[T] == ha_full["HA_Close"].iloc[T]
    assert ha_truncated["HA_High"].iloc[T] == ha_full["HA_High"].iloc[T]
    assert ha_truncated["HA_Low"].iloc[T] == ha_full["HA_Low"].iloc[T]


def test_wilder_atr_is_immutable_to_future_data():
    full = _synthetic_ohlc(60)
    T = 40

    atr_truncated = wilder_atr(full.iloc[: T + 1], period=14)
    atr_full = wilder_atr(full, period=14)

    assert atr_truncated.iloc[T] == atr_full.iloc[T]


def test_breakout_state_is_immutable_to_future_data():
    full = _synthetic_ohlc(60)
    bb_full = calculate_bollinger_bands(full, period=20)
    T = 40

    bo_truncated = calculate_breakout_state(full["Close"].iloc[: T + 1], bb_full["BB_Upper"].iloc[: T + 1])
    bo_full = calculate_breakout_state(full["Close"], bb_full["BB_Upper"])

    assert bo_truncated["Breakout_Type"].iloc[T] == bo_full["Breakout_Type"].iloc[T]
    assert bo_truncated["Days_Above_Upper_BB"].iloc[T] == bo_full["Days_Above_Upper_BB"].iloc[T]


def test_appending_many_future_bars_never_changes_the_past_signal():
    """Stronger version: append future data incrementally, re-verify at every step."""
    base = _synthetic_ohlc(35, seed=5)
    T = 25
    bb_at_T = calculate_bollinger_bands(base.iloc[: T + 1], period=20)["BB_Upper"].iloc[T]
    ha_at_T = calculate_heikin_ashi(base.iloc[: T + 1])["HA_Close"].iloc[T]

    for extra_bars in (1, 5, 10):
        extended = pd.concat([base, _synthetic_ohlc(extra_bars, seed=100 + extra_bars)], ignore_index=True)
        bb_ext = calculate_bollinger_bands(extended, period=20)["BB_Upper"].iloc[T]
        ha_ext = calculate_heikin_ashi(extended)["HA_Close"].iloc[T]
        assert bb_ext == bb_at_T
        assert ha_ext == ha_at_T
