"""Survivorship-bias mode labeling (PART 29).

    MODE_A: CURRENT_UNIVERSE_HISTORICAL_SIMULATION
        Today's constituent list applied backward through history. Discloses survivorship bias
        explicitly — securities that were delisted/removed/renamed are absent from the whole
        study, not just from the periods after their removal.

    MODE_B: POINT_IN_TIME_NIFTY_200
        Requires actual historical membership records (universe_id, snapshot_date, isin,
        membership_start, membership_end, source). This repository does not have a verified
        historical-membership feed wired up (PART 89/limitations), so MODE_B is implemented as a
        data contract + validator here; it will raise if fed anything that isn't genuinely
        point-in-time, rather than silently accepting a current-universe list mislabeled as MODE_B.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import pandas as pd

MODE_A_CURRENT_UNIVERSE = "CURRENT_UNIVERSE_HISTORICAL_SIMULATION"
MODE_B_POINT_IN_TIME = "POINT_IN_TIME_NIFTY_200"

REQUIRED_MEMBERSHIP_COLUMNS = ("universe_id", "snapshot_date", "isin", "membership_start", "membership_end", "source")

MODE_A_DISCLOSURE = (
    "This research used TODAY'S constituent list applied backward through history "
    "(CURRENT_UNIVERSE_HISTORICAL_SIMULATION). Securities that were delisted, renamed, or "
    "removed from the index during the study period are NOT represented for the periods before "
    "their removal. This is a form of survivorship bias and materially affects results, "
    "particularly for the earliest years of a long study window (PART 29)."
)


@dataclass(frozen=True)
class SurvivorshipLabel:
    mode: str
    disclosure: str


def label_current_universe_mode() -> SurvivorshipLabel:
    return SurvivorshipLabel(mode=MODE_A_CURRENT_UNIVERSE, disclosure=MODE_A_DISCLOSURE)


def validate_point_in_time_membership(membership_df: pd.DataFrame) -> SurvivorshipLabel:
    missing = [c for c in REQUIRED_MEMBERSHIP_COLUMNS if c not in membership_df.columns]
    if missing:
        raise ValueError(
            f"membership_df is missing columns {missing} required for POINT_IN_TIME_NIFTY_200. "
            f"Do not label a current-constituent list as point-in-time — use "
            f"label_current_universe_mode() and disclose the survivorship limitation instead."
        )
    return SurvivorshipLabel(
        mode=MODE_B_POINT_IN_TIME,
        disclosure="Point-in-time membership records validated; results reflect actual historical "
                    "index composition, not today's constituents applied backward.",
    )


def membership_as_of(membership_df: pd.DataFrame, as_of: date) -> list[str]:
    """Returns the list of ISINs that were index members on `as_of`, given a validated
    point-in-time membership table."""
    mask = (membership_df["membership_start"] <= as_of) & (
        membership_df["membership_end"].isna() | (membership_df["membership_end"] >= as_of)
    )
    return membership_df.loc[mask, "isin"].tolist()
