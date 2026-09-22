"""Portfolio-level backtest engine (PART 34).

**Audit note (BUG 10):** a sequence of independent event returns strung end-to-end is NOT a
portfolio drawdown — it ignores capital constraints, concurrent-position limits, and the fact
that real trades overlap in time and compete for the same capital. This module is the genuine
article: it takes a list of `TradeCandidate`s (produced by the research/events + forward_returns
pipeline), enforces `max_concurrent_positions` / sector exposure / portfolio exposure caps when
deciding which candidates actually get filled, marks the whole book to market daily using each
open position's own price series, and only THEN derives an equity curve and drawdown from that
day-by-day state.

This module is intentionally only used once event-level research (research/events.py,
research/forward_returns.py) is validated — running a portfolio simulation on top of a broken
event definition just produces a differently-broken result (PART 28's phase ordering).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd

from nse_scanner.config import CostProfileConfig, RiskConfig
from nse_scanner.portfolio.costs import apply_costs


@dataclass(frozen=True)
class TradeCandidate:
    symbol: str
    sector: str | None
    signal_date: date
    entry_date: date
    entry_price: float
    stop_price: float
    exit_date: date
    exit_price: float
    price_series: pd.Series  # Close, indexed by date, spanning at least [entry_date, exit_date]


@dataclass
class OpenPosition:
    trade: TradeCandidate
    shares: int
    entry_value: float


@dataclass
class PortfolioResult:
    equity_curve: pd.Series
    daily_returns: pd.Series
    trades_taken: list[TradeCandidate]
    trades_skipped_capacity: list[TradeCandidate]
    metrics: dict[str, float]


def run_portfolio_backtest(candidates: list[TradeCandidate], initial_capital: float,
                            risk_cfg: RiskConfig, cost_cfg: CostProfileConfig) -> PortfolioResult:
    """Single-pass, chronological, capital-constrained simulation.

    Candidates are processed in entry-date order. A candidate is skipped (not filled) if taking
    it would exceed `max_concurrent_positions`, `max_sector_exposure_pct`, or
    `max_portfolio_exposure_pct` at its entry date, given positions already open at that time.
    This is what makes it a genuine portfolio backtest rather than an event-return sequence.
    """
    ordered = sorted(candidates, key=lambda c: c.entry_date)
    if not ordered:
        return PortfolioResult(pd.Series(dtype=float), pd.Series(dtype=float), [], [], {})

    all_dates = sorted(set().union(*[set(c.price_series.index) for c in ordered]))
    cash = initial_capital
    open_positions: list[OpenPosition] = []
    closed_positions: list[tuple[OpenPosition, float]] = []  # (position, exit_value)
    trades_taken: list[TradeCandidate] = []
    trades_skipped: list[TradeCandidate] = []

    equity_by_date: dict[date, float] = {}
    candidates_by_entry_date: dict[date, list[TradeCandidate]] = {}
    for c in ordered:
        candidates_by_entry_date.setdefault(c.entry_date, []).append(c)

    for d in all_dates:
        # 1) close out any positions whose exit_date is today
        still_open: list[OpenPosition] = []
        for pos in open_positions:
            if pos.trade.exit_date == d:
                exit_value = pos.shares * pos.trade.exit_price
                cash += exit_value
                closed_positions.append((pos, exit_value))
            else:
                still_open.append(pos)
        open_positions = still_open

        # 2) attempt to fill today's new candidates, subject to portfolio limits
        for cand in candidates_by_entry_date.get(d, []):
            if len(open_positions) >= risk_cfg.max_concurrent_positions:
                trades_skipped.append(cand)
                continue

            current_exposure = sum(p.entry_value for p in open_positions)
            portfolio_value_now = cash + current_exposure
            sector_exposure = sum(
                p.entry_value for p in open_positions if p.trade.sector == cand.sector
            )

            per_position_budget = portfolio_value_now / risk_cfg.max_concurrent_positions
            shares = int(per_position_budget / cand.entry_price) if cand.entry_price > 0 else 0
            entry_value = shares * cand.entry_price

            if shares <= 0:
                trades_skipped.append(cand)
                continue
            if (current_exposure + entry_value) / portfolio_value_now * 100.0 > risk_cfg.max_portfolio_exposure_pct:
                trades_skipped.append(cand)
                continue
            if (sector_exposure + entry_value) / portfolio_value_now * 100.0 > risk_cfg.max_sector_exposure_pct:
                trades_skipped.append(cand)
                continue
            if entry_value > cash:
                trades_skipped.append(cand)
                continue

            cash -= entry_value
            open_positions.append(OpenPosition(trade=cand, shares=shares, entry_value=entry_value))
            trades_taken.append(cand)

        # 3) mark remaining open positions to market using today's close, if available
        mtm = 0.0
        for pos in open_positions:
            price_today = pos.trade.price_series.get(d)
            if price_today is None or (isinstance(price_today, float) and np.isnan(price_today)):
                mtm += pos.entry_value  # no update available today — hold last known value
            else:
                mtm += pos.shares * float(price_today)

        equity_by_date[d] = cash + mtm

    equity_curve = pd.Series(equity_by_date).sort_index()
    daily_returns = equity_curve.pct_change().dropna()

    metrics = _compute_metrics(equity_curve, daily_returns, closed_positions, cost_cfg, initial_capital)

    return PortfolioResult(
        equity_curve=equity_curve, daily_returns=daily_returns,
        trades_taken=trades_taken, trades_skipped_capacity=trades_skipped, metrics=metrics,
    )


def _compute_metrics(equity_curve: pd.Series, daily_returns: pd.Series,
                      closed_positions: list[tuple[OpenPosition, float]], cost_cfg: CostProfileConfig,
                      initial_capital: float) -> dict[str, float]:
    if equity_curve.empty:
        return {}

    n_days = len(equity_curve)
    total_return = equity_curve.iloc[-1] / initial_capital - 1.0
    years = max(n_days / 252.0, 1e-9)
    cagr = (1.0 + total_return) ** (1.0 / years) - 1.0

    running_max = equity_curve.cummax()
    drawdown = equity_curve / running_max - 1.0
    max_drawdown = float(drawdown.min())

    daily_std = daily_returns.std(ddof=1) if len(daily_returns) > 1 else 0.0
    sharpe = float((daily_returns.mean() / daily_std) * np.sqrt(252)) if daily_std > 0 else float("nan")

    downside = daily_returns[daily_returns < 0]
    downside_std = downside.std(ddof=1) if len(downside) > 1 else 0.0
    sortino = float((daily_returns.mean() / downside_std) * np.sqrt(252)) if downside_std > 0 else float("nan")

    calmar = float(cagr / abs(max_drawdown)) if max_drawdown < 0 else float("nan")

    trade_returns_gross = [
        (exit_value - pos.entry_value) / pos.entry_value * 100.0 for pos, exit_value in closed_positions
    ]
    trade_returns_net = [apply_costs(r, cost_cfg).broker_net_return_pct for r in trade_returns_gross]

    wins = [r for r in trade_returns_net if r > 0]
    losses = [r for r in trade_returns_net if r < 0]
    profit_factor = (sum(wins) / abs(sum(losses))) if losses else (float("inf") if wins else float("nan"))

    longest_losing_streak = _longest_losing_streak(trade_returns_net)
    holding_periods = [
        (pos.trade.exit_date - pos.trade.entry_date).days for pos, _ in closed_positions
    ]

    return {
        "CAGR": cagr,
        "Max_Drawdown": max_drawdown,
        "Sharpe": sharpe,
        "Sortino": sortino,
        "Calmar": calmar,
        "Num_Closed_Trades": len(closed_positions),
        "Win_Rate_Net": (len(wins) / len(trade_returns_net)) if trade_returns_net else float("nan"),
        "Profit_Factor_Net": profit_factor,
        "Avg_Holding_Days": float(np.mean(holding_periods)) if holding_periods else float("nan"),
        "Longest_Losing_Streak": longest_losing_streak,
        "Turnover_Trades": len(closed_positions),
    }


def _longest_losing_streak(trade_returns: list[float]) -> int:
    longest = current = 0
    for r in trade_returns:
        if r < 0:
            current += 1
            longest = max(longest, current)
        else:
            current = 0
    return longest
