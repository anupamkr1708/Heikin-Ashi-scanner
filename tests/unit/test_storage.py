import pandas as pd
import pytest
from nse_scanner.data.storage import (
    PRICE_BASIS_ADJUSTED,
    PRICE_BASIS_BLENDED,
    PRICE_BASIS_RAW,
    MarketDataStore,
)


def _row(symbol, trade_date, close, source, price_basis):
    return {
        "nse_symbol": symbol,
        "isin": None,
        "trade_date": pd.Timestamp(trade_date),
        "open": close - 1,
        "high": close + 1,
        "low": close - 2,
        "close": close,
        "volume": 1000,
        "turnover": None,
        "source": source,
        "price_basis": price_basis,
        "schema_version": "test",
        "ingested_at": pd.Timestamp("2026-01-01"),
    }


def test_empty_store_returns_none_and_zero(tmp_path):
    store = MarketDataStore(tmp_path / "processed", tmp_path / "db.duckdb")

    assert store.read_symbol_history("FOO") is None
    assert store.row_count() == 0
    assert store.latest_trade_date() is None
    assert store.price_basis_composition("FOO") == PRICE_BASIS_RAW


def test_one_day_history_is_insufficient_but_readable(tmp_path):
    store = MarketDataStore(tmp_path / "processed", tmp_path / "db.duckdb")

    df = pd.DataFrame([_row("FOO", "2026-09-11", 100.0, "NSE", "RAW")])

    store.append_eod_prices(df)

    hist = store.read_symbol_history("FOO")

    assert hist is not None
    assert len(hist) == 1


def test_incremental_daily_append_accumulates(tmp_path):
    store = MarketDataStore(tmp_path / "processed", tmp_path / "db.duckdb")

    store.append_eod_prices(pd.DataFrame([_row("FOO", "2026-09-10", 100.0, "NSE", "RAW")]))

    store.append_eod_prices(pd.DataFrame([_row("FOO", "2026-09-11", 101.0, "NSE", "RAW")]))

    hist = store.read_symbol_history("FOO")

    assert len(hist) == 2
    assert list(hist["Close"]) == [100.0, 101.0]


def test_duplicate_session_ingestion_overwrites_not_doubles(tmp_path):
    store = MarketDataStore(tmp_path / "processed", tmp_path / "db.duckdb")

    store.append_eod_prices(pd.DataFrame([_row("FOO", "2026-09-11", 100.0, "NSE", "RAW")]))

    store.append_eod_prices(pd.DataFrame([_row("FOO", "2026-09-11", 999.0, "NSE", "RAW")]))

    hist = store.read_symbol_history("FOO")

    assert len(hist) == 1
    assert hist["Close"].iloc[0] == 999.0


def test_year_partitioning_only_touches_relevant_partition_file(tmp_path):
    store = MarketDataStore(tmp_path / "processed", tmp_path / "db.duckdb")

    store.append_eod_prices(pd.DataFrame([_row("FOO", "2024-06-01", 100.0, "NSE", "RAW")]))

    store.append_eod_prices(pd.DataFrame([_row("FOO", "2025-06-01", 110.0, "NSE", "RAW")]))

    store.append_eod_prices(pd.DataFrame([_row("FOO", "2026-06-01", 120.0, "NSE", "RAW")]))

    assert (store.eod_prices_dir / "year=2024.parquet").exists()
    assert (store.eod_prices_dir / "year=2025.parquet").exists()
    assert (store.eod_prices_dir / "year=2026.parquet").exists()

    hist = store.read_symbol_history("FOO")

    assert len(hist) == 3
    assert list(hist["Close"]) == [100.0, 110.0, 120.0]


def test_nse_preferred_over_yfinance_on_same_date(tmp_path):
    """
    The P0 price-basis correctness fix: if bootstrap
    (YFINANCE/ADJUSTED) and daily ingestion (NSE/RAW) both
    cover the same date, NSE wins — never an arbitrary
    insert-order tiebreak.
    """
    store = MarketDataStore(tmp_path / "processed", tmp_path / "db.duckdb")

    store.append_eod_prices(pd.DataFrame([_row("FOO", "2026-09-11", 105.0, "YFINANCE", "ADJUSTED")]))

    store.append_eod_prices(pd.DataFrame([_row("FOO", "2026-09-11", 100.0, "NSE", "RAW")]))

    hist = store.read_symbol_history("FOO")

    assert len(hist) == 1
    assert hist["Close"].iloc[0] == 100.0


def test_price_basis_composition_pure_raw(tmp_path):
    store = MarketDataStore(tmp_path / "processed", tmp_path / "db.duckdb")

    store.append_eod_prices(pd.DataFrame([_row("FOO", "2026-09-11", 100.0, "NSE", "RAW")]))

    assert store.price_basis_composition("FOO") == PRICE_BASIS_RAW


def test_price_basis_composition_pure_adjusted(tmp_path):
    store = MarketDataStore(tmp_path / "processed", tmp_path / "db.duckdb")

    store.append_eod_prices(pd.DataFrame([_row("FOO", "2025-01-01", 90.0, "YFINANCE", "ADJUSTED")]))

    assert store.price_basis_composition("FOO") == PRICE_BASIS_ADJUSTED


def test_price_basis_composition_blended_bootstrap_plus_daily(tmp_path):
    store = MarketDataStore(tmp_path / "processed", tmp_path / "db.duckdb")

    # Bootstrap covers older history (ADJUSTED); daily ingestion
    # covers the newest session (RAW). These are DIFFERENT dates,
    # so both survive read_symbol_history's per-date dedup, and
    # the effective series is genuinely blended.
    store.append_eod_prices(pd.DataFrame([_row("FOO", "2025-01-01", 90.0, "YFINANCE", "ADJUSTED")]))

    store.append_eod_prices(pd.DataFrame([_row("FOO", "2026-09-11", 100.0, "NSE", "RAW")]))

    assert store.price_basis_composition("FOO") == PRICE_BASIS_BLENDED


def test_missing_required_columns_raises(tmp_path):
    from nse_scanner.exceptions import StorageError

    store = MarketDataStore(tmp_path / "processed", tmp_path / "db.duckdb")

    with pytest.raises(StorageError):
        store.append_eod_prices(pd.DataFrame([{"nse_symbol": "FOO"}]))


def test_eod_parquet_schema_is_stable_for_nullable_fields(tmp_path):
    store = MarketDataStore(
        tmp_path / "processed",
        tmp_path / "db.duckdb",
    )

    df = pd.DataFrame(
        [
            _row(
                "FOO",
                "2026-09-11",
                100.0,
                "YFINANCE",
                "ADJUSTED",
            )
        ]
    )

    store.append_eod_prices(df)

    parquet_path = store.eod_prices_dir / "year=2026.parquet"

    import duckdb

    con = duckdb.connect(":memory:")
    try:
        schema = con.execute(
            """
            DESCRIBE
            SELECT *
            FROM read_parquet(?)
            """,
            [str(parquet_path)],
        ).fetchdf()
    finally:
        con.close()

    types = dict(
        zip(
            schema["column_name"],
            schema["column_type"],
            strict=False,
        )
    )

    assert types["nse_symbol"] == "VARCHAR"
    assert types["isin"] == "VARCHAR"
    assert types["turnover"] == "DOUBLE"
    assert types["open"] == "DOUBLE"
    assert types["close"] == "DOUBLE"


def test_legacy_partition_is_normalized_before_append(tmp_path):
    store = MarketDataStore(
        tmp_path / "processed",
        tmp_path / "db.duckdb",
    )

    legacy = pd.DataFrame(
        {
            "nse_symbol": ["LEGACY"],
            "isin": pd.Series([None], dtype="Int64"),
            "trade_date": pd.to_datetime(["2024-06-01"]),
            "open": [100.0],
            "high": [101.0],
            "low": [99.0],
            "close": [100.5],
            "volume": [1000.0],
            "turnover": pd.Series([None], dtype="Int64"),
            "source": ["YFINANCE"],
            "price_basis": ["ADJUSTED"],
            "schema_version": ["yfinance_bootstrap"],
            "ingested_at": pd.to_datetime(
                ["2026-01-01"],
                utc=True,
            ),
        }
    )

    partition = store.eod_prices_dir / "year=2024.parquet"

    legacy.to_parquet(
        partition,
        index=False,
    )

    store.append_eod_prices(
        pd.DataFrame(
            [
                _row(
                    "NEW",
                    "2024-06-02",
                    110.0,
                    "YFINANCE",
                    "ADJUSTED",
                )
            ]
        )
    )

    import duckdb

    con = duckdb.connect(":memory:")
    try:
        schema = con.execute(
            """
            DESCRIBE
            SELECT *
            FROM read_parquet(?)
            """,
            [str(partition)],
        ).fetchdf()

        result = con.execute(
            """
            SELECT COUNT(*)
            FROM read_parquet(?)
            """,
            [str(partition)],
        ).fetchone()
    finally:
        con.close()

    types = dict(
        zip(
            schema["column_name"],
            schema["column_type"],
            strict=False,
        )
    )

    assert types["isin"] == "VARCHAR"
    assert types["turnover"] == "DOUBLE"
    assert types["nse_symbol"] == "VARCHAR"
    assert types["open"] == "DOUBLE"
    assert types["close"] == "DOUBLE"

    assert result is not None
    assert result[0] == 2
