# P1 provider-hardening — checkpoint notes

**Base:** `4f7a40adb90714f99b2242d934ae392e827f1b34`
**v1.3 checkpoint HEAD (verified against the real pushed `feat/research-integrity-v13`):**
`d92f0b969e17ff50a3222d9adc4c31ec6df34794` — tree `259340413e04000c053095301516e8bcc74b1ff5`
**This checkpoint's branch:** `feat/p1-provider-hardening` (based on the above)
**Scope:** exactly the two concrete provider failures below. No strategy changes, no new
indicators, no baseline changes, no P1 price-basis/corporate-action work — those remain open.

---

## Sandbox network reality (stated once, applies to everything below)

This sandbox's outbound network does not include Yahoo Finance or `nseindia.com`. This was
**verified by direct attempt, not assumed**:

```
$ yf.download('^NSEI', ...)
HTTP Error 403: Host not in allowlist: query2.finance.yahoo.com
HTTP Error 403: Host not in allowlist: query1.finance.yahoo.com

$ curl -sI https://nsearchives.nseindia.com/content/cm/NSE_CM_security_10092026.csv.gz
HTTP/2 403
x-deny-reason: host_not_allowed
```

Everything below marked "verified" means: traced in code, and/or proven against an offline/mocked
test suite built from the *exact* real dataframe shapes and error text in the production report.
Nothing here was confirmed against the actual live NSE/Yahoo endpoints from this sandbox — that
requires running the commands in the "reproduce locally" section on a machine that *can* reach
them.

---

## Issue A — benchmark normalization

**Root cause:** `data/benchmark.py` normalized a raw yfinance frame with
`df.columns.get_level_values(0)` — correct only when level 0 happens to be the OHLCV-field level.
The real production response for `yf.download("^NSEI", group_by="ticker", ...)` was:

```
MultiIndex([('^NSEI','Open'), ('^NSEI','High'), ('^NSEI','Low'),
            ('^NSEI','Close'), ('^NSEI','Volume')], names=['Ticker','Price'])
```

level 0 = Ticker, level 1 = Price — the opposite orientation. Collapsing to level 0 produced five
columns all literally named `'^NSEI'`; none of Open/High/Low/Close/Volume survived.

**Fix — `data/normalization.py::normalize_ticker_frame`:** determines the field level from its
*values* (does the level's distinct values look like a set of OHLCV field tokens —
open/high/low/close/adj close/volume, case/whitespace-insensitive?) rather than position, and
never special-cases a specific ticker string. Works identically for both real observed
orientations (`(Ticker, Price)` and `(Price, Ticker)`), a single-ticker request that still comes
back as a MultiIndex, and extracting one ticker out of a multi-ticker frame. Raises the new
`FrameNormalizationError` with a specific message (which level/values were seen, which field was
missing, which ticker wasn't found) rather than a bare `KeyError`.

**Volume:** confirmed by inspection (`indicators/relative_strength.py::calculate_index_features`
and everywhere downstream) that nothing reads Volume from the benchmark frame — it's now optional.
A zero-volume session (the real observed 2026-09-22 `^NSEI` row, Volume=0) is preserved and
allowed; a genuinely absent Volume column stays absent rather than being fabricated as 0 or NaN.

**As-of contract:** unchanged. `YahooBenchmarkProvider.as_of_date` still enforces the request-layer
`end=` bound and the response-layer post-filter added in the v1.3 checkpoint — this fix only
replaces the column-normalization step inside that same fetch path.

**Tests:** 26 new/changed (`tests/unit/test_normalize_ticker_frame.py`, 15 new, built from the
exact real dataframe shapes and the exact real 2026-09-22 zero-volume row quoted above;
`tests/unit/test_benchmark_provider.py`, +4 net — one outdated test replaced by two reflecting the
corrected Volume-optional semantics, plus the three exact-named as-of tests requested for this
checkpoint alongside the existing differently-named as-of coverage, which was left untouched).

**Known, explicitly out-of-scope finding:** `data/normalization.py::extract_symbol_frame`'s
`batch_size == 1` branch (used by the *stock* batch downloader, not the benchmark path) makes the
identical wrong position-based assumption. Flagged in that function's docstring as a real,
same-class latent bug — not fixed here, since this checkpoint's scope is the two named provider
failures and the stock downloader was not one of them.

---

## Issue B — NSE security-master discovery

**Root cause investigation** (web search + direct fetch of the live nseindia.com "All Reports"
page — the only way to reach it from this session, since this sandbox's own tools can't):

- The filename `NSE_CM_security_ddmmyyyy.csv.gz` is confirmed correct against a real NSE circular
  (NSE/MSD/60315, effective 2024-02-05) — a primary source, not a third-party aggregator.
- This report is **not discontinued**. The "Discontinued... switch to CM-UDiFF" notice visible on
  the live page is attached specifically to the legacy "CM - Bhavcopy(csv)" and "CM - Common
  Bhavcopy (csv)" entries (per circular 62424) — not to either "CM - MII - Security File (.gz)"
  entry, which the live page still lists as an active report.
- **Best-reasoned hypothesis, not independently confirmed against the live endpoint:** unlike the
  bhavcopy (published every trading session), a security/instrument master file plausibly is only
  republished when something actually changes — a listing, delisting, or series change — which
  would explain a 404 on a session with no such change without the report being broken or gone.
- Separately identified, real, and now defended against: NSE's anti-bot layer can return HTTP 200
  with an HTML block/captcha page instead of the file. A bare `raise_for_status()` does not catch
  this.

**Fix — pipeline refactor in `data/nse_reports.py`:**
`discover_report → resolve_download → download_raw → hash_raw → validate_artifact → parse
(unchanged) → derive_mainboard (= filter_mainboard_equity, unchanged) → snapshot`.

- `discover_report` scans a bounded window of recent dates (default 10 days back) per dated URL
  template, using a cheap ~512-byte ranged GET per attempt, and records every single
  (template, date) attempt with a specific outcome (`AVAILABLE` / `NOT_FOUND` / `UNREACHABLE` /
  `INVALID_CONTENT`) — surfaced in full on failure.
- `validate_artifact` rejects empty responses, HTML-looking content, and `.gz` URLs whose bytes
  don't start with real gzip magic bytes — used both as the cheap discovery-time probe and as a
  full post-download check.
- No behavior change to `parse_security_file` or `filter_mainboard_equity` — untouched.
- The real fixture file mentioned in this checkpoint's brief (`NSE_CM_security_10092026.csv.gz`)
  was checked for and is **not present** in this environment. Per the brief's own instruction not
  to invent a schema, `SECURITY_FILE_COLUMN_CANDIDATES` is carried forward unchanged.
- Fail-closed behavior unchanged and re-verified: `universe/mainboard.py`'s
  `UniverseIntegrityError` wrapping needed no changes — it already refuses to fall back to
  NIFTY_200/a hard-coded list/a stale cache, and now simply wraps a richer `DataProviderError`.

`universe/validation.py::UniverseSnapshot` gained optional fields (`source_url`, `file_hash`,
`schema_version`, `raw_row_count`, `eligible_count`, `definition`), defaulted to `None` so the
existing NIFTY_200 caller is unaffected, so the new `snapshot()` stage reuses this type rather than
inventing a parallel one.

**Tests:** 21 new (`tests/unit/test_security_master_discovery.py`) — discovery date-scanning
(including the specific date-mismatch and network-vs-not-found distinctions), the HTML block-page
defense, hash recording, and full-pipeline orchestration with zero fallback on total failure.
Schema failure, suspicious row count, and EQ/BE filtering were already covered by the pre-existing
`tests/integration/test_nse_parser.py` / `test_mainboard_universe.py` and are unchanged.

---

## What was actually run for real against the live network from this sandbox

Both fail identically at the network-egress layer (this sandbox's own block, `x-deny-reason:
host_not_allowed` / yfinance's `Host not in allowlist`), but running them for real still proved
the *mechanics* of the new code execute correctly end-to-end, not just under mocks:

```
$ python scripts/run_replay.py --as-of 2026-09-21 --universe NIFTY_200
UNIVERSE INTEGRITY FAILURE — ABORTING RUN
(fails at the NIFTY_200 constituent fetch, before reaching the benchmark provider at all)

$ python scripts/update_universe.py --universe NSE_MAINBOARD_EQ
UNIVERSE INTEGRITY FAILURE — ABORTING RUN
Could not discover a current CM-MII security file. Attempts:
  - https://nsearchives.nseindia.com/content/cm/NSE_CM_security_22092026.csv.gz -> UNREACHABLE (HTTP 403)
  - https://nsearchives.nseindia.com/content/equity_bhavcopy/security_22092026.csv.gz -> UNREACHABLE (HTTP 403)
  - https://nsearchives.nseindia.com/content/equity/EQUITY_L.csv -> UNREACHABLE (HTTP 403)
  - ... (repeats across the 10-day lookback window, all UNREACHABLE)
```

The second run confirms `discover_report`'s date-scanning and rich per-attempt diagnostic fire
correctly in a real (not mocked) execution — but every attempt is `UNREACHABLE (HTTP 403)`, this
sandbox's own egress block, **not** NSE's real 404 response. This is evidence the new mechanism
works mechanically; it is not evidence the underlying discovery/schema issue is actually resolved
against the real service. That requires the commands below, run somewhere with real network access.

---

## Exact commands to reproduce locally (where live network is available)

```
git checkout feat/p1-provider-hardening
pip install -e ".[dev]"
pytest tests/ -v
ruff check src/ scripts/ tests/
mypy src/nse_scanner
python -m build --sdist --wheel --no-isolation

# Live smoke tests (require real network to nseindia.com / Yahoo Finance):
python -c "
from nse_scanner.config import DataConfig
from nse_scanner.data.benchmark import YahooBenchmarkProvider
df, err = YahooBenchmarkProvider(DataConfig()).fetch_benchmark('^NSEI', period='1mo', interval='1d')
print(err or df.tail())
"
python scripts/run_replay.py --as-of 2026-09-21 --universe NIFTY_200
python scripts/update_universe.py --universe NSE_MAINBOARD_EQ
```

---

## Remaining limitations (unchanged from v1.3 except where noted)

Everything listed as open in `RESEARCH_INTEGRITY_V13_NOTES.md` remains open. Additionally, specific
to this checkpoint:
- The security-master 404 fix is **evidence-based but unverified against live NSE** — see caveat
  above. If the real cause turns out to be something this investigation didn't surface (a required
  session cookie/header beyond the existing homepage warm-up, a fully different current endpoint,
  etc.), `discover_report`'s rich diagnostics should at least make that fast to identify on a
  machine with real network access, which the pre-fix "tried 3 URLs, all failed" could not.
- `extract_symbol_frame`'s same-class latent bug (stock downloader, single-ticker branch) — found,
  documented, not fixed (out of this checkpoint's scope).
- Python version mismatch: this sandbox runs 3.12.3; the checkpoint brief cited 3.11.13 for the
  environment where the real executions happened. Not expected to matter for pure-Python logic,
  noted for completeness.
