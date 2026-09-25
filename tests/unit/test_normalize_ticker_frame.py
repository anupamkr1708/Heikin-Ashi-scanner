"""Direct unit tests for data/normalization.py::normalize_ticker_frame.

P1 provider-hardening fix: a REAL production run against live yfinance 1.7.0 showed
`yf.download("^NSEI", group_by="ticker", ...)` returns:

    MultiIndex([('^NSEI','Open'), ('^NSEI','High'), ('^NSEI','Low'),
                ('^NSEI','Close'), ('^NSEI','Volume')], names=['Ticker','Price'])

— i.e. level 0 = Ticker, level 1 = Price/field. The pre-fix normalizer collapsed to level 0
unconditionally (`.get_level_values(0)`), which for THIS orientation produces five columns all
literally named '^NSEI' — none of Open/High/Low/Close/Volume survive. The same real environment
also produced, for a differently-shaped request (`group_by="column"`):

    MultiIndex([('Close','^NSEI'), ('High','^NSEI'), ('Low','^NSEI'),
                ('Open','^NSEI'), ('Volume','^NSEI')], names=['Price','Ticker'])

— the OPPOSITE orientation. Both real shapes are used verbatim as fixtures below (not
re-invented/approximated), alongside the exact real 2026-09-22 zero-volume ^NSEI row from the
same production report.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from nse_scanner.data.normalization import normalize_ticker_frame
from nse_scanner.exceptions import FrameNormalizationError


def _closes(n: int, seed: int = 7) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return 23000.0 + np.cumsum(rng.normal(0, 50, size=n))


def _plain_ohlcv(dates: list[str], seed: int = 7, with_volume: bool = True) -> pd.DataFrame:
    idx = pd.DatetimeIndex(dates)
    close = _closes(len(idx), seed)
    data = {"Open": close - 5, "High": close + 10, "Low": close - 10, "Close": close}
    if with_volume:
        data["Volume"] = np.full(len(idx), 0)  # index volume is legitimately 0/unreported
    return pd.DataFrame(data, index=idx)


def _ticker_first_multiindex(dates: list[str], ticker: str = "^NSEI", seed: int = 7) -> pd.DataFrame:
    """Exact real shape: names=['Ticker', 'Price'], level 0 = ticker, level 1 = field."""
    plain = _plain_ohlcv(dates, seed)
    plain.columns = pd.MultiIndex.from_product([[ticker], plain.columns], names=["Ticker", "Price"])
    return plain


def _price_first_multiindex(dates: list[str], ticker: str = "^NSEI", seed: int = 7) -> pd.DataFrame:
    """Exact real shape: names=['Price', 'Ticker'], level 0 = field, level 1 = ticker."""
    plain = _plain_ohlcv(dates, seed)
    plain.columns = pd.MultiIndex.from_product([plain.columns, [ticker]], names=["Price", "Ticker"])
    return plain


# --- 1. plain single-level columns -------------------------------------------------------------


def test_plain_single_level():
    df = _plain_ohlcv(["2026-09-21", "2026-09-22"])
    out = normalize_ticker_frame(df, "^NSEI")
    assert list(out.columns[:4]) == ["Open", "High", "Low", "Close"]
    assert len(out) == 2


# --- 2/3. both real MultiIndex orientations, order-independent ---------------------------------


def test_ticker_first_multiindex():
    """The EXACT real shape that broke production: names=['Ticker','Price'], level0=ticker."""
    df = _ticker_first_multiindex(["2026-09-21", "2026-09-22"])
    out = normalize_ticker_frame(df, "^NSEI")
    assert list(out.columns[:4]) == ["Open", "High", "Low", "Close"]
    assert len(out) == 2
    # Prove real values survived the extraction correctly, not just column names.
    raw_close = df[("^NSEI", "Close")]
    pd.testing.assert_series_equal(
        out["Close"], raw_close.rename("Close"), check_index_type=False, check_freq=False, check_names=False
    )


def test_price_first_multiindex():
    """The other real shape observed: names=['Price','Ticker'], level0=field."""
    df = _price_first_multiindex(["2026-09-21", "2026-09-22"])
    out = normalize_ticker_frame(df, "^NSEI")
    assert list(out.columns[:4]) == ["Open", "High", "Low", "Close"]
    assert len(out) == 2
    raw_close = df[("Close", "^NSEI")]
    pd.testing.assert_series_equal(
        out["Close"], raw_close.rename("Close"), check_index_type=False, check_freq=False, check_names=False
    )


def test_orientation_detected_from_values_not_position():
    """The core fix, stated as an explicit invariant: swapping which physical level holds the
    ticker vs the field must not change the result AT ALL — position must never matter."""
    dates = ["2026-09-21", "2026-09-22"]
    out_ticker_first = normalize_ticker_frame(_ticker_first_multiindex(dates), "^NSEI")
    out_price_first = normalize_ticker_frame(_price_first_multiindex(dates), "^NSEI")
    pd.testing.assert_frame_equal(out_ticker_first, out_price_first)


# --- 4/5. ticker extraction, single- and multi-ticker frames ------------------------------------


def test_single_ticker_extraction():
    """A single-ticker MultiIndex frame extracts correctly even when the requested ticker string
    has different case/whitespace than what the provider returned (matched case-insensitively;
    falls back to the one available ticker if no exact match, since a single-ticker frame is
    unambiguous regardless of exact spelling)."""
    df = _ticker_first_multiindex(["2026-09-21", "2026-09-22"], ticker="^NSEI")
    out = normalize_ticker_frame(df, "  ^nsei  ")  # different case/whitespace than stored
    assert len(out) == 2
    assert list(out.columns[:4]) == ["Open", "High", "Low", "Close"]


def test_multi_ticker_extraction():
    """A frame carrying MULTIPLE tickers' columns must extract only the requested ticker's data,
    not silently mix or pick the wrong one — no ticker == '^NSEI' special case, so this must work
    for an arbitrary second ticker's presence too."""
    dates = ["2026-09-21", "2026-09-22"]
    nsei = _ticker_first_multiindex(dates, ticker="^NSEI", seed=1)
    other = _ticker_first_multiindex(dates, ticker="RELIANCE.NS", seed=2)
    combined = pd.concat([nsei, other], axis=1)

    out_nsei = normalize_ticker_frame(combined, "^NSEI")
    out_other = normalize_ticker_frame(combined, "RELIANCE.NS")

    pd.testing.assert_series_equal(
        out_nsei["Close"],
        nsei[("^NSEI", "Close")].rename("Close"),
        check_index_type=False,
        check_freq=False,
        check_names=False,
    )
    pd.testing.assert_series_equal(
        out_other["Close"],
        other[("RELIANCE.NS", "Close")].rename("Close"),
        check_index_type=False,
        check_freq=False,
        check_names=False,
    )
    assert not out_nsei["Close"].equals(out_other["Close"])


def test_unrequested_ticker_missing_from_multi_ticker_frame_fails_clearly():
    dates = ["2026-09-21", "2026-09-22"]
    combined = pd.concat(
        [
            _ticker_first_multiindex(dates, ticker="^NSEI", seed=1),
            _ticker_first_multiindex(dates, ticker="RELIANCE.NS", seed=2),
        ],
        axis=1,
    )
    with pytest.raises(FrameNormalizationError, match="TCS.NS"):
        normalize_ticker_frame(combined, "TCS.NS")


# --- 6. zero index volume, using the EXACT real 2026-09-22 ^NSEI row ----------------------------


def test_zero_index_volume_is_allowed():
    """Exact real row from the production report: 2026-09-22, Open=23454.050781,
    High=23489.000000, Low=23285.750000, Close=23329.000000, Volume=0. Must NOT be rejected for
    having zero volume — index volume semantics differ from security OHLCV validation."""
    df = pd.DataFrame(
        {
            "Open": [23454.050781],
            "High": [23489.000000],
            "Low": [23285.750000],
            "Close": [23329.000000],
            "Volume": [0],
        },
        index=pd.DatetimeIndex(["2026-09-22"]),
    )
    out = normalize_ticker_frame(df, "^NSEI")
    assert len(out) == 1
    assert out.iloc[0]["Volume"] == 0
    assert out.iloc[0]["Close"] == pytest.approx(23329.000000)


def test_volume_absent_is_not_fabricated():
    df = _plain_ohlcv(["2026-09-21", "2026-09-22"], with_volume=False)
    out = normalize_ticker_frame(df, "^NSEI")
    assert "Volume" not in out.columns  # not silently filled with 0 or NaN


# --- 7. missing required OHLC field --------------------------------------------------------------


def test_missing_required_ohlc_fails():
    df = _plain_ohlcv(["2026-09-21", "2026-09-22"]).drop(columns=["Close"])
    with pytest.raises(FrameNormalizationError, match="Close"):
        normalize_ticker_frame(df, "^NSEI")


# --- 8. duplicate dates, deterministic policy ----------------------------------------------------


def test_duplicate_dates_are_deterministically_handled():
    """Documented policy: for a duplicated date, the row that appeared LAST in the original
    (pre-sort) frame wins — mirrors normalize_ohlcv_columns's existing stock-path convention. This
    test proves the SPECIFIC surviving value, not merely that normalization doesn't crash."""
    df = pd.DataFrame(
        {
            "Open": [100.0, 999.0],
            "High": [101.0, 999.0],
            "Low": [99.0, 999.0],
            "Close": [100.5, 999.0],
            "Volume": [10, 20],
        },
        index=pd.DatetimeIndex(["2026-09-22", "2026-09-22"]),
    )  # same date, appears twice
    out = normalize_ticker_frame(df, "^NSEI")
    assert len(out) == 1
    assert out.iloc[0]["Close"] == 999.0  # the LAST-occurring row for that date wins
    assert out.iloc[0]["Volume"] == 20


# --- 9-11. None / empty / unsupported structures --------------------------------------------------


def test_none_input_fails_clearly():
    with pytest.raises(FrameNormalizationError, match="no_data_returned"):
        normalize_ticker_frame(None, "^NSEI")


def test_empty_input_fails_clearly():
    with pytest.raises(FrameNormalizationError, match="no_data_returned"):
        normalize_ticker_frame(pd.DataFrame(), "^NSEI")


def test_three_level_multiindex_is_rejected_not_silently_guessed():
    dates = ["2026-09-21", "2026-09-22"]
    df = _ticker_first_multiindex(dates)
    df.columns = pd.MultiIndex.from_tuples([(*c, "extra") for c in df.columns], names=[*df.columns.names, "Extra"])
    with pytest.raises(FrameNormalizationError, match="expected 2"):
        normalize_ticker_frame(df, "^NSEI")


def test_neither_level_looks_like_fields_is_rejected():
    df = pd.DataFrame(
        [[1, 2], [3, 4]],
        columns=pd.MultiIndex.from_tuples([("A", "X"), ("B", "Y")], names=["L0", "L1"]),
        index=pd.DatetimeIndex(["2026-09-21", "2026-09-22"]),
    )
    with pytest.raises(FrameNormalizationError, match="unrecognized MultiIndex orientation"):
        normalize_ticker_frame(df, "^NSEI")
