"""Synthetic OHLCV / bhavcopy / security-file generators used by tests and by
`--offline-fixture` mode.

**These are NOT real NSE files.** The user-supplied "real NSE fixture files" referenced in
project instructions did not actually arrive as uploads in this conversation (see
README.md "Known limitations" / CODE_REVIEW.md) — only the legacy notebook was uploaded. Rather
than invent a "real" schema from memory and risk silently shipping a wrong parser (which PART 3 /
the continuation prompt both explicitly forbid), this module generates clearly-synthetic data
shaped like the DOCUMENTED schema in data/nse_eod.py / data/nse_reports.py, deterministically
(seeded), so tests are reproducible.

Drop real files in tests/fixtures/nse/ (e.g. a real
`BhavCopy_NSE_CM_0_0_0_YYYYMMDD_F_0000.csv.zip` and `NSE_CM_security_DDMMYYYY.csv.gz`) and add a
regression test that parses them with data/nse_eod.py::fetch_bhavcopy /
data/nse_reports.py::parse_security_file directly — see tests/integration/test_nse_parser.py for
where that test should go once real files are available.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

SYNTHETIC_SYMBOLS = ["ALPHATEST", "BETATEST", "GAMMATEST", "DELTATEST", "EPSILONTEST"]


def _deterministic_symbol_offset(symbol: str) -> int:
    """A stable, process-independent offset derived from `symbol` for RNG seeding. Python's
    built-in `hash()` is randomized per-process (PYTHONHASHSEED) for security reasons, so it must
    NOT be used here — using it silently made these fixtures non-reproducible across runs, which
    is exactly the kind of nondeterminism this module's docstring promises not to have."""
    return sum(ord(c) for c in symbol) % 1000


def generate_synthetic_ohlcv(
    symbol: str,
    n_days: int = 80,
    start_price: float = 1000.0,
    seed: int = 42,
    engineer_breakout_on_last_day: bool = False,
) -> pd.DataFrame:
    """Deterministic random-walk OHLCV, optionally engineered so the LAST day satisfies the
    baseline BB+HA mandatory condition (used to prove the end-to-end pipeline can actually
    produce a signal in offline mode)."""
    rng = np.random.default_rng(seed + _deterministic_symbol_offset(symbol))
    dates = pd.bdate_range(end=pd.Timestamp.today().normalize() - pd.Timedelta(days=1), periods=n_days)

    closes_list: list[float] = [start_price]
    for _ in range(n_days - 1):
        pct_change = rng.normal(loc=0.0003, scale=0.012)
        closes_list.append(closes_list[-1] * (1 + pct_change))
    closes = np.array(closes_list)

    opens = np.empty(n_days)
    highs = np.empty(n_days)
    lows = np.empty(n_days)
    opens[0] = closes[0] * 0.998
    for i in range(n_days):
        prev_close = closes[i - 1] if i > 0 else closes[0]
        opens[i] = prev_close * (1 + rng.normal(0, 0.002))
        daily_range = abs(rng.normal(0.012, 0.004)) * closes[i]
        highs[i] = max(opens[i], closes[i]) + daily_range * rng.uniform(0.2, 0.6)
        lows[i] = min(opens[i], closes[i]) - daily_range * rng.uniform(0.2, 0.6)

    volumes = rng.integers(50_000, 500_000, size=n_days)

    if engineer_breakout_on_last_day and n_days >= 25:
        # Solve (by fixed-point iteration, exact for this closed-form problem) for a last-day
        # Close that lands the overshoot above the 20-day Bollinger upper band at a fixed target
        # (2%, safely inside the mandatory (0, 4] window) REGARDLESS of the realized volatility
        # of the preceding random walk — a fixed "+3.5%" offset is not robust to that, since the
        # dynamically-computed band width varies with the random draw.
        target_overshoot_pct = 2.0
        window = closes[-20:].copy()  # last 20 closes, last slot will be overwritten each iteration
        guess = closes[-2] * 1.03
        for _ in range(25):
            window[-1] = guess
            mean = window.mean()
            std = window.std(ddof=1)
            upper = mean + 2.0 * std
            new_guess = upper * (1 + target_overshoot_pct / 100.0)
            if abs(new_guess - guess) < 1e-6:
                guess = new_guess
                break
            guess = new_guess

        base = closes[-2]
        closes[-1] = guess
        opens[-1] = base * 1.001
        highs[-1] = closes[-1] * 1.005
        lows[-1] = opens[-1] * 0.998

    df = pd.DataFrame({"Open": opens, "High": highs, "Low": lows, "Close": closes, "Volume": volumes}, index=dates)
    df.index.name = "trade_date"
    return df


def generate_synthetic_index(n_days: int = 260, seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range(end=pd.Timestamp.today().normalize() - pd.Timedelta(days=1), periods=n_days)
    closes_list: list[float] = [20000.0]
    for _ in range(n_days - 1):
        closes_list.append(closes_list[-1] * (1 + rng.normal(0.0004, 0.008)))
    closes = np.array(closes_list)
    df = pd.DataFrame(
        {
            "Open": closes * 0.999,
            "High": closes * 1.004,
            "Low": closes * 0.996,
            "Close": closes,
            "Volume": rng.integers(100_000_000, 300_000_000, size=n_days),
        },
        index=dates,
    )
    return df


def generate_synthetic_universe() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "Symbol": SYNTHETIC_SYMBOLS,
            "Company_Name": [f"{s} Limited" for s in SYNTHETIC_SYMBOLS],
            "Sector": ["Technology", "Financials", "Energy", "Consumer", "Industrials"],
        }
    )


def generate_synthetic_udiff_bhavcopy(session_date: pd.Timestamp) -> pd.DataFrame:
    """One session's cross-sectional bhavcopy in the UDiFF column-name convention documented in
    data/nse_eod.py::UDIFF_COLUMN_MAP — for parser unit tests, not a full history."""
    rows = []
    for i, sym in enumerate(SYNTHETIC_SYMBOLS):
        base = 500 + i * 137.5
        rows.append(
            {
                "TradDt": session_date.strftime("%Y-%m-%d"),
                "TckrSymb": sym,
                "ISIN": f"INE{i:03d}A0101{i}",
                "SctySrs": "EQ",
                "OpnPric": round(base * 0.998, 2),
                "HghPric": round(base * 1.015, 2),
                "LwPric": round(base * 0.99, 2),
                "ClsPric": round(base * 1.005, 2),
                "TtlTradgVol": 100000 + i * 5000,
                "TtlTrfVal": (100000 + i * 5000) * base,
            }
        )
    return pd.DataFrame(rows)


def generate_synthetic_security_file() -> pd.DataFrame:
    """Security-master-shaped rows in the column-name convention documented in
    data/nse_reports.py::SECURITY_FILE_COLUMN_CANDIDATES."""
    rows = []
    for i, sym in enumerate(SYNTHETIC_SYMBOLS):
        rows.append(
            {
                "SYMBOL": sym,
                "NAME OF COMPANY": f"{sym} Limited",
                "SERIES": "EQ",
                "ISIN NUMBER": f"INE{i:03d}A0101{i}",
                "DATE OF LISTING": "01-01-2010",
                "FACE VALUE": 10,
                "STATUS": "Active",
            }
        )
    return pd.DataFrame(rows)


def generate_mainboard_semantics_fixture_cases() -> pd.DataFrame:
    """Realistic-shaped (UDiFF raw-header) rows for the mainboard-universe-semantics forensic
    exercise's Step 8 case list. **Synthetic, not a real NSE file** — same convention as
    `generate_synthetic_security_file` above (see this module's docstring). Uses the exact raw
    UDiFF column names (TckrSymb, SctySrs, DelFlg, PrtdToTrad, ElgbltyNrmlMkt, SctyStsNrmlMkt,
    FinInstrmId, FinInstrmTp, ...) rather than the legacy EQUITY_L.csv-style names, since these
    cases exist specifically to exercise the diagnostic-field-presence/pass-through behavior in
    `compute_mainboard_diagnostics` — those fields are NOT in SECURITY_FILE_COLUMN_CANDIDATES, so
    parse_security_file leaves them under these exact raw names (see nse_reports.py comment on
    MAINBOARD_DIAGNOSTIC_FIELDS).

    One row per case, `TckrSymb` names the case so test failures are self-explanatory:
      NORMALEQ          - ordinary active, tradable, eligible EQ row
      NORMALEQ_BE       - the SAME company's BE (surveillance) row -- same ISIN as NORMALEQ,
                          exercises deterministic EQ-preferred dedup (Step 5/9/12)
      ETFCASE           - series EQ, name suggests an ETF -- included exactly like any other EQ
                          row today (no authoritative ETF field exists yet); a symbol-name
                          heuristic is deliberately NOT used to exclude it (see the forensic
                          report's explicit instruction against that). Present so a future
                          authoritative exclusion mechanism has something real to test against.
      REITCASE          - same rationale as ETFCASE, for REIT units
      INVITCASE         - same rationale as ETFCASE, for InvIT units
      DELETEDEQ         - DelFlg=Y -- still included today (filter_mainboard_equity does not
                          filter on DelFlg); present so DelFlg's presence/value is visible in
                          diagnostics even though it is not acted on
      NONTRADABLE       - PrtdToTrad=0 -- current-operational-status field, not filtered on
                          (see the forensic report's PrtdToTrad section on why not)
      MKTINELIGIBLE     - ElgbltyNrmlMkt=0 -- same rationale, not filtered on
      DUPEISIN_SAMESERIES - a second EQ row sharing NORMALEQ's ISIN but a DIFFERENT symbol/
                          FinInstrmId -- a genuine data-quality edge case (same-ISIN, both EQ,
                          not the normal EQ/BE pattern); deduplicate_mainboard must not silently
                          treat this like the ordinary EQ/BE case (see Dedup_Reason)
      SERIESCHANGED     - shares NORMALEQ's ISIN under series 'BZ' (an unranked series, not in
                          MAINBOARD_SERIES_DEDUP_PREFERENCE) -- exercises the "unranked series
                          sorts last, deterministically" path
      OTHERINSTR_NCD    - series 'N1' (a non-convertible-debenture-style series) -- must be
                          excluded by filter_mainboard_equity same as before this fixture existed;
                          present to confirm that stays true
    """
    base_isin = "INE900A01019"
    rows = [
        dict(
            TckrSymb="NORMALEQ",
            FinInstrmNm="Normal Equity Ltd",
            SctySrs="EQ",
            ISIN=base_isin,
            FinInstrmId="1001",
            DelFlg="N",
            PrtdToTrad="1",
            ElgbltyNrmlMkt="1",
            SctyStsNrmlMkt="1",
            FinInstrmTp="STK",
            ListgDt="01-01-2010",
            RmvlDt="",
            UpdDt="23-09-2026",
        ),
        dict(
            TckrSymb="NORMALEQ",
            FinInstrmNm="Normal Equity Ltd",
            SctySrs="BE",
            ISIN=base_isin,
            FinInstrmId="1002",
            DelFlg="N",
            PrtdToTrad="1",
            ElgbltyNrmlMkt="1",
            SctyStsNrmlMkt="1",
            FinInstrmTp="STK",
            ListgDt="01-01-2010",
            RmvlDt="",
            UpdDt="23-09-2026",
        ),
        dict(
            TckrSymb="ETFCASE",
            FinInstrmNm="Synthetic Nifty ETF",
            SctySrs="EQ",
            ISIN="INE901A01011",
            FinInstrmId="1003",
            DelFlg="N",
            PrtdToTrad="1",
            ElgbltyNrmlMkt="1",
            SctyStsNrmlMkt="1",
            FinInstrmTp="STK",
            ListgDt="01-01-2015",
            RmvlDt="",
            UpdDt="23-09-2026",
        ),
        dict(
            TckrSymb="REITCASE",
            FinInstrmNm="Synthetic Office Parks REIT",
            SctySrs="EQ",
            ISIN="INE902A01012",
            FinInstrmId="1004",
            DelFlg="N",
            PrtdToTrad="1",
            ElgbltyNrmlMkt="1",
            SctyStsNrmlMkt="1",
            FinInstrmTp="STK",
            ListgDt="01-01-2019",
            RmvlDt="",
            UpdDt="23-09-2026",
        ),
        dict(
            TckrSymb="INVITCASE",
            FinInstrmNm="Synthetic Infra InvIT",
            SctySrs="EQ",
            ISIN="INE903A01013",
            FinInstrmId="1005",
            DelFlg="N",
            PrtdToTrad="1",
            ElgbltyNrmlMkt="1",
            SctyStsNrmlMkt="1",
            FinInstrmTp="STK",
            ListgDt="01-01-2017",
            RmvlDt="",
            UpdDt="23-09-2026",
        ),
        dict(
            TckrSymb="DELETEDEQ",
            FinInstrmNm="Deleted Equity Ltd",
            SctySrs="EQ",
            ISIN="INE904A01014",
            FinInstrmId="1006",
            DelFlg="Y",
            PrtdToTrad="0",
            ElgbltyNrmlMkt="0",
            SctyStsNrmlMkt="3",
            FinInstrmTp="STK",
            ListgDt="01-01-2011",
            RmvlDt="10-03-2026",
            UpdDt="10-03-2026",
        ),
        dict(
            TckrSymb="NONTRADABLE",
            FinInstrmNm="Non Tradable Ltd",
            SctySrs="EQ",
            ISIN="INE905A01015",
            FinInstrmId="1007",
            DelFlg="N",
            PrtdToTrad="0",
            ElgbltyNrmlMkt="1",
            SctyStsNrmlMkt="1",
            FinInstrmTp="STK",
            ListgDt="01-01-2012",
            RmvlDt="",
            UpdDt="23-09-2026",
        ),
        dict(
            TckrSymb="MKTINELIGIBLE",
            FinInstrmNm="Market Ineligible Ltd",
            SctySrs="EQ",
            ISIN="INE906A01016",
            FinInstrmId="1008",
            DelFlg="N",
            PrtdToTrad="1",
            ElgbltyNrmlMkt="0",
            SctyStsNrmlMkt="2",
            FinInstrmTp="STK",
            ListgDt="01-01-2013",
            RmvlDt="",
            UpdDt="23-09-2026",
        ),
        dict(
            TckrSymb="DUPEISIN_SAMESERIES",
            FinInstrmNm="Same Isin Same Series Ltd",
            SctySrs="EQ",
            ISIN=base_isin,
            FinInstrmId="1009",
            DelFlg="N",
            PrtdToTrad="1",
            ElgbltyNrmlMkt="1",
            SctyStsNrmlMkt="1",
            FinInstrmTp="STK",
            ListgDt="01-01-2010",
            RmvlDt="",
            UpdDt="23-09-2026",
        ),
        dict(
            TckrSymb="SERIESCHANGED",
            FinInstrmNm="Series Changed Ltd",
            SctySrs="BZ",
            ISIN=base_isin,
            FinInstrmId="1010",
            DelFlg="N",
            PrtdToTrad="0",
            ElgbltyNrmlMkt="0",
            SctyStsNrmlMkt="6",
            FinInstrmTp="STK",
            ListgDt="01-01-2010",
            RmvlDt="",
            UpdDt="23-09-2026",
        ),
        dict(
            TckrSymb="OTHERINSTR_NCD",
            FinInstrmNm="Some Co Non Convertible Debenture",
            SctySrs="N1",
            ISIN="INE907A07018",
            FinInstrmId="1011",
            DelFlg="N",
            PrtdToTrad="1",
            ElgbltyNrmlMkt="1",
            SctyStsNrmlMkt="1",
            FinInstrmTp="DEB",
            ListgDt="01-01-2020",
            RmvlDt="",
            UpdDt="23-09-2026",
        ),
    ]
    return pd.DataFrame(rows)
