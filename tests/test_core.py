"""Tests for the parallel core/ package (pure logic only — no DB needed)."""
import pandas as pd

from core.dates import sort_key, parse, to_str, to_iso
from core.signals import zscore, mean_reversion_composite
from core.validate import backtest


def test_to_iso_normalizes_all_formats():
    assert to_iso("06-28-2026") == "2026-06-28"   # US MM-DD-YYYY
    assert to_iso("2026-06-28") == "2026-06-28"   # already ISO
    assert to_iso("2026-06-28 17:00:00") == "2026-06-28"  # ISO + time
    assert to_iso("08Jan2026") == "2026-01-08"    # DDMonYYYY outlier
    assert to_iso("") == ""                         # empty passthrough


def test_sort_key_orders_mixed_formats():
    dates = ["12-31-2024", "2025-01-02", "06-15-2024"]  # mixed US + ISO
    assert sorted(dates, key=sort_key) == ["06-15-2024", "12-31-2024", "2025-01-02"]


def test_parse_and_to_str_are_iso():
    assert to_str(parse("06-28-2026")) == "2026-06-28"


def test_zscore_has_values_after_lookback():
    z = zscore(pd.Series(range(25)), 20)
    assert z.iloc[:19].isna().all()
    assert z.iloc[20:].notna().all()


def test_composite_sign_is_long_when_oversold():
    df = pd.DataFrame({
        "pcr_oi": [1.0] * 19 + [5.0],   # PCR spikes up
        "close": [100.0] * 19 + [90.0],  # price drops
        "net_oi": [0.0] * 19 + [-1000.0],  # net puts added
    })
    c = mean_reversion_composite(df, 19)
    assert c.iloc[-1] > 0  # all three terms push composite positive -> LONG bias


def test_backtest_shapes_and_baseline():
    # All three inputs must vary, else their z-scores are NaN (zero std) and
    # the composite is undefined. Periodic series keep rolling std > 0.
    df = pd.DataFrame({
        "close": [100 + (i % 7) for i in range(30)],
        "pcr_oi": [1.0 + 0.1 * (i % 5) for i in range(30)],
        "net_oi": [100.0 * (i % 3) for i in range(30)],
    })
    res, fires = backtest(df, lookback=20, horizon=5, threshold=3.0)
    # valid = rows with both a composite (idx>=19) and a fwd_ret (idx<=24) -> 19..24
    assert res["n_days"] == 6
    assert 0.0 <= res["baseline_up_rate"] <= 1.0
    assert len(fires) == res["n_fires"]
