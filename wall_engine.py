"""wall_engine.py — gamma/OI wall computation.

Recreated source: the original wall_engine.py was never committed and only a
stale compiled wall_engine.pyc remained, which crashed under Python 3.13
("'<=' not supported between instances of 'function' and 'int'"). This is a
clean reimplementation matching how dashboard.py calls it.

compute_walls(df, spot) identifies:
  • call wall = strike with the most call open interest (resistance)
  • put  wall = strike with the most put  open interest (support)
along with each wall's OI and its strength (OI ÷ mean OI on that side).

Input df must have columns: strike, openInt_Call_now, openInt_Put_now.
Returns a dict; walls are None when there is no usable data.
"""
from __future__ import annotations

import pandas as pd


def compute_walls(df, spot=None):
    out = {
        "call_wall": None, "put_wall": None,
        "call_wall_oi": 0.0, "put_wall_oi": 0.0,
        "call_wall_strength": 0.0, "put_wall_strength": 0.0,
    }
    if df is None or len(df) == 0:
        return out
    try:
        d = df.copy()
        for col in ("strike", "openInt_Call_now", "openInt_Put_now"):
            if col in d.columns:
                d[col] = pd.to_numeric(d[col], errors="coerce")
        d = d.dropna(subset=["strike"])
        if d.empty:
            return out

        c = d["openInt_Call_now"].fillna(0.0) if "openInt_Call_now" in d.columns else pd.Series(0.0, index=d.index)
        p = d["openInt_Put_now"].fillna(0.0) if "openInt_Put_now" in d.columns else pd.Series(0.0, index=d.index)

        mean_c = float(c[c > 0].mean()) if (c > 0).any() else 0.0
        mean_p = float(p[p > 0].mean()) if (p > 0).any() else 0.0

        if (c > 0).any():
            ci = c.idxmax()
            out["call_wall"] = float(d.loc[ci, "strike"])
            out["call_wall_oi"] = float(c.loc[ci])
            out["call_wall_strength"] = (out["call_wall_oi"] / mean_c) if mean_c > 0 else 0.0

        if (p > 0).any():
            pi = p.idxmax()
            out["put_wall"] = float(d.loc[pi, "strike"])
            out["put_wall_oi"] = float(p.loc[pi])
            out["put_wall_strength"] = (out["put_wall_oi"] / mean_p) if mean_p > 0 else 0.0
    except Exception:
        return out
    return out
