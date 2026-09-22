# Migration map: legacy notebook → new repository

Source: `legacy/Nifty200_BB_HA_Research_Scanner_v2.ipynb` (frozen, unmodified — see AUDIT.md for
its sha256 hash). This document maps each piece of notebook logic to where it now lives, and
notes any intentional behavior differences. See AUDIT.md for the underlying bug list.

| Notebook section | New location | Behavior change |
|---|---|---|
| `Config` cell (BB/HA/ATR params, filter flags) | `src/nse_scanner/config.py` | Same defaults; now a typed, validated dataclass hierarchy instead of a single flat object; env-var and YAML override support added |
| NIFTY 200 constituent download + validation | `src/nse_scanner/universe/nifty200.py` | Same hard-fail policy, same candidate-URL retry pattern; wrapped behind the `UniverseProvider` interface |
| NSE symbol → Yahoo symbol mapping + override dict | `src/nse_scanner/universe/symbol_mapping.py` + `config/symbol_overrides.yaml` | Override table moved from an in-notebook dict to a versioned config file (PART 4) |
| yfinance batch download | `src/nse_scanner/data/yfinance_provider.py` + `src/nse_scanner/data/normalization.py` | Explicit MultiIndex-shape handling extracted into its own tested module (PART 10/11); every yfinance parameter now passed explicitly and version-pinned |
| Benchmark (`^NSEI`) download | `src/nse_scanner/data/benchmark.py` | Given its own dedicated provider/function instead of sharing the stock downloader's code path (fixes BUG 3) |
| OHLC validation (drop bad rows, don't fabricate) | `src/nse_scanner/data/validation.py` | Same non-interpolation policy; flat `MIN_ROWS` replaced with tiered thresholds (fixes BUG 6) |
| Bollinger engine | `src/nse_scanner/indicators/bollinger.py` | Identical formulas; `ddof` still configurable |
| Heikin-Ashi engine | `src/nse_scanner/indicators/heikin_ashi.py` | Identical recursive formulation; added `seed_dependency_weight()` for explicit history-quality reporting |
| ATR ("Wilder", simple rolling mean in practice) | `src/nse_scanner/indicators/atr.py` | **Behavior change:** now genuine recursive Wilder smoothing (fixes BUG 5) — ATR14 values from this repository will differ numerically from the legacy notebook's output |
| Breakout state machine | `src/nse_scanner/strategy/breakout.py` | Identical FRESH/CONTINUATION/FAILED/NO_BREAKOUT logic |
| Trend/volume/candle/RS feature calculations | `src/nse_scanner/indicators/{moving_averages,volume,candles,relative_strength}.py` | Same formulas, split into one module per concern; RS/regime now explicitly report `UNAVAILABLE` rather than defaulting |
| Market regime classification | `src/nse_scanner/regime/market.py` | Same rule, explicitly labeled a RESEARCH DEFINITION in the module docstring |
| Signal sanity assertions before writing a PASS row | `src/nse_scanner/indicators/sanity.py` | Same assertions, now raise a typed `SignalMathError` instead of an ad-hoc check |
| Ranking / heuristic score | `src/nse_scanner/strategy/ranking.py` | Same "not a probability" framing; `Score_Status` (`FULL`/`PARTIAL`/`UNAVAILABLE`) formalized |
| Historical event extraction | `src/nse_scanner/research/events.py` | **Behavior change:** now reports both `ALL_SIGNAL_DAYS` (matches legacy behavior) and a new `INDEPENDENT_EVENTS` subset (fixes BUG 8) — legacy research sample sizes were `ALL_SIGNAL_DAYS`-equivalent and are therefore larger/more correlated than the new `INDEPENDENT_EVENTS` counts |
| Forward returns / entry timing | `src/nse_scanner/research/forward_returns.py` | Same next-open default entry; `next_close` made equally explicit and tested |
| MFE / MAE | `src/nse_scanner/research/mfe_mae.py` | Same entry-relative windowing; explicit test distinguishing the next-open-includes-entry-bar vs. next-close-excludes-entry-bar cases |
| "Walk-forward" static split | `src/nse_scanner/research/walk_forward.py` | **Behavior change:** now generates a genuine sequence of rolling windows (fixes BUG 11) instead of one static split — a caller migrating from the notebook's single split must now iterate `generate_walk_forward_windows(...)` |
| Sensitivity grid | `src/nse_scanner/research/sensitivity.py` | Same "report, don't auto-optimize" policy; grid construction extracted so it doesn't couple to the data layer |
| Portfolio equity curve | *(new — did not exist as a labeled "portfolio backtest" in the notebook)* | `src/nse_scanner/portfolio/backtest.py` — a genuine capital-constrained simulation, built new rather than migrated |
| Cost assumptions | *(scattered in the notebook, if present at all)* | `src/nse_scanner/portfolio/costs.py` — formalized into a configurable `CostProfileConfig` |
| Excel export | `src/nse_scanner/reporting/excel.py` | Same percentage-representation convention (literal `%` suffix, not native Excel percent format), now unit-tested |
| Survivorship-bias disclosure (final cell) | `src/nse_scanner/research/survivorship.py` | Same disclosure text, formalized into a `MODE_A`/`MODE_B` data contract with a validator that refuses to mislabel a current-constituent list as point-in-time |

## What was NOT migrated (did not exist in the notebook, built new)

- The NSE trading-session calendar (`data/calendar.py`) — the notebook used calendar-day
  staleness (BUG 1); there was no session model to migrate from.
- The security-master / ISIN-as-identity model (`models/security.py`,
  `universe/security_master.py`) — the notebook used the NSE symbol as the working identity
  throughout.
- The DuckDB + Parquet storage layer (`data/storage.py`) — the notebook operated on in-memory
  DataFrames for a single run and did not persist between runs.
- The daily EOD ingestion pipeline (`pipeline/ingestion.py`, `data/nse_eod.py`,
  `data/nse_reports.py`) — the notebook downloaded a fresh multi-year yfinance history on every
  run rather than incrementally ingesting official NSE EOD files.
- The run manifest (`reporting/run_manifest.py`) — the notebook printed parameters to the output
  but did not write a machine-readable reproducibility record.
- `--offline-fixture` mode and the synthetic-data generators (`testing/synthetic_market_data.py`)
  — built specifically so this repository can be tested and demonstrated without network access.

## Known intentional numeric differences vs. the legacy notebook

If you run both the legacy notebook and this repository against the *same* underlying price
data, expect these fields to differ:

- **ATR14 / ATR_Pct / BB_Overshoot_ATR** — the notebook's simple-rolling-mean ATR vs. this
  repository's genuine Wilder ATR will diverge, especially after high-volatility periods (Wilder
  smoothing has longer memory of a shock than a simple rolling mean of the same window).
- **Independent-event research sample sizes** — smaller here by design (BUG 8 fix); do not
  compare `N` between the two systems' research output directly.
- **Walk-forward metrics** — the notebook produced one train/test result; this repository
  produces one result per rolling window, which cannot be reduced to a single comparable number.

The mandatory baseline signal itself (`Close > Upper_BB`, overshoot bound, HA body bound) is
**not** expected to differ, given identical input price data — see `tests/unit/test_signal.py`
and the regression procedure in `CODE_REVIEW.md`.
