"""Event-study descriptive statistics and clustered bootstrap confidence intervals (PART 47/48).

Stock-day observations are not independent (PART 48): multiple signals on the same calendar day
share market-wide moves, and multiple signals on the same stock share stock-specific momentum.
`bootstrap_ci_clustered` resamples whole clusters (all of one day's, or one stock's, observations
together) rather than resampling individual rows, which would understate the true uncertainty.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class DescriptiveStats:
    n: int
    unique_stocks: int
    unique_days: int
    mean: float | None
    median: float | None
    std: float | None
    win_rate: float | None
    average_win: float | None
    average_loss: float | None
    best: float | None
    worst: float | None
    profit_factor: float | None
    expectancy: float | None


def describe_returns(returns_pct: np.ndarray, symbols: np.ndarray | None = None,
                      dates: np.ndarray | None = None) -> DescriptiveStats:
    r = np.asarray(returns_pct, dtype=float)
    r = r[~np.isnan(r)]
    n = r.size
    if n == 0:
        return DescriptiveStats(
            n=0, unique_stocks=0, unique_days=0, mean=None, median=None, std=None, win_rate=None,
            average_win=None, average_loss=None, best=None, worst=None, profit_factor=None, expectancy=None,
        )

    wins = r[r > 0]
    losses = r[r < 0]
    win_rate = wins.size / n if n else None
    avg_win = float(wins.mean()) if wins.size else None
    avg_loss = float(losses.mean()) if losses.size else None
    gross_profit = float(wins.sum()) if wins.size else 0.0
    gross_loss = float(-losses.sum()) if losses.size else 0.0
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else (float("inf") if gross_profit > 0 else None)
    expectancy = float(r.mean())

    return DescriptiveStats(
        n=n,
        unique_stocks=int(len(set(symbols))) if symbols is not None else n,
        unique_days=int(len(set(dates))) if dates is not None else n,
        mean=float(r.mean()), median=float(np.median(r)), std=float(r.std(ddof=1)) if n > 1 else 0.0,
        win_rate=win_rate, average_win=avg_win, average_loss=avg_loss,
        best=float(r.max()), worst=float(r.min()),
        profit_factor=profit_factor, expectancy=expectancy,
    )


def bootstrap_ci_clustered(returns_pct: np.ndarray, cluster_ids: np.ndarray, n_boot: int = 2000,
                            ci: float = 0.90, statistic: str = "mean", random_state: int = 42
                            ) -> tuple[float, float]:
    """Block/cluster bootstrap: resample cluster IDs with replacement, take all observations in
    the resampled clusters, compute the statistic. Returns (lower, upper) at the given CI level.
    Use once with cluster_ids = day, once with cluster_ids = stock (PART 48 — both matter and
    neither alone is a full dependence correction)."""
    rng = np.random.default_rng(random_state)
    r = np.asarray(returns_pct, dtype=float)
    c = np.asarray(cluster_ids)
    mask = ~np.isnan(r)
    r, c = r[mask], c[mask]

    unique_clusters = np.unique(c)
    if unique_clusters.size == 0:
        return (float("nan"), float("nan"))

    boot_stats = np.empty(n_boot)
    cluster_to_values = {u: r[c == u] for u in unique_clusters}
    for b in range(n_boot):
        sampled = rng.choice(unique_clusters, size=unique_clusters.size, replace=True)
        pooled = np.concatenate([cluster_to_values[u] for u in sampled]) if sampled.size else np.array([])
        if pooled.size == 0:
            boot_stats[b] = np.nan
            continue
        if statistic == "mean":
            boot_stats[b] = pooled.mean()
        elif statistic == "win_rate":
            boot_stats[b] = float((pooled > 0).mean())
        else:
            raise ValueError(f"unsupported statistic: {statistic}")

    boot_stats = boot_stats[~np.isnan(boot_stats)]
    alpha = (1 - ci) / 2
    lower = float(np.percentile(boot_stats, alpha * 100))
    upper = float(np.percentile(boot_stats, (1 - alpha) * 100))
    return lower, upper


def cluster_summary(symbols: np.ndarray, dates: np.ndarray) -> dict[str, float]:
    """Signals-per-day / signals-per-stock summary (PART 48), so a research report always shows
    how concentrated the sample is before quoting a confidence interval."""
    n = len(symbols)
    if n == 0:
        return {"n": 0, "unique_stocks": 0, "unique_days": 0,
                "avg_signals_per_day": 0.0, "avg_signals_per_stock": 0.0}
    unique_stocks = len(set(symbols))
    unique_days = len(set(dates))
    return {
        "n": n,
        "unique_stocks": unique_stocks,
        "unique_days": unique_days,
        "avg_signals_per_day": n / unique_days if unique_days else 0.0,
        "avg_signals_per_stock": n / unique_stocks if unique_stocks else 0.0,
    }


def sector_summary(sectors: np.ndarray, returns_pct: np.ndarray) -> pd.DataFrame:
    """Per-sector signal count, mean/median return, and % contribution to total signal count
    (PART 31). A strategy that "works" only because one sector dominated the sample is a
    materially different (and weaker) finding than one with broad sector participation — this
    table exists so that difference is never invisible.

    Rows with a missing/NaN sector are grouped under 'UNKNOWN' rather than silently dropped, so
    the sector table's total row count still reconciles with the overall sample size.
    """
    sectors_clean = pd.Series(sectors).fillna("UNKNOWN").replace("", "UNKNOWN")
    df = pd.DataFrame({"Sector": sectors_clean, "Return_Pct": returns_pct})
    total_n = len(df)

    grouped = df.groupby("Sector")["Return_Pct"].agg(
        N="count", Mean_Return_Pct="mean", Median_Return_Pct="median",
    ).reset_index()
    grouped["Pct_Of_Total_Signals"] = (grouped["N"] / total_n * 100.0) if total_n else 0.0
    grouped = grouped.sort_values("N", ascending=False).reset_index(drop=True)
    return grouped


def top_sector_contribution_pct(sectors: np.ndarray) -> float:
    """The single largest sector's share of the total signal count (PART 31's "top-sector
    contribution") — a quick single-number flag for "is this result actually one sector wearing
    a strategy costume."""
    if len(sectors) == 0:
        return 0.0
    counts = pd.Series(sectors).fillna("UNKNOWN").value_counts()
    return float(counts.iloc[0] / counts.sum() * 100.0)


def clustering_by_dimension(symbols: np.ndarray, dates: np.ndarray, sectors: np.ndarray) -> dict[str, float]:
    """Extends `cluster_summary` with the sector dimension (PART 32: "signals/day/sector")."""
    base = cluster_summary(symbols, dates)
    if len(sectors) == 0:
        base["unique_sectors"] = 0
        base["avg_signals_per_sector"] = 0.0
        return base
    sectors_clean = pd.Series(sectors).fillna("UNKNOWN")
    unique_sectors = sectors_clean.nunique()
    base["unique_sectors"] = int(unique_sectors)
    base["avg_signals_per_sector"] = len(symbols) / unique_sectors if unique_sectors else 0.0
    return base
