from datetime import date

import pytest
from nse_scanner.config import ScannerConfig, load_config
from nse_scanner.exceptions import ConfigurationError
from nse_scanner.research.walk_forward import generate_walk_forward_windows


def test_default_config_validates():
    cfg = ScannerConfig()
    cfg.validate()  # no raise
    assert cfg.filters.use_trend_filter is False
    assert cfg.filters.use_volume_filter is False
    assert cfg.filters.use_market_regime_filter is False


def test_baseline_defaults_match_immutable_spec():
    cfg = ScannerConfig()
    assert cfg.baseline.bb_period == 20
    assert cfg.baseline.bb_std_mult == 2.0
    assert cfg.baseline.bb_ddof == 1
    assert cfg.baseline.max_bb_overshoot_pct == 4.0
    assert cfg.baseline.min_ha_body_pct == 1.0
    assert cfg.baseline.strategy_id == "bb_ha_v1_base"


def test_invalid_universe_scope_rejected():
    cfg = ScannerConfig()
    bad = cfg
    from dataclasses import replace

    bad = replace(cfg, universe=replace(cfg.universe, universe_scope="NOT_A_REAL_UNIVERSE"))
    with pytest.raises(ConfigurationError):
        bad.validate()


def test_load_config_with_cli_overrides(tmp_path):
    cfg = load_config(yaml_path=None, cli_overrides={"filters": {"use_trend_filter": True}})
    assert cfg.filters.use_trend_filter is True
    assert cfg.filters.use_volume_filter is False  # untouched fields keep their default


def test_load_config_unknown_key_raises():
    with pytest.raises(ConfigurationError):
        load_config(yaml_path=None, cli_overrides={"baseline": {"not_a_real_field": 1}})


def test_walk_forward_windows_roll_forward_and_never_overlap_train_into_next_test_start():
    windows = generate_walk_forward_windows(date(2015, 1, 1), date(2024, 1, 1), train_years=3, test_months=6)
    assert len(windows) > 1
    for w in windows:
        assert w.train_end < w.test_start
        assert w.test_start <= w.test_end
    # Rolling forward: each window's train_start should be >= the previous window's train_start
    for a, b in zip(windows, windows[1:], strict=False):
        assert b.train_start > a.train_start
        assert b.window_id == a.window_id + 1


def test_walk_forward_never_extends_past_data_end():
    data_end = date(2020, 6, 30)
    windows = generate_walk_forward_windows(date(2015, 1, 1), data_end, train_years=3, test_months=6)
    for w in windows:
        assert w.test_end <= data_end
