"""Local storage: DuckDB query layer over durable Parquet files (PART 12).

No database cluster, no server process — this is a single local DuckDB file plus a
`data/processed/*.parquet` directory tree, designed to run entirely on a developer laptop or a
single CI/scheduled-workflow runner (PART 12 / PART 54).

Layout:
    data/raw/nse/<session_date>.csv                     (untouched original bhavcopy files)
    data/raw/universe/<snapshot_date>_<id>.csv           (untouched original universe snapshot files)
    data/processed/eod_prices/year=YYYY.parquet          (one file per calendar year — see below)
    data/processed/security_master.parquet
    data/processed/universe_snapshots.parquet
    data/processed/corporate_actions.parquet

**Partitioning (P1 fix):** `eod_prices` used to be a single Parquet file rewritten in full on
every daily append. That's fine at NIFTY-200-for-a-few-years scale but doesn't scale to
NSE_MAINBOARD_EQ (~2,000 securities) times several years of daily bars. Partitioning by calendar
year bounds a daily append to rewriting only the current year's file, not the entire history —
without introducing a second database/service (PART 12's "do not overengineer" and PART 54).

**Price-basis correctness (P0 fix):** NSE bhavcopy rows (`source="NSE"`, `price_basis="RAW"`) and
yfinance bootstrap rows (`source="YFINANCE"`, `price_basis="ADJUSTED"`) can both exist for the
same `(nse_symbol, trade_date)` pair at the boundary between bootstrap history and daily
ingestion. `read_symbol_history` prefers the NSE row whenever both exist for the same date (NSE
is the primary source — PART 3), rather than silently keeping whichever happened to be inserted
last. It also reports the resulting basis composition via `price_basis_composition()` so a mixed
(bootstrap-adjusted + daily-raw) series is visible to callers, never silently invisible.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import duckdb
import pandas as pd

from nse_scanner.exceptions import StorageError

EOD_PRICES_SCHEMA = (
    "nse_symbol",
    "isin",
    "trade_date",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "turnover",
    "source",
    "price_basis",
    "schema_version",
    "ingested_at",
)

PRICE_BASIS_RAW = "RAW"
PRICE_BASIS_ADJUSTED = "ADJUSTED"
PRICE_BASIS_BLENDED = "BLENDED_RAW_NSE_ADJUSTED_YFINANCE_BOOTSTRAP"


@dataclass
class MarketDataStore:
    processed_dir: str | Path
    duckdb_path: str | Path

    def __post_init__(self) -> None:
        self.processed_dir = Path(self.processed_dir)
        self.processed_dir.mkdir(parents=True, exist_ok=True)
        self.eod_prices_dir.mkdir(parents=True, exist_ok=True)
        Path(self.duckdb_path).parent.mkdir(parents=True, exist_ok=True)

    @property
    def eod_prices_dir(self) -> Path:
        return Path(self.processed_dir) / "eod_prices"

    def _partition_path(self, year: int) -> Path:
        return self.eod_prices_dir / f"year={year}.parquet"

    def _eod_glob(self) -> str:
        return str(self.eod_prices_dir / "*.parquet")

    def _has_any_partition(self) -> bool:
        return any(self.eod_prices_dir.glob("*.parquet"))

    def _connect(self) -> duckdb.DuckDBPyConnection:
        return duckdb.connect(str(self.duckdb_path))

    def append_eod_prices(self, df: pd.DataFrame) -> int:
        """Appends normalized EOD rows, de-duplicating on (nse_symbol, trade_date, source) so a
        re-ingested session overwrites rather than doubles up. Only the calendar-year
        partition(s) actually touched by `df` are read + rewritten — a single day's append never
        touches prior years' files. Returns the resulting row count across ALL partitions for
        visibility, not just the touched ones.
        """
        missing = [c for c in EOD_PRICES_SCHEMA if c not in df.columns]
        if missing:
            raise StorageError(f"append_eod_prices: missing columns {missing}")

        incoming = df[list(EOD_PRICES_SCHEMA)].copy()
        incoming["trade_date"] = pd.to_datetime(incoming["trade_date"])
        incoming["_year"] = incoming["trade_date"].dt.year

        for year, year_df in incoming.groupby("_year"):
            year_df = year_df.drop(columns="_year")
            partition_path = self._partition_path(int(year))
            if partition_path.exists():
                existing = pd.read_parquet(partition_path)
                combined = pd.concat([existing, year_df], ignore_index=True)
            else:
                combined = year_df
            combined = combined.drop_duplicates(subset=["nse_symbol", "trade_date", "source"], keep="last")
            combined = combined.sort_values(["nse_symbol", "trade_date"])
            combined.to_parquet(partition_path, index=False)

        if not self._has_any_partition():
            return 0
        con = self._connect()
        try:
            result = con.execute(f"SELECT count(*) FROM read_parquet('{self._eod_glob()}')").fetchone()
        finally:
            con.close()
        return int(result[0]) if result is not None else 0

    def read_symbol_history(self, nse_symbol: str, as_of: "pd.Timestamp | str | None" = None) -> pd.DataFrame | None:
        """One row per trade_date, NSE preferred over yfinance whenever both exist for that date
        (see module docstring — price-basis correctness fix).

        `as_of`, when given, is the historical-replay cutoff (PHASE 1/3): only rows with
        `trade_date <= as_of` are returned — rows ingested AFTER this call was made (e.g. by a
        `run_daily.py` that has continued running daily since) are invisible to this read. This
        is what makes `data/asof_provider.py::AsOfDataProvider` a genuine point-in-time replay
        rather than "whatever happens to be in the store right now, mislabeled as historical."
        """
        if not self._has_any_partition():
            return None
        con = self._connect()
        try:
            cutoff_clause = "AND trade_date <= ?" if as_of is not None else ""
            params = [nse_symbol] + ([str(pd.Timestamp(as_of).date())] if as_of is not None else [])
            df = con.execute(
                f"""
                SELECT trade_date, open AS "Open", high AS "High", low AS "Low",
                       close AS "Close", volume AS "Volume"
                FROM (
                    SELECT *, row_number() OVER (
                        PARTITION BY trade_date
                        ORDER BY CASE WHEN source = 'NSE' THEN 0 ELSE 1 END
                    ) AS rn
                    FROM read_parquet('{self._eod_glob()}')
                    WHERE nse_symbol = ? {cutoff_clause}
                )
                WHERE rn = 1
                ORDER BY trade_date
                """,
                params,
            ).fetchdf()
        finally:
            con.close()
        if df.empty:
            return None
        df["trade_date"] = pd.to_datetime(df["trade_date"])
        df = df.set_index("trade_date")
        return df

    def price_basis_composition(self, nse_symbol: str, as_of: "pd.Timestamp | str | None" = None) -> str:
        """RAW / ADJUSTED / BLENDED for the symbol's EFFECTIVE (post-NSE-preference) series —
        i.e. this reflects what `read_symbol_history` actually returned, not just every row ever
        stored (a superseded YFINANCE row for a date NSE has since covered doesn't count)."""
        if not self._has_any_partition():
            return PRICE_BASIS_RAW
        con = self._connect()
        try:
            cutoff_clause = "AND trade_date <= ?" if as_of is not None else ""
            params = [nse_symbol] + ([str(pd.Timestamp(as_of).date())] if as_of is not None else [])
            rows = con.execute(
                f"""
                SELECT DISTINCT price_basis
                FROM (
                    SELECT *, row_number() OVER (
                        PARTITION BY trade_date
                        ORDER BY CASE WHEN source = 'NSE' THEN 0 ELSE 1 END
                    ) AS rn
                    FROM read_parquet('{self._eod_glob()}')
                    WHERE nse_symbol = ? {cutoff_clause}
                )
                WHERE rn = 1
                """,
                params,
            ).fetchall()
        finally:
            con.close()
        bases = {r[0] for r in rows}
        if len(bases) <= 1:
            return next(iter(bases), PRICE_BASIS_RAW)
        return PRICE_BASIS_BLENDED

    def latest_trade_date(self, nse_symbol: str | None = None) -> pd.Timestamp | None:
        if not self._has_any_partition():
            return None
        con = self._connect()
        try:
            if nse_symbol:
                row = con.execute(
                    f"SELECT max(trade_date) FROM read_parquet('{self._eod_glob()}') WHERE nse_symbol = ?",
                    [nse_symbol],
                ).fetchone()
            else:
                row = con.execute(f"SELECT max(trade_date) FROM read_parquet('{self._eod_glob()}')").fetchone()
        finally:
            con.close()
        return pd.Timestamp(row[0]) if row and row[0] is not None else None

    def row_count(self, nse_symbol: str | None = None) -> int:
        if not self._has_any_partition():
            return 0
        con = self._connect()
        try:
            if nse_symbol:
                row = con.execute(
                    f"SELECT count(*) FROM read_parquet('{self._eod_glob()}') WHERE nse_symbol = ?",
                    [nse_symbol],
                ).fetchone()
            else:
                row = con.execute(f"SELECT count(*) FROM read_parquet('{self._eod_glob()}')").fetchone()
        finally:
            con.close()
        return int(row[0]) if row else 0

    def write_parquet(self, name: str, df: pd.DataFrame) -> Path:
        path = Path(self.processed_dir) / f"{name}.parquet"
        df.to_parquet(path, index=False)
        return path

    def read_parquet(self, name: str) -> pd.DataFrame | None:
        path = Path(self.processed_dir) / f"{name}.parquet"
        if not path.exists():
            return None
        return pd.read_parquet(path)
