"""Universe drift diagnostics (mainboard-universe-integrity-v2, Task 9).

Compares two membership snapshots and describes what changed. **Report-only: nothing here ever
raises on a membership change.** The brief is explicit that expected constituent churn (IPOs,
delistings, index reconstitution) must be distinguished from structural source corruption rather
than failing on every change — the *hard* structural guard lives in the cardinality gate
(`universe/mainboard.py`) and source validation (`data/nse_reports.py`); this module makes an
unusual change *obvious* to an operator, it does not decide to abort.

**The thresholds below are operator-review heuristics, NOT evidence-derived.** This project has one
real snapshot of each list and therefore no measured historical churn rate to calibrate against; the
numbers are conservative starting points meant to be tuned once real history accumulates in the
append-only snapshot store (`universe/snapshot_store.py`). They are parameters, not constants baked
into logic, for that reason.

Dimensions NOT covered, stated explicitly rather than silently omitted: status/tradability changes
(`EQUITY_L.csv` carries no `DelFlg`/`PrtdToTrad`/etc. — see MAINBOARD_UNIVERSE_INTEGRITY_V2_AUDIT.md
B.7) — reported under `unavailable_dimensions`.
"""

from __future__ import annotations

import pandas as pd

# Fraction of the PREVIOUS universe that was added or removed. Heuristic starting points — see the
# module docstring: no measured churn history exists yet to justify these numerically.
DRIFT_ELEVATED_FRACTION = 0.02
DRIFT_ALERT_FRACTION = 0.10
SAMPLE_CAP = 25  # max entries listed per category in the report (counts are always exact)

STATUS_NO_PREVIOUS = "NO_PREVIOUS_SNAPSHOT"
STATUS_NORMAL = "NORMAL_CHURN"
STATUS_ELEVATED = "ELEVATED_CHURN"
STATUS_ALERT = "STRUCTURAL_ALERT"


def _require_symbol(df: pd.DataFrame, label: str) -> None:
    if "Symbol" not in df.columns:
        raise ValueError(f"{label} membership frame has no 'Symbol' column; got {list(df.columns)}")


def _isin_series(df: pd.DataFrame) -> pd.Series | None:
    if "ISIN" not in df.columns:
        return None
    return df["ISIN"]


def _usable_isin(v: object) -> bool:
    return isinstance(v, str) and bool(v) and not v.startswith("UNKNOWN:")


def compute_universe_drift(
    previous: pd.DataFrame | None,
    current: pd.DataFrame,
    *,
    previous_diagnostics: dict | None = None,
    current_diagnostics: dict | None = None,
    elevated_fraction: float = DRIFT_ELEVATED_FRACTION,
    alert_fraction: float = DRIFT_ALERT_FRACTION,
) -> dict:
    """Deterministic (all lists sorted), order-independent description of the change from
    `previous` to `current`. `previous=None` means "no earlier snapshot exists" — reported as such,
    never guessed at."""
    _require_symbol(current, "current")
    cur_symbols = set(current["Symbol"].astype(str))
    report: dict = {
        "current_count": len(current),
        "unavailable_dimensions": ["status_tradability"],
        "thresholds": {"elevated_fraction": elevated_fraction, "alert_fraction": alert_fraction},
    }
    if previous is None:
        report.update({"status": STATUS_NO_PREVIOUS, "previous_count": None})
        return report

    _require_symbol(previous, "previous")
    prev_symbols = set(previous["Symbol"].astype(str))
    added = sorted(cur_symbols - prev_symbols)
    removed = sorted(prev_symbols - cur_symbols)
    churn_fraction = (len(added) + len(removed)) / max(len(prev_symbols), 1)

    if churn_fraction > alert_fraction:
        status = STATUS_ALERT
    elif churn_fraction > elevated_fraction:
        status = STATUS_ELEVATED
    else:
        status = STATUS_NORMAL

    report.update(
        {
            "status": status,
            "previous_count": len(previous),
            "churn_fraction": round(churn_fraction, 6),
            "added_count": len(added),
            "removed_count": len(removed),
            "added_symbols": added[:SAMPLE_CAP],
            "removed_symbols": removed[:SAMPLE_CAP],
            "previous_duplicate_symbol_rows": int(previous["Symbol"].duplicated().sum()),
            "current_duplicate_symbol_rows": int(current["Symbol"].duplicated().sum()),
        }
    )

    # Series / ISIN changes among symbols present in BOTH snapshots (first row per symbol; both
    # frames are expected to already be deduplicated — duplicates are reported separately above).
    common = sorted(cur_symbols & prev_symbols)
    prev_idx = previous.drop_duplicates("Symbol").set_index(previous.drop_duplicates("Symbol")["Symbol"].astype(str))
    cur_idx = current.drop_duplicates("Symbol").set_index(current.drop_duplicates("Symbol")["Symbol"].astype(str))
    for col, key in (("Series", "series_changes"), ("ISIN", "isin_changes")):
        if col in previous.columns and col in current.columns:
            changes = [
                {"symbol": s, "previous": prev_idx.at[s, col], "current": cur_idx.at[s, col]}
                for s in common
                if prev_idx.at[s, col] != cur_idx.at[s, col]
                and not (pd.isna(prev_idx.at[s, col]) and pd.isna(cur_idx.at[s, col]))
            ]
            report[f"{key}_count"] = len(changes)
            report[key] = changes[:SAMPLE_CAP]
        else:
            report[f"{key}_count"] = None  # column absent on one side: unknown, not zero
            report[key] = []

    # Probable renames (Task 4's mitigation for the unresolved symbol-change gap): a symbol that
    # disappeared while a different symbol carrying the SAME real ISIN appeared. A *signal for the
    # operator*, not an authoritative resolution — this project cannot confirm renames itself.
    renames: list[dict] = []
    prev_isin, cur_isin = _isin_series(previous), _isin_series(current)
    if prev_isin is not None and cur_isin is not None:
        removed_by_isin = {
            row.ISIN: str(row.Symbol)
            for row in previous[previous["Symbol"].astype(str).isin(removed)].itertuples()
            if _usable_isin(row.ISIN)
        }
        for row in current[current["Symbol"].astype(str).isin(added)].itertuples():
            if _usable_isin(row.ISIN) and row.ISIN in removed_by_isin:
                renames.append(
                    {"previous_symbol": removed_by_isin[row.ISIN], "current_symbol": str(row.Symbol), "isin": row.ISIN}
                )
    renames.sort(key=lambda r: (r["previous_symbol"], r["current_symbol"]))
    report["probable_renames_count"] = len(renames)
    report["probable_renames"] = renames[:SAMPLE_CAP]

    # Classification drift: how many rows each exclusion category removed, then vs now.
    if previous_diagnostics is not None and current_diagnostics is not None:
        prev_cat = previous_diagnostics.get("excluded_by_category") or {}
        cur_cat = current_diagnostics.get("excluded_by_category") or {}
        report["excluded_by_category_delta"] = {
            k: int(cur_cat.get(k, 0)) - int(prev_cat.get(k, 0)) for k in sorted(set(prev_cat) | set(cur_cat))
        }
    else:
        report["excluded_by_category_delta"] = None
    return report
