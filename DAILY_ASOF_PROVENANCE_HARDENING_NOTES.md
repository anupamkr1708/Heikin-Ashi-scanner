# Daily as-of + provenance hardening — checkpoint notes

**Base:** `feat/p1-provider-hardening` @ `23bc175e87048e1031750c5b1c6345aa9f65e8fa`
(tree `4fedda6c3dfab632fa6e5050eb076c65aa42e8a0` — independently re-verified against the pushed
branch at the start of this session, not assumed).
**Backup branch:** `backup/pre-p1-provider-hardening` -> `d92f0b9` (created locally this session —
did not previously exist on the remote under that name; see "Discrepancies" below).
**This branch:** `feat/daily-asof-provenance-hardening`, two commits:
1. `fix: bind daily/run_scan benchmark provider to the completed session`
2. `fix: security-master source-date provenance, not retrieval-timestamp`

**Scope:** exactly the two high-confidence fixes forensically identified in the prior audit turn.
No strategy changes, no new indicators, **no `NSE_MAINBOARD_EQ` universe predicate change** — EQ
vs EQ+BE, `DelFlg`, `PrtdToTrad`, `ElgbltyNrmlMkt`, `SctyStsNrmlMkt`, and ETF/REIT/INVIT
identification all remain genuinely open and are **not** touched here.

---

## Discrepancies found before editing (Part 7 git discipline)

The handoff for this patch gave a HEAD SHA that did not match the real repository in either of
its two stated variants; the real, verified HEAD (`23bc175e87048e1031750c5b1c6345aa9f65e8fa`) was
used instead. The stated `backup/pre-p1-provider-hardening -> d92f0b9` branch did not exist on the
remote (nor locally) — it was created locally in this sandbox at the start of this session; no
push credentials are available here, so it exists only in this sandbox until pushed for real.

## Issue A — daily/run_scan benchmark as-of binding

**Root cause (re-confirmed by direct grep, this session, before editing):**
`cli/run_daily.py:134` and `cli/run_scan.py:66` both constructed
`YahooBenchmarkProvider(cfg.data)` with no `as_of_date` — `session` was already in scope at both
call sites and simply never used for this. `cli/run_replay.py:113` already correctly does
`YahooBenchmarkProvider(cfg.data, as_of_date=session.expected_completed_session)`. When
`as_of_date` is `None`, `data/benchmark.py` uses an unbounded rolling `period=` request and skips
its response-layer cutoff filter entirely (`if self.as_of_date is not None: ...`).

**Fix:** both call sites now pass `as_of_date=session.expected_completed_session` — the exact
field `run_replay.py` already binds to. No change to `benchmark.py`, `run_replay.py`, or the
`BenchmarkProvider` interface.

**Tests:** `tests/integration/test_daily_benchmark_asof_binding.py`, run against the *real*
`cli.run_daily.main()` / `cli.run_scan.main()` entry points (session resolution mocked to a fixed,
deterministic `SessionInfo` matching the project's own worked example — run date 2026-09-24,
completed session 2026-09-23 — so the test doesn't depend on the real holiday calendar or on
`--date`/`--as-of`'s "force to NSE close" semantics, which would make the forced date itself the
completed session rather than the intended scenario):
- Call-site binding: spies on the real `YahooBenchmarkProvider` class as imported into each CLI
  module and asserts the actual constructor call carries
  `as_of_date=session.expected_completed_session`.
- No-lookahead: a mocked `yfinance.download` response spanning 2026-09-22/23/24 must not let the
  2026-09-24 row reach `Market_Regime.Index_Close`.

Verified with a `git stash`/`stash pop` round-trip: all 4 tests fail on the pre-fix call sites
(the 2026-09-24 row, given a deliberately implausible value, was observed leaking straight through
into `Market_Regime`) and pass after the fix.

## Issue B — security-master source-date vs retrieval-timestamp provenance

**Root cause (re-confirmed, this session):** `nse_reports.py::snapshot()` set
`UniverseSnapshot.snapshot_date = result.retrieved_at.date()`. For the real scenario in the prior
audit (`NSE_CM_security_23092026.csv.gz`, report date 2026-09-23, retrieved 2026-09-24), this
silently recorded the snapshot as being *of* 2026-09-24 — the download timestamp, not the file's
own date.

**Fix:** `DiscoveryResult` and `SecurityFileResult` now carry `source_date: date | None`, set at
the exact point `discover_report` already knows it (the `candidate_date` used to build the
winning dated URL) — not re-derived by parsing the URL string after the fact. It is `None` only
when discovery found nothing, or matched the undated `EQUITY_L.csv` fallback template (no date is
embeddable there at all). `snapshot()` now sets `snapshot_date=result.source_date` and **raises
`DataProviderError`** — rather than silently falling back to `retrieved_at` — when `source_date`
is `None`. `retrieved_at` is untouched and still recorded on both `SecurityFileResult` and
`UniverseSnapshot`.

**Scope note found while re-tracing:** `snapshot()` is currently invoked **only from tests** —
`universe/mainboard.py::NSEMainboardEquityUniverseProvider.get_constituents()` (the live path)
builds its DataFrame directly and never calls `snapshot()`. This fix is correct and worth having
now, but it does not change any live-run output today; it corrects the record for whenever
`snapshot()` is wired into the live path. **Not fixed** (explicitly out of scope, flagged for a
follow-up): `universe/validation.py:68`'s `build_snapshot()` (the separate NIFTY_200 path) has the
identical `retrieved_at.date()` pattern.

**Tests:** `tests/unit/test_security_master_provenance.py` (5 new): filename→`source_date`
parsing, retrieval timestamp differing from source date without altering it, both dates surviving
into `UniverseSnapshot`/`to_dict()` separately, and the undated-template case raising instead of
substituting. One pre-existing test
(`test_security_master_discovery.py::test_snapshot_marks_invalid_when_mainboard_is_empty`)
updated to supply a `source_date` in its fixture — its actual intent (empty mainboard ->
`INVALID`) is unrelated to provenance and was incidentally tripping the new fail-safe.
`git stash`/`stash pop` verified: all 5 fail on the pre-fix code (the exact conflation —
`snapshot_date` coming out as the retrieval date, `2026-09-24`, instead of `2026-09-23` — was the
observed failure) and pass after.

## Strategy immutability (Part 5)

`bb_ha_v1_base` and its parameters (`bb_period=20`, `bb_std_mult=2.0`, `bb_ddof=1`,
`max_bb_overshoot_pct=4.0`, `min_ha_body_pct=1.0`) are defined in `config.py`, which this patch
does not touch — confirmed by an explicit `git diff` against the P1 baseline for `config.py` and
`indicators/` (empty diff, both files). `tests/unit/test_bollinger.py`, `test_heikin_ashi.py`,
`test_lookahead.py`, `test_weekly_no_lookahead.py`, and both replay no-lookahead integration test
files (25 tests total) were re-run, unmodified, and all pass.

## Sandbox network reality (unchanged from the P1 checkpoint, re-verified this session)

This sandbox's outbound network still does not include Yahoo Finance or `nseindia.com` —
re-verified directly, not assumed:

```
$ curl -sI https://query1.finance.yahoo.com
HTTP/2 403
x-deny-reason: host_not_allowed

$ curl -sI https://nsearchives.nseindia.com/content/cm/NSE_CM_security_23092026.csv.gz
HTTP/2 403
x-deny-reason: host_not_allowed
```

Consequently, the "smoke validation" for this patch is the mocked-network-but-real-code-path CLI
test described under Issue A (`test_daily_benchmark_asof_binding.py`) — it exercises the actual
`main()` functions, actual `resolve_download`/`benchmark.py` logic, and actual `Market_Regime`
construction, with only the HTTP layer (`yfinance.download`) mocked. It is not a substitute for a
genuinely live run against real Yahoo/NSE endpoints, which requires a machine with real network
access — see commands below.

## Exact commands to reproduce locally (where live network is available)

```
git checkout feat/daily-asof-provenance-hardening
pip install -e ".[dev]"
pytest tests/ -v
ruff check src/ scripts/ tests/
mypy src/nse_scanner
python -m build --sdist --wheel --no-isolation

# Live smoke test distinguishing run date / completed session / benchmark as-of (requires real
# network): run on a date where "today" is a trading day, and confirm the printed
# EXPECTED COMPLETED SESSION / BENCHMARK AS-OF are one session behind when run before NSE close,
# and that no Market_Regime row is dated after that session.
python scripts/run_daily.py --skip-ingest
python scripts/run_scan.py --universe NIFTY_200
```

## Remaining unresolved universe questions (unchanged, explicitly not addressed here)

- `NSE_MAINBOARD_EQ` predicate: EQ vs EQ+BE, `DelFlg`, `PrtdToTrad`, `ElgbltyNrmlMkt`,
  `SctyStsNrmlMkt` — see the prior forensic report's Parts B–E. Candidate B (`EQ` ∧ `DelFlg=="N"`)
  remains the leading candidate on field semantics, not implemented.
- ETF/REIT/INVIT identification: exact field still unidentified from evidence available so far.
- Deterministic EQ/BE dedup preference (currently first-row-wins via undocumented file order).
- Point-in-time security-master support for historical replay: does not exist; the existing
  `SURVIVORSHIP_BIAS_PRESENT`/`CURRENT_UNIVERSE_HISTORICAL_SIMULATION` disclosure pattern used for
  NIFTY_200 has not been extended to `NSE_MAINBOARD_EQ`.
- `universe/validation.py:68`'s identical retrieval-date-as-snapshot-date bug (NIFTY_200 path) —
  not fixed in this patch.

**Proposed next forensic step:** obtain a real, current CM-MII security file (or the NSE UDiFF
Catalogue Version 4.0 spec — located but not machine-readable from this sandbox in the prior
session) from a machine with live NSE network access, to pin down the ETF/REIT/INVIT field and
the exact `SctyStsNrmlMkt`/`ElgbltyNrmlMkt` code semantics before touching the universe predicate.
