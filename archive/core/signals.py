"""Pure signal functions — no I/O, no Telegram, no DB. Easy to unit-test.

Currently implements the mean-reversion composite documented in CLAUDE.md:
    composite = 1.5*PCR_z - Price_z - NetOI_z   (>= +3 -> LONG, lookback 20d)
Add more pure signals here as they are ported from the bot.
"""
from __future__ import annotations

import pandas as pd

from core.registry import register


def zscore(s: pd.Series, lookback: int = 20) -> pd.Series:
    """Trailing rolling z-score over `lookback` rows (population std)."""
    mean = s.rolling(lookback).mean()
    std = s.rolling(lookback).std(ddof=0)
    return (s - mean) / std.replace(0, pd.NA)


@register("mean_reversion")
def mean_reversion_composite(df: pd.DataFrame, lookback: int = 20) -> pd.Series:
    """1.5*PCR_z - Price_z - NetOI_z. Expects columns: pcr_oi, close, net_oi.

    High PCR + falling price + net put accumulation -> strongly positive -> LONG.
    """
    pcr_z = zscore(df["pcr_oi"], lookback)
    price_z = zscore(df["close"], lookback)
    netoi_z = zscore(df["net_oi"], lookback)
    return 1.5 * pcr_z - price_z - netoi_z
