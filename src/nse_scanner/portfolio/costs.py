"""Applies a configurable cost profile to a single round-trip trade (PART 50).

Separates gross return from net-of-cost return under different assumptions:
    gross            : no costs at all
    statutory_net    : STT + exchange + GST + stamp duty only (regulatory/exchange, not broker-specific)
    broker_net       : statutory + the configured brokerage + slippage (the "realistic" default)
    stress_net       : broker_net with slippage doubled, for a conservative sensitivity check

Nothing here hard-codes "the" real trading cost (PART 50) — every component comes from
config.CostProfileConfig, which is itself swappable per broker.
"""

from __future__ import annotations

from dataclasses import dataclass

from nse_scanner.config import CostProfileConfig


@dataclass(frozen=True)
class CostBreakdown:
    gross_return_pct: float
    statutory_net_return_pct: float
    broker_net_return_pct: float
    stress_net_return_pct: float


def _statutory_cost_pct(cfg: CostProfileConfig) -> float:
    exch = cfg.exchange_buy_pct + cfg.exchange_sell_pct
    gst = exch * cfg.gst_pct
    return cfg.stt_buy_pct + cfg.stt_sell_pct + exch + gst + cfg.stamp_duty_buy_pct + cfg.stamp_duty_sell_pct


def _broker_cost_pct(cfg: CostProfileConfig, slippage_multiplier: float = 1.0) -> float:
    brokerage = cfg.brokerage_buy_pct + cfg.brokerage_sell_pct
    slippage = (cfg.slippage_buy_pct + cfg.slippage_sell_pct) * slippage_multiplier
    gst_on_brokerage = brokerage * cfg.gst_pct
    return _statutory_cost_pct(cfg) + brokerage + gst_on_brokerage + slippage


def apply_costs(gross_return_pct: float, cfg: CostProfileConfig) -> CostBreakdown:
    statutory = _statutory_cost_pct(cfg)
    broker = _broker_cost_pct(cfg, slippage_multiplier=1.0)
    stress = _broker_cost_pct(cfg, slippage_multiplier=2.0)
    return CostBreakdown(
        gross_return_pct=gross_return_pct,
        statutory_net_return_pct=gross_return_pct - statutory,
        broker_net_return_pct=gross_return_pct - broker,
        stress_net_return_pct=gross_return_pct - stress,
    )
