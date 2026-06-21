"""
RUDRARJUN ANALYTICS — Unified Options Intelligence Dashboard
=============================================================
Bloomberg-style terminal for options trading, OI analytics,
portfolio management, and risk prediction.

All data: Yahoo Finance + SQLite (US_data.db).  Zero IBKR dependency.
"""

import streamlit as st
import pandas as pd
import numpy as np
import sqlite3
import plotly.graph_objects as go
import plotly.express as px
from plotly.subplots import make_subplots
from datetime import datetime, timedelta
import yfinance as yf
from scipy.stats import norm
import math
import warnings, sys, os

warnings.filterwarnings("ignore")


def compute_walls(df, spot=None):
    """Identify the call wall (max call-OI strike = resistance) and put wall
    (max put-OI strike = support), with each wall's OI and strength (OI / mean OI).
    Input df needs columns: strike, openInt_Call_now, openInt_Put_now.
    (Inlined here — kept in the single main file, no separate module.)"""
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

import re

# Sanitize strings passed to Streamlit `markdown`/`write` to strip , style, and class
# attributes so any HTML produced here is safe for downstream use (e.g., Telegram messages).
_original_st_markdown = st.markdown
_original_st_write = st.write
def _sanitize_text(s):
    if not isinstance(s, str):
        return s
    s = re.sub(r'</?span[^>]*>', '', s)
    s = re.sub(r"\s*(style|class)=(\".*?\"|'.*?')", '', s)
    return s
def _st_markdown(text, *args, **kwargs):
    return _original_st_markdown(_sanitize_text(text), *args, **kwargs)
def _st_write(text, *args, **kwargs):
    return _original_st_write(_sanitize_text(text), *args, **kwargs)
st.markdown = _st_markdown
st.write = _st_write

# One-time in-place cleanup: remove remaining  tags and inline style/class
# attributes from the source file so the dashboard source is safe for reuse.
def _cleanup_dashboard_source():
    try:
        path = __file__
        with open(path, 'r', encoding='utf-8') as f:
            src = f.read()
        cleaned = re.sub(r'</?span[^>]*>', '', src)
        cleaned = re.sub(r"\s*(style|class)=(\".*?\"|'.*?')", '', cleaned)
        if cleaned != src:
            with open(path, 'w', encoding='utf-8') as f:
                f.write(cleaned)
            # Log to stdout so developers notice the cleanup
            print("[dashboard] cleaned /style/class from source")
    except Exception:
        pass

# Run the cleanup during import (safe and idempotent).
_cleanup_dashboard_source()

# ---------------------------------------------------------------------------
# PATHS
# ---------------------------------------------------------------------------
DB_PATH = r"C:\Users\srini\Options_chain_data\US_data.db"
LIB_DIR = os.path.join(os.path.dirname(__file__), "_lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

# ---------------------------------------------------------------------------
# DB HELPER
# ---------------------------------------------------------------------------
# OI Intent Algorithm (hedge-aware) — used across all OI analysis sections
# ---------------------------------------------------------------------------
def _oi_signal_light(call_chg, put_chg, pcr=1.0):
    """Aggregate-level hedge-aware OI signal. Returns (label, hex_color)."""
    c, p = float(call_chg or 0), float(put_chg or 0)
    pcr  = float(pcr or 1.0)
    if c > abs(p) * 1.2 and c > 0:
        return ("BULLISH", "#2E7D32")
    if p > abs(c) * 1.2 and p > 0:
        if pcr > 1.5 and c >= -200:
            return ("HEDGE", "#1565C0")
        return ("BEARISH", "#C62828")
    if c > 200 and p > 200:
        if c > p * 1.4:  return ("BULL+HEDGE", "#388E3C")
        return ("STRADDLE", "#6A1B9A")
    if c < 0 and p < 0:
        return ("UNWIND", "#757575")
    return ("NEUTRAL", "#455A64")


def _oi_intent_algo(df, spot):
    """
    Per-strike hedge-aware OI intent classification.
    Expects df with 'strike', 'call_oi_change', 'put_oi_change' columns.
    Returns (enriched_df, signal, color, description, details_dict).

    Zones: ATM (+-3%), NEAR_PUT (3-10% below), DEEP_PUT (>10% below = hedge),
           NEAR_CALL (3-7% above), FAR_CALL (>7% = covered call).
    Deep OTM puts get 70% discount in score -- they are protective hedges, not directional.
    """
    df = df.copy()
    df["_pct"] = (df["strike"] - spot) / spot
    ATM_BAND, NEAR_BAND, FAR_CALL = 0.03, 0.10, 0.07
    _COLORS = {
        "BULLISH": "#2E7D32", "BEARISH": "#C62828", "STRADDLE": "#6A1B9A",
        "NEAR_BEARISH": "#BF360C", "HEDGE": "#1565C0", "HEDGE_UNWIND": "#42A5F5",
        "BULLISH_BREAK": "#388E3C", "COVERED_CALL": "#F57F17",
        "UNWIND": "#757575", "NEUTRAL": "#90A4AE",
    }
    def _cls(row):
        pct, cd, pd_ = row["_pct"], float(row.get("call_oi_change", 0)), float(row.get("put_oi_change", 0))
        z = ("ATM" if abs(pct) <= ATM_BAND else
             "NEAR_PUT" if pct < -ATM_BAND and pct >= -NEAR_BAND else
             "DEEP_PUT" if pct < -NEAR_BAND else
             "NEAR_CALL" if pct > ATM_BAND and pct <= FAR_CALL else "FAR_CALL")
        if z == "ATM":
            if cd>0 and pd_>0 and min(cd,pd_)/(abs(cd)+abs(pd_)+1)>0.25: return "STRADDLE"
            if cd>0: return "BULLISH"
            if pd_>0: return "BEARISH"
            if cd<0 and pd_<0: return "UNWIND"
        elif z == "NEAR_PUT":
            if pd_>0: return "NEAR_BEARISH"
            if cd>0: return "BULLISH_BREAK"
        elif z == "DEEP_PUT":
            if pd_>0: return "HEDGE"
            if pd_<0: return "HEDGE_UNWIND"
            if cd>0: return "BULLISH_BREAK"
        elif z == "NEAR_CALL":
            if cd>0: return "BULLISH_BREAK"
            if pd_>0: return "NEAR_BEARISH"
        elif z == "FAR_CALL":
            if cd>0: return "COVERED_CALL"
        return "NEUTRAL"
    df["intent"]  = df.apply(_cls, axis=1)
    df["bar_col"] = df["intent"].map(_COLORS).fillna("#90A4AE")
    m_atm = abs(df["_pct"]) <= ATM_BAND
    m_np  = (df["_pct"] < -ATM_BAND) & (df["_pct"] >= -NEAR_BAND)
    m_dp  = df["_pct"] < -NEAR_BAND
    m_oc  = df["_pct"] > ATM_BAND
    atm_cd = float(df.loc[m_atm,"call_oi_change"].sum()); atm_pd = float(df.loc[m_atm,"put_oi_change"].sum())
    nput_pd = float(df.loc[m_np,"put_oi_change"].sum());  dput_pd = float(df.loc[m_dp,"put_oi_change"].sum())
    otm_cd  = float(df.loc[m_oc,"call_oi_change"].sum())
    score  = atm_cd*2.0 - atm_pd*2.0 - nput_pd*1.5 - dput_pd*0.3 + otm_cd*0.8
    total  = abs(atm_cd)+abs(atm_pd)+abs(nput_pd)+abs(dput_pd)+abs(otm_cd)
    thresh = max(total*0.25, 500)
    h_ratio = dput_pd / (abs(dput_pd)+abs(nput_pd)+abs(atm_pd)+1)
    if dput_pd>0 and h_ratio>0.5 and atm_cd>=0:
        sig,sc,desc = "HEDGED BULL","#1B5E20","Institutions hedging longs — call side accumulating"
    elif score>thresh:  sig,sc,desc = "BULLISH","#2E7D32","Net call build at ATM — buyers entering"
    elif score>0:       sig,sc,desc = "MILD BULL","#558B2F","Slight call bias — watch for follow-through"
    elif score<-thresh: sig,sc,desc = "BEARISH","#B71C1C","Net put build at ATM — directional shorts"
    elif score<0:       sig,sc,desc = "MILD BEAR","#BF360C","Slight put bias — monitor for acceleration"
    elif atm_cd>0 and atm_pd>0: sig,sc,desc = "STRADDLE","#6A1B9A","Both sides at ATM — vol/event play"
    elif total<200:     sig,sc,desc = "QUIET","#455A64","Low OI change — no conviction"
    else:               sig,sc,desc = "NEUTRAL","#455A64","Balanced activity — no edge"
    return df, sig, sc, desc, dict(atm_cd=atm_cd,atm_pd=atm_pd,nput_pd=nput_pd,
                                    dput_pd=dput_pd,otm_cd=otm_cd,score=score,hedge_pct=h_ratio*100)

# ---------------------------------------------------------------------------
def get_conn():
    return sqlite3.connect(DB_PATH)

def q(sql, params=None):
    """Quick query → DataFrame"""
    with get_conn() as c:
        return pd.read_sql(sql, c, params=params or [])

@st.cache_data(ttl=60, show_spinner=False)
def _cached_history(ticker: str, period: str = "5d", interval: str = "1d"):
    """yfinance history — cached 60 s to avoid hammering the API on every rerender."""
    try:
        return yf.Ticker(ticker).history(period=period, interval=interval)
    except Exception:
        return pd.DataFrame()

@st.cache_data(ttl=300, show_spinner=False)
def _db_spot(ticker: str) -> float:
    """Confirmed EOD close from stock_daily DB (MM-DD-YYYY sorted). Primary source for portfolio calcs."""
    try:
        with get_conn() as _c:
            row = pd.read_sql(
                "SELECT close FROM stock_daily WHERE ticker=? "
                "ORDER BY substr(trade_date,7,4)||substr(trade_date,1,2)||substr(trade_date,4,2) DESC LIMIT 1",
                _c, params=[ticker]
            )
        if not row.empty:
            return float(row["close"].iloc[0])
    except Exception:
        pass
    return 0.0

@st.cache_data(ttl=60, show_spinner=False)
def _cached_price(ticker: str) -> float:
    """Latest confirmed close — DB first (stock_daily), then yfinance history fallback."""
    db = _db_spot(ticker)
    if db > 0:
        return db
    try:
        h = _cached_history(ticker, period="5d")
        # Exclude today's bar (might be partial/pre-market): use last entry whose date < today
        import datetime as _dt
        today = _dt.date.today()
        for i in range(len(h) - 1, -1, -1):
            bar_date = h.index[i].date() if hasattr(h.index[i], 'date') else h.index[i]
            if bar_date < today:
                return float(h["Close"].iloc[i])
        return float(h["Close"].iloc[-1]) if len(h) >= 1 else 0.0
    except Exception:
        return 0.0

@st.cache_data(ttl=3600, show_spinner=False)
def _historical_vol(ticker: str, window: int = 30) -> float:
    """Annualised 30-day realised volatility from daily log-returns. Fallback 0.30."""
    try:
        h = _cached_history(ticker, period="90d")
        if len(h) < 10:
            return 0.30
        lr = np.log(h["Close"] / h["Close"].shift(1)).dropna()
        hv = float(lr.tail(window).std() * np.sqrt(252))
        return max(0.05, min(4.0, hv))
    except Exception:
        return 0.30

@st.cache_data(ttl=120, show_spinner=False)
def _fetch_option_mid(ticker: str, expiry_str: str, strike: float, opt_type: str
                      ) -> tuple:
    """Fetch real option mid-price and IV from yfinance options chain.
    Returns (mid_price, implied_vol) — both None when unavailable (market closed
    or expiry not listed)."""
    try:
        chain = yf.Ticker(ticker).option_chain(expiry_str)
        df = chain.calls if opt_type.upper() == "CALL" else chain.puts
        row = df[df["strike"] == float(strike)]
        if row.empty:
            near = df.iloc[(df["strike"] - strike).abs().argsort().iloc[:1]]
            if abs(float(near["strike"].iloc[0]) - strike) <= 2.5:
                row = near
        if row.empty:
            return None, None
        r = row.iloc[0]
        bid  = float(r.get("bid", 0) or 0)
        ask  = float(r.get("ask", 0) or 0)
        iv   = float(r.get("impliedVolatility", 0) or 0)
        # Only use real prices when market is live (bid+ask both non-zero)
        # Stale lastPrice during closed hours can be badly wrong
        if bid > 0 and ask > 0:
            mid = (bid + ask) / 2
        else:
            mid = None   # market closed → caller uses HV-based BS
        return mid, (iv if iv > 0.02 else None)
    except Exception:
        return None, None

@st.cache_data(ttl=300, show_spinner=False)
def _db_option_price(ticker: str, expiry_iso: str, strike: float, opt_type: str) -> float | None:
    """EOD option last-price from options_change DB (most recent trade_date_now).
    Converts expiry from YYYY-MM-DD to MM-DD-YYYY for DB lookup.
    Returns float price or None if not found."""
    try:
        parts = expiry_iso.split("-")
        db_exp = f"{parts[1]}-{parts[2]}-{parts[0]}"
        col = "lastPrice_Call_now" if opt_type.upper() == "CALL" else "lastPrice_Put_now"
        with get_conn() as _c:
            row = pd.read_sql(
                f"SELECT {col} AS price, trade_date_now FROM options_change "
                "WHERE ticker=? AND strike=? AND expiry_date=? "
                "ORDER BY substr(trade_date_now,7,4)||substr(trade_date_now,1,2)||substr(trade_date_now,4,2) DESC LIMIT 1",
                _c, params=[ticker, float(strike), db_exp]
            )
        if not row.empty and row["price"].iloc[0] is not None:
            return float(row["price"].iloc[0])
    except Exception:
        pass
    return None

@st.cache_data(ttl=60, show_spinner=False)
def _get_ah_price(ticker: str) -> dict:
    """Fetch after-hours / pre-market price via yfinance fast_info. Returns dict with
    spot_reg, spot_ah, ah_chg_pct, is_extended, label ('AH'/'PM'/'EOD')."""
    result = {"spot_reg": 0.0, "spot_ah": 0.0, "ah_chg_pct": 0.0, "is_extended": False, "label": "EOD"}
    try:
        fi = yf.Ticker(ticker).fast_info
        # Use DB stock_daily as primary for spot_reg (confirmed EOD close, no pre-market contamination)
        reg = _db_spot(ticker)
        if reg <= 0:
            reg = float(getattr(fi, "regular_market_previous_close", 0) or 0)
        result["spot_reg"] = reg

        post = 0.0; pre = 0.0
        try:
            _di = yf.Ticker(ticker).info
            post = float(_di.get("postMarketPrice") or 0)
            pre  = float(_di.get("preMarketPrice") or 0)
        except Exception:
            pass
        last = float(getattr(fi, "last_price", 0) or 0)

        if post > 0:
            result["spot_ah"]     = post
            result["is_extended"] = True
            result["label"]       = "AH"
        elif pre > 0:
            result["spot_ah"]     = pre
            result["is_extended"] = True
            result["label"]       = "PM"
        elif last > 0 and reg > 0 and abs(last - reg) / reg > 0.0005:
            result["spot_ah"]     = last
            result["is_extended"] = True
            result["label"]       = "Live"
        else:
            result["spot_ah"]     = reg

        if reg > 0:
            result["ah_chg_pct"] = (result["spot_ah"] - reg) / reg * 100
    except Exception:
        result["spot_ah"] = result["spot_reg"]
    return result

def _spot(ticker: str) -> float:
    """Return AH price when toggle is on, else EOD close. Use this everywhere instead of _cached_price()."""
    if st.session_state.get("use_ah", False):
        d = _get_ah_price(ticker)
        return d["spot_ah"] if d["spot_ah"] > 0 else d["spot_reg"]
    return _cached_price(ticker)

def _spot_label(ticker: str) -> str:
    """Short label showing EOD and AH price, e.g. 'EOD $248.50 → AH $251.20 (+1.1%)'."""
    d = _get_ah_price(ticker)
    reg = d["spot_reg"]
    ah  = d["spot_ah"]
    chg = d["ah_chg_pct"]
    lbl = d["label"]
    if d["is_extended"]:
        return f"EOD ${reg:.2f}  →  {lbl} <b>${ah:.2f}</b> ({chg:+.1f}%)"
    return f"EOD ${reg:.2f}"

@st.cache_data(ttl=30, show_spinner=False)
def _cached_trades(status: str = None):
    """Load trades from DB — cached 30 s."""
    sql = "SELECT * FROM trades" + (" WHERE status=?" if status else "")
    params = [status] if status else []
    return q(sql, params)

@st.cache_data(ttl=3600, show_spinner=False)
def _iv_rank_pct(ticker: str):
    """52-week IV rank and IV percentile using 20-day HV as IV proxy. Returns (rank, pct, cur_hv)."""
    try:
        h = yf.Ticker(ticker).history(period="1y")
        if len(h) < 60:
            return None, None, None
        lr = np.log(h["Close"] / h["Close"].shift(1)).dropna()
        hv = lr.rolling(20).std() * np.sqrt(252)
        cur = float(hv.iloc[-1]) if not np.isnan(hv.iloc[-1]) else None
        if cur is None:
            return None, None, None
        hv_clean = hv.dropna()
        mn, mx = float(hv_clean.min()), float(hv_clean.max())
        rank = round((cur - mn) / (mx - mn) * 100, 1) if mx > mn else 50.0
        pct  = round(float((hv_clean <= cur).sum() / len(hv_clean) * 100), 1)
        return rank, pct, round(cur * 100, 1)
    except Exception:
        return None, None, None

@st.cache_data(ttl=3600, show_spinner=False)
def _days_to_earnings(ticker: str):
    """Days to next earnings. Returns int or None."""
    try:
        cal = yf.Ticker(ticker).calendar
        if cal is None:
            return None
        if isinstance(cal, dict):
            ed = cal.get("Earnings Date")
            if ed:
                ed = pd.to_datetime(ed[0] if isinstance(ed, list) else ed)
                days = (ed.date() - datetime.now().date()).days
                return days if days >= 0 else None
        elif hasattr(cal, "loc") and "Earnings Date" in cal.index:
            ed = pd.to_datetime(cal.loc["Earnings Date"].iloc[0])
            days = (ed.date() - datetime.now().date()).days
            return days if days >= 0 else None
    except Exception:
        pass
    return None

@st.cache_data(ttl=14400, show_spinner=False)
def _get_short_data_dash(ticker: str) -> dict:
    """Fetch short interest & float from yfinance info. Cached 4h."""
    empty = {"float_shares": None, "shares_short": None, "short_pct_float": None,
             "short_ratio": None, "shares_short_prior": None,
             "shares_outstanding": None, "squeeze_score": 0, "squeeze_label": "N/A"}
    try:
        info = yf.Ticker(ticker).info
        float_s  = info.get("floatShares")
        ss       = info.get("sharesShort")
        spf      = info.get("shortPercentOfFloat")
        sr       = info.get("shortRatio")
        ss_prior = info.get("sharesShortPriorMonth")
        sout     = info.get("sharesOutstanding")
        if spf and spf < 1:
            spf = spf * 100
        if spf is None and float_s and ss:
            spf = ss / float_s * 100
        score = 0
        if spf:
            if spf >= 30: score += 4
            elif spf >= 20: score += 3
            elif spf >= 10: score += 2
            elif spf >= 5: score += 1
        if sr:
            if sr >= 10: score += 3
            elif sr >= 5: score += 2
            elif sr >= 3: score += 1
        if ss and ss_prior and ss > ss_prior * 1.10: score += 2
        elif ss and ss_prior and ss < ss_prior * 0.90: score -= 1
        score = max(0, min(10, score))
        return {
            "float_shares": float_s, "shares_short": ss,
            "short_pct_float": spf, "short_ratio": sr,
            "shares_short_prior": ss_prior, "shares_outstanding": sout,
            "squeeze_score": score,
            "squeeze_label": "HIGH SQUEEZE RISK" if score >= 7 else ("MODERATE" if score >= 4 else "LOW"),
        }
    except Exception:
        return empty


def _roll_suggestion(leg: dict, spot: float) -> str:
    """Generate a specific roll recommendation for a losing/expiring leg."""
    dte = leg.get("DTE", 99)
    pnl_pct = leg.get("PnL%", 0)
    opt_type = leg.get("Type", "CALL")
    strike = leg.get("Strike", 0)
    qty = leg.get("Qty", 1)
    side = "BUY" if qty > 0 else "SELL"

    if dte > 21 and pnl_pct > -30:
        return ""  # No roll needed yet

    today = datetime.now().date()
    # Target roll: next monthly expiry 30-45 days out
    roll_dte = 35
    target_month = today + timedelta(days=roll_dte)
    # Find 3rd Friday of that month
    first_day = target_month.replace(day=1)
    fridays = [first_day + timedelta(days=d) for d in range(31) if (first_day + timedelta(days=d)).weekday() == 4]
    roll_exp = fridays[2].strftime("%Y-%m-%d") if len(fridays) >= 3 else target_month.strftime("%Y-%m-%d")

    # Suggest roll strike: ATM for losing positions, same strike for mild loss
    if pnl_pct < -40:
        roll_strike = round(spot / 5) * 5  # Round to nearest $5 = ATM
        reason = "roll to ATM (stop digging deeper)"
    elif pnl_pct < -20:
        roll_strike = strike
        reason = "roll same strike, extend time"
    else:
        roll_strike = strike
        reason = "roll before theta accelerates"

    action = "ROLL" if side == "BUY" else "ROLL SHORT"
    return (f"🔄 {action} suggestion: Close {side} {opt_type} ${strike:.0f} exp now → "
            f"Open {side} {opt_type} ${roll_strike:.0f} exp ~{roll_exp} ({reason})")

# ---------------------------------------------------------------------------
# MARKET SNAPSHOTS TABLE
# ---------------------------------------------------------------------------
def _init_market_snapshots_table():
    with get_conn() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS market_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                name TEXT NOT NULL,
                symbol TEXT NOT NULL,
                price REAL,
                change REAL,
                pct REAL
            )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS idx_ms_symbol ON market_snapshots(symbol)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_ms_ts ON market_snapshots(timestamp)")
_init_market_snapshots_table()

def _save_market_snapshot(df):
    """Persist market snapshot rows to DB (dedupes by minute)."""
    if df.empty:
        return
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    with get_conn() as c:
        # Skip if we already saved this minute
        existing = c.execute("SELECT 1 FROM market_snapshots WHERE timestamp=? LIMIT 1", (ts,)).fetchone()
        if existing:
            return
        rows = [(ts, r["Name"], r["Symbol"], float(r["Price"]), float(r["Change"]), float(r["Pct"]))
                for _, r in df.iterrows()]
        c.executemany("INSERT INTO market_snapshots(timestamp,name,symbol,price,change,pct) VALUES(?,?,?,?,?,?)", rows)

def load_market_history(symbol, limit=500):
    """Load stored price history for a symbol from market_snapshots."""
    return q("SELECT timestamp, price, change, pct FROM market_snapshots WHERE symbol=? ORDER BY timestamp DESC LIMIT ?",
             [symbol, limit])

# ---------------------------------------------------------------------------
# PAGE CONFIG
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="RUDRARJUN Analytics",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ---------------------------------------------------------------------------
# DARK THEME CSS  (Bloomberg-style)
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# THEME SYSTEM — dark "fintech" (default) + light minimal, switchable at runtime
# (toggle lives at the top of the sidebar; selection persists via session_state)
# ---------------------------------------------------------------------------
_FONT_IMPORT = "@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&display=swap');"

_CSS_DARK = """
<style>
__FONT__
:root{
  --panel:rgba(20,28,46,0.72); --panel-solid:#141c2e; --border:rgba(120,140,180,0.18);
  --text:#e6edf3; --muted:#8b9bb4; --accent:#3d8bff;
  --green:#00e676; --red:#ff5c6c; --amber:#ffb74d; --purple:#b388ff;
}
html, body, [data-testid="stAppViewContainer"]{ font-family:'Inter',sans-serif; color:var(--text); }
[data-testid="stAppViewContainer"]{
  background:radial-gradient(900px 480px at 8% -10%, rgba(61,139,255,.22), transparent 60%),
             radial-gradient(820px 520px at 102% 2%, rgba(124,77,255,.18), transparent 55%),
             radial-gradient(700px 600px at 50% 120%, rgba(0,230,118,.07), transparent 60%),
             linear-gradient(180deg,#0a0e17,#0b1120);
}
[data-testid="stHeader"]{ background:transparent; }
.block-container{ padding-top:2.2rem; max-width:1400px; }
section[data-testid="stSidebar"]{ background:linear-gradient(180deg,#0c1226,#0a0e17); border-right:1px solid var(--border); }
section[data-testid="stSidebar"] *{ color:var(--text)!important; }
section[data-testid="stSidebar"] .stRadio label{ font-size:.92rem; padding:8px 10px; border-radius:8px; transition:all .15s; }
section[data-testid="stSidebar"] .stRadio label:hover{ background:rgba(61,139,255,.14); }
section[data-testid="stSidebar"] .stRadio label:has(input:checked){
  background:linear-gradient(90deg,rgba(61,139,255,.32),rgba(124,77,255,.10) 70%,transparent);
  box-shadow:inset 3px 0 0 var(--accent); font-weight:700;
}
h1,h2{ color:#8ab4ff!important; font-weight:800!important; letter-spacing:.2px; }
h3,h4{ color:#dbe6ff!important; font-weight:700; letter-spacing:.2px; }
h1 a,h2 a,h3 a,h4 a{ display:none!important; }
[data-testid="stMetric"]{
  background:linear-gradient(135deg,rgba(61,139,255,.10),var(--panel)); backdrop-filter:blur(10px); -webkit-backdrop-filter:blur(10px);
  border:1px solid var(--border); border-left:3px solid var(--accent); border-radius:14px; padding:16px 18px;
  box-shadow:0 8px 24px rgba(0,0,0,.35), 0 0 0 1px rgba(61,139,255,.05), inset 0 1px 0 rgba(255,255,255,.05);
  transition:transform .15s, box-shadow .15s;
}
[data-testid="stMetric"]:hover{ transform:translateY(-2px); box-shadow:0 12px 30px rgba(61,139,255,.22); }
[data-testid="stMetricValue"]{ font-size:1.55rem!important; font-weight:800; color:#fff!important; }
[data-testid="stMetricLabel"]{ font-size:.72rem!important; color:#9fb3d4; text-transform:uppercase; letter-spacing:.8px; }
[data-testid="stMetricDelta"]{ font-size:.85rem!important; font-weight:700; }
button[data-baseweb="tab"]{ font-size:.85rem!important; font-weight:600; color:var(--muted)!important; padding:6px 16px; }
button[data-baseweb="tab"][aria-selected="true"]{ color:#fff!important;
  background:linear-gradient(135deg,rgba(61,139,255,.26),rgba(124,77,255,.16)); border-radius:10px 10px 0 0; }
[data-baseweb="tab-highlight"]{ background:linear-gradient(90deg,var(--accent),#9c7bff)!important; height:3px!important; }
.stDataFrame,[data-testid="stDataFrame"]{ border-radius:12px; overflow:hidden; border:1px solid var(--border); }
.stButton>button,[data-testid="stBaseButton-secondary"]{
  background:linear-gradient(135deg,#3d8bff,#7c4dff); color:#fff!important;
  border:none; border-radius:10px; font-weight:700; transition:all .15s;
  box-shadow:0 4px 14px rgba(61,139,255,.32);
}
.stButton>button:hover{ filter:brightness(1.08); transform:translateY(-1px); box-shadow:0 7px 22px rgba(124,77,255,.45); }
[data-testid="stSelectbox"] label,[data-testid="stMultiSelect"] label,
[data-testid="stTextInput"] label,[data-testid="stNumberInput"] label{
  color:var(--muted)!important; font-size:.82rem!important; text-transform:uppercase; letter-spacing:.5px;
}
[data-testid="stExpander"]{ background:var(--panel); border:1px solid var(--border); border-radius:12px; }
[data-testid="stExpander"] summary{ color:var(--text)!important; }
[data-testid="stExpander"] *{ color:var(--text); }
/* ── make every native surface dark so text is always readable ── */
[data-testid="stMarkdownContainer"], .stMarkdown, p, li, label,
.stRadio, .stCheckbox, [data-testid="stWidgetLabel"]{ color:var(--text); }
[data-testid="stCaptionContainer"], [data-testid="stCaptionContainer"] *{ color:var(--muted)!important; }
/* text / number inputs */
[data-baseweb="input"], [data-baseweb="base-input"], [data-baseweb="textarea"]{ background:#0f1626!important; }
[data-baseweb="input"] input, [data-baseweb="base-input"] input, textarea,
.stTextInput input, .stNumberInput input, [data-testid="stDateInput"] input{
  background:#0f1626!important; color:var(--text)!important;
}
input::placeholder, textarea::placeholder{ color:#62748f!important; }
/* selectbox / multiselect control + its text */
[data-testid="stSelectbox"] [data-baseweb="select"] > div,
[data-testid="stMultiSelect"] [data-baseweb="select"] > div{
  background:#0f1626!important; border-color:var(--border)!important;
}
[data-baseweb="select"] *{ color:var(--text)!important; }
/* dropdown / autocomplete popover menus (rendered at body root) */
[data-baseweb="popover"] [role="listbox"], [data-baseweb="menu"],
[data-baseweb="popover"] ul, [data-baseweb="popover"] > div{ background:#0f1626!important; }
[role="option"]{ background:#0f1626!important; color:var(--text)!important; }
[role="option"]:hover, [role="option"][aria-selected="true"]{ background:rgba(61,139,255,.20)!important; }
/* alerts (st.info / success / warning / error) */
[data-testid="stAlert"]{ border-radius:12px; background:var(--panel-solid)!important; border:1px solid var(--border); }
[data-testid="stAlert"] *{ color:var(--text)!important; }
/* sliders, code, static tables */
code, kbd{ background:#0f1626!important; color:#9ad1ff!important; }
pre{ background:#0f1626!important; }
[data-testid="stTable"] td, [data-testid="stTable"] th,
table td, table th{ color:var(--text)!important; border-color:var(--border)!important; }
/* tabs panel + toggle */
[data-baseweb="tab-panel"]{ background:transparent; }
.section-header{ font-size:1.05rem; font-weight:700; color:var(--accent); border-bottom:1px solid var(--border); padding-bottom:6px; margin:20px 0 12px; letter-spacing:.3px; }
.card{ background:var(--panel); border:1px solid var(--border); border-radius:14px; padding:16px; margin-bottom:12px; backdrop-filter:blur(10px); }
.news-card{ background:var(--panel-solid); border-left:3px solid var(--accent); padding:10px 14px; margin:4px 0; border-radius:0 10px 10px 0; }
.news-card.bull{ border-left-color:var(--green); } .news-card.bear{ border-left-color:var(--red); }
.alert-bar{ background:linear-gradient(90deg,rgba(255,183,77,.12),transparent); border:1px solid var(--amber); border-radius:10px; padding:10px 16px; margin:8px 0; animation:pulse 2s infinite; }
.trade-idea{ background:linear-gradient(135deg,rgba(0,230,118,.10),rgba(0,230,118,.02)); border:1px solid rgba(0,230,118,.40); border-radius:10px; padding:10px 14px; margin:4px 0; }
.trade-idea.bearish{ background:linear-gradient(135deg,rgba(255,92,108,.10),rgba(255,92,108,.02)); border-color:rgba(255,92,108,.40); }
@keyframes pulse{ 0%,100%{opacity:1} 50%{opacity:.85} }
.badge-bull{ background:var(--green); color:#06210f; padding:3px 12px; border-radius:20px; font-weight:700; font-size:.78rem; }
.badge-bear{ background:var(--red); color:#2a0a0e; padding:3px 12px; border-radius:20px; font-weight:700; font-size:.78rem; }
.badge-neutral{ background:#54637a; color:#fff; padding:3px 12px; border-radius:20px; font-weight:600; font-size:.78rem; }
.badge-warn{ background:var(--amber); color:#231600; padding:3px 12px; border-radius:20px; font-weight:700; font-size:.78rem; }
.badge-volatile{ background:var(--purple); color:#1a0a2e; padding:3px 12px; border-radius:20px; font-weight:700; font-size:.78rem; }
.prop-card{ background:var(--panel); border:1px solid var(--border); border-radius:14px; padding:18px; margin:8px 0; backdrop-filter:blur(10px); }
.prop-card h4{ color:var(--accent); margin:0 0 8px; }
.analysis-finding{ background:var(--panel-solid); border-left:3px solid var(--accent); padding:10px 14px; margin:6px 0; border-radius:0 10px 10px 0; }
.analysis-finding.bearish{ border-left-color:var(--red); } .analysis-finding.bullish{ border-left-color:var(--green); } .analysis-finding.volatile{ border-left-color:var(--purple); }
</style>
""".replace("__FONT__", _FONT_IMPORT)

_CSS_LIGHT = """
<style>
__FONT__
:root{
  --panel:#ffffff; --panel-solid:#ffffff; --border:#e6e8ee;
  --text:#1a2332; --muted:#64748b; --accent:#4f46e5;
  --green:#16a34a; --red:#dc2626; --amber:#d97706; --purple:#7c3aed;
}
html, body, [data-testid="stAppViewContainer"]{ font-family:'Inter',sans-serif; color:var(--text); }
[data-testid="stAppViewContainer"]{
  background:radial-gradient(900px 480px at 8% -10%, rgba(79,70,229,.08), transparent 60%),
             radial-gradient(820px 520px at 102% 2%, rgba(124,58,237,.07), transparent 55%),
             #f4f5f8;
}
[data-testid="stHeader"]{ background:transparent; }
.block-container{ padding-top:2.2rem; max-width:1400px; }
section[data-testid="stSidebar"]{ background:linear-gradient(180deg,#ffffff,#f3f1fe); border-right:1px solid var(--border); }
section[data-testid="stSidebar"] *{ color:var(--text)!important; }
section[data-testid="stSidebar"] .stRadio label{ font-size:.92rem; padding:8px 10px; border-radius:8px; transition:all .15s; }
section[data-testid="stSidebar"] .stRadio label:hover{ background:rgba(79,70,229,.10); }
section[data-testid="stSidebar"] .stRadio label:has(input:checked){
  background:linear-gradient(90deg,rgba(79,70,229,.16),transparent); box-shadow:inset 3px 0 0 var(--accent); font-weight:700;
}
h1,h2{ color:#4f46e5!important; font-weight:800!important; letter-spacing:.2px; }
h3,h4{ color:#0f172a!important; font-weight:700; letter-spacing:.2px; }
h1 a,h2 a,h3 a,h4 a{ display:none!important; }
[data-testid="stMetric"]{ background:linear-gradient(135deg,rgba(79,70,229,.05),#fff); border:1px solid var(--border); border-left:3px solid var(--accent); border-radius:14px; padding:16px 18px; box-shadow:0 1px 3px rgba(16,24,40,.06); transition:transform .15s, box-shadow .15s; }
[data-testid="stMetric"]:hover{ transform:translateY(-2px); box-shadow:0 10px 24px rgba(79,70,229,.15); }
[data-testid="stMetricValue"]{ font-size:1.55rem!important; font-weight:800; color:#0f172a!important; }
[data-testid="stMetricLabel"]{ font-size:.72rem!important; color:var(--muted); text-transform:uppercase; letter-spacing:.8px; }
[data-testid="stMetricDelta"]{ font-size:.85rem!important; font-weight:700; }
button[data-baseweb="tab"]{ font-size:.85rem!important; font-weight:600; color:var(--muted)!important; padding:6px 16px; }
button[data-baseweb="tab"][aria-selected="true"]{ color:var(--accent)!important; background:rgba(79,70,229,.08); border-radius:10px 10px 0 0; }
[data-baseweb="tab-highlight"]{ background:linear-gradient(90deg,var(--accent),#7c3aed)!important; height:3px!important; }
.stDataFrame,[data-testid="stDataFrame"]{ border-radius:12px; overflow:hidden; border:1px solid var(--border); }
.stButton>button{ background:linear-gradient(135deg,#4f46e5,#7c3aed); color:#fff!important; border:none; border-radius:10px; font-weight:700; transition:all .15s; box-shadow:0 3px 10px rgba(79,70,229,.25); }
.stButton>button:hover{ filter:brightness(1.06); transform:translateY(-1px); box-shadow:0 6px 18px rgba(124,58,237,.35); }
[data-testid="stSelectbox"] label,[data-testid="stMultiSelect"] label,
[data-testid="stTextInput"] label,[data-testid="stNumberInput"] label{
  color:var(--muted)!important; font-size:.82rem!important; text-transform:uppercase; letter-spacing:.5px;
}
[data-testid="stExpander"]{ background:#fff; border:1px solid var(--border); border-radius:12px; }
[data-testid="stExpander"] summary{ color:var(--text)!important; }
[data-testid="stExpander"] *{ color:var(--text); }
/* ── force light surfaces (overrides the dark config base when toggled) ── */
[data-testid="stMarkdownContainer"], .stMarkdown, p, li, label,
.stRadio, .stCheckbox, [data-testid="stWidgetLabel"]{ color:var(--text); }
[data-testid="stCaptionContainer"], [data-testid="stCaptionContainer"] *{ color:var(--muted)!important; }
[data-baseweb="input"], [data-baseweb="base-input"], [data-baseweb="textarea"]{ background:#fff!important; }
[data-baseweb="input"] input, [data-baseweb="base-input"] input, textarea,
.stTextInput input, .stNumberInput input, [data-testid="stDateInput"] input{ background:#fff!important; color:var(--text)!important; }
input::placeholder, textarea::placeholder{ color:#94a3b8!important; }
[data-testid="stSelectbox"] [data-baseweb="select"] > div,
[data-testid="stMultiSelect"] [data-baseweb="select"] > div{ background:#fff!important; border-color:var(--border)!important; }
[data-baseweb="select"] *{ color:var(--text)!important; }
[data-baseweb="popover"] [role="listbox"], [data-baseweb="menu"],
[data-baseweb="popover"] ul, [data-baseweb="popover"] > div{ background:#fff!important; }
[role="option"]{ background:#fff!important; color:var(--text)!important; }
[role="option"]:hover, [role="option"][aria-selected="true"]{ background:rgba(79,70,229,.10)!important; }
[data-testid="stAlert"]{ border-radius:12px; background:#fff!important; border:1px solid var(--border); }
[data-testid="stAlert"] *{ color:var(--text)!important; }
code, kbd{ background:#eef0f6!important; color:#4f46e5!important; }
pre{ background:#f1f3f8!important; }
[data-testid="stTable"] td, [data-testid="stTable"] th,
table td, table th{ color:var(--text)!important; border-color:var(--border)!important; }
[data-baseweb="tab-panel"]{ background:transparent; }
.section-header{ font-size:1.05rem; font-weight:700; color:var(--accent); border-bottom:2px solid var(--accent); padding-bottom:6px; margin:20px 0 12px; }
.card{ background:#fff; border:1px solid var(--border); border-radius:14px; padding:16px; margin-bottom:12px; box-shadow:0 1px 3px rgba(16,24,40,.05); }
.news-card{ background:#fff; border-left:3px solid var(--accent); padding:10px 14px; margin:4px 0; border-radius:0 10px 10px 0; box-shadow:0 1px 2px rgba(16,24,40,.04); }
.news-card.bull{ border-left-color:var(--green); } .news-card.bear{ border-left-color:var(--red); }
.alert-bar{ background:#fffbeb; border:1px solid var(--amber); border-radius:10px; padding:10px 16px; margin:8px 0; animation:pulse 2s infinite; }
.trade-idea{ background:#f0fdf4; border:1px solid #86efac; border-radius:10px; padding:10px 14px; margin:4px 0; }
.trade-idea.bearish{ background:#fef2f2; border-color:#fca5a5; }
@keyframes pulse{ 0%,100%{opacity:1} 50%{opacity:.85} }
.badge-bull{ background:var(--green); color:#fff; padding:3px 12px; border-radius:20px; font-weight:700; font-size:.78rem; }
.badge-bear{ background:var(--red); color:#fff; padding:3px 12px; border-radius:20px; font-weight:700; font-size:.78rem; }
.badge-neutral{ background:#94a3b8; color:#fff; padding:3px 12px; border-radius:20px; font-weight:600; font-size:.78rem; }
.badge-warn{ background:var(--amber); color:#fff; padding:3px 12px; border-radius:20px; font-weight:700; font-size:.78rem; }
.badge-volatile{ background:var(--purple); color:#fff; padding:3px 12px; border-radius:20px; font-weight:700; font-size:.78rem; }
.prop-card{ background:#fff; border:1px solid var(--border); border-radius:14px; padding:18px; margin:8px 0; box-shadow:0 1px 3px rgba(16,24,40,.05); }
.prop-card h4{ color:var(--accent); margin:0 0 8px; }
.analysis-finding{ background:#fff; border-left:3px solid var(--accent); padding:10px 14px; margin:6px 0; border-radius:0 10px 10px 0; }
.analysis-finding.bearish{ border-left-color:var(--red); } .analysis-finding.bullish{ border-left-color:var(--green); } .analysis-finding.volatile{ border-left-color:var(--purple); }
</style>
""".replace("__FONT__", _FONT_IMPORT)

# Default to dark fintech; the sidebar toggle (key="ui_theme") flips it on rerun.
st.session_state.setdefault("ui_theme", "🌙 Dark")
st.markdown(_CSS_DARK if "Dark" in st.session_state["ui_theme"] else _CSS_LIGHT,
            unsafe_allow_html=True)

# ===================================================================
# ──  GREEKS  (Black-Scholes)
# ===================================================================
def bs_greeks(S, K, T, r, sigma, opt="call"):
    """Black-Scholes option price + Greeks."""
    if T <= 0:
        intr = max(0, S - K) if opt == "call" else max(0, K - S)
        return dict(delta=1.0 if (opt == "call" and S > K) else (-1.0 if opt == "put" and S < K else 0.0),
                    gamma=0, theta=0, vega=0, rho=0, price=intr)
    d1 = (np.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * np.sqrt(T))
    d2 = d1 - sigma * np.sqrt(T)
    nd1, nd2 = norm.cdf(d1), norm.cdf(d2)
    pdf_d1 = norm.pdf(d1)
    if opt == "call":
        delta = nd1
        price = S * nd1 - K * np.exp(-r * T) * nd2
        theta = (-S * pdf_d1 * sigma / (2 * np.sqrt(T)) - r * K * np.exp(-r * T) * nd2) / 365
        rho = K * T * np.exp(-r * T) * nd2 / 100
    else:
        delta = -norm.cdf(-d1)
        price = K * np.exp(-r * T) * norm.cdf(-d2) - S * norm.cdf(-d1)
        theta = (-S * pdf_d1 * sigma / (2 * np.sqrt(T)) + r * K * np.exp(-r * T) * norm.cdf(-d2)) / 365
        rho = -K * T * np.exp(-r * T) * norm.cdf(-d2) / 100
    gamma = pdf_d1 / (S * sigma * np.sqrt(T))
    vega = S * pdf_d1 * np.sqrt(T) / 100
    return dict(delta=delta, gamma=gamma, theta=theta, vega=vega, rho=rho, price=price)


def _implied_vol(market_price: float, S: float, K: float, T: float, r: float, opt: str,
                 fallback: float = 0.30) -> float:
    """Back-solve implied volatility via bisection. Falls back to `fallback` if price is zero or T<=0."""
    if market_price <= 1e-6 or T <= 0 or S <= 0 or K <= 0:
        return fallback
    intrinsic = max(0, S - K) if opt == "call" else max(0, K - S)
    if market_price <= intrinsic + 1e-6:
        return fallback
    lo, hi = 0.01, 6.0
    for _ in range(80):
        mid = (lo + hi) / 2
        try:
            p = bs_greeks(S, K, T, r, mid, opt)["price"]
        except Exception:
            return fallback
        if abs(p - market_price) < 0.0005:
            return mid
        if p < market_price:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def _live_iv(eod_iv: float, spot_eod: float, spot_live: float, opt_type: str) -> float:
    """Adjust implied volatility for a spot-price move using simplified skew model.
    Puts: negative spot-vol correlation (stock down → IV up).
    Calls: mild positive correlation.
    """
    if spot_eod <= 0:
        return eod_iv
    move = (spot_live - spot_eod) / spot_eod   # e.g. -0.02 for -2% drop
    if opt_type == "put":
        # Roughly: each 1% drop adds ~1.5% to IV (put skew)
        adj = eod_iv * (1.0 - 1.5 * move)
    else:
        # Calls: each 1% rise adds ~0.5% to IV
        adj = eod_iv * (1.0 + 0.5 * move)
    return max(0.05, min(5.0, adj))


# ===================================================================
# ──  MARKET SNAPSHOT (Yahoo Finance)
# ===================================================================
GLOBAL_SYMBOLS = {
    "S&P 500": "^GSPC", "Dow Jones": "^DJI", "Nasdaq": "^IXIC",
    "Russell 2000": "^RUT", "VIX": "^VIX",
    "S&P 500 Futures": "ES=F", "Nasdaq 100 Futures": "NQ=F",
    "Dow Jones Futures": "YM=F", "Russell 2000 Futures": "RTY=F",
    "Nifty 50": "^NSEI", "Sensex": "^BSESN",
    "Gold": "GC=F", "WTI Oil": "CL=F", "Brent": "BZ=F",
    "Silver": "SI=F", "Nat Gas": "NG=F", "Copper": "HG=F",
    "EUR/USD": "EURUSD=X", "USD/JPY": "JPY=X", "GBP/USD": "GBPUSD=X",
    "Dollar Index": "DX-Y.NYB", "USD/INR": "INR=X",
    "Bitcoin": "BTC-USD", "Ethereum": "ETH-USD",
    "10Y Yield": "^TNX", "30Y Yield": "^TYX",
}

# Display icons for each instrument
SYMBOL_ICONS = {
    "S&P 500": "📈", "Dow Jones": "🏛️", "Nasdaq": "💻", "Russell 2000": "📊", "VIX": "⚡",
    "S&P 500 Futures": "📈", "Nasdaq 100 Futures": "💻",
    "Dow Jones Futures": "🏛️", "Russell 2000 Futures": "📊",
    "Nifty 50": "🇮🇳", "Sensex": "🇮🇳",
    "Gold": "🥇", "WTI Oil": "🛢️", "Brent": "🛢️",
    "Silver": "🥈", "Nat Gas": "🔥", "Copper": "🔶",
    "EUR/USD": "🇪🇺", "USD/JPY": "🇯🇵", "GBP/USD": "🇬🇧",
    "Dollar Index": "💵", "USD/INR": "🇮🇳",
    "Bitcoin": "₿", "Ethereum": "⟠",
    "10Y Yield": "📋", "30Y Yield": "📋",
}

@st.cache_data(ttl=60)
def fetch_market_snapshot():
    rows = []
    tickers_str = " ".join(GLOBAL_SYMBOLS.values())
    try:
        data = yf.download(tickers_str, period="5d", group_by="ticker", progress=False, threads=True)
    except Exception:
        data = pd.DataFrame()
    for name, sym in GLOBAL_SYMBOLS.items():
        try:
            hist = pd.DataFrame()
            # Try bulk data first
            if not data.empty and sym in data.columns.get_level_values(0):
                sub = data[sym]["Close"].dropna()
                if len(sub) >= 2:
                    hist = data[sym].loc[sub.index]
            # Fallback to individual fetch
            if hist.empty or len(hist) < 2:
                t = yf.Ticker(sym)
                hist = t.history(period="5d")
            if len(hist) >= 2:
                close_col = hist["Close"].dropna()
                if len(close_col) < 2:
                    continue
                cur = float(close_col.iloc[-1])
                prev = float(close_col.iloc[-2])
                chg = cur - prev
                pct = chg / prev * 100 if prev else 0
                rows.append(dict(Name=name, Symbol=sym, Price=cur, Change=chg, Pct=pct))
        except Exception:
            pass
    result = pd.DataFrame(rows)
    # Persist to DB for historical charts
    if not result.empty:
        _save_market_snapshot(result)
    return result


# ===================================================================
# ──  OI  HELPERS
# ===================================================================
def _sort_dates_chrono(dates_list, descending=True):
    """Sort MM-DD-YYYY date strings chronologically."""
    parsed = []
    for d in dates_list:
        try:
            parsed.append((d, pd.to_datetime(d, format="%m-%d-%Y")))
        except Exception:
            pass
    parsed.sort(key=lambda x: x[1], reverse=descending)
    return [p[0] for p in parsed]


@st.cache_data(ttl=60, show_spinner=False)
def available_trade_dates():
    raw = q("SELECT DISTINCT trade_date_now FROM options_change")["trade_date_now"].tolist()
    return _sort_dates_chrono(raw, descending=True)


@st.cache_data(ttl=60, show_spinner=False)
def load_oi_for_date(td):
    return q("SELECT * FROM options_change WHERE trade_date_now=?", [td])


@st.cache_data(ttl=120, show_spinner=False)
def load_stock_daily(ticker):
    df = q("SELECT * FROM stock_daily WHERE ticker=?", [ticker])
    if not df.empty:
        df["_dt"] = pd.to_datetime(df["trade_date"], format="%m-%d-%Y", errors="coerce")
        df = df.sort_values("_dt", ascending=False).drop(columns=["_dt"])
    return df


def get_next_day_stock_move(ticker, trade_date_mmddyyyy):
    """Get the stock price change on the next trading day after trade_date."""
    sd = load_stock_daily(ticker)
    if sd.empty:
        return None, None, None
    sd = sd.copy()
    sd["_dt"] = pd.to_datetime(sd["trade_date"], format="%m-%d-%Y", errors="coerce")
    sd = sd.sort_values("_dt").reset_index(drop=True)
    ref_dt = pd.to_datetime(trade_date_mmddyyyy, format="%m-%d-%Y")
    ref_row = sd[sd["_dt"] == ref_dt]
    if ref_row.empty:
        return None, None, None
    idx = ref_row.index[0]
    if idx + 1 >= len(sd):
        return float(ref_row["close"].iloc[0]), None, None
    next_row = sd.iloc[idx + 1]
    close_today = float(ref_row["close"].iloc[0])
    close_next = float(next_row["close"])
    pct = (close_next - close_today) / close_today * 100 if close_today else 0
    return close_today, close_next, pct

def oi_anomalies(df, z_thresh=2.0):
    """Aggregate OI changes per ticker and flag z-score anomalies."""
    agg = df.groupby("ticker").agg(
        call_oi_chg=("change_OI_Call", "sum"),
        put_oi_chg=("change_OI_Put", "sum"),
        call_vol_chg=("change_vol_Call", "sum"),
        put_vol_chg=("change_vol_Put", "sum"),
        call_oi=("openInt_Call_now", "sum"),
        put_oi=("openInt_Put_now", "sum"),
    ).reset_index()
    for c in ["call_oi_chg", "put_oi_chg", "call_vol_chg", "put_vol_chg"]:
        mu, sd = agg[c].mean(), agg[c].std()
        agg[f"{c}_z"] = (agg[c] - mu) / sd if sd > 0 else 0
    z_cols = [c for c in agg.columns if c.endswith("_z")]
    agg["max_z"] = agg[z_cols].abs().max(axis=1)
    agg["pcr"] = np.where(agg["call_oi"] > 0, agg["put_oi"] / agg["call_oi"], 0)
    return agg.sort_values("max_z", ascending=False)


def infer_pressure(row):
    """Infer buy/sell/hedge for a single options_change row."""
    c_sig, c_conf, p_sig, p_conf, reasons = "NEUTRAL", 0, "NEUTRAL", 0, []

    # CALL side
    oi_c = row.get("change_OI_Call", 0) or 0
    vol_c = row.get("vol_Call_now", 0) or 0
    c_open = row.get("call_open_now", None)
    c_close = row.get("call_close_now", None)
    c_high = row.get("call_high_now", None)
    c_low = row.get("call_low_now", None)

    if oi_c > 100:
        vol_r = vol_c / abs(oi_c) if oi_c else 0
        if c_close is not None and c_open is not None and c_close > 0:
            up = c_close > c_open
            rng = (c_high or c_close) - (c_low or c_open)
            pos = ((c_close - (c_low or c_open)) / rng) if rng > 0 else 0.5
            if up and vol_r >= 0.8 and pos > 0.6:
                c_sig, c_conf = "STRONG BUY", 90
            elif up and vol_r >= 0.5:
                c_sig, c_conf = "BUY", 70
            elif not up and pos < 0.4:
                c_sig, c_conf = "WRITING", 65
            else:
                c_sig, c_conf = "MIXED", 40
        else:
            c_sig, c_conf = "BUY" if vol_r > 0.5 else "MIXED", 55
        reasons.append(f"Call OI +{oi_c:,.0f}")
    elif oi_c < -100:
        c_sig, c_conf = "CLOSING", 80
        reasons.append(f"Call OI {oi_c:,.0f} closing")

    # PUT side
    oi_p = row.get("change_OI_Put", 0) or 0
    vol_p = row.get("vol_Put_now", 0) or 0
    p_open = row.get("put_open_now", None)
    p_close = row.get("put_close_now", None)
    p_high = row.get("put_high_now", None)
    p_low = row.get("put_low_now", None)

    if oi_p > 100:
        vol_r = vol_p / abs(oi_p) if oi_p else 0
        if p_close is not None and p_open is not None and p_close > 0:
            up = p_close > p_open
            rng = (p_high or p_close) - (p_low or p_open)
            pos = ((p_close - (p_low or p_open)) / rng) if rng > 0 else 0.5
            if up and vol_r >= 0.8 and pos > 0.6:
                p_sig, p_conf = "STRONG BUY", 90
            elif up and vol_r >= 0.5:
                p_sig, p_conf = "BUY", 70
            elif not up and pos < 0.4:
                p_sig, p_conf = "WRITING", 65
            else:
                p_sig, p_conf = "MIXED", 40
        else:
            p_sig, p_conf = "BUY" if vol_r > 0.5 else "MIXED", 55
        reasons.append(f"Put OI +{oi_p:,.0f}")
    elif oi_p < -100:
        p_sig, p_conf = "CLOSING", 80
        reasons.append(f"Put OI {oi_p:,.0f} closing")

    # Net interpretation
    net = ""
    if c_sig in ("STRONG BUY", "BUY") and p_sig in ("CLOSING", "WRITING", "NEUTRAL"):
        net = "BULLISH"
    elif p_sig in ("STRONG BUY", "BUY") and c_sig in ("CLOSING", "WRITING", "NEUTRAL"):
        net = "BEARISH / HEDGE"
    elif c_sig == "WRITING" and p_sig == "WRITING":
        net = "PREMIUM SELLING (range-bound)"
    elif c_sig == "CLOSING" and p_sig == "CLOSING":
        net = "RISK-OFF (unwinding)"
    elif c_sig in ("STRONG BUY", "BUY") and p_sig in ("STRONG BUY", "BUY"):
        net = "STRADDLE / VOL BUY"
    else:
        net = "MIXED"

    return dict(call_signal=c_sig, call_conf=c_conf, put_signal=p_sig,
                put_conf=p_conf, net_view=net, reasons="; ".join(reasons))


# ===================================================================
# ──  OI PREDICTION ENGINE  (next-day hints from OI changes)
# ===================================================================
def oi_prediction_analysis(ticker, dates_back=5):
    """
    Analyse OI changes across multiple days and predict next-day direction.

    Logic:
    1. Aggregate call/put OI change per day
    2. Calculate PCR trend, net delta proxy, gamma exposure proxy
    3. Backtest the signal against actual next-day stock moves
    4. Return prediction + accuracy table
    """
    conn = get_conn()
    # Get last N+1 trade dates for the ticker (chronological sort)
    raw_dates = pd.read_sql(
        "SELECT DISTINCT trade_date_now FROM options_change WHERE ticker=?",
        conn, params=[ticker])["trade_date_now"].tolist()
    all_dates = _sort_dates_chrono(raw_dates, descending=True)

    if len(all_dates) < 3:
        conn.close()
        return None, None

    dates_use = all_dates[:dates_back + 1]  # one extra for "next day"

    rows = []
    for td in dates_use:
        day_df = pd.read_sql(
            "SELECT change_OI_Call, change_OI_Put, openInt_Call_now, openInt_Put_now, "
            "vol_Call_now, vol_Put_now, strike, expiry_date, "
            "lastPrice_Call_now, lastPrice_Put_now "
            "FROM options_change WHERE ticker=? AND trade_date_now=?",
            conn, params=[ticker, td])
        if day_df.empty:
            continue

        call_oi_chg = day_df["change_OI_Call"].sum()
        put_oi_chg = day_df["change_OI_Put"].sum()
        call_oi_total = day_df["openInt_Call_now"].sum()
        put_oi_total = day_df["openInt_Put_now"].sum()
        pcr = put_oi_total / call_oi_total if call_oi_total > 0 else 0
        call_vol = day_df["vol_Call_now"].sum()
        put_vol = day_df["vol_Put_now"].sum()
        vol_pcr = put_vol / call_vol if call_vol > 0 else 0

        # Net OI bias: positive = more call OI added → bullish
        net_oi_bias = call_oi_chg - put_oi_chg

        # Gamma exposure proxy: sum(OI * strike) weighted
        day_df["gex_call"] = day_df["openInt_Call_now"] * day_df["strike"]
        day_df["gex_put"] = day_df["openInt_Put_now"] * day_df["strike"]
        gex_net = day_df["gex_call"].sum() - day_df["gex_put"].sum()

        rows.append(dict(
            trade_date=td,
            call_oi_chg=call_oi_chg, put_oi_chg=put_oi_chg,
            net_oi_bias=net_oi_bias,
            pcr_oi=pcr, vol_pcr=vol_pcr,
            call_vol=call_vol, put_vol=put_vol,
            gex_net=gex_net,
            call_oi_total=call_oi_total, put_oi_total=put_oi_total,
        ))

    conn.close()
    if not rows:
        return None, None
    pred_df = pd.DataFrame(rows)

    # Get stock price data for backtest
    stock_df = load_stock_daily(ticker)
    if not stock_df.empty:
        pred_df = pred_df.merge(stock_df[["trade_date", "close", "volume"]].rename(
            columns={"trade_date": "trade_date"}), left_on="trade_date",
            right_on="trade_date", how="left")
        pred_df["next_close"] = pred_df["close"].shift(-1)
        pred_df["actual_move"] = pred_df["next_close"] - pred_df["close"]
        pred_df["actual_pct"] = pred_df["actual_move"] / pred_df["close"] * 100

    # Signal: composite score
    pred_df["oi_signal"] = np.where(pred_df["net_oi_bias"] > 0, 1, -1)
    pred_df["pcr_signal"] = np.where(pred_df["pcr_oi"] < 0.7, 1,
                            np.where(pred_df["pcr_oi"] > 1.3, -1, 0))
    pred_df["vol_signal"] = np.where(pred_df["vol_pcr"] < 0.7, 1,
                            np.where(pred_df["vol_pcr"] > 1.3, -1, 0))
    pred_df["composite"] = pred_df["oi_signal"] + pred_df["pcr_signal"] + pred_df["vol_signal"]
    pred_df["prediction"] = np.where(pred_df["composite"] >= 2, "BULLISH",
                            np.where(pred_df["composite"] <= -2, "BEARISH", "NEUTRAL"))

    # Backtest accuracy
    if "actual_move" in pred_df.columns:
        valid = pred_df.dropna(subset=["actual_move"])
        if len(valid) > 0:
            correct = ((valid["composite"] > 0) & (valid["actual_move"] > 0)) | \
                      ((valid["composite"] < 0) & (valid["actual_move"] < 0))
            accuracy = correct.sum() / len(valid) * 100
        else:
            accuracy = None
    else:
        accuracy = None

    return pred_df, accuracy


# ===================================================================
# ──  MULTI-DAY OI ACCUMULATION  (cumulative build + conviction)
# ===================================================================

@st.cache_data(ttl=60, show_spinner=False)
def _load_oi_multi_day(ticker, n_days=7):
    """Load OI data for last n_days trade dates for a ticker.
    Returns (DataFrame with trade_date column, list of dates newest-first).
    """
    conn = get_conn()
    raw_dates = pd.read_sql(
        "SELECT DISTINCT trade_date_now FROM options_change WHERE ticker=?",
        conn, params=[ticker])["trade_date_now"].tolist()
    conn.close()
    all_dates = _sort_dates_chrono(raw_dates, descending=True)
    dates_use = all_dates[:n_days]
    if not dates_use:
        return pd.DataFrame(), []
    frames = []
    for td in dates_use:
        df = q(
            "SELECT strike, expiry_date, change_OI_Call, change_OI_Put, "
            "openInt_Call_now, openInt_Put_now, vol_Call_now, vol_Put_now "
            "FROM options_change WHERE ticker=? AND trade_date_now=?",
            [ticker, td])
        if not df.empty:
            df["trade_date"] = td
            frames.append(df)
    if not frames:
        return pd.DataFrame(), dates_use
    return pd.concat(frames, ignore_index=True), dates_use


def _compute_oi_conviction(multi_df, dates_newest_first, live_px=0):
    """Per-strike conviction score based on N-day OI accumulation consistency.

    Returns DataFrame sorted by conviction descending.
    Columns: strike, direction, conviction, streak, streak_dir, cum_net,
             cum_call, cum_put, consistency, bull_days, bear_days, n_days
    """
    if multi_df.empty:
        return pd.DataFrame()

    # Aggregate across expiries to get per (strike, date) net
    day_agg = multi_df.groupby(["strike", "trade_date"]).agg(
        call_chg=("change_OI_Call", "sum"),
        put_chg=("change_OI_Put", "sum"),
        call_vol=("vol_Call_now", "sum"),
        put_vol=("vol_Put_now", "sum"),
    ).reset_index()
    day_agg["net_chg"] = day_agg["call_chg"] - day_agg["put_chg"]

    # Cumulative totals per strike across all N days
    strike_agg = day_agg.groupby("strike").agg(
        cum_call=("call_chg", "sum"),
        cum_put=("put_chg", "sum"),
        cum_net=("net_chg", "sum"),
        avg_call_vol=("call_vol", "mean"),
        avg_put_vol=("put_vol", "mean"),
        n_days=("trade_date", "count"),
        bull_days=("net_chg", lambda x: (x > 0).sum()),
        bear_days=("net_chg", lambda x: (x < 0).sum()),
    ).reset_index()

    # Current streak: consecutive matching direction from most recent day back
    date_rank = {d: i for i, d in enumerate(dates_newest_first)}  # 0 = newest
    streaks = []
    for strike in strike_agg["strike"].unique():
        sk = day_agg[day_agg["strike"] == strike].copy()
        sk["rank"] = sk["trade_date"].map(date_rank)
        sk = sk.sort_values("rank")  # newest first (rank 0 = newest)
        streak, streak_dir = 0, "FLAT"
        for _, row in sk.iterrows():
            d = row["net_chg"]
            cur_dir = "BULL" if d > 0 else ("BEAR" if d < 0 else "FLAT")
            if streak == 0:
                streak_dir = cur_dir
                streak = 1 if cur_dir != "FLAT" else 0
            elif cur_dir == streak_dir and cur_dir != "FLAT":
                streak += 1
            else:
                break
        streaks.append({"strike": strike, "streak": streak, "streak_dir": streak_dir})

    streak_df = pd.DataFrame(streaks)
    strike_agg = strike_agg.merge(streak_df, on="strike", how="left")

    # Directional consistency (fraction of days in winning direction)
    strike_agg["consistency"] = strike_agg.apply(
        lambda r: r["bull_days"] / r["n_days"] if r["cum_net"] >= 0
                  else r["bear_days"] / r["n_days"], axis=1)

    # Conviction score 0-10
    max_cum = strike_agg["cum_net"].abs().max()
    strike_agg["mag_score"] = (
        (strike_agg["cum_net"].abs() / max_cum * 4).clip(0, 4)
        if max_cum > 0 else 0.0
    )
    strike_agg["consistency_score"] = (strike_agg["consistency"] * 3).clip(0, 3)
    strike_agg["streak_score"] = strike_agg["streak"].clip(0, 2).astype(float)

    # ATM proximity bonus (+1 within 5% of spot)
    if live_px > 0:
        strike_agg["dist_pct"] = (strike_agg["strike"] - live_px).abs() / live_px * 100
        strike_agg["atm_bonus"] = np.where(strike_agg["dist_pct"] <= 5, 1.0, 0.0)
    else:
        strike_agg["dist_pct"] = 0.0
        strike_agg["atm_bonus"] = 0.0

    strike_agg["conviction"] = (
        strike_agg["mag_score"] + strike_agg["consistency_score"]
        + strike_agg["streak_score"] + strike_agg["atm_bonus"]
    ).clip(0, 10).round(1)

    strike_agg["direction"] = np.where(
        strike_agg["cum_net"] > 0, "BULL",
        np.where(strike_agg["cum_net"] < 0, "BEAR", "FLAT"))

    return strike_agg.sort_values("conviction", ascending=False).reset_index(drop=True)


def _oi_idea_metrics(conn, ticker, strike, direction, spot, r=0.045):
    """Concrete economics for an OI-conviction idea: entry premium (from DB), expiry/DTE,
    IV (back-solved), POP (~|delta|), a 1-sigma expected-move target + the option's value
    there, max risk and reward:risk. Returns dict, or None if no usable premium in DB."""
    if not spot or spot <= 0:
        return None
    typ = "call" if direction == "BULL" else "put"
    last_col = "lastPrice_Call_now" if typ == "call" else "lastPrice_Put_now"
    oi_col = "openInt_Call_now" if typ == "call" else "openInt_Put_now"
    try:
        rows = pd.read_sql(
            f"SELECT expiry_date, {last_col} AS last, {oi_col} AS oi "
            "FROM options_change WHERE ticker=? AND strike=?",
            conn, params=(ticker, float(strike)))
    except Exception:
        return None
    if rows.empty:
        return None
    today = datetime.now()
    cand = []
    for _, rr in rows.iterrows():
        ed = None
        for fmt in ("%m-%d-%Y", "%Y-%m-%d"):
            try:
                ed = datetime.strptime(str(rr["expiry_date"]), fmt); break
            except Exception:
                pass
        if ed is None:
            continue
        dte = (ed - today).days
        last = float(rr["last"] or 0)
        if dte >= 1 and last > 0:
            cand.append((dte, str(rr["expiry_date"]), last, float(rr["oi"] or 0)))
    if not cand:
        return None
    # prefer the ideal 21-50 DTE entry zone, else the nearest future expiry
    ideal = [c for c in cand if 21 <= c[0] <= 50]
    dte, exp, entry, oi = sorted(ideal or cand, key=lambda c: (0 if 21 <= c[0] <= 50 else 1, c[0]))[0]
    T = max(dte, 1) / 365.0
    iv = _implied_vol(entry, spot, float(strike), T, r, typ)
    g = bs_greeks(spot, float(strike), T, r, iv, typ)
    pop = abs(g.get("delta", 0.0)) * 100
    H = min(dte, 10)                                   # ~2-week hold (capped by DTE)
    sigma_move = spot * iv * (H / 252.0) ** 0.5        # 1-sigma expected move
    target = spot + sigma_move if typ == "call" else spot - sigma_move
    T_rem = max(dte - H, 1) / 365.0
    tval = bs_greeks(target, float(strike), T_rem, r, iv, typ).get("price", 0.0)
    profit_pct = (tval - entry) / entry * 100 if entry > 0 else 0.0
    return {
        "type": typ, "expiry": exp, "dte": dte, "entry": entry, "invest": entry * 100,
        "iv": iv, "pop": pop, "target": target, "target_val": tval,
        "profit_pct": profit_pct, "max_risk": entry * 100,
        "rr": (max(tval - entry, 0) / entry) if entry > 0 else 0.0,
        "hold": H, "move_pct": sigma_move / spot * 100 * (1 if typ == "call" else -1),
    }


@st.cache_data(ttl=600, show_spinner=False)
def _backtest_oi_conviction(ticker, lookback=7, hold_days=5, min_conv=6, max_ideas=5,
                            tp_pct=100.0, sl_pct=50.0):
    """Walk-forward backtest of OI-conviction ideas using stored option premiums.
    For each historical date, fire ideas (conviction >= min_conv) and track the long
    option over the next `hold_days` on the SAME strike+expiry:
      • fixed-hold P&L (exit on day N),
      • managed P&L with a take-profit / stop rule (first daily close to touch),
      • MFE / MAE (best & worst close reached during the hold).
    Returns (summary dict, trades DataFrame)."""
    conn = get_conn()
    try:
        hist = pd.read_sql(
            "SELECT strike, expiry_date, trade_date_now, change_OI_Call, change_OI_Put, "
            "openInt_Call_now, openInt_Put_now, vol_Call_now, vol_Put_now, "
            "lastPrice_Call_now, lastPrice_Put_now FROM options_change WHERE ticker=?",
            conn, params=(ticker,))
        sd = pd.read_sql("SELECT trade_date, close FROM stock_daily WHERE ticker=?",
                         conn, params=(ticker,))
    finally:
        conn.close()
    if hist.empty:
        return {"n": 0}, pd.DataFrame()
    spot_by_date = {str(d): float(c) for d, c in zip(sd["trade_date"], sd["close"])}
    dates = _sort_dates_chrono(list(hist["trade_date_now"].dropna().unique()), descending=False)
    if len(dates) < lookback + hold_days + 1:
        return {"n": 0, "error": "not enough history"}, pd.DataFrame()

    def _parse(d):
        for fmt in ("%m-%d-%Y", "%Y-%m-%d"):
            try:
                return datetime.strptime(str(d), fmt)
            except Exception:
                pass
        return None

    # fast price index: (date, strike, expiry) -> (callLast, putLast)
    pidx = {}
    for t in hist.itertuples(index=False):
        pidx[(t.trade_date_now, float(t.strike), t.expiry_date)] = (
            float(t.lastPrice_Call_now or 0), float(t.lastPrice_Put_now or 0))

    tp, sl = tp_pct / 100.0, sl_pct / 100.0
    trades = []
    for i in range(lookback - 1, len(dates) - hold_days):
        sig_d = dates[i]
        path_dates = dates[i + 1: i + hold_days + 1]
        exit_d = path_dates[-1]
        window = dates[i - lookback + 1: i + 1]
        sub = hist[hist["trade_date_now"].isin(window)].copy()
        spot_sig = spot_by_date.get(sig_d)
        if sub.empty or not spot_sig:
            continue
        sub = sub.rename(columns={"trade_date_now": "trade_date"})
        conv = _compute_oi_conviction(sub, _sort_dates_chrono(list(window), descending=True), spot_sig)
        if conv.empty:
            continue
        exd = _parse(exit_d)
        for _, rr2 in conv[conv["conviction"] >= min_conv].head(max_ideas).iterrows():
            direction = rr2["direction"]
            if direction not in ("BULL", "BEAR"):
                continue
            strike = float(rr2["strike"]); typ = "call" if direction == "BULL" else "put"
            ix = 0 if typ == "call" else 1
            lc = "lastPrice_Call_now" if typ == "call" else "lastPrice_Put_now"
            oc = "openInt_Call_now" if typ == "call" else "openInt_Put_now"
            er = hist[(hist["trade_date_now"] == sig_d) & (hist["strike"] == strike)]
            best = None
            for _, e in er.iterrows():
                ed = _parse(e["expiry_date"])
                if ed is None or (exd is not None and ed <= exd):
                    continue                          # skip expiries that die during the hold
                entry_px = float(e[lc] or 0)
                if entry_px <= 0:
                    continue
                if best is None or float(e[oc] or 0) > best[2]:
                    best = (str(e["expiry_date"]), entry_px, float(e[oc] or 0))
            if best is None:
                continue
            exp, entry_px, _ = best
            # daily option-price path over the hold
            path = []
            for d in path_dates:
                pv = pidx.get((d, strike, exp))
                if pv is not None and pv[ix] >= 0:
                    path.append(pv[ix])
            if not path:
                continue
            exit_px = path[-1]
            fixed = (exit_px - entry_px) / entry_px * 100
            mfe = (max(path) - entry_px) / entry_px * 100
            mae = (min(path) - entry_px) / entry_px * 100
            managed, reason = fixed, "TIME"          # first daily close to hit TP or SL
            for pv in path:
                ch = (pv - entry_px) / entry_px
                if ch >= tp:
                    managed, reason = tp * 100, "TP"; break
                if ch <= -sl:
                    managed, reason = -sl * 100, "SL"; break
            trades.append({
                "signal_date": sig_d, "exit_date": exit_d, "strike": strike,
                "dir": direction, "type": typ, "expiry": exp, "entry": round(entry_px, 2),
                "exit": round(exit_px, 2), "pnl_pct": round(fixed, 1),
                "managed_pct": round(managed, 1), "exit_reason": reason,
                "mfe_pct": round(mfe, 1), "mae_pct": round(mae, 1),
                "conviction": float(rr2["conviction"]),
                "path": [round((pv - entry_px) / entry_px * 100, 1) for pv in path],
            })
    tdf = pd.DataFrame(trades)
    if tdf.empty:
        return {"n": 0}, tdf

    def _pf(series):
        g = float(series[series > 0].sum()); l = float(series[series < 0].sum())
        return (g / abs(l)) if l < 0 else (float("inf") if g > 0 else 0.0)

    wins = tdf["pnl_pct"] > 0
    mw = tdf["managed_pct"] > 0
    buckets = {}
    for lo, hi, lbl in [(6, 7, "6–7"), (7, 8, "7–8"), (8, 10.01, "8–10")]:
        b = tdf[(tdf["conviction"] >= lo) & (tdf["conviction"] < hi)]
        if not b.empty:
            buckets[lbl] = {"n": int(len(b)), "win": float((b["pnl_pct"] > 0).mean() * 100),
                            "avg": float(b["pnl_pct"].mean())}
    summary = {
        "n": int(len(tdf)),
        "win_rate": float(wins.mean() * 100),
        "avg_pnl": float(tdf["pnl_pct"].mean()),
        "median_pnl": float(tdf["pnl_pct"].median()),
        "avg_win": float(tdf[wins]["pnl_pct"].mean()) if wins.any() else 0.0,
        "avg_loss": float(tdf[~wins]["pnl_pct"].mean()) if (~wins).any() else 0.0,
        "profit_factor": _pf(tdf["pnl_pct"]),
        "avg_mfe": float(tdf["mfe_pct"].mean()),
        "avg_mae": float(tdf["mae_pct"].mean()),
        "mgd_win_rate": float(mw.mean() * 100),
        "mgd_avg_pnl": float(tdf["managed_pct"].mean()),
        "mgd_profit_factor": _pf(tdf["managed_pct"]),
        "tp_hit": int((tdf["exit_reason"] == "TP").sum()),
        "sl_hit": int((tdf["exit_reason"] == "SL").sum()),
        "time_exit": int((tdf["exit_reason"] == "TIME").sum()),
        "bull_n": int((tdf["dir"] == "BULL").sum()),
        "bear_n": int((tdf["dir"] == "BEAR").sum()),
        "bull_win": float((tdf[tdf["dir"] == "BULL"]["pnl_pct"] > 0).mean() * 100) if (tdf["dir"] == "BULL").any() else 0.0,
        "bear_win": float((tdf[tdf["dir"] == "BEAR"]["pnl_pct"] > 0).mean() * 100) if (tdf["dir"] == "BEAR").any() else 0.0,
        "buckets": buckets,
        "hold_days": hold_days, "lookback": lookback, "min_conv": min_conv,
        "tp_pct": tp_pct, "sl_pct": sl_pct,
    }
    return summary, tdf


@st.cache_data(ttl=600, show_spinner=False)
def _optimize_oi_exit(ticker, lookback, hold_days, min_conv):
    """Grid-search take-profit/stop-loss over the backtested trades' daily price paths,
    then walk-forward validate (optimize on the 1st half, test out-of-sample on the 2nd).
    Returns (best dict, grid DataFrame, walk_forward dict)."""
    _, tdf = _backtest_oi_conviction(ticker, lookback, hold_days, min_conv)
    if tdf is None or tdf.empty or "path" not in tdf.columns:
        return {}, pd.DataFrame(), {}
    tdf = tdf.sort_values("signal_date").reset_index(drop=True)
    paths = list(tdf["path"]); fixed = list(tdf["pnl_pct"])
    tp_grid = [25, 40, 50, 75, 100, 150, 200]
    sl_grid = [25, 40, 50, 60, 75, 100]

    def _apply(ps, fs, tp, sl):
        out = []
        for pth, fx in zip(ps, fs):
            r = fx
            for ch in (pth or []):
                if ch >= tp:
                    r = float(tp); break
                if ch <= -sl:
                    r = float(-sl); break
            out.append(r)
        return np.array(out, dtype=float) if out else np.array([0.0])

    def _stats(arr):
        g = float(arr[arr > 0].sum()); l = float(arr[arr < 0].sum())
        return {"exp": float(arr.mean()), "win": float((arr > 0).mean() * 100),
                "pf": (g / abs(l)) if l < 0 else (float("inf") if g > 0 else 0.0)}

    rows = []
    for tp in tp_grid:
        for sl in sl_grid:
            s = _stats(_apply(paths, fixed, tp, sl))
            rows.append({"tp": tp, "sl": sl, **s})
    grid = pd.DataFrame(rows)
    best = grid.loc[grid["exp"].idxmax()].to_dict()

    # walk-forward: optimize on first half, apply to held-out second half
    n = len(tdf); mid = max(1, n // 2)
    tr_p, tr_f = paths[:mid], fixed[:mid]
    te_p, te_f = paths[mid:], fixed[mid:]
    b_tp, b_sl, b_exp = tp_grid[0], sl_grid[0], -1e9
    for tp in tp_grid:
        for sl in sl_grid:
            e = float(_apply(tr_p, tr_f, tp, sl).mean())
            if e > b_exp:
                b_tp, b_sl, b_exp = tp, sl, e
    te = _apply(te_p, te_f, b_tp, b_sl)
    te_s = _stats(te)
    wf = {"tp": b_tp, "sl": b_sl, "train_exp": b_exp, "n_train": mid, "n_test": len(te_p),
          "test_exp": te_s["exp"], "test_win": te_s["win"], "test_pf": te_s["pf"]}
    return best, grid, wf


# ── News + sentiment helpers (free sources, no API key) ──
_NEWS_POS = ("rally", "surge", "bull", "gain", "beat", "strong", "rise", "record", "buy",
             "upgrade", "boost", "growth", "profit", "optimis", "soar", "jump", "outperform",
             "raise", "tops", "win", "deal", "approval", "rebound", "expand", "demand")
_NEWS_NEG = ("drop", "fall", "crash", "sell-off", "bear", "loss", "cut", "slash", "tariff",
             "warn", "fear", "decline", "recession", "weak", "miss", "layoff", "plunge",
             "tumble", "sink", "dump", "concern", "risk", "threat", "crisis", "downgrade",
             "probe", "lawsuit", "halt", "slump", "fraud", "ban", "selloff", "glut")


# Words/phrases that dominate — if present, the headline reads negative regardless of any
# upbeat keyword (handles "deal paused", "Hormuz closed", "talks collapse", geopolitics, etc.)
_NEG_OVERRIDE = (
    "paused", "pause", "halt", "stall", "collapse", "delay", "fail", "fell through",
    "off the table", "blocked", "breakdown", "no deal", "scrapped", "called off",
    "closure", "closed", "shut", "blockade", "hormuz", "strait", "embargo", "sanction",
    "escalat", "conflict", " war", "attack", "strike on", "missile", "disrupt", "shortage",
    "glut", "probe", "lawsuit", "fraud", "recall", "downgrade", "plunge", "crash",
    "selloff", "sell-off", "tariff", "ban ", "slump", "tumble",
)
_NEG_FLIP = tuple(f"{neg} {pw}" for neg in ("no", "not", "without", "never", "denies", "denied")
                  for pw in ("deal", "beat", "gain", "growth", "upgrade", "demand", "approval"))


def _headline_tone(title):
    t = str(title).lower()
    if any(w in t for w in _NEG_OVERRIDE) or any(ph in t for ph in _NEG_FLIP):
        return -1                                  # decisive negative / disruption / negation
    p = sum(1 for w in _NEWS_POS if w in t)
    n = sum(1 for w in _NEWS_NEG if w in t)
    return 1 if p > n else (-1 if n > p else 0)


@st.cache_data(ttl=600, show_spinner=False)
def _ticker_news(ticker, n=6):
    """Free real-time headlines (Google News + Yahoo + Reddit RSS) with a simple
    bullish/bearish tone score. No API key. Returns dict(items, bull, bear, label)."""
    out = {"items": [], "bull": 0, "bear": 0, "label": "NEUTRAL"}
    try:
        import feedparser, time as _t, html as _h, socket
        socket.setdefaulttimeout(6)
    except Exception:
        return out
    srcs = [
        ("Google", f"https://news.google.com/rss/search?q={ticker}%20stock&hl=en-US&gl=US&ceid=US:en"),
        ("Yahoo", f"https://feeds.finance.yahoo.com/rss/2.0/headline?s={ticker}&region=US&lang=en-US"),
        ("Reddit", f"https://www.reddit.com/search.rss?q={ticker}&sort=new"),
    ]
    seen, items = set(), []
    for sname, url in srcs:
        try:
            fp = feedparser.parse(url)
            for e in fp.entries[:8]:
                title = _h.unescape(e.get("title", "")).strip()
                key = title.lower()[:80]
                if not title or key in seen:
                    continue
                seen.add(key)
                pp = e.get("published_parsed")
                when = _t.strftime("%d %b %H:%M", pp).lstrip("0") if pp else ""
                items.append({"title": title, "link": e.get("link", ""), "source": sname,
                              "when": when, "tone": _headline_tone(title)})
        except Exception:
            continue
    bull = sum(1 for i in items if i["tone"] > 0)
    bear = sum(1 for i in items if i["tone"] < 0)
    label = ("BULLISH" if bull > bear + 1 else "BEARISH" if bear > bull + 1
             else "MIXED" if (bull or bear) else "NEUTRAL")
    out.update({"items": items[:n], "bull": bull, "bear": bear, "label": label})
    return out


@st.cache_data(ttl=600, show_spinner=False)
def _stocktwits_sentiment(ticker):
    """Free StockTwits crowd sentiment (Bullish/Bearish message tags). No API key."""
    try:
        import urllib.request, json as _j
        req = urllib.request.Request(
            f"https://api.stocktwits.com/api/2/streams/symbol/{ticker}.json",
            headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=6) as r:
            d = _j.loads(r.read().decode())
        bull = bear = 0
        for m in d.get("messages", []):
            b = ((m.get("entities") or {}).get("sentiment") or {}).get("basic")
            if b == "Bullish": bull += 1
            elif b == "Bearish": bear += 1
        if bull + bear > 0:
            return {"bull": bull, "bear": bear,
                    "label": "BULLISH" if bull > bear * 1.3 else "BEARISH" if bear > bull * 1.3 else "MIXED"}
    except Exception:
        pass
    return None


@st.cache_data(ttl=900, show_spinner=False)
def _finnhub_sentiment(ticker):
    """Finnhub sentiment. Tries the news-sentiment endpoint (premium on some plans); if that's
    gated, falls back to the free company-news endpoint and scores the headlines locally.
    Needs free FINNHUB_API_KEY env var; returns None if no key / nothing usable."""
    key = os.environ.get("FINNHUB_API_KEY") or os.environ.get("FINNHUB_KEY")
    if not key:
        return None
    import urllib.request, json as _j
    # 1) news-sentiment (richest, but premium on some tiers)
    try:
        with urllib.request.urlopen(
                f"https://finnhub.io/api/v1/news-sentiment?symbol={ticker}&token={key}", timeout=6) as r:
            d = _j.loads(r.read().decode())
        bp = (d.get("sentiment") or {}).get("bullishPercent")
        if bp is not None:
            return {"bull_pct": bp * 100, "buzz": (d.get("buzz") or {}).get("buzz"),
                    "label": "BULLISH" if bp >= 0.6 else "BEARISH" if bp <= 0.4 else "MIXED",
                    "src": "news-sentiment"}
    except Exception:
        pass
    # 2) free fallback: company-news → score headlines with the context-aware tone fn
    try:
        from datetime import date, timedelta
        _to = date.today(); _from = _to - timedelta(days=7)
        with urllib.request.urlopen(
                f"https://finnhub.io/api/v1/company-news?symbol={ticker}"
                f"&from={_from}&to={_to}&token={key}", timeout=6) as r:
            arts = _j.loads(r.read().decode())
        bull = bear = 0
        for a in (arts or [])[:40]:
            t = _headline_tone((a.get("headline", "") + " " + a.get("summary", "")))
            if t > 0: bull += 1
            elif t < 0: bear += 1
        if bull + bear > 0:
            return {"bull_pct": bull / (bull + bear) * 100, "buzz": None, "bull": bull, "bear": bear,
                    "label": "BULLISH" if bull > bear * 1.3 else "BEARISH" if bear > bull * 1.3 else "MIXED",
                    "src": "company-news"}
    except Exception:
        pass
    return None


def _gp_writeup(tk, spot, em, walls, r1, s1, dd, th, nw, tlegs, stt=None):
    """Plain-language next-day read for one stock: levels + expected move + news + position risk."""
    cw, pw = walls.get("call_wall"), walls.get("put_wall")
    P = []
    if cw and pw:
        if pw < spot < cw:
            P.append(f"{tk} sits between put-wall ${pw:.0f} (support) and call-wall ${cw:.0f} "
                     "(resistance) — dealers tend to pin price inside this range.")
        elif spot >= cw:
            P.append(f"{tk} is above its call wall ${cw:.0f} — extended; that level often caps or reverses moves.")
        else:
            P.append(f"{tk} is below its put wall ${pw:.0f} — support has broken, momentum is weak.")
    elif cw:
        P.append(f"{tk} faces overhead resistance at the call wall ${cw:.0f}.")
    elif pw:
        P.append(f"{tk} has support at the put wall ${pw:.0f}.")
    else:
        P.append(f"{tk} has no dominant OI wall nearby, so price can roam.")
    P.append(f"Tomorrow's 1-day expected move is about ±${em:.2f} (≈{em/spot*100:.1f}%) — a normal "
             f"session likely stays in ${spot-em:.0f}–${spot+em:.0f}.")
    if nw.get("items"):
        if nw["label"] == "BULLISH":
            P.append(f"News flow leans positive ({nw['bull']} up vs {nw['bear']} down headlines) — supportive "
                     "for the open, though good news can deflate option IV.")
        elif nw["label"] == "BEARISH":
            P.append(f"News flow leans negative ({nw['bear']} down vs {nw['bull']} up headlines) — raises gap-down "
                     "and IV-spike risk overnight.")
        elif nw["label"] == "MIXED":
            P.append(f"News is mixed ({nw['bull']} up / {nw['bear']} down) — no clean catalyst, so the levels above "
                     "should drive the tape.")
        else:
            P.append("Newsflow is quiet — technicals and the walls should dominate.")
    if stt:
        P.append(f"Retail crowd on StockTwits is {stt['label'].lower()} "
                 f"({stt['bull']} bullish / {stt['bear']} bearish).")
        if (nw.get("items") and nw["label"] in ("BULLISH", "BEARISH")
                and stt["label"] in ("BULLISH", "BEARISH") and stt["label"] != nw["label"]):
            P.append("Heads-up: news and the crowd disagree — expect choppier, headline-driven moves.")
    if dd > 0:
        P.append(f"Your {tk} legs are net long (${dd:,.0f} per +1%) — a green open helps; a gap-down is the risk.")
    elif dd < 0:
        P.append(f"Your {tk} legs are net short (${dd:,.0f} per +1%) — a gap-up is the main risk.")
    risks = []
    for l in tlegs:
        money = "ITM" if ((l["spot"] > l["K"]) if l["typ"] == "call" else (l["spot"] < l["K"])) else "OTM"
        if l["side"] == "short" and money == "ITM":
            risks.append(f"short ${l['K']:.0f}{l['typ'][0].upper()} is ITM (assignment risk)")
        if l["dte"] <= 7:
            risks.append(f"${l['K']:.0f}{l['typ'][0].upper()} has {l['dte']}DTE (theta/gamma spike)")
    if risks:
        P.append("Watch: " + "; ".join(dict.fromkeys(risks)) + ".")
    if cw and pw and pw < spot < cw and th >= 0:
        P.append("Plan: hold — let the range and time decay work; act only on a clean break of a wall.")
    else:
        P.append("Plan: manage the flagged legs at the open (roll / trim / hedge) per the table below.")
    return " ".join(P)


@st.cache_data(ttl=300, show_spinner=False)
def _macro_backdrop():
    """Live-ish cross-asset/international backdrop via yfinance (futures, vol, FX, rates,
    commodities, China/EM, crypto) with a risk-on/off read. Returns (dict, label, score)."""
    syms = {
        "S&P fut": "ES=F", "Nasdaq fut": "NQ=F", "VIX": "^VIX", "WTI oil": "CL=F",
        "Gold": "GC=F", "Dollar (DXY)": "DX=F", "US 10y": "^TNX",
        "China (FXI)": "FXI", "EM (EEM)": "EEM", "Bitcoin": "BTC-USD",
    }
    out = {}
    for name, sym in syms.items():
        try:
            c = yf.Ticker(sym).history(period="5d")["Close"].dropna()
            if len(c) >= 2:
                out[name] = {"price": float(c.iloc[-1]),
                             "pct": (float(c.iloc[-1]) / float(c.iloc[-2]) - 1) * 100}
        except Exception:
            continue
    score = 0
    if "S&P fut" in out:
        score += 1 if out["S&P fut"]["pct"] > 0 else -1
    if "VIX" in out:
        score += -1 if out["VIX"]["pct"] > 3 else (1 if out["VIX"]["pct"] < -3 else 0)
    if "Dollar (DXY)" in out:
        score += -1 if out["Dollar (DXY)"]["pct"] > 0.3 else (1 if out["Dollar (DXY)"]["pct"] < -0.3 else 0)
    if "US 10y" in out:
        score += -1 if out["US 10y"]["pct"] > 2 else 0
    label = "RISK-ON" if score >= 2 else ("RISK-OFF" if score <= -2 else "MIXED")
    return out, label, score


def _macro_writeup(mac, label):
    def mv(k):
        if k in mac:
            p = mac[k]["pct"]
            return ("up" if p > 0.1 else "down" if p < -0.1 else "flat") + f" {p:+.1f}%"
        return None
    bits = []
    for k in ("S&P fut", "VIX", "Dollar (DXY)", "US 10y", "WTI oil", "Gold", "China (FXI)"):
        m = mv(k)
        if m:
            bits.append(f"{k} {m}")
    tail = {
        "RISK-ON": "Supportive backdrop for longs / semis / tech — tailwind into the open.",
        "RISK-OFF": "Headwind — favor hedges, trim high-beta, and expect wider swings.",
        "MIXED": "No clear global push — let your stock-level levels lead.",
    }[label]
    return f"Overnight, {', '.join(bits)}. {tail}"


_EDGAR_H = {"User-Agent": "RUDRARJUN Analytics research srinivas.analystsas@gmail.com"}
_EDGAR_DDL = ("CREATE TABLE IF NOT EXISTS edgar_13f (cik TEXT, fund TEXT, quarter TEXT, "
              "filing_date TEXT, cusip TEXT, issuer TEXT, shares REAL, value REAL, put_call TEXT, "
              "PRIMARY KEY (cik, quarter, cusip, put_call))")


def _edgar_13f_filings(cik, n=12):
    """List the last n 13F-HR filings for a CIK from SEC EDGAR submissions JSON."""
    import urllib.request, json as _j
    url = f"https://data.sec.gov/submissions/CIK{int(cik):010d}.json"
    req = urllib.request.Request(url, headers=_EDGAR_H)
    with urllib.request.urlopen(req, timeout=20) as r:
        d = _j.loads(r.read().decode())
    rec = d["filings"]["recent"]
    seen = {}
    for form, acc, fd, rd in zip(rec["form"], rec["accessionNumber"], rec["filingDate"], rec["reportDate"]):
        if form == "13F-HR" and rd not in seen:
            seen[rd] = {"accession": acc, "filing_date": fd, "report_date": rd}
    return sorted(seen.values(), key=lambda x: x["report_date"], reverse=True)[:n]


def _edgar_parse_infotable(cik, accession):
    """Parse a 13F information-table XML into a list of holdings dicts."""
    import urllib.request, json as _j, xml.etree.ElementTree as ET
    ciki = str(int(cik)); acc = accession.replace("-", "")
    idx = f"https://www.sec.gov/Archives/edgar/data/{ciki}/{acc}/index.json"
    req = urllib.request.Request(idx, headers=_EDGAR_H)
    with urllib.request.urlopen(req, timeout=20) as r:
        items = _j.loads(r.read().decode())["directory"]["item"]
    xmls = [it["name"] for it in items if it["name"].lower().endswith(".xml")]
    cand = [n for n in xmls if n.lower() != "primary_doc.xml"]
    rows = []
    for nm in (cand or xmls):                          # combine ALL info-table files (big filers split them)
        try:
            url = f"https://www.sec.gov/Archives/edgar/data/{ciki}/{acc}/{nm}"
            req = urllib.request.Request(url, headers=_EDGAR_H)
            with urllib.request.urlopen(req, timeout=25) as r:
                root = ET.fromstring(r.read().decode(errors="ignore"))
            for el in root.iter():
                if el.tag.split("}")[-1] == "infoTable":
                    d = {}
                    for ch in el.iter():
                        t = ch.tag.split("}")[-1]
                        if t in ("nameOfIssuer", "cusip", "value", "putCall", "sshPrnamt"):
                            d[t] = (ch.text or "").strip()
                    if d.get("cusip"):
                        rows.append(d)
        except Exception:
            continue
    return rows


def _edgar_build_history(cik, fund, n=12, force=False):
    """Fetch + parse + store the last n 13F-HR holdings for a CIK. Returns quarters stored."""
    import time as _t
    conn = get_conn()
    try:
        conn.execute(_EDGAR_DDL); conn.commit()
        have = set() if force else {x[0] for x in conn.execute(
            "SELECT DISTINCT quarter FROM edgar_13f WHERE cik=?", (str(cik),)).fetchall()}
        stored = 0
        for f in _edgar_13f_filings(cik, n):
            q = f["report_date"]
            if q in have:
                stored += 1; continue
            rows = _edgar_parse_infotable(cik, f["accession"]); _t.sleep(0.2)
            dollars = f["filing_date"] >= "2023-01-01"   # SEC switched $thousands→dollars in 2023
            recs = []
            for d in rows:
                try:
                    val = float(d.get("value") or 0) * (1 if dollars else 1000)
                except Exception:
                    val = 0.0
                try:
                    sh = float(d.get("sshPrnamt") or 0)
                except Exception:
                    sh = 0.0
                recs.append((str(cik), fund, q, f["filing_date"], d.get("cusip", ""),
                             d.get("nameOfIssuer", ""), sh, val, d.get("putCall", "") or ""))
            if recs:
                conn.execute("DELETE FROM edgar_13f WHERE cik=? AND quarter=?", (str(cik), q))
                conn.executemany("INSERT OR REPLACE INTO edgar_13f VALUES (?,?,?,?,?,?,?,?,?)", recs)
                conn.commit(); stored += 1
        return stored
    finally:
        conn.close()


def _edgar_load(cik):
    conn = get_conn()
    try:
        conn.execute(_EDGAR_DDL); conn.commit()
        return pd.read_sql("SELECT * FROM edgar_13f WHERE cik=?", conn, params=(str(cik),))
    except Exception:
        return pd.DataFrame()
    finally:
        conn.close()


# ── Global Opportunities: curated regions / themes / country sectors (proxy ETFs) ──
_GLOBAL_REGIONS = {
    "🇧🇷 Brazil": ("EWZ", "Brazil large-caps — commodities, banks, cheap valuations", "Brazil investment stocks commodities Lula rates"),
    "🇯🇵 Japan": ("EWJ", "Japan — corporate reform, end of deflation, buybacks", "Japan stocks investment BOJ reform buybacks"),
    "🇮🇳 India": ("INDA", "India — fastest-growing major economy, manufacturing push", "India stocks investment manufacturing growth"),
    "🇨🇳 China": ("FXI", "China — deep value, stimulus-dependent, policy risk", "China stimulus stocks investment property"),
    "🌎 LatAm": ("ILF", "Latin America — commodities + nearshoring beneficiaries", "Latin America investment commodities nearshoring"),
    "🇲🇽 Mexico": ("EWW", "Mexico — #1 nearshoring winner from US-China split", "Mexico nearshoring investment manufacturing"),
    "🇻🇳 Vietnam": ("VNM", "Vietnam — supply-chain shift out of China", "Vietnam manufacturing investment supply chain"),
    "🇰🇷 Korea": ("EWY", "Korea — memory chips, shipbuilding, value-up reform", "South Korea chips shipbuilding investment"),
    "🇹🇼 Taiwan": ("EWT", "Taiwan — the AI semiconductor heart (TSMC)", "Taiwan semiconductor TSMC investment"),
    "🇮🇩 Indonesia": ("EIDO", "Indonesia — nickel/EV supply chain, young demographics", "Indonesia nickel EV investment"),
    "🇸🇦 Gulf/Saudi": ("KSA", "Saudi — giga-projects, diversification from oil", "Saudi Gulf giga project investment"),
    "🇪🇺 Europe": ("VGK", "Europe — defense rearmament, cheap vs US", "Europe stocks defense investment"),
}
_GLOBAL_THEMES = {
    "🌾 Agriculture/Soft": ("DBA", "Farm commodities — food security, weather, EM demand", "farmland agriculture investment food security Brazil"),
    "🚜 Agribusiness": ("MOO", "Fertilizer, equipment, seeds — the farmland value chain", "agribusiness fertilizer farmland investment"),
    "🏞 Farmland REITs": ("LAND", "Direct US farmland (Gladstone) — inflation hedge, rents", "farmland REIT investment Gladstone returns"),
    "☢️ Uranium/Nuclear": ("URA", "Nuclear renaissance to power AI + decarbonization", "uranium nuclear power investment data center"),
    "🔌 Power & Grid (AI)": ("GRID", "Electrification + AI data-center power demand", "AI data center power grid electricity investment"),
    "🥉 Copper/Materials": ("COPX", "Copper — structural deficit, AI + electrification", "copper supply deficit AI investment"),
    "🛡 Defense": ("ITA", "Global rearmament — sustained budget growth", "defense spending rearmament investment"),
    "💧 Water": ("PHO", "Water scarcity + infrastructure renewal", "water infrastructure scarcity investment"),
    "🏗 Reshoring/Infra": ("PAVE", "US reshoring + infrastructure buildout", "US infrastructure reshoring investment"),
    "🔐 Cybersecurity": ("CIBR", "Rising cyber spend, AI-driven threats", "cybersecurity spending investment"),
    "🤖 Robotics/Automation": ("BOTZ", "Automation + physical AI / humanoids", "robotics automation humanoid investment"),
    "🥇 Gold & Miners": ("GDX", "Central-bank buying, debasement hedge", "gold central bank buying investment"),
    "₿ Crypto/Bitcoin": ("IBIT", "Institutional adoption via spot ETFs", "bitcoin crypto institutional ETF investment"),
}
_COUNTRY_SECTORS = {
    "🇺🇸 USA — future sectors": [("AI Semis", "SMH"), ("Power & Nuclear", "URA"), ("Reshoring/Infra", "PAVE"),
                                  ("Defense", "ITA"), ("Biotech", "XBI"), ("Cybersecurity", "CIBR")],
    "🇮🇳 India — future sectors": [("Broad India", "INDA"), ("India Small-Cap", "SMIN"), ("Earnings-weighted", "EPI"),
                                   ("Infosys (IT)", "INFY"), ("ICICI Bank", "IBN"), ("HDFC Bank", "HDB")],
    "🌏 Other EM — themes": [("Mexico nearshoring", "EWW"), ("Vietnam mfg", "VNM"),
                             ("Indonesia nickel", "EIDO"), ("Saudi giga-projects", "KSA")],
}


@st.cache_data(ttl=1800, show_spinner=False)
def _global_prices():
    """Batch-download ~9mo of closes for all global proxy tickers (+SPY). One network call."""
    tks = {"SPY"}
    for d in (_GLOBAL_REGIONS, _GLOBAL_THEMES):
        for v in d.values():
            tks.add(v[0])
    for arr in _COUNTRY_SECTORS.values():
        for _, t in arr:
            tks.add(t)
    tks = sorted(tks)
    try:
        data = yf.download(tks, period="9mo", interval="1d", auto_adjust=True,
                           progress=False, threads=True, group_by="ticker")
    except Exception:
        return {}
    out = {}
    for t in tks:
        try:
            s = (data[t]["Close"] if len(tks) > 1 else data["Close"]).dropna()
            if len(s) > 20:
                out[t] = s
        except Exception:
            continue
    return out


def _flow_signal(tk, prices):
    """Money-flow proxy: momentum (3m/6m) + relative strength vs SPY + 52w-high proximity."""
    s = prices.get(tk); spy = prices.get("SPY")
    if s is None or len(s) < 40:
        return None
    px = float(s.iloc[-1])

    def _ret(n):
        return (px / float(s.iloc[-n]) - 1) * 100 if len(s) > n else 0.0

    r1, r3, r6 = _ret(21), _ret(63), _ret(126)
    near = px / float(s.max()) * 100
    rs = 0.0
    if spy is not None and len(spy) > 63:
        rs = r3 - (float(spy.iloc[-1]) / float(spy.iloc[-63]) - 1) * 100
    score = (1 if r3 > 0 else -1) + (1 if r6 > 0 else -1) + (1 if rs > 0 else -1) \
            + (1 if near > 90 else (-1 if near < 75 else 0))
    label = "🟢 Inflow" if score >= 2 else "🔴 Outflow" if score <= -2 else "🟡 Neutral"
    return {"px": px, "r1": r1, "r3": r3, "r6": r6, "rs": rs, "near": near, "label": label, "score": score}


@st.cache_data(ttl=3600, show_spinner=False)
def _theme_news(query, n=3):
    """Live thematic headlines (Google News RSS) with context-aware tone."""
    try:
        import feedparser, urllib.parse as _u, html as _h, time as _t
        fp = feedparser.parse("https://news.google.com/rss/search?q=" + _u.quote(query) +
                              "&hl=en-US&gl=US&ceid=US:en")
        out = []
        for e in fp.entries[:n]:
            title = _h.unescape(e.get("title", "")).strip()
            pp = e.get("published_parsed", None)
            out.append({"title": title, "link": e.get("link", ""),
                        "tone": _headline_tone(title),
                        "when": _t.strftime("%d%b", pp) if pp else ""})
        return out
    except Exception:
        return []


def _render_global_card(label, proxy, thesis, query, prices, expanded=False):
    sig = _flow_signal(proxy, prices)
    _hdr = f"{label} · {proxy}"
    if sig:
        _hdr += f" · {sig['label']} · 3m {sig['r3']:+.0f}%"
    with st.expander(_hdr, expanded=expanded):
        if sig:
            _c = st.columns(4)
            _c[0].metric("Price", f"${sig['px']:.2f}", f"1m {sig['r1']:+.0f}%")
            _c[1].metric("3m / 6m", f"{sig['r3']:+.0f}% / {sig['r6']:+.0f}%")
            _c[2].metric("vs SPY (3m)", f"{sig['rs']:+.0f}%",
                         "leading" if sig['rs'] > 0 else "lagging",
                         delta_color="normal" if sig['rs'] > 0 else "inverse")
            _c[3].metric("% of 52w high", f"{sig['near']:.0f}%")
        st.caption("💡 " + thesis)
        for it in _theme_news(query):
            e = "🟢" if it["tone"] > 0 else ("🔴" if it["tone"] < 0 else "⚪")
            st.markdown(f"- {e} [{it['title']}]({it['link']}) · _{it['when']}_")


@st.cache_data(ttl=86400, show_spinner=False)
def _shares_outstanding(ticker):
    """Shares outstanding via yfinance (for % of company). Cached 24h. None on failure."""
    try:
        info = yf.Ticker(ticker).get_info()
        so = info.get("sharesOutstanding") or info.get("impliedSharesOutstanding")
        return float(so) if so else None
    except Exception:
        return None


@st.cache_data(ttl=900, show_spinner=False)
def _iv_rank(ticker, r=0.045):
    """ATM implied-vol rank/percentile over the stored ~6-month premium history.
    Returns dict(iv, rank, pct, lo, hi, n) or None. Built entirely from the DB."""
    conn = get_conn()
    try:
        df = pd.read_sql(
            "SELECT trade_date_now, strike, expiry_date, lastPrice_Call_now FROM options_change "
            "WHERE UPPER(ticker)=?", conn, params=(ticker.upper(),))
        sd = pd.read_sql("SELECT trade_date, close FROM stock_daily WHERE UPPER(ticker)=?",
                         conn, params=(ticker.upper(),))
    finally:
        conn.close()
    if df.empty or sd.empty:
        return None
    spot_by = {str(d): float(c) for d, c in zip(sd["trade_date"], sd["close"])}

    def _pd_(x):
        for fmt in ("%m-%d-%Y", "%Y-%m-%d"):
            try:
                return datetime.strptime(str(x), fmt)
            except Exception:
                pass
        return None

    ivs = []
    for d, g in df.groupby("trade_date_now"):
        spot = spot_by.get(str(d))
        dd = _pd_(d)
        if not spot or dd is None:
            continue
        best = None
        for _, row in g.iterrows():
            ed = _pd_(row["expiry_date"])
            if ed is None:
                continue
            dte = (ed - dd).days
            if dte < 10 or dte > 70:
                continue
            prem = float(row["lastPrice_Call_now"] or 0)
            if prem <= 0:
                continue
            dist = abs(float(row["strike"]) - spot)
            if best is None or dist < best[0]:
                best = (dist, float(row["strike"]), prem, dte)
        if best:
            _, K, prem, dte = best
            iv = _implied_vol(prem, spot, K, dte / 365.0, r, "call")
            if 0.01 < iv < 5:
                ivs.append(iv)
    if len(ivs) < 10:
        return None
    cur, lo, hi = ivs[-1], min(ivs), max(ivs)
    rank = (cur - lo) / (hi - lo) * 100 if hi > lo else 50.0
    pct = sum(1 for v in ivs if v <= cur) / len(ivs) * 100
    return {"iv": cur, "rank": rank, "pct": pct, "lo": lo, "hi": hi, "n": len(ivs)}


@st.cache_data(ttl=3600, show_spinner=False)
def _next_earnings(ticker):
    """Next earnings date + days away via yfinance. Returns dict(date, days) or None."""
    try:
        import pandas as _pd
        t = yf.Ticker(ticker)
        now = _pd.Timestamp.now().normalize()
        dts = []
        try:
            ed = t.get_earnings_dates(limit=12)
            if ed is not None and len(ed):
                for ix in ed.index:
                    ts = _pd.Timestamp(ix)
                    ts = ts.tz_localize(None) if ts.tzinfo else ts
                    dts.append(ts.normalize())
        except Exception:
            pass
        if not dts:
            try:
                cal = t.calendar
                e = cal.get("Earnings Date") if isinstance(cal, dict) else None
                for x in (e if isinstance(e, (list, tuple)) else [e]) if e else []:
                    dts.append(_pd.Timestamp(x).normalize())
            except Exception:
                pass
        fut = sorted(d for d in dts if d >= now)
        if fut:
            return {"date": fut[0].strftime("%b %d"), "days": int((fut[0] - now).days)}
    except Exception:
        pass
    return None


def _roll_suggestion(conn, leg):
    """Suggest a concrete roll for a flagged leg: target expiry/strike + est. credit/debit
    from the stored chain. Returns a short string or None."""
    try:
        tk, typ, K, side, spot, cur = (leg["ticker"], leg["typ"], leg["K"],
                                       leg["side"], leg["spot"], leg["cur"])
        last_col = "lastPrice_Call_now" if typ == "call" else "lastPrice_Put_now"
        ch = pd.read_sql(
            f"SELECT expiry_date, strike, {last_col} AS last FROM options_change WHERE UPPER(ticker)=?",
            conn, params=(tk.upper(),))
        if ch.empty:
            return None
        today = datetime.now()
        # target expiry: nearest with 25-45 DTE (further out than the current leg)
        exps = {}
        for _, rr in ch.iterrows():
            for fmt in ("%m-%d-%Y", "%Y-%m-%d"):
                try:
                    ed = datetime.strptime(str(rr["expiry_date"]), fmt); break
                except Exception:
                    ed = None
            if ed is None:
                continue
            dte = (ed - today).days
            if 25 <= dte <= 50:
                exps.setdefault(str(rr["expiry_date"]), dte)
        if not exps:
            return None
        tgt_exp = min(exps, key=lambda e: abs(exps[e] - 35))
        # target strike: short call ITM → roll up to ~3% OTM; else keep same strike (calendar)
        tgt_K = K
        if side == "short" and typ == "call" and spot > K:
            tgt_K = round(spot * 1.03)
        sub = ch[(ch["expiry_date"] == tgt_exp)].copy()
        sub["d"] = (sub["strike"] - tgt_K).abs()
        sub = sub[sub["last"].astype(float) > 0].sort_values("d")
        if sub.empty:
            return None
        row = sub.iloc[0]
        tgt_prem = float(row["last"]); tgt_K = float(row["strike"])
        net = tgt_prem - cur                      # >0 credit for shorts / extra debit for longs
        if side == "short":
            cost = f"~${net:.2f} credit" if net > 0 else f"~${-net:.2f} debit"
        else:
            cost = f"~${net:.2f} debit" if net > 0 else f"~${-net:.2f} credit"
        return f"roll → {tgt_exp[:10]} ${tgt_K:.0f}{typ[0].upper()} ({cost})"
    except Exception:
        return None


def _portfolio_var(legs, r=0.045, lookback=60, conf=0.05):
    """Historical 1-day VaR: replay each underlying's recent daily returns through the book
    (BS reprice, 1 day decay). Returns dict(var, cvar, worst, best, n) in $ or None."""
    conn = get_conn()
    try:
        rets = {}
        for tk in {l["ticker"] for l in legs}:
            sd = pd.read_sql(
                "SELECT close FROM stock_daily WHERE UPPER(ticker)=? ORDER BY "
                "substr(trade_date,7,4)||substr(trade_date,1,2)||substr(trade_date,4,2) DESC LIMIT ?",
                conn, params=(tk.upper(), lookback + 1))
            c = list(sd["close"].astype(float))[::-1]
            rets[tk] = [c[i] / c[i - 1] - 1 for i in range(1, len(c))] if len(c) > 2 else []
    finally:
        conn.close()
    n = min((len(v) for v in rets.values() if v), default=0)
    if n < 20:
        return None
    pnls = []
    for i in range(n):
        tot = 0.0
        for l in legs:
            s = rets[l["ticker"]][i]
            ns = l["spot"] * (1 + s); t1 = max(l["dte"] - 1, 0) / 365.0
            ivs = max(l["iv"] * (1 - 2.0 * s), 0.05)
            npx = bs_greeks(ns, l["K"], t1, r, ivs, l["typ"]).get("price", l["cur"]) if t1 > 0 \
                else (max(ns - l["K"], 0) if l["typ"] == "call" else max(l["K"] - ns, 0))
            tot += (npx - l["cur"]) * l["m"]
        pnls.append(tot)
    pnls.sort()
    k = max(0, int(conf * len(pnls)) - 1)
    var = pnls[k]
    tail = [p for p in pnls if p <= var]
    cvar = sum(tail) / len(tail) if tail else var
    return {"var": var, "cvar": cvar, "worst": pnls[0], "best": pnls[-1], "n": len(pnls)}


# ===================================================================
# ──  OI WEEKLY COMPARISON  (same-week strike analysis)
# ===================================================================
def oi_weekly_strike_analysis(ticker, td1, td2):
    """
    Compare OI at same strikes between two dates.
    Classify each strike as: ACCUMULATION, LIQUIDATION, HEDGE, ROLL, STABLE.
    Infer whether activity is buy, sell, or hedge.
    """
    df1 = q("SELECT strike, expiry_date, openInt_Call_now as c_oi_1, openInt_Put_now as p_oi_1, "
            "vol_Call_now as c_vol_1, vol_Put_now as p_vol_1, "
            "lastPrice_Call_now as c_px_1, lastPrice_Put_now as p_px_1 "
            "FROM options_change WHERE ticker=? AND trade_date_now=?", [ticker, td1])
    df2 = q("SELECT strike, expiry_date, openInt_Call_now as c_oi_2, openInt_Put_now as p_oi_2, "
            "vol_Call_now as c_vol_2, vol_Put_now as p_vol_2, "
            "lastPrice_Call_now as c_px_2, lastPrice_Put_now as p_px_2 "
            "FROM options_change WHERE ticker=? AND trade_date_now=?", [ticker, td2])

    if df1.empty or df2.empty:
        return pd.DataFrame()

    merged = df1.merge(df2, on=["strike", "expiry_date"], how="outer").fillna(0)
    merged["c_oi_chg"] = merged["c_oi_2"] - merged["c_oi_1"]
    merged["p_oi_chg"] = merged["p_oi_2"] - merged["p_oi_1"]
    merged["c_oi_pct"] = np.where(merged["c_oi_1"] > 0, merged["c_oi_chg"] / merged["c_oi_1"] * 100, 0)
    merged["p_oi_pct"] = np.where(merged["p_oi_1"] > 0, merged["p_oi_chg"] / merged["p_oi_1"] * 100, 0)
    merged["c_px_chg"] = merged["c_px_2"] - merged["c_px_1"]
    merged["p_px_chg"] = merged["p_px_2"] - merged["p_px_1"]

    def classify(r):
        c, p = r["c_oi_chg"], r["p_oi_chg"]
        c_px, p_px = r["c_px_chg"], r["p_px_chg"]

        # Accumulation: large OI increase + price up → buying
        if c > 200 and c_px > 0:
            return "CALL ACCUMULATION (BUY)"
        if p > 200 and p_px > 0:
            return "PUT ACCUMULATION (BUY)"
        # Writing: OI increase + price down → selling premium
        if c > 200 and c_px <= 0:
            return "CALL WRITING (SELL)"
        if p > 200 and p_px <= 0:
            return "PUT WRITING (SELL)"
        # Liquidation
        if c < -200:
            return "CALL LIQUIDATION"
        if p < -200:
            return "PUT LIQUIDATION"
        # Hedge: both sides increase
        if c > 100 and p > 100:
            return "HEDGE / STRADDLE"
        # Roll: one side drops, other rises
        if c > 100 and p < -100:
            return "ROLL → CALLS (bullish)"
        if p > 100 and c < -100:
            return "ROLL → PUTS (bearish)"
        return "STABLE"

    merged["classification"] = merged.apply(classify, axis=1)

    # Escape ease score (liquidity/ability to exit)
    merged["escape_score"] = np.clip(
        (merged["c_vol_2"] + merged["p_vol_2"]) /
        (merged["c_oi_2"].clip(1) + merged["p_oi_2"].clip(1)) * 100, 0, 100
    ).round(1)
    merged["escape_label"] = np.where(merged["escape_score"] > 50, "Easy Exit",
                             np.where(merged["escape_score"] > 20, "Moderate", "Difficult"))
    return merged.sort_values("c_oi_chg", ascending=False, key=abs)


# ===================================================================
# ──  LOSS PREDICTION ENGINE
# ===================================================================
def predict_trade_risk(ticker, option_type, strike, expiry_str, entry_price, qty=1):
    """
    Before-trade risk analysis: predict max loss scenarios,
    probability of loss, escape difficulty, and OI-based crowd positioning.
    """
    try:
        stock = yf.Ticker(ticker)
        hist = stock.history(period="3mo")
        if hist.empty:
            return None
        S = float(hist["Close"].iloc[-1])
        # Realized vol
        returns = hist["Close"].pct_change().dropna()
        sigma = float(returns.std() * np.sqrt(252))
        avg_daily = float(returns.mean())
        daily_vol = float(returns.std())

        exp_dt = datetime.strptime(expiry_str, "%Y-%m-%d")
        T = max((exp_dt - datetime.now()).days, 1) / 365
        r = 0.045

        greeks = bs_greeks(S, strike, T, r, sigma, option_type.lower())

        # 1-day, 3-day, 5-day scenario analysis
        scenarios = []
        for days, label in [(1, "1-Day"), (3, "3-Day"), (5, "5-Day")]:
            for move_pct, move_label in [(-3, "Sharp Down"), (-1.5, "Moderate Down"),
                                         (0, "Flat"), (1.5, "Moderate Up"), (3, "Sharp Up")]:
                new_S = S * (1 + move_pct / 100)
                new_T = max(T - days / 365, 0.001)
                new_greeks = bs_greeks(new_S, strike, new_T, r, sigma, option_type.lower())
                pnl = (new_greeks["price"] - greeks["price"]) * qty * 100
                pnl_pct = (new_greeks["price"] - greeks["price"]) / greeks["price"] * 100 if greeks["price"] > 0 else 0
                scenarios.append(dict(
                    timeframe=label, scenario=move_label,
                    stock_move=f"{move_pct:+.1f}%",
                    new_price=round(new_greeks["price"], 2),
                    pnl=round(pnl, 2), pnl_pct=round(pnl_pct, 1),
                ))

        # Max loss
        max_loss = -entry_price * qty * 100  # Total premium paid

        # Probability of profit (simplified: prob stock > strike for call)
        d2 = (np.log(S / strike) + (r - 0.5 * sigma**2) * T) / (sigma * np.sqrt(T))
        prob_itm = norm.cdf(d2) if option_type.lower() == "call" else norm.cdf(-d2)

        # OI-based crowd positioning
        conn = get_conn()
        oi_data = pd.read_sql(
            "SELECT openInt_Call_now, openInt_Put_now, change_OI_Call, change_OI_Put "
            "FROM options_change WHERE ticker=? AND strike=? "
            "LIMIT 1",
            conn, params=[ticker, strike])
        conn.close()

        crowd = {}
        if not oi_data.empty:
            r_data = oi_data.iloc[0]
            crowd["call_oi"] = int(r_data.get("openInt_Call_now", 0) or 0)
            crowd["put_oi"] = int(r_data.get("openInt_Put_now", 0) or 0)
            crowd["call_oi_chg"] = int(r_data.get("change_OI_Call", 0) or 0)
            crowd["put_oi_chg"] = int(r_data.get("change_OI_Put", 0) or 0)
            pcr = crowd["put_oi"] / crowd["call_oi"] if crowd["call_oi"] > 0 else 0
            crowd["pcr"] = round(pcr, 2)
            crowd["crowd_bias"] = "BEARISH" if pcr > 1.2 else ("BULLISH" if pcr < 0.7 else "NEUTRAL")
        else:
            crowd = {"call_oi": 0, "put_oi": 0, "pcr": 0, "crowd_bias": "NO DATA"}

        # Escape difficulty
        escape = "EASY" if greeks.get("gamma", 0) > 0.01 and crowd.get("call_oi", 0) > 500 else \
                 "MODERATE" if crowd.get("call_oi", 0) > 100 else "DIFFICULT"

        return dict(
            current_price=S, strike=strike, theo_price=round(greeks["price"], 2),
            greeks=greeks, sigma=round(sigma * 100, 1),
            max_loss=round(max_loss, 2),
            prob_itm=round(prob_itm * 100, 1),
            scenarios=pd.DataFrame(scenarios),
            crowd=crowd, escape=escape,
            daily_vol_pct=round(daily_vol * 100, 2),
        )
    except Exception as e:
        return {"error": str(e)}


# ===================================================================
# ──  PROP SCREEN  (opportunity scanner)
# ===================================================================
def scan_prop_opportunities(trade_date=None, min_z=1.5):
    """
    Scan for institutional-grade trade opportunities.
    Returns top setups with risk/reward, OI signals, and escape analysis.
    """
    dates = available_trade_dates()
    if not dates:
        return pd.DataFrame()
    td = trade_date or dates[0]
    df = load_oi_for_date(td)
    if df.empty:
        return pd.DataFrame()

    anom = oi_anomalies(df, z_thresh=min_z)
    top = anom[anom["max_z"] >= min_z].head(15)

    opps = []
    for _, tk in top.iterrows():
        ticker = tk["ticker"]
        tk_df = df[df["ticker"] == ticker].copy()
        if tk_df.empty:
            continue

        # Apply pressure inference to top 5 strikes
        tk_df["abs_oi"] = tk_df["change_OI_Call"].abs() + tk_df["change_OI_Put"].abs()
        top_strikes = tk_df.nlargest(5, "abs_oi")

        for _, row in top_strikes.iterrows():
            press = infer_pressure(row)
            strike = row.get("strike", 0)
            expiry = row.get("expiry_date", "")
            c_oi = row.get("openInt_Call_now", 0) or 0
            p_oi = row.get("openInt_Put_now", 0) or 0
            c_vol = row.get("vol_Call_now", 0) or 0
            p_vol = row.get("vol_Put_now", 0) or 0

            # Escape score
            total_vol = c_vol + p_vol
            total_oi = max(c_oi + p_oi, 1)
            escape = min(total_vol / total_oi * 100, 100)

            opps.append(dict(
                Ticker=ticker, Strike=strike, Expiry=expiry,
                Call_OI_Chg=row.get("change_OI_Call", 0),
                Put_OI_Chg=row.get("change_OI_Put", 0),
                Call_Signal=press["call_signal"],
                Put_Signal=press["put_signal"],
                Net_View=press["net_view"],
                Z_Score=round(tk["max_z"], 2),
                PCR=round(p_oi / c_oi, 2) if c_oi > 0 else 0,
                Vol_OI_Ratio=round(escape, 1),
                Escape=("Easy" if escape > 50 else "Moderate" if escape > 20 else "Hard"),
            ))

    return pd.DataFrame(opps)


# ===================================================================
# ──  BACKTEST ENGINE
# ===================================================================
def backtest_oi_signals(ticker, lookback=10):
    """
    Backtest OI-based signals against actual next-day stock moves.
    Returns accuracy metrics and day-by-day results.
    """
    conn = get_conn()
    raw_dates = pd.read_sql(
        "SELECT DISTINCT trade_date_now FROM options_change WHERE ticker=?",
        conn, params=[ticker])["trade_date_now"].tolist()
    dates = _sort_dates_chrono(raw_dates, descending=True)[:lookback + 1]
    stock_raw = pd.read_sql("SELECT * FROM stock_daily WHERE ticker=?", conn, params=[ticker])
    conn.close()
    # Sort stock data chronologically
    if not stock_raw.empty:
        stock_raw["_dt"] = pd.to_datetime(stock_raw["trade_date"], format="%m-%d-%Y", errors="coerce")
        stock = stock_raw.sort_values("_dt", ascending=False).drop(columns=["_dt"])
    else:
        stock = stock_raw

    if len(dates) < 3 or stock.empty:
        return None

    results = []
    for i in range(len(dates) - 1):
        td = dates[i]
        td_next = dates[i + 1] if i + 1 < len(dates) else None
        day_data = q("SELECT change_OI_Call, change_OI_Put, openInt_Call_now, openInt_Put_now, "
                     "vol_Call_now, vol_Put_now FROM options_change WHERE ticker=? AND trade_date_now=?",
                     [ticker, td])
        if day_data.empty:
            continue

        c_chg = day_data["change_OI_Call"].sum()
        p_chg = day_data["change_OI_Put"].sum()
        c_oi = day_data["openInt_Call_now"].sum()
        p_oi = day_data["openInt_Put_now"].sum()
        pcr = p_oi / c_oi if c_oi > 0 else 0
        net_bias = c_chg - p_chg
        signal = "BULLISH" if net_bias > 0 and pcr < 1.0 else ("BEARISH" if net_bias < 0 and pcr > 1.0 else "NEUTRAL")

        # Match stock price
        stock_row = stock[stock["trade_date"] == td]
        stock_next = stock[stock["trade_date"] == td_next] if td_next else pd.DataFrame()
        close_today = float(stock_row["close"].iloc[0]) if not stock_row.empty else None
        close_next = float(stock_next["close"].iloc[0]) if not stock_next.empty else None
        actual_move = ((close_next - close_today) / close_today * 100) if close_today and close_next else None

        correct = None
        if actual_move is not None and signal != "NEUTRAL":
            correct = (signal == "BULLISH" and actual_move > 0) or (signal == "BEARISH" and actual_move < 0)

        results.append(dict(
            date=td, signal=signal, net_oi_bias=net_bias, pcr=round(pcr, 2),
            stock_close=close_today,
            next_day_move=round(actual_move, 2) if actual_move is not None else None,
            correct=correct,
        ))

    res_df = pd.DataFrame(results)
    if not res_df.empty and "correct" in res_df.columns:
        valid = res_df.dropna(subset=["correct"])
        accuracy = valid["correct"].sum() / len(valid) * 100 if len(valid) > 0 else None
    else:
        accuracy = None
    return res_df, accuracy


# ===================================================================
# ──  OI COMPARISON & ADVANCED ANALYSIS HELPERS
# ===================================================================

def compact_price(v):
    """Format price compactly: 0.70 → .7, 1.50 → 1.5, 12.00 → 12"""
    if pd.isna(v) or not np.isfinite(v):
        return ""
    av = abs(v)
    sign = "-" if v < 0 else ""
    if av == 0:
        return "0"
    if av < 0.01:
        return f"{sign}{av:.3f}".rstrip('0').rstrip('.')
    s = f"{av:.2f}".rstrip('0').rstrip('.')
    if s.startswith('0.'):
        s = s[1:]  # 0.7 → .7
    return sign + s


def load_oi_for_two_dates(ticker, td_now, td_prev):
    """Load strike-level OI data for two dates from options_change & options_daily."""
    df_now = q(
        "SELECT ticker, expiry_date, strike, openInt_Call_now, openInt_Put_now, "
        "change_OI_Call, change_OI_Put, vol_Call_now, vol_Put_now, "
        "lastPrice_Call_now, lastPrice_Put_now, "
        "call_open_now, call_high_now, call_low_now, call_close_now, "
        "put_open_now, put_high_now, put_low_now, put_close_now "
        "FROM options_change WHERE ticker=? AND trade_date_now=?",
        [ticker, td_now],
    )
    df_prev = q(
        "SELECT ticker, expiry_date, strike, openInt_Call_now AS openInt_Call_prev, "
        "openInt_Put_now AS openInt_Put_prev, "
        "lastPrice_Call_now AS lastPrice_Call_prev, lastPrice_Put_now AS lastPrice_Put_prev, "
        "call_close_now AS call_close_prev, put_close_now AS put_close_prev, "
        "vol_Call_now AS vol_Call_prev, vol_Put_now AS vol_Put_prev "
        "FROM options_change WHERE ticker=? AND trade_date_now=?",
        [ticker, td_prev],
    )
    if df_now.empty:
        return None
    key = ["ticker", "expiry_date", "strike"]
    for d in [df_now, df_prev]:
        d["strike"] = pd.to_numeric(d["strike"], errors="coerce")
    df = df_now.merge(df_prev[key + [
        "openInt_Call_prev", "openInt_Put_prev",
        "lastPrice_Call_prev", "lastPrice_Put_prev",
        "call_close_prev", "put_close_prev",
        "vol_Call_prev", "vol_Put_prev",
    ]], on=key, how="left")
    num_cols = [
        "openInt_Call_now", "openInt_Put_now", "openInt_Call_prev", "openInt_Put_prev",
        "change_OI_Call", "change_OI_Put", "vol_Call_now", "vol_Put_now",
        "vol_Call_prev", "vol_Put_prev",
        "lastPrice_Call_now", "lastPrice_Put_now",
        "lastPrice_Call_prev", "lastPrice_Put_prev",
        "call_close_now", "call_close_prev", "put_close_now", "put_close_prev",
        "call_open_now", "call_high_now", "call_low_now",
        "put_open_now", "put_high_now", "put_low_now",
    ]
    for c in num_cols:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0)
    df = df.dropna(subset=["strike"]).sort_values("strike").reset_index(drop=True)
    return df


def advanced_oi_analysis(df, ticker, spot, td_now, td_prev):
    """Deep analysis of OI + Price + Volume producing actionable conclusions."""
    findings = []
    if df is None or df.empty or spot is None:
        return findings

    total_call_oi = df["openInt_Call_now"].sum()
    total_put_oi = df["openInt_Put_now"].sum()
    pcr = total_put_oi / total_call_oi if total_call_oi > 0 else 0

    # 1) Max Pain
    strikes = df["strike"].unique()
    pain = {}
    for k in strikes:
        call_pain = df.loc[df["strike"] < k, "openInt_Call_now"].sum() * 0  # ITM calls = stock > strike
        put_pain = 0
        for _, r in df.iterrows():
            if k > r["strike"]:
                call_pain += r["openInt_Call_now"] * (k - r["strike"])
            if k < r["strike"]:
                put_pain += r["openInt_Put_now"] * (r["strike"] - k)
        pain[k] = call_pain + put_pain
    if pain:
        max_pain_strike = min(pain, key=pain.get)
        dist_pct = (spot - max_pain_strike) / spot * 100 if spot else 0
        findings.append({
            "category": "🎯 Max Pain",
            "signal": f"${max_pain_strike:.0f}",
            "detail": f"Stock at ${spot:.2f} is {abs(dist_pct):.1f}% {'above' if dist_pct > 0 else 'below'} max pain. "
                      f"{'Gravitational pull DOWN — hedging activity likely.' if dist_pct > 2 else 'Gravitational pull UP.' if dist_pct < -2 else 'Near max pain — expect pin/range-bound.'}",
            "impact": "BEARISH" if dist_pct > 3 else "BULLISH" if dist_pct < -3 else "NEUTRAL",
        })

    # 2) OI Walls (support/resistance)
    call_wall = df.nlargest(3, "openInt_Call_now")[["strike", "openInt_Call_now"]]
    put_wall = df.nlargest(3, "openInt_Put_now")[["strike", "openInt_Put_now"]]
    call_wall_strikes = call_wall["strike"].tolist()
    put_wall_strikes = put_wall["strike"].tolist()
    findings.append({
        "category": "🧱 OI Walls",
        "signal": f"R: {', '.join(f'${s:.0f}' for s in call_wall_strikes)} | S: {', '.join(f'${s:.0f}' for s in put_wall_strikes)}",
        "detail": f"Resistance (heavy call OI) at {call_wall_strikes[0]:.0f}. Support (heavy put OI) at {put_wall_strikes[0]:.0f}. "
                  f"Range: ${put_wall_strikes[0]:.0f}-${call_wall_strikes[0]:.0f}.",
        "impact": "NEUTRAL",
    })

    # 3) GEX (Gamma Exposure estimate)
    # Simplified: positive GEX = dealers short calls, they buy dips / sell rips → dampens moves
    df["call_gex"] = df["openInt_Call_now"] * df["strike"] * 0.01  # proxy
    df["put_gex"] = -df["openInt_Put_now"] * df["strike"] * 0.01
    net_gex = df["call_gex"].sum() + df["put_gex"].sum()
    gex_label = "POSITIVE (dealers dampen moves → low vol)" if net_gex > 0 else "NEGATIVE (dealers amplify moves → high vol)"
    findings.append({
        "category": "⚡ Gamma Exposure",
        "signal": f"Net GEX: {'+'if net_gex>0 else ''}{net_gex/1e6:.1f}M",
        "detail": gex_label + ". " + ("Market likely to stay range-bound." if net_gex > 0 else "Expect larger swings; breakout potential."),
        "impact": "NEUTRAL" if net_gex > 0 else "VOLATILE",
    })

    # 4) OI Change Analysis — hedge-aware intent algo
    call_oi_chg = df["change_OI_Call"].sum()
    put_oi_chg  = df["change_OI_Put"].sum()
    if "strike" in df.columns and len(df) >= 2 and spot:
        _df2 = df.rename(columns={"change_OI_Call": "call_oi_change", "change_OI_Put": "put_oi_change"})
        _, _isig, _isc, _idesc, _idet = _oi_intent_algo(_df2, spot)
        h_pct = _idet.get("hedge_pct", 0)
        flow   = _idesc + (f"  (Hedge% of puts: {h_pct:.0f}%)" if h_pct > 20 else "")
        impact = _isig.replace(" ", "_").upper()
        if "BULL" in impact:  impact = "BULLISH"
        elif "BEAR" in impact: impact = "BEARISH"
        elif "HEDGE" == _isig: impact = "NEUTRAL"
        else:                  impact = "NEUTRAL"
    else:
        _sig_l, _ = _oi_signal_light(call_oi_chg, put_oi_chg, pcr)
        flow, impact = (_sig_l + " — " +
                        ("Call accumulation" if _sig_l == "BULLISH" else
                         "Put accumulation (directional)" if _sig_l == "BEARISH" else
                         "Institutional hedge — NOT directional" if _sig_l == "HEDGE" else
                         "Vol play / event" if _sig_l == "STRADDLE" else
                         "Position unwinding" if _sig_l == "UNWIND" else "Balanced")), \
                       ("BULLISH" if _sig_l in ("BULLISH","BULL+HEDGE","HEDGED BULL")
                        else "BEARISH" if _sig_l == "BEARISH" else "NEUTRAL")
    findings.append({
        "category": "📊 OI Flow",
        "signal": f"Call ΔOI: {call_oi_chg:+,.0f} | Put ΔOI: {put_oi_chg:+,.0f}",
        "detail": flow,
        "impact": impact,
    })

    # 5) Volume vs OI ratio (smart money vs retail)
    call_vol = df["vol_Call_now"].sum()
    put_vol = df["vol_Put_now"].sum()
    vol_oi_call = call_vol / total_call_oi if total_call_oi > 0 else 0
    vol_oi_put = put_vol / total_put_oi if total_put_oi > 0 else 0
    if vol_oi_call > 0.5 or vol_oi_put > 0.5:
        smart = "HIGH vol/OI ratio → New positions being opened aggressively (likely institutional)."
    elif vol_oi_call < 0.1 and vol_oi_put < 0.1:
        smart = "LOW vol/OI ratio → Existing positions held, no new conviction. Wait for catalyst."
    else:
        smart = "Moderate vol/OI. Mix of position maintenance and new entries."
    findings.append({
        "category": "🔍 Smart Money",
        "signal": f"Call V/OI: {vol_oi_call:.2f} | Put V/OI: {vol_oi_put:.2f}",
        "detail": smart,
        "impact": "BULLISH" if vol_oi_call > vol_oi_put * 1.5 else "BEARISH" if vol_oi_put > vol_oi_call * 1.5 else "NEUTRAL",
    })

    # 6) PCR analysis
    pcr_prev_call = df["openInt_Call_prev"].sum()
    pcr_prev_put = df["openInt_Put_prev"].sum()
    pcr_prev = pcr_prev_put / pcr_prev_call if pcr_prev_call > 0 else 0
    pcr_delta = pcr - pcr_prev
    pcr_txt = f"PCR {pcr:.2f} (prev {pcr_prev:.2f}, Δ{pcr_delta:+.2f}). "
    if pcr > 1.3:
        pcr_txt += "Elevated put activity — market fearful or hedging. Contrarian BULLISH if extreme."
        pcr_impact = "CONTRARIAN_BULL"
    elif pcr < 0.7:
        pcr_txt += "Low put activity — complacency. Contrarian BEARISH watch."
        pcr_impact = "CONTRARIAN_BEAR"
    else:
        pcr_txt += "Balanced positioning."
        pcr_impact = "NEUTRAL"
    findings.append({
        "category": "📈 PCR Shift",
        "signal": f"PCR: {pcr:.2f} → Δ{pcr_delta:+.2f}",
        "detail": pcr_txt,
        "impact": pcr_impact,
    })

    # 7) Price convergence with OI walls
    nearest_call_wall = min(call_wall_strikes, key=lambda x: abs(x - spot)) if call_wall_strikes else spot
    nearest_put_wall = min(put_wall_strikes, key=lambda x: abs(x - spot)) if put_wall_strikes else spot
    if abs(spot - nearest_call_wall) / spot < 0.01:
        findings.append({
            "category": "⚠️ Pin Risk",
            "signal": f"Stock ${spot:.2f} near call wall ${nearest_call_wall:.0f}",
            "detail": "Price pinned near heavy call OI. Dealer hedging creates resistance. Breakout above is significant.",
            "impact": "NEUTRAL",
        })
    if abs(spot - nearest_put_wall) / spot < 0.01:
        findings.append({
            "category": "⚠️ Support Test",
            "signal": f"Stock ${spot:.2f} near put wall ${nearest_put_wall:.0f}",
            "detail": "Price testing heavy put OI support. Break below triggers delta-hedging cascade.",
            "impact": "BEARISH",
        })

    # 8) Overall verdict
    bull_count = sum(1 for f in findings if f["impact"] in ("BULLISH", "CONTRARIAN_BULL"))
    bear_count = sum(1 for f in findings if f["impact"] in ("BEARISH", "CONTRARIAN_BEAR"))
    vol_count = sum(1 for f in findings if f["impact"] == "VOLATILE")
    if bull_count > bear_count and vol_count == 0:
        verdict = "🟢 NET BULLISH — OI flow, PCR, and positioning favor upside."
    elif bear_count > bull_count:
        verdict = "🔴 NET BEARISH — Protective put buying + call unwinding point to downside."
    elif vol_count > 0:
        verdict = "🟡 VOLATILE — Expect a big move. Straddle/strangle setups favorable."
    else:
        verdict = "⚪ NEUTRAL — Mixed signals. Range-bound likely. Sell premium strategies."
    findings.append({
        "category": "🏁 VERDICT",
        "signal": verdict.split(" — ")[0],
        "detail": verdict,
        "impact": "BULLISH" if bull_count > bear_count else "BEARISH" if bear_count > bull_count else "NEUTRAL",
    })

    return findings


# ===================================================================
# ──  SIDEBAR
# ===================================================================
_PAGE_HELP = {
    "🌍 Market Overview":        "Big-picture market snapshot. VIX, sector rotation, Fear & Greed, macro correlations, and top OI movers. Start here every morning.",
    "🔬 OI Comparison Charts":   "Deep-dive OI analysis per ticker and expiry. Compare Open Interest changes between two dates to spot institutional positioning, gamma walls, and money flow direction.",
    "🔥 OI Analytics & Prediction": "AI-style OI signal engine. Runs 5-factor composite scoring (OI bias, PCR, volume, flow pattern, GEX) to generate BULLISH/BEARISH/NEUTRAL signals with next-day backtest.",
    "🎯 Prop Trading Screen":    "Prop-desk style trade ideas. Scans for high-conviction setups using OI, PCR, and momentum filters — shows entry/exit levels and risk:reward.",
    "💼 Portfolio & Suggestions": "Your open and closed positions. Track unrealized P&L, Greeks, IV rank, earnings alerts, and get per-leg roll suggestions. Add/close/edit trades here.",
    "📊 Backtest Lab":           "Test OI-based trading signals against historical data. See what win rate and P&L your strategy would have produced over the selected date range.",
    "🔮 Live Position Predictor": "Monte Carlo simulation for a single position. Models 10,000 price paths to estimate tomorrow's expected P&L, probability of profit, and VaR.",
    "📈 Insider / Congress / Whales": "Track institutional money flows — SEC insider filings, congress trades, and dark pool / block order signals for the stocks you follow.",
    "📰 News & Calendar":        "Economic calendar (FOMC, CPI, NFP, earnings) and live news feed. Know what events could move your positions before they happen.",
    "🧠 Smart Money Hub":        "10-section institutional tracking system: Unusual Options Activity, Dark Pool OI blocks, COT positioning proxy, Dealer GEX, Oil crash signal, Credit lead-lag, VIX term structure, Market regime, and Confluence Score. All computed from your local DB + yfinance.",
    "⚡ Trade Risk Calculator":  "Pre-trade risk sizing tool. Enter entry price, stop loss, and account size to compute the correct number of contracts so you risk no more than 2% of capital.",
    "🎯 Next-Day Exit Planner":  "Pre-market daily brief. Fetches live option mid-prices and tells you which positions to take profit, cut loss, or hold. Run every morning before market open.",
    "🚀 Live Momentum Scanner":  "Intraday momentum scanner. Screens for tickers with unusual volume, OI spikes, or RSI extremes in real time — find the next big mover.",
}

with st.sidebar:
    st.markdown("## 📊 RUDRARJUN")
    st.markdown("##### *Options Intelligence Terminal*")
    st.radio("Theme", ["🌙 Dark", "☀️ Light"], key="ui_theme", horizontal=True,
             label_visibility="collapsed",
             help="Switch between dark fintech and light minimal. Applies on the next interaction.")
    st.markdown("---")

    _NAV_GROUPS = {
        "📈 Markets & OI": [
            "🌍 Market Overview",
            "🔬 OI Comparison Charts",
            "🔥 OI Analytics & Prediction",
        ],
        "💡 Trade Ideas": [
            "🎯 Prop Trading Screen",
            "🧠 High-Prob Engine",
            "🎯 Gamma Wall Advisor",
            "🚀 Live Momentum Scanner",
        ],
        "💼 Portfolio & Risk": [
            "💼 Portfolio & Suggestions",
            "🔮 Live Position Predictor",
            "⚡ Trade Risk Calculator",
            "🎯 Next-Day Exit Planner",
            "📊 Backtest Lab",
        ],
        "🏛 Smart Money & News": [
            "📈 Insider / Congress / Whales",
            "🏆 Legendary Investors (13F)",
            "🧠 Smart Money Hub",
            "📰 News & Calendar",
        ],
        "🌍 Global / Macro": [
            "🌍 Global Opportunities",
            "📡 Macro/Event Hub",
        ],
    }
    _cat = st.selectbox("Section", list(_NAV_GROUPS), key="nav_cat")
    page = st.radio("Navigate", _NAV_GROUPS[_cat], label_visibility="collapsed")

    st.markdown("---")
    # Mini help for current page
    if page in _PAGE_HELP:
        st.info(f"**{page}**\n\n{_PAGE_HELP[page]}")
    st.markdown("---")

    st.markdown("---")
    st.caption(f"Data: Yahoo Finance + Local DB")
    st.caption(f"DB: US_data.db")


# ── Page header helper ──────────────────────────────────────────────
def _page_header(title: str, help_text: str = ""):
    """Render a styled page header with AH toggle top-right and optional help pill."""
    h1, h_ah, h2 = st.columns([4, 2, 1])
    with h1:
        st.markdown(f"<h2>{title}</h2>", unsafe_allow_html=True)
    with h_ah:
        st.toggle(
            "🌙 AH Prices",
            value=st.session_state.get("use_ah", False),
            key="use_ah",
            help="Use After-Hours / Pre-Market price for all spot, premium & P&L calculations across every page.",
        )
        _mode_lbl = "🌙 After-Hours mode" if st.session_state.get("use_ah") else "☀️ EOD close mode"
        st.caption(_mode_lbl)
    if help_text:
        with h2:
            with st.popover("ℹ️ Help"):
                st.markdown(help_text)

# ===================================================================
# ──  PAGE 1: MARKET OVERVIEW
# ===================================================================
if page == "🌍 Market Overview":
    _page_header("🌍 Market Overview", _PAGE_HELP["🌍 Market Overview"])
    # ── Controls row ──
    _ov_c1, _ov_c2, _ov_c3 = st.columns([3, 1, 1])
    with _ov_c1:
        st.markdown("### 🌍 Global Market Overview")
    with _ov_c2:
        auto_ref = st.toggle("⟳ Auto-refresh (60s)", value=False, key="mo_autoref")
    with _ov_c3:
        _force_reload = st.button("🔄 Refresh Now", key="mo_reload")

    if _force_reload:
        st.cache_data.clear()

    with st.spinner("Loading global market data..."):
        snap = fetch_market_snapshot()

    _pulled_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    if snap.empty:
        st.warning("⚠️ Could not load market data — check network / yfinance.")
    else:
        # ── Section ordering ──
        _SECTIONS = [
            ("📈 Indices",    ["S&P 500", "Nasdaq", "Dow Jones", "Russell 2000", "VIX"]),
            ("⚡ Futures",    ["S&P 500 Futures", "Nasdaq 100 Futures", "Dow Jones Futures"]),
            ("🛢️ Commodities", ["Gold", "WTI Oil", "Brent", "Silver", "Nat Gas", "Copper"]),
            ("💱 FX / Crypto", ["EUR/USD", "USD/JPY", "GBP/USD", "Dollar Index", "USD/INR", "Bitcoin", "Ethereum"]),
            ("📋 Bonds",      ["10Y Yield", "30Y Yield"]),
        ]

        def _em(pct):
            return "🟢" if pct > 0.5 else ("🔴" if pct < -0.5 else "🟡")

        def _px_fmt(name, px):
            if "Yield" in name:   return f"{px:.3f}%"
            if "USD/INR" in name: return f"₹{px:,.2f}"
            if px > 999:          return f"{px:,.0f}"
            if px < 10:           return f"{px:.4f}"
            return f"{px:,.2f}"

        for _sec_label, _sec_names in _SECTIONS:
            _sec_rows = snap[snap["Name"].isin(_sec_names)].copy()
            if _sec_rows.empty:
                continue
            st.markdown(f"#### {_sec_label}")
            _rows_list = list(_sec_rows.iterrows())
            # Render in rows of up to 5 cards each
            _chunk_size = 5
            for _chunk_start in range(0, len(_rows_list), _chunk_size):
                _chunk = _rows_list[_chunk_start:_chunk_start + _chunk_size]
                _cols = st.columns(len(_chunk))
                for _ci, (_, _row) in enumerate(_chunk):
                    _n   = _row["Name"]
                    _px  = float(_row["Price"])
                    _pct = float(_row["Pct"])
                    _em_s = _em(_pct)
                    _icon = SYMBOL_ICONS.get(_n, "")
                    _border_color = "#00c853" if _pct >= 0.5 else ("#d32f2f" if _pct < -0.5 else "#ff9100")
                    _px_s = _px_fmt(_n, _px)
                    _cols[_ci].markdown(
                        f"<div style='background:var(--panel-solid);color:var(--text);"
                        f"border:1px solid var(--border);border-left:4px solid {_border_color};"
                        f"border-radius:12px;padding:12px 12px;margin-bottom:6px;'>"
                        f"<div>{_em_s} {_icon} <b>{_n}</b></div>"
                        f"<div>"
                        f"{_px_s} {_pct:+.2f}%</div>"
                        f"</div>",
                        unsafe_allow_html=True,
                    )

        # ── VIX Term Structure ──
        st.markdown("---")
        st.markdown("#### ⚡ VIX Term Structure")
        try:
            _vix_hist  = _cached_history("^VIX",  period="5d", interval="1d")
            _vix3m_hist = _cached_history("^VIX3M", period="5d", interval="1d")
            if len(_vix_hist) >= 1 and len(_vix3m_hist) >= 1:
                _vix_now  = float(_vix_hist["Close"].iloc[-1])
                _vix3m_now = float(_vix3m_hist["Close"].iloc[-1])
                _vts_ratio = _vix_now / _vix3m_now if _vix3m_now > 0 else 1.0
                if _vts_ratio > 1.05:
                    _vts_label = "🔴 BACKWARDATION — Fear spike / short-term stress elevated"
                    _vts_color = "#d32f2f"
                elif _vts_ratio < 0.95:
                    _vts_label = "🟢 CONTANGO — Normal / market calm"
                    _vts_color = "#00c853"
                else:
                    _vts_label = "🟡 FLAT — Transitioning / uncertain"
                    _vts_color = "#e6a800"
                _vtc1, _vtc2, _vtc3, _vtc4 = st.columns(4)
                _vtc1.metric("VIX (spot)", f"{_vix_now:.2f}", help="Short-term fear gauge")
                _vtc2.metric("VIX3M (3-month)", f"{_vix3m_now:.2f}", help="3-month implied vol")
                _vtc3.metric("VIX/VIX3M Ratio", f"{_vts_ratio:.3f}")
                _vtc4.markdown(
                    f"<div>"
                    f"<b>{_vts_label}</b></div>", unsafe_allow_html=True)
        except Exception as _ve:
            st.info(f"VIX term structure unavailable: {_ve}")

        # ── Market Breadth ──
        st.markdown("---")
        st.markdown("#### 📊 Market Breadth (1-day returns)")
        try:
            _breadth_tickers = {"SPY": "Large Cap", "QQQ": "Tech/NDX", "IWM": "Small Cap", "MDY": "Mid Cap"}
            _bc_cols = st.columns(4)
            for _bi, (_bsym, _blabel) in enumerate(_breadth_tickers.items()):
                _bh = _cached_history(_bsym, period="5d", interval="1d")
                if len(_bh) >= 2:
                    _bp = float(_bh["Close"].iloc[-1])
                    _bprev = float(_bh["Close"].iloc[-2])
                    _bpct = (_bp - _bprev) / _bprev * 100
                    _bc_cols[_bi].metric(_blabel, f"${_bp:,.2f}", f"{_bpct:+.2f}%",
                                         delta_color="normal")
        except Exception as _be:
            st.info(f"Market breadth unavailable: {_be}")

        # ── Sector Rotation ──
        st.markdown("---")
        st.markdown("#### 🔄 Sector Rotation (5-day performance)")
        try:
            _sectors = {"XLK": "Tech", "XLF": "Finance", "XLE": "Energy", "XLV": "Health",
                        "XLI": "Industrial", "XLC": "Comms", "XLU": "Utilities", "XLRE": "Real Estate"}
            _sr_rows = []
            for _ss, _sl in _sectors.items():
                try:
                    _sh = _cached_history(_ss, period="10d", interval="1d")
                    if len(_sh) >= 5:
                        _s5d = (_sh["Close"].iloc[-1] / _sh["Close"].iloc[-5] - 1) * 100
                        _sr_rows.append({"Sector": _sl, "ETF": _ss, "5d%": round(_s5d, 2)})
                except Exception:
                    pass
            if _sr_rows:
                _sr_df = pd.DataFrame(_sr_rows).sort_values("5d%", ascending=False)
                _sr_cols = st.columns(len(_sr_df))
                for _sri, (_, _srr) in enumerate(_sr_df.iterrows()):
                    _src = "#00c853" if _srr["5d%"] > 0 else "#d32f2f"
                    _srem = "🟢" if _srr["5d%"] > 1 else ("🔴" if _srr["5d%"] < -1 else "🟡")
                    _sr_cols[_sri].markdown(
                        f"<div style='text-align:center;padding:6px;background:var(--panel-solid);color:var(--text);"
                        f"border-radius:8px;border-top:3px solid {_src}'>"
                        f"{_srem}<br><b>{_srr['ETF']}</b><br><small>{_srr['Sector']}</small><br>"
                        f"<b>{_srr['5d%']:+.1f}%</b></div>",
                        unsafe_allow_html=True)
        except Exception as _sre:
            st.info(f"Sector rotation unavailable: {_sre}")

        # ── Fear & Greed Composite ──
        st.markdown("---")
        st.markdown("#### 😱 Fear & Greed Composite")
        try:
            _vix_row = snap[snap["Name"] == "VIX"]
            _spy_row  = snap[snap["Name"] == "S&P 500"]
            if not _vix_row.empty and not _spy_row.empty:
                _vix_fg = float(_vix_row["Price"].iloc[0])
                _spy_pct = float(_spy_row["Pct"].iloc[0])
                # VIX score: VIX<15→90 fear=low, VIX>40→0 fear=extreme
                _vix_score = max(0, min(100, (40 - _vix_fg) / 25 * 100))
                # Momentum score: SPY 1d return ±3% → 0-100
                _mom_score = max(0, min(100, (_spy_pct + 3) / 6 * 100))
                _fg_score  = int(0.6 * _vix_score + 0.4 * _mom_score)
                if _fg_score >= 75:   _fg_label, _fg_color = "EXTREME GREED 🤑", "#00c853"
                elif _fg_score >= 55: _fg_label, _fg_color = "GREED 😀",         "#4caf50"
                elif _fg_score >= 45: _fg_label, _fg_color = "NEUTRAL 😐",       "#e6a800"
                elif _fg_score >= 25: _fg_label, _fg_color = "FEAR 😨",           "#ff5722"
                else:                 _fg_label, _fg_color = "EXTREME FEAR 😱",   "#d32f2f"
                _filled = int(_fg_score / 5)
                _bar_s = "█" * _filled + "░" * (20 - _filled)
                _fgc1, _fgc2 = st.columns([1, 3])
                _fgc1.metric("Fear & Greed", str(_fg_score), _fg_label)
                _fgc2.markdown(
                    f"<div style='padding:12px;background:var(--panel-solid);color:var(--text);border-radius:8px;"
                    f"border-left:4px solid {_fg_color};margin-top:8px'>"
                    f"<code>{_bar_s}</code><br>"
                    f"<small>VIX: {_vix_fg:.1f} | SPY 1d: {_spy_pct:+.2f}% | "
                    f"Score: {_fg_score}/100</small></div>",
                    unsafe_allow_html=True)
        except Exception as _fge:
            st.info(f"Fear & Greed unavailable: {_fge}")

        # ── Market Heatmap (Treemap — ETFs + Stocks) ──────────────────
        st.markdown("---")
        st.markdown("#### 🟥🟩 Market Heatmap — ETFs & Stocks")
        try:
            _tm_file = "C:/Users/srini/Options_chain_data/US_CHARTS/ticker_universe.xlsx"
            _tm_df = pd.read_excel(_tm_file, sheet_name="bk")
            _tm_df = _tm_df[["ticker", "name", "category"]].dropna(subset=["ticker"])
            _tm_df["ticker"] = _tm_df["ticker"].str.strip().str.upper()
            _tm_df = _tm_df[~_tm_df["ticker"].str.contains(r"\^|-USD", regex=True, na=False)]
            _tm_tickers = tuple(sorted(set(_tm_df["ticker"].tolist())))

            # Fetch 35 trading days of history (cached 10 min)
            @st.cache_data(ttl=600)
            def _fetch_hm_history(tickers):
                import yfinance as _yf2
                try:
                    _raw = _yf2.download(
                        list(tickers), period="40d", interval="1d",
                        progress=False, auto_adjust=True, group_by="ticker")
                    # Build {ticker: Series(date->close)}
                    result = {}
                    for tk in tickers:
                        try:
                            if len(tickers) == 1:
                                _cl = _raw["Close"].dropna()
                            else:
                                _cl = _raw[tk]["Close"].dropna() if tk in _raw else pd.Series(dtype=float)
                            if not _cl.empty:
                                result[tk] = _cl
                        except Exception:
                            continue
                    return result
                except Exception:
                    return {}

            _hm_hist = _fetch_hm_history(_tm_tickers)
            if not _hm_hist:
                st.info("Heatmap data unavailable — yfinance returned no data.")
            else:
                # Collect all available trading dates across tickers
                _all_dates = sorted(set(
                    d.date() for _s in _hm_hist.values() for d in _s.index
                ))[-30:]  # last 30 trading days

                # Date slider (default = latest)
                _sel_date = st.select_slider(
                    "Select trading date",
                    options=_all_dates,
                    value=_all_dates[-1],
                    key="hm_date_slider",
                    format_func=lambda d: str(d),
                )
                _is_today = (_sel_date == _all_dates[-1])
                st.caption(
                    f"Showing: **{_sel_date}**{'  (today)' if _is_today else ''}  |  "
                    "Box size = |% change|. Green = up, Red = down."
                )

                # Compute pct change for selected date vs prior day
                _hm_rows = []
                for tk, _s in _hm_hist.items():
                    _s_dates = [d.date() for d in _s.index]
                    if _sel_date not in _s_dates:
                        continue
                    _idx_pos = _s_dates.index(_sel_date)
                    _px_now = float(_s.iloc[_idx_pos])
                    if _idx_pos > 0:
                        _px_prev = float(_s.iloc[_idx_pos - 1])
                        _pct = round((_px_now - _px_prev) / _px_prev * 100, 2)
                    else:
                        _pct = 0.0
                    _hm_rows.append({"ticker": tk, "pct": _pct, "price": round(_px_now, 2)})

                _hm_data = pd.DataFrame(_hm_rows)
                if _hm_data.empty:
                    st.info("No price data for selected date.")
                else:
                    _hm_merged = _tm_df.merge(_hm_data, on="ticker", how="inner")
                    _cat_map = {
                        "sp500": "S&P 500", "etf": "ETF / Index", "bond": "Bonds",
                        "metal": "Metals", "commodity": "Commodity",
                        "non_s&p": "Other Stocks", "index": "ETF / Index", "crypto": "Crypto"
                    }
                    _hm_merged["group"] = _hm_merged["category"].map(_cat_map).fillna("Other")
                    _hm_merged["size"] = _hm_merged["pct"].abs().clip(lower=0.1)

                    def _tm_color(p):
                        if p >= 3:    return "#00695c"
                        if p >= 1.5:  return "#2e7d32"
                        if p >= 0.5:  return "#43a047"
                        if p >= 0:    return "#a5d6a7"
                        if p >= -0.5: return "#ef9a9a"
                        if p >= -1.5: return "#e53935"
                        if p >= -3:   return "#c62828"
                        return "#7f0000"

                    _hm_merged["color"] = _hm_merged["pct"].apply(_tm_color)
                    # label shown inside each box (short: ticker + pct)
                    _hm_merged["label"] = (
                        _hm_merged["ticker"] + "<br>"
                        + _hm_merged["pct"].apply(lambda p: f"{p:+.2f}%")
                    )

                    _etf_rows = _hm_merged[_hm_merged["group"].isin(
                        ["ETF / Index", "Bonds", "Metals", "Commodity"])].copy()
                    _stk_rows = _hm_merged[_hm_merged["group"].isin(
                        ["S&P 500", "Other Stocks"])].copy()

                    _tm_tab_etf, _tm_tab_stk, _tm_tab_all = st.tabs(
                        ["📈 ETFs & Indexes", "📊 Stocks", "🌍 All"])

                    def _draw_treemap(df_tm, title_tm, key_tm):
                        if df_tm.empty:
                            st.info("No data for this group.")
                            return
                        # Plotly treemap requires parent group rows to appear in labels
                        # Build group-level rows (parent = "")
                        _grp_labels, _grp_parents, _grp_vals, _grp_colors, _grp_cd = [], [], [], [], []
                        for _g in df_tm["group"].unique():
                            _grp_labels.append(_g)
                            _grp_parents.append("")
                            _grp_vals.append(0.001)
                            _grp_colors.append("#546e7a")
                            _grp_cd.append([0.0, 0.0])
                        # Ticker rows
                        _tk_labels  = df_tm["label"].tolist()
                        _tk_parents = df_tm["group"].tolist()
                        _tk_vals    = df_tm["size"].tolist()
                        _tk_colors  = df_tm["color"].tolist()
                        _tk_cd      = df_tm[["pct", "price"]].values.tolist()

                        _fig_tm = go.Figure(go.Treemap(
                            labels  = _grp_labels + _tk_labels,
                            parents = _grp_parents + _tk_parents,
                            values  = _grp_vals + _tk_vals,
                            marker=dict(
                                colors=_grp_colors + _tk_colors,
                                line=dict(width=1.5, color="#ffffff"),
                            ),
                            textfont=dict(size=12, color="white"),
                            hovertemplate=(
                                "<b>%{label}</b><br>"
                                "% Change: %{customdata[0]:+.2f}%<br>"
                                "Price: $%{customdata[1]:,.2f}"
                                "<extra></extra>"
                            ),
                            customdata=_grp_cd + _tk_cd,
                            pathbar=dict(visible=False),
                            tiling=dict(squarifyratio=1.618),
                        ))
                        _fig_tm.update_layout(
                            height=540,
                            title=dict(text=title_tm, font=dict(color="#1565C0", size=14)),
                            paper_bgcolor="rgba(0,0,0,0)",
                            margin=dict(t=45, b=5, l=5, r=5),
                        )
                        st.plotly_chart(_fig_tm, use_container_width=True, key=key_tm)

                    def _hm_summary(df_s, label):
                        _up = (df_s["pct"] > 0).sum()
                        _dn = (df_s["pct"] < 0).sum()
                        _cols = st.columns(4)
                        _cols[0].metric(f"{label} Up", f"{_up}")
                        _cols[1].metric(f"{label} Down", f"{_dn}")
                        if not df_s.empty:
                            _best_i = df_s["pct"].idxmax()
                            _wrst_i = df_s["pct"].idxmin()
                            _cols[2].metric("Best", df_s.loc[_best_i, "ticker"],
                                            f"{df_s.loc[_best_i, 'pct']:+.2f}%")
                            _cols[3].metric("Worst", df_s.loc[_wrst_i, "ticker"],
                                            f"{df_s.loc[_wrst_i, 'pct']:+.2f}%")

                    with _tm_tab_etf:
                        _draw_treemap(_etf_rows,
                            f"ETF & Index Heatmap — {_sel_date} ({len(_etf_rows)} tickers)",
                            "tm_etf")
                        _hm_summary(_etf_rows, "ETFs")

                    with _tm_tab_stk:
                        _draw_treemap(_stk_rows,
                            f"Stock Heatmap — {_sel_date} ({len(_stk_rows)} tickers)",
                            "tm_stk")
                        _hm_summary(_stk_rows, "Stocks")

                    with _tm_tab_all:
                        _draw_treemap(_hm_merged,
                            f"Full Market Heatmap — {_sel_date} ({len(_hm_merged)} tickers)",
                            "tm_all")

                    st.caption(
                        "🟩 Dark green >=+3%  |  🟢 +0.5 to +3%  |  "
                        "Light green 0 to +0.5%  |  Light red 0 to -0.5%  |  "
                        "🔴 -0.5 to -3%  |  Dark red <=-3%  |  "
                        "Box size = magnitude of move"
                    )
        except Exception as _tme:
            st.error(f"Market heatmap error: {_tme}")

        # ── Timestamp ──
        st.markdown(
            f"<div>"
            f"🕐 Data pulled at: <b>{_pulled_at}</b></div>",
            unsafe_allow_html=True)

    # ── Interactive Instrument Detail Panel ──
    if not snap.empty:
        st.markdown("---")
        st.markdown("<div>🔍 Instrument Deep Dive — Search or Select</div>", unsafe_allow_html=True)
        _mo_search_col1, _mo_search_col2 = st.columns([2, 3])
        with _mo_search_col1:
            _mo_search = st.text_input("🔍 Type ticker or name (e.g. AAPL, Apple, Gold)", key="mo_search_input", placeholder="Search…")
        all_instrument_names = snap["Name"].tolist()
        # Filter instruments by search text
        if _mo_search.strip():
            _q = _mo_search.strip().upper()
            _filtered_names = [n for n in all_instrument_names
                               if _q in n.upper() or _q in GLOBAL_SYMBOLS.get(n, "").upper()]
            # Also allow custom ticker lookup not in snap
            _custom_ticker = _mo_search.strip().upper()
        else:
            _filtered_names = all_instrument_names
            _custom_ticker = ""
        with _mo_search_col2:
            sel_instrument = st.selectbox("Select from results", ["— Select —"] + _filtered_names, key="mo_instrument")
        # If nothing matched in snap but user typed a ticker, allow custom lookup
        if sel_instrument == "— Select —" and _custom_ticker and _custom_ticker not in [GLOBAL_SYMBOLS.get(n,"") for n in all_instrument_names]:
            sel_instrument = f"__custom__{_custom_ticker}"

        if sel_instrument != "— Select —":
            _is_custom = sel_instrument.startswith("__custom__")
            if _is_custom:
                _inst_sym = sel_instrument.replace("__custom__", "")
                _inst_icon = "🔎"
                # Fetch live price for custom ticker
                try:
                    _ct_data = yf.download(_inst_sym, period="2d", interval="1d", progress=False)
                    # yfinance ≥0.2 returns MultiIndex columns for multi-ticker; squeeze to Series
                    if isinstance(_ct_data.columns, pd.MultiIndex):
                        _ct_data.columns = _ct_data.columns.get_level_values(0)
                    _ct_name = _inst_sym
                    if not _ct_data.empty:
                        _close_col = _ct_data["Close"].squeeze()
                        _ct_close = float(_close_col.iloc[-1])
                        _ct_prev  = float(_close_col.iloc[-2]) if len(_ct_data) > 1 else _ct_close
                        _ct_chg   = (_ct_close - _ct_prev) / _ct_prev * 100 if _ct_prev > 0 else 0
                        _inst_row = pd.Series({"Name": _ct_name, "Price": _ct_close, "Pct": _ct_chg})
                    else:
                        st.warning(f"Could not find ticker: {_inst_sym}")
                        _inst_row = None
                except Exception as _cte:
                    st.error(f"Custom ticker lookup failed: {_cte}")
                    _inst_row = None
            else:
                _inst_row = snap[snap["Name"] == sel_instrument].iloc[0]
                _inst_sym = GLOBAL_SYMBOLS.get(sel_instrument, "")
                _inst_icon = SYMBOL_ICONS.get(sel_instrument, "")
            if _inst_row is None:
                st.stop()

            _disp_name = _inst_sym if _is_custom else sel_instrument

            # ── Stored History Chart (from DB snapshots) ──
            _stored = load_market_history(_inst_sym)
            if not _stored.empty:
                _stored["timestamp"] = pd.to_datetime(_stored["timestamp"])
                _stored = _stored.sort_values("timestamp")
                _fig_stored = go.Figure()
                _fig_stored.add_trace(go.Scatter(
                    x=_stored["timestamp"], y=_stored["price"], mode="lines+markers",
                    line=dict(color="#0066cc", width=2), marker=dict(size=4),
                    name="Price", hovertemplate="%{x}<br>$%{y:,.2f}<extra></extra>",
                ))
                _st_ymin = float(_stored["price"].min()) * 0.997
                _st_ymax = float(_stored["price"].max()) * 1.003
                _fig_stored.update_layout(
                    template="plotly_white", height=250,
                    title=f"{_inst_icon} {_disp_name} — Stored Price History ({len(_stored)} snapshots)",
                    xaxis_title="Time", yaxis_title="Price",
                    margin=dict(t=40, b=30, l=50, r=20),
                )
                _fig_stored.update_yaxes(range=[_st_ymin, _st_ymax])
                st.plotly_chart(_fig_stored, use_container_width=True)
                st.caption(f"📦 {len(_stored)} data points · Earliest: {_stored['timestamp'].iloc[0].strftime('%Y-%m-%d %H:%M')} · Latest: {_stored['timestamp'].iloc[-1].strftime('%Y-%m-%d %H:%M')}")

            with st.spinner(f"Loading {_disp_name} data..."):
                try:
                    # Period selector
                    _per_col1, _per_col2 = st.columns([3, 1])
                    with _per_col2:
                        _chart_period = st.selectbox("Chart Period", ["5d", "1mo", "3mo", "6mo", "1y", "2y"], index=2, key="mo_period")

                    _hist = _cached_history(_inst_sym, period=_chart_period)

                    if not _hist.empty:
                        # ── Header card ──
                        _cur_px = float(_inst_row["Price"])
                        _chg_pct = float(_inst_row["Pct"])
                        _chg_color = "#00c853" if _chg_pct >= 0 else "#ff1744"
                        _arrow = "▲" if _chg_pct >= 0 else "▼"
                        _px_display = f"{_cur_px:,.4f}" if _cur_px < 1 else f"${_cur_px:,.2f}"
                        st.markdown(
                            f"<div style='background:var(--panel-solid);color:var(--text);border-radius:12px;"
                            f"padding:20px 28px;margin:12px 0;border-left:5px solid {_chg_color};"
                            f"box-shadow:0 2px 8px rgba(0,0,0,0.08);'>"
                            f"<h2>{_inst_icon} {_disp_name} ({_inst_sym})</h2>"
                            f"{_px_display}"
                            f""
                            f"{_arrow} {_chg_pct:+.2f}%</div>",
                            unsafe_allow_html=True,
                        )

                        # ── Price Chart with Technicals ──
                        # Compute indicators
                        _c = _hist["Close"].copy()

                        # EMA 20
                        _hist["EMA20"] = _c.ewm(span=20, adjust=False).mean()
                        # SMA 50
                        if len(_c) >= 50:
                            _hist["SMA50"] = _c.rolling(50).mean()

                        # Bollinger Bands (20, 2)
                        if len(_c) >= 20:
                            _bb_mid = _c.rolling(20).mean()
                            _bb_std = _c.rolling(20).std()
                            _hist["BB_upper"] = _bb_mid + 2 * _bb_std
                            _hist["BB_lower"] = _bb_mid - 2 * _bb_std
                            _hist["BB_mid"]   = _bb_mid

                        # RSI 14
                        _rsi_series = None
                        if len(_c) >= 15:
                            _delta = _c.diff()
                            _gain = _delta.clip(lower=0).rolling(14).mean()
                            _loss = (-_delta.clip(upper=0)).rolling(14).mean()
                            _rs   = _gain / _loss.replace(0, float("nan"))
                            _rsi_series = 100 - 100 / (1 + _rs)

                        # MACD (12,26,9)
                        _macd_line = None
                        _macd_signal = None
                        _macd_hist_s = None
                        if len(_c) >= 27:
                            _ema12 = _c.ewm(span=12, adjust=False).mean()
                            _ema26 = _c.ewm(span=26, adjust=False).mean()
                            _macd_line   = _ema12 - _ema26
                            _macd_signal = _macd_line.ewm(span=9, adjust=False).mean()
                            _macd_hist_s = _macd_line - _macd_signal

                        # Determine subplot rows
                        _n_subplots = 1
                        if _rsi_series is not None:   _n_subplots += 1
                        if _macd_line is not None:     _n_subplots += 1

                        from plotly.subplots import make_subplots
                        _row_heights = [0.6] + [0.2] * (_n_subplots - 1)
                        fig_inst = make_subplots(
                            rows=_n_subplots, cols=1, shared_xaxes=True,
                            row_heights=_row_heights, vertical_spacing=0.04,
                        )

                        # Row 1: Candlestick / Line
                        if all(col in _hist.columns for col in ["Open", "High", "Low", "Close"]):
                            fig_inst.add_trace(go.Candlestick(
                                x=_hist.index, open=_hist["Open"], high=_hist["High"],
                                low=_hist["Low"], close=_hist["Close"], name="Price",
                                increasing_line_color="#00c853", decreasing_line_color="#ff1744",
                            ), row=1, col=1)
                        else:
                            fig_inst.add_trace(go.Scatter(
                                x=_hist.index, y=_c, mode="lines",
                                line=dict(color="#0066cc", width=2), name="Price",
                            ), row=1, col=1)

                        # EMA20
                        fig_inst.add_trace(go.Scatter(
                            x=_hist.index, y=_hist["EMA20"], mode="lines",
                            line=dict(color="#ff9100", width=1.5, dash="dot"), name="EMA 20",
                        ), row=1, col=1)

                        if "SMA50" in _hist.columns:
                            fig_inst.add_trace(go.Scatter(
                                x=_hist.index, y=_hist["SMA50"], mode="lines",
                                line=dict(color="#9c27b0", width=1.5, dash="dash"), name="SMA 50",
                            ), row=1, col=1)

                        # Bollinger Bands
                        if "BB_upper" in _hist.columns:
                            fig_inst.add_trace(go.Scatter(
                                x=_hist.index, y=_hist["BB_upper"], mode="lines",
                                line=dict(color="rgba(100,149,237,0.6)", width=1), name="BB Upper",
                                showlegend=False,
                            ), row=1, col=1)
                            fig_inst.add_trace(go.Scatter(
                                x=_hist.index, y=_hist["BB_lower"], mode="lines",
                                line=dict(color="rgba(100,149,237,0.6)", width=1), name="BB Lower",
                                fill="tonexty", fillcolor="rgba(100,149,237,0.07)",
                                showlegend=False,
                            ), row=1, col=1)

                        # Tight Y-axis range for price
                        _ymin = float(_hist["Low"].min() if "Low" in _hist.columns else _c.min()) * 0.997
                        _ymax = float(_hist["High"].max() if "High" in _hist.columns else _c.max()) * 1.003
                        fig_inst.update_yaxes(range=[_ymin, _ymax], row=1, col=1, title_text="Price")

                        # RSI row
                        _rsi_row = 2
                        if _rsi_series is not None:
                            fig_inst.add_trace(go.Scatter(
                                x=_hist.index, y=_rsi_series, mode="lines",
                                line=dict(color="#e91e63", width=1.5), name="RSI 14",
                            ), row=_rsi_row, col=1)
                            fig_inst.add_hline(y=70, line_dash="dot", line_color="red",   row=_rsi_row, col=1)
                            fig_inst.add_hline(y=30, line_dash="dot", line_color="green", row=_rsi_row, col=1)
                            fig_inst.update_yaxes(range=[0, 100], row=_rsi_row, col=1, title_text="RSI")

                        # MACD row
                        _macd_row = _rsi_row + (1 if _rsi_series is not None else 0)
                        if _macd_line is not None:
                            _bar_colors = ["#00c853" if v >= 0 else "#ff1744" for v in _macd_hist_s.fillna(0)]
                            fig_inst.add_trace(go.Bar(
                                x=_hist.index, y=_macd_hist_s, marker_color=_bar_colors, name="MACD Hist",
                                opacity=0.6,
                            ), row=_macd_row, col=1)
                            fig_inst.add_trace(go.Scatter(
                                x=_hist.index, y=_macd_line, mode="lines",
                                line=dict(color="#0066cc", width=1.5), name="MACD",
                            ), row=_macd_row, col=1)
                            fig_inst.add_trace(go.Scatter(
                                x=_hist.index, y=_macd_signal, mode="lines",
                                line=dict(color="#ff9100", width=1.5), name="Signal",
                            ), row=_macd_row, col=1)
                            fig_inst.update_yaxes(row=_macd_row, col=1, title_text="MACD")

                        _total_height = 420 + (_n_subplots - 1) * 160
                        fig_inst.update_layout(
                            template="plotly_white", height=_total_height,
                            title=f"{_inst_icon} {sel_instrument if not _is_custom else _inst_sym} — {_chart_period.upper()}",
                            xaxis_rangeslider_visible=False,
                            margin=dict(t=50, b=40, l=60, r=20),
                            legend=dict(orientation="h", y=1.02, x=0),
                        )
                        fig_inst.update_xaxes(showgrid=True, gridcolor="#eeeeee")
                        st.plotly_chart(fig_inst, use_container_width=True)

                        # ── Volume Chart ──
                        if "Volume" in _hist.columns and _hist["Volume"].sum() > 0:
                            _vol_colors = ["#00c853" if _hist["Close"].iloc[i] >= _hist["Open"].iloc[i]
                                           else "#ff1744" for i in range(len(_hist))]
                            fig_vol = go.Figure(go.Bar(
                                x=_hist.index, y=_hist["Volume"], marker_color=_vol_colors, name="Volume",
                            ))
                            fig_vol.update_layout(
                                template="plotly_white", height=180, title="Volume",
                                margin=dict(t=30, b=20), yaxis_title="Volume",
                            )
                            st.plotly_chart(fig_vol)

                        # ── Key Stats ──
                        st.markdown("<div>📊 Key Statistics</div>", unsafe_allow_html=True)
                        _close = _hist["Close"]
                        _high_period = float(_close.max())
                        _low_period = float(_close.min())
                        _pct_from_high = (_cur_px - _high_period) / _high_period * 100 if _high_period > 0 else 0
                        _pct_from_low = (_cur_px - _low_period) / _low_period * 100 if _low_period > 0 else 0
                        _avg_vol = int(_hist["Volume"].mean()) if "Volume" in _hist.columns and _hist["Volume"].sum() > 0 else 0
                        _daily_returns = _close.pct_change().dropna()
                        _volatility = float(_daily_returns.std() * np.sqrt(252) * 100) if len(_daily_returns) > 5 else 0

                        ks1, ks2, ks3, ks4, ks5, ks6 = st.columns(6)
                        ks1.metric(f"High ({_chart_period})", f"${_high_period:,.2f}")
                        ks2.metric(f"Low ({_chart_period})", f"${_low_period:,.2f}")
                        ks3.metric("From High", f"{_pct_from_high:+.1f}%")
                        ks4.metric("From Low", f"{_pct_from_low:+.1f}%")
                        ks5.metric("Avg Volume", f"{_avg_vol:,.0f}" if _avg_vol > 0 else "N/A")
                        ks6.metric("Ann. Volatility", f"{_volatility:.1f}%")

                        # ── Technical Analysis Writeup ──
                        st.markdown("<div>📝 Technical Analysis</div>", unsafe_allow_html=True)

                        _ta_points = []
                        # Trend
                        if len(_close) >= 20:
                            _sma20 = float(_close.rolling(20).mean().iloc[-1])
                            if _cur_px > _sma20:
                                _ta_points.append(("🟢", f"**Above SMA-20** (${_sma20:,.2f}) — Short-term uptrend."))
                            else:
                                _ta_points.append(("🔴", f"**Below SMA-20** (${_sma20:,.2f}) — Short-term downtrend."))

                        if len(_close) >= 50:
                            _sma50 = float(_close.rolling(50).mean().iloc[-1])
                            if _cur_px > _sma50:
                                _ta_points.append(("🟢", f"**Above SMA-50** (${_sma50:,.2f}) — Medium-term bullish."))
                            else:
                                _ta_points.append(("🔴", f"**Below SMA-50** (${_sma50:,.2f}) — Medium-term bearish."))

                            # Golden/Death cross
                            if len(_close) >= 50 and len(_close) >= 20:
                                _sma20_prev = float(_close.rolling(20).mean().iloc[-2])
                                _sma50_prev = float(_close.rolling(50).mean().iloc[-2])
                                if _sma20_prev < _sma50_prev and _sma20 > _sma50:
                                    _ta_points.append(("⭐", "**Golden Cross!** SMA-20 crossed above SMA-50 — Bullish signal."))
                                elif _sma20_prev > _sma50_prev and _sma20 < _sma50:
                                    _ta_points.append(("💀", "**Death Cross!** SMA-20 crossed below SMA-50 — Bearish signal."))

                        # RSI approximation (14-period)
                        if len(_daily_returns) >= 14:
                            _gains = _daily_returns.clip(lower=0)
                            _losses = (-_daily_returns.clip(upper=0))
                            _avg_gain = _gains.rolling(14).mean().iloc[-1]
                            _avg_loss = _losses.rolling(14).mean().iloc[-1]
                            _rsi = 100 - (100 / (1 + _avg_gain / _avg_loss)) if _avg_loss > 0 else 100
                            if _rsi > 70:
                                _ta_points.append(("🔴", f"**RSI = {_rsi:.0f}** — OVERBOUGHT. Potential pullback ahead."))
                            elif _rsi < 30:
                                _ta_points.append(("🟢", f"**RSI = {_rsi:.0f}** — OVERSOLD. Potential bounce ahead."))
                            else:
                                _ta_points.append(("⚪", f"**RSI = {_rsi:.0f}** — Neutral range."))

                        # Support/Resistance (simple recent high/low)
                        if len(_close) >= 10:
                            _recent_high = float(_close.iloc[-10:].max())
                            _recent_low = float(_close.iloc[-10:].min())
                            _ta_points.append(("📏", f"**Support:** ${_recent_low:,.2f} | **Resistance:** ${_recent_high:,.2f} (10-day range)"))

                        # Volatility assessment
                        if _volatility > 40:
                            _ta_points.append(("⚠️", f"**High volatility** ({_volatility:.0f}% annualized) — Use tight stops, smaller position size."))
                        elif _volatility > 25:
                            _ta_points.append(("🟡", f"**Moderate volatility** ({_volatility:.0f}% annualized) — Normal trading conditions."))
                        else:
                            _ta_points.append(("🟢", f"**Low volatility** ({_volatility:.0f}% annualized) — Stable. Good for premium selling."))

                        # Recent momentum
                        if len(_close) >= 5:
                            _5d_ret = (_close.iloc[-1] / _close.iloc[-5] - 1) * 100
                            if _5d_ret > 3:
                                _ta_points.append(("🚀", f"**Strong 5-day momentum:** {_5d_ret:+.1f}% — Momentum is bullish."))
                            elif _5d_ret < -3:
                                _ta_points.append(("📉", f"**Weak 5-day momentum:** {_5d_ret:+.1f}% — Momentum is bearish."))
                            else:
                                _ta_points.append(("➡️", f"**5-day return:** {_5d_ret:+.1f}% — Consolidating."))

                        for _ico, _txt in _ta_points:
                            st.markdown(f"{_ico} {_txt}")

                        # ── Overall Verdict ──
                        _bull_count = sum(1 for ico, _ in _ta_points if ico in ("🟢", "⭐", "🚀"))
                        _bear_count = sum(1 for ico, _ in _ta_points if ico in ("🔴", "💀", "📉"))
                        if _bull_count > _bear_count + 1:
                            st.success(f"✅ **BULLISH OUTLOOK** for {sel_instrument} — {_bull_count} bullish vs {_bear_count} bearish signals.")
                        elif _bear_count > _bull_count + 1:
                            st.error(f"⛔ **BEARISH OUTLOOK** for {sel_instrument} — {_bear_count} bearish vs {_bull_count} bullish signals.")
                        else:
                            st.info(f"📊 **NEUTRAL / MIXED** for {sel_instrument} — {_bull_count} bullish, {_bear_count} bearish signals.")

                        # ── OI-based Support / Resistance ──
                        with st.expander("📊 OI Support & Resistance (Current + Next Expiry)"):
                            try:
                                _oi_ticker = _inst_sym.replace("^", "")
                                with sqlite3.connect(DB_PATH) as _oi_conn:
                                    _oi_dates = pd.read_sql(
                                        "SELECT DISTINCT trade_date_now FROM options_change WHERE ticker=? "
                                        "ORDER BY substr(trade_date_now,7,4)||substr(trade_date_now,1,2)||substr(trade_date_now,4,2) DESC LIMIT 1",
                                        _oi_conn, params=(_oi_ticker,)
                                    )
                                    if not _oi_dates.empty:
                                        _oi_date = _oi_dates.iloc[0, 0]
                                        _oi_df = pd.read_sql(
                                            "SELECT strike, expiry_date, openInt_Call_now, openInt_Put_now, "
                                            "change_OI_Call, change_OI_Put FROM options_change "
                                            "WHERE ticker=? AND trade_date_now=? ORDER BY strike",
                                            _oi_conn, params=(_oi_ticker, _oi_date)
                                        )
                                        if not _oi_df.empty:
                                            # Sort expiries — current = nearest, next = second nearest
                                            _oi_df["_exp_sort"] = _oi_df["expiry_date"].apply(
                                                lambda d: d[6:10]+d[0:2]+d[3:5] if isinstance(d, str) and len(d)==10 else "99999999"
                                            )
                                            _expiries = sorted(_oi_df["_exp_sort"].unique())
                                            _cur_exp_sort  = _expiries[0] if len(_expiries) >= 1 else None
                                            _next_exp_sort = _expiries[1] if len(_expiries) >= 2 else None

                                            def _oi_sr_block(exp_sort, label, spot):
                                                _ef = _oi_df[_oi_df["_exp_sort"] == exp_sort].copy()
                                                if _ef.empty:
                                                    return
                                                st.markdown(f"**{label}** (expiry: {_ef['expiry_date'].iloc[0]})")
                                                _ef["total_oi"] = _ef["openInt_Call_now"].fillna(0) + _ef["openInt_Put_now"].fillna(0)
                                                _mean_oi = _ef["total_oi"].mean()
                                                # Gamma walls: strikes ≥ 2× mean total OI
                                                _walls = _ef[_ef["total_oi"] >= 2 * _mean_oi]["strike"].tolist()
                                                # Max pain: strike minimising sum of (test-strike)*call_OI + (strike-test)*put_OI
                                                _strikes = _ef["strike"].tolist()
                                                _c_oi = _ef["openInt_Call_now"].fillna(0).tolist()
                                                _p_oi = _ef["openInt_Put_now"].fillna(0).tolist()
                                                _pain = {}
                                                for _t in _strikes:
                                                    _pain[_t] = sum(max(0, _t - _s) * _co for _s, _co in zip(_strikes, _c_oi)) \
                                                               + sum(max(0, _s - _t) * _po for _s, _po in zip(_strikes, _p_oi))
                                                _max_pain = min(_pain, key=_pain.get) if _pain else None
                                                # Key OI levels near spot
                                                _near = _ef[(_ef["strike"] >= spot * 0.94) & (_ef["strike"] <= spot * 1.06)].copy()
                                                _near = _near.sort_values("total_oi", ascending=False).head(6)

                                                _oi_sr_cols = st.columns(3)
                                                _oi_sr_cols[0].metric("Max Pain", f"${_max_pain:,.0f}" if _max_pain else "N/A")
                                                _oi_sr_cols[1].metric("Gamma Walls", ", ".join([f"${w:,.0f}" for w in _walls[:3]]) or "None")
                                                _oi_sr_cols[2].metric("Strikes Scanned", str(len(_ef)))

                                                if not _near.empty:
                                                    _near_disp = _near[["strike", "openInt_Call_now", "openInt_Put_now",
                                                                          "change_OI_Call", "change_OI_Put"]].copy()
                                                    _near_disp.columns = ["Strike", "Call OI", "Put OI", "Call Δ", "Put Δ"]
                                                    # Add notional columns: Δ contracts × strike × 100
                                                    _near_disp["C Notional"] = _near["change_OI_Call"].fillna(0).abs() * _near["strike"] * 100
                                                    _near_disp["P Notional"] = _near["change_OI_Put"].fillna(0).abs()  * _near["strike"] * 100
                                                    def _fmt_nt(n):
                                                        if n >= 1_000_000: return f"${n/1_000_000:.1f}M"
                                                        if n >= 1_000: return f"${n/1_000:.0f}K"
                                                        return f"${n:.0f}"
                                                    _near_disp["Strike"] = _near_disp["Strike"].apply(lambda x: f"${x:,.0f}")
                                                    for col in ["Call OI", "Put OI", "Call Δ", "Put Δ"]:
                                                        _near_disp[col] = _near_disp[col].apply(
                                                            lambda x: f"{int(x):,}" if pd.notna(x) else "—")
                                                    _near_disp["C Notional"] = _near_disp["C Notional"].apply(_fmt_nt)
                                                    _near_disp["P Notional"] = _near_disp["P Notional"].apply(_fmt_nt)
                                                    st.dataframe(_near_disp, hide_index=True, use_container_width=True)

                                            _spot_price = float(_inst_row["Price"])
                                            if _cur_exp_sort:
                                                _oi_sr_block(_cur_exp_sort, "📅 Current Expiry", _spot_price)
                                            if _next_exp_sort:
                                                _oi_sr_block(_next_exp_sort, "📅 Next Expiry", _spot_price)

                                            # ── Multi-week OI build trend ──
                                            st.markdown("---")
                                            st.markdown("**📅 OI Build Trend (1W / 1M)**")
                                            _trend_dates = pd.read_sql(
                                                "SELECT DISTINCT trade_date_now FROM options_change WHERE ticker=? "
                                                "ORDER BY substr(trade_date_now,7,4)||substr(trade_date_now,1,2)||substr(trade_date_now,4,2) DESC LIMIT 25",
                                                _oi_conn, params=(_oi_ticker,)
                                            )["trade_date_now"].tolist()

                                            def _oi_period_sum(dates):
                                                if not dates: return 0.0, 0.0
                                                _ph = ",".join("?" * len(dates))
                                                _r = pd.read_sql(
                                                    f"SELECT SUM(change_OI_Call) c, SUM(change_OI_Put) p FROM options_change WHERE ticker=? AND trade_date_now IN ({_ph})",
                                                    _oi_conn, params=[_oi_ticker] + dates
                                                )
                                                return float(_r["c"].iloc[0] or 0), float(_r["p"].iloc[0] or 0)

                                            _wc, _wp = _oi_period_sum(_trend_dates[:5])
                                            _mc, _mp = _oi_period_sum(_trend_dates[:20])

                                            def _fk_tr(n):
                                                a = abs(n); s = "+" if n >= 0 else ""
                                                if a >= 1_000_000: return f"{s}{a/1_000_000:.1f}M"
                                                if a >= 1_000: return f"{s}{a/1_000:.0f}K"
                                                return f"{s}{n:.0f}"

                                            _trend_data = {
                                                "Period": ["1 Week (5d)", "1 Month (20d)"],
                                                "Call OI Δ": [_fk_tr(_wc), _fk_tr(_mc)],
                                                "Put OI Δ":  [_fk_tr(_wp), _fk_tr(_mp)],
                                                "Bias": [
                                                    "📈 Call-dominant" if _wc > abs(_wp)*1.5 else ("📉 Put-dominant" if _wp > abs(_wc)*1.5 else "⇔ Mixed"),
                                                    "📈 Call-dominant" if _mc > abs(_mp)*1.5 else ("📉 Put-dominant" if _mp > abs(_mc)*1.5 else "⇔ Mixed"),
                                                ],
                                            }
                                            st.dataframe(pd.DataFrame(_trend_data), hide_index=True, use_container_width=True)
                                        else:
                                            st.info(f"No OI data found for {_oi_ticker}.")
                                    else:
                                        st.info(f"No OI data found for {_oi_ticker}.")
                            except Exception as _oi_err:
                                st.info(f"OI data unavailable: {_oi_err}")

                        # ── Returns table ──
                        with st.expander("📈 Returns Breakdown"):
                            _ret_rows = []
                            for _n, _label in [(1, "1-Day"), (5, "5-Day"), (10, "10-Day"), (20, "1-Month"), (60, "3-Month")]:
                                if len(_close) > _n:
                                    _r = (_close.iloc[-1] / _close.iloc[-_n - 1] - 1) * 100
                                    _ret_rows.append({"Period": _label, "Return": f"{_r:+.2f}%",
                                                      "Start": f"${float(_close.iloc[-_n - 1]):,.2f}",
                                                      "End": f"${float(_close.iloc[-1]):,.2f}"})
                            if _ret_rows:
                                st.dataframe(pd.DataFrame(_ret_rows), hide_index=True)

                        # ── Short Interest & Float ──
                        with st.expander("📉 Short Interest & Float"):
                            try:
                                _si_ticker = _inst_sym.replace("^", "")
                                _si_info = yf.Ticker(_si_ticker).info
                                _si_float  = _si_info.get("floatShares")
                                _si_ss     = _si_info.get("sharesShort")
                                _si_spf    = _si_info.get("shortPercentOfFloat")
                                _si_sr     = _si_info.get("shortRatio")
                                _si_ssp    = _si_info.get("sharesShortPriorMonth")
                                _si_out    = _si_info.get("sharesOutstanding")
                                if _si_spf and _si_spf < 1:
                                    _si_spf = _si_spf * 100

                                def _si_fmt(n):
                                    if n is None: return "N/A"
                                    if n >= 1e9: return f"{n/1e9:.2f}B"
                                    if n >= 1e6: return f"{n/1e6:.1f}M"
                                    return f"{n:,.0f}"

                                _si_chg = None
                                if _si_ss and _si_ssp:
                                    _si_chg = (_si_ss - _si_ssp) / _si_ssp * 100

                                # Squeeze score
                                _sq = 0
                                if _si_spf:
                                    if _si_spf >= 30: _sq += 4
                                    elif _si_spf >= 20: _sq += 3
                                    elif _si_spf >= 10: _sq += 2
                                    elif _si_spf >= 5: _sq += 1
                                if _si_sr:
                                    if _si_sr >= 10: _sq += 3
                                    elif _si_sr >= 5: _sq += 2
                                    elif _si_sr >= 3: _sq += 1
                                if _si_chg and _si_chg > 10: _sq += 2
                                elif _si_chg and _si_chg < -10: _sq -= 1
                                _sq = max(0, min(10, _sq))
                                _sq_label = "HIGH SQUEEZE RISK" if _sq >= 7 else ("MODERATE" if _sq >= 4 else "LOW")

                                _si_cols = st.columns(3)
                                _si_cols[0].metric("Float Shares", _si_fmt(_si_float))
                                _si_cols[1].metric("Shares Short", _si_fmt(_si_ss),
                                    delta=f"{_si_chg:+.1f}% vs prev mo" if _si_chg else None)
                                _si_cols[2].metric("Short % Float",
                                    f"{_si_spf:.1f}%" if _si_spf else "N/A")

                                _si_cols2 = st.columns(3)
                                _si_cols2[0].metric("Days to Cover",
                                    f"{_si_sr:.1f}d" if _si_sr else "N/A")
                                _si_cols2[1].metric("Shares Outstanding", _si_fmt(_si_out))
                                _si_cols2[2].metric(f"Squeeze Score  [{_sq_label}]",
                                    f"{_sq}/10")

                                # Squeeze signal
                                if _sq >= 7:
                                    st.error(f"🔴 **HIGH SHORT SQUEEZE RISK** — {_si_spf:.1f}% shorted, {_si_sr:.1f}d to cover. "
                                             f"Any bullish catalyst could trigger rapid covering rally.")
                                elif _sq >= 4:
                                    st.warning(f"🟡 **MODERATE SHORT INTEREST** — watch for short covering rallies on positive news.")
                                else:
                                    st.success(f"🟢 **LOW SHORT INTEREST** — minimal squeeze risk.")

                                # Next-day predictor context
                                st.markdown("**Next-Day Short Impact:**")
                                if _si_spf and _si_spf >= 20 and _si_sr and _si_sr >= 5:
                                    st.markdown(f"- High short ratio ({_si_sr:.1f}d to cover) + heavy short load ({_si_spf:.1f}%) → **squeeze amplifier** on any gap-up")
                                if _si_chg and _si_chg > 15:
                                    st.markdown(f"- Short interest rising fast (+{_si_chg:.1f}% MoM) → **bearish sentiment building**, watch for continuation down or violent reversal")
                                elif _si_chg and _si_chg < -15:
                                    st.markdown(f"- Short interest declining ({_si_chg:.1f}% MoM) → **shorts covering**, reduces downside pressure")
                                if _si_sr and _si_sr >= 10:
                                    st.markdown(f"- DTC={_si_sr:.1f}d → if stock gaps up 3%+, covering pressure could add another 2-5% intraday")

                            except Exception as _si_err:
                                st.info(f"Short interest data unavailable: {_si_err}")

                    else:
                        st.warning(f"No historical data available for {sel_instrument}.")
                except Exception as e:
                    st.error(f"Could not load data for {sel_instrument}: {e}")

    # ── Live News Feed with links and trade ideas ──
    st.markdown("---")
    st.markdown("<div>📰 Breaking News & Market Impact</div>", unsafe_allow_html=True)
    try:
        import feedparser
        _news_feeds = [
            "https://feeds.finance.yahoo.com/rss/2.0/headline?s=^GSPC&region=US&lang=en-US",
            "https://feeds.finance.yahoo.com/rss/2.0/headline?s=^DJI&region=US&lang=en-US",
        ]
        all_entries = []
        for url in _news_feeds:
            try:
                feed = feedparser.parse(url)
                all_entries.extend(feed.entries[:6])
            except Exception:
                pass
        seen_titles = set()
        unique_entries = []
        for e in all_entries:
            t = e.get("title", "")
            if t and t not in seen_titles:
                seen_titles.add(t)
                unique_entries.append(e)

        bull_words = ["rally", "surge", "gain", "rise", "jump", "bull", "record", "high", "beat", "strong", "buy", "upgrade"]
        bear_words = ["fall", "drop", "crash", "plunge", "bear", "low", "miss", "weak", "sell", "fear", "tariff", "recession", "downgrade", "cut", "dive", "tumble", "plummet", "slump"]

        for entry in unique_entries[:10]:
            title = entry.get("title", "")
            link = entry.get("link", "#")
            published = entry.get("published", "")
            title_lower = title.lower()
            if any(w in title_lower for w in bull_words):
                css_class, emoji = "news-card bull", "🟢"
            elif any(w in title_lower for w in bear_words):
                css_class, emoji = "news-card bear", "🔴"
            else:
                css_class, emoji = "news-card", "📰"

            # Auto-generate trade idea from news headline
            news_idea = ""
            tl = title_lower
            if "oil" in tl and ("surge" in tl or "soar" in tl or "100" in tl or "spike" in tl):
                news_idea = "→ 💡 LONG XLE/OXY, SHORT airlines. Buy inflation hedges."
            elif "crash" in tl or "plunge" in tl or "dive" in tl or "tumble" in tl:
                news_idea = "→ 💡 BUY puts on indices. LONG VIX calls. Raise cash levels."
            elif "iran" in tl or "war" in tl or "geopolitical" in tl:
                news_idea = "→ 💡 LONG defense (LMT, RTX), gold, oil. SHORT travel & consumer."
            elif "bubble" in tl or "ai bubble" in tl:
                news_idea = "→ 💡 Reduce AI/tech exposure. Rotate to value/defensives."
            elif "nikkei" in tl or "japan" in tl:
                news_idea = "→ 💡 SHORT EWJ. Global risk-off may spread to US markets."
            elif "bitcoin" in tl or "crypto" in tl:
                news_idea = "→ 💡 Watch BTC support. Crypto correlates with risk appetite."
            elif "rally" in tl or "record" in tl:
                news_idea = "→ 💡 Momentum trade — ride the trend but set stops."

            pub_short = published.split(",")[-1].strip() if "," in published else published
            st.markdown(
                f"<div>"
                f"{emoji} <a href='{link}' target='_blank'><b>{title}</b></a> ({pub_short})"
                + (f"<br>{news_idea}" if news_idea else "") +
                "</div>",
                unsafe_allow_html=True,
            )
        if not unique_entries:
            st.info("No news available. Check network.")
    except ImportError:
        st.info("Install feedparser for live news: `pip install feedparser`")
    except Exception:
        st.info("News feed temporarily unavailable.")

    # ── Technical Signals (moondevonyt / Harvard RBI: RSI, MACD, BB, EMA) ──
    st.markdown("---")
    st.markdown("### 📐 Technical Signals — RSI · MACD · Bollinger Bands · EMA20")
    st.caption("Inspired by moondevonyt / Harvard Algorithmic Trading with AI (RBI methodology)")
    try:
        import pandas_ta as pta
        _ts_tickers = ["SPY", "QQQ", "IWM", "NVDA", "AAPL", "TSLA", "AMZN", "META", "MSFT", "GOOG"]
        _ts_rows = []
        for _sym in _ts_tickers:
            try:
                _h = _cached_history(_sym, period="60d", interval="1d")
                if len(_h) < 26:
                    continue
                _close = _h["Close"]
                _px = float(_close.iloc[-1])
                _prev = float(_close.iloc[-2])
                _day_chg = (_px - _prev) / _prev * 100
                # RSI(14)
                _rsi_s = pta.rsi(_close, length=14)
                _rsi = float(_rsi_s.iloc[-1]) if _rsi_s is not None and not _rsi_s.empty else 50.0
                # MACD(12,26,9)
                _macd_df = pta.macd(_close, fast=12, slow=26, signal=9)
                _macd_cross = "+"
                if _macd_df is not None and not _macd_df.empty:
                    _mc = _macd_df.columns.tolist()
                    _macd_col = next((c for c in _mc if c.startswith("MACD_")), _mc[0])
                    _sigs_col  = next((c for c in _mc if c.startswith("MACDs_")), _mc[2])
                    _macd_v     = float(_macd_df[_macd_col].iloc[-1])
                    _macd_sig_v = float(_macd_df[_sigs_col].iloc[-1])
                    _macd_cross = "+" if _macd_v > _macd_sig_v else "-"
                # Bollinger Bands(20,2)
                _bb_pos = "MID"
                _bb_df = pta.bbands(_close, length=20, std=2)
                if _bb_df is not None and not _bb_df.empty:
                    _bc = _bb_df.columns.tolist()
                    _bbu_col = next((c for c in _bc if c.startswith("BBU")), None)
                    _bbl_col = next((c for c in _bc if c.startswith("BBL")), None)
                    if _bbu_col and _bbl_col:
                        _bb_upper = float(_bb_df[_bbu_col].iloc[-1])
                        _bb_lower = float(_bb_df[_bbl_col].iloc[-1])
                        if _px >= _bb_upper * 0.995:
                            _bb_pos = "TOP"
                        elif _px <= _bb_lower * 1.005:
                            _bb_pos = "BOT"
                # EMA(20)
                _ema_s = pta.ema(_close, length=20)
                _ema = float(_ema_s.iloc[-1]) if _ema_s is not None and not _ema_s.empty else _px
                _ema_rel = "Above" if _px > _ema else "Below"
                # Composite signal score
                _pts = sum([
                    _rsi < 70,
                    _rsi > 50,
                    _macd_cross == "+",
                    _bb_pos != "TOP",
                    _ema_rel == "Above",
                ])
                _sig = "BULL" if _pts >= 4 else ("BEAR" if _pts <= 1 else "NEUT")
                _sig_color = "🟢" if _sig == "BULL" else ("🔴" if _sig == "BEAR" else "🟡")
                _rsi_flag = " ⚠️OB" if _rsi > 70 else (" ⚠️OS" if _rsi < 30 else "")
                _ts_rows.append({
                    "Ticker": _sym,
                    "Price": round(_px, 2),
                    "Day%": round(_day_chg, 2),
                    "RSI(14)": round(_rsi, 1),
                    "RSI Note": "Overbought" if _rsi > 70 else ("Oversold" if _rsi < 30 else "Neutral"),
                    "MACD": _macd_cross,
                    "BB": _bb_pos,
                    "EMA20": round(_ema, 2),
                    "vs EMA": _ema_rel,
                    "Signal": f"{_sig_color} {_sig}",
                })
            except Exception:
                continue
        if _ts_rows:
            _ts_df = pd.DataFrame(_ts_rows)
            # Color-code Signal column
            def _color_sig(val):
                if "BULL" in str(val): return "color: #00c853; font-weight: bold"
                if "BEAR" in str(val): return "color: #c62828; font-weight: bold"
                return "color: #e6a800"
            def _color_rsi(val):
                try:
                    v = float(val)
                    if v > 70: return "background-color: #ffcccc; color:#111"
                    if v < 30: return "background-color: #ccffcc; color:#111"
                except: pass
                return ""
            styled = _ts_df.style\
                .map(_color_sig, subset=["Signal"])\
                .map(_color_rsi, subset=["RSI(14)"])\
                .format({"Price": "${:,.2f}", "Day%": "{:+.2f}%", "EMA20": "${:,.2f}"})
            st.dataframe(styled, hide_index=True, use_container_width=True)

            # Volume Spike Detection (moondevonyt WhaleAgent concept)
            st.markdown("#### 🐋 Volume Spike Monitor")
            _vol_rows = []
            for _sym in _ts_tickers[:8]:
                try:
                    _hv = _cached_history(_sym, period="10d", interval="1d")
                    if len(_hv) < 6: continue
                    _vol_today = float(_hv["Volume"].iloc[-1])
                    _vol_avg = float(_hv["Volume"].iloc[-6:-1].mean())
                    if _vol_avg <= 0: continue
                    _ratio = _vol_today / _vol_avg
                    if _ratio >= 1.3:
                        _px2 = float(_hv["Close"].iloc[-1])
                        _chg2 = (_px2 - float(_hv["Close"].iloc[-2])) / float(_hv["Close"].iloc[-2]) * 100
                        _note = "SPIKE" if _ratio >= 2.0 else ("HIGH" if _ratio >= 1.5 else "ABOVE AVG")
                        _vol_rows.append({
                            "Ticker": _sym,
                            "Vol Ratio": f"{_ratio:.1f}x",
                            "Day%": round(_chg2, 2),
                            "Note": _note,
                            "Direction": "UP" if _chg2 > 0 else "DOWN",
                        })
                except Exception:
                    continue
            if _vol_rows:
                _vdf = pd.DataFrame(_vol_rows)
                def _color_note(val):
                    if val == "SPIKE": return "background-color: #ffcccc; color:#111; font-weight: bold"
                    if val == "HIGH": return "background-color: #fff3cd; color:#111"
                    return ""
                st.dataframe(_vdf.style.map(_color_note, subset=["Note"]),
                             hide_index=True, use_container_width=True)
            else:
                st.info("No unusual volume detected right now.")
        else:
            st.info("Technical data unavailable.")
    except ImportError:
        st.info("Install pandas-ta: `pip install pandas-ta`")
    except Exception as _te:
        st.warning(f"Technical signals error: {_te}")

    # ── DB Data Summary ──
    st.markdown("---")
    st.markdown("<div>📊 DB Data Summary</div>", unsafe_allow_html=True)
    try:
        dates = available_trade_dates()
        if dates:
            latest = dates[0]
            day_df = load_oi_for_date(latest)
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Latest OI Date", latest)
            c2.metric("Tickers Tracked", day_df["ticker"].nunique())
            c3.metric("Total Call OI Chg", f"{day_df['change_OI_Call'].sum():+,.0f}")
            c4.metric("Total Put OI Chg", f"{day_df['change_OI_Put'].sum():+,.0f}")
    except Exception:
        st.info("No OI data loaded in DB yet.")

    # ── Auto-refresh via rerun ──
    if auto_ref:
        import time as _time
        _time.sleep(60)
        st.rerun()


# ===================================================================
# ──  PAGE 2: OI ANALYTICS & PREDICTION
# ===================================================================
elif page == "🔥 OI Analytics & Prediction":
    _hdr1, _hdr2 = st.columns([4, 1])
    with _hdr1:
        st.markdown("## 🔥 OI Analytics & Next-Day Prediction Engine")
    with _hdr2:
        if st.button("🔄 Refresh", key="refresh_oi_analytics"):
            st.rerun()
        with st.popover("ℹ️"):
            st.markdown(_PAGE_HELP.get(page, ""))

    dates = available_trade_dates()
    if not dates:
        st.warning("No OI data in database.")
        st.stop()

    col_a, col_b, col_c = st.columns([1, 1, 1])
    with col_a:
        sel_date = st.selectbox("📅 Trade Date (snapshot)", dates, index=0)
    with col_b:
        day_df = load_oi_for_date(sel_date)
        tickers_avail = sorted(day_df["ticker"].unique()) if not day_df.empty else []
        sel_ticker = st.selectbox("🎯 Ticker", tickers_avail, index=0 if tickers_avail else None)
    with col_c:
        # Future expiry dates for this ticker on this snapshot date
        _oa_exp_raw = []
        if sel_ticker and not day_df.empty:
            _tk_exp = day_df[day_df["ticker"] == sel_ticker]["expiry_date"].dropna().unique().tolist()
            _today_dt = None
            try:
                import datetime as _dt_mod
                _today_dt = _dt_mod.datetime.strptime(sel_date, "%m-%d-%Y").date()
            except Exception:
                pass
            def _oa_exp_sort(d):
                try:
                    p = d.split("-")
                    return (int(p[2]), int(p[0]), int(p[1]))
                except Exception:
                    return (9999, 99, 99)
            def _oa_is_future(d):
                if _today_dt is None:
                    return True
                try:
                    p = d.split("-")
                    edt = _dt_mod.date(int(p[2]), int(p[0]), int(p[1]))
                    return edt >= _today_dt
                except Exception:
                    return True
            _future_exps = sorted([e for e in _tk_exp if _oa_is_future(e)], key=_oa_exp_sort)
            _past_exps   = sorted([e for e in _tk_exp if not _oa_is_future(e)], key=_oa_exp_sort, reverse=True)
            _oa_exp_raw  = _future_exps + _past_exps
        _oa_exp_labels = (
            [f"🟢 {e}" for e in _future_exps] +
            [f"🔴 {e} (past)" for e in _past_exps]
        ) if sel_ticker else []
        _oa_exp_opts = ["All Expiries"] + _oa_exp_labels
        _sel_oa_expiry_lbl = st.selectbox(
            "📅 Filter by Expiry",
            _oa_exp_opts, index=0, key="oa_expiry",
            help="Filter all charts and tables to a single expiry cycle"
        )
        _sel_oa_expiry = None
        if _sel_oa_expiry_lbl != "All Expiries":
            # Strip the emoji prefix to get raw date
            _sel_oa_expiry = _sel_oa_expiry_lbl.replace("🟢 ", "").replace("🔴 ", "").replace(" (past)", "")

    if day_df.empty:
        st.stop()

    # ── Anomaly Scanner ──
    st.markdown("<div>🚨 Anomaly Scanner (Z-Score)</div>", unsafe_allow_html=True)
    anom = oi_anomalies(day_df)
    top_anom = anom.head(10)
    display_cols = ["ticker", "call_oi_chg", "put_oi_chg", "call_vol_chg", "put_vol_chg", "pcr", "max_z"]
    display_df = top_anom[[c for c in display_cols if c in top_anom.columns]].copy()
    display_df.columns = ["Ticker", "Call OI Δ", "Put OI Δ", "Call Vol Δ", "Put Vol Δ", "PCR", "Z-Score"]
    for c in ["Call OI Δ", "Put OI Δ", "Call Vol Δ", "Put Vol Δ"]:
        if c in display_df.columns:
            display_df[c] = display_df[c].apply(lambda x: f"{x:+,.0f}")
    display_df["PCR"] = display_df["PCR"].apply(lambda x: f"{x:.2f}")
    display_df["Z-Score"] = display_df["Z-Score"].apply(lambda x: f"{x:.2f}")
    st.dataframe(display_df, hide_index=True)

    if not sel_ticker:
        st.stop()

    # ── Ticker deep-dive ──
    st.markdown(f"<div>🔍 {sel_ticker} — Strike-Level OI Analysis</div>", unsafe_allow_html=True)
    tk_df = day_df[day_df["ticker"] == sel_ticker].copy()

    if tk_df.empty:
        st.info("No data for this ticker on selected date.")
        st.stop()

    # Apply expiry filter if selected
    if _sel_oa_expiry:
        tk_df = tk_df[tk_df["expiry_date"] == _sel_oa_expiry].copy()
        if tk_df.empty:
            st.info(f"No data for {sel_ticker} at expiry {_sel_oa_expiry} on {sel_date}.")
            st.stop()
        st.caption(f"📅 Filtered to expiry: **{_sel_oa_expiry}**  |  Snapshot: {sel_date}")

    # Apply pressure inference
    pressure_list = tk_df.apply(lambda r: pd.Series(infer_pressure(r)), axis=1)
    tk_df = pd.concat([tk_df.reset_index(drop=True), pressure_list.reset_index(drop=True)], axis=1)
    tk_df["abs_oi"] = tk_df["change_OI_Call"].abs() + tk_df["change_OI_Put"].abs()
    tk_df = tk_df.sort_values("abs_oi", ascending=False)

    # Summary metrics
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Call OI Total Δ", f"{tk_df['change_OI_Call'].sum():+,.0f}")
    c2.metric("Put OI Total Δ", f"{tk_df['change_OI_Put'].sum():+,.0f}")
    total_c = tk_df["openInt_Call_now"].sum()
    total_p = tk_df["openInt_Put_now"].sum()
    pcr_val = total_p / total_c if total_c > 0 else 0
    c3.metric("PCR (OI)", f"{pcr_val:.2f}")
    c4.metric("Call Vol", f"{tk_df['vol_Call_now'].sum():,.0f}")
    c5.metric("Put Vol", f"{tk_df['vol_Put_now'].sum():,.0f}")

    # Strike table
    strike_display = tk_df.head(20)[["strike", "expiry_date", "change_OI_Call", "change_OI_Put",
                                      "openInt_Call_now", "openInt_Put_now",
                                      "vol_Call_now", "vol_Put_now",
                                      "call_signal", "put_signal", "net_view"]].copy()
    strike_display.columns = ["Strike", "Expiry", "Call OI Δ", "Put OI Δ",
                              "Call OI", "Put OI", "Call Vol", "Put Vol",
                              "Call Signal", "Put Signal", "Net View"]
    st.dataframe(strike_display, hide_index=True)

    # ── OI Change Visualization ──
    st.markdown("<div>📊 OI Change by Strike</div>", unsafe_allow_html=True)
    chart_df = tk_df.head(30).sort_values("strike")
    fig = make_subplots(rows=1, cols=1)
    fig.add_trace(go.Bar(x=chart_df["strike"], y=chart_df["change_OI_Call"],
                         name="Call OI Δ", marker_color="#00c853"))
    fig.add_trace(go.Bar(x=chart_df["strike"], y=chart_df["change_OI_Put"],
                         name="Put OI Δ", marker_color="#ff1744"))
    fig.update_layout(barmode="group", template="plotly_white",
                      xaxis_title="Strike", yaxis_title="OI Change",
                      height=400, margin=dict(t=30, b=40))
    st.plotly_chart(fig)

    # ── Multi-Day OI Accumulation & Conviction ──
    st.markdown("<div>📈 Multi-Day OI Accumulation & Strike Conviction</div>", unsafe_allow_html=True)
    _oi_days = st.slider("Lookback (trade days)", 3, 15, 7, key="oi_conv_days")

    with st.spinner(f"Loading {_oi_days}-day OI trend…"):
        _mdf, _mdates = _load_oi_multi_day(sel_ticker, _oi_days)

    if not _mdf.empty:
        # Spot price for ATM reference line
        _spot = 0.0
        try:
            _spot = _cached_price(sel_ticker)
        except Exception:
            pass

        # Cumulative OI build per strike (sum across all dates)
        _cum_agg = _mdf.groupby("strike").agg(
            cum_call=("change_OI_Call", "sum"),
            cum_put=("change_OI_Put", "sum"),
        ).reset_index()
        _cum_agg["cum_net"] = _cum_agg["cum_call"] - _cum_agg["cum_put"]
        _cum_agg = _cum_agg[
            (_cum_agg["cum_call"].abs() + _cum_agg["cum_put"].abs()) > 0
        ].sort_values("strike")

        _fig_trend = go.Figure()
        _fig_trend.add_trace(go.Bar(
            x=_cum_agg["strike"], y=_cum_agg["cum_call"],
            name=f"Cum Call OI Δ ({_oi_days}d)", marker_color="#00c853"))
        _fig_trend.add_trace(go.Bar(
            x=_cum_agg["strike"], y=_cum_agg["cum_put"],
            name=f"Cum Put OI Δ ({_oi_days}d)", marker_color="#ff1744"))
        _fig_trend.add_trace(go.Scatter(
            x=_cum_agg["strike"], y=_cum_agg["cum_net"],
            name="Net Bias (Call−Put)", mode="lines+markers",
            line=dict(color="#ffd600", width=2), marker=dict(size=5)))
        if _spot > 0:
            _fig_trend.add_vline(
                x=_spot, line_dash="dash", line_color="#aaaaaa",
                annotation_text=f"Spot ${_spot:.1f}", annotation_position="top right")
        _fig_trend.update_layout(
            barmode="group", template="plotly_dark",
            title=f"{sel_ticker} — {_oi_days}-Day Cumulative OI Build",
            xaxis_title="Strike", yaxis_title="Cumulative OI Change",
            height=430, margin=dict(t=55, b=40),
            legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0))
        st.plotly_chart(_fig_trend, use_container_width=True)

        # Conviction scoring
        _conv_df = _compute_oi_conviction(_mdf, _mdates, _spot)

        if not _conv_df.empty:
            _cv1, _cv2 = st.columns([3, 2])
            with _cv1:
                st.markdown(f"**🎯 Strike Conviction Table ({_oi_days}d build)**")
                _dc = _conv_df.head(15)[
                    ["strike", "direction", "conviction", "streak", "streak_dir",
                     "cum_net", "consistency", "n_days"]
                ].copy()
                _dc.columns = ["Strike", "Dir", "Score/10", "Streak", "Streak Dir",
                               "Cum Net OI Δ", "Consistency", "Days"]
                _dc["Cum Net OI Δ"] = _dc["Cum Net OI Δ"].apply(lambda x: f"{x:+,.0f}")
                _dc["Consistency"] = _dc["Consistency"].apply(lambda x: f"{x:.0%}")
                st.dataframe(_dc, hide_index=True)

            with _cv2:
                st.markdown("**📊 Market Bias Summary**")
                _bull_hi = len(_conv_df[(_conv_df["direction"] == "BULL") & (_conv_df["conviction"] >= 7)])
                _bear_hi = len(_conv_df[(_conv_df["direction"] == "BEAR") & (_conv_df["conviction"] >= 7)])
                _bias_lbl = ("🟢 BULLISH" if _bull_hi > _bear_hi
                             else "🔴 BEARISH" if _bear_hi > _bull_hi else "⚪ NEUTRAL")
                st.metric("High-Conv BULL strikes (≥7)", _bull_hi)
                st.metric("High-Conv BEAR strikes (≥7)", _bear_hi)
                st.markdown(f"**Overall: {_bias_lbl}**")
                if _spot > 0:
                    _atm_row = _conv_df[
                        (_conv_df["strike"] - _spot).abs() / _spot <= 0.05
                    ].head(1)
                    if not _atm_row.empty:
                        _ar = _atm_row.iloc[0]
                        _atm_icon = "🟢" if _ar["direction"] == "BULL" else "🔴" if _ar["direction"] == "BEAR" else "⚪"
                        st.markdown(f"**ATM zone bias:** {_atm_icon} {_ar['direction']} "
                                    f"({_ar['streak']}d streak, {_ar['conviction']:.0f}/10)")

            # High-conviction trade setup cards
            _hc = _conv_df[_conv_df["conviction"] >= 6].head(5)
            if not _hc.empty:
                st.markdown("### 🚀 High-Conviction Trade Ideas")
                st.caption(
                    f"Strikes where OI has been consistently building for {_oi_days} days. "
                    "Score ≥6/10. Not financial advice — confirm with price action.")

                for _, _r in _hc.iterrows():
                    _sk = float(_r["strike"])
                    _dir = str(_r["direction"])
                    _conv_val = float(_r["conviction"])
                    _stk = int(_r["streak"])
                    _consist = float(_r["consistency"])
                    _cum = float(_r["cum_net"])
                    _dist_pct = abs(_sk - _spot) / _spot * 100 if _spot > 0 else 0
                    _sk_type = "ATM" if _dist_pct < 3 else ("NTM" if _dist_pct < 8 else "OTM")
                    _icon = "🟢" if _dir == "BULL" else "🔴" if _dir == "BEAR" else "⚪"

                    if _dir == "BULL":
                        _setup = (f"Long ${_sk:.0f} Call" if _sk_type in ("ATM", "NTM")
                                  else f"Long ${_sk:.0f} Call (OTM — gamma if breaks out)")
                        _why = (
                            f"Call OI added for **{_stk} consecutive days** — smart money accumulating longs. "
                            f"Net +{_cum:,.0f} contracts over {_oi_days}d. "
                            f"Direction consistent **{_consist:.0%}** of sessions. "
                            f"{'ATM — high delta, maximum P&L sensitivity.' if _sk_type == 'ATM' else 'NTM — favorable risk/reward for directional bet.' if _sk_type == 'NTM' else 'OTM — lottery ticket; size small, wins big if breaks.'}"
                        )
                    elif _dir == "BEAR":
                        _setup = (f"Long ${_sk:.0f} Put" if _sk_type in ("ATM", "NTM")
                                  else f"${_sk:.0f} Resistance / Gamma Wall — sell calls above")
                        _why = (
                            f"Put OI building for **{_stk} consecutive days** — hedging or directional shorts loading. "
                            f"Net {_cum:,.0f} contracts over {_oi_days}d. "
                            f"Direction consistent **{_consist:.0%}** of sessions. "
                            f"{'ATM puts — high delta hedge or outright short.' if _sk_type == 'ATM' else 'NTM — downside protection, good risk/reward.' if _sk_type == 'NTM' else 'OTM put wall — gamma wall acts as magnet; stock could be pinned here.'}"
                        )
                    else:
                        continue

                    with st.container(border=True):
                        _tc1, _tc2, _tc3 = st.columns([3, 5, 1])
                        _tc1.markdown(f"{_icon} **{_setup}**  \n`{_sk_type}` · {_dist_pct:.1f}% from spot")
                        _tc2.markdown(_why)
                        _tc3.metric("Score", f"{_conv_val:.0f}/10")
    else:
        st.info(f"Not enough multi-day OI data for {sel_ticker} — need at least 3 trade dates.")

    # ── Expiry Breakdown ──
    st.markdown("<div>📅 Expiry Breakdown (Future Expiries)</div>", unsafe_allow_html=True)
    # Reload full ticker data for the breakdown (ignore expiry filter here)
    _tk_all = day_df[day_df["ticker"] == sel_ticker].copy()
    exp_agg = _tk_all.groupby("expiry_date").agg(
        Call_OI_Chg=("change_OI_Call", "sum"),
        Put_OI_Chg=("change_OI_Put", "sum"),
        Call_OI=("openInt_Call_now", "sum"),
        Put_OI=("openInt_Put_now", "sum"),
    ).reset_index()
    exp_agg["PCR"] = np.where(exp_agg["Call_OI"] > 0, exp_agg["Put_OI"] / exp_agg["Call_OI"], 0).round(2)
    # Sort expiries chronologically and compute DTE
    def _dte_from_exp(exp_str, ref=sel_date):
        try:
            ep = exp_str.split("-"); rp = ref.split("-")
            import datetime as _dmod
            ed = _dmod.date(int(ep[2]), int(ep[0]), int(ep[1]))
            rd = _dmod.date(int(rp[2]), int(rp[0]), int(rp[1]))
            return (ed - rd).days
        except Exception:
            return 9999
    exp_agg["DTE"] = exp_agg["expiry_date"].apply(_dte_from_exp)
    exp_agg["Status"] = exp_agg["DTE"].apply(lambda d: "🟢 Future" if d >= 0 else "🔴 Expired")
    exp_agg = exp_agg.sort_values("DTE")
    # Show future expiries first
    exp_future = exp_agg[exp_agg["DTE"] >= 0]
    exp_past   = exp_agg[exp_agg["DTE"] < 0]
    st.markdown("**🟢 Future Expiries**")
    if not exp_future.empty:
        st.dataframe(
            exp_future[["expiry_date","DTE","Call_OI_Chg","Put_OI_Chg","Call_OI","Put_OI","PCR"]].rename(
                columns={"expiry_date":"Expiry","Call_OI_Chg":"Call OI Δ","Put_OI_Chg":"Put OI Δ",
                         "Call_OI":"Call OI","Put_OI":"Put OI"}),
            hide_index=True, use_container_width=True)
    if not exp_past.empty:
        with st.expander(f"🔴 Past / Expired ({len(exp_past)} expiries)"):
            st.dataframe(
                exp_past[["expiry_date","DTE","Call_OI_Chg","Put_OI_Chg","Call_OI","Put_OI","PCR"]].rename(
                    columns={"expiry_date":"Expiry","Call_OI_Chg":"Call OI Δ","Put_OI_Chg":"Put OI Δ",
                             "Call_OI":"Call OI","Put_OI":"Put OI"}),
                hide_index=True, use_container_width=True)

    # ── Next-Day OI Prediction ──
    st.markdown("<div>🔮 OI-Based Next-Day Prediction</div>", unsafe_allow_html=True)
    with st.spinner("Analyzing OI signals..."):
        pred_df, accuracy = oi_prediction_analysis(sel_ticker, dates_back=8)

    if pred_df is not None and not pred_df.empty:
        latest_pred = pred_df.iloc[0]
        c1, c2, c3, c4 = st.columns(4)
        pred_color = "normal" if latest_pred.get("prediction") == "BULLISH" else "inverse"

        c1.metric("Prediction", latest_pred.get("prediction", "N/A"),
                  delta=f"Score: {latest_pred.get('composite', 0):+d}", delta_color=pred_color)
        c2.metric("PCR (OI)", f"{latest_pred.get('pcr_oi', 0):.2f}")
        c3.metric("Net OI Bias", f"{latest_pred.get('net_oi_bias', 0):+,.0f}")
        if accuracy is not None:
            c4.metric("Backtest Accuracy", f"{accuracy:.0f}%")
        else:
            c4.metric("Backtest Accuracy", "N/A")

        # Prediction reasoning
        st.markdown("**Signal Components:**")
        sig_cols = st.columns(3)
        oi_s = latest_pred.get("oi_signal", 0)
        pcr_s = latest_pred.get("pcr_signal", 0)
        vol_s = latest_pred.get("vol_signal", 0)
        sig_cols[0].markdown(f"OI Bias: **{'🟢 BULL' if oi_s > 0 else '🔴 BEAR' if oi_s < 0 else '⚪ FLAT'}**")
        sig_cols[1].markdown(f"PCR Signal: **{'🟢 BULL' if pcr_s > 0 else '🔴 BEAR' if pcr_s < 0 else '⚪ FLAT'}**")
        sig_cols[2].markdown(f"Volume PCR: **{'🟢 BULL' if vol_s > 0 else '🔴 BEAR' if vol_s < 0 else '⚪ FLAT'}**")

        # History table
        show_cols = ["trade_date", "prediction", "composite", "net_oi_bias", "pcr_oi"]
        if "actual_pct" in pred_df.columns:
            show_cols.append("actual_pct")
        st.dataframe(pred_df[show_cols].head(10), hide_index=True)
    else:
        st.info("Not enough data for prediction analysis.")

    # ── Short Interest — Next-Day Context ──
    st.markdown("<div>📉 Short Interest & Next-Day Squeeze Probability</div>", unsafe_allow_html=True)
    try:
        _si = _get_short_data_dash(sel_ticker)
        _si_spf = _si.get("short_pct_float")
        _si_sr  = _si.get("short_ratio")
        _si_ss  = _si.get("shares_short")
        _si_flt = _si.get("float_shares")
        _si_ssp = _si.get("shares_short_prior")
        _si_sc  = _si.get("squeeze_score", 0)
        _si_lbl = _si.get("squeeze_label", "N/A")

        def _si_fmt2(n):
            if n is None: return "N/A"
            if n >= 1e9: return f"{n/1e9:.2f}B"
            if n >= 1e6: return f"{n/1e6:.1f}M"
            return f"{n:,.0f}"

        _si_mo_chg = (_si_ss - _si_ssp) / _si_ssp * 100 if _si_ss and _si_ssp else None
        _si_c1, _si_c2, _si_c3, _si_c4 = st.columns(4)
        _si_c1.metric("Short % Float", f"{_si_spf:.1f}%" if _si_spf else "N/A",
            delta=f"{_si_mo_chg:+.1f}% MoM" if _si_mo_chg else None)
        _si_c2.metric("Days to Cover", f"{_si_sr:.1f}d" if _si_sr else "N/A")
        _si_c3.metric("Shares Short",  _si_fmt2(_si_ss))
        _si_c4.metric("Squeeze Score", f"{_si_sc}/10  [{_si_lbl}]")

        # Next-day implications
        _si_insights = []
        if _si_spf and _si_spf >= 20:
            _si_insights.append(f"🔴 **Heavy short load ({_si_spf:.1f}%)** — upside gap could trigger cascade covering")
        if _si_sr and _si_sr >= 5:
            _si_insights.append(f"⏱️ **{_si_sr:.1f} days to cover** — prolonged covering = extended rally if squeeze starts")
        if _si_mo_chg and _si_mo_chg > 15:
            _si_insights.append(f"📈 Short interest rising +{_si_mo_chg:.1f}% MoM → bearish consensus building; contrarian squeeze potential")
        elif _si_mo_chg and _si_mo_chg < -15:
            _si_insights.append(f"📉 Short interest falling {_si_mo_chg:.1f}% MoM → shorts already covering; less downside pressure")
        if _si_sc >= 7:
            _si_insights.append("🚀 **SQUEEZE SETUP** — combine with OI bullish signal above for high-conviction long entry")
        if pred_df is not None and not pred_df.empty:
            _pred = pred_df.iloc[0].get("prediction", "NEUTRAL")
            if _pred == "BULLISH" and _si_sc >= 5:
                _si_insights.append(f"✅ **OI signal BULLISH + squeeze score {_si_sc}/10** → strong next-day long setup")
            elif _pred == "BEARISH" and _si_sc >= 7:
                _si_insights.append(f"⚠️ OI signal BEARISH but extreme squeeze risk — short could backfire; use caution")
        for _ins in _si_insights:
            st.markdown(_ins)
        if not _si_insights:
            st.markdown(f"🟢 Short interest normal — no unusual squeeze risk for {sel_ticker}")
    except Exception as _si_ex:
        st.info(f"Short interest data unavailable: {_si_ex}")

    # ═══════════════════════════════════════════════════════════════
    # ── OI ADVISOR — Position-aware writeup + LLM-style Q&A ───────
    # ═══════════════════════════════════════════════════════════════
    st.markdown("---")
    st.markdown("## 🧠 OI Advisor — Position Analysis & Q&A")

    # ── Load open positions for this ticker ───────────────────────
    _pos_df = q("SELECT * FROM trades WHERE status='OPEN' AND ticker=?", [sel_ticker])

    # ── Build OI context dict (used by writeup + Q&A engine) ──────
    _call_chg  = float(tk_df["change_OI_Call"].sum())
    _put_chg   = float(tk_df["change_OI_Put"].sum())
    _call_oi   = float(tk_df["openInt_Call_now"].sum())
    _put_oi    = float(tk_df["openInt_Put_now"].sum())
    _call_vol  = float(tk_df["vol_Call_now"].sum())
    _put_vol   = float(tk_df["vol_Put_now"].sum())
    _pcr       = _put_oi / _call_oi if _call_oi > 0 else 0
    _vol_pcr   = _put_vol / _call_vol if _call_vol > 0 else 0
    _net_bias  = _call_chg - _put_chg
    _top_call_strike = tk_df.nlargest(1, "change_OI_Call")["strike"].iloc[0] if not tk_df.empty else 0
    _top_put_strike  = tk_df.nlargest(1, "change_OI_Put")["strike"].iloc[0]  if not tk_df.empty else 0
    _top_abs_row     = tk_df.nlargest(1, "abs_oi").iloc[0] if not tk_df.empty else None

    _oi_bias   = "BULLISH" if _call_chg > abs(_put_chg) * 1.2 else ("BEARISH" if _put_chg > abs(_call_chg) * 1.2 else "NEUTRAL")
    _pcr_bias  = "BEARISH" if _pcr > 1.3 else ("BULLISH" if _pcr < 0.7 else "NEUTRAL")
    _vol_bias  = "BEARISH" if _vol_pcr > 1.3 else ("BULLISH" if _vol_pcr < 0.7 else "NEUTRAL")

    # Signals agree count (0–3)
    _all_bias  = [_oi_bias, _pcr_bias, _vol_bias]
    _bull_ct   = _all_bias.count("BULLISH")
    _bear_ct   = _all_bias.count("BEARISH")
    _conviction = "HIGH" if max(_bull_ct, _bear_ct) >= 3 else ("MEDIUM" if max(_bull_ct, _bear_ct) == 2 else "LOW")
    _direction  = "BULLISH" if _bull_ct > _bear_ct else ("BEARISH" if _bear_ct > _bull_ct else "NEUTRAL")

    # Live price — AH-aware
    try:
        _live_px_eod = _cached_price(sel_ticker)
        _live_ah_d   = _get_ah_price(sel_ticker)
        _live_px = (_live_ah_d["spot_ah"] if _live_ah_d["spot_ah"] > 0 else _live_px_eod) \
                   if st.session_state.get("use_ah") else _live_px_eod
        if _live_ah_d["is_extended"]:
            st.info(f"🌙 **{sel_ticker}** — EOD ${_live_px_eod:.2f}  →  {_live_ah_d['label']} **${_live_ah_d['spot_ah']:.2f}** "
                    f"({_live_ah_d['ah_chg_pct']:+.1f}%)  {'*(used for calcs)*' if st.session_state.get('use_ah') else '*(toggle AH mode to use)*'}")
    except Exception:
        _live_px = 0

    # ── 1. OI Writeup ─────────────────────────────────────────────
    with st.expander("📝 OI Market Writeup", expanded=True):
        _w = []
        _w.append(f"**{sel_ticker}** — OI analysis as of **{sel_date}**")
        _w.append("")

        # What OI is doing
        if _call_chg > 0 and _put_chg > 0:
            _w.append(f"**OI Flow:** Both call and put OI are building — Call +{_call_chg:,.0f}, Put +{_put_chg:,.0f}. "
                      f"This is an **event-driven setup** (earnings/catalyst). Market is pricing in a big move but direction is unclear.")
        elif _call_chg > 0 and _put_chg <= 0:
            _w.append(f"**OI Flow:** Call OI increasing (+{_call_chg:,.0f}) while put OI shrinking ({_put_chg:,.0f}). "
                      f"Participants are **opening new call positions / closing puts** — classically bullish positioning.")
        elif _put_chg > 0 and _call_chg <= 0:
            _w.append(f"**OI Flow:** Put OI increasing (+{_put_chg:,.0f}) while call OI shrinking ({_call_chg:,.0f}). "
                      f"Participants are **opening new put positions / closing calls** — classically bearish/defensive positioning.")
        else:
            _w.append(f"**OI Flow:** Both call ({_call_chg:,.0f}) and put ({_put_chg:,.0f}) OI are declining. "
                      f"Positions being closed out — possible **post-event unwinding**. Low conviction environment.")

        # PCR interpretation
        _w.append("")
        if _pcr > 1.5:
            _w.append(f"**PCR = {_pcr:.2f}** (Bearish — heavy put accumulation). When PCR exceeds 1.5, it signals "
                      f"either **institutional hedging** of long positions or outright bearish bets. At extreme levels (>2.0) "
                      f"it can be contrarian bullish — too much fear often marks a bottom.")
        elif _pcr > 1.0:
            _w.append(f"**PCR = {_pcr:.2f}** (Mildly bearish). More put OI than call OI suggests **cautious or hedged** positioning. "
                      f"Not yet at panic levels — watch for PCR rising further.")
        elif _pcr < 0.7:
            _w.append(f"**PCR = {_pcr:.2f}** (Bullish — call-heavy). Calls dominate open interest. "
                      f"Suggests **bullish speculation** or covered call selling. If stock is already extended, high call OI can act as resistance.")
        else:
            _w.append(f"**PCR = {_pcr:.2f}** (Neutral). Balanced put/call ratio — no strong directional conviction from options market.")

        # Key strike
        if _top_abs_row is not None:
            _ts = float(_top_abs_row["strike"])
            _tc = float(_top_abs_row["change_OI_Call"])
            _tp = float(_top_abs_row["change_OI_Put"])
            _w.append("")
            _w.append(f"**Key Strike: ${_ts:.0f}** — highest OI activity (Call Δ {_tc:+,.0f}, Put Δ {_tp:+,.0f}). "
                      f"This strike acts as a **magnet / pin level** heading into expiry. "
                      f"{'Market makers are net short calls here — potential resistance at this level.' if _tc > abs(_tp) else 'Heavy put accumulation here — acts as support or target for a bearish move.'}")

        # Overall signal
        _w.append("")
        if _direction == "BULLISH":
            _w.append(f"**Overall Signal: BULLISH ({_conviction} conviction)** — {_bull_ct}/3 signals agree (OI flow: {_oi_bias}, PCR: {_pcr_bias}, Vol PCR: {_vol_bias}). "
                      f"Options market is leaning long on {sel_ticker}.")
        elif _direction == "BEARISH":
            _w.append(f"**Overall Signal: BEARISH ({_conviction} conviction)** — {_bear_ct}/3 signals agree (OI flow: {_oi_bias}, PCR: {_pcr_bias}, Vol PCR: {_vol_bias}). "
                      f"Options market is leaning short/defensive on {sel_ticker}.")
        else:
            _w.append(f"**Overall Signal: NEUTRAL (LOW conviction)** — Signals are mixed (OI: {_oi_bias}, PCR: {_pcr_bias}, Vol PCR: {_vol_bias}). "
                      f"No clear directional edge from options flow.")

        st.markdown("\n\n".join(_w))

    # ── 2. Your Positions vs OI Signal ────────────────────────────
    if not _pos_df.empty:
        with st.expander(f"💼 Your {sel_ticker} Positions vs OI Signal", expanded=True):
            for _, _tr in _pos_df.iterrows():
                _qty    = int(_tr.get("quantity", 1))
                _side   = "BUY" if _qty >= 0 else "SELL"
                _otype  = str(_tr.get("option_type", "?")).upper()
                _strike = float(_tr.get("strike", 0))
                _entry  = float(_tr.get("entry_price", 0))
                _expiry = str(_tr.get("expiry", "?"))
                _tid    = _tr.get("trade_id", "?")

                # Current option price estimate
                try:
                    _opt_chain = yf.Ticker(sel_ticker).option_chain(_expiry[:10])
                    _chain_df  = _opt_chain.calls if _otype == "CALL" else _opt_chain.puts
                    _near      = _chain_df[_chain_df["strike"] == _strike]
                    _cur_px    = float(_near["lastPrice"].iloc[0]) if not _near.empty else _entry
                except Exception:
                    _cur_px = _entry

                _pnl       = (_cur_px - _entry) * _qty * 100
                _pnl_pct   = (_pnl / abs(_entry * _qty * 100) * 100) if _entry > 0 else 0

                # Alignment check
                _pos_bull = (_side == "BUY" and _otype == "CALL") or (_side == "SELL" and _otype == "PUT")
                _aligned  = (_pos_bull and _direction == "BULLISH") or (not _pos_bull and _direction == "BEARISH")

                _c1, _c2 = st.columns([1, 2])
                with _c1:
                    st.markdown(f"**#{_tid} {_side} {_otype} ${_strike:.0f}** exp {_expiry[:10]}")
                    st.markdown(f"Entry: **${_entry:.2f}** | Current: **${_cur_px:.2f}**")
                    if _pnl >= 0:
                        st.success(f"P&L: **${_pnl:+,.0f}** ({_pnl_pct:+.1f}%)")
                    else:
                        st.error(f"P&L: **${_pnl:+,.0f}** ({_pnl_pct:+.1f}%)")
                with _c2:
                    if _aligned:
                        st.success(f"✅ **OI ALIGNED** — OI signal ({_direction}) supports your {_side} {_otype} position.")
                        if _pnl > 0:
                            st.markdown("**Recommendation:** OI flow confirms your direction and you're profitable. "
                                        "Consider trailing stop or partial exit to lock in gains.")
                        else:
                            st.markdown("**Recommendation:** OI is still supportive even though you're underwater. "
                                        "Reevaluate at next expiry cycle — if OI signal weakens, cut the loss.")
                    else:
                        st.warning(f"⚠️ **OI OPPOSING** — OI signal ({_direction}) is AGAINST your {_side} {_otype} position.")
                        if _pnl > 0:
                            st.markdown("**Recommendation:** You're profitable but OI is turning against you. "
                                        "**Consider taking profits now.** Don't let a winner become a loser.")
                        else:
                            st.markdown("**Recommendation:** Both P&L and OI signal are working against you. "
                                        "**Seriously consider cutting this position.** The options market disagrees with your thesis.")

                # Strike proximity check
                if _live_px > 0 and _strike > 0:
                    _dist_pct = abs(_live_px - _strike) / _live_px * 100
                    if _dist_pct < 2:
                        st.info(f"Strike ${_strike:.0f} is {_dist_pct:.1f}% from current price ${_live_px:.2f} — **near ATM, high gamma risk.**")
                    elif _dist_pct > 15:
                        st.warning(f"Strike ${_strike:.0f} is {_dist_pct:.1f}% from current price — **deep OTM, needs a large move to profit.**")
                st.markdown("---")
    else:
        st.info(f"No open positions for {sel_ticker}. Use the Portfolio page to add trades.")

    # ── 3. LLM-style Q&A ──────────────────────────────────────────
    st.markdown("### 💬 Ask About This OI Data")
    st.caption("Ask anything about the OI signals, your positions, or what to do next.")

    # Build context string for the answer engine
    def _oi_answer(question: str) -> str:
        q_low = question.lower()
        # Context shorthand
        tk = sel_ticker
        px_s = f"${_live_px:.2f}" if _live_px > 0 else "unknown"
        n_pos = len(_pos_df)

        # ── What is happening / overview ──────────────────────────
        if any(w in q_low for w in ["what is happening", "what's happening", "overview", "summarize", "summary", "explain"]):
            return (f"**{tk} OI Overview ({sel_date}):**\n\n"
                    f"- Call OI change: **{_call_chg:+,.0f}** | Put OI change: **{_put_chg:+,.0f}**\n"
                    f"- PCR (open interest): **{_pcr:.2f}** → {_pcr_bias}\n"
                    f"- Volume PCR: **{_vol_pcr:.2f}** → {_vol_bias}\n"
                    f"- Overall signal: **{_direction}** ({_conviction} conviction)\n\n"
                    f"{'Call OI is building faster than puts — smart money is positioning bullishly on ' + tk + '.' if _direction == 'BULLISH' else ('Put OI is building faster — defensive/bearish positioning dominates on ' + tk + '.' if _direction == 'BEARISH' else 'No clear directional signal — mixed flow on ' + tk + '.')}")

        # ── Should I hold ─────────────────────────────────────────
        elif any(w in q_low for w in ["should i hold", "hold my", "keep my", "should i keep"]):
            if _pos_df.empty:
                return f"You don't have any open {tk} positions recorded. Add them in the Portfolio page first."
            _ans = []
            for _, _tr in _pos_df.iterrows():
                _s = "BUY" if int(_tr.get("quantity",1)) >= 0 else "SELL"
                _o = str(_tr.get("option_type","?")).upper()
                _st = float(_tr.get("strike",0))
                _pb = (_s == "BUY" and _o == "CALL") or (_s == "SELL" and _o == "PUT")
                _aln = (_pb and _direction == "BULLISH") or (not _pb and _direction == "BEARISH")
                if _aln:
                    _ans.append(f"✅ **{_s} {_o} ${_st:.0f}** — OI SUPPORTS holding. Signal aligned.")
                else:
                    _ans.append(f"⚠️ **{_s} {_o} ${_st:.0f}** — OI OPPOSES your position. Consider exiting.")
            return "\n".join(_ans)

        # ── Should I exit / close ─────────────────────────────────
        elif any(w in q_low for w in ["should i exit", "should i close", "close my", "exit my", "take profit", "cut loss"]):
            if _pos_df.empty:
                return f"No open {tk} positions found."
            _ans = []
            for _, _tr in _pos_df.iterrows():
                _s = "BUY" if int(_tr.get("quantity",1)) >= 0 else "SELL"
                _o = str(_tr.get("option_type","?")).upper()
                _st = float(_tr.get("strike",0))
                _ep = float(_tr.get("entry_price",0))
                _exp= str(_tr.get("expiry","?"))
                _pb = (_s == "BUY" and _o == "CALL") or (_s == "SELL" and _o == "PUT")
                _aln = (_pb and _direction == "BULLISH") or (not _pb and _direction == "BEARISH")
                if not _aln:
                    _ans.append(f"**{_s} {_o} ${_st:.0f} (exp {_exp[:10]}):** OI is going against you → **EXIT recommended.**")
                else:
                    _ans.append(f"**{_s} {_o} ${_st:.0f}:** OI still supportive → Hold, but set a stop at entry price.")
            return "\n\n".join(_ans) if _ans else "No clear exit signal based on current OI."

        # ── PCR explanation ───────────────────────────────────────
        elif any(w in q_low for w in ["pcr", "put call", "put/call"]):
            return (f"**Put/Call Ratio (PCR) for {tk}: {_pcr:.2f}**\n\n"
                    f"PCR = Total Put OI ÷ Total Call OI = {_put_oi:,.0f} ÷ {_call_oi:,.0f}\n\n"
                    f"- PCR < 0.7 → Bullish (more calls than puts)\n"
                    f"- PCR 0.7–1.0 → Neutral/Slightly Bullish\n"
                    f"- PCR 1.0–1.3 → Neutral/Slightly Bearish\n"
                    f"- PCR > 1.3 → Bearish (heavy put accumulation)\n\n"
                    f"**Current {_pcr:.2f} → {_pcr_bias}.** "
                    f"{'This means for every call contract open, there are {:.1f} put contracts — bearish bias.'.format(_pcr) if _pcr > 1 else 'More call OI than puts — market leaning bullish.'}")

        # ── Key strike / support / resistance ────────────────────
        elif any(w in q_low for w in ["key strike", "support", "resistance", "important strike", "pin", "magnet", "wall"]):
            if _top_abs_row is None:
                return "No strike data available."
            _ts = float(_top_abs_row["strike"])
            _tc = float(_top_abs_row["change_OI_Call"])
            _tp = float(_top_abs_row["change_OI_Put"])
            return (f"**Key Strike for {tk}: ${_ts:.0f}**\n\n"
                    f"Call OI Δ: {_tc:+,.0f} | Put OI Δ: {_tp:+,.0f}\n\n"
                    f"This strike has the highest combined OI activity. It often acts as a **pin/magnet** as expiry approaches — "
                    f"market makers hedge there and price tends to gravitate toward it. "
                    f"{'Heavy call writing at ${:.0f} creates resistance — price may struggle to break above.'.format(_ts) if _tc > abs(_tp) else 'Heavy put accumulation at ${:.0f} — acts as a downside target or support level.'.format(_ts)}\n\n"
                    f"Current price: **{px_s}** | Strike distance: **{abs(_live_px - _ts) / _live_px * 100:.1f}%** away.")

        # ── Bullish / bearish ─────────────────────────────────────
        elif any(w in q_low for w in ["bullish", "bearish", "direction", "signal", "bias"]):
            return (f"**{tk} OI Direction: {_direction} ({_conviction} conviction)**\n\n"
                    f"| Signal | Value | Bias |\n|---|---|---|\n"
                    f"| OI Flow | Call {_call_chg:+,.0f} / Put {_put_chg:+,.0f} | {_oi_bias} |\n"
                    f"| PCR (OI) | {_pcr:.2f} | {_pcr_bias} |\n"
                    f"| Vol PCR | {_vol_pcr:.2f} | {_vol_bias} |\n\n"
                    f"{_bull_ct}/3 signals bullish, {_bear_ct}/3 bearish.")

        # ── What should I do ─────────────────────────────────────
        elif any(w in q_low for w in ["what should i do", "what to do", "recommend", "advice", "action", "next step"]):
            _actions = []
            if _direction == "BULLISH" and _conviction in ("HIGH","MEDIUM"):
                _actions.append(f"📈 **OI is bullish on {tk}.** Consider buying calls or bull call spreads near the ${_top_call_strike:.0f} strike.")
                _actions.append(f"If you already have call positions and OI is supportive, **hold and trail your stop.**")
            elif _direction == "BEARISH" and _conviction in ("HIGH","MEDIUM"):
                _actions.append(f"📉 **OI is bearish on {tk}.** Consider buying puts or bear put spreads near the ${_top_put_strike:.0f} strike.")
                _actions.append(f"If you have long call positions, **OI is working against them — review your stop.**")
            else:
                _actions.append(f"⚪ **Mixed/neutral OI signal on {tk}.** No strong directional edge — avoid new directional bets.")
                _actions.append(f"Consider selling premium (iron condor, short strangle) if IV is elevated.")
            if not _pos_df.empty:
                _actions.append(f"\n**For your {len(_pos_df)} open position(s):** See the Position Analysis section above for specific per-trade guidance.")
            return "\n\n".join(_actions)

        # ── Volume / unusual activity ─────────────────────────────
        elif any(w in q_low for w in ["volume", "unusual", "activity", "flow"]):
            return (f"**{tk} Options Volume:**\n\n"
                    f"- Call volume: **{_call_vol:,.0f}**\n"
                    f"- Put volume: **{_put_vol:,.0f}**\n"
                    f"- Volume PCR: **{_vol_pcr:.2f}** → {_vol_bias}\n\n"
                    f"{'High put volume relative to calls suggests defensive buying or bearish bets.' if _vol_pcr > 1.3 else ('High call volume — directional bullish bets or covered call selling.' if _vol_pcr < 0.7 else 'Balanced volume — no unusual directional flow detected.')}")

        # ── Expiry / when to trade ────────────────────────────────
        elif any(w in q_low for w in ["expiry", "expiration", "when", "which expiry", "what expiry"]):
            _exp_agg = tk_df.groupby("expiry_date").agg(
                total_oi=("openInt_Call_now","sum")).reset_index().sort_values("total_oi", ascending=False)
            if _exp_agg.empty:
                return "No expiry data available."
            _top_exp = _exp_agg.iloc[0]["expiry_date"]
            return (f"**Most Active Expiry for {tk}: {_top_exp}**\n\n"
                    f"This expiry has the highest total open interest. For short-term directional trades, "
                    f"focus on this cycle. For longer-term plays, the next expiry gives more time for the thesis to play out.\n\n"
                    f"**Rule of thumb:** If signal is strong ({_conviction} conviction), buy the nearest expiry with decent OI. "
                    f"If signal is weak or mixed, use a longer expiry (30–45 DTE) to give yourself time.")

        # ── My positions ─────────────────────────────────────────
        elif any(w in q_low for w in ["my position", "my trade", "my call", "my put", "what do i have"]):
            if _pos_df.empty:
                return f"No open {tk} positions found in the database."
            _lines = [f"**Your open {tk} positions:**"]
            for _, _tr in _pos_df.iterrows():
                _s = "BUY" if int(_tr.get("quantity",1)) >= 0 else "SELL"
                _o = str(_tr.get("option_type","?")).upper()
                _st = float(_tr.get("strike",0))
                _ep = float(_tr.get("entry_price",0))
                _exp= str(_tr.get("expiry","?"))
                _lines.append(f"- #{_tr['trade_id']}: {_s} {_o} ${_st:.0f} exp {_exp[:10]} @ ${_ep:.2f}")
            return "\n".join(_lines)

        # ── Fallback ─────────────────────────────────────────────
        else:
            return (f"I can answer questions about **{tk}** OI data. Try asking:\n\n"
                    f"- *What is happening?*\n"
                    f"- *Is it bullish or bearish?*\n"
                    f"- *What is the PCR?*\n"
                    f"- *What is the key strike?*\n"
                    f"- *Should I hold my position?*\n"
                    f"- *Should I exit?*\n"
                    f"- *What should I do?*\n"
                    f"- *Which expiry should I use?*\n"
                    f"- *What is the volume saying?*")

    # Quick-question buttons
    _qq_cols = st.columns(4)
    _quick_qs = ["What is happening?", "Is it bullish or bearish?",
                 "Should I hold my position?", "What should I do?"]
    for _i, (_col, _qq) in enumerate(zip(_qq_cols, _quick_qs)):
        if _col.button(_qq, key=f"oi_qq_{_i}"):
            st.session_state[f"oi_qa_{sel_ticker}"] = st.session_state.get(f"oi_qa_{sel_ticker}", [])
            st.session_state[f"oi_qa_{sel_ticker}"].append({"role":"user","content":_qq})
            st.session_state[f"oi_qa_{sel_ticker}"].append({"role":"assistant","content":_oi_answer(_qq)})

    # Chat history
    _chat_key = f"oi_qa_{sel_ticker}"
    if _chat_key not in st.session_state:
        st.session_state[_chat_key] = []

    for _msg in st.session_state[_chat_key]:
        with st.chat_message(_msg["role"]):
            st.markdown(_msg["content"])

    # Chat input
    if _user_q := st.chat_input(f"Ask about {sel_ticker} OI data..."):
        st.session_state[_chat_key].append({"role":"user","content":_user_q})
        _answer = _oi_answer(_user_q)
        st.session_state[_chat_key].append({"role":"assistant","content":_answer})
        with st.chat_message("user"):
            st.markdown(_user_q)
        with st.chat_message("assistant"):
            st.markdown(_answer)

    if st.session_state[_chat_key]:
        if st.button("🗑️ Clear Chat", key="clear_oi_chat"):
            st.session_state[_chat_key] = []
            st.rerun()

    # ── Weekly Strike Comparison ──
    st.markdown("<div>📈 Weekly OI Comparison (Same Strikes)</div>", unsafe_allow_html=True)
    if len(dates) >= 2:
        wc1, wc2, wc3 = st.columns(3)
        with wc1:
            date_from = st.selectbox("From Date (snapshot)", dates[1:], index=0, key="wk_from")
        with wc2:
            date_to = st.selectbox("To Date (snapshot)", dates, index=0, key="wk_to")
        with wc3:
            # Load future expiry dates for the selected ticker from the latest snapshot
            _latest_date = dates[0]
            _expiry_raw = q(
                "SELECT DISTINCT expiry_date FROM options_change "
                "WHERE ticker=? AND trade_date_now=? ORDER BY expiry_date",
                [sel_ticker, _latest_date]
            )["expiry_date"].tolist() if sel_ticker else []

            # Sort expiry dates chronologically (stored as MM-DD-YYYY)
            def _exp_sort_key(d):
                try:
                    p = d.split("-")
                    return (int(p[2]), int(p[0]), int(p[1]))
                except Exception:
                    return (9999, 99, 99)

            _expiry_sorted = sorted(_expiry_raw, key=_exp_sort_key)
            _expiry_opts = ["All Expiries"] + _expiry_sorted
            _sel_expiry = st.selectbox("Filter by Expiry", _expiry_opts, index=0, key="wk_expiry")

        if date_from != date_to:
            weekly_df = oi_weekly_strike_analysis(sel_ticker, date_from, date_to)
            if not weekly_df.empty:
                # Apply expiry filter
                if _sel_expiry != "All Expiries":
                    weekly_df = weekly_df[weekly_df["expiry_date"] == _sel_expiry]

                if weekly_df.empty:
                    st.info(f"No strikes found for expiry {_sel_expiry} between the selected dates.")
                else:
                    # Show expiry summary bar across top
                    _exp_summary = weekly_df.groupby("expiry_date").agg(
                        call_oi_chg=("c_oi_chg", "sum"),
                        put_oi_chg=("p_oi_chg", "sum"),
                    ).reset_index().sort_values("expiry_date")
                    _exp_summary.columns = ["Expiry", "Net Call OI Δ", "Net Put OI Δ"]
                    st.caption(f"📅 Expiry breakdown — trade dates: **{date_from}** → **{date_to}**")
                    st.dataframe(_exp_summary, hide_index=True, use_container_width=True)

                    disp = weekly_df[["strike", "expiry_date", "c_oi_chg", "p_oi_chg",
                                      "c_oi_pct", "p_oi_pct", "c_px_chg", "p_px_chg",
                                      "classification", "escape_score", "escape_label"]].head(40).copy()
                    disp.columns = ["Strike", "Expiry", "Call OI Δ", "Put OI Δ",
                                    "Call OI %Δ", "Put OI %Δ", "Call Px Δ", "Put Px Δ",
                                    "Classification", "Escape Score", "Escape"]

                    def _wk_color(row):
                        cls = str(row.get("Classification", ""))
                        if "ACCUMULATION" in cls or "ROLL → CALLS" in cls:
                            return ["background-color:#1a4a1a; color:#e8ffe8"] * len(row)
                        elif "LIQUIDATION" in cls or "ROLL → PUTS" in cls:
                            return ["background-color:#4a1a1a; color:#ffe8e8"] * len(row)
                        elif "WRITING" in cls:
                            return ["background-color:#3a3a10; color:#fffff0"] * len(row)
                        elif "HEDGE" in cls:
                            return ["background-color:#1a2a4a; color:#e8f0ff"] * len(row)
                        return [""] * len(row)

                    st.dataframe(
                        disp.style.apply(_wk_color, axis=1),
                        hide_index=True, use_container_width=True
                    )

                    # Classification summary
                    class_counts = weekly_df["classification"].value_counts()
                    fig = px.pie(values=class_counts.values, names=class_counts.index,
                                 title="Activity Breakdown", hole=0.4,
                                 color_discrete_sequence=px.colors.qualitative.Set2)
                    fig.update_layout(template="plotly_white", height=320)
                    st.plotly_chart(fig, use_container_width=True)
            else:
                st.info("No matching strikes between these dates.")
    else:
        st.info("Need at least 2 trade dates in the database to compare.")


# ===================================================================
# ──  PAGE 3: PROP TRADING SCREEN
# ===================================================================
elif page == "🎯 Prop Trading Screen":
    _hdr1, _hdr2 = st.columns([4, 1])
    with _hdr1:
        st.markdown("## 🎯 Prop Trading Opportunities")
    with _hdr2:
        if st.button("🔄 Refresh", key="refresh_prop"):
            st.rerun()
        with st.popover("ℹ️"):
            st.markdown(_PAGE_HELP.get(page, ""))
    st.markdown("*Institutional-grade opportunity scanner with risk assessment*")

    dates = available_trade_dates()
    if not dates:
        st.warning("No data.")
        st.stop()

    c1, c2, c3 = st.columns([1, 1, 1])
    with c1:
        prop_date = st.selectbox("📅 Scan Date (snapshot)", dates, index=0)
    with c2:
        min_z = st.slider("Min Z-Score", 1.0, 4.0, 1.5, 0.25)
    with c3:
        # Load all future expiry dates from this snapshot
        _prop_exp_raw = q(
            "SELECT DISTINCT expiry_date FROM options_change WHERE trade_date_now=?",
            [prop_date]
        )["expiry_date"].tolist() if prop_date else []
        def _prop_exp_sort(d):
            try:
                p = d.split("-"); return (int(p[2]),int(p[0]),int(p[1]))
            except Exception:
                return (9999,99,99)
        def _prop_is_future(d, ref=prop_date):
            try:
                import datetime as _dmod
                ep = d.split("-"); rp = ref.split("-")
                return _dmod.date(int(ep[2]),int(ep[0]),int(ep[1])) >= _dmod.date(int(rp[2]),int(rp[0]),int(rp[1]))
            except Exception:
                return True
        _prop_future = sorted([e for e in _prop_exp_raw if _prop_is_future(e)], key=_prop_exp_sort)
        _prop_past   = sorted([e for e in _prop_exp_raw if not _prop_is_future(e)], key=_prop_exp_sort, reverse=True)
        _prop_exp_labels = [f"🟢 {e}" for e in _prop_future] + [f"🔴 {e} (past)" for e in _prop_past]
        _prop_exp_opts = ["All Expiries"] + _prop_exp_labels
        _sel_prop_exp_lbl = st.selectbox(
            "📅 Filter by Expiry", _prop_exp_opts, index=0, key="prop_expiry",
            help="Narrow prop scan to a specific expiry cycle"
        )
        _sel_prop_expiry = None
        if _sel_prop_exp_lbl != "All Expiries":
            _sel_prop_expiry = _sel_prop_exp_lbl.replace("🟢 ", "").replace("🔴 ", "").replace(" (past)", "")

    with st.spinner("Scanning for opportunities..."):
        opps = scan_prop_opportunities(prop_date, min_z)

    # Apply expiry filter
    if _sel_prop_expiry and not opps.empty:
        if "expiry_date" in opps.columns:
            opps = opps[opps["expiry_date"] == _sel_prop_expiry]
        elif "expiry" in opps.columns:
            opps = opps[opps["expiry"] == _sel_prop_expiry]
    if opps.empty:
        st.info("No opportunities meeting criteria." + (f" (Expiry: {_sel_prop_expiry})" if _sel_prop_expiry else ""))
        st.stop()
    if _sel_prop_expiry:
        st.caption(f"📅 Filtered to expiry: **{_sel_prop_expiry}**")

    # Summary
    st.markdown(f"<div>Found {len(opps)} setups across {opps['Ticker'].nunique()} tickers</div>",
                unsafe_allow_html=True)

    # Filter by net view
    views = ["ALL"] + sorted(opps["Net_View"].unique().tolist())
    sel_view = st.selectbox("Filter by Signal", views)
    if sel_view != "ALL":
        opps = opps[opps["Net_View"] == sel_view]

    # Color-code the table
    st.dataframe(opps, hide_index=True)

    # ── INTERACTIVE DETAIL: click any setup to see full strategy ──
    st.markdown("<div>🔍 Setup Detail — Select a Row for Full Analysis</div>", unsafe_allow_html=True)

    # Build selection options
    setup_labels = [
        f"{row['Ticker']} ${row['Strike']:.0f} {row['Expiry']} — {row['Net_View']} (Z:{row['Z_Score']})"
        for _, row in opps.iterrows()
    ]
    sel_idx = st.selectbox("Select Setup", range(len(setup_labels)), format_func=lambda i: setup_labels[i])
    opp = opps.iloc[sel_idx]

    with st.spinner(f"Analyzing {opp['Ticker']} ${opp['Strike']:.0f}..."):
        try:
            _prop_ah = _get_ah_price(opp["Ticker"])
            _prop_eod = _prop_ah["spot_reg"] or 0
            _prop_ext = _prop_ah["spot_ah"] if _prop_ah["spot_ah"] > 0 else _prop_eod
            current_px = _prop_ext if st.session_state.get("use_ah") else _prop_eod
            if current_px <= 0:
                current_px = float(yf.Ticker(opp["Ticker"]).history(period="1d")["Close"].iloc[-1])
            _prop_src = (_prop_ah["label"] if _prop_ah["is_extended"] and st.session_state.get("use_ah")
                         else "EOD")
            _prop_icon = "🌙" if "AH" in _prop_src or "PM" in _prop_src else ("☀️" if _prop_src == "EOD" else "📈")
        except Exception:
            current_px = 0
            _prop_src = "EOD"
            _prop_icon = "☀️"

        # ── Determine strategy based on Net_View ──
        net_view = str(opp["Net_View"])
        strike = float(opp["Strike"])
        ticker = opp["Ticker"]
        escape = opp["Escape"]
        pcr_val = float(opp["PCR"]) if pd.notna(opp["PCR"]) else 0

        # Strategy recommendation engine
        if "BULL" in net_view:
            strategies = [
                {"name": "Bull Call Spread", "type": "DEBIT",
                 "legs": [f"BUY Call @ ${strike:.0f}", f"SELL Call @ ${strike + 5:.0f}"],
                 "max_risk": "Net debit paid (premium difference)",
                 "max_reward": f"${5:.0f} - premium = spread width minus cost",
                 "breakeven": f"${strike:.0f} + net debit",
                 "best_when": "Moderately bullish. Stock rises toward upper strike by expiry.",
                 "opt_type": "call", "direction": "long"},
                {"name": "Long Call", "type": "DEBIT",
                 "legs": [f"BUY Call @ ${strike:.0f}"],
                 "max_risk": "100% of premium paid",
                 "max_reward": "Unlimited upside",
                 "breakeven": f"${strike:.0f} + premium",
                 "best_when": "Strongly bullish. Expect a big move up before expiry.",
                 "opt_type": "call", "direction": "long"},
                {"name": "Cash-Secured Put (income)", "type": "CREDIT",
                 "legs": [f"SELL Put @ ${strike:.0f}"],
                 "max_risk": f"${strike:.0f} × 100 minus premium received (if stock drops to $0)",
                 "max_reward": "Premium received",
                 "breakeven": f"${strike:.0f} - premium received",
                 "best_when": "Neutral-to-bullish. Willing to buy stock at strike if assigned.",
                 "opt_type": "put", "direction": "short"},
            ]
        elif "BEAR" in net_view:
            strategies = [
                {"name": "Bear Put Spread", "type": "DEBIT",
                 "legs": [f"BUY Put @ ${strike:.0f}", f"SELL Put @ ${strike - 5:.0f}"],
                 "max_risk": "Net debit paid",
                 "max_reward": f"${5:.0f} - premium = spread width minus cost",
                 "breakeven": f"${strike:.0f} - net debit",
                 "best_when": "Moderately bearish. Stock falls toward lower strike.",
                 "opt_type": "put", "direction": "long"},
                {"name": "Long Put", "type": "DEBIT",
                 "legs": [f"BUY Put @ ${strike:.0f}"],
                 "max_risk": "100% of premium paid",
                 "max_reward": f"${strike:.0f} × 100 minus premium (if stock → $0)",
                 "breakeven": f"${strike:.0f} - premium",
                 "best_when": "Strongly bearish or hedging existing long stock position.",
                 "opt_type": "put", "direction": "long"},
            ]
        elif "STRADDLE" in net_view or "VOL" in net_view:
            strategies = [
                {"name": "Long Straddle", "type": "DEBIT",
                 "legs": [f"BUY Call @ ${strike:.0f}", f"BUY Put @ ${strike:.0f}"],
                 "max_risk": "Total premium paid (both legs)",
                 "max_reward": "Unlimited (either direction)",
                 "breakeven": f"${strike:.0f} ± total premium",
                 "best_when": "Big move expected, direction unknown. Earnings, events.",
                 "opt_type": "call", "direction": "long"},
                {"name": "Long Strangle (cheaper)", "type": "DEBIT",
                 "legs": [f"BUY Call @ ${strike + 5:.0f}", f"BUY Put @ ${strike - 5:.0f}"],
                 "max_risk": "Total premium paid (both legs)",
                 "max_reward": "Unlimited (either direction)",
                 "breakeven": f"Upper: ${strike + 5:.0f} + premium | Lower: ${strike - 5:.0f} - premium",
                 "best_when": "Expect a big move. Cheaper than straddle but needs larger move.",
                 "opt_type": "call", "direction": "long"},
            ]
        elif "PREMIUM" in net_view or "RANGE" in net_view:
            strategies = [
                {"name": "Iron Condor", "type": "CREDIT",
                 "legs": [f"SELL Put @ ${strike - 5:.0f}", f"BUY Put @ ${strike - 10:.0f}",
                          f"SELL Call @ ${strike + 5:.0f}", f"BUY Call @ ${strike + 10:.0f}"],
                 "max_risk": f"${5:.0f} spread width minus credit received",
                 "max_reward": "Net credit received",
                 "breakeven": f"Lower: ${strike - 5:.0f} - credit | Upper: ${strike + 5:.0f} + credit",
                 "best_when": "Range-bound market. Collect premium with defined risk.",
                 "opt_type": "call", "direction": "short"},
                {"name": "Short Strangle", "type": "CREDIT",
                 "legs": [f"SELL Call @ ${strike + 5:.0f}", f"SELL Put @ ${strike - 5:.0f}"],
                 "max_risk": "Unlimited (naked, use margin carefully)",
                 "max_reward": "Total premium received",
                 "breakeven": f"Upper: ${strike + 5:.0f} + credit | Lower: ${strike - 5:.0f} - credit",
                 "best_when": "Low vol expected. Range-bound with wide expected range.",
                 "opt_type": "call", "direction": "short"},
            ]
        else:  # RISK-OFF, MIXED
            strategies = [
                {"name": "Protective Put (hedge)", "type": "DEBIT",
                 "legs": [f"BUY Put @ ${strike:.0f}"],
                 "max_risk": "Premium paid (insurance cost)",
                 "max_reward": f"Protection below ${strike:.0f}",
                 "breakeven": "Current stock price + premium",
                 "best_when": "Already long stock, want downside protection.",
                 "opt_type": "put", "direction": "long"},
                {"name": "Collar (free hedge)", "type": "ZERO COST",
                 "legs": [f"BUY Put @ ${strike - 5:.0f}", f"SELL Call @ ${strike + 5:.0f}"],
                 "max_risk": f"Stock loss down to ${strike - 5:.0f} (capped)",
                 "max_reward": f"Stock gain up to ${strike + 5:.0f} (capped)",
                 "breakeven": "Current stock price (near zero cost)",
                 "best_when": "Already long stock. Want to hedge without paying premium.",
                 "opt_type": "put", "direction": "long"},
            ]

        # ── Display WHY this signal ──
        st.markdown(f"""
        <div>
            <h3>{ticker} — ${strike:.0f} ({opp['Expiry']})</h3>
            <b>Stock:</b> {_prop_icon} {_prop_src} ${current_px:.2f} &nbsp;|&nbsp;
            <b>Signal:</b> {net_view} &nbsp;|&nbsp;
            <b>Z-Score:</b> {opp['Z_Score']} &nbsp;|&nbsp;
            <b>PCR:</b> {pcr_val:.2f} &nbsp;|&nbsp;
            <b>Escape:</b> {escape}
        </div>
        """, unsafe_allow_html=True)

        # ── WHY this signal explanation ──
        with st.expander("🧠 WHY this signal? (Explanation)", expanded=True):
            call_sig = opp['Call_Signal']
            put_sig = opp['Put_Signal']
            call_chg = opp['Call_OI_Chg']
            put_chg = opp['Put_OI_Chg']

            reasons = []
            reasons.append(f"**Call OI Change:** {call_chg:+,.0f} → Signal: **{call_sig}**")
            reasons.append(f"**Put OI Change:** {put_chg:+,.0f} → Signal: **{put_sig}**")
            reasons.append(f"**Z-Score:** {opp['Z_Score']} (anomaly threshold: >1.5 = unusual activity)")

            if call_chg > 0 and put_chg < 0:
                reasons.append("📈 **Call buying + Put unwinding** = Strong bullish accumulation by institutions")
            elif call_chg < 0 and put_chg > 0:
                reasons.append("📉 **Call unwinding + Put buying** = Institutional hedging / bearish positioning")
            elif call_chg > 0 and put_chg > 0:
                reasons.append("⚡ **Both call & put OI rising** = Straddle/hedge activity. Big move expected.")
            elif call_chg < 0 and put_chg < 0:
                reasons.append("🔄 **Both closing** = Position unwinding. Conviction dropping.")

            if pcr_val > 1.3:
                reasons.append(f"🔴 **PCR {pcr_val:.2f} > 1.3** = Heavy put positioning. Fear elevated (contrarian bullish if extreme).")
            elif pcr_val < 0.7:
                reasons.append(f"🟢 **PCR {pcr_val:.2f} < 0.7** = Complacent. Light hedging (contrarian watch).")
            else:
                reasons.append(f"⚪ **PCR {pcr_val:.2f}** = Balanced put/call positioning.")

            if escape == "Easy":
                reasons.append("✅ **Escape: Easy** — Good liquidity, can exit quickly.")
            elif escape == "Hard":
                reasons.append("⚠️ **Escape: Hard** — Low liquidity. Difficult to exit. Watch bid-ask spread.")
            else:
                reasons.append("🟡 **Escape: Moderate** — Use limit orders for best fills.")

            for r in reasons:
                st.markdown(r)

        # ── Strategy cards with full detail ──
        st.markdown("<div>📋 Recommended Strategies</div>", unsafe_allow_html=True)

        for si, strat in enumerate(strategies):
            with st.expander(f"{'🟢' if strat['type']=='DEBIT' else '🔵' if strat['type']=='CREDIT' else '⚪'} {strat['name']} ({strat['type']})", expanded=(si == 0)):
                st.markdown(f"**Legs:**")
                for leg in strat["legs"]:
                    st.markdown(f"  - {leg}")

                sc1, sc2, sc3 = st.columns(3)
                sc1.markdown(f"**Max Risk:**\n{strat['max_risk']}")
                sc2.markdown(f"**Max Reward:**\n{strat['max_reward']}")
                sc3.markdown(f"**Breakeven:**\n{strat['breakeven']}")

                st.markdown(f"**Best when:** {strat['best_when']}")

                # ── Run full risk analysis + payoff chart ──
                try:
                    risk = predict_trade_risk(ticker, strat["opt_type"],
                                              strike, opp["Expiry"].replace("-", ""),  # try original format
                                              5.0, 1)
                    # Try standard date format
                    if risk is None or "error" in (risk or {}):
                        # Try MM-DD-YYYY → YYYY-MM-DD conversion
                        try:
                            exp_parsed = datetime.strptime(opp["Expiry"], "%m-%d-%Y")
                            exp_str = exp_parsed.strftime("%Y-%m-%d")
                        except Exception:
                            exp_str = opp["Expiry"]
                        risk = predict_trade_risk(ticker, strat["opt_type"],
                                                  strike, exp_str, 5.0, 1)

                    if risk and "error" not in risk:
                        # Investment & Risk metrics
                        st.markdown("**💰 Investment & Risk Profile:**")
                        ic1, ic2, ic3, ic4, ic5 = st.columns(5)
                        ic1.metric("Stock Price", f"${risk['current_price']:.2f}")
                        ic2.metric("Option Theo", f"${risk['theo_price']:.2f}")
                        est_cost = risk['theo_price'] * 100
                        ic3.metric("Investment (1 lot)", f"${est_cost:,.0f}")
                        ic4.metric("Max Loss", f"${abs(risk['max_loss']):,.0f}")
                        ic5.metric("P(ITM)", f"{risk['prob_itm']:.0f}%")

                        # Greeks
                        st.markdown("**Greeks:**")
                        g = risk["greeks"]
                        gc1, gc2, gc3, gc4 = st.columns(4)
                        gc1.metric("Delta", f"{g['delta']:.3f}")
                        gc2.metric("Gamma", f"{g['gamma']:.4f}")
                        gc3.metric("Theta", f"${g['theta']*100:.2f}/day")
                        gc4.metric("Vega", f"${g['vega']*100:.2f}")

                        # Waiting time / DTE
                        try:
                            exp_dt = datetime.strptime(opp["Expiry"], "%m-%d-%Y")
                        except Exception:
                            try:
                                exp_dt = datetime.strptime(opp["Expiry"], "%Y-%m-%d")
                            except Exception:
                                exp_dt = datetime.now() + timedelta(days=30)
                        dte = max((exp_dt - datetime.now()).days, 0)
                        st.markdown(f"**⏱️ Waiting Time:** {dte} days to expiry | "
                                    f"Theta decay: **${abs(g['theta'])*100:.2f}/day** "
                                    f"(~${abs(g['theta'])*100*dte:.0f} total if held to expiry)")

                        # Escape analysis
                        st.markdown(f"**🚪 Escape:** {risk['escape']} — "
                                    f"{'Can exit quickly with minimal slippage.' if risk['escape'] == 'EASY' else 'Use limit orders.' if risk['escape'] == 'MODERATE' else '⚠️ Hard to exit. Wide spreads likely.'}")

                        # Scenario matrix
                        scenarios = risk["scenarios"]
                        st.markdown("**📈 P&L Scenarios:**")
                        for tf in ["1-Day", "3-Day", "5-Day"]:
                            tf_df = scenarios[scenarios["timeframe"] == tf][["scenario", "stock_move", "new_price", "pnl", "pnl_pct"]]
                            tf_df.columns = ["Scenario", "Stock Move", "Option Price", "P&L ($)", "P&L (%)"]
                            st.markdown(f"*{tf}:*")
                            st.dataframe(tf_df, hide_index=True)

                        # ── Payoff chart at expiry ──
                        st.markdown("**📊 Payoff at Expiry:**")
                        spot_range = np.linspace(strike * 0.85, strike * 1.15, 100)
                        entry_est = risk['theo_price']
                        if strat["opt_type"] == "call":
                            payoff = np.maximum(spot_range - strike, 0) - entry_est
                        else:
                            payoff = np.maximum(strike - spot_range, 0) - entry_est
                        payoff_dollar = payoff * 100  # per contract

                        fig_payoff = go.Figure()
                        fig_payoff.add_trace(go.Scatter(
                            x=spot_range, y=payoff_dollar,
                            mode="lines", name="P&L at Expiry",
                            fill="tozeroy",
                            line=dict(color="#0066cc", width=2),
                            fillcolor="rgba(0,102,204,0.15)",
                        ))
                        fig_payoff.add_hline(y=0, line_dash="dash", line_color="#888")
                        fig_payoff.add_vline(x=current_px, line_dash="dash", line_color="#e65100",
                                             annotation_text=f"Current ${current_px:.0f}")
                        fig_payoff.add_vline(x=strike, line_dash="dot", line_color="#888",
                                             annotation_text=f"Strike ${strike:.0f}")
                        fig_payoff.update_layout(
                            template="plotly_white", height=300,
                            xaxis_title="Stock Price at Expiry",
                            yaxis_title="P&L ($)",
                            margin=dict(t=20, b=40),
                        )
                        st.plotly_chart(fig_payoff)

                        # P&L Heatmap
                        pivot = scenarios.pivot(index="timeframe", columns="scenario", values="pnl")
                        pivot = pivot.reindex(["1-Day", "3-Day", "5-Day"])
                        col_order = ["Sharp Down", "Moderate Down", "Flat", "Moderate Up", "Sharp Up"]
                        pivot = pivot[[c for c in col_order if c in pivot.columns]]
                        fig_heat = px.imshow(pivot, text_auto=".0f", color_continuous_scale="RdYlGn",
                                             labels=dict(x="Scenario", y="Timeframe", color="P&L ($)"),
                                             title="P&L Heatmap")
                        fig_heat.update_layout(template="plotly_white", height=250)
                        st.plotly_chart(fig_heat)

                        # Bottom-line
                        prob = risk["prob_itm"]
                        if prob > 60 and risk["escape"] != "DIFFICULT":
                            st.success(f"✅ FAVORABLE — {prob:.0f}% P(ITM), {risk['escape'].lower()} exit. "
                                       f"Invest ~${est_cost:,.0f}, max risk ${abs(risk['max_loss']):,.0f}.")
                        elif prob > 40:
                            st.info(f"📊 MODERATE — {prob:.0f}% P(ITM). Size accordingly. "
                                    f"Invest ~${est_cost:,.0f}.")
                        else:
                            st.warning(f"⚠️ SPECULATIVE — Only {prob:.0f}% P(ITM). "
                                       f"Low odds. Max risk ${abs(risk['max_loss']):,.0f}.")
                except Exception as e:
                    st.info(f"Risk analysis unavailable for this expiry format. ({e})")


# ===================================================================
# ──  PAGE 4: PORTFOLIO & SUGGESTIONS
# ===================================================================
elif page == "💼 Portfolio & Suggestions":
    _page_header("💼 Portfolio & Suggestions", _PAGE_HELP["💼 Portfolio & Suggestions"])

    try:
        _conn = get_conn()
        trades = pd.read_sql("SELECT * FROM trades WHERE status='OPEN'", _conn)
        closed = pd.read_sql("SELECT * FROM trades WHERE status='CLOSED'", _conn)
        _conn.close()
    except Exception as _e:
        trades = pd.DataFrame(); closed = pd.DataFrame()
        st.error(f"Could not load trades: {_e}")

    if not trades.empty and "expiry" in trades.columns:
        _today_str = datetime.now().strftime("%Y-%m-%d")
        _expired = trades[trades["expiry"] < _today_str]
        if not _expired.empty:
            try:
                _ac = get_conn()
                for _, _er in _expired.iterrows():
                    _ac.execute("UPDATE trades SET status='CLOSED',exit_date=?,exit_reason=?,updated_at=? WHERE trade_id=?",
                                (_today_str,"Expired",datetime.now().isoformat(),int(_er["trade_id"])))
                _ac.commit(); _ac.close()
                st.info(f"Auto-closed {len(_expired)} expired position(s).")
                trades = trades[trades["expiry"] >= _today_str]
                closed = pd.concat([closed, _expired], ignore_index=True)
            except Exception:
                pass

    updated_rows = []
    _spot_cache: dict = {}
    if not trades.empty:
        for _, _t in trades.iterrows():
            _tk  = str(_t.get("ticker","")).upper()
            _k   = float(_t.get("strike",0))
            _exp = str(_t.get("expiry",""))
            _opt = str(_t.get("option_type","CALL")).upper()
            _qty = int(_t.get("quantity",1))
            _ep  = float(_t.get("entry_price",0))
            _acct= str(_t.get("account_type","Taxable"))
            _nts = str(_t.get("notes","") or "")
            if _tk not in _spot_cache:
                # DB stock_daily is primary — confirmed EOD close, no pre-market contamination
                _db_s = _db_spot(_tk)
                _spot_cache[_tk] = _db_s if _db_s > 0 else (_cached_price(_tk) or 0.0)
            _spot_eod = _spot_cache[_tk]
            try:
                _edt = datetime.strptime(_exp,"%Y-%m-%d")
                _dte = max((_edt.date()-datetime.now().date()).days,0)
                _T   = _dte/365.0
                _hv   = _historical_vol(_tk)
                # Priority 1: live market mid (bid/ask, market open)
                _real_mid, _chain_iv = _fetch_option_mid(_tk, _exp, _k, _opt)
                if _real_mid is not None and _T > 0:
                    _cp      = _real_mid
                    _iv_used = (_chain_iv if _chain_iv else
                                _implied_vol(_real_mid, _spot_eod, _k, _T, 0.045, _opt.lower(),
                                             fallback=_hv))
                    _g = bs_greeks(_spot_eod, _k, _T, 0.045, _iv_used, _opt.lower())
                else:
                    # Market closed → BS with historical vol at confirmed DB EOD spot
                    _iv_used = _hv
                    _g   = bs_greeks(_spot_eod, _k, _T, 0.045, _iv_used, _opt.lower())
                    _cp  = _g["price"]
                _d   = _g["delta"]*_qty; _gm=_g["gamma"]*abs(_qty)
                _th  = _g["theta"]*abs(_qty); _ve=_g["vega"]*abs(_qty)
            except:
                _cp=_ep; _d=_gm=_th=_ve=0.0; _dte=0; _iv_used=0.30
            _pnl  = (_cp-_ep)*_qty*100
            _cost = _ep*abs(_qty)*100
            _pp   = (_pnl/_cost*100) if _cost>0 else 0
            updated_rows.append({
                "ID":int(_t.get("trade_id",0)), "Ticker":_tk, "Type":_opt,
                "Strike":_k, "Expiry":_exp, "Qty":_qty,
                "Entry":_ep, "Current":_cp, "Stock_Px":_spot_eod,
                "IV": round(_iv_used, 4),
                "PnL":round(_pnl,2), "PnL%":round(_pp,1), "DTE":_dte,
                "Delta":_d, "Gamma":_gm, "Theta":_th, "Vega":_ve,
                "Account":_acct, "Notes":_nts,
                "Entry_Date":str(_t.get("entry_date","")),
                "Strategy":str(_t.get("strategy","") or ""),
            })

    def _detect_strategy(legs):
        if len(legs)==1:
            return ("Long " if legs[0]["Qty"]>0 else "Short ")+legs[0]["Type"].title()
        calls=sorted([l for l in legs if l["Type"]=="CALL"],key=lambda x:x["Strike"])
        puts =sorted([l for l in legs if l["Type"]=="PUT" ],key=lambda x:x["Strike"])
        expiries=set(l["Expiry"] for l in legs)
        n=len(legs)
        if len(expiries)>1: return "Calendar/Diagonal"
        if n==2:
            if len(calls)==2: return "Bull Call Spread" if calls[0]["Qty"]>0 else "Bear Call Spread"
            if len(puts) ==2: return "Bull Put Spread"  if puts[-1]["Qty"]>0 else "Bear Put Spread"
            if len(calls)==1 and len(puts)==1:
                same_k = abs(calls[0]["Strike"]-puts[0]["Strike"])<1
                if same_k: return "Long Straddle" if calls[0]["Qty"]>0 else "Short Straddle"
                return "Long Strangle" if calls[0]["Qty"]>0 else "Short Strangle"
        if n==3: return "Butterfly"
        if n==4:
            if len(calls)==2 and len(puts)==2:
                return "Iron Butterfly" if abs(calls[0]["Strike"]-puts[-1]["Strike"])<1 else "Iron Condor"
        return f"Custom({n})"

    def _group_payoff(legs, sr):
        tot=np.zeros(len(sr))
        for leg in legs:
            k=float(leg["Strike"]); ep=float(leg["Entry"]); q=int(leg["Qty"])
            if leg["Type"]=="CALL": tot+=(np.maximum(sr-k,0)-ep)*q*100
            else:                   tot+=(np.maximum(k-sr,0)-ep)*q*100
        return tot

    def _color_pnl(val):
        try:
            v=float(val)
            if v>0: return "background-color:#c8f7c5; color:#111"
            if v<0: return "background-color:#f7c5c5; color:#111"
        except: pass
        return ""

    from collections import defaultdict as _dd
    _tg: dict = _dd(list)
    for _r in updated_rows: _tg[_r["Ticker"]].append(_r)
    _LC=["#0066cc","#e65100","#388e3c","#7b1fa2","#c62828","#00838f"]

    tab1,tab2,tab3,tab4=st.tabs([
        "📋 Open Positions","📊 Performance",
        "💡 Suggestions","📈 P&L Breakdown"
    ])

    with tab1:
        st.markdown("#### ➕ Add / ✏️ Edit Positions — scroll below table or use the expander on each leg")
        if not updated_rows:
            st.info("No open positions. Use **Add New Position** below.")
        else:
            _tp=sum(r["PnL"] for r in updated_rows)
            _tc=sum(abs(r["Entry"])*abs(r["Qty"])*100 for r in updated_rows)
            _nd=sum(r["Delta"] for r in updated_rows)
            _nt=sum(r["Theta"] for r in updated_rows)
            _ws=sum(1 for r in updated_rows if r["PnL"]>0)
            _tpc=(_tp/_tc*100) if _tc>0 else 0
            _ng=sum(r["Gamma"] for r in updated_rows)
            _nv=sum(r["Vega"]  for r in updated_rows)
            _max_tk_cost=max((sum(abs(l["Entry"])*abs(l["Qty"])*100 for l in v) for v in _tg.values()),default=0)
            _conc_pct=(_max_tk_cost/_tc*100) if _tc>0 else 0
            _conc_tk=max(_tg,key=lambda k:sum(abs(l["Entry"])*abs(l["Qty"])*100 for l in _tg[k]),default="")
            m1,m2,m3,m4,m5=st.columns(5)
            m1.metric("Unrealized P&L",f"${_tp:,.2f}",f"{_tpc:+.1f}%")
            m2.metric("Legs / Tickers",f"{len(updated_rows)} / {len(_tg)}")
            m3.metric("Winners",f"{_ws}/{len(updated_rows)}")
            m4.metric("Net Delta",f"{_nd:+.2f}")
            m5.metric("Net Theta/day",f"${_nt*100:+.2f}")
            n1,n2,n3,n4,n5=st.columns(5)
            n1.metric("Net Gamma",f"{_ng:.4f}")
            n2.metric("Net Vega",f"{_nv:.3f}")
            n3.metric("Cost Basis",f"${_tc:,.0f}")
            _conc_flag="⚠️ " if _conc_pct>50 else ""
            n4.metric(f"{_conc_flag}Largest Position",f"{_conc_tk} {_conc_pct:.0f}%",delta="Concentrated" if _conc_pct>50 else "Diversified",delta_color="inverse" if _conc_pct>50 else "normal")
            # Earnings alerts across all tickers
            _earn_alerts=[f"{tk} ⚠️{_days_to_earnings(tk)}d" for tk in _tg if (_days_to_earnings(tk) or 99)<14]
            n5.metric("Earnings <14d",", ".join(_earn_alerts) if _earn_alerts else "None","Check before expiry" if _earn_alerts else "All clear")

            _grp_rows=[]
            for _stk,_legs in sorted(_tg.items()):
                _gp=sum(l["PnL"] for l in _legs)
                _gc=sum(abs(l["Entry"])*abs(l["Qty"])*100 for l in _legs)
                _gpc=(_gp/_gc*100) if _gc>0 else 0
                _iv_r,_iv_p,_iv_cur=_iv_rank_pct(_stk)
                _dte_earn=_days_to_earnings(_stk)
                _earn_warn="⚠️ "+str(_dte_earn)+"d" if _dte_earn is not None and _dte_earn<14 else (""+str(_dte_earn)+"d" if _dte_earn else "—")
                _iv_str=f"{_iv_r:.0f}%" if _iv_r is not None else "—"
                _iv_note=("🔥HIGH" if (_iv_r or 0)>75 else ("💤LOW" if (_iv_r or 100)<25 else "MID"))
                _conc2=round(_gc/_tc*100,0) if _tc>0 else 0
                _grp_ah = _get_ah_price(_stk)
                _grp_eod = _grp_ah["spot_reg"] if _grp_ah["spot_reg"] > 0 else _legs[0]["Stock_Px"]
                _grp_ahe = _grp_ah["spot_ah"]  if _grp_ah["spot_ah"]  > 0 else _grp_eod
                _grp_has_ah = abs(_grp_ahe - _grp_eod) > 0.05
                _grp_spot_str = (f"${_grp_ahe:.2f}🌙 (EOD ${_grp_eod:.2f})" if _grp_has_ah
                                 else f"${_grp_eod:.2f}")
                _grp_rows.append({
                    "Ticker":_stk,"Stock $":_grp_spot_str,
                    "Strategy":_detect_strategy(_legs),"Legs":len(_legs),
                    "Group P&L $":round(_gp,2),"Group P&L %":round(_gpc,1),
                    "Net Delta":round(sum(l["Delta"] for l in _legs),3),
                    "Net Theta/d":round(sum(l["Theta"] for l in _legs)*100,2),
                    "IV Rank":f"{_iv_str} {_iv_note}",
                    "Earnings":_earn_warn,
                    "Conc%":f"{_conc2:.0f}%",
                })
            _gdf=pd.DataFrame(_grp_rows)
            if _grp_rows:
                _tot_row={
                    "Ticker":"📊 TOTAL","Stock $":"","Strategy":f"{len(_grp_rows)} ticker(s)","Legs":sum(r["Legs"] for r in _grp_rows),
                    "Group P&L $":round(sum(r["Group P&L $"] for r in _grp_rows),2),
                    "Group P&L %":round(_tpc,1),
                    "Net Delta":round(sum(r["Net Delta"] for r in _grp_rows),3),
                    "Net Theta/d":round(sum(r["Net Theta/d"] for r in _grp_rows),2),
                    "IV Rank":"","Earnings":"","Conc%":"100%",
                }
                _gdf=pd.concat([_gdf,pd.DataFrame([_tot_row])],ignore_index=True)
            st.markdown("#### Ticker Group Summary")
            try: st.dataframe(_gdf.style.applymap(_color_pnl,subset=["Group P&L $","Group P&L %"]),hide_index=True,use_container_width=True)
            except: st.dataframe(_gdf,hide_index=True,use_container_width=True)

            st.markdown("---")
            st.markdown("#### Details by Ticker")
            for _stk,_legs in sorted(_tg.items()):
                _gp=sum(l["PnL"] for l in _legs)
                _gc=sum(abs(l["Entry"])*abs(l["Qty"])*100 for l in _legs)
                _gpc=(_gp/_gc*100) if _gc>0 else 0
                _strat=_detect_strategy(_legs)
                _spv_ah  = _get_ah_price(_stk)
                _spv_eod = _spv_ah["spot_reg"] if _spv_ah["spot_reg"] > 0 else _legs[0]["Stock_Px"]
                _spv_ext = _spv_ah["spot_ah"]  if _spv_ah["spot_ah"]  > 0 else _spv_eod
                _spv_is_ext = _spv_ah["is_extended"]
                _spv_has_ah = abs(_spv_ext - _spv_eod) > 0.05   # any AH data at all
                _spv     = _spv_ext if st.session_state.get("use_ah") else _spv_eod
                _spv_lbl = (f"EOD ${_spv_eod:.2f} → {_spv_ah['label']} **${_spv_ext:.2f}** ({_spv_ah['ah_chg_pct']:+.1f}%)"
                            if _spv_has_ah else f"${_spv_eod:.2f}")
                _ico="🟢" if _gp>=0 else "🔴"
                _lbl=f"{_ico} **{_stk}** — {_strat} ({len(_legs)} leg{'s' if len(_legs)>1 else ''})  |  Stock {_spv_lbl}  |  Group P&L **${_gp:+,.2f}** ({_gpc:+.1f}%)"
                with st.expander(_lbl,expanded=(len(_tg)==1)):
                    _ngd=sum(l["Delta"] for l in _legs); _ngt=sum(l["Theta"] for l in _legs)
                    _ngg=sum(l["Gamma"] for l in _legs); _ngv=sum(l["Vega"]  for l in _legs)
                    gc0,gc1,gc2,gc3,gc4,gc5=st.columns(6)
                    # Stock price metric — always show AH when available
                    if _spv_has_ah:
                        gc0.metric(
                            f"Stock EOD→{_spv_ah['label']}",
                            f"${_spv_ext:.2f}",
                            f"{_spv_ah['ah_chg_pct']:+.2f}%  (EOD ${_spv_eod:.2f})",
                        )
                    else:
                        gc0.metric("Stock (EOD)", f"${_spv_eod:.2f}")
                    gc1.metric("Group P&L",f"${_gp:+,.2f}",f"{_gpc:+.1f}%")
                    gc2.metric("Net Delta",f"{_ngd:+.3f}")
                    gc3.metric("Net Theta/d",f"${_ngt*100:+.2f}")
                    gc4.metric("Net Gamma",f"{_ngg:.4f}")
                    gc5.metric("Net Vega",f"{_ngv:.3f}")
                    # IV rank + Earnings warning banner
                    _iv_rk,_iv_pk,_iv_ck=_iv_rank_pct(_stk)
                    _dte_e=_days_to_earnings(_stk)
                    _info_parts=[]
                    if _iv_rk is not None:
                        _iv_label="🔥 HIGH IV" if _iv_rk>75 else ("💤 LOW IV" if _iv_rk<25 else "🟡 MID IV")
                        _iv_action=("Sell premium / spreads are expensive — favour credit strategies" if _iv_rk>75
                                    else "Buy premium is cheap — favour debit spreads or long options" if _iv_rk<25
                                    else "IV is average — standard positioning applies")
                        _info_parts.append(f"**IV Rank: {_iv_rk:.0f}% ({_iv_label}) | HV: {_iv_ck:.0f}%** — {_iv_action}")
                    if _dte_e is not None and _dte_e < 30:
                        _earn_color="#ff1744" if _dte_e<7 else ("#ff9800" if _dte_e<14 else "#1565C0")
                        _info_parts.append(f"⚠️ EARNINGS IN {_dte_e} DAYS — IV typically spikes before earnings then collapses (IV crush). If holding long options through earnings, check if premium is worth the risk. Consider closing before or buying a cheaper spread.")
                    if _info_parts:
                        st.markdown("<br>".join(_info_parts),unsafe_allow_html=True)
                        st.markdown("")

                    _ks=[l["Strike"] for l in _legs]
                    _sr=np.linspace(min(_ks)*0.80,max(_ks)*1.20,200)
                    _comb=_group_payoff(_legs,_sr)
                    fig_g=go.Figure()
                    for _li,_leg in enumerate(_legs):
                        _lc=_LC[_li%len(_LC)]; _sl="BUY" if _leg["Qty"]>0 else "SELL"
                        fig_g.add_trace(go.Scatter(x=_sr,y=_group_payoff([_leg],_sr),mode="lines",
                            name=f"L{_li+1}:{_sl} {_leg['Type']} ${_leg['Strike']}",
                            line=dict(color=_lc,width=1.5,dash="dot"),opacity=0.65))
                    fig_g.add_trace(go.Scatter(x=_sr,y=_comb,mode="lines",name="Combined",
                        fill="tozeroy",line=dict(color="#00d4aa",width=3),fillcolor="rgba(0,212,170,0.10)"))
                    fig_g.add_hline(y=0,line_dash="dash",line_color="#aaa")
                    fig_g.add_vline(x=_spv_eod,line_dash="dot",line_color="#e65100",
                                   annotation_text=f"EOD ${_spv_eod:.0f}",annotation_position="top right")
                    if _spv_has_ah and abs(_spv_ext - _spv_eod) > 0.10:
                        fig_g.add_vline(x=_spv_ext,line_dash="dot",line_color="#7b1fa2",
                                       annotation_text=f"{_spv_ah['label']} ${_spv_ext:.0f} ({_spv_ah['ah_chg_pct']:+.1f}%)",
                                       annotation_position="top left")
                    for _i in np.where(np.diff(np.sign(_comb)))[0]:
                        _be=(_sr[_i]+_sr[_i+1])/2
                        fig_g.add_vline(x=_be,line_dash="longdash",line_color="#ffa000",
                                        annotation_text=f"BE ${_be:.0f}",annotation_position="top left")
                    fig_g.update_layout(template="plotly_white",height=320,
                        title=f"{_stk} — {_strat} Payoff at Expiry",
                        xaxis_title="Stock Price",yaxis_title="P&L ($)",
                        legend=dict(orientation="h",y=-0.30),margin=dict(t=40,b=90))
                    st.plotly_chart(fig_g,use_container_width=True)

                    st.markdown("**Individual Legs:**")
                    for _li,pos in enumerate(_legs):
                        _lc=_LC[_li%len(_LC)]; _sl="BUY" if pos["Qty"]>0 else "SELL"
                        _pnl_color="#1b5e20" if pos["PnL"]>=0 else "#b71c1c"
                        _roll_hint=_roll_suggestion(pos, _spv)
                        _bg="#fff8e1" if pos["DTE"]<=5 else ("#ffebee" if pos["PnL%"]<-30 else "#fafafa")
                        # Option type colour + emoji for instant visual scan
                        _is_call = pos["Type"].upper() == "CALL"
                        _is_long = pos["Qty"] > 0
                        _type_emoji = "📈" if _is_call else "📉"
                        _type_badge_bg  = ("#e8f5e9" if _is_call else "#fce4ec")
                        _type_badge_txt = ("#2e7d32" if _is_call else "#880e4f")
                        _dir_tag = ("Long" if _is_long else "Short")
                        _dir_tag_bg  = ("#e3f2fd" if _is_long else "#fff3e0")
                        _dir_tag_txt = ("#0d47a1" if _is_long else "#e65100")

                        # ── Live/AH premium: use stored IV + skew adjustment ────
                        _ah_opt, _ah_pnl = None, None
                        _lv_lbl = _spv_ah["label"]   # "Live" / "AH" / "PM" / "EOD"
                        _pos_iv = pos.get("IV", 0.30) or 0.30
                        if pos["DTE"] >= 0:  # include DTE=0 (expiry-day intrinsic estimate)
                            try:
                                _opt_T  = max(pos["DTE"], 0.5) / 365.0
                                # Skew-adjust IV for spot move (neg spot-vol for puts)
                                _lv_iv  = _live_iv(_pos_iv, _spv_eod, _spv_ext, pos["Type"].lower())
                                # Price at live spot with adjusted IV
                                _ah_opt = bs_greeks(_spv_ext, pos["Strike"], _opt_T,
                                                    0.045, _lv_iv, pos["Type"].lower())["price"]
                                _ah_pnl = (_ah_opt - pos["Entry"]) * pos["Qty"] * 100
                            except Exception:
                                pass

                        # ── Leg header ──────────────────────────────────────────
                        _dte_warn = "⚠️ " if pos["DTE"] <= 5 else ""
                        st.markdown(
                            f"<div style='border-left:5px solid {_lc};padding:6px 12px 0 12px;"
                            f"margin-top:10px;background:{_bg};border-radius:4px 4px 0 0;'>"
                            f"<b>L{_li+1}:</b> &nbsp;"
                            f""
                            f"{_dir_tag} &nbsp;"
                            f""
                            f"{_type_emoji} {pos['Type']} &nbsp;"
                            f"<b>Strike ${pos['Strike']:.0f}</b> &nbsp; "
                            f"exp {pos['Expiry']} &nbsp; ×{abs(pos['Qty'])}"
                            f" &nbsp;&nbsp; {_dte_warn}DTE <b>{pos['DTE']}</b> &nbsp; "
                            f"Δ <b>{pos['Delta']:+.3f}</b> &nbsp; Θ <b>${pos['Theta']*100:.2f}/d</b>"
                            f"</div>", unsafe_allow_html=True)

                        # ── Metric row ───────────────────────────────────────────
                        _show_live = _ah_opt is not None
                        _ncols = 6 if _show_live else 5
                        _mc = st.columns(_ncols)
                        _mc[0].metric("Entry Premium", f"${pos['Entry']:.2f}",
                                      help="Option premium you paid (long) or received (short) per share")
                        _iv_src = "market mid" if pos.get("IV",0) != _historical_vol(pos["Ticker"]) else f"HV {_pos_iv:.0%}"
                        _mc[1].metric("☀️ EOD Premium", f"${pos['Current']:.2f}",
                                      delta=f"{pos['Current']-pos['Entry']:+.2f} vs entry",
                                      delta_color="normal" if pos["Qty"]>0 else "inverse",
                                      help=f"BS @ EOD ${pos['Stock_Px']:.2f}, IV={_pos_iv:.0%} ({_iv_src})")
                        if _show_live:
                            _lv_help = (f"BS price at {_lv_lbl} spot ${_spv_ext:.2f} "
                                        f"({_spv_ah['ah_chg_pct']:+.1f}% vs EOD), "
                                        f"IV skew-adjusted for spot move")
                            _mc[2].metric(f"🌙 {_lv_lbl} Premium", f"${_ah_opt:.2f}",
                                          delta=f"{_ah_opt-pos['Current']:+.2f} vs EOD",
                                          delta_color="normal" if pos["Qty"]>0 else "inverse",
                                          help=_lv_help)
                            _mc[3].metric("P&L (EOD)", f"${pos['PnL']:+,.0f}",
                                          f"{pos['PnL%']:+.1f}%",
                                          delta_color="normal" if pos["PnL"]>=0 else "inverse")
                            _mc[4].metric(f"P&L ({_lv_lbl})", f"${_ah_pnl:+,.0f}",
                                          f"{(_ah_pnl/(pos['Entry']*abs(pos['Qty'])*100)*100) if pos['Entry']>0 else 0:+.1f}%",
                                          delta_color="normal" if _ah_pnl>=0 else "inverse")
                            _mc[5].metric("Stock", f"${_spv_ext:.2f}",
                                          f"{_spv_ah['ah_chg_pct']:+.1f}% {_lv_lbl}  (EOD ${_spv_eod:.2f})")
                        else:
                            _mc[2].metric("P&L $", f"${pos['PnL']:+,.0f}",
                                          delta_color="normal" if pos["PnL"]>=0 else "inverse")
                            _mc[3].metric("P&L %", f"{pos['PnL%']:+.1f}%",
                                          delta_color="normal" if pos["PnL"]>=0 else "inverse")
                            _mc[4].metric("Stock (EOD)", f"${_spv_eod:.2f}")

                        if _roll_hint:
                            st.markdown(
                                f"<div style='border-left:5px solid {_lc};padding:4px 12px;"
                                f"margin-bottom:4px;background:{_bg};border-radius:0 0 4px 4px;"
                                f"font-size:0.85em;color:#555;'>{_roll_hint}</div>",
                                unsafe_allow_html=True)
                        with st.expander(f"  ↳ Close/Edit L{_li+1}: {_sl} {pos['Type']} ${pos['Strike']}"):
                            _et1, _et2 = st.tabs(["✅ Close", "✏️ Edit"])
                            with _et1:
                                with st.form(f"close_{pos['ID']}"):
                                    _a,_b,_c=st.columns(3)
                                    cp=_a.number_input("Exit Price",min_value=0.0,value=float(pos["Current"]),step=0.01,key=f"cp_{pos['ID']}")
                                    cd=_b.date_input("Exit Date",value=datetime.now(),key=f"cd_{pos['ID']}")
                                    cr=_c.selectbox("Reason",["Target Hit","Stop Loss","Expired","Manual","Rolling"],key=f"cr_{pos['ID']}")
                                    if st.form_submit_button("✅ Close This Leg"):
                                        try:
                                            _rp=(cp-pos["Entry"])*pos["Qty"]*100
                                            _rpc=(_rp/(pos["Entry"]*abs(pos["Qty"])*100)*100) if pos["Entry"]>0 else 0
                                            _ed=datetime.strptime(pos["Entry_Date"],"%Y-%m-%d") if pos["Entry_Date"] else datetime.now()
                                            _dh=(cd-_ed.date()).days
                                            _cc=get_conn()
                                            _cc.execute("UPDATE trades SET status='CLOSED',exit_date=?,exit_price=?,exit_reason=?,pnl=?,pnl_pct=?,days_held=?,updated_at=? WHERE trade_id=?",
                                                        (cd.strftime("%Y-%m-%d"),cp,cr,round(_rp,2),round(_rpc,1),_dh,datetime.now().isoformat(),pos["ID"]))
                                            _cc.commit(); _cc.close()
                                            st.success(f"Closed! P&L ${_rp:,.2f} ({_rpc:+.1f}%)")
                                            st.rerun()
                                        except Exception as _ce: st.error(f"{_ce}")
                                with st.form(f"notes_{pos['ID']}"):
                                    nn=st.text_area("📝 Notes",value=pos.get("Notes",""),key=f"nn_{pos['ID']}",height=60)
                                    if st.form_submit_button("💾 Save Notes"):
                                        try:
                                            _nc=get_conn(); _nc.execute("UPDATE trades SET notes=?,updated_at=? WHERE trade_id=?",(nn,datetime.now().isoformat(),pos["ID"]))
                                            _nc.commit(); _nc.close(); st.success("Saved!")
                                        except Exception as _ne: st.error(f"{_ne}")
                            with _et2:
                                with st.form(f"edit_{pos['ID']}"):
                                    _e1,_e2,_e3=st.columns(3)
                                    _etk=_e1.text_input("Ticker",value=pos["Ticker"],key=f"etk_{pos['ID']}").upper().strip()
                                    _eot=_e2.selectbox("Type",["CALL","PUT"],index=0 if pos["Type"]=="CALL" else 1,key=f"eot_{pos['ID']}")
                                    _eqt=_e3.number_input("Qty (neg=short)",value=int(pos["Qty"]),step=1,key=f"eqt_{pos['ID']}")
                                    _e4,_e5,_e6=st.columns(3)
                                    _estk=_e4.number_input("Strike",min_value=0.0,value=float(pos["Strike"]),step=0.5,key=f"estk_{pos['ID']}")
                                    _eep=_e5.number_input("Entry Price",min_value=0.0,value=float(pos["Entry"]),step=0.01,key=f"eep_{pos['ID']}")
                                    try: _eexp_def=datetime.strptime(pos["Expiry"],"%Y-%m-%d").date()
                                    except: _eexp_def=datetime.now().date()
                                    _eexp=_e6.date_input("Expiry",value=_eexp_def,key=f"eexp_{pos['ID']}")
                                    _strat_opts=["manual","long_call","long_put","spread","iron_condor","covered_call","cash_secured_put"]
                                    _cur_strat=str(pos.get("Strategy","manual") or "manual").lower()
                                    _strat_idx=_strat_opts.index(_cur_strat) if _cur_strat in _strat_opts else 0
                                    _estrat=st.selectbox("Strategy",_strat_opts,index=_strat_idx,key=f"estrat_{pos['ID']}")
                                    if st.form_submit_button("💾 Save Changes"):
                                        if _etk and _estk>0 and _eep>0:
                                            try:
                                                _ec=get_conn()
                                                _ec.execute("UPDATE trades SET ticker=?,option_type=?,quantity=?,strike=?,entry_price=?,expiry=?,strategy=?,updated_at=? WHERE trade_id=?",
                                                            (_etk,_eot.lower(),int(_eqt),float(_estk),float(_eep),_eexp.strftime("%Y-%m-%d"),_estrat,datetime.now().isoformat(),pos["ID"]))
                                                _ec.commit(); _ec.close()
                                                st.success(f"Updated {_etk} {_eot} ${_estk:.0f}")
                                                st.rerun()
                                            except Exception as _ee: st.error(f"{_ee}")
                                        else:
                                            st.warning("Ticker, strike and entry price required.")

    # ── Add / Edit Positions ──────────────────────────────────────────
    with tab1:
        st.markdown("---")
        st.markdown("#### ➕ Add New Position")
        with st.form("add_trade_form", clear_on_submit=True):
            _c1, _c2, _c3 = st.columns(3)
            _tk  = _c1.text_input("Ticker", placeholder="AAPL").upper().strip()
            _ot  = _c2.selectbox("Type", ["CALL", "PUT"])
            _qty = _c3.number_input("Qty (neg=short)", value=1, step=1)
            _c4, _c5, _c6 = st.columns(3)
            _stk = _c4.number_input("Strike", min_value=0.0, step=0.5)
            _ep  = _c5.number_input("Entry Price", min_value=0.0, step=0.01)
            _exp = _c6.date_input("Expiry")
            _c7, _c8 = st.columns(2)
            _strat = _c7.selectbox("Strategy", ["manual", "long_call", "long_put", "spread", "iron_condor", "covered_call", "cash_secured_put"])
            _notes = _c8.text_input("Notes (optional)")
            if st.form_submit_button("✅ Add Position"):
                if _tk and _stk > 0 and _ep > 0:
                    try:
                        _nc = get_conn()
                        _nc.execute("""INSERT INTO trades
                            (ticker, option_type, strike, entry_price, quantity, expiry,
                             strategy, notes, status, entry_date, created_at, updated_at)
                            VALUES (?,?,?,?,?,?,?,?,'OPEN',?,?,?)""",
                            (_tk, _ot.lower(), float(_stk), float(_ep), int(_qty),
                             _exp.strftime("%Y-%m-%d"), _strat, _notes,
                             datetime.now().strftime("%Y-%m-%d"),
                             datetime.now().isoformat(), datetime.now().isoformat()))
                        _nc.commit(); _nc.close()
                        st.success(f"✅ Added {_tk} {_ot} ${_stk:.0f}  entry ${_ep:.2f}")
                        st.rerun()
                    except Exception as _ae:
                        st.error(f"Error: {_ae}")
                else:
                    st.warning("Ticker, strike and entry price are required.")

    with tab2:
        if closed.empty:
            st.info("No closed trades yet.")
        else:
            closed["pnl_f"]    =pd.to_numeric(closed.get("pnl",0),    errors="coerce").fillna(0)
            closed["pnl_pct_f"]=pd.to_numeric(closed.get("pnl_pct",0),errors="coerce").fillna(0)
            closed["exit_dt"]  =pd.to_datetime(closed.get("exit_date",""),errors="coerce")
            closed["days_held_n"]=pd.to_numeric(closed.get("days_held",0),errors="coerce").fillna(0)
            _wins=closed[closed["pnl_f"]>0]; _losses=closed[closed["pnl_f"]<=0]
            _win_rate=(len(_wins)/len(closed)*100) if len(closed) else 0
            _avg_win=_wins["pnl_f"].mean() if len(_wins) else 0
            _avg_loss=_losses["pnl_f"].mean() if len(_losses) else 0
            _rr=abs(_avg_win/_avg_loss) if _avg_loss!=0 else 0
            _expect=(_win_rate/100*_avg_win + (1-_win_rate/100)*_avg_loss) if len(closed) else 0
            c1,c2,c3,c4=st.columns(4)
            c1.metric("Realized P&L",f"${closed['pnl_f'].sum():,.2f}")
            c2.metric("Win Rate",f"{_win_rate:.0f}%",f"{len(_wins)}W/{len(_losses)}L")
            c3.metric("Total Closed",len(closed))
            c4.metric("Avg P&L",f"${closed['pnl_f'].mean():,.2f}")
            c5,c6,c7,c8=st.columns(4)
            c5.metric("Avg Win",f"${_avg_win:,.2f}")
            c6.metric("Avg Loss",f"${_avg_loss:,.2f}")
            c7.metric("Risk:Reward",f"{_rr:.2f}:1",delta="Good" if _rr>=2 else "Poor",delta_color="normal" if _rr>=2 else "inverse")
            c8.metric("Expectancy/Trade",f"${_expect:,.2f}",delta="Positive edge" if _expect>0 else "Negative edge",delta_color="normal" if _expect>0 else "inverse")
            # Best / Worst trade
            if len(closed)>0:
                _best=closed.loc[closed["pnl_f"].idxmax()]
                _worst=closed.loc[closed["pnl_f"].idxmin()]
                bw1,bw2=st.columns(2)
                bw1.success(f"🏆 Best: **{_best.get('ticker','')} {_best.get('option_type','').upper()} ${_best.get('strike','')}** — ${_best['pnl_f']:+,.2f} ({_best['pnl_pct_f']:+.0f}%) held {int(_best['days_held_n'])}d")
                bw2.error(f"💀 Worst: **{_worst.get('ticker','')} {_worst.get('option_type','').upper()} ${_worst.get('strike','')}** — ${_worst['pnl_f']:+,.2f} ({_worst['pnl_pct_f']:+.0f}%) held {int(_worst['days_held_n'])}d")
            st.markdown("#### Avg Hold Time by Outcome")
            _hold_stats=closed.groupby(closed["pnl_f"]>0).agg(avg_days=("days_held_n","mean"),count=("pnl_f","count")).reset_index()
            _hold_stats["Outcome"]=_hold_stats["pnl_f"].map({True:"Winners",False:"Losers"})
            if not _hold_stats.empty:
                st.dataframe(_hold_stats[["Outcome","avg_days","count"]].rename(columns={"avg_days":"Avg Days Held","count":"Count"}),hide_index=True,use_container_width=True)

            st.markdown("#### By Ticker")
            _tkc=(closed.groupby("ticker").agg(trades=("pnl_f","count"),total_pnl=("pnl_f","sum"),avg_pnl=("pnl_f","mean"),
                  win_rate=("pnl_f",lambda x:f"{(x>0).sum()/len(x)*100:.0f}%")).reset_index().sort_values("total_pnl",ascending=False))
            _tkc.columns=["Ticker","Trades","Total P&L","Avg P&L","Win%"]
            _tkc_tot=pd.DataFrame([{
                "Ticker":"📊 TOTAL","Trades":int(closed["pnl_f"].count()),
                "Total P&L":closed["pnl_f"].sum(),"Avg P&L":closed["pnl_f"].mean(),
                "Win%":f"{(closed['pnl_f']>0).sum()/len(closed)*100:.0f}%",
            }])
            _tkc=pd.concat([_tkc,_tkc_tot],ignore_index=True)
            try: st.dataframe(_tkc.style.applymap(_color_pnl,subset=["Total P&L","Avg P&L"]),hide_index=True,use_container_width=True)
            except: st.dataframe(_tkc,hide_index=True,use_container_width=True)

            if "account_type" in closed.columns:
                st.markdown("#### By Account")
                _acd=(closed.groupby(closed["account_type"].fillna("Taxable")).agg(trades=("pnl_f","count"),total_pnl=("pnl_f","sum"),avg_pnl=("pnl_f","mean")).reset_index())
                _acd.columns=["Account","Trades","Total P&L","Avg P&L"]
                st.dataframe(_acd,hide_index=True)

            if "strategy" in closed.columns:
                st.markdown("#### By Strategy")
                _scd=(closed.groupby(closed["strategy"].fillna("manual")).agg(trades=("pnl_f","count"),total_pnl=("pnl_f","sum"),avg_pnl=("pnl_f","mean"),
                      win_rate=("pnl_f",lambda x:f"{(x>0).sum()/len(x)*100:.0f}%")).reset_index())
                _scd.columns=["Strategy","Trades","Total P&L","Avg P&L","Win%"]
                st.dataframe(_scd,hide_index=True)

            _ve=closed.dropna(subset=["exit_dt"]).sort_values("exit_dt").copy()
            if not _ve.empty:
                _ve["cum_pnl"]=_ve["pnl_f"].cumsum()
                fig_eq=go.Figure()
                fig_eq.add_trace(go.Scatter(x=_ve["exit_dt"],y=_ve["cum_pnl"],mode="lines+markers",fill="tonexty",line=dict(color="#00d4aa",width=2)))
                fig_eq.add_hline(y=0,line_dash="dash",line_color="#888")
                fig_eq.update_layout(template="plotly_white",title="Equity Curve",height=280)
                st.plotly_chart(fig_eq,use_container_width=True)

            st.markdown("#### All Closed Trades")
            _sc2=["trade_id","ticker","option_type","strike","expiry","entry_price","exit_price","entry_date","exit_date","pnl_f","pnl_pct_f","days_held","exit_reason"]
            for _ex in ["account_type","strategy","notes"]:
                if _ex in closed.columns: _sc2.append(_ex)
            _ac3=[c for c in _sc2 if c in closed.columns]
            try: st.dataframe(closed[_ac3].rename(columns={"pnl_f":"P&L $","pnl_pct_f":"P&L %"}).style.applymap(_color_pnl,subset=["P&L $","P&L %"]),hide_index=True,use_container_width=True)
            except: st.dataframe(closed[_ac3],hide_index=True,use_container_width=True)

    with tab3:
        st.markdown("#### 💡 Suggestions by Ticker")
        if not updated_rows:
            st.info("Add positions to get suggestions.")
        else:
            _oib: dict={}
            try:
                _oid2=available_trade_dates()
                if _oid2:
                    _oirow=load_oi_for_date(_oid2[0])
                    for _stk2 in _tg:
                        _trow=_oirow[_oirow["ticker"]==_stk2]
                        if not _trow.empty:
                            _cc2=pd.to_numeric(_trow["change_OI_Call"],errors="coerce").sum()
                            _pc2=pd.to_numeric(_trow["change_OI_Put"],errors="coerce").sum()
                            if _cc2>0 and _pc2<0:   _oib[_stk2]="🟢 BULLISH"
                            elif _cc2<0 and _pc2>0: _oib[_stk2]="🔴 BEARISH"
                            else:                    _oib[_stk2]="⚪ NEUTRAL"
            except: pass

            for _stk2,_legs2 in sorted(_tg.items()):
                _gp2=sum(l["PnL"] for l in _legs2)
                _gc2b=sum(abs(l["Entry"])*abs(l["Qty"])*100 for l in _legs2)
                _gpc2=(_gp2/_gc2b*100) if _gc2b>0 else 0
                _str2=_detect_strategy(_legs2)
                _spv2=_legs2[0]["Stock_Px"]
                _ob=_oib.get(_stk2,"⚪ NEUTRAL")
                _nd2=sum(l["Delta"] for l in _legs2)
                _md2=min(l["DTE"] for l in _legs2)
                if _md2<=3:              _gr,_gcol="🔴 EXIT ALL","#ff1744"; _gd2=f"Min DTE={_md2}. Close all legs."
                elif "BEARISH" in _ob and _nd2>0: _gr,_gcol="🟡 HEDGE — Bearish OI","#ff9800"; _gd2="OI bearish but net long delta. Buy puts or reduce."
                elif "BULLISH" in _ob and _nd2<0: _gr,_gcol="🟡 HEDGE — Bullish OI","#ff9800"; _gd2="OI bullish but net short delta. Trim short legs."
                elif _gpc2>25:           _gr,_gcol="🟢 TAKE PROFIT","#00c853"; _gd2=f"Group up {_gpc2:.1f}%. Lock gains or roll."
                elif _gpc2<-40:          _gr,_gcol="🔴 CUT LOSS","#ff1744"; _gd2=f"Group down {_gpc2:.1f}%. Manage risk."
                elif abs(_nd2)<0.05:     _gr,_gcol="🟢 DELTA-NEUTRAL","#00c853"; _gd2="Well-hedged. Monitor theta decay."
                else:                    _gr,_gcol="🟢 HOLD","#00c853"; _gd2=f"No action. Net Δ={_nd2:+.3f}, OI={_ob}."
                _iv_rk2,_iv_pk2,_iv_ck2=_iv_rank_pct(_stk2)
                _dte_e2=_days_to_earnings(_stk2)
                _iv_badge=f" | IV Rank:{_iv_rk2:.0f}%({'🔥HIGH' if (_iv_rk2 or 0)>75 else '💤LOW' if (_iv_rk2 or 100)<25 else 'MID'})" if _iv_rk2 is not None else ""
                _earn_badge=(f" | ⚠️ EARNINGS {_dte_e2}d" if _dte_e2 is not None and _dte_e2<14 else "")
                st.markdown(
                    f"<div>"
                    f"<b>{_stk2}</b> &nbsp;"
                    f"{_str2} &nbsp; Stock <b>${_spv2:.2f}</b> &nbsp; OI:{_ob} &nbsp; NetΔ:{_nd2:+.3f}{_iv_badge}{_earn_badge}<br>"
                    f"<b>{_gr}</b> {_gd2}<br>"
                    f"Group P&L ${_gp2:+,.2f} ({_gpc2:+.1f}%) | {len(_legs2)} leg(s) | Min DTE {_md2}"
                    f"</div>",unsafe_allow_html=True)
                with st.expander(f"  ↳ {_stk2} leg-by-leg"):
                    for _li2,t2 in enumerate(_legs2):
                        _sl2="BUY" if t2["Qty"]>0 else "SELL"; _lc2=_LC[_li2%len(_LC)]
                        _lr=("🔴 EXIT" if t2["DTE"]<=3 else "🟢 TP" if t2["PnL%"]>50 else "🔴 CUT" if t2["PnL%"]<-60 else "⚪ HOLD")
                        st.markdown(
                            f"<div>"
                            f"L{_li2+1}: {_sl2} {t2['Type']} ${t2['Strike']} exp {t2['Expiry']} ×{abs(t2['Qty'])} "
                            f"&nbsp; {_lr} P&L <b>${t2['PnL']:+,.2f}</b> ({t2['PnL%']:+.1f}%) "
                            f"Δ:{t2['Delta']:+.3f} Θ:${t2['Theta']*100:.2f}/d"
                            f"</div>",unsafe_allow_html=True)

    with tab4:
        st.markdown("#### 📈 P&L Breakdown")
        if closed.empty and not updated_rows:
            st.info("No trades to analyze.")
        else:
            _bd_rows=[]
            for _,_r in closed.iterrows():
                _pv=pd.to_numeric(_r.get("pnl",0),errors="coerce") or 0
                _dt=pd.to_datetime(_r.get("exit_date",""),errors="coerce")
                _bd_rows.append({"date":_dt,"pnl":_pv,"account":_r.get("account_type") or "Taxable","ticker":_r.get("ticker",""),"type":"Realized"})
            for _r2 in updated_rows:
                _pv2=_r2.get("PnL",0) or 0
                _dt2=pd.to_datetime(_r2.get("Entry_Date",""),errors="coerce")
                _bd_rows.append({"date":_dt2,"pnl":_pv2,"account":_r2.get("Account","Taxable"),"ticker":_r2.get("Ticker",""),"type":"Unrealized"})
            _bd=pd.DataFrame(_bd_rows).dropna(subset=["date"])
            if _bd.empty:
                st.info("No dated trade data.")
            else:
                _now2=pd.Timestamp.now()
                _w=_bd[_bd["date"]>=_now2-pd.Timedelta(days=7)]["pnl"].sum()
                _m=_bd[_bd["date"]>=_now2-pd.Timedelta(days=30)]["pnl"].sum()
                _y=_bd[_bd["date"]>=_now2-pd.Timedelta(days=365)]["pnl"].sum()
                _ov=_bd["pnl"].sum()
                b1,b2,b3,b4=st.columns(4)
                b1.metric("This Week",f"${_w:,.2f}"); b2.metric("This Month",f"${_m:,.2f}")
                b3.metric("This Year",f"${_y:,.2f}"); b4.metric("Overall",f"${_ov:,.2f}")
                st.markdown("#### By Account")
                for _at in _bd["account"].unique():
                    _ad=_bd[_bd["account"]==_at]
                    _ai="🏦" if _at=="Taxable" else "🛡️"
                    _aw=_ad[_ad["date"]>=_now2-pd.Timedelta(days=7)]["pnl"].sum()
                    _am=_ad[_ad["date"]>=_now2-pd.Timedelta(days=30)]["pnl"].sum()
                    _ay=_ad[_ad["date"]>=_now2-pd.Timedelta(days=365)]["pnl"].sum()
                    _at2=_ad["pnl"].sum()
                    st.markdown(f"{_ai} **{_at}**: Week ${_aw:,.2f} | Month ${_am:,.2f} | Year ${_ay:,.2f} | Total ${_at2:,.2f}"+(" *(no tax)*" if _at!="Taxable" else " *(taxable)*"))
                st.markdown("#### By Ticker")
                _tkbd=_bd.groupby("ticker")["pnl"].sum().sort_values(ascending=False).reset_index()
                _brc=["#2e7d32" if v>=0 else "#c62828" for v in _tkbd["pnl"]]
                fig_tk=go.Figure(go.Bar(x=_tkbd["ticker"],y=_tkbd["pnl"],marker_color=_brc,
                                        text=_tkbd["pnl"].apply(lambda v:f"${v:,.0f}"),textposition="outside"))
                fig_tk.add_hline(y=0,line_dash="dash",line_color="#888")
                fig_tk.update_layout(template="plotly_white",height=280,xaxis_title="Ticker",yaxis_title="P&L ($)",margin=dict(t=20,b=40))
                st.plotly_chart(fig_tk,use_container_width=True)
                _bd["month"]=_bd["date"].dt.to_period("M").astype(str)
                _mo=_bd.groupby("month")["pnl"].sum().reset_index()
                if len(_mo)>1:
                    st.markdown("#### Monthly Trend")
                    _mc2=["#2e7d32" if v>=0 else "#c62828" for v in _mo["pnl"]]
                    fig_mo=go.Figure(go.Bar(x=_mo["month"],y=_mo["pnl"],marker_color=_mc2))
                    fig_mo.add_hline(y=0,line_dash="dash",line_color="#888")
                    fig_mo.update_layout(template="plotly_white",height=260,margin=dict(t=10,b=40))
                    st.plotly_chart(fig_mo,use_container_width=True)
                st.markdown("#### Realized vs Unrealized")
                _rv=_bd.groupby("type")["pnl"].sum().reset_index(); _rv.columns=["Type","P&L $"]
                st.dataframe(_rv,hide_index=True)

# ===================================================================
# ──  PAGE 5: BACKTEST LAB
# ===================================================================
elif page == "📊 Backtest Lab":
    _hdr1, _hdr2 = st.columns([4, 1])
    with _hdr1:
        st.markdown("## 📊 OI Signal Backtest Lab")
    with _hdr2:
        if st.button("🔄 Refresh", key="refresh_backtest"):
            st.rerun()
        with st.popover("ℹ️"):
            st.markdown(_PAGE_HELP.get(page, ""))
    st.markdown("*Backtested, data-proven strategies — only show what actually worked in your real data*")

    tab_edge, tab_oi = st.tabs(["📐 Edge Lab (Proven Strategies)", "📊 OI Direction Backtest"])

    # ═══════════════════════════════════════════════════════════════════
    # TAB 1: EDGE LAB — Gamma Wall + OI Flow with actual historical proof
    # ═══════════════════════════════════════════════════════════════════
    with tab_edge:
        st.markdown("### 📐 Edge Lab — What Actually Works in Your Data")
        st.info(
            "**Finding from backtesting Jan–May 2026 data across 8 tickers:**\n\n"
            "✅ **Gamma Wall Selling** — MSFT: 100% win rate (85 trades), GOOGL: 92% (77 trades), SPY: 70% (56 trades)\n\n"
            "✅ **OI Flow 3d MA** — Acts as CONTRARIAN for ETFs (SPY/QQQ IC = -0.35), MOMENTUM for individual stocks\n\n"
            "❌ **PCR Contrarian alone** — Does NOT work in trending markets (win rate ~42% in bear trend)\n\n"
            "The logic for gamma walls: market makers hold massive short gamma at these strikes "
            "and actively sell stock as price approaches to stay delta-neutral. This creates a structural ceiling."
        )

        st.markdown("#### 🧱 Gamma Wall Scanner — Current Opportunities")
        st.caption(
            "Call Wall = strike where call OI is ≥ 2.5× average OI. "
            "Strategy: sell a call spread with short leg AT the wall. "
            "Collect premium. Win when stock stays below wall (historically 70–100%)."
        )

        if st.button("🔍 Scan All Tickers for Gamma Walls", type="primary", key="el_scan"):
            with st.spinner("Scanning all tickers…"):
                _el_conn = get_conn()
                _el_today = _el_conn.execute(
                    "SELECT trade_date FROM stock_daily "
                    "ORDER BY substr(trade_date,7,4)||substr(trade_date,1,2)||substr(trade_date,4,2) DESC LIMIT 1"
                ).fetchone()
                _today_d = _el_today[0] if _el_today else ""
                _all_tickers = [r[0] for r in _el_conn.execute(
                    "SELECT DISTINCT ticker FROM stock_daily").fetchall()]

                import math as _math

                def _bt_gamma(ticker, conn_inner, wall_mult=2.5, hold=5):
                    _dfp = pd.read_sql(
                        "SELECT trade_date, close FROM stock_daily WHERE ticker=? "
                        "ORDER BY substr(trade_date,7,4)||substr(trade_date,1,2)||substr(trade_date,4,2)",
                        conn_inner, params=(ticker,))
                    _dfp['close'] = pd.to_numeric(_dfp['close'], errors='coerce')
                    _dfp = _dfp.dropna().reset_index(drop=True)
                    _dfoi = pd.read_sql(
                        "SELECT trade_date_now, strike, openInt_Call_now "
                        "FROM options_change WHERE ticker=? AND openInt_Call_now > 0",
                        conn_inner, params=(ticker,))
                    _dfoi['openInt_Call_now'] = pd.to_numeric(_dfoi['openInt_Call_now'], errors='coerce')
                    _dfoi = _dfoi.dropna()
                    trades = []
                    for _date, _grp in _dfoi.groupby('trade_date_now'):
                        if len(_grp) < 6: continue
                        _avg = _grp['openInt_Call_now'].mean()
                        _w = _grp[_grp['openInt_Call_now'] >= _avg * wall_mult]
                        if _w.empty: continue
                        _cw = float(_w.sort_values('openInt_Call_now', ascending=False)['strike'].iloc[0])
                        _pr = _dfp[_dfp['trade_date'] == _date]
                        if _pr.empty: continue
                        _i = _pr.index[0]
                        _spot = float(_pr['close'].iloc[0])
                        if _spot >= _cw or (_cw - _spot) / _spot < 0.003: continue
                        if _i + hold >= len(_dfp): continue
                        _fh = float(_dfp['close'].iloc[_i+1:_i+hold+1].max())
                        trades.append({'win': _fh < _cw, 'wall_str': _grp['openInt_Call_now'].max()/_avg})
                    if not trades or len(trades) < 5:
                        return None
                    _t = pd.DataFrame(trades)
                    return {'trades': len(_t), 'win_rate': round(_t['win'].mean()*100,1),
                            'avg_str': round(_t['wall_str'].mean(),1)}

                _scan_rows = []
                for _tk in _all_tickers:
                    try:
                        _hist = _bt_gamma(_tk, _el_conn)
                        if not _hist: continue
                        # Current wall
                        _oi_today = pd.read_sql(
                            "SELECT strike, openInt_Call_now FROM options_change "
                            "WHERE ticker=? AND trade_date_now=? AND openInt_Call_now>0",
                            _el_conn, params=(_tk, _today_d))
                        _oi_today['openInt_Call_now'] = pd.to_numeric(_oi_today['openInt_Call_now'], errors='coerce')
                        _oi_today = _oi_today.dropna()
                        if _oi_today.empty: continue
                        _avg_oi = _oi_today['openInt_Call_now'].mean()
                        _walls_now = _oi_today[_oi_today['openInt_Call_now'] >= _avg_oi * 2.5]
                        if _walls_now.empty: continue
                        _wall_strike = float(_walls_now.sort_values('openInt_Call_now', ascending=False)['strike'].iloc[0])
                        _wall_str_now = float(_walls_now['openInt_Call_now'].max()) / _avg_oi
                        _spot_r = _el_conn.execute(
                            "SELECT close FROM stock_daily WHERE ticker=? AND trade_date=?",
                            (_tk, _today_d)).fetchone()
                        if not _spot_r: continue
                        _spot_now = float(_spot_r[0])
                        if _spot_now >= _wall_strike: continue
                        _dist = (_wall_strike - _spot_now) / _spot_now * 100
                        if _dist < 0.3: continue
                        _scan_rows.append({
                            'Ticker': _tk,
                            'Spot': f"${_spot_now:.2f}",
                            'Call Wall': f"${_wall_strike:.2f}",
                            'Distance': f"{_dist:.1f}%",
                            'Wall Strength': f"{_wall_str_now:.1f}× avg OI",
                            'Historical Win%': f"{_hist['win_rate']:.0f}%",
                            'Historical Trades': _hist['trades'],
                            'Action': f"Sell call spread at ${_wall_strike:.0f}",
                        })
                    except Exception:
                        continue
                _el_conn.close()

                if _scan_rows:
                    _scan_df = pd.DataFrame(_scan_rows).sort_values('Historical Win%', ascending=False)
                    st.success(f"Found {len(_scan_df)} gamma wall opportunities today")
                    st.dataframe(_scan_df, hide_index=True, use_container_width=True)

                    st.markdown("#### 💡 How to Trade This")
                    st.markdown("""
**Strategy: Call Credit Spread at the Gamma Wall**

| Step | What to do |
|------|-----------|
| 1 | Pick the ticker with highest Historical Win% and wall distance ≥ 1% |
| 2 | Sell a call option AT the wall strike (e.g. GOOGL $175 call) |
| 3 | Buy a call 1–2 strikes HIGHER for protection (e.g. GOOGL $177.5 call) |
| 4 | Collect the net premium (the difference) |
| 5 | Hold to expiry — keep full premium if stock stays below wall |

**Why it works:** Market makers (dealers) have sold massive call OI at that strike.
To stay delta-neutral, they SELL stock as price rises toward the wall.
This creates a self-reinforcing ceiling — the wall repels price.

**Risk:** If a major catalyst (earnings, news) pushes stock above the wall, you lose the spread width.
**Mitigation:** Use tight spreads (1–2 strikes wide), never risk more than 2× potential profit.
""")
                else:
                    st.info("No gamma wall opportunities found for today's data.")

        st.markdown("---")
        st.markdown("#### 📊 Run Backtest on a Specific Ticker")
        st.caption("See exact trade-by-trade history for gamma wall strategy on any ticker in your DB")
        _el_tickers = [r[0] for r in get_conn().execute("SELECT DISTINCT ticker FROM stock_daily ORDER BY ticker").fetchall()]
        _el_sel = st.selectbox("Ticker", _el_tickers, key="el_ticker_sel")
        _el_hold = st.slider("Hold period (days)", 3, 10, 5, key="el_hold")
        _el_mult = st.slider("Wall multiplier (OI threshold)", 1.5, 4.0, 2.5, 0.5, key="el_mult")

        if st.button("▶ Run Gamma Wall Backtest", key="el_run"):
            with st.spinner(f"Backtesting gamma wall for {_el_sel}…"):
                _bconn = get_conn()
                _dfp_bt = pd.read_sql(
                    "SELECT trade_date, close FROM stock_daily WHERE ticker=? "
                    "ORDER BY substr(trade_date,7,4)||substr(trade_date,1,2)||substr(trade_date,4,2)",
                    _bconn, params=(_el_sel,))
                _dfp_bt['close'] = pd.to_numeric(_dfp_bt['close'], errors='coerce')
                _dfp_bt = _dfp_bt.dropna().reset_index(drop=True)
                _dfoi_bt = pd.read_sql(
                    "SELECT trade_date_now, strike, openInt_Call_now FROM options_change "
                    "WHERE ticker=? AND openInt_Call_now>0",
                    _bconn, params=(_el_sel,))
                _dfoi_bt['openInt_Call_now'] = pd.to_numeric(_dfoi_bt['openInt_Call_now'], errors='coerce')
                _dfoi_bt = _dfoi_bt.dropna()
                _bconn.close()

                _bt_trades = []
                for _bdate, _bgrp in _dfoi_bt.groupby('trade_date_now'):
                    if len(_bgrp) < 5: continue
                    _bavg = _bgrp['openInt_Call_now'].mean()
                    _bw = _bgrp[_bgrp['openInt_Call_now'] >= _bavg * _el_mult]
                    if _bw.empty: continue
                    _bcw = float(_bw.sort_values('openInt_Call_now', ascending=False)['strike'].iloc[0])
                    _bwstr = float(_bw['openInt_Call_now'].max()) / _bavg
                    _bpr = _dfp_bt[_dfp_bt['trade_date'] == _bdate]
                    if _bpr.empty: continue
                    _bi = _bpr.index[0]
                    _bspot = float(_bpr['close'].iloc[0])
                    if _bspot >= _bcw or (_bcw - _bspot) / _bspot < 0.003: continue
                    if _bi + _el_hold >= len(_dfp_bt): continue
                    _bfuture = _dfp_bt['close'].iloc[_bi+1:_bi+_el_hold+1]
                    _bfh = float(_bfuture.max())
                    _bwin = _bfh < _bcw
                    _bdist = (_bcw - _bspot) / _bspot * 100
                    _bt_trades.append({
                        'Date': _bdate, 'Spot': round(_bspot,2), 'Call Wall': round(_bcw,2),
                        'Wall Str (×avg)': round(_bwstr,1),
                        'Dist to Wall': f"{_bdist:.1f}%",
                        'High over hold': round(_bfh,2),
                        'Result': '✅ WIN (kept premium)' if _bwin else '❌ LOSS (breached)',
                        'Win': _bwin,
                    })

                if not _bt_trades:
                    st.warning("No trades found — try reducing the wall multiplier or pick a different ticker.")
                else:
                    _bt_df = pd.DataFrame(_bt_trades)
                    _wr = _bt_df['Win'].mean() * 100
                    _wins = _bt_df['Win'].sum()
                    _losses = len(_bt_df) - _wins

                    _cm1, _cm2, _cm3, _cm4 = st.columns(4)
                    _cm1.metric("Win Rate", f"{_wr:.1f}%",
                                delta="Strong ✅" if _wr >= 65 else ("Marginal ⚠️" if _wr >= 50 else "Weak ❌"))
                    _cm2.metric("Total Trades", len(_bt_df))
                    _cm3.metric("Wins / Losses", f"{int(_wins)} / {int(_losses)}")
                    _cm4.metric("Wall Threshold", f"{_el_mult}× avg OI")

                    if _wr >= 65:
                        st.success(
                            f"✅ **Strong Edge Found!** {_el_sel} gamma wall held {_wr:.0f}% of the time over "
                            f"{len(_bt_df)} observations. Selling call spreads at the wall has a provable statistical edge on this ticker."
                        )
                    elif _wr >= 50:
                        st.warning(f"⚠️ Marginal edge ({_wr:.0f}%). Better than random but not strong enough to trade aggressively.")
                    else:
                        st.error(f"❌ No edge ({_wr:.0f}%). The wall was frequently breached. Try a higher multiplier or different ticker.")

                    # Equity curve simulation (assuming fixed 1% premium collected per win)
                    _bt_df['pnl_sim'] = _bt_df['Win'].apply(lambda w: 1.0 if w else -2.0)  # 1:2 reward:risk
                    _bt_df['cum_pnl'] = _bt_df['pnl_sim'].cumsum()

                    _fig_eq = go.Figure()
                    _fig_eq.add_trace(go.Scatter(
                        x=list(range(len(_bt_df))), y=_bt_df['cum_pnl'],
                        mode='lines+markers',
                        line=dict(color='#26a69a', width=2),
                        marker=dict(color=_bt_df['Win'].map({True:'#26a69a', False:'#ef5350'}), size=8),
                        name='Cumulative P&L (units)'
                    ))
                    _fig_eq.add_hline(y=0, line_color='#888', line_dash='dash')
                    _fig_eq.update_layout(
                        title=f"{_el_sel} Gamma Wall — Simulated Equity Curve (1:2 R:R)",
                        xaxis_title="Trade #", yaxis_title="Cumulative P&L (units)",
                        height=300, plot_bgcolor='rgba(0,0,0,0)', paper_bgcolor='rgba(0,0,0,0)',
                        font_color='#fafafa', margin=dict(t=40,b=20)
                    )
                    st.plotly_chart(_fig_eq, use_container_width=True)
                    st.caption("P&L simulation: +1 unit per win, -2 units per loss (conservative 1:2 R:R assuming tight spread)")

                    with st.expander("📋 Full Trade Log", expanded=False):
                        st.dataframe(_bt_df.drop(columns=['Win','pnl_sim','cum_pnl']),
                                     hide_index=True, use_container_width=True)

    # ═══════════════════════════════════════════════════════════════════
    # TAB 2: OI DIRECTION BACKTEST (original)
    # ═══════════════════════════════════════════════════════════════════
    with tab_oi:
        st.markdown("### 📊 OI Direction Signal Backtest")
        st.caption("Tests whether net call vs put OI change predicted the next-day price direction.")

        dates = available_trade_dates()
        if not dates:
            st.warning("No data.")
            st.stop()

        day_df = load_oi_for_date(dates[0])
        tickers = sorted(day_df["ticker"].unique()) if not day_df.empty else []

        c1, c2 = st.columns(2)
        bt_ticker = c1.selectbox("Ticker", tickers, index=0 if tickers else None)
        bt_lookback = c2.slider("Lookback Days", 3, 20, 8)

        if bt_ticker:
            with st.spinner("Backtesting..."):
                result = backtest_oi_signals(bt_ticker, bt_lookback)

            if result is None:
                st.warning("Not enough data for backtest.")
            else:
                res_df, accuracy = result
                valid_moves = res_df.dropna(subset=["next_day_move"])

                # ── Metrics row ──────────────────────────────────────────────
                avg_move   = valid_moves["next_day_move"].abs().mean() if not valid_moves.empty else 0
                avg_bull   = valid_moves[valid_moves["signal"] == "BULLISH"]["next_day_move"].mean()
                avg_bear   = valid_moves[valid_moves["signal"] == "BEARISH"]["next_day_move"].mean()
                bulls      = len(res_df[res_df["signal"] == "BULLISH"])
                bears      = len(res_df[res_df["signal"] == "BEARISH"])
                last_sig   = res_df.iloc[0]["signal"] if not res_df.empty else "N/A"
                last_pcr   = res_df.iloc[0]["pcr"]    if not res_df.empty else 0

                mc1, mc2, mc3, mc4, mc5 = st.columns(5)
                if accuracy is not None:
                    mc1.metric("Signal Accuracy", f"{accuracy:.0f}%",
                               delta="Reliable ✅" if accuracy > 55 else "Weak ⚠️",
                               delta_color="normal" if accuracy > 55 else "inverse")
                else:
                    mc1.metric("Signal Accuracy", "N/A")
                mc2.metric("Days Tested",    len(res_df))
                mc3.metric("Avg Next Move",  f"{avg_move:.2f}%")
                mc4.metric("Bull/Bear Days", f"{bulls}/{bears}")
                mc5.metric("Latest Signal",  last_sig)

                # ── Insights Panel ───────────────────────────────────────────
                st.markdown("### 🧠 What This Means & What To Do")

                # 1. Signal reliability
                if accuracy is None:
                    st.warning("**Accuracy N/A** — not enough confirmed signal days to evaluate reliability.")
                elif accuracy >= 65:
                    st.success(f"**Accuracy {accuracy:.0f}% — HIGH RELIABILITY.** OI signals for {bt_ticker} have historically predicted next-day direction well. Trust the signal.")
                elif accuracy >= 50:
                    st.info(f"**Accuracy {accuracy:.0f}% — MODERATE.** Signals are slightly better than random. Use as one input, not the only one.")
                else:
                    st.warning(f"**Accuracy {accuracy:.0f}% — WEAK.** OI signals have NOT reliably predicted direction for {bt_ticker}. Do not trade on OI signal alone here.")

                # 2. Current signal interpretation
                with st.expander("📊 Current Signal Breakdown", expanded=True):
                    sc1, sc2 = st.columns(2)
                    with sc1:
                        st.markdown(f"**Latest Signal:** `{last_sig}`")
                        st.markdown(f"**Put/Call Ratio:** `{last_pcr:.2f}` — {'Bearish (>1.0)' if last_pcr > 1.0 else 'Bullish (<1.0)'}")
                        if not pd.isna(avg_bull):
                            st.markdown(f"**Avg move on BULL signals:** `{avg_bull:+.2f}%`")
                        if not pd.isna(avg_bear):
                            st.markdown(f"**Avg move on BEAR signals:** `{avg_bear:+.2f}%`")
                    with sc2:
                        # What to do box
                        if last_sig == "BULLISH" and (accuracy or 0) >= 55:
                            st.success("**ACTION:** Consider CALL options or long position.\n\nOI shows call accumulation. If accuracy ≥ 55% this signal has edge.")
                        elif last_sig == "BEARISH" and (accuracy or 0) >= 55:
                            st.error("**ACTION:** Consider PUT options or hedge longs.\n\nOI shows put accumulation. Protect open positions.")
                        elif last_sig == "NEUTRAL":
                            st.info("**ACTION:** Stay flat or use a straddle/strangle.\n\nNo clear directional bias in OI flow.")
                        else:
                            st.warning("**ACTION:** Signal present but accuracy is low.\n\nWait for confirmation from price action before entering.")

                # 3. Avg move context
                with st.expander("📐 Expected Move Context"):
                    st.markdown(f"""
    | Metric | Value |
    |---|---|
    | Avg absolute next-day move | **{avg_move:.2f}%** |
    | Avg move on BULL signals | **{avg_bull:+.2f}%** if confirmed |
    | Avg move on BEAR signals | **{avg_bear:+.2f}%** if confirmed |
    | What this means | {'Volatile ticker — wide strikes needed' if avg_move > 2 else 'Low-volatility ticker — tight spreads viable'} |
    """)
                    if avg_move > 0:
                        st.markdown(f"**Strike selection tip:** For {bt_ticker} with avg move {avg_move:.2f}%, "
                                    f"{'go 1–2 strikes OTM for directional plays.' if avg_move < 2 else 'consider ATM or 1 strike OTM — moves are large enough to profit quickly.'}")

                # 4. Day-by-day results table
                with st.expander("📋 Day-by-Day Signal Log"):
                    _show = res_df.copy()
                    if "correct" in _show.columns:
                        _show["result"] = _show["correct"].map({True: "✅ Correct", False: "❌ Wrong", None: "—"})
                    st.dataframe(_show, hide_index=True, use_container_width=True)

                # 5. Chart
                if "next_day_move" in res_df.columns and "stock_close" in res_df.columns:
                    valid = res_df.dropna(subset=["next_day_move", "stock_close"])
                    if not valid.empty:
                        fig = make_subplots(rows=2, cols=1, shared_xaxes=True,
                                            row_heights=[0.6, 0.4],
                                            subplot_titles=["Stock Price", "Next-Day Move % (Signal Annotated)"])
                        fig.add_trace(go.Scatter(x=valid["date"], y=valid["stock_close"],
                                                mode="lines+markers", name="Price",
                                                line=dict(color="#00d4aa")), row=1, col=1)
                        colors = ["#00c853" if x > 0 else "#ff1744" for x in valid["next_day_move"]]
                        fig.add_trace(go.Bar(x=valid["date"], y=valid["next_day_move"],
                                            name="Actual Move %", marker_color=colors), row=2, col=1)
                        for _, r in valid.iterrows():
                            color = "#00c853" if r["signal"] == "BULLISH" else "#ff1744" if r["signal"] == "BEARISH" else "#888"
                            fig.add_annotation(x=r["date"], y=r.get("next_day_move", 0),
                                              text="▲" if r["signal"] == "BULLISH" else "▼" if r["signal"] == "BEARISH" else "●",
                                              showarrow=False, font=dict(color=color, size=14), row=2, col=1)
                        fig.update_layout(template="plotly_white", height=480, showlegend=False,
                                          title=f"{bt_ticker} — OI Signal vs Actual Next-Day Move")
                        st.plotly_chart(fig, use_container_width=True)

        # Multi-ticker accuracy comparison
        st.markdown("<div>📊 Multi-Ticker Accuracy Comparison</div>", unsafe_allow_html=True)
        if st.button("Run Accuracy Scan (all tickers)"):
            accuracies = []
            for tk in tickers[:15]:
                try:
                    result = backtest_oi_signals(tk, 8)
                    if result and result[1] is not None:
                        accuracies.append(dict(Ticker=tk, Accuracy=round(result[1], 1), Days=len(result[0])))
                except Exception:
                    pass
            if accuracies:
                acc_df = pd.DataFrame(accuracies).sort_values("Accuracy", ascending=False)
                st.dataframe(acc_df, hide_index=True)
                fig = px.bar(acc_df, x="Ticker", y="Accuracy", color="Accuracy",
                             color_continuous_scale="RdYlGn", title="OI Signal Accuracy by Ticker")
                fig.add_hline(y=50, line_dash="dash", line_color="white", annotation_text="Random (50%)")
                fig.update_layout(template="plotly_white", height=350)
                st.plotly_chart(fig)


# ===================================================================
# ──  PAGE 6: LIVE POSITION PREDICTOR
# ===================================================================
elif page == "🔮 Live Position Predictor":
    _hdr1, _hdr2 = st.columns([4, 1])
    with _hdr1:
        st.markdown("## 🔮 Live Position Predictor")
    with _hdr2:
        if st.button("🔄 Refresh", key="refresh_predictor"):
            st.rerun()
        with st.popover("ℹ️"):
            st.markdown(_PAGE_HELP.get(page, ""))
    st.markdown("*Futures + VIX + OI flow → Real-time position recommendations*")

    # ── 1) Fetch live market data ──
    @st.cache_data(ttl=120)
    def _fetch_live_data():
        syms = {
            "ES=F": "S&P 500 Futures", "NQ=F": "Nasdaq Futures",
            "YM=F": "Dow Futures", "RTY=F": "Russell 2K Futures",
            "^VIX": "VIX", "^GSPC": "S&P 500", "^IXIC": "Nasdaq",
            "^DJI": "Dow Jones", "GC=F": "Gold", "CL=F": "WTI Oil",
            "^TNX": "10Y Yield", "DX-Y.NYB": "Dollar Index",
        }
        rows = []
        for sym, name in syms.items():
            try:
                t = yf.Ticker(sym)
                h = t.history(period="5d")
                if len(h) >= 2:
                    cur = float(h["Close"].iloc[-1])
                    prev = float(h["Close"].iloc[-2])
                    chg_pct = (cur - prev) / prev * 100 if prev else 0
                    rows.append(dict(Symbol=sym, Name=name, Price=cur, Change=cur - prev, Pct=chg_pct))
            except Exception:
                pass
        return pd.DataFrame(rows)

    live = _fetch_live_data()

    # ── 2) Market Regime Detection ──
    vix_row = live[live["Symbol"] == "^VIX"] if not live.empty else pd.DataFrame()
    es_row = live[live["Symbol"] == "ES=F"] if not live.empty else pd.DataFrame()
    nq_row = live[live["Symbol"] == "NQ=F"] if not live.empty else pd.DataFrame()
    spx_row = live[live["Symbol"] == "^GSPC"] if not live.empty else pd.DataFrame()
    tnx_row = live[live["Symbol"] == "^TNX"] if not live.empty else pd.DataFrame()
    dx_row = live[live["Symbol"] == "DX-Y.NYB"] if not live.empty else pd.DataFrame()

    vix_val = float(vix_row["Price"].iloc[0]) if not vix_row.empty else None
    vix_chg = float(vix_row["Pct"].iloc[0]) if not vix_row.empty else None
    es_pct = float(es_row["Pct"].iloc[0]) if not es_row.empty else None
    nq_pct = float(nq_row["Pct"].iloc[0]) if not nq_row.empty else None
    spx_price = float(spx_row["Price"].iloc[0]) if not spx_row.empty else None
    tnx_val = float(tnx_row["Price"].iloc[0]) if not tnx_row.empty else None
    dx_pct = float(dx_row["Pct"].iloc[0]) if not dx_row.empty else None

    # Regime classification
    regime = "UNKNOWN"
    regime_color = "#ffd600"
    regime_detail = ""
    if vix_val is not None and es_pct is not None:
        if vix_val < 15 and es_pct > 0:
            regime, regime_color = "RISK-ON (Low Vol Bull)", "#00c853"
            regime_detail = "VIX below 15, futures green. Complacency — favor long positions but watch for reversal."
        elif vix_val < 20 and es_pct > 0:
            regime, regime_color = "RISK-ON (Moderate Vol)", "#00d4aa"
            regime_detail = "Normal volatility, positive momentum. Standard bullish positioning OK."
        elif vix_val >= 20 and vix_val < 30 and es_pct < 0:
            regime, regime_color = "RISK-OFF (Elevated Vol)", "#ff9100"
            regime_detail = "VIX elevated, futures red. Hedging recommended. Reduce long exposure."
        elif vix_val >= 30:
            regime, regime_color = "PANIC (High Vol)", "#ff1744"
            regime_detail = "VIX above 30 — fear spike. Contrarian buy setups possible but extremely risky."
        elif vix_val < 20 and es_pct < 0:
            regime, regime_color = "CAUTIOUS (Low Vol Dip)", "#ffd600"
            regime_detail = "Futures dip on low VIX. Likely a buy-the-dip setup. Watch OI for confirmation."
        elif vix_val >= 20 and es_pct >= 0:
            regime, regime_color = "RECOVERY (Vol + Green)", "#4fc3f7"
            regime_detail = "VIX still elevated but futures recovering. Transition phase — watch for follow-through."
        else:
            regime, regime_color = "MIXED", "#ffd600"
            regime_detail = "No clear regime. Use OI signals for direction."

    # ── Dashboard header ──
    st.markdown(
        f"<div style='background:var(--panel-solid);color:var(--text);border:2px solid {regime_color};"
        f"border-radius:12px;padding:18px 24px;margin-bottom:16px;box-shadow:0 2px 8px rgba(0,0,0,0.08);'>"
        f"<h3>Market Regime: {regime}</h3>"
        f"<p>{regime_detail}</p></div>",
        unsafe_allow_html=True,
    )

    # ── Live market cards ──
    if not live.empty:
        st.markdown("<div>📡 Live Market Snapshot</div>", unsafe_allow_html=True)
        cols = st.columns(4)
        for i, (_, row) in enumerate(live.iterrows()):
            c = cols[i % 4]
            color = "#00c853" if row["Pct"] >= 0 else "#ff1744"
            arrow = "▲" if row["Pct"] >= 0 else "▼"
            c.markdown(
                f"<div><b>{row['Name']}</b><br>"
                f"${row['Price']:,.2f} {arrow} {row['Pct']:+.2f}%</div>",
                unsafe_allow_html=True,
            )

    # ── 3) VIX Term Structure Signal ──
    st.markdown("<div>📊 Volatility & Yield Analysis</div>", unsafe_allow_html=True)
    vc1, vc2, vc3, vc4 = st.columns(4)
    vc1.metric("VIX Level", f"{vix_val:.1f}" if vix_val else "N/A",
               delta=f"{vix_chg:+.1f}%" if vix_chg else None,
               delta_color="inverse")
    vc2.metric("ES Futures", f"{es_pct:+.2f}%" if es_pct is not None else "N/A")
    vc3.metric("10Y Yield", f"{tnx_val:.2f}%" if tnx_val else "N/A")
    vc4.metric("Dollar Index", f"{dx_pct:+.2f}% chg" if dx_pct is not None else "N/A")

    # ── 4) OI-Based Position Analysis for user's tickers ──
    st.markdown("---")
    st.markdown("<div>🎯 Position Prediction Engine</div>", unsafe_allow_html=True)

    dates = available_trade_dates()
    latest_date = dates[0] if dates else None
    day_df = load_oi_for_date(latest_date) if latest_date else pd.DataFrame()
    all_tickers = sorted(day_df["ticker"].unique().tolist()) if not day_df.empty else []

    # User's open trades
    trades_df = q("SELECT * FROM trades WHERE status='OPEN'") if latest_date else pd.DataFrame()
    open_tickers = trades_df["ticker"].unique().tolist() if not trades_df.empty else []

    # Default to open trade tickers + SPY + QQQ
    default_tickers = list(set(open_tickers + ["SPY", "QQQ"]))
    default_tickers = [t for t in default_tickers if t in all_tickers][:6]

    sel_tickers = st.multiselect("Tickers to Analyze", all_tickers, default=default_tickers)

    if sel_tickers and latest_date:
        for tk in sel_tickers:
            tk_data = day_df[day_df["ticker"] == tk]
            if tk_data.empty:
                continue

            # Aggregate OI data
            c_oi = pd.to_numeric(tk_data["openInt_Call_now"], errors="coerce").sum()
            p_oi = pd.to_numeric(tk_data["openInt_Put_now"], errors="coerce").sum()
            c_chg = pd.to_numeric(tk_data["change_OI_Call"], errors="coerce").sum()
            p_chg = pd.to_numeric(tk_data["change_OI_Put"], errors="coerce").sum()
            c_vol = pd.to_numeric(tk_data["vol_Call_now"], errors="coerce").sum()
            p_vol = pd.to_numeric(tk_data["vol_Put_now"], errors="coerce").sum()
            pcr = p_oi / c_oi if c_oi > 0 else 0
            vol_pcr = p_vol / c_vol if c_vol > 0 else 0
            net_bias = c_chg - p_chg

            # Spot price — EOD from DB, AH from yfinance if toggle on
            sd = load_stock_daily(tk)
            spot_eod = None
            if not sd.empty:
                row_sd = sd[sd["trade_date"] == latest_date]
                if not row_sd.empty:
                    spot_eod = float(row_sd["close"].iloc[0])
            if spot_eod is None:
                try: spot_eod = _cached_price(tk)
                except Exception: spot_eod = 0
            _ah_d  = _get_ah_price(tk)
            spot_ah = _ah_d["spot_ah"] if _ah_d["spot_ah"] > 0 else spot_eod
            spot = spot_ah if st.session_state.get("use_ah") else spot_eod

            # Composite signal
            oi_signal = 1 if net_bias > 0 else -1
            pcr_signal = 1 if pcr < 0.7 else (-1 if pcr > 1.3 else 0)
            vol_signal = 1 if vol_pcr < 0.7 else (-1 if vol_pcr > 1.3 else 0)
            # Futures alignment
            futures_signal = 0
            if es_pct is not None:
                futures_signal = 1 if es_pct > 0.3 else (-1 if es_pct < -0.3 else 0)
            # VIX alignment
            vix_signal = 0
            if vix_val is not None:
                vix_signal = 1 if vix_val < 18 else (-1 if vix_val > 25 else 0)

            composite = oi_signal + pcr_signal + vol_signal + futures_signal + vix_signal
            if composite >= 3:
                pred, pred_color, pred_badge = "STRONG BULLISH", "#00c853", "badge-bull"
                action = "ADD LONG / BUY CALLS"
            elif composite >= 1:
                pred, pred_color, pred_badge = "BULLISH", "#00d4aa", "badge-bull"
                action = "HOLD LONGS / ADD ON DIP"
            elif composite <= -3:
                pred, pred_color, pred_badge = "STRONG BEARISH", "#ff1744", "badge-bear"
                action = "ADD PUTS / HEDGE"
            elif composite <= -1:
                pred, pred_color, pred_badge = "BEARISH", "#ef5350", "badge-bear"
                action = "REDUCE LONGS / BUY PROTECTION"
            else:
                pred, pred_color, pred_badge = "NEUTRAL", "#ffd600", "badge-neutral"
                action = "HOLD / SELL PREMIUM"

            # Display ticker card
            _ah_chg = _ah_d["ah_chg_pct"]; _ah_lbl = _ah_d["label"]
            _ext_tag = f"  🌙{_ah_lbl} ${spot_ah:.2f} ({_ah_chg:+.1f}%)" if _ah_d["is_extended"] else ""
            with st.expander(f"{'🟢' if composite > 0 else '🔴' if composite < 0 else '🟡'} **{tk}** — {pred} (Score: {composite:+d}) | EOD ${spot_eod:.2f}{_ext_tag}", expanded=(tk in open_tickers)):
                pc1, pc2, pc3, pc4, pc5, pc6 = st.columns(6)
                if _ah_d["is_extended"]:
                    pc1.metric(f"EOD → {_ah_lbl}", f"${spot_ah:.2f}", delta=f"{_ah_chg:+.1f}%",
                               help=f"EOD close: ${spot_eod:.2f}")
                else:
                    pc1.metric("Spot (EOD)", f"${spot_eod:.2f}" if spot_eod else "N/A")
                pc2.metric("PCR (OI)", f"{pcr:.2f}")
                pc3.metric("Net OI Bias", f"{net_bias:+,.0f}")
                pc4.metric("Call Vol", f"{c_vol:,.0f}")
                pc5.metric("Put Vol", f"{p_vol:,.0f}")
                pc6.metric("Vol PCR", f"{vol_pcr:.2f}")

                # Signal breakdown
                _sig_bg = "#e8f5e9" if composite > 0 else "#ffebee" if composite < 0 else "#fffde7"
                _sig_border = "#2e7d32" if composite > 0 else "#c62828" if composite < 0 else "#f9a825"
                _spot_src_lbl = (f"🌙 {_ah_lbl} ${spot_ah:.2f} ({_ah_chg:+.1f}%)"
                                 if _ah_d["is_extended"] and spot_ah != spot_eod
                                 else f"☀️ EOD ${spot_eod:.2f}")
                st.markdown(
                    f"<div style='background:{_sig_bg};border-left:4px solid {_sig_border};"
                    f"padding:8px 12px;border-radius:4px;margin:4px 0'>"
                    f"<b>Prediction:</b> {pred} &nbsp; "
                    f"<b>Action:</b> {action}<br>"
                    f"OI: {'▲' if oi_signal > 0 else '▼'} | "
                    f"PCR: {'Bull' if pcr_signal > 0 else 'Bear' if pcr_signal < 0 else 'Neut'} | "
                    f"Vol: {'Bull' if vol_signal > 0 else 'Bear' if vol_signal < 0 else 'Neut'} | "
                    f"Futures: {'▲' if futures_signal > 0 else '▼' if futures_signal < 0 else '—'} | "
                    f"VIX: {'Low' if vix_signal > 0 else 'High' if vix_signal < 0 else 'Norm'}<br>"
                    f"<small>Price: {_spot_src_lbl}</small>"
                    f"</div>",
                    unsafe_allow_html=True,
                )

                # If user has open trades in this ticker, show P&L context
                if tk in open_tickers:
                    tk_trades = trades_df[trades_df["ticker"] == tk]
                    st.markdown("**📋 Your Open Positions:**")
                    for _, tr in tk_trades.iterrows():
                        opt_type = tr.get("option_type", "CALL")
                        strike_t = tr.get("strike", 0)
                        entry_px = tr.get("entry_price", 0)
                        expiry_t = tr.get("expiry_date", "")
                        _hold = ((composite > 0 and opt_type.upper() == "CALL") or
                                 (composite < 0 and opt_type.upper() == "PUT"))
                        _rec = "✅ HOLD" if _hold else "⚠️ REVIEW / HEDGE"
                        # Live option price estimate at current spot
                        _live_opt_str = ""
                        if spot > 0 and entry_px > 0:
                            try:
                                _dte_tr = max((datetime.strptime(expiry_t, "%Y-%m-%d").date()
                                               - datetime.now().date()).days, 0) if expiry_t else 7
                                _opt_T_tr = max(_dte_tr, 0.5) / 365.0
                                _iv_tr = float(tr.get("iv", 0) or 0.35)
                                if _iv_tr <= 0: _iv_tr = 0.35
                                _lv_opt = bs_greeks(spot, float(strike_t), _opt_T_tr,
                                                    0.045, _iv_tr, opt_type.lower())["price"]
                                _lv_pnl = (_lv_opt - entry_px) * 100
                                _pnl_icon = "📈" if _lv_pnl >= 0 else "📉"
                                _live_opt_str = (f"  |  {_spot_src_lbl} est: **${_lv_opt:.2f}** "
                                                 f"{_pnl_icon} P&L: **${_lv_pnl:+.0f}**")
                            except Exception:
                                pass
                        st.markdown(
                            f"- {opt_type} ${strike_t} exp {expiry_t} @ ${entry_px:.2f} — "
                            f"**{_rec}**{_live_opt_str}"
                        )

                # ── What-If P&L Simulator per ticker ──
                st.markdown("---")
                st.markdown("**🔮 P&L Simulator — Drag sliders to explore**")
                _wlk = f"wi_live_{tk}"
                _wl1, _wl2, _wl3 = st.columns(3)
                _wl_otype = _wl1.selectbox("Type", ["call", "put"], key=f"{_wlk}_ot")
                _wl_strike = _wl2.number_input("Strike", value=float(round(spot)) if spot else 100.0, step=1.0, key=f"{_wlk}_k")
                _wl_expiry = _wl3.date_input("Expiry", value=datetime.now() + timedelta(days=30), key=f"{_wlk}_exp")
                _wl4, _wl5 = st.columns(2)
                _wl_entry = _wl4.number_input("Entry Premium ($)", value=0.0, step=0.1, key=f"{_wlk}_ep",
                                               help="Price paid per share. 0 if exploring.")
                _wl_qty = _wl5.number_input("Contracts", min_value=1, value=1, key=f"{_wlk}_qty")

                # Auto-fetch IV
                _wl_iv = 0.30
                _wl_iv_src = "Default"
                try:
                    _wl_chain = yf.Ticker(tk).option_chain(_wl_expiry.strftime("%Y-%m-%d"))
                    _wl_oc = _wl_chain.calls if _wl_otype == "call" else _wl_chain.puts
                    _wl_m = _wl_oc[_wl_oc["strike"] == float(_wl_strike)]
                    if not _wl_m.empty and "impliedVolatility" in _wl_m.columns:
                        _iv_v = float(_wl_m.iloc[0]["impliedVolatility"])
                        if _iv_v > 0.01:
                            _wl_iv = _iv_v
                            _wl_iv_src = f"Live ({_wl_iv:.1%})"
                    elif not _wl_oc.empty and "impliedVolatility" in _wl_oc.columns:
                        _wl_oc_c = _wl_oc.copy()
                        _wl_oc_c["_dist"] = abs(_wl_oc_c["strike"] - float(_wl_strike))
                        _nr = _wl_oc_c.sort_values("_dist").iloc[0]
                        _iv_v = float(_nr["impliedVolatility"])
                        if _iv_v > 0.01:
                            _wl_iv = _iv_v
                            _wl_iv_src = f"Nearest ({_wl_iv:.1%})"
                except Exception:
                    pass

                _wl_exp_dt = datetime.combine(_wl_expiry, datetime.min.time())
                _wl_dte = max((_wl_exp_dt - datetime.now()).days, 1)
                _wl_low = round(spot * 0.80, 2) if spot else 80.0
                _wl_high = round(spot * 1.20, 2) if spot else 120.0

                _wl_ah_d = _get_ah_price(tk)
                _wl_spot_lbl = (f"EOD ${spot_eod:.2f} → {_wl_ah_d['label']} ${_wl_ah_d['spot_ah']:.2f} ({_wl_ah_d['ah_chg_pct']:+.1f}%)"
                                if _wl_ah_d["is_extended"] else f"EOD ${spot_eod:.2f}")
                st.caption(f"IV: {_wl_iv:.1%} ({_wl_iv_src}) | {_wl_spot_lbl}" if spot else f"IV: {_wl_iv:.1%}")

                _wl_sl_px = st.slider("📈 Future Stock Price ($)",
                    min_value=_wl_low, max_value=_wl_high,
                    value=round(spot, 2) if spot else 100.0, step=0.5, key=f"{_wlk}_sl_px")
                _wl_sl_days = st.slider("📅 Days from now",
                    min_value=0, max_value=_wl_dte, value=min(7, _wl_dte), key=f"{_wlk}_sl_d")

                _wl_tgt_dt = datetime.now() + timedelta(days=_wl_sl_days)
                _wl_T = max((_wl_exp_dt - _wl_tgt_dt).days, 0) / 365.0
                _wl_g = bs_greeks(_wl_sl_px, _wl_strike, _wl_T, 0.045, _wl_iv, _wl_otype)
                _wl_theo = _wl_g["price"]

                # Current theo for reference
                _wl_T_now = max((_wl_exp_dt - datetime.now()).days, 0) / 365.0
                _wl_g_now = bs_greeks(spot if spot else _wl_sl_px, _wl_strike, _wl_T_now, 0.045, _wl_iv, _wl_otype)

                _result_parts = (
                    f"<div style='background:var(--panel-solid);color:var(--text);border-radius:10px;padding:14px 20px;"
                    f"border-left:4px solid #3d8bff;margin:8px 0;'>"
                    f"<b>Stock ${_wl_sl_px:.2f} on Day {_wl_sl_days}:</b> "
                    f"{_wl_otype.upper()} ${_wl_strike:.0f} = ${_wl_theo:.2f}"
                    f" &nbsp; (Current: ${_wl_g_now['price']:.2f})"
                )
                if _wl_entry > 0:
                    _wl_pnl = (_wl_theo - _wl_entry) * _wl_qty * 100
                    _wl_pnl_pct = (_wl_theo - _wl_entry) / _wl_entry * 100
                    _wpc = "#00c853" if _wl_pnl >= 0 else "#ff1744"
                    _result_parts += (
                        f"<br>P&L: ${_wl_pnl:+,.2f} ({_wl_pnl_pct:+.1f}%)"
                        f" {'✅ Profit' if _wl_pnl > 0 else '❌ Loss'}"
                    )
                _result_parts += (
                    f"<br>Δ: {_wl_g['delta']:.3f} | "
                    f"Θ: ${_wl_g['theta']*100:.2f}/day | DTE: {max((_wl_exp_dt - _wl_tgt_dt).days, 0)}</div>"
                )
                st.markdown(_result_parts, unsafe_allow_html=True)

                # Mini P&L chart
                _wl_s_range = np.linspace(_wl_low, _wl_high, 60)
                _wl_entry_use = _wl_entry if _wl_entry > 0 else _wl_g_now["price"]
                _fig_wl = go.Figure()
                for _dd, _dlbl, _dcol in [(0, "Today", "#0066cc"), (_wl_sl_days, f"Day {_wl_sl_days}", "#ff9100"), (_wl_dte, "Expiry", "#ff1744")]:
                    _dd_dt = datetime.now() + timedelta(days=_dd)
                    _dd_T = max((_wl_exp_dt - _dd_dt).days, 0) / 365.0
                    _pl = [((bs_greeks(s, _wl_strike, _dd_T, 0.045, _wl_iv, _wl_otype)["price"]) - _wl_entry_use) * _wl_qty * 100
                           for s in _wl_s_range]
                    _fig_wl.add_trace(go.Scatter(x=_wl_s_range, y=_pl, mode="lines", name=_dlbl,
                                                  line=dict(color=_dcol, width=2.5 if _dd == _wl_sl_days else 1.5,
                                                            dash="solid" if _dd == _wl_sl_days else "dot")))
                _fig_wl.add_hline(y=0, line_dash="dash", line_color="#888")
                if spot:
                    _fig_wl.add_vline(x=spot, line_dash="dot", line_color="#e65100", annotation_text=f"Now ${spot:.0f}")
                _wl_marker_pnl = (_wl_theo - _wl_entry_use) * _wl_qty * 100
                _wl_mc = "#00c853" if _wl_marker_pnl >= 0 else "#ff1744"
                _fig_wl.add_trace(go.Scatter(x=[_wl_sl_px], y=[_wl_marker_pnl], mode="markers",
                                              marker=dict(size=12, color=_wl_mc, symbol="diamond"),
                                              name=f"Target ${_wl_sl_px:.0f}", showlegend=True))
                _fig_wl.update_layout(template="plotly_white", height=280,
                                       xaxis_title="Stock Price", yaxis_title="P&L ($)",
                                       legend=dict(orientation="h", y=1.12), margin=dict(t=40, b=30))
                st.plotly_chart(_fig_wl)

                # Quick backtest
                bt_result = backtest_oi_signals(tk, lookback=10)
                if bt_result is not None:
                    res_df, acc = bt_result if isinstance(bt_result, tuple) else (bt_result, None)
                    if acc is not None:
                        st.markdown(f"**Backtest Accuracy (10d):** {acc:.0f}% — {'Reliable ✅' if acc > 55 else 'Weak ⚠️'}")

    # ── 5) Sector Heatmap from OI ──
    if latest_date and not day_df.empty:
        st.markdown("---")
        st.markdown("<div>🔥 Ticker OI Sentiment Heatmap</div>", unsafe_allow_html=True)
        anom = oi_anomalies(day_df)
        if not anom.empty:
            anom["net_bias"] = anom["call_oi_chg"] - anom["put_oi_chg"]
            anom["sentiment"] = np.where(anom["net_bias"] > 0, "Bullish", np.where(anom["net_bias"] < 0, "Bearish", "Neutral"))
            anom_disp = anom[["ticker", "call_oi_chg", "put_oi_chg", "net_bias", "pcr", "max_z", "sentiment"]].head(20)
            anom_disp.columns = ["Ticker", "Call OI Δ", "Put OI Δ", "Net Bias", "PCR", "Z-Score", "Sentiment"]
            st.dataframe(anom_disp.style.map(
                lambda v: "color: #00c853" if v == "Bullish" else ("color: #ff1744" if v == "Bearish" else ""),
                subset=["Sentiment"]
            ), hide_index=True)

    # ── 6) News Impact (RSS) ──
    st.markdown("---")
    st.markdown("<div>📰 Market News Impact</div>", unsafe_allow_html=True)
    try:
        import feedparser
        feed = feedparser.parse("https://feeds.finance.yahoo.com/rss/2.0/headline?s=^GSPC&region=US&lang=en-US")
        if feed.entries:
            for entry in feed.entries[:8]:
                title = entry.get("title", "")
                link = entry.get("link", "")
                published = entry.get("published", "")
                # Simple sentiment from title
                bull_words = ["rally", "surge", "gain", "rise", "jump", "bull", "record", "high", "beat", "strong"]
                bear_words = ["fall", "drop", "crash", "plunge", "bear", "low", "miss", "weak", "sell", "fear", "tariff", "recession",
                              "dive", "tumble", "slump", "plummet", "war", "bubble", "soar"]
                title_lower = title.lower()
                if any(w in title_lower for w in bull_words):
                    emoji, color = "🟢", "#00c853"
                elif any(w in title_lower for w in bear_words):
                    emoji, color = "🔴", "#ff1744"
                else:
                    emoji, color = "⚪", "#8899aa"

                # Auto trade idea from headline
                _tl = title_lower
                _idea = ""
                if "oil" in _tl and ("surge" in _tl or "soar" in _tl or "100" in _tl or "115" in _tl):
                    _idea = "💡 LONG XLE/OXY, SHORT airlines. Buy inflation hedges."
                elif "crash" in _tl or "plunge" in _tl or "dive" in _tl or "tumble" in _tl:
                    _idea = "💡 BUY puts on indices. LONG VIX calls. Raise cash."
                elif "iran" in _tl or "war" in _tl or "geopolitical" in _tl:
                    _idea = "💡 LONG defense (LMT, RTX), gold, oil. SHORT travel."
                elif "bubble" in _tl or "ai bubble" in _tl:
                    _idea = "💡 Reduce AI/tech exposure. Rotate to value/defensives."
                elif "nikkei" in _tl or "japan" in _tl:
                    _idea = "💡 SHORT EWJ. Global risk-off may spread to US."

                if link:
                    st.markdown(
                        f"{emoji} <a href='{link}' target='_blank'>"
                        f"{title}</a> ({published})"
                        + (f"<br>{_idea}" if _idea else "")
                        ,
                        unsafe_allow_html=True,
                    )
                else:
                    st.markdown(
                        f"{emoji} **{title}** ({published})",
                        unsafe_allow_html=True,
                    )
        else:
            st.info("No recent news available.")
    except Exception:
        st.info("News feed unavailable. Install feedparser: `pip install feedparser`")


# ===================================================================
# ──  PAGE 7: INSIDER / CONGRESS / WHALES
# ===================================================================
elif page == "📈 Insider / Congress / Whales":
    _hdr1, _hdr2 = st.columns([4, 1])
    with _hdr1:
        st.markdown("## 📈 Smart Money Tracker")
    with _hdr2:
        if st.button("🔄 Refresh", key="refresh_insider"):
            st.rerun()
        with st.popover("ℹ️"):
            st.markdown(_PAGE_HELP.get(page, ""))

    tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs(["👤 Insider Trades", "🏛️ Congress Trades", "🐋 Whale Holdings", "🏆 Legendary Investors (13F)", "🩳 Short Sellers", "📜 13F History (EDGAR)"])

    with tab6:
        st.markdown("### 📜 13F History — live from SEC EDGAR")
        st.caption("Real quarterly 13F filings pulled from SEC EDGAR (free). Builds portfolio %, "
                   "quarter-over-quarter change, and a 12-quarter holding trend. First fetch per fund "
                   "takes ~10–20s; then it's stored in your DB.")
        _EDGAR_FUNDS = {
            "Berkshire Hathaway": "0001067983", "Vanguard Group": "0001029160",
            "BlackRock": "0001364742", "Citadel Advisors": "0001423053",
            "ARK Investment Management": "0001649339", "Soros Fund Management": "0001549626",
            "Bridgewater Associates": "0001350694", "Renaissance Technologies": "0001037389",
            "Pershing Square (Ackman)": "0001336528", "Scion (Burry)": "0001649339",
            "Third Point (Loeb)": "0001040273", "Tiger Global (Coleman)": "0001167483",
            "Greenlight (Einhorn)": "0001079114", "Baupost (Klarman)": "0001061768",
        }
        _ef1, _ef2 = st.columns([2, 1])
        _fpick = _ef1.selectbox("Fund", list(_EDGAR_FUNDS) + ["(enter CIK manually)"], key="edgar_fund")
        if _fpick == "(enter CIK manually)":
            _cik = _ef1.text_input("CIK (10 digits, e.g. 0001067983)", key="edgar_cik_manual").strip()
            _fund = _cik
        else:
            _cik = _EDGAR_FUNDS[_fpick]; _fund = _fpick
        _nq = _ef2.slider("Quarters", 4, 12, 8, key="edgar_nq")
        if _ef2.button("⬇️ Fetch / refresh from EDGAR", key="edgar_fetch") and _cik:
            with st.spinner("Pulling 13F filings from SEC EDGAR…"):
                try:
                    _stq = _edgar_build_history(_cik, _fund, _nq)
                    st.success(f"Stored {_stq} quarters for {_fund}.")
                except Exception as _ee:
                    st.error(f"EDGAR fetch failed: {_ee}")
        _hist = _edgar_load(_cik) if _cik else pd.DataFrame()
        if _hist is None or _hist.empty:
            st.info("No stored 13F history yet for this fund — click **Fetch / refresh** above.")
        else:
            _qs = sorted(_hist["quarter"].unique())
            # 1) AUM trend across quarters
            _aum = _hist.groupby("quarter")["value"].sum().reset_index().sort_values("quarter")
            _npos = _hist.groupby("quarter")["cusip"].nunique().reindex(
                _aum["quarter"]).values if not _aum.empty else []
            _af = go.Figure()
            _af.add_trace(go.Scatter(x=_aum["quarter"], y=_aum["value"] / 1e9, mode="lines+markers",
                                     name="AUM ($B)", line=dict(color="#3d8bff", width=3)))
            _af.update_layout(template="plotly_dark", height=300,
                              title=f"{_fund} — 13F portfolio value ({len(_qs)} quarters)",
                              xaxis_title="Quarter end", yaxis_title="AUM ($B)", margin=dict(t=44, b=10))
            st.plotly_chart(_af, use_container_width=True)
            # 2) latest-quarter holdings with % portfolio + QoQ
            _latest = _qs[-1]; _prev = _qs[-2] if len(_qs) > 1 else None
            _cur = _hist[_hist["quarter"] == _latest].copy()
            _curtot = _cur["value"].sum() or 1
            _cur["% Port"] = (_cur["value"] / _curtot * 100).round(1)
            if _prev:
                _pmap = _hist[_hist["quarter"] == _prev].groupby("cusip")["shares"].sum()
                _cur["QoQ%"] = _cur.apply(
                    lambda r: round((r["shares"] - _pmap.get(r["cusip"], 0)) /
                                    _pmap.get(r["cusip"], 1) * 100) if _pmap.get(r["cusip"], 0) else None, axis=1)
            _cur = _cur.sort_values("value", ascending=False)
            st.markdown(f"**Holdings as of {_latest}** ({len(_cur)} positions, "
                        f"${_curtot/1e9:.1f}B)" + (f" · vs {_prev}" if _prev else ""))
            _show = _cur[["issuer", "value", "% Port"] + (["QoQ%"] if _prev else []) + ["shares", "put_call"]].head(40).copy()
            _show["value"] = _show["value"].apply(lambda v: f"${v/1e9:.2f}B" if v >= 1e9 else f"${v/1e6:.0f}M")
            _show["shares"] = _show["shares"].apply(lambda v: f"{v/1e6:.1f}M" if v >= 1e6 else f"{v:,.0f}")
            st.dataframe(_show, hide_index=True, use_container_width=True)
            # 3) per-holding 12Q trend
            _sel_h = st.selectbox("📈 12-quarter trend for holding",
                                  list(_cur["issuer"].head(40)), key="edgar_hold")
            _ht = _hist[_hist["issuer"] == _sel_h].groupby("quarter")["value"].sum().reset_index().sort_values("quarter")
            if not _ht.empty:
                _hf = go.Figure(go.Bar(x=_ht["quarter"], y=_ht["value"] / 1e9, marker_color="#00e676"))
                _hf.update_layout(template="plotly_dark", height=280,
                                  title=f"{_sel_h} — position value by quarter ($B)",
                                  xaxis_title="Quarter", yaxis_title="$B", margin=dict(t=44, b=10))
                st.plotly_chart(_hf, use_container_width=True)
            st.caption("Note: 13F lists CUSIPs/issuer names (not tickers); % of company isn't shown here "
                       "without a CUSIP→ticker map. Values are as-reported (whole $ for 2023+ filings).")

    with tab1:
        insiders = q("SELECT * FROM insider_trades ORDER BY transaction_date DESC")
        if insiders.empty:
            st.info("No insider trades data.")
        else:
            c1, c2, c3 = st.columns(3)
            buys = insiders[insiders.get("transaction_type", pd.Series()) == "PURCHASE"]
            sells = insiders[insiders.get("transaction_type", pd.Series()) == "SALE"]
            buy_val = pd.to_numeric(buys.get("transaction_value_usd", 0), errors="coerce").sum()
            sell_val = pd.to_numeric(sells.get("transaction_value_usd", 0), errors="coerce").sum()
            c1.metric("Purchases", f"${buy_val/1e6:.1f}M", delta="Bullish")
            c2.metric("Sales", f"${sell_val/1e6:.1f}M", delta="Bearish", delta_color="inverse")
            c3.metric("Net", f"${(buy_val-sell_val)/1e6:.1f}M")

            display_cols = [c for c in ["ticker", "insider_name", "position_title",
                           "transaction_type", "transaction_value_usd", "transaction_date",
                           "signal_strength"] if c in insiders.columns]
            st.dataframe(insiders[display_cols], hide_index=True)

            # By ticker
            if "ticker" in insiders.columns and "transaction_value_usd" in insiders.columns:
                insiders["val_num"] = pd.to_numeric(insiders["transaction_value_usd"], errors="coerce")
                by_tk = insiders.groupby("ticker")["val_num"].sum().sort_values(ascending=False).head(10)
                fig = px.bar(x=by_tk.index, y=by_tk.values, labels={"x": "Ticker", "y": "Value ($)"},
                             color=by_tk.values, color_continuous_scale="Blues")
                fig.update_layout(template="plotly_white", title="Insider Activity by Ticker", height=350)
                st.plotly_chart(fig)

    with tab2:
        congress = q("SELECT * FROM congress_trades ORDER BY transaction_date DESC")
        if congress.empty:
            st.info("No congress trades data.")
        else:
            c1, c2, c3 = st.columns(3)
            c_buys = congress[congress.get("action", pd.Series()).str.upper() == "PURCHASE"]
            c_sells = congress[congress.get("action", pd.Series()).str.upper() == "SALE"]
            c_buy_val = pd.to_numeric(c_buys.get("value_usd", 0), errors="coerce").sum()
            c_sell_val = pd.to_numeric(c_sells.get("value_usd", 0), errors="coerce").sum()
            c1.metric("Purchases", f"${c_buy_val/1e3:.1f}K")
            c2.metric("Sales", f"${c_sell_val/1e3:.1f}K", delta_color="inverse")
            c3.metric("Politicians", congress.get("politician_name", pd.Series()).nunique())

            display_cols = [c for c in ["politician_name", "ticker", "action",
                           "value_usd", "transaction_date", "trading_signal_strength"]
                           if c in congress.columns]
            st.dataframe(congress[display_cols], hide_index=True)

    with tab3:
        # Deduplicate by filer+ticker keeping max value_usd
        whales_raw = q("SELECT * FROM institutional_holdings")
        if whales_raw.empty:
            st.info("No institutional holdings data.")
        else:
            whales_raw["val_num"] = pd.to_numeric(whales_raw.get("value_usd", 0), errors="coerce").fillna(0)
            # Deduplicate: keep one row per filer+ticker (latest filing)
            if "filing_date" in whales_raw.columns:
                whales = (whales_raw.sort_values("filing_date", ascending=False)
                          .drop_duplicates(subset=["filer_name","ticker"])
                          .sort_values("val_num", ascending=False))
            else:
                whales = whales_raw.drop_duplicates(subset=["filer_name","ticker"]).sort_values("val_num", ascending=False)

            total_val = whales["val_num"].sum()
            n_inst    = whales["filer_name"].nunique() if "filer_name" in whales.columns else 0
            n_tickers = whales["ticker"].nunique()     if "ticker"     in whales.columns else 0

            # Net change signal — use value_change_usd if available
            net_change = pd.to_numeric(whales.get("value_change_usd", pd.Series(dtype=float)), errors="coerce").sum() \
                         if "value_change_usd" in whales.columns else None
            buys  = whales[whales.get("action_type", pd.Series(dtype=str)) == "BUY"]  if "action_type" in whales.columns else pd.DataFrame()
            holds = whales[whales.get("action_type", pd.Series(dtype=str)) == "HOLD"] if "action_type" in whales.columns else pd.DataFrame()
            sells = whales[whales.get("action_type", pd.Series(dtype=str)) == "SELL"] if "action_type" in whales.columns else pd.DataFrame()

            mc1, mc2, mc3, mc4 = st.columns(4)
            mc1.metric("Institutions",  n_inst)
            mc2.metric("Tickers Held",  n_tickers)
            mc3.metric("Total AUM",     f"${total_val/1e9:.1f}B" if total_val >= 1e9 else f"${total_val/1e6:.1f}M")
            if net_change is not None and net_change != 0:
                mc4.metric("Net Flow", f"${net_change/1e9:+.1f}B" if abs(net_change) >= 1e9 else f"${net_change/1e6:+.1f}M",
                           delta="Inflow" if net_change > 0 else "Outflow",
                           delta_color="normal" if net_change > 0 else "inverse")
            else:
                mc4.metric("Buy/Hold/Sell", f"{len(buys)}/{len(holds)}/{len(sells)}")

            # Signal banner based on net_change or action mix
            if net_change is not None and net_change > 0:
                st.success(f"Institutional signal: **NET BUYING** (${net_change/1e9:+.1f}B net inflow) — Smart money accumulating")
            elif net_change is not None and net_change < 0:
                st.warning(f"Institutional signal: **NET SELLING** (${net_change/1e9:+.1f}B net outflow) — Smart money distributing")
            elif len(buys) > len(sells):
                st.success("Institutional signal: **NET BUYING** — More buy actions than sells")
            elif len(sells) > len(buys):
                st.warning("Institutional signal: **NET SELLING** — More sell actions than buys")
            else:
                st.info("Institutional signal: **HOLD / MAINTAIN** — Institutions holding current positions")

            # ── Enrich: % of fund portfolio, % of company, QoQ change ──
            _filer_tot = whales.groupby("filer_name")["val_num"].transform("sum")
            whales["pct_port"] = (whales["val_num"] / _filer_tot * 100).round(1)
            if "shares_held" in whales.columns:
                _sh = pd.to_numeric(whales["shares_held"], errors="coerce")
                _so = whales["ticker"].apply(lambda t: _shares_outstanding(str(t)))
                whales["pct_company"] = (_sh / _so * 100).round(2)
                if "shares_change" in whales.columns:
                    _chg = pd.to_numeric(whales["shares_change"], errors="coerce")
                    _prev = _sh - _chg
                    whales["qoq_shares_pct"] = (_chg / _prev.replace(0, pd.NA) * 100).round(0)
            st.caption("**% Port** = position weight in that fund · **% Co** = shares held ÷ shares "
                       "outstanding · **QoQ%** = share change vs prior quarter.")

            # Table — use actual columns present in DB + enriched metrics
            _want = ["filer_name", "ticker", "shares_held", "value_usd", "pct_port", "pct_company",
                     "shares_change", "qoq_shares_pct", "value_change_usd",
                     "filing_date", "quarter_end_date",
                     "action_type", "action_confidence"]
            display_cols = [c for c in _want if c in whales.columns]
            # Format value columns for readability
            _disp = whales[display_cols].copy().head(50)
            _disp = _disp.rename(columns={"pct_port": "% Port", "pct_company": "% Co",
                                          "qoq_shares_pct": "QoQ%"})
            for _vc in ["value_usd", "value_change_usd"]:
                if _vc in _disp.columns:
                    _disp[_vc] = pd.to_numeric(_disp[_vc], errors="coerce").apply(
                        lambda v: f"${v/1e9:.2f}B" if pd.notna(v) and abs(v) >= 1e9
                        else (f"${v/1e6:.1f}M" if pd.notna(v) and abs(v) >= 1e6 else (f"${v:,.0f}" if pd.notna(v) else "—")))
            if "shares_held" in _disp.columns:
                _disp["shares_held"] = pd.to_numeric(_disp["shares_held"], errors="coerce").apply(
                    lambda v: f"{v/1e6:.1f}M" if pd.notna(v) and v >= 1e6 else (f"{v:,.0f}" if pd.notna(v) else "—"))
            st.dataframe(_disp, hide_index=True, use_container_width=True)

            # Chart 1 — top holdings by ticker
            by_tk = whales.groupby("ticker")["val_num"].sum().sort_values(ascending=False).head(12)
            if not by_tk.empty:
                fig1 = px.bar(x=by_tk.index, y=by_tk.values / 1e9,
                              labels={"x": "Ticker", "y": "AUM ($B)"},
                              color=by_tk.values, color_continuous_scale="Greens",
                              title="Top Holdings by Ticker ($B)")
                fig1.update_layout(template="plotly_white", height=300, showlegend=False)
                st.plotly_chart(fig1, use_container_width=True)

            # Chart 2 — top institutions by AUM
            if "filer_name" in whales.columns:
                by_inst = whales.groupby("filer_name")["val_num"].sum().sort_values(ascending=False).head(10)
                if not by_inst.empty:
                    fig2 = px.bar(x=by_inst.values / 1e9, y=by_inst.index,
                                  orientation="h",
                                  labels={"x": "AUM ($B)", "y": "Institution"},
                                  color=by_inst.values, color_continuous_scale="Blues",
                                  title="Top Institutions by AUM ($B)")
                    fig2.update_layout(template="plotly_white", height=350, showlegend=False)
                    st.plotly_chart(fig2, use_container_width=True)

    # ── Tab 4: Legendary Investors 13F ─────────────────────────────
    with tab4:
        _LEGENDS = {
            "buffett":       {"name":"Warren Buffett",     "firm":"Berkshire Hathaway",   "aum":"$300B+",   "style":"Value / Long-term"},
            "soros":         {"name":"George Soros",       "firm":"Soros Fund Mgmt",      "aum":"$9.1B",    "style":"Macro / Reflexivity"},
            "rentec":        {"name":"Jim Simons",         "firm":"Renaissance Tech",      "aum":"$63.9B",   "style":"Quant / Stat Arb"},
            "dalio":         {"name":"Ray Dalio",          "firm":"Bridgewater",           "aum":"$22.4B",   "style":"All-Weather"},
            "tepper":        {"name":"David Tepper",       "firm":"Appaloosa",             "aum":"$5.9B",    "style":"Event-Driven"},
            "ackman":        {"name":"Bill Ackman",        "firm":"Pershing Square",       "aum":"$13.7B",   "style":"Activist"},
            "druckenmiller": {"name":"Stan Druckenmiller", "firm":"Duquesne",              "aum":"~$3-4B",   "style":"Macro"},
            "loeb":          {"name":"Dan Loeb",           "firm":"Third Point",           "aum":"$2.1B",    "style":"Event-Driven"},
            "cohen":         {"name":"Steve Cohen",        "firm":"Point72",               "aum":"$78B",     "style":"Multi-Strategy"},
            "burry":         {"name":"Michael Burry",      "firm":"Scion",                 "aum":"$68M",     "style":"Contrarian Value"},
            "coleman":       {"name":"Chase Coleman",      "firm":"Tiger Global",          "aum":"$22.8B",   "style":"Growth/Tech"},
            "tudor":         {"name":"Paul Tudor Jones",   "firm":"Tudor",                 "aum":"$53.9B",   "style":"Macro/CTA"},
            "aschenbrenner": {"name":"L. Aschenbrenner",  "firm":"Situational Awareness", "aum":"$13.7B",   "style":"AI Thesis"},
            "gerstner":      {"name":"Brad Gerstner",      "firm":"Altimeter Capital",     "aum":"$6.7B",    "style":"Growth/AI"},
            "laffont":       {"name":"Philippe Laffont",   "firm":"Coatue",                "aum":"$29B",     "style":"Growth/Tech"},
            "wood":          {"name":"Cathie Wood",        "firm":"ARK Invest",            "aum":"$12.86B",  "style":"Disruptive"},
            "chamath":       {"name":"Chamath Palihapitiya","firm":"Social Capital",       "aum":"$2.1B",    "style":"Contrarian VC"},
            "einhorn":       {"name":"David Einhorn",      "firm":"Greenlight Capital",    "aum":"$3.2B",    "style":"Value/Short"},
            "klarman":       {"name":"Seth Klarman",       "firm":"Baupost Group",         "aum":"$5.1B",    "style":"Deep Value"},
            "griffin":       {"name":"Ken Griffin",        "firm":"Citadel Advisors",      "aum":"$618B",    "style":"Quant Multi-Strat"},
        }
        _LEGENDS_BUYS = {
            "buffett":       [("GOOGL","Added","$16.6B","Large","+204% tripled"),("DAL","New","$2.6B","Mid","Delta 39.8M shares"),("NYT","Added","--","Mid","+199% media"),("LEN","Added","--","Mid","+43% homebuilder"),("M","New","$55M","Small","Macys $55M deep value")],
            "soros":         [("NVDA","Added","--","Large","AI chipmaker"),("TSM","Added","--","Large","Taiwan Semi"),("EA","Added","--","Mid","Electronic Arts"),("BRK.B","New","--","Large","Berkshire position")],
            "rentec":        [("AAPL","New","$781M","Large","New position"),("NVDA","Added","$278M","Large","AI chip"),("MU","Added","$520M","Large","+50% Micron"),("NEM","New","$278M","Mid","Newmont gold"),("AVGO","New","$245M","Large","Broadcom AI"),("GOLD","New","$227M","Small","Alamos Gold"),("UTHR","Hold","--","Mid","#1 rare disease")],
            "dalio":         [("TSM","New","--","Large","Taiwan Semi"),("GOOGL","New","--","Large","Alphabet"),("NUE","New","--","Mid","Nucor steel"),("NVDA","Added","--","Large","GPU"),("MU","Added","--","Large","Micron")],
            "tepper":        [("AMZN","Added","$470M","Large","Nearly doubled"),("MU","Added","--","Large","+6x Micron"),("UBER","Added","--","Large","+3x tripled"),("VST","Added","--","Mid","Vistra 2x"),("SAND","New","--","Small","SanDisk flash")],
            "ackman":        [("MSFT","New","$2B+","Large","~15% of portfolio"),("AMZN","Added","--","Large","Cloud/AI")],
            "druckenmiller": [("CRWV","Added","--","Mid","CoreWeave AI"),("TSM","Added","--","Large","Taiwan Semi")],
            "loeb":          [("AMZN","Hold","--","Large","#1 19.4%"),("META","New","--","Large","New Meta AI"),("TDS","Hold","--","Small","#2 13.3% rural telecom"),("CRS","Hold","--","Small","#5 5.9% specialty alloys"),("HUT","New","--","Small","Hut 8 crypto miner")],
            "cohen":         [("NVDA","Hold","--","Large","#1 holding"),("CRDO","Hold","--","Small","#5 AI interconnect"),("TDG","New","$336M","Mid","TransDigm aerospace"),("EQIX","Added","--","Large","Equinix data center")],
            "burry":         [("MOH","New","--","Mid","35.1% Molina Healthcare"),("LULU","Added","--","Mid","Doubled Lululemon"),("SLM","New","--","Small","19.5% Sallie Mae"),("BRK","New","--","Small","19.3% Bruker Corp")],
            "coleman":       [("GOOGL","Hold","--","Large","#1 13.4%"),("NVDA","Hold","--","Large","#2 9.2%"),("AMZN","Hold","--","Large","#3 9.1%"),("INTC","New","$180M","Large","Intel turnaround")],
            "tudor":         [("GLD","Hold","--","ETF","Gold ETF"),("BTC","Added","--","Crypto","Bitcoin")],
            "aschenbrenner": [("BE","Hold","$879M","Mid","#1 Bloom Energy power"),("CLSK","Added","--","Small","CleanSpark AI miner"),("RIOT","Added","--","Small","Riot BTC/AI"),("APLD","Added","--","Small","Applied Digital HPC"),("SNDK","New","--","Small","SanDisk + calls")],
            "gerstner":      [("NVDA","Hold","$1.51B","Large","#1 AI chip"),("META","Hold","$1.22B","Large","#2 AI ads"),("CRWV","Added","$348M","Mid","CoreWeave AI"),("ARM","New","$259M","Large","ARM chip arch")],
            "laffont":       [("ASML","New","$655M","Large","EUV monopoly"),("NFLX","Added","--","Large","Doubled +104%"),("NU","Hold","--","Mid","Nu Holdings LatAm")],
            "wood":          [("AMD","Added","--","Large","AI GPU"),("CRSP","Added","--","Mid","CRISPR gene editing"),("TEM","Added","--","Mid","Tempus AI clinical"),("CRCL","Added","--","Small","Circle stablecoin"),("HOOD","Added","--","Mid","Robinhood +24.9%"),("RXRX","Hold","--","Small","Recursion +682% rev")],
            "chamath":       [("COPX","Thesis","--","ETF","Copper ETF -- AI 5x copper"),("GROQ","Exit","~$1B","Private","NVIDIA $20B -- 3000% return")],
            "einhorn":       [("GRBK","Hold","$611M","Small","#1 19.1% Green Brick homebuilder"),("FLR","Hold","$222M","Mid","#2 6.9% Fluor engineering"),("CNR","Hold","$195M","Mid","#3 6.1% Core Natural coal"),("BHF","Added","--","Small","#4 5.3% Brighthouse insurer")],
            "klarman":       [("AMZN","Hold","$649M","Large","#1 12.7%"),("QSR","Added","$529M","Mid","#2 Doubled +4.2M shares"),("ELV","Added","$426M","Large","Doubled Elevance Health"),("WCC","Hold","$393M","Mid","#3 7.7% WESCO elec dist"),("UNP","Hold","$374M","Large","#4 7.3% Union Pacific")],
            "griffin":       [("NVDA","CALL","--","Large","NVDA calls AI upside"),("TSLA","CALL","--","Large","TSLA calls EV/auto"),("SPY","CALL","--","ETF","SPY calls market long"),("AAPL","Hold","--","Large","Apple top equity")],
        }
        _LEGENDS_SELLS = {
            "buffett":       [("V","Full exit"),("MA","Full exit"),("UNH","Full exit"),("AMZN","Full exit"),("BAC","Trimmed"),("CVX","Trimmed")],
            "soros":         [("AMZN","Reduced"),("GOOGL","Trimmed"),("MSFT","Reduced"),("CRM","Trimmed")],
            "rentec":        [("NFLX","-$673M"),("COST","-$578M"),("PLTR","-$542M"),("TSLA","-$534M"),("MSFT","-$329M"),("PG","Full exit")],
            "dalio":         [("CRM","Full exit"),("WDAY","Full exit"),("NOW","Full exit"),("BKNG","Reduced"),("ADBE","Reduced")],
            "tepper":        [("AAL","Full exit"),("OC","Full exit"),("MHK","Full exit"),("FXI","Reduced")],
            "ackman":        [("HLT","Full exit"),("GOOGL","Exited"),("BN","Trimmed")],
            "druckenmiller": [("GOOGL","Sold out")],
            "loeb":          [("PGE","Full exit"),("MSFT","Full exit"),("BN","Full exit")],
            "cohen":         [], "burry": [], "coleman": [], "tudor": [],
            "aschenbrenner": [], "chamath": [],
            "gerstner":      [("GOOGL","Full exit -- sold all"),("BABA","Full exit China"),("JD","Full exit China")],
            "laffont":       [("AMZN","-82% cut"),("TSM","-83% slashed"),("NVDA","-70% reduced"),("LRCX","-71% slashed")],
            "wood":          [("TSLA","Trimmed"),("PLTR","Trimmed"),("COIN","Trimmed"),("ROKU","-35% cut")],
            "einhorn":       [("META","Reduced"),("AMZN","Trimmed")],
            "klarman":       [], "griffin": [],
        }
        _LEGENDS_OPTIONS = {
            "soros":         [("CRWV","PUT","Q2 2026","CoreWeave hedge"),("TSM","PUT","Q2 2026","Geo-risk hedge")],
            "tudor":         [("IWM","PUT","Q2 2026","Small cap hedge"),("IWM","CALL","Q2 2026","Small cap long"),("QQQ","PUT","Q2 2026","Tech hedge"),("QQQ","CALL","Q2 2026","Tech long"),("SPY","CALL","Q2 2026","Market long")],
            "aschenbrenner": [("SMH","PUT","Q2 2026","$2B VanEck Semi ETF"),("NVDA","PUT","Q2 2026","$1.6B Nvidia"),("AVGO","PUT","Q2 2026","Broadcom"),("AMD","PUT","Q2 2026","AMD"),("MU","CALL","Q2 2026","$422M Micron long"),("TSM","CALL","Q2 2026","$355M TSM long")],
            "griffin":       [("SPY","PUT","Q2 2026","#1 SPY puts macro hedge"),("QQQ","PUT","Q2 2026","#2 QQQ tech hedge"),("SPY","CALL","Q2 2026","#3 SPY calls long"),("TSLA","CALL","Q2 2026","#4 TSLA calls"),("NVDA","CALL","Q2 2026","#5 NVDA calls")],
        }
        _LEGENDS_TOPS = {
            "buffett":       [("AAPL","21.9%"),("AXP","17.4%"),("KO","11.6%"),("BAC","9.5%"),("CVX","6.6%")],
            "soros":         [("AMZN","#1"),("GPN","#2"),("EA","#3"),("BILL","#4"),("NVDA","#5")],
            "rentec":        [("UTHR","#1"),("PLTR","#2 trimmed"),("AAPL","#3 new"),("MU","$520M"),("NVDA","$278M")],
            "dalio":         [("EEM","EM ETF"),("SPY","S&P ETF"),("GLD","Gold ETF"),("TSM","New"),("NVDA","Added")],
            "tepper":        [("AMZN","15.2%"),("MU","9.5%"),("GOOG","8.4%"),("UBER","7.7%"),("TSM","7.6%")],
            "ackman":        [("BN","17.6%"),("AMZN","17.4%"),("UBER","15.7%"),("MSFT","15.3%"),("QSR","12.2%")],
            "druckenmiller": [("CRWV","AI infra"),("TSM","Chips")],
            "loeb":          [("AMZN","19.4%"),("TDS","13.3%"),("CRH","9.6%"),("TPX","8.1%"),("CRS","5.9%")],
            "cohen":         [("NVDA","#1"),("AMZN","#2"),("ANET","#3"),("ASML","#4"),("CRDO","#5")],
            "burry":         [("MOH","35.1%"),("LULU","26.1%"),("SLM","19.5%"),("BRK","19.3%")],
            "coleman":       [("GOOGL","13.4%"),("NVDA","9.2%"),("AMZN","9.1%"),("TSM","8.2%"),("META","7.7%")],
            "tudor":         [("IWM puts","Hedge"),("QQQ puts","Hedge"),("SPY calls","Long")],
            "aschenbrenner": [("BE","$879M #1"),("CLSK","AI miner"),("RIOT","BTC/AI"),("SMH PUTS","$2B")],
            "gerstner":      [("NVDA","$1.51B"),("META","$1.22B"),("MSFT","$618M"),("AMZN","$511M"),("UBER","$457M")],
            "laffont":       [("ASML","New $655M"),("NFLX","2x doubled"),("NU","LatAm bank")],
            "wood":          [("TSLA","#1 trimmed"),("AMD","#2"),("CRSP","gene edit"),("HOOD","retail")],
            "chamath":       [("Copper/COPX","AI material"),("Groq","Sold $20B")],
            "einhorn":       [("GRBK","19.1%"),("FLR","6.9%"),("CNR","6.1%"),("BHF","5.3%"),("PCG","3.6%")],
            "klarman":       [("AMZN","12.7%"),("QSR","11.7%"),("WCC","7.7%"),("UNP","7.3%"),("ELV","doubled")],
            "griffin":       [("SPY PUTS","#1 macro"),("QQQ PUTS","#2 tech"),("SPY CALLS","#3 long"),("TSLA CALLS","#4"),("NVDA CALLS","#5")],
        }
        _LEGENDS_GEMS = {
            "buffett":       [("NYT","Mid-$3B","+199% -- digital subs + AI licensing"),("LEN","Mid-$15B","homebuilder -- rate cut + housing deficit"),("DAL","Mid-$13B","Delta -- travel + fleet renewal"),("M","Small-$3B","Macys $55M -- deep value asset play")],
            "soros":         [("BILL","Mid-$6B","#4 holding -- SMB fintech payments"),("GPN","Mid-$11B","#2 holding -- global payment rails")],
            "rentec":        [("UTHR","Mid-$16B","#1 -- United Therapeutics rare disease"),("GOLD","Small-$3B","$227M Alamos Gold junior miner"),("NEM","Mid-$40B","$278M Newmont gold -- uncertainty hedge")],
            "dalio":         [("NUE","Mid-$16B","Nucor steel -- reshoring + AI data center build")],
            "tepper":        [("VST","Mid-$25B","Vistra -- AI data center power, doubled"),("SAND","Small-$5B","SanDisk NAND flash spinoff AI storage")],
            "ackman":        [("QSR","Mid-$18B","12.2% -- Restaurant Brands intl compounder"),("BN","Mid-$90B","Brookfield Corp complex asset mgr")],
            "druckenmiller": [("CRWV","Mid-$60B","CoreWeave -- NVIDIA-powered AI compute")],
            "loeb":          [("TDS","Small-$2.5B","#2 13.3% -- Telephone Data rural telecom"),("CRS","Small-$6B","#5 5.9% -- Carpenter Tech specialty alloys"),("HUT","Small-$1.5B","Hut 8 -- Bitcoin infrastructure new")],
            "cohen":         [("CRDO","Small-$6B","#5 -- Credo Technology AI interconnect chips"),("TDG","Mid-$75B","$336M TransDigm -- aerospace pricing power")],
            "burry":         [("MOH","Mid-$8B","35.1% Molina -- Medicaid HMO deep value"),("SLM","Small-$3B","19.5% Sallie Mae -- student loans discount"),("BRK","Small-$4B","19.3% Bruker -- scientific instruments")],
            "coleman":       [("INTC","Large-$80B","$180M Intel -- turnaround + foundry separation")],
            "tudor":         [("IWM","ETF","Holds BOTH puts+calls = straddle, expects volatility")],
            "aschenbrenner": [("BE","Mid-$4B","#1 $879M Bloom Energy biofuel for AI data centers"),("CLSK","Small-$2B","CleanSpark BTC->AI compute pivot"),("APLD","Small-$1B","Applied Digital AI/HPC"),("IREN","Small-$1B","IREN AI hosting")],
            "gerstner":      [("CRWV","Mid-$60B","$348M CoreWeave -- AI compute cloud"),("BE","Mid-$4B","Bloom Energy -- AI power"),("ARM","Large-$120B","$259M ARM -- chip arch monopoly")],
            "laffont":       [("NU","Mid-$55B","Nu Holdings -- 90M+ Brazil digital bank"),("ASML","Large-$280B","$655M ASML -- only EUV maker")],
            "wood":          [("CRSP","Mid-$5B","CRISPR -- first gene editing cure"),("TEM","Mid-$8B","Tempus AI -- 200+ hospitals"),("CRCL","Small-$6B","Circle -- USDC stablecoin"),("RXRX","Small-$2B","Recursion +682% AI drug")],
            "chamath":       [("COPX","ETF","Copper ETF -- AI data centers 5x copper"),("FCX","Large-$70B","Freeport McMoRan -- largest copper producer")],
            "einhorn":       [("GRBK","Small-$3B","#1 19.1% Green Brick -- Texas homebuilder"),("CNR","Mid-$8B","Coal at ESG-ignored value"),("FLR","Mid-$7B","Fluor -- AI data center construction")],
            "klarman":       [("QSR","Mid-$18B","11.7% $529M -- BK/Tim Hortons/Popeyes franchise royalties"),("WCC","Mid-$7B","WESCO -- electrical dist for AI data centers"),("ELV","Large-$35B","Doubled -- Elevance managed care value")],
            "griffin":       [],
        }
        _LEGENDS_THEMES = {
            "buffett":       ("AI + Media pivot. Exit payments. Homebuilder + airline + media.", "BULLISH AI/Search | EXITING Financials"),
            "soros":         ("AI chip long + protective puts. Macro hedging.", "MIXED -- long chips, hedging puts"),
            "rentec":        ("Hard rotation: chips/gold in, consumer/EV out. 3,207 stocks.", "BULLISH chips+gold | CUTTING consumer+EV"),
            "dalio":         ("Chips in SaaS out. Steel reshoring.", "BULLISH semiconductors | EXITING SaaS"),
            "tepper":        ("Memory supercycle + Power grid + AI cloud.", "VERY BULLISH memory/chips | EXITING China"),
            "ackman":        ("Hyper-concentrated. MSFT mega-bet. Cloud.", "BULLISH MSFT/AMZN | EXITING travel"),
            "druckenmiller": ("AI infra pure play. Next-gen over established tech.", "BULLISH CRWV/TSM | EXITING search"),
            "loeb":          ("Event-driven + small gems + AI + gold.", "BULLISH META/hard assets | EXITING utilities"),
            "cohen":         ("AI infrastructure full stack.", "BULLISH AI infra | ADDING aerospace"),
            "burry":         ("Contrarian value. Healthcare + consumer. No AI.", "CONTRARIAN -- healthcare vs AI crowd"),
            "coleman":       ("AI hyperscaler + INTC turnaround.", "BULLISH AI mega-cap | INTC turnaround"),
            "tudor":         ("Pure macro: gold + options straddles.", "NEUTRAL-MACRO -- expects volatility"),
            "aschenbrenner": ("Long AI power. Short chip sector $8.5B puts.", "ULTRA-BULL AI power | SHORT semi puts"),
            "gerstner":      ("AI supercycle. All-in NVDA/META + infra.", "ULTRA-BULL AI | EXITING China"),
            "laffont":       ("Rotating from chips to chip equipment.", "ROTATING -- selling NVDA | BUYING ASML"),
            "wood":          ("Disruptive: gene edit + AI clinical + stablecoins.", "LONG-TERM BULL disruptive 5-10yr"),
            "chamath":       ("Copper is the real AI play. Power + materials.", "BULLISH copper | AI raw materials"),
            "einhorn":       ("Old-economy value: homebuilders + energy + infra.", "CONTRARIAN VALUE -- ignoring AI hype"),
            "klarman":       ("Deep value: franchise royalties + rail + health.", "BULLISH QSR/AMZN/ELV | Margin of Safety"),
            "griffin":       ("Pure quant: options on everything. 12,857 positions.", "NEUTRAL-QUANT -- volatility arb"),
        }
        _LEGENDS_INSIGHTS = {
            "buffett":       "First post-Buffett 13F. GOOGL tripled. Portfolio shrunk 40->26 positions -- most aggressive cleanup ever. Hidden: NYT digital subs + Macys $55M asset play.",
            "soros":         "Buying NVDA/TSM equity + buying puts = reflexive hedge. BILL (fintech #4) and GPN (#2 global payment rails) are rarely discussed.",
            "rentec":        "UTHR (United Therapeutics rare disease) is the #1 holding -- almost never discussed. Quant buying gold miners. Q1 perf: -1.44%.",
            "dalio":         "NUE (Nucor steel) is the hidden gem -- reshoring + AI data center construction. Dumped all SaaS simultaneously.",
            "tepper":        "+6x on MU and VST doubling are boldest calls. SAND (SanDisk) hidden small cap. FXI exit = permanent China risk-off.",
            "ackman":        "MSFT $2B+ = 15% portfolio instantly. QSR (12.2%) is overlooked gem -- intl franchise + AI drive-thru thesis.",
            "druckenmiller": "CRWV (CoreWeave) key pick. Sold GOOGL while Buffett added = sharpest divergence. Hardware > software shift.",
            "loeb":          "TDS (rural telecom 13.3%) and CRS (specialty alloys 5.9%) are top 5 but never discussed. HUT 8 = crypto infra bet.",
            "cohen":         "CRDO (Credo Technology) small-cap AI interconnect in #5 spot. TDG $336M new = aerospace pricing power.",
            "burry":         "Ignoring AI. Molina (35.1%) = Medicaid HMO discount. Only 4 stocks -- max conviction. LULU doubled = consumer trough call.",
            "coleman":       "Pure AI conviction (GOOGL/NVDA/AMZN/TSM/META top 5). Surprise: $180M Intel -- contrarian foundry separation bet.",
            "tudor":         "Holds IWM puts AND calls = expects large move, unsure direction. Gold + BTC = hard asset hedge. CTA non-directional.",
            "aschenbrenner": "Ex-OpenAI researcher turned $225M into $13.7B in ~1yr. Long AI power (BE $879M) + $8.46B chip PUTS = AI demand real but chips overvalued.",
            "gerstner":      "Coined AI supercycle. Exited all China stocks. CRWV + ARM + BE = AI needs power AND compute. 29.52% annualized return.",
            "laffont":       "Cut NVDA -70% while others held. ASML $655M = chip equipment monopoly over makers. NFLX doubled. NU = LatAm fintech.",
            "wood":          "Only fund with CRISPR exposure. TEM = AI + personalized medicine. CRCL = stablecoin. 5-10yr bets most funds can't hold.",
            "chamath":       "Made 3000% on Groq ($62M to $20B). Copper parabolic thesis -- AI data centers use 5x more copper.",
            "einhorn":       "GRBK (Green Brick) Texas homebuilder at steep discount. CNR (coal) ESG-ignored value. +6.5% Q1 return while AI funds got volatile.",
            "klarman":       "Margin of Safety framework. QSR $529M doubled = BK/Tim Hortons/Popeyes franchise royalties recession-resistant. WCC for AI data center electrical distribution. 22 total holdings.",
            "griffin":       "Citadel $618B = world's largest hedge fund. SPY puts + calls simultaneously = market neutral volatility capture. Pure quant 12,857 positions.",
        }

        st.markdown("### 🏆 Legendary Investors — Q1 2026 13F Filing Tracker")
        st.caption("Filed 2026-05-15  |  Period: Mar 31 2026  |  Source: SEC 13F (45-day lag)  |  20 Investors")

        _sel_key = st.selectbox(
            "Select Investor",
            list(_LEGENDS.keys()),
            format_func=lambda k: f"{_LEGENDS[k]['name']} — {_LEGENDS[k]['firm']} ({_LEGENDS[k]['aum']})",
            key="legend_sel"
        )
        d = _LEGENDS[_sel_key]
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Firm", d["firm"])
        c2.metric("AUM", d["aum"])
        c3.metric("Style", d["style"])
        c4.metric("Filed", "2026-05-15")

        st.markdown("---")
        lcol, rcol = st.columns(2)
        with lcol:
            st.markdown("#### 🟢 Additions / New Buys")
            buys_data = _LEGENDS_BUYS.get(_sel_key, [])
            if buys_data:
                _df_buys = pd.DataFrame(buys_data, columns=["Ticker","Action","Value","Cap","Note"])
                st.dataframe(_df_buys, hide_index=True, use_container_width=True)
            else:
                st.info("No buy data.")
        with rcol:
            st.markdown("#### 🔴 Exits / Reductions")
            sells_data = _LEGENDS_SELLS.get(_sel_key, [])
            if sells_data:
                _df_sells = pd.DataFrame(sells_data, columns=["Ticker","Note"])
                st.dataframe(_df_sells, hide_index=True, use_container_width=True)
            else:
                st.info("No sell data.")

        opts = _LEGENDS_OPTIONS.get(_sel_key, [])
        if opts:
            st.markdown("#### ⚙️ Options Positions")
            _df_opts = pd.DataFrame(opts, columns=["Ticker","Type","Expiry","Note"])
            _df_opts["Signal"] = _df_opts["Type"].map({"PUT":"🔴 PUT","CALL":"🟢 CALL"})
            st.dataframe(_df_opts, hide_index=True, use_container_width=True)

        gems = _LEGENDS_GEMS.get(_sel_key, [])
        if gems:
            st.markdown("#### 💎 Small / Mid Cap Gems")
            _df_gems = pd.DataFrame(gems, columns=["Ticker","Market Cap","Insight"])
            st.dataframe(_df_gems, hide_index=True, use_container_width=True)

        tops = _LEGENDS_TOPS.get(_sel_key, [])
        if tops:
            st.markdown("#### 💼 Top Holdings")
            _df_tops = pd.DataFrame(tops, columns=["Ticker","Weight"])
            try:
                _df_tops["_w"] = pd.to_numeric(_df_tops["Weight"].str.extract(r"([\d.]+)")[0])
                _fig_h = px.bar(_df_tops, x="Ticker", y="_w", text="Weight",
                                color="_w", color_continuous_scale="Blues",
                                labels={"_w":"Weight (%)"})
                _fig_h.update_layout(template="plotly_white", height=240, showlegend=False,
                                     coloraxis_showscale=False)
                _fig_h.update_traces(textposition="outside")
                st.plotly_chart(_fig_h, use_container_width=True)
            except Exception:
                st.dataframe(_df_tops[["Ticker","Weight"]], hide_index=True, use_container_width=True)

        theme_txt, signal_txt = _LEGENDS_THEMES.get(_sel_key, ("—","—"))
        insight_txt = _LEGENDS_INSIGHTS.get(_sel_key, "—")
        tc1, tc2 = st.columns(2)
        tc1.info(f"**🎯 Theme**\n\n{theme_txt}")
        tc2.success(f"**📡 Signal**\n\n{signal_txt}")
        st.warning(f"**💡 Key Insight**\n\n{insight_txt}")

        st.markdown("---")
        # ── Consensus ────────────────────────────────────────────────
        st.markdown("### 📊 Consensus — What Most Are Buying")
        _consensus_buys = [
            ("NVDA",  8, 20, "8/20 legends buying -- near-universal AI chip"),
            ("TSM",   7, 20, "7/20 -- chip supply chain consensus"),
            ("AMZN",  7, 20, "7/20 -- cloud/AI platform"),
            ("GOOGL", 6, 20, "6 bought vs 4 sold -- key divergence"),
            ("MU",    5, 20, "5/20 -- memory chip supercycle"),
            ("META",  4, 20, "4/20 -- AI ad platform"),
            ("MSFT",  4, 20, "4/20 -- cloud infrastructure"),
            ("AVGO",  3, 20, "3/20 -- AI networking silicon"),
            ("CRWV",  4, 20, "4/20 -- AI compute infrastructure"),
            ("UBER",  3, 20, "3/20 -- mobility/AI platform"),
        ]
        _consensus_sells = [
            ("CRM",   5, "Dalio+Soros+Buffett(prev)+Bridgewater+Loeb -- full SaaS exit"),
            ("GOOGL", 4, "Soros+Ackman+Druck+Gerstner SOLD vs Buffett/Coleman ADDED"),
            ("MSFT",  3, "Soros+RenTec+Loeb trimmed"),
            ("NFLX",  2, "RenTec -$673M"),
            ("TSLA",  2, "RenTec -$534M, Wood trimmed"),
            ("PLTR",  2, "RenTec -$542M, Wood trimmed"),
        ]
        cb1, cb2 = st.columns(2)
        with cb1:
            st.markdown("#### 🟢 Most Bought")
            _df_cb = pd.DataFrame([(t, c, f"{c}/{mx}", n) for t,c,mx,n in _consensus_buys],
                                   columns=["Ticker","Count","Out of 20","Notes"])
            _fig_cb = px.bar(_df_cb, x="Count", y="Ticker", orientation="h",
                             color="Count", color_continuous_scale="Greens",
                             hover_data=["Notes"], title="Conviction Breadth (out of 20)")
            _fig_cb.update_layout(template="plotly_white", height=350, showlegend=False,
                                  coloraxis_showscale=False, yaxis={"categoryorder":"total ascending"})
            st.plotly_chart(_fig_cb, use_container_width=True)
        with cb2:
            st.markdown("#### 🔴 Most Sold")
            _df_cs = pd.DataFrame(_consensus_sells, columns=["Ticker","Count","Notes"])
            _fig_cs = px.bar(_df_cs, x="Count", y="Ticker", orientation="h",
                             color="Count", color_continuous_scale="Reds",
                             hover_data=["Notes"], title="Exit Conviction (out of 20)")
            _fig_cs.update_layout(template="plotly_white", height=280, showlegend=False,
                                  coloraxis_showscale=False, yaxis={"categoryorder":"total ascending"})
            st.plotly_chart(_fig_cs, use_container_width=True)

        # ── Options Activity ──────────────────────────────────────────
        st.markdown("#### ⚙️ Options Activity Across All Legends")
        _opts_all = [
            ("SMH PUTS","Aschenbrenner","Q2 2026","$2B notional VanEck Semi hedge","🔴 PUT"),
            ("NVDA PUTS","Aschenbrenner","Q2 2026","$1.6B notional Nvidia hedge","🔴 PUT"),
            ("CRWV PUTS","Soros","Q2 2026","CoreWeave AI hedge","🔴 PUT"),
            ("TSM PUTS","Soros","Q2 2026","Geo-risk hedge","🔴 PUT"),
            ("SPY PUTS","Griffin/Citadel","Q2 2026","#1 position macro hedge","🔴 PUT"),
            ("QQQ PUTS","Griffin/Tudor","Q2 2026","Tech correction hedge","🔴 PUT"),
            ("MU CALLS","Aschenbrenner","Q2 2026","$422M Micron long","🟢 CALL"),
            ("TSM CALLS","Aschenbrenner","Q2 2026","$355M TSM long","🟢 CALL"),
            ("IWM straddle","Tudor","Q2 2026","Both puts+calls = volatility bet","🟡 STRADDLE"),
            ("NVDA CALLS","Griffin","Q2 2026","AI chip upside","🟢 CALL"),
        ]
        _df_opts_all = pd.DataFrame(_opts_all, columns=["Position","Fund","Expiry","Note","Type"])
        st.dataframe(_df_opts_all, hide_index=True, use_container_width=True)

        # ── QoQ Tracker ──────────────────────────────────────────────
        st.markdown("---")
        st.markdown("### 📊 Quarter-over-Quarter Tracker (Q4 2025 → Q1 2026)")
        st.caption("Loading = adding. Unloading = selling. Net $ and avg acquisition price per quarter.")

        _qoq_raw = [
            ("Buffett",  "GOOGL", 18000, 2800,  57800, 16600, 155.6, 287.2, "LOADING+++", "+221% tripled +$13.8B"),
            ("Buffett",  "BAC",   680000,29000, 570000, 24000,  42.6,  42.1, "UNLOADING",  "Trimmed -$5B"),
            ("Buffett",  "DAL",       0,    0,   39800,  2600,   0.0,  65.3, "INITIATE",   "New Delta $2.6B"),
            ("Buffett",  "V",      8500, 2100,       0,     0, 247.1,   0.0, "EXITED",     "Full exit payments"),
            ("Buffett",  "NYT",    9000,  340,   27000,  1020,  37.8,  37.8, "LOADING++",  "+199% media +$680M"),
            ("RenTec",   "AAPL",      0,    0,   43000,   781,   0.0, 181.6, "INITIATE",   "New $781M Apple"),
            ("RenTec",   "MU",    12000, 1380,   18100,  1900, 115.0, 105.0, "LOADING++",  "+50% $520M Micron"),
            ("RenTec",   "NFLX",  18000, 8100,    5000,  2250, 450.0, 450.0, "UNLOADING",  "-$5.85B exit"),
            ("RenTec",   "PLTR",  14500, 1900,    8400,  1100, 131.0, 130.1, "UNLOADING",  "-$800M Palantir"),
            ("Tepper",   "MU",     1800,  207,   10800,  1134, 115.0, 105.0, "LOADING+++", "+6x Micron +$927M"),
            ("Tepper",   "UBER",   3200,  310,    9600,   930,  96.9,  96.9, "LOADING+++", "Tripled +$620M"),
            ("Tepper",   "VST",    2800,  280,    5600,   560, 100.0, 100.0, "LOADING++",  "Doubled +$280M"),
            ("Tepper",   "AAL",    8200,  148,       0,     0,  18.0,   0.0, "EXITED",     "Full exit airlines"),
            ("Ackman",   "MSFT",      0,    0,   16000,  2000,   0.0, 125.0, "INITIATE",   "New $2B MSFT"),
            ("Ackman",   "HLT",    4800,  845,       0,     0, 176.0,   0.0, "EXITED",     "Full exit Hilton"),
            ("Loeb",     "META",      0,    0,    1800,   900,   0.0, 500.0, "INITIATE",   "New Meta $900M"),
            ("Loeb",     "MSFT",   2400,  900,       0,     0, 375.0,   0.0, "EXITED",     "Full exit MSFT"),
            ("Druck",    "CRWV",   1200,  186,    3400,   527, 155.0, 155.0, "LOADING++",  "Nearly 3x CoreWeave"),
            ("Druck",    "GOOGL",  4800,  736,       0,     0, 153.3,   0.0, "EXITED",     "Full exit Alphabet"),
            ("Aschenb",  "BE",     8000,  520,   13400,   879,  65.0,  65.6, "LOADING++",  "+67% Bloom Energy"),
            ("Gerstner", "CRWV",   3200,  496,    4499,   348, 155.0,  77.3, "LOADING+",   "+1.28M CoreWeave"),
            ("Gerstner", "ARM",       0,    0,    1715,   259,   0.0, 151.0, "INITIATE",   "New ARM $259M"),
            ("Gerstner", "GOOGL",   519,  162,       0,     0, 312.1,   0.0, "EXITED",     "Full exit Alphabet"),
            ("Laffont",  "ASML",      0,    0,     496,   655,   0.0,1320.6, "INITIATE",   "New ASML $655M monopoly"),
            ("Laffont",  "NFLX",   3100, 1395,    6200,  2790, 450.0, 450.0, "LOADING++",  "Doubled Netflix +$1.4B"),
            ("Laffont",  "NVDA",  28000, 3794,    8400,  1180, 135.5, 140.5, "UNLOADING",  "-70% -$2.6B"),
            ("Laffont",  "AMZN",  18000, 3600,    3240,   648, 200.0, 200.0, "UNLOADING",  "-82% -$2.95B"),
            ("Wood",     "CRSP",   6400,  352,    8800,   484,  55.0,  55.0, "LOADING+",   "+38% CRISPR +$132M"),
            ("Wood",     "TSLA",  98000,28420,   86000, 24940, 290.0, 290.0, "UNLOADING",  "Trimmed -$3.48B"),
            ("Klarman",  "QSR",    4050,  304,    8253,   529,  75.1,  64.1, "LOADING++",  "Doubled +$225M"),
            ("Klarman",  "ELV",     616,  217,    1319,   426, 352.3, 323.0, "LOADING++",  "Doubled +$209M"),
            ("Einhorn",  "GRBK",  18000,  486,   22600,   611,  27.0,  27.0, "LOADING+",   "Added +$125M"),
            ("Griffin",  "NVDA",  42000, 5700,   51000,  7140, 135.7, 140.0, "LOADING+",   "Added +$1.44B"),
            ("Griffin",  "AAPL",  38000, 6250,   40000,  7160, 164.5, 179.0, "LOADING+",   "Added +$910M"),
            ("Dalio",    "TSM",       0,    0,    8200,   900,   0.0, 109.8, "INITIATE",   "New TSM position"),
            ("Dalio",    "CRM",    4200,  900,       0,     0, 214.3,   0.0, "EXITED",     "Full Salesforce exit"),
        ]
        _df_qoq = pd.DataFrame(_qoq_raw,
            columns=["Investor","Ticker","Q4_Shares(K)","Q4_Val($M)","Q1_Shares(K)","Q1_Val($M)",
                     "AvgPx_Q4($)","AvgPx_Q1($)","Action","Notes"])
        _df_qoq["Net_$_Chg(M)"] = _df_qoq["Q1_Val($M)"] - _df_qoq["Q4_Val($M)"]
        _df_qoq["Share_Chg_%"] = (
            (_df_qoq["Q1_Shares(K)"] - _df_qoq["Q4_Shares(K)"]) /
            _df_qoq["Q4_Shares(K)"].replace(0, float("nan")) * 100
        ).round(1)
        _action_em = {"LOADING+++":"🔥🔥🔥 ","LOADING++":"🔥🔥 ","LOADING+":"🟢 ",
                      "INITIATE":"🆕 ","HOLD":"⚪ ","UNLOADING":"🔴 ","EXITED":"💀 "}
        _df_qoq["Signal"] = _df_qoq["Action"].map(_action_em).fillna("") + _df_qoq["Action"]

        qc1, qc2, qc3 = st.columns(3)
        _qoq_invs = ["All"] + sorted(_df_qoq["Investor"].unique().tolist())
        _qoq_acts = ["All","LOADING+++","LOADING++","LOADING+","INITIATE","UNLOADING","EXITED"]
        _sel_qi = qc1.selectbox("Filter Investor", _qoq_invs, key="qoq_inv")
        _sel_qa = qc2.selectbox("Filter Action",   _qoq_acts, key="qoq_act")
        _sel_qs = qc3.selectbox("Sort By", ["Net $ Change","Investor","Ticker"], key="qoq_sort")

        _dq = _df_qoq.copy()
        if _sel_qi != "All": _dq = _dq[_dq["Investor"] == _sel_qi]
        if _sel_qa != "All": _dq = _dq[_dq["Action"] == _sel_qa]
        _sort_map = {"Net $ Change": ("Net_$_Chg(M)", False),
                     "Investor": ("Investor", True), "Ticker": ("Ticker", True)}
        _sc, _sasc = _sort_map[_sel_qs]
        _dq = _dq.sort_values(_sc, ascending=_sasc)

        st.dataframe(_dq[["Investor","Ticker","Signal","Q4_Val($M)","Q1_Val($M)",
                           "Net_$_Chg(M)","Share_Chg_%","AvgPx_Q4($)","AvgPx_Q1($)","Notes"]],
                     hide_index=True, use_container_width=True)

        try:
            _net_inv = _df_qoq.groupby("Investor")["Net_$_Chg(M)"].sum().sort_values()
            _fig_qoq = px.bar(x=_net_inv.values, y=_net_inv.index,
                              orientation="h",
                              color=_net_inv.values,
                              color_continuous_scale=["red","gray","green"],
                              color_continuous_midpoint=0,
                              text=[f"${v:+.0f}M" for v in _net_inv.values],
                              labels={"x":"Net $ Change ($M)","y":"Investor"},
                              title="Net $ Position Change by Investor (Q4 2025 -> Q1 2026)")
            _fig_qoq.update_layout(template="plotly_white", height=500,
                                   coloraxis_showscale=False)
            _fig_qoq.update_traces(textposition="outside")
            st.plotly_chart(_fig_qoq, use_container_width=True)
        except Exception:
            pass

        st.markdown("#### 🏆 Top 10 Biggest Single Position Moves")
        _top10 = _df_qoq.iloc[_df_qoq["Net_$_Chg(M)"].abs().nlargest(10).index]
        st.dataframe(_top10[["Investor","Ticker","Q4_Val($M)","Q1_Val($M)","Net_$_Chg(M)",
                              "AvgPx_Q4($)","AvgPx_Q1($)","Action","Notes"]],
                     hide_index=True, use_container_width=True)

        # ── Future Catches ────────────────────────────────────────────
        st.markdown("---")
        st.markdown("### 🚀 Future Catches — Small/Mid Caps Legends Are Betting On")
        _future = [
            ("BE",   "Bloom Energy",       "Mid-$4B",   "AI Power",    "Aschenbrenner $879M #1","5-10x","High"),
            ("CRWV", "CoreWeave",          "Mid-$60B",  "AI Compute",  "Druck+Altimeter+ARK",   "3-5x", "High"),
            ("CLSK", "CleanSpark",         "Small-$2B", "AI/BTC Infra","Aschenbrenner",          "5-10x","V.High"),
            ("APLD", "Applied Digital",    "Small-$1B", "AI Datacenter","Aschenbrenner",         "5-15x","V.High"),
            ("TEM",  "Tempus AI",          "Mid-$8B",   "AI Biotech",  "ARK Invest",             "3-8x", "High"),
            ("CRSP", "CRISPR Therap",      "Mid-$5B",   "Gene Editing","ARK #2 holding",         "5-20x","V.High"),
            ("CRCL", "Circle Internet",    "Small-$6B", "Stablecoin",  "ARK Invest",             "3-8x", "High"),
            ("RXRX", "Recursion Pharma",   "Small-$2B", "AI Drug",     "ARK, institutional",     "5-20x","V.High"),
            ("CRDO", "Credo Technology",   "Small-$6B", "AI Networking","Cohen Point72 #5",      "5-10x","High"),
            ("ARM",  "ARM Holdings",       "Large-$120B","AI Chip Arch","Altimeter $259M",        "2-4x", "Medium"),
            ("ASML", "ASML Holding",       "Large-$280B","Chip Equip",  "Coatue $655M, Cohen",   "2-3x", "Med-Low"),
            ("VST",  "Vistra Energy",      "Mid-$25B",  "AI Power",    "Tepper 2x",              "3-6x", "Medium"),
            ("SNDK", "SanDisk",            "Small-$5B", "AI Storage",  "Aschenbrenner+Tepper",   "3-6x", "High"),
            ("UTHR", "United Therapeutics","Mid-$16B",  "Rare Disease","RenTec #1 holding",      "2-4x", "Medium"),
            ("NUE",  "Nucor Steel",        "Mid-$16B",  "Steel/Infra", "Dalio Bridgewater",      "2-4x", "Medium"),
            ("COPX", "Copper Miners ETF",  "ETF",       "AI Materials","Chamath thesis",          "3-8x", "High"),
            ("HOOD", "Robinhood",          "Mid-$20B",  "Retail Finance","ARK Invest",            "3-5x", "Medium"),
            ("NU",   "Nu Holdings",        "Mid-$55B",  "LatAm Fintech","Coatue holding",        "2-5x", "Medium"),
            ("QSR",  "Restaurant Brands",  "Mid-$18B",  "Franchise",   "Ackman 12.2%, Klarman", "2-4x", "Medium"),
            ("GRBK", "Green Brick Part",   "Small-$3B", "Homebuilder", "Einhorn #1 19.1%",       "2-5x", "Medium"),
            ("MOH",  "Molina Healthcare",  "Mid-$8B",   "Healthcare",  "Burry #1 35.1%",         "2-4x", "Medium"),
            ("CRS",  "Carpenter Tech",     "Small-$6B", "Aero Metal",  "Loeb #5 5.9%",           "2-4x", "Medium"),
            ("BILL", "Bill Holdings",      "Mid-$6B",   "SMB Fintech", "Soros #4 holding",       "2-5x", "Medium"),
            ("INTC", "Intel",              "Large-$80B","Chip Turnar", "Coleman $180M new",       "2-5x", "High"),
        ]
        _df_fc = pd.DataFrame(_future, columns=["Ticker","Name","Market Cap","Sector","Who","Potential","Risk"])
        _fcc1, _fcc2 = st.columns(2)
        _sec_opts = ["All"] + sorted(_df_fc["Sector"].unique().tolist())
        _rsk_opts = ["All","V.High","High","Medium","Med-Low"]
        _sel_fsec = _fcc1.selectbox("Sector", _sec_opts, key="fc_sec2")
        _sel_frsk = _fcc2.selectbox("Risk",   _rsk_opts, key="fc_rsk2")
        _dffc = _df_fc.copy()
        if _sel_fsec != "All": _dffc = _dffc[_dffc["Sector"] == _sel_fsec]
        if _sel_frsk != "All": _dffc = _dffc[_dffc["Risk"] == _sel_frsk]
        st.dataframe(_dffc[["Ticker","Name","Market Cap","Sector","Who","Potential","Risk"]],
                     hide_index=True, use_container_width=True)
        try:
            _dffc["_pot"] = pd.to_numeric(_dffc["Potential"].str.extract(r"(\d+)")[0])
            _fig_fc2 = px.scatter(_dffc, x="Sector", y="_pot", size="_pot",
                                  color="Risk", hover_data=["Ticker","Name","Who"],
                                  color_discrete_map={"V.High":"red","High":"orange","Medium":"gold","Med-Low":"green"},
                                  labels={"_pot":"Upside (x)"},
                                  title="Future Catches: Upside vs Risk by Sector")
            _fig_fc2.update_layout(template="plotly_white", height=400)
            st.plotly_chart(_fig_fc2, use_container_width=True)
        except Exception:
            pass



    # ── Tab 5: Short Sellers ────────────────────────────────────
    with tab5:
        st.markdown("### 🩳 Short Sellers — Most Shorted Stocks")
        st.caption("Short interest, days-to-cover, float %, squeeze score. Source: Yahoo Finance (bi-monthly SEC update).")

        _SHORT_UNIVERSE = {
            "Mega Cap / High Profile": ["TSLA","AAPL","NVDA","AMZN","MSFT","META","GOOGL","NFLX","PLTR","COIN","AMD","SMCI","CRWD","SNOW","UBER"],
            "EV / Clean Energy":       ["RIVN","LCID","NIO","PLUG","CHPT","BLNK","FSLR","ENPH","ARRY","BE","VST","CEG","NRG"],
            "Biotech / Pharma":        ["MRNA","BNTX","RXRX","CRSP","NVAX","SAVA","VKTX","GILD","BIIB","SMMT","ACHR","IONS"],
            "Retail / Consumer":       ["GME","AMC","BYND","LULU","CPNG","W","ETSY","RH","PTON","WOLF","PRTY","REAL"],
            "Financials / Fintech":    ["UPST","AFRM","OPEN","NAVI","PFSI","UWMC","RKT","SFT","CURO","PRAA","LC","SOFI"],
            "AI / Tech Mid-Cap":       ["AI","BBAI","SOUN","ARQQ","AMBA","MKSI","FORM","AIOT","AEHR","SPWR","LAZR","MVIS"],
            "Energy / Commodities":    ["GEVO","AMRC","CLNE","MAXN","NOVA","STEM","TELL","HYLN","HOUS","SPCE","JOBY"],
            "Your Tickers (DB)":       [],
        }
        try:
            _db_tickers = q("SELECT DISTINCT ticker FROM options_change ORDER BY ticker")["ticker"].tolist()
            _SHORT_UNIVERSE["Your Tickers (DB)"] = _db_tickers[:30]
        except Exception:
            pass

        _sc1, _sc2, _sc3 = st.columns([2, 1, 1])
        with _sc1:
            _sel_group = st.selectbox("Stock Group", list(_SHORT_UNIVERSE.keys()), key="short_group")
        with _sc2:
            _min_short_pct = st.slider("Min Short % Float", 0, 30, 3, 1, key="short_min_pct")
        with _sc3:
            _sort_by = st.selectbox("Sort By", ["Short % Float","Short Ratio","Squeeze Score","MoM Chg %"], key="short_sort")

        _tickers_to_scan = _SHORT_UNIVERSE.get(_sel_group, [])
        if not _tickers_to_scan:
            st.info("No tickers in this group.")
        else:
            if st.button("🔍 Scan Short Interest", type="primary", key="short_scan_btn"):
                with st.spinner(f"Fetching short data for {len(_tickers_to_scan)} stocks..."):
                    _short_rows = []
                    _prog = st.progress(0)
                    for _si_i, _stk in enumerate(_tickers_to_scan):
                        try:
                            _si = _get_short_data_dash(_stk)
                            _spf = _si.get("short_pct_float")
                            _sr  = _si.get("short_ratio")
                            _ss  = _si.get("shares_short")
                            _ssp = _si.get("shares_short_prior")
                            _flt = _si.get("float_shares")
                            _sc2v= _si.get("squeeze_score", 0)
                            _sl  = _si.get("squeeze_label", "LOW")
                            if _spf is None or _spf < _min_short_pct:
                                continue
                            _mom = None
                            if _ss and _ssp and _ssp > 0:
                                _mom = (_ss - _ssp) / _ssp * 100
                            try:
                                _inf = yf.Ticker(_stk).info
                                _px   = _inf.get("currentPrice") or _inf.get("regularMarketPrice") or 0
                                _mcap = _inf.get("marketCap") or 0
                                _name = _inf.get("shortName", _stk)[:22]
                                _sect = _inf.get("sector", "—")
                            except Exception:
                                _px=0; _mcap=0; _name=_stk; _sect="—"
                            def _fmts(n):
                                if not n: return "—"
                                if n>=1e9: return f"{n/1e9:.2f}B"
                                if n>=1e6: return f"{n/1e6:.1f}M"
                                return f"{n:,.0f}"
                            _short_rows.append({
                                "Ticker":        _stk,
                                "Name":          _name,
                                "Sector":        _sect,
                                "Price":         round(_px,2) if _px else None,
                                "Mkt Cap":       _fmts(_mcap),
                                "Short % Float": round(_spf,1) if _spf else None,
                                "Short Ratio":   round(_sr,1) if _sr else None,
                                "Shares Short":  _fmts(_ss),
                                "Float":         _fmts(_flt),
                                "MoM Chg %":     round(_mom,1) if _mom is not None else None,
                                "Squeeze Score": _sc2v,
                                "Squeeze Risk":  _sl,
                                "_spf":  _spf or 0,
                                "_sr":   _sr or 0,
                                "_sc":   _sc2v,
                                "_mom":  _mom or 0,
                            })
                        except Exception:
                            pass
                        _prog.progress((_si_i+1)/len(_tickers_to_scan))
                    _prog.empty()

                st.session_state["_short_df_result"] = _short_rows
                st.session_state["_short_group_used"] = _sel_group

            # Display if results cached
            _cached = st.session_state.get("_short_df_result", [])
            if _cached:
                _sdf = pd.DataFrame(_cached)
                _sort_col = {"Short % Float":"_spf","Short Ratio":"_sr","Squeeze Score":"_sc","MoM Chg %":"_mom"}.get(_sort_by,"_spf")
                _sdf = _sdf.sort_values(_sort_col, ascending=False).reset_index(drop=True)

                # Summary
                _sm1,_sm2,_sm3,_sm4,_sm5 = st.columns(5)
                _sm1.metric("Stocks Found", len(_sdf))
                _sm2.metric("Short % ≥ 20%",  len(_sdf[_sdf["_spf"]>=20]), delta="Heavy shorts")
                _sm3.metric("Squeeze Risk ≥7", len(_sdf[_sdf["_sc"]>=7]),  delta="🔥 Watch")
                _sm4.metric("Rising Short ↑",  len(_sdf[_sdf["_mom"]>10]), delta="Bearish signal")
                _sm5.metric("Covering ↓",      len(_sdf[_sdf["_mom"]<-10]),delta="Squeeze trigger")

                # Styled table
                def _srow(row):
                    sc=row.get("Squeeze Score",0) or 0
                    spf=row.get("Short % Float",0) or 0
                    mom=row.get("MoM Chg %",0) or 0
                    if sc>=7:   return ["background-color:#4a1a00;color:#ffddaa;font-weight:700"]*len(row)
                    if spf>=25: return ["background-color:#3a0a0a;color:#ffcccc;font-weight:600"]*len(row)
                    if spf>=15: return ["background-color:#2a1a00;color:#ffe0aa"]*len(row)
                    if mom>15:  return ["background-color:#0a0a2a;color:#aaaaff"]*len(row)
                    return [""]*len(row)

                _disp_cols = ["Ticker","Name","Sector","Price","Mkt Cap","Short % Float","Short Ratio","Shares Short","MoM Chg %","Squeeze Score","Squeeze Risk"]
                st.dataframe(
                    _sdf[_disp_cols].style.apply(_srow,axis=1).format({
                        "Short % Float": lambda v: f"{v:.1f}%" if v else "—",
                        "Short Ratio":   lambda v: f"{v:.1f}d"  if v else "—",
                        "MoM Chg %":     lambda v: f"{v:+.1f}%" if v else "—",
                        "Price":         lambda v: f"${v:.2f}"  if v else "—",
                    }),
                    hide_index=True, use_container_width=True
                )
                st.caption("🟧 Orange = Squeeze Score ≥7  |  🟥 Red = Short % ≥25%  |  🟨 Amber = 15-25%  |  🟦 Blue = Rising short interest +15%")

                # Squeeze candidates
                _sqdf = _sdf[_sdf["_sc"]>=5].head(5)
                if not _sqdf.empty:
                    st.markdown("---")
                    st.markdown("#### 🔥 Squeeze Candidates (Score ≥ 5/10)")
                    st.caption("High short float + high days-to-cover + rising shorts = squeeze setup to watch.")
                    for _, _sqr in _sqdf.iterrows():
                        _spf2=_sqr.get("Short % Float") or 0
                        _sr2 =_sqr.get("Short Ratio") or 0
                        _mom2=_sqr.get("MoM Chg %") or 0
                        _sc2v2=_sqr.get("Squeeze Score") or 0
                        with st.container(border=True):
                            _q1,_q2 = st.columns([1,3])
                            with _q1:
                                st.markdown(f"### {'🔥' if _sc2v2>=8 else '⚠️'} {_sqr['Ticker']}")
                                st.markdown(f"Score: **{_sc2v2}/10**")
                            with _q2:
                                st.markdown(f"**{_sqr['Name']}** | {_sqr['Sector']}")
                                _why2=[]
                                if _spf2>=20: _why2.append(f"**{_spf2:.1f}% of float shorted** — heavily crowded")
                                if _sr2>=5:   _why2.append(f"**{_sr2:.0f} days to cover** — slow exit = squeeze fuel")
                                if _mom2>10:  _why2.append(f"Short interest **rising +{_mom2:.0f}% MoM** — conviction growing")
                                elif _mom2<-10: _why2.append(f"Short interest **falling {_mom2:.0f}% MoM** — COVERING = trigger")
                                for _w2 in _why2: st.markdown(f"  ▪ {_w2}")

                # Charts
                st.markdown("---")
                _ch1,_ch2 = st.columns(2)
                with _ch1:
                    st.markdown("#### 📊 Short % of Float — Top 15")
                    _top_spf = _sdf.nlargest(15,"_spf")[["Ticker","_spf"]].copy()
                    _fig_spf = px.bar(_top_spf,x="_spf",y="Ticker",orientation="h",
                                      color="_spf",color_continuous_scale=["#ffd600","#ff6d00","#ff1744"],
                                      text=_top_spf["_spf"].apply(lambda v: f"{v:.1f}%"),
                                      labels={"_spf":"Short % Float"})
                    _fig_spf.update_traces(textposition="outside")
                    _fig_spf.add_vline(x=10,line=dict(color="#26a69a",dash="dash",width=1),annotation_text="10%",annotation_font_color="#26a69a")
                    _fig_spf.add_vline(x=20,line=dict(color="#ff5252",dash="dash",width=1),annotation_text="20%",annotation_font_color="#ff5252")
                    _fig_spf.update_layout(template="plotly_dark",height=380,showlegend=False,coloraxis_showscale=False,
                                           yaxis={"categoryorder":"total ascending"},margin=dict(t=10,b=10,l=10,r=60))
                    st.plotly_chart(_fig_spf, use_container_width=True)

                with _ch2:
                    st.markdown("#### 📅 Days to Cover — Top 15")
                    _top_sr = _sdf[_sdf["_sr"]>0].nlargest(15,"_sr")[["Ticker","_sr"]].copy()
                    if not _top_sr.empty:
                        _fig_sr = px.bar(_top_sr,x="_sr",y="Ticker",orientation="h",
                                         color="_sr",color_continuous_scale=["#1565C0","#6a1b9a","#b71c1c"],
                                         text=_top_sr["_sr"].apply(lambda v: f"{v:.1f}d"),
                                         labels={"_sr":"Days to Cover"})
                        _fig_sr.update_traces(textposition="outside")
                        _fig_sr.add_vline(x=5,line=dict(color="#ff5252",dash="dash",width=1),annotation_text="5d",annotation_font_color="#ff5252")
                        _fig_sr.update_layout(template="plotly_dark",height=380,showlegend=False,coloraxis_showscale=False,
                                              yaxis={"categoryorder":"total ascending"},margin=dict(t=10,b=10,l=10,r=60))
                        st.plotly_chart(_fig_sr, use_container_width=True)

                # MoM change chart
                _mom_df = _sdf[_sdf["_mom"].abs()>0].copy()
                if not _mom_df.empty:
                    st.markdown("#### 📈 Month-over-Month Short Interest Change")
                    st.caption("🔴 Rising = more bears piling in. 🟢 Falling = shorts covering — watch for squeeze trigger.")
                    _mom_top = pd.concat([_mom_df.nlargest(8,"_mom"),_mom_df.nsmallest(8,"_mom")]).drop_duplicates("Ticker").sort_values("_mom")
                    _fig_mom = px.bar(_mom_top,x="_mom",y="Ticker",orientation="h",
                                      color="_mom",color_continuous_scale=["#26a69a","#78909c","#ef5350"],
                                      color_continuous_midpoint=0,
                                      text=_mom_top["_mom"].apply(lambda v: f"{v:+.1f}%"),
                                      labels={"_mom":"MoM Change %"})
                    _fig_mom.update_traces(textposition="outside")
                    _fig_mom.add_vline(x=0,line=dict(color="#FFD700",width=1.5))
                    _fig_mom.update_layout(template="plotly_dark",height=380,showlegend=False,coloraxis_showscale=False,
                                           yaxis={"categoryorder":"total ascending"},margin=dict(t=10,b=10,l=10,r=70))
                    st.plotly_chart(_fig_mom, use_container_width=True)

                # Legend
                st.markdown("---")
                st.markdown("""
#### 📖 How to Read Short Data
| Metric | What it means | Red flag |
|---|---|---|
| **Short % Float** | % of freely tradeable shares sold short | ≥20% = heavily crowded |
| **Short Ratio** | Days to unwind at avg daily volume | ≥5d = squeeze risk |
| **MoM Change %** | Month-over-month short interest change | +15%↑ = bears increasing |
| **Squeeze Score** | Combined risk score 0-10 | ≥7 = high squeeze potential |

**Trading signals:**
- 📈 **Rising shorts + falling price** → Bearish conviction, trend likely continuing
- 📉 **Rising shorts + rising price** → Short squeeze building, explosive upside risk
- 🔄 **Falling shorts (covering)** → Covering rally likely, reduce bearish bets
- ⚠️ **Short % >25%** → Avoid new shorts — too crowded, reversal risk high
                """)

# ===================================================================
# ──  LEGENDARY INVESTORS — Standalone page
# ===================================================================
elif page == "🏆 Legendary Investors (13F)":
    st.markdown("## 🏆 Legendary Investors — Q1 2026 13F Tracker")
    st.caption("Filed 2026-05-15  |  Period: Mar 31 2026  |  Source: SEC 13F (45-day lag)")
    st.info("Navigate to **📈 Insider / Congress / Whales → 🏆 Legendary Investors (13F)** tab for the full interactive dashboard.", icon="💡")


# ===================================================================
# ──  SMART MONEY HUB — 10 Sections
#  Sources: Pan & Poteshman MIT 2006, Springer 2025, CFTC COT,
#  SpotGamma/Cem Karsan, Prandelli 2026, AQR/State Street, MDPI 2024
# ===================================================================
elif page == "\U0001f9e0 Smart Money Hub":
    # ── Single-view Smart Money report ────────────────────────────────────────────────
    st.markdown("## \U0001f9e0 Smart Money Report")
    st.caption(
        "See what big funds & insiders are doing — explained in plain English. "
        "All 7 signals in one scrollable view."
    )

    conn = get_conn()
    try:
        # Use latest date actually in DB (market may not have run today yet)
        _td_row = conn.execute(
            "SELECT trade_date_now FROM options_change ORDER BY "
            "substr(trade_date_now,7,4)||substr(trade_date_now,1,2)||substr(trade_date_now,4,2) DESC LIMIT 1"
        ).fetchone()
        _td = _td_row[0] if _td_row else datetime.now().strftime("%m-%d-%Y")
        _td2_row = conn.execute(
            "SELECT trade_date FROM stock_daily ORDER BY "
            "substr(trade_date,7,4)||substr(trade_date,1,2)||substr(trade_date,4,2) DESC LIMIT 1"
        ).fetchone()
        _td2 = _td2_row[0] if _td2_row else _td
        st.caption(f"📅 Data as of: **{_td}**")
        signals = []   # list of (label, score, max_score)

        # ── 1. UNUSUAL OPTIONS ACTIVITY ─────────────────────────────────────
        with st.expander("\U0001f4ca 1. Unusual Options Activity (UOA)", expanded=True):
            st.caption(
                "\U0001f4a1 **What it means:** Big investors quietly buy huge option blocks before a big move. "
                "We compare today’s activity to the 10-day average. "
                "**Surge ≥1.5×** = someone knows something. "
                "**Insight:** Look at which expiry they chose — that tells you WHEN they expect the move."
            )
            try:
                _df_uoa = pd.read_sql(
                    "SELECT ticker, expiry_date, "
                    "  SUM(openInt_Call_now+openInt_Put_now) AS oi_today, COUNT(*) AS strikes "
                    "FROM options_change WHERE trade_date_now=? GROUP BY ticker, expiry_date HAVING oi_today>0",
                    conn, params=[_td]
                )
                _df_avg = pd.read_sql(
                    "SELECT ticker, AVG(total_oi) AS avg_oi FROM ("
                    "  SELECT ticker, trade_date_now, SUM(openInt_Call_now+openInt_Put_now) AS total_oi "
                    "  FROM options_change GROUP BY ticker, trade_date_now) GROUP BY ticker",
                    conn
                )
                _df_uoa = _df_uoa.merge(_df_avg, on="ticker", how="left")
                _df_uoa["surge"] = _df_uoa["oi_today"] / _df_uoa["avg_oi"].replace(0, np.nan)
                _hot = _df_uoa[_df_uoa["surge"] >= 1.5].sort_values("surge", ascending=False).head(15)
                _sc = min(2, len(_hot))
                signals.append(("Unusual Options (UOA)", _sc, 2))
                if _hot.empty:
                    st.info("\U0001f7e1 No unusual bets today — market is quiet.")
                else:
                    st.success(f"\U0001f534 {len(_hot)} rows show unusual options bets — check the expiry dates for timing clues")
                    _hot_show = _hot[["ticker","expiry_date","surge","strikes"]].copy()
                    _hot_show.columns = ["Ticker","Expiry","Surge (×avg)","# Strikes"]
                    _hot_show["Expiry"] = _hot_show["Expiry"].astype(str).str[:10]
                    st.dataframe(_hot_show, use_container_width=True, hide_index=True,
                                 column_config={"Surge (×avg)": st.column_config.ProgressColumn(format="%.1f×", min_value=0, max_value=5)})
            except Exception as _ex:
                st.warning(f"UOA unavailable: {_ex}")
                signals.append(("Unusual Options (UOA)", 0, 2))

        # ── 2. DARK POOL / BLOCK TRADES ──────────────────────────────────────
        with st.expander("\U0001f3e6 2. Dark Pool / Block Trades", expanded=True):
            st.caption(
                "\U0001f4a1 **What it means:** Institutions hide big trades in ‘dark pools’. "
                "A single strike with Open Interest ≥3× the average = they quietly built a huge position. "
                "**CALL bias = they expect it to go UP. PUT bias = they expect DOWN.** "
                "**Expiry = when they expect the move to happen.**"
            )
            try:
                _df_dp = pd.read_sql(
                    "SELECT ticker, strike, expiry_date, "
                    "  (openInt_Call_now+openInt_Put_now) AS oi, "
                    "  ROUND(openInt_Call_now*100.0/NULLIF(openInt_Call_now+openInt_Put_now,0),0) AS call_pct "
                    "FROM options_change WHERE trade_date_now=? ORDER BY oi DESC LIMIT 500",
                    conn, params=[_td]
                )
                if not _df_dp.empty:
                    _avg_oi = _df_dp["oi"].mean()
                    _blocks = _df_dp[_df_dp["oi"] >= _avg_oi * 3].head(15).copy()
                    _blocks["Direction"] = _blocks["call_pct"].apply(
                        lambda p: "\U0001f7e2 CALL (Bullish)" if p > 60 else ("\U0001f534 PUT (Bearish)" if p < 40 else "\U0001f7e1 Mixed"))
                    _blocks["Expiry"] = _blocks["expiry_date"].astype(str).str[:10]
                    _sc = min(1, len(_blocks))
                    signals.append(("Dark Pool Blocks", _sc, 1))
                    if _blocks.empty:
                        st.info("\U0001f7e1 No block concentrations found today.")
                    else:
                        st.success(f"\U0001f535 {len(_blocks)} institutional block positions detected")
                        st.dataframe(
                            _blocks[["ticker","strike","Expiry","oi","Direction"]].rename(
                                columns={"ticker":"Ticker","strike":"Strike","oi":"Open Interest"}),
                            use_container_width=True, hide_index=True)
                else:
                    signals.append(("Dark Pool Blocks", 0, 1))
                    st.info("No options data for today yet.")
            except Exception as _ex:
                st.warning(f"Dark Pool unavailable: {_ex}")
                signals.append(("Dark Pool Blocks", 0, 1))

        # ── 3. MARKET MOOD (PCR Z-Score) ───────────────────────────────────
        with st.expander("\U0001f628 3. Market Mood — Fear vs Greed", expanded=True):
            st.caption(
                "\U0001f4a1 **What it means:** The Put/Call Ratio (PCR) measures fear vs greed. "
                "When people panic-buy puts, PCR spikes — but extreme fear often precedes a bounce (rubber-band effect). "
                "**Z-Score > 1.5 = unusual fear → possible bounce. Z-Score < -1.5 = unusual greed → possible pullback.**"
            )
            try:
                _df_pcr = pd.read_sql(
                    "SELECT ticker, pcr_oi FROM stock_daily WHERE trade_date=? AND pcr_oi IS NOT NULL AND ticker!='SPY'",
                    conn, params=[_td2])
                _df_base = pd.read_sql(
                    "SELECT ticker, AVG(pcr_oi) AS mean_pcr, "
                    "  (AVG(pcr_oi*pcr_oi) - AVG(pcr_oi)*AVG(pcr_oi)) AS var_pcr "
                    "FROM stock_daily WHERE ticker!='SPY' GROUP BY ticker", conn)
                _df_pcr = _df_pcr.merge(_df_base, on="ticker", how="inner")
                _df_pcr["std"] = np.sqrt(_df_pcr["var_pcr"].clip(0))
                _df_pcr["z"] = (_df_pcr["pcr_oi"] - _df_pcr["mean_pcr"]) / _df_pcr["std"].replace(0, np.nan)
                _fear  = _df_pcr[_df_pcr["z"] >= 1.5].sort_values("z", ascending=False).head(8)
                _greed = _df_pcr[_df_pcr["z"] <= -1.5].sort_values("z").head(8)
                _sc = min(2, len(_fear) + len(_greed))
                signals.append(("Market Mood", _sc, 2))
                _c1, _c2 = st.columns(2)
                with _c1:
                    st.markdown("**\U0001f628 FEAR tickers** → Extreme put buying → Possible bounce")
                    if _fear.empty:
                        st.info("None today \U0001f7e2")
                    else:
                        st.dataframe(_fear[["ticker","pcr_oi","z"]].rename(columns={"pcr_oi":"PCR","z":"Z-Score"}),
                                     use_container_width=True, hide_index=True)
                with _c2:
                    st.markdown("**\U0001f60e GREED tickers** → Extreme call buying → Possible pullback")
                    if _greed.empty:
                        st.info("None today \U0001f7e2")
                    else:
                        st.dataframe(_greed[["ticker","pcr_oi","z"]].rename(columns={"pcr_oi":"PCR","z":"Z-Score"}),
                                     use_container_width=True, hide_index=True)
            except Exception as _ex:
                st.warning(f"Mood unavailable: {_ex}")
                signals.append(("Market Mood", 0, 2))

        # ── 4. DEALER GEX ─────────────────────────────────────────────────────────────
        with st.expander("⚖️ 4. Dealer GEX — Is the Market Stable or Volatile?", expanded=True):
            st.caption(
                "\U0001f4a1 **What it means:** Market makers (dealers) must hedge constantly. "
                "When they hold more calls than puts near current price, they BUY every dip — "
                "the market stays calm. When they hold more puts, they SELL every rally — "
                "bigger swings, more fear. \U0001f7e2 Positive GEX = stable. \U0001f534 Negative GEX = wild swings."
            )
            try:
                _df_gex = pd.read_sql(
                    "SELECT ticker, strike, openInt_Call_now, openInt_Put_now "
                    "FROM options_change WHERE trade_date_now=? LIMIT 3000", conn, params=[_td])
                _df_sp  = pd.read_sql(
                    "SELECT ticker, close AS spot FROM stock_daily WHERE trade_date=?", conn, params=[_td2])
                _df_gex = _df_gex.merge(_df_sp, on="ticker", how="left").dropna(subset=["spot"])
                _df_gex["w"] = np.exp(-0.5*((_df_gex["strike"]-_df_gex["spot"])/(_df_gex["spot"]*0.05))**2)
                _df_gex["gex"] = _df_gex["w"]*(_df_gex["openInt_Call_now"]-_df_gex["openInt_Put_now"])
                _gex_sum = _df_gex.groupby("ticker")["gex"].sum().reset_index()
                _gex_sum.columns = ["Ticker","GEX"]
                _pos = (_gex_sum["GEX"] > 0).sum()
                _tot = len(_gex_sum)
                _pct = _pos/_tot*100 if _tot else 0
                if _pct >= 60:
                    st.success(f"\U0001f7e2 STABILIZING — {_pct:.0f}% of tickers have positive GEX. Dealers will buy dips. Expect calmer markets.")
                    signals.append(("Dealer GEX", 2, 2))
                elif _pct <= 40:
                    st.error(f"\U0001f534 AMPLIFYING — {100-_pct:.0f}% of tickers have negative GEX. Expect bigger swings in both directions.")
                    signals.append(("Dealer GEX", 0, 2))
                else:
                    st.warning(f"\U0001f7e1 MIXED — {_pct:.0f}% positive GEX. No dominant stabilizer. Markets may be unpredictable.")
                    signals.append(("Dealer GEX", 1, 2))
                st.dataframe(_gex_sum.sort_values("GEX", ascending=False).head(15),
                             use_container_width=True, hide_index=True)
            except Exception as _ex:
                st.warning(f"GEX unavailable: {_ex}")
                signals.append(("Dealer GEX", 0, 2))

        # ── 5. OIL CRASH SIGNAL ───────────────────────────────────────────────────────
        with st.expander("\U0001f6e2️ 5. Oil Crash Signal", expanded=True):
            st.caption(
                "\U0001f4a1 **What it means:** Oil rising 100%+ in 12 months has preceded EVERY major crash since 1987: "
                "1990, 2000, 2008, 2022, and the 2026 Iran War. This is one of the most reliable macro early-warning signals. "
                "**Below 100% = safer zone. Above 100% = reduce risk immediately.**"
            )
            try:
                _oil_hist = yf.Ticker("CL=F").history(period="14mo")["Close"].dropna()
                _oil_now  = float(_oil_hist.iloc[-1]) if len(_oil_hist) else 0
                if len(_oil_hist) >= 250:
                    _roc = (_oil_hist.iloc[-1]/_oil_hist.iloc[-252]-1)*100
                elif len(_oil_hist) >= 2:
                    _roc = (_oil_hist.iloc[-1]/_oil_hist.iloc[0]-1)*100
                else:
                    _roc = 0
                _c1, _c2, _c3 = st.columns(3)
                _c1.metric("Oil Price (CL=F)", f"${_oil_now:.1f}")
                _c2.metric("12-Month Change", f"{_roc:+.1f}%")
                _c3.metric("Distance from 100% threshold", f"{_roc-100:+.1f}%", delta_color="inverse")
                if _roc >= 100:
                    st.error("\U0001f6a8 **CRASH WARNING ACTIVE** — Oil has risen 100%+ in 12 months. Every crash since 1987 had this signal. Reduce risk NOW. Consider buying SPY put options for protection.")
                    signals.append(("Oil Crash Signal", 2, 2))
                elif _roc >= 60:
                    st.warning("⚠️ **WATCH ZONE** — Oil rising fast. Not at crash threshold yet but approaching. Stay cautious, don't add new long positions.")
                    signals.append(("Oil Crash Signal", 1, 2))
                else:
                    st.success("\U0001f7e2 **SAFE ZONE** — Oil below the crash threshold. This indicator is not flashing any warning.")
                    signals.append(("Oil Crash Signal", 0, 2))
                
                _fig_oil = go.Figure()
                _fig_oil.add_trace(go.Scatter(x=_oil_hist.index, y=_oil_hist.values, name="Oil", line=dict(color="#f0a500", width=2)))
                _fig_oil.update_layout(title="Oil Price — last 14 months", height=240, margin=dict(t=30,b=20,l=10,r=10))
                st.plotly_chart(_fig_oil, use_container_width=True)
            except Exception as _ex:
                st.warning(f"Oil signal unavailable: {_ex}")
                signals.append(("Oil Crash Signal", 0, 2))

        # ── 6. CREDIT WARNING ──────────────────────────────────────────────────────────────
        with st.expander("\U0001f4c9 6. Credit Warning — Bonds Lead Stocks by 2–5 Days", expanded=True):
            st.caption(
                "\U0001f4a1 **What it means:** Bond investors are often smarter money. "
                "When junk bonds (HYG) fall while stocks (SPY) rise, stocks usually follow bonds down within 2–5 days — "
                "like a canary in a coal mine. Also: when VIX > VIX3M, short-term fear > long-term fear = volatility spike coming."
            )
            try:
                _spy5 = yf.Ticker("SPY").history(period="15d")["Close"].dropna()
                _hyg5 = yf.Ticker("HYG").history(period="15d")["Close"].dropna()
                _vix  = yf.Ticker("^VIX").history(period="5d")["Close"].dropna()
                _vix3m= yf.Ticker("^VIX3M").history(period="5d")["Close"].dropna()
                _spy_r = (_spy5.iloc[-1]/_spy5.iloc[-6]-1)*100 if len(_spy5)>=6 else 0
                _hyg_r = (_hyg5.iloc[-1]/_hyg5.iloc[-6]-1)*100 if len(_hyg5)>=6 else 0
                _div = _spy_r - _hyg_r
                _vr  = float(_vix.iloc[-1]/_vix3m.iloc[-1]) if len(_vix) and len(_vix3m) else 1.0
                _c1,_c2,_c3 = st.columns(3)
                _c1.metric("SPY 5-day return", f"{_spy_r:+.2f}%")
                _c2.metric("HYG 5-day return (junk bonds)", f"{_hyg_r:+.2f}%",
                           delta=f"Divergence: {_div:+.2f}%", delta_color="inverse")
                _c3.metric("VIX/VIX3M Ratio", f"{_vr:.2f}",
                           delta="\U0001f6a8 BACKWARDATION" if _vr>1.05 else ("\U0001f7e2 Contango" if _vr<0.95 else "Normal"))
                _sc6 = 0
                if _div >= 2.0:
                    st.error(f"\U0001f6a8 **DIVERGENCE ALERT** — Stocks up {_spy_r:+.1f}% but bonds down {_hyg_r:+.1f}% (5-day). "
                             "Stocks may correct in the next 2–5 days. Consider trimming longs.")
                    _sc6 += 2
                elif _div >= 1.0:
                    st.warning(f"⚠️ Mild divergence (gap {_div:.1f}%). Keep an eye on it — may widen.")
                    _sc6 += 1
                else:
                    st.success("\U0001f7e2 No divergence. Bonds and stocks are moving together — no warning from this signal.")
                if _vr > 1.05:
                    st.error("\U0001f6a8 **VIX BACKWARDATION** — Short-term fear is higher than long-term fear. "
                             "This often precedes a sharp volatility spike within days.")
                    _sc6 += 1
                signals.append(("Credit Lead-Lag", min(_sc6, 3), 3))
            except Exception as _ex:
                st.warning(f"Credit unavailable: {_ex}")
                signals.append(("Credit Lead-Lag", 0, 3))

        # ── 7. MARKET REGIME ───────────────────────────────────────────────────────────────
        with st.expander("\U0001f321️ 7. Market Regime — BULL, BEAR, or CHOPPY?", expanded=True):
            st.caption(
                "\U0001f4a1 **What it means:** Markets go through phases. In BULL phase: buy dips, favor calls. "
                "In BEAR phase: sell rallies, favor puts or cash. In CHOPPY: stay small, no clear edge. "
                "Knowing the regime helps you pick the right strategy."
            )
            try:
                _df_reg = pd.read_sql(
                    "SELECT bull_score, bear_score FROM us_analytics_daily ORDER BY rowid DESC LIMIT 20", conn)
                if not _df_reg.empty:
                    _ab = _df_reg["bull_score"].mean()
                    _ae = _df_reg["bear_score"].mean()
                    _c1,_c2 = st.columns(2)
                    _c1.metric("Avg Bull Score (20 days)", f"{_ab:.1f}", help="Higher = more bullish signals recently")
                    _c2.metric("Avg Bear Score (20 days)", f"{_ae:.1f}", help="Higher = more bearish signals recently")
                    if _ab > _ae * 1.3:
                        st.success("\U0001f7e2 **BULL REGIME** — Buy dips. Favor call options. Momentum is upward. Don't fight the trend.")
                        signals.append(("Market Regime", 0, 2))
                    elif _ae > _ab * 1.3:
                        st.error("\U0001f534 **BEAR REGIME** — Sell rallies. Favor put options or hold cash. Risk is elevated. Don't buy blindly.")
                        signals.append(("Market Regime", 2, 2))
                    else:
                        st.warning("\U0001f7e1 **CHOPPY** — No clear direction. Keep positions small, wait for a confirmed breakout before committing.")
                        signals.append(("Market Regime", 1, 2))
                else:
                    st.info("Regime data not available yet.")
                    signals.append(("Market Regime", 0, 2))
            except Exception as _ex:
                st.warning(f"Regime unavailable: {_ex}")
                signals.append(("Market Regime", 0, 2))

        # ── OVERALL SCORECARD ────────────────────────────────────────────────────────────────────
        st.markdown("---")
        st.markdown("### \U0001f3af Overall Risk Scorecard")
        st.caption(
            "All 7 signals combined into one number. "
            "**Higher score = more warning signs = more caution needed.** "
            "This is your single answer to: “Should I be worried today?”"
        )
        _total_score = sum(s for _, s, _ in signals)
        _total_max   = sum(m for _, _, m in signals)
        _pct_sc = _total_score / _total_max * 100 if _total_max else 0

        _vc1, _vc2 = st.columns([1, 2])
        with _vc1:
            st.metric("Risk Score", f"{_total_score} / {_total_max}", f"{_pct_sc:.0f}% of maximum")
            st.progress(int(_pct_sc))
        with _vc2:
            if _pct_sc >= 60:
                st.error(
                    "\U0001f6a8 **HIGH RISK — Multiple warning systems are active.**\n\n"
                    "What to do: Reduce position sizes. Consider buying SPY puts as insurance. "
                    "Avoid opening new longs until signals cool down. "
                    "This is not a time to be aggressive."
                )
            elif _pct_sc >= 35:
                st.warning(
                    "⚠️ **CAUTION — Some warning signs present.**\n\n"
                    "What to do: Keep positions smaller than usual. "
                    "Avoid large leveraged bets. "
                    "Watch the credit and oil signals — if they worsen, reduce further."
                )
            else:
                st.success(
                    "\U0001f7e2 **CALM — No major warnings active.**\n\n"
                    "What to do: Normal risk-taking is fine. "
                    "You can buy dips with confidence. "
                    "Stay diversified and keep stops in place."
                )

        st.markdown("#### Signal Breakdown")
        _sc_df = pd.DataFrame(
            [(l, s, m, f"{s/m*100:.0f}%") for l, s, m in signals],
            columns=["Signal", "Score", "Max Points", "Risk %"]
        )
        st.dataframe(_sc_df, use_container_width=True, hide_index=True,
                     column_config={"Score": st.column_config.ProgressColumn(format="%d", min_value=0, max_value=3)})
        st.caption("⚠️ Not financial advice. For learning and research only.")

    except Exception as _outer_ex:
        st.error(f"Smart Money Hub error: {_outer_ex}")
    finally:
        conn.close()




# ===================================================================
# ──  GAMMA WALL ADVISOR — Professional advisor + position tracking
# ===================================================================
elif page == "\U0001f3af Gamma Wall Advisor":
    import math as _gmath
    import plotly.graph_objects as _ggo
    from plotly.subplots import make_subplots as _gmsp

    # ── Inline backtest helper (self-contained, no telegram dependency) ──
    def _ga_bt(ticker, conn_inner, wall_mult=2.5, hold=5, min_dist_pct=0.3):
        """Return (trades_df, metrics_dict) with expectancy + far-OTM subset stats."""
        _dfp = pd.read_sql(
            "SELECT trade_date, close FROM stock_daily WHERE ticker=? "
            "ORDER BY substr(trade_date,7,4)||substr(trade_date,1,2)||substr(trade_date,4,2)",
            conn_inner, params=(ticker,))
        _dfp["close"] = pd.to_numeric(_dfp["close"], errors="coerce")
        _dfp = _dfp.dropna().reset_index(drop=True)
        _dfoi = pd.read_sql(
            "SELECT trade_date_now, strike, openInt_Call_now FROM options_change "
            "WHERE ticker=? AND openInt_Call_now > 0",
            conn_inner, params=(ticker,))
        _dfoi["openInt_Call_now"] = pd.to_numeric(_dfoi["openInt_Call_now"], errors="coerce")
        _dfoi = _dfoi.dropna()
        trades = []
        for _dt, _grp in _dfoi.groupby("trade_date_now"):
            if len(_grp) < 5:
                continue
            _avg = _grp["openInt_Call_now"].mean()
            _w = _grp[_grp["openInt_Call_now"] >= _avg * wall_mult]
            if _w.empty:
                continue
            _cw = float(_w.sort_values("openInt_Call_now", ascending=False)["strike"].iloc[0])
            _wstr = float(_w["openInt_Call_now"].max()) / _avg
            _pr = _dfp[_dfp["trade_date"] == _dt]
            if _pr.empty:
                continue
            _i = _pr.index[0]
            _spot = float(_pr["close"].iloc[0])
            if _spot >= _cw or (_cw - _spot) / _spot * 100 < min_dist_pct:
                continue
            if _i + hold >= len(_dfp):
                continue
            _fh = float(_dfp["close"].iloc[_i + 1 : _i + hold + 1].max())
            _win = _fh < _cw
            _dist = (_cw - _spot) / _spot * 100
            # Simulated P&L: credit = dist * 0.25 (rough), loss = spread_width * 0.75
            _est_credit = _dist * 0.25   # % of spot
            _est_loss   = _est_credit * 3.0  # 1:3 R risk (tastytrade standard)
            _pnl_unit = _est_credit if _win else -_est_loss
            trades.append({
                "date": _dt, "spot": round(_spot, 2), "call_wall": round(_cw, 2),
                "wall_str": round(_wstr, 1), "dist_pct": round(_dist, 2),
                "future_high": round(_fh, 2), "win": _win,
                "est_credit_pct": round(_est_credit, 3),
                "pnl_unit": round(_pnl_unit, 3),
                "ticker": ticker,
            })
        t = pd.DataFrame(trades)
        if t.empty or len(t) < 3:
            return t, {}
        _wr = t["win"].mean()
        _wins = t[t["win"]]
        _losses = t[~t["win"]]
        _avg_c = t["est_credit_pct"].mean()
        _avg_l = _avg_c * 3.0
        _expectancy = round((_wr * _avg_c) - ((1 - _wr) * _avg_l), 4)  # % per trade
        # Far OTM subset: dist >= 2.0%
        _t_far = t[t["dist_pct"] >= 2.0]
        _far_wr = round(_t_far["win"].mean() * 100, 1) if len(_t_far) >= 3 else None
        m = {
            "trades": len(t), "win_rate": round(_wr * 100, 1),
            "avg_dist": round(t["dist_pct"].mean(), 2),
            "avg_str":  round(t["wall_str"].mean(), 1),
            "expectancy_pct": _expectancy,
            "far_otm_wr": _far_wr,
            "far_otm_n": len(_t_far),
        }
        return t, m

    # ── DB setup ──────────────────────────────────────────────────────
    _ga_conn = get_conn()
    _ga_conn.execute("""
        CREATE TABLE IF NOT EXISTS gamma_wall_trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT NOT NULL, trade_date TEXT NOT NULL, expiry TEXT NOT NULL,
            short_strike REAL NOT NULL, long_strike REAL NOT NULL,
            spread_type TEXT NOT NULL, credit REAL NOT NULL, quantity INTEGER DEFAULT 1,
            wall_strength REAL, dist_to_wall_pct REAL, spot_at_entry REAL,
            gex_regime TEXT, advisor_score INTEGER, advisor_notes TEXT,
            exit_date TEXT, exit_price REAL, exit_reason TEXT,
            pnl_dollar REAL, pnl_pct REAL, status TEXT DEFAULT 'OPEN',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP)""")
    _ga_conn.commit()

    # ── Latest DB dates ───────────────────────────────────────────────
    _ga_td  = _ga_conn.execute(
        "SELECT trade_date_now FROM options_change ORDER BY "
        "substr(trade_date_now,7,4)||substr(trade_date_now,1,2)||substr(trade_date_now,4,2) DESC LIMIT 1"
    ).fetchone()
    _ga_today = _ga_td[0] if _ga_td else datetime.now().strftime("%m-%d-%Y")
    _ga_td2 = _ga_conn.execute(
        "SELECT trade_date FROM stock_daily ORDER BY "
        "substr(trade_date,7,4)||substr(trade_date,1,2)||substr(trade_date,4,2) DESC LIMIT 1"
    ).fetchone()
    _ga_today2 = _ga_td2[0] if _ga_td2 else _ga_today
    _ga_all_tickers = sorted([r[0] for r in _ga_conn.execute(
        "SELECT DISTINCT ticker FROM stock_daily ORDER BY ticker").fetchall()])

    # ── Page header ───────────────────────────────────────────────────
    st.markdown("""
<div>
<h2>\U0001f3af Gamma Wall Advisor</h2>
<p>
Professional options desk — dealer GEX analysis, expiry-level walls, position tracking &amp; P&amp;L
</p></div>""", unsafe_allow_html=True)

    # ── Tabs ──────────────────────────────────────────────────────────
    _tab_scan, _tab_all, _tab_exp, _tab_pos, _tab_log, _tab_rules = st.tabs([
        "\U0001f50d Advisor Scan", "\U0001f30e All-Ticker Scanner",
        "\U0001f4c5 Expiry Map", "\U0001f4ca My Positions",
        "\U0001f4dd Log Trade", "\U0001f4da Rules"
    ])

    # ════════════════════════════════════════════════════════════════
    # TAB 1: ADVISOR SCAN — single ticker deep dive
    # ════════════════════════════════════════════════════════════════
    with _tab_scan:
        # ── Row 1: Ticker + Expiry selector (always visible) ──────────
        _r1a, _r1b, _r1c = st.columns([2, 2, 1])
        with _r1a:
            _ga_sel = st.selectbox("Select ticker for full advisor report",
                                   _ga_all_tickers,
                                   index=_ga_all_tickers.index("SPY") if "SPY" in _ga_all_tickers else 0,
                                   key="ga_sel_adv")

        # Load OI silently to populate expiry dropdown (lightweight query)
        _ga_oi_meta = pd.read_sql(
            "SELECT DISTINCT expiry_date FROM options_change "
            "WHERE ticker=? AND trade_date_now=? AND (openInt_Call_now>0 OR openInt_Put_now>0)",
            _ga_conn, params=(_ga_sel, _ga_today))
        def _ga_exp_sort(d):
            try:
                p = str(d).split("-"); return (int(p[2]), int(p[0]), int(p[1]))
            except Exception:
                return (9999, 99, 99)
        _all_ga_exps = sorted(_ga_oi_meta["expiry_date"].tolist(), key=_ga_exp_sort)
        try:
            import datetime as _dmod_ga
            _ref_ga = _dmod_ga.datetime.strptime(_ga_today, "%m-%d-%Y").date()
            _fut_ga = [e for e in _all_ga_exps
                       if _dmod_ga.date(int(e.split("-")[2]),int(e.split("-")[0]),int(e.split("-")[1])) >= _ref_ga]
            _pst_ga = [e for e in _all_ga_exps if e not in _fut_ga]
        except Exception:
            _fut_ga = _all_ga_exps; _pst_ga = []
        _ga_exp_labels = (["📊 All Expiries (Aggregate)"] +
                          [f"🟢 {e}" for e in _fut_ga] +
                          [f"🔴 {e} (expired)" for e in _pst_ga])
        with _r1b:
            _sel_ga_exp_lbl = st.selectbox(
                "📅 Select Expiry",
                _ga_exp_labels, index=0, key="ga_wall_exp",
                help="All Expiries = aggregate gamma wall across full chain. "
                     "Select a specific expiry to filter ALL charts, tables and signals to that cycle only."
            )
        with _r1c:
            st.markdown("<br>", unsafe_allow_html=True)
            _ga_run = st.button("🎯 Analyze", type="primary", use_container_width=True, key="ga_run_adv")

        # Parse selected expiry
        _sel_ga_expiry = None
        if _sel_ga_exp_lbl and _sel_ga_exp_lbl != "📊 All Expiries (Aggregate)":
            _sel_ga_expiry = _sel_ga_exp_lbl.replace("🟢 ","").replace("🔴 ","").replace(" (expired)","")
        _wall_mode_label = f"Expiry: {_sel_ga_expiry}" if _sel_ga_expiry else "All Expiries (Aggregate)"

        if _sel_ga_expiry:
            st.info(f"📅 Showing all analysis for **{_ga_sel}** — Expiry **{_sel_ga_expiry}** "
                    f"({[e for e in _fut_ga if e==_sel_ga_expiry and True] and 'Future' or 'Expired'})",
                    icon="📅")

        if _ga_run:
            with st.spinner(f"Deep analysis for {_ga_sel} — {_wall_mode_label}…"):
                # ── Load full OI data ──────────────────────────────────
                _ga_oi = pd.read_sql(
                    "SELECT strike, expiry_date, openInt_Call_now, openInt_Put_now "
                    "FROM options_change WHERE ticker=? AND trade_date_now=? "
                    "AND (openInt_Call_now>0 OR openInt_Put_now>0)",
                    _ga_conn, params=(_ga_sel, _ga_today))
                for _c in ["strike","openInt_Call_now","openInt_Put_now"]:
                    _ga_oi[_c] = pd.to_numeric(_ga_oi[_c], errors="coerce").fillna(0)

                _spot_r = _ga_conn.execute(
                    "SELECT close, pcr_oi FROM stock_daily WHERE ticker=? AND trade_date=?",
                    (_ga_sel, _ga_today2)).fetchone()
                _spot = float(_spot_r[0]) if _spot_r else None
                _pcr  = float(_spot_r[1]) if _spot_r and _spot_r[1] else 1.0

                if not _spot or _ga_oi.empty:
                    st.warning(f"No data for {_ga_sel} on {_ga_today}. Try a different ticker.")
                else:
                    # ── Build working OI frame (single expiry OR aggregate) ──
                    if _sel_ga_expiry:
                        _ga_oi_work = _ga_oi[_ga_oi["expiry_date"] == _sel_ga_expiry].copy()
                        if _ga_oi_work.empty:
                            st.warning(f"No data for {_ga_sel} at expiry {_sel_ga_expiry}.")
                            st.stop()
                    else:
                        # Aggregate: sum OI across all expiries per strike
                        _ga_oi_agg = _ga_oi.groupby("strike", as_index=False).agg(
                            openInt_Call_now=("openInt_Call_now", "sum"),
                            openInt_Put_now=("openInt_Put_now", "sum"),
                        )
                        _dom_exp = (
                            _ga_oi.sort_values("openInt_Call_now", ascending=False)
                                  .drop_duplicates(subset=["strike"])[["strike","expiry_date"]]
                        )
                        _ga_oi_work = _ga_oi_agg.merge(_dom_exp, on="strike", how="left")

                    st.caption(f"📊 Mode: **{_wall_mode_label}** | {len(_fut_ga)} future expiries, "
                               f"{len(_pst_ga)} expired | Snapshot: {_ga_today}")

                    _avg_c = _ga_oi_work["openInt_Call_now"].mean()
                    _avg_p = _ga_oi_work["openInt_Put_now"].mean()

                    _walls = compute_walls(_ga_oi_work, _spot)
                    _cw  = _walls["call_wall"]
                    _pw  = _walls["put_wall"]
                    _cws = _walls["call_wall_strength"]
                    _pws = _walls["put_wall_strength"]

                    # Expiry label shown in metrics and strategy cards
                    if _sel_ga_expiry:
                        _cw_expiry = _sel_ga_expiry
                        _pw_expiry = _sel_ga_expiry
                    else:
                        # Aggregate: show dominant expiry per wall strike
                        def _dom_exp_for(strike, col):
                            _r = _ga_oi[_ga_oi["strike"] == strike].sort_values(col, ascending=False).head(1)
                            return _r["expiry_date"].iloc[0] if not _r.empty else "—"
                        _cw_expiry = _dom_exp_for(_cw, "openInt_Call_now") if _cw else "—"
                        _pw_expiry = _dom_exp_for(_pw, "openInt_Put_now")   if _pw else "—"

                    _cw_above = bool(_cw and _cw >= _spot)
                    _pw_below = bool(_pw and _pw <= _spot)
                    _gex = "POSITIVE" if _pcr < 1.2 else "NEGATIVE"

                    # ── Metrics strip ──────────────────────────────────
                    _mc = st.columns(5)
                    _mc[0].metric("Spot", f"${_spot:.2f}")
                    if _cw:
                        _cw_dist = (_cw - _spot) / _spot * 100
                        _cw_lbl = f"Call Wall ({'↑ Ceiling' if _cw_above else '↓ Broken'})"
                        _mc[1].metric(_cw_lbl, f"${_cw:.0f}",
                                      delta=f"{_cw_dist:+.1f}% | {_cws:.1f}× OI | exp {_cw_expiry}",
                                      delta_color="normal" if _cw_above else "inverse")
                    if _pw:
                        _pw_dist = (_pw - _spot) / _spot * 100
                        _pw_lbl = f"Put Wall ({'↓ Floor' if _pw_below else '↑ Broken'})"
                        _mc[2].metric(_pw_lbl, f"${_pw:.0f}",
                                      delta=f"{_pw_dist:+.1f}% | {_pws:.1f}× OI | exp {_pw_expiry}",
                                      delta_color="normal" if _pw_below else "inverse")
                    _mc[3].metric("PCR", f"{_pcr:.2f}",
                                  delta="✅ Normal" if _pcr < 1.5 else "⚠️ Elevated")
                    _mc[4].metric("GEX", _gex,
                                  delta="✅ Dampening" if _gex=="POSITIVE" else "⚠️ Amplifying")

                    # ── Expiry Wall Summary Table + Chart ──────────────
                    st.markdown("---")
                    st.markdown("#### 📅 Call & Put Wall by Expiry")
                    st.caption("Each expiry's dominant wall strike — the level with the highest OI concentration. "
                               "🟡 = selected wall | 🟢 = call wall above spot | 🔴 = put wall below spot")

                    _wall_rows = []
                    for _ex in _fut_ga:          # future expiries only
                        _eg = _ga_oi[_ga_oi["expiry_date"] == _ex].copy()
                        if len(_eg) < 3:
                            continue

                        _ex_walls  = compute_walls(_eg, _spot)
                        _cw_ex     = _ex_walls["call_wall"]
                        _pw_ex     = _ex_walls["put_wall"]
                        _cw_oi_ex  = _ex_walls["call_wall_oi"]
                        _pw_oi_ex  = _ex_walls["put_wall_oi"]
                        _cw_str_ex = _ex_walls["call_wall_strength"]
                        _pw_str_ex = _ex_walls["put_wall_strength"]

                        try:
                            _ep = _ex.split("-")
                            _ed = _dmod_ga.date(int(_ep[2]), int(_ep[0]), int(_ep[1]))
                            _dte_ex = (_ed - _ref_ga).days
                        except Exception:
                            _dte_ex = -1

                        _is_sel = (_ex == _sel_ga_expiry) if _sel_ga_expiry else False
                        _zone = ("✅ IDEAL" if 21 <= _dte_ex <= 50 else
                                 "⚡ NEAR" if 0 < _dte_ex < 21 else
                                 "📌 FAR"  if _dte_ex > 50 else "—")

                        _wall_rows.append({
                            "Expiry":       _ex,
                            "DTE":          _dte_ex,
                            "Zone":         _zone,
                            "Call Wall $":  _cw_ex,
                            "CW Dist %":    round((_cw_ex - _spot) / _spot * 100, 1) if _cw_ex else None,
                            "CW Strength":  f"{_cw_str_ex:.1f}×",
                            "CW OI":        int(_cw_oi_ex),
                            "Put Wall $":   _pw_ex,
                            "PW Dist %":    round((_pw_ex - _spot) / _spot * 100, 1) if _pw_ex else None,
                            "PW Strength":  f"{_pw_str_ex:.1f}×",
                            "PW OI":        int(_pw_oi_ex),
                            "Selected":     _is_sel,
                        })

                    if _wall_rows:
                        _wdf = pd.DataFrame(_wall_rows)

                        # Colour rows
                        def _wall_row_style(row):
                            if row.get("Selected"):
                                return ["background-color:#3a3000; color:#FFD700; font-weight:700"] * len(row)
                            if row["DTE"] <= 7:
                                return ["background-color:#2a0a0a; color:#ffcccc"] * len(row)
                            if 21 <= row["DTE"] <= 50:
                                return ["background-color:#0a2a0a; color:#ccffcc"] * len(row)
                            return [""] * len(row)

                        _disp_cols = ["Expiry","DTE","Zone","Call Wall $","CW Dist %","CW Strength",
                                      "Put Wall $","PW Dist %","PW Strength"]
                        st.dataframe(
                            _wdf[_disp_cols].style.apply(_wall_row_style, axis=1)
                                                   .format({
                                                       "Call Wall $": lambda v: f"${v:.0f}" if v else "—",
                                                       "Put Wall $":  lambda v: f"${v:.0f}" if v else "—",
                                                       "CW Dist %":   lambda v: f"{v:+.1f}%" if v else "—",
                                                       "PW Dist %":   lambda v: f"{v:+.1f}%" if v else "—",
                                                   }),
                            hide_index=True, use_container_width=True,
                        )

                        # ── Wall chart: all expiries side by side ──────
                        _wdf_c = _wdf[_wdf["Call Wall $"].notna() | _wdf["Put Wall $"].notna()].copy()
                        if not _wdf_c.empty:
                            import plotly.graph_objects as _pgo_wall
                            _fig_wall = _pgo_wall.Figure()

                            # Spot line
                            _fig_wall.add_hline(
                                y=_spot,
                                line=dict(color="#FFD700", width=2, dash="solid"),
                                annotation_text=f"SPOT ${_spot:.2f}",
                                annotation_font_color="#FFD700",
                                annotation_bgcolor="rgba(0,0,0,0.6)",
                            )

                            # Call wall dots
                            _cw_valid = _wdf_c[_wdf_c["Call Wall $"].notna()]
                            if not _cw_valid.empty:
                                _fig_wall.add_trace(_pgo_wall.Scatter(
                                    x=_cw_valid["Expiry"],
                                    y=_cw_valid["Call Wall $"],
                                    mode="markers+lines+text",
                                    name="Call Wall",
                                    marker=dict(color="#00e676", size=14, symbol="triangle-up",
                                                line=dict(color="#ffffff", width=1)),
                                    line=dict(color="#00e676", width=1.5, dash="dot"),
                                    text=[f"${v:.0f}" for v in _cw_valid["Call Wall $"]],
                                    textposition="top center",
                                    textfont=dict(color="#00e676", size=11),
                                    hovertemplate="<b>%{x}</b><br>Call Wall: $%{y:.0f}<extra></extra>",
                                ))

                            # Put wall dots
                            _pw_valid = _wdf_c[_wdf_c["Put Wall $"].notna()]
                            if not _pw_valid.empty:
                                _fig_wall.add_trace(_pgo_wall.Scatter(
                                    x=_pw_valid["Expiry"],
                                    y=_pw_valid["Put Wall $"],
                                    mode="markers+lines+text",
                                    name="Put Wall",
                                    marker=dict(color="#ff5252", size=14, symbol="triangle-down",
                                                line=dict(color="#ffffff", width=1)),
                                    line=dict(color="#ff5252", width=1.5, dash="dot"),
                                    text=[f"${v:.0f}" for v in _pw_valid["Put Wall $"]],
                                    textposition="bottom center",
                                    textfont=dict(color="#ff5252", size=11),
                                    hovertemplate="<b>%{x}</b><br>Put Wall: $%{y:.0f}<extra></extra>",
                                ))

                            # Selected expiry marker — use add_shape for categorical x-axis
                            if _sel_ga_expiry:
                                _sel_row = _wdf_c[_wdf_c["Expiry"] == _sel_ga_expiry]
                                if not _sel_row.empty:
                                    # get integer index position of the selected expiry label
                                    _exp_list = _wdf_c["Expiry"].tolist()
                                    if _sel_ga_expiry in _exp_list:
                                        _sel_idx = _exp_list.index(_sel_ga_expiry)
                                        _fig_wall.add_shape(
                                            type="line",
                                            x0=_sel_idx, x1=_sel_idx,
                                            y0=0, y1=1,
                                            xref="x", yref="paper",
                                            line=dict(color="#FFD700", width=2, dash="dash"),
                                        )
                                        _fig_wall.add_annotation(
                                            x=_sel_ga_expiry, y=1.0,
                                            xref="x", yref="paper",
                                            text="▼ Selected",
                                            showarrow=False,
                                            font=dict(color="#FFD700", size=11),
                                            bgcolor="rgba(0,0,0,0.5)",
                                            yanchor="top",
                                        )

                            _fig_wall.update_layout(
                                height=360,
                                title=dict(
                                    text=f"{_ga_sel} — Call & Put Wall per Expiry  |  Spot ${_spot:.2f}",
                                    font=dict(color="#ffffff", size=15, family="Courier New, monospace"),
                                    x=0.02,
                                ),
                                plot_bgcolor="#0e1117",
                                paper_bgcolor="#0e1117",
                                font=dict(family="Courier New, monospace", color="#e0e0e0"),
                                xaxis=dict(
                                    title=dict(text="Expiry Date", font=dict(color="#aaaaaa")),
                                    tickfont=dict(color="#cccccc"),
                                    gridcolor="rgba(255,255,255,0.07)",
                                    tickangle=-30,
                                ),
                                yaxis=dict(
                                    title=dict(text="Strike Price ($)", font=dict(color="#aaaaaa")),
                                    tickfont=dict(color="#cccccc"),
                                    gridcolor="rgba(255,255,255,0.07)",
                                ),
                                legend=dict(
                                    orientation="h", yanchor="bottom", y=1.02,
                                    bgcolor="rgba(0,0,0,0)",
                                    font=dict(color="#e0e0e0"),
                                ),
                                hovermode="x unified",
                                margin=dict(t=65, b=65, l=65, r=20),
                            )
                            st.plotly_chart(_fig_wall, use_container_width=True)
                            st.caption("🟢 ▲ = Call Wall (ceiling)  |  🔴 ▼ = Put Wall (floor)  "
                                       "|  🟡 line = Spot  |  🟡 dashed = selected expiry  "
                                       "|  🟩 table row = ideal 21–50 DTE zone")

                            # ── Chart Assessment ──────────────────────────────────
                            _assess_lines = []
                            _warnings = []   # anomaly flags shown separately

                            # 0. Put wall jump detection — flag unnatural drops
                            _pw_sequence = [(r["Expiry"], r["DTE"], r["Put Wall $"], r["PW Dist %"])
                                            for r in _wall_rows if r["Put Wall $"] is not None]
                            for _pi in range(1, len(_pw_sequence)):
                                _p_prev = _pw_sequence[_pi - 1]
                                _p_curr = _pw_sequence[_pi]
                                _pw_drop = _p_prev[2] - _p_curr[2]   # positive = wall fell
                                _pw_drop_pct = _pw_drop / _spot * 100
                                if _pw_drop_pct >= 5:
                                    # Classify the nature of the drop
                                    _curr_dist = abs(_p_curr[3] or 0)
                                    if _curr_dist >= 20:
                                        _classification = (
                                            "🚨 **TAIL RISK HEDGE** — "
                                            f"${_p_curr[2]:.0f} is {_curr_dist:.1f}% below spot. "
                                            "This is a disaster/black-swan hedge, NOT a normal floor. "
                                            "Institutions buy deep OTM puts far out as portfolio insurance — "
                                            "it does NOT mean price is expected to reach that level. "
                                            "**Ignore as a price target. Use near-term walls for actual support.**"
                                        )
                                    elif _curr_dist >= 10:
                                        _classification = (
                                            "⚠️ **STRUCTURAL PUT HEDGE** — "
                                            f"${_p_curr[2]:.0f} is {_curr_dist:.1f}% below spot. "
                                            "Fund managers buying longer-dated puts further OTM as portfolio protection. "
                                            "Common behaviour — longer DTE puts are cheaper far OTM. "
                                            "Real support is closer to spot in near-term expiries."
                                        )
                                    else:
                                        _classification = (
                                            "📉 **PUT WALL SHIFT** — "
                                            f"Floor dropped ${_pw_drop:.0f} ({_pw_drop_pct:.1f}%) "
                                            f"between {_p_prev[0]} and {_p_curr[0]}. "
                                            "Possible roll of put positions to lower strikes — "
                                            "watch for this expiry's put OI build trend."
                                        )
                                    _warnings.append(
                                        f"**{_p_curr[0]} ({_p_curr[1]}d):** Put wall dropped from "
                                        f"**${_p_prev[2]:.0f}** → **${_p_curr[2]:.0f}** "
                                        f"(−${_pw_drop:.0f}, {_pw_drop_pct:.1f}% from spot). "
                                        + _classification
                                    )

                            # 0b. Call wall jump detection
                            _cw_sequence = [(r["Expiry"], r["DTE"], r["Call Wall $"], r["CW Dist %"])
                                            for r in _wall_rows if r["Call Wall $"] is not None]
                            for _ci in range(1, len(_cw_sequence)):
                                _c_prev = _cw_sequence[_ci - 1]
                                _c_curr = _cw_sequence[_ci]
                                _cw_jump = _c_curr[2] - _c_prev[2]   # positive = wall rose
                                _cw_jump_pct = _cw_jump / _spot * 100
                                if _cw_jump_pct >= 5:
                                    _warnings.append(
                                        f"**{_c_curr[0]} ({_c_curr[1]}d):** Call wall jumped from "
                                        f"**${_c_prev[2]:.0f}** → **${_c_curr[2]:.0f}** "
                                        f"(+${_cw_jump:.0f}, +{_cw_jump_pct:.1f}%). "
                                        "📈 Likely a new block of call hedges/covered calls written at that strike. "
                                        "If it's also far OTM it may be a leveraged upside bet."
                                    )

                            # 1. Near-term wall proximity
                            if _wall_rows:
                                _near = [r for r in _wall_rows if 0 <= r["DTE"] <= 14]
                                if _near:
                                    _nr = _near[0]
                                    _nr_cw = _nr["Call Wall $"]; _nr_pw = _nr["Put Wall $"]
                                    _nr_exp = _nr["Expiry"]
                                    if _nr_cw and _nr_pw:
                                        _rng = _nr_cw - _nr_pw
                                        _rng_pct = _rng / _spot * 100
                                        _assess_lines.append(
                                            f"**⚡ Near-term ({_nr_exp}, {_nr['DTE']}d):** "
                                            f"Price pinned between **${_nr_pw:.0f}** (floor) and **${_nr_cw:.0f}** (ceiling) "
                                            f"— range {_rng_pct:.1f}%. "
                                            + ("Tight range — expect low movement, ideal for premium selling." if _rng_pct < 4
                                               else "Wide range — directional move possible, reduce short premium size.")
                                        )

                            # 2. Ideal zone wall assessment
                            _ideal = [r for r in _wall_rows if 21 <= r["DTE"] <= 50]
                            if _ideal:
                                _ir = _ideal[0]
                                _ir_exp = _ir["Expiry"]; _ir_dte = _ir["DTE"]
                                _ir_cw = _ir["Call Wall $"]; _ir_pw = _ir["Put Wall $"]
                                _ir_cws = _ir["CW Strength"]; _ir_pws = _ir["PW Strength"]
                                _cw_dist_pct = _ir["CW Dist %"] or 0
                                _pw_dist_pct = abs(_ir["PW Dist %"] or 0)
                                _bias = ("BULLISH" if _cw_dist_pct > _pw_dist_pct * 1.2
                                         else "BEARISH" if _pw_dist_pct > _cw_dist_pct * 1.2
                                         else "NEUTRAL")
                                _bias_em = {"BULLISH": "🟢", "BEARISH": "🔴", "NEUTRAL": "⚪"}[_bias]
                                _assess_lines.append(
                                    f"**✅ Ideal zone ({_ir_exp}, {_ir_dte}d):** "
                                    f"Call wall **${_ir_cw:.0f}** ({_cw_dist_pct:+.1f}%, {_ir_cws} OI) | "
                                    f"Put wall **${_ir_pw:.0f}** ({-_pw_dist_pct:+.1f}%, {_ir_pws} OI). "
                                    f"{_bias_em} Bias: **{_bias}** — "
                                    + ("call wall further away, bulls have more room to run." if _bias == "BULLISH"
                                       else "put wall closer to spot, bears are pressing." if _bias == "BEARISH"
                                       else "walls equidistant — mean-reversion / iron condor setup.")
                                )

                            # 3. Wall convergence / divergence across expiries
                            _cw_vals = [r["Call Wall $"] for r in _wall_rows if r["Call Wall $"]]
                            _pw_vals = [r["Put Wall $"]  for r in _wall_rows if r["Put Wall $"]]
                            if len(_cw_vals) >= 2:
                                _cw_trend = _cw_vals[-1] - _cw_vals[0]
                                _pw_trend = _pw_vals[-1] - _pw_vals[0] if len(_pw_vals) >= 2 else 0
                                if abs(_cw_trend) >= 5:
                                    _dir = "rising" if _cw_trend > 0 else "falling"
                                    _assess_lines.append(
                                        f"**📈 Call wall trend:** Wall is **{_dir}** across expiries "
                                        f"(${_cw_vals[0]:.0f} → ${_cw_vals[-1]:.0f}, Δ${_cw_trend:+.0f}). "
                                        + ("Dealers expect price expansion higher long-term." if _cw_trend > 0
                                           else "Dealers compressing ceiling — expect range-bound or lower prices.")
                                    )
                                if len(_pw_vals) >= 2 and abs(_pw_trend) >= 5:
                                    _dir2 = "rising" if _pw_trend > 0 else "falling"
                                    _assess_lines.append(
                                        f"**📉 Put wall trend:** Floor **{_dir2}** across expiries "
                                        f"(${_pw_vals[0]:.0f} → ${_pw_vals[-1]:.0f}, Δ${_pw_trend:+.0f}). "
                                        + ("Floor rising — growing downside protection / put buying." if _pw_trend > 0
                                           else "Floor falling — lower support expected, risk increasing.")
                                    )

                            # 4. Strongest wall overall
                            _all_cw_str = [(r["CW Strength"], r["Expiry"], r["Call Wall $"]) for r in _wall_rows if r["Call Wall $"]]
                            _all_pw_str = [(r["PW Strength"], r["Expiry"], r["Put Wall $"]) for r in _wall_rows if r["Put Wall $"]]
                            if _all_cw_str:
                                try:
                                    _strongest_cw = max(_all_cw_str, key=lambda x: float(str(x[0]).replace("×","")))
                                    _assess_lines.append(
                                        f"**🔒 Strongest call wall:** **${_strongest_cw[2]:.0f}** on {_strongest_cw[1]} "
                                        f"({_strongest_cw[0]} OI) — highest dealer gamma concentration above spot."
                                    )
                                except Exception:
                                    pass
                            if _all_pw_str:
                                try:
                                    _strongest_pw = max(_all_pw_str, key=lambda x: float(str(x[0]).replace("×","")))
                                    _assess_lines.append(
                                        f"**🛡️ Strongest put wall:** **${_strongest_pw[2]:.0f}** on {_strongest_pw[1]} "
                                        f"({_strongest_pw[0]} OI) — strongest dealer support below spot."
                                    )
                                except Exception:
                                    pass

                            # 5. Overall verdict
                            _ideal_cw_d = _ideal[0]["CW Dist %"] if _ideal else None
                            _ideal_pw_d = abs(_ideal[0]["PW Dist %"]) if _ideal and _ideal[0]["PW Dist %"] else None
                            if _ideal_cw_d and _ideal_pw_d:
                                if _ideal_cw_d >= 4 and _ideal_pw_d >= 4:
                                    _verdict = "🎯 **Wide-range setup** — both walls far from spot. High-probability iron condor territory if GEX is positive."
                                elif _ideal_cw_d < 2:
                                    _verdict = "⚠️ **Call wall extremely tight** — price near ceiling. Avoid bullish entries; consider bear call spread."
                                elif _ideal_pw_d < 2:
                                    _verdict = "⚠️ **Put wall extremely tight** — price near floor. Downside risk elevated; consider bear put spread or reduce longs."
                                elif _ideal_cw_d >= 3 and _ideal_pw_d < 2:
                                    _verdict = "🟢 **Bullish setup** — more room above than below. Bull put spread or long call near-term."
                                else:
                                    _verdict = "⚪ **Neutral setup** — walls balanced. Iron condor or sell ATM strangle near ideal expiry."
                                _assess_lines.append(f"\n{_verdict}")

                            # ── Render warnings first (anomalies) ─────────────
                            if _warnings:
                                st.markdown("##### ⚠️ Wall Anomalies Detected")
                                st.caption("These are abnormal jumps in wall levels across expiries — explained below.")
                                for _wn in _warnings:
                                    st.warning(_wn, icon="⚠️")

                            # ── Render assessment ──────────────────────────────
                            if _assess_lines:
                                st.markdown("##### 🧠 Chart Assessment")
                                for _al in _assess_lines:
                                    st.markdown(f"- {_al}")

                            # ── Put wall interpretation guide ──────────────────
                            if any(abs(r["PW Dist %"] or 0) >= 10 for r in _wall_rows if r["PW Dist %"]):
                                with st.expander("📖 Why do put walls move so far OTM for longer expiries?"):
                                    st.markdown("""
**This is normal institutional behaviour — here's why:**

| DTE | Typical Put Wall Distance | Reason |
|-----|--------------------------|--------|
| 0–14d | 0–3% OTM | Dealers and MMs hedge close to spot — real support |
| 15–30d | 2–7% OTM | Fund managers buying near-ATM protection |
| 30–60d | 5–15% OTM | Portfolio hedges — cheap to buy OTM with more time |
| 60–180d | 15–30% OTM | **Tail risk / disaster hedges** — institutions buy far OTM puts as black-swan insurance |

**Key rule:** The further out the expiry, the less the put wall represents a real price floor.
- **Near-term put wall ($370–$375)** → True dealer support. Price likely to bounce here.
- **Mid-term put wall ($350)** → Structural hedge. Meaningful only if price breaks near-term walls.
- **Far-term put wall ($285)** → Tail risk insurance. **NOT a price target.** Treat as irrelevant to normal trading.

**What to focus on:** Use the **nearest future expiry's put wall** as the actionable floor for your trade.
The far-dated deep OTM puts are bought by pension funds and institutions as catastrophic protection — ignore them for intraday/swing trading.
                                    """)

                    else:
                        st.info("Not enough OI data to compute per-expiry walls.")
                    st.markdown("---")

                    # ── Industry-level OI chart ────────────────────────
                    _chart_title = f"Open Interest Map — {_wall_mode_label}"
                    st.markdown(f"#### 📊 {_chart_title}")
                    # Use the working frame (respects expiry filter)
                    _agg = _ga_oi_work.groupby("strike")[["openInt_Call_now","openInt_Put_now"]].sum().reset_index()
                    _view = _agg[(_agg["strike"] >= _spot*0.88) & (_agg["strike"] <= _spot*1.12)]
                    if not _view.empty:
                        _max_oi = max(_view["openInt_Call_now"].max(), _view["openInt_Put_now"].max())
                        # color: highlight walls
                        _call_colors = [
                            "#FFD700" if abs(r["strike"] - (_cw or -1)) < 0.01 else
                            "#26a69a" if r["openInt_Call_now"] >= _avg_c * 2.5 else "#1565C0"
                            for _, r in _view.iterrows()
                        ]
                        _put_colors = [
                            "#FFD700" if abs(r["strike"] - (_pw or -1)) < 0.01 else
                            "#e57373" if r["openInt_Put_now"] >= _avg_p * 2.5 else "#c62828"
                            for _, r in _view.iterrows()
                        ]
                        _fig_oi = _ggo.Figure()
                        _fig_oi.add_trace(_ggo.Bar(
                            x=_view["strike"], y=_view["openInt_Call_now"],
                            name="Call OI", marker_color=_call_colors,
                            hovertemplate="Strike: $%{x}<br>Call OI: %{y:,.0f}<extra></extra>",
                            opacity=0.9))
                        _fig_oi.add_trace(_ggo.Bar(
                            x=_view["strike"], y=-_view["openInt_Put_now"],
                            name="Put OI", marker_color=_put_colors,
                            hovertemplate="Strike: $%{x}<br>Put OI: %{y:,.0f}<extra></extra>",
                            opacity=0.9))
                        # Key level lines
                        _fig_oi.add_vline(x=_spot, line=dict(color="#FFD700", width=2, dash="solid"),
                            annotation=dict(text=f"<b>SPOT ${_spot:.2f}</b>", font_color="#1565C0",
                                          bgcolor="rgba(255,255,255,0.85)", bordercolor="#f0a500", y=1.05))
                        if _cw:
                            _fig_oi.add_vline(x=_cw, line=dict(color="#00e676", width=2, dash="dash"),
                                annotation=dict(text=f"<b>CALL WALL ${_cw:.0f}</b>",
                                              font_color="#00e676", bgcolor="rgba(255,255,255,0.85)", y=0.95))
                        if _pw:
                            _fig_oi.add_vline(x=_pw, line=dict(color="#ff5252", width=2, dash="dash"),
                                annotation=dict(text=f"<b>PUT WALL ${_pw:.0f}</b>",
                                              font_color="#ff5252", bgcolor="rgba(255,255,255,0.85)", y=0.85))
                        _fig_oi.update_layout(
                            barmode="overlay", height=420,
                            plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
                            font=dict(family="Courier New, monospace"),
                            title=dict(text=f"{_ga_sel} — OI Structure ({_ga_today})",
                                      font=dict(color="#1565C0", size=16)),
                            xaxis=dict(title="Strike Price", gridcolor="rgba(0,0,0,0.08)", showspikes=True,
                                      spikecolor="#FFD700", spikethickness=1),
                            yaxis=dict(title="↑ Call OI   |   Put OI ↓",
                                      gridcolor="rgba(0,0,0,0.08)", zeroline=True, zerolinecolor="rgba(0,0,0,0.2)"),
                            legend=dict(orientation="h", yanchor="bottom", y=1.02,
                                       bgcolor="rgba(0,0,0,0)"),
                            hovermode="x unified",
                            margin=dict(t=60, b=40, l=60, r=20)
                        )
                        st.plotly_chart(_fig_oi, use_container_width=True)
                        st.caption("\U0001f7e1 Gold = strongest wall   \U0001f7e2 Teal = call wall zone   \U0001f534 Red = put wall zone   White line = current spot")

                    # ── Volume-Gamma Pairing ───────────────────────────
                    st.markdown("---")
                    st.markdown("#### \U0001f4ca Volume-Gamma Conviction")
                    st.caption("Volume at price = what institutions actually traded. When VPOC aligns with a gamma wall, price has TWO reasons to stall — dealer hedging AND real supply/demand. Strongest sell zone.")
                    _vol_score = 0; _vol_notes = []
                    try:
                        _vg_raw = yf.download(_ga_sel, period="22d", interval="1d",
                                              progress=False, auto_adjust=True)
                        if not _vg_raw.empty and len(_vg_raw) >= 5:
                            _vg_df = _vg_raw.copy()
                            if isinstance(_vg_df.columns, pd.MultiIndex):
                                _vg_df.columns = _vg_df.columns.get_level_values(0)
                            _vg_df = _vg_df.dropna(subset=["Close", "High", "Low", "Volume"])
                            # VRVP: 30-bin volume profile
                            _pmin = float(_vg_df["Low"].min())
                            _pmax = float(_vg_df["High"].max())
                            _bins = np.linspace(_pmin, _pmax, 31)
                            _bctrs = (_bins[:-1] + _bins[1:]) / 2
                            _bvol  = np.zeros(30)
                            for _, _rr in _vg_df.iterrows():
                                _lo, _hi, _dv = float(_rr["Low"]), float(_rr["High"]), float(_rr["Volume"])
                                _rng = _hi - _lo
                                if _rng <= 0: continue
                                for _bi in range(30):
                                    _ov = max(0, min(_hi, _bins[_bi+1]) - max(_lo, _bins[_bi]))
                                    _bvol[_bi] += _dv * (_ov / _rng)
                            _poc_i = int(np.argmax(_bvol))
                            _poc_p = _bctrs[_poc_i]
                            # Value Area (70%)
                            _tv = _bvol.sum()
                            _vai = []; _vav = 0
                            for _ii in np.argsort(_bvol)[::-1]:
                                _vav += _bvol[_ii]; _vai.append(_ii)
                                if _vav >= _tv * 0.70: break
                            _vah = _bctrs[max(_vai)]; _val = _bctrs[min(_vai)]
                            # Volume signals → score
                            if _cw and abs(_poc_p - _cw) / max(_spot, 1) < 0.018:
                                _vol_score += 1
                                _vol_notes.append(f"VPOC ${_poc_p:.2f} aligns with call wall ${_cw:.0f} — double magnet ceiling \U0001f7e1")
                            if _pw and abs(_poc_p - _pw) / max(_spot, 1) < 0.018:
                                _vol_score += 1
                                _vol_notes.append(f"VPOC ${_poc_p:.2f} aligns with put wall ${_pw:.0f} — double magnet floor \U0001f7e1")
                            if _val < _spot < _vah:
                                _vol_score += 1
                                _vol_notes.append(f"Spot ${_spot:.2f} inside Value Area [{_val:.2f}–{_vah:.2f}] — mean-reversion zone, premium selling favoured ✅")
                            else:
                                _vol_notes.append(f"Spot outside Value Area [{_val:.2f}–{_vah:.2f}] — trending; reduce size ⚠️")
                            _avg_v20 = float(_vg_df["Volume"].mean())
                            _tod_v   = float(_vg_df["Volume"].iloc[-1])
                            _vrat    = _tod_v / _avg_v20 if _avg_v20 > 0 else 1.0
                            if _vrat > 1.3:
                                _vol_notes.append(f"Today volume {_vrat:.1f}× 20d avg — high-activity day, walls more likely to hold")
                            elif _vrat < 0.7:
                                _vol_notes.append(f"Today volume {_vrat:.1f}× 20d avg — low liquidity, walls may be softer")
                            if _cw and _cw_above:
                                _hold_pct = float((_vg_df["Close"] < _cw).sum()) / len(_vg_df) * 100
                                if _hold_pct >= 75:
                                    _vol_score = min(_vol_score + 1, 3)
                                    _vol_notes.append(f"Price closed below call wall {_hold_pct:.0f}% of last {len(_vg_df)} sessions — wall holding well ✅")
                                elif _hold_pct < 50:
                                    _vol_notes.append(f"Price closed above call wall {100-_hold_pct:.0f}% of recent sessions — structurally weak ⚠️")
                            # Chart: Candlestick + vol bars | VRVP side by side
                            _vg_fig = make_subplots(rows=2, cols=2,
                                row_heights=[0.7, 0.3], column_widths=[0.70, 0.30],
                                shared_yaxes="columns", shared_xaxes=False,
                                subplot_titles=[f"{_ga_sel} Price + Volume (20d)", "Volume Profile (VRVP)", "Volume Bars", ""],
                                vertical_spacing=0.04, horizontal_spacing=0.03)
                            _vg_idx = _vg_df.reset_index()
                            _dc = "Date" if "Date" in _vg_idx.columns else _vg_idx.columns[0]
                            _vcols = ["#26a69a" if c >= o else "#ef5350"
                                      for c, o in zip(_vg_idx["Close"], _vg_idx["Open"])]
                            _vg_fig.add_trace(go.Candlestick(
                                x=_vg_idx[_dc], open=_vg_idx["Open"], high=_vg_idx["High"],
                                low=_vg_idx["Low"], close=_vg_idx["Close"],
                                increasing_line_color="#26a69a", decreasing_line_color="#ef5350",
                                name="Price"), row=1, col=1)
                            _vg_fig.add_trace(go.Bar(
                                x=_vg_idx[_dc], y=_vg_idx["Volume"],
                                marker_color=_vcols, opacity=0.5, name="Volume"), row=2, col=1)
                            # Wall hlines on candle chart
                            for _hl_y, _hl_c, _hl_t in [
                                (_spot, "#FFD700", f"SPOT ${_spot:.2f}"),
                                (_poc_p, "#FF8C00", f"VPOC ${_poc_p:.2f}"),
                                (_cw, "#00e676", f"CALL WALL ${_cw:.0f}" if _cw else None),
                                (_pw, "#ff5252", f"PUT WALL ${_pw:.0f}" if _pw else None),
                                (_vah, "#4fc3f7", f"VAH ${_vah:.2f}"),
                                (_val, "#4fc3f7", f"VAL ${_val:.2f}"),
                            ]:
                                if _hl_y is None or _hl_t is None: continue
                                _vg_fig.add_hline(y=_hl_y, row=1, col=1,
                                    line=dict(color=_hl_c, width=1.2,
                                              dash="solid" if _hl_y in (_spot, _poc_p) else "dash"),
                                    annotation=dict(text=_hl_t, font_color=_hl_c,
                                                    bgcolor="rgba(255,255,255,0.85)", xanchor="right", font_size=10))
                            # VRVP bars (horizontal)
                            _vrvp_c = ["#FFD700" if _i == _poc_i else
                                       "#26a69a" if _bctrs[_i] >= _val and _bctrs[_i] <= _vah
                                       else "#1a3a5c" for _i in range(30)]
                            _vg_fig.add_trace(go.Bar(
                                x=_bvol, y=_bctrs, orientation="h",
                                marker_color=_vrvp_c, opacity=0.88, name="Vol Profile",
                                hovertemplate="$%{y:.2f}: %{x:,.0f}<extra></extra>"), row=1, col=2)
                            for _hl_y2, _hl_c2 in [(_cw, "#00e676"), (_pw, "#ff5252"),
                                                    (_poc_p, "#FF8C00"), (_spot, "#FFD700")]:
                                if _hl_y2 is None: continue
                                _vg_fig.add_hline(y=_hl_y2, row=1, col=2,
                                    line=dict(color=_hl_c2, width=1, dash="dash"))
                            _vg_fig.update_layout(
                                height=500, showlegend=False,
                                plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
                                font=dict(family="Courier New, monospace"),
                                xaxis=dict(gridcolor="rgba(0,0,0,0.08)", showspikes=True, spikecolor="#FFD700"),
                                yaxis=dict(gridcolor="rgba(0,0,0,0.08)", title="Price"),
                                xaxis2=dict(gridcolor="rgba(0,0,0,0.08)", title="Volume"),
                                xaxis3=dict(gridcolor="rgba(0,0,0,0.08)", title="Date"),
                                yaxis3=dict(gridcolor="rgba(0,0,0,0.08)"),
                                margin=dict(t=50, b=40, l=60, r=20),
                                hovermode="x unified")
                            st.plotly_chart(_vg_fig, use_container_width=True)
                            st.caption(f"\U0001f7e1 VPOC ${_poc_p:.2f}  \U0001f7e2 VAH ${_vah:.2f} / VAL ${_val:.2f}  \U0001f535 Value Area (70% vol)  | Today vol {_vrat:.1f}× avg")
                            # OI build trend at wall strike
                            if _cw:
                                _oib = pd.read_sql(
                                    "SELECT trade_date_now AS dt, SUM(openInt_Call_now) AS oi "
                                    "FROM options_change WHERE ticker=? AND ABS(strike-?)<1.5 "
                                    "GROUP BY trade_date_now "
                                    "ORDER BY substr(trade_date_now,7,4)||substr(trade_date_now,1,2)||substr(trade_date_now,4,2) DESC LIMIT 10",
                                    _ga_conn, params=(_ga_sel, _cw))
                                if len(_oib) >= 3:
                                    _oib = _oib.iloc[::-1].reset_index(drop=True)
                                    _oib_trend = "building" if float(_oib["oi"].iloc[-1]) > float(_oib["oi"].iloc[0]) else "declining"
                                    if _oib_trend == "building":
                                        _vol_score = min(_vol_score + 1, 3)
                                        _vol_notes.append(f"Call OI at ${_cw:.0f}: {_oib['oi'].iloc[0]:,.0f} → {_oib['oi'].iloc[-1]:,.0f} (BUILDING — institutions accumulating) ✅")
                                    else:
                                        _vol_notes.append(f"Call OI at ${_cw:.0f}: {_oib['oi'].iloc[0]:,.0f} → {_oib['oi'].iloc[-1]:,.0f} (DECLINING — wall weakening) ⚠️")
                                    _fig_oib = go.Figure()
                                    _fig_oib.add_trace(go.Scatter(
                                        x=_oib["dt"], y=_oib["oi"], mode="lines+markers",
                                        line=dict(color="#FFD700", width=2),
                                        marker=dict(color="#FFD700", size=7),
                                        fill="tozeroy", fillcolor="rgba(255,215,0,0.08)",
                                        hovertemplate="%{x}<br>OI: %{y:,.0f}<extra></extra>",
                                        name="Call OI at wall"))
                                    _fig_oib.update_layout(
                                        height=200,
                                        title=dict(text=f"Call OI at ${_cw:.0f} Strike — {len(_oib)}-Session Trend ({_oib_trend.upper()})",
                                                   font=dict(color="#1565C0", size=13)),
                                        plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
                                        font=dict(family="Courier New, monospace"),
                                        xaxis=dict(gridcolor="rgba(0,0,0,0.08)"),
                                        yaxis=dict(gridcolor="rgba(0,0,0,0.08)", title="Open Interest"),
                                        margin=dict(t=35, b=30, l=60, r=20))
                                    st.plotly_chart(_fig_oib, use_container_width=True)
                            # Volume-Gamma insight summary
                            if _vol_notes:
                                st.markdown("**\U0001f4ca Volume-Gamma Signals:**")
                                for _vn in _vol_notes:
                                    st.markdown(f"- {_vn}")
                    except Exception as _vge:
                        st.caption(f"Volume data unavailable: {_vge}")

                    # ── Historical backtest ───────────────────────────
                    _ht, _hm = _ga_bt(_ga_sel, _ga_conn, wall_mult=2.5, hold=5)
                    _hist_wr = _hm.get("win_rate", 0) if _hm else 0
                    _hist_n  = _hm.get("trades", 0)  if _hm else 0

                    # ── Conviction scoring ─────────────────────────────
                    _score = 0; _pos_args = []; _neg_args = []
                    # _vol_score and _vol_notes already set in Volume-Gamma section above
                    if _cw and _cw_above:
                        _d = (_cw - _spot) / _spot * 100
                        if _d >= 2.0:   _score += 1; _pos_args.append(f"Call wall {_d:.1f}% above spot — strong ceiling buffer")
                        elif _d >= 0.8: _pos_args.append(f"Call wall {_d:.1f}% above spot — moderate buffer")
                        else:           _neg_args.append(f"Call wall only {_d:.1f}% above — TIGHT, reduce size 50%")
                        if _cws >= 4.0: _score += 1; _pos_args.append(f"Very strong call wall ({_cws:.1f}× avg OI) — exp {_cw_expiry}")
                        elif _cws >= 2.5: _pos_args.append(f"Call wall strength {_cws:.1f}× avg OI — exp {_cw_expiry}")
                        else:           _neg_args.append(f"Weak call wall ({_cws:.1f}×)")
                    elif _cw:
                        _neg_args.append(f"Call wall ${_cw:.0f} is BELOW spot — price has broken through; no overhead ceiling")
                    if _pw and _pw_below:
                        _d2 = (_spot - _pw) / _spot * 100
                        if _d2 >= 2.0: _pos_args.append(f"Put wall {_d2:.1f}% below spot — floor support ({_pws:.1f}× OI, exp {_pw_expiry})")
                        if _pws >= 4.0: _score += 1; _pos_args.append(f"Very strong put floor ({_pws:.1f}× avg OI)")
                    if _gex == "POSITIVE": _score += 1; _pos_args.append("Positive GEX — dealers buy dips, dampen volatility")
                    else:                  _neg_args.append("Negative GEX — dealers amplify moves; avoid short premium")
                    if _hist_wr >= 80:   _score += 1; _pos_args.append(f"Backtested: {_hist_wr:.0f}% win rate ({_hist_n} trades)")
                    elif _hist_wr >= 65: _pos_args.append(f"Backtested: {_hist_wr:.0f}% win rate ({_hist_n} trades)")
                    else:                _neg_args.append(f"Weak backtest: {_hist_wr:.0f}% win rate")
                    if _pcr < 1.5: _score += 1; _pos_args.append("PCR normal — no extreme fear signal")
                    else:          _neg_args.append(f"High PCR {_pcr:.2f} — elevated put demand / fear")
                    # Volume bonus (capped so total max = 5)
                    if _vol_score > 0: _pos_args.append(f"Volume-Gamma alignment: +{_vol_score} conviction pts")
                    _score = min(_score + _vol_score, 5)

                    # ── Conviction gauge chart ─────────────────────────
                    _fig_gauge = _ggo.Figure(_ggo.Indicator(
                        mode="gauge+number+delta",
                        value=_score,
                        delta={"reference": 3, "valueformat": ".0f"},
                        title={"text": "Advisor Conviction", "font": {"color": "#FFD700", "size": 16}},
                        gauge={
                            "axis": {"range": [0, 5], "tickcolor": "#aaa"},
                            "bar":  {"color": "#FFD700"},
                            "steps": [
                                {"range": [0, 2], "color": "#3a0000"},
                                {"range": [2, 3.5], "color": "#3a2200"},
                                {"range": [3.5, 5], "color": "#0a2a0a"},
                            ],
                            "threshold": {"line": {"color": "#ff5252", "width": 3}, "value": 3},
                        },
                        number={"suffix": "/5", "font": {"color": "#FFD700", "size": 40}},
                    ))
                    _fig_gauge.update_layout(
                        height=260,
                        font_color=None, margin=dict(t=30, b=10, l=30, r=30))

                    _gc1, _gc2 = st.columns([1, 2])
                    with _gc1:
                        st.plotly_chart(_fig_gauge, use_container_width=True)
                    with _gc2:
                        if _pos_args:
                            st.markdown("**✅ In favour:**")
                            for _a in _pos_args: st.markdown(f"- {_a}")
                        if _neg_args:
                            st.markdown("**⚠️ Against:**")
                            for _a in _neg_args: st.markdown(f"- {_a}")

                    # ── Multi-Strategy Trade Recommendations ──────────
                    st.markdown("---")
                    st.markdown("#### \U0001f4cb Strategy Recommendations")
                    st.caption("Each strategy is scored independently. Pick the one that matches your risk tolerance and current setup.")

                    # Helper: spread width and credit estimate
                    def _sw_cr(short, direction="call"):
                        _width = max(2.5, round(_spot * 0.012 / 2.5) * 2.5)
                        _long  = short + _width if direction == "call" else short - _width
                        _credit = round(_spot * 0.003, 2)  # ~0.3% of spot as rough credit
                        return _width, _long, _credit

                    _strategies = []   # list of dicts, rendered below

                    # ── Strategy 1: Sell Call Spread (if call wall ABOVE spot) ──
                    if _cw and _cw_above:
                        _d_cw = (_cw - _spot) / _spot * 100
                        _w1, _l1, _c1 = _sw_cr(_cw, "call")
                        _mp1 = round(_c1 * 100, 0)
                        _ml1 = round((_w1 - _c1) * 100, 0)
                        _be1 = _cw + _c1
                        _prob1 = min(95, max(50, _hist_wr * 0.9 + (_d_cw * 3)))
                        _margin1 = _ml1  # max loss = margin required
                        _roi1 = round(_mp1 / _margin1 * 100, 1) if _margin1 > 0 else 0
                        _strategies.append({
                            "name": "\U0001f43b SELL CALL SPREAD (Bear Call)",
                            "color": "#26a69a",
                            "icon": "📉",
                            "when": f"Price stays below ${_cw:.0f} by expiry {_cw_expiry}",
                            "short": f"${_cw:.0f} Call (SELL — at the wall)",
                            "long":  f"${_l1:.0f} Call (BUY — protection)",
                            "expiry": _cw_expiry,
                            "credit": _c1,
                            "max_profit": _mp1,
                            "max_loss": _ml1,
                            "margin": _margin1,
                            "breakeven": _be1,
                            "win_prob": _prob1,
                            "roi": _roi1,
                            "hold": "21-45 days",
                            "take_profit": f"${round(_c1*0.5,2):.2f} (50% of credit)",
                            "stop_loss": f"${round(_c1*2,2):.2f} (2× credit)",
                            "conviction": "HIGH" if _d_cw >= 2 and _cws >= 4 else "MEDIUM",
                            "spread_type": "CALL_SPREAD",
                            "short_strike": _cw, "long_strike": _l1,
                            "wall_strength": _cws,
                        })

                    # ── Strategy 2: Sell Put Spread (if put wall BELOW spot) ──
                    if _pw and _pw_below:
                        _d_pw = (_spot - _pw) / _spot * 100
                        _w2, _l2, _c2 = _sw_cr(_pw, "put")
                        _l2p = _pw - _w2
                        _mp2 = round(_c2 * 100, 0)
                        _ml2 = round((_w2 - _c2) * 100, 0)
                        _be2 = _pw - _c2
                        _prob2 = min(92, max(50, 70 + (_d_pw * 2) + (_pws - 2.5) * 3))
                        _margin2 = _ml2
                        _roi2 = round(_mp2 / _margin2 * 100, 1) if _margin2 > 0 else 0
                        _strategies.append({
                            "name": "\U0001f402 SELL PUT SPREAD (Bull Put)",
                            "color": "#ef5350",
                            "icon": "📈",
                            "when": f"Price stays above ${_pw:.0f} by expiry {_pw_expiry}",
                            "short": f"${_pw:.0f} Put (SELL — at the floor)",
                            "long":  f"${_l2p:.0f} Put (BUY — protection)",
                            "expiry": _pw_expiry,
                            "credit": _c2,
                            "max_profit": _mp2,
                            "max_loss": _ml2,
                            "margin": _margin2,
                            "breakeven": _be2,
                            "win_prob": _prob2,
                            "roi": _roi2,
                            "hold": "21-45 days",
                            "take_profit": f"${round(_c2*0.5,2):.2f} (50% of credit)",
                            "stop_loss": f"${round(_c2*2,2):.2f} (2× credit)",
                            "conviction": "HIGH" if _d_pw >= 2 and _pws >= 4 else "MEDIUM",
                            "spread_type": "PUT_SPREAD",
                            "short_strike": _pw, "long_strike": _l2p,
                            "wall_strength": _pws,
                        })

                    # ── Strategy 3: Iron Condor (both walls exist, positive GEX) ──
                    if _cw and _cw_above and _pw and _pw_below and _gex == "POSITIVE":
                        _range_pct = (_cw - _pw) / _spot * 100
                        _w3 = max(2.5, round(_spot * 0.01 / 2.5) * 2.5)
                        _c3 = round(_spot * 0.005, 2)  # combined credit both sides
                        _mp3 = round(_c3 * 100, 0)
                        _ml3 = round((_w3 - _c3/2) * 100, 0)
                        _prob3 = min(85, 60 + _range_pct * 1.5)
                        _roi3  = round(_mp3 / _ml3 * 100, 1) if _ml3 > 0 else 0
                        _strategies.append({
                            "name": "\U0001f985 IRON CONDOR (Both Walls)",
                            "color": "#FFD700",
                            "icon": "🦅",
                            "when": f"Price stays between ${_pw:.0f} and ${_cw:.0f} (range {_range_pct:.1f}%)",
                            "short": f"Sell ${_cw:.0f} Call + Sell ${_pw:.0f} Put",
                            "long":  f"Buy ${_cw+_w3:.0f} Call + Buy ${_pw-_w3:.0f} Put",
                            "expiry": _cw_expiry,
                            "credit": _c3,
                            "max_profit": _mp3,
                            "max_loss": _ml3,
                            "margin": _ml3,
                            "breakeven": None,
                            "win_prob": _prob3,
                            "roi": _roi3,
                            "hold": "21-45 days",
                            "take_profit": f"${round(_c3*0.5,2):.2f} (50% of credit)",
                            "stop_loss": f"${round(_c3*2,2):.2f} (2× credit)",
                            "conviction": "HIGH" if _range_pct >= 5 and _gex == "POSITIVE" else "MEDIUM",
                            "spread_type": "IRON_CONDOR",
                            "short_strike": _cw, "long_strike": _pw,
                            "wall_strength": (_cws + _pws) / 2,
                        })

                    if not _strategies:
                        st.warning(f"⚠️ No tradeable setup for {_ga_sel} today (score {_score}/5). "
                                   "Walls may be too close, missing, or GEX is negative. "
                                   "Check All-Ticker Scanner for better opportunities.")
                    else:
                        _pay_colors = ["#26a69a", "#ef5350", "#FFD700"]
                        for _si, _strat in enumerate(_strategies):
                            _cc = _pay_colors[_si % 3]
                            _cv = _strat["conviction"]
                            st.markdown(f"##### {_strat['name']}")
                            st.caption(f"\U0001f3af Win condition: {_strat['when']}")
                            # Row 1: key metrics
                            _rc = st.columns(5)
                            _rc[0].metric("Win Probability", f"{_strat['win_prob']:.0f}%")
                            _rc[1].metric("Conviction", _cv)
                            _rc[2].metric("Max Profit / contract", f"${_strat['max_profit']:.0f}")
                            _rc[3].metric("Max Loss / Margin", f"${_strat['max_loss']:.0f}")
                            _rc[4].metric("ROI on Margin", f"{_strat['roi']:.1f}%")
                            # Row 2: trade details table
                            _tbl = {
                                "Detail": ["SELL (Short)", "BUY (Hedge)", "Expiry", "Est Credit", "Hold Period", "✅ Take Profit", "🛑 Stop Loss", "⏰ 21-DTE Rule"],
                                "Value": [
                                    _strat["short"], _strat["long"], _strat["expiry"],
                                    f"${_strat['credit']:.2f}/share  =  ${_strat['credit']*100:.0f} per contract",
                                    _strat["hold"], _strat["take_profit"], _strat["stop_loss"],
                                    "Close regardless at 21 days to expiry — no exceptions"
                                ]
                            }
                            st.dataframe(pd.DataFrame(_tbl), hide_index=True, use_container_width=True)
                            st.divider()

                        # ── Payoff diagram — all strategies overlaid ──
                        st.markdown("#### \U0001f4c8 Payoff at Expiry")
                        _sr = np.linspace(_spot * 0.88, _spot * 1.12, 300)
                        _fig_pay = go.Figure()
                        for _si2, _strat2 in enumerate(_strategies):
                            _sc2 = float(_strat2["short_strike"])
                            _lc2 = float(_strat2["long_strike"])
                            _cc2 = float(_strat2["credit"])
                            _stype2 = _strat2["spread_type"]
                            _ml2f = float(_strat2["max_loss"])
                            if _stype2 == "CALL_SPREAD":
                                _width2 = _lc2 - _sc2
                                _pnl2 = np.where(_sr <= _sc2, _cc2 * 100,
                                        np.where(_sr <= _lc2, (_cc2 - (_sr - _sc2)) * 100,
                                        -(_width2 - _cc2) * 100))
                                _pnl2m = np.where(_sr <= _sc2, _cc2 * 50,
                                         np.where(_sr <= _lc2, (_cc2 * 0.5 - (_sr - _sc2) * 0.5) * 100,
                                         -(_width2 - _cc2) * 50))
                            elif _stype2 == "PUT_SPREAD":
                                _width2 = _sc2 - _lc2
                                _pnl2 = np.where(_sr >= _sc2, _cc2 * 100,
                                        np.where(_sr >= _lc2, (_cc2 - (_sc2 - _sr)) * 100,
                                        -(_width2 - _cc2) * 100))
                                _pnl2m = _pnl2 * 0.5
                            else:  # IRON_CONDOR: _sc2=call short, _lc2=put short
                                _cw2 = _sc2; _pw2 = _lc2
                                _pnl2 = np.where(
                                    (_sr >= _pw2) & (_sr <= _cw2), _cc2 * 100,
                                    np.where(_sr > _cw2,
                                        np.maximum(-_ml2f, (_cc2 - (_sr - _cw2)) * 100),
                                        np.maximum(-_ml2f, (_cc2 - (_pw2 - _sr)) * 100)))
                                _pnl2m = _pnl2 * 0.5
                            _col2 = _pay_colors[_si2 % 3]
                            _r2, _g2, _b2 = int(_col2[1:3], 16), int(_col2[3:5], 16), int(_col2[5:7], 16)
                            _fig_pay.add_trace(go.Scatter(
                                x=_sr, y=_pnl2, mode="lines",
                                line=dict(color=_col2, width=2.5),
                                name=_strat2["name"].split("(")[0].strip(),
                                fill="tozeroy", fillcolor=f"rgba({_r2},{_g2},{_b2},0.08)",
                                hovertemplate="Price: $%{x:.2f}<br>P&L: $%{y:.0f}<extra></extra>"))
                            _fig_pay.add_trace(go.Scatter(
                                x=_sr, y=_pnl2m, mode="lines",
                                line=dict(color=_col2, width=1, dash="dot"),
                                showlegend=False,
                                hovertemplate="Mid-term P&L: $%{y:.0f}<extra></extra>"))
                        _fig_pay.add_hline(y=0, line=dict(color="#555", dash="dash", width=1))
                        _fig_pay.add_vline(x=_spot, line=dict(color="#FFD700", width=1.5),
                            annotation=dict(text=f"Spot ${_spot:.2f}", font_color="#1565C0",
                                          bgcolor="rgba(255,255,255,0.85)", bordercolor="#f0a500"))
                        if _cw and _cw_above:
                            _fig_pay.add_vline(x=_cw, line=dict(color="#00e676", width=1.2, dash="dash"),
                                annotation=dict(text=f"Call Wall ${_cw:.0f}", font_color="#00e676", bgcolor="rgba(255,255,255,0.85)"))
                        if _pw and _pw_below:
                            _fig_pay.add_vline(x=_pw, line=dict(color="#ff5252", width=1.2, dash="dash"),
                                annotation=dict(text=f"Put Wall ${_pw:.0f}", font_color="#ff5252", bgcolor="rgba(255,255,255,0.85)"))
                        _fig_pay.update_layout(
                            height=380,
                            title=dict(text=f"{_ga_sel} — Strategy P&L at Expiry  (solid = at expiry | dotted = ~50% decay)",
                                      font=dict(color="#1565C0", size=14)),
                            plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
                            font=dict(family="Courier New, monospace"),
                            xaxis=dict(title="Stock Price at Expiry ($)", gridcolor="rgba(0,0,0,0.08)",
                                      showspikes=True, spikecolor="#FFD700"),
                            yaxis=dict(title="P&L per contract ($)", gridcolor="rgba(0,0,0,0.08)",
                                      zeroline=True, zerolinecolor="rgba(0,0,0,0.2)"),
                            legend=dict(orientation="h", yanchor="bottom", y=1.02, bgcolor="rgba(0,0,0,0)"),
                            hovermode="x unified", margin=dict(t=50, b=40))
                        st.plotly_chart(_fig_pay, use_container_width=True)

                        # ── Log best trade ─────────────────────────────
                        _best = _strategies[0]
                        _log_c1, _log_c2 = st.columns([3, 1])
                        with _log_c1:
                            st.info(f"\U0001f4dd **Best setup:** {_best['name']} | "
                                    f"Short ${_best['short_strike']:.0f} / Long ${_best['long_strike']:.0f} | "
                                    f"Expiry {_best['expiry']} | Credit ${_best['credit']:.2f} | Score {_score}/5")
                        with _log_c2:
                            if st.button("\U0001f4dd Log This Trade", key="ga_log_adv_btn", type="primary"):
                                try:
                                    _ga_conn.execute(
                                        "INSERT INTO gamma_wall_trades "
                                        "(ticker,trade_date,expiry,short_strike,long_strike,spread_type,"
                                        " credit,quantity,wall_strength,dist_to_wall_pct,spot_at_entry,"
                                        " gex_regime,advisor_score,advisor_notes,status) "
                                        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                                        (_ga_sel, _ga_today, _best["expiry"],
                                         _best["short_strike"], _best["long_strike"],
                                         _best["spread_type"], _best["credit"], 1,
                                         _best["wall_strength"],
                                         round(abs(_best["short_strike"] - _spot) / _spot * 100, 2),
                                         _spot, _gex, _score,
                                         "; ".join(_pos_args[:3]), "OPEN"))
                                    _ga_conn.commit()
                                    st.success("✅ Trade logged! See 'My Positions' tab.")
                                except Exception as _le:
                                    st.error(f"Log error: {_le}")

    # ════════════════════════════════════════════════════════════════
    # TAB 2: ALL-TICKER SCANNER — Index + Stock sub-tabs
    # ════════════════════════════════════════════════════════════════
    with _tab_all:
        st.markdown("### \U0001f30e Gamma Wall Scanner")
        st.caption("Indexes and stocks scanned separately. Ranked by **expectancy** (real edge), not just win rate.")

        # VIX from yfinance — used for index tier classification
        _vix_val = None
        try:
            _vix_raw = yf.download("^VIX", period="2d", interval="1d", progress=False, auto_adjust=True)
            if not _vix_raw.empty:
                if isinstance(_vix_raw.columns, pd.MultiIndex):
                    _vix_raw.columns = _vix_raw.columns.get_level_values(0)
                _vix_val = float(_vix_raw["Close"].iloc[-1])
        except Exception:
            pass

        _vix_label = (f"VIX: {_vix_val:.1f} — " +
                      ("🟢 ULTRA LOW (<16, ideal for selling)" if _vix_val and _vix_val < 16 else
                       "🟡 LOW (16-20, good for selling)" if _vix_val and _vix_val < 20 else
                       "🔴 ELEVATED (>20, reduce size)" if _vix_val else "VIX unavailable"))
        st.info(_vix_label)

        # Expected Value explainer
        with st.expander("💡 Why Expectancy > Win Rate?"):
            st.markdown("""
**Win rate alone is misleading.** A 90% win rate with tiny credits wiped by one loss = negative expectancy.

| Strategy | Win Rate | Avg Win | Avg Loss | **Expectancy per trade** |
|----------|----------|---------|----------|--------------------------|
| Far OTM (1.5σ+), +GEX, low VIX | 82% | 0.25% | 0.75% | **+0.07%** ✅ |
| Standard gamma wall | 70% | 0.40% | 1.20% | **-0.08%** ❌ |
| Ultra far OTM (chasing 95%) | 95% | 0.05% | 1.50% | **-0.025%** ❌ |

**Layer for 80%+:** Far OTM (≥1.5σ) + Positive GEX + VIX < 20 + Wall strength ≥ 3× + 21–45 DTE.
Real edge comes from discipline and filters — not a higher strike.
""")

        # Full universe of major ETF indexes worth tracking for gamma walls
        _INDEXES = {
            # Broad market
            "SPY","QQQ","IWM","DIA","MDY","VOO","VTI","SCHB","RSP",
            # Leveraged
            "TQQQ","SQQQ","SPXL","SPXS","UPRO","SPDN","SSO","SDS",
            # Volatility
            "VXX","UVXY","SVXY","VIXY",
            # Sector ETFs (SPDR)
            "XLF","XLE","XLK","XLV","XLB","XLI","XLU","XLRE","XLP","XLY","XLC",
            # Technology / Semiconductors
            "SOXX","SMH","SOXL","SOXS","XSD","FTXL",
            # Data center / AI / Cloud
            "CLOU","WCLD","SKYY","DTCR","DTLA","IGV","BOTZ","IRBO","ROBO",
            # Space / Defense
            "UFO","ARKX","ITA","XAR","PPA","KTOS",
            # Healthcare sub-sectors
            "XBI","IBB","GXC","ARKG",
            # Energy / Commodities
            "GLD","SLV","GDX","GDXJ","USO","UNG","DBO","PDBC",
            # International
            "EEM","EFA","FXI","KWEB","MCHI","VWO","EWJ","INDA",
            # Fixed Income / Rates
            "TLT","TMF","TBT","HYG","LQD","AGG","SHY",
            # Thematic
            "ARKK","ARKG","ARKF","ARKQ","ARKW","ARKB",
            "FINX","IPAY","ETHO","HACK","CIBR","BUG",
        }
        _db_indexes = sorted([t for t in _ga_all_tickers if t in _INDEXES])
        _db_stocks  = sorted([t for t in _ga_all_tickers if t not in _INDEXES])
        # Show which important indexes are NOT in the DB
        _key_missing = sorted([t for t in
            {"SPY","QQQ","IWM","DIA","SOXX","SMH","SOXL","XLK","XLF","XLE","XBI","GLD","TLT",
             "TQQQ","VXX","CLOU","IGV","ITA","UFO","ARKK","EEM","EFA","KWEB"}
            if t not in _ga_all_tickers])

        _s_idx, _s_stk = st.tabs([f"\U0001f4c8 Indexes ({len(_db_indexes)})",
                                   f"\U0001f4ca Stocks ({len(_db_stocks)})"])

        def _run_scanner(tickers_list, tab_key, vix=None, is_index=False):
            """Shared scanner logic for both index and stock tabs."""
            _btn = st.button(f"\U0001f50d Scan {len(tickers_list)} tickers",
                             type="primary", key=f"ga_scan_{tab_key}")
            if not _btn:
                return

            with st.spinner("Scanning…"):
                _rows = []
                for _tk in tickers_list:
                    try:
                        _bt_df, _hm = _ga_bt(_tk, _ga_conn, wall_mult=2.5, hold=5)
                        if not _hm or _hm.get("trades", 0) < 3:
                            continue
                        _toi2 = pd.read_sql(
                            "SELECT strike, expiry_date, openInt_Call_now, openInt_Put_now "
                            "FROM options_change WHERE ticker=? AND trade_date_now=? "
                            "AND (openInt_Call_now > 0 OR openInt_Put_now > 0)",
                            _ga_conn, params=(_tk, _ga_today))
                        for _c3 in ["openInt_Call_now", "openInt_Put_now", "strike"]:
                            _toi2[_c3] = pd.to_numeric(_toi2[_c3], errors="coerce").fillna(0)
                        if _toi2.empty or len(_toi2) < 5:
                            continue
                        _tsp_r2 = _ga_conn.execute(
                            "SELECT close, pcr_oi FROM stock_daily WHERE ticker=? AND trade_date=?",
                            (_tk, _ga_today2)).fetchone()
                        if not _tsp_r2:
                            continue
                        _tsp2 = float(_tsp_r2[0])
                        _tpcr2 = float(_tsp_r2[1]) if _tsp_r2[1] else 1.0

                        # Call wall above spot
                        _ta2 = _toi2["openInt_Call_now"].mean()
                        _tw2 = _toi2[(_toi2["openInt_Call_now"] >= _ta2 * 2.5) &
                                      (_toi2["strike"] >= _tsp2)].sort_values("openInt_Call_now", ascending=False)
                        if _tw2.empty:
                            continue
                        _tcw2 = float(_tw2["strike"].iloc[0])
                        _tws2 = float(_tw2["openInt_Call_now"].iloc[0]) / _ta2
                        _texp2 = _tw2["expiry_date"].iloc[0] if "expiry_date" in _tw2.columns else ""
                        _tdist2 = (_tcw2 - _tsp2) / _tsp2 * 100
                        if _tdist2 < 0.3:
                            continue

                        # σ-distance: using 20d close std as proxy for 1-day σ
                        _tclose_hist = _bt_df["spot"].tail(20) if not _bt_df.empty and len(_bt_df) >= 5 else None
                        _sigma_dist = None
                        if _tclose_hist is not None and len(_tclose_hist) >= 5:
                            _daily_sd = _tclose_hist.pct_change().dropna().std() * 100  # % daily σ
                            _sigma_dist = round(_tdist2 / _daily_sd, 2) if _daily_sd > 0 else None

                        # Layered filter tier
                        _gex2 = "POS" if _tpcr2 < 1.2 else "NEG"
                        _tier = "❌ Skip"
                        if _gex2 == "POS" and _tdist2 >= 2.0 and _tws2 >= 3.0:
                            if vix and vix < 16:
                                _tier = "🥇 Tier 1 (80%+)"
                            elif vix and vix < 20:
                                _tier = "🥈 Tier 2 (75%+)"
                            else:
                                _tier = "🥉 Tier 3 (70%)"
                        elif _gex2 == "POS" and _tdist2 >= 1.0:
                            _tier = "🥉 Tier 3 (70%)"

                        # Conviction
                        _sc2 = 0
                        if _tdist2 >= 2.0: _sc2 += 1
                        if _tws2 >= 4.0:   _sc2 += 1
                        if _gex2 == "POS": _sc2 += 1
                        if _hm["win_rate"] >= 80: _sc2 += 1
                        if _tpcr2 < 1.5:   _sc2 += 1
                        _sc2 = min(_sc2, 5)

                        _exp = _hm.get("expectancy_pct", 0) or 0
                        _far_wr = _hm.get("far_otm_wr")
                        _rows.append({
                            "Ticker": _tk,
                            "Spot": round(_tsp2, 2),
                            "Call Wall": round(_tcw2, 2),
                            "Expiry": _texp2,
                            "Dist %": round(_tdist2, 1),
                            "σ away": _sigma_dist,
                            "Wall Str": round(_tws2, 1),
                            "GEX": _gex2,
                            "Win%": _hm["win_rate"],
                            "Far OTM Win%": _far_wr,
                            "Expectancy%": round(_exp, 4),
                            "Trades": _hm["trades"],
                            "Conviction": _sc2,
                            "Tier": _tier,
                            "Action": f"Sell ${_tcw2:.0f} Call Spread exp {_texp2}",
                        })
                    except Exception:
                        continue

            if not _rows:
                st.info("No opportunities found.")
                return
            _sdf2 = pd.DataFrame(_rows).sort_values(
                ["Conviction", "Win%"], ascending=False).reset_index(drop=True)
            st.success(f"✅ {len(_sdf2)} opportunities found")

            # ── Summary bar chart: Win% + Far OTM Win% ────────────
            _fig_s2 = go.Figure()
            _col_s2 = ["#FFD700" if r["Conviction"] >= 4 else
                       "#26a69a" if r["Conviction"] >= 3 else "#1565C0"
                       for _, r in _sdf2.iterrows()]
            _fig_s2.add_trace(go.Bar(
                name="Standard Win%",
                x=_sdf2["Ticker"], y=_sdf2["Win%"],
                marker_color=_col_s2, opacity=0.85,
                hovertemplate="<b>%{x}</b><br>Win%: %{y:.0f}%<extra></extra>"))
            _far_valid = _sdf2["Far OTM Win%"].dropna()
            if not _far_valid.empty:
                _fig_s2.add_trace(go.Bar(
                    name="Far OTM (≥2%) Win%",
                    x=_sdf2["Ticker"],
                    y=_sdf2["Far OTM Win%"].fillna(0),
                    marker_color="#FF8C00", opacity=0.6,
                    hovertemplate="<b>%{x}</b><br>Far OTM Win%: %{y:.0f}%<extra></extra>"))
            _fig_s2.add_hline(y=80, line=dict(color="#26a69a", dash="dash", width=1),
                              annotation_text="80% target", annotation_font_color="#1b7a6a")
            _fig_s2.add_hline(y=70, line=dict(color="#FFD700", dash="dot", width=1),
                              annotation_text="70% baseline", annotation_font_color="#1565C0")
            _fig_s2.update_layout(
                barmode="group", height=360,
                title=dict(text="Win Rate — Standard vs Far OTM (≥2% from wall)",
                          font=dict(color="#1565C0", size=14)),
                plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
                font=dict(family="Courier New, monospace"),
                xaxis=dict(gridcolor="rgba(0,0,0,0.08)"), yaxis=dict(gridcolor="rgba(0,0,0,0.08)", range=[0, 110]),
                legend=dict(orientation="h", yanchor="bottom", y=1.02, bgcolor="rgba(0,0,0,0)"),
                margin=dict(t=50, b=40))
            st.plotly_chart(_fig_s2, use_container_width=True)

            # ── Expectancy scatter ─────────────────────────────────
            _sdf2_exp = _sdf2[_sdf2["Expectancy%"].notna()].copy()
            if len(_sdf2_exp) >= 2:
                _fig_exp = go.Figure()
                _exp_colors = ["#26a69a" if e > 0 else "#ef5350" for e in _sdf2_exp["Expectancy%"]]
                _fig_exp.add_trace(go.Scatter(
                    x=_sdf2_exp["Ticker"], y=_sdf2_exp["Expectancy%"],
                    mode="markers+text",
                    marker=dict(color=_exp_colors, size=14, symbol="diamond",
                               line=dict(color="#FFD700", width=1)),
                    text=[f"{e:+.4f}%" for e in _sdf2_exp["Expectancy%"]],
                    textposition="top center",
                    hovertemplate="<b>%{x}</b><br>Expectancy: %{y:.4f}% per trade<br>"
                                  "= $%{customdata:.2f} per $100 margin<extra></extra>",
                    customdata=_sdf2_exp["Expectancy%"] * 100))
                _fig_exp.add_hline(y=0, line=dict(color="#FFD700", dash="dash", width=1.5),
                                  annotation_text="Break-even", annotation_font_color="#1565C0")
                _fig_exp.update_layout(
                    height=280,
                    title=dict(text="Expectancy per Trade — Positive = Real Edge ✅",
                              font=dict(color="#1565C0", size=13)),
                    plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
                    font=dict(family="Courier New, monospace"),
                    xaxis=dict(gridcolor="rgba(0,0,0,0.08)"),
                    yaxis=dict(gridcolor="rgba(0,0,0,0.08)", title="Expectancy % per trade",
                              zeroline=True, zerolinecolor="rgba(0,0,0,0.2)"),
                    margin=dict(t=40, b=30))
                st.plotly_chart(_fig_exp, use_container_width=True)

            # ── Full table ─────────────────────────────────────────
            st.dataframe(_sdf2, use_container_width=True, hide_index=True,
                         column_config={
                             "Win%": st.column_config.ProgressColumn(
                                 "Win%", format="%.0f%%", min_value=0, max_value=100),
                             "Conviction": st.column_config.ProgressColumn(
                                 "Conviction", format="%d/5", min_value=0, max_value=5),
                             "Tier": st.column_config.TextColumn("Tier"),
                             "σ away": st.column_config.NumberColumn("σ away", format="%.1f"),
                             "Expectancy%": st.column_config.NumberColumn(
                                 "Expectancy%", format="%.4f"),
                         })
            st.caption("\U0001f947 Tier 1 = Far OTM ≥2% + GEX+ + VIX<16   "
                       "\U0001f948 Tier 2 = same but VIX 16-20   "
                       "\U0001f949 Tier 3 = GEX+ only   "
                       "| σ away = how many daily σ the wall is from spot")

        with _s_idx:
            st.markdown("#### \U0001f4c8 Major Index Gamma Walls")
            st.caption("Indexes have the most reliable gamma walls (SPY/QQQ dominate dealer hedging). "
                       "SPY 0DTE especially: sell far OTM call spread when VIX < 16 + wall ≥ 3σ away.")
            if _key_missing:
                st.warning(
                    f"⚠️ **{len(_key_missing)} key indexes missing from your DB** — "
                    f"add these tickers to get broader coverage: "
                    f"`{'`, `'.join(_key_missing)}`\n\n"
                    "**Recommended additions by theme:**\n"
                    "- Semiconductors: `SOXX`, `SMH`, `SOXL`\n"
                    "- AI / Data Center: `CLOU`, `IGV`, `BOTZ`\n"
                    "- Space / Defense: `UFO`, `ITA`, `ARKX`\n"
                    "- Volatility: `VXX`, `UVXY`\n"
                    "- Bonds / Rates: `TLT`, `HYG`\n"
                    "- International: `EEM`, `KWEB`")
            if _db_indexes:
                _run_scanner(_db_indexes, "idx", vix=_vix_val, is_index=True)
            else:
                st.warning("No major index tickers found in DB (looking for SPY, QQQ, IWM, DIA, etc.)")

        with _s_stk:
            st.markdown("#### \U0001f4ca Individual Stock Gamma Walls")
            st.caption("Stocks have earnings risk — check earnings calendar before selling. "
                       "Apply stricter filter: wall ≥ 2.5% + GEX+ + no earnings within 14 days.")
            if _db_stocks:
                _run_scanner(_db_stocks, "stk", vix=_vix_val, is_index=False)
            else:
                st.warning("No stock tickers found in DB.")

    # ════════════════════════════════════════════════════════════════
    # TAB 3: EXPIRY MAP
    # ════════════════════════════════════════════════════════════════
    with _tab_exp:
        st.markdown("### \U0001f4c5 Expiry-Level Wall Map")
        st.caption("Each expiry has its own wall structure. 21–50 DTE is the ideal entry zone.")
        _exp_sel = st.selectbox("Ticker", _ga_all_tickers,
                                index=_ga_all_tickers.index("SPY") if "SPY" in _ga_all_tickers else 0,
                                key="ga_exp_sel")
        if st.button("Load Expiry Map", key="ga_exp_btn"):
            _eoi = pd.read_sql(
                "SELECT strike, expiry_date, openInt_Call_now, openInt_Put_now "
                "FROM options_change WHERE ticker=? AND trade_date_now=? "
                "AND (openInt_Call_now>0 OR openInt_Put_now>0)",
                _ga_conn, params=(_exp_sel, _ga_today))
            for _c in ["strike","openInt_Call_now","openInt_Put_now"]:
                _eoi[_c] = pd.to_numeric(_eoi[_c], errors="coerce").fillna(0)
            _espot_r = _ga_conn.execute(
                "SELECT close FROM stock_daily WHERE ticker=? AND trade_date=?",
                (_exp_sel, _ga_today2)).fetchone()
            _espot = float(_espot_r[0]) if _espot_r else None

            if _eoi.empty or not _espot:
                st.warning("No data.")
            else:
                # Sort expiries chronologically (MM-DD-YYYY format)
                def _exp_chron_key(d):
                    try:
                        p = str(d).split("-")
                        return (int(p[2]), int(p[0]), int(p[1]))
                    except Exception:
                        return (9999, 99, 99)
                _exp_rows = []
                _all_expiries = sorted(_eoi["expiry_date"].unique(), key=_exp_chron_key)
                for _ex in _all_expiries:
                    _eg = _eoi[_eoi["expiry_date"] == _ex]
                    if len(_eg) < 4: continue
                    _ex_w = compute_walls(_eg, _spot)
                    _bc   = _ex_w["call_wall"]
                    _bp   = _ex_w["put_wall"]
                    _bcs  = _ex_w["call_wall_strength"]
                    _bps  = _ex_w["put_wall_strength"]
                    try:
                        _dte = (datetime.strptime(str(_ex), "%Y-%m-%d") - datetime.now()).days
                    except Exception:
                        try: _dte = (datetime.strptime(str(_ex), "%m-%d-%Y") - datetime.now()).days
                        except: _dte = -1
                    _zone = "✅ IDEAL (21-50d)" if 21<=_dte<=50 else \
                            ("⚠️ Short (<21d)" if 0<_dte<21 else
                             ("\U0001f4cc Long (>50d)" if _dte>50 else "past"))
                    _exp_rows.append({
                        "Expiry": str(_ex)[:10], "DTE": _dte, "Zone": _zone,
                        "Call Wall": _bc, "CW Str": round(_bcs,1),
                        "CW Dist%": round((_bc-_espot)/_espot*100,1) if _bc else None,
                        "Put Wall": _bp, "PW Str": round(_bps,1),
                        "PW Dist%": round((_bp-_espot)/_espot*100,1) if _bp else None,
                    })
                _edf = pd.DataFrame(_exp_rows)
                _edf = _edf[_edf["DTE"] >= 0].sort_values("DTE").reset_index(drop=True)

                # ── 3D-style expiry × strike heatmap ─────────────────
                _hm_data = []
                _hm_exp = []; _hm_strikes = []
                _view_oi = _eoi[(_eoi["strike"] >= _espot*0.9) & (_eoi["strike"] <= _espot*1.1)]
                _hm_strikes = sorted(_view_oi["strike"].unique())
                _hm_exps = sorted(_view_oi["expiry_date"].unique(), key=_exp_chron_key)
                for _he in _hm_exps:
                    _row = []
                    for _hs in _hm_strikes:
                        _v = _view_oi[(_view_oi["expiry_date"]==_he) & (_view_oi["strike"]==_hs)]["openInt_Call_now"].sum()
                        _row.append(float(_v))
                    _hm_data.append(_row)
                if _hm_data and _hm_strikes:
                    _fig_hm = _ggo.Figure(_ggo.Heatmap(
                        z=_hm_data, x=[f"${s:.0f}" for s in _hm_strikes],
                        y=[str(e)[:10] for e in _hm_exps],
                        colorscale="Viridis", showscale=True,
                        hoverongaps=False,
                        hovertemplate="Expiry: %{y}<br>Strike: %{x}<br>Call OI: %{z:,.0f}<extra></extra>",
                        colorbar=dict(title=dict(text="Call OI", font=dict(color="#aaa")),
                                     tickfont=dict(color="#aaa"))
                    ))
                    try:
                        # Categorical x-axis uses string labels "$NNN" — find nearest
                        _hm_x_labels = [f"${s:.0f}" for s in _hm_strikes]
                        _nearest_spot_label = min(
                            _hm_x_labels,
                            key=lambda lbl: abs(float(lbl.replace("$","")) - _espot)
                        )
                        _spot_idx = _hm_x_labels.index(_nearest_spot_label)
                        _fig_hm.add_shape(
                            type="line",
                            x0=_spot_idx - 0.5, x1=_spot_idx + 0.5,
                            y0=0, y1=1,
                            xref="x", yref="paper",
                            line=dict(color="#FFD700", width=2),
                        )
                        _fig_hm.add_annotation(
                            x=_nearest_spot_label, y=1.0,
                            xref="x", yref="paper",
                            text=f"SPOT ${_espot:.0f}",
                            showarrow=False,
                            font=dict(color="#FFD700", size=10),
                            bgcolor="rgba(0,0,0,0.6)",
                            yanchor="bottom",
                        )
                    except Exception:
                        pass  # categorical axis - skip spot line
                    _fig_hm.update_layout(
                        title=dict(text=f"{_exp_sel} Call OI Heatmap — Strike × Expiry",
                                  font=dict(color="#1565C0", size=15)),
                        height=max(350, len(_hm_exps)*25 + 100),
                        plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
                        font=dict(family="Courier New, monospace"),
                        xaxis=dict(title="Strike", gridcolor="rgba(0,0,0,0.08)"),
                        yaxis=dict(title="Expiry Date", gridcolor="rgba(0,0,0,0.08)"),
                        margin=dict(t=50, b=40))
                    st.plotly_chart(_fig_hm, use_container_width=True)
                    st.caption("Bright = high call OI = gamma wall zone. Look for bright columns above spot (gold line).")

                # Expiry table
                st.dataframe(_edf, use_container_width=True, hide_index=True,
                             column_config={
                                 "CW Dist%": st.column_config.NumberColumn(format="%.1f%%"),
                                 "PW Dist%": st.column_config.NumberColumn(format="%.1f%%"),
                             })
                st.info(
                    "\U0001f4a1 **How to pick expiry:** Sell the expiry in the ✅ IDEAL (21–50 DTE) zone "
                    "with the strongest call wall and distance ≥1.5%. "
                    "Closer to 45 DTE = highest theta, lowest gamma risk."
                )

    # ════════════════════════════════════════════════════════════════
    # TAB 4: MY POSITIONS
    # ════════════════════════════════════════════════════════════════
    with _tab_pos:
        st.markdown("### \U0001f4ca My Gamma Wall Positions")
        if st.button("\U0001f504 Refresh", key="ga_pos_refresh"):
            st.rerun()

        # ── Main portfolio open positions (the shared `trades` ledger) ──
        try:
            _main_pos = pd.read_sql(
                "SELECT ticker, option_type, strike, quantity, entry_date, expiry, strategy, pnl "
                "FROM trades WHERE status='OPEN' ORDER BY expiry", _ga_conn)
        except Exception:
            _main_pos = pd.DataFrame()
        st.markdown(f"#### \U0001f4cc Main Portfolio — Open ({len(_main_pos)})")
        if _main_pos.empty:
            st.caption("No open trades in the main portfolio (`trades` table). Add them on the Portfolio page.")
        else:
            _spots, _rows = {}, []
            for _, _r in _main_pos.iterrows():
                _tk = str(_r["ticker"]).upper()
                if _tk not in _spots:
                    _sp = _ga_conn.execute(
                        "SELECT close FROM stock_daily WHERE UPPER(ticker)=? ORDER BY "
                        "substr(trade_date,7,4)||substr(trade_date,1,2)||substr(trade_date,4,2) DESC LIMIT 1",
                        (_tk,)).fetchone()
                    _spots[_tk] = float(_sp[0]) if _sp else None
                _spot_v = _spots[_tk]
                _dte = None
                for _fmt in ("%Y-%m-%d", "%m-%d-%Y"):
                    try:
                        _dte = (datetime.strptime(str(_r["expiry"]), _fmt) - datetime.now()).days
                        break
                    except Exception:
                        pass
                _k = float(_r["strike"] or 0)
                _dist = ((_k - _spot_v) / _spot_v * 100) if (_spot_v and _k) else None
                _rows.append({
                    "Ticker": _tk,
                    "Type": str(_r["option_type"]).upper(),
                    "Strike": _k,
                    "Qty": int(_r["quantity"] or 0),
                    "Expiry": str(_r["expiry"]),
                    "DTE": _dte if _dte is not None else "—",
                    "Spot": round(_spot_v, 2) if _spot_v else "—",
                    "Strike vs Spot %": round(_dist, 1) if _dist is not None else "—",
                    "Strategy": _r["strategy"],
                })
            st.dataframe(pd.DataFrame(_rows), hide_index=True, use_container_width=True)
            st.caption("Your live trades from the shared portfolio. The Advisor-spread ledger below "
                       "is only for credit spreads logged via Advisor Scan.")
        st.markdown("---")

        _open_p = pd.read_sql(
            "SELECT * FROM gamma_wall_trades WHERE status='OPEN' ORDER BY trade_date DESC", _ga_conn)
        _closed_p = pd.read_sql(
            "SELECT * FROM gamma_wall_trades WHERE status!='OPEN' ORDER BY exit_date DESC", _ga_conn)

        # ── Open positions ────────────────────────────────────────────
        st.markdown(f"#### \U0001f7e2 Advisor Spreads — Open ({len(_open_p)})")
        if _open_p.empty:
            st.info("No open positions. Use Advisor Scan to find a setup and log it.")
        else:
            for _, _p in _open_p.iterrows():
                try:
                    _pexp = datetime.strptime(str(_p["expiry"]), "%Y-%m-%d")
                except Exception:
                    try: _pexp = datetime.strptime(str(_p["expiry"]), "%m-%d-%Y")
                    except: _pexp = None
                _dte_p = (_pexp - datetime.now()).days if _pexp else None
                _csp_r = _ga_conn.execute(
                    "SELECT close FROM stock_daily WHERE ticker=? ORDER BY "
                    "substr(trade_date,7,4)||substr(trade_date,1,2)||substr(trade_date,4,2) DESC LIMIT 1",
                    (_p["ticker"],)).fetchone()
                _csp = float(_csp_r[0]) if _csp_r else None
                _ss = float(_p["short_strike"]); _ls = float(_p["long_strike"])
                _dist_p = ((_ss - _csp) / _csp * 100) if _csp else None
                _cred   = float(_p["credit"])
                # Status
                if _dist_p is not None and _dist_p < 0:
                    _sc_bg="#4a0000"; _sc_txt="\U0001f6a8 BREACHED"; _sc_act="CLOSE IMMEDIATELY — take the loss"
                elif _dist_p is not None and _dist_p < 1.0:
                    _sc_bg="#3a1500"; _sc_txt="⚠️ DANGER — <1% from wall"; _sc_act="Consider closing early"
                elif _dte_p is not None and _dte_p <= 21:
                    _sc_bg="#2a1a00"; _sc_txt=f"⏰ 21-DTE TRIGGERED ({_dte_p}d left)"; _sc_act="CLOSE NOW per 21-DTE rule"
                else:
                    _sc_bg="#0a1a0a"; _sc_txt="✅ SAFE — wall holding"; _sc_act="Hold — monitor daily"

                st.markdown(
                    f"""<div style='background:{_sc_bg};border-radius:8px;padding:14px 16px;margin-bottom:10px;
                              border:1px solid #333'>
#{int(_p["id"])} — {_p["ticker"]} {_p["spread_type"]}
{_p["trade_date"]}<br>

Short <b>${_ss:.0f}</b> | Long <b>${_ls:.0f}</b> |
Credit <b>${_cred:.2f}</b> | Qty {int(_p["quantity"])} |
Expiry <b>{_p["expiry"]}</b> | DTE <b>{_dte_p if _dte_p is not None else "?"}</b><br>
Spot now: <b>${_csp:.2f if _csp else "?"}</b> |
Distance to wall: <b>{f"{_dist_p:.1f}%" if _dist_p is not None else "?"}</b><br>
Status: <b>{_sc_txt}</b><br>
Advisor: {_sc_act}<br>
Close at 50%: buy back at <b>${_cred*0.5:.2f}</b> |
Stop: buy back at <b>${_cred*2:.2f}</b>
</div>""", unsafe_allow_html=True)
                _bc1, _bc2, _bc3, _bc4 = st.columns(4)
                if _bc1.button(f"✅ WIN 50%", key=f"p_win_{_p['id']}"):
                    _pnl = _cred * 0.5 * 100 * int(_p["quantity"])
                    _ga_conn.execute(
                        "UPDATE gamma_wall_trades SET status='CLOSED',exit_date=?,exit_price=?,"
                        "exit_reason='50%_PROFIT',pnl_dollar=?,pnl_pct=50 WHERE id=?",
                        (datetime.now().strftime("%m-%d-%Y"), _cred*0.5, _pnl, _p["id"]))
                    _ga_conn.commit(); st.rerun()
                if _bc2.button(f"\U0001f6d1 STOP 2×", key=f"p_stop_{_p['id']}"):
                    _pnl = -_cred * 2.0 * 100 * int(_p["quantity"])
                    _ga_conn.execute(
                        "UPDATE gamma_wall_trades SET status='CLOSED',exit_date=?,exit_price=?,"
                        "exit_reason='2X_STOP',pnl_dollar=?,pnl_pct=-200 WHERE id=?",
                        (datetime.now().strftime("%m-%d-%Y"), _cred*2, _pnl, _p["id"]))
                    _ga_conn.commit(); st.rerun()
                if _bc3.button(f"\U0001f4cb EXPIRED", key=f"p_exp_{_p['id']}"):
                    _pnl = _cred * 100 * int(_p["quantity"])
                    _ga_conn.execute(
                        "UPDATE gamma_wall_trades SET status='CLOSED',exit_date=?,exit_price=0,"
                        "exit_reason='EXPIRED',pnl_dollar=?,pnl_pct=100 WHERE id=?",
                        (datetime.now().strftime("%m-%d-%Y"), _pnl, _p["id"]))
                    _ga_conn.commit(); st.rerun()
                if _bc4.button(f"✏️ Manual", key=f"p_man_{_p['id']}"):
                    _mc_pr = st.number_input("Close price", min_value=0.0, step=0.01,
                                             key=f"p_man_pr_{_p['id']}")
                    if st.button("Confirm", key=f"p_man_ok_{_p['id']}"):
                        _pnl = (_cred - _mc_pr) * 100 * int(_p["quantity"])
                        _ga_conn.execute(
                            "UPDATE gamma_wall_trades SET status='CLOSED',exit_date=?,exit_price=?,"
                            "exit_reason='MANUAL',pnl_dollar=?,pnl_pct=? WHERE id=?",
                            (datetime.now().strftime("%m-%d-%Y"), _mc_pr,
                             _pnl, (_cred-_mc_pr)/_cred*100, _p["id"]))
                        _ga_conn.commit(); st.rerun()

        # ── Closed analytics ──────────────────────────────────────────
        st.markdown(f"#### \U0001f4c1 Advisor Spreads — Closed ({len(_closed_p)})")
        if not _closed_p.empty:
            _closed_p["pnl_dollar"] = pd.to_numeric(_closed_p["pnl_dollar"],errors="coerce").fillna(0)
            _cw_ = (_closed_p["pnl_dollar"] > 0).sum()
            _tot = _closed_p["pnl_dollar"].sum()
            _wr_ = _cw_ / len(_closed_p) * 100
            _sm1, _sm2, _sm3, _sm4 = st.columns(4)
            _sm1.metric("Win Rate", f"{_wr_:.0f}%", f"{int(_cw_)}W / {len(_closed_p)-int(_cw_)}L")
            _sm2.metric("Total P&L", f"${_tot:.0f}")
            _sm3.metric("Avg P&L", f"${_tot/len(_closed_p):.0f}")
            _sm4.metric("Trades", len(_closed_p))

            # Equity + per-trade bars
            _cp = _closed_p.sort_values("exit_date").reset_index(drop=True)
            _cp["cum"] = _cp["pnl_dollar"].cumsum()
            _ec_fig = _gmsp(rows=2, cols=1, shared_xaxes=True,
                           row_heights=[0.4, 0.6],
                           subplot_titles=["Trade P&L ($)", "Cumulative Equity ($)"])
            _bar_colors = ["#26a69a" if v > 0 else "#ef5350" for v in _cp["pnl_dollar"]]
            _ec_fig.add_trace(_ggo.Bar(
                x=list(range(len(_cp))), y=_cp["pnl_dollar"],
                marker_color=_bar_colors, name="Trade P&L",
                customdata=np.column_stack([_cp["ticker"], _cp["exit_reason"]]),
                hovertemplate="Trade %{x}<br>%{customdata[0]}<br>P&L: $%{y:.0f}<br>Exit: %{customdata[1]}<extra></extra>"
            ), row=1, col=1)
            _ec_fig.add_trace(_ggo.Scatter(
                x=list(range(len(_cp))), y=_cp["cum"],
                mode="lines+markers",
                line=dict(color="#FFD700", width=2),
                marker=dict(color=_bar_colors, size=8, line=dict(color="#FFD700", width=1)),
                name="Cumulative P&L",
                hovertemplate="Trade %{x}<br>Cumulative: $%{y:.0f}<extra></extra>"
            ), row=2, col=1)
            _ec_fig.add_hline(y=0, line=dict(color="#555", dash="dash"), row=2, col=1)
            _ec_fig.update_layout(
                height=500, showlegend=False,
                plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
                font=dict(family="Courier New, monospace"),
                title=dict(text="Position History — Equity Curve",
                          font=dict(color="#1565C0", size=15)),
                xaxis2=dict(title="Trade #", gridcolor="rgba(0,0,0,0.08)"),
                yaxis=dict(gridcolor="rgba(0,0,0,0.08)", zeroline=True, zerolinecolor="rgba(0,0,0,0.2)"),
                yaxis2=dict(gridcolor="rgba(0,0,0,0.08)", zeroline=True, zerolinecolor="rgba(0,0,0,0.2)"),
                margin=dict(t=50, b=40))
            st.plotly_chart(_ec_fig, use_container_width=True)
            st.dataframe(_closed_p[["ticker","spread_type","trade_date","expiry",
                                     "short_strike","long_strike","credit","exit_reason","pnl_dollar"]],
                         use_container_width=True, hide_index=True)

    # ════════════════════════════════════════════════════════════════
    # TAB 5: LOG TRADE
    # ════════════════════════════════════════════════════════════════
    with _tab_log:
        st.markdown("### \U0001f4dd Log a Trade")
        _gal_rst = st.button("🔄 Reset Form", key="ga_log_reset")
        if _gal_rst:
            for _k in ["f_tk2", "f_type2", "f_qty2", "f_sh2", "f_lg2", "f_cr2",
                       "f_acct2", "f_ent2", "f_exp2", "f_note2"]:
                st.session_state.pop(_k, None)
            st.rerun()
        with st.form("ga_log_form_v2"):
            _fl1, _fl2, _fl3 = st.columns(3)
            _f_tk   = _fl1.selectbox("Ticker", _ga_all_tickers, key="f_tk2")
            _f_type = _fl2.selectbox("Type", ["CALL_SPREAD","PUT_SPREAD"], key="f_type2")
            _f_qty  = _fl3.number_input("Contracts", value=1, min_value=1, key="f_qty2")
            _fl4, _fl5 = st.columns(2)
            _f_short = _fl4.number_input("Short Strike", value=0.0, step=0.5, key="f_sh2")
            _f_long  = _fl5.number_input("Long Strike",  value=0.0, step=0.5, key="f_lg2")
            _fl6, _fl7 = st.columns(2)
            _f_cr   = _fl6.number_input("Credit ($/share)", value=0.0, step=0.01, key="f_cr2")
            _f_acct = _fl7.number_input("Account size ($)", value=50000, step=1000, key="f_acct2")
            _fl8, _fl9 = st.columns(2)
            _f_ent  = _fl8.date_input("Entry Date", value=datetime.now(), key="f_ent2")
            _f_exp  = _fl9.date_input("Expiry", value=datetime.now()+timedelta(days=35), key="f_exp2")
            _f_note = st.text_area("Notes", placeholder="Why you took this trade…", key="f_note2")

            # Risk calculator inline
            if _f_short > 0 and _f_long > 0 and _f_cr > 0:
                _sw_ = abs(_f_long - _f_short)
                _ml_ = (_sw_ - _f_cr) * 100 * int(_f_qty)
                _mp_ = _f_cr * 100 * int(_f_qty)
                _acct_risk = _ml_ / _f_acct * 100 if _f_acct else 0
                _rrr = _mp_ / _ml_ if _ml_ else 0
                _rk1, _rk2, _rk3, _rk4 = st.columns(4)
                _rk1.metric("Max Profit", f"${_mp_:.0f}")
                _rk2.metric("Max Loss", f"${_ml_:.0f}")
                _rk3.metric("R:R", f"{_rrr:.2f}")
                _rk4.metric("Account Risk", f"{_acct_risk:.1f}%",
                            delta="✅ OK" if _acct_risk <= 2 else "⚠️ Too high",
                            delta_color="normal" if _acct_risk <= 2 else "inverse")

            _f_sub = st.form_submit_button("\U0001f4dd Log Trade", type="primary")
            if _f_sub:
                if _f_short > 0 and _f_long > 0 and _f_cr > 0:
                    _ga_conn.execute(
                        "INSERT INTO gamma_wall_trades "
                        "(ticker,trade_date,expiry,short_strike,long_strike,spread_type,"
                        " credit,quantity,advisor_notes,status) VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (_f_tk, _f_ent.strftime("%m-%d-%Y"), _f_exp.strftime("%m-%d-%Y"),
                         _f_short, _f_long, _f_type, _f_cr, int(_f_qty),
                         str(_f_note), "OPEN"))
                    _ga_conn.commit()
                    st.success(f"✅ Logged: {_f_tk} {_f_type} ${_f_short:.0f}/${_f_long:.0f}")
                else:
                    st.error("Fill all required fields.")

    # ════════════════════════════════════════════════════════════════
    # TAB 6: RULES
    # ════════════════════════════════════════════════════════════════
    with _tab_rules:
        st.markdown("### \U0001f4da Professional Rules — Gamma Wall Selling")
        st.markdown("""
<div>

#### What is a Gamma Wall?
A strike where call Open Interest ≥ **2.5× the average OI**. Market makers who sold these calls must
sell stock as price rises toward the strike to stay delta-neutral. This creates a **mechanical ceiling**
— not a prediction, a structural force from dealer hedging flows.

</div>
""", unsafe_allow_html=True)

        _r1, _r2 = st.columns(2)
        with _r1:
            st.markdown("""
**⏱️ Entry Rules (SpotGamma / tastytrade)**
| Rule | Requirement |
|------|------------|
| DTE | 21–50 DTE (45 DTE optimal) |
| Distance | Spot ≥1% below wall |
| Wall strength | ≥2.5× avg OI |
| GEX regime | Positive preferred |
| Earnings | No earnings within 7 days |
| IV Rank | >30% preferred |
| PCR | <2.0 on ETFs, <3.0 on stocks |
""")
        with _r2:
            st.markdown("""
**\U0001f6aa Exit Rules (Tastytrade 4,000-trade study)**
| Rule | Action |
|------|--------|
| 50% profit | Close — win rate 77%→83% |
| 2× stop | Buy back at 2× credit |
| 21 DTE | Close regardless of P&L |
| Breach watch | <1% from wall → consider close |
""")
        st.markdown("""
**\U0001f4ca Your Backtest Results (Jan–May 2026)**

| Ticker | Historical Win Rate | Observations |
|--------|-------------------|-------------|
| MSFT | **100%** | 85 trades |
| GOOGL | **92%** | 77 trades |
| SPY | **70%** | 56 trades |
| QQQ | **57%** | 53 trades |

*Wall held = spot stayed below call wall for 5 days after signal*

**Sources:** SpotGamma, Cem Karsan (@jam\\_croissant), tastytrade, r/thetagang, optionAlpha 0DTE studies
""")

    _ga_conn.close()

# ===================================================================
# ──  PAGE 7: NEWS & CALENDAR
# ===================================================================
elif page == "📰 News & Calendar":
    _hdr1, _hdr2 = st.columns([4, 1])
    with _hdr1:
        st.markdown("## 📰 Market News & Economic Calendar")
    with _hdr2:
        if st.button("🔄 Refresh", key="refresh_news"):
            st.rerun()
        with st.popover("ℹ️"):
            st.markdown(_PAGE_HELP.get(page, ""))

    tab1, tab2 = st.tabs(["📰 News", "📅 Economic Calendar"])

    with tab1:
        st.markdown("*Live news from multiple sources*")
        try:
            import feedparser
            feeds = {
                "Yahoo Finance": "https://finance.yahoo.com/news/rssindex",
                "MarketWatch": "https://feeds.marketwatch.com/marketwatch/topstories/",
                "CNBC": "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=100003114",
            }
            all_news = []
            for source, url in feeds.items():
                try:
                    feed = feedparser.parse(url)
                    for entry in feed.entries[:5]:
                        all_news.append(dict(
                            headline=entry.get("title", ""),
                            source=source,
                            link=entry.get("link", ""),
                            published=entry.get("published", ""),
                        ))
                except Exception:
                    pass
            if all_news:
                for n in all_news[:15]:
                    st.markdown(f"**[{n['headline']}]({n['link']})** — *{n['source']}* {n['published']}")
            else:
                st.info("No news available. Install feedparser: pip install feedparser")
        except ImportError:
            st.warning("Install feedparser for live news: `pip install feedparser`")
            st.info("Showing DB news instead...")
            try:
                news = q("SELECT * FROM news_feed ORDER BY ROWID DESC LIMIT 20")
                if not news.empty:
                    st.dataframe(news, hide_index=True)
            except Exception:
                pass

    with tab2:
        st.markdown("### 📅 Key Economic Events")
        st.caption("Approximate scheduled dates — always verify on the official Fed/BLS website before trading.")
        # Upcoming 2026 events (rolling schedule — update quarterly)
        events = [
            {"Event": "FOMC Meeting",       "Date": "2026-06-17", "Impact": "HIGH",
             "Description": "Federal Reserve interest rate decision. Markets move 1–3% on surprises."},
            {"Event": "CPI Report",         "Date": "2026-05-13", "Impact": "HIGH",
             "Description": "Consumer Price Index — inflation gauge. High CPI = Fed may hike rates."},
            {"Event": "PPI Report",         "Date": "2026-05-14", "Impact": "MEDIUM",
             "Description": "Producer Price Index — factory-level inflation. Leads CPI by ~1 month."},
            {"Event": "Retail Sales",       "Date": "2026-05-15", "Impact": "MEDIUM",
             "Description": "Consumer spending. Strong = economy healthy. Weak = slowdown risk."},
            {"Event": "Jobs Report (NFP)",  "Date": "2026-06-05", "Impact": "HIGH",
             "Description": "Non-Farm Payrolls. Biggest monthly jobs number — moves markets strongly."},
            {"Event": "PCE (Core)",         "Date": "2026-05-29", "Impact": "HIGH",
             "Description": "Fed's preferred inflation gauge. Directly influences rate decisions."},
            {"Event": "GDP Q1 2026",        "Date": "2026-05-28", "Impact": "HIGH",
             "Description": "First quarter 2026 GDP estimate. Negative = recession territory."},
            {"Event": "FOMC Meeting",       "Date": "2026-07-29", "Impact": "HIGH",
             "Description": "Next Fed rate decision after June."},
            {"Event": "CPI Report",         "Date": "2026-06-11", "Impact": "HIGH",
             "Description": "May 2026 inflation data."},
            {"Event": "Jobs Report (NFP)",  "Date": "2026-07-02", "Impact": "HIGH",
             "Description": "June 2026 jobs data."},
        ]
        ev_df = pd.DataFrame(events)
        ev_df["Days Until"] = (pd.to_datetime(ev_df["Date"]) - datetime.now()).dt.days
        # Sort: upcoming first, then past
        ev_df = ev_df.sort_values("Days Until")

        upcoming = ev_df[ev_df["Days Until"] >= 0]
        past     = ev_df[ev_df["Days Until"] < 0]

        if not upcoming.empty:
            st.markdown("**⏳ Upcoming**")
            for _, ev in upcoming.iterrows():
                impact_badge = "🔴" if ev["Impact"] == "HIGH" else "🟡" if ev["Impact"] == "MEDIUM" else "🟢"
                days = int(ev["Days Until"])
                urgency = "⚡ TODAY" if days == 0 else f"⚡ {days}d away" if days <= 3 else f"📌 {days}d"
                st.markdown(f"{impact_badge} **{ev['Event']}** — {ev['Date']} &nbsp; `{urgency}`")
                st.caption(ev["Description"])

        if not past.empty:
            with st.expander("📁 Past events (last 90 days)", expanded=False):
                for _, ev in past[past["Days Until"] >= -90].sort_values("Days Until", ascending=False).iterrows():
                    days = int(ev["Days Until"])
                    impact_badge = "🔴" if ev["Impact"] == "HIGH" else "🟡"
                    st.markdown(f"{impact_badge} ~~{ev['Event']}~~ — {ev['Date']} ({abs(days)}d ago)")
                    st.caption(ev["Description"])


# ===================================================================
# ──  PAGE 8: TRADE RISK CALCULATOR
# ===================================================================
elif page == "⚡ Trade Risk Calculator":
    _hdr1, _hdr2 = st.columns([4, 1])
    with _hdr1:
        st.markdown("## ⚡ Pre-Trade Risk & Loss Prediction")
    with _hdr2:
        if st.button("🔄 Refresh", key="refresh_risk"):
            st.rerun()
        with st.popover("ℹ️"):
            st.markdown(_PAGE_HELP.get(page, ""))
    st.markdown("*Bloomberg-style scenario analysis before you enter a trade*")

    _rc_reset_keys = ["rc_tk", "rc_type", "rc_strike", "rc_exp", "rc_entry", "rc_qty"]
    if st.button("🔄 Reset Fields", key="rc_reset"):
        for _k in _rc_reset_keys:
            st.session_state.pop(_k, None)
        st.rerun()

    c1, c2, c3, c4 = st.columns(4)
    rc_ticker = c1.text_input("Ticker", "SPY", key="rc_tk")
    rc_type = c2.selectbox("Option Type", ["call", "put"], key="rc_type")
    rc_strike = c3.number_input("Strike", value=580.0, key="rc_strike")
    rc_expiry = c4.date_input("Expiry", value=datetime.now() + timedelta(days=30), key="rc_exp")

    c5, c6 = st.columns(2)
    rc_entry = c5.number_input("Entry Price (per contract)", value=5.0, step=0.5, key="rc_entry")
    rc_qty = c6.number_input("Quantity", min_value=1, value=1, key="rc_qty")

    if st.button("🔍 Analyze Risk", type="primary"):
        with st.spinner("Running scenario analysis..."):
            risk = predict_trade_risk(rc_ticker, rc_type, rc_strike,
                                      rc_expiry.strftime("%Y-%m-%d"), rc_entry, rc_qty)

        if risk is None or "error" in risk:
            st.error(f"Error: {risk.get('error', 'Unknown')}" if risk else "Could not analyze.")
        else:
            # Key metrics
            st.markdown("<div>📊 Risk Profile</div>", unsafe_allow_html=True)
            c1, c2, c3, c4, c5 = st.columns(5)
            c1.metric("Stock Price", f"${risk['current_price']:.2f}")
            c2.metric("Theo Price", f"${risk['theo_price']:.2f}")
            c3.metric("Max Loss", f"${risk['max_loss']:,.2f}", delta="Total premium", delta_color="inverse")
            c4.metric("P(ITM)", f"{risk['prob_itm']:.1f}%")
            c5.metric("Hist Vol", f"{risk['sigma']:.1f}%")

            # Greeks
            st.markdown("<div>Greeks</div>", unsafe_allow_html=True)
            g = risk["greeks"]
            gc1, gc2, gc3, gc4, gc5 = st.columns(5)
            gc1.metric("Delta", f"{g['delta']:.4f}")
            gc2.metric("Gamma", f"{g['gamma']:.5f}")
            gc3.metric("Theta", f"${g['theta']*100:.2f}/day")
            gc4.metric("Vega", f"${g['vega']*100:.2f}")
            gc5.metric("Daily Vol", f"{risk['daily_vol_pct']:.2f}%")

            # OI Crowd positioning
            crowd = risk["crowd"]
            st.markdown("<div>🏟️ Crowd Positioning at This Strike</div>",
                        unsafe_allow_html=True)
            cc1, cc2, cc3, cc4 = st.columns(4)
            cc1.metric("Call OI", f"{crowd.get('call_oi', 0):,}")
            cc2.metric("Put OI", f"{crowd.get('put_oi', 0):,}")
            cc3.metric("PCR", f"{crowd.get('pcr', 0):.2f}")
            cb = crowd.get("crowd_bias", "N/A")
            badge = "badge-bull" if cb == "BULLISH" else "badge-bear" if cb == "BEARISH" else "badge-neutral"
            cc4.markdown(f"**Crowd Bias:** {cb}", unsafe_allow_html=True)

            # Escape analysis
            st.markdown(f"**Escape Difficulty:** {risk['escape']}")
            if risk["escape"] == "DIFFICULT":
                st.warning("⚠️ Low liquidity at this strike. If trade goes bad, exiting will be costly (wide spreads).")
            elif risk["escape"] == "MODERATE":
                st.info("Moderate liquidity. Use limit orders for best exit fills.")
            else:
                st.success("Good liquidity. Should be able to exit quickly with minimal slippage.")

            # Scenario matrix
            st.markdown("<div>📈 Scenario Matrix (P&L)</div>", unsafe_allow_html=True)
            scenarios = risk["scenarios"]
            for tf in ["1-Day", "3-Day", "5-Day"]:
                tf_df = scenarios[scenarios["timeframe"] == tf][["scenario", "stock_move", "new_price", "pnl", "pnl_pct"]]
                tf_df.columns = ["Scenario", "Stock Move", "Option Price", "P&L ($)", "P&L (%)"]
                st.markdown(f"**{tf} Scenarios:**")
                st.dataframe(tf_df, hide_index=True)

            # Visual heatmap
            pivot = scenarios.pivot(index="timeframe", columns="scenario", values="pnl")
            pivot = pivot.reindex(["1-Day", "3-Day", "5-Day"])
            col_order = ["Sharp Down", "Moderate Down", "Flat", "Moderate Up", "Sharp Up"]
            pivot = pivot[[c for c in col_order if c in pivot.columns]]

            fig = px.imshow(pivot, text_auto=".0f", color_continuous_scale="RdYlGn",
                           labels=dict(x="Scenario", y="Timeframe", color="P&L ($)"),
                           title="P&L Heatmap")
            fig.update_layout(template="plotly_white", height=300)
            st.plotly_chart(fig)

            # Bottom-line recommendation
            st.markdown("---")
            prob = risk["prob_itm"]
            escape = risk["escape"]
            max_loss = abs(risk["max_loss"])
            if prob > 60 and escape != "DIFFICULT":
                st.success(f"✅ **FAVORABLE SETUP** — {prob:.0f}% probability ITM, {escape.lower()} exit. Max risk: ${max_loss:,.0f}")
            elif prob > 40 and escape != "DIFFICULT":
                st.info(f"📊 **MODERATE SETUP** — {prob:.0f}% probability ITM. Consider position sizing. Max risk: ${max_loss:,.0f}")
            elif escape == "DIFFICULT":
                st.error(f"⛔ **CAUTION** — Low liquidity. P(ITM): {prob:.0f}%. Hard to exit if wrong. Max risk: ${max_loss:,.0f}")
            else:
                st.warning(f"⚠️ **LOW PROBABILITY** — Only {prob:.0f}% chance ITM. Speculative. Max risk: ${max_loss:,.0f}")

    # ── What-If Option Profit/Loss Simulator ──
    st.markdown("---")
    st.markdown("<div>🔮 Option P&L Simulator (OptionsStrat-style)</div>", unsafe_allow_html=True)
    _sim_hdr, _sim_rst = st.columns([6, 1])
    _sim_hdr.markdown("*Drag the sliders to explore option value at any stock price & date — see P&L chart live*")
    if _sim_rst.button("🔄 Reset", key="sim_reset"):
        for _k in ["sim_tk", "sim_otype", "sim_strike", "sim_expiry", "sim_entry", "sim_qty", "sim_buy_dt", "sim_iv_ovr"]:
            st.session_state.pop(_k, None)
        st.rerun()

    sim_c1, sim_c2, sim_c3, sim_c4 = st.columns(4)
    sim_ticker = sim_c1.text_input("Ticker", value="SPY", key="sim_tk")
    sim_opt_type = sim_c2.selectbox("Option Type", ["call", "put"], key="sim_otype")
    sim_strike = sim_c3.number_input("Strike", value=580.0, step=1.0, key="sim_strike")
    sim_expiry = sim_c4.date_input("Option Expiry", value=datetime.now() + timedelta(days=30), key="sim_expiry")

    sim_c5, sim_c6, sim_c7 = st.columns(3)
    sim_entry_px = sim_c5.number_input("Entry Premium ($)", value=5.0, step=0.1, key="sim_entry",
                                        help="Price you paid/plan to pay per share")
    sim_qty = sim_c6.number_input("Contracts", min_value=1, value=1, key="sim_qty")
    sim_buy_date = sim_c7.date_input("Buy Date", value=datetime.now().date(), key="sim_buy_dt",
                                      help="When you bought / plan to buy")

    # Auto-fetch IV + spot (AH-aware)
    _sim_iv = 0.30
    _sim_iv_src = "Default (30%)"
    _sim_spot = 580.0
    try:
        _sim_tk_obj = yf.Ticker(sim_ticker)
        _sim_eod = float(_sim_tk_obj.history(period="1d")["Close"].iloc[-1])
        _sim_ah_d = _get_ah_price(sim_ticker)
        _sim_spot = (_sim_ah_d["spot_ah"] if _sim_ah_d["spot_ah"] > 0 else _sim_eod) \
                    if st.session_state.get("use_ah") else _sim_eod
        if _sim_ah_d["is_extended"]:
            st.caption(f"🌙 AH price: **${_sim_ah_d['spot_ah']:.2f}** ({_sim_ah_d['ah_chg_pct']:+.1f}%)  EOD: ${_sim_eod:.2f}")
        try:
            _sim_chain = _sim_tk_obj.option_chain(sim_expiry.strftime("%Y-%m-%d"))
            _sim_oc = _sim_chain.calls if sim_opt_type == "call" else _sim_chain.puts
            _sim_m = _sim_oc[_sim_oc["strike"] == float(sim_strike)]
            if not _sim_m.empty and "impliedVolatility" in _sim_m.columns:
                _fiv = float(_sim_m.iloc[0]["impliedVolatility"])
                if _fiv > 0.01:
                    _sim_iv = _fiv
                    _sim_iv_src = f"Live ({_sim_iv:.1%})"
            elif not _sim_oc.empty and "impliedVolatility" in _sim_oc.columns:
                _sim_oc2 = _sim_oc.copy()
                _sim_oc2["_d"] = abs(_sim_oc2["strike"] - float(sim_strike))
                _nr = _sim_oc2.sort_values("_d").iloc[0]
                _fiv = float(_nr["impliedVolatility"])
                if _fiv > 0.01:
                    _sim_iv = _fiv
                    _sim_iv_src = f"Nearest ({_sim_iv:.1%})"
        except Exception:
            pass
    except Exception:
        pass

    with st.expander("⚙️ Advanced: Override IV", expanded=False):
        _sim_iv_ovr = st.number_input("IV Override (0 = auto)", value=0.0, min_value=0.0, max_value=3.0, step=0.01, key="sim_iv_ovr")
        if _sim_iv_ovr > 0.001:
            _sim_iv = _sim_iv_ovr
            _sim_iv_src = f"Manual ({_sim_iv:.1%})"

    _sim_mode = "🌙 AH" if st.session_state.get("use_ah") else "☀️ EOD"
    st.caption(f"📊 {_sim_mode} Spot: ${_sim_spot:.2f} | IV: {_sim_iv:.1%} ({_sim_iv_src})")

    # ── Sliders ──
    sim_exp_dt = datetime.combine(sim_expiry, datetime.min.time())
    _total_dte = max((sim_exp_dt - datetime.now()).days, 1)
    _price_low = round(_sim_spot * 0.80, 2)
    _price_high = round(_sim_spot * 1.20, 2)

    st.markdown("#### 🎚️ Drag to Explore")
    sl_c1, sl_c2 = st.columns(2)
    sim_target_price = sl_c1.slider(
        "📈 Future Stock Price ($)", min_value=_price_low, max_value=_price_high,
        value=round(_sim_spot, 2), step=0.5, key="sim_sl_px")
    sim_days_fwd = sl_c2.slider(
        "📅 Days from now", min_value=0, max_value=_total_dte, value=min(7, _total_dte), key="sim_sl_days")
    _sim_target_date = datetime.now() + timedelta(days=sim_days_fwd)

    # Calculate at slider position
    _sim_T = max((sim_exp_dt - _sim_target_date).days, 0) / 365.0
    _sim_g = bs_greeks(sim_target_price, sim_strike, _sim_T, 0.045, _sim_iv, sim_opt_type)
    _sim_theo = _sim_g["price"]
    _sim_pnl = (_sim_theo - sim_entry_px) * sim_qty * 100
    _sim_pnl_pct = (_sim_theo - sim_entry_px) / sim_entry_px * 100 if sim_entry_px > 0 else 0
    _pnl_c = "#00c853" if _sim_pnl >= 0 else "#ff1744"

    # ── Result card at slider position ──
    _sim_intr = max(0, sim_target_price - sim_strike) if sim_opt_type == "call" else max(0, sim_strike - sim_target_price)
    st.markdown(
        f"<div style='background:var(--panel-solid);color:var(--text);border-radius:12px;"
        f"padding:18px 24px;margin:12px 0;border-left:5px solid {_pnl_c};"
        f"box-shadow:0 2px 8px rgba(0,0,0,0.08);'>"
        f"<h3>{sim_ticker} {sim_opt_type.upper()} ${sim_strike:.0f} — "
        f"Day {sim_days_fwd} ({_sim_target_date.strftime('%b %d')})</h3>"
        f"<table><tr>"
        f"<td><b>Stock Price</b></td><td>{sim_target_price:.2f}</td>"
        f"<td><b>Option Value</b></td><td>{_sim_theo:.2f}</td>"
        f"<td><b>P&L ({sim_qty} contract{'s' if sim_qty > 1 else ''})</b></td><td>{_sim_pnl:+,.2f} ({_sim_pnl_pct:+.1f}%)</td>"
        f"<td><b>Delta</b></td><td>{_sim_g['delta']:.3f}</td>"
        f"<td><b>DTE</b></td><td>{max((sim_exp_dt - _sim_target_date).days, 0)}</td>"
        f"</tr></table>"
        f"<p>Intrinsic: ${_sim_intr:.2f} | "
        f"Time Value: ${max(0, _sim_theo - _sim_intr):.2f} | "
        f"Theta: ${_sim_g['theta']*100:.2f}/day | Gamma: {_sim_g['gamma']:.5f}</p>"
        f"</div>",
        unsafe_allow_html=True,
    )

    # ── P&L Chart: multiple date lines across stock prices ──
    st.markdown("<div>📈 P&L Chart — Option Value vs Stock Price at Different Dates</div>",
                unsafe_allow_html=True)
    _s_range = np.linspace(_price_low, _price_high, 80)
    # Date lines: today, selected day, halfway to expiry, expiry
    _date_steps = sorted(set([0, sim_days_fwd, _total_dte // 2, _total_dte]))
    _date_colors = ["#0066cc", "#ff9100", "#9c27b0", "#ff1744"]

    fig_sim = go.Figure()
    for idx, d in enumerate(_date_steps):
        _d_dt = datetime.now() + timedelta(days=d)
        _d_T = max((sim_exp_dt - _d_dt).days, 0) / 365.0
        _pnl_line = []
        for s in _s_range:
            _g = bs_greeks(s, sim_strike, _d_T, 0.045, _sim_iv, sim_opt_type)
            _p = (_g["price"] - sim_entry_px) * sim_qty * 100
            _pnl_line.append(_p)
        _lbl = f"Day {d}" if d > 0 else "Today"
        if d == _total_dte:
            _lbl = "At Expiry"
        fig_sim.add_trace(go.Scatter(
            x=_s_range, y=_pnl_line, mode="lines", name=_lbl,
            line=dict(color=_date_colors[idx % len(_date_colors)],
                      width=3 if d == sim_days_fwd else 1.5,
                      dash="solid" if d == sim_days_fwd else "dot"),
        ))
    # Breakeven line
    fig_sim.add_hline(y=0, line_dash="dash", line_color="#888", annotation_text="Breakeven")
    # Current spot marker
    fig_sim.add_vline(x=_sim_spot, line_dash="dot", line_color="#e65100",
                      annotation_text=f"Spot ${_sim_spot:.0f}")
    # Strike marker
    fig_sim.add_vline(x=sim_strike, line_dash="dot", line_color="#999",
                      annotation_text=f"Strike ${sim_strike:.0f}")
    # Slider position marker
    fig_sim.add_trace(go.Scatter(
        x=[sim_target_price], y=[_sim_pnl], mode="markers",
        marker=dict(size=14, color=_pnl_c, symbol="diamond", line=dict(width=2, color="white")),
        name=f"Your target: ${sim_target_price:.0f}",
        showlegend=True,
    ))
    fig_sim.update_layout(
        template="plotly_white", height=450,
        xaxis_title="Stock Price ($)", yaxis_title=f"P&L ($) — {sim_qty} contract(s)",
        legend=dict(orientation="h", y=1.12),
        margin=dict(t=50, b=40),
    )
    st.plotly_chart(fig_sim)

    # ── P&L Heatmap: Date vs Stock Price ──
    st.markdown("<div>🗺️ P&L Heatmap — Days vs Stock Price</div>", unsafe_allow_html=True)
    _hm_prices = np.linspace(_price_low, _price_high, 25)
    _hm_days = list(range(0, _total_dte + 1, max(1, _total_dte // 12)))
    if _total_dte not in _hm_days:
        _hm_days.append(_total_dte)
    _hm_data = []
    for d in _hm_days:
        _d_dt = datetime.now() + timedelta(days=d)
        _d_T = max((sim_exp_dt - _d_dt).days, 0) / 365.0
        row = []
        for s in _hm_prices:
            _g = bs_greeks(s, sim_strike, _d_T, 0.045, _sim_iv, sim_opt_type)
            _p = (_g["price"] - sim_entry_px) * sim_qty * 100
            row.append(round(_p, 2))
        _hm_data.append(row)
    fig_hm = px.imshow(
        _hm_data, text_auto=".0f",
        x=[f"${s:.0f}" for s in _hm_prices],
        y=[f"Day {d}" for d in _hm_days],
        color_continuous_scale="RdYlGn", color_continuous_midpoint=0,
        labels=dict(x="Stock Price", y="Days Forward", color="P&L ($)"),
    )
    fig_hm.update_layout(template="plotly_white", height=380, margin=dict(t=30, b=40))
    st.plotly_chart(fig_hm)

    # ── Breakeven Analysis ──
    st.markdown("<div>🎯 Key Levels</div>", unsafe_allow_html=True)
    # Find breakeven stock price at slider date
    _be_price = None
    for s in np.linspace(_price_low, _price_high, 500):
        _g = bs_greeks(s, sim_strike, _sim_T, 0.045, _sim_iv, sim_opt_type)
        if abs(_g["price"] - sim_entry_px) < 0.02:
            _be_price = s
            break
    _max_loss = -sim_entry_px * sim_qty * 100
    _be_str = f"${_be_price:.2f}" if _be_price else "N/A"
    kl1, kl2, kl3, kl4, kl5 = st.columns(5)
    kl1.metric("Breakeven (at slider date)", _be_str)
    kl2.metric("Max Loss", f"${_max_loss:,.2f}")
    kl3.metric("Cost Basis", f"${sim_entry_px * sim_qty * 100:,.2f}")
    kl4.metric("Current Option Value", f"${_sim_theo:.2f}")
    if sim_opt_type == "call":
        kl5.metric("Upside if +10%", f"${bs_greeks(_sim_spot * 1.10, sim_strike, _sim_T, 0.045, _sim_iv, sim_opt_type)['price']:.2f}")
    else:
        kl5.metric("Upside if -10%", f"${bs_greeks(_sim_spot * 0.90, sim_strike, _sim_T, 0.045, _sim_iv, sim_opt_type)['price']:.2f}")


# ===================================================================
# ──  PAGE 10: NEXT-DAY EXIT PLANNER
# ===================================================================
elif page == "🎯 Next-Day Exit Planner":
    _hdr1, _hdr2 = st.columns([4, 1])
    with _hdr1:
        st.markdown("## 🎯 Next-Day Exit Planner")
    with _hdr2:
        if st.button("🔄 Refresh", key="refresh_exit_planner"):
            st.rerun()
        with st.popover("ℹ️"):
            st.markdown(_PAGE_HELP.get(page, ""))
    st.markdown("*Pre-market intelligence: predict tomorrow's option value, set optimal sell orders, and protect your capital*")

    with st.expander("📘 How to Use This Page — Exit Planner Guide", expanded=False):
        st.markdown("""
**Purpose:** This page gives you a pre-market daily brief on every open option position.
It fetches live market mid-prices (bid+ask)/2 and compares them to what you paid, telling you
whether to hold, take profit, or cut losses before tomorrow's open.

**What each mode does:**
| Mode | Best for |
|---|---|
| 📋 Individual Position | Deep dive on one specific trade — scenarios, Greeks, Monte Carlo |
| 🏢 All positions — by Ticker | Full picture on a stock you're watching — payoff chart, OI, news |
| 🌐 All Open Positions | Morning scan across everything — spot any positions needing action |

**Decision framework (corporate standard):**
- 🟢 **TAKE PROFIT** (>50% gain): Close at least half. Options decay — realized gains beat paper gains.
- 🔴 **CUT LOSS** (>30% loss): Exit or roll. The 2:1 rule — if you wouldn't enter this trade today, exit it.
- ⚠️ **NEAR EXPIRY** (<5 DTE in loss): Theta decay is exponential in the last 5 days. Close or roll immediately.
- 🟡 **CLOSE SOON** (<5 DTE in profit): Lock in gains — don't let time decay erode a winner.
- ⚪ **HOLD**: No urgent action. Set a mental stop (typically 25–30% below your entry on any remaining gain).

**Reading the P&L column:**
- `$7.29→$1.66` = option premium dropped from $7.29 to $1.66 (↓77%) — this is the *option price*, not stock price
- Each contract controls 100 shares, so a $1 move = $100 P&L per contract
- For **SELL** positions: you collected premium upfront; profit = entry premium − current mid

**Risk management at a glance:**
The TOTAL row shows your net unrealized P&L across all positions.
Positive = portfolio is net profitable. Negative = review which legs to cut first (start with highest loss %).
        """)

    # ── Load from Portfolio ──
    _ep_open_trades = pd.DataFrame()
    try:
        _ep_conn = get_conn()
        _ep_open_trades = pd.read_sql("SELECT * FROM trades WHERE status='OPEN'", _ep_conn)
        _ep_conn.close()
    except Exception:
        pass

    # ── Analysis Mode selector ──
    _ep_mode = st.radio(
        "Analysis Mode",
        ["🌅 Next-Day Game Plan", "📋 Individual Position",
         "🏢 All positions — by Ticker", "🌐 All Open Positions"],
        horizontal=True, key="ep_analysis_mode",
    )

    # ══════════════════════════════════════════════════════════════════
    #  NEXT-DAY GAME PLAN — whole-portfolio scenario analysis + action plan
    # ══════════════════════════════════════════════════════════════════
    if _ep_mode == "🌅 Next-Day Game Plan":
        st.caption("Whole-portfolio view: what tomorrow's open could do to your positions, the key "
                   "levels to watch, and a ranked action checklist. Built from your DB (latest close + "
                   "stored option prices) with Black-Scholes — no waiting on live feeds.")
        _gp_conn = get_conn()
        try:
            _gp_tr = pd.read_sql("SELECT * FROM trades WHERE status='OPEN'", _gp_conn)
        except Exception:
            _gp_tr = pd.DataFrame()
        if _gp_tr.empty:
            st.info("No open positions in the portfolio.")
            try: _gp_conn.close()
            except Exception: pass
            st.stop()

        # ── Global / cross-asset backdrop (oil, gold, FX, rates, China/EM, crypto) ──
        st.markdown("#### 🌐 Global macro backdrop (overnight)")
        try:
            _mac, _mlab, _mscore = _macro_backdrop()
        except Exception:
            _mac, _mlab, _mscore = {}, "MIXED", 0
        if _mac:
            _mcol = {"RISK-ON": "🟢", "RISK-OFF": "🔴", "MIXED": "🟡"}[_mlab]
            st.markdown(f"**{_mcol} Risk read: {_mlab}** (score {_mscore:+d})")
            _order = ["S&P fut", "Nasdaq fut", "VIX", "WTI oil", "Gold", "Dollar (DXY)",
                      "US 10y", "China (FXI)", "EM (EEM)", "Bitcoin"]
            _shown = [k for k in _order if k in _mac]
            for _i in range(0, len(_shown), 5):
                _cols = st.columns(5)
                for _j, _k in enumerate(_shown[_i:_i + 5]):
                    _v = _mac[_k]
                    _cols[_j].metric(_k, f"{_v['price']:,.2f}", f"{_v['pct']:+.2f}%",
                                     delta_color="normal" if _v["pct"] >= 0 else "inverse")
            st.info(_macro_writeup(_mac, _mlab))
        else:
            st.caption("Macro feed unavailable right now (yfinance rate-limited).")

        _R = 0.045

        def _gp_spot(tk):
            try:
                row = _gp_conn.execute(
                    "SELECT close FROM stock_daily WHERE UPPER(ticker)=? ORDER BY "
                    "substr(trade_date,7,4)||substr(trade_date,1,2)||substr(trade_date,4,2) DESC LIMIT 1",
                    (tk.upper(),)).fetchone()
                return float(row[0]) if row else None
            except Exception:
                return None

        def _gp_to_mdy(s):
            for fmt in ("%Y-%m-%d", "%m-%d-%Y"):
                try: return datetime.strptime(str(s), fmt).strftime("%m-%d-%Y")
                except Exception: pass
            return None

        def _gp_premium(tk, K, exp_mdy, typ):
            col = "lastPrice_Call_now" if typ == "call" else "lastPrice_Put_now"
            try:
                pr = pd.read_sql(
                    f"SELECT {col} AS last FROM options_change WHERE UPPER(ticker)=? AND strike=? "
                    "AND expiry_date=? ORDER BY substr(trade_date_now,7,4)||substr(trade_date_now,1,2)"
                    "||substr(trade_date_now,4,2) DESC LIMIT 1",
                    _gp_conn, params=(tk.upper(), float(K), exp_mdy))
                if not pr.empty and pr.iloc[0]["last"] and float(pr.iloc[0]["last"]) > 0:
                    return float(pr.iloc[0]["last"])
            except Exception:
                pass
            return None

        # ── Build per-leg analytics ──
        _legs = []
        for _, _t in _gp_tr.iterrows():
            _tk = str(_t["ticker"]).upper()
            _typ = "call" if str(_t["option_type"]).lower().startswith("c") else "put"
            _K = float(_t["strike"] or 0)
            _qty = int(_t["quantity"] or 0)
            _exp = str(_t["expiry"])
            _exp_mdy = _gp_to_mdy(_exp)
            _spot = _gp_spot(_tk)
            _dte = None
            for _fmt in ("%Y-%m-%d", "%m-%d-%Y"):
                try:
                    _dte = (datetime.strptime(_exp, _fmt) - datetime.now()).days; break
                except Exception:
                    pass
            if _spot is None or _dte is None or _K <= 0 or _qty == 0:
                continue
            _T = max(_dte, 0) / 365.0
            _entry = float(_t["entry_price"] or 0)
            _prem = _gp_premium(_tk, _K, _exp_mdy, _typ)
            _iv = _implied_vol(_prem, _spot, _K, _T, _R, _typ) if (_prem and _T > 0) else (float(_t["entry_iv"] or 0) or 0.30)
            _g = bs_greeks(_spot, _K, _T, _R, _iv, _typ) if _T > 0 else {"delta": 0, "gamma": 0, "theta": 0, "vega": 0, "price": (_prem or _entry)}
            _cur = _prem if _prem else _g.get("price", _entry)
            _m = _qty * 100                                   # signed contract multiplier
            _side = "short" if _qty < 0 else "long"
            _be = (_K + _entry) if _typ == "call" else (_K - _entry)   # long breakeven ref
            _legs.append({
                "ticker": _tk, "typ": _typ, "K": _K, "qty": _qty, "side": _side,
                "exp": _exp, "exp_mdy": _exp_mdy, "dte": _dte, "spot": _spot,
                "iv": _iv, "entry": _entry, "cur": _cur, "g": _g, "m": _m, "be": _be,
                "pnl": (_cur - _entry) * _m,
                "pos_delta": _g["delta"] * _m, "pos_theta": _g["theta"] * _m,
                "pos_vega": _g["vega"] * _m, "pos_gamma": _g["gamma"] * _m,
                "ddelta_1pct": _g["delta"] * _m * _spot * 0.01,
            })

        if not _legs:
            st.warning("Couldn't price any positions (missing spot/expiry data).")
            try: _gp_conn.close()
            except Exception: pass
            st.stop()

        # ── Portfolio Greeks strip ──
        _net_ddelta = sum(l["ddelta_1pct"] for l in _legs)
        _net_theta = sum(l["pos_theta"] for l in _legs)
        _net_vega = sum(l["pos_vega"] for l in _legs)
        _net_pnl = sum(l["pnl"] for l in _legs)
        _gross_risk = sum(abs(l["cur"] * l["m"]) for l in _legs if l["side"] == "long") \
            + sum(l["entry"] * abs(l["m"]) for l in _legs if l["side"] == "short")
        st.markdown("#### 🧮 Portfolio Greeks (next-day exposure)")
        _pg = st.columns(4)
        _pg[0].metric("Net Δ per +1% move", f"${_net_ddelta:,.0f}",
                      "bullish" if _net_ddelta >= 0 else "bearish",
                      delta_color="normal" if _net_ddelta >= 0 else "inverse")
        _pg[1].metric("Theta / day", f"${_net_theta:,.0f}",
                      "you collect" if _net_theta >= 0 else "decay drag",
                      delta_color="normal" if _net_theta >= 0 else "inverse")
        _pg[2].metric("Vega per +1 vol pt", f"${_net_vega:,.0f}")
        _pg[3].metric("Open P&L", f"${_net_pnl:,.0f}",
                      delta_color="normal" if _net_pnl >= 0 else "inverse")
        st.caption(f"Net Δ: a market +1% tomorrow ≈ **${_net_ddelta:,.0f}** P&L. "
                   f"Time decay runs **${_net_theta:,.0f}/day**. "
                   f"A +1 vol-point IV change ≈ **${_net_vega:,.0f}**.")

        # ── Portfolio risk: 1-day historical VaR + concentration ──
        st.markdown("#### 🛡️ Portfolio risk")
        _var = _portfolio_var(_legs)
        _vc = st.columns(3)
        if _var:
            _vc[0].metric("1-day 95% VaR", f"${_var['var']:,.0f}", "worst realistic day", delta_color="inverse")
            _vc[1].metric("Expected shortfall", f"${_var['cvar']:,.0f}", "avg of the bad tail", delta_color="inverse")
            _vc[2].metric("Worst sim day", f"${_var['worst']:,.0f}", f"of {_var['n']} days", delta_color="inverse")
            st.caption(f"Replays the last ~{_var['n']} daily moves of your underlyings through the book "
                       "(BS reprice + 1 day decay). 95% VaR = on a bad-but-normal day you lose about this much; "
                       "expected shortfall = average loss on the worst 5% of days.")
        else:
            st.caption("VaR needs more price history for these names.")
        _gross = {}
        for l in _legs:
            gr = abs(l["cur"] * l["m"]) if l["side"] == "long" else l["entry"] * abs(l["m"])
            _gross[l["ticker"]] = _gross.get(l["ticker"], 0.0) + gr
        _tot = sum(_gross.values()) or 1.0
        _top = max(_gross, key=_gross.get)
        _toppct = _gross[_top] / _tot * 100
        if _toppct >= 50:
            st.warning(f"⚠️ **Concentration:** {_top} is **{_toppct:.0f}%** of your capital-at-risk. "
                       "A single-name shock hits hard — consider diversifying or hedging it.")
        else:
            st.caption("Capital-at-risk split: "
                       + " · ".join(f"{k} {v/_tot*100:.0f}%" for k, v in sorted(_gross.items(), key=lambda x: -x[1])))

        # ── Next-day market-shock P&L grid ──
        st.markdown("#### 📉 Tomorrow's scenarios — portfolio P&L vs a market move")
        _shocks = [-0.03, -0.02, -0.01, -0.005, 0.0, 0.005, 0.01, 0.02, 0.03]
        _scn_rows = []
        for s in _shocks:
            tot = 0.0
            for l in _legs:
                ns = l["spot"] * (1 + s)
                t1 = max(l["dte"] - 1, 0) / 365.0
                iv_s = max(l["iv"] * (1 - 2.0 * s), 0.05)     # vol rises when market drops
                np_ = bs_greeks(ns, l["K"], t1, _R, iv_s, l["typ"]).get("price", l["cur"]) if t1 > 0 else \
                    (max(ns - l["K"], 0) if l["typ"] == "call" else max(l["K"] - ns, 0))
                tot += (np_ - l["cur"]) * l["m"]
            _scn_rows.append({"Market move": f"{s*100:+.1f}%", "Portfolio P&L $": round(tot),
                              "_s": s, "_pnl": tot})
        _scn = pd.DataFrame(_scn_rows)
        _sc1, _sc2 = st.columns([2, 3])
        with _sc1:
            _disp = _scn[["Market move", "Portfolio P&L $"]].copy()
            st.dataframe(_disp, hide_index=True, use_container_width=True,
                         column_config={"Portfolio P&L $": st.column_config.NumberColumn(format="$%d")})
        with _sc2:
            _sfig = go.Figure(go.Bar(
                x=_scn["Market move"], y=_scn["_pnl"],
                marker_color=["#ff5c6c" if v < 0 else "#00e676" for v in _scn["_pnl"]]))
            _sfig.update_layout(template="plotly_dark", height=300,
                                title="Next-day P&L by market move (1 day of decay + vol shift)",
                                xaxis_title="Market move", yaxis_title="P&L $",
                                margin=dict(t=42, b=10))
            st.plotly_chart(_sfig, use_container_width=True)
        _down = next(r["_pnl"] for r in _scn_rows if abs(r["_s"] + 0.02) < 1e-9)
        _up = next(r["_pnl"] for r in _scn_rows if abs(r["_s"] - 0.02) < 1e-9)
        st.caption(f"A **−2%** gap tomorrow ≈ **${_down:,.0f}**; a **+2%** gap ≈ **${_up:,.0f}**. "
                   "Includes one day of theta and a simple vol bump on down moves.")

        # ── Stock-by-stock game plan (levels + news + sentiment + writeup + legs) ──
        st.markdown("#### 🏢 Stock-by-stock game plan")
        _by_tk = {}
        for l in _legs:
            _by_tk.setdefault(l["ticker"], []).append(l)
        _checklist = []
        for _tk, _tl in _by_tk.items():
            _spot = _tl[0]["spot"]
            _ivs = sorted(x["iv"] for x in _tl); _ivm = _ivs[len(_ivs) // 2]
            _em = _spot * _ivm * (1 / 252.0) ** 0.5
            _tk_dd = sum(x["ddelta_1pct"] for x in _tl)
            _tk_th = sum(x["pos_theta"] for x in _tl)
            _tk_pnl = sum(x["pnl"] for x in _tl)
            _near = min(_tl, key=lambda x: x["dte"])
            try:
                _chain = pd.read_sql(
                    "SELECT strike, openInt_Call_now, openInt_Put_now, R1, S1 FROM options_change "
                    "WHERE UPPER(ticker)=? AND expiry_date=?",
                    _gp_conn, params=(_tk, _near["exp_mdy"]))
            except Exception:
                _chain = pd.DataFrame()
            _w = compute_walls(_chain, _spot) if not _chain.empty else {}
            _r1 = _s1 = None
            if not _chain.empty:
                try:
                    _r1 = float(_chain["R1"].dropna().iloc[0]); _s1 = float(_chain["S1"].dropna().iloc[0])
                except Exception:
                    pass
            _nw = _ticker_news(_tk)
            _stt = _stocktwits_sentiment(_tk)
            _te = {"BULLISH": "🟢", "BEARISH": "🔴", "MIXED": "🟡", "NEUTRAL": "⚪"}[_nw["label"]]
            with st.expander(f"{_te} {_tk} · ${_spot:.2f} · {len(_tl)} legs · open P&L ${_tk_pnl:,.0f} · news {_nw['label']}",
                             expanded=True):
                _mc = st.columns(4)
                _mc[0].metric("Spot", f"${_spot:.2f}")
                _mc[1].metric("1-day exp. move", f"±${_em:.2f}", f"±{_em/_spot*100:.1f}%")
                _mc[2].metric("Δ per +1%", f"${_tk_dd:,.0f}")
                _mc[3].metric("Theta / day", f"${_tk_th:,.0f}")
                _lv = []
                if _w.get("put_wall"): _lv.append(f"🟩 put wall ${_w['put_wall']:.0f}")
                if _w.get("call_wall"): _lv.append(f"🟥 call wall ${_w['call_wall']:.0f}")
                if _s1: _lv.append(f"S1 ${_s1:.0f}")
                if _r1: _lv.append(f"R1 ${_r1:.0f}")
                if _lv:
                    st.markdown("**Key levels:** " + " · ".join(_lv))
                if _stt:
                    _ste = {"BULLISH": "🟢", "BEARISH": "🔴", "MIXED": "🟡"}.get(_stt["label"], "⚪")
                    st.markdown(f"**💬 StockTwits crowd:** {_ste} {_stt['label']} "
                                f"({_stt['bull']} bullish / {_stt['bear']} bearish)")
                _fh = _finnhub_sentiment(_tk)
                if _fh:
                    _fhe = {"BULLISH": "🟢", "BEARISH": "🔴", "MIXED": "🟡"}.get(_fh["label"], "⚪")
                    _buzz = f" · buzz {_fh['buzz']:.1f}×" if _fh.get("buzz") else ""
                    st.markdown(f"**🛰 Finnhub news-sentiment:** {_fhe} {_fh['label']} "
                                f"({_fh['bull_pct']:.0f}% bullish){_buzz}")
                _ivr = _iv_rank(_tk)
                if _ivr:
                    _hint = ("🟢 cheap — favor buying premium / long options" if _ivr["rank"] < 30
                             else "🔴 rich — favor selling premium / spreads" if _ivr["rank"] > 70
                             else "🟡 mid-range")
                    st.markdown(f"**🌡️ IV Rank {_ivr['rank']:.0f}** (IV {_ivr['iv']*100:.0f}% vs "
                                f"{_ivr['lo']*100:.0f}–{_ivr['hi']*100:.0f}% over 6mo) — {_hint}")
                _earn = _next_earnings(_tk)
                if _earn and _earn["days"] <= 14:
                    st.warning(f"📅 **{_tk} earnings in {_earn['days']}d ({_earn['date']})** — binary gap "
                               "risk; consider sizing down or closing options before the print.")
                elif _earn:
                    st.caption(f"📅 Next {_tk} earnings: {_earn['date']} ({_earn['days']}d out).")
                st.info(_gp_writeup(_tk, _spot, _em, _w, _r1, _s1, _tk_dd, _tk_th, _nw, _tl, _stt))

                _sm = []
                for s in (-0.03, -0.02, -0.01, 0.0, 0.01, 0.02, 0.03):
                    tot = 0.0
                    for l in _tl:
                        ns = l["spot"] * (1 + s); t1 = max(l["dte"] - 1, 0) / 365.0
                        ivs = max(l["iv"] * (1 - 2.0 * s), 0.05)
                        npx = bs_greeks(ns, l["K"], t1, _R, ivs, l["typ"]).get("price", l["cur"]) if t1 > 0 \
                            else (max(ns - l["K"], 0) if l["typ"] == "call" else max(l["K"] - ns, 0))
                        tot += (npx - l["cur"]) * l["m"]
                    _sm.append({f"If {_tk} moves": f"{s*100:+.0f}%", "P&L $": round(tot)})
                st.dataframe(pd.DataFrame(_sm), hide_index=True, use_container_width=True,
                             column_config={"P&L $": st.column_config.NumberColumn(format="$%d")})

                if _nw["items"]:
                    st.markdown("**📰 Latest headlines & tone:**")
                    for it in _nw["items"]:
                        e = "🟢" if it["tone"] > 0 else ("🔴" if it["tone"] < 0 else "⚪")
                        st.markdown(f"- {e} [{it['title']}]({it['link']}) · _{it['source']} {it['when']}_")
                else:
                    st.caption("No fresh headlines found (feeds may be rate-limited).")

                _rows = []
                for l in _tl:
                    money = "ITM" if ((l["spot"] > l["K"]) if l["typ"] == "call" else (l["spot"] < l["K"])) else "OTM"
                    pnl_pct = ((l["cur"] - l["entry"]) / l["entry"] * 100 * (1 if l["qty"] > 0 else -1)) if l["entry"] else 0
                    acts = []
                    if l["dte"] <= 7:
                        acts.append(f"{l['dte']}DTE — decide now")
                    elif l["dte"] <= 21:
                        acts.append(f"{l['dte']}DTE — plan exit/roll")
                    if l["side"] == "short" and money == "ITM":
                        acts.append("ITM short — assignment risk")
                    if pnl_pct >= 50:
                        acts.append("up ≥50% — take profit")
                    elif pnl_pct <= -50:
                        acts.append("down ≥50% — cut/roll")
                    action = "; ".join(acts) if acts else "hold & monitor"
                    if acts and (l["dte"] <= 21 or (l["side"] == "short" and money == "ITM")):
                        _rs = _roll_suggestion(_gp_conn, l)
                        if _rs:
                            action += f" · {_rs}"
                    _rows.append({
                        "Leg": f"{l['side']} {abs(l['qty'])}× ${l['K']:.0f}{l['typ'][0].upper()}",
                        "Exp": l["exp"][:10], "DTE": l["dte"], "Money": money,
                        "Entry": round(l["entry"], 2), "Now": round(l["cur"], 2),
                        "P&L %": round(pnl_pct), "P&L $": round(l["pnl"]), "Action": action,
                    })
                    if action != "hold & monitor":
                        _checklist.append(f"**{_tk} ${l['K']:.0f}{l['typ'][0].upper()}** ({l['side']}, {l['dte']}DTE): {action}")
                st.dataframe(pd.DataFrame(_rows), hide_index=True, use_container_width=True,
                             column_config={"P&L %": st.column_config.NumberColumn(format="%d%%")})

        # ── Morning checklist ──
        st.markdown("#### ✅ Tomorrow's open — action checklist")
        if _checklist:
            for c in _checklist:
                st.markdown(f"- {c}")
        else:
            st.markdown("- No urgent actions — positions are in good shape; monitor the key levels above.")
        if _net_ddelta > 0:
            st.markdown(f"- **Net long bias** (${_net_ddelta:,.0f}/+1%). If you want neutral into the open, "
                        "a small index/put hedge offsets a gap-down.")
        elif _net_ddelta < 0:
            st.markdown(f"- **Net short bias** (${_net_ddelta:,.0f}/+1%). A gap-up hurts — consider a call hedge "
                        "or trimming shorts.")
        if _net_theta < 0:
            st.markdown(f"- You're paying **${abs(_net_theta):,.0f}/day** in decay — long premium needs the move soon.")
        st.caption("Educational scenario analysis from your DB + Black-Scholes; not financial advice. "
                   "Real fills, IV shifts and gaps will differ.")

        try: _gp_conn.close()
        except Exception: pass
        st.stop()

    # ──────────────────────────────────────────────────────────────
    # BATCH HELPER: fetch current option mid-price from yfinance
    # ──────────────────────────────────────────────────────────────
    def _fetch_option_mid(ticker, expiry_str, strike, opt_type):
        """Return (mid_price, iv, spot) or (None, None, None) on failure.
        Finds nearest available expiry when exact date not listed by yfinance."""
        try:
            tk = yf.Ticker(ticker)
            hist = tk.history(period="2d")
            spot = float(hist["Close"].iloc[-1]) if len(hist) >= 1 else None
            try:
                exp_dt = datetime.strptime(expiry_str, "%Y-%m-%d").date()
            except Exception:
                return None, None, spot
            # Find nearest available expiry
            available = tk.options  # tuple of YYYY-MM-DD strings
            if not available:
                return None, None, spot
            exp_str = expiry_str  # default: try exact
            if expiry_str not in available:
                # pick closest available expiry by calendar distance
                avail_dts = [datetime.strptime(e, "%Y-%m-%d").date() for e in available]
                nearest = min(avail_dts, key=lambda d: abs((d - exp_dt).days))
                exp_str = nearest.strftime("%Y-%m-%d")
            chain = tk.option_chain(exp_str)
            df_c = chain.calls if opt_type.lower() == "call" else chain.puts
            row = df_c[abs(df_c["strike"] - strike) < 0.01]
            if row.empty:
                row = df_c.iloc[(df_c["strike"] - strike).abs().argsort()[:1]]
            if row.empty:
                return None, None, spot
            bid = float(row["bid"].iloc[0])
            ask = float(row["ask"].iloc[0])
            iv  = float(row["impliedVolatility"].iloc[0]) if "impliedVolatility" in row.columns else None
            mid = (bid + ask) / 2 if bid >= 0 and ask >= 0 else None
            return mid, iv, spot
        except Exception:
            return None, None, None

    def _ep_batch_table(trades_df):
        """Build batch exit analysis rows for a set of open trades."""
        rows = []
        today = datetime.now().date()
        for _, r in trades_df.iterrows():
            try:
                strike = float(r["strike"])
                entry  = float(r["entry_price"])
                raw_qty = int(r.get("quantity", 1) or 1)
                side   = "SELL" if raw_qty < 0 else "BUY"
                qty    = abs(raw_qty)
                opt    = str(r["option_type"]).lower()
                ticker = str(r["ticker"]).upper()
                try:
                    exp_dt = datetime.strptime(str(r["expiry"]), "%Y-%m-%d").date()
                    dte = (exp_dt - today).days
                except Exception:
                    dte = None
                mid, iv, spot = _fetch_option_mid(ticker, str(r["expiry"]), strike, opt)
                # P&L direction: for SELL, profit when option price goes DOWN
                if mid is not None:
                    if side == "BUY":
                        pnl = round((mid - entry) * qty * 100, 2)
                        pnl_pct = round((mid - entry) / entry * 100, 1) if entry > 0 else None
                    else:  # SELL — collected premium, profit = entry - mid
                        pnl = round((entry - mid) * qty * 100, 2)
                        pnl_pct = round((entry - mid) / entry * 100, 1) if entry > 0 else None
                else:
                    pnl = pnl_pct = None
                # signal
                if pnl_pct is None:
                    sig = "⚪ N/A"
                elif pnl_pct >= 50:
                    sig = "🟢 TAKE PROFIT"
                elif pnl_pct <= -30:
                    sig = "🔴 CUT LOSS"
                elif dte is not None and dte <= 5 and pnl_pct < 0:
                    sig = "🔴 NEAR EXPIRY"
                elif dte is not None and dte <= 5 and pnl_pct > 0:
                    sig = "🟡 CLOSE SOON"
                else:
                    sig = "⚪ HOLD"
                price_note = ""
                if mid is not None:
                    direction = "↑" if mid > entry else "↓"
                    price_note = f"${entry:.2f}→${mid:.2f} ({direction}{abs(mid-entry)/entry*100:.0f}% option Δ)"
                rows.append({
                    "Side": side,
                    "Ticker": ticker,
                    "Type": opt.upper(),
                    "Strike": f"${strike:.0f}",
                    "Expiry": str(r["expiry"]),
                    "DTE": dte,
                    "Qty": f"{side} ×{qty}",
                    "Premium paid→now": price_note if mid is not None else "N/A",
                    "P&L $": f"${pnl:+,.0f}" if pnl is not None else "N/A",
                    "P&L %": f"{pnl_pct:+.1f}%" if pnl_pct is not None else "N/A",
                    "IV": f"{iv*100:.0f}%" if iv else "N/A",
                    "Spot": f"${spot:.2f}" if spot else "N/A",
                    "Signal": sig,
                })
            except Exception:
                continue
        return pd.DataFrame(rows)

    def _add_total_row(df):
        """Append a TOTAL row summing P&L $ to a batch df."""
        if df.empty:
            return df
        pnl_vals = []
        for v in df["P&L $"]:
            try:
                pnl_vals.append(float(str(v).replace("$","").replace(",","").replace("+","")))
            except Exception:
                pass
        tot = sum(pnl_vals)
        tot_em = "🟢" if tot >= 0 else "🔴"
        tot_row = {c: "" for c in df.columns}
        tot_row["Side"] = f"{tot_em} TOTAL"
        tot_row["Ticker"] = f"{len(df)} leg(s)"
        tot_row["P&L $"] = f"${tot:+,.0f}"
        win_n = sum(1 for v in pnl_vals if v > 0)
        tot_row["Signal"] = f"{win_n}W/{len(pnl_vals)-win_n}L"
        return pd.concat([df, pd.DataFrame([tot_row])], ignore_index=True)

    if _ep_mode == "🌐 All Open Positions":
        st.markdown("#### 🌐 All Open Positions — Batch Analysis")
        if _ep_open_trades.empty:
            st.info("No open positions in portfolio.")
        else:
            with st.spinner(f"Analysing {len(_ep_open_trades)} positions..."):
                _ep_batch_df = _ep_batch_table(_ep_open_trades)
            if not _ep_batch_df.empty:
                # Summary metrics
                _bm1, _bm2, _bm3, _bm4 = st.columns(4)
                _tp_n = len(_ep_batch_df[_ep_batch_df["Signal"].str.contains("TAKE PROFIT")])
                _cl_n = len(_ep_batch_df[_ep_batch_df["Signal"].str.contains("CUT LOSS|NEAR EXPIRY")])
                _hold_n = len(_ep_batch_df[_ep_batch_df["Signal"] == "⚪ HOLD"])
                _ep_pnl_vals = [float(str(v).replace("$","").replace(",","").replace("+","")) for v in _ep_batch_df["P&L $"] if v != "N/A"]
                _ep_total_pnl = sum(_ep_pnl_vals)
                _bm1.metric("Total Positions", len(_ep_batch_df))
                _bm2.metric("🟢 Take Profit", _tp_n)
                _bm3.metric("🔴 Exit Alerts", _cl_n)
                _bm4.metric("Total P&L", f"${_ep_total_pnl:+,.0f}")
                with st.expander("ℹ️ How to read this table"):
                    st.markdown(
                        "- **Side**: BUY = you bought the option (paid premium), SELL = you sold it (collected premium)\n"
                        "- **Premium paid→now**: What you paid vs current market mid-price. "
                          "e.g. `$7.29→$1.66` means option premium fell from $7.29 to $1.66 — an 77% loss on the option *price* (not the stock)\n"
                        "- **P&L $**: Dollar profit/loss. For BUY: (now − entry) × qty × 100. For SELL: (entry − now) × qty × 100\n"
                        "- **P&L %**: % change in option premium. A 50% loss means the option halved in value\n"
                        "- **IV**: Implied Volatility — higher IV = more expensive options\n"
                        "- **Signal**: TAKE PROFIT (>50% gain), CUT LOSS (>30% loss), NEAR EXPIRY (<5 DTE), HOLD otherwise\n"
                        "- **TOTAL row**: Last row shows sum of all P&L and win/loss count"
                    )
                st.dataframe(_add_total_row(_ep_batch_df), hide_index=True, use_container_width=True)
                # Highlight alerts
                _alerts = _ep_batch_df[_ep_batch_df["Signal"].str.contains("TAKE PROFIT|CUT LOSS|NEAR EXPIRY|CLOSE SOON")]
                if not _alerts.empty:
                    st.warning("⚠️ **Action Required:**")
                    for _, _ar in _alerts.iterrows():
                        st.markdown(f"- **{_ar['Ticker']} {_ar['Type']} {_ar['Strike']}** exp {_ar['Expiry']} — {_ar['Signal']} | P&L: {_ar['P&L $']} ({_ar['P&L %']})")
        st.stop()

    elif _ep_mode == "🏢 All positions — by Ticker":
        st.markdown("#### 🏢 All Positions — by Ticker")
        if _ep_open_trades.empty:
            st.info("No open positions in portfolio.")
            st.stop()
        _ep_tickers = sorted(_ep_open_trades["ticker"].unique())
        _ep_sel_tk = st.selectbox("Select Ticker", _ep_tickers, key="ep_batch_ticker")
        _ep_tk_trades = _ep_open_trades[_ep_open_trades["ticker"] == _ep_sel_tk]

        with st.spinner(f"Loading {_ep_sel_tk} data..."):
            try:
                _btk_obj   = yf.Ticker(_ep_sel_tk)
                _btk_hist  = _btk_obj.history(period="60d")
                _btk_eod   = float(_btk_hist["Close"].iloc[-1]) if len(_btk_hist) >= 1 else 0
                _btk_prev  = float(_btk_hist["Close"].iloc[-2]) if len(_btk_hist) >= 2 else _btk_eod
                _btk_chg   = (_btk_eod - _btk_prev) / _btk_prev * 100 if _btk_prev else 0
            except Exception:
                _btk_eod, _btk_prev, _btk_chg = 0, 0, 0
            _btk_ah_d  = _get_ah_price(_ep_sel_tk)
            _btk_ah    = _btk_ah_d["spot_ah"] if _btk_ah_d["spot_ah"] > 0 else _btk_eod
            _btk_spot  = _btk_ah if st.session_state.get("use_ah") else _btk_eod
            _btk_is_ext = _btk_ah_d["is_extended"]
            _btk_ah_chg = _btk_ah_d["ah_chg_pct"]
            _btk_ah_lbl = _btk_ah_d["label"]

        # ── Header metrics ──
        bh1, bh2, bh3, bh4 = st.columns(4)
        if _btk_is_ext:
            bh1.metric(
                f"{_ep_sel_tk} EOD → {_btk_ah_lbl}",
                f"${_btk_ah:.2f}",
                f"{_btk_ah_chg:+.2f}% AH  (EOD ${_btk_eod:.2f}  {_btk_chg:+.1f}%)",
            )
        else:
            bh1.metric(f"{_ep_sel_tk} Spot", f"${_btk_eod:.2f}", f"{_btk_chg:+.2f}%")
        bh2.metric("Open Legs", len(_ep_tk_trades))

        # Short interest quick view
        try:
            _ep_si = _get_short_data_dash(_ep_sel_tk)
            _ep_spf = _ep_si.get("short_pct_float")
            _ep_sr  = _ep_si.get("short_ratio")
            _ep_sc  = _ep_si.get("squeeze_score", 0)
            bh3.metric("Short % Float",  f"{_ep_spf:.1f}%" if _ep_spf else "N/A")
            bh4.metric("Squeeze Score",  f"{_ep_sc}/10",
                       delta="HIGH SQUEEZE RISK" if _ep_sc >= 7 else ("MODERATE" if _ep_sc >= 4 else "LOW"))
        except Exception:
            pass

        # ── Per-leg table ──
        st.markdown(f"##### 📋 Legs")
        with st.spinner("Fetching live prices..."):
            _ep_tk_batch = _ep_batch_table(_ep_tk_trades)
        if not _ep_tk_batch.empty:
            # Total P&L
            try:
                _btk_total_pnl = sum(
                    float(r.replace("$","").replace(",","").replace("+",""))
                    for r in _ep_tk_batch["P&L $"] if r != "N/A"
                )
                bh3.metric("Group P&L", f"${_btk_total_pnl:+,.0f}",
                           delta_color="normal" if _btk_total_pnl >= 0 else "inverse")
            except Exception:
                pass
            with st.expander("ℹ️ How to read this table"):
                st.markdown(
                    "- **Side**: BUY = you paid the premium (long option), SELL = you collected premium (short option)\n"
                    "- **Premium paid→now**: Option price at entry vs current market mid. "
                      "e.g. `$7.29→$1.66 (↓77%)` means the option premium dropped 77% — this is the *option price*, not the stock price. "
                      "A BUY position loses money when option price falls; a SELL position profits when option price falls.\n"
                    "- **P&L $**: Total dollar P&L = (price change) × contracts × 100\n"
                    "- **P&L %**: % gain or loss on the option premium paid/received\n"
                    "- **IV**: Implied Volatility — market's expectation of future move\n"
                    "- **Signal**: TAKE PROFIT (>50% gain), CUT LOSS (>30% loss), NEAR EXPIRY (<5 DTE)\n"
                    "- **TOTAL row**: Last row shows sum of all P&L and win/loss count"
                )
            st.dataframe(_add_total_row(_ep_tk_batch), hide_index=True, use_container_width=True)

        # ── Payoff chart (combined all legs) ──
        st.markdown("---")
        st.markdown(f"##### 📈 Combined Payoff at Expiry")
        try:
            from scipy.stats import norm as _norm
            _r_rate = 0.05
            _spot_range = np.linspace(_btk_eod * 0.70, _btk_eod * 1.30, 200) if _btk_eod > 0 else np.array([])
            if len(_spot_range) > 0:
                _total_payoff = np.zeros(len(_spot_range))
                _total_entry_cost = 0.0
                _leg_lines = []
                for _, _leg in _ep_tk_trades.iterrows():
                    _lk = float(_leg["strike"])
                    _lep = float(_leg["entry_price"])
                    _lqty = int(_leg.get("quantity", 1) or 1)
                    _lot = str(_leg["option_type"]).lower()
                    _lpayoff = np.where(
                        _lot == "call",
                        np.maximum(_spot_range - _lk, 0),
                        np.maximum(_lk - _spot_range, 0)
                    ) * abs(_lqty) * 100
                    _lpayoff = _lpayoff if _lqty > 0 else -_lpayoff
                    _lentry_cost = _lep * abs(_lqty) * 100 * (1 if _lqty > 0 else -1)
                    _lpnl = _lpayoff - _lentry_cost
                    _total_payoff += _lpnl
                    _total_entry_cost += _lentry_cost
                    _side = "BUY" if _lqty > 0 else "SELL"
                    _leg_lines.append(f"{_side} {_lot.upper()} ${_lk:.0f}")

                _fig_pf = go.Figure()
                _fig_pf.add_trace(go.Scatter(
                    x=_spot_range, y=_total_payoff,
                    mode="lines", name="Combined P&L at expiry",
                    line=dict(color="#00d4aa", width=2.5),
                    fill="tozeroy",
                    fillcolor="rgba(0,212,170,0.15)",
                ))
                _fig_pf.add_hline(y=0, line_color="#888", line_width=1)
                if _btk_spot > 0:
                    _fig_pf.add_vline(x=_btk_spot, line_color="#e65100", line_dash="dash",
                                      annotation_text=f"Spot ${_btk_spot:.2f}", annotation_position="top right")
                # Breakevens
                _be_pts = []
                for _i in range(len(_total_payoff)-1):
                    if (_total_payoff[_i] <= 0 <= _total_payoff[_i+1]) or (_total_payoff[_i] >= 0 >= _total_payoff[_i+1]):
                        _be = _spot_range[_i] + (_spot_range[_i+1]-_spot_range[_i]) * (0 - _total_payoff[_i]) / (_total_payoff[_i+1] - _total_payoff[_i] + 1e-9)
                        _be_pts.append(_be)
                        _fig_pf.add_vline(x=_be, line_color="#ffd600", line_dash="dot",
                                          annotation_text=f"B/E ${_be:.1f}", annotation_position="bottom right")
                _fig_pf.update_layout(
                    template="plotly_white", height=360,
                    xaxis_title="Stock Price at Expiry",
                    yaxis_title="P&L ($)",
                    title=f"{_ep_sel_tk} — Combined payoff: {' | '.join(_leg_lines)}",
                    margin=dict(l=60, r=30, t=50, b=40),
                )
                st.plotly_chart(_fig_pf, use_container_width=True)
                if _be_pts:
                    _bec1, _bec2 = st.columns(2)
                    _bec1.metric("Breakeven(s)", " / ".join([f"${b:.2f}" for b in _be_pts[:3]]))
                    _max_gain = _total_payoff.max()
                    _max_loss = _total_payoff.min()
                    _bec2.metric("Max Profit / Max Loss",
                                 f"${_max_gain:,.0f} / ${_max_loss:,.0f}")
        except Exception as _pe:
            st.caption(f"Payoff chart unavailable: {_pe}")

        # ── OI Context ──
        st.markdown("---")
        st.markdown(f"##### 📊 OI Signal for {_ep_sel_tk}")
        try:
            _ep_oi_td = available_trade_dates()
            if _ep_oi_td:
                _ep_oi_df = load_oi_for_date(_ep_oi_td[0])
                _ep_oi_tk = _ep_oi_df[_ep_oi_df["ticker"] == _ep_sel_tk]
                if not _ep_oi_tk.empty:
                    _ep_cc  = pd.to_numeric(_ep_oi_tk["change_OI_Call"], errors="coerce").sum()
                    _ep_pc  = pd.to_numeric(_ep_oi_tk["change_OI_Put"],  errors="coerce").sum()
                    _ep_coi = pd.to_numeric(_ep_oi_tk["openInt_Call_now"], errors="coerce").sum()
                    _ep_poi = pd.to_numeric(_ep_oi_tk["openInt_Put_now"],  errors="coerce").sum()
                    _ep_pcr = _ep_poi / _ep_coi if _ep_coi > 0 else 0
                    _ep_sig, _ep_col = _oi_signal_light(_ep_cc, _ep_pc, _ep_pcr)
                    oc1, oc2, oc3, oc4 = st.columns(4)
                    oc1.metric("OI Signal", _ep_sig)
                    oc2.metric("PCR", f"{_ep_pcr:.2f}")
                    oc3.metric("Call ΔOI", f"{_ep_cc:+,.0f}")
                    oc4.metric("Put ΔOI", f"{_ep_pc:+,.0f}")
                    # Mini OI bar chart by strike (ATM ±5 strikes)
                    if _btk_spot > 0:
                        _oi_near = _ep_oi_tk[
                            (pd.to_numeric(_ep_oi_tk["strike"], errors="coerce") >= _btk_spot * 0.90) &
                            (pd.to_numeric(_ep_oi_tk["strike"], errors="coerce") <= _btk_spot * 1.10)
                        ].copy()
                        _oi_near["strike"] = pd.to_numeric(_oi_near["strike"], errors="coerce")
                        _oi_near = _oi_near.dropna(subset=["strike"]).sort_values("strike")
                        if not _oi_near.empty:
                            _fig_oi = go.Figure()
                            _fig_oi.add_trace(go.Bar(
                                x=_oi_near["strike"].astype(str),
                                y=pd.to_numeric(_oi_near["openInt_Call_now"], errors="coerce"),
                                name="Call OI", marker_color="#4fc3f7"))
                            _fig_oi.add_trace(go.Bar(
                                x=_oi_near["strike"].astype(str),
                                y=-pd.to_numeric(_oi_near["openInt_Put_now"], errors="coerce"),
                                name="Put OI", marker_color="#ef5350"))
                            _fig_oi.add_vline(x=str(round(_btk_spot)), line_dash="dash",
                                              line_color="#e65100", annotation_text="Spot")
                            # Mark position strikes
                            for _, _leg in _ep_tk_trades.iterrows():
                                _fig_oi.add_vline(x=str(int(_leg["strike"])), line_dash="dot",
                                                  line_color="#ffd600", annotation_text=f"${int(_leg['strike'])}")
                            _fig_oi.update_layout(
                                barmode="overlay", template="plotly_white", height=280,
                                title=f"{_ep_sel_tk} OI ±10% of spot (yellow=your strikes)",
                                margin=dict(l=50, r=20, t=40, b=30),
                            )
                            st.plotly_chart(_fig_oi, use_container_width=True)
        except Exception:
            pass

        # ── News ──
        st.markdown("---")
        st.markdown(f"##### 📰 Recent News — {_ep_sel_tk}")
        try:
            import feedparser
            _feed = feedparser.parse(
                f"https://feeds.finance.yahoo.com/rss/2.0/headline?s={_ep_sel_tk}&region=US&lang=en-US")
            _news_items = _feed.entries[:6] if _feed.entries else []
            if _news_items:
                for _ni in _news_items:
                    _title = _ni.get("title", "")
                    _link  = _ni.get("link", "#")
                    _date  = _ni.get("published", "")[:16]
                    _bull  = any(w in _title.lower() for w in ["rise","gain","bull","up","beat","rally","surge","record"])
                    _bear  = any(w in _title.lower() for w in ["fall","drop","loss","bear","down","miss","crash","cut"])
                    _sent  = "🟢" if _bull else ("🔴" if _bear else "⚪")
                    st.markdown(f"{_sent} [{_title}]({_link}) <small>{_date}</small>", unsafe_allow_html=True)
            else:
                st.caption("No recent news found.")
        except Exception:
            st.caption("News unavailable.")

        # ── Strategy Suggestions ──
        st.markdown("---")
        st.markdown(f"##### 💡 Suggestions for your {_ep_sel_tk} positions")
        try:
            _sugg = []
            today_dt = datetime.now().date()
            for _, _leg in _ep_tk_trades.iterrows():
                _lk   = float(_leg["strike"])
                _lep  = float(_leg["entry_price"])
                _lqty = int(_leg.get("quantity", 1) or 1)
                _lot  = str(_leg["option_type"]).lower()
                _side = "BUY" if _lqty > 0 else "SELL"
                try:
                    _lexp = datetime.strptime(str(_leg["expiry"]), "%Y-%m-%d").date()
                    _ldte = (_lexp - today_dt).days
                except Exception:
                    _ldte = 30
                # Match leg to batch row for P&L
                _matching = _ep_tk_batch[
                    (_ep_tk_batch["Strike"] == f"${_lk:.0f}") &
                    (_ep_tk_batch["Type"] == _lot.upper())
                ] if not _ep_tk_batch.empty else pd.DataFrame()
                _lpnl_pct_str = _matching["P&L %"].iloc[0] if not _matching.empty else "N/A"
                try:
                    _lpnl_pct = float(_lpnl_pct_str.replace("%","").replace("+",""))
                except Exception:
                    _lpnl_pct = None

                _sig_label = f"{_side} {_lot.upper()} ${_lk:.0f} DTE:{_ldte}"
                if _ldte <= 5 and _lpnl_pct is not None and _lpnl_pct > 0:
                    _sugg.append(f"🟡 **{_sig_label}** — DTE≤5 with profit: consider closing to lock gains before theta crush")
                elif _ldte <= 5 and _lpnl_pct is not None and _lpnl_pct < 0:
                    _sugg.append(f"🔴 **{_sig_label}** — DTE≤5 with loss: close now to limit further decay damage")
                elif _lpnl_pct is not None and _lpnl_pct >= 50:
                    _sugg.append(f"🟢 **{_sig_label}** — +{_lpnl_pct:.0f}% profit: consider closing half or move stop to breakeven")
                elif _lpnl_pct is not None and _lpnl_pct <= -40:
                    _sugg.append(f"🔴 **{_sig_label}** — {_lpnl_pct:.0f}% loss: evaluate roll or close to prevent further damage")
                elif _lot == "call" and _lqty > 0 and _btk_spot < _lk * 0.95:
                    _sugg.append(f"⚠️ **{_sig_label}** — call is deep OTM (stock ${_btk_spot:.2f} vs strike ${_lk:.0f}): consider rolling down or out")
                elif _lot == "put" and _lqty > 0 and _btk_spot > _lk * 1.05:
                    _sugg.append(f"⚠️ **{_sig_label}** — put is deep OTM (stock ${_btk_spot:.2f} vs strike ${_lk:.0f}): consider rolling up or out")
                else:
                    _sugg.append(f"⚪ **{_sig_label}** — no urgent action, monitor OI flow and news")

            # OI-based overlay suggestion
            if _ep_sig in ("BULLISH", "BULL+HEDGE"):
                _sugg.append(f"📊 **OI says {_ep_sig}** — OI flow supports upside. Long calls or call spreads favored.")
            elif _ep_sig in ("BEARISH", "HEDGE"):
                _sugg.append(f"📊 **OI says {_ep_sig}** — Put accumulation dominant. Consider hedging longs or taking bearish positions.")

            for _s in _sugg:
                st.markdown(f"- {_s}")
        except Exception as _se:
            st.caption(f"Suggestions unavailable: {_se}")

        st.stop()

    # ── Individual mode: existing load-from-portfolio ──
    if not _ep_open_trades.empty:
        st.markdown("#### 📂 Load from Open Positions")
        _ep_leg_labels = ["— Manual Entry —"]
        _ep_leg_map = {}
        for _, _ep_r in _ep_open_trades.iterrows():
            _side = "BUY" if int(_ep_r.get("quantity", 1)) > 0 else "SELL"
            _lbl = (f"{_ep_r['ticker']} {_side} {str(_ep_r['option_type']).upper()} "
                    f"${_ep_r['strike']} exp {_ep_r['expiry']} "
                    f"(entry ${_ep_r['entry_price']:.2f} ×{abs(int(_ep_r['quantity']))})")
            _ep_leg_labels.append(_lbl)
            _ep_leg_map[_lbl] = _ep_r

        _ep_selected_lbl = st.selectbox("Select position to analyse", _ep_leg_labels, key="ep_load_pos")

        if _ep_selected_lbl != "— Manual Entry —":
            _ep_sel = _ep_leg_map[_ep_selected_lbl]
            # Push values into session_state so widgets below pick them up
            st.session_state["ep_tk"]      = str(_ep_sel["ticker"]).upper()
            st.session_state["ep_type"]    = str(_ep_sel["option_type"]).lower()
            st.session_state["ep_strike"]  = float(_ep_sel["strike"])
            st.session_state["ep_entry"]   = float(_ep_sel["entry_price"])
            st.session_state["ep_qty"]     = abs(int(_ep_sel["quantity"]))
            try:
                _ep_exp_dt = datetime.strptime(str(_ep_sel["expiry"]), "%Y-%m-%d").date()
                st.session_state["ep_expiry"] = _ep_exp_dt
            except Exception:
                pass
            try:
                _ep_buy_dt = datetime.strptime(str(_ep_sel.get("entry_date", "")), "%Y-%m-%d").date()
                st.session_state["ep_buy_dt"] = _ep_buy_dt
            except Exception:
                pass
        st.markdown("---")

    # ── Position Inputs ──
    _ep_hdr, _ep_rst = st.columns([6, 1])
    _ep_hdr.markdown("<div>📋 Your Position</div>", unsafe_allow_html=True)
    if _ep_rst.button("🔄 Reset", key="ep_reset"):
        for _k in ["ep_tk", "ep_type", "ep_strike", "ep_entry", "ep_buy_dt", "ep_expiry",
                   "ep_qty", "_ep_last_tk", "ep_load_pos"]:
            st.session_state.pop(_k, None)
        st.rerun()
    ep_c1, ep_c2, ep_c3, ep_c4 = st.columns(4)
    ep_ticker = ep_c1.text_input("Ticker", value="GOOG", key="ep_tk")
    ep_type = ep_c2.selectbox("Option Type", ["put", "call"],
                               index=["put","call"].index(st.session_state.get("ep_type","put")) if st.session_state.get("ep_type") in ["put","call"] else 0,
                               key="ep_type")

    # Auto-set ATM strike when ticker changes — must happen BEFORE widget creation
    _ep_pre_tk = ep_ticker.upper().strip()
    if st.session_state.get("_ep_last_tk") != _ep_pre_tk:
        st.session_state["_ep_last_tk"] = _ep_pre_tk
        try:
            _pre_hist = _cached_history(_ep_pre_tk, period="2d")
            _pre_spot = float(_pre_hist["Close"].iloc[-1]) if len(_pre_hist) >= 1 else 0
            if _pre_spot > 0:
                st.session_state["ep_strike"] = float(round(_pre_spot / 5) * 5)
        except Exception:
            pass

    ep_strike = ep_c3.number_input("Strike Price", value=float(st.session_state.get("ep_strike", 180.0)), step=1.0, key="ep_strike")
    ep_entry = ep_c4.number_input("Entry Price ($)", value=float(st.session_state.get("ep_entry", 5.20)), step=0.10, key="ep_entry")

    ep_c5, ep_c6, ep_c7 = st.columns(3)
    ep_buy_date = ep_c5.date_input("Buy Date", value=st.session_state.get("ep_buy_dt", datetime.now().date() - timedelta(days=1)), key="ep_buy_dt")
    ep_expiry = ep_c6.date_input("Expiry", value=st.session_state.get("ep_expiry", datetime.now().date() + timedelta(days=30)), key="ep_expiry")
    ep_qty = ep_c7.number_input("Contracts", min_value=1, value=int(st.session_state.get("ep_qty", 1)), key="ep_qty")

    # ═══════════════════════════════════════════════════════════════
    # 1) FETCH CURRENT DATA: Spot, IV, Futures, News
    # ═══════════════════════════════════════════════════════════════
    st.markdown("---")
    with st.spinner("Fetching market intelligence..."):
        # ── Spot price (regular hours) ──
        _ep_spot = 0.0
        _ep_prev_close = 0.0
        _ep_hist_returns = np.array([])  # for MC calibration
        try:
            _ep_tk_obj = yf.Ticker(ep_ticker)
            _ep_hist = _ep_tk_obj.history(period="3mo")  # 60+ days for volatility
            if len(_ep_hist) >= 2:
                _ep_spot = float(_ep_hist["Close"].iloc[-1])
                _ep_prev_close = float(_ep_hist["Close"].iloc[-2])
                # Log returns for Monte Carlo calibration
                _ep_closes = _ep_hist["Close"].dropna().values
                if len(_ep_closes) > 5:
                    _ep_hist_returns = np.diff(np.log(_ep_closes))
        except Exception:
            pass

        # ── Get the MOST CURRENT price via intraday data ──
        _ep_realtime_price = 0.0
        _ep_realtime_src = ""
        _ep_data_stale = False
        try:
            _ep_intra = _ep_tk_obj.history(period="1d", interval="1m")
            if len(_ep_intra) > 0:
                _ep_rt_candidate = float(_ep_intra["Close"].iloc[-1])
                _last_ts = _ep_intra.index[-1]
                try:
                    _intra_date = _last_ts.tz_convert(None).date()
                except Exception:
                    _intra_date = _last_ts.date() if hasattr(_last_ts, 'date') else datetime.now().date()
                if _ep_rt_candidate > 0 and _intra_date >= datetime.now().date():
                    # Today's data — use it as the live price
                    _ep_realtime_price = _ep_rt_candidate
                    _ep_realtime_src = "Live (intraday)"
                else:
                    # Stale data from a previous day — flag it
                    _ep_data_stale = True
        except Exception:
            pass

        # Also check info dict for currentPrice
        _ep_info_price = 0.0
        try:
            _ep_info = _ep_tk_obj.info
            _ep_info_price = float(_ep_info.get("currentPrice", 0) or _ep_info.get("regularMarketPrice", 0) or 0)
        except Exception:
            _ep_info = {}

        # Pick the best spot price: prefer live intraday > info (if different) > daily close
        if _ep_realtime_price > 0:
            _ep_spot = _ep_realtime_price
            _ep_spot_src = _ep_realtime_src
        elif _ep_info_price > 0 and abs(_ep_info_price - _ep_spot) > 0.50:
            # info price is significantly different from daily close — might be more current
            _ep_spot = _ep_info_price
            _ep_spot_src = "Info (currentPrice)"
        else:
            _ep_spot_src = "Daily close"
            if _ep_data_stale:
                _ep_spot_src = "Daily close (delayed)"

        if _ep_spot <= 0:
            st.error(f"Could not fetch price for {ep_ticker}. Check ticker symbol.")
            st.stop()

        # ── After-hours / Pre-market detection ──
        # Only treat as AH if the AH price differs significantly from the best spot
        # AND data is not stale (otherwise AH price is also from the previous day)
        _ep_ah_price = None
        _ep_ah_chg_pct = None
        _ep_ah_source = ""
        if not _ep_data_stale:
            try:
                _ep_post_price = float(_ep_info.get("postMarketPrice", 0) or 0)
                _ep_pre_price = float(_ep_info.get("preMarketPrice", 0) or 0)
                # Pre-market takes priority (it's more recent than post-market)
                if _ep_pre_price > 0 and abs(_ep_pre_price - _ep_spot) > 0.50:
                    _ep_ah_price = _ep_pre_price
                    _ep_ah_chg_pct = (_ep_ah_price - _ep_spot) / _ep_spot * 100
                    _ep_ah_source = "Pre-Market"
                elif _ep_post_price > 0 and abs(_ep_post_price - _ep_spot) > 0.50:
                    _ep_ah_price = _ep_post_price
                    _ep_ah_chg_pct = (_ep_ah_price - _ep_spot) / _ep_spot * 100
                    _ep_ah_source = "After-Hours"
            except Exception:
                pass

            # Also check fast_info.last_price as a live source
            if _ep_ah_price is None:
                try:
                    _ep_fast_info = _ep_tk_obj.fast_info
                    _ep_last_price = float(getattr(_ep_fast_info, 'last_price', 0) or 0)
                    if _ep_last_price > 0 and abs(_ep_last_price - _ep_spot) > 0.50:
                        _ep_ah_price = _ep_last_price
                        _ep_ah_chg_pct = (_ep_ah_price - _ep_spot) / _ep_spot * 100
                        _ep_ah_source = "Last Tick"
                except Exception:
                    pass

        # Use AH price as live reference — always when toggle is on, else only if available
        _use_ah_now = st.session_state.get("use_ah", False)
        _ep_live_price = (_ep_ah_price if _ep_ah_price else _ep_spot) if (_use_ah_now or _ep_ah_price) else _ep_spot
        _ep_live_src = (_ep_ah_source if _ep_ah_price else _ep_spot_src) if (_use_ah_now or _ep_ah_price) else _ep_spot_src
        _ep_day_chg_pct = (_ep_spot - _ep_prev_close) / _ep_prev_close * 100 if _ep_prev_close > 0 else 0

        # ── Auto-fetch IV ──
        _ep_iv = 0.30
        _ep_iv_src = "Default (30%)"
        _ep_iv_raw = 0.0  # raw fetched value (before sanity check)
        _ep_iv_suspect = False
        try:
            _ep_chain = _ep_tk_obj.option_chain(ep_expiry.strftime("%Y-%m-%d"))
            _ep_oc = _ep_chain.puts if ep_type == "put" else _ep_chain.calls
            _ep_m = _ep_oc[_ep_oc["strike"] == float(ep_strike)]
            if not _ep_m.empty and "impliedVolatility" in _ep_m.columns:
                _fiv = float(_ep_m.iloc[0]["impliedVolatility"])
                if _fiv > 0.01:
                    _ep_iv_raw = _fiv
            elif not _ep_oc.empty and "impliedVolatility" in _ep_oc.columns:
                _ep_oc2 = _ep_oc.copy()
                _ep_oc2["_d"] = abs(_ep_oc2["strike"] - float(ep_strike))
                _nr = _ep_oc2.sort_values("_d").iloc[0]
                _fiv = float(_nr["impliedVolatility"])
                if _fiv > 0.01:
                    _ep_iv_raw = _fiv
        except Exception:
            pass

        # ── IV sanity check — reject garbage values ──
        # Equity option IV below 5% is almost certainly stale/broken data
        if _ep_iv_raw >= 0.05:
            _ep_iv = _ep_iv_raw
            _ep_iv_src = f"Live ({_ep_iv:.1%})"
        else:
            # IV is garbage or missing — build a smart estimate
            _ep_iv_suspect = True
            # Best proxy: VIX level (fetched later, but we can pre-fetch here)
            _ep_vix_proxy = 0.0
            try:
                _vix_tk = yf.Ticker("^VIX")
                _vix_h = _vix_tk.history(period="5d")
                if len(_vix_h) >= 1:
                    _ep_vix_proxy = float(_vix_h["Close"].iloc[-1]) / 100.0  # VIX 29.5 → 0.295
            except Exception:
                pass
            # Individual stock IV ≈ VIX × 1.2-1.5 (stocks are more volatile than index)
            _ep_vix_iv = _ep_vix_proxy * 1.3 if _ep_vix_proxy > 0.10 else 0
            # HV from historical returns
            _ep_hv_fallback = 0.0
            if len(_ep_hist_returns) >= 20:
                _ep_hv_fallback = float(np.std(_ep_hist_returns)) * np.sqrt(252)
            # Pick the best: VIX-derived > HV > default 30%
            if _ep_vix_iv > 0:
                _ep_iv = max(_ep_vix_iv, _ep_hv_fallback, 0.15)
                _ep_iv_src = f"VIX-derived ({_ep_iv:.1%})"
                _ep_iv_detail = f"VIX={_ep_vix_proxy*100:.0f}, live IV {_ep_iv_raw:.1%} unavailable"
            elif _ep_hv_fallback > 0.10:
                _ep_iv = max(_ep_hv_fallback, 0.15)
                _ep_iv_src = f"HV fallback ({_ep_iv:.1%})"
                _ep_iv_detail = f"live IV {_ep_iv_raw:.1%} unavailable"
            else:
                _ep_iv = 0.30
                _ep_iv_src = f"Default (30%)"
                _ep_iv_detail = f"live IV {_ep_iv_raw:.1%} unavailable"

        # ── Futures data (overnight sentiment) ──
        _futures_data = {}
        for _fn, _fs in [("S&P 500 Futures", "ES=F"), ("Nasdaq 100 Futures", "NQ=F"), ("VIX", "^VIX")]:
            try:
                _ft = yf.Ticker(_fs)
                _fh = _ft.history(period="5d")
                if len(_fh) >= 2:
                    _fc = float(_fh["Close"].iloc[-1])
                    _fp = float(_fh["Close"].iloc[-2])
                    _fpct = (_fc - _fp) / _fp * 100 if _fp > 0 else 0
                    _futures_data[_fn] = {"price": _fc, "pct": _fpct}
            except Exception:
                pass

        # ── News sentiment (RSS) ──
        _news_items = []
        _news_bull = 0
        _news_bear = 0
        try:
            import feedparser
            _nf = feedparser.parse(f"https://feeds.finance.yahoo.com/rss/2.0/headline?s={ep_ticker}&region=US&lang=en-US")
            for _ne in _nf.entries[:8]:
                _title = _ne.get("title", "")
                _link = _ne.get("link", "")
                _news_items.append({"title": _title, "link": _link})
                _tl = _title.lower()
                _neg_words = ["drop", "fall", "crash", "sell", "bear", "down", "loss", "cut", "slash",
                              "tariff", "warn", "fear", "decline", "recession", "weak", "miss", "layoff",
                              "plunge", "tumble", "sink", "dump", "concern", "risk", "threat", "crisis"]
                _pos_words = ["rally", "surge", "bull", "up", "gain", "beat", "strong", "rise", "high",
                              "buy", "upgrade", "record", "boost", "growth", "profit", "optimis"]
                if any(w in _tl for w in _neg_words):
                    _news_bear += 1
                elif any(w in _tl for w in _pos_words):
                    _news_bull += 1
        except Exception:
            pass

        _news_sentiment = "NEUTRAL"
        _news_iv_adj = 0
        if _news_bear > _news_bull + 1:
            _news_sentiment = "BEARISH"
            _news_iv_adj = 0.05  # IV expansion from fear
        elif _news_bull > _news_bear + 1:
            _news_sentiment = "BULLISH"
            _news_iv_adj = -0.02  # IV might contract on good news
        elif _news_bear > 0 or _news_bull > 0:
            _news_sentiment = "MIXED"
            _news_iv_adj = 0.02

        # ── Compute market regime from futures ──
        _es_pct = _futures_data.get("S&P 500 Futures", {}).get("pct", 0)
        _nq_pct = _futures_data.get("Nasdaq 100 Futures", {}).get("pct", 0)
        _vix_val = _futures_data.get("VIX", {}).get("price", 0)
        _vix_pct = _futures_data.get("VIX", {}).get("pct", 0)

        _mkt_direction = "FLAT"
        _predicted_gap_pct = (_es_pct + _nq_pct) / 2  # avg futures move
        if _predicted_gap_pct <= -1.0:
            _mkt_direction = "STRONG SELL-OFF"
        elif _predicted_gap_pct <= -0.3:
            _mkt_direction = "WEAK / DOWN"
        elif _predicted_gap_pct >= 1.0:
            _mkt_direction = "STRONG RALLY"
        elif _predicted_gap_pct >= 0.3:
            _mkt_direction = "POSITIVE / UP"

        # ── Monte Carlo Simulation Engine ──
        # Calibrate volatility from historical returns
        _mc_n_sims = 10000
        _mc_use_hist = len(_ep_hist_returns) >= 20

        if _mc_use_hist:
            _mc_hist_vol = float(np.std(_ep_hist_returns)) * np.sqrt(252)  # annualised
            _mc_hist_mean = float(np.mean(_ep_hist_returns)) * 252          # annualised drift
            _mc_hist_skew = float(pd.Series(_ep_hist_returns).skew())       # fat-tail awareness
        else:
            _mc_hist_vol = _ep_iv
            _mc_hist_mean = 0.0
            _mc_hist_skew = 0.0

        # Blend implied vol and realised vol (IV responds to expectations, HV to reality)
        # When VIX is elevated, the current vol regime is HIGHER than historical average
        _mc_vix_vol = _vix_val / 100.0 * 1.3 if _vix_val > 15 else 0  # VIX-implied stock vol
        if _mc_use_hist and _mc_vix_vol > 0:
            # Tri-blend: IV (market expectation) + HV (base) + VIX-derived (current regime)
            _mc_vol = 0.4 * _ep_iv + 0.3 * _mc_hist_vol + 0.3 * _mc_vix_vol
        elif _mc_use_hist:
            _mc_vol = 0.6 * _ep_iv + 0.4 * _mc_hist_vol
        else:
            _mc_vol = _ep_iv
        # If VIX is spiking, vol should reflect the fear regime
        if _vix_pct > 10 and _mc_vix_vol > _mc_vol:
            # VIX spike — current regime vol should dominate
            _mc_vol = max(_mc_vol, _mc_vix_vol * 0.85)
        # Floor: MC vol should never be below 15% for equities
        _mc_vol = max(_mc_vol, 0.15)

        # Compute signal-adjusted drift for overnight gap
        _ah_gap_pct_mc = (_ep_ah_chg_pct / 100.0) if _ep_ah_chg_pct else 0
        _futures_drift = _predicted_gap_pct / 100.0  # daily futures signal
        _news_drift = 0.0
        if _news_sentiment == "BEARISH":
            _news_drift = -0.003
        elif _news_sentiment == "BULLISH":
            _news_drift = 0.002
        elif _news_sentiment == "MIXED":
            _news_drift = -0.001

        # Total overnight drift = futures residual beyond AH move + news bias
        if _ep_ah_price:
            _mc_overnight_drift = max(_futures_drift - _ah_gap_pct_mc, _futures_drift * 0.3) + _news_drift
        else:
            _mc_overnight_drift = _futures_drift + _news_drift

        # GBM: S_tomorrow = S_live * exp((drift - 0.5*sigma^2)*dt + sigma*sqrt(dt)*Z)
        _mc_dt = 1.0 / 252.0  # one trading day
        _mc_vol_daily = _mc_vol * np.sqrt(_mc_dt)  # daily vol
        np.random.seed(42)
        _mc_Z = np.random.standard_normal(_mc_n_sims)

        # If we have enough historical data and skew is significant, use empirical distribution
        if _mc_use_hist and abs(_mc_hist_skew) > 0.5 and len(_ep_hist_returns) >= 30:
            # Resample from actual returns (preserves skew + kurtosis)
            _mc_empirical_returns = np.random.choice(_ep_hist_returns, size=_mc_n_sims, replace=True)
            # Shift by signal drift
            _mc_sim_returns = _mc_empirical_returns + _mc_overnight_drift
        else:
            # Standard GBM with signal-adjusted drift
            _mc_sim_returns = _mc_overnight_drift + (-0.5 * _mc_vol**2 * _mc_dt) + _mc_vol_daily * _mc_Z

        _mc_sim_prices = _ep_live_price * np.exp(_mc_sim_returns)

        # IV perturbation: simulate IV changes for option pricing
        # Key insight: option pricing IV should reflect TOMORROW's expected IV,
        # which in a VIX spike regime is much higher than historical
        _mc_iv_base = _ep_iv  # start from our best IV estimate
        # If VIX is elevated, tomorrow's option IV should be at least near VIX level
        if _vix_val > 20:
            _mc_iv_base = max(_mc_iv_base, _vix_val / 100.0 * 1.2)  # VIX 29.5 → ~35.4%
        _mc_iv_noise = np.random.normal(0, 0.03, _mc_n_sims)  # IV uncertainty
        _mc_iv_vix_adj = _news_iv_adj + (0.03 if abs(_predicted_gap_pct) > 1 else 0)
        if _vix_pct > 10:
            _mc_iv_vix_adj += 0.05 + (_vix_pct - 10) * 0.002  # scale with VIX spike size
        elif _vix_pct < -5:
            _mc_iv_vix_adj -= 0.03
        _mc_sim_ivs = np.clip(_mc_iv_base + _mc_iv_vix_adj + _mc_iv_noise, 0.05, 2.0)

        # Price option for every simulation path (vectorised Black-Scholes)
        _ep_dte_mc = max((datetime.combine(ep_expiry, datetime.min.time()) - datetime.now()).days, 1)
        _mc_T_tomorrow = max(_ep_dte_mc - 1, 1) / 365.0

        _mc_S = _mc_sim_prices
        _mc_K = float(ep_strike)
        _mc_r = 0.045
        _mc_sig = _mc_sim_ivs
        _mc_sqrt_T = np.sqrt(_mc_T_tomorrow)
        _mc_d1 = (np.log(_mc_S / _mc_K) + (_mc_r + 0.5 * _mc_sig**2) * _mc_T_tomorrow) / (_mc_sig * _mc_sqrt_T)
        _mc_d2 = _mc_d1 - _mc_sig * _mc_sqrt_T
        if ep_type == "put":
            _mc_option_vals = _mc_K * np.exp(-_mc_r * _mc_T_tomorrow) * norm.cdf(-_mc_d2) - _mc_S * norm.cdf(-_mc_d1)
        else:
            _mc_option_vals = _mc_S * norm.cdf(_mc_d1) - _mc_K * np.exp(-_mc_r * _mc_T_tomorrow) * norm.cdf(_mc_d2)
        _mc_option_vals = np.maximum(_mc_option_vals, 0.0)  # options can't be negative

        _mc_pnls = (_mc_option_vals - ep_entry) * ep_qty * 100

        # Key Monte Carlo statistics
        _mc_expected_val = float(np.mean(_mc_option_vals))
        _mc_median_val = float(np.median(_mc_option_vals))
        _mc_p10 = float(np.percentile(_mc_option_vals, 10))
        _mc_p25 = float(np.percentile(_mc_option_vals, 25))
        _mc_p75 = float(np.percentile(_mc_option_vals, 75))
        _mc_p90 = float(np.percentile(_mc_option_vals, 90))
        _mc_expected_pnl = float(np.mean(_mc_pnls))
        _mc_median_pnl = float(np.median(_mc_pnls))
        _mc_prob_profit = float(np.mean(_mc_pnls > 0)) * 100
        _mc_prob_loss = float(np.mean(_mc_pnls < 0)) * 100
        _mc_var_95 = float(np.percentile(_mc_pnls, 5))  # 95% VaR
        _mc_expected_stock = float(np.mean(_mc_sim_prices))
        _mc_median_stock = float(np.median(_mc_sim_prices))
        _mc_stock_p10 = float(np.percentile(_mc_sim_prices, 10))
        _mc_stock_p90 = float(np.percentile(_mc_sim_prices, 90))

    # ═══════════════════════════════════════════════════════════════
    # 2) DISPLAY MARKET INTELLIGENCE
    # ═══════════════════════════════════════════════════════════════
    st.markdown("<div>📡 Market Intelligence</div>", unsafe_allow_html=True)

    _mi_cols = st.columns(6)
    _mi_cols[0].metric(f"{ep_ticker} {'Live' if not _ep_data_stale else 'Close'}", f"${_ep_spot:.2f}", f"{_ep_day_chg_pct:+.2f}%")
    if _ep_ah_price:
        _ah_col_clr = "normal" if _ep_ah_chg_pct >= 0 else "inverse"
        _mi_cols[1].metric(f"🌙 {_ep_ah_source}", f"${_ep_ah_price:.2f}", f"{_ep_ah_chg_pct:+.2f}% vs close", delta_color=_ah_col_clr)
    else:
        _mi_cols[1].metric("🌙 After-Hours", "N/A", "Market open")
    _mi_cols[2].metric("ES Futures", f"{_es_pct:+.2f}%")
    _mi_cols[3].metric("NQ Futures", f"{_nq_pct:+.2f}%")
    _mi_cols[4].metric("VIX", f"{_vix_val:.1f}", f"{_vix_pct:+.1f}%", delta_color="inverse")
    _mi_cols[5].metric("News Tone", _news_sentiment)

    # Staleness warning if data is not from today
    if _ep_data_stale:
        st.warning(f"⚠️ **Price data may be delayed** — latest market data is from a previous trading day. "
                   f"Showing ${_ep_spot:.2f} ({_ep_spot_src}). "
                   f"Use the **Predicted Stock Price slider** below to enter the current live price for accurate calculations.")

    # Bad IV warning
    if _ep_iv_suspect:
        if _ep_data_stale:
            st.info(f"ℹ️ **IV from options chain unavailable** (market closed / stale data). "
                    f"Using **{_ep_iv_src}** — {_ep_iv_detail}. "
                    f"Adjust IV with the slider below if needed.")
        else:
            st.warning(f"⚠️ **IV data appears low** — yfinance returned {_ep_iv_raw:.1%}. "
                       f"Using **{_ep_iv_src}** instead — {_ep_iv_detail}. "
                       f"Adjust IV with the slider below if needed.")

    # Market direction card
    _dir_color = "#c62828" if "SELL" in _mkt_direction else "#e65100" if "WEAK" in _mkt_direction or "DOWN" in _mkt_direction else "#2e7d32" if "RALLY" in _mkt_direction or "POSITIVE" in _mkt_direction else "#546e7a"
    _live_badge = f"🌙 <b>Live Price: ${_ep_live_price:.2f}</b> ({_ep_live_src})" if _ep_ah_price else f"Close: ${_ep_spot:.2f}"
    st.markdown(f"""
    <div style='background:var(--panel-solid);color:var(--text);border-left:5px solid {_dir_color};
                padding:14px 20px;border-radius:10px;margin:10px 0;'>
        {_live_badge} &nbsp;|&nbsp;
        <b>Market: {_mkt_direction}</b> &nbsp;|&nbsp;
        Predicted Gap: <b>{_predicted_gap_pct:+.2f}%</b> &nbsp;|&nbsp;
        News: <b>{_news_bull} bullish / {_news_bear} bearish</b> &nbsp;|&nbsp;
        IV: <b>{_ep_iv_src}</b>
    </div>
    """, unsafe_allow_html=True)

    # ── Computation Parameters Banner ── (shows actual values used)
    _ep_dte_display = max((datetime.combine(ep_expiry, datetime.min.time()) - datetime.now()).days, 1)
    _otm_pct = abs(float(ep_strike) - _ep_live_price) / _ep_live_price * 100
    _otm_label = "ITM" if (ep_type == "put" and float(ep_strike) > _ep_live_price) or (ep_type == "call" and float(ep_strike) < _ep_live_price) else "ATM" if _otm_pct < 1 else f"OTM {_otm_pct:.0f}%"
    _params_border = "#c62828" if _otm_pct > 15 else "#0066cc"
    st.markdown(f"""
    <div>
        <b>📐 Computing:</b> &nbsp;
        <b>{ep_ticker}</b> {ep_type.upper()} &nbsp;|&nbsp;
        Strike: <b>${float(ep_strike):.0f}</b> ({_otm_label}) &nbsp;|&nbsp;
        Stock: <b>${_ep_live_price:.2f}</b> &nbsp;|&nbsp;
        DTE: <b>{_ep_dte_display}</b> &nbsp;|&nbsp;
        IV: <b>{_ep_iv:.1%}</b> &nbsp;|&nbsp;
        Entry: <b>${ep_entry:.2f}</b>
    </div>
    """, unsafe_allow_html=True)

    # ── OTM Warning ──
    if _otm_pct > 15:
        st.warning(f"⚠️ **Strike ${float(ep_strike):.0f} is {_otm_pct:.0f}% away from current price ${_ep_live_price:.2f}** — "
                   f"this {ep_type} is deep OTM and likely has near-zero value. "
                   f"Did you mean to set strike near **${round(_ep_live_price / 5) * 5:.0f}**? "
                   f"Change the Strike Price above and press Enter.")

    # ═══════════════════════════════════════════════════════════════
    # 2b) MONTE CARLO PREDICTION — 10,000 Simulations
    # ═══════════════════════════════════════════════════════════════
    st.markdown("<div>🎲 Monte Carlo Prediction — 10,000 Simulations</div>", unsafe_allow_html=True)

    _mc_vol_src = f"Blended ({_mc_vol:.1%} = 60% IV + 40% HV)" if _mc_use_hist else f"IV-only ({_mc_vol:.1%})"
    _mc_method = "Empirical resampling (preserves skew/kurtosis)" if (_mc_use_hist and abs(_mc_hist_skew) > 0.5 and len(_ep_hist_returns) >= 30) else "Geometric Brownian Motion"
    _mc_exp_color = "#2e7d32" if _mc_expected_pnl >= 0 else "#c62828"

    # Main MC prediction card
    st.markdown(f"""
    <div style='background:var(--panel-solid);color:var(--text);border-left:6px solid {_mc_exp_color};
                padding:20px 24px;border-radius:12px;margin:12px 0;box-shadow:0 2px 10px rgba(0,0,0,0.1);'>
        <h3>🎲 Monte Carlo Expected Value ({_mc_n_sims:,} sims · {_mc_method})</h3>
        <div>
            <div>
                <div>Expected Stock</div>
                <div>${_mc_expected_stock:.2f}
                    (${_mc_stock_p10:.2f} – ${_mc_stock_p90:.2f})</div>
            </div>
            <div>
                <div>Expected Option Value</div>
                <div>${_mc_expected_val:.2f}
                    (${_mc_p10:.2f} – ${_mc_p90:.2f})</div>
            </div>
            <div>
                <div>Expected P&L</div>
                <div>${_mc_expected_pnl:+,.0f}</div>
            </div>
            <div>
                <div>P(Profit)</div>
                <div>{_mc_prob_profit:.1f}%</div>
            </div>
            <div>
                <div>95% VaR</div>
                <div>${_mc_var_95:+,.0f}</div>
            </div>
            <div>
                <div>Vol Source</div>
                <div>{_mc_vol_src}</div>
            </div>
        </div>
    </div>
    """, unsafe_allow_html=True)

    # Distribution metrics row
    _mc_m1, _mc_m2, _mc_m3, _mc_m4, _mc_m5, _mc_m6 = st.columns(6)
    _mc_m1.metric("Median Value", f"${_mc_median_val:.2f}")
    _mc_m2.metric("10th Pctl (Bear)", f"${_mc_p10:.2f}")
    _mc_m3.metric("25th Pctl", f"${_mc_p25:.2f}")
    _mc_m4.metric("75th Pctl", f"${_mc_p75:.2f}")
    _mc_m5.metric("90th Pctl (Bull)", f"${_mc_p90:.2f}")
    _mc_m6.metric("Hist. Vol" if _mc_use_hist else "IV Used", f"{_mc_hist_vol:.1%}" if _mc_use_hist else f"{_mc_vol:.1%}")

    # Distribution chart — option value histogram + stock price histogram side by side
    _mc_fig_col1, _mc_fig_col2 = st.columns(2)

    with _mc_fig_col1:
        _fig_mc_opt = go.Figure()
        _fig_mc_opt.add_trace(go.Histogram(
            x=_mc_option_vals, nbinsx=80,
            marker_color="rgba(0,102,204,0.5)", marker_line_color="#0066cc", marker_line_width=0.5,
            name="Option Value Distribution",
        ))
        _fig_mc_opt.add_vline(x=_mc_expected_val, line_dash="solid", line_color="#0066cc", line_width=2,
                              annotation_text=f"Expected ${_mc_expected_val:.2f}")
        _fig_mc_opt.add_vline(x=ep_entry, line_dash="dash", line_color="#c62828", line_width=2,
                              annotation_text=f"Entry ${ep_entry:.2f}")
        _fig_mc_opt.add_vline(x=_mc_p10, line_dash="dot", line_color="#e65100", line_width=1,
                              annotation_text=f"P10 ${_mc_p10:.2f}")
        _fig_mc_opt.add_vline(x=_mc_p90, line_dash="dot", line_color="#2e7d32", line_width=1,
                              annotation_text=f"P90 ${_mc_p90:.2f}")
        _fig_mc_opt.update_layout(
            template="plotly_white", height=340,
            title=f"Option Value Distribution ({_mc_n_sims:,} paths)",
            xaxis_title="Option Value ($)", yaxis_title="Frequency",
            margin=dict(t=50, b=30), showlegend=False,
        )
        st.plotly_chart(_fig_mc_opt)

    with _mc_fig_col2:
        _fig_mc_stk = go.Figure()
        _fig_mc_stk.add_trace(go.Histogram(
            x=_mc_sim_prices, nbinsx=80,
            marker_color="rgba(156,39,176,0.4)", marker_line_color="#7b1fa2", marker_line_width=0.5,
            name="Stock Price Distribution",
        ))
        _fig_mc_stk.add_vline(x=_ep_live_price, line_dash="solid", line_color="#ff9100", line_width=2,
                              annotation_text=f"Live ${_ep_live_price:.2f}")
        _fig_mc_stk.add_vline(x=ep_strike, line_dash="dash", line_color="#c62828", line_width=2,
                              annotation_text=f"Strike ${ep_strike:.0f}")
        _fig_mc_stk.add_vline(x=_mc_expected_stock, line_dash="solid", line_color="#0066cc", line_width=2,
                              annotation_text=f"Expected ${_mc_expected_stock:.2f}")
        _fig_mc_stk.update_layout(
            template="plotly_white", height=340,
            title=f"{ep_ticker} Price Distribution (tomorrow)",
            xaxis_title=f"{ep_ticker} Price ($)", yaxis_title="Frequency",
            margin=dict(t=50, b=30), showlegend=False,
        )
        st.plotly_chart(_fig_mc_stk)

    # Probability table
    _mc_prob_data = []
    for _pct_threshold in [10, 20, 30, 50, -10, -20, -30, -50]:
        _thresh_val = ep_entry * (1 + _pct_threshold / 100)
        if _pct_threshold > 0:
            _prob = float(np.mean(_mc_option_vals >= _thresh_val)) * 100
            _mc_prob_data.append({"Outcome": f"≥ +{_pct_threshold}% gain", "Option ≥": f"${_thresh_val:.2f}",
                                  "Probability": f"{_prob:.1f}%", "_sort": -_pct_threshold})
        else:
            _prob = float(np.mean(_mc_option_vals <= _thresh_val)) * 100
            _mc_prob_data.append({"Outcome": f"≤ {_pct_threshold}% loss", "Option ≤": f"${_thresh_val:.2f}",
                                  "Probability": f"{_prob:.1f}%", "_sort": -_pct_threshold})
    _mc_prob_df = pd.DataFrame(_mc_prob_data).sort_values("_sort").drop(columns=["_sort"])
    with st.expander("📊 Probability Table — Detailed Outcome Odds", expanded=False):
        st.dataframe(_mc_prob_df, hide_index=True)
        if _mc_use_hist:
            st.caption(f"Calibrated on {len(_ep_hist_returns)} daily returns · Hist. skew: {_mc_hist_skew:.2f} · "
                       f"Blended vol: {_mc_vol:.1%} (IV {_ep_iv:.1%} + HV {_mc_hist_vol:.1%})")
        else:
            st.caption(f"Using implied volatility only ({_ep_iv:.1%}) — not enough historical data for calibration.")

    # ═══════════════════════════════════════════════════════════════
    # 3) SCENARIO ANALYSIS — Predict next-day option value
    # ═══════════════════════════════════════════════════════════════
    st.markdown("<div>🔮 Next-Day Scenario Analysis</div>", unsafe_allow_html=True)

    _ep_dte = max((datetime.combine(ep_expiry, datetime.min.time()) - datetime.now()).days, 1)
    _ep_T = _ep_dte / 365.0
    _ep_T_tomorrow = max(_ep_dte - 1, 1) / 365.0  # one day less

    # Current theoretical value — use live price for most accurate calc
    _ep_now = bs_greeks(_ep_live_price, ep_strike, _ep_T, 0.045, _ep_iv, ep_type)
    _ep_current_value = _ep_now["price"]

    # Define scenarios — base predicted move off live price + futures
    _scenarios = []

    # Scenario inputs: (name, stock_move_pct, iv_adjustment, probability_weight)
    # Bear scenarios (good for puts)
    _scenarios.append(("🔴 Crash (-3%)", -3.0, 0.15, 0.05))
    _scenarios.append(("🔴 Sharp Drop (-2%)", -2.0, 0.10, 0.10))
    _scenarios.append(("🟠 Moderate Drop (-1%)", -1.0, 0.05, 0.15))
    _scenarios.append(("🟡 Slight Down (-0.5%)", -0.5, 0.02, 0.15))
    # Flat
    _scenarios.append(("⚪ Flat (0%)", 0.0, 0.0, 0.15))
    # Bull scenarios (bad for puts)
    _scenarios.append(("🟢 Slight Up (+0.5%)", 0.5, -0.02, 0.15))
    _scenarios.append(("🟢 Moderate Rally (+1%)", 1.0, -0.03, 0.15))
    _scenarios.append(("🟢 Sharp Rally (+2%)", 2.0, -0.05, 0.08))
    _scenarios.append(("🟢 Moon (+3%)", 3.0, -0.07, 0.02))

    # Also add a "Predicted" scenario based on futures + news
    _pred_stock_move = _predicted_gap_pct
    _pred_iv_adj = _news_iv_adj + (0.03 if abs(_predicted_gap_pct) > 1 else 0)
    if _vix_pct > 10:
        _pred_iv_adj += 0.05  # VIX spiking = IV expansion
    elif _vix_pct < -5:
        _pred_iv_adj -= 0.03  # VIX dropping = IV contraction

    # Auto-computed prediction from market signals
    _ah_gap_pct = _ep_ah_chg_pct if _ep_ah_chg_pct else 0
    _auto_pred_stock_adj = _predicted_gap_pct
    if _ep_ah_price:
        _auto_pred_stock_adj = max(_predicted_gap_pct - _ah_gap_pct, _predicted_gap_pct * 0.3)
    _auto_pred_stock = _ep_live_price * (1 + _auto_pred_stock_adj / 100)
    _auto_pred_iv = max(_ep_iv + _pred_iv_adj, 0.05)

    # ── Override Prediction with Sliders ──
    st.markdown("<div>🎚️ Adjust Prediction (AI defaults — drag to override)</div>", unsafe_allow_html=True)
    _sl_c1, _sl_c2 = st.columns(2)
    _ep_pred_px_lo = round(_ep_live_price * 0.90, 2)
    _ep_pred_px_hi = round(_ep_live_price * 1.10, 2)
    _pred_stock = _sl_c1.slider(
        f"📈 Predicted {ep_ticker} Price Tomorrow ($)",
        _ep_pred_px_lo, _ep_pred_px_hi, float(round(_auto_pred_stock, 2)),
        step=0.50, key="ep_pred_stock_sl")
    _pred_iv_pct = _sl_c2.slider(
        "📊 Predicted IV Tomorrow",
        5, 150, int(round(_auto_pred_iv * 100)),
        step=1, key="ep_pred_iv_sl", format="%d%%")
    _pred_iv = _pred_iv_pct / 100.0
    _sl_info = ""
    if abs(_pred_stock - _auto_pred_stock) > 0.01 or abs(_pred_iv - _auto_pred_iv) > 0.005:
        _sl_info = "⚡ Using your override"
    else:
        _sl_info = "🤖 Using AI prediction"
    st.caption(f"{_sl_info}  ·  AI predicted: **${_auto_pred_stock:.2f}** stock, **{_auto_pred_iv:.1%}** IV")

    _scenario_rows = []
    for _sname, _smove, _siv_adj, _sprob in _scenarios:
        _s_stock = _ep_live_price * (1 + _smove / 100)
        _s_iv = max(_ep_iv + _siv_adj, 0.05)
        _s_greeks = bs_greeks(_s_stock, ep_strike, _ep_T_tomorrow, 0.045, _s_iv, ep_type)
        _s_val = _s_greeks["price"]
        _s_pnl = (_s_val - ep_entry) * ep_qty * 100
        _s_pnl_pct = (_s_val - ep_entry) / ep_entry * 100 if ep_entry > 0 else 0
        _scenario_rows.append({
            "Scenario": _sname,
            f"{ep_ticker} Price": f"${_s_stock:.2f}",
            "IV": f"{_s_iv:.1%}",
            "Option Value": f"${_s_val:.2f}",
            "P&L / Contract": f"${(_s_val - ep_entry) * 100:+,.0f}",
            "Total P&L": f"${_s_pnl:+,.0f}",
            "P&L %": f"{_s_pnl_pct:+.1f}%",
            "_val": _s_val,
            "_pnl": _s_pnl,
            "_stock": _s_stock,
        })

    # Predicted scenario — uses slider values (which default to AI prediction)
    _pred_g = bs_greeks(_pred_stock, ep_strike, _ep_T_tomorrow, 0.045, _pred_iv, ep_type)
    _pred_option_val = _pred_g["price"]
    _pred_pnl = (_pred_option_val - ep_entry) * ep_qty * 100
    _pred_pnl_pct = (_pred_option_val - ep_entry) / ep_entry * 100 if ep_entry > 0 else 0

    _pred_icon = "🔴" if _pred_stock < _ep_live_price else "🟢" if _pred_stock > _ep_live_price else "⚪"

    # Show predicted value prominently
    _pred_color = "#2e7d32" if _pred_pnl >= 0 else "#c62828"
    _ah_note = f" Based on {_ep_ah_source} ${_ep_ah_price:.2f} + futures residual" if _ep_ah_price else ""
    st.markdown(
        f"<div>"
        f"<h3>{_pred_icon} Signal-Based Prediction (slider-adjustable){_ah_note}</h3>"
        f"<div>"
        f"<div><div>Predicted {ep_ticker} Open</div><div>${_pred_stock:.2f} ({((_pred_stock / _ep_spot) - 1) * 100:+.2f}% vs close)</div></div>"
        f"<div><div>Predicted Option Value</div><div>${_pred_option_val:.2f}</div></div>"
        f"<div><div>Entry Price</div><div>${ep_entry:.2f}</div></div>"
        f"<div><div>P&L ({ep_qty} contract{'s' if ep_qty > 1 else ''})</div><div>${_pred_pnl:+,.0f} ({_pred_pnl_pct:+.1f}%)</div></div>"
        f"<div><div>Adjusted IV</div><div>{_pred_iv:.1%}</div></div>"
        f"</div>"
        f"</div>",
        unsafe_allow_html=True
    )

    # ── Scenario table ──
    _sdf = pd.DataFrame(_scenario_rows)
    _display_cols = ["Scenario", f"{ep_ticker} Price", "IV", "Option Value", "P&L / Contract", "Total P&L", "P&L %"]
    st.dataframe(_sdf[_display_cols], hide_index=True)

    # ── Scenario P&L Chart ──
    _fig_sc = go.Figure()
    _s_vals = [r["_val"] for r in _scenario_rows]
    _s_pnls = [r["_pnl"] for r in _scenario_rows]
    _s_stocks = [r["_stock"] for r in _scenario_rows]
    _s_names = [r["Scenario"] for r in _scenario_rows]
    _s_colors = ["#c62828" if p < 0 else "#2e7d32" for p in _s_pnls]

    _fig_sc.add_trace(go.Bar(
        x=_s_names, y=_s_pnls, marker_color=_s_colors,
        text=[f"${p:+,.0f}" for p in _s_pnls], textposition="outside",
        hovertemplate="%{x}<br>P&L: $%{y:+,.0f}<extra></extra>",
    ))
    # Add MC expected line and slider-predicted line
    _fig_sc.add_hline(y=_pred_pnl, line_dash="dot", line_color="#1565c0", line_width=2,
                      annotation_text=f"Slider: ${_pred_pnl:+,.0f}", annotation_position="top right")
    _fig_sc.add_hline(y=_mc_expected_pnl, line_dash="dash", line_color="#2e7d32", line_width=2,
                      annotation_text=f"MC Expected: ${_mc_expected_pnl:+,.0f}", annotation_position="bottom right")
    _fig_sc.update_layout(
        template="plotly_white", height=380,
        title="P&L by Scenario (next day)",
        yaxis_title="P&L ($)", xaxis_title="",
        margin=dict(t=50, b=30),
    )
    st.plotly_chart(_fig_sc)

    # ═══════════════════════════════════════════════════════════════
    # 4) RECOMMENDED SELL ORDERS
    # ═══════════════════════════════════════════════════════════════
    st.markdown("<div>💰 Recommended Sell Orders</div>", unsafe_allow_html=True)

    # Calculate key price levels
    _ep_theta_decay = abs(_ep_now.get("theta", 0))
    _ep_intrinsic = max(0, ep_strike - _ep_live_price) if ep_type == "put" else max(0, _ep_live_price - ep_strike)
    _ep_time_value = max(_ep_current_value - _ep_intrinsic, 0)
    _ep_breakeven = ep_entry  # need to get back entry price per share

    # Profit targets
    _target_conservative = ep_entry * 1.15  # 15% gain
    _target_moderate = ep_entry * 1.30  # 30% gain
    _target_aggressive = ep_entry * 1.50  # 50% gain

    # Stop loss levels
    _stop_tight = ep_entry * 0.80  # 20% loss
    _stop_moderate = ep_entry * 0.65  # 35% loss
    _stop_wide = ep_entry * 0.50  # 50% loss

    # Smart recommendation based on MC probability + signal prediction
    _rec_action = ""
    _rec_price = 0
    _rec_reason = ""

    # Use MC expected P&L as the primary signal, slider P&L as secondary
    _best_pnl_pct = (_mc_expected_val - ep_entry) / ep_entry * 100 if ep_entry > 0 else 0

    if _mc_expected_pnl > 0 and _mc_prob_profit > 55:
        # MC says profitable with high confidence
        if _best_pnl_pct > 30 or _mc_prob_profit > 70:
            _rec_action = "🟢 SET LIMIT SELL (Take Profit)"
            _rec_price = _target_moderate
            _rec_reason = (f"MC simulation: {_mc_prob_profit:.0f}% chance of profit. "
                           f"Expected value ${_mc_expected_val:.2f} vs entry ${ep_entry:.2f}. "
                           f"Set limit sell at ${_target_moderate:.2f} (30% gain) with "
                           f"stop-loss at ${_stop_tight:.2f}.")
        elif _best_pnl_pct > 10:
            _rec_action = "🟢 SET LIMIT SELL (Lock Gains)"
            _rec_price = _target_conservative
            _rec_reason = (f"MC simulation: {_mc_prob_profit:.0f}% chance of profit. "
                           f"Expected value ${_mc_expected_val:.2f}. "
                           f"Set limit at ${_target_conservative:.2f} (15% gain). "
                           f"Trail stop at ${ep_entry:.2f} (breakeven) if it moves further.")
        else:
            _rec_action = "🟡 HOLD / SET BREAKEVEN STOP"
            _rec_price = ep_entry * 1.05
            _rec_reason = (f"MC simulation: {_mc_prob_profit:.0f}% chance of profit but small edge. "
                           f"Expected value ${_mc_expected_val:.2f}. Hold with stop at ${_stop_tight:.2f}. "
                           f"VaR (95%): ${_mc_var_95:+,.0f} worst case.")
    else:
        # MC says losing or coin flip
        if _mc_prob_loss > 70 or _best_pnl_pct < -30:
            _rec_action = "🔴 CUT LOSS — SET MARKET SELL AT OPEN"
            _rec_price = _mc_expected_val
            _rec_reason = (f"MC simulation: {_mc_prob_loss:.0f}% chance of loss. "
                           f"Expected value ${_mc_expected_val:.2f} (P&L {_best_pnl_pct:+.0f}%). "
                           f"95% VaR: ${_mc_var_95:+,.0f}. "
                           f"Sell at market open to limit damage. Theta: -${_ep_theta_decay * 100:.2f}/day.")
        elif _mc_prob_loss > 55 or _best_pnl_pct < -15:
            _rec_action = "🟠 SET STOP-LOSS ORDER"
            _rec_price = _stop_tight
            _rec_reason = (f"MC simulation: {_mc_prob_loss:.0f}% chance of loss. "
                           f"Expected value ${_mc_expected_val:.2f}. "
                           f"Set stop at ${_stop_tight:.2f} (20% loss cap). "
                           f"Consider rolling to later expiry if conviction holds.")
        else:
            _rec_action = "🟡 HOLD WITH TIGHT STOP"
            _rec_price = _stop_tight
            _rec_reason = (f"MC simulation: roughly even odds ({_mc_prob_profit:.0f}% profit / {_mc_prob_loss:.0f}% loss). "
                           f"Expected value ${_mc_expected_val:.2f}. Set stop at ${_stop_tight:.2f} "
                           f"and give it room. Time value: ${_ep_time_value:.2f} remaining.")

    # Check VIX-specific alert
    _vix_warning = ""
    if _vix_val > 25:
        _vix_warning = "⚠️ VIX elevated (>25) — IV crush risk after resolution. Consider closing sooner."
    elif _vix_val < 15 and ep_type == "put":
        _vix_warning = "⚠️ VIX low (<15) — puts are cheap but unlikely to pay off without a catalyst."

    _rec_border = "#2e7d32" if "🟢" in _rec_action else "#c62828" if "🔴" in _rec_action else "#e65100"
    st.markdown(f"""
    <div style='background:var(--panel-solid);color:var(--text);border-left:6px solid {_rec_border};
                padding:22px 26px;border-radius:12px;margin:12px 0;box-shadow:0 3px 12px rgba(0,0,0,0.12);'>
        <h3>{_rec_action}</h3>
        <p>{_rec_reason}</p>
        <div>
            <div>
                <b>Suggested Limit</b>
${_rec_price:.2f}
            </div>
            <div>
                <b>Current Theo</b>
${_ep_current_value:.2f}
            </div>
            <div>
                <b>DTE</b>
{_ep_dte}
            </div>
            <div>
                <b>Theta/Day</b>
-${_ep_theta_decay * 100:.2f}
            </div>
            <div>
                <b>MC Expected</b>
${_mc_expected_val:.2f}
            </div>
            <div>
                <b>P(Profit)</b>
                    = 50 else "#c62828"}};'>{_mc_prob_profit:.0f}%
            </div>
        </div>
        {'<p>' + _vix_warning + '</p>' if _vix_warning else ''}
    </div>
    """, unsafe_allow_html=True)

    # ═══════════════════════════════════════════════════════════════
    # 5) ORDER STRATEGY TABLE
    # ═══════════════════════════════════════════════════════════════
    st.markdown("<div>📋 Order Strategy Levels</div>", unsafe_allow_html=True)
    _orders = [
        {"Order Type": "🟢 Take Profit (Aggressive)", "Limit Price": f"${_target_aggressive:.2f}",
         "P&L If Hit": f"${(_target_aggressive - ep_entry) * ep_qty * 100:+,.0f}", "Note": "50% gain target — needs strong move"},
        {"Order Type": "🟢 Take Profit (Moderate)", "Limit Price": f"${_target_moderate:.2f}",
         "P&L If Hit": f"${(_target_moderate - ep_entry) * ep_qty * 100:+,.0f}", "Note": "30% gain target — realistic if thesis plays out"},
        {"Order Type": "🟢 Take Profit (Conservative)", "Limit Price": f"${_target_conservative:.2f}",
         "P&L If Hit": f"${(_target_conservative - ep_entry) * ep_qty * 100:+,.0f}", "Note": "15% gain — quick scalp"},
        {"Order Type": "⚪ Breakeven", "Limit Price": f"${ep_entry:.2f}",
         "P&L If Hit": "$0", "Note": "Exit at cost if thesis fails"},
        {"Order Type": "🟠 Stop Loss (Tight)", "Limit Price": f"${_stop_tight:.2f}",
         "P&L If Hit": f"${(_stop_tight - ep_entry) * ep_qty * 100:+,.0f}", "Note": "20% loss cap — disciplined exit"},
        {"Order Type": "🔴 Stop Loss (Moderate)", "Limit Price": f"${_stop_moderate:.2f}",
         "P&L If Hit": f"${(_stop_moderate - ep_entry) * ep_qty * 100:+,.0f}", "Note": "35% loss cap — wider room"},
        {"Order Type": "🔴 Stop Loss (Wide)", "Limit Price": f"${_stop_wide:.2f}",
         "P&L If Hit": f"${(_stop_wide - ep_entry) * ep_qty * 100:+,.0f}", "Note": "50% loss cap — last resort"},
    ]
    st.dataframe(pd.DataFrame(_orders), hide_index=True)

    # ═══════════════════════════════════════════════════════════════
    # 6) GREEKS & RISK METRICS
    # ═══════════════════════════════════════════════════════════════
    st.markdown("<div>📊 Position Greeks & Risk</div>", unsafe_allow_html=True)
    _gr1, _gr2, _gr3, _gr4, _gr5, _gr6 = st.columns(6)
    _gr1.metric("Delta", f"{_ep_now['delta']:.4f}")
    _gr2.metric("Gamma", f"{_ep_now['gamma']:.5f}")
    _gr3.metric("Theta", f"-${abs(_ep_now['theta']) * 100:.2f}/day")
    _gr4.metric("Vega", f"${_ep_now['vega'] * 100:.2f}")
    _gr5.metric("Intrinsic", f"${_ep_intrinsic:.2f}")
    _gr6.metric("Time Value", f"${_ep_time_value:.2f}")

    # Risk decomposition
    _carry_cost_1d = abs(_ep_now["theta"]) * 100 * ep_qty  # theta burn per day
    _carry_cost_5d = _carry_cost_1d * 5
    _decay_warn = "<br>Less than 7 DTE — theta decay is exponential. Exit or roll ASAP." if _ep_dte < 7 else ""
    st.markdown(
        f"<div><b>⏰ Time Decay Warning:</b> You lose ~<b>${_carry_cost_1d:.2f}/day</b> holding this position "
        f"(${_carry_cost_5d:.2f} over 5 days). With {_ep_dte} DTE, theta accelerates — each day costs more."
        f"{_decay_warn}</div>",
        unsafe_allow_html=True)

    # ═══════════════════════════════════════════════════════════════
    # 7) IV SENSITIVITY (what if IV changes?)
    # ═══════════════════════════════════════════════════════════════
    st.markdown("<div>📈 IV Sensitivity — What If Volatility Changes?</div>", unsafe_allow_html=True)
    _iv_shifts = [-0.15, -0.10, -0.05, 0, 0.05, 0.10, 0.15, 0.20]
    _iv_rows = []
    for _ivs in _iv_shifts:
        _new_iv = max(_ep_iv + _ivs, 0.05)
        _iv_g = bs_greeks(_ep_spot, ep_strike, _ep_T_tomorrow, 0.045, _new_iv, ep_type)
        _iv_val = _iv_g["price"]
        _iv_pnl = (_iv_val - ep_entry) * ep_qty * 100
        _iv_rows.append({
            "IV Shift": f"{_ivs:+.0%}",
            "New IV": f"{_new_iv:.1%}",
            "Option Value": f"${_iv_val:.2f}",
            "P&L": f"${_iv_pnl:+,.0f}",
            "Change": f"${_iv_val - _ep_current_value:+.2f}",
        })
    st.dataframe(pd.DataFrame(_iv_rows), hide_index=True)

    # ═══════════════════════════════════════════════════════════════
    # 8) ALTERNATIVE STRATEGIES (hedging / rolling)
    # ═══════════════════════════════════════════════════════════════
    st.markdown("<div>🛡️ Alternative Strategies</div>", unsafe_allow_html=True)

    _strats = []
    if _pred_pnl < 0:
        _strats.append({
            "Strategy": "🔄 Roll to Later Expiry",
            "Action": f"Sell current {ep_type} → Buy same strike with more DTE",
            "When": "You still believe the thesis but need more time",
            "Benefit": "Resets theta clock, costs the debit to roll",
        })
        _strats.append({
            "Strategy": "📊 Spread Conversion",
            "Action": f"Sell a lower strike {ep_type} against your position" if ep_type == "put" else
                      f"Sell a higher strike {ep_type} against your position",
            "When": "Reduce cost basis and cap further loss",
            "Benefit": f"Turns naked {ep_type} into a vertical spread — defined risk",
        })
    if _pred_pnl >= 0:
        _strats.append({
            "Strategy": "🎯 Trail Stop",
            "Action": f"Set trailing stop 15-20% below current value",
            "When": "Position is profitable and you want to ride momentum",
            "Benefit": f"Locks in gains while allowing upside. Stop at ~${_ep_current_value * 0.82:.2f}",
        })
        _strats.append({
            "Strategy": "💰 Sell Half, Hold Half",
            "Action": f"Sell {max(ep_qty // 2, 1)} contracts now, hold rest with stop",
            "When": "You want some guaranteed profit plus upside",
            "Benefit": "Secures realized gains, free-rolls remaining position",
        })
    _strats.append({
        "Strategy": "⚡ Hedge with Opposite Side",
        "Action": f"Buy a {'call' if ep_type == 'put' else 'put'} to create a straddle/strangle",
        "When": "Expecting big move but unsure of direction",
        "Benefit": "Profits from volatility regardless of direction",
    })
    st.dataframe(pd.DataFrame(_strats), hide_index=True)

    # ═══════════════════════════════════════════════════════════════
    # 9) NEWS FEED
    # ═══════════════════════════════════════════════════════════════
    if _news_items:
        st.markdown(f"<div>📰 Latest {ep_ticker} News</div>", unsafe_allow_html=True)
        for _ni in _news_items:
            _title = _ni["title"]
            _link = _ni.get("link", "")
            if _link:
                st.markdown(f"• [{_title}]({_link})")
            else:
                st.markdown(f"• {_title}")

    # ═══════════════════════════════════════════════════════════════
    # 10) INTERACTIVE SLIDER — Fine-tune your own prediction
    # ═══════════════════════════════════════════════════════════════
    st.markdown("---")
    st.markdown("<div>🎚️ Custom Scenario — Drag to Explore</div>", unsafe_allow_html=True)

    _ep_px_low = round(_ep_live_price * 0.90, 2)
    _ep_px_high = round(_ep_live_price * 1.10, 2)
    _ep_sl_c1, _ep_sl_c2, _ep_sl_c3 = st.columns(3)
    _ep_custom_px = _ep_sl_c1.slider("📈 Stock Price ($)", _ep_px_low, _ep_px_high, float(round(_pred_stock, 2)),
                                      step=0.50, key="ep_cust_px")
    _ep_custom_iv_pct = _ep_sl_c2.slider("📊 IV Override", 5, 150, int(round(_ep_iv * 100)),
                                          step=1, key="ep_cust_iv", format="%d%%")
    _ep_custom_iv = _ep_custom_iv_pct / 100.0
    _ep_custom_days = _ep_sl_c3.slider("📅 Days Forward", 0, _ep_dte, 1, key="ep_cust_days")

    _ep_cust_T = max(_ep_dte - _ep_custom_days, 1) / 365.0
    _ep_cust_g = bs_greeks(_ep_custom_px, ep_strike, _ep_cust_T, 0.045, _ep_custom_iv, ep_type)
    _ep_cust_val = _ep_cust_g["price"]
    _ep_cust_pnl = (_ep_cust_val - ep_entry) * ep_qty * 100
    _ep_cust_pnl_pct = (_ep_cust_val - ep_entry) / ep_entry * 100 if ep_entry > 0 else 0
    _ep_cust_color = "#2e7d32" if _ep_cust_pnl >= 0 else "#c62828"

    _ep_intrinsic_val = max(0, ep_strike - _ep_custom_px) if ep_type == 'put' else max(0, _ep_custom_px - ep_strike)
    _ep_cust_dte_rem = max(_ep_dte - _ep_custom_days, 1)
    st.markdown(
        f"<div><div>"
        f"<div><b>Option Value:</b> ${_ep_cust_val:.2f}</div>"
        f"<div><b>P&L:</b> ${_ep_cust_pnl:+,.0f} ({_ep_cust_pnl_pct:+.1f}%)</div>"
        f"<div><b>Delta:</b> {_ep_cust_g['delta']:.4f}</div>"
        f"<div><b>DTE:</b> {_ep_cust_dte_rem}</div>"
        f"<div><b>Intrinsic:</b> ${_ep_intrinsic_val:.2f}</div>"
        f"</div></div>",
        unsafe_allow_html=True)

    # P&L chart across stock prices at custom day
    _ep_chart_prices = np.linspace(_ep_px_low, _ep_px_high, 80)
    _ep_chart_pnls = []
    for _cp in _ep_chart_prices:
        _cg = bs_greeks(_cp, ep_strike, _ep_cust_T, 0.045, _ep_custom_iv, ep_type)
        _ep_chart_pnls.append((_cg["price"] - ep_entry) * ep_qty * 100)

    _fig_ep = go.Figure()
    _fig_ep.add_trace(go.Scatter(
        x=_ep_chart_prices, y=_ep_chart_pnls, mode="lines",
        line=dict(color="#0066cc", width=2.5), name="P&L",
        fill="tozeroy", fillcolor="rgba(0,102,204,0.08)",
    ))
    _fig_ep.add_hline(y=0, line_dash="dash", line_color="#546e7a", line_width=1)
    _fig_ep.add_vline(x=_ep_spot, line_dash="dot", line_color="#ff9100",
                      annotation_text=f"Close ${_ep_spot:.2f}")
    if _ep_ah_price:
        _fig_ep.add_vline(x=_ep_ah_price, line_dash="dash", line_color="#7b1fa2",
                          annotation_text=f"{_ep_ah_source} ${_ep_ah_price:.2f}")
    _fig_ep.add_vline(x=_ep_custom_px, line_dash="dash", line_color="#9c27b0",
                      annotation_text=f"Slider ${_ep_custom_px:.2f}")
    # Mark entry breakeven
    _fig_ep.add_vline(x=ep_strike, line_dash="dot", line_color="#c62828",
                      annotation_text=f"Strike ${ep_strike:.0f}")
    _fig_ep.add_trace(go.Scatter(
        x=[_ep_custom_px], y=[_ep_cust_pnl], mode="markers",
        marker=dict(color=_ep_cust_color, size=14, symbol="diamond"),
        name="Selected",
    ))
    _fig_ep.update_layout(
        template="plotly_white", height=380,
        title=f"P&L Curve — {ep_ticker} {ep_type.upper()} ${ep_strike:.0f} (Day +{_ep_custom_days})",
        xaxis_title=f"{ep_ticker} Stock Price", yaxis_title="P&L ($)",
        margin=dict(t=50, b=30),
    )
    st.plotly_chart(_fig_ep)



# ===================================================================
# ──  PAGE 9: OI COMPARISON CHARTS
# ===================================================================
if page == "🔬 OI Comparison Charts":
    _hdr1, _hdr2 = st.columns([4, 1])
    with _hdr1:
        st.markdown("## 🔬 OI Comparison Charts")
    with _hdr2:
        if st.button("🔄 Refresh", key="refresh_oi_charts"):
            st.rerun()
        with st.popover("ℹ️"):
            st.markdown(_PAGE_HELP.get(page, ""))
    st.markdown("*NYSE-Telegram style OI vs Price vs Volume — full expiry chain with backtesting*")

    dates = available_trade_dates()
    if not dates or len(dates) < 2:
        st.warning("Need at least 2 trade dates in DB for comparison.")
        st.stop()

    # ── Selectors row ──
    col_d1, col_d2, col_tk = st.columns([1, 1, 1])
    td_now = col_d1.selectbox("📅 Current Date", dates, index=0)
    prev_options = [d for d in dates if d != td_now]
    td_prev = col_d2.selectbox("📅 Compare To (prev)", prev_options, index=0 if prev_options else None)

    day_df = load_oi_for_date(td_now)
    tickers_avail = sorted(day_df["ticker"].unique()) if not day_df.empty else []
    default_tk = tickers_avail.index("SPY") if "SPY" in tickers_avail else 0
    sel_ticker = col_tk.selectbox("🎯 Ticker", tickers_avail, index=default_tk if tickers_avail else None)

    if not sel_ticker:
        st.stop()

    # ── Get spot price (DB first, then live Yahoo) ──
    stock_data = load_stock_daily(sel_ticker)
    spot = None
    if not stock_data.empty:
        row_now = stock_data[stock_data["trade_date"] == td_now]
        if not row_now.empty:
            spot = float(row_now["close"].iloc[0])
    if spot is None:
        try:
            t = yf.Ticker(sel_ticker)
            spot = float(t.history(period="1d")["Close"].iloc[-1])
        except Exception:
            spot = 0

    # ── Next-day stock move (for backtesting) ──
    close_today, close_next, next_day_pct = get_next_day_stock_move(sel_ticker, td_now)

    # ── Load comparison data ──
    comp_df = load_oi_for_two_dates(sel_ticker, td_now, td_prev)
    if comp_df is None or comp_df.empty:
        st.warning(f"No OI data for {sel_ticker} on {td_now}.")
        st.stop()

    # ── Classify & sort expiries ──
    all_expiries_raw = comp_df["expiry_date"].dropna().unique().tolist()
    ref_dt = pd.to_datetime(td_now, format="%m-%d-%Y")
    future_exp, past_exp = [], []
    for e in all_expiries_raw:
        try:
            edt = pd.to_datetime(e, format="%m-%d-%Y")
            if edt >= ref_dt:
                future_exp.append((e, edt))
            else:
                past_exp.append((e, edt))
        except Exception:
            pass
    future_exp.sort(key=lambda x: x[1])
    past_exp.sort(key=lambda x: x[1], reverse=True)

    future_labels = [f"🟢 {e[0]}" for e in future_exp]
    past_labels = [f"🔴 {e[0]} (expired)" for e in past_exp]
    all_labels = future_labels + past_labels
    all_expiry_map = {f"🟢 {e[0]}": e[0] for e in future_exp}
    all_expiry_map.update({f"🔴 {e[0]} (expired)": e[0] for e in past_exp})

    # Default: first 4 future expiries
    default_sel = future_labels[:4] if future_labels else all_labels[:3]
    sel_expiry_labels = st.multiselect(
        f"Expiries ({len(future_exp)} future, {len(past_exp)} past/expired)",
        all_labels, default=default_sel,
    )
    sel_expiries = [all_expiry_map[l] for l in sel_expiry_labels if l in all_expiry_map]
    if not sel_expiries:
        st.info("Select at least one expiry.")
        st.stop()

    # ── Header metrics ──
    mc1, mc2, mc3, mc4, mc5 = st.columns(5)
    mc1.metric("Ticker", sel_ticker)
    mc2.metric("Spot", f"${spot:.2f}" if spot else "N/A")
    mc3.metric("Date", td_now)
    mc4.metric("vs", td_prev)
    if next_day_pct is not None:
        mc5.metric("Next-Day Move", f"{next_day_pct:+.2f}%",
                   delta=f"${close_next:.2f}" if close_next else "", delta_color="normal")
    else:
        mc5.metric("Next-Day Move", "N/A (latest)", delta="Live data")

    # ═══════════════════════════════════════════════════════════════
    # ──  MARKET REGIME DETECTION (6th factor)
    # ═══════════════════════════════════════════════════════════════
    _mkt_regime_sig = 0
    _mkt_regime_txt = ""
    _spy_pct = None
    try:
        _spy_close_t, _spy_close_n, _spy_pct_val = get_next_day_stock_move("SPY", td_now)
        if _spy_pct_val is None:
            _spy_sd = load_stock_daily("SPY")
            if not _spy_sd.empty:
                _spy_sd["_dt"] = pd.to_datetime(_spy_sd["trade_date"], format="%m-%d-%Y", errors="coerce")
                _spy_sd = _spy_sd.sort_values("_dt").reset_index(drop=True)
                _ref_dt = pd.to_datetime(td_now, format="%m-%d-%Y")
                _ref_row = _spy_sd[_spy_sd["_dt"] == _ref_dt]
                if not _ref_row.empty:
                    _idx = _ref_row.index[0]
                    if _idx > 0:
                        _prev_close = float(_spy_sd.iloc[_idx - 1]["close"])
                        _cur_close = float(_ref_row["close"].iloc[0])
                        _spy_pct_val = (_cur_close - _prev_close) / _prev_close * 100 if _prev_close else 0
        if _spy_pct_val is None:
            try:
                _spy_hist = _cached_history("SPY", period="5d")
                if len(_spy_hist) >= 2:
                    _spy_pct_val = (_spy_hist["Close"].iloc[-1] / _spy_hist["Close"].iloc[-2] - 1) * 100
            except Exception:
                pass
        if _spy_pct_val is not None:
            _spy_pct = _spy_pct_val
            if _spy_pct_val <= -1.5:
                _mkt_regime_sig = -2
                _mkt_regime_txt = f"🔴 Market SELL-OFF (SPY {_spy_pct_val:+.2f}%) — Strong bearish headwind"
            elif _spy_pct_val <= -0.5:
                _mkt_regime_sig = -1
                _mkt_regime_txt = f"🟠 Market WEAK (SPY {_spy_pct_val:+.2f}%) — Bearish headwind"
            elif _spy_pct_val >= 1.5:
                _mkt_regime_sig = 2
                _mkt_regime_txt = f"🟢 Market RALLY (SPY {_spy_pct_val:+.2f}%) — Strong bullish tailwind"
            elif _spy_pct_val >= 0.5:
                _mkt_regime_sig = 1
                _mkt_regime_txt = f"🟢 Market UP (SPY {_spy_pct_val:+.2f}%) — Bullish tailwind"
            else:
                _mkt_regime_sig = 0
                _mkt_regime_txt = f"⚪ Market FLAT (SPY {_spy_pct_val:+.2f}%) — No directional bias"
    except Exception:
        pass

    # ═══════════════════════════════════════════════════════════════
    # ──  SIGNAL SCANNER — ALL TICKERS (quick 6-factor scan)
    # ═══════════════════════════════════════════════════════════════
    st.markdown("---")
    st.markdown("<div>📡 Signal Scanner — All Tickers</div>", unsafe_allow_html=True)
    if _mkt_regime_txt:
        st.markdown(f"**Market Regime:** {_mkt_regime_txt}")

    _scan_data = load_oi_for_date(td_now)
    _scan_rows = []
    for _stk in sorted(_scan_data["ticker"].unique()):
        _stk_df = _scan_data[_scan_data["ticker"] == _stk]
        _s_cc = pd.to_numeric(_stk_df["change_OI_Call"], errors="coerce").sum()
        _s_pc = pd.to_numeric(_stk_df["change_OI_Put"], errors="coerce").sum()
        _s_nb = _s_cc - _s_pc
        _s_os = 1 if _s_nb > 0 else -1
        _s_coi = pd.to_numeric(_stk_df["openInt_Call_now"], errors="coerce").sum()
        _s_poi = pd.to_numeric(_stk_df["openInt_Put_now"], errors="coerce").sum()
        _s_pcr = _s_poi / _s_coi if _s_coi > 0 else 0
        _s_ps = 1 if _s_pcr > 1.3 else (-1 if _s_pcr < 0.7 else 0)
        _s_cv = pd.to_numeric(_stk_df.get("vol_Call_now", pd.Series([0])), errors="coerce").sum()
        _s_pv = pd.to_numeric(_stk_df.get("vol_Put_now", pd.Series([0])), errors="coerce").sum()
        _s_vp = _s_pv / _s_cv if _s_cv > 0 else 0
        _s_vs = 1 if _s_vp > 1.3 else (-1 if _s_vp < 0.7 else 0)
        _s_fs = 1 if _s_cc > 0 and _s_pc < 0 else (-1 if _s_cc < 0 and _s_pc > 0 else 0)
        _s_sk = pd.to_numeric(_stk_df.get("strike", pd.Series([0])), errors="coerce")
        _s_gn = (_s_coi * _s_sk).sum() - (_s_poi * _s_sk).sum()
        _s_gs = 1 if _s_gn > 0 and _s_os > 0 else (-1 if _s_gn < 0 and _s_os < 0 else 0)
        _s_oi_only = _s_os + _s_ps + _s_vs + _s_fs + _s_gs
        _s_comp = _s_oi_only + _mkt_regime_sig
        if _s_comp >= 2:
            _s_sig = "🟢 BULLISH"
        elif _s_comp <= -2:
            _s_sig = "🔴 BEARISH"
        else:
            _s_sig = "⚪ NEUTRAL"
        _s_str = abs(_s_comp)
        if _s_str >= 4:
            _s_conf = "HIGH"
        elif _s_str >= 2:
            _s_conf = "MODERATE"
        else:
            _s_conf = "LOW"
        _scan_rows.append({
            "Ticker": _stk, "Signal": _s_sig, "Score": f"{_s_comp:+d}/7",
            "OI Score": f"{_s_oi_only:+d}/5", "Mkt Adj": f"{_mkt_regime_sig:+d}",
            "Confidence": _s_conf, "PCR": round(_s_pcr, 2),
            "Net OI Bias": f"{_s_nb:+,.0f}",
            "Call Δ": f"{_s_cc:+,.0f}", "Put Δ": f"{_s_pc:+,.0f}",
        })
    _scan_df = pd.DataFrame(_scan_rows)
    if not _scan_df.empty:
        _fc1, _fc2 = st.columns(2)
        with _fc1:
            _dir_filter = st.selectbox("📊 Signal Direction", ["All", "🟢 BULLISH", "🔴 BEARISH", "⚪ NEUTRAL"], key="scan_dir_f")
        with _fc2:
            _conf_filter = st.selectbox("🎯 Confidence Level", ["All", "HIGH", "MODERATE", "LOW"], key="scan_conf_f")
        _filt = _scan_df.copy()
        if _dir_filter != "All":
            _filt = _filt[_filt["Signal"] == _dir_filter]
        if _conf_filter != "All":
            _filt = _filt[_filt["Confidence"] == _conf_filter]
        st.caption(f"Showing {len(_filt)} of {len(_scan_df)} tickers")
        st.dataframe(_filt, hide_index=True)

        _bull_n = len(_scan_df[_scan_df["Signal"] == "🟢 BULLISH"])
        _bear_n = len(_scan_df[_scan_df["Signal"] == "🔴 BEARISH"])
        _neut_n = len(_scan_df[_scan_df["Signal"] == "⚪ NEUTRAL"])
        _hi_n = len(_scan_df[_scan_df["Confidence"] == "HIGH"])
        _sm1, _sm2, _sm3, _sm4 = st.columns(4)
        _sm1.metric("🟢 Bullish", _bull_n)
        _sm2.metric("🔴 Bearish", _bear_n)
        _sm3.metric("⚪ Neutral", _neut_n)
        _sm4.metric("🎯 High Conf", _hi_n)

    # ═══════════════════════════════════════════════════════════════
    # ──  LIVE OI SIGNAL RECOMMENDATION (uses 6-factor + backtest)
    # ═══════════════════════════════════════════════════════════════
    st.markdown("---")
    st.markdown("<div>🧠 OI Signal Recommendation for " + sel_ticker + "</div>", unsafe_allow_html=True)
    try:
        # Run 6-factor signal on current date
        _sig_data = load_oi_for_date(td_now)
        _sig_tk = _sig_data[_sig_data["ticker"] == sel_ticker]
        if not _sig_tk.empty:
            _c_chg = pd.to_numeric(_sig_tk["change_OI_Call"], errors="coerce").sum()
            _p_chg = pd.to_numeric(_sig_tk["change_OI_Put"], errors="coerce").sum()
            _net_bias = _c_chg - _p_chg
            _oi_sig = 1 if _net_bias > 0 else -1

            _c_oi = pd.to_numeric(_sig_tk["openInt_Call_now"], errors="coerce").sum()
            _p_oi = pd.to_numeric(_sig_tk["openInt_Put_now"], errors="coerce").sum()
            _pcr = _p_oi / _c_oi if _c_oi > 0 else 0
            _pcr_sig = 1 if _pcr > 1.3 else (-1 if _pcr < 0.7 else 0)

            _c_vol = pd.to_numeric(_sig_tk.get("vol_Call_now", pd.Series([0])), errors="coerce").sum()
            _p_vol = pd.to_numeric(_sig_tk.get("vol_Put_now", pd.Series([0])), errors="coerce").sum()
            _vol_pcr = _p_vol / _c_vol if _c_vol > 0 else 0
            _vol_sig = 1 if _vol_pcr > 1.3 else (-1 if _vol_pcr < 0.7 else 0)

            if _c_chg > 0 and _p_chg < 0:
                _flow_sig = 1
            elif _c_chg < 0 and _p_chg > 0:
                _flow_sig = -1
            else:
                _flow_sig = 0

            _strikes_d = pd.to_numeric(_sig_tk.get("strike", pd.Series([0])), errors="coerce")
            _gex_net = (_c_oi * _strikes_d).sum() - (_p_oi * _strikes_d).sum()
            _gex_sig = 1 if _gex_net > 0 and _oi_sig > 0 else (-1 if _gex_net < 0 and _oi_sig < 0 else 0)

            _oi_only_composite = _oi_sig + _pcr_sig + _vol_sig + _flow_sig + _gex_sig
            _composite = _oi_only_composite + _mkt_regime_sig
            if _composite >= 2:
                _signal = "BULLISH"
            elif _composite <= -2:
                _signal = "BEARISH"
            else:
                _signal = "NEUTRAL"

            # Run mini-backtest for accuracy (last 20 days)
            _bt_dates = available_trade_dates()
            _bt_wins, _bt_total, _bt_bull, _bt_bear = 0, 0, 0, 0
            for _bd in _bt_dates[:20]:
                _bd_data = load_oi_for_date(_bd)
                _bd_tk = _bd_data[_bd_data["ticker"] == sel_ticker]
                if _bd_tk.empty:
                    continue
                _b_cc = pd.to_numeric(_bd_tk["change_OI_Call"], errors="coerce").sum()
                _b_pc = pd.to_numeric(_bd_tk["change_OI_Put"], errors="coerce").sum()
                _b_nb = _b_cc - _b_pc
                _b_os = 1 if _b_nb > 0 else -1
                _b_coi = pd.to_numeric(_bd_tk["openInt_Call_now"], errors="coerce").sum()
                _b_poi = pd.to_numeric(_bd_tk["openInt_Put_now"], errors="coerce").sum()
                _b_pcr = _b_poi / _b_coi if _b_coi > 0 else 0
                _b_ps = 1 if _b_pcr > 1.3 else (-1 if _b_pcr < 0.7 else 0)
                _b_cv = pd.to_numeric(_bd_tk.get("vol_Call_now", pd.Series([0])), errors="coerce").sum()
                _b_pv = pd.to_numeric(_bd_tk.get("vol_Put_now", pd.Series([0])), errors="coerce").sum()
                _b_vp = _b_pv / _b_cv if _b_cv > 0 else 0
                _b_vs = 1 if _b_vp > 1.3 else (-1 if _b_vp < 0.7 else 0)
                _b_fs = 1 if _b_cc > 0 and _b_pc < 0 else (-1 if _b_cc < 0 and _b_pc > 0 else 0)
                _b_sk = pd.to_numeric(_bd_tk.get("strike", pd.Series([0])), errors="coerce")
                _b_gn = (_b_coi * _b_sk).sum() - (_b_poi * _b_sk).sum()
                _b_gs = 1 if _b_gn > 0 and _b_os > 0 else (-1 if _b_gn < 0 and _b_os < 0 else 0)
                # Include market regime in backtest too
                _b_spy_t, _b_spy_n, _b_spy_pct = get_next_day_stock_move("SPY", _bd)
                _b_mkt = 0
                if _b_spy_pct is not None:
                    if _b_spy_pct <= -1.5: _b_mkt = -2
                    elif _b_spy_pct <= -0.5: _b_mkt = -1
                    elif _b_spy_pct >= 1.5: _b_mkt = 2
                    elif _b_spy_pct >= 0.5: _b_mkt = 1
                _b_comp = _b_os + _b_ps + _b_vs + _b_fs + _b_gs + _b_mkt
                if _b_comp >= 2:
                    _b_sig = "BULLISH"; _bt_bull += 1
                elif _b_comp <= -2:
                    _b_sig = "BEARISH"; _bt_bear += 1
                else:
                    continue
                _bt, _bn, _bpct = get_next_day_stock_move(sel_ticker, _bd)
                if _bpct is not None:
                    _bt_total += 1
                    if (_b_sig == "BULLISH" and _bpct > 0) or (_b_sig == "BEARISH" and _bpct < 0):
                        _bt_wins += 1
            _bt_acc = (_bt_wins / _bt_total * 100) if _bt_total > 0 else 0

            # Build recommendation
            _reasons = []
            if _mkt_regime_txt:
                _reasons.append(_mkt_regime_txt)
            if _c_chg > 0 and _p_chg < 0:
                _reasons.append("📈 Call accumulation + Put unwinding = Bullish flow")
            elif _c_chg < 0 and _p_chg > 0:
                _reasons.append("📉 Call unwinding + Put accumulation = Bearish flow")
            elif _c_chg > 0 and _p_chg > 0:
                _reasons.append("⚡ Both OI increasing = Hedging activity, expect big move")
            else:
                _reasons.append("🔄 Both OI decreasing = Conviction unwinding")

            if _pcr > 1.3:
                _reasons.append(f"🔴 PCR {_pcr:.2f} > 1.3 — Heavy put hedging (contrarian bullish)")
            elif _pcr < 0.7:
                _reasons.append(f"🟢 PCR {_pcr:.2f} < 0.7 — Complacent (contrarian bearish caution)")
            else:
                _reasons.append(f"⚪ PCR {_pcr:.2f} — Balanced sentiment")

            _reasons.append(f"📊 Net OI Bias: {_net_bias:+,.0f} (Call Δ: {_c_chg:+,.0f}, Put Δ: {_p_chg:+,.0f})")
            _reasons.append(f"📈 Volume PCR: {_vol_pcr:.2f} | Call Vol: {_c_vol:,.0f} | Put Vol: {_p_vol:,.0f}")

            if _bt_acc >= 70:
                _conf = "HIGH"
                _conf_color = "#2e7d32"
                _conf_icon = "🟢"
            elif _bt_acc >= 55:
                _conf = "MODERATE"
                _conf_color = "#e65100"
                _conf_icon = "🟡"
            else:
                _conf = "LOW"
                _conf_color = "#c62828"
                _conf_icon = "🔴"

            if _signal == "BULLISH":
                _sig_color = "#2e7d32"
                _sig_icon = "🟢"
                _action = "BUY calls or sell puts" if _bt_acc >= 55 else "Lean bullish but low confidence — small size"
                _strategy = "Bull Call Spread or Long Call" if _bt_acc >= 65 else "Sell OTM Put (income) or small Long Call"
            elif _signal == "BEARISH":
                _sig_color = "#c62828"
                _sig_icon = "🔴"
                _action = "BUY puts or sell calls" if _bt_acc >= 55 else "Lean bearish but low confidence — small size"
                _strategy = "Bear Put Spread or Long Put" if _bt_acc >= 65 else "Sell OTM Call (income) or small Long Put"
            else:
                _sig_color = "#e65100"
                _sig_icon = "⚪"
                _action = "No strong edge — stay flat or play range"
                _strategy = "Iron Condor or Short Straddle (collect premium)"

            _max_score = 7  # 5 OI factors + market regime (±2)
            st.markdown(
                f"<div>"
                f"<div>"
                f"<div>"
                f"<h3>{_sig_icon} {_signal} — Score {_composite:+d}/{_max_score}</h3>"
                f"<p><b>Action:</b> {_action}<br><b>Strategy:</b> {_strategy}</p>"
                f"</div>"
                f"<div>"
                f"<div>{_bt_acc:.0f}%</div>"
                f"<div>{_conf_icon} {_conf} confidence {_bt_total} days backtested Bull: {_bt_bull} | Bear: {_bt_bear}</div>"
                f"</div>"
                f"</div>"
                f"</div>",
                unsafe_allow_html=True
            )

            # Show reasoning
            with st.expander("📋 Signal Reasoning (6-factor breakdown)", expanded=False):
                for _r in _reasons:
                    st.markdown(f"  {_r}")
                st.markdown(f"  **Composite Score**: OI({_oi_sig:+d}) + PCR({_pcr_sig:+d}) + "
                            f"VolPCR({_vol_sig:+d}) + Flow({_flow_sig:+d}) + GEX({_gex_sig:+d}) + "
                            f"Mkt({_mkt_regime_sig:+d}) = **{_composite:+d}**")
                if _oi_only_composite >= 2 and _mkt_regime_sig < 0:
                    st.warning(f"⚠️ OI signals are bullish ({_oi_only_composite:+d}/5) but market is weak "
                              f"({_mkt_regime_sig:+d}). Signal downgraded by market headwind.")
                elif _oi_only_composite <= -2 and _mkt_regime_sig > 0:
                    st.warning(f"⚠️ OI signals are bearish ({_oi_only_composite:+d}/5) but market is strong "
                              f"({_mkt_regime_sig:+d}). Signal softened by market tailwind.")
                if _bt_acc >= 65:
                    st.success(f"✅ Backtest shows {_bt_acc:.0f}% accuracy — signal is reliable for {sel_ticker}")
                elif _bt_acc >= 50:
                    st.warning(f"⚠️ Backtest shows {_bt_acc:.0f}% — slightly above random. Use with caution.")
                else:
                    st.error(f"❌ Backtest shows {_bt_acc:.0f}% — signal not reliable for {sel_ticker}. Avoid trading on this alone.")
        else:
            st.info(f"No OI data for {sel_ticker} on {td_now} to generate recommendation.")
    except Exception as _e:
        st.info(f"Could not generate recommendation: {_e}")
    st.markdown("---")

    # ── Management overview ──
    with st.expander("📘 How to Use This Page — OI Comparison Guide", expanded=False):
        st.markdown("""
**What you're looking at:** Open Interest (OI) shows how many option contracts are outstanding at each strike price.
When OI *increases*, new money is entering. When it *decreases*, positions are closing. The comparison (prev vs now)
reveals where smart money is building or liquidating positions.

**As a trader, you want to know:**
- 🟢 **Where are institutions buying calls?** → Likely support / upside target
- 🔴 **Where are they buying puts?** → Likely resistance / downside hedge
- 🔵 **Deep OTM put buildup?** → Institutional hedge (not directional — market-makers are long)
- 📊 **PCR > 1.3** → Fear in the market (contrarian bullish signal)
- 📊 **PCR < 0.7** → Complacency (contrarian bearish signal)

**Intent color system** (bottom-left chart):
| Color | Signal | What It Means |
|---|---|---|
| 🟢 Dark Green | BULLISH | Calls accumulating ATM — buyers stepping in |
| 🌲 Forest Green | HEDGED BULL | Deep puts + call accumulation — institutions hedging longs |
| 🟣 Purple | STRADDLE | Both calls + puts rising ATM — event/vol play |
| 🔴 Dark Red | BEARISH | Puts building at ATM — directional short pressure |
| 🟠 Orange-Red | NEAR_BEARISH | Puts building just below spot — near-term downside hedge |
| 🔵 Dark Blue | HEDGE | Deep OTM puts growing — institutional tail risk protection |
| 💙 Light Blue | HEDGE_UNWIND | Deep puts being closed — fear subsiding |
| 🟢 Medium Green | BULLISH_BREAK | OTM calls building — expecting breakout higher |
| 🟡 Amber | COVERED_CALL | Far OTM calls written against long stock — capped upside |
| ⚫ Gray | NEUTRAL / UNWIND | No strong conviction or position closing |

**Money Flow interpretation:**
- Large green bars at ATM = bullish positioning → expect support at that strike
- Large red bars below spot = put hedging → spot acts as resistance / floor depending on size
- Rising bars far OTM in calls = momentum traders buying upside — often leads spot
- Gamma wall (huge OI spike) = market-makers will defend that level aggressively
        """)

    # ── Load current open trades for this ticker (for per-chart overlay) ──
    _oi_open_trades = pd.DataFrame()
    try:
        _oi_conn = get_conn()
        _oi_open_trades = pd.read_sql(
            "SELECT * FROM trades WHERE status='OPEN' AND ticker=?", _oi_conn, params=(sel_ticker,))
        _oi_conn.close()
    except Exception:
        pass

    # ── Build charts per expiry ──
    for expiry in sel_expiries:
        edf = comp_df[comp_df["expiry_date"] == expiry].copy()
        if edf.empty:
            continue

        edf = edf.sort_values("strike").reset_index(drop=True)
        strikes = edf["strike"].values
        n_strikes = len(strikes)
        if n_strikes == 0:
            continue
        x_pos = list(range(n_strikes))
        tick_labels = [compact_price(s) if s != int(s) else f"{int(s)}" for s in strikes]

        # Determine spot position
        spot_x = np.interp(spot, strikes, x_pos) if n_strikes > 1 and spot > 0 else 0

        # Expiry type label
        try:
            edt = pd.to_datetime(expiry, format="%m-%d-%Y")
            is_future = edt >= ref_dt
            dte = (edt - ref_dt).days
            exp_tag = f"{'🟢' if is_future else '🔴'} {expiry} | DTE: {dte}"
        except Exception:
            exp_tag = expiry
            is_future = True
            dte = 0

        # ── Intent classification (hedge-aware coloring) ──
        _edf_intent = edf.rename(columns={
            "change_OI_Call": "call_oi_change",
            "change_OI_Put": "put_oi_change",
        }).copy()
        try:
            _edf_intent, _isig, _isig_col, _isig_desc, _idet = _oi_intent_algo(_edf_intent, spot) if spot else (None, "N/A", "#455A64", "No spot", {})
            if _edf_intent is not None:
                edf["intent"] = _edf_intent["intent"].values
                edf["bar_col"] = _edf_intent["bar_col"].values
            else:
                edf["intent"] = "NEUTRAL"; edf["bar_col"] = "#90A4AE"
        except Exception:
            _isig, _isig_col, _isig_desc, _idet = "N/A", "#455A64", "Error", {}
            edf["intent"] = "NEUTRAL"; edf["bar_col"] = "#90A4AE"

        # ── Chart: 2 rows × 2 cols — OI (top-left) | Price+Vol (top-right) | Intent (bottom) ──
        _first = (expiry == sel_expiries[0])
        _lbl_prev_c = f"Call EOD {td_prev}"
        _lbl_prev_p = f"Put EOD {td_prev}"
        _lbl_now_c  = "Call LIVE"
        _lbl_now_p  = "Put LIVE"

        fig = make_subplots(
            rows=2, cols=2,
            row_heights=[0.62, 0.38],
            column_widths=[0.55, 0.45],
            horizontal_spacing=0.06,
            vertical_spacing=0.12,
            subplot_titles=[
                f"{exp_tag} — OI (prev▓ vs now□)",
                f"{exp_tag} — Price & Volume",
                f"OI Change by Intent  [{_isig}]",
                "",
            ],
            specs=[[{"secondary_y": True}, {"secondary_y": True}],
                   [{"secondary_y": False}, {"secondary_y": False}]],
        )

        # ── LEFT: OI comparison ──
        fig.add_trace(go.Bar(
            x=x_pos, y=edf["openInt_Call_prev"],
            name=_lbl_prev_c, marker_color="rgba(144,238,144,0.65)",
            showlegend=_first,
        ), row=1, col=1)
        fig.add_trace(go.Bar(
            x=x_pos, y=-edf["openInt_Put_prev"] * 1.1,
            name=_lbl_prev_p, marker_color="rgba(255,160,160,0.65)",
            showlegend=_first,
        ), row=1, col=1)
        fig.add_trace(go.Bar(
            x=x_pos, y=edf["openInt_Call_now"],
            name=_lbl_now_c, marker_color="rgba(0,0,0,0)",
            marker_line_color="#00c853", marker_line_width=2.0,
            width=0.4, showlegend=_first,
        ), row=1, col=1)
        fig.add_trace(go.Bar(
            x=x_pos, y=-edf["openInt_Put_now"] * 1.1,
            name=_lbl_now_p, marker_color="rgba(0,0,0,0)",
            marker_line_color="#d50000", marker_line_width=2.0,
            width=0.4, showlegend=_first,
        ), row=1, col=1)

        # Price diff lines on OI chart (secondary y)
        call_price_diff = edf["lastPrice_Call_now"] - edf["lastPrice_Call_prev"]
        put_price_diff = edf["lastPrice_Put_now"] - edf["lastPrice_Put_prev"]
        fig.add_trace(go.Scatter(
            x=x_pos, y=call_price_diff,
            name="Call $Δ", mode="lines+markers",
            line=dict(color="#2e7d32", width=2, dash="dot"),
            marker=dict(size=4), showlegend=(expiry == sel_expiries[0]),
        ), row=1, col=1, secondary_y=True)
        fig.add_trace(go.Scatter(
            x=x_pos, y=-put_price_diff,
            name="Put $Δ", mode="lines+markers",
            line=dict(color="#c62828", width=2, dash="dot"),
            marker=dict(size=4), showlegend=(expiry == sel_expiries[0]),
        ), row=1, col=1, secondary_y=True)

        # Last price annotations (compact)
        for idx_r in range(len(edf)):
            row_data = edf.iloc[idx_r]
            cp = row_data["lastPrice_Call_now"]
            pp = row_data["lastPrice_Put_now"]
            if cp > 0:
                fig.add_annotation(
                    x=idx_r, y=row_data["openInt_Call_now"],
                    text=compact_price(cp), showarrow=False,
                    font=dict(size=7, color="#00e676"), yshift=10, row=1, col=1,
                )
            if pp > 0:
                fig.add_annotation(
                    x=idx_r, y=-row_data["openInt_Put_now"] * 1.1,
                    text=compact_price(pp), showarrow=False,
                    font=dict(size=7, color="#ff9100"), yshift=-10, row=1, col=1,
                )

        # Spot vertical line + ATM ±3% shaded region
        if spot > 0:
            fig.add_vline(x=spot_x, line_dash="dash", line_color="#e65100", line_width=2, row=1, col=1)
            fig.add_vline(x=spot_x, line_dash="dash", line_color="#e65100", line_width=2, row=1, col=2)
            fig.add_annotation(
                x=spot_x, y=1.0, yref="y domain", text=f"${spot:.0f}",
                showarrow=False, font=dict(size=8, color="#e65100"),
                yshift=5, row=1, col=1,
            )
            # ATM ±3% shaded band
            _atm_lo = spot * 0.97
            _atm_hi = spot * 1.03
            _atm_lo_x = max(spot_x - (_atm_hi - _atm_lo) / (edf["strike"].iloc[1] - edf["strike"].iloc[0] if len(edf) > 1 else 5) * 1, 0) if len(edf) > 1 else 0
            try:
                _sk_step = float(edf["strike"].iloc[1] - edf["strike"].iloc[0]) if len(edf) > 1 else 5.0
                _atm_lo_x = spot_x - (_atm_hi - _atm_lo) / _sk_step / 2
                _atm_hi_x = spot_x + (_atm_hi - _atm_lo) / _sk_step / 2
                fig.add_vrect(x0=_atm_lo_x, x1=_atm_hi_x, fillcolor="rgba(255,214,0,0.10)",
                              line_width=0, row=1, col=1)
                fig.add_vrect(x0=_atm_lo_x, x1=_atm_hi_x, fillcolor="rgba(255,214,0,0.10)",
                              line_width=0, row=2, col=1)
            except Exception:
                pass

        # OI changes annotation box (top-left of OI chart)
        _c_chg_total = pd.to_numeric(edf["change_OI_Call"], errors="coerce").sum()
        _p_chg_total = pd.to_numeric(edf["change_OI_Put"],  errors="coerce").sum()
        _c_oi_prev   = pd.to_numeric(edf["openInt_Call_prev"], errors="coerce").sum()
        _p_oi_prev   = pd.to_numeric(edf["openInt_Put_prev"],  errors="coerce").sum()
        _pcr_prev    = _p_oi_prev / _c_oi_prev if _c_oi_prev > 0 else 0
        _c_oi_now    = pd.to_numeric(edf["openInt_Call_now"], errors="coerce").sum()
        _p_oi_now    = pd.to_numeric(edf["openInt_Put_now"],  errors="coerce").sum()
        _pcr_now     = _p_oi_now  / _c_oi_now  if _c_oi_now  > 0 else 0
        _c_pct = _c_chg_total / _c_oi_prev * 100 if _c_oi_prev > 0 else 0
        _p_pct = _p_chg_total / _p_oi_prev * 100 if _p_oi_prev > 0 else 0
        # Hedge%: deep OTM puts / total puts
        _deep_puts = pd.to_numeric(
            edf.loc[pd.to_numeric(edf["strike"], errors="coerce") < spot * 0.90, "openInt_Put_now"],
            errors="coerce"
        ).sum() if spot > 0 else 0
        _hedge_pct = int(_deep_puts / _p_oi_now * 100) if _p_oi_now > 0 else 0
        _oi_box = (
            f"OI CHANGES<br>"
            f"Call: {_c_chg_total:+,.0f} ({_c_pct:+.1f}%)<br>"
            f"Put: {_p_chg_total:+,.0f} ({_p_pct:+.1f}%)<br>"
            f"PCR: {_pcr_prev:.2f} → {_pcr_now:.2f}<br>"
            f"Hedge %: {_hedge_pct}%"
        )
        fig.add_annotation(
            xref="x domain", yref="y domain", x=0.01, y=0.02,
            text=_oi_box, showarrow=False,
            font=dict(size=8, color="#1a2332"),
            bgcolor="rgba(255,245,180,0.92)", bordercolor="#aaa", borderwidth=1,
            align="left", xanchor="left", yanchor="bottom",
            row=1, col=1,
        )

        # ── RIGHT: Price + Volume ──
        fig.add_trace(go.Bar(
            x=x_pos, y=edf["call_close_prev"],
            name="Call Px prev", marker_color="rgba(30,120,200,0.6)",
            showlegend=False,
        ), row=1, col=2)
        fig.add_trace(go.Bar(
            x=x_pos, y=-edf["put_close_prev"],
            name="Put Px prev", marker_color="rgba(200,50,50,0.6)",
            showlegend=False,
        ), row=1, col=2)
        fig.add_trace(go.Bar(
            x=x_pos, y=edf["call_close_now"],
            name="Call Px now", marker_color="rgba(0,0,0,0)",
            marker_line_color="#4fc3f7", marker_line_width=1.5,
            width=0.4, showlegend=False,
            text=[compact_price(v) for v in edf["call_close_now"]],
            textposition="outside", textfont=dict(size=7, color="#00e676"),
            cliponaxis=False,
        ), row=1, col=2)
        fig.add_trace(go.Bar(
            x=x_pos, y=-edf["put_close_now"],
            name="Put Px now", marker_color="rgba(0,0,0,0)",
            marker_line_color="#ef5350", marker_line_width=1.5,
            width=0.4, showlegend=False,
            text=[compact_price(v) for v in edf["put_close_now"]],
            textposition="outside", textfont=dict(size=7, color="#ff9100"),
            cliponaxis=False,
        ), row=1, col=2)

        # Volume lines
        fig.add_trace(go.Scatter(
            x=x_pos, y=edf["vol_Call_now"] / 1000,
            name="Call Vol(k)", mode="lines+markers",
            line=dict(color="#4fc3f7", width=1.5), marker=dict(size=3),
            showlegend=False,
        ), row=1, col=2, secondary_y=True)
        fig.add_trace(go.Scatter(
            x=x_pos, y=-edf["vol_Put_now"] / 1000,
            name="Put Vol(k)", mode="lines+markers",
            line=dict(color="#ef5350", width=1.5), marker=dict(size=3),
            showlegend=False,
        ), row=1, col=2, secondary_y=True)

        # ── Row 2: Intent-colored OI Delta bars ──
        _call_chg = pd.to_numeric(edf["change_OI_Call"], errors="coerce").fillna(0)
        _put_chg  = pd.to_numeric(edf["change_OI_Put"],  errors="coerce").fillna(0)
        _bar_cols = list(edf["bar_col"])
        _intents  = list(edf["intent"])

        # Call delta (positive = up)
        fig.add_trace(go.Bar(
            x=x_pos, y=_call_chg,
            name="Call ΔOI", marker_color=_bar_cols,
            width=0.45, showlegend=False,
            text=[f"<b>{v}</b>" if v != "NEUTRAL" else "" for v in _intents],
            textposition="outside", textfont=dict(size=6),
        ), row=2, col=1)
        # Put delta (mirrored negative)
        _put_bar_cols = ["rgba(200,50,50,0.55)" if c == "#90A4AE" else c for c in _bar_cols]
        fig.add_trace(go.Bar(
            x=x_pos, y=-_put_chg,
            name="Put ΔOI", marker_color=_put_bar_cols,
            width=0.45, showlegend=False, opacity=0.75,
        ), row=2, col=1)
        fig.add_hline(y=0, line_color="#212121", line_width=0.8, row=2, col=1)

        # Signal badge in delta panel
        fig.add_annotation(
            xref="x3 domain", yref="y3 domain", x=0.99, y=0.97,
            text=f"<b>{_isig}</b><br>{_isig_desc}",
            showarrow=False, font=dict(size=10, color="white"),
            bgcolor=_isig_col, bordercolor="white", borderwidth=1,
            align="right", xanchor="right", yanchor="top",
        )

        # Intent color legend (row 2 col 2)
        _legend_items = [
            ("BULLISH", "#2E7D32"), ("HEDGED BULL", "#1B5E20"), ("STRADDLE", "#6A1B9A"),
            ("NEAR_BEARISH", "#BF360C"), ("BEARISH", "#C62828"),
            ("HEDGE", "#1565C0"), ("HEDGE_UNWIND", "#42A5F5"),
            ("BULLISH_BREAK", "#388E3C"), ("COVERED_CALL", "#F57F17"),
            ("UNWIND", "#757575"), ("NEUTRAL", "#90A4AE"),
        ]
        _present = set(_intents)
        for _li, (_lbl, _lcol) in enumerate(_legend_items):
            if _lbl in _present:
                fig.add_annotation(
                    xref="x4 domain", yref="y4 domain",
                    x=0.05, y=1.0 - _li * 0.092,
                    text=f"<b>█</b> {_lbl}",
                    showarrow=False, font=dict(size=9, color="#1a2332"),
                    xanchor="left", yanchor="top",
                )

        # Axis ranges
        max_oi = max(
            edf["openInt_Call_now"].max(), edf["openInt_Call_prev"].max(),
            edf["openInt_Put_now"].max() * 1.1, edf["openInt_Put_prev"].max() * 1.1, 1,
        )
        max_px = max(
            edf["call_close_now"].max(), edf["call_close_prev"].max(),
            edf["put_close_now"].max(), edf["put_close_prev"].max(), 1,
        )
        max_vol_k = max(edf["vol_Call_now"].max(), edf["vol_Put_now"].max(), 1) / 1000
        max_delta = max(abs(_call_chg).max(), abs(_put_chg).max(), 1)

        for _rc in [1, 2]:
            fig.update_xaxes(tickmode="array", tickvals=x_pos, ticktext=tick_labels, row=1, col=_rc)
            fig.update_xaxes(tickmode="array", tickvals=x_pos, ticktext=tick_labels, row=2, col=_rc)
        fig.update_yaxes(range=[-max_oi * 1.3, max_oi * 1.3], title_text="OI", row=1, col=1, secondary_y=False)
        fig.update_yaxes(title_text="Price Δ ($)", row=1, col=1, secondary_y=True)
        fig.update_yaxes(range=[-max_px * 2, max_px * 2], title_text="Price", row=1, col=2, secondary_y=False)
        fig.update_yaxes(range=[-max_vol_k * 1.3, max_vol_k * 1.3], title_text="Vol (k)", row=1, col=2, secondary_y=True)
        fig.update_yaxes(range=[-max_delta * 1.4, max_delta * 1.4], title_text="ΔOI", row=2, col=1)
        fig.update_xaxes(showticklabels=False, row=2, col=2)
        fig.update_yaxes(showticklabels=False, showgrid=False, zeroline=False, row=2, col=2)

        # PCR badge
        total_c = edf["openInt_Call_now"].sum()
        total_p = edf["openInt_Put_now"].sum()
        pcr_val = total_p / total_c if total_c > 0 else 0
        bias = "Bullish" if pcr_val < 0.7 else "Bearish" if pcr_val > 1.3 else "Neutral"
        bias_color = "#00e676" if bias == "Bullish" else "#ef5350" if bias == "Bearish" else "#ffd600"
        fig.add_annotation(
            xref="paper", yref="paper", x=0.98, y=0.99,
            text=f"<b>PCR {pcr_val:.2f} ({bias})</b>",
            showarrow=False, font=dict(size=11, color=bias_color),
            bgcolor="rgba(255,255,255,0.9)", bordercolor=bias_color, borderwidth=1,
        )

        fig.update_layout(
            template="plotly_white",
            height=640,
            barmode="overlay",
            bargap=0.02,
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            legend=dict(orientation="h", y=1.08, x=0.5, xanchor="center",
                       font=dict(size=9, color="#1a2332"),
                       bgcolor="rgba(255,255,255,0.9)"),
            margin=dict(l=60, r=30, t=95, b=40),
            font=dict(color="#1a2332"),
        )
        fig.update_xaxes(fixedrange=False)
        fig.update_yaxes(fixedrange=False)
        st.plotly_chart(fig, config={'scrollZoom': True})

        # ── Analysis panel: color legend + trade overlay + outlook ──
        _an1, _an2 = st.columns([1, 1])

        with _an1:
            st.markdown("##### 🎨 Intent Color Guide")
            _INTENT_META = [
                ("BULLISH",       "#2E7D32", "ATM call buildup — fresh buyers entering"),
                ("HEDGED BULL",   "#1B5E20", "Deep puts + ATM calls — institutions hedging longs"),
                ("MILD BULL",     "#558B2F", "Slight call bias — watch for follow-through"),
                ("STRADDLE",      "#6A1B9A", "Both sides at ATM — event/volatility play"),
                ("BEARISH",       "#B71C1C", "ATM put buildup — directional shorts"),
                ("NEAR_BEARISH",  "#BF360C", "Puts below spot — near-term downside hedge"),
                ("MILD BEAR",     "#BF360C", "Slight put bias — monitor for acceleration"),
                ("HEDGE",         "#1565C0", "Deep OTM puts — institutional tail risk protection"),
                ("HEDGE_UNWIND",  "#42A5F5", "Deep puts closing — fear subsiding, bullish tilt"),
                ("BULLISH_BREAK", "#388E3C", "OTM calls building — breakout buyers positioning"),
                ("COVERED_CALL",  "#F57F17", "Far OTM calls written — capped upside, range play"),
                ("UNWIND",        "#757575", "Both sides falling — position liquidation"),
                ("NEUTRAL",       "#90A4AE", "Balanced / low conviction — no actionable edge"),
            ]
            _present_intents = set(edf.get("intent", pd.Series([])).unique()) | {_isig.split()[0]}
            _html_legend = "<div>"
            for _il, (_iname, _icol, _iexpl) in enumerate(_INTENT_META):
                _dim = "opacity:0.45;" if _iname not in _present_intents else ""
                _html_legend += (
                    f"<div>"
                    f"{_iname}"
                    f" {_iexpl}</div>"
                )
            _html_legend += "</div>"
            st.markdown(_html_legend, unsafe_allow_html=True)

        with _an2:
            st.markdown("##### 🧠 Money Flow & Trade Outlook")

            # ── Key levels ──
            _gamma_wall_threshold = edf[["openInt_Call_now","openInt_Put_now"]].sum(axis=1).mean() * 2
            _gamma_walls = edf[edf[["openInt_Call_now","openInt_Put_now"]].sum(axis=1) >= _gamma_wall_threshold]["strike"].tolist()
            _max_pain_candidates = edf.copy()
            _mp_best, _mp_pain = None, float("inf")
            for _mps in edf["strike"]:
                _pain = (edf[edf["strike"] > _mps]["openInt_Call_now"] * (edf[edf["strike"] > _mps]["strike"] - _mps)).sum() + \
                        (edf[edf["strike"] < _mps]["openInt_Put_now"] * (_mps - edf[edf["strike"] < _mps]["strike"])).sum()
                if _pain < _mp_pain:
                    _mp_pain, _mp_best = _pain, _mps

            _net_call_chg = edf["change_OI_Call"].sum()
            _net_put_chg  = edf["change_OI_Put"].sum()
            _flow_dir = "📈 Call-heavy" if _net_call_chg > _net_put_chg * 1.2 else ("📉 Put-heavy" if _net_put_chg > _net_call_chg * 1.2 else "↔️ Balanced")

            _sig_color_map = {
                "BULLISH":"#2E7D32","HEDGED BULL":"#1B5E20","MILD BULL":"#558B2F",
                "BEARISH":"#B71C1C","NEAR_BEARISH":"#BF360C","MILD BEAR":"#BF360C",
                "STRADDLE":"#6A1B9A","HEDGE":"#1565C0","NEUTRAL":"#455A64","QUIET":"#455A64",
            }
            _sig_hex = _sig_color_map.get(_isig, "#455A64")

            _outlook_lines = []
            _outlook_lines.append(f"**Signal:** {_isig} — {_isig_desc}")
            _outlook_lines.append(f"**Money Flow:** {_flow_dir} | Net Call ΔOI: {_net_call_chg:+,.0f} | Net Put ΔOI: {_net_put_chg:+,.0f}")
            if _mp_best:
                _mp_dist = (_mp_best - spot) / spot * 100 if spot else 0
                _mp_dir = "above" if _mp_best > spot else "below"
                _outlook_lines.append(f"**Max Pain:** ${_mp_best:.0f} ({abs(_mp_dist):.1f}% {_mp_dir} spot) — options expire worthless here for most buyers")
            if _gamma_walls:
                _outlook_lines.append(f"**Gamma Walls:** ${', $'.join(f'{g:.0f}' for g in _gamma_walls[:4])} — market-makers defend these levels aggressively")

            # ── What to expect ──
            _expect = []
            if "BULLISH" in _isig or "BULL" in _isig:
                _expect.append("🟢 Expect upward pressure — call writers (dealers) will buy stock to delta-hedge, amplifying moves up")
                if _gamma_walls:
                    _expect.append(f"🟢 If spot clears ${max(_gamma_walls):.0f} gamma wall, move could accelerate (gamma squeeze)")
            elif "BEARISH" in _isig or "BEAR" in _isig:
                _expect.append("🔴 Expect downward pressure — put buyers are positioning for a drop; dealers selling stock to hedge")
                if _mp_best and spot and _mp_best < spot:
                    _expect.append(f"🔴 Max pain ${_mp_best:.0f} is below spot — gravity pulls toward max pain into expiry")
            elif "HEDGE" == _isig:
                _expect.append("🔵 Institutions are buying protective puts — they expect volatility but are still long the stock")
                _expect.append("🔵 Large hedge positions reduce dealer gamma → expect wider bid/ask and choppier moves")
            elif "STRADDLE" in _isig:
                _expect.append("🟣 Both sides active at ATM — expect a big move but direction unclear (earnings, Fed, event?)")
            else:
                _expect.append("⚪ No strong institutional conviction at this expiry — follow price action and volume")

            # ── Current trades overlay ──
            _trades_this_exp = pd.DataFrame()
            if not _oi_open_trades.empty:
                try:
                    _exp_ymd = pd.to_datetime(expiry, format="%m-%d-%Y").strftime("%Y-%m-%d")
                except Exception:
                    _exp_ymd = expiry
                _trades_this_exp = _oi_open_trades[_oi_open_trades["expiry"] == _exp_ymd]

            if not _trades_this_exp.empty:
                _outlook_lines.append("---")
                _outlook_lines.append("**📂 Your Positions at This Expiry:**")
                for _, _tr in _trades_this_exp.iterrows():
                    _tr_k = float(_tr.get("strike", 0))
                    _tr_ot = str(_tr.get("option_type","")).upper()
                    _tr_ep = float(_tr.get("entry_price", 0))
                    _tr_qty = int(_tr.get("quantity", 1) or 1)
                    _tr_side = "BUY" if _tr_qty > 0 else "SELL"
                    # Find nearest strike row in edf
                    try:
                        _nearest = edf.iloc[(edf["strike"] - _tr_k).abs().argsort()[:1]]
                        _at_intent = _nearest["intent"].iloc[0] if "intent" in _nearest.columns else "N/A"
                        _at_call_chg = int(_nearest["change_OI_Call"].iloc[0])
                        _at_put_chg  = int(_nearest["change_OI_Put"].iloc[0])
                        _oi_context  = f"ΔCall OI: {_at_call_chg:+,} | ΔPut OI: {_at_put_chg:+,} | Zone: {_at_intent}"
                    except Exception:
                        _oi_context = ""
                    # Trade-specific outlook
                    _tr_pnl_hint = ""
                    if _tr_side == "BUY" and _tr_ot == "CALL":
                        _tr_pnl_hint = "✅ Benefits if market moves up past your strike" if "BULL" in _isig else "⚠️ OI signal working against your long call"
                    elif _tr_side == "BUY" and _tr_ot == "PUT":
                        _tr_pnl_hint = "✅ Benefits if market moves down" if "BEAR" in _isig else "⚠️ OI signal working against your long put"
                    elif _tr_side == "SELL" and _tr_ot == "CALL":
                        _tr_pnl_hint = "✅ Covered call / short call benefits from BULLISH OI → stay in range" if "NEUTRAL" in _isig else "⚠️ Watch for breakout above strike"
                    elif _tr_side == "SELL" and _tr_ot == "PUT":
                        _tr_pnl_hint = "✅ Short put benefits from bullish/neutral OI" if "BULL" in _isig or "NEUTRAL" in _isig else "⚠️ OI pressure at puts — monitor closely"
                    _outlook_lines.append(f"  • **{_tr_side} {_tr_ot} ${_tr_k:.0f}** ×{abs(_tr_qty)} entry ${_tr_ep:.2f} — {_oi_context}")
                    if _tr_pnl_hint:
                        _outlook_lines.append(f"    {_tr_pnl_hint}")

            if not _trades_this_exp.empty:
                _expect_prefix = "**What to expect given your positions:**"
            else:
                _expect_prefix = "**What to expect:**"
            _outlook_lines.append("---")
            _outlook_lines.append(_expect_prefix)
            for _el in _expect:
                _outlook_lines.append(f"  {_el}")

            st.markdown("<br>".join(_outlook_lines), unsafe_allow_html=True)

        # ── Detail table ──
        tbl = edf[["strike", "openInt_Call_prev", "openInt_Call_now", "change_OI_Call",
                    "openInt_Put_prev", "openInt_Put_now", "change_OI_Put",
                    "lastPrice_Call_now", "lastPrice_Put_now"]].copy()
        tbl.columns = ["Strike", "Call OI Prev", "Call OI Now", "Call ΔOI",
                       "Put OI Prev", "Put OI Now", "Put ΔOI", "Call Px", "Put Px"]
        tbl["Call Px"] = tbl["Call Px"].apply(compact_price)
        tbl["Put Px"] = tbl["Put Px"].apply(compact_price)
        with st.expander(f"📋 {expiry} — Strike Detail ({len(edf)} strikes)"):
            st.dataframe(tbl, hide_index=True)

    # ══════════════════════════════════════════════════════════════════
    # ──  BACKTESTING: OI signals vs actual next-day moves
    # ══════════════════════════════════════════════════════════════════
    st.markdown("---")
    st.markdown("<div>📊 OI Signal Backtest — Did OI Predict the Move?</div>", unsafe_allow_html=True)
    st.caption("**Enhanced 5-factor composite signal**: OI bias + PCR contrarian + Volume PCR + OI flow pattern + GEX direction. "
               "Signals only fire at high conviction (score ≥2 or ≤-2 out of 5).")

    bt_dates = available_trade_dates()
    max_bt = st.slider("Backtest span (days)", min_value=5, max_value=len(bt_dates), value=min(len(bt_dates), 50), step=5)
    bt_results = []
    for d in bt_dates[:max_bt]:
        day_data = load_oi_for_date(d)
        tk_data = day_data[day_data["ticker"] == sel_ticker]
        if tk_data.empty:
            continue

        # ── Factor 1: Net OI bias (call OI change − put OI change) ──
        c_chg = pd.to_numeric(tk_data["change_OI_Call"], errors="coerce").sum()
        p_chg = pd.to_numeric(tk_data["change_OI_Put"], errors="coerce").sum()
        net_bias = c_chg - p_chg
        oi_signal = 1 if net_bias > 0 else -1

        # ── Factor 2: PCR contrarian (high PCR = fearful = contrarian bullish) ──
        c_oi = pd.to_numeric(tk_data["openInt_Call_now"], errors="coerce").sum()
        p_oi = pd.to_numeric(tk_data["openInt_Put_now"], errors="coerce").sum()
        pcr_d = p_oi / c_oi if c_oi > 0 else 0
        pcr_signal = 1 if pcr_d > 1.3 else (-1 if pcr_d < 0.7 else 0)  # contrarian

        # ── Factor 3: Volume PCR (high put volume = fear = contrarian bullish) ──
        c_vol = pd.to_numeric(tk_data.get("vol_Call_now", pd.Series([0])), errors="coerce").sum()
        p_vol = pd.to_numeric(tk_data.get("vol_Put_now", pd.Series([0])), errors="coerce").sum()
        vol_pcr = p_vol / c_vol if c_vol > 0 else 0
        vol_signal = 1 if vol_pcr > 1.3 else (-1 if vol_pcr < 0.7 else 0)  # contrarian

        # ── Factor 4: OI flow pattern ──
        if c_chg > 0 and p_chg < 0:
            flow_signal = 1   # Call accumulation + put liquidation = bullish
        elif c_chg < 0 and p_chg > 0:
            flow_signal = -1  # Call liquidation + put accumulation = bearish
        else:
            flow_signal = 0   # Both rising (hedge) or both falling (unwind)

        # ── Factor 5: GEX direction (positive GEX = range-bound, negative = big move) ──
        strikes_data = pd.to_numeric(tk_data.get("strike", pd.Series([0])), errors="coerce")
        gex_net = (c_oi * strikes_data).sum() - (p_oi * strikes_data).sum() if len(strikes_data) > 0 else 0
        # Positive GEX with bullish flow = confirming, negative GEX = volatile
        gex_signal = 1 if gex_net > 0 and oi_signal > 0 else (-1 if gex_net < 0 and oi_signal < 0 else 0)

        # ── Composite score (-5 to +5) ──
        composite = oi_signal + pcr_signal + vol_signal + flow_signal + gex_signal
        if composite >= 2:
            signal = "BULLISH"
        elif composite <= -2:
            signal = "BEARISH"
        else:
            signal = "NEUTRAL"

        close_t, close_n, pct_n = get_next_day_stock_move(sel_ticker, d)
        correct = None
        reason = ""
        if signal == "NEUTRAL":
            reason = f"Mixed signals (score={composite:+d}) — no high-conviction bias"
        elif pct_n is not None:
            correct = (signal == "BULLISH" and pct_n > 0) or (signal == "BEARISH" and pct_n < 0)
            if correct is False and abs(pct_n) > 1.5:
                reason = "Large counter-move — likely news/event driven"
            elif correct is False:
                reason = f"Signal missed (score={composite:+d})"
            else:
                reason = f"✓ Correct (score={composite:+d})"
        else:
            reason = "No next-day data (latest or holiday)"
        bt_results.append({
            "Date": d, "Signal": signal, "Score": f"{composite:+d}",
            "Net Bias": f"{net_bias:+,.0f}", "PCR": f"{pcr_d:.2f}",
            "Close": f"${close_t:.2f}" if close_t else "—",
            "Next Close": f"${close_n:.2f}" if close_n else "—",
            "Next Day %": f"{pct_n:+.2f}%" if pct_n is not None else "—",
            "Correct?": "✅" if correct is True else ("❌" if correct is False else "—"),
            "Reason": reason,
        })

    if bt_results:
        bt_df = pd.DataFrame(bt_results)
        total = sum(1 for r in bt_results if r["Correct?"] in ("✅", "❌"))
        wins = sum(1 for r in bt_results if r["Correct?"] == "✅")
        neutrals = sum(1 for r in bt_results if r["Correct?"] == "—")
        acc = wins / total * 100 if total > 0 else 0
        bc1, bc2, bc3, bc4 = st.columns(4)
        bc1.metric("Signal Accuracy", f"{acc:.0f}%", delta="Good" if acc > 55 else "Weak", delta_color="normal")
        bc2.metric("Days Tested", total)
        bc3.metric("Win / Lose", f"{wins} / {total - wins}")
        bc4.metric("Neutral (—)", neutrals, delta="Filtered out")
        st.caption("**Score** = 5-factor composite (OI bias + PCR contrarian + Vol PCR + Flow pattern + GEX). "
                   "Range: -5 to +5. Signal fires only at ≥+2 (BULLISH) or ≤-2 (BEARISH). "
                   "**—** = NEUTRAL (low conviction). **❌ large counter-move** = likely news-driven.")
        st.dataframe(bt_df, hide_index=True)

        # ── Visual: signal accuracy over time ──
        signal_rows = [r for r in bt_results if r["Correct?"] in ("✅", "❌")]
        if len(signal_rows) >= 3:
            sig_df = pd.DataFrame(signal_rows)
            sig_df["win"] = sig_df["Correct?"] == "✅"
            sig_df["rolling_acc"] = sig_df["win"].rolling(min(5, len(sig_df)), min_periods=1).mean() * 100
            fig_acc = go.Figure()
            fig_acc.add_trace(go.Scatter(
                x=sig_df["Date"], y=sig_df["rolling_acc"],
                mode="lines+markers", name="Rolling Accuracy",
                line=dict(color="#0066cc", width=2),
                marker=dict(size=5),
            ))
            fig_acc.add_hline(y=50, line_dash="dash", line_color="#888", annotation_text="Random (50%)")
            fig_acc.update_layout(template="plotly_white", height=250,
                                  yaxis_title="Accuracy %", xaxis_title="Date",
                                  margin=dict(t=20, b=30))
            st.plotly_chart(fig_acc)
    else:
        st.info("Not enough data to backtest.")

    # ══════════════════════════════════════════════════════════════════
    # ──  ADVANCED ANALYSIS
    # ══════════════════════════════════════════════════════════════════
    st.markdown("---")
    st.markdown("<div>🧠 Advanced OI + Price + Volume Analysis</div>", unsafe_allow_html=True)

    full_df = comp_df.copy()
    analysis = advanced_oi_analysis(full_df, sel_ticker, spot, td_now, td_prev)

    if analysis:
        for finding in analysis:
            cat = finding["category"]
            sig = finding["signal"]
            det = finding["detail"]
            imp = finding["impact"]

            if imp in ("BULLISH", "CONTRARIAN_BULL"):
                badge = "badge-bull"
                border_class = "bullish"
            elif imp in ("BEARISH", "CONTRARIAN_BEAR"):
                badge = "badge-bear"
                border_class = "bearish"
            elif imp == "VOLATILE":
                badge = "badge-volatile"
                border_class = "volatile"
            else:
                badge = "badge-neutral"
                border_class = ""

            if cat == "🏁 VERDICT":
                st.markdown(f"### {det}")
            else:
                st.markdown(
                    f"<div><b>{cat}</b> &nbsp; {sig}<br>{det}</div>",
                    unsafe_allow_html=True,
                )
    else:
        st.info("Not enough data for advanced analysis.")

    # ── Multi-Day OI Accumulation & Conviction ──
    st.markdown("---")
    st.markdown("<div>📈 Multi-Day OI Accumulation & Strike Conviction</div>", unsafe_allow_html=True)
    _oi_days_c = st.slider("Lookback (trade days)", 3, 15, 7, key="oi_comp_conv_days")

    with st.spinner(f"Loading {_oi_days_c}-day OI trend…"):
        _mdf_c, _mdates_c = _load_oi_multi_day(sel_ticker, _oi_days_c)

    if not _mdf_c.empty:
        _cum_c = _mdf_c.groupby("strike").agg(
            cum_call=("change_OI_Call", "sum"),
            cum_put=("change_OI_Put", "sum"),
        ).reset_index()
        _cum_c["cum_net"] = _cum_c["cum_call"] - _cum_c["cum_put"]
        _cum_c = _cum_c[(_cum_c["cum_call"].abs() + _cum_c["cum_put"].abs()) > 0].sort_values("strike")

        _fig_c = go.Figure()
        _fig_c.add_trace(go.Bar(x=_cum_c["strike"], y=_cum_c["cum_call"],
                                name=f"Cum Call OI Δ ({_oi_days_c}d)", marker_color="#00c853"))
        _fig_c.add_trace(go.Bar(x=_cum_c["strike"], y=_cum_c["cum_put"],
                                name=f"Cum Put OI Δ ({_oi_days_c}d)", marker_color="#ff1744"))
        _fig_c.add_trace(go.Scatter(x=_cum_c["strike"], y=_cum_c["cum_net"],
                                    name="Net Bias", mode="lines+markers",
                                    line=dict(color="#ffd600", width=2), marker=dict(size=5)))
        if spot and spot > 0:
            _fig_c.add_vline(x=spot, line_dash="dash", line_color="#aaaaaa",
                             annotation_text=f"Spot ${spot:.1f}", annotation_position="top right")
        _fig_c.update_layout(
            barmode="group", template="plotly_dark",
            title=f"{sel_ticker} — {_oi_days_c}-Day Cumulative OI Build",
            xaxis_title="Strike", yaxis_title="Cumulative OI Change",
            height=430, margin=dict(t=55, b=40),
            legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0))
        st.plotly_chart(_fig_c, use_container_width=True)

        _conv_c = _compute_oi_conviction(_mdf_c, _mdates_c, spot or 0)
        if not _conv_c.empty:
            _cc1, _cc2 = st.columns([3, 2])
            with _cc1:
                st.markdown(f"**🎯 Strike Conviction ({_oi_days_c}d build)**")
                _dc2 = _conv_c.head(15)[
                    ["strike", "direction", "conviction", "streak", "streak_dir", "cum_net", "consistency", "n_days"]
                ].copy()
                _dc2.columns = ["Strike", "Dir", "Score/10", "Streak", "Streak Dir", "Cum Net OI Δ", "Consistency", "Days"]
                _dc2["Cum Net OI Δ"] = _dc2["Cum Net OI Δ"].apply(lambda x: f"{x:+,.0f}")
                _dc2["Consistency"] = _dc2["Consistency"].apply(lambda x: f"{x:.0%}")
                st.dataframe(_dc2, hide_index=True)
            with _cc2:
                st.markdown("**📊 Market Bias**")
                _bh = len(_conv_c[(_conv_c["direction"] == "BULL") & (_conv_c["conviction"] >= 7)])
                _bea = len(_conv_c[(_conv_c["direction"] == "BEAR") & (_conv_c["conviction"] >= 7)])
                _bias2 = ("🟢 BULLISH" if _bh > _bea else "🔴 BEARISH" if _bea > _bh else "⚪ NEUTRAL")
                st.metric("High-Conv BULL strikes (≥7)", _bh)
                st.metric("High-Conv BEAR strikes (≥7)", _bea)
                st.markdown(f"**Overall: {_bias2}**")
            # Trade ideas
            _hc2 = _conv_c[_conv_c["conviction"] >= 6].head(5)
            if not _hc2.empty:
                st.markdown("### 🚀 High-Conviction Trade Ideas")
                st.caption("Based on consistent OI accumulation. Not financial advice.")
                for _, _r2 in _hc2.iterrows():
                    _sk2 = float(_r2["strike"])
                    _dir2 = str(_r2["direction"])
                    _cv2 = float(_r2["conviction"])
                    _stk2 = int(_r2["streak"])
                    _cum2 = float(_r2["cum_net"])
                    _dist2 = abs(_sk2 - (spot or _sk2)) / (spot or _sk2) * 100 if spot else 0
                    _skt2 = "ATM" if _dist2 < 3 else ("NTM" if _dist2 < 8 else "OTM")
                    _icon2 = "🟢" if _dir2 == "BULL" else "🔴" if _dir2 == "BEAR" else "⚪"
                    if _dir2 == "BULL":
                        _setup2 = f"Long ${_sk2:.0f} Call" if _skt2 in ("ATM","NTM") else f"Long ${_sk2:.0f} Call (OTM)"
                        _why2 = f"Call OI built {_stk2} consecutive days · Net +{_cum2:,.0f} contracts · {_r2['consistency']:.0%} consistent"
                    elif _dir2 == "BEAR":
                        _setup2 = f"Long ${_sk2:.0f} Put" if _skt2 in ("ATM","NTM") else f"${_sk2:.0f} gamma wall — sell calls above"
                        _why2 = f"Put OI built {_stk2} consecutive days · Net {_cum2:,.0f} contracts · {_r2['consistency']:.0%} consistent"
                    else:
                        continue
                    with st.container(border=True):
                        _tc1, _tc2, _tc3 = st.columns([3, 5, 1])
                        _tc1.markdown(f"{_icon2} **{_setup2}**  \n`{_skt2}` · {_dist2:.1f}% from spot")
                        _tc2.markdown(_why2)
                        _tc3.metric("Score", f"{_cv2:.0f}/10")
                        _cc = get_conn()
                        try:
                            _m = _oi_idea_metrics(_cc, sel_ticker, _sk2, _dir2, spot)
                        finally:
                            _cc.close()
                        if _m:
                            _q1, _q2, _q3, _q4 = st.columns(4)
                            _q1.metric("Entry / Invest", f"${_m['entry']:.2f}",
                                       f"${_m['invest']:,.0f}/contract")
                            _q2.metric(f"Target (1σ ~{_m['hold']}d)", f"${_m['target']:.2f}",
                                       f"{_m['move_pct']:+.1f}% spot")
                            _q3.metric("Est. profit", f"{_m['profit_pct']:+.0f}%",
                                       f"R:R {_m['rr']:.1f}")
                            _q4.metric("POP (≈Δ)", f"{_m['pop']:.0f}%",
                                       f"IV {_m['iv']*100:.0f}% · {_m['dte']}DTE")
                            st.caption(
                                f"🧠 **Logic:** {_why2}. Buy the **{_m['expiry']} ${_sk2:.0f} "
                                f"{_m['type'].upper()}** at **${_m['entry']:.2f}** — max risk = premium "
                                f"(**${_m['max_risk']:,.0f}**/contract). On a 1-sigma move to "
                                f"**${_m['target']:.2f}** in ~{_m['hold']} trading days the option is "
                                f"worth ≈**${_m['target_val']:.2f}** (**{_m['profit_pct']:+.0f}%**). "
                                f"Prob. of finishing ITM ≈ |Δ| = **{_m['pop']:.0f}%**."
                            )
                        else:
                            st.caption(f"🧠 **Logic:** {_why2}. _(No live premium stored for this "
                                       f"strike — can't size investment/target.)_")

                # ── Backtest the conviction signals on stored option premiums ──
                st.markdown("---")
                with st.expander("📊 Backtest these signals — win rate, P&L, exit rules & equity curve"):
                    st.caption("Walks every historical date, fires the same OI-conviction ideas, then "
                               "tracks the long option over the next few days using the option prices in "
                               "your DB. Compares a fixed hold vs a take-profit/stop rule, shows the best "
                               "& worst the trade reached (MFE/MAE), and whether the score predicts wins.")
                    _b1, _b2, _b3 = st.columns(3)
                    _bt_look = _b1.slider("Signal build (days)", 3, 12, int(_oi_days_c), key="bt_look")
                    _bt_hold = _b2.slider("Hold (trade days)", 2, 15, 5, key="bt_hold")
                    _bt_mc = _b3.slider("Min conviction", 5, 9, 6, key="bt_mc")
                    _b4, _b5 = st.columns(2)
                    _bt_tp = _b4.slider("Take-profit %", 25, 300, 100, step=25, key="bt_tp")
                    _bt_sl = _b5.slider("Stop-loss %", 25, 100, 50, step=5, key="bt_sl")
                    if st.button("▶ Run backtest", key="bt_run"):
                        with st.spinner("Backtesting OI-conviction signals over stored history…"):
                            _summ, _btdf = _backtest_oi_conviction(
                                sel_ticker, _bt_look, _bt_hold, _bt_mc, 5, float(_bt_tp), float(_bt_sl))
                        if not _summ or _summ.get("n", 0) == 0:
                            st.warning("Not enough stored option-premium history to backtest this "
                                       "ticker / these parameters. Try a shorter hold or lower conviction.")
                        else:
                            st.markdown(f"**Fixed {_bt_hold}-day hold**")
                            _r1 = st.columns(4)
                            _r1[0].metric("Signals tested", _summ["n"])
                            _r1[1].metric("Win rate", f"{_summ['win_rate']:.0f}%")
                            _r1[2].metric("Avg P&L / trade", f"{_summ['avg_pnl']:+.0f}%")
                            _r1[3].metric("Profit factor", f"{_summ['profit_factor']:.2f}")

                            st.markdown(f"**Managed: +{_bt_tp}% take-profit / −{_bt_sl}% stop**")
                            _r2 = st.columns(4)
                            _r2[0].metric("Win rate", f"{_summ['mgd_win_rate']:.0f}%",
                                          f"{_summ['mgd_win_rate']-_summ['win_rate']:+.0f} pts vs hold")
                            _r2[1].metric("Avg P&L", f"{_summ['mgd_avg_pnl']:+.0f}%",
                                          f"{_summ['mgd_avg_pnl']-_summ['avg_pnl']:+.0f} pts")
                            _r2[2].metric("Profit factor", f"{_summ['mgd_profit_factor']:.2f}")
                            _r2[3].metric("TP / SL / Time",
                                          f"{_summ['tp_hit']}/{_summ['sl_hit']}/{_summ['time_exit']}")

                            _r3 = st.columns(4)
                            _r3[0].metric("Avg win", f"{_summ['avg_win']:+.0f}%")
                            _r3[1].metric("Avg loss", f"{_summ['avg_loss']:+.0f}%")
                            _r3[2].metric("Avg MFE (best)", f"{_summ['avg_mfe']:+.0f}%")
                            _r3[3].metric("Avg MAE (worst)", f"{_summ['avg_mae']:+.0f}%")

                            if _summ.get("buckets"):
                                st.markdown("**Does the score predict? Win rate by conviction**")
                                _bk = pd.DataFrame([
                                    {"Conviction": k, "Trades": v["n"],
                                     "Win %": round(v["win"]), "Avg P&L %": round(v["avg"])}
                                    for k, v in _summ["buckets"].items()])
                                st.dataframe(_bk, hide_index=True, use_container_width=True)

                            _eqd = _btdf.sort_values("signal_date").reset_index(drop=True)
                            _eqd["cum_fixed"] = _eqd["pnl_pct"].cumsum()
                            _eqd["cum_managed"] = _eqd["managed_pct"].cumsum()
                            _efig = go.Figure()
                            _efig.add_trace(go.Scatter(y=_eqd["cum_fixed"], mode="lines",
                                                       name="Fixed hold", line=dict(color="#8ab4ff", width=2)))
                            _efig.add_trace(go.Scatter(y=_eqd["cum_managed"], mode="lines",
                                                       name="Managed (TP/SL)", line=dict(color="#00e676", width=2)))
                            _efig.update_layout(template="plotly_dark", height=300,
                                                title="Cumulative P&L (sum of per-trade %, equal weight)",
                                                xaxis_title="Trade #", yaxis_title="Cumulative %",
                                                margin=dict(t=44, b=20),
                                                legend=dict(orientation="h", y=1.02, yanchor="bottom"))
                            st.plotly_chart(_efig, use_container_width=True)

                            st.caption(
                                f"{_summ['bull_n']} bull · {_summ['bear_n']} bear · conviction ≥ {_bt_mc}. "
                                f"BULL win {_summ['bull_win']:.0f}% · BEAR win {_summ['bear_win']:.0f}%. "
                                "**Educational backtest on ~6 months of EOD snapshots — daily closes only "
                                "(intraday TP/SL touches not captured); no commissions/slippage. Past "
                                "results don't guarantee future returns.**")
                            st.dataframe(
                                _btdf.drop(columns=["path"], errors="ignore").sort_values("signal_date"),
                                hide_index=True, use_container_width=True)

                    # ── Auto-optimize exit + walk-forward (out-of-sample) ──
                    st.markdown("---")
                    if st.button("🔧 Auto-optimize exit + walk-forward test", key="bt_opt"):
                        with st.spinner("Grid-searching TP/SL and walk-forward testing…"):
                            _best, _grid, _wf = _optimize_oi_exit(sel_ticker, _bt_look, _bt_hold, _bt_mc)
                        if not _best:
                            st.warning("Not enough data to optimize for this ticker / parameters.")
                        else:
                            st.markdown("**🔧 Best exit rule (in-sample grid-search, ranked by expectancy)**")
                            _o = st.columns(4)
                            _o[0].metric("Best take-profit", f"+{_best['tp']:.0f}%")
                            _o[1].metric("Best stop-loss", f"−{_best['sl']:.0f}%")
                            _o[2].metric("Expectancy", f"{_best['exp']:+.0f}%/trade")
                            _o[3].metric("Profit factor",
                                         ("∞" if _best['pf'] == float('inf') else f"{_best['pf']:.2f}"))
                            try:
                                _piv = _grid.pivot(index="tp", columns="sl", values="exp")
                                _hf = go.Figure(go.Heatmap(
                                    z=_piv.values,
                                    x=[f"−{c:.0f}%" for c in _piv.columns],
                                    y=[f"+{r:.0f}%" for r in _piv.index],
                                    colorscale="RdYlGn", zmid=0,
                                    colorbar=dict(title="Exp %/trade")))
                                _hf.update_layout(template="plotly_dark", height=340,
                                                  title="Expectancy by Take-Profit (rows) × Stop-Loss (cols)",
                                                  xaxis_title="Stop-loss", yaxis_title="Take-profit",
                                                  margin=dict(t=44, b=10))
                                st.plotly_chart(_hf, use_container_width=True)
                            except Exception:
                                pass
                            st.markdown("**🧪 Walk-forward — optimize on 1st half → test on unseen 2nd half**")
                            _w = st.columns(4)
                            _w[0].metric("Chosen TP / SL", f"+{_wf['tp']:.0f}% / −{_wf['sl']:.0f}%")
                            _w[1].metric("In-sample exp", f"{_wf['train_exp']:+.0f}%")
                            _w[2].metric("Out-of-sample exp", f"{_wf['test_exp']:+.0f}%")
                            _w[3].metric("OOS win / PF",
                                         f"{_wf['test_win']:.0f}% / " +
                                         ("∞" if _wf['test_pf'] == float('inf') else f"{_wf['test_pf']:.2f}"))
                            _oos_ok = _wf["test_exp"] > 0
                            st.caption(
                                f"Trained on {_wf['n_train']} trades, tested on {_wf['n_test']} **unseen** trades. "
                                + ("✅ Out-of-sample expectancy stayed **positive** — the edge held up, not just "
                                   "curve-fit." if _oos_ok else
                                   "⚠️ Out-of-sample expectancy went **negative** — the in-sample 'best' was likely "
                                   "curve-fit; don't trust it.")
                                + " The grid 'best' above is in-sample and always looks good; this OOS number is "
                                  "the honest test. Educational, not advice.")
    else:
        st.info(f"Not enough multi-day OI data for {sel_ticker}.")




# ===================================================================
# ──  PAGE: LIVE MOMENTUM SCANNER
# ===================================================================
if page == "🚀 Live Momentum Scanner":

    SCAN_UNIVERSE_DASH = [
        "AAPL","MSFT","NVDA","AMZN","GOOGL","META","AVGO","ORCL",
        "AMD","MRVL","MU","QCOM","INTC","ARM","SMCI","ON","TXN","AMAT","LRCX","KLAC",
        "PLTR","SNOW","NET","CRWD","ZS","DDOG","PANW","NOW","OKTA","FTNT",
        "JPM","GS","MS","BAC","V","MA","PYPL","COIN","HOOD",
        "TSLA","RIVN","LCID","ENPH","FSLR","NEE",
        "LLY","ABBV","MRNA","BIIB","PFE","GILD",
        "COST","WMT","TGT","NKE",
        "MSTR","RKLB","SOFI","IBIT","SQQQ","TQQQ",
        "SPY","QQQ","IWM","XLK","XLE","GLD","SLV","USO",
    ]

    @st.cache_data(ttl=300)
    def _run_scanner_dash(universe):
        import warnings; warnings.filterwarnings("ignore")
        try:
            raw = yf.download(
                tickers=" ".join(universe),
                period="60d", interval="1d",
                group_by="ticker", auto_adjust=True,
                progress=False, threads=True,
            )
        except Exception:
            return []
        results = []
        for tk in universe:
            try:
                h = raw[tk] if tk in raw.columns.get_level_values(0) else pd.DataFrame()
                if h.empty or len(h) < 10:
                    continue
                h = h.dropna(subset=["Close"])
                if len(h) < 10:
                    continue
                close   = float(h["Close"].iloc[-1])
                vol_now = float(h["Volume"].iloc[-1])
                vol_20d = float(h["Volume"].iloc[-21:-1].mean()) if len(h) > 21 else vol_now
                ret_5d  = (close / float(h["Close"].iloc[-6])  - 1) * 100 if len(h) > 5  else 0
                ret_10d = (close / float(h["Close"].iloc[-11]) - 1) * 100 if len(h) > 10 else 0
                ret_20d = (close / float(h["Close"].iloc[-21]) - 1) * 100 if len(h) > 20 else 0
                vol_rat = vol_now / max(vol_20d, 1)
                closes  = h["Close"].values
                consec_up = consec_dn = 0
                for i in range(len(closes) - 1, 0, -1):
                    if closes[i] > closes[i - 1]: consec_up += 1
                    else: break
                for i in range(len(closes) - 1, 0, -1):
                    if closes[i] < closes[i - 1]: consec_dn += 1
                    else: break
                high_20d = float(h["High"].iloc[-20:].max())
                low_20d  = float(h["Low"].iloc[-20:].min())
                atr      = float((h["High"] - h["Low"]).rolling(14).mean().iloc[-1])
                momentum  = ret_5d * 3 + ret_10d * 2 + ret_20d
                vol_bonus = 20 if vol_rat >= 2.5 else (12 if vol_rat >= 2.0 else (5 if vol_rat >= 1.5 else 0))
                if momentum > 0: momentum += vol_bonus
                elif momentum < 0: momentum -= vol_bonus

                # PCR from DB
                pcr = 1.0
                try:
                    _pd = q("SELECT pcr_oi FROM stock_daily WHERE ticker=? ORDER BY substr(trade_date,7,4)||substr(trade_date,1,2)||substr(trade_date,4,2) DESC LIMIT 1", (tk,))
                    if _pd and _pd[0][0]:
                        pcr = float(_pd[0][0])
                except Exception:
                    pass

                results.append(dict(
                    Ticker=tk, Price=round(close, 2),
                    Ret5d=round(ret_5d, 1), Ret10d=round(ret_10d, 1), Ret20d=round(ret_20d, 1),
                    VolRatio=round(vol_rat, 1), ConsecUp=consec_up, ConsecDn=consec_dn,
                    Momentum=round(momentum, 1), ATR=round(atr, 2),
                    High20d=round(high_20d, 2), Low20d=round(low_20d, 2), PCR=round(pcr, 2),
                    Stop_Bull=round(max(close - atr * 1.8, low_20d * 0.99), 2),
                    T1_Bull=round(close + atr * 2.0, 2),
                    T2_Bull=round(close + atr * 4.5, 2),
                    Stop_Bear=round(min(close + atr * 1.8, high_20d * 1.01), 2),
                    T1_Bear=round(close - atr * 2.0, 2),
                    T2_Bear=round(close - atr * 4.5, 2),
                ))
            except Exception:
                continue
        return results

    st.title("🚀 Live Momentum Scanner")
    st.caption("Scans 60+ tickers for unstoppable runners and breakdown stocks. Cached 5 min.")

    col_r, col_s = st.columns([3, 1])
    with col_s:
        if st.button("🔄 Refresh Now", type="primary"):
            st.cache_data.clear()

    with st.spinner("Scanning universe (~20s first load, cached after)..."):
        scan_results = _run_scanner_dash(tuple(SCAN_UNIVERSE_DASH))

    if not scan_results:
        st.error("Scanner failed to fetch data. Check internet connection.")
        st.stop()

    df_scan = pd.DataFrame(scan_results).sort_values("Momentum", ascending=False)
    bulls = df_scan[df_scan["Momentum"] > 15].copy()
    bears = df_scan[df_scan["Momentum"] < -15].copy().iloc[::-1]
    neutral = df_scan[(df_scan["Momentum"] >= -15) & (df_scan["Momentum"] <= 15)].copy()

    # ── Summary metrics ──────────────────────────────────────────
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("🟢 Bull Signals", len(bulls))
    m2.metric("🔴 Bear Signals", len(bears))
    m3.metric("⚪ Neutral", len(neutral))
    m4.metric("📊 Universe", len(df_scan))

    st.markdown("---")

    # ── Bull signals ─────────────────────────────────────────────
    tab_bull, tab_bear, tab_all = st.tabs(["🟢 Bull Runners", "🔴 Falling / Breakdown", "📊 Full Universe"])

    with tab_bull:
        if bulls.empty:
            st.info("No strong bull momentum detected right now. Markets may be choppy.")
        else:
            st.subheader(f"🟢 {len(bulls)} Bull Runner{'s' if len(bulls) != 1 else ''}")

            for _, row in bulls.head(8).iterrows():
                tk    = row["Ticker"]
                close = row["Price"]
                r5    = row["Ret5d"]
                r20   = row["Ret20d"]
                vr    = row["VolRatio"]
                cons  = row["ConsecUp"]
                pcr   = row["PCR"]
                stop  = row["Stop_Bull"]
                t1    = row["T1_Bull"]
                t2    = row["T2_Bull"]
                atr   = row["ATR"]
                l20   = row["Low20d"]
                h20   = row["High20d"]
                tag   = "🚀 UNSTOPPABLE" if r5 > 15 else ("📈 BULL RUNNER" if r5 > 7 else "↗️ BUILDING")
                rr    = (t1 - close) / max(close - stop, 0.01)
                ph    = round(close * 0.97, 0)
                pl    = round(stop  * 0.97, 0)

                with st.expander(f"{tag}  {tk}  ${close:.2f}  |  5d {r5:+.1f}%  20d {r20:+.1f}%  Vol {vr:.1f}×", expanded=r5 > 15):
                    c1, c2, c3 = st.columns(3)
                    c1.metric("Price", f"${close:.2f}")
                    c2.metric("5d Return", f"{r5:+.1f}%", delta=f"{r5:+.1f}%")
                    c3.metric("20d Return", f"{r20:+.1f}%", delta=f"{r20:+.1f}%")

                    c4, c5, c6 = st.columns(3)
                    c4.metric("Vol Surge", f"{vr:.1f}×")
                    c5.metric("Consec Days Up", cons)
                    c6.metric("PCR", f"{pcr:.2f}", delta="Bullish" if pcr < 0.7 else ("Bearish" if pcr > 1.3 else "Neutral"))

                    st.markdown("**📥 Trade Setup**")
                    t_c1, t_c2, t_c3, t_c4 = st.columns(4)
                    t_c1.metric("Entry Zone", f"${close - atr*0.3:.1f}–${close + atr*0.2:.1f}")
                    t_c2.metric("🛑 Stop Loss", f"${stop:.2f}", delta=f"{(stop/close-1)*100:.1f}%")
                    t_c3.metric("🎯 Target 1", f"${t1:.2f}", delta=f"{(t1/close-1)*100:+.1f}%")
                    t_c4.metric("🎯 Target 2", f"${t2:.2f}", delta=f"{(t2/close-1)*100:+.1f}%")

                    st.markdown(f"""
**📐 R:R Ratio:** 1 : {rr:.1f} &nbsp;&nbsp; **20d Range:** ${l20:.1f} – ${h20:.1f}

**🛡 Hedge:** Buy **${ph:.0f}p** / Sell **${pl:.0f}p** put spread (≥21 DTE)

**⚠️ Safety Rules:**
- Position size ≤ 2% of NAV
- Trail stop to breakeven after Target 1 hit
- Reduce size or exit if VIX > 25
- Avoid adding near option expiry dates
""")

                    # Mini price chart
                    try:
                        _h = _cached_history(tk, period="30d", interval="1d")
                        if not _h.empty:
                            fig_mini = go.Figure()
                            fig_mini.add_trace(go.Candlestick(
                                x=_h.index, open=_h["Open"], high=_h["High"],
                                low=_h["Low"], close=_h["Close"], name=tk,
                                increasing_line_color="#26a69a", decreasing_line_color="#ef5350",
                            ))
                            fig_mini.add_hline(y=stop, line_color="red", line_dash="dash",
                                               annotation_text=f"Stop ${stop:.1f}")
                            fig_mini.add_hline(y=t1, line_color="green", line_dash="dot",
                                               annotation_text=f"T1 ${t1:.1f}")
                            fig_mini.update_layout(height=280, margin=dict(l=0, r=0, t=20, b=0),
                                                   xaxis_rangeslider_visible=False,
                                                   paper_bgcolor="rgba(0,0,0,0)",
                                                   plot_bgcolor="rgba(0,0,0,0)")
                            st.plotly_chart(fig_mini, use_container_width=True)
                    except Exception:
                        pass

    with tab_bear:
        if bears.empty:
            st.info("No strong breakdown signals right now.")
        else:
            st.subheader(f"🔴 {len(bears)} Falling Stock{'s' if len(bears) != 1 else ''}")

            for _, row in bears.head(8).iterrows():
                tk    = row["Ticker"]
                close = row["Price"]
                r5    = row["Ret5d"]
                r20   = row["Ret20d"]
                vr    = row["VolRatio"]
                cons  = row["ConsecDn"]
                pcr   = row["PCR"]
                stop  = row["Stop_Bear"]
                t1    = row["T1_Bear"]
                t2    = row["T2_Bear"]
                atr   = row["ATR"]
                l20   = row["Low20d"]
                h20   = row["High20d"]
                tag   = "🔥 FALLING KNIFE" if r5 < -15 else ("📉 BREAKDOWN" if r5 < -7 else "↘️ WEAKENING")
                rr    = (close - t1) / max(stop - close, 0.01)
                ch    = round(close * 1.03, 0)
                cl_   = round(stop  * 1.01, 0)

                with st.expander(f"{tag}  {tk}  ${close:.2f}  |  5d {r5:+.1f}%  20d {r20:+.1f}%  Vol {vr:.1f}×", expanded=r5 < -15):
                    c1, c2, c3 = st.columns(3)
                    c1.metric("Price", f"${close:.2f}")
                    c2.metric("5d Return", f"{r5:+.1f}%", delta=f"{r5:+.1f}%")
                    c3.metric("20d Return", f"{r20:+.1f}%", delta=f"{r20:+.1f}%")

                    c4, c5, c6 = st.columns(3)
                    c4.metric("Vol Surge", f"{vr:.1f}×")
                    c5.metric("Consec Days Dn", cons)
                    c6.metric("PCR", f"{pcr:.2f}", delta="Bearish" if pcr > 1.3 else ("Bullish" if pcr < 0.7 else "Neutral"))

                    st.markdown("**📥 Trade Setup (Short via Puts)**")
                    t_c1, t_c2, t_c3, t_c4 = st.columns(4)
                    t_c1.metric("Short Entry", f"${close - atr*0.2:.1f}–${close + atr*0.3:.1f}")
                    t_c2.metric("🛑 Stop Loss", f"${stop:.2f}", delta=f"{(stop/close-1)*100:+.1f}%")
                    t_c3.metric("🎯 Target 1", f"${t1:.2f}", delta=f"{(t1/close-1)*100:+.1f}%")
                    t_c4.metric("🎯 Target 2", f"${t2:.2f}", delta=f"{(t2/close-1)*100:+.1f}%")

                    st.markdown(f"""
**📐 R:R Ratio:** 1 : {rr:.1f} &nbsp;&nbsp; **20d Range:** ${l20:.1f} – ${h20:.1f}

**🛡 Hedge:** Buy **${ch:.0f}c** / Sell **${cl_:.0f}c** call spread (≥21 DTE)

**⚠️ Safety Rules:**
- Use puts with ≥ 21 DTE — avoid expiry-day gamma risk
- Cover/exit on a sharp gap-down open (avoid chasing)
- PCR > 1.5 may signal put exhaustion — tighten stop
- Do NOT short into known support / gamma walls
""")

                    try:
                        _h = _cached_history(tk, period="30d", interval="1d")
                        if not _h.empty:
                            fig_mini = go.Figure()
                            fig_mini.add_trace(go.Candlestick(
                                x=_h.index, open=_h["Open"], high=_h["High"],
                                low=_h["Low"], close=_h["Close"], name=tk,
                                increasing_line_color="#26a69a", decreasing_line_color="#ef5350",
                            ))
                            fig_mini.add_hline(y=stop, line_color="red", line_dash="dash",
                                               annotation_text=f"Stop ${stop:.1f}")
                            fig_mini.add_hline(y=t1, line_color="#00bcd4", line_dash="dot",
                                               annotation_text=f"T1 ${t1:.1f}")
                            fig_mini.update_layout(height=280, margin=dict(l=0, r=0, t=20, b=0),
                                                   xaxis_rangeslider_visible=False,
                                                   paper_bgcolor="rgba(0,0,0,0)",
                                                   plot_bgcolor="rgba(0,0,0,0)")
                            st.plotly_chart(fig_mini, use_container_width=True)
                    except Exception:
                        pass

    with tab_all:
        st.subheader("📊 Full Universe Ranking")

        def _color_momentum(val):
            if val > 30:    return "background-color: #1b5e20; color: white"
            if val > 15:    return "background-color: #2e7d32; color: white"
            if val > 0:     return "background-color: #388e3c; color: white"
            if val > -15:   return "background-color: #bf360c; color: white"
            return "background-color: #b71c1c; color: white"

        def _color_ret(val):
            if val > 10:    return "color: #69f0ae"
            if val > 3:     return "color: #a5d6a7"
            if val < -10:   return "color: #ff5252"
            if val < -3:    return "color: #ef9a9a"
            return ""

        display_cols = ["Ticker", "Price", "Ret5d", "Ret10d", "Ret20d",
                        "VolRatio", "ConsecUp", "ConsecDn", "PCR", "Momentum"]
        df_disp = df_scan[display_cols].rename(columns={
            "Ret5d": "5d%", "Ret10d": "10d%", "Ret20d": "20d%",
            "VolRatio": "Vol×", "ConsecUp": "↑Days", "ConsecDn": "↓Days",
        })
        styled = (df_disp.style
                  .map(_color_momentum, subset=["Momentum"])
                  .map(_color_ret, subset=["5d%", "10d%", "20d%"])
                  .format({"Price": "${:.2f}", "5d%": "{:+.1f}%", "10d%": "{:+.1f}%",
                           "20d%": "{:+.1f}%", "Vol×": "{:.1f}×", "Momentum": "{:.0f}"}))
        st.dataframe(styled, use_container_width=True, height=600)

        # Scatter plot: 5d% vs 20d% bubble chart
        st.subheader("📡 Momentum Map (5d vs 20d Return)")
        fig_scatter = go.Figure()
        for _, row in df_scan.iterrows():
            color = "#2e7d32" if row["Momentum"] > 15 else ("#b71c1c" if row["Momentum"] < -15 else "#546e7a")
            fig_scatter.add_trace(go.Scatter(
                x=[row["Ret20d"]], y=[row["Ret5d"]],
                mode="markers+text",
                text=[row["Ticker"]], textposition="top center",
                marker=dict(size=max(8, min(30, abs(row["Momentum"]) / 3)),
                            color=color, opacity=0.8, line=dict(width=1, color="white")),
                name=row["Ticker"], showlegend=False,
                hovertemplate=f"<b>{row['Ticker']}</b><br>5d: {row['Ret5d']:+.1f}%<br>20d: {row['Ret20d']:+.1f}%<br>Vol: {row['VolRatio']:.1f}×<extra></extra>",
            ))
        fig_scatter.add_hline(y=0, line_color="gray", line_width=1)
        fig_scatter.add_vline(x=0, line_color="gray", line_width=1)
        fig_scatter.update_layout(
            height=550, xaxis_title="20d Return %", yaxis_title="5d Return %",
            paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
            font=dict(color="white"),
        )
        st.plotly_chart(fig_scatter, use_container_width=True)

    st.caption("⚠️ All signals are algorithmic based on price momentum and volume. Always verify with OI data, news, and your own analysis before trading. Past momentum does not guarantee future returns.")

# ═══════════════════════════════════════════════════════════════════════════
# PAGE: HIGH-PROB ENGINE (24-model ensemble)
# ═══════════════════════════════════════════════════════════════════════════
if page == "🧠 High-Prob Engine":
    import math as _hpmath
    try:
        from scipy.stats import norm as _hpnorm
    except ImportError:
        _hpnorm = None
    import numpy as _hpnp

    st.title("🧠 24-Model High-Probability Signal Engine")
    st.caption("VRVP · VWAP · VRP · Put/Call Wall · IV Rank · GEX · PCR-Z · Left Skew · VRP · EM Breach")

    # Import model functions from telegram_bot at runtime
    @st.cache_resource(show_spinner=False)
    def _load_hp_models():
        import importlib.util, sys as _sys
        spec = importlib.util.spec_from_file_location(
            "telegram_bot",
            r"C:\Users\srini\Options_chain_data\NYSE_DATA\telegram_bot.py")
        mod = importlib.util.module_from_spec(spec)
        # Prevent bot from starting
        mod.__name__ = "_hp_import_only"
        try:
            spec.loader.exec_module(mod)
        except SystemExit:
            pass
        except Exception:
            pass
        return mod

    # Preferred: import the bot module directly so we get its FULL namespace
    # (all helpers like get_conn/_safe_float/yf, the engine, and the fixed GEX).
    # Falls back to the source-slice exec only if the import fails.
    @st.cache_resource(show_spinner=False)
    def _get_hp_ns():
        try:
            import telegram_bot as _tb
            _ns = vars(_tb)
            if "high_prob_signals_engine" in _ns:
                return _ns
        except Exception:
            pass
        # Fallback: extract the HP functions via exec of the relevant slice
        import math as _m
        try:
            from scipy.stats import norm as _sn
        except ImportError:
            return None
        import numpy as _np, pandas as _pd
        from datetime import datetime as _dt, timezone as _tz
        import logging
        _log = logging.getLogger("hp_dash")
        with open(r"C:\Users\srini\Options_chain_data\NYSE_DATA\telegram_bot.py", encoding="utf-8") as _f:
            _src = _f.read()
        _start = _src.find("def _bs_gamma_hp")
        _end   = _src.find("\ndef main():", _start)
        if _end == -1:
            _end = len(_src)
        _ns = {"_math": _m, "_spnorm": _sn, "np": _np, "pd": _pd,
               "datetime": _dt, "timezone": _tz, "log": _log}
        exec(_src[_start:_end], _ns)
        return _ns

    hp_ns = _get_hp_ns()

    # Ticker selector
    col_t, col_r = st.columns([3, 1])
    with col_t:
        all_tickers = sorted([r[0] for r in get_conn().execute(
            "SELECT DISTINCT ticker FROM stock_daily ORDER BY ticker").fetchall()])
        sel_ticker = st.selectbox("Select Ticker", all_tickers, index=all_tickers.index("SPY") if "SPY" in all_tickers else 0)
    with col_r:
        run_btn = st.button("🚀 Run Engine", type="primary", use_container_width=True)

    # Also show open positions for quick access
    open_pos_tickers = []
    try:
        _ot = get_conn().execute("SELECT DISTINCT ticker FROM trades WHERE status='OPEN'").fetchall()
        open_pos_tickers = [r[0] for r in _ot]
    except Exception:
        pass
    if open_pos_tickers:
        st.info(f"**Open positions:** {' · '.join(open_pos_tickers)} — click to analyze")
        pos_cols = st.columns(min(len(open_pos_tickers), 6))
        for ci, tk in enumerate(open_pos_tickers[:6]):
            if pos_cols[ci].button(tk, key=f"hp_pos_{tk}"):
                sel_ticker = tk
                run_btn = True

    if run_btn and hp_ns:
        with st.spinner(f"Running 24 models for {sel_ticker}…"):
            try:
                conn_hp = get_conn()
                # Get SPY context
                try:
                    _spy = conn_hp.execute(
                        "SELECT close FROM stock_daily WHERE ticker='SPY'"
                        " ORDER BY substr(trade_date,7,4)||substr(trade_date,1,2)||substr(trade_date,4,2) DESC LIMIT 2"
                    ).fetchall()
                    spy_ret = (float(_spy[0][0]) / float(_spy[1][0]) - 1) * 100 if len(_spy) >= 2 else 0.0
                except Exception:
                    spy_ret = 0.0

                res = hp_ns["high_prob_signals_engine"](sel_ticker, conn_hp, spy_ret)
                conn_hp.close()

                sig   = res["signal"]
                prob  = res["prob"]
                conf  = res["conf"]
                spot  = res["spot"]
                total_m = res.get("total_m", 24)

                # Header metrics
                sig_color = {"BULL": "green", "BEAR": "red", "SELL_PREMIUM": "orange", "NEUTRAL": "gray"}.get(sig, "gray")
                sig_icon  = {"BULL": "🟢", "BEAR": "🔴", "SELL_PREMIUM": "💰", "NEUTRAL": "⚪"}.get(sig, "⚪")
                conf_icon = {"HIGH": "🔥", "MEDIUM": "✅", "LOW": "⚠️"}.get(conf, "⚠️")

                m1, m2, m3, m4, m5 = st.columns(5)
                m1.metric("Signal", f"{sig_icon} {sig}")
                m2.metric("Probability", f"{prob:.0f}%")
                m3.metric("Confidence", f"{conf_icon} {conf}")
                m4.metric("Spot Price", f"${spot:.2f}")
                m5.metric("Models", f"{res['bull_v']}🟢 {res['bear_v']}🔴 {res.get('sell_v',0)}💰 /{total_m}")

                # Strategy box
                warn = res.get("warn", "")
                box_color = "#1a3a1a" if sig == "BULL" else ("#3a1a1a" if sig == "BEAR" else "#2a2a0a")
                st.markdown(
                    f"<div style='background:{box_color};padding:14px;border-radius:8px;"
                    f"border-left:4px solid {'#4CAF50' if sig=='BULL' else '#f44336' if sig=='BEAR' else '#FFC107'}'>"
                    f"<b>📋 Strategy:</b> {res['strategy']}<br>"
                    + (f"⚠️ {warn}" if warn else "")
                    + "</div>", unsafe_allow_html=True)
                st.markdown("")

                # ── VRVP + Walls visual section ──────────────────────────────
                vbox = res.get("vrvp_box", {})
                # Null-guard all vbox values
                _vb_poc = vbox.get("poc"); _vb_vah = vbox.get("vah"); _vb_val = vbox.get("val")
                _vb_hi  = vbox.get("hi");  _vb_lo  = vbox.get("lo")
                wall = res["models"].get("put_call_wall", {})

                # ── Volume Profile Chart (rebuilt from DB for visual) ────────────
                st.markdown("#### 📊 Volume Profile (VRVP) — Last 60 Days")
                st.caption(
                    "Each bar = a price zone. Taller bar = more trading happened there = **stronger support/resistance**. "
                    "**POC** (Point of Control) = the price where the most volume traded — acts as a magnet. "
                    "**Value Area** = where 70% of volume happened — the 'fair price' range."
                )
                try:
                    _vp_conn = get_conn()
                    _px = pd.read_sql(
                        "SELECT high, low, close, volume FROM stock_daily WHERE ticker=?"
                        " ORDER BY substr(trade_date,7,4)||substr(trade_date,1,2)||substr(trade_date,4,2) DESC LIMIT 60",
                        _vp_conn, params=(sel_ticker,))
                    _vp_conn.close()
                    for _c in ['high','low','close','volume']:
                        _px[_c] = pd.to_numeric(_px[_c], errors='coerce')
                    _px = _px.dropna()

                    if len(_px) >= 5:
                        _N = 40
                        _pmin = float(_px['low'].min()); _pmax = float(_px['high'].max())
                        _bsz  = (_pmax - _pmin) / _N
                        _vprof = [0.0] * _N
                        for _, _r in _px.iterrows():
                            _h=float(_r['high']); _l=float(_r['low']); _v=float(_r['volume'])
                            if _h<=_l or _v<=0: continue
                            _lb=max(0,int((_l-_pmin)/_bsz)); _hb=min(_N-1,int((_h-_pmin)/_bsz))
                            _nb=_hb-_lb+1
                            for _b in range(_lb,_hb+1):
                                _vprof[_b]+=_v/_nb
                        _prices = [round(_pmin+(_b+0.5)*_bsz,2) for _b in range(_N)]
                        _poc_b  = int(max(range(_N),key=lambda b:_vprof[b]))
                        _poc_p  = _prices[_poc_b]

                        # Value Area: 70% of total volume
                        _tv = sum(_vprof); _lo_b=_poc_b; _hi_b=_poc_b; _acc=_vprof[_poc_b]
                        while _acc/_tv < 0.70 and (_lo_b>0 or _hi_b<_N-1):
                            _exp_lo=_vprof[_lo_b-1] if _lo_b>0 else 0
                            _exp_hi=_vprof[_hi_b+1] if _hi_b<_N-1 else 0
                            if _exp_lo>=_exp_hi and _lo_b>0: _lo_b-=1; _acc+=_vprof[_lo_b]
                            elif _hi_b<_N-1: _hi_b+=1; _acc+=_vprof[_hi_b]
                            else: break
                        _val_p=_prices[_lo_b]; _vah_p=_prices[_hi_b]
                        _spot_b=max(0,min(_N-1,int((spot-_pmin)/_bsz)))

                        # Assign colors: POC=gold, Value Area=teal, Spot=white, else=blue
                        _colors=[]
                        for _b in range(_N):
                            if _b==_poc_b: _colors.append("#FFD700")
                            elif _lo_b<=_b<=_hi_b: _colors.append("#26a69a")
                            elif _b==_spot_b: _colors.append("#ffffff")
                            else: _colors.append("#1565C0")

                        _fig_vp = go.Figure()
                        _fig_vp.add_trace(go.Bar(
                            x=_vprof, y=_prices, orientation='h',
                            marker_color=_colors,
                            hovertemplate="Price: $%{y:.2f}<br>Volume: %{x:,.0f}<extra></extra>",
                            name="Volume"
                        ))
                        # Vertical lines for key levels
                        for _lv, _lc, _ln in [
                            (spot,   "#ffffff", f"Spot ${spot:.2f}"),
                            (_poc_p, "#FFD700", f"POC ${_poc_p:.2f}"),
                            (_val_p, "#26a69a", f"VAL ${_val_p:.2f}"),
                            (_vah_p, "#26a69a", f"VAH ${_vah_p:.2f}"),
                        ]:
                            _fig_vp.add_hline(y=_lv, line_color=_lc, line_width=1.5,
                                              line_dash="dot", annotation_text=_ln,
                                              annotation_font_color=_lc, annotation_position="right")
                        # Add wall lines if available
                        if wall.get("put_wall"):
                            _fig_vp.add_hline(y=wall["put_wall"], line_color="#ef5350",
                                              line_width=2, annotation_text=f"Put Wall ${wall['put_wall']:.0f}",
                                              annotation_font_color="#ef5350", annotation_position="right")
                        if wall.get("call_wall"):
                            _fig_vp.add_hline(y=wall["call_wall"], line_color="#66bb6a",
                                              line_width=2, annotation_text=f"Call Wall ${wall['call_wall']:.0f}",
                                              annotation_font_color="#66bb6a", annotation_position="right")
                        _fig_vp.update_layout(
                            height=500, showlegend=False,
                            plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
                            font_color="#fafafa",
                            xaxis=dict(title="Volume", showgrid=False),
                            yaxis=dict(title="Price ($)", tickprefix="$"),
                            margin=dict(l=10,r=120,t=10,b=30),
                        )
                        st.plotly_chart(_fig_vp, use_container_width=True)

                        # Legend explanation
                        _lc1, _lc2, _lc3, _lc4 = st.columns(4)
                        _lc1.markdown("🟡 **POC** — Max volume price (magnet)")
                        _lc2.markdown("🟦 **Value Area** — 70% of volume (fair range)")
                        _lc3.markdown("⚪ **Spot** — Current price")
                        _lc4.markdown("🟥🟩 **Walls** — OI support/resistance")

                        # Key level metrics below chart
                        _km1, _km2, _km3, _km4, _km5 = st.columns(5)
                        _km1.metric("POC (Magnet)", f"${_poc_p:.2f}",
                                    delta=f"{(spot-_poc_p)/spot*100:+.1f}% from spot",
                                    delta_color="off")
                        _km2.metric("Value Area High", f"${_vah_p:.2f}",
                                    delta="Sell calls above")
                        _km3.metric("Value Area Low", f"${_val_p:.2f}",
                                    delta="Sell puts below")
                        if wall.get("call_wall"):
                            _km4.metric("Call Wall (Resistance)", f"${wall['call_wall']:.2f}",
                                        delta=f"{wall.get('cw_str',1):.1f}× OI avg")
                        if wall.get("put_wall"):
                            _km5.metric("Put Wall (Support)", f"${wall['put_wall']:.2f}",
                                        delta=f"{wall.get('pw_str',1):.1f}× OI avg")

                        # Strategy insight box
                        if spot > _poc_p:
                            _vp_insight = f"Spot **${spot:.2f}** is ABOVE POC **${_poc_p:.2f}** → bullish structure. Price tends to return to POC when momentum fades."
                        elif spot < _val_p:
                            _vp_insight = f"Spot **${spot:.2f}** is BELOW Value Area → bearish pressure. Watch for bounce at VAL **${_val_p:.2f}**."
                        else:
                            _vp_insight = f"Spot **${spot:.2f}** is INSIDE Value Area (${_val_p:.2f}–${_vah_p:.2f}) → price is at equilibrium. Range-bound trading likely."
                        st.info(f"💡 **Volume Profile Insight:** {_vp_insight}")

                        vrvp_r = res["models"].get("vrvp", {})
                        if vrvp_r.get("reason"):
                            st.caption(f"Engine note: {vrvp_r['reason']}")
                    else:
                        st.info("Not enough price history to build volume profile (need 5+ days).")
                except Exception as _vp_ex:
                    st.warning(f"Volume profile chart error: {_vp_ex}")
                st.markdown("---")

                # All 24 model results table
                st.markdown("#### 📊 All 22 Model Outputs")
                _ML_LABELS = {
                    "gex": "GEX Regime", "pcr_z": "PCR Z-Score",
                    "oi_momentum": "OI 3D Momentum", "gamma_pin": "Gamma Pin",
                    "vol_flow": "Vol Flow", "iv_skew": "IV Skew",
                    "rv_iv": "RV/IV Spread", "oi_term_struct": "OI Term Struct",
                    "maxpain_vel": "Max Pain Velocity", "iv_rank": "IV Rank",
                    "pcp_dev": "PCP Deviation", "vol_regime": "Vol Regime",
                    "multi_expiry": "Multi-Expiry OI", "smart_uoa": "Smart Money UOA",
                    "hhi_pin": "HHI Pin", "pcr_vel": "PCR Velocity",
                    "vrvp": "VRVP (Vol Profile)", "vwap_dev": "VWAP Deviation",
                    "expected_move": "Expected Move", "left_skew": "Left-Tail Skew",
                    "vrp": "Vol Risk Premium", "put_call_wall": "Put/Call Wall",
                }
                tbl_rows = []
                for nm, lbl in _ML_LABELS.items():
                    r   = res["models"].get(nm, {})
                    ms  = r.get("signal", "NEUTRAL")
                    mp  = r.get("prob", 50)
                    mw  = res["weights"].get(nm, 1.0)
                    rsn = r.get("reason", "")[:80]
                    em  = {"BULL": "🟢", "BEAR": "🔴", "NEUTRAL": "⚪", "SELL_PREMIUM": "💰"}.get(ms, "⚪")
                    tbl_rows.append({
                        "Model": lbl, "Signal": f"{em} {ms}",
                        "Prob%": mp, "Weight": round(mw, 2), "Reason": rsn,
                    })
                import pandas as _pdf2
                tdf = _pdf2.DataFrame(tbl_rows)

                def _highlight(row):
                    sig_ = row["Signal"]
                    if "BULL" in sig_:
                        # Bloomberg/industry green: deep green bg, bright white text
                        return ["background-color:#0d4f1c; color:#ffffff; font-weight:600"]*len(row)
                    elif "BEAR" in sig_:
                        # Bloomberg/industry red: deep red bg, bright white text
                        return ["background-color:#6b0000; color:#ffffff; font-weight:600"]*len(row)
                    elif "PREMIUM" in sig_:
                        # Premium sell: amber/gold bg, dark text for contrast
                        return ["background-color:#7a5500; color:#ffffff; font-weight:600"]*len(row)
                    # Neutral: very subtle grey — visible in both light and dark themes
                    return ["background-color:#2d2d2d; color:#e0e0e0"]*len(row)

                st.dataframe(
                    tdf.style.apply(_highlight, axis=1)
                              .bar(subset=["Prob%"], color=["#66bb6a", "#ef5350"],
                                   vmin=40, vmax=90, align="mid")
                              .format({"Prob%": "{:.0f}%", "Weight": "{:.2f}"}),
                    hide_index=True, use_container_width=True, height=680)

                # Premium sell signals summary
                sell_models = [(nm, r) for nm, r in res["models"].items()
                               if r.get("signal") == "SELL_PREMIUM" and r.get("prob", 0) >= 60]
                if sell_models:
                    st.markdown("#### 💰 Premium Collection Opportunities")
                    for nm, r in sorted(sell_models, key=lambda x: -x[1].get("prob", 0)):
                        lbl = _ML_LABELS.get(nm, nm)
                        prob_ = r.get("prob", 0)
                        rsn_  = r.get("reason", "")
                        st.success(f"**{lbl}** — {prob_}% confidence\n\n{rsn_}")

            except Exception as _e:
                st.error(f"Engine error: {_e}")
                import traceback
                st.code(traceback.format_exc())
    elif run_btn and not hp_ns:
        st.error("Could not load model functions. Check that scipy is installed.")


# ===================================================================
# ──  PAGE: MACRO / EVENT HUB
# ──  Squeeze · OpEx · Events · Briefing · Journal · GEX · Macro
# ──  Reuses the Telegram bot's engine + formatters (single source of truth).
# ===================================================================
if page == "📡 Macro/Event Hub":
    _page_header("📡 Macro / Event Hub")
    try:
        import telegram_bot as _tbmod
    except Exception as _e:
        st.error(f"Could not load engine module: {_e}")
        _tbmod = None

    def _render_tg(_html):
        st.markdown(f"<div>{_html}</div>",
                    unsafe_allow_html=True)

    if _tbmod is not None:
        _hub_conn = get_conn()
        _tb_brief, _tb_opex, _tb_sq, _tb_gex, _tb_van, _tb_mom, _tb_ev, _tb_jr, _tb_mac = st.tabs(
            ["☀️ Briefing", "🗓️ OpEx", "🩳 Squeeze", "📐 GEX", "🌀 Vanna", "🚀 Momentum",
             "🌍 Events", "📓 Journal", "📊 Macro"])

        with _tb_brief:
            st.caption("Daily macro brief — optimistic / pessimistic / balanced, with live news.")
            if st.button("🔄 Refresh", key="hub_brief_btn"):
                st.rerun()
            try:
                _render_tg(_tbmod._fmt_briefing(_tbmod.morning_briefing(_hub_conn)))
            except Exception as e:
                st.error(f"Briefing error: {e}")

        with _tb_opex:
            st.caption("Options-expiration radar + post-OpEx playbook.")
            try:
                _render_tg(_tbmod._fmt_opex_report(_tbmod.opex_radar(_hub_conn)))
            except Exception as e:
                st.error(f"OpEx error: {e}")

        with _tb_sq:
            st.caption("Short-interest / days-to-cover / short-covering detector (0–5).")
            _sqt = st.text_input("Ticker", "GME", key="hub_sq_tk").upper().strip()
            if _sqt:
                try:
                    _render_tg(_tbmod._fmt_squeeze_report(_tbmod.short_squeeze_signal(_sqt, _hub_conn)))
                except Exception as e:
                    st.error(f"Squeeze error: {e}")

        with _tb_gex:
            st.caption("Gamma walls / flip / regime + position-aware notes + short interest.")
            _gxt = st.text_input("Ticker(s), comma-separated (blank = your open positions)",
                                 "", key="hub_gex_tk")
            _tks = [x.strip().upper() for x in _gxt.split(",") if x.strip()] or None
            try:
                _reps = _tbmod._gex_reports(_hub_conn, _tks)
                for _r in _reps:
                    _render_tg(_r)
                    st.markdown("---")
            except Exception as e:
                st.error(f"GEX error: {e}")

        with _tb_van:
            st.caption("Dealer vanna/charm (2nd-order greeks) — vanna rally / OpEx melt-up read.")
            _vt = st.text_input("Ticker(s), comma-separated (blank = open positions)", "SPY", key="hub_van_tk")
            _vtks = [x.strip().upper() for x in _vt.split(",") if x.strip()] or None
            try:
                for _r in _tbmod._vanna_reports(_hub_conn, _vtks):
                    _render_tg(_r); st.markdown("---")
            except Exception as e:
                st.error(f"Vanna error: {e}")

        with _tb_mom:
            st.caption("Full-universe 12-1 cross-sectional momentum (Jegadeesh-Titman / AQR). "
                       "Ranks every name in your DB; top decile = trend longs, bottom = shorts/avoid.")
            with st.expander("ℹ️ How to read this — what each heading means"):
                st.markdown("""
**What it is** — ranks every name in your DB by trend strength (12-1 momentum), so you can see leaders to ride and laggards to avoid.

**Columns**
- **Rank** — position out of all names (#1 = strongest trend)
- **Ticker** — the symbol
- **12-1 %** — 12-month return *skipping the most recent month* — the trend that historically persists. **The core score.**
- **6-1 %** — 6-month version (skip last month) — confirms whether the trend is still building or fading
- **1m %** — last month alone — health-check (still going, or rolling over?)
- **>200DMA** — ✅ above / ❌ below the 200-day average (long-term uptrend intact?)
- **Decile** — universe split into 10 buckets: **1 = top 10% (best)**, 10 = bottom 10% (worst)

**Sections**
- 🟢 **Top momentum** — strongest trends → "ride winners" long candidates
- 🔴 **Bottom momentum** — weakest names → avoid longs / careful shorts
- ⭐ **Your open positions, ranked** — where your trades sit
- **Bar chart** — all names; green = top decile, red = bottom decile

**How to use**
- Longs work best in **RISK-ON** (see the Risk Regime view).
- Don't chase a name with a huge 1m pop — wait for a pullback.
- The bottom list is mostly a *"don't go long"* list; short only with defined risk.
- Leveraged / inverse / vol ETFs are excluded for a cleaner signal.

*Educational, not advice. Size for being wrong.*
""")
            _mc1, _mc2, _mc3 = st.columns([1.4, 1, 1])
            with _mc1:
                if st.button("🔄 Recompute today's ranks", key="hub_mom_btn"):
                    with st.spinner("Ranking the whole universe (~1 min)…"):
                        _ms, _mcnt, _masof2 = _tbmod.compute_universe_momentum(force=True)
                    st.success(f"{_ms}: {_mcnt} names ranked (as of {_masof2}).")
                    st.rerun()
            _mdf, _masof = _tbmod.load_momentum_ranks(_hub_conn)
            if _mdf is None or _mdf.empty:
                st.info("No snapshot yet — click **Recompute** to build the first one.")
            else:
                import datetime as _dtm
                _is_today = (_masof == _dtm.date.today().strftime("%Y-%m-%d"))
                with _mc2:
                    st.metric("Universe", f"{len(_mdf)} names")
                with _mc3:
                    st.metric("As of", _masof, delta=("today" if _is_today else "stale"),
                              delta_color=("normal" if _is_today else "inverse"))
                try:
                    _phl = [t.upper() for t in pd.read_sql(
                        "SELECT DISTINCT UPPER(ticker) tk FROM trades WHERE status='OPEN'",
                        _hub_conn)["tk"].tolist()]
                except Exception:
                    _phl = []

                _disp = _mdf[["mom_rank", "ticker", "ret_12_1", "ret_6_1", "ret_1m",
                              "above200", "decile"]].copy()
                _disp.columns = ["Rank", "Ticker", "12-1 %", "6-1 %", "1m %", ">200DMA", "Decile"]
                _disp[">200DMA"] = _disp[">200DMA"].map({1: "✅", 0: "❌"})
                _mom_cc = {
                    "12-1 %": st.column_config.NumberColumn(format="%+.0f%%"),
                    "6-1 %": st.column_config.NumberColumn(format="%+.0f%%"),
                    "1m %": st.column_config.NumberColumn(format="%+.0f%%"),
                }

                if _phl:
                    _mine = _disp[_disp["Ticker"].isin(_phl)]
                    if not _mine.empty:
                        st.markdown("##### ⭐ Your open positions, ranked")
                        st.dataframe(_mine, hide_index=True, use_container_width=True,
                                     column_config=_mom_cc)

                _lc, _rc = st.columns(2)
                with _lc:
                    st.markdown("##### 🟢 Top momentum — long bias")
                    st.dataframe(_disp.head(12), hide_index=True, use_container_width=True,
                                 column_config=_mom_cc)
                with _rc:
                    st.markdown("##### 🔴 Bottom momentum — short / avoid")
                    st.dataframe(_disp.tail(12).iloc[::-1], hide_index=True,
                                 use_container_width=True, column_config=_mom_cc)

                try:
                    import plotly.graph_objects as _go
                    _sm = _mdf.sort_values("ret_12_1")
                    _cols = ["#1b5e20" if d <= 3 else ("#b71c1c" if d >= 8 else "#90a4ae")
                             for d in _sm["decile"]]
                    _fig = _go.Figure(_go.Bar(
                        x=_sm["ret_12_1"], y=_sm["ticker"], orientation="h",
                        marker_color=_cols,
                        hovertemplate="%{y}: %{x:+.0f}%<extra></extra>"))
                    _fig.update_layout(
                        height=max(420, len(_sm) * 13),
                        title="12-1 momentum by ticker (green = top decile, red = bottom)",
                        xaxis_title="12-1 return %", margin=dict(l=10, r=10, t=40, b=10),
                        showlegend=False)
                    st.plotly_chart(_fig, use_container_width=True)
                except Exception as _e:
                    st.caption(f"(chart unavailable: {_e})")
                st.caption("Factor logic: buy past 12-month winners (skipping the most recent month "
                           "to avoid short-term reversal). Pairs with the Risk Regime tab — press "
                           "longs when RISK-ON, lighten when RISK-OFF.")

        with _tb_ev:
            st.caption("Macro/geopolitical event → 1st/2nd/3rd-order liquid trades + hedge.")
            _evk = st.selectbox("Event", sorted(_tbmod.MACRO_EVENT_MAP), key="hub_ev_sel")
            try:
                _render_tg(_tbmod._fmt_event_report(_tbmod.event_trade_map(_evk)))
            except Exception as e:
                st.error(f"Event error: {e}")

        with _tb_jr:
            st.caption("Your logged event trades + running hit-rate (verified vs live prices).")
            try:
                _render_tg(_tbmod._fmt_journal_review(_hub_conn))
            except Exception as e:
                st.error(f"Journal error: {e}")

        with _tb_mac:
            st.caption("FRED macro indicators + AlphaVantage news sentiment (set keys to enable).")
            try:
                _render_tg(_tbmod._fmt_macro_report())
            except Exception as e:
                st.error(f"Macro error: {e}")

        try:
            _hub_conn.close()
        except Exception:
            pass


# ===================================================================
# ──  PAGE: GLOBAL OPPORTUNITIES (regions / themes / country sectors)
# ===================================================================
if page == "🌍 Global Opportunities":
    _page_header("🌍 Global Opportunities")
    st.caption("Where capital is flowing — by region, theme, and country sector. **Flow signal** = price "
               "momentum (3m/6m) + relative strength vs SPY + proximity to 52-week high (a free proxy for "
               "fund flows). News is pulled live with sentiment. Curated proxy ETFs — educational, not advice.")
    with st.spinner("Loading global proxies (one batch download)…"):
        _gp = _global_prices()
    if not _gp:
        st.warning("Could not load global price data (network/yfinance). Try Refresh.")
    else:
        _gt1, _gt2, _gt3, _gt4 = st.tabs(
            ["🌎 Regions", "🚀 Themes & Sectors", "🏳️ Country Future Sectors", "📡 Research Feed"])

        def _flow_leaderboard(mapping, title):
            rows = []
            for lbl, (proxy, _desc, _q) in mapping.items():
                sig = _flow_signal(proxy, _gp)
                if sig:
                    rows.append({"Name": lbl, "Proxy": proxy, "Flow": sig["label"],
                                 "3m %": round(sig["r3"]), "6m %": round(sig["r6"]),
                                 "vs SPY": round(sig["rs"]), "% 52w hi": round(sig["near"]),
                                 "_score": sig["score"]})
            if rows:
                _df = pd.DataFrame(rows).sort_values(["_score", "3m %"], ascending=False).drop(columns="_score")
                st.markdown(f"**🏁 {title} — strongest inflows first**")
                st.dataframe(_df, hide_index=True, use_container_width=True,
                             column_config={c: st.column_config.NumberColumn(format="%d%%")
                                            for c in ["3m %", "6m %", "vs SPY", "% 52w hi"]})

        with _gt1:
            _flow_leaderboard(_GLOBAL_REGIONS, "Regions")
            st.markdown("---")
            _ordered = sorted(_GLOBAL_REGIONS.items(),
                              key=lambda kv: (_flow_signal(kv[1][0], _gp) or {}).get("score", -9), reverse=True)
            for _lbl, (_px, _desc, _q) in _ordered:
                _render_global_card(_lbl, _px, _desc, _q, _gp)

        with _gt2:
            _flow_leaderboard(_GLOBAL_THEMES, "Themes")
            st.markdown("---")
            _ordered = sorted(_GLOBAL_THEMES.items(),
                              key=lambda kv: (_flow_signal(kv[1][0], _gp) or {}).get("score", -9), reverse=True)
            for _lbl, (_px, _desc, _q) in _ordered:
                _render_global_card(_lbl, _px, _desc, _q, _gp)

        with _gt3:
            for _country, _arr in _COUNTRY_SECTORS.items():
                st.markdown(f"### {_country}")
                for _name, _tk in _arr:
                    _render_global_card(_name, _tk, f"{_country.split('—')[0].strip()} — {_name}",
                                        f"{_country.split('—')[0].strip()} {_name} investment opportunity", _gp)

        with _gt4:
            st.markdown("#### 📡 Research & idea feed")
            st.caption("Free macro/thematic feeds with sentiment. Add your own RSS/blog URLs below "
                       "(Substack, Morningstar, a hidden blog) — they persist for this session.")
            _DEFAULT_FEEDS = {
                "🌐 Global capital flows": "global capital flows where to invest 2026",
                "🌾 Farmland / real assets": "farmland real assets investment institutional",
                "🇮🇳 India opportunity": "India investment opportunity sectors 2026",
                "🏭 Reshoring / supply chain": "reshoring nearshoring supply chain investment",
                "⚡ AI power & energy": "AI data center power energy investment",
            }
            for _name, _q in _DEFAULT_FEEDS.items():
                with st.expander(_name, expanded=False):
                    for it in _theme_news(_q, n=5):
                        e = "🟢" if it["tone"] > 0 else ("🔴" if it["tone"] < 0 else "⚪")
                        st.markdown(f"- {e} [{it['title']}]({it['link']}) · _{it['when']}_")

            st.markdown("#### 🏛 Institutional research & white papers")
            st.caption("Big-4 / bank / institution reports surfaced via news (most don't offer clean RSS). "
                       "Each pulls the latest coverage of that publisher's reports & outlooks.")
            _WHITEPAPER_FEEDS = {
                "🟢 Deloitte Insights": "Deloitte Insights economic outlook industry report",
                "🔵 McKinsey Global Institute": "McKinsey Global Institute report outlook",
                "🟠 PwC / EY / KPMG": "PwC OR EY OR KPMG outlook report sector",
                "🏦 Goldman Sachs Research": "Goldman Sachs research outlook markets economy",
                "🏦 JPMorgan (Guide to Markets)": "JPMorgan Guide to the Markets outlook research",
                "⚫ BlackRock Investment Institute": "BlackRock Investment Institute outlook",
                "🌐 IMF / World Bank": "IMF World Economic Outlook OR World Bank Global Economic Prospects",
                "🏛 BIS / OECD": "BIS OR OECD economic outlook report",
            }
            for _name, _q in _WHITEPAPER_FEEDS.items():
                with st.expander(_name, expanded=False):
                    _items = _theme_news(_q, n=5)
                    if _items:
                        for it in _items:
                            e = "🟢" if it["tone"] > 0 else ("🔴" if it["tone"] < 0 else "⚪")
                            st.markdown(f"- {e} [{it['title']}]({it['link']}) · _{it['when']}_")
                    else:
                        st.caption("No recent coverage found right now.")
            st.markdown("---")
            _custom = st.text_input("Add an RSS feed URL (e.g. a Substack/Morningstar/blog RSS)", key="glob_rss")
            if _custom:
                try:
                    import feedparser, html as _h
                    _fp = feedparser.parse(_custom)
                    st.markdown(f"**{_h.unescape(getattr(_fp.feed, 'title', _custom))}**")
                    for _e in _fp.entries[:8]:
                        _ti = _h.unescape(_e.get("title", "")).strip()
                        _tn = _headline_tone(_ti)
                        _em = "🟢" if _tn > 0 else ("🔴" if _tn < 0 else "⚪")
                        st.markdown(f"- {_em} [{_ti}]({_e.get('link', '')})")
                except Exception as _e:
                    st.error(f"Couldn't read that feed: {_e}")
            st.caption("Tip: most newsletters/blogs have an RSS link (often /feed or /rss). "
                       "Paste it above to fold it into your sentiment feed.")
