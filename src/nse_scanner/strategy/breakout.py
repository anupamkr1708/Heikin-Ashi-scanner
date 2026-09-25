"""Breakout state engine (PART 21).

Not every day above the Upper Bollinger Band is treated as a brand-new breakout.

    FRESH_BREAKOUT : prev Close <= prev Upper_BB AND curr Close > curr Upper_BB
    CONTINUATION   : prev Close >  prev Upper_BB AND curr Close > curr Upper_BB
    FAILED_BREAKOUT: prev Close >  prev Upper_BB AND curr Close <= curr Upper_BB
    NO_BREAKOUT    : anything else

Days_Above_Upper_BB: consecutive-day counter of Close > Upper_BB ending at the current row
(0 when not currently above). This module never alters the base BB+HA signal — it only
classifies context around it.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

FRESH_BREAKOUT = "FRESH_BREAKOUT"
CONTINUATION = "CONTINUATION"
FAILED_BREAKOUT = "FAILED_BREAKOUT"
NO_BREAKOUT = "NO_BREAKOUT"

EXT_NOT_ABOVE = "NOT_ABOVE_BAND"
EXT_CONTROLLED = "CONTROLLED"
EXT_STRONG = "STRONG"
EXT_EXTENDED = "EXTENDED"
EXT_EXCESSIVE = "EXCESSIVE"


def calculate_breakout_state(close: pd.Series, upper: pd.Series, max_bb_overshoot_pct: float = 4.0) -> pd.DataFrame:
    bo = pd.DataFrame(index=close.index)

    is_above = close > upper
    prev_close = close.shift(1)
    prev_upper = upper.shift(1)
    was_above = prev_close > prev_upper

    conditions = [
        (~was_above) & is_above,
        was_above & is_above,
        was_above & (~is_above),
    ]
    choices = [FRESH_BREAKOUT, CONTINUATION, FAILED_BREAKOUT]
    bo["Breakout_Type"] = np.select(conditions, choices, default=NO_BREAKOUT)

    # Consecutive days above the upper band, ending at each row.
    streak_id = (~is_above).cumsum()
    bo["Days_Above_Upper_BB"] = is_above.groupby(streak_id).cumsum().where(is_above, 0).astype(int)

    overshoot_pct = (close - upper) / upper * 100.0
    ext_conditions = [
        ~is_above,
        overshoot_pct < 1.0,
        overshoot_pct < 2.0,
        overshoot_pct <= max_bb_overshoot_pct,
    ]
    ext_choices = [EXT_NOT_ABOVE, EXT_CONTROLLED, EXT_STRONG, EXT_EXTENDED]
    bo["Extension_Class"] = np.select(ext_conditions, ext_choices, default=EXT_EXCESSIVE)

    return bo
