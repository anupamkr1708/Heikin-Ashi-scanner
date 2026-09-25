"""Assembles the full feature set for one security's OHLCV history.

This is the ONLY place that wires together every indicator module (PART 22 / PART 65) — the
indicator modules themselves know nothing about each other. Optional-filter evaluation and
ranking live downstream in pipeline/scan.py, not here.
"""

from __future__ import annotations

import pandas as pd

from nse_scanner.config import ScannerConfig
from nse_scanner.indicators.atr import atr_pct, bb_overshoot_atr, wilder_atr
from nse_scanner.indicators.bollinger import bb_overshoot_pct, calculate_bollinger_bands
from nse_scanner.indicators.candles import calculate_candle_quality
from nse_scanner.indicators.heikin_ashi import calculate_heikin_ashi, ha_body_pct
from nse_scanner.indicators.moving_averages import calculate_trend_structure, history_quality
from nse_scanner.indicators.relative_strength import calculate_relative_strength
from nse_scanner.indicators.volume import calculate_volume_liquidity
from nse_scanner.strategy.breakout import calculate_breakout_state


def build_feature_frame(
    ohlc: pd.DataFrame, cfg: ScannerConfig, index_features: pd.DataFrame | None = None
) -> pd.DataFrame:
    """`ohlc` must already be validated (data/validation.py) — this function does not repair or
    reject rows, it only computes derived features. Returns a single wide DataFrame, one row per
    trading session, indexed the same as `ohlc`."""
    df = ohlc.copy()

    bb = calculate_bollinger_bands(df, cfg.baseline.bb_period, cfg.baseline.bb_std_mult, cfg.baseline.bb_ddof)
    df = df.join(bb)
    df["BB_Overshoot_Pct"] = bb_overshoot_pct(df["Close"], df["BB_Upper"])

    ha = calculate_heikin_ashi(df[["Open", "High", "Low", "Close"]])
    df = df.join(ha)
    df["HA_Body_Pct"] = ha_body_pct(df["HA_Open"], df["HA_Close"])

    atr = wilder_atr(df, cfg.history.atr_period)
    df[f"ATR{cfg.history.atr_period}"] = atr
    df["ATR_Pct"] = atr_pct(atr, df["Close"])
    df["BB_Overshoot_ATR"] = bb_overshoot_atr(df["Close"], df["BB_Upper"], atr)

    trend = calculate_trend_structure(
        df, cfg.history.min_rows_sma20, cfg.history.min_rows_sma50, cfg.history.min_rows_sma200
    )
    df = df.join(trend)

    vol = calculate_volume_liquidity(df)
    df = df.join(vol)

    cq = calculate_candle_quality(df)
    df = df.join(cq)

    bo = calculate_breakout_state(df["Close"], df["BB_Upper"], cfg.baseline.max_bb_overshoot_pct)
    df = df.join(bo)

    rs = calculate_relative_strength(df, index_features)
    df = df.join(rs, rsuffix="_rs")

    df["History_Quality"] = history_quality(len(df), cfg.history.min_rows_sma200, cfg.history.min_rows_sma50)
    df["N_Rows_Available"] = len(df)

    return df
