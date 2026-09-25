import pytest
from nse_scanner.exceptions import SignalMathError
from nse_scanner.indicators.sanity import check_bollinger_ordering, check_heikin_ashi_ordering, check_pass_row


def test_bollinger_ordering_ok():
    check_bollinger_ordering(bb_upper=110, bb_middle=100, bb_lower=90)  # no raise


def test_bollinger_ordering_violation_raises():
    with pytest.raises(SignalMathError):
        check_bollinger_ordering(bb_upper=90, bb_middle=100, bb_lower=110)


def test_bollinger_ordering_nan_is_skipped():
    check_bollinger_ordering(bb_upper=float("nan"), bb_middle=100, bb_lower=90)  # no raise


def test_ha_ordering_ok():
    check_heikin_ashi_ordering(ha_high=105, ha_low=95, ha_open=100, ha_close=102)


def test_ha_ordering_violation_raises():
    with pytest.raises(SignalMathError):
        check_heikin_ashi_ordering(ha_high=95, ha_low=90, ha_open=100, ha_close=102)


def test_pass_row_validates_mandatory_conditions():
    check_pass_row(
        close=104, bb_upper=100, bb_overshoot_pct=4.0, ha_body_pct=1.5, max_bb_overshoot_pct=4.0, min_ha_body_pct=1.0
    )


def test_pass_row_raises_if_close_not_above_upper():
    with pytest.raises(SignalMathError):
        check_pass_row(
            close=99, bb_upper=100, bb_overshoot_pct=4.0, ha_body_pct=1.5, max_bb_overshoot_pct=4.0, min_ha_body_pct=1.0
        )


def test_pass_row_raises_if_overshoot_out_of_bounds():
    with pytest.raises(SignalMathError):
        check_pass_row(
            close=110,
            bb_upper=100,
            bb_overshoot_pct=10.0,
            ha_body_pct=1.5,
            max_bb_overshoot_pct=4.0,
            min_ha_body_pct=1.0,
        )
