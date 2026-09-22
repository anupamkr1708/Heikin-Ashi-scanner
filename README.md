# NSE EOD/T-1 Technical Scanner

A daily, end-of-day (T-1) NSE technical scanner built around a fixed Bollinger-Band +
Heikin-Ashi baseline strategy, plus a modular research/backtesting toolkit. This is **not** a
live/intraday trading platform and has **no broker order execution**.

The intended workflow: sit down in the evening, run one command, and get an Excel report of
which stocks closed above their upper Bollinger Band with a strong Heikin-Ashi body today —
along with a clear statement of whether the run's data is actually trustworthy.

> **Read this first:** [`CODE_REVIEW.md`](CODE_REVIEW.md) and the "Known limitations" section
> below. This repository was built in a sandboxed environment with no network access to
> nseindia.com or Yahoo Finance, so the NSE parsing logic was originally written and tested only
> against synthetic, schema-documented fixtures. **It has since been run against real, live NSE
> data by the maintainer and worked**: real daily bhavcopy ingestion (2,885 securities) and the
> real NIFTY 200 constituent list (200 constituents) both parsed correctly on the first try. What
> is still unverified against live data: the NSE CM-MII security master file (used to derive
> `NSE_MAINBOARD_EQ`) and the historical bootstrap path (`scripts/bootstrap_history.py`, which
> uses yfinance) — see "Known limitations".

---

## Quick start (Windows)

```bat
python -m venv .venv
.venv\Scripts\activate
pip install -e .

:: one-time (or occasional) historical backfill — the daily EOD path only appends ONE session
:: at a time, so a fresh install needs this before the baseline strategy has enough history
python scripts\bootstrap_history.py --universe NIFTY_200 --period 2y

:: one-click daily run, from then on
run_daily.bat
```

or in PowerShell:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e .
python scripts\bootstrap_history.py --universe NIFTY_200 --period 2y
.\run_daily.ps1
```

## Quick start (macOS/Linux)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
python scripts/bootstrap_history.py --universe NIFTY_200 --period 2y
python scripts/run_daily.py
```

## Quick start — no network at all (offline fixture mode)

```bash
python scripts/run_daily.py --offline-fixture
```

This generates deterministic **synthetic** data shaped like the documented NSE schema, runs the
full pipeline (universe → ingestion-equivalent → features → signal → optional context → Excel →
manifest), and writes a report to `reports/`. Use this to prove the software works before you
trust it against real data, and for CI/development/debugging.

---

## What this actually does

1. Determines today's date in IST and, from an explicit NSE trading-session calendar (not
   `today - 1 day`), figures out the most recent **completed** NSE session.
2. Checks — by actually probing the report, not by assuming a clock time — whether NSE has
   published the final EOD bhavcopy for that session yet. If not, it stops and says
   `WAITING_FOR_EOD_DATA` rather than silently scanning yesterday's data.
3. Downloads the official NSE CM-UDiFF Bhavcopy (primary data source), saves an untouched raw
   copy, hashes it, parses it, validates it, and appends it to a local DuckDB + Parquet store.
4. Computes indicators (Bollinger Bands, Heikin-Ashi, Wilder ATR, trend, volume, candle
   quality, relative strength) for every security with enough history.
5. Evaluates the **immutable baseline signal**:
   `Close > Upper_BB` AND `0 < BB_Overshoot_Pct <= 4.0` AND `HA_Body_Pct >= 1.0`.
6. Optionally applies confirmation filters (all off by default), classifies breakout state
   (fresh/continuation/failed), computes a frankly-labeled `Research_Heuristic_Score`
   (explicitly **not** a probability), and generates a plain-language reason string.
7. Writes an Excel workbook (`Live_Signals`, `Stale_Signals`, `Diagnostics`, `Data_Health`,
   `Scan_Log`, `Universe`, `Parameters`, `Market_Regime`, plus research sheets) and a
   machine-readable `run_manifest.json` for full reproducibility.

## Commands

```bash
python scripts/bootstrap_history.py --universe NIFTY_200 --period 2y  # one-time/occasional backfill
python scripts/bootstrap_history.py --start 2018-01-01 --end 2026-09-11  # or an explicit date range
python scripts/run_daily.py                       # the one-command daily workflow
python scripts/run_daily.py --date 2026-09-10      # force a specific session (debugging/backfill)
python scripts/run_daily.py --offline-fixture      # no network, synthetic data
python scripts/run_daily.py --universe NIFTY_200   # override universe
python scripts/run_daily.py --allow-missing-holidays  # don't block on an incomplete holiday calendar

python scripts/update_universe.py                  # fetch + validate + snapshot the universe
python scripts/ingest_eod.py --date 2026-09-10      # ingest one session's bhavcopy
python scripts/run_scan.py --universe NIFTY_200     # scan already-ingested local data only
python scripts/run_replay.py --as-of 2026-09-08     # read-only historical as-of replay — the
                                                     # central capability of this release
python scripts/run_research.py --start 2018-01-01 --end 2026-09-11 --horizon 5  # event-study research
python scripts/run_walk_forward.py --train-years 3 --test-months 6  # genuine rolling walk-forward
```

Every script accepts `--help`.

## Repository layout

```
src/nse_scanner/
  config.py              typed configuration (every strategy parameter lives here)
  data/                  NSE + yfinance providers, calendar, validation, DuckDB/Parquet storage
  indicators/            Bollinger, Heikin-Ashi, Wilder ATR, trend, volume, candles, RS
  strategy/              the immutable baseline signal, breakout states, optional filters,
                          strategy-variant registry, ranking score
  regime/                market-regime classification (explicitly a RESEARCH DEFINITION)
  research/              event extraction, forward returns, MFE/MAE, statistics, walk-forward,
                          survivorship-bias labeling
  portfolio/              cost model, position sizing, a genuine capital-constrained backtest
  reporting/              Excel report, run manifest, signal-reason text
  pipeline/               feature assembly, scan orchestration, EOD ingestion
  cli/                    the actual command implementations (scripts/*.py are thin launchers)
  testing/                synthetic fixture generators (shared by tests AND --offline-fixture)
tests/
  unit/                   indicator math, calendar, signal, sanity gates, anti-look-ahead
  integration/            NSE parser (vs. synthetic fixtures), full offline pipeline
config/                   default.yaml, nse_holidays.yaml, symbol_overrides.yaml
legacy/                   the original Colab notebook, frozen and unmodified
.github/workflows/        ci.yml (no network needed), eod_scan.yml, research.yml
```

## Data sources

- **Primary:** NSE's official CM-UDiFF Bhavcopy (daily EOD prices) and CM security master file.
  See `data/nse_eod.py` and `data/nse_reports.py`.
- **Secondary/fallback:** Yahoo Finance via `yfinance`, isolated behind its own provider
  (`data/yfinance_provider.py`) with explicit MultiIndex-shape normalization
  (`data/normalization.py`).
- Both are isolated behind a `DataProvider` interface (`data/base.py`) — the strategy/indicator
  layers never know which one produced a given bar.

## Universe

`NIFTY_200` and `NSE_MAINBOARD_EQ` (the default production universe — standard EQ/BE equity
series only, ETFs/SME/debt/REITs/InvITs excluded) are both real, independently-selected
providers, chosen via `universe/factory.py::get_universe_provider(cfg)` based on
`universe_scope` — setting `NSE_MAINBOARD_EQ` in config actually changes which universe gets
scanned (an earlier version of `run_daily.py` had a bug where it didn't). `NSE_MAINBOARD_EQ` is
derived from the live NSE security master file (`data/nse_reports.py`), never a hard-coded list.
A failed official-source download **raises** for either universe — it never silently falls back
to a small hard-coded list — see `universe/nifty200.py`, `universe/mainboard.py`, and PART 6 of
the original specification.

## The baseline strategy (immutable)

```
strategy_id = "bb_ha_v1_base"

Close > Upper_BB
0 < BB_Overshoot_Pct <= 4.0     where BB_Overshoot_Pct = (Close - Upper_BB) / Upper_BB * 100
HA_Body_Pct >= 1.0              where HA_Body_Pct = (HA_Close - HA_Open) / HA_Open * 100

BB_PERIOD=20, BB_STD_MULT=2.0, BB_DDOF=1
```

This lives in `strategy/bb_ha.py` and is covered by `tests/unit/test_signal.py`. Any proposed
change is a new, separately-registered variant in `strategy/registry.py` — never an in-place
edit.

## Data freshness / the NSE holiday calendar

`data/calendar.py` replaces the old "calendar days" staleness check with a genuine
trading-session model — but NSE's holiday list includes lunar-calendar festivals whose dates
this repository's build sandbox could not verify against a live source. **`config/nse_holidays.yaml`
ships empty.** Populate it from NSE's official trading-holiday circular before relying on
production staleness detection for a given year — see the comments in that file.

## Testing

```bash
pytest tests/ -v          # 145 tests as of this writing — all offline, no network required
ruff check src/ scripts/ tests/
mypy src/nse_scanner
```

Notable test files:
- `tests/unit/test_lookahead.py` — the research-immutability / anti-look-ahead tests: proves
  appending future bars never changes a historical Bollinger/Heikin-Ashi/ATR/breakout-state
  value.
- `tests/unit/test_events_independence.py` — proves `INDEPENDENT_EVENTS` never exceeds
  `ALL_SIGNAL_DAYS` and that a continuation streak collapses under the cooldown rule.
- `tests/unit/test_atr.py` — validates the real recursive Wilder formula against a hand-computed
  reference, and explicitly checks it's NOT the same as a simple rolling mean.
- `tests/integration/test_nse_parser.py` — validates the UDiFF/security-file column mapping
  against synthetic (not real — see below) fixtures.
- `tests/integration/test_full_pipeline_offline.py` — proves the whole wiring works end to end.

## Research

`scripts/run_research.py` runs an event study over whatever history is in your local store. It
separates `ALL_SIGNAL_DAYS` from `INDEPENDENT_EVENTS` (a continuation streak is not five
independent observations), uses next-session-open entry by default (never the signal-day close),
and discloses survivorship bias explicitly — see `research/survivorship.py` and
[`RESEARCH_METHODOLOGY.md`](RESEARCH_METHODOLOGY.md). Nothing in this repository claims the
strategy is profitable; the research tooling exists to let you check, honestly.

## Known limitations

1. **NSE bhavcopy + NIFTY 200 parsing has been verified against real live data; the security
   master and yfinance bootstrap paths have not.** A real run against live NSE confirmed
   `data/nse_eod.py`'s UDiFF bhavcopy parsing (2,885 securities ingested correctly) and
   `universe/nifty200.py`'s constituent fetch (200 real constituents) both work as written — the
   documented-schema guesses turned out to be correct. **Still unverified against a real
   response:** `data/nse_reports.py::fetch_security_file`/`parse_security_file` (used to derive
   `NSE_MAINBOARD_EQ` — see `universe/mainboard.py`) and `scripts/bootstrap_history.py`'s yfinance
   download path. If either errors out, it's written to fail loudly and name exactly which
   column/URL didn't match — that's the fix, not a redesign.
2. **NSE holiday calendar has only a partial 2026 entry.** `config/nse_holidays.yaml` now has
   fixed-date national holidays plus one maintainer-supplied date for 2026, each with explicit
   provenance comments — but lunar-calendar festival holidays (Diwali, Holi, Eid, etc.) are NOT
   yet included for any year. A production run **blocks** (rather than silently degrading) if a
   required year is entirely missing from the file; it does NOT currently detect that a covered
   year is merely incomplete. Complete it from NSE's official circular before relying on it
   across a full year.
3. **Point-in-time NIFTY 200 membership is not implemented.** Every replay/research run uses
   today's constituent list applied backward and explicitly discloses the resulting survivorship
   bias (`SURVIVORSHIP_BIAS_PRESENT = True`) — see `research/survivorship.py`.
4. **There is no true corporate-action-adjusted continuous research price series.**
   `data/corporate_actions.py` can flag a large gap and reconcile it against a supplied CA record,
   but no real CA feed is wired up. A small corporate-action-sized price change can currently land
   inside the mandatory signal window indistinguishable from organic price action — this is
   stated explicitly (not hidden) in `tests/unit/test_corporate_action_synthetic.py` and
   `RESEARCH_METHODOLOGY.md`.
5. **No data-snooping / multiple-testing correction exists** (no White's Reality Check, deflated
   Sharpe, or PBO statistic). Documented as an explicit future milestone in
   `RESEARCH_METHODOLOGY.md` rather than implemented incorrectly.
6. **The portfolio backtest is a genuine single-pass simulation, not a production execution
   engine.** No partial fills, no realistic order-book modeling, no intraday stop monitoring.
7. **A blended price basis is possible and is surfaced, not hidden.** Once `bootstrap_history.py`
   (yfinance, `ADJUSTED`) and daily ingestion (NSE, `RAW`) both cover a symbol, `data/storage.py`
   prefers the NSE row for any date both providers touch, and reports the resulting composition
   (`RAW` / `ADJUSTED` / `BLENDED_RAW_NSE_ADJUSTED_YFINANCE_BOOTSTRAP`) per symbol in
   `Diagnostics`, the run manifest, and (for replay) a printed `*** WARNING ***` line.
8. **Weekly-data no-look-ahead enforcement (`indicators/weekly.py`) exists and is tested, but
   nothing currently consumes it** — the baseline strategy doesn't use weekly data. It's built
   for a future strategy variant, not wired into any active signal today.
9. **The GitHub Actions `eod_scan.yml` / `research.yml` workflows are syntactically valid and
   structurally sound but have never actually been run** (this environment has no way to trigger
   a real GitHub Actions run against live NSE data). Treat them as a well-documented starting
   point.
10. **Universe scopes beyond `NIFTY_200` and `NSE_MAINBOARD_EQ`** (`NSE_SME`, `NSE_ETF`,
    `NSE_REIT`, `NSE_INVIT`, `NSE_OTHER`) are recognized by config validation but have no provider
    implementation yet — `universe/factory.py` raises a clear `ConfigurationError` naming this
    rather than silently falling back to something else.
11. See [`CODE_REVIEW.md`](CODE_REVIEW.md) for a category-by-category PASS/WARN/FAIL audit.

## Historical bootstrap (do this once before the first daily run)

`run_daily.py`'s NSE ingestion appends exactly ONE trading session per run, by design (PART 55:
"do not redownload years of history every evening"). On a fresh install that means every stock
has only 1 row of history — nowhere near the 20+ the Bollinger calculation needs. Run this once
(or occasionally, to backfill a gap):

```bash
python scripts/bootstrap_history.py --universe NIFTY_200 --period 2y
```

This uses yfinance (never NSE) to populate multi-year history in one batch, tagged
`source=YFINANCE, price_basis=ADJUSTED` in the store, completely separately from the daily
`source=NSE, price_basis=RAW` path. It prints a coverage report (populated / insufficient /
failed-download counts, median/min/max rows per symbol) so you know exactly what got backfilled
before you trust `run_daily.py`'s first real scan. If `run_daily.py` detects that most of the
universe still lacks sufficient history, it tells you to run this — it does not silently produce
a report with zero trustworthy signals and call it done.

## Historical as-of replay — "what would the strategy have detected on date T?"

```bash
python scripts/run_replay.py --as-of 2026-09-08
```

This is the central capability this repository is built around: a read-only command that
answers, honestly, what the baseline strategy would have flagged on a past date using ONLY
information that existed by that date's close. It never writes to the database. The cutoff is
enforced at the storage query level (`data/asof_provider.py`), not by convention — proven in
`tests/integration/test_replay_no_lookahead.py` by ingesting 30 more future sessions into a real
store (including an engineered future breakout) and confirming the replay's output for an earlier
date doesn't change by a single value.

The report explicitly separates what it knows from what it doesn't:
- `UNIVERSE_MODE` / `SURVIVORSHIP_BIAS_PRESENT` — this repository has no verified point-in-time
  NIFTY-200 membership feed, so replay uses today's constituent list applied to the historical
  date, and says so on every run rather than presenting it as if it were the true historical
  membership.
- `PRICE_BASIS` — flags explicitly if a symbol's history blends NSE (`RAW`) and yfinance
  (`ADJUSTED`) data rather than silently treating a blended series as clean.
- `BASELINE_SIGNAL_STATUS` — distinguishes `NOT_EVALUABLE` (no security had enough history as of
  that date) from `EVALUATED (0 signals found)`, which are very different situations.

## Windows one-click launchers

`run_daily.bat` and `run_daily.ps1` at the repository root wrap `scripts/run_daily.py`. They
look for a `.venv` folder first (created by the quick-start steps above) and fall back to
whatever `python` is on your PATH.
