"""Parameter sensitivity analysis (PART 27 / PART 51 H10).

This module runs the strategy across a parameter grid and reports the results — it never picks
a "best" combination or claims one is optimal. Look for plateaus/stability across the grid, not
a single maximum (PART 27's explicit instruction). The caller decides what, if anything, to do
with the table; nothing here mutates config.BaselineStrategyConfig.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import product

import pandas as pd

DEFAULT_BB_PERIOD_GRID = (15, 20, 25)
DEFAULT_BB_STD_MULT_GRID = (1.5, 2.0, 2.5)
DEFAULT_MAX_OVERSHOOT_GRID = (2.0, 3.0, 4.0, 5.0)
DEFAULT_MIN_HA_BODY_GRID = (0.5, 1.0, 1.5, 2.0)


@dataclass(frozen=True)
class SensitivityCell:
    bb_period: int
    bb_std_mult: float
    max_bb_overshoot_pct: float
    min_ha_body_pct: float
    n_events: int
    mean_forward_return: float | None
    win_rate: float | None


def build_parameter_grid(
    bb_periods=DEFAULT_BB_PERIOD_GRID,
    bb_std_mults=DEFAULT_BB_STD_MULT_GRID,
    max_overshoots=DEFAULT_MAX_OVERSHOOT_GRID,
    min_ha_bodies=DEFAULT_MIN_HA_BODY_GRID,
) -> list[dict]:
    """Returns the full cartesian grid as a list of parameter dicts, for the caller to run the
    strategy+research pipeline against, one cell at a time. This module never runs the strategy
    itself — that would couple it to the data/indicator layers unnecessarily (PART 65)."""
    grid = []
    for bp, sm, mo, mh in product(bb_periods, bb_std_mults, max_overshoots, min_ha_bodies):
        grid.append(
            {
                "bb_period": bp,
                "bb_std_mult": sm,
                "max_bb_overshoot_pct": mo,
                "min_ha_body_pct": mh,
            }
        )
    return grid


def sensitivity_results_to_frame(cells: list[SensitivityCell]) -> pd.DataFrame:
    return pd.DataFrame([c.__dict__ for c in cells])
