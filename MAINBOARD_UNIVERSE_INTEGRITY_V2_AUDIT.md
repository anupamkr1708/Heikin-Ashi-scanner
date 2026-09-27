# Mainboard universe integrity v2 — Task 1/2 audit + forensic findings

**Base:** `main` @ `2cd7226` (PR #6 merged — independently re-cloned and verified this session:
248 tests, ruff clean, ruff format clean, mypy clean).
**This branch:** `feat/mainboard-universe-integrity-v2`, created fresh from `origin/main`.

**Status: audit + forensics only. No implementation code has been changed in this commit**, per
explicit instruction ("Start by auditing the CURRENT origin/main... Do not modify code until that
audit is complete"). Tasks 4–21 (identity model, deterministic dedup implementation, provenance
redesign, tests, reporting) follow in subsequent commits after this findings document is reviewed.

---

## Part A — Task 1: full current-code audit

### A.1 How NIFTY_200 is discovered

`universe/nifty200.py::NSENifty200UniverseProvider.get_constituents()`. Tries, in order, three
official `niftyindices.com`/`nseindia.com` CSV URLs (`NSE_NIFTY200_URLS`), or a user-supplied
`override_csv_path`. Parses via `parse_constituent_csv`, which fuzzy-matches column names
(`"symbol" in lc`, etc.), sanity-checks the resulting count against `[min_count, max_count]`
(default `[150, 210]`), and de-duplicates by `Symbol` with a bare `drop_duplicates()` (no
documented tie-break — acceptable here because NIFTY_200's own index-constituent list is not
observed to contain genuine duplicates, unlike the mainboard case below, but this is an
*assumption*, not a tested guarantee). **Hard-fails** (`UniverseIntegrityError`) if every URL
fails and no override is given — never substitutes a smaller/cached list.

### A.2 How NSE_MAINBOARD_EQ is discovered

`universe/mainboard.py::NSEMainboardEquityUniverseProvider.get_constituents()` calls
`data/nse_reports.py::fetch_security_file()`, an 8-stage pipeline: `discover_report` (scans a
10-day lookback window of dated URL templates, since a security *master* — unlike a daily
bhavcopy — is plausibly only republished when something actually changes) → `resolve_download` →
`download_raw` → `hash_raw` → `validate_artifact` (catches NSE's HTTP-200-with-HTML-block-page
anti-bot failure mode) → `parse_security_file` → `filter_mainboard_equity` → (in the provider)
exclusion-hook + `deduplicate_mainboard` → `compute_mainboard_diagnostics`. Same hard-fail policy
as NIFTY_200: `UniverseIntegrityError` on any stage failure or an out-of-range final count
(`[500, 4000]`), no silent fallback.

**Three candidate source URLs are tried, in this order** (`SECURITY_FILE_URL_TEMPLATES`):
1. `NSE_CM_security_{ddmmyyyy}.csv.gz` (the CM-MII security master — richest schema: `FinInstrmId`,
   `DelFlg`, `PrtdToTrad`, `ElgbltyNrmlMkt`, `SctyStsNrmlMkt`, etc. — **never successfully fetched
   in any session to date**; the one real download attempt this conversation produced a 0-byte
   file due to a mid-stream HTTP/2 error, unusable and correctly not used as evidence anywhere)
2. `security_{ddmmyyyy}.csv.gz` (an alternate dated path — also unverified)
3. `EQUITY_L.csv` (the classic, undated "List of securities available for trading" report — **a
   real copy of this exact file was uploaded this session and is analyzed in Part B below; it is
   the only one of the three templates this project has ever actually verified against real data**)

### A.3 How security-master rows become universe members

`data/nse_reports.py::filter_mainboard_equity()` — a single-line predicate,
`security_df[security_df["Series"].isin(("EQ", "BE"))]`. No other field is filtered on. This
predicate is applied identically regardless of which of the three source templates above actually
won discovery — a significant, previously under-examined risk given Part B's finding that the
templates are **not schema-equivalent** (see A.10).

### A.4 How duplicates are currently resolved

`data/nse_reports.py::deduplicate_mainboard()`. Order-independent (sorts by
`(NSE_Symbol, series-preference-rank, original-row-order)` before dropping), with an explicit,
evidenced tie-break: `MAINBOARD_SERIES_DEDUP_PREFERENCE = ("EQ", "BE")` — i.e. if a symbol has both
an EQ row and a BE row, EQ is kept. Every drop is explained in the returned `dropped_df` via
`Dedup_Reason` (`SERIES_PREFERENCE_EQ_OVER_BE` vs the residual, flagged
`DUPLICATE_ROW_SAME_SERIES_ROW_ORDER_TIEBREAK` case). This was genuinely implemented and tested in
PR #5 — it is real, working code, not aspirational. **What it does not do:** dedup by ISIN (only
`NSE_Symbol`); a same-ISIN/different-symbol row is a known, tested, *unfixed* limitation
(`test_same_isin_different_symbol_is_a_known_limitation_not_silently_deduped`).

### A.5 Which fields are currently retained

Provider output (`NSEMainboardEquityUniverseProvider.get_constituents()`'s returned frame):
`Symbol, Company_Name, Sector (always NA — not in the security master), ISIN, Series`.

### A.6 Which fields are currently discarded

Everything else the security master might carry: `DelFlg, PrtdToTrad, ElgbltyNrmlMkt,
SctyStsNrmlMkt, FinInstrmId, FinInstrmTp`, and any other raw column. These six are not silently
dropped, though — `compute_mainboard_diagnostics()`'s `MAINBOARD_DIAGNOSTIC_FIELDS` records
*whether each is even present* in a given file (`diagnostic_field_presence`), for schema-drift
visibility — but their **values** are never surfaced, and they are never filtered on. `FaceVal`
and `Date_of_Listing`, which the column-candidate map (`SECURITY_FILE_COLUMN_CANDIDATES`) already
knows how to rename, are parsed but then dropped before the provider's final output frame too.

### A.7 What provenance is currently retained — and a real gap this audit found

`data/nse_reports.py::SecurityFileResult` carries good raw provenance (`source_url, retrieved_at,
file_hash, row_count, discovery_attempts, source_date`), and `nse_reports.snapshot()` turns that,
plus the post-filter frame, into a full `UniverseSnapshot` (`schema_version, raw_row_count,
eligible_count, definition`, and the diagnostics dict) — with an explicit, tested refusal to
substitute `retrieved_at` for a genuine source/report date
(`test_security_master_provenance.py`).

**This is dead code in the actual runtime path.** `nse_reports.snapshot()` is imported and called
*only* from two unit tests
(`test_security_master_provenance.py`, `test_security_master_discovery.py`) — grep-confirmed
against every non-test `.py` file in `src/`. Neither `cli/update_universe.py` nor
`cli/run_daily.py` ever calls it. Concretely:

- `cli/update_universe.py` calls the **generic** `universe/validation.py::build_snapshot()` for
  *every* `universe_scope`, including `NSE_MAINBOARD_EQ`. That function sets
  `snapshot_date=retrieved_at.date()` — exactly the retrieval-timestamp-as-source-date
  substitution the mainboard-specific `snapshot()` was written to refuse. It also has no
  `diagnostics` field populated (the parameter exists on `UniverseSnapshot` but `build_snapshot()`
  never passes it), so the persisted `universe_snapshots` parquet table has **no** cardinality
  funnel for a mainboard run — the rich diagnostics dict is computed, logged via
  `logger.info(...)`, and then discarded when the provider object goes out of scope.
- `cli/run_daily.py` never touches `provider.last_diagnostics` at all. Its manifest's
  `universe_snapshot_date` field is hard-set to `session.as_of_date` — the **trading session being
  scanned**, not the security master's own report/effective date. For NIFTY_200 this is a
  defensible simplification (that source has no reliable embedded date to extract in the first
  place — see A.9). For NSE_MAINBOARD_EQ it silently discards exactly the distinction
  `nse_reports.snapshot()` exists to preserve.

**Net finding: the correct-provenance code path exists, is genuinely tested in isolation, and is
never reachable from either CLI entry point that actually persists a snapshot or builds a run
manifest.** This is arguably the single most important Task-1 finding — Task 8/14's "the run
manifest should eventually be able to record... source snapshot date" cannot be satisfied for
NSE_MAINBOARD_EQ today even though the machinery to compute the right value already exists.

### A.8 Whether a point-in-time security-master snapshot is stored and replayable

**No.** `cli/update_universe.py` writes a `security_master_latest` parquet table — the word
"latest" is accurate: each run overwrites it (`store.write_parquet`, not append/versioned).
`universe_snapshots` is append-friendly by shape (one row per run) but, per A.7, carries the wrong
date for mainboard and no diagnostics. There is no mechanism to ask "what was the mainboard
universe on 2026-09-15" — only "what is it now, and here is a possibly-growing log of past
`retrieved_at` timestamps with no accompanying diagnostics." Task 8 is explicit that this stage
should not *claim* PIT support it doesn't have; consider this audit that explicit non-claim,
confirmed at the code level rather than asserted from the docs alone.

### A.9 Where universe semantics are currently encoded

Split across `universe/mainboard.py` (orchestration, exclusion hook, sanity gate),
`data/nse_reports.py` (discovery, parsing, the actual `Series.isin(("EQ","BE"))` predicate,
dedup, diagnostics), and `universe/factory.py` (which `universe_scope` string maps to which
provider). `universe/validation.py::UniverseSnapshot` is the (under-used, per A.7) provenance
record shape shared with NIFTY_200. There is no single "universe contract" file — semantics live
in code comments and in `MAINBOARD_UNIVERSE_SEMANTICS_NOTES.md` (prose, not enforced).

### A.10 Where current behavior is inferred/documented rather than enforced — including a new finding this session

Everything in `MAINBOARD_UNIVERSE_SEMANTICS_NOTES.md`'s evidence matrix marked INFERENCE or
UNKNOWN (reproduced and substantially upgraded in Part B). **New this session:** the three
`SECURITY_FILE_URL_TEMPLATES` are coded as interchangeable fallbacks behind one parsing/filtering
pipeline, but Part B's real-data analysis shows the classic `EQUITY_L.csv` (template 3, the only
one ever actually fetched) and the CM-MII master (template 1, described by the pre-existing
forensic notes' prior-session numbers: 37,854 raw rows, 7,810 EQ+BE, ~3,346 dropped by dedup) are
**not the same kind of file** — see B.6. `filter_mainboard_equity`'s single predicate is applied to
whichever one wins discovery without that difference being documented or tested. This is a real,
previously-unexamined risk this audit surfaces, not a pre-existing documented limitation.

---

## Part B — Task 2: primary-source forensics (this session)

### B.0 Evidence base for this session

**Files uploaded directly by the user this conversation, stated as downloaded from official NSE
sources** (the CM-MII master download itself failed — 0 bytes, correctly excluded from all
analysis below): `EQUITY_L__1_.csv` (2,585 rows), `eq_etfseclist.csv` (351 rows), `REITS_L.csv` (3
real rows + 2 footer/disclaimer rows), `INVITS_L.csv` (5 real rows + 2 footer rows),
`SME_EQUITY_L.csv` (572 rows), and a real daily CM-segment bhavcopy, `2026-09-25_UDIFF.csv` (3,654
rows, the actual current architecture's primary daily data source per this stage's brief).

**Primary NSE documentation fetched directly this session:** NSE's own official
["Legend of Series"](https://www.nseindia.com/static/market-data/legend-of-series) page
(`nseindia.com`, last updated per the page itself 19/09/2024) — the complete series-code
reference table. This resolves nearly every previously-UNKNOWN/INFERENCE series-code question in
one authoritative source; reproduced in full in B.2.

This is a materially stronger evidence base than the prior session's (which relied on a secondary,
independent open-source project's code comments for several of these same questions, explicitly
flagged there as "well-corroborated but not primary-sourced"). Confidence levels below are
re-assessed from scratch against this evidence, not inherited.

### B.1 EQUITY_L.csv: structural findings

- **2,585 rows, 2,585 unique `SYMBOL`, 2,585 unique `ISIN NUMBER` — zero duplication of any kind.**
  Every symbol appears exactly once, with exactly one `SERIES` value. This directly contradicts
  the implicit premise behind needing `deduplicate_mainboard()` *for this specific file*: the
  "same company has both an EQ row and a BE row" scenario that function exists to resolve does not
  occur here. (It may still occur in the CM-MII master — the prior session's 7,810→4,464 dedup
  funnel implies it does there — but that is now confirmed to be a property of *that* file, not a
  universal property of "the mainboard security master" as a concept.)
- `SERIES` distribution: `EQ=2,317, BE=241, BZ=27`. **BZ is real and present** in this exact file —
  not a hypothetical the current `Series.isin(("EQ","BE"))` predicate merely disclaims.
- No embedded "as-of"/report date column, and the filename itself carries none either (unlike the
  CM-MII master's `_ddmmyyyy` naming). **This is a genuine, currently-unsolved provenance gap if
  this file is ever used as the actual production source**: A.7/A.8's "no invented source date"
  principle would have nothing to attach to for this specific file — an honest limitation to carry
  forward into Task 8's design, not a data-file to blame.

### B.2 NSE's official Legend of Series — the authoritative series-code reference

Full table, fetched directly from `nseindia.com` this session (page last updated 19/09/2024 per
its own footer):

| Security | Rolling settlement | Trade for Trade |
|---|---|---|
| Fully paid equity shares/ETFs | **EQ** | **BE/BZ** |
| Fully paid equity shares, SME | **SM** | **ST/SZ** |
| Partly paid equity shares | E@ | X@ |
| Units of mutual funds (closed-ended) | MF | ME |
| Non-convertible preference shares | P@ | O@ |
| Fully convertible preference shares | Q@ | F@ |
| Non-convertible debt instruments | N@, Y@, Z@, A@, B@ | (various) |
| Fully convertible debt instruments | D@ | S@ |
| Convertible warrants | W@ | K@ |
| Units of **InvITs** | **IV** | ID |
| Gold Bond | GB | — |
| Government Securities | GS | — |
| Units of **REITs** | **RR** | RT |
| Rights Entitlement | — | **BE** |
| State Development Loans | SG | — |
| Treasury Bills | TB | — |
| Buyback of equity shares (exchange route) | — | BO |
| **Block Deals** | **BL** | — |

Footnotes (verbatim, from the same page): *"EQ = Presently rolling settlement... BE/ST = Rolling
settlement before July 2, 2001. The same is presently being used for the securities moved to
Trade for Trade (Surveillance). BZ/SZ = The securities moved to Trade for Trade (Z category) for
non-compliance of certain listing conditions as per SEBI circular no. CIR/MRD/DSA/31/2013 dated
September 30, 2013."* And, from NSE's own separate "Securities available for Trading" /
"Market Turnover" page (same fetch): *"Instrument type in Equity includes fully paid equity
shares/ETFs, units of REITs/INVITs and partly paid equity shares."*

**This one table upgrades essentially the entire prior evidence matrix from INFERENCE to
CONFIRMED**, and resolves two things the prior session could not:

1. **Why ETFs carry `Series=EQ`** (previously inferred from an unrelated open-source project's code
   comments): NSE's own segment taxonomy explicitly groups "fully paid equity shares/ETF's" under
   one Rolling-Settlement series code. It is not a data-quality artifact or an omission — it is the
   documented design. Distinguishing an ETF from ordinary equity therefore **structurally
   requires** a cross-reference against a separate list (B.4), not a security-master field, exactly
   as the prior session hypothesized, now confirmed from NSE's own primary documentation.
2. **`BE` is an overloaded code**, used for two semantically unrelated things: (a) an ordinary
   equity company moved to trade-for-trade surveillance, and (b) Rights Entitlement (RE) trading.
   This was not previously documented anywhere in this repository and is a genuine new finding —
   see B.5.

### B.3 SME_EQUITY_L.csv: confirms the EQ:BE :: SM:ST/SZ structural parallel

`SERIES` distribution: `SM=464, ST=107, SZ=1`. Per B.2's table, this is now **CONFIRMED** (not
inferred) as the exact structural parallel to mainboard's EQ:BE/BZ — SM is SME's "rolling
settlement" series, ST is SME's trade-for-trade-surveillance series, SZ is SME's Z-category
non-compliance series. Zero symbol overlap with `EQUITY_L.csv` (572/572 SME symbols, 0 found in
the 2,585-row mainboard file) — SME is a wholly separate list at the file level, not merely a
series-code distinction within one combined file, at least for these two specific source files.

### B.4 eq_etfseclist.csv / REITS_L.csv / INVITS_L.csv: confirms clean separation from EQUITY_L.csv, and reveals list staleness as a real, observable phenomenon

- **351/351 ETF symbols, 0 found in EQUITY_L.csv.** ISINs cross-checked for the (zero) overlapping
  symbols — moot here, but the check itself is now a template for a real membership test.
  **CONFIRMED** (was INFERENCE, secondary-sourced): ETF exclusion via a separate NSE list, cross-
  referenced by symbol/ISIN, is both necessary (per B.2, Series alone cannot distinguish them) and
  sufficient against this file (EQUITY_L.csv already excludes them, so if EQUITY_L.csv is the
  chosen source, no additional cross-reference is even needed — see B.6's recommendation).
- **3/3 REIT symbols (BIRET, MINDSPACE, EMBASSY), 0 found in EQUITY_L.csv**, all carrying
  `Series=RR` in the real bhavcopy — matches B.2's table exactly. **CONFIRMED.**
- **5 InvIT symbols in INVITS_L.csv, but only 3 found in the real 2026-09-25 bhavcopy**, which
  itself carries **15 total `IV`-series rows** — meaning at least one real, currently-trading
  InvIT (identified: `ANANTAM` / Anantam Highways Trust, `Series=IV`, `ISIN` present in the
  bhavcopy) is **not present in `INVITS_L.csv` at all.** Likewise `BAGMANE` (Bagmane Prime Office
  REIT, `Series=RR`) trades in the real bhavcopy but is absent from the 3-row `REITS_L.csv`.
  **This is a real, observed instance of exactly the "list staleness vs. live trading data" drift
  problem Task 9 asks the system to be able to detect** — not a hypothetical. Any design that
  treats these small reference lists as a complete, static membership source will silently miss
  newly-listed instruments between list refreshes.

### B.5 A new, unresolved-until-now identity finding: `BL` (Block Deals) and same-day multiplicity

Cross-referencing `EQUITY_L.csv` symbols against the real bhavcopy's `Series` values surfaced
**2 rows** — `ADANIENT` (Adani Enterprises Limited) and `DALBHARAT` (Dalmia Bharat Limited), both
large, unambiguous, liquid mainboard companies listed as `Series=EQ` in `EQUITY_L.csv` — trading
under `Series=BL` in the same day's bhavcopy. Per B.2's table, `BL = Block Deals`, a *separate
reporting line for a specific trade mechanism*, not a reclassification of the company's listing
status. **Confirmed real, concrete consequence for Task 4/5's identity design:** a single
`(NSE_Symbol)` can legitimately have **more than one row on the same trading day** in the daily
bhavcopy under different `Series` values that do not represent alternate identities of the company
at all — they represent different *transaction types* for the same underlying instrument on that
day. A canonical-identity/dedup design that does not explicitly exclude or separately handle
non-listing-status series (`BL`, and by the same logic `MF`/`ME` fund units, `BE`-as-Rights-
Entitlement per B.2, buyback `BO` rows, etc.) risks either double-counting a company or, worse,
silently preferring a Block Deal's often-thinner, potentially price-distorted print over the
company's real primary-series OHLCV for the day, if a future implementation ever tries to derive
"the" row for a symbol from the combined bhavcopy without series discipline. The current codebase
does not do this today (bhavcopy ingestion is a separate module from mainboard universe
discovery), but this is exactly the kind of assumption Task 4 asks to be made explicit rather than
silently correct-by-accident.

### B.6 The central architectural finding of this forensic pass: two structurally different "security master" concepts exist, and this codebase's three URL templates conflate them

- **Template 1/2 (CM-MII master, unverified in this session):** per the prior session's real-file
  numbers (37,854 raw rows → 7,810 EQ+BE → ~4,464 after dedup), a granular, all-instruments file
  where a single symbol can have multiple EQ/BE rows and — per B.2 — where `Series=EQ` is
  documented to legitimately include ETFs.
- **Template 3 (`EQUITY_L.csv`, verified this session):** a pre-curated "list of securities
  available for trading" that already (a) has zero duplicate symbols, (b) contains zero ETF/REIT/
  InvIT/SME contamination by construction (B.1, B.3, B.4), and (c) is the only one of the three
  templates this project has ever successfully downloaded and inspected.

**These are not interchangeable fallbacks for the same underlying data — they are two different
NSE products with different membership semantics**, and `filter_mainboard_equity`'s single
`Series.isin(("EQ","BE"))` predicate, plus `deduplicate_mainboard`'s EQ-over-BE tie-break, were
designed against the *first* file's known duplication characteristics but have only ever been
*tested* (in the sense of "run against real bytes") against the *second*. This does not mean the
current code is wrong — the predicate and dedup logic both produce a correct, if slightly
redundant (dedup is a no-op on EQUITY_L.csv, per B.1), result on the file this session actually
verified. It means the assumption that all three templates are safely interchangeable is itself
unverified, and should be treated as a design decision for Task 3, not carried forward silently.

**Recommendation for Task 3 (not yet implemented, flagged here for review):** given `EQUITY_L.csv`
is (a) the only template ever actually confirmed downloadable, (b) already free of the ETF/REIT/
InvIT/SME contamination the exclusion-hook machinery exists to solve, and (c) free of the
symbol-duplication problem the dedup machinery exists to solve, it is a strong candidate to become
the **primary** source for `NSE_MAINBOARD_EQ`, with the CM-MII master demoted to an optional
richer-schema enrichment (for `FinInstrmId`/`DelFlg`/etc., if a future session can ever actually
fetch it) rather than the primary path. This would not simplify the Series-code taxonomy question
away (B.2's table still matters for interpreting whatever file is used) but would make the
"expected cardinality" question in Task 6 answerable from a source this project has real numbers
for (2,585 rows, not "somewhere around 4,464 after a dedup step never verified against this exact
file") — flagged as a recommendation for Task 3, not decided unilaterally here.

### B.7 Fields still genuinely unresolved from primary sources

Repeated, targeted web search this session for `PrtdToTrad`, `ElgbltyNrmlMkt`, `SctyStsNrmlMkt`
against NSE/UDiFF documentation surfaced no primary source beyond this project's own prior-session
citations (themselves already flagged there as reduced-confidence, uncited-this-session
inheritance). **Status unchanged: genuinely unresolved, not silently assumed.** `DelFlg` likewise
remains INFERENCE-only (plausible from naming + adjacent `RmvlDt`, never confirmed). These fields
are also only present in the CM-MII master (template 1/2), which per B.6 has never been
successfully fetched by this project — meaning even *if* their semantics were resolved, they
would not currently be usable unless that source becomes reachable, or `EQUITY_L.csv` is adopted
as primary per B.6 (in which case these fields are moot, since that file doesn't carry them at
all).

---

## Part C — carried-forward risk register (updated)

Everything in `MAINBOARD_UNIVERSE_SEMANTICS_NOTES.md`'s prior risk list remains open except where
explicitly upgraded above. **Newly added this session:**

1. `EQUITY_L.csv` has no embeddable source/effective date (B.1) — a real constraint for Task 8's
   design, not a gap to paper over with `retrieved_at`.
2. The three `SECURITY_FILE_URL_TEMPLATES` are not schema-equivalent (B.6) — needs an explicit
   Task 3 decision, not a silent shared-predicate assumption.
3. `BE` is a genuinely overloaded series code (Rights Entitlement vs. surveillance-reclassified
   equity, B.2/B.5) — the current predicate's inclusion of `BE` has always meant "surveillance
   equity" in practice (Rights Entitlement instruments do not carry ordinary equity symbols/ISINs
   and would not plausibly collide with `EQUITY_L.csv`/CM-MII symbols), but this has never been
   explicitly tested or documented before this session.
4. Small reference lists (`REITS_L.csv`, `INVITS_L.csv`) are observably stale relative to a same-
   week live bhavcopy (B.4) — any design treating them as complete rather than point-in-time
   snapshots will under-count.
5. `nse_reports.snapshot()`'s correct provenance/diagnostics machinery is unreachable from both
   real CLI entry points (A.7) — a concrete, scoped implementation gap for Task 3/8, distinct from
   "provenance is unresolved" (the mechanism exists; it's just not wired in).

---

## What this document is not

It is not Task 3's formal universe-definition table (draft only, sketched in B.6's recommendation,
not finalized), not an implementation of any dedup/identity/classification logic, and not a claim
that mainboard is ready to enable in production. Per the acceptance criteria for this stage, that
comes after Tasks 3–17 are actually implemented and tested against this evidence base — next step
pending review of this findings document.
