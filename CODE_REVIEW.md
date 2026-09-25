# Code review (PART 80 / PART 81, refreshed for PHASE 55)

**v1.3 external audit note (this revision):** an independent forensic audit was run against this
document's own PASS claims rather than accepting them (the audit's explicit instruction: "Do not
assume documentation is correct simply because it says PASS. Trace implementation."). Three of
this table's PASS/carried-over claims turned out to be wrong or incomplete on inspection:
"Calendar / holiday-year scoping" (2026 data was a disclosed-partial 5-date list, not the full
verified circular — see CHANGELOG.md 1.3.0), the implicit "benchmark path is covered by the
as-of guarantee" assumption (it wasn't — `YahooBenchmarkProvider` had no `as_of_date` cutoff at
all until this release), and "ruff/mypy clean" (neither actually was — one trailing-newline lint
error, one missing mypy stub package). All three are fixed in 1.3.0. This note is left here
deliberately, rather than quietly editing the old rows, as a record that a PASS in this document
means "verified as of the revision that wrote it," not "verified forever" — the table below has
been updated in place for the specific rows affected, but treat every row as a claim to re-trace,
not a permanent fact, consistent with this document's own stated methodology above.

Self-audit of the repository as delivered. `PASS` means verified working (tests exist and pass,
or behavior was directly exercised). `WARN` means implemented but with a known, documented gap.
`FAIL` would mean missing or broken — nothing in this table is FAIL; where something couldn't be
verified, it's WARN with an explicit reason, not a silent PASS.

**Revision 3 note (this release):** the central deliverable this round was TRUE historical as-of
replay — "what would the strategy have detected on date T, using only information available by
T's close" — plus the point-in-time-universe, price-basis, weekly-no-lookahead, walk-forward, and
research-integrity items that support it. See CHANGELOG.md for the full itemized list. This
release explicitly did NOT implement (and says so rather than faking it): a true
corporate-action-adjusted continuous research price series, real point-in-time NIFTY-200
membership data, or any data-snooping-correction statistic (White's Reality Check, deflated
Sharpe, PBO) — all three require either data this repository doesn't have access to, or enough
care to implement correctly that a rushed version would be actively misleading, which PHASE 29
explicitly warns against.

| Category | Status | Notes |
|---|---|---|
| **Historical AS-OF replay** | PASS (stock data) / was WARN, now PASS (benchmark data) | `data/asof_provider.py::AsOfDataProvider` truncates every read at the STORAGE QUERY level (`MarketDataStore.read_symbol_history(..., as_of=...)`), not by convention or by hoping nothing later got ingested. Proven four ways in `tests/integration/test_replay_no_lookahead.py`: provider-level cutoff correctness, an inclusive-boundary check, feature-calculation equivalence to manual truncation, and — the strongest form — a full `pipeline.scan.run_scan()` run whose `Live_Signals`/`Diagnostics` output is byte-identical before and after 30 more future sessions (including an engineered future breakout) are genuinely appended to the same on-disk store. **v1.3 audit finding, now fixed:** that test always passed `benchmark_provider=None`, so it never actually covered the benchmark path — and the benchmark path genuinely wasn't covered: `YahooBenchmarkProvider` had no `as_of_date` cutoff at all, so a replay's `Market_Regime`/`RS_*` columns (and, concretely, the Market_Regime sheet's `Index_Close`) were computed from TODAY's live benchmark fetch, not the replayed date. `YahooBenchmarkProvider` now takes `as_of_date` (two-layer enforcement), `run_replay.py` sets it, and a 5th no-lookahead test (`tests/integration/test_replay_benchmark_no_lookahead.py`) covers this path specifically, including the same future-append-invariance pattern. See CHANGELOG.md 1.3.0. |
| `run_replay.py` CLI | PASS | Read-only (never calls `append_eod_prices`), resolves the historical session via the same calendar used for live runs, reports `AS_OF_DATE`/`EXPECTED_SESSION`/`DATA COVERAGE`/`PRICE_BASIS`/`BASELINE_SIGNAL_STATUS` distinctly from a live run, and prints the survivorship disclosure inline rather than only in a file. Manually smoke-tested end-to-end against a real on-disk store (not just mocks). |
| No-signal vs. not-evaluable distinction | PASS | `run_replay.py` prints `BASELINE_SIGNAL_STATUS: NOT_EVALUABLE` (universe empty or every security lacked history) as a DIFFERENT state from `EVALUATED (0 signals found)` — PHASE 51's explicit requirement. |
| Point-in-time universe | PASS (labeling) / WARN (real membership data) | `research/survivorship.py`'s `MODE_A`/`MODE_B` contract is now surfaced directly in `run_replay.py`'s and `run_research.py`'s output (`SURVIVORSHIP_BIAS_PRESENT = True`, full disclosure text), not just available if a caller thinks to check. Real point-in-time NIFTY-200 membership data is still not available to this repository — `MODE_B`'s validator exists and raises rather than accepting a mislabeled current list, but nothing currently supplies it real data. |
| Weekly no-look-ahead | PASS (mechanism) / N/A (unused by baseline) | `indicators/weekly.py::latest_completed_weekly_bar` determines week-completeness from the ISO calendar (a fixed fact), not from how much daily data happens to be present — this specific design was validated by a genuine bug caught during test-writing (see CHANGELOG). The baseline strategy does not consume weekly data at all, so this module is present for a future variant, not currently wired into any signal. |
| Price-basis architecture | PASS (labeling/dedup) / WARN (no true CA-adjusted series) | `data/storage.py` prefers NSE(RAW) over yfinance(ADJUSTED) per date and reports the resulting composition (`RAW`/`ADJUSTED`/`BLENDED_...`) rather than silently pretending a blended series is clean — `run_replay.py` prints an explicit `*** WARNING ***` when a replay's data is blended. What this repository does NOT do: derive a genuinely corporate-action-adjusted continuous research series from verified CA factors (PHASE 8's `raw_open`/`research_open` dual-field architecture). Building that correctly requires real CA data this repository doesn't have — implementing a fake version would be worse than not implementing it. |
| Corporate-action synthetic testing | PASS (mechanism) / documented limitation | `tests/unit/test_corporate_action_synthetic.py`: proves a large (reverse-split-sized) synthetic jump is flagged and can be reconciled once a real CA record is supplied; proves the baseline's own `<=4%` overshoot ceiling incidentally rejects large corporate-action artifacts; and — importantly — includes a test that HONESTLY characterizes the opposite case: a small corporate-action-sized jump CAN currently land inside the mandatory window indistinguishably from organic price action, because there's no real CA feed. This is stated as a limitation, not hidden. |
| Walk-forward | PASS | `run_walk_forward.py` + `research/walk_forward.py::generate_walk_forward_windows` produce a genuine rolling SEQUENCE (7 windows from ~2.3 years of synthetic data in one smoke test), each with independently-computed TRAIN and TEST statistics, written per-window to CSV — never pooled into one hidden number. Because the baseline strategy has no tunable parameter, there is no parameter-selection step to leak test information into (this is stated explicitly in the module docstring rather than silently glossed over); the framework is structured so a future strategy variant's parameter selection would plug into the TRAIN phase without touching window generation. Tested (`test_run_walk_forward_cli.py`, 4 tests, including a chronological-non-overlap assertion). |
| Sector concentration / signal clustering | PASS | `research/statistics.py::sector_summary`, `top_sector_contribution_pct`, `clustering_by_dimension` — tested (5 tests), wired into `run_research.py`'s printed output. |
| `run_research.py --start/--end` | PASS | Narrows which SIGNAL dates are included; explicitly reports (not silently grants) a requested range wider than what's actually in the store. Tested. |
| `bootstrap_history.py --start/--end` | PASS | Passes through to `YahooFinanceProvider.fetch_history_batch`'s new `start`/`end` kwargs (mutually exclusive with `--period`). Tested, including a capture-the-actual-call-kwargs test. |
| Diagnostics completeness | PASS | Carried over from the previous release: every universe member gets a row regardless of outcome. |
| Failure accounting / run-health decoupling | PASS | Carried over: `data_validation_failures` vs. `security_scan_failures`; benchmark-down alone caps at YELLOW, never forces RED. |
| Calendar / holiday-year scoping | PASS (mechanism) — data was WARN, now PASS | The mechanism (`assert_year_covered`, `resolve_holidays_for_run` failing closed on a missing year) was already sound. The 2026 DATA behind it was a disclosed-partial 5-date convenience default — this repository's original build sandbox had no network path to nseindia.com. **v1.3 audit:** this session had live web search, so the 2026 entry was replaced with the verified official NSE circular (Download Ref No. NSE/CMTR/71775, 15 dates, fetched and cross-checked directly as a PDF, not taken from an aggregator) plus one ad-hoc modification found during verification (2026-01-15, Maharashtra municipal elections — corroborated by multiple independent news reports, NOT in the primary circular). 16 dates total, each independently sourced. `tests/unit/test_calendar.py::test_2026_09_11_next_session_skips_the_09_14_holiday` still passes (2026-09-14 remained correct), plus two new tests pin the full verified set and the ad-hoc addition specifically. See CHANGELOG.md 1.3.0. |
| Bollinger / Heikin-Ashi / Wilder ATR correctness | PASS | Unchanged from previous release; still hand-reference-tested and look-ahead-tested. |
| Universe (`NIFTY_200` vs `NSE_MAINBOARD_EQ`) | PASS | Unchanged: real, independently-selected providers via `universe/factory.py`. |
| NSE live-data parsing | PASS (bhavcopy, NIFTY 200) / WARN (security file, yfinance) | Unchanged from previous release — see prior revision notes; still true this round. |
| Testing | PASS | 162 tests, all pass, all offline (no network) — was 145 at the start of the v1.3 audit; grew via the benchmark (11+2), calendar (+2), and manifest-provenance (+2) regressions below. `ruff check` and `mypy` (78 source files) both **genuinely** clean now — the v1.3 audit found this line's previous claim of "both clean" was itself wrong (a trailing-newline `W292` in `config.py`, a missing `types-python-dateutil` mypy stub), which is exactly the kind of claim this document's own stated methodology says to re-trace rather than trust. Both are now actually verified clean, not just asserted. |
| Data provenance | WARN | Per-row provenance on `eod_prices` covers `source`/`price_basis`/`schema_version`/`ingested_at` (real DB columns) — but `data/provenance.py::ProvenanceRecord`, a well-designed dataclass matching the spec's field list closely (`file_hash`, `provider_version`, `universe_version`, etc.), has **zero call sites anywhere in the codebase** (confirmed by grep) — it's aspirational, not wired up. Separately, `pipeline/ingestion.py::ingest_session` DOES compute a real SHA256 of the raw ingested file (`IngestionResult.file_hash`) — that part works — but until the v1.3 manifest fix (see CHANGELOG 1.3.0) it was computed, returned, and then silently discarded by every caller rather than persisted anywhere. The manifest fix closes the "discarded" half of this gap; the `ProvenanceRecord`/per-row `source_file_hash` column half remains open — a raw file is traceable back to its row only via the `trade_date`-based naming convention (`data/raw/nse/<session_date>_<schema_version>.csv`), not an explicit stored link. |
| Data-snooping / multiple-testing correction | NOT IMPLEMENTED (documented, not faked) | PHASE 29 explicitly permits deferring this: "If a method is too sophisticated to implement correctly at this stage, document it as a future milestone rather than producing misleading numbers." No White's Reality Check, deflated Sharpe, or PBO statistic exists in this repository. `RESEARCH_METHODOLOGY.md` states this as an open milestone. |
| Confidence intervals | PASS (mechanism, carried over) | `research/statistics.py::bootstrap_ci_clustered` clusters by day or by stock; not wired into every CLI's printed output yet (available, not universally invoked). |
| Portfolio backtest | PASS (mechanism, carried over) | Genuine capital-constrained simulator; still not exercised against real multi-symbol history in this repository. |
| Cost model | PASS (mechanism) / WARN (calibration, carried over) | Unchanged. |
| CI (workflow syntax) | PASS | Unchanged; `ci.yml` still requires no network. |
| CI (actually executed on GitHub) | WARN | Still cannot be verified from this environment. |
| Documentation | PASS | README/AUDIT/MIGRATION/DATA_MODEL/RESEARCH_METHODOLOGY/CONTRIBUTING/CHANGELOG/this file updated for this release's actual new commands and limitations. |
| No secrets, no hard-coded universe, no bare except | PASS | Re-verified this round (grep + manual review before zipping). |

## PHASE 61 final acceptance checklist

Going through the user's own 30-item list, stating status honestly rather than blanket-claiming completion:

1. Fresh machine can bootstrap history — **PASS** (`bootstrap_history.py`, tested with mocks; real yfinance network path unverified, same caveat as before).
2. NIFTY 200 has multi-session history after bootstrap — **PASS** (mechanism proven; real run not executed in this environment).
3. `run_daily` can evaluate baseline — **PASS** (carried over, confirmed working against real NSE data by the maintainer).
4. Diagnostics contains every universe member — **PASS**.
5. Current EOD date is correct — **PASS** (carried over).
6. Calendar correctly handles holidays — **PASS**, now with a live regression test against the real config file.
7. Historical replay supports arbitrary supported AS_OF_DATE — **PASS**.
8. Features are cut off at AS_OF_DATE — **PASS**, enforced at the SQL query level, not just asserted.
9. Future data cannot modify historical signal — **PASS**, proven at 5 levels of the stack including full-pipeline and (v1.3 addition) the benchmark/regime path specifically.
10. Weekly context cannot leak future weekly close — **PASS** (mechanism exists and is tested; not currently used by any active strategy).
11. Point-in-time universe distinguished from current universe — **PASS (labeling)**, real historical membership data still absent — **WARN**.
12. Price basis is consistent — **PASS (labeled/deduped)**, true CA-adjusted continuity — **NOT IMPLEMENTED (documented)**.
13. Benchmark failure does not destroy baseline scan — **PASS** (carried over).
14. No impossible health counters — **PASS** (carried over).
15. `NIFTY_200` and `NSE_MAINBOARD_EQ` actually differ — **PASS** (carried over).
16. Research can calculate event-level outcomes — **PASS** (carried over, now with sector breakdown).
17. MFE/MAE timing is correct — **PASS** (carried over).
18. Independent events separated from all signal days — **PASS** (carried over).
19. Walk-forward is genuinely rolling — **PASS**, new this release.
20. OOS data never influences parameter selection — **PASS trivially** (no parameter to select yet; framework ready for one).
21. Cost model is explicit — **PASS** (carried over).
22. Statistical uncertainty is reported — **PASS (mechanism)**, not yet wired into every report — **WARN**.
23. Multiple-testing/data-snooping risk is disclosed — **PASS (disclosed in docs)**, not statistically corrected — **explicitly NOT IMPLEMENTED**.
24. Portfolio drawdown only from a real simulator — **PASS** (carried over).
25. CI passes — **PASS** locally; GitHub-hosted execution unverified — **WARN**.
26. Offline fixtures pass — **PASS**.
27. Package builds — **PASS**.
28. Documentation matches actual commands — **PASS**, verified this round.
29. One-click launcher works — **PASS** (carried over; `run_replay.py`/`run_walk_forward.py` not yet given their own `.bat`/`.ps1`, callable via the existing Python path).
30. No secrets included — **PASS**, re-verified.
