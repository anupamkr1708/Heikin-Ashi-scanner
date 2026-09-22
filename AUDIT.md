# Audit of the legacy notebook (`Nifty200_BB_HA_Research_Scanner_v2.ipynb`)

This document records what was actually found by reading every cell of the uploaded notebook
before any code was written, per the project specification's PART 89 ("First deliverable before
large rewrite"). It is a factual record, not a summary of intentions.

## Headline finding

The uploaded notebook is already labeled **v2** and turns out to be a fairly well-engineered,
already-audited Colab notebook — it has already fixed several of the issues the original project
brief described as being present in "the current notebook" (hard-fail universe integrity, tiered
history requirements, %B represented as a ratio, an Excel percentage-format fix, honest
"Research Heuristic Score" labeling instead of a fake probability, and explicit survivorship-bias
disclosure for its walk-forward/backtest sections). Real bugs remain, listed below.

## What the notebook actually contains (cell-by-cell summary)

- **Setup/config cell:** a single `Config` object holding `BB_PERIOD=20`, `BB_STD_MULT=2.0`,
  `BB_DDOF=1`, `MAX_BB_OVERSHOOT_PCT=4.0`, `MIN_HA_BODY_PCT=1.0`, `STALE_DATA_MAX_DAYS=5`, a
  `MIN_ROWS` history floor, and a block of `USE_*_FILTER` flags all defaulted to `False`.
- **Universe acquisition (Section 4a):** downloads the official NIFTY 200 constituent CSV from
  NSE archives with multiple candidate URLs tried in sequence; validates the constituent count is
  in a sane range (roughly 195–205); **raises and stops the whole run** if no source validates —
  it does NOT fall back to a small hard-coded backup list. This is the correct, hard-fail
  behavior described in PART 6 of the specification, already implemented.
- **Symbol mapping:** NSE symbol → Yahoo symbol via a `<SYMBOL>.NS` suffix rule, with a small
  in-notebook override dict for a handful of known-different tickers (e.g. `M&M`, `BAJAJ-AUTO`).
  This is the "four-symbol manual override list" pattern PART 4 explicitly asks to replace with a
  versioned config file — migrated to `config/symbol_overrides.yaml` in the new repository.
- **Data download:** yfinance batch download for the stock universe, plus a separate benchmark
  (`^NSEI`) download. Includes retry/backoff and per-symbol error capture so one bad symbol
  doesn't halt the batch.
- **Data validation:** checks for missing OHLC, non-positive prices, `High>=Low` etc., and drops
  offending rows rather than fabricating replacements. Uses a flat `MIN_ROWS` floor for whether a
  security has "enough" history.
- **Bollinger engine:** `SMA(Close,20)`, rolling std with an explicit, configurable `ddof`,
  `Upper/Lower = Middle ± 2*Std`, and `%B = (Close-Lower)/(Upper-Lower)` kept as a **ratio**
  (already correctly NOT treated as a percentage).
- **Heikin-Ashi engine:** standard recursive formulation, seeded on the first bar's
  `(Open+Close)/2`, with an explicit Python loop for the recursion (not a broken vectorized
  shortcut).
- **ATR:** computes True Range correctly, then smooths it with **a simple rolling mean**, while
  the surrounding comments/docstring call it "Wilder's ATR" (with a caveat noting it's an
  approximation). The values produced are **not** what Wilder's actual recursive smoothing
  produces.
- **Breakout state machine:** implements FRESH_BREAKOUT / CONTINUATION / FAILED_BREAKOUT /
  NO_BREAKOUT using the previous-day vs. current-day Close-vs-Upper-Band comparison, plus a
  `Days_Above_Upper_BB` streak counter — already correctly implemented.
- **Feature assembly:** trend (SMA20/50/200 with per-security "unavailable" status when history
  is short, NOT a flat rejection), volume ratios, candle-quality (CLV etc.), ATR-normalized
  overshoot, relative strength vs. `^NSEI`.
- **Signal evaluation + sanity checks:** re-derives the mandatory PASS conditions from a row's own
  fields immediately before writing it to a signal sheet (an assertion-based gate).
- **Ranking score:** a manually-weighted composite explicitly named as a heuristic, with a
  completeness/"partial" flag when the benchmark is unavailable — already NOT presented as a
  probability.
- **Historical event extraction (research section):** scans the full history of every stock for
  every row where the mandatory condition holds and treats **each such row as its own event** for
  forward-return statistics. A stock that stays extended above the band for five consecutive
  sessions contributes five rows to the sample.
- **Forward returns / MFE / MAE:** computes next-day-open entry and multi-horizon forward
  returns; computes MFE/MAE over a window starting at the entry bar.
- **"Walk-forward" section:** a single, static `TRAIN_START/TRAIN_END/TEST_START/TEST_END` split,
  labeled "walk-forward" in the surrounding prose and cell headers.
- **Sensitivity section:** re-runs the strategy across a small grid of `BB_PERIOD` /
  `MAX_BB_OVERSHOOT_PCT` / `MIN_HA_BODY_PCT` values and reports a results table, without
  auto-selecting a "best" combination — already following PART 27's "do not silently optimize"
  rule.
- **Excel export:** writes multiple sheets (signals, diagnostics, parameters, research summary,
  etc.) using `openpyxl`, with an explicit internal-representation-vs-display-format convention
  for percentage columns documented in a comment.
- **Disclaimer cell (final):** explicitly states the walk-forward split is a single static split
  and not genuine rolling walk-forward, states the backtest uses current-universe constituents
  applied backward (survivorship bias), and states the ranking score is a heuristic, not a
  probability. The notebook is self-aware about several of its own limitations.

## Confirmed bugs (carried into the new repository's bug list, fixed there)

| # | Bug | Notebook behavior | Status in new repository |
|---|---|---|---|
| BUG 1 | Calendar-day staleness | `STALE_DATA_MAX_DAYS = 5` compares raw calendar days | Fixed — session-count-based, `data/calendar.py` |
| BUG 2 | Mixed-freshness "current" signals | Rows are grouped mainly by not-too-old date | Fixed — `Data_Status` gate, only `CURRENT` rows enter `Live_Signals` |
| BUG 3 | Benchmark shares the stock downloader's shape assumptions | Same `yf.download` code path for both | Fixed — dedicated `data/benchmark.py` |
| BUG 4/16 | Benchmark-unavailable score still looks numeric | Score computed with RS defaulted rather than flagged | Fixed — `Score_Status = PARTIAL`, `strategy/ranking.py` |
| BUG 5 | ATR mislabeled "Wilder" | Simple rolling mean of True Range | Fixed — genuine recursive Wilder formula, `indicators/atr.py`, tested against a hand-computed reference |
| BUG 6 | Flat `MIN_ROWS` floor | One threshold rejects newly-listed securities | Fixed — tiered thresholds per indicator, `config.HistoryTierConfig` |
| BUG 7 | %B semantic confusion | Already correct in the notebook (ratio) | Preserved — `indicators/bollinger.py` |
| BUG 8 | Overlapping events treated as independent | Every signal-day row is its own event | Fixed — `ALL_SIGNAL_DAYS` vs `INDEPENDENT_EVENTS`, `research/events.py` |
| BUG 9 | MFE/MAE aggregate mislabeling | Notebook already used Mean/Median explicitly | Preserved — `research/mfe_mae.py` |
| BUG 10 | Event-return sequence called a portfolio drawdown | Not present as a labeled "portfolio backtest" in the notebook (it doesn't claim this) | A genuine capital-constrained engine now exists, `portfolio/backtest.py`, kept clearly separate from event-study output |
| BUG 11 | Static split called "walk-forward" | Confirmed, one static split | Fixed — genuine rolling window generator, `research/walk_forward.py` |
| BUG 12 | Research vs. live history conflated | Notebook already separates `LIVE_LOOKBACK` from a longer research start | Preserved — `config.ResearchConfig.live_history_period` / `.research_history_start` |
| BUG 13 | Survivorship bias | Notebook already discloses this in its final cell | Formalized — `research/survivorship.py` with an explicit `MODE_A`/`MODE_B` contract |
| BUG 14 | Percentage double-scaling in Excel | Notebook already had a documented convention to avoid this | Formalized and tested — `reporting/excel.py`, `tests/unit/test_excel_percent.py` |
| BUG 15 | Score weights not statistically validated | Notebook already states this | Preserved and restated — `strategy/ranking.py` docstring |
| BUG 17 | Market-regime sheet silently empty | Not specifically observed as broken in the notebook, but no explicit RUN_HEALTH indicator existed | Fixed — `Data_Health` sheet with `RUN_HEALTH = GREEN/YELLOW/RED`, `pipeline/scan.py` |

## Intentional behavior preserved unchanged

- The exact mandatory baseline arithmetic (`Close > Upper_BB`, `0 < BB_Overshoot_Pct <= 4.0`,
  `HA_Body_Pct >= 1.0`), including the `>=`/`<=` boundary inclusivity.
- `BB_DDOF=1` (sample standard deviation) as the default.
- Hard-fail universe integrity (no silent fallback to a small backup list).
- `%B` as a ratio, not a percentage.
- All optional filters default to `False`.
- The heuristic ranking score is descriptive only, never presented as a probability.

## What could not be verified

- The notebook's own most recent run output (universe=200, 3 base signals: SOLARINDS, GVT&D,
  COALINDIA) could not be reproduced as a byte-for-byte regression fixture, because reproducing
  it requires live NSE/yfinance data as of that run's as-of-date, and this repository's build
  environment has no network path to either source. See `CODE_REVIEW.md` for how this is handled
  instead (a documented regression *procedure*, not a byte-identical replay).
