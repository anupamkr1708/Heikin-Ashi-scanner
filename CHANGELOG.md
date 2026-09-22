# Changelog

## 1.3.0 — v1.3 research-integrity audit: benchmark as-of leak, calendar, manifest provenance

An external forensic audit against the full v1.3 research-integrity specification (P0 items:
full codebase audit, as-of replay verification, future-data invariance, benchmark
normalization, calendar correctness, data provenance). Findings and fixes below; see the audit
report delivered alongside this release for the full PASS/WARN/FAIL table and what remains
unverified.

- **Fixed (highest-severity finding):** `data/benchmark.py::YahooBenchmarkProvider` always
  called `yf.download(period=...)` with no upper date bound, regardless of caller.
  `run_replay.py --as-of <past date>` used it unmodified, so a historical replay's
  `Market_Regime`/`RS_20D`/`RS_60D`/`RS_120D` columns were computed from TODAY's live benchmark
  data, not from what was actually available by the close of the replayed date — the exact
  violation the `AsOfDataProvider` mechanism exists to prevent for stock data. Concrete
  symptom: the Market_Regime sheet's `Index_Close` in a historical replay was silently today's
  live index bar, not the replayed date's. `YahooBenchmarkProvider` now takes an `as_of_date`,
  enforced at two independent layers (request `end=` param, response-layer post-filter),
  mirroring `MarketDataStore`'s SQL cutoff. Disclosed residual limitation: this does not protect
  against a provider retroactively revising an already-published adjusted close — the benchmark
  path is not locally frozen the way stock history is. 13 new tests (11 direct unit tests for a
  provider that previously had zero, 2 pipeline-level future-append-invariance tests).

- **Fixed:** `config/nse_holidays.yaml`'s 2026 entry was a disclosed-as-partial 5-date
  convenience default (this repository's original build sandbox had no network path to
  nseindia.com). Replaced with the verified official NSE circular (Download Ref No.
  NSE/CMTR/71775, 15 dates) plus one ad-hoc modification found during verification
  (2026-01-15, Maharashtra municipal elections — corroborated by multiple independent
  contemporary news reports; NOT in the primary annual circular, added via a later
  modification). 16 dates total, each independently sourced and commented. Also removed a
  dead constant (`_FIXED_DATE_HOLIDAYS_MMDD`, defined, zero call sites) that the old module
  docstring implied was in use.

- **Fixed:** `run_manifest_*.json` (the machine-readable, reproducible run record — PART 54)
  was missing `benchmark_status`, data-quality counts, scan/signal counts, and (for
  `run_daily.py`) the ingested file's SHA256 hash, across all four places a manifest gets
  built. Every one of these values was already computed and printed to the console —
  `IngestionResult.file_hash` in particular was computed, returned, and then discarded without
  ever being printed OR persisted. Now threaded through via `build_run_manifest`'s `extra=`
  field at all four call sites. Also fixed `offline_fixture.py` omitting `price_basis` entirely
  (silently falling back to the config default instead of the actual basis returned).

- **Fixed:** `ruff`/`mypy` were not actually clean despite `CODE_REVIEW.md` claiming so — a
  trailing-newline `W292` in `config.py` and a missing `types-python-dateutil` mypy stub. Both
  fixed; both tools now genuinely clean.

- **Fixed:** version mismatch — `pyproject.toml` said `1.2.2`, `version.py`'s `__version__`
  (what actually ends up in the run manifest's `software_version` field) said `1.2.0`. Both now
  `1.3.0`.

- **Confirmed correct (traced, not just read):** `AsOfDataProvider` → `MarketDataStore`'s SQL
  `trade_date <= ?` cutoff for stock data is a genuine query-level guarantee, not just
  documentation. Universe point-in-time handling (`research/survivorship.py`) is honestly
  labeled `CURRENT_UNIVERSE_HISTORICAL_SIMULATION` with `SURVIVORSHIP_BIAS_PRESENT = True`
  rather than claiming true point-in-time membership it doesn't have.

- **Found, not yet fixed (documented in the audit report, not silently dropped):**
  `data/provenance.py::ProvenanceRecord` is a well-designed dataclass matching PART 23's field
  list closely, but has zero call sites anywhere in the codebase — aspirational/dead code, not
  a wired-up mechanism. Per-row provenance in the actual `eod_prices` table covers `source`,
  `price_basis`, `schema_version`, `ingested_at` but not `source_file_hash`/`ingestion_run_id`
  as explicit columns (traceable back to the raw file only via the `trade_date`-based naming
  convention, not an explicit link).

162 tests passing (was 145 at the start of this audit), `ruff`/`mypy` clean.

## 1.2.2 — yfinance repair disabled by default (2nd missing-dependency hit)

The 1.2.1 fix addressed `scipy`; the very next bootstrap run hit a DIFFERENT missing dependency
(`scikit-learn`) from the same `repair=True` feature, because yfinance's repair logic pulls in
different optional packages depending on WHICH kind of bad data it happens to find on a given
batch — so a fix that only adds the one dependency you just hit is guaranteed to eventually miss
another. Rather than continue that one at a time, `repair` is now **off by default**
(`config.DataConfig.yfinance_repair = False`). It isn't needed for this scanner's own OHLC
validation (which already drops bad rows independently), and turning it off removes the whole
unpredictable dependency chain. `scikit-learn` is still added to `pyproject.toml` alongside
`scipy` so re-enabling `repair` later just works without a repeat of this.

## 1.2.1 — yfinance dependency fix (found via real bootstrap run)

- **Fixed:** `pyproject.toml` pinned `yfinance==0.2.40`, which a real user found being rejected
  outright by Yahoo's current API (`JSONDecodeError` on every single request). Bumped to
  `yfinance>=1.7.0,<2.0`, which resolved it.
- **Fixed:** the newer yfinance's `repair=True` path (already enabled by
  `config.DataConfig.yfinance_repair`) depends on `scipy`, which wasn't declared as a dependency —
  surfaced as `ModuleNotFoundError("No module named 'scipy'")`, intermittently (only on batches
  where yfinance's repair logic actually found something to fix, e.g. a phantom dividend), which
  is why a quick smoke test hadn't caught it. Added `scipy>=1.11` to `pyproject.toml`.
- Updated `config.DataConfig.yfinance_version_tested` default to `"1.7.0"` to match.
- No code logic changed; 145/145 tests still pass, `ruff`/`mypy` still clean.

## 1.2.0 — Historical as-of replay engine and research integrity

The central deliverable: a genuine answer to "what would the strategy have detected on
historical date T, using only information available by T's close" — enforced at the storage
query layer, not by convention.

- **Added `data/asof_provider.py::AsOfDataProvider`** and `MarketDataStore.read_symbol_history(...,
  as_of=...)` — every read is truncated at the SQL level to `trade_date <= as_of`, so data
  ingested after a replay's as-of date (including by continued daily runs) is structurally
  invisible to that replay.
- **Added `scripts/run_replay.py`** — read-only historical replay CLI. Reports
  `AS_OF_DATE`/`EXPECTED_SESSION`/`DATA COVERAGE`/`PRICE_BASIS`/`BASELINE_SIGNAL_STATUS`
  (distinguishing `NOT_EVALUABLE` from "0 signals found"), and prints the survivorship
  disclosure inline. Never writes to the database.
- **Added `tests/integration/test_replay_no_lookahead.py`** (4 tests) — the strongest proves a
  full `pipeline.scan.run_scan()` run produces byte-identical `Live_Signals`/`Diagnostics` before
  and after 30 more future sessions (including an engineered future breakout) are genuinely
  appended to the same on-disk store.
- **Added `indicators/weekly.py`** — weekly OHLC construction with as-of enforcement based on the
  ISO calendar (not on how much daily data happens to be present, which a first draft got wrong
  and the test suite caught — see below). Not currently consumed by the baseline strategy; built
  for a future variant.
- **Added `scripts/run_walk_forward.py`** — genuine rolling TRAIN/TEST windows via
  `research/walk_forward.py`, per-window (never pooled) CSV output.
- **Added `research/statistics.py::sector_summary` / `top_sector_contribution_pct` /
  `clustering_by_dimension`** — sector concentration and multi-dimensional signal clustering,
  wired into `run_research.py`'s output.
- **Added `--start`/`--end`** to `run_research.py` (narrows signal dates, reports rather than
  silently grants an out-of-range request) and `bootstrap_history.py` (passed through to
  `YahooFinanceProvider.fetch_history_batch`'s new `start`/`end` kwargs).
- **Added a real 2026 entry to `config/nse_holidays.yaml`** with explicit per-line provenance
  (fixed-date convenience defaults vs. one maintainer-supplied fact vs. not-yet-verified
  festival dates), and the exact regression test requested: 2026-09-11 (Friday) correctly rolls
  to 2026-09-15, skipping the 2026-09-14 holiday, reading the real config file.
- **Added `tests/unit/test_corporate_action_synthetic.py`** — proves gap-flagging and
  CA-reconciliation mechanics work, proves the baseline's own overshoot ceiling incidentally
  rejects large corporate-action artifacts, AND explicitly documents (via a passing test) the
  honest limitation that a small corporate-action jump can still land inside the mandatory
  window without a real CA feed to explain it.
- **A genuine bug caught by this release's own test-writing process:** the first draft of
  `latest_completed_weekly_bar` inferred week-completeness from "the last trading day present in
  the data", which is unsafe under truncation (a replay with only 2 of 5 days of a week would
  have treated that partial week as complete). Fixed to use the ISO calendar's fixed Friday date
  instead — a calendar fact, never a data-presence artifact.
- **Explicitly NOT implemented, and documented as such rather than faked** (PHASE 29's own
  instruction): a true corporate-action-adjusted continuous research price series (needs real CA
  data this repository doesn't have); real point-in-time NIFTY-200 membership (same); White's
  Reality Check / deflated Sharpe / probability-of-backtest-overfitting statistics (implementing
  these incorrectly would be actively misleading).
- 26 new tests (119 → 145); `ruff`/`mypy` remain clean.

## 1.1.0 — Real-data fixes: historical bootstrap, diagnostics, run-health, price basis

Prompted by a real run against live NSE data, which confirmed the NSE bhavcopy/NIFTY-200 parsing
worked correctly on the first try but surfaced a genuine architectural gap: daily EOD ingestion
appends one session at a time, so a fresh install had exactly 1 row of history per stock and
every security failed the 30-row minimum.

- **Added `scripts/bootstrap_history.py`** — the missing one-time/occasional historical backfill
  (yfinance, configurable 1y/2y/5y/10y/max), with a coverage report (populated / insufficient /
  failed-download counts, median/min/max rows). This closes the P0 gap.
- **Fixed: `Diagnostics` only contained PASS rows.** Every successfully-attempted universe member
  now gets a row regardless of outcome (PASS/FAIL/INSUFFICIENT_HISTORY/STALE/etc.).
- **Fixed: invalid failure accounting.** `Mapping_OK = universe_count - len(scan_log)` (could go
  negative, conflated failure types) replaced with explicit `data_validation_failures` (routine)
  vs. `security_scan_failures` (real bug signal).
- **Fixed: `RUN_HEALTH` went RED purely because the benchmark was unavailable**, even though the
  baseline BB+HA signal doesn't need one. Now RED is reserved for the baseline scan itself being
  untrustworthy; benchmark-down alone caps the result at YELLOW.
- **Fixed: price-basis metadata could lie.** The run manifest used to report the *configured*
  `price_basis` rather than what the provider actually returned. Also fixed a deeper issue: once
  bootstrap (yfinance/ADJUSTED) and daily ingestion (NSE/RAW) share one store, a naive read could
  silently blend them — `data/storage.py` now prefers NSE per date and reports the resulting
  `RAW`/`ADJUSTED`/`BLENDED` composition explicitly per symbol.
- **Fixed: holiday-year over-requiring.** A September run no longer demands the *previous*
  January's holiday data it could never touch. Production runs now **block** on a genuinely
  missing required year (`--allow-missing-holidays` is the explicit opt-out) instead of silently
  degrading.
- **Fixed: `run_daily.py` ignored `universe_scope`.** It always built the NIFTY 200 provider
  regardless of config. Added `universe/factory.py` as the single selection point and
  `universe/mainboard.py` — a real `NSE_MAINBOARD_EQ` provider derived from the live NSE security
  master, not a hard-coded list.
- **Added:** year-partitioned Parquet storage (a daily append no longer rewrites the entire
  multi-year history file).
- Added 33 new tests (86 → 119) covering all of the above; `ruff`/`mypy` remain clean.

## 1.0.0 — Initial modular repository

- Migrated the validated BB+HA baseline strategy and breakout-state logic out of
  `legacy/Nifty200_BB_HA_Research_Scanner_v2.ipynb` into a tested Python package (see
  `MIGRATION.md` for the full module-by-module map).
- Fixed BUG 1 (calendar-day staleness → trading-session-based), BUG 5 (mislabeled ATR → genuine
  Wilder recursion), BUG 6 (flat history floor → tiered thresholds), BUG 8 (overlapping events
  counted as independent → `ALL_SIGNAL_DAYS` vs `INDEPENDENT_EVENTS`), BUG 11 (static split
  called "walk-forward" → genuine rolling windows), and others — see `AUDIT.md` for the complete
  list with before/after status.
- Added: NSE trading-session calendar, ISIN-based security master, DuckDB + Parquet storage,
  daily EOD ingestion pipeline with availability checking, a genuine capital-constrained
  portfolio backtest, GitHub Actions CI/EOD/research workflows, `--offline-fixture` mode, Windows
  one-click launchers.
- Known limitations documented in `README.md` and audited category-by-category in
  `CODE_REVIEW.md` — most notably, no real NSE fixture files or live network access were
  available while building this repository; the NSE parsing logic is tested against synthetic,
  schema-documented fixtures only.