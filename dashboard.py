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
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap');

/* ─── Global ─── */
html, body, [data-testid="stAppViewContainer"] {
    font-family: 'Inter', sans-serif;
    color: #1a2332;
}
[data-testid="stAppViewContainer"] {
    background: linear-gradient(180deg, #f0f4f8, #e8edf3);
}
section[data-testid="stSidebar"] {
    background: linear-gradient(180deg, #e65100, #bf360c);
    border-right: 2px solid #ff6d00;
}
section[data-testid="stSidebar"] * {
    color: #ffffff !important;
}
section[data-testid="stSidebar"] .stRadio label {
    font-size: 0.92rem;
    padding: 6px 4px;
    border-radius: 6px;
    transition: background 0.15s;
    color: #ffffff;
}
section[data-testid="stSidebar"] .stRadio label:hover {
    background: rgba(255,255,255,0.2);
}
/* ─── Metric cards ─── */
[data-testid="stMetric"] {
    background: linear-gradient(135deg, #ffffff, #f6f9fc);
    border: 1px solid #d0dbe8;
    border-radius: 10px;
    padding: 14px 16px;
    box-shadow: 0 2px 8px rgba(0,0,0,0.06);
}
[data-testid="stMetricValue"] { font-size: 1.3rem !important; font-weight: 700; color: #1a2332 !important; }
[data-testid="stMetricLabel"] { font-size: 0.78rem !important; color: #5a7290; text-transform: uppercase; letter-spacing: 0.5px; }
[data-testid="stMetricDelta"] { font-size: 0.82rem !important; }
/* ─── DataFrames ─── */
.stDataFrame { border-radius: 8px; overflow: hidden; }
/* ─── Tabs ─── */
button[data-baseweb="tab"] { font-size: 0.85rem !important; font-weight: 500; color: #5a7290 !important; }
button[data-baseweb="tab"][aria-selected="true"] { color: #0066cc !important; border-bottom-color: #0066cc !important; }
/* ─── Section headers ─── */
.section-header {
    font-size: 1.1rem; font-weight: 700; color: #0066cc;
    border-bottom: 2px solid #0066cc; padding-bottom: 6px; margin: 20px 0 12px 0;
    letter-spacing: 0.3px;
}
.card {
    background: linear-gradient(135deg, #ffffff, #f8fafe); border: 1px solid #d0dbe8; border-radius: 10px;
    padding: 16px; margin-bottom: 12px;
}
.news-card {
    background: linear-gradient(135deg, #ffffff, #f6f9fc); border-left: 3px solid #2196f3;
    padding: 10px 14px; margin: 4px 0; border-radius: 0 8px 8px 0;
}
.news-card.bull { border-left-color: #00c853; background: linear-gradient(135deg, #f0fff4, #f6f9fc); }
.news-card.bear { border-left-color: #e53935; background: linear-gradient(135deg, #fff5f5, #f6f9fc); }
.alert-bar {
    background: linear-gradient(90deg, #fff3e0, #ffffff); border: 1px solid #ff9100;
    border-radius: 8px; padding: 10px 16px; margin: 8px 0;
    animation: pulse 2s infinite;
}
.trade-idea {
    background: linear-gradient(135deg, #e8f5e9, #f1f8e9); border: 1px solid #81c784;
    border-radius: 8px; padding: 10px 14px; margin: 4px 0;
}
.trade-idea.bearish { background: linear-gradient(135deg, #fce4ec, #fff3e0); border-color: #ef5350; }
@keyframes pulse { 0%,100%{opacity:1} 50%{opacity:0.88} }
/* ─── Signal badges ─── */
.badge-bull { background: #00c853; color: #fff; padding: 3px 12px; border-radius: 12px; font-weight: 700; font-size: 0.78rem; }
.badge-bear { background: #e53935; color: #fff; padding: 3px 12px; border-radius: 12px; font-weight: 700; font-size: 0.78rem; }
.badge-neutral { background: #78909c; color: #fff; padding: 3px 12px; border-radius: 12px; font-weight: 600; font-size: 0.78rem; }
.badge-warn { background: #ff9100; color: #fff; padding: 3px 12px; border-radius: 12px; font-weight: 700; font-size: 0.78rem; }
.badge-volatile { background: #ab47bc; color: #fff; padding: 3px 12px; border-radius: 12px; font-weight: 700; font-size: 0.78rem; }
/* ─── Prop screen ─── */
.prop-card {
    background: linear-gradient(135deg, #ffffff, #f0f5fa);
    border: 1px solid #c8d8e8; border-radius: 12px;
    padding: 18px; margin: 8px 0;
}
.prop-card h4 { color: #0066cc; margin: 0 0 8px 0; }
/* ─── Analysis cards ─── */
.analysis-finding {
    background: linear-gradient(135deg, #ffffff, #f6f9fc); border-left: 3px solid #0066cc;
    padding: 10px 14px; margin: 6px 0; border-radius: 0 8px 8px 0;
}
.analysis-finding.bearish { border-left-color: #e53935; }
.analysis-finding.bullish { border-left-color: #00c853; }
.analysis-finding.volatile { border-left-color: #ab47bc; }
/* ─── Hide anchor links ─── */
h1 a, h2 a, h3 a, h4 a { display: none !important; }
/* ─── Selectbox/input ─── */
[data-testid="stSelectbox"] label, [data-testid="stMultiSelect"] label {
    color: #5a7290 !important; font-size: 0.85rem !important;
}
</style>
""", unsafe_allow_html=True)

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
    "⚡ Trade Risk Calculator":  "Pre-trade risk sizing tool. Enter entry price, stop loss, and account size to compute the correct number of contracts so you risk no more than 2% of capital.",
    "🎯 Next-Day Exit Planner":  "Pre-market daily brief. Fetches live option mid-prices and tells you which positions to take profit, cut loss, or hold. Run every morning before market open.",
    "🚀 Live Momentum Scanner":  "Intraday momentum scanner. Screens for tickers with unusual volume, OI spikes, or RSI extremes in real time — find the next big mover.",
}

with st.sidebar:
    st.markdown("## 📊 RUDRARJUN")
    st.markdown("##### *Options Intelligence Terminal*")
    st.markdown("---")

    page = st.radio("Navigate", [
        "🌍 Market Overview",
        "🔬 OI Comparison Charts",
        "🔥 OI Analytics & Prediction",
        "🎯 Prop Trading Screen",
        "💼 Portfolio & Suggestions",
        "📊 Backtest Lab",
        "🔮 Live Position Predictor",
        "📈 Insider / Congress / Whales",
        "📰 News & Calendar",
        "⚡ Trade Risk Calculator",
        "🎯 Next-Day Exit Planner",
        "🚀 Live Momentum Scanner",
    ], label_visibility="collapsed")

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
                        f"<div style='background:#f8f9fa;border-radius:10px;padding:12px 10px;"
                        f"border-left:4px solid {_border_color};margin-bottom:6px;'>"
                        f"{_em_s} {_icon} <b>{_n}</b><br>"
                        f"{_px_s} "
                        f"{_pct:+.2f}%"
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
                        f"<div style='text-align:center;padding:6px;background:#f8f9fa;"
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
                    f"<div style='padding:12px;background:#f8f9fa;border-radius:8px;"
                    f"border-left:4px solid {_fg_color};margin-top:8px'>"
                    f"<code>{_bar_s}</code><br>"
                    f"<small>VIX: {_vix_fg:.1f} | SPY 1d: {_spy_pct:+.2f}% | "
                    f"Score: {_fg_score}/100</small></div>",
                    unsafe_allow_html=True)
        except Exception as _fge:
            st.info(f"Fear & Greed unavailable: {_fge}")

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
                    _ct_name = _inst_sym
                    if not _ct_data.empty:
                        _ct_close = float(_ct_data["Close"].iloc[-1])
                        _ct_prev  = float(_ct_data["Close"].iloc[-2]) if len(_ct_data) > 1 else _ct_close
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
                st.caption(f"📦 {len(_stored)} data points stored in DB · Earliest: {_stored['timestamp'].iloc[0].strftime('%Y-%m-%d %H:%M')} · Latest: {_stored['timestamp'].iloc[-1].strftime('%Y-%m-%d %H:%M')}")
            else:
                st.info("📦 No stored history yet — data will accumulate as the dashboard refreshes.")

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
                            f"<div style='background:linear-gradient(135deg,#ffffff,#f0f4f8);border-radius:12px;"
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
                    if v > 70: return "background-color: #ffcccc"
                    if v < 30: return "background-color: #ccffcc"
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
                    if val == "SPIKE": return "background-color: #ffcccc; font-weight: bold"
                    if val == "HIGH": return "background-color: #fff3cd"
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

    col_a, col_b = st.columns([1, 2])
    with col_a:
        sel_date = st.selectbox("Trade Date", dates, index=0)
    with col_b:
        day_df = load_oi_for_date(sel_date)
        tickers_avail = sorted(day_df["ticker"].unique()) if not day_df.empty else []
        sel_ticker = st.selectbox("Ticker", tickers_avail, index=0 if tickers_avail else None)

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
    st.markdown("<div>📅 Expiry Breakdown</div>", unsafe_allow_html=True)
    exp_agg = tk_df.groupby("expiry_date").agg(
        Call_OI_Chg=("change_OI_Call", "sum"),
        Put_OI_Chg=("change_OI_Put", "sum"),
        Call_OI=("openInt_Call_now", "sum"),
        Put_OI=("openInt_Put_now", "sum"),
    ).reset_index()
    exp_agg["PCR"] = np.where(exp_agg["Call_OI"] > 0, exp_agg["Put_OI"] / exp_agg["Call_OI"], 0).round(2)
    st.dataframe(exp_agg, hide_index=True)

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
        wc1, wc2 = st.columns(2)
        with wc1:
            date_from = st.selectbox("From Date", dates[1:], index=0, key="wk_from")
        with wc2:
            date_to = st.selectbox("To Date", dates, index=0, key="wk_to")

        if date_from != date_to:
            weekly_df = oi_weekly_strike_analysis(sel_ticker, date_from, date_to)
            if not weekly_df.empty:
                disp = weekly_df[["strike", "expiry_date", "c_oi_chg", "p_oi_chg",
                                  "c_oi_pct", "p_oi_pct", "c_px_chg", "p_px_chg",
                                  "classification", "escape_score", "escape_label"]].head(25).copy()
                disp.columns = ["Strike", "Expiry", "Call OI Δ", "Put OI Δ",
                                "Call OI %Δ", "Put OI %Δ", "Call Px Δ", "Put Px Δ",
                                "Classification", "Escape Score", "Escape"]
                st.dataframe(disp, hide_index=True)

                # Classification summary
                class_counts = weekly_df["classification"].value_counts()
                fig = px.pie(values=class_counts.values, names=class_counts.index,
                             title="Activity Breakdown", hole=0.4,
                             color_discrete_sequence=px.colors.qualitative.Set2)
                fig.update_layout(template="plotly_white", height=350)
                st.plotly_chart(fig)
            else:
                st.info("No matching strikes between these dates.")


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

    c1, c2 = st.columns([1, 1])
    with c1:
        prop_date = st.selectbox("Scan Date", dates, index=0)
    with c2:
        min_z = st.slider("Min Z-Score", 1.0, 4.0, 1.5, 0.25)

    with st.spinner("Scanning for opportunities..."):
        opps = scan_prop_opportunities(prop_date, min_z)

    if opps.empty:
        st.info("No opportunities meeting criteria.")
        st.stop()

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
            if v>0: return "background-color:#c8f7c5"
            if v<0: return "background-color:#f7c5c5"
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
    st.markdown("*Test how well OI signals predicted actual stock moves*")

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
        f"<div style='background:linear-gradient(135deg,#ffffff,#f0f4f8);border:2px solid {regime_color};"
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
                    f"<div style='background:#fff;border-radius:10px;padding:14px 20px;"
                    f"border-left:4px solid #0066cc;margin:8px 0;'>"
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

    tab1, tab2, tab3 = st.tabs(["👤 Insider Trades", "🏛️ Congress Trades", "🐋 Whale Holdings"])

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

            # Table — use actual columns present in DB
            _want = ["filer_name", "ticker", "shares_held", "value_usd",
                     "shares_change", "value_change_usd",
                     "filing_date", "quarter_end_date",
                     "action_type", "action_confidence"]
            display_cols = [c for c in _want if c in whales.columns]
            # Format value columns for readability
            _disp = whales[display_cols].copy().head(50)
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
        # Hardcoded major events
        events = [
            {"Event": "FOMC Meeting", "Date": "2026-03-18", "Impact": "HIGH",
             "Description": "Federal Reserve interest rate decision"},
            {"Event": "CPI Report", "Date": "2026-03-12", "Impact": "HIGH",
             "Description": "Consumer Price Index — inflation gauge"},
            {"Event": "Jobs Report (NFP)", "Date": "2026-04-03", "Impact": "HIGH",
             "Description": "Non-Farm Payrolls"},
            {"Event": "PPI Report", "Date": "2026-03-13", "Impact": "MEDIUM",
             "Description": "Producer Price Index"},
            {"Event": "Retail Sales", "Date": "2026-03-17", "Impact": "MEDIUM",
             "Description": "Consumer spending indicator"},
            {"Event": "GDP (Q4 Final)", "Date": "2026-03-27", "Impact": "HIGH",
             "Description": "Q4 2025 GDP final estimate"},
            {"Event": "PCE (Core)", "Date": "2026-03-28", "Impact": "HIGH",
             "Description": "Fed's preferred inflation gauge"},
        ]
        ev_df = pd.DataFrame(events)
        ev_df["Days Until"] = (pd.to_datetime(ev_df["Date"]) - datetime.now()).dt.days

        for _, ev in ev_df.iterrows():
            impact_badge = "🔴" if ev["Impact"] == "HIGH" else "🟡" if ev["Impact"] == "MEDIUM" else "🟢"
            days = ev["Days Until"]
            urgency = "⚡" if days <= 3 else "📌" if days <= 7 else ""
            st.markdown(f"{impact_badge} **{ev['Event']}** — {ev['Date']} ({days}d) {urgency}")
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
    st.markdown("*Drag the sliders to explore option value at any stock price & date — see P&L chart live*")

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
        f"<div style='background:linear-gradient(135deg,#ffffff,#f0f4f8);border-radius:12px;"
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
        ["📋 Individual Position", "🏢 All positions — by Ticker", "🌐 All Open Positions"],
        horizontal=True, key="ep_analysis_mode",
    )

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
    st.markdown("<div>📋 Your Position</div>", unsafe_allow_html=True)
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
    <div style='background:linear-gradient(135deg,#f8fafb,#eef2f7);border-left:5px solid {_dir_color};
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
    <div style='background:linear-gradient(135deg,#e8f5e9,#c8e6c9);border-left:6px solid {_mc_exp_color};
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
    <div style='background:linear-gradient(135deg,#ffffff,#f0f4f8);border-left:6px solid {_rec_border};
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
            paper_bgcolor="#ffffff",
            plot_bgcolor="#f8fafb",
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
                                                   plot_bgcolor="rgba(20,20,20,0.8)")
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
                                                   plot_bgcolor="rgba(20,20,20,0.8)")
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
            paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(20,20,20,0.8)",
            font=dict(color="white"),
        )
        st.plotly_chart(fig_scatter, use_container_width=True)

    st.caption("⚠️ All signals are algorithmic based on price momentum and volume. Always verify with OI data, news, and your own analysis before trading. Past momentum does not guarantee future returns.")
