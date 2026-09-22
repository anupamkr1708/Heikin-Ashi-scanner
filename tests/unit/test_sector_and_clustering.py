import numpy as np
from nse_scanner.research.statistics import (
    clustering_by_dimension,
    sector_summary,
    top_sector_contribution_pct,
)


def test_sector_summary_basic_aggregation():
    sectors = np.array(["Tech", "Tech", "Financials", "Tech"])
    returns = np.array([5.0, 3.0, -2.0, 1.0])
    table = sector_summary(sectors, returns)

    assert set(table["Sector"]) == {"Tech", "Financials"}
    tech_row = table[table["Sector"] == "Tech"].iloc[0]
    assert tech_row["N"] == 3
    assert tech_row["Mean_Return_Pct"] == np.mean([5.0, 3.0, 1.0])
    assert tech_row["Pct_Of_Total_Signals"] == 75.0


def test_sector_summary_groups_missing_sector_as_unknown():
    sectors = np.array(["Tech", None, np.nan])
    returns = np.array([1.0, 2.0, 3.0])
    table = sector_summary(sectors, returns)
    unknown_row = table[table["Sector"] == "UNKNOWN"].iloc[0]
    assert unknown_row["N"] == 2  # None and NaN both collapse into UNKNOWN, never dropped
    assert table["N"].sum() == 3  # total row count still reconciles with the input


def test_top_sector_contribution_detects_concentration():
    sectors = np.array(["Tech"] * 9 + ["Energy"] * 1)
    assert top_sector_contribution_pct(sectors) == 90.0


def test_top_sector_contribution_zero_for_empty_input():
    assert top_sector_contribution_pct(np.array([])) == 0.0


def test_clustering_by_dimension_adds_sector_stats():
    symbols = np.array(["A", "B", "C", "D"])
    dates = np.array(["2026-01-01"] * 2 + ["2026-01-02"] * 2)
    sectors = np.array(["Tech", "Tech", "Financials", "Energy"])
    stats = clustering_by_dimension(symbols, dates, sectors)
    assert stats["unique_sectors"] == 3
    assert stats["avg_signals_per_sector"] == 4 / 3
    assert stats["unique_stocks"] == 4
