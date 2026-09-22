"""Genuine rolling walk-forward window generation (PART 49, fixes BUG 11).

**Audit note (BUG 11):** the legacy notebook had one static TRAIN_START/TRAIN_END/TEST_START/
TEST_END split and called it "walk-forward." A single split is an out-of-sample test, not
walk-forward. This module generates a SEQUENCE of rolling windows; each one must be run through
the research pipeline independently and its own out-of-sample metrics stored (window_id,
train_start, train_end, test_start, test_end, parameters, signals, metrics — PART 49). Nothing
here optimizes a parameter on the test period — parameter selection (if any) is the caller's
responsibility and must use only the train period's data.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from dateutil.relativedelta import relativedelta


@dataclass(frozen=True)
class WalkForwardWindow:
    window_id: int
    train_start: date
    train_end: date
    test_start: date
    test_end: date


def generate_walk_forward_windows(data_start: date, data_end: date, train_years: float = 3.0,
                                   test_months: float = 6.0) -> list[WalkForwardWindow]:
    """Rolls a fixed-length train window forward by `test_months` each step, with the test window
    immediately following the train window. Stops once the test window would run past `data_end`.
    """
    train_delta = relativedelta(years=int(train_years), months=round((train_years % 1) * 12))
    test_delta = relativedelta(months=int(test_months), days=round((test_months % 1) * 30))

    windows: list[WalkForwardWindow] = []
    window_id = 1
    train_start = data_start

    while True:
        train_end = train_start + train_delta - relativedelta(days=1)
        test_start = train_end + relativedelta(days=1)
        test_end = test_start + test_delta - relativedelta(days=1)

        if test_end > data_end:
            break

        windows.append(WalkForwardWindow(window_id, train_start, train_end, test_start, test_end))
        window_id += 1
        train_start = train_start + test_delta

    return windows
