"""Risk-based position sizing (PART 35).

Kept strictly separate from strategy/signal generation (PART 73's SIGNAL vs CONTEXT vs FILTER
distinction) — nothing in strategy/ imports this module, and nothing here can suppress a signal.
"""

from __future__ import annotations

from dataclasses import dataclass

from nse_scanner.config import RiskConfig


@dataclass(frozen=True)
class PositionSizeResult:
    shares: int
    risk_capital: float
    position_value: float
    capped_by: str | None   # None | "min_position_size" | "max_position_size" | "liquidity"


def calculate_position_size(capital: float, entry_price: float, stop_price: float, cfg: RiskConfig,
                             avg_daily_dollar_volume: float | None = None,
                             max_pct_of_adv: float = 0.05) -> PositionSizeResult:
    if entry_price <= stop_price:
        raise ValueError("entry_price must be > stop_price for a long position")

    risk_capital = capital * (cfg.risk_per_trade_pct / 100.0)
    per_share_risk = entry_price - stop_price
    raw_shares = risk_capital / per_share_risk

    shares = int(raw_shares)
    capped_by: str | None = None

    if cfg.max_position_size is not None and shares > cfg.max_position_size:
        shares = cfg.max_position_size
        capped_by = "max_position_size"

    if avg_daily_dollar_volume is not None:
        liquidity_cap_shares = int((avg_daily_dollar_volume * max_pct_of_adv) / entry_price)
        if shares > liquidity_cap_shares:
            shares = liquidity_cap_shares
            capped_by = "liquidity"

    if shares < cfg.min_position_size:
        shares = 0  # cannot meet minimum size within the risk budget — do not silently round up

    return PositionSizeResult(
        shares=shares, risk_capital=risk_capital, position_value=shares * entry_price, capped_by=capped_by,
    )
