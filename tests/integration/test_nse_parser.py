"""Tests the UDiFF-column-mapping and security-file-parsing logic against synthetic,
schema-representative data (see tests/fixtures/synthetic_market_data.py for why these are
synthetic rather than real NSE files — the real files referenced in project instructions were
never actually attached to this conversation).

These tests validate the PARSING/NORMALIZATION logic in isolation from the network layer by
constructing DataFrames/bytes in exactly the column-name shape data/nse_eod.py and
data/nse_reports.py expect, and are marked so they're trivially replaced once real fixtures are
available: swap `generate_synthetic_udiff_bhavcopy()` for `pd.read_csv(real_file)` and everything
downstream (column mapping, OHLC invariant checks, series filtering) is exercised identically.
"""

import gzip

import pandas as pd
import pytest
from nse_scanner.data.nse_eod import UDIFF_COLUMN_MAP
from nse_scanner.data.nse_reports import filter_mainboard_equity, parse_security_file
from nse_scanner.exceptions import DataProviderError
from nse_scanner.testing.synthetic_market_data import (
    SYNTHETIC_SYMBOLS,
    generate_synthetic_security_file,
    generate_synthetic_udiff_bhavcopy,
)


def test_udiff_column_map_renames_to_canonical_schema():
    raw = generate_synthetic_udiff_bhavcopy(pd.Timestamp("2026-09-10"))
    renamed = raw.rename(columns={k: v for k, v in UDIFF_COLUMN_MAP.items() if k in raw.columns})

    for canonical in ("NSE_Symbol", "ISIN", "Series", "Open", "High", "Low", "Close", "Volume", "Turnover"):
        assert canonical in renamed.columns

    assert len(renamed) == len(SYNTHETIC_SYMBOLS)
    assert set(renamed["NSE_Symbol"]) == set(SYNTHETIC_SYMBOLS)


def test_udiff_ohlc_invariants_hold_on_synthetic_bhavcopy():
    raw = generate_synthetic_udiff_bhavcopy(pd.Timestamp("2026-09-10"))
    renamed = raw.rename(columns={k: v for k, v in UDIFF_COLUMN_MAP.items() if k in raw.columns})
    assert (renamed["High"] >= renamed["Low"]).all()
    assert (renamed["High"] >= renamed["Open"]).all()
    assert (renamed["High"] >= renamed["Close"]).all()
    assert (renamed["Low"] <= renamed["Open"]).all()
    assert (renamed["Low"] <= renamed["Close"]).all()
    assert (renamed["Volume"] >= 0).all()


def test_security_file_parser_accepts_documented_column_names():
    df = generate_synthetic_security_file()
    raw_bytes = gzip.compress(df.to_csv(index=False).encode("utf-8"))
    parsed = parse_security_file(raw_bytes, is_gzip=True)

    assert set(parsed["NSE_Symbol"]) == set(SYNTHETIC_SYMBOLS)
    assert "Company_Name" in parsed.columns
    assert "Series" in parsed.columns
    assert (parsed["Series"] == "EQ").all()


def test_security_file_parser_fails_loudly_on_unknown_schema():
    df = pd.DataFrame({"TOTALLY_UNKNOWN_COL": ["A", "B"]})
    raw_bytes = gzip.compress(df.to_csv(index=False).encode("utf-8"))
    with pytest.raises(DataProviderError):
        parse_security_file(raw_bytes, is_gzip=True)


def test_filter_mainboard_equity_excludes_non_eq_be_series():
    df = generate_synthetic_security_file()
    df = df.rename(columns={"SYMBOL": "NSE_Symbol", "SERIES": "Series", "NAME OF COMPANY": "Company_Name"})
    extra = df.iloc[[0]].copy()
    extra["Series"] = "SM"  # SME series — must be excluded from NSE_MAINBOARD_EQ
    df2 = pd.concat([df, extra], ignore_index=True)

    filtered = filter_mainboard_equity(df2)
    assert "SM" not in set(filtered["Series"])
    assert len(filtered) == len(df)  # only the injected SM row was dropped
