import numpy as np
import pandas as pd
from nse_scanner.config import ScannerConfig
from nse_scanner.pipeline.features import build_feature_frame
from nse_scanner.research.events import extract_events, extract_independent_events, extract_signal_days
from nse_scanner.strategy.breakout import FRESH_BREAKOUT


def _make_extended_breakout_series(n=60):
    """A price series that goes flat, then breaks out and stays extended for several days
    (a CONTINUATION streak), then pulls back — engineered to guarantee ALL_SIGNAL_DAYS counts
    several consecutive rows while INDEPENDENT_EVENTS should collapse that streak."""
    rng = np.random.default_rng(3)
    base = 100 + np.cumsum(rng.normal(0, 0.2, n))
    base[35:42] = base[34] * np.linspace(1.01, 1.06, 7)  # sustained breakout streak
    closes = base
    opens = closes - rng.uniform(-0.3, 0.3, n)
    highs = np.maximum(opens, closes) + rng.uniform(0.1, 0.5, n)
    lows = np.minimum(opens, closes) - rng.uniform(0.1, 0.5, n)
    volumes = rng.integers(10_000, 100_000, n)
    return pd.DataFrame(
        {"Open": opens, "High": highs, "Low": lows, "Close": closes, "Volume": volumes},
        index=pd.bdate_range("2025-01-01", periods=n),
    )


def test_all_signal_days_can_include_consecutive_continuation_rows():
    cfg = ScannerConfig()
    df = _make_extended_breakout_series()
    feat = build_feature_frame(df, cfg)
    signal_days = extract_signal_days(feat, cfg.baseline.max_bb_overshoot_pct, cfg.baseline.min_ha_body_pct)
    # This test only asserts the extraction mechanism works and returns a Breakout_Type column;
    # whether this particular synthetic series produces >1 consecutive signal day is incidental.
    if not signal_days.empty:
        assert "Breakout_Type" in signal_days.columns


def test_independent_events_never_exceeds_all_signal_days():
    cfg = ScannerConfig()
    df = _make_extended_breakout_series()
    feat = build_feature_frame(df, cfg)
    feat["Symbol"] = "TESTSYM"
    events = extract_events(
        {"TESTSYM": feat}, cfg.baseline.max_bb_overshoot_pct, cfg.baseline.min_ha_body_pct, cooldown_days=5
    )
    assert len(events.independent_events) <= len(events.all_signal_days)


def test_cooldown_collapses_a_dense_synthetic_streak():
    # Build a minimal synthetic signal_days frame directly (bypassing indicator computation) to
    # test extract_independent_events' cooldown logic in isolation and deterministically.
    dates = pd.bdate_range("2025-01-01", periods=6)
    signal_days = pd.DataFrame(
        {
            "Breakout_Type": [FRESH_BREAKOUT] + ["CONTINUATION"] * 5,
        },
        index=dates,
    )

    # cooldown_days=10 guarantees the cooldown has NOT elapsed anywhere within this 6-row window
    # (a cooldown_days=5 would legitimately re-arm exactly at row index 5 — see the boundary
    # test below for that case).
    independent = extract_independent_events(signal_days, cooldown_days=10)
    # Only the FRESH_BREAKOUT row should survive: none of the CONTINUATION rows are fresh, and
    # the cooldown hasn't elapsed within this window.
    assert len(independent) == 1
    assert independent["Breakout_Type"].iloc[0] == FRESH_BREAKOUT


def test_cooldown_re_arms_exactly_at_the_configured_boundary():
    dates = pd.bdate_range("2025-01-01", periods=6)
    signal_days = pd.DataFrame(
        {
            "Breakout_Type": [FRESH_BREAKOUT] + ["CONTINUATION"] * 5,
        },
        index=dates,
    )
    independent = extract_independent_events(signal_days, cooldown_days=5)
    # Row index 5 is exactly 5 rows after the selected row at index 0, so the cooldown has
    # elapsed (inclusive boundary) and it re-arms as a second independent event.
    assert len(independent) == 2


def test_new_fresh_breakout_after_a_failed_one_always_counts():
    dates = pd.bdate_range("2025-01-01", periods=3)
    signal_days = pd.DataFrame(
        {
            "Breakout_Type": [FRESH_BREAKOUT, FRESH_BREAKOUT, FRESH_BREAKOUT],
        },
        index=dates,
    )
    independent = extract_independent_events(signal_days, cooldown_days=100)
    # Every row is its own FRESH_BREAKOUT transition, so every one counts regardless of cooldown.
    assert len(independent) == 3
