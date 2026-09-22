"""Synthetic corporate-action scenarios (PHASE 9).

This repository has no real corporate-action feed (see module docstring in
data/corporate_actions.py) — these tests prove what the gap-flagging/reconciliation MECHANISM
actually does, and explicitly characterize what it does NOT do, rather than claiming a capability
that isn't there.
"""


import numpy as np
import pandas as pd
from nse_scanner.config import ScannerConfig
from nse_scanner.data.corporate_actions import (
    CorporateActionRecord,
    flag_large_gaps,
    reconcile_with_ca_calendar,
)
from nse_scanner.strategy.bb_ha import evaluate_mandatory


def _stable_series_with_one_jump(jump_pct: float, n: int = 40, jump_at: int = 30) -> pd.DataFrame:
    """A flat, low-volatility series (so the Bollinger bands stay tight) with exactly one
    overnight jump of `jump_pct` at `jump_at`, simulating an unadjusted corporate-action-driven
    discontinuity (e.g. a reverse split roughly doubling the nominal price, or a special dividend
    knocking a few percent off the ex-date open)."""
    dates = pd.bdate_range("2026-01-01", periods=n)
    base = 100.0
    closes = np.full(n, base) + np.random.default_rng(0).normal(0, 0.3, n)
    closes[jump_at:] = closes[jump_at:] * (1 + jump_pct / 100.0)
    opens = closes.copy()
    opens[jump_at] = closes[jump_at - 1] * (1 + jump_pct / 100.0)  # the actual overnight gap
    highs = np.maximum(opens, closes) + 0.5
    lows = np.minimum(opens, closes) - 0.5
    return pd.DataFrame({"Open": opens, "High": highs, "Low": lows, "Close": closes}, index=dates)


def test_large_reverse_split_like_jump_is_flagged_as_a_large_gap():
    df = _stable_series_with_one_jump(jump_pct=100.0)  # a reverse-split-sized overnight doubling
    flagged = flag_large_gaps(df, gap_threshold_pct=10.0)
    jump_row = flagged.iloc[30]
    assert jump_row["large_gap_flag"] == True  # noqa: E712 - explicit bool comparison for clarity
    assert jump_row["price_discontinuity_flag"] == True  # noqa: E712
    assert jump_row["corporate_action_flag"] == False  # noqa: E712 - no CA record supplied yet


def test_reconciling_with_a_matching_ca_record_reclassifies_the_gap():
    df = _stable_series_with_one_jump(jump_pct=100.0)
    flagged = flag_large_gaps(df, gap_threshold_pct=10.0)
    jump_date = flagged.index[30].date()

    ca_record = CorporateActionRecord(
        isin="INE_TEST_0001", nse_symbol="TESTCA", action_type="SPLIT", ex_date=jump_date,
        ratio_or_amount="1:2 (reverse split)", source="TEST_FIXTURE", retrieved_at="2026-01-01T00:00:00Z",
    )
    reconciled = reconcile_with_ca_calendar(flagged, [ca_record])

    jump_row = reconciled.loc[reconciled.index.date == jump_date].iloc[0]
    assert jump_row["corporate_action_flag"] == True  # noqa: E712
    # Once explained by a real CA record, it's no longer an UNEXPLAINED discontinuity.
    assert jump_row["price_discontinuity_flag"] == False  # noqa: E712


def test_the_mandatory_overshoot_ceiling_rejects_a_large_corporate_action_jump():
    """A useful, TRUE finding: the immutable baseline's `0 < BB_Overshoot_Pct <= 4.0` ceiling
    already rejects a large (e.g. 100%) corporate-action-sized jump — real splits/bonuses move
    price far more than 4%, so the mandatory condition's own tight bound is incidental protection
    against the most dramatic corporate-action artifacts, even with zero CA data."""
    close = 200.0   # doubled from ~100
    bb_upper = 101.0  # a tight band, unaware of the jump
    overshoot_pct = (close - bb_upper) / bb_upper * 100.0
    assert overshoot_pct > 90  # far outside the mandatory window
    result = evaluate_mandatory(close, bb_upper, overshoot_pct, ha_body_pct=5.0,
                                 max_bb_overshoot_pct=4.0, min_ha_body_pct=1.0)
    assert result.bb_size_ok is False
    assert result.mandatory_pass is False


def test_honest_limitation_a_moderate_ca_jump_can_still_land_inside_the_mandatory_window():
    """The explicit, documented LIMITATION (not a false claim of robustness): a SMALL corporate
    action — e.g. a special-dividend ex-date drop, or a modest bonus ratio — can produce a price
    change small enough to land INSIDE the mandatory overshoot window, and WITHOUT a real CA feed
    this repository cannot currently distinguish that from organic price action. This test
    characterizes that known gap rather than pretending it's solved — see
    data/corporate_actions.py's module docstring and README "Known limitations"."""
    cfg = ScannerConfig()
    # A modest positive jump (comparable to a typical bonus/rights adjustment residual) landing
    # within the mandatory (0, 4] window and clearing the HA body threshold too.
    close = 102.5
    bb_upper = 100.0
    overshoot_pct = (close - bb_upper) / bb_upper * 100.0
    assert 0 < overshoot_pct <= cfg.baseline.max_bb_overshoot_pct

    result = evaluate_mandatory(close, bb_upper, overshoot_pct, ha_body_pct=1.5,
                                 max_bb_overshoot_pct=cfg.baseline.max_bb_overshoot_pct,
                                 min_ha_body_pct=cfg.baseline.min_ha_body_pct)
    # This DOES currently evaluate as a pass — documenting, not silently hiding, the limitation.
    assert result.mandatory_pass is True
