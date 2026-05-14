"""
bot_optimized.py — Streamlined NYSE Options Bot
================================================
Clean, fast, single-copy architecture. Drop-in replacement for telegram_bot.py.

Key improvements vs telegram_bot.py:
  • No duplicate function copies — one definition per function
  • asyncio.gather + run_in_executor for all DB-heavy handlers
  • Edit-loading-message pattern (no delete+reply flicker)
  • Message splitting for long content (no "message too long" errors)
  • Pipe-box tables throughout (28-char mobile safe)
  • Centralised config and DB pool

Run:  python bot_optimized.py
"""

from __future__ import annotations
import asyncio
import functools
import logging
import os
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from io import BytesIO
from typing import Optional

import numpy as np
import pandas as pd
import yfinance as yf
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

# ─── Config ──────────────────────────────────────────────────────────────────
DATA_DIR   = r"C:\Users\srini\Options_chain_data"
NYSE_DIR   = os.path.join(DATA_DIR, "NYSE_DATA")
DB_PATH    = os.path.join(DATA_DIR, "US_data.db")
TOKEN_FILE = os.path.join(NYSE_DIR, "token.txt")
CHATID_FILE = os.path.join(NYSE_DIR, "us_bot_id.txt")

H = ParseMode.HTML
_EXECUTOR = ThreadPoolExecutor(max_workers=8)

log = logging.getLogger("bot_opt")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


# ─── Async helpers ────────────────────────────────────────────────────────────

async def _bg(fn, *args, **kwargs):
    """Run a sync function in the thread pool without blocking the event loop."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(_EXECUTOR, functools.partial(fn, *args, **kwargs))


async def _loading(query, text="⏳ Loading..."):
    """Send a loading placeholder and return the message object."""
    return await query.message.reply_text(text)


async def _send_result(msg, text: str, parse_mode=H, reply_markup=None, max_len=4000):
    """Edit the loading message with the final result. Splits if too long."""
    chunks = _split_text(text, max_len)
    try:
        await msg.edit_text(
            chunks[0], parse_mode=parse_mode,
            reply_markup=reply_markup if len(chunks) == 1 else None,
            disable_web_page_preview=True,
        )
    except Exception:
        await msg.reply_text(chunks[0], parse_mode=parse_mode, disable_web_page_preview=True)
    for chunk in chunks[1:]:
        await msg.reply_text(chunk, parse_mode=parse_mode,
                             reply_markup=reply_markup if chunk == chunks[-1] else None,
                             disable_web_page_preview=True)


def _split_text(text: str, limit=4000) -> list[str]:
    """Split text at newlines to fit within Telegram's 4096-char limit."""
    if len(text) <= limit:
        return [text]
    parts, cur = [], []
    cur_len = 0
    for line in text.split("\n"):
        if cur_len + len(line) + 1 > limit and cur:
            parts.append("\n".join(cur))
            cur, cur_len = [], 0
        cur.append(line)
        cur_len += len(line) + 1
    if cur:
        parts.append("\n".join(cur))
    return parts


# ─── DB ───────────────────────────────────────────────────────────────────────

def get_conn():
    return sqlite3.connect(DB_PATH)


def _safe_float(v, d=0.0):
    try:
        return float(v)
    except Exception:
        return d


def _latest_date(ticker: str) -> str:
    conn = get_conn()
    try:
        r = conn.execute(
            """SELECT trade_date_now FROM options_change WHERE ticker=?
               ORDER BY substr(trade_date_now,7,4)||substr(trade_date_now,1,2)||substr(trade_date_now,4,2) DESC
               LIMIT 1""",
            (ticker.upper(),),
        ).fetchone()
        return r[0] if r else ""
    finally:
        conn.close()


def _prev_date(ticker: str, ref_date: str) -> str:
    conn = get_conn()
    try:
        r = conn.execute(
            """SELECT DISTINCT trade_date_now FROM options_change WHERE ticker=?
               AND substr(trade_date_now,7,4)||substr(trade_date_now,1,2)||substr(trade_date_now,4,2)
                 < substr(?,7,4)||substr(?,1,2)||substr(?,4,2)
               ORDER BY substr(trade_date_now,7,4)||substr(trade_date_now,1,2)||substr(trade_date_now,4,2) DESC
               LIMIT 1""",
            (ticker.upper(), ref_date, ref_date, ref_date),
        ).fetchone()
        return r[0] if r else ""
    finally:
        conn.close()


def _spot_price(ticker: str) -> float:
    try:
        h = yf.Ticker(ticker).history(period="2d")
        return float(h["Close"].iloc[-1]) if not h.empty else 0.0
    except Exception:
        return 0.0


# ─── Table helpers (28-char mobile safe) ─────────────────────────────────────

def _pipe_table(headers: list[str], widths: list[int], rows: list[list]) -> str:
    """Build a pipe-box table. widths[i] = column width (chars)."""
    sep = "+" + "+".join("-" * w for w in widths) + "+"
    fmt = "|" + "|".join(f"{{:<{w}}}" if i == 0 else f"{{:>{w}}}" for i, w in enumerate(widths)) + "|"
    hdr = fmt.format(*[str(h)[:w] for h, w in zip(headers, widths)])
    lines = [sep, hdr, sep]
    for row in rows:
        lines.append(fmt.format(*[str(v)[:w] for v, w in zip(row, widths)]))
    lines.append(sep)
    return "\n".join(lines)


# ─── OI helpers ───────────────────────────────────────────────────────────────

def _oi_key_levels(ticker: str, conn) -> dict:
    try:
        df = pd.read_sql(
            """SELECT strike, SUM(openInt_Call_now) AS c_oi, SUM(openInt_Put_now) AS p_oi
               FROM options_change WHERE ticker=? GROUP BY strike""",
            conn, params=(ticker.upper(),)
        )
        if df.empty:
            return {}
        mean_oi = (df["c_oi"] + df["p_oi"]).mean()
        # Gamma walls
        walls = df[(df["c_oi"] + df["p_oi"]) >= mean_oi * 2]
        call_wall = float(walls.loc[walls["c_oi"].idxmax(), "strike"]) if not walls.empty else 0
        put_wall  = float(walls.loc[walls["p_oi"].idxmax(), "strike"]) if not walls.empty else 0
        # Max pain
        strikes = sorted(df["strike"].unique())
        mp_losses = {}
        for s in strikes:
            loss = float(((df["strike"] - s).clip(lower=0) * df["c_oi"]).sum()
                       + ((s - df["strike"]).clip(lower=0) * df["p_oi"]).sum())
            mp_losses[s] = loss
        max_pain = min(mp_losses, key=mp_losses.get) if mp_losses else 0
        cw_oi = float(df.loc[df["strike"] == call_wall, "c_oi"].sum()) if call_wall else 0
        pw_oi = float(df.loc[df["strike"] == put_wall, "p_oi"].sum()) if put_wall else 0
        return {"call_wall": call_wall, "put_wall": put_wall, "max_pain": max_pain,
                "call_wall_oi": cw_oi, "put_wall_oi": pw_oi}
    except Exception:
        return {}


def _oi_signal_verdict(ticker: str, today_date: str, prev_date: str) -> str:
    """BULLISH/BEARISH verdict comparing today vs prev EOD OI."""
    try:
        conn = get_conn()
        df_t = pd.read_sql(
            "SELECT strike, SUM(openInt_Call_now) AS c_oi, SUM(openInt_Put_now) AS p_oi "
            "FROM options_change WHERE ticker=? AND trade_date_now=? GROUP BY strike",
            conn, params=(ticker.upper(), today_date))
        df_p = pd.read_sql(
            "SELECT strike, SUM(openInt_Call_now) AS c_oi, SUM(openInt_Put_now) AS p_oi "
            "FROM options_change WHERE ticker=? AND trade_date_now=? GROUP BY strike",
            conn, params=(ticker.upper(), prev_date))
        sd = pd.read_sql(
            "SELECT close FROM stock_daily WHERE ticker=? "
            "ORDER BY substr(trade_date,7,4)||substr(trade_date,1,2)||substr(trade_date,4,2) DESC LIMIT 1",
            conn, params=(ticker.upper(),))
        kl = _oi_key_levels(ticker.upper(), conn)
        conn.close()
    except Exception:
        return ""
    if df_t.empty or df_p.empty:
        return ""

    c_now = float(df_t["c_oi"].sum()); p_now = float(df_t["p_oi"].sum())
    c_prv = float(df_p["c_oi"].sum()); p_prv = float(df_p["p_oi"].sum())
    call_chg = c_now - c_prv; put_chg = p_now - p_prv
    call_pct = call_chg / max(c_prv, 1) * 100
    put_pct  = put_chg  / max(p_prv, 1) * 100
    pcr_eod  = p_prv / max(c_prv, 1)
    pcr_now  = p_now / max(c_now, 1)
    spot     = float(sd["close"].iloc[0]) if not sd.empty else 0.0

    if put_chg > 0 and abs(put_chg) > abs(call_chg) * 1.2:
        sig, sig_em = "BEARISH", "\U0001f4c9"
    elif call_chg > 0 and call_chg > abs(put_chg) * 1.2:
        sig, sig_em = "BULLISH", "\U0001f4c8"
    elif call_chg > 0 and put_chg > 0:
        sig, sig_em = "STRADDLE", "⚡"
    elif call_chg < 0 and put_chg < 0:
        sig, sig_em = "UNWIND", "\U0001f504"
    elif pcr_now > 1.3:
        sig, sig_em = "BEARISH", "\U0001f4c9"
    elif pcr_now < 0.7:
        sig, sig_em = "BULLISH", "\U0001f4c8"
    else:
        sig, sig_em = "NEUTRAL", "⚪"

    reasons = []
    if put_chg > 0 and abs(put_chg) > abs(call_chg):
        reasons.append(f"• Put OI +{put_chg:,.0f} ({put_pct:+.1f}%) — downside bets building")
    if call_chg > 0 and call_chg > abs(put_chg):
        reasons.append(f"• Call OI +{call_chg:,.0f} ({call_pct:+.1f}%) — bullish positioning")
    if call_chg < 0:
        reasons.append(f"• Call OI {call_chg:+,.0f} ({call_pct:+.1f}%) — bulls reducing")
    if pcr_now > pcr_eod * 1.05:
        reasons.append(f"• PCR {pcr_eod:.2f}→{pcr_now:.2f} (bearish lean)")
    elif pcr_now < pcr_eod * 0.95:
        reasons.append(f"• PCR {pcr_eod:.2f}→{pcr_now:.2f} (bullish lean)")
    if not reasons:
        reasons.append(f"• Calls {call_chg:+,.0f}  Puts {put_chg:+,.0f}")

    strike_lines = []
    if kl:
        cw = kl.get("call_wall", 0); pw = kl.get("put_wall", 0); mp = kl.get("max_pain", 0)
        cw_oi = kl.get("call_wall_oi", 0); pw_oi = kl.get("put_wall_oi", 0)
        if cw and spot:
            strike_lines.append(f"  Call Wall ${cw:.0f} ({(cw-spot)/spot*100:+.1f}%) OI:{cw_oi/1000:.0f}K")
        if pw and spot:
            strike_lines.append(f"  Put Wall  ${pw:.0f} ({(pw-spot)/spot*100:+.1f}%) OI:{pw_oi/1000:.0f}K")
        if mp and spot:
            strike_lines.append(f"  Max Pain  ${mp:.0f} ({(mp-spot)/spot*100:+.1f}%)")

    simple_ans = ""
    if spot and kl:
        cw = kl.get("call_wall", 0); pw = kl.get("put_wall", 0); mp = kl.get("max_pain", 0)
        bull_tgt = cw if cw and cw > spot else spot * 1.03
        bear_tgt = pw if pw and pw < spot else spot * 0.97
        simple_ans = (
            "\n\U0001f3af <b>Simple Answer</b>\n"
            + ("⚠️" if sig in ("BEARISH", "STRADDLE") else "✅")
            + f" Bull → ${bull_tgt:.0f}"
            + (" (call wall)" if cw and cw > spot else " (+3% est)") + "\n"
            + ("❌" if sig in ("BEARISH", "STRADDLE") else "⚠️")
            + f" Bear → ${bear_tgt:.0f}"
            + (" (put wall)" if pw and pw < spot else " (-3% est)")
            + (f"\n\U0001f9f2 Max Pain ${mp:.0f}" if mp else "")
        )

    return (
        f"{sig_em} <b>{ticker} OI SIGNAL — {today_date}</b>\n"
        f"<b>Verdict: {sig}</b>\n\n"
        f"<b>Why {sig.title()}?</b>\n"
        + "\n".join(reasons)
        + ("\n\n<b>Key Levels</b>\n" + "\n".join(strike_lines) if strike_lines else "")
        + simple_ans
        + f"\n\n<i>Calls {call_chg:+,.0f}  Puts {put_chg:+,.0f}</i>"
    )


def _oi_volume_chart(ticker: str, conn, spot: float, latest_date: str) -> Optional[BytesIO]:
    """Option volume profile: calls +ve / puts -ve, OI as dashed outline."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        BG = "#0D1117"; PANEL = "#161B22"; TXT = "#E6EDF3"; GRID = "#30363D"

        df = pd.read_sql(
            """SELECT strike,
                      SUM(vol_Call_now)     AS c_vol,
                      SUM(vol_Put_now)      AS p_vol,
                      SUM(openInt_Call_now) AS c_oi,
                      SUM(openInt_Put_now)  AS p_oi
               FROM options_change
               WHERE ticker=? AND trade_date_now=?
                 AND strike BETWEEN ? AND ?
               GROUP BY strike ORDER BY strike""",
            conn, params=(ticker, latest_date, spot * 0.80, spot * 1.20),
        )
        if df.empty:
            return None
        for col in ["c_vol", "p_vol", "c_oi", "p_oi"]:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)
        df["total_vol"] = df["c_vol"] + df["p_vol"]
        df = df[df["total_vol"] > 0].nlargest(20, "total_vol").sort_values("strike")
        if df.empty:
            return None

        strikes = df["strike"].tolist()
        c_vol = df["c_vol"].tolist()
        p_vol = [-v for v in df["p_vol"].tolist()]
        c_oi  = df["c_oi"].tolist()
        p_oi  = [-v for v in df["p_oi"].tolist()]
        x = np.arange(len(strikes)); w = 0.38

        fig, ax = plt.subplots(figsize=(10, 5), facecolor=BG)
        ax.set_facecolor(PANEL)
        ax.bar(x - w/2, c_vol, width=w, color="#2D8B2D", alpha=0.85, label="Call Vol")
        ax.bar(x + w/2, p_vol, width=w, color="#8B0000", alpha=0.85, label="Put Vol")
        ax.bar(x - w/2, c_oi,  width=w, fill=False, edgecolor="#00CC66", linewidth=0.8, linestyle="--", label="Call OI")
        ax.bar(x + w/2, p_oi,  width=w, fill=False, edgecolor="#FF6666", linewidth=0.8, linestyle="--", label="Put OI")

        atm_idx = min(range(len(strikes)), key=lambda i: abs(strikes[i] - spot))
        ax.axvline(atm_idx, color="#FFD700", linewidth=1.5, linestyle="--", alpha=0.9, label="ATM")

        ax.set_xticks(x)
        ax.set_xticklabels([f"${s:.0f}" for s in strikes], fontsize=7, color=TXT, rotation=45, ha="right")
        ax.tick_params(colors=TXT, length=0)
        ax.axhline(0, color=TXT, linewidth=0.6)
        ax.legend(fontsize=7, facecolor=PANEL, edgecolor=GRID, labelcolor=TXT, loc="upper right")
        ax.set_title(f"{ticker}  Volume Profile  ·  ${spot:.0f}  ·  {latest_date}",
                     color=TXT, fontsize=10, fontweight="bold")
        ax.set_ylabel("Volume (calls +, puts −)", fontsize=8, color=TXT)
        for spine in ax.spines.values():
            spine.set_edgecolor(GRID)
        ax.yaxis.label.set_color(TXT)
        ax.tick_params(axis="y", colors=TXT)
        fig.text(0.5, 0.01,
                 "Solid=today volume  Dashed=open interest  Gold=ATM",
                 ha="center", fontsize=7, color="#8B949E")
        plt.tight_layout(rect=[0, 0.04, 1, 1])

        buf = BytesIO()
        plt.savefig(buf, format="png", dpi=105, bbox_inches="tight", facecolor=BG)
        plt.close(fig)
        buf.seek(0)
        return buf
    except Exception as e:
        log.warning(f"_oi_volume_chart: {e}")
        return None


# ─── Strike breakdown (one clean function) ───────────────────────────────────

def _oi_strike_breakdown_data(ticker: str, date: str) -> dict:
    """Fetch and compute strike breakdown tables. Returns dict of table strings."""
    conn = get_conn()
    try:
        df = pd.read_sql(
            """SELECT strike,
                      SUM(openInt_Call_now) AS c_oi_now,
                      SUM(openInt_Put_now)  AS p_oi_now,
                      SUM(openInt_Call_prev) AS c_oi_prv,
                      SUM(openInt_Put_prev)  AS p_oi_prv,
                      SUM(vol_Call_now)     AS c_vol,
                      SUM(vol_Put_now)      AS p_vol
               FROM options_change WHERE ticker=? AND trade_date_now=?
               GROUP BY strike ORDER BY strike""",
            conn, params=(ticker.upper(), date),
        )
        sd = pd.read_sql(
            "SELECT close FROM stock_daily WHERE ticker=? "
            "ORDER BY substr(trade_date,7,4)||substr(trade_date,1,2)||substr(trade_date,4,2) DESC LIMIT 1",
            conn, params=(ticker.upper(),),
        )
        kl = _oi_key_levels(ticker.upper(), conn)
    finally:
        conn.close()

    spot = float(sd["close"].iloc[0]) if not sd.empty else 0.0
    if df.empty:
        return {}

    for col in ["c_oi_now", "p_oi_now", "c_oi_prv", "p_oi_prv", "c_vol", "p_vol"]:
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)

    df["c_chg"] = df["c_oi_now"] - df["c_oi_prv"]
    df["p_chg"] = df["p_oi_now"] - df["p_oi_prv"]

    # Near-ATM strikes only (±10% of spot)
    if spot:
        df = df[df["strike"].between(spot * 0.90, spot * 1.10)]
    df = df.nlargest(12, "c_oi_now")

    def _sig(cc, pc):
        if cc > 0 and pc > 0:
            return "HDGE"
        if cc > abs(pc) * 1.2:
            return "BUY"
        if pc > abs(cc) * 1.2:
            return "SEL"
        if cc < 0 and pc < 0:
            return "UNW"
        return "NEU"

    # Table A: OI change
    rows_a = []
    for _, r in df.iterrows():
        cc = int(r["c_chg"]); pc = int(r["p_chg"])
        rows_a.append([
            f"${r['strike']:.0f}",
            f"{cc/1000:+.0f}K" if abs(cc) >= 1000 else f"{cc:+d}",
            f"{pc/1000:+.0f}K" if abs(pc) >= 1000 else f"{pc:+d}",
            _sig(cc, pc),
        ])
    tbl_a = _pipe_table(["Stk", "C-Chg", "P-Chg", "Sig"], [5, 6, 6, 4], rows_a)

    # Table B: Volume
    rows_b = []
    for _, r in df.iterrows():
        cv = int(r["c_vol"]); pv = int(r["p_vol"])
        zone = "ATM" if spot and abs(r["strike"] - spot) / spot < 0.02 else (
               "ITM" if r["strike"] < spot else "OTM")
        rows_b.append([
            f"${r['strike']:.0f}",
            zone,
            f"{cv/1000:.0f}K" if cv >= 1000 else str(cv),
            f"{pv/1000:.0f}K" if pv >= 1000 else str(pv),
        ])
    tbl_b = _pipe_table(["Stk", "Zone", "C-Vol", "P-Vol"], [5, 4, 5, 5], rows_b)

    result = {"table_a": tbl_a, "table_b": tbl_b, "spot": spot, "key_levels": kl}
    return result


# ─── Main OI view handler ─────────────────────────────────────────────────────

async def oi_view(query, ticker: str):
    """Full OI analysis: heatmap + verdict + volume chart + tables."""
    ticker = ticker.upper()
    msg = await _loading(query)

    # Parallel fetch
    today, spot = await asyncio.gather(
        _bg(_latest_date, ticker),
        _bg(_spot_price, ticker),
    )
    prev = await _bg(_prev_date, ticker, today)

    # Breakdown data (sync, in bg)
    breakdown = await _bg(_oi_strike_breakdown_data, ticker, today)
    if not breakdown:
        await _send_result(msg, f"❌ No OI data for {ticker}")
        return

    spot = breakdown.get("spot") or spot
    kl   = breakdown.get("key_levels", {})

    # Build text card
    lines = [f"<b>📊 {ticker} OI Analysis — {today}</b>"]
    if spot:
        lines.append(f"Spot: <b>${spot:.2f}</b>")
    if kl:
        cw = kl.get("call_wall", 0); pw = kl.get("put_wall", 0); mp = kl.get("max_pain", 0)
        if cw:
            lines.append(f"Call Wall: <b>${cw:.0f}</b> ({(cw-spot)/spot*100:+.1f}%)")
        if pw:
            lines.append(f"Put Wall:  <b>${pw:.0f}</b> ({(pw-spot)/spot*100:+.1f}%)")
        if mp:
            lines.append(f"Max Pain:  <b>${mp:.0f}</b>")

    lines.append("\n<b>OI Change (near ATM)</b>")
    lines.append(f"<pre>{breakdown['table_a']}</pre>")
    lines.append("\n<b>Volume by Strike</b>")
    lines.append(f"<pre>{breakdown['table_b']}</pre>")

    await _send_result(msg, "\n".join(lines))

    # Verdict
    verdict = await _bg(_oi_signal_verdict, ticker, today, prev)
    if verdict:
        for chunk in _split_text(verdict):
            await query.message.reply_text(chunk, parse_mode=H, disable_web_page_preview=True)

    # Volume chart
    conn = get_conn()
    vol_buf = await _bg(_oi_volume_chart, ticker, conn, spot, today)
    conn.close()
    if vol_buf:
        await query.message.reply_photo(vol_buf, caption=f"{ticker} Volume Profile · {today}")


# ─── Mini chart handler (with message-length guard) ──────────────────────────

async def mini_chart(query, ticker: str, days: int = 7):
    ticker = ticker.upper()
    msg = await _loading(query, f"⏳ {ticker} {days}d chart...")

    def _fetch():
        try:
            h = yf.Ticker(ticker).history(period=f"{days+5}d").tail(days)
            if h.empty:
                return None
            return h
        except Exception:
            return None

    h = await _bg(_fetch)
    if h is None:
        await _send_result(msg, f"❌ No price data for {ticker}")
        return

    # Build text sparkline (28-char safe)
    closes = h["Close"].tolist()
    mn, mx = min(closes), max(closes)
    bars = "▁▂▃▄▅▆▇█"
    spark = "".join(bars[min(int((c - mn) / max(mx - mn, 0.01) * 7), 7)] for c in closes)
    chg = (closes[-1] - closes[0]) / closes[0] * 100
    em = "🟢" if chg >= 0 else "🔴"
    text = (
        f"<b>{ticker} {days}d</b>\n"
        f"<pre>{spark}</pre>\n"
        f"{em} {chg:+.1f}%  ${closes[-1]:.2f}\n"
        f"Hi:${mx:.2f}  Lo:${mn:.2f}"
    )
    await _send_result(msg, text)


# ─── Market Overview ──────────────────────────────────────────────────────────

def _market_overview_data() -> str:
    conn = get_conn()
    try:
        df = pd.read_sql(
            """SELECT trade_date, bull_score, bear_score,
                      call_notional_oi, put_notional_oi, avg_spot
               FROM us_analytics_daily
               ORDER BY substr(trade_date,7,4)||substr(trade_date,1,2)||substr(trade_date,4,2) DESC
               LIMIT 5""",
            conn,
        )
    finally:
        conn.close()

    if df.empty:
        return "❌ No market data available."

    rows = []
    for _, r in df.iterrows():
        date = str(r["trade_date"])[:5]  # MM-DD
        bull = _safe_float(r["bull_score"])
        bear = _safe_float(r["bear_score"])
        bias = "BUL" if bull > bear * 1.1 else ("BER" if bear > bull * 1.1 else "NEU")
        rows.append([date, f"{bull:.0f}", f"{bear:.0f}", bias])

    tbl = _pipe_table(["Date", "Bull", "Bear", "Bias"], [5, 4, 4, 3], rows)
    return f"<b>📊 Market Overview (5d)</b>\n<pre>{tbl}</pre>"


async def market_overview(query):
    msg = await _loading(query)
    text = await _bg(_market_overview_data)
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔄 Refresh", callback_data="menu_market")]])
    await _send_result(msg, text, reply_markup=kb)


# ─── Button / command routing ─────────────────────────────────────────────────

async def button_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data or ""

    if data.startswith("oi_"):
        ticker = data[3:]
        await oi_view(query, ticker)
    elif data.startswith("chart_"):
        await mini_chart(query, data[6:], 7)
    elif data == "menu_market":
        await market_overview(query)
    else:
        await query.message.reply_text(f"Unknown action: {data}")


async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 Market Overview", callback_data="menu_market")],
        [InlineKeyboardButton("SPY OI", callback_data="oi_SPY"),
         InlineKeyboardButton("QQQ OI", callback_data="oi_QQQ")],
        [InlineKeyboardButton("SPY Chart", callback_data="chart_SPY"),
         InlineKeyboardButton("AAPL Chart", callback_data="chart_AAPL")],
    ])
    await update.message.reply_text(
        "<b>NYSE Options Bot</b>\nSelect an action:", parse_mode=H, reply_markup=kb
    )


async def cmd_oi(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    args = ctx.args
    if not args:
        await update.message.reply_text("Usage: /oi TICKER")
        return
    # Create a mock query object
    class MockQuery:
        message = update.message
    await oi_view(MockQuery(), args[0])


# ─── Entry point ─────────────────────────────────────────────────────────────

def main():
    token = open(TOKEN_FILE).read().strip()
    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("oi", cmd_oi))
    app.add_handler(CallbackQueryHandler(button_handler))
    log.info("bot_optimized starting...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
