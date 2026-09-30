"""Tests for universe/drift.py (Task 9 / Task 10 item 20)."""

from __future__ import annotations

import pandas as pd
import pytest
from nse_scanner.universe.drift import (
    DRIFT_ALERT_FRACTION,
    DRIFT_ELEVATED_FRACTION,
    STATUS_ALERT,
    STATUS_ELEVATED,
    STATUS_NO_PREVIOUS,
    STATUS_NORMAL,
    compute_universe_drift,
)


def _members(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows)


def test_no_previous_snapshot_is_reported_not_guessed():
    current = _members([{"Symbol": "A", "Series": "EQ", "ISIN": "INE000A"}])
    report = compute_universe_drift(None, current)
    assert report["status"] == STATUS_NO_PREVIOUS
    assert report["previous_count"] is None
    assert report["current_count"] == 1


def test_identical_snapshots_are_normal_churn_with_zero_changes():
    members = _members(
        [
            {"Symbol": "A", "Series": "EQ", "ISIN": "INE000A"},
            {"Symbol": "B", "Series": "BE", "ISIN": "INE000B"},
        ]
    )
    report = compute_universe_drift(members.copy(), members.copy())
    assert report["status"] == STATUS_NORMAL
    assert report["churn_fraction"] == 0.0
    assert report["added_symbols"] == []
    assert report["removed_symbols"] == []
    assert report["series_changes"] == []
    assert report["isin_changes"] == []
    assert report["probable_renames"] == []


def test_small_addition_stays_normal_churn():
    previous = _members([{"Symbol": f"S{i:03d}", "Series": "EQ", "ISIN": f"INE{i:03d}"} for i in range(100)])
    current = pd.concat([previous, _members([{"Symbol": "NEWONE", "Series": "EQ", "ISIN": "INENEW"}])])
    report = compute_universe_drift(previous, current)
    assert report["churn_fraction"] == pytest.approx(0.01)  # well under DRIFT_ELEVATED_FRACTION (2%)
    assert report["status"] == STATUS_NORMAL
    assert report["added_symbols"] == ["NEWONE"]


def test_elevated_churn_crosses_the_elevated_threshold_but_not_alert():
    n = 100
    previous = _members([{"Symbol": f"S{i:03d}", "Series": "EQ", "ISIN": f"INE{i:03d}"} for i in range(n)])
    n_remove = int(n * (DRIFT_ELEVATED_FRACTION + 0.01))
    current = previous.iloc[n_remove:].copy()
    report = compute_universe_drift(previous, current)
    assert report["status"] == STATUS_ELEVATED
    assert report["removed_count"] == n_remove


def test_alert_churn_crosses_the_alert_threshold():
    n = 100
    previous = _members([{"Symbol": f"S{i:03d}", "Series": "EQ", "ISIN": f"INE{i:03d}"} for i in range(n)])
    n_remove = int(n * (DRIFT_ALERT_FRACTION + 0.05))
    current = previous.iloc[n_remove:].copy()
    report = compute_universe_drift(previous, current)
    assert report["status"] == STATUS_ALERT


def test_series_and_isin_changes_detected_for_common_symbols():
    previous = _members(
        [
            {"Symbol": "A", "Series": "EQ", "ISIN": "INE000A"},
            {"Symbol": "B", "Series": "EQ", "ISIN": "INE000B"},
        ]
    )
    current = _members(
        [
            {"Symbol": "A", "Series": "BZ", "ISIN": "INE000A"},  # series changed
            {"Symbol": "B", "Series": "EQ", "ISIN": "INE000B999"},  # ISIN changed
        ]
    )
    report = compute_universe_drift(previous, current)
    assert report["series_changes_count"] == 1
    assert report["series_changes"][0] == {"symbol": "A", "previous": "EQ", "current": "BZ"}
    assert report["isin_changes_count"] == 1
    assert report["isin_changes"][0]["symbol"] == "B"


def test_probable_rename_detected_via_shared_isin():
    previous = _members([{"Symbol": "OLDNAME", "Series": "EQ", "ISIN": "INE12345678"}])
    current = _members([{"Symbol": "NEWNAME", "Series": "EQ", "ISIN": "INE12345678"}])
    report = compute_universe_drift(previous, current)
    assert report["probable_renames_count"] == 1
    assert report["probable_renames"][0] == {
        "previous_symbol": "OLDNAME",
        "current_symbol": "NEWNAME",
        "isin": "INE12345678",
    }
    # a probable rename is still counted as one add + one remove -- this module never silently
    # nets it out, it only ANNOTATES the pair as a plausible rename for the operator
    assert report["added_symbols"] == ["NEWNAME"]
    assert report["removed_symbols"] == ["OLDNAME"]


def test_placeholder_unknown_isin_never_produces_a_false_rename():
    """`build_security_master` falls back to f"UNKNOWN:{symbol}" for missing ISINs -- two
    completely unrelated symbols both getting UNKNOWN:-style placeholders must never be reported
    as a rename just because they superficially 'share' a non-ISIN value."""
    previous = _members([{"Symbol": "GONE", "Series": "EQ", "ISIN": "UNKNOWN:GONE"}])
    current = _members([{"Symbol": "NEWCO", "Series": "EQ", "ISIN": "UNKNOWN:NEWCO"}])
    report = compute_universe_drift(previous, current)
    assert report["probable_renames_count"] == 0


def test_missing_isin_column_reports_changes_as_unknown_not_zero():
    previous = _members([{"Symbol": "A", "Series": "EQ"}])
    current = _members([{"Symbol": "A", "Series": "EQ"}])
    report = compute_universe_drift(previous, current)
    assert report["isin_changes_count"] is None  # unknown, not silently zero
    assert report["probable_renames_count"] == 0  # no ISIN available to detect one


def test_result_independent_of_row_order():
    previous = _members([{"Symbol": s, "Series": "EQ", "ISIN": f"INE{s}"} for s in "ABCDE"])
    current_forward = _members([{"Symbol": s, "Series": "EQ", "ISIN": f"INE{s}"} for s in "BCDEF"])
    current_shuffled = current_forward.iloc[::-1].reset_index(drop=True)
    r1 = compute_universe_drift(previous, current_forward)
    r2 = compute_universe_drift(previous, current_shuffled)
    assert r1["added_symbols"] == r2["added_symbols"]
    assert r1["removed_symbols"] == r2["removed_symbols"]
    assert r1["churn_fraction"] == r2["churn_fraction"]


def test_duplicate_symbol_rows_are_counted_in_both_snapshots():
    previous = _members(
        [
            {"Symbol": "A", "Series": "EQ", "ISIN": "INE000A"},
            {"Symbol": "A", "Series": "BE", "ISIN": "INE000A"},  # duplicate symbol, un-deduped input
        ]
    )
    current = _members([{"Symbol": "A", "Series": "EQ", "ISIN": "INE000A"}])
    report = compute_universe_drift(previous, current)
    assert report["previous_duplicate_symbol_rows"] == 1
    assert report["current_duplicate_symbol_rows"] == 0


def test_classification_drift_delta_computed_when_diagnostics_supplied():
    previous = _members([{"Symbol": "A", "Series": "EQ", "ISIN": "INE000A"}])
    current = _members([{"Symbol": "A", "Series": "EQ", "ISIN": "INE000A"}])
    report = compute_universe_drift(
        previous,
        current,
        previous_diagnostics={"excluded_by_category": {"ETF": 350, "REIT": 3}},
        current_diagnostics={"excluded_by_category": {"ETF": 351, "REIT": 3, "SME": 1}},
    )
    assert report["excluded_by_category_delta"] == {"ETF": 1, "REIT": 0, "SME": 1}


def test_classification_drift_delta_is_none_without_diagnostics():
    previous = _members([{"Symbol": "A", "Series": "EQ", "ISIN": "INE000A"}])
    current = _members([{"Symbol": "A", "Series": "EQ", "ISIN": "INE000A"}])
    report = compute_universe_drift(previous, current)
    assert report["excluded_by_category_delta"] is None


def test_missing_symbol_column_raises_a_clear_error():
    with pytest.raises(ValueError, match="Symbol"):
        compute_universe_drift(pd.DataFrame({"NotSymbol": ["A"]}), pd.DataFrame({"Symbol": ["A"]}))


def test_unavailable_dimensions_always_disclosed():
    current = _members([{"Symbol": "A", "Series": "EQ", "ISIN": "INE000A"}])
    report = compute_universe_drift(None, current)
    assert "status_tradability" in report["unavailable_dimensions"]
