# MAINBOARD_UNIVERSE_SPEC.md — universe definitions & canonical identity (Tasks 3–4)

This is the "universe contract" file that `MAINBOARD_UNIVERSE_INTEGRITY_V2_AUDIT.md` (Part A.9)
found this project did not have — semantics previously lived only in code comments and prose
notes. Every rule below cites the specific evidence for it (real file, real NSE page, or an
explicit INFERENCE/UNKNOWN flag) rather than asserting a definition first and finding evidence
after. Where this document proposes a *change* from current `main` behavior, it says so
explicitly and gives the reason; it does not silently redefine anything.

---

## Task 3 — target universe definitions

### A. NIFTY_200

**Unchanged by this branch.** Source: `niftyindices.com`/`nseindia.com`'s official index-
constituent CSV (`universe/nifty200.py`). Include: whatever that file lists (an index membership
decision NSE itself makes; this project does not re-derive it). Dedup: bare `Symbol`
`drop_duplicates()` — acceptable because this project has never observed genuine duplication in
this specific source (unlike mainboard's CM-MII master); this is an **assumption carried
forward, not newly re-verified this branch** (Task 11 requires proving NIFTY_200 stays green, not
re-auditing its source). Provenance: `retrieved_at`-based only — this source has no embedded
report date to extract, a genuine, permanent limitation (not a bug to fix).

### B. NSE_MAINBOARD_EQ — revised definition, evidence-backed

**Proposed primary source (change from current `main`):** `EQUITY_L.csv`, at
`https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv` (plural "equities" — see
"Bug found" below), promoted from position-3 fallback to primary, per audit finding B.6.
**Reason:** it is the only one of the three current templates ever actually verified against real
bytes (Audit B.1); it has zero duplicate symbols and zero ETF/REIT/InvIT/SME contamination by
construction; the CM-MII master (current position-1/2 templates) has never been successfully
fetched by this project in any session and its exact schema/URL/cadence remain unverified.

- **Bug found this branch:** the current position-3 template
  (`data/nse_reports.py::SECURITY_FILE_URL_TEMPLATES`) uses
  `.../content/equity/EQUITY_L.csv` (singular). Every independently-verified real source this
  session — a third-party ISIN-database project's fetch config, a Medium walkthrough's working
  code, the `rsquaredacademy/nse2r` R package's own test fixture path, **and a direct
  content-identical search-result match for `INVITS_L.csv` at
  `.../content/equities/INVITS_L.csv`** (plural) — uses `.../content/equities/...`. This is a
  real, likely-broken URL in the current codebase, not a hypothetical; fixed in this branch's
  implementation commit.
- **Include:** `Series` = `EQ`, `BE`, or `BZ` (Audit B.1 confirms all three appear in
  `EQUITY_L.csv`; current `main` only includes `EQ`/`BE`, silently excluding `BZ` without
  discussion — Part D below explains why `BZ` should be included as *mainboard, non-compliant*
  rather than silently dropped as if it were a different instrument type entirely. This is
  a genuine, considered change from current behavior, not a decision this document invents from
  nothing: NSE's own Legend of Series (Audit B.2) defines `BZ` as "securities moved to Trade for
  Trade (Z category)" — a *status* of an existing mainboard-equity company, structurally the
  same category as `BE`, not a separate instrument class).
- **Exclude:** any symbol/ISIN appearing in a same-day-fetched `eq_etfseclist.csv` (ETF),
  `REITS_L.csv` (REIT), or `INVITS_L.csv` (InvIT) — all three now real, fetchable cross-references
  (see Task 7 below), not the inert empty hook current `main` ships. **Moot in practice against
  `EQUITY_L.csv` specifically** (Audit B.4: zero overlap observed), but implemented and applied
  regardless, for defense-in-depth and because a future schema change or a switch back to the
  CM-MII master would make it load-bearing again.
- **Not filtered on (explicitly, not silently):** `DelFlg`, `PrtdToTrad`, `ElgbltyNrmlMkt`,
  `SctyStsNrmlMkt` — genuinely unresolved (Audit B.7); `EQUITY_L.csv` doesn't carry these fields
  at all, so the question is moot for the proposed primary source, but remains open if the CM-MII
  master is ever added back as a richer-schema secondary source.
- **Dedup:** `NSE_Symbol`, EQ preferred over BE preferred over BZ (extending the existing
  `MAINBOARD_SERIES_DEDUP_PREFERENCE`, evidence: same NSE Legend-of-Series status hierarchy).
  Moot against `EQUITY_L.csv` today (zero duplicates observed) but retained for defense-in-depth
  and because it is the correctly-evidenced answer if the CM-MII master (which does have
  duplicates, per the prior 7,810→4,464 dedup funnel) is ever reintroduced.
- **Identity:** `NSE_Symbol` remains the practical dedup/join key (see Task 4) — `ISIN` is
  recorded and carried through, but not yet the join key, an explicit, tested, continuing
  limitation, not silently fixed here.
- **Cardinality gate:** see Task 6 below — no longer `[500, 4000]` against an unverified count;
  a range derived from `EQUITY_L.csv`'s own real, observed shape.
- **Provenance:** `EQUITY_L.csv` has no embedded report date (Audit B.1) — `snapshot_date` is
  honestly recorded as unavailable/unknown for this source rather than invented from
  `retrieved_at`, consistent with the "never invent a source date" principle `nse_reports.snapshot()`
  already enforces for the CM-MII path. This is a real, disclosed limitation of choosing this
  source, not a regression — see Task 8.

### C. Instrument exclusions (cross-cutting)

Defined operationally by categories D–H below — a security is "mainboard equity" (B) if and only
if it is *not* classified into any of D–H. This is a closed-world assumption over the categories
this project has real evidence for (D–G) plus an explicit "everything else observed but
unclassified" bucket (H) — not a claim that D–H is an exhaustive, permanent list of every non-
equity instrument type NSE has ever created (Audit B.2's Legend of Series shows at least a dozen
more: preference shares, convertible/non-convertible debt, warrants, gold bonds, G-Secs, T-Bills,
Block Deals, Rights Entitlements — all real, all excludable in principle, but this project has
verified real reference *lists* only for D–G; H exists precisely so category-less rows are
visible rather than silently mis-classified as equity).

### D. ETFs

Source: `eq_etfseclist.csv` (`https://nsearchives.nseindia.com/content/equities/eq_etfseclist.csv`
by directory-pattern analogy with the confirmed `EQUITY_L.csv`/`INVITS_L.csv` paths — not
independently content-verified this session the way `INVITS_L.csv` was, so held at slightly lower
confidence: **STRONG INFERENCE**, not fully CONFIRMED, pending an actual fetch). Real file
evidence (Audit B.4): 351 rows, 351 unique symbols/ISINs, zero overlap with `EQUITY_L.csv`.
Classification key: `Symbol` and/or `ISINNumber` membership. Evidence for *why* this
cross-reference is structurally necessary (not optional/defensive-only): NSE's own Legend of
Series confirms ETFs share `Series=EQ` with ordinary equity (Audit B.2) — Series alone cannot
distinguish them.

### E. REITs

Source: `REITS_L.csv` (URL: **STRONG INFERENCE** by the same directory-pattern reasoning as D,
one step further removed since even the byte-content match this session was for `INVITS_L.csv`,
not `REITS_L.csv` itself). Real file evidence: 3 rows (`BIRET`, `MINDSPACE`, `EMBASSY`), all
`Series=RR`, confirmed against the real bhavcopy, zero overlap with `EQUITY_L.csv`. **Known
limitation, observed not hypothesized (Audit B.4):** the reference list is a point-in-time
snapshot and can be stale — `BAGMANE` (Bagmane Prime Office REIT) trades under `Series=RR` in the
real 2026-09-25 bhavcopy but is absent from the uploaded `REITS_L.csv`. A production system must
re-fetch this list on a defined cadence (Task 9), not treat one snapshot as permanent truth.

### F. InvITs

Source: `INVITS_L.csv`, URL **CONFIRMED this session** via a direct, content-identical match:
`https://nsearchives.nseindia.com/content/equities/INVITS_L.csv`. Real file evidence: 5 rows
(`OSEINTRUST`, `INDINFR`, `INDIGRID`, `IRBINVIT`, `PGINVIT`), all `Series=IV`. Same staleness
caveat as E: `ANANTAM` (Anantam Highways Trust) trades under `Series=IV` in the real bhavcopy but
is absent from this list.

### G. SME

Source: `SME_EQUITY_L.csv`, URL **CONFIRMED this session** via content match:
`https://nsearchives.nseindia.com/emerge/corporates/content/SME_EQUITY_L.csv` (a structurally
different path space — `/emerge/corporates/...` — reflecting that SME/Emerge is a distinct NSE
platform, not merely a series-code distinction within the mainboard file; Audit B.3 independently
confirms zero symbol overlap with `EQUITY_L.csv` at the file level too). Real file evidence: 572
rows, `Series` in `{SM, ST, SZ}`, exactly mirroring mainboard's `{EQ, BE, BZ}` structurally
(Audit B.2/B.3 — NSE's own Legend of Series documents SM:ST/SZ as the literal SME-platform analog
of EQ:BE/BZ).

### H. Other non-target instruments

Everything else: debt instruments (`N@`/`Y@`/`Z@`/`A@`/`B@` series — non-convertible;
`D@`/`S@` — convertible), Gold Bonds (`GB`), Government Securities (`GS`), Treasury Bills (`TB`),
State Development Loans (`SG`), preference shares (`P@`/`O@`/`Q@`/`F@`), convertible warrants
(`W@`/`K@`), partly-paid equity (`E@`/`X@`), mutual fund units (`MF`/`ME`), Rights Entitlements
(also, confusingly, coded `BE` — Audit B.5), buybacks (`BO`), and Block Deals (`BL` — Audit B.5:
a same-day *reporting line* for an existing mainboard company, not a distinct instrument at all;
see the identity discussion in Task 4). Real evidence: 254 rows in the 2026-09-25 bhavcopy fell
into this bucket after matching against every list this project has for D–G — roughly 7% of that
day's total row count, concentrated in the debt-series codes. **No dedicated exclusion list
exists for H as a whole** — it is defined negatively (not in B, D, E, F, or G) rather than
positively sourced, an honest gap: a genuinely new NSE instrument type this project has never seen
would fall into H by default (correctly excluded from mainboard equity) rather than silently
misclassified as ordinary equity, which is the safe failure direction, but it means H's own
internal composition is not further broken down by sub-type in this branch (flagged as follow-up
work, not attempted here).

---

## Task 4 — canonical security identity

**Decision: `NSE_Symbol` remains the practical join/dedup key for this branch — not changed to
ISIN.** This is a continuation of current behavior, made explicit rather than left implicit, for
concrete evidenced reasons:

1. **`ISIN` is not always available or trustworthy as a universal key across the sources this
   project actually has.** `EQUITY_L.csv`/`REITS_L.csv`/`INVITS_L.csv`/`SME_EQUITY_L.csv` all
   carry it cleanly (1:1 with symbol, per Audit B.1/B.3/B.4), but `universe/security_master.py`'s
   own docstring already documents that NIFTY_200's real source is not confirmed to reliably carry
   it, and `build_security_master` explicitly falls back to `f"UNKNOWN:{symbol}"` rather than
   fabricate one — a real, current, and reasonable design constraint this document is not
   proposing to remove.
2. **A single `NSE_Symbol` can legitimately appear multiple times in the *daily bhavcopy* under
   different `Series` values that are not alternate identities of the company at all** — Audit
   B.5's `BL` (Block Deal) finding. This is not an identity-model problem to solve by preferring
   ISIN over Symbol; it is a *filtering* problem (a Block Deal row must never be treated as "the"
   OHLCV row for that symbol on that day) that is out of scope for the *universe membership*
   question this stage addresses (universe membership answers "is this company in NSE_MAINBOARD_EQ
   at all", not "which bhavcopy row is its EOD close price") — flagged here so it is not lost, and
   explicitly recorded as a scoped-out concern for the ingestion/bhavcopy-parsing layer, not this
   universe layer, to solve.
3. **Company renames / symbol changes are a real, known risk this project has no current
   detection for** (Task 4 asks this to be documented, not necessarily solved this branch): none
   of the 5 real reference files carry an explicit "previous symbol" or "effective date of this
   symbol" field, and this project has not been able to fetch NSE's actual symbol-change circular
   feed. **Explicitly flagged as unresolved, not silently assumed away** — a future universe-drift
   diagnostic (Task 9) comparing today's symbol set against yesterday's snapshot is the practical
   mitigation (new symbol appears + a similarly-named symbol disappears in the same diff is a
   detectable, reportable *signal* of a rename, without requiring this project to authoritatively
   resolve renames itself).
4. **The `BE`-is-overloaded finding (Audit B.5) does not, in practice, create an ambiguous
   `NSE_Symbol` join for mainboard membership**, because Rights Entitlement instruments trade
   under symbols/ISINs structurally distinct from the underlying company's own ordinary-equity
   symbol/ISIN (e.g. a RE instrument's ISIN is a distinct, RE-specific ISIN, not the parent
   company's equity ISIN) — this project has not independently verified a concrete RE row this
   session to confirm that pattern directly (no RE row was observed in the specific 2026-09-25
   bhavcopy this project has), so this remains a plausibility argument, not a confirmed-by-example
   fact, and is recorded as such rather than silently asserted as settled.

**What this section changes vs. current `main`:** nothing to the dedup *mechanism*
(`deduplicate_mainboard` is retained, extended only to rank `BZ` alongside `EQ`/`BE` per Part B).
What it adds is the *documentation* of why Symbol-keyed identity is the considered choice for this
stage rather than an unexamined default — Task 4 asks for evidenced reasoning, not necessarily a
different algorithm.

---

## Explicitly deferred to Task 5+ implementation commits (not yet done as of this document)

- Wiring `eq_etfseclist.csv`/`REITS_L.csv`/`INVITS_L.csv` fetchers into
  `universe/mainboard.py`'s exclusion hook with real data (currently ships empty).
- Promoting `EQUITY_L.csv` to primary source order and fixing the singular/plural URL bug.
- Extending `MAINBOARD_SERIES_DEDUP_PREFERENCE` and `filter_mainboard_equity` to include `BZ`.
- Redesigning the `[500, 4000]` cardinality gate (Task 6) around `EQUITY_L.csv`'s real shape.
- An explicit `instrument_classification` field (`ordinary_equity | ETF | REIT | InvIT | SME |
  other`) per Task 7's acceptance criterion.
- Wiring `nse_reports.snapshot()` into the real CLI paths (Task 1 finding A.7).
- `universe_definition_id` versioning (Task 14).
- The full offline-fixture test matrix (Task 10).

These are the next commits on this branch, built directly against the evidence and decisions
recorded above — not a separate re-derivation.
