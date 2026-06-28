"""Parallel signal-validation backtester.

Implements the "Signal validation" playbook from CLAUDE.md as runnable code,
read-only against US_data.db and fully independent of the bot:

  1. Build per-day features (pcr_oi, close, net OI from options_change).
  2. Compute the mean-reversion composite signal.
  3. Join forward N-day return from stock_daily.close.
  4. Score hit-rate + avg forward return vs the unconditional baseline.

Run:  python -m core.validate --ticker SPY --horizon 5 --threshold 3
"""
from __future__ import annotations

import argparse

import pandas as pd

from core import registry, signals  # noqa: F401  (import registers signals)
from core.db import get_conn
from core.dates import sort_key
from core.signals import mean_reversion_composite


def load_features(conn, ticker: str) -> pd.DataFrame:
    """Per-day features for one ticker, sorted by true calendar order."""
    sd = pd.read_sql_query(
        "SELECT ticker, trade_date, close, pcr_oi FROM stock_daily WHERE ticker = ?",
        conn, params=(ticker,),
    )
    oc = pd.read_sql_query(
        "SELECT trade_date_now AS trade_date, "
        "SUM(change_OI_Call) AS coi_call, SUM(change_OI_Put) AS coi_put "
        "FROM options_change WHERE ticker = ? GROUP BY trade_date_now",
        conn, params=(ticker,),
    )
    oc["net_oi"] = oc["coi_call"].fillna(0) - oc["coi_put"].fillna(0)

    df = sd.merge(oc[["trade_date", "net_oi"]], on="trade_date", how="left")
    df["net_oi"] = df["net_oi"].fillna(0)
    df = df.sort_values("trade_date", key=lambda s: s.map(sort_key)).reset_index(drop=True)
    return df


def backtest(df: pd.DataFrame, signal_fn=None, lookback: int = 20, horizon: int = 5,
             threshold: float = 3.0) -> tuple[dict, pd.DataFrame]:
    df = df.copy()
    signal_fn = signal_fn or mean_reversion_composite
    df["composite"] = signal_fn(df, lookback)
    df["fwd_ret"] = df["close"].shift(-horizon) / df["close"] - 1.0
    valid = df.dropna(subset=["composite", "fwd_ret"])
    fires = valid[valid["composite"] >= threshold]

    res = {
        "n_days": int(len(valid)),
        "n_fires": int(len(fires)),
        "hit_rate": float((fires["fwd_ret"] > 0).mean()) if len(fires) else float("nan"),
        "avg_fwd": float(fires["fwd_ret"].mean()) if len(fires) else float("nan"),
        "baseline_up_rate": float((valid["fwd_ret"] > 0).mean()) if len(valid) else float("nan"),
        "baseline_avg_fwd": float(valid["fwd_ret"].mean()) if len(valid) else float("nan"),
    }
    return res, fires


def _fmt_pct(x: float, signed: bool = False) -> str:
    if x != x:  # NaN
        return "n/a"
    return f"{x:+.2%}" if signed else f"{x:.1%}"


def main() -> None:
    ap = argparse.ArgumentParser(description="Backtest a registered signal vs DB history.")
    ap.add_argument("--ticker", default="SPY")
    ap.add_argument("--signal", default="mean_reversion", help=f"one of: {', '.join(registry.names())}")
    ap.add_argument("--lookback", type=int, default=20)
    ap.add_argument("--horizon", type=int, default=5)
    ap.add_argument("--threshold", type=float, default=3.0)
    args = ap.parse_args()

    signal_fn = registry.get(args.signal)

    with get_conn() as conn:
        df = load_features(conn, args.ticker)

    if df.empty:
        print(f"No stock_daily rows for {args.ticker}")
        return

    res, _ = backtest(df, signal_fn, args.lookback, args.horizon, args.threshold)
    edge = (res["hit_rate"] - res["baseline_up_rate"]) if res["n_fires"] else float("nan")

    print(f"\nSignal '{args.signal}' (>= {args.threshold} -> LONG)")
    print(f"{args.ticker} | lookback {args.lookback}d | horizon {args.horizon}d\n")
    print(f"  days evaluated   : {res['n_days']}")
    print(f"  signal fires     : {res['n_fires']}")
    print(f"  hit-rate (LONG)  : {_fmt_pct(res['hit_rate'])}")
    print(f"  avg fwd return   : {_fmt_pct(res['avg_fwd'], signed=True)}")
    print(f"  baseline up-rate : {_fmt_pct(res['baseline_up_rate'])}")
    print(f"  baseline avg fwd : {_fmt_pct(res['baseline_avg_fwd'], signed=True)}")
    print(f"  edge vs baseline : {_fmt_pct(edge)}\n")


if __name__ == "__main__":
    main()
