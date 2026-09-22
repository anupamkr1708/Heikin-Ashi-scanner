"""Direct tests for data/benchmark.py::YahooBenchmarkProvider.

Before this test file existed, `YahooBenchmarkProvider.fetch_benchmark` had ZERO direct test
coverage — `tests/unit/test_yfinance_normalization.py` covers the shared `normalize_ohlcv_columns`
/ `extract_symbol_frame` helpers, but nothing exercised this provider's own code path (the inline
MultiIndex collapse, the exception handling, the empty-response handling, or — the reason this
file was added — the AS-OF cutoff). Found during the v1.3 research-integrity audit.

`yfinance.download` is mocked throughout: these are unit tests of this module's own logic, not
network integration tests (PART 57 — offline unit tests must not depend on live providers).
"""

from __future__ import annotations

from datetime import date
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest
from nse_scanner.config import DataConfig
from nse_scanner.data.benchmark import YahooBenchmarkProvider


def _index_df(dates: list[str]) -> pd.DataFrame:
    idx = pd.DatetimeIndex(dates)
    n = len(idx)
    rng = np.random.default_rng(0)
    close = 100 + np.cumsum(rng.uniform(-1, 1, size=n))
    return pd.DataFrame({
        "Open": close - 0.5, "High": close + 1, "Low": close - 1, "Close": close,
        "Volume": rng.integers(1000, 5000, size=n),
    }, index=idx)


def _multiindex_single_ticker_df(dates: list[str], ticker: str = "^NSEI") -> pd.DataFrame:
    plain = _index_df(dates)
    plain.columns = pd.MultiIndex.from_product([plain.columns, [ticker]])
    return plain


@pytest.fixture
def cfg() -> DataConfig:
    return DataConfig()


# --- basic shapes -----------------------------------------------------------------------------

def test_plain_columns_single_ticker(cfg):
    df = _index_df(["2026-01-01", "2026-01-02", "2026-01-05"])
    with patch("yfinance.download", return_value=df) as mock_dl:
        provider = YahooBenchmarkProvider(cfg)
        out, err = provider.fetch_benchmark("^NSEI", period="1y", interval="1d")
    assert err is None
    assert out is not None
    assert list(out.columns) == ["Open", "High", "Low", "Close", "Volume"]
    assert len(out) == 3
    # Live path (as_of_date=None) uses period, not start/end.
    assert mock_dl.call_args.kwargs["period"] == "1y"
    assert "start" not in mock_dl.call_args.kwargs
    assert "end" not in mock_dl.call_args.kwargs


def test_multiindex_single_ticker_is_collapsed(cfg):
    """A single-ticker call can still come back with a (field, ticker) MultiIndex depending on
    yfinance version/group_by — BUG 3's original failure mode."""
    df = _multiindex_single_ticker_df(["2026-01-01", "2026-01-02"])
    with patch("yfinance.download", return_value=df):
        provider = YahooBenchmarkProvider(cfg)
        out, err = provider.fetch_benchmark("^NSEI", period="1y", interval="1d")
    assert err is None
    assert out is not None
    assert list(out.columns) == ["Open", "High", "Low", "Close", "Volume"]


def test_empty_response_is_reported_not_silently_none(cfg):
    with patch("yfinance.download", return_value=pd.DataFrame()):
        provider = YahooBenchmarkProvider(cfg)
        out, err = provider.fetch_benchmark("^NSEI", period="1y", interval="1d")
    assert out is None
    assert err == "no_data_returned"


def test_none_response(cfg):
    with patch("yfinance.download", return_value=None):
        provider = YahooBenchmarkProvider(cfg)
        out, err = provider.fetch_benchmark("^NSEI", period="1y", interval="1d")
    assert out is None
    assert err == "no_data_returned"


def test_provider_exception_is_captured_not_raised(cfg):
    with patch("yfinance.download", side_effect=RuntimeError("connection reset")):
        provider = YahooBenchmarkProvider(cfg)
        out, err = provider.fetch_benchmark("^NSEI", period="1y", interval="1d")
    assert out is None
    assert err is not None and "connection reset" in err


def test_missing_ohlc_column_reported_not_raised(cfg):
    df = _index_df(["2026-01-01", "2026-01-02"]).drop(columns=["Volume"])
    with patch("yfinance.download", return_value=df):
        provider = YahooBenchmarkProvider(cfg)
        out, err = provider.fetch_benchmark("^NSEI", period="1y", interval="1d")
    assert out is None
    assert err is not None and "missing column" in err


# --- AS-OF cutoff (the gap this file was primarily added to close) ----------------------------

def test_live_provider_has_no_as_of_cutoff_by_default(cfg):
    provider = YahooBenchmarkProvider(cfg)
    assert provider.as_of_date is None


def test_as_of_provider_requests_bounded_end_date(cfg):
    """Request layer: `end` must be passed so future sessions are never even requested."""
    df = _index_df(["2026-09-01", "2026-09-08"])
    with patch("yfinance.download", return_value=df) as mock_dl:
        provider = YahooBenchmarkProvider(cfg, as_of_date=date(2026, 9, 8))
        provider.fetch_benchmark("^NSEI", period="2y", interval="1d")
    assert mock_dl.call_args.kwargs["end"] == "2026-09-09"  # exclusive end => as_of + 1 day
    assert "period" not in mock_dl.call_args.kwargs


def test_as_of_provider_drops_future_rows_the_mock_incorrectly_returns(cfg):
    """Response-layer defense in depth (module docstring): even if a misbehaving/mocked provider
    returns sessions after as_of_date, this provider must not pass them through. This is the
    scenario that matters most for replay correctness: the request-layer `end` kwarg is a request
    to Yahoo, not a guarantee, so the response layer is the one enforcing the real invariant.
    """
    df = _index_df(["2026-09-04", "2026-09-08", "2026-09-09", "2026-09-11", "2026-09-15"])
    with patch("yfinance.download", return_value=df):
        provider = YahooBenchmarkProvider(cfg, as_of_date=date(2026, 9, 8))
        out, err = provider.fetch_benchmark("^NSEI", period="2y", interval="1d")
    assert err is None
    assert out is not None
    assert list(out.index.normalize()) == [pd.Timestamp("2026-09-04"), pd.Timestamp("2026-09-08")]
    assert out.index.max().date() <= date(2026, 9, 8)


def test_as_of_provider_with_all_future_rows_reports_explicit_error(cfg):
    """If everything the (mocked/misbehaving) provider returns is after as_of_date, this must be
    a clean 'no data on or before as_of' error — never an empty-but-'OK' result and never a
    silent pass-through of future-only data."""
    df = _index_df(["2026-09-09", "2026-09-10"])
    with patch("yfinance.download", return_value=df):
        provider = YahooBenchmarkProvider(cfg, as_of_date=date(2026, 9, 8))
        out, err = provider.fetch_benchmark("^NSEI", period="2y", interval="1d")
    assert out is None
    assert err is not None and "as_of=2026-09-08" in err


def test_as_of_provider_boundary_is_inclusive(cfg):
    """trade_date == as_of_date must be KEPT (mirrors MarketDataStore's `trade_date <= ?`)."""
    df = _index_df(["2026-09-07", "2026-09-08"])
    with patch("yfinance.download", return_value=df):
        provider = YahooBenchmarkProvider(cfg, as_of_date=date(2026, 9, 8))
        out, _err = provider.fetch_benchmark("^NSEI", period="2y", interval="1d")
    assert out is not None
    assert date(2026, 9, 8) in {ts.date() for ts in out.index}
