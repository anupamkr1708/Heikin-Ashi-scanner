"""Normalizes raw yfinance output into the internal OHLCV schema.

**Audit note (BUG 3):** the legacy notebook's benchmark download and per-stock download shared
one code path that assumed a consistent MultiIndex shape. yfinance's `download()` with multiple
tickers returns a `MultiIndex` (whose level order depends on `group_by`), but a *single*-ticker
request returns plain columns — and an empty/failed response can come back as `None`, an empty
DataFrame, or a DataFrame that is all-NaN. This module handles each case explicitly and is
covered by tests/unit/test_yfinance_normalization.py for: single ticker, multiple tickers,
missing ticker, partial response, empty response, MultiIndex, ordinary columns.

**P1 audit note (`normalize_ticker_frame`, added this revision):** a REAL production failure
showed the "single-ticker downloads never carry a MultiIndex" assumption above (see
`extract_symbol_frame`'s `batch_size == 1` branch) is false. The actual observed yfinance 1.7.0
response for `yf.download("^NSEI", group_by="ticker", ...)` — a single ticker, passed as a string
— was:
    MultiIndex([('^NSEI','Open'), ('^NSEI','High'), ('^NSEI','Low'),
                ('^NSEI','Close'), ('^NSEI','Volume')], names=['Ticker','Price'])
i.e. level 0 = Ticker, level 1 = Price/field — the OPPOSITE of what `.get_level_values(0)`
(used by both `extract_symbol_frame`'s single-ticker branch and the pre-fix
`data/benchmark.py`) assumes. Collapsing to level 0 there produces five columns all named
`'^NSEI'` — none of Open/High/Low/Close/Volume survive, guaranteeing the
"unexpected benchmark schema, missing column" failure this was built to fix.

`normalize_ticker_frame` is the provider-agnostic replacement used by `data/benchmark.py`: it
determines which MultiIndex level is the field level FROM THE LEVEL'S VALUES (does it look like
a set of OHLCV field names?), never from position and never from a `if ticker == "^NSEI":`
special case — see that function's docstring for the exact algorithm. It is NOT wired into
`extract_symbol_frame`/the stock batch-download path in this revision — that function has the
same latent bug in its `batch_size == 1` branch (single ticker requested from the stock
provider), but fixing it is out of scope for this checkpoint (P1 scope: benchmark provider +
security-master discovery only) and is flagged as a known follow-up rather than silently left
unmentioned.
"""

from __future__ import annotations

from collections.abc import Sequence

import pandas as pd

from nse_scanner.exceptions import FrameNormalizationError

REQUIRED_COLUMNS = ("Open", "High", "Low", "Close", "Volume")

# Recognized OHLCV field tokens (lowercased) used ONLY to decide which MultiIndex level is the
# "field" level — never to decide which column is which ticker. "adj close" is included because
# auto_adjust=False responses carry it; this codebase always requests auto_adjust=True so it is
# not part of REQUIRED_COLUMNS, but a frame containing it should still be recognized correctly.
_OHLCV_FIELD_TOKENS = {"open", "high", "low", "close", "adj close", "volume"}
_CANONICAL_FIELD_NAMES = {
    "open": "Open", "high": "High", "low": "Low", "close": "Close",
    "adj close": "Adj Close", "volume": "Volume",
}


def extract_symbol_frame(batch_result: pd.DataFrame | None, symbol: str, batch_size: int) -> pd.DataFrame | None:
    """Extracts a single symbol's OHLCV frame out of a yfinance batch-download result.

    `batch_size` is the number of tickers originally requested in this call — NOT
    `len(batch_result.columns)`, which is unreliable once some tickers silently return no data.

    KNOWN LATENT BUG, not fixed in this revision (see module docstring): the `batch_size == 1`
    branch assumes a single-ticker download never carries a MultiIndex. Real production evidence
    (see `normalize_ticker_frame`) disproves this for at least one yfinance 1.7.0 code path. This
    function is used by the stock batch downloader, which in practice always requests batches of
    `cfg.batch_size` symbols (rarely, if ever, exactly 1), so the blast radius is believed small —
    but it is a real, same-class latent bug, not a hypothetical one, and is flagged here rather
    than silently left for a future reader to rediscover independently.
    """
    if batch_result is None:
        return None

    if batch_size == 1:
        # Single-ticker downloads never carry a MultiIndex, regardless of group_by.
        df = batch_result.copy()
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        return _none_if_empty(df)

    if isinstance(batch_result.columns, pd.MultiIndex):
        level0_values = set(batch_result.columns.get_level_values(0))
        level1_values = set(batch_result.columns.get_level_values(1)) if batch_result.columns.nlevels > 1 else set()
        if symbol in level0_values:
            df = batch_result[symbol].copy()
        elif symbol in level1_values:
            # group_by="column" puts the field first and the ticker second.
            df = batch_result.xs(symbol, axis=1, level=1, drop_level=True).copy()
        else:
            return None
        return _none_if_empty(df)

    # Ordinary (non-MultiIndex) columns with more than one ticker requested is unexpected but
    # can happen if yfinance silently collapsed to a single successful ticker — only trust it
    # when the requested symbol is genuinely the only one that could be in scope.
    return None


def _none_if_empty(df: pd.DataFrame) -> pd.DataFrame | None:
    df = df.dropna(how="all")
    if df.empty:
        return None
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        return None
    return df


def normalize_ohlcv_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Coerces to the canonical column set/order and numeric dtypes. Does not validate ranges —
    see data/validation.py for that."""
    out = df.copy()
    if isinstance(out.columns, pd.MultiIndex):
        out.columns = out.columns.get_level_values(0)
    out = out[list(REQUIRED_COLUMNS)]
    for c in REQUIRED_COLUMNS:
        out[c] = pd.to_numeric(out[c], errors="coerce")
    out = out[~out.index.duplicated(keep="last")].sort_index()
    return out


def _level_is_field_level(values: Sequence) -> bool:
    """A level 'is the field level' when at least half of its DISTINCT values (case/whitespace
    normalized) are recognized OHLCV field tokens. Distinct-value based (not raw-count based) so
    a multi-ticker frame — where each field token repeats once per ticker — isn't skewed by
    ticker count. This is the ONLY signal used to pick the field level; ticker identity is used
    afterward only to select which ticker's columns to extract, never to decide orientation."""
    distinct = {str(v).strip().lower() for v in values}
    if not distinct:
        return False
    return len(distinct & _OHLCV_FIELD_TOKENS) / len(distinct) >= 0.5


def normalize_ticker_frame(
    raw: pd.DataFrame | None,
    ticker: str,
    *,
    required_fields: Sequence[str] = ("Open", "High", "Low", "Close"),
    optional_fields: Sequence[str] = ("Volume",),
) -> pd.DataFrame:
    """Canonical, provider-agnostic normalizer for a single ticker's OHLCV frame out of a raw
    yfinance-shaped `DataFrame`. Handles, without any `if ticker == "...":` special case:

      1. Plain single-level OHLCV columns.
      2. MultiIndex oriented (ticker, field)   — e.g. yfinance `group_by="ticker"`.
      3. MultiIndex oriented (field, ticker)   — e.g. yfinance `group_by="column"`.
      4. A single-ticker request that STILL comes back as a MultiIndex (the real bug this
         function exists to fix — see module docstring).
      5. A multi-ticker frame, extracting just `ticker`'s columns.
      6. `None` / empty / all-NaN input.
      7. Missing required OHLC fields.
      8. Unsupported/unexpected MultiIndex structures (>2 levels, or neither/both levels look
         like field names).
      9. Duplicate dates in the index.
      10. Date-index normalization to a plain (tz-naive, midnight-normalized) `DatetimeIndex`.

    **Orientation detection algorithm:** for a 2-level MultiIndex, each level's DISTINCT values
    are checked against the known OHLCV field-name tokens (open/high/low/close/adj close/volume,
    case/whitespace-insensitive) via `_level_is_field_level`. Whichever level looks like field
    names is the field level; the other is the ticker level. This is a VALUE-based test, not a
    position-based one (`get_level_values(0)`, the bug being fixed) and not a ticker-based one —
    it works identically regardless of which physical level index yfinance happens to put the
    field names in for a given `group_by`/version/single-vs-multi-ticker combination.

    Once the ticker level is known, the requested `ticker` is matched against its distinct values
    case/whitespace-insensitively; if no match is found but there is exactly ONE distinct ticker
    value in that level, it is used anyway (yfinance can return a resolved/canonical spelling that
    differs slightly from the request) — this is the only place ticker identity matters, and only
    for *selecting a column group*, never for deciding orientation.

    **Volume is optional** (`optional_fields=("Volume",)` by default): index/benchmark regime and
    relative-strength calculations (`indicators/relative_strength.py::calculate_index_features`)
    only ever read `Close` — confirmed by inspection, zero references to Volume anywhere in that
    module or downstream in `pipeline/scan.py`'s Market_Regime sheet construction. A benchmark
    index legitimately reports Volume=0 for many sessions (not a data-quality problem, just index
    semantics — indices aggregate constituent volumes inconsistently or not at all depending on
    the exchange), so Volume is preserved AND ALLOWED TO BE ZERO when present, but never required,
    and never fabricated when the column is genuinely absent from the response — a missing Volume
    column stays missing, not silently filled with 0 or NaN as if it had been observed.

    Raises `FrameNormalizationError` (never returns a partially-wrong frame) on any failure, with
    a message naming what was actually seen — the caller (`data/benchmark.py`) converts this to
    the `(None, error_message)` tuple contract the rest of the pipeline expects.
    """
    if raw is None:
        raise FrameNormalizationError("no_data_returned")
    df = raw.copy()
    if df.empty:
        raise FrameNormalizationError("no_data_returned")

    if isinstance(df.columns, pd.MultiIndex):
        if df.columns.nlevels != 2:
            raise FrameNormalizationError(
                f"unsupported MultiIndex depth for ticker {ticker!r}: "
                f"{df.columns.nlevels} levels (expected 2)"
            )
        level0_vals = list(df.columns.get_level_values(0))
        level1_vals = list(df.columns.get_level_values(1))
        level0_is_field = _level_is_field_level(level0_vals)
        level1_is_field = _level_is_field_level(level1_vals)

        if level0_is_field and not level1_is_field:
            ticker_level = 1
        elif level1_is_field and not level0_is_field:
            ticker_level = 0
        elif level0_is_field and level1_is_field:
            raise FrameNormalizationError(
                f"ambiguous MultiIndex for ticker {ticker!r}: both levels look like OHLCV "
                f"fields (level 0 sample={level0_vals[:5]!r}, level 1 sample={level1_vals[:5]!r})"
            )
        else:
            raise FrameNormalizationError(
                f"unrecognized MultiIndex orientation for ticker {ticker!r}: neither level looks "
                f"like OHLCV fields (level 0 sample={level0_vals[:5]!r}, "
                f"level 1 sample={level1_vals[:5]!r})"
            )

        ticker_values = set(df.columns.get_level_values(ticker_level))
        match = next(
            (t for t in ticker_values if str(t).strip().lower() == str(ticker).strip().lower()),
            None,
        )
        if match is None:
            if len(ticker_values) == 1:
                match = next(iter(ticker_values))
            else:
                raise FrameNormalizationError(
                    f"ticker {ticker!r} not found in MultiIndex ticker level "
                    f"{sorted(str(t) for t in ticker_values)!r}"
                )
        df = df.xs(match, axis=1, level=ticker_level, drop_level=True)

    # Case/whitespace-normalize field names to their canonical Title Case spelling.
    rename_map = {}
    for col in df.columns:
        canonical = _CANONICAL_FIELD_NAMES.get(str(col).strip().lower())
        if canonical:
            rename_map[col] = canonical
    df = df.rename(columns=rename_map)
    df = df.loc[:, ~df.columns.duplicated(keep="first")]

    missing = [f for f in required_fields if f not in df.columns]
    if missing:
        raise FrameNormalizationError(
            f"missing required field(s) for ticker {ticker!r}: {missing}; "
            f"available columns after normalization: {list(df.columns)}"
        )

    keep = list(required_fields) + [f for f in optional_fields if f in df.columns]
    df = df[keep].copy()
    for c in keep:
        df[c] = pd.to_numeric(df[c], errors="coerce")

    idx = pd.DatetimeIndex(pd.to_datetime(df.index))
    if idx.tz is not None:
        idx = idx.tz_localize(None)  # keep as-of comparisons (data/benchmark.py) tz-naive
    df.index = idx.normalize()
    df.index.name = "Date"

    # Duplicate dates: deterministic policy — keep the row that appeared LAST in the original
    # (pre-sort) frame for a given date, mirroring normalize_ohlcv_columns's existing convention
    # for the stock path (a later-appearing row for the same date is treated as a correction),
    # then sort chronologically.
    df = df[~df.index.duplicated(keep="last")].sort_index()

    df = df.dropna(subset=list(required_fields), how="all")
    if df.empty:
        raise FrameNormalizationError(f"no usable rows for ticker {ticker!r} after normalization")

    return df
