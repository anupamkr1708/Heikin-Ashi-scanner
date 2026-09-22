import pandas as pd
from nse_scanner.strategy.breakout import (
    CONTINUATION,
    FAILED_BREAKOUT,
    FRESH_BREAKOUT,
    NO_BREAKOUT,
    calculate_breakout_state,
)


def test_breakout_states_hand_traced():
    # upper band constant at 100 for simplicity
    close = pd.Series([95, 101, 102, 99, 103, 90])
    upper = pd.Series([100, 100, 100, 100, 100, 100])

    bo = calculate_breakout_state(close, upper)

    assert bo["Breakout_Type"].iloc[0] == NO_BREAKOUT     # 95 <= 100
    assert bo["Breakout_Type"].iloc[1] == FRESH_BREAKOUT   # prev<=upper, now>upper
    assert bo["Breakout_Type"].iloc[2] == CONTINUATION     # prev>upper, now>upper
    assert bo["Breakout_Type"].iloc[3] == FAILED_BREAKOUT  # prev>upper, now<=upper
    assert bo["Breakout_Type"].iloc[4] == FRESH_BREAKOUT   # prev<=upper, now>upper again
    assert bo["Breakout_Type"].iloc[5] == FAILED_BREAKOUT  # prev>upper, now<=upper


def test_days_above_upper_bb_streak_counter():
    close = pd.Series([95, 101, 102, 103, 99, 105])
    upper = pd.Series([100] * 6)
    bo = calculate_breakout_state(close, upper)
    assert list(bo["Days_Above_Upper_BB"]) == [0, 1, 2, 3, 0, 1]
