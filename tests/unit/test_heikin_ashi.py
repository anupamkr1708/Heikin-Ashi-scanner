import pandas as pd
import pytest
from nse_scanner.indicators.heikin_ashi import calculate_heikin_ashi, ha_body_pct, seed_dependency_weight


def test_ha_seed_and_recursion_hand_calculated():
    # Two bars, hand-computed.
    df = pd.DataFrame(
        {
            "Open": [100.0, 108.0],
            "High": [110.0, 112.0],
            "Low": [95.0, 104.0],
            "Close": [105.0, 110.0],
        }
    )
    ha = calculate_heikin_ashi(df)

    # Bar 0 (seed)
    expected_ha_close_0 = (100 + 110 + 95 + 105) / 4.0
    expected_ha_open_0 = (100 + 105) / 2.0
    assert ha["HA_Close"].iloc[0] == pytest.approx(expected_ha_close_0)
    assert ha["HA_Open"].iloc[0] == pytest.approx(expected_ha_open_0)
    assert ha["HA_High"].iloc[0] == pytest.approx(max(110, expected_ha_open_0, expected_ha_close_0))
    assert ha["HA_Low"].iloc[0] == pytest.approx(min(95, expected_ha_open_0, expected_ha_close_0))

    # Bar 1 (recursion)
    expected_ha_close_1 = (108 + 112 + 104 + 110) / 4.0
    expected_ha_open_1 = (expected_ha_open_0 + expected_ha_close_0) / 2.0
    assert ha["HA_Close"].iloc[1] == pytest.approx(expected_ha_close_1)
    assert ha["HA_Open"].iloc[1] == pytest.approx(expected_ha_open_1)


def test_ha_ordering_invariants_hold_on_random_data():
    import numpy as np

    rng = np.random.default_rng(0)
    n = 100
    opens = rng.uniform(90, 110, n)
    closes = opens + rng.normal(0, 2, n)
    highs = np.maximum(opens, closes) + rng.uniform(0, 3, n)
    lows = np.minimum(opens, closes) - rng.uniform(0, 3, n)
    df = pd.DataFrame({"Open": opens, "High": highs, "Low": lows, "Close": closes})

    ha = calculate_heikin_ashi(df)
    assert (ha["HA_High"] >= ha["HA_Open"] - 1e-9).all()
    assert (ha["HA_High"] >= ha["HA_Close"] - 1e-9).all()
    assert (ha["HA_Low"] <= ha["HA_Open"] + 1e-9).all()
    assert (ha["HA_Low"] <= ha["HA_Close"] + 1e-9).all()


def test_ha_body_pct_formula():
    ha_open = pd.Series([100.0])
    ha_close = pd.Series([103.0])
    assert ha_body_pct(ha_open, ha_close).iloc[0] == pytest.approx(3.0)


def test_seed_dependency_weight_halves_each_step():
    assert seed_dependency_weight(0) == 1.0
    assert seed_dependency_weight(1) == 0.5
    assert seed_dependency_weight(2) == 0.25
    with pytest.raises(ValueError):
        seed_dependency_weight(-1)
