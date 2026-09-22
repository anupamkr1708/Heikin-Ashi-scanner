"""Market regime classification (PART 24).

**This is a RESEARCH DEFINITION, not an objectively correct market regime.** It is never a
mandatory filter by default (config.OptionalFilterConfig.use_market_regime_filter defaults to
False) — it exists to segment research and, optionally, to confirm a signal.

    BULL    : Index_Close > Index_SMA200 AND Index_SMA50 > Index_SMA200
    BEAR    : Index_Close < Index_SMA200 AND Index_SMA50 < Index_SMA200
    NEUTRAL : anything else, once SMA200 is available
    UNAVAILABLE_INSUFFICIENT_INDEX_HISTORY : SMA200 cannot be computed yet
"""

from __future__ import annotations

import pandas as pd

BULL = "BULL"
BEAR = "BEAR"
NEUTRAL = "NEUTRAL"
UNAVAILABLE_INSUFFICIENT_HISTORY = "UNAVAILABLE_INSUFFICIENT_INDEX_HISTORY"

REGIME_RULE_DESCRIPTION = (
    "RESEARCH DEFINITION (not an objectively correct regime): "
    "BULL if Close>SMA200 & SMA50>SMA200; BEAR if Close<SMA200 & SMA50<SMA200; else NEUTRAL."
)


def classify_regime(index_close: float, index_sma50: float, index_sma200: float) -> str:
    if pd.isna(index_sma200):
        return UNAVAILABLE_INSUFFICIENT_HISTORY
    if index_close > index_sma200 and index_sma50 > index_sma200:
        return BULL
    if index_close < index_sma200 and index_sma50 < index_sma200:
        return BEAR
    return NEUTRAL
