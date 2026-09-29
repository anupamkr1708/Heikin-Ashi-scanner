"""Tests for universe/snapshot_store.py (Task 8 / Task 10 items 18-19)."""

from __future__ import annotations

from datetime import date, datetime, timezone

import pandas as pd
import pytest
from nse_scanner.universe.snapshot_store import (
    list_snapshot_stamps,
    read_universe_members,
    read_universe_snapshot_record,
    snapshot_stamp,
    write_universe_snapshot,
)
from nse_scanner.universe.validation import UniverseSnapshot


def _snapshot(retrieved_at: datetime, universe_id: str = "NSE_MAINBOARD_EQ", **overrides) -> UniverseSnapshot:
    defaults = dict(
        universe_id=universe_id,
        snapshot_date=retrieved_at.date(),
        source="https://fake/EQUITY_L.csv",
        source_version=None,
        retrieved_at=retrieved_at,
        constituent_count=2585,
        symbol_count=2585,
        duplicate_count=0,
        invalid_count=0,
        validation_status="VALID",
        source_url="https://fake/EQUITY_L.csv",
        file_hash="deadbeef",
        schema_version="v1",
        raw_row_count=2585,
        eligible_count=2585,
        definition="test definition",
        diagnostics={"final_series_breakdown": {"EQ": 2317, "BE": 241, "BZ": 27}},
    )
    defaults.update(overrides)
    return UniverseSnapshot(**defaults)


def _members(n: int = 3) -> pd.DataFrame:
    return pd.DataFrame({"Symbol": [f"S{i}" for i in range(n)], "Series": ["EQ"] * n})


def test_snapshot_stamp_is_utc_and_naive_datetimes_treated_as_utc():
    aware = datetime(2026, 9, 24, 10, 30, 0, tzinfo=timezone.utc)
    naive = datetime(2026, 9, 24, 10, 30, 0)
    assert snapshot_stamp(aware) == snapshot_stamp(naive) == "20260924T103000Z"


def test_write_then_read_roundtrip(tmp_path):
    snap = _snapshot(datetime(2026, 9, 24, 8, 0, 0, tzinfo=timezone.utc))
    members = _members(5)
    members_path, snapshot_path = write_universe_snapshot(tmp_path, snap, members)

    assert members_path.exists() and snapshot_path.exists()

    read_back_members = read_universe_members(tmp_path, "NSE_MAINBOARD_EQ")
    pd.testing.assert_frame_equal(read_back_members.reset_index(drop=True), members.reset_index(drop=True))

    record = read_universe_snapshot_record(tmp_path, "NSE_MAINBOARD_EQ")
    assert record["universe_id"] == "NSE_MAINBOARD_EQ"
    assert record["constituent_count"] == 2585
    assert record["diagnostics"]["final_series_breakdown"] == {"EQ": 2317, "BE": 241, "BZ": 27}  # JSON round-trip


def test_refuses_to_overwrite_an_existing_stamp(tmp_path):
    ts = datetime(2026, 9, 24, 8, 0, 0, tzinfo=timezone.utc)
    write_universe_snapshot(tmp_path, _snapshot(ts), _members())
    with pytest.raises(FileExistsError):
        write_universe_snapshot(tmp_path, _snapshot(ts), _members())


def test_read_with_no_history_returns_none_not_an_error(tmp_path):
    assert read_universe_members(tmp_path, "NSE_MAINBOARD_EQ") is None
    assert read_universe_snapshot_record(tmp_path, "NSE_MAINBOARD_EQ") is None
    assert list_snapshot_stamps(tmp_path, "NSE_MAINBOARD_EQ") == []


def test_multiple_snapshots_are_append_only_and_most_recent_wins_by_default(tmp_path):
    ts1 = datetime(2026, 9, 22, 8, 0, 0, tzinfo=timezone.utc)
    ts2 = datetime(2026, 9, 23, 8, 0, 0, tzinfo=timezone.utc)
    ts3 = datetime(2026, 9, 24, 8, 0, 0, tzinfo=timezone.utc)
    write_universe_snapshot(tmp_path, _snapshot(ts2), _members(2))  # written out of order
    write_universe_snapshot(tmp_path, _snapshot(ts1), _members(1))
    write_universe_snapshot(tmp_path, _snapshot(ts3), _members(3))

    stamps = list_snapshot_stamps(tmp_path, "NSE_MAINBOARD_EQ")
    assert stamps == [snapshot_stamp(ts1), snapshot_stamp(ts2), snapshot_stamp(ts3)]  # sorted, not insertion order

    latest = read_universe_members(tmp_path, "NSE_MAINBOARD_EQ")
    assert len(latest) == 3  # ts3's members, not the last-written (ts3 IS also last-written here, but by date)


def test_specific_stamp_retrieval(tmp_path):
    ts1 = datetime(2026, 9, 22, 8, 0, 0, tzinfo=timezone.utc)
    ts2 = datetime(2026, 9, 23, 8, 0, 0, tzinfo=timezone.utc)
    write_universe_snapshot(tmp_path, _snapshot(ts1), _members(1))
    write_universe_snapshot(tmp_path, _snapshot(ts2), _members(2))

    members_ts1 = read_universe_members(tmp_path, "NSE_MAINBOARD_EQ", stamp=snapshot_stamp(ts1))
    assert len(members_ts1) == 1


def test_unknown_stamp_returns_none_not_the_nearest_date(tmp_path):
    ts = datetime(2026, 9, 24, 8, 0, 0, tzinfo=timezone.utc)
    write_universe_snapshot(tmp_path, _snapshot(ts), _members())
    assert read_universe_members(tmp_path, "NSE_MAINBOARD_EQ", stamp="20200101T000000Z") is None


def test_different_universe_ids_are_kept_separate(tmp_path):
    ts = datetime(2026, 9, 24, 8, 0, 0, tzinfo=timezone.utc)
    write_universe_snapshot(tmp_path, _snapshot(ts, universe_id="NSE_MAINBOARD_EQ"), _members(3))
    write_universe_snapshot(tmp_path, _snapshot(ts, universe_id="NIFTY_200"), _members(7))

    assert len(read_universe_members(tmp_path, "NSE_MAINBOARD_EQ")) == 3
    assert len(read_universe_members(tmp_path, "NIFTY_200")) == 7


def test_undated_source_snapshot_persists_none_source_date_honestly(tmp_path):
    ts = datetime(2026, 9, 24, 8, 0, 0, tzinfo=timezone.utc)
    snap = _snapshot(ts, source_date=None, snapshot_date_basis="RETRIEVAL_DATE_SOURCE_UNDATED")
    write_universe_snapshot(tmp_path, snap, _members())
    record = read_universe_snapshot_record(tmp_path, "NSE_MAINBOARD_EQ")
    assert record["source_date"] is None
    assert record["snapshot_date_basis"] == "RETRIEVAL_DATE_SOURCE_UNDATED"


def test_dated_source_snapshot_persists_real_source_date(tmp_path):
    ts = datetime(2026, 9, 24, 8, 0, 0, tzinfo=timezone.utc)
    snap = _snapshot(ts, source_date=date(2026, 9, 23), snapshot_date_basis="SOURCE_DATE")
    write_universe_snapshot(tmp_path, snap, _members())
    record = read_universe_snapshot_record(tmp_path, "NSE_MAINBOARD_EQ")
    assert record["source_date"] == "2026-09-23"
    assert record["snapshot_date_basis"] == "SOURCE_DATE"
