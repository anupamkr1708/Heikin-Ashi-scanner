"""Strategy variant registry.

The base strategy (``bb_ha_v1_base``) is immutable. Every additional idea from PART 37 / PART 72
("BB+HA+ATR context", "+volume", "+relative strength", ...) is registered here as a distinct,
named, independently-testable variant — never by editing strategy/bb_ha.py in place.

A variant is just a *filter preset* (which optional filters in config.OptionalFilterConfig are
turned on) plus metadata. The mandatory arithmetic underneath every variant is always exactly
strategy.bb_ha.evaluate_mandatory.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from nse_scanner.config import OptionalFilterConfig


@dataclass(frozen=True)
class StrategyVariant:
    strategy_id: str
    description: str
    filters: OptionalFilterConfig


_BASE_FILTERS = OptionalFilterConfig()

REGISTRY: dict[str, StrategyVariant] = {
    "bb_ha_v1_base": StrategyVariant(
        strategy_id="bb_ha_v1_base",
        description="Immutable baseline: Close>Upper_BB, 0<overshoot<=4%, HA body>=1%. No optional filters.",
        filters=_BASE_FILTERS,
    ),
    "bb_ha_v1_atr_v1": StrategyVariant(
        strategy_id="bb_ha_v1_atr_v1",
        description="Baseline + ATR-normalized-overshoot confirmation filter (RESEARCH HYPOTHESIS H3).",
        filters=replace(_BASE_FILTERS, use_atr_filter=True),
    ),
    "bb_ha_v1_volume_v1": StrategyVariant(
        strategy_id="bb_ha_v1_volume_v1",
        description="Baseline + volume-ratio confirmation filter (RESEARCH HYPOTHESIS H8).",
        filters=replace(_BASE_FILTERS, use_volume_filter=True),
    ),
    "bb_ha_v1_rs_v1": StrategyVariant(
        strategy_id="bb_ha_v1_rs_v1",
        description="Baseline + positive relative-strength filter (RESEARCH HYPOTHESIS H6).",
        filters=replace(_BASE_FILTERS, use_relative_strength_filter=True),
    ),
    "bb_ha_v1_regime_v1": StrategyVariant(
        strategy_id="bb_ha_v1_regime_v1",
        description="Baseline + BULL market-regime filter (RESEARCH HYPOTHESIS H7).",
        filters=replace(_BASE_FILTERS, use_market_regime_filter=True),
    ),
    "bb_ha_v1_candle_v1": StrategyVariant(
        strategy_id="bb_ha_v1_candle_v1",
        description="Baseline + candle-quality (CLV) filter (RESEARCH HYPOTHESIS H9).",
        filters=replace(_BASE_FILTERS, use_candle_quality_filter=True),
    ),
    "bb_ha_v1_fresh_only_v1": StrategyVariant(
        strategy_id="bb_ha_v1_fresh_only_v1",
        description="Baseline + fresh-breakout-only filter (RESEARCH HYPOTHESIS H2).",
        filters=replace(_BASE_FILTERS, use_fresh_breakout_only=True),
    ),
}


def get_variant(strategy_id: str) -> StrategyVariant:
    if strategy_id not in REGISTRY:
        raise KeyError(
            f"Unknown strategy_id '{strategy_id}'. Registered variants: {sorted(REGISTRY)}"
        )
    return REGISTRY[strategy_id]
