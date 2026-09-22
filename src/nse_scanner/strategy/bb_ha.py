"""Baseline strategy: ``bb_ha_v1_base``.

============================================================================================
THIS IS THE NON-NEGOTIABLE BASELINE. DO NOT SILENTLY MODIFY THIS FILE'S MANDATORY ARITHMETIC.
============================================================================================

    Condition 1:  Close > Upper_BB
    Condition 2:  0 < BB_Overshoot_Pct <= MAX_BB_OVERSHOOT_PCT   (default 4.0)
    Condition 3:  HA_Body_Pct >= MIN_HA_BODY_PCT                 (default 1.0)

    primary_signal = Condition1 AND Condition2 AND Condition3

Any proposed change must be introduced as a new, separately named strategy variant
(PART 1 / PART 72 — see strategy/registry.py), never as an in-place edit here. The default
config values live in config.BaselineStrategyConfig; this module receives them as explicit
parameters and never reads global state.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

STRATEGY_ID = "bb_ha_v1_base"


@dataclass(frozen=True)
class MandatoryResult:
    bb_breakout: bool
    bb_size_ok: bool
    ha_strength_ok: bool
    mandatory_pass: bool


def evaluate_mandatory(close: float, bb_upper: float, bb_overshoot_pct: float, ha_body_pct: float,
                        max_bb_overshoot_pct: float = 4.0, min_ha_body_pct: float = 1.0) -> MandatoryResult:
    """Evaluate the three mandatory conditions for a single (already-computed) row.

    Takes scalar, already-computed feature values rather than a raw OHLC row so that this
    function has a trivial, exhaustively-testable contract and cannot accidentally depend on
    anything else in a feature row.
    """
    bb_breakout = bool(close > bb_upper)
    bb_size_ok = bool(0 < bb_overshoot_pct <= max_bb_overshoot_pct)
    ha_strength_ok = bool(ha_body_pct >= min_ha_body_pct)
    return MandatoryResult(
        bb_breakout=bb_breakout,
        bb_size_ok=bb_size_ok,
        ha_strength_ok=ha_strength_ok,
        mandatory_pass=bb_breakout and bb_size_ok and ha_strength_ok,
    )


def evaluate_mandatory_vectorized(close: pd.Series, bb_upper: pd.Series, bb_overshoot_pct: pd.Series,
                                   ha_body_pct: pd.Series, max_bb_overshoot_pct: float = 4.0,
                                   min_ha_body_pct: float = 1.0) -> pd.DataFrame:
    """Vectorized form used by the historical event extractor (research/events.py) — must stay
    mathematically identical to `evaluate_mandatory` (tests/unit/test_signal.py asserts this by
    comparing the two on the same synthetic data)."""
    bb_breakout = close > bb_upper
    bb_size_ok = (bb_overshoot_pct > 0) & (bb_overshoot_pct <= max_bb_overshoot_pct)
    ha_strength_ok = ha_body_pct >= min_ha_body_pct
    out = pd.DataFrame(index=close.index)
    out["bb_breakout"] = bb_breakout
    out["bb_size_ok"] = bb_size_ok
    out["ha_strength_ok"] = ha_strength_ok
    out["mandatory_pass"] = bb_breakout & bb_size_ok & ha_strength_ok
    return out
