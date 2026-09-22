"""Data-quality validation engine (PART 15).

Rejects a security only if it cannot support the PRIMARY calculation (tiered history — PART 16 /
BUG 6). Never interpolates, never fabricates missing observations, never silently repairs
corrupted prices. Every failure reason is explicit and returned to the caller, which logs it
under the appropriate pipeline stage (PART 15's stage list).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from nse_scanner.data.normalization import REQUIRED_COLUMNS, normalize_ohlcv_columns
from nse_scanner.exceptions import DataValidationError


@dataclass
class ValidationReport:
    ok: bool
    rows_in: int
    rows_valid: int
    issues: list[str] = field(default_factory=list)


def validate_and_clean_ohlc(df: pd.DataFrame | None, min_rows: int) -> tuple[pd.DataFrame, ValidationReport]:
    """Raises DataValidationError if the PRIMARY calculation cannot be supported. Otherwise
    returns (cleaned_df, report) where report.issues lists non-fatal observations (e.g. rows
    dropped for non-positive prices) so they are never silently invisible."""
    issues: list[str] = []
    if df is None or df.empty:
        raise DataValidationError("no data returned")

    missing_input_cols = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing_input_cols:
        raise DataValidationError(f"missing required columns: {missing_input_cols}")

    df = normalize_ohlcv_columns(df)
    rows_in = len(df)

    missing_cols = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing_cols:
        raise DataValidationError(f"missing required columns: {missing_cols}")

    df = df.dropna(subset=["Open", "High", "Low", "Close"])
    n_after_na_drop = len(df)
    if n_after_na_drop < rows_in:
        issues.append(f"dropped {rows_in - n_after_na_drop} rows with NaN OHLC")

    df["Volume"] = df["Volume"].fillna(0)

    non_positive = ~((df["Open"] > 0) & (df["High"] > 0) & (df["Low"] > 0) & (df["Close"] > 0))
    n_bad_price = int(non_positive.sum())
    if n_bad_price:
        issues.append(f"dropped {n_bad_price} rows with non-positive OHLC")
    df = df[~non_positive]

    bad_ohlc_order = ~(
        (df["High"] >= df["Low"])
        & (df["High"] >= df["Open"])
        & (df["High"] >= df["Close"])
        & (df["Low"] <= df["Open"])
        & (df["Low"] <= df["Close"])
    )
    n_bad_order = int(bad_ohlc_order.sum())
    if n_bad_order:
        issues.append(f"dropped {n_bad_order} rows violating High/Low/Open/Close ordering")
    df = df[~bad_ohlc_order]

    bad_volume = df["Volume"] < 0
    if bad_volume.any():
        issues.append(f"dropped {int(bad_volume.sum())} rows with negative volume")
    df = df[~bad_volume]

    if len(df) < min_rows:
        raise DataValidationError(
            f"insufficient history for PRIMARY calculation: {len(df)} valid rows (need >= {min_rows})"
        )

    report = ValidationReport(ok=True, rows_in=rows_in, rows_valid=len(df), issues=issues)
    return df, report
