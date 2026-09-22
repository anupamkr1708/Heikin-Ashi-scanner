"""Indicator sanity checks — fail loudly on a logic bug, never silently corrupt output (PART 67).

These are assertions over a single already-computed feature row. They are cheap, and are run on
every row in the scan pipeline (pipeline/scan.py) and in CI-facing unit tests. A failure here
means a bug in the indicator math, not bad market data — bad/missing market data is handled by
data/validation.py instead, before the indicator layer ever sees it.
"""

from __future__ import annotations

import math

from nse_scanner.exceptions import SignalMathError


def _is_nan(x: object) -> bool:
    try:
        return math.isnan(float(x))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return False


def check_bollinger_ordering(bb_upper: float, bb_middle: float, bb_lower: float) -> None:
    if _is_nan(bb_upper) or _is_nan(bb_middle) or _is_nan(bb_lower):
        return
    if not (bb_upper >= bb_middle >= bb_lower):
        raise SignalMathError(
            f"Bollinger ordering violated: upper={bb_upper} middle={bb_middle} lower={bb_lower}"
        )


def check_heikin_ashi_ordering(ha_high: float, ha_low: float, ha_open: float, ha_close: float,
                                tol: float = 1e-9) -> None:
    if ha_high < ha_open - tol:
        raise SignalMathError(f"HA_High ({ha_high}) < HA_Open ({ha_open})")
    if ha_high < ha_close - tol:
        raise SignalMathError(f"HA_High ({ha_high}) < HA_Close ({ha_close})")
    if ha_low > ha_open + tol:
        raise SignalMathError(f"HA_Low ({ha_low}) > HA_Open ({ha_open})")
    if ha_low > ha_close + tol:
        raise SignalMathError(f"HA_Low ({ha_low}) > HA_Close ({ha_close})")


def check_pass_row(close: float, bb_upper: float, bb_overshoot_pct: float, ha_body_pct: float,
                    max_bb_overshoot_pct: float, min_ha_body_pct: float) -> None:
    """Re-derive the mandatory conditions from the row's own stored fields and assert they still
    hold, immediately before that row is ever written into a Signals sheet. Catches any bug
    where a row's Signal flag and its own feature values have drifted apart."""
    if not (close > bb_upper):
        raise SignalMathError("PASS row fails Close > BB_Upper")
    if not (0 < bb_overshoot_pct <= max_bb_overshoot_pct):
        raise SignalMathError("PASS row fails overshoot bound")
    if not (ha_body_pct >= min_ha_body_pct):
        raise SignalMathError("PASS row fails HA body threshold")
