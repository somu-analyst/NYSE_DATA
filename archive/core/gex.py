"""Signed Gamma Exposure (GEX) — pure, read-only port of the bot's _compute_gex.

Net GEX/strike = gamma x (call_OI - put_OI) x spot^2 x 0.01   (calls +, puts -).
  Positive total GEX -> dealers long gamma -> vol suppression (pinning / mean-revert).
  Negative total GEX -> dealers short gamma -> moves amplified (trending).
Gamma flip (zero_gamma) = price where TOTAL GEX crosses zero on a price grid.

Independent of the bot/Telegram; reads US_data.db (ISO dates). Run:
    python -m core.gex --ticker SPY --spot 540
"""
from __future__ import annotations

import argparse

import pandas as pd

from core.dates import parse as _parse
from core.db import get_conn
from core.options_math import bs_gamma, implied_vol


def compute_gex(conn, ticker: str, spot: float, expiry: str | None = None) -> dict:
    result = {"total_gex": 0.0, "zero_gamma": None, "gex_signal": "UNKNOWN",
              "top_strikes": [], "regime": "UNKNOWN", "total_gex_m": 0.0,
              "call_wall": None, "put_wall": None, "expiry": None, "dte": None}
    if not spot or spot <= 0:
        return result

    ld = pd.read_sql("SELECT trade_date_now FROM options_change WHERE ticker=? "
                     "ORDER BY trade_date_now DESC LIMIT 1", conn, params=(ticker,))
    if ld.empty:
        return result
    date_str = ld["trade_date_now"].iloc[0]
    ref_dt = _parse(date_str)

    edf = pd.read_sql("SELECT expiry_date, SUM(openInt_Call_now)+SUM(openInt_Put_now) AS oi "
                      "FROM options_change WHERE ticker=? AND trade_date_now=? GROUP BY expiry_date",
                      conn, params=(ticker, date_str))
    cand = []
    for _, e in edf.iterrows():
        try:
            dte = (_parse(str(e["expiry_date"])) - ref_dt).days
        except Exception:
            continue
        if dte >= 0:
            cand.append((dte, float(e["oi"] or 0), str(e["expiry_date"])))
    if not cand:
        return result

    if expiry and any(c[2] == expiry for c in cand):
        dte_days, _oi, expiry_s = next(c for c in cand if c[2] == expiry)
    else:
        pool = [c for c in cand if c[0] >= 1] or cand
        near = [c for c in pool if c[0] <= 60]
        dte_days, _oi, expiry_s = (max(near, key=lambda c: c[1]) if near
                                   else min(pool, key=lambda c: c[0]))
    T = max(dte_days / 365.0, 1.0 / 365.0)
    result["expiry"] = expiry_s
    result["dte"] = dte_days

    hv = 0.30
    hsd = pd.read_sql("SELECT close FROM stock_daily WHERE ticker=? "
                      "ORDER BY trade_date DESC LIMIT 25", conn, params=(ticker,))
    if len(hsd) >= 10:
        rets = hsd["close"].astype(float).pct_change().dropna()
        hv = max(0.10, min(float(rets.std() * (252 ** 0.5)), 2.0))

    df = pd.read_sql(
        "SELECT strike, SUM(openInt_Call_now) AS c_oi, SUM(openInt_Put_now) AS p_oi, "
        "AVG(CASE WHEN lastPrice_Call_now>0 THEN lastPrice_Call_now END) AS c_px "
        "FROM options_change WHERE ticker=? AND trade_date_now=? AND expiry_date=? "
        "GROUP BY strike ORDER BY strike",
        conn, params=(ticker, date_str, expiry_s))
    if df.empty:
        return result

    strikes = []
    for _, row in df.iterrows():
        K = float(row["strike"])
        c_oi, p_oi = float(row["c_oi"] or 0), float(row["p_oi"] or 0)
        if c_oi <= 0 and p_oi <= 0:
            continue
        c_px = float(row["c_px"] or 0)
        sigma = hv
        if c_px > 0.10 and abs(K - spot) / spot < 0.30 and K >= spot * 0.85:
            iv = implied_vol(c_px, spot, K, T)
            if iv and 0.03 < iv < 3.0:
                sigma = iv
        strikes.append((K, c_oi, p_oi, max(0.05, min(sigma, 3.0))))
    if not strikes:
        return result

    gex_by_strike = []
    for K, c_oi, p_oi, sig in strikes:
        g = bs_gamma(spot, K, T, sig)
        gex_by_strike.append((K, (c_oi - p_oi) * g * spot * spot * 0.01,
                              c_oi * g * spot * spot * 0.01, p_oi * g * spot * spot * 0.01))

    total_gex = sum(g[1] for g in gex_by_strike)
    result["total_gex"] = total_gex
    result["total_gex_m"] = total_gex / 1e6

    cw = max(gex_by_strike, key=lambda x: x[1])
    pw = min(gex_by_strike, key=lambda x: x[1])
    result["call_wall"] = cw[0] if cw[1] > 0 else None
    result["put_wall"] = pw[0] if pw[1] < 0 else None

    lo, hi, n, prev = spot * 0.80, spot * 1.20, 80, None
    for i in range(n + 1):
        S = lo + (hi - lo) * i / n
        tot = sum((c_oi - p_oi) * bs_gamma(S, K, T, sig) * S * S * 0.01
                  for K, c_oi, p_oi, sig in strikes)
        if prev is not None and (tot >= 0) != (prev[1] >= 0):
            result["zero_gamma"] = round((prev[0] + S) / 2, 2)
            break
        prev = (S, tot)

    if total_gex > 0:
        result["gex_signal"], result["regime"] = "PINNING", "Low vol - dealers suppress moves, mean revert"
    else:
        result["gex_signal"], result["regime"] = "TRENDING", "High vol - dealers amplify direction, trend follow"

    top = sorted(gex_by_strike, key=lambda x: -abs(x[1]))[:5]
    result["top_strikes"] = [{"strike": s, "gex_m": g / 1e6, "c_gex": cg / 1e6, "p_gex": pg / 1e6}
                             for s, g, cg, pg in top]
    return result


def main() -> None:
    ap = argparse.ArgumentParser(description="Compute signed GEX for a ticker.")
    ap.add_argument("--ticker", default="SPY")
    ap.add_argument("--spot", type=float, required=True, help="current spot price")
    ap.add_argument("--expiry", default=None)
    args = ap.parse_args()
    with get_conn() as conn:
        r = compute_gex(conn, args.ticker, args.spot, args.expiry)
    print(f"\n{args.ticker}  spot={args.spot}  expiry={r['expiry']} (dte={r['dte']})")
    print(f"  total GEX     : {r['total_gex_m']:+,.1f} $M  -> {r['gex_signal']}")
    print(f"  regime        : {r['regime']}")
    print(f"  zero-gamma    : {r['zero_gamma']}")
    print(f"  call/put wall : {r['call_wall']} / {r['put_wall']}")
    for s in r["top_strikes"]:
        print(f"    {s['strike']:>9.2f}  net {s['gex_m']:+8.2f}$M  (C {s['c_gex']:+.2f} / P {s['p_gex']:+.2f})")
    print()


if __name__ == "__main__":
    main()
