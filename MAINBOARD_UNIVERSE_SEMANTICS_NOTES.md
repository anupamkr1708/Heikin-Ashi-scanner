# Mainboard universe semantics — forensic exercise + scoped implementation notes

**Base:** `main` @ `5bd3d7418615285ef3618184bd6ba5de3083bdde` (PR #4 merged — independently re-verified
against the real remote this session: 211 tests, ruff clean, ruff format clean, mypy clean, all
reproduced directly, not assumed).
**This branch:** `feat/mainboard-universe-semantics` (created locally this session — did not exist
on the remote; same situation as the prior `backup/pre-p1-provider-hardening` branch).

**Scope of what was actually implemented:** explainability/diagnostics infrastructure,
deterministic EQ/BE identity resolution, and an inert (default-empty) exclusion hook for a future
authoritative ETF/REIT/InvIT list. **The `NSE_MAINBOARD_EQ` inclusion predicate itself
(`Series.isin(("EQ", "BE"))`) was deliberately left unchanged** — the fields that would justify
narrowing it (`DelFlg`, `PrtdToTrad`, `ElgbltyNrmlMkt`, `SctyStsNrmlMkt`) remain genuinely
unresolved; see the evidence matrix below.

## What changed, concretely

- `data/nse_reports.py`: `deduplicate_mainboard()` (deterministic EQ-over-BE identity resolution,
  evidence-backed, explainable — replaces undocumented file-row-order dedup) and
  `compute_mainboard_diagnostics()` (cardinality funnel + diagnostic-field presence detection).
  `MAINBOARD_DIAGNOSTIC_FIELDS` and `MAINBOARD_SERIES_DEDUP_PREFERENCE` constants.
- `universe/mainboard.py`: uses the above; adds `excluded_symbols`/`excluded_isins` (both default
  `frozenset()` — inert) and `last_diagnostics` (populated after `get_constituents()`) to
  `NSEMainboardEquityUniverseProvider`. `get_constituents()`'s return signature is unchanged
  (still the `UniverseProvider` ABC's `tuple[DataFrame, str, str]`).
- `universe/validation.py`: `UniverseSnapshot` gains `diagnostics: dict | None = None`.
- `testing/synthetic_market_data.py`: `generate_mainboard_semantics_fixture_cases()` — 11
  synthetic (never real) rows covering the Step-8 case list, in real UDiFF raw-column-name shape.
- 18 new tests across `tests/unit/test_mainboard_diagnostics.py`,
  `tests/unit/test_mainboard_deterministic_dedup.py`,
  `tests/integration/test_mainboard_known_non_equity_exclusion.py`.

**Confirmed regression-free:** all 211 pre-existing tests pass unmodified; with the new hooks at
their defaults, `NSEMainboardEquityUniverseProvider` produces byte-identical output to before —
proven directly by `test_default_empty_exclusion_sets_change_nothing`, not just asserted.

## Evidence matrix

| Field | Observed values (prior real-file audit) | Meaning | Role in universe selection today | Confidence | Source |
|---|---|---|---|---|---|
| `SctySrs` (Series) | EQ, BE, (BZ/N1 not in the real snapshot but real elsewhere) | EQ = standard rolling-settlement equity; BE = compulsory-delivery trade-to-trade **surveillance reclassification of an existing EQ company**, not a distinct security | Inclusion predicate (`Series.isin(("EQ","BE"))`, unchanged) + dedup tie-break (EQ preferred) | **CONFIRMED** (re-verified this session against real NSE GSM/surveillance circulars, independently of the prior session's citation) | NSE Legend of Series; multiple real NSE surveillance circulars (`SURV/68544`, `SURV/72261`, etc.) showing "Series Change from EQ to BE" as an action on an existing company |
| `DelFlg` | N=3999, Y=458 (within EQ) | Believed: N=not removed, Y=removed from the file, correlated with `RmvlDt` | Not filtered on (surfaced as a diagnostic only) | INFERENCE (naming + adjacent `RmvlDt` column; not independently confirmed against a primary spec this session either) | — |
| `PrtdToTrad` | 0=3389, 1=1068 (within EQ) | "PermittedToTrade" — current/operational trading-permission flag, explicitly distinguished from "Eligible" by NSE circular NSE/MSD/67344 (cited by the prior session; **not re-fetched by me this session**, so held at moderate confidence, not upgraded to CONFIRMED) | Not filtered on | INFERENCE (carried from prior session's citation, not independently re-verified this session — flagged explicitly rather than silently inherited as fact) | NSE/MSD/67344 (not re-fetched this session) |
| `ElgbltyNrmlMkt` | 0=1765, 1=2692 (within EQ) | Believed: current Normal-Market segment eligibility, structurally adjacent to but distinct from `PrtdToTrad` | Not filtered on | INFERENCE | — |
| `SctyStsNrmlMkt` | values observed: 1, 2, 3, 6 | Unknown exact code meanings | Not filtered on | **UNKNOWN** | — |
| `FinInstrmTp` | not in the prior real-file audit's field list; found this session | `STK` observed for **both plain stock and ETF rows** in NSE's CM bhavcopy (a different, related NSE file) — does NOT distinguish ETF from ordinary equity | Not used | INFERENCE, well-corroborated (real column list + real code comment from an independent, unrelated open-source NSE bhavcopy integration found via web research this session) | GitHub PR `dev-pmallapp/jesse#62` (independent project); `stocktrack.co.in` bhavcopy column reference |
| ETF identifying mechanism | 48 ETF rows observed in EQ/BE in the prior real-file audit | Likely **not a field inside the CM-MII security master at all** — NSE separately publishes an ETF security list (commonly referenced as `eq_etfseclist.csv`), cross-referenced by symbol/ISIN | Not implemented (inert `excluded_symbols`/`excluded_isins` hook exists, unpopulated) | INFERENCE, well-corroborated but **not primary-sourced** (same independent project: "Symbol catalog built from EQUITY_L.csv and eq_etfseclist.csv (kind Stock or ETF)... Catalog: 2,583 stocks and 351 ETFs") | GitHub PR `dev-pmallapp/jesse#62` |
| REIT / INVIT identifying mechanism | 0 observed in the prior real-file audit's snapshot | NSE's own public communications treat "equity shares, REITs and InvITs" as three explicitly distinct categories (not "REITs are a kind of equity share") | Not implemented | INFERENCE (regulatory-framing evidence, not a field-level spec) | Business Standard coverage of NSE's 2021 Nifty-index-eligibility circular for REITs/InvITs |
| `ISIN` | EQ: 4457 rows/4457 symbols/4435 ISIN; EQ+BE: 7808/4463/4463 | Stable economic identity of the underlying security | Carried through to output; **not** yet the dedup key (dedup is Symbol-keyed) — a documented, tested limitation, not silently fixed here | CONFIRMED (structural, from the count relationships themselves) | Prior real-file audit's own counts |
| `FinInstrmId` | EQ: 4457 rows = 4457 unique | Appears to be a per-(symbol,series)-row instrument ID | Surfaced as a diagnostic field only | INFERENCE | — |

## Authoritative sources actually consulted this session

- NSE Legend of Series + multiple real NSE GSM/surveillance circulars (EQ→BE reclassification) —
  fetched/read this session, independent of the prior session's citations.
- GitHub PR `dev-pmallapp/jesse#62` — an unrelated, independent open-source NSE-bhavcopy
  integration (merged Sep 23, 2026), found via web research. Real column list (`FinInstrmTp`,
  `FinInstrmId`, `SctySrs`, ...), real code behavior (`FinInstrmTp=STK` for both stock and ETF;
  `series EQ > BE > BZ` dedup preference — independent corroboration of the EQ-preferred choice
  made in this patch; `eq_etfseclist.csv` as the ETF cross-reference file). **This is a secondary
  source** (another project's engineering description, not NSE's own official documentation) —
  treated as well-corroborated INFERENCE, not CONFIRMED, throughout the evidence matrix above.
- Business Standard coverage of NSE's 2021 REIT/InvIT Nifty-index-eligibility circular.
- NSE/MSD/67344 (`PrtdToTrad`) — **not re-fetched this session**; inherited from the prior
  session's citation at reduced confidence, explicitly flagged as such above rather than silently
  treated as re-confirmed.
- The NSE UDiFF Catalogue Version 4.0 spreadsheet (the actual field-by-field spec) was located in
  a prior session but could not be read as a binary spreadsheet with available tools, and this
  sandbox has no network path to `nseindia.com`/`nsearchives.nseindia.com` (re-confirmed this
  session via direct `curl` — see `DAILY_ASOF_PROVENANCE_HARDENING_NOTES.md`). This remains the
  single highest-value next research artifact.

## Universe contract for NSE_MAINBOARD_EQ — current, honest state

**This is a description of what the code does today, not a claim that the semantics are fully
proven.** Included: `Series` in `{EQ, BE}`, minus any symbol/ISIN in `excluded_symbols`/
`excluded_isins` (empty today), deduplicated one row per `NSE_Symbol` with EQ preferred over BE.
Excluded: every other `Series` value (SME `SM`/`ST`, and, per this session's new evidence, `BZ`
and debenture-style series such as `N1`). Lifecycle/trading-eligibility rules
(`DelFlg`/`PrtdToTrad`/`ElgbltyNrmlMkt`/`SctyStsNrmlMkt`): **none applied** — genuinely
unresolved, not silently assumed. Identity: `NSE_Symbol`, not `ISIN` — a known, tested limitation
(a same-ISIN/different-symbol row is NOT merged; see
`test_same_isin_different_symbol_is_a_known_limitation_not_silently_deduped`). Point-in-time:
**none** — this is a current snapshot only; no historical/PIT security-master mechanism exists.
Failure conditions: unchanged (`UniverseIntegrityError` on retrieval failure or a constituent
count outside `[min_count, max_count]`). Provenance: unchanged plus the new `diagnostics` dict
now available via `UniverseSnapshot.diagnostics` / `provider.last_diagnostics`.

## Explicitly NOT done, and why

- **No change to `Series.isin(("EQ","BE"))`.** Step 9 of this exercise is explicit: implement the
  predicate only after semantics are proven. They are not.
- **No `DelFlg`/`PrtdToTrad`/`ElgbltyNrmlMkt`/`SctyStsNrmlMkt` filtering.** Same reason; see the
  evidence matrix's confidence column.
- **No ETF/REIT/InvIT filtering, heuristic or otherwise.** The forensic exercise explicitly
  forbids symbol-name heuristics without documenting the limitation; rather than add a heuristic
  and disclaim it, no filter was added at all. The `excluded_symbols`/`excluded_isins` hook exists
  and is tested, but ships empty.
- **No regression tests claiming "no ETFs/REITs/InvITs enter the universe."** The exercise's own
  Step 12 asks for these, but writing them would mean asserting a guarantee that does not actually
  hold today (no real exclusion list is wired in) — that would be implementing the predicate
  through a test rather than through the field semantics the exercise itself says to prove first.
  Tests instead prove the *mechanism* works and remains inert by default (see
  `test_mainboard_known_non_equity_exclusion.py`).
- **No ISIN-based identity/dedup.** `models/security.py` already states ISIN as the intended
  canonical identity, but wiring that through `universe/mainboard.py`'s actual dedup would be a
  larger identity-model change than "deterministic tie-break for the existing Symbol-keyed
  approach" — flagged as the concrete next step, not attempted here.

## Risks / unresolved (unchanged from the prior forensic report, plus this session's additions)

Everything the prior forensic report flagged remains open. Added this session: NSE's real
snapshots evidently include series beyond `EQ`/`BE` (e.g. `BZ`) that the current predicate's own
docstring doesn't discuss — worth explicit acknowledgment even though this patch doesn't change
behavior for them. The `PrtdToTrad` circular citation is currently one level less confident than
previously stated, since it was not re-verified this session.

## Proposed next step

Fetch and cross-reference NSE's ETF security list (candidate: `eq_etfseclist.csv`, by analogy with
`EQUITY_L.csv`'s existing URL pattern in `SECURITY_FILE_URL_TEMPLATES`) from a machine with real
NSE network access, verify it against a real current CM-MII file, and only then populate
`excluded_symbols`/`excluded_isins` (or extend the discovery/fetch pipeline with a dedicated ETF-
list fetcher) with a real, sourced, provenance-tracked list — never a symbol-name heuristic.
