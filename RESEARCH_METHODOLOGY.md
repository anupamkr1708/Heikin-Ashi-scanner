# Research methodology

This document describes how `scripts/run_research.py` and the `research/` package work, and — as
important — what they do not claim.

## Modes (PART 28)

1. **LIVE/EOD SCANNER** (`scripts/run_daily.py`, `pipeline/scan.py`) — today's candidates only.
2. **HISTORICAL EVENT STUDY** (`scripts/run_research.py`, this document) — forward-return
   statistics over past signals.
3. **WALK-FORWARD RESEARCH** (`research/walk_forward.py`) — rolling train/test windows; each
   window's out-of-sample metrics are stored separately, never averaged into one number.
4. **PORTFOLIO BACKTEST** (`portfolio/backtest.py`) — capital-constrained simulation, built and
   validated only on top of a correct event study, per the specification's phase ordering.

These outputs are never mixed. An event-study return sequence is not a portfolio equity curve; a
single train/test split is not walk-forward; see AUDIT.md for the specific legacy bugs this
separation fixes.

## Event definition

`research/events.py` reports two different things and a report using this research should always
say which one it means:

- **`ALL_SIGNAL_DAYS`** — every row where the mandatory baseline condition holds, including every
  day of a multi-day CONTINUATION streak.
- **`INDEPENDENT_EVENTS`** — a de-correlated subset: a new independent event starts at a
  FRESH_BREAKOUT transition, or after `independent_event_cooldown_days` (default 5) trading rows
  have elapsed since the last selected event for that security.

`INDEPENDENT_EVENTS` is always `<= ALL_SIGNAL_DAYS` in count (tested,
`tests/unit/test_events_independence.py`). Quoting `ALL_SIGNAL_DAYS` as your sample size inflates
N and understates dependence between adjacent observations.

## Entry timing

Default: **next trading session's OPEN** after the signal (`config.ResearchConfig.entry_price_method
= "next_open"`). The signal-day close is never used as an executable price. `next_close` is
supported and equally explicit. See `research/forward_returns.py::resolve_entry`.

## MFE / MAE

Computed strictly from the entry bar forward. For `next_open` entry, the entry bar's own
intraday high/low is included (the position existed intraday from the open). For `next_close`
entry, it is excluded (the position didn't exist until that bar's close). This distinction is
directly tested (`tests/unit/test_entry_and_excursion.py`) because getting it backward is a
subtle, easy-to-miss look-ahead bug. Aggregates are reported as `Mean/Median/P10/P90`, never as
if a mean were itself "the" MFE.

## Statistical discipline (PART 52)

- **Multiple testing / data snooping** — the sensitivity grid (`research/sensitivity.py`) reports
  every cell; nothing auto-selects a "best" combination. Look for plateaus of similar performance
  across neighboring parameter values, not a single peak.
- **Dependence** — `research/statistics.py::bootstrap_ci_clustered` resamples whole day-clusters
  or whole stock-clusters, not individual rows, because stock-day observations are not
  independent (shared market moves, shared stock-specific momentum). `cluster_summary` reports
  signals-per-day and signals-per-stock alongside any confidence interval so a reader can judge
  how concentrated the sample is.
- **Survivorship bias** — see `research/survivorship.py`. This repository has no point-in-time
  NIFTY 200 membership feed, so research runs in `CURRENT_UNIVERSE_HISTORICAL_SIMULATION` mode
  and `label_current_universe_mode()`'s disclosure text is meant to be surfaced in every research
  report, not buried in a footnote.
- **Transaction costs** — `portfolio/costs.py::apply_costs` reports gross, statutory-net,
  broker-net, and a doubled-slippage "stress" net return side by side. No single number is
  presented as "the" real return.
- **Look-ahead** — `tests/unit/test_lookahead.py` directly tests that appending future bars never
  changes a historical Bollinger/Heikin-Ashi/ATR/breakout-state value.

## What this research tooling does NOT claim

- It does not claim the BB+HA baseline is profitable. No number in this repository should be
  read as investment advice.
- `Research_Heuristic_Score` (used only in the live scanner's ranking, not in the research
  module) is explicitly not a probability, not an expected return, and not statistically
  validated — see `strategy/ranking.py`.
- A "robust" or "validated" strategy claim would require, per PART 52: persistence across time,
  contribution from multiple stocks and sectors, parameter stability under perturbation, survival
  of realistic transaction costs, genuine out-of-sample (walk-forward) persistence, no look-ahead,
  controlled survivorship, and accounting for event dependence. This repository provides the
  tooling to check each of those; it does not assert any of them are satisfied.

## Explicit future milestones (not implemented — stated, not faked)

Per the instruction that a statistical method too sophisticated to implement correctly right now
should be documented as a future milestone rather than shipped as a misleading number:

- **Data-snooping / multiple-testing correction.** No White's Reality Check, no deflated Sharpe
  ratio, no probability-of-backtest-overfitting (PBO) statistic exists in this repository. The
  sensitivity grid (`research/sensitivity.py`) and walk-forward windows
  (`research/walk_forward.py`) give the raw material such a correction would need (results across
  many parameter combinations / windows), but nothing here currently corrects for having looked
  at that many combinations. Treat every individual result as provisional until this exists.
- **True corporate-action-adjusted continuous research series.** `data/corporate_actions.py`
  flags large gaps and can reconcile a flagged gap against a supplied CA record, but there is no
  real CA feed wired up, so no genuinely-adjusted `research_open/high/low/close` series (as
  distinct from the raw NSE series or yfinance's own black-box adjustment) exists. See
  `tests/unit/test_corporate_action_synthetic.py` for what current behavior actually is,
  including the honest characterization of what it can't yet catch.
- **Real point-in-time NIFTY 200 membership.** `research/survivorship.py::MODE_B` is a validated
  data contract with no data source feeding it. Every research run in this repository operates in
  `MODE_A` (today's constituents applied backward) and discloses the resulting survivorship bias.
