import pandas as pd
from nse_scanner.strategy.bb_ha import evaluate_mandatory, evaluate_mandatory_vectorized


def test_mandatory_all_pass():
    r = evaluate_mandatory(
        close=104.0,
        bb_upper=100.0,
        bb_overshoot_pct=4.0,
        ha_body_pct=1.5,
        max_bb_overshoot_pct=4.0,
        min_ha_body_pct=1.0,
    )
    assert r.bb_breakout is True
    assert r.bb_size_ok is True
    assert r.ha_strength_ok is True
    assert r.mandatory_pass is True


def test_mandatory_fails_when_not_above_band():
    r = evaluate_mandatory(close=99.0, bb_upper=100.0, bb_overshoot_pct=-1.0, ha_body_pct=2.0)
    assert r.bb_breakout is False
    assert r.mandatory_pass is False


def test_mandatory_fails_when_overshoot_too_large():
    # close 4.5% above upper -> overshoot 4.5, exceeds default max 4.0
    r = evaluate_mandatory(close=104.5, bb_upper=100.0, bb_overshoot_pct=4.5, ha_body_pct=2.0)
    assert r.bb_breakout is True
    assert r.bb_size_ok is False
    assert r.mandatory_pass is False


def test_mandatory_boundary_inclusive_at_max_overshoot():
    r = evaluate_mandatory(
        close=104.0,
        bb_upper=100.0,
        bb_overshoot_pct=4.0,
        ha_body_pct=1.0,
        max_bb_overshoot_pct=4.0,
        min_ha_body_pct=1.0,
    )
    assert r.mandatory_pass is True  # <=, not <


def test_mandatory_fails_when_overshoot_exactly_zero():
    # 0 < overshoot is strict — exactly 0 must fail
    r = evaluate_mandatory(close=100.0, bb_upper=100.0, bb_overshoot_pct=0.0, ha_body_pct=2.0)
    assert r.bb_size_ok is False


def test_mandatory_fails_when_ha_body_too_small():
    r = evaluate_mandatory(close=101.0, bb_upper=100.0, bb_overshoot_pct=1.0, ha_body_pct=0.5)
    assert r.ha_strength_ok is False
    assert r.mandatory_pass is False


def test_vectorized_matches_scalar_on_synthetic_data():
    close = pd.Series([99, 101, 104, 104.5, 105])
    upper = pd.Series([100, 100, 100, 100, 100])
    overshoot = (close - upper) / upper * 100.0
    ha_body = pd.Series([2.0, 2.0, 2.0, 2.0, 0.5])

    vec = evaluate_mandatory_vectorized(close, upper, overshoot, ha_body)

    for i in range(len(close)):
        scalar = evaluate_mandatory(close.iloc[i], upper.iloc[i], overshoot.iloc[i], ha_body.iloc[i])
        assert bool(vec["mandatory_pass"].iloc[i]) == scalar.mandatory_pass
        assert bool(vec["bb_breakout"].iloc[i]) == scalar.bb_breakout
        assert bool(vec["bb_size_ok"].iloc[i]) == scalar.bb_size_ok
        assert bool(vec["ha_strength_ok"].iloc[i]) == scalar.ha_strength_ok
