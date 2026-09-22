"""Descriptive ranking score (PART 25).

**NOT A PROBABILITY. NOT EXPECTED RETURN. NOT STATISTICALLY VALIDATED.** Named
`Research_Heuristic_Score` throughout the codebase and reports for exactly that reason. It exists
only to order candidates within a single day's Live_Signals sheet.

If the benchmark (needed for the RS component) is unavailable, the score is NOT silently
renormalized over the remaining components (BUG 4 / BUG 16) — `Score_Status` becomes "PARTIAL"
and that must be surfaced to the reader, not hidden.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

SCORE_STATUS_FULL = "FULL"
SCORE_STATUS_PARTIAL = "PARTIAL"
SCORE_STATUS_UNAVAILABLE = "UNAVAILABLE"

# Manually chosen weights (PART 25 / BUG 15) — explicitly NOT statistically validated.
_WEIGHTS = {
    "trend": 0.25,      # Dist_SMA50_Pct, clipped/normalized
    "volume": 0.15,     # Volume_Ratio_20, clipped/normalized
    "candle": 0.15,     # Close_Location_Value, already in [-1, 1]
    "rs": 0.25,         # RS_20D, clipped/normalized
    "volatility": 0.20,  # inverse of BB_Overshoot_ATR extremity
}


@dataclass(frozen=True)
class ScoreResult:
    score: float | None
    status: str
    coverage: float  # fraction of components that had usable data


def _norm(value: float | None, lo: float, hi: float) -> float | None:
    if value is None or pd.isna(value):
        return None
    x = max(lo, min(hi, value))
    return (x - lo) / (hi - lo)


def calculate_research_heuristic_score(row: pd.Series, benchmark_available: bool) -> ScoreResult:
    components: dict[str, float | None] = {
        "trend": _norm(row.get("Dist_SMA50_Pct"), -10, 25),
        "volume": _norm(row.get("Volume_Ratio_20"), 0.5, 3.0),
        "candle": _norm(row.get("Close_Location_Value"), -1, 1),
        "volatility": _norm(-(abs(row.get("BB_Overshoot_ATR")) if pd.notna(row.get("BB_Overshoot_ATR")) else None)
                             if row.get("BB_Overshoot_ATR") is not None else None, -3, 0),
        "rs": _norm(row.get("RS_20D"), -15, 15) if benchmark_available else None,
    }

    usable = {k: v for k, v in components.items() if v is not None}
    coverage = len(usable) / len(_WEIGHTS)

    if not usable:
        return ScoreResult(score=None, status=SCORE_STATUS_UNAVAILABLE, coverage=0.0)

    weighted_sum = sum(usable[k] * _WEIGHTS[k] for k in usable)
    weight_total = sum(_WEIGHTS[k] for k in usable)
    score = weighted_sum / weight_total  # renormalized over AVAILABLE components only — and the
                                          # PARTIAL status below is exactly what keeps that from
                                          # being a silent renormalization (BUG 4/16).

    status = SCORE_STATUS_FULL if coverage == 1.0 and benchmark_available else SCORE_STATUS_PARTIAL
    return ScoreResult(score=round(score * 100, 2), status=status, coverage=round(coverage, 2))
