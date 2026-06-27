async def group_stock_detail(query, ticker):
    """Show all open option positions for a stock with per-leg advice (close vs keep)."""
    conn = get_conn()
    try:
        trades_df = pd.read_sql("SELECT * FROM trades WHERE status='OPEN' AND ticker = ?", conn, params=(ticker,))
    except Exception:
        trades_df = pd.DataFrame()
    finally:
        conn.close()

    if trades_df.empty:
        await query.message.reply_text(
            f"❌ No open positions for {ticker}.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Groups", callback_data="menu_groups")]])
        )
        return

    try:
        _h = yf.Ticker(ticker).history(period="2d")
        spot = float(_h["Close"].iloc[-1]) if len(_h) >= 1 else 0.0
    except Exception:
        spot = 0.0

    total_pnl = 0.0
    parts = [hdr(f"📦 {ticker} — Group Positions")]
    if spot:
        parts.append(f"<b>Spot:</b> ${spot:.2f}\n")

    # IV Rank + Earnings warning
    try:
        _tkr_iv = yf.Ticker(ticker)
        _h_iv = _tkr_iv.history(period="1y")
        if len(_h_iv) >= 60:
            _lr_iv = np.log(_h_iv["Close"] / _h_iv["Close"].shift(1)).dropna()
            _hv_iv = _lr_iv.rolling(20).std() * np.sqrt(252)
            _hvc_iv = float(_hv_iv.iloc[-1]) if not pd.isna(_hv_iv.iloc[-1]) else None
            if _hvc_iv:
                _hvc2 = _hv_iv.dropna()
                _ivr = (_hvc_iv - float(_hvc2.min())) / (float(_hvc2.max()) - float(_hvc2.min()) + 1e-9) * 100
                _ivl = "HIGH" if _ivr > 75 else ("LOW" if _ivr < 25 else "MID")
                _iva = "sell premium" if _ivr > 75 else ("buy premium" if _ivr < 25 else "standard approach")
                parts.append(f"<b>IV Rank:</b> {_ivr:.0f}% {_ivl} -> favour {_iva}\n")

    except Exception:
        pass
    try:
        _cal_iv = yf.Ticker(ticker).calendar
        _earn_d = None
        if isinstance(_cal_iv, dict):
            _ed_iv = _cal_iv.get("Earnings Date")
            if _ed_iv:
                _ed_iv = pd.to_datetime(_ed_iv[0] if isinstance(_ed_iv, list) else _ed_iv)
                _earn_d = (_ed_iv.date() - datetime.now().date()).days
        elif hasattr(_cal_iv, "loc") and "Earnings Date" in _cal_iv.index:
            _ed_iv = pd.to_datetime(_cal_iv.loc["Earnings Date"].iloc[0])
            _earn_d = (_ed_iv.date() - datetime.now().date()).days
        if _earn_d is not None and 0 <= _earn_d < 30:
            parts.append(f"<b>EARNINGS IN {_earn_d} DAYS</b> - IV crush risk.\n")

    except Exception:
        pass


    leg_advice = []

    for _, trade in trades_df.iterrows():
        tid = int(trade["trade_id"])
        ot = str(trade["option_type"]).upper()
        st = float(trade["strike"])
        qty = int(trade.get("quantity", 1) or 1)
        exp = str(trade.get("expiry", ""))
        entry = float(trade.get("entry_price", 0) or 0)
        cur = float(trade["current_price"]) if "current_price" in trade and trade["current_price"] is not None else 0.0
        pnl = float(trade["unrealized_pnl"]) if "unrealized_pnl" in trade and trade["unrealized_pnl"] is not None else 0.0
        total_pnl += pnl

        try:
            exp_dt = datetime.strptime(exp, "%Y-%m-%d").date()
            dte = max((exp_dt - datetime.now().date()).days, 0)
        except Exception:
            dte = 999

        side_s = "SHORT" if qty < 0 else "LONG"
        pnl_pct = (pnl / (abs(entry) * 100 * abs(qty))) * 100 if entry and qty else 0
        em = "🟢" if pnl >= 0 else "🔴"

        if dte <= 5:
            advice = "⚠️ CLOSE — Expiry in ≤5 days, time decay accelerating"
            priority = 0
        elif pnl_pct >= 80:
            advice = f"💰 CLOSE — Exceptional profit (+{pnl_pct:.0f}%), take the win"
            priority = 0
        elif pnl_pct >= 50 and pnl >= 0:
            advice = f"💰 CLOSE HALF — Strong profit (+{pnl_pct:.0f}%), lock in gains"
            priority = 1
        elif pnl < 0 and pnl_pct < -50:
            advice = "✂️ CLOSE — Loss exceeds 50%, limit further damage"
            priority = 0
        elif pnl < 0 and pnl_pct < -30:
            advice = f"⚠️ REVIEW — Loss at {pnl_pct:.0f}%, consider stop or roll"
            priority = 2
        elif pnl >= 0 and dte > 10:
            advice = f"✅ HOLD — Profit growing, {dte} days left to run"
            priority = 3
        else:
            advice = "👁 MONITOR — No urgent action, check OI flow"
            priority = 3

        leg_advice.append((priority, pnl, advice, tid, ot, st, entry, exp, qty, cur, pnl_pct, dte, side_s, em))

    leg_advice.sort(key=lambda x: x[0])

    r_rate = 0.05
    for leg_idx, (priority, pnl, advice, tid, ot, st, entry, exp, qty, cur, pnl_pct, dte, side_s, em) in enumerate(leg_advice):
        pnl_s = f"${pnl:+,.0f} ({pnl_pct:+.0f}%)"
        side_label = "BUY" if side_s == "LONG" else "SELL"
        abs_qty = abs(qty)

        # Greeks via B-S
        greek_line = ""
        try:
            T = max(dte / 365, 1/365)
            _chain = yf.Ticker(ticker).option_chain(exp)
            _df_c = _chain.puts if ot == "PUT" else _chain.calls
            _row = _df_c.iloc[(_df_c["strike"] - st).abs().argsort()[:1]]
            iv = float(_row["impliedVolatility"].iloc[0]) if not _row.empty else 0.25
            g = bs_greeks(spot, st, T, r_rate, iv, opt=ot.lower())
            delta_sign = "+" if g["delta"] >= 0 else ""
            theta_day = g["theta"] * abs_qty * 100
            greek_line = f"   Δ:{delta_sign}{g['delta']:.3f}  Θ:${theta_day:.2f}/d  γ:{g['gamma']:.4f}  IV:{iv*100:.0f}%"
        except Exception:
            pass

        # Price context explanation
        price_move = cur - entry
        price_move_pct = (price_move / entry * 100) if entry > 0 else 0
        price_dir = "↑" if price_move >= 0 else "↓"
        price_context = (
            f"Option premium: paid ${entry:.2f}, now ${cur:.2f} "
            f"({price_dir}{abs(price_move_pct):.0f}% move in option value)"
        )

        btn_row = [
            InlineKeyboardButton(f"↳ Close L{leg_idx+1}: {side_label} {ot} ${st:.0f}",
                                 callback_data=f"exitmc|{ticker}|{ot.lower()}|{st}|{entry}|{exp}|{qty}")
        ]

        parts.append(
            f"{em} <b>L{leg_idx+1}: {side_label} {ot} ${st:.0f}  exp {exp}  ×{abs_qty}</b>  DTE:{dte}\n"
            f"   {em} P&L <b>{pnl_s}</b>\n"
            f"   📌 {price_context}\n"
            f"   💡 {advice}"
            + (f"\n{greek_line}" if greek_line else "")
        )

    net_em = "🟢" if total_pnl >= 0 else "🔴"
    net_qty = trades_df["quantity"].apply(lambda x: int(x or 1)).sum()
    parts.append(f"\n{net_em} <b>Group P&L: ${total_pnl:+,.0f}</b>  ({len(leg_advice)} legs, net qty: {net_qty:+d})")

    # Per-leg close buttons
    close_btns = []
    for leg_idx, (priority, pnl, advice, tid, ot, st, entry, exp, qty, cur, pnl_pct, dte, side_s, em) in enumerate(leg_advice):
        side_label = "BUY" if side_s == "LONG" else "SELL"
        close_btns.append([InlineKeyboardButton(
            f"↳ Close/Edit L{leg_idx+1}: {side_label} {ot} ${st:.0f}",
            callback_data=f"exitmc|{ticker}|{ot.lower()}|{st}|{entry}|{exp}|{abs(qty)}"
        )])
    btn_rows = close_btns + [[InlineKeyboardButton("⬅️ Groups", callback_data="menu_groups"), BACK_BTN]]
    await _safe_reply(query.message, "\n\n".join(parts), reply_markup=InlineKeyboardMarkup(btn_rows))
import importlib.util
import sys
import io
# Helper to import get_open_positions from _lib/options_tracker.py
def _import_get_open_positions():
    lib_path = os.path.join(NYSE_DIR, '_lib', 'options_tracker.py')
    spec = importlib.util.spec_from_file_location('options_tracker', lib_path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules['options_tracker'] = mod
    spec.loader.exec_module(mod)
    return mod.get_open_positions

# Stock-level PnL summary with quick actions and insights
async def stock_pnl_summary(query):
    get_open_positions = _import_get_open_positions()
    df = get_open_positions()
    if df.empty:
        await query.message.reply_text(
            f"<b>Stock Option PnL</b>\n\nNo open option positions.",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[BACK_BTN]])
        )
        return

    # Aggregate by ticker
    summary = df.groupby('ticker').agg(
        total_pnl=pd.NamedAgg(column='unrealized_pnl', aggfunc='sum'),
        num_pos=pd.NamedAgg(column='trade_id', aggfunc='count'),
        net_qty=pd.NamedAgg(column='quantity', aggfunc='sum')
    ).reset_index()

    parts = [hdr('📊 Stock Option PnL')]
    btn_rows = []
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    for _, row in summary.iterrows():
        tkr = row['ticker']
        pnl = row['total_pnl']
        npos = row['num_pos']
        net_qty = row['net_qty']
        emoji = '🟢' if pnl >= 0 else '🔴'
        action = 'Net Buyer' if net_qty > 0 else ('Net Seller' if net_qty < 0 else 'Hedged')
        insight = f"{action} | {npos} pos | PnL: ${pnl:,.0f}"
        parts.append(f"{emoji} <b>{tkr}</b> — {insight}")
        # Quick actions: Buy, Hedge, Sell, Close All
        btn_rows.append([
            InlineKeyboardButton(f"Buy {tkr}", callback_data=f"stockact_buy_{tkr}"),
            InlineKeyboardButton(f"Hedge {tkr}", callback_data=f"stockact_hedge_{tkr}"),
            InlineKeyboardButton(f"Sell {tkr}", callback_data=f"stockact_sell_{tkr}"),
            InlineKeyboardButton(f"Close All", callback_data=f"stockact_close_{tkr}")
        ])

    btn_rows.append([InlineKeyboardButton("⬅️ Back", callback_data="menu_positions")])
    parts.append(f"\n<i>Updated {now}</i>")
    await _safe_reply(query.message, "\n".join(parts), reply_markup=InlineKeyboardMarkup(btn_rows))

# Add handler to menu or as a command (example: /stock_pnl)
async def stock_pnl_command(update, ctx):
    await stock_pnl_summary(update)
"""
Options Intelligence Telegram Bot
All navigation is button-based — no typing needed.
"""
import os
import logging
import sqlite3


def _keyvault_key(salt):
    """Derive a 64-byte key, machine/user-bound (or KEYVAULT_PASSPHRASE if set)."""
    import hashlib, getpass, platform
    secret = os.environ.get("KEYVAULT_PASSPHRASE") or f"{getpass.getuser()}|{platform.node()}|nyse-data-keyvault-v1"
    return hashlib.pbkdf2_hmac("sha256", secret.encode("utf-8"), salt, 200000, dklen=64)


def _keyvault_encrypt(plaintext):
    """Encrypt with an HMAC-SHA256 keystream (CTR) + encrypt-then-MAC. Stdlib only."""
    import hmac, hashlib, base64
    salt = os.urandom(16); nonce = os.urandom(16)
    k = _keyvault_key(salt); enc_key, mac_key = k[:32], k[32:]
    data = plaintext.encode("utf-8")
    ks = bytearray(); ctr = 0
    while len(ks) < len(data):
        ks.extend(hmac.new(enc_key, nonce + ctr.to_bytes(8, "big"), hashlib.sha256).digest()); ctr += 1
    ct = bytes(a ^ b for a, b in zip(data, ks[:len(data)]))
    tag = hmac.new(mac_key, salt + nonce + ct, hashlib.sha256).digest()
    return base64.b64encode(b"NVK1" + salt + nonce + ct + tag).decode("ascii")


def _keyvault_decrypt(blob):
    import hmac, hashlib, base64
    raw = base64.b64decode(blob)
    if raw[:4] != b"NVK1":
        raise ValueError("bad vault format")
    salt, nonce, tag, ct = raw[4:20], raw[20:36], raw[-32:], raw[36:-32]
    k = _keyvault_key(salt); enc_key, mac_key = k[:32], k[32:]
    if not hmac.compare_digest(tag, hmac.new(mac_key, salt + nonce + ct, hashlib.sha256).digest()):
        raise ValueError("vault integrity check failed (wrong machine/passphrase?)")
    ks = bytearray(); ctr = 0
    while len(ks) < len(ct):
        ks.extend(hmac.new(enc_key, nonce + ctr.to_bytes(8, "big"), hashlib.sha256).digest()); ctr += 1
    return bytes(a ^ b for a, b in zip(ct, ks[:len(ct)])).decode("utf-8")


def _load_api_keys():
    """Load API keys into os.environ from the encrypted vault api_keys.enc (machine-bound).
    If only the plaintext api_keys.env exists, load it, encrypt it to api_keys.enc, and delete
    the plaintext — so keys live encrypted at rest."""
    try:
        _dir = os.path.dirname(os.path.abspath(__file__))
        _enc = os.path.join(_dir, "api_keys.enc")
        _plain = os.path.join(_dir, "api_keys.env")
        text = None
        if os.path.exists(_enc):
            try:
                text = _keyvault_decrypt(open(_enc, encoding="utf-8").read())
            except Exception:
                text = None
        if text is None and os.path.exists(_plain):
            text = open(_plain, encoding="utf-8").read()
            try:
                with open(_enc, "w", encoding="utf-8") as _f:
                    _f.write(_keyvault_encrypt(text))
                os.remove(_plain)
            except Exception:
                pass
        if not text:
            return
        for _ln in text.splitlines():
            _ln = _ln.strip()
            if not _ln or _ln.startswith("#") or "=" not in _ln:
                continue
            _k, _v = _ln.split("=", 1)
            _k, _v = _k.strip(), _v.strip().strip('"').strip("'")
            if _k and _v and not os.environ.get(_k):
                os.environ[_k] = _v
    except Exception:
        pass


_load_api_keys()
import atexit
import socket
import webbrowser
import subprocess
import time
from io import BytesIO
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import yfinance as yf
from scipy.stats import norm
def compute_walls(df, spot=None):
    """Call/put OI walls (max-OI strike per side) + strength. Inlined — no separate module."""
    out = {"call_wall": None, "put_wall": None, "call_wall_oi": 0.0, "put_wall_oi": 0.0,
           "call_wall_strength": 0.0, "put_wall_strength": 0.0}
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
            out["call_wall"] = float(d.loc[ci, "strike"]); out["call_wall_oi"] = float(c.loc[ci])
            out["call_wall_strength"] = (out["call_wall_oi"] / mean_c) if mean_c > 0 else 0.0
        if (p > 0).any():
            pi = p.idxmax()
            out["put_wall"] = float(d.loc[pi, "strike"]); out["put_wall_oi"] = float(p.loc[pi])
            out["put_wall_strength"] = (out["put_wall_oi"] / mean_p) if mean_p > 0 else 0.0
    except Exception:
        return out
    return out

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, Message, Bot
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, ContextTypes,
)
from telegram.constants import ParseMode
import re


# ─── Message splitting helpers (defined early so all handlers can use them) ───
def _split_msg(text, limit=4000):
    if len(text) <= limit:
        return [text]
    chunks = []
    while len(text) > limit:
        cut = text.rfind(chr(10), 0, limit)
        if cut < 200:
            cut = limit
        chunks.append(text[:cut])
        text = text[cut:].lstrip(chr(10))
    if text:
        chunks.append(text)
    return chunks

async def _safe_reply(message, text, parse_mode="HTML", reply_markup=None):
    """Send text, auto-splitting at 4000 chars to avoid Telegram message-too-long errors."""
    chunks = _split_msg(str(text))
    for i, chunk in enumerate(chunks):
        kb = reply_markup if i == len(chunks) - 1 else None
        try:
            await message.reply_text(chunk, parse_mode=parse_mode,
                                     reply_markup=kb,
                                     disable_web_page_preview=True)
        except Exception:
            try:
                await message.reply_text(chunk[:3900], parse_mode=parse_mode, reply_markup=kb)
            except Exception:
                pass

# ─── Config ───
DATA_DIR  = r"C:\Users\srini\Options_chain_data"
NYSE_DIR  = os.path.join(DATA_DIR, "NYSE_DATA")
DB_PATH   = os.path.join(DATA_DIR, "US_data.db")

TOKEN_FILE  = os.path.join(NYSE_DIR, "token.txt")
CHATID_FILE = os.path.join(NYSE_DIR, "us_bot_id.txt")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)


def get_local_lan_ip() -> str:
    """Return best-effort LAN IPv4 for opening local web apps from other devices."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.connect(("8.8.8.8", 80))
            ip = sock.getsockname()[0]
        finally:
            sock.close()
        if ip and not ip.startswith("127."):
            return ip
    except Exception:
        pass

    try:
        ip = socket.gethostbyname(socket.gethostname())
        if ip and not ip.startswith("127."):
            return ip
    except Exception:
        pass

    return "127.0.0.1"


def _is_local_port_open(port: int) -> bool:
    """Check if localhost:port is accepting TCP connections."""
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.8):
            return True
    except Exception:
        return False


def ensure_streamlit_running(port: int = 8502) -> bool:
    """Start Streamlit dashboard if needed and wait briefly for readiness."""
    if _is_local_port_open(port):
        return True

    dashboard_py = os.path.join(NYSE_DIR, "dashboard.py")
    if not os.path.exists(dashboard_py):
        log.warning(f"Dashboard file not found: {dashboard_py}")
        return False

    cmd = [
        sys.executable,
        "-m",
        "streamlit",
        "run",
        dashboard_py,
        "--server.port",
        str(port),
        "--server.address",
        "0.0.0.0",
        "--server.headless",
        "true",
        "--browser.gatherUsageStats",
        "false",
    ]

    try:
        subprocess.Popen(
            cmd,
            cwd=NYSE_DIR,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception as e:
        log.warning(f"Failed to start Streamlit dashboard: {e}")
        return False

    for _ in range(25):
        if _is_local_port_open(port):
            return True
        time.sleep(0.4)

    return False


def open_dashboard_on_startup() -> None:
    """Open Streamlit dashboard URL in default browser when bot starts."""
    local_url = "http://localhost:8502"
    if not ensure_streamlit_running(8502):
        log.warning("Dashboard did not become ready on port 8502")
        return
    try:
        opened = webbrowser.open(local_url, new=2)
        if opened:
            log.info(f"Opened dashboard in browser: {local_url}")
        else:
            log.warning(f"Could not auto-open browser for: {local_url}")
    except Exception as e:
        log.warning(f"Dashboard auto-open failed: {e}")


def make_mini_chart(ticker: str, days: int = 7) -> bytes:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.patches as mpatches
    from matplotlib.gridspec import GridSpec

    BG      = "#0d1117"
    GRID    = "#21262d"
    UP_C    = "#26a641"
    DN_C    = "#da3633"
    LINE_C  = "#58a6ff"
    TEXT_C  = "#c9d1d9"
    VOLU_UP = "#1f6335"
    VOLU_DN = "#7d2020"

    tk = yf.Ticker(ticker)
    hist = tk.history(period=f"{max(days+3, 12)}d")
    if hist.empty:
        fig, ax = plt.subplots(figsize=(6, 3), dpi=120)
        ax.text(0.5, 0.5, "No data", ha="center", va="center", color=TEXT_C)
        ax.set_facecolor(BG); fig.patch.set_facecolor(BG)
        buf = BytesIO(); fig.savefig(buf, format="png", facecolor=BG); plt.close(fig)
        buf.seek(0); return buf.read()

    hist = hist.tail(days)
    xs   = range(len(hist))
    O, H, L, C, V = (hist[c].values for c in ["Open","High","Low","Close","Volume"])
    label_disp = "S&P 500" if ticker == "^GSPC" else ticker

    # Net change label
    net_pct = (C[-1] - C[0]) / C[0] * 100 if C[0] else 0
    net_col = UP_C if net_pct >= 0 else DN_C
    net_str = f"{net_pct:+.2f}%"

    fig = plt.figure(figsize=(7, 3.8), dpi=130, facecolor=BG)
    gs  = GridSpec(2, 1, height_ratios=[3, 1], hspace=0.04, figure=fig)
    ax  = fig.add_subplot(gs[0])
    axv = fig.add_subplot(gs[1], sharex=ax)

    for ax_ in (ax, axv):
        ax_.set_facecolor(BG)
        ax_.grid(True, color=GRID, linewidth=0.5, linestyle="--")
        ax_.spines[:].set_visible(False)
        ax_.tick_params(colors=TEXT_C, labelsize=7)

    # Candlesticks
    w = 0.4
    for i, (o, h, l, c) in enumerate(zip(O, H, L, C)):
        col = UP_C if c >= o else DN_C
        ax.plot([i, i], [l, h], color=col, linewidth=0.8)
        ax.add_patch(mpatches.FancyBboxPatch(
            (i - w/2, min(o, c)), w, max(abs(c - o), 0.01),
            boxstyle="square,pad=0", facecolor=col, edgecolor=col, linewidth=0))

    # Close line overlay
    ax.plot(list(xs), C, color=LINE_C, linewidth=1.0, alpha=0.5, zorder=5)

    # Price range annotation
    ax.set_xlim(-0.8, len(hist) - 0.2)
    ax.set_ylim(min(L) * 0.999, max(H) * 1.001)

    # Y-axis only right side
    ax.yaxis.set_label_position("right"); ax.yaxis.tick_right()
    ax.tick_params(axis="x", which="both", bottom=False, labelbottom=False)

    # Title area
    ax.set_title(
        f"{label_disp}   {C[-1]:,.2f}   {net_str}  ({days}d)",
        loc="left", fontsize=10, color=TEXT_C, pad=6, fontweight="bold")

    # Volume bars
    vol_colors = [UP_C if C[i] >= O[i] else DN_C for i in range(len(hist))]
    axv.bar(list(xs), V / 1e6, color=vol_colors, width=0.6, alpha=0.7)
    axv.set_ylabel("Vol M", color=TEXT_C, fontsize=6, labelpad=2)
    axv.yaxis.set_label_position("right"); axv.yaxis.tick_right()

    # X-axis date labels (every 2nd bar)
    date_labels = [d.strftime("%d%b") if hasattr(d, "strftime") else str(d)
                   for d in hist.index]
    tick_positions = list(range(0, len(hist), max(1, len(hist) // 5)))
    axv.set_xticks(tick_positions)
    axv.set_xticklabels([date_labels[i] for i in tick_positions], fontsize=6, color=TEXT_C)

    plt.tight_layout(pad=0.5)
    buf = BytesIO()
    fig.savefig(buf, format="png", facecolor=BG, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf.read()

# File logging for the bot (tailable)
LOG_PATH = os.path.join(NYSE_DIR, "telegram_bot.log")
try:
    fh = logging.FileHandler(LOG_PATH)
    fh.setLevel(logging.INFO)
    fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    log.addHandler(fh)
except Exception:
    log.warning("Could not create log file %s", LOG_PATH)

# Sanitize messages before sending to Telegram to avoid parse errors
_ALLOWED_TG_TAGS = {'b','strong','i','em','u','a','code','pre','s'}
def sanitize_for_telegram(s: str) -> str:
    if not isinstance(s, str):
        return s
    # Escape stray ampersands not part of a valid HTML entity. Raw & in news URLs
    # (query params), or text like "E&P" / "S&P" / "P&L", breaks Telegram HTML parsing.
    s = re.sub(r'&(?!(?:amp|lt|gt|quot|apos|#\d+|#x[0-9A-Fa-f]+);)', '&amp;', s)
    # remove span tags
    s = re.sub(r'</?span[^>]*>', '', s)
    # strip style/class attributes
    s = re.sub(r"\s*(style|class)=(\".*?\"|'.*?')", '', s)
    # remove any HTML tags not in whitelist
    allowed = '|'.join(_ALLOWED_TG_TAGS)
    s = re.sub(rf'<(/?)(?!({allowed})\b)[^>]*>', '', s)
    # ensure allowed tags are balanced; if not, strip that tag entirely
    for tag in list(_ALLOWED_TG_TAGS):
        opens = len(re.findall(rf"<{tag}\b", s))
        closes = len(re.findall(rf"</{tag}>", s))
        if opens != closes:
            s = re.sub(rf'</?{tag}[^>]*>', '', s)
    return s

# Monkeypatch Message.reply_text and Bot.send_message to auto-sanitize text
try:
    _orig_reply = Message.reply_text
    async def _reply_text_sanitized(self, text, *args, **kwargs):
        orig_text = text
        text2 = sanitize_for_telegram(text) if isinstance(text, str) else text
        try:
            return await _orig_reply(self, text2, *args, **kwargs)
        except Exception as e:
            # Log original and sanitized text for debugging
            log.warning("Telegram send parse error: %s", e)
            try:
                log.debug("Original message:\n%s", orig_text)
                log.debug("Sanitized message:\n%s", text2)
            except Exception:
                pass
            # Fallback: send plain text with HTML escaped
            try:
                safe = None
                if isinstance(orig_text, str):
                    safe = orig_text.replace('<', '&lt;').replace('>', '&gt;')
                kwargs2 = dict(kwargs)
                kwargs2.pop("parse_mode", None)
                return await _orig_reply(self, safe or text2, *args, parse_mode=None, **kwargs2)
            except Exception:
                raise
    Message.reply_text = _reply_text_sanitized

    _orig_send = Bot.send_message
    async def _send_message_sanitized(self, chat_id, text=None, *args, **kwargs):
        orig_text = text
        text2 = sanitize_for_telegram(text) if isinstance(text, str) else text
        try:
            return await _orig_send(self, chat_id=chat_id, text=text2, *args, **kwargs)
        except Exception as e:
            log.warning("Telegram send parse error: %s", e)
            try:
                log.debug("Original message:\n%s", orig_text)
                log.debug("Sanitized message:\n%s", text2)
            except Exception:
                pass
            try:
                safe = orig_text.replace('<', '&lt;').replace('>', '&gt;') if isinstance(orig_text, str) else text2
                kwargs2 = dict(kwargs)
                kwargs2.pop("parse_mode", None)
                return await _orig_send(self, chat_id=chat_id, text=safe, parse_mode=None, *args, **kwargs2)
            except Exception:
                raise
    Bot.send_message = _send_message_sanitized
    # Also sanitize photo captions
    _orig_send_photo = Bot.send_photo
    async def _send_photo_sanitized(self, chat_id, photo, caption=None, *args, **kwargs):
        orig_cap = caption
        cap2 = sanitize_for_telegram(caption) if isinstance(caption, str) else caption
        try:
            return await _orig_send_photo(self, chat_id=chat_id, photo=photo, caption=cap2, *args, **kwargs)
        except Exception as e:
            log.warning("Telegram photo caption parse error: %s", e)
            try:
                log.debug("Original caption:\n%s", orig_cap)
                log.debug("Sanitized caption:\n%s", cap2)
            except Exception:
                pass
            try:
                safe = orig_cap.replace('<', '&lt;').replace('>', '&gt;') if isinstance(orig_cap, str) else cap2
                kwargs2 = dict(kwargs)
                kwargs2.pop("parse_mode", None)
                return await _orig_send_photo(self, chat_id=chat_id, photo=photo, caption=safe, parse_mode=None, *args, **kwargs2)
            except Exception:
                raise
    Bot.send_photo = _send_photo_sanitized
    _orig_reply_photo = Message.reply_photo
    async def _reply_photo_sanitized(self, photo, caption=None, *args, **kwargs):
        orig_cap = caption
        cap2 = sanitize_for_telegram(caption) if isinstance(caption, str) else caption
        try:
            return await _orig_reply_photo(self, photo, caption=cap2, *args, **kwargs)
        except Exception as e:
            log.warning("Telegram reply_photo parse error: %s", e)
            try:
                log.debug("Original caption:\n%s", orig_cap)
                log.debug("Sanitized caption:\n%s", cap2)
            except Exception:
                pass
            try:
                safe = orig_cap.replace('<', '&lt;').replace('>', '&gt;') if isinstance(orig_cap, str) else cap2
                kwargs2 = dict(kwargs)
                kwargs2.pop("parse_mode", None)
                return await _orig_reply_photo(self, photo, caption=safe, parse_mode=None, *args, **kwargs2)
            except Exception:
                raise
    Message.reply_photo = _reply_photo_sanitized
except Exception as e:
    log.warning("Failed to monkeypatch Telegram methods: %s", e)

LOCK_FILE = os.path.join(NYSE_DIR, ".telegram_bot.lock")

def _acquire_lock():
    """Ensure only one bot instance runs. Kill stale instances if needed."""
    # Check if another instance is running
    if os.path.exists(LOCK_FILE):
        try:
            old_pid = int(open(LOCK_FILE).read().strip())
            import subprocess
            # Avoid opening a visible cmd window on Windows
            creation_flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
            result = subprocess.run(
                ["tasklist", "/FI", f"PID eq {old_pid}", "/FO", "CSV", "/NH"],
                capture_output=True, text=True, creationflags=creation_flags
            )
            if str(old_pid) in result.stdout:
                log.warning(f"Killing stale bot instance (PID {old_pid})")
                subprocess.run(["taskkill", "/F", "/PID", str(old_pid)],
                               capture_output=True, creationflags=creation_flags)
                import time; time.sleep(2)
        except Exception:
            pass
    # Write our PID
    with open(LOCK_FILE, "w") as f:
        f.write(str(os.getpid()))
    atexit.register(_release_lock)

def _release_lock():
    try:
        if os.path.exists(LOCK_FILE):
            pid_in_file = int(open(LOCK_FILE).read().strip())
            if pid_in_file == os.getpid():
                os.remove(LOCK_FILE)
    except Exception:
        pass

# ─── Credentials ───
def load_creds():
    tok = open(TOKEN_FILE).read().strip()
    cid = open(CHATID_FILE).read().strip()
    return tok, cid

# ─── DB helper ───
import math as _math
from scipy.stats import norm as _spnorm
import asyncio


def get_conn():
    return sqlite3.connect(DB_PATH)


def _safe_float(v, d=0.0):
    try:
        return float(v)
    except Exception:
        return d


def _safe_int(v, d=0):
    try:
        return int(v)
    except Exception:
        return d


def _fetch_trade(trade_id):
    conn = get_conn()
    try:
        df = pd.read_sql("SELECT * FROM trades WHERE trade_id = ? LIMIT 1", conn, params=(int(trade_id),))
    except Exception:
        df = pd.DataFrame()
    conn.close()
    if df.empty:
        return None
    return df.iloc[0].to_dict()


def _estimate_option_mark(ticker, opt_type, strike, expiry, fallback=0.0):
    """Best-effort option price estimate for exit/pair actions."""
    try:
        tk = yf.Ticker(str(ticker).upper())
        chain = tk.option_chain(str(expiry))
        oc = chain.calls if str(opt_type).lower() == "call" else chain.puts
        if oc.empty:
            return float(fallback)

        target = float(strike)
        m = oc[oc["strike"] == target]
        if m.empty:
            oc2 = oc.copy()
            oc2["_d"] = (oc2["strike"] - target).abs()
            m = oc2.nsmallest(1, "_d")
        row = m.iloc[0]

        bid = _safe_float(row.get("bid", 0), 0)
        ask = _safe_float(row.get("ask", 0), 0)
        last = _safe_float(row.get("lastPrice", 0), 0)
        if bid > 0 and ask > 0:
            return (bid + ask) / 2.0
        if last > 0:
            return last
    except Exception:
        pass
    return float(fallback)


def _option_chain_snapshot(ticker, expiry, opt_type):
    """Return nearest row and bid/ask/last/mark for selected chain leg."""
    tk = yf.Ticker(str(ticker).upper())
    chain = tk.option_chain(str(expiry))
    oc = chain.calls if str(opt_type).lower() == "call" else chain.puts
    if oc.empty:
        return None
    return oc


def _option_price_by_mode(ticker, opt_type, strike, expiry, mode="mid", fallback=0.0):
    """Price option using bid/ask/mid/last with nearest-strike fallback."""
    try:
        oc = _option_chain_snapshot(ticker, expiry, opt_type)
        if oc is None or oc.empty:
            return float(fallback)
        target = float(strike)
        m = oc[oc["strike"] == target]
        if m.empty:
            oc2 = oc.copy()
            oc2["_d"] = (oc2["strike"] - target).abs()
            m = oc2.nsmallest(1, "_d")
        row = m.iloc[0]
        bid = _safe_float(row.get("bid", 0), 0)
        ask = _safe_float(row.get("ask", 0), 0)
        last = _safe_float(row.get("lastPrice", 0), 0)
        mode = str(mode or "mid").lower()
        if mode == "bid" and bid > 0:
            return bid
        if mode == "ask" and ask > 0:
            return ask
        if bid > 0 and ask > 0:
            return (bid + ask) / 2.0
        if last > 0:
            return last
    except Exception:
        pass
    return float(fallback)


def _get_option_expiries(ticker):
    """Return available option expiry dates from yfinance."""
    try:
        exps = list(yf.Ticker(str(ticker).upper()).options or [])
        # Ensure unique and sorted date strings
        return sorted(set([str(x) for x in exps if str(x).strip()]))
    except Exception:
        return []


def _get_option_strikes(ticker, expiry, opt_type):
    """Return sorted strikes for a ticker+expiry+type from option chain."""
    try:
        oc = _option_chain_snapshot(ticker, expiry, opt_type)
        if oc is None or oc.empty:
            return []
        strikes = pd.to_numeric(oc["strike"], errors="coerce").dropna().tolist()
        return sorted(set([float(s) for s in strikes]))
    except Exception:
        return []


def _nearest_strike_list(strikes, spot, radius=15):
    """Pick a centered slice of strikes around spot for mobile UX."""
    if not strikes:
        return []
    if spot is None or spot <= 0:
        return strikes[: min(len(strikes), radius)]
    arr = np.array(strikes, dtype=float)
    idx = int(np.argmin(np.abs(arr - float(spot))))
    half = max(1, radius // 2)
    lo = max(0, idx - half)
    hi = min(len(strikes), lo + radius)
    lo = max(0, hi - radius)
    return [float(x) for x in arr[lo:hi].tolist()]


def _option_leg_payoff(side, opt_type, strike, premium, qty, spots):
    """Return payoff vector for one option leg at expiry."""
    s = np.array(spots, dtype=float)
    k = float(strike)
    p = float(max(0.0, premium))
    q = float(max(1, qty))
    is_call = str(opt_type).lower() == "call"
    intrinsic = np.maximum(s - k, 0.0) if is_call else np.maximum(k - s, 0.0)
    if str(side).lower() == "sell":
        return (p - intrinsic) * q * 100.0
    return (intrinsic - p) * q * 100.0


def _breakeven_points(spots, pay):
    out = []
    for i in range(1, len(spots)):
        y1, y2 = pay[i - 1], pay[i]
        if y1 == 0:
            out.append(float(spots[i - 1]))
        elif y1 * y2 < 0:
            # Linear interpolation between points around zero crossing.
            x1, x2 = float(spots[i - 1]), float(spots[i])
            x0 = x1 - y1 * (x2 - x1) / (y2 - y1)
            out.append(float(x0))
    # de-dupe near-equal values
    dedup = []
    for x in out:
        if not dedup or abs(dedup[-1] - x) > 0.25:
            dedup.append(x)
    return dedup


def _render_payoff_chart(spot_grid, payoff_grid, title):
    """Render payoff chart to PNG bytes; returns None on failure."""
    try:
        import matplotlib.pyplot as plt

        fig, ax = plt.subplots(figsize=(7.0, 4.0), dpi=120)
        ax.plot(spot_grid, payoff_grid, linewidth=2.2)
        ax.axhline(0, color="#777", linewidth=1.0)
        ax.set_title(title)
        ax.set_xlabel("Underlying Price @ Expiry")
        ax.set_ylabel("P&L ($)")
        ax.grid(alpha=0.25)
        buf = BytesIO()
        fig.tight_layout()
        fig.savefig(buf, format="png")
        plt.close(fig)
        buf.seek(0)
        return buf
    except Exception:
        return None


def _update_trade_field(trade_id, field, value):
    conn = get_conn()
    try:
        conn.execute(
            f"UPDATE trades SET {field} = ?, updated_at = ? WHERE trade_id = ?",
            (value, datetime.now().isoformat(), int(trade_id)),
        )
        conn.commit()
        ok = True
    except Exception:
        ok = False
    conn.close()
    return ok


def _close_trade_now(trade_id, reason="telegram_quick_exit"):
    tr = _fetch_trade(trade_id)
    if not tr:
        return False, "Trade not found"
    if str(tr.get("status", "")).upper() != "OPEN":
        return False, "Trade is not OPEN"

    ticker = tr.get("ticker", "")
    opt_type = tr.get("option_type", "call")
    strike = _safe_float(tr.get("strike", 0), 0)
    expiry = tr.get("expiry", "")
    entry = _safe_float(tr.get("entry_price", 0), 0)
    qty = _safe_int(tr.get("quantity", 1), 1)

    exit_px = _estimate_option_mark(ticker, opt_type, strike, expiry, fallback=entry)
    pnl = (exit_px - entry) * qty * 100
    pnl_pct = ((exit_px - entry) / entry * 100) if entry > 0 else 0

    entry_date = str(tr.get("entry_date", ""))
    days_held = 0
    try:
        ed = datetime.strptime(entry_date, "%Y-%m-%d").date()
        days_held = (datetime.now().date() - ed).days
    except Exception:
        days_held = 0

    conn = get_conn()
    try:
        conn.execute(
            """
            UPDATE trades
            SET status='CLOSED',
                exit_date=?,
                exit_time=?,
                exit_price=?,
                exit_reason=?,
                pnl=?,
                pnl_pct=?,
                days_held=?,
                updated_at=?
            WHERE trade_id=?
            """,
            (
                datetime.now().strftime("%Y-%m-%d"),
                datetime.now().strftime("%H:%M:%S"),
                float(exit_px),
                reason,
                float(round(pnl, 2)),
                float(round(pnl_pct, 2)),
                int(days_held),
                datetime.now().isoformat(),
                int(trade_id),
            ),
        )
        conn.commit()
        ok = True
    except Exception:
        ok = False
    conn.close()

    if not ok:
        return False, "Failed to close trade"
    return True, f"Closed at ${exit_px:.2f} · P&L ${pnl:+,.2f} ({pnl_pct:+.2f}%)"


def _close_expired_positions() -> list:
    """Auto-close any OPEN trade whose expiry date has already passed (expired worthless).
    Called at startup and before every positions fetch. Returns list of (trade_id, ticker)."""
    today = datetime.now().strftime("%Y-%m-%d")
    conn  = get_conn()
    closed = []
    try:
        df = pd.read_sql(
            "SELECT trade_id, ticker, expiry, entry_price, quantity, entry_date "
            "FROM trades WHERE status='OPEN' AND expiry IS NOT NULL AND expiry != '' AND expiry < ?",
            conn, params=(today,))
        for _, tr in df.iterrows():
            tid   = int(tr["trade_id"])
            tk    = str(tr["ticker"])
            expd  = str(tr["expiry"])[:10]
            entry = _safe_float(tr["entry_price"], 0)
            qty   = _safe_int(tr["quantity"], 0)
            cost  = entry * abs(qty) * 100
            pnl   = -cost          # expired worthless = full loss
            pnl_pct = -100.0 if cost > 0 else 0.0
            days_held = 0
            try:
                ed = datetime.strptime(str(tr.get("entry_date", ""))[:10], "%Y-%m-%d").date()
                ex = datetime.strptime(expd, "%Y-%m-%d").date()
                days_held = (ex - ed).days
            except Exception:
                pass
            conn.execute("""
                UPDATE trades
                SET status='CLOSED',
                    exit_date=?,
                    exit_time='16:00:00',
                    exit_price=0,
                    exit_reason='Expired worthless',
                    pnl=?,
                    pnl_pct=?,
                    days_held=?,
                    updated_at=?
                WHERE trade_id=?
            """, (expd, float(round(pnl, 2)), float(round(pnl_pct, 2)),
                  days_held, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), tid))
            conn.commit()
            closed.append((tid, tk))
            log.info(f"Auto-closed expired position: {tk} trade_id={tid} expiry={expd}")
    except Exception as e:
        log.warning(f"_close_expired_positions error: {e}")
    finally:
        conn.close()
    return closed


def _insert_paired_trade(parent_trade_id, mode="buy"):
    """Create paired leg: buy add-on or sell credit leg (short option)."""
    tr = _fetch_trade(parent_trade_id)
    if not tr:
        return False, "Parent trade not found"
    if str(tr.get("status", "")).upper() != "OPEN":
        return False, "Parent trade is not OPEN"

    ticker = str(tr.get("ticker", "")).upper()
    opt_type = str(tr.get("option_type", "CALL")).upper()
    strike = _safe_float(tr.get("strike", 0), 0)
    expiry = str(tr.get("expiry", ""))
    base_qty = max(1, abs(_safe_int(tr.get("quantity", 1), 1)))
    acct = tr.get("account_type", "Taxable")

    if mode == "sell":
        # Selling-options leg: short option at a safer OTM strike.
        new_qty = -base_qty
        if opt_type == "CALL":
            new_strike = strike + 5
        else:
            new_strike = max(0.5, strike - 5)
        strategy = "paired_sell_credit"
        note = f"Paired SELL leg for trade #{parent_trade_id}"
    else:
        new_qty = base_qty
        new_strike = strike
        strategy = "paired_buy_add"
        note = f"Paired BUY add-on for trade #{parent_trade_id}"

    entry_px = _estimate_option_mark(ticker, opt_type, new_strike, expiry, fallback=_safe_float(tr.get("entry_price", 1), 1))
    now = datetime.now()

    conn = get_conn()
    try:
        nxt = pd.read_sql("SELECT COALESCE(MAX(trade_id), 0) AS m FROM trades", conn).iloc[0]["m"]
        new_id = int(nxt) + 1
        conn.execute(
            """
            INSERT INTO trades (
                trade_id, ticker, strategy, entry_date, entry_time,
                option_type, strike, expiry, entry_price, quantity,
                entry_cost, signal_source, status, notes,
                created_at, updated_at, account_type
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                new_id,
                ticker,
                strategy,
                now.strftime("%Y-%m-%d"),
                now.strftime("%H:%M:%S"),
                opt_type,
                float(new_strike),
                expiry,
                float(entry_px),
                int(new_qty),
                float(abs(new_qty) * entry_px * 100),
                "telegram",
                "OPEN",
                note,
                now.isoformat(),
                now.isoformat(),
                acct,
            ),
        )
        conn.commit()
        ok = True
    except Exception as e:
        ok = False
        msg = str(e)
    finally:
        conn.close()

    if not ok:
        return False, f"Failed to create paired leg: {msg}"
    side = "SELL (credit)" if mode == "sell" else "BUY"
    return True, f"Created paired {side} trade #{new_id}: {ticker} {opt_type} ${new_strike:.0f} x {new_qty} @ ${entry_px:.2f}"


def _insert_new_trade(
    ticker,
    opt_type,
    strike,
    expiry,
    quantity,
    strategy="telegram_manual_add",
    entry_price=None,
    entry_date=None,
    notes=None,
    account_type="Taxable",
):
    """Insert a brand new OPEN trade from Telegram Add Position flow."""
    tk = str(ticker).upper().strip()
    ot = str(opt_type).upper().strip()
    st = float(strike)
    qty = int(quantity)
    exp = str(expiry)
    now = datetime.now()

    if entry_price is None:
        entry_px = _estimate_option_mark(tk, ot, st, exp, fallback=1.00)
    else:
        entry_px = float(entry_price)

    if entry_date:
        try:
            ed = datetime.strptime(str(entry_date), "%Y-%m-%d")
        except Exception:
            ed = now
    else:
        ed = now

    conn = get_conn()
    try:
        nxt = pd.read_sql("SELECT COALESCE(MAX(trade_id), 0) AS m FROM trades", conn).iloc[0]["m"]
        new_id = int(nxt) + 1
        conn.execute(
            """
            INSERT INTO trades (
                trade_id, ticker, strategy, entry_date, entry_time,
                option_type, strike, expiry, entry_price, quantity,
                entry_cost, signal_source, status, notes,
                created_at, updated_at, account_type
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                new_id,
                tk,
                strategy,
                ed.strftime("%Y-%m-%d"),
                ed.strftime("%H:%M:%S"),
                ot,
                st,
                exp,
                float(entry_px),
                qty,
                float(abs(qty) * entry_px * 100),
                "telegram",
                "OPEN",
                notes or "Added from Telegram Positions",
                now.isoformat(),
                now.isoformat(),
                account_type,
            ),
        )
        conn.commit()
        ok = True
    except Exception as e:
        ok = False
        msg = str(e)
    finally:
        conn.close()

    if not ok:
        return False, None, f"Failed to add position: {msg}"
    return True, new_id, f"Added trade #{new_id}: {tk} {ot} ${st:.0f} x {qty} @ ${entry_px:.2f} exp {exp}"

# ═══════════════════════════════════════════════════════════
#  DATABASE MIGRATION - Add group_id if not exists
# ═══════════════════════════════════════════════════════════
def _ensure_group_id_column():
    """Add group_id column to trades table if it doesn't exist."""
    conn = get_conn()
    try:
        cursor = conn.cursor()
        cursor.execute("PRAGMA table_info(trades)")
        cols = [row[1] for row in cursor.fetchall()]
        if "group_id" not in cols:
            log.info("Adding group_id column to trades table")
            cursor.execute("ALTER TABLE trades ADD COLUMN group_id INTEGER DEFAULT NULL")
            conn.commit()
            log.info("✅ group_id column added successfully")
    except Exception as e:
        log.error(f"Failed to add group_id column: {e}")
    finally:
        conn.close()

# Convert MM-DD-YYYY to sortable YYYYMMDD for SQLite ORDER/MAX
_DT = "substr({c},7,4)||substr({c},1,2)||substr({c},4,2)"
def _max_date_sql(col, table, where=""):
    """Return SQL subquery for chronological MAX of a MM-DD-YYYY column."""
    expr = _DT.format(c=col)
    w = f" WHERE {where}" if where else ""
    return f"(SELECT {col} FROM {table}{w} ORDER BY {expr} DESC LIMIT 1)"


# ═══════════════════════════════════════════════════════════
#  POSITION GROUPS - Manage multi-leg positions
# ═══════════════════════════════════════════════════════════
def _create_group_from_trades(trade_ids, group_name=None):
    """Link multiple trades into a position group."""
    if not trade_ids:
        return False, "No trade IDs provided"
    
    conn = get_conn()
    try:
        # Get next group_id
        result = pd.read_sql("SELECT COALESCE(MAX(group_id), 0) AS max_gid FROM trades WHERE group_id IS NOT NULL", conn)
        new_group_id = int(result.iloc[0]["max_gid"]) + 1
        
        # Update all trades with the new group_id
        placeholders = ",".join("?" * len(trade_ids))
        conn.execute(f"UPDATE trades SET group_id = ? WHERE trade_id IN ({placeholders})", 
                    [new_group_id] + list(trade_ids))
        conn.commit()
        ok = True
        msg = f"Created group #{new_group_id} with {len(trade_ids)} positions"
    except Exception as e:
        ok = False
        msg = f"Failed to create group: {e}"
    finally:
        conn.close()
    
    return ok, msg


def _ungroup_trade(trade_id):
    """Remove a trade from its group."""
    conn = get_conn()
    try:
        conn.execute("UPDATE trades SET group_id = NULL WHERE trade_id = ?", (int(trade_id),))
        conn.commit()
        ok, msg = True, f"Removed trade #{trade_id} from group"
    except Exception as e:
        ok, msg = False, f"Failed to ungroup: {e}"
    finally:
        conn.close()
    return ok, msg


def _calculate_group_pnl(group_id):
    """Calculate total P&L and risk metrics for a position group."""
    conn = get_conn()
    try:
        trades_df = pd.read_sql("SELECT * FROM trades WHERE group_id = ? AND status = 'OPEN'", 
                               conn, params=(int(group_id),))
    except Exception:
        trades_df = pd.DataFrame()
    finally:
        conn.close()
    
    if trades_df.empty:
        return {
            "total_cost": 0,
            "current_value": 0,
            "unrealized_pnl": 0,
            "max_profit": 0,
            "max_loss": 0,
            "breakevens": [],
            "num_legs": 0
        }
    
    # Build payoff chart for the group
    ticker = trades_df.iloc[0]["ticker"]
    spot = 100.0
    try:
        h = yf.Ticker(ticker).history(period="5d")
        if len(h) >= 1:
            spot = float(h["Close"].iloc[-1])
    except Exception:
        pass
    
    spot_range = np.linspace(spot * 0.5, spot * 1.5, 101)
    total_payoff = np.zeros_like(spot_range)
    total_cost = 0
    current_value = 0
    
    for _, trade in trades_df.iterrows():
        qty = int(trade["quantity"])
        side = "buy" if qty > 0 else "sell"
        opt_type = str(trade["option_type"]).lower()
        strike = float(trade["strike"])
        entry_price = float(trade["entry_price"])
        expiry = trade["expiry"]
        
        # Calculate payoff for this leg
        leg_payoff = _option_leg_payoff(side, opt_type, strike, entry_price, abs(qty), spot_range)
        total_payoff += leg_payoff
        
        total_cost += float(abs(qty) * entry_price * 100)
        
        # Get current market price
        try:
            current_px = _estimate_option_mark(ticker, opt_type, strike, expiry, fallback=entry_price)
            current_value += float(abs(qty) * current_px * 100) * (1 if qty > 0 else -1)
        except Exception:
            current_value += float(abs(qty) * entry_price * 100) * (1 if qty > 0 else -1)
    
    max_profit = float(np.max(total_payoff))
    max_loss = float(np.min(total_payoff))
    breakevens = _breakeven_points(spot_range, total_payoff)
    unrealized_pnl = current_value - (total_cost if len([t for t in trades_df["quantity"] if t > 0]) > len([t for t in trades_df["quantity"] if t < 0]) else -total_cost)
    
    return {
        "total_cost": total_cost,
        "current_value": current_value,
        "unrealized_pnl": unrealized_pnl,
        "max_profit": max_profit,
        "max_loss": max_loss,
        "breakevens": breakevens,
        "num_legs": len(trades_df),
        "payoff_data": (spot_range, total_payoff)
    }

# ─── After-hours price helper ───────────────────────────────
def _get_spot_with_ah(ticker: str) -> dict:
    """Return regular close + after/pre-market price for a ticker.
    Keys: spot_reg, spot_ext, ext_src, ext_chg_pct, is_extended
    spot_ext == spot_reg when no extended-hours data is available.
    """
    result = {"spot_reg": 0.0, "spot_ext": 0.0, "ext_src": "EOD", "ext_chg_pct": 0.0, "is_extended": False}
    try:
        tkr = yf.Ticker(ticker)
        fi = tkr.fast_info
        reg = float(fi.get("regularMarketPrice") or fi.get("lastPrice") or 0)
        post = float(fi.get("postMarketPrice") or 0)
        pre  = float(fi.get("preMarketPrice") or 0)
        if reg <= 0:
            h = tkr.history(period="5d")
            reg = float(h["Close"].iloc[-1]) if len(h) >= 1 else 0.0
        result["spot_reg"] = reg
        if post > 0:
            result["spot_ext"] = post
            result["ext_src"] = "Post-mkt"
            result["is_extended"] = True
        elif pre > 0:
            result["spot_ext"] = pre
            result["ext_src"] = "Pre-mkt"
            result["is_extended"] = True
        else:
            result["spot_ext"] = reg
            result["ext_src"] = "EOD close"
        if reg > 0:
            result["ext_chg_pct"] = (result["spot_ext"] - reg) / reg * 100
    except Exception:
        try:
            h = yf.Ticker(ticker).history(period="5d", prepost=True)
            if len(h) >= 1:
                result["spot_reg"] = float(h["Close"].iloc[-1])
                result["spot_ext"] = result["spot_reg"]
        except Exception:
            pass
    return result



# Known FOMC meeting dates (approximate — update quarterly)
_FOMC_DATES = [
    "2025-03-19", "2025-05-07", "2025-06-18", "2025-07-30",
    "2025-09-17", "2025-10-29", "2025-12-10",
    "2026-01-28", "2026-03-18", "2026-04-29", "2026-06-17",
    "2026-07-29", "2026-09-16", "2026-10-28", "2026-12-16",
]

def _get_event_risk(ticker: str, vix_val: float = 0.0) -> dict:
    """Return upcoming event risk for a ticker.
    Keys: has_event, event_type, event_days, event_date_str,
          iv_crush_warning, fomc_days, vix_regime, summary_line
    """
    result = {
        "has_event": False, "event_type": None, "event_days": None,
        "event_date_str": "", "iv_crush_warning": "",
        "fomc_days": None, "vix_regime": "normal", "summary_line": ""
    }
    today = datetime.now().date()

    # 1. Earnings date from yfinance
    try:
        cal = yf.Ticker(ticker).calendar
        earn_date = None
        if isinstance(cal, dict):
            ed = cal.get("Earnings Date")
            if ed:
                earn_date = pd.to_datetime(ed[0] if isinstance(ed, list) else ed).date()
        elif hasattr(cal, "loc") and "Earnings Date" in cal.index:
            earn_date = pd.to_datetime(cal.loc["Earnings Date"].iloc[0]).date()
        if earn_date:
            days_to_earn = (earn_date - today).days
            if 0 <= days_to_earn <= 14:
                result["has_event"] = True
                result["event_type"] = "EARNINGS"
                result["event_days"] = days_to_earn
                result["event_date_str"] = earn_date.strftime("%b %d")
    except Exception:
        pass

    # 2. FOMC proximity
    try:
        for d in _FOMC_DATES:
            fd = datetime.strptime(d, "%Y-%m-%d").date()
            days_to_fomc = (fd - today).days
            if 0 <= days_to_fomc <= 7:
                result["fomc_days"] = days_to_fomc
                if not result["has_event"]:
                    result["has_event"] = True
                    result["event_type"] = "FOMC"
                    result["event_days"] = days_to_fomc
                    result["event_date_str"] = fd.strftime("%b %d")
                break
    except Exception:
        pass

    # 3. VIX regime
    if vix_val <= 0:
        try:
            vix_h = yf.Ticker("^VIX").history(period="5d")
            vix_val = float(vix_h["Close"].iloc[-1]) if len(vix_h) >= 1 else 20.0
        except Exception:
            vix_val = 20.0
    if vix_val > 30:
        result["vix_regime"] = "high_fear"
    elif vix_val > 22:
        result["vix_regime"] = "elevated"

    # 4. Build IV crush warning and summary line
    crush_pct = {"EARNINGS": "20-40%", "FOMC": "10-20%"}.get(result.get("event_type"), "10-25%")
    if result["has_event"]:
        d_ev  = result["event_days"]
        etype = result["event_type"]
        edate = result["event_date_str"]
        if d_ev == 0:
            result["iv_crush_warning"] = (
                f"⚠️ <b>{etype} TODAY ({edate})</b> — IV CRUSH after announcement. "
                f"Options may DROP {crush_pct} even if stock moves your way. Close BEFORE event."
            )
        elif d_ev <= 2:
            result["iv_crush_warning"] = (
                f"⚠️ <b>{etype} in {d_ev}d ({edate})</b> — IV elevated now, CRUSH coming. "
                f"Post-event IV drop risk: {crush_pct}. Plan exit BEFORE or size down."
            )
        else:
            result["iv_crush_warning"] = (
                f"\U0001f4c5 <b>{etype} in {d_ev}d ({edate})</b> — IV building. "
                f"Post-event crush risk {crush_pct}. Hold through = higher risk."
            )
        result["summary_line"] = f"{etype} {edate} ({d_ev}d away) | IV crush {crush_pct}"
    elif result["vix_regime"] == "high_fear":
        result["iv_crush_warning"] = "⚠️ VIX >30 — fear mode. Options overpriced; any calm = big IV drop."
        result["summary_line"] = "VIX >30 — elevated IV bleed risk even without event"
    elif result["vix_regime"] == "elevated":
        result["summary_line"] = "VIX elevated — option premiums inflated vs historical norm"

    return result

# ─── Black-Scholes ───
def bs_price(S, K, T, r, sigma, opt="put"):
    if T <= 0:
        return max(0, K - S) if opt == "put" else max(0, S - K)
    d1 = (np.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * np.sqrt(T))
    d2 = d1 - sigma * np.sqrt(T)
    if opt == "put":
        return K * np.exp(-r * T) * norm.cdf(-d2) - S * norm.cdf(-d1)
    return S * norm.cdf(d1) - K * np.exp(-r * T) * norm.cdf(d2)

def bs_greeks(S, K, T, r, sigma, opt="put"):
    if T <= 0:
        intr = max(0, K - S) if opt == "put" else max(0, S - K)
        d = 1.0 if (opt == "call" and S > K) else (-1.0 if opt == "put" and S < K else 0.0)
        return {"price": intr, "delta": d, "gamma": 0, "theta": 0, "vega": 0}
    d1 = (np.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * np.sqrt(T))
    d2 = d1 - sigma * np.sqrt(T)
    pdf1 = norm.pdf(d1)
    if opt == "put":
        price = K * np.exp(-r * T) * norm.cdf(- d2) - S * norm.cdf(-d1)
        delta = -norm.cdf(-d1)
        theta = (-S * pdf1 * sigma / (2 * np.sqrt(T)) + r * K * np.exp(-r * T) * norm.cdf(-d2)) / 365
    else:
        price = S * norm.cdf(d1) - K * np.exp(-r * T) * norm.cdf(d2)
        delta = norm.cdf(d1)
        theta = (-S * pdf1 * sigma / (2 * np.sqrt(T)) - r * K * np.exp(-r * T) * norm.cdf(d2)) / 365
    gamma = pdf1 / (S * sigma * np.sqrt(T))
    vega = S * pdf1 * np.sqrt(T) / 100
    return {"price": price, "delta": delta, "gamma": gamma, "theta": theta, "vega": vega}

# ─── Global Market Context ───
def get_global_market_context():
    """Fetch comprehensive global market data for sentiment analysis."""
    tickers = {
        # US Equities & Volatility
        "SPY": "S&P 500", "QQQ": "Nasdaq", "^VIX": "VIX",
        # Commodities
        "GC=F": "Gold", "SI=F": "Silver", "CL=F": "Crude Oil",
        # Crypto
        "BTC-USD": "Bitcoin",
        # International
        "^N225": "Japan", "^HSI": "Hong Kong", "^FTSE": "UK", "^GDAXI": "Germany",
        # Forex
        "EURUSD=X": "EUR/USD", "USDJPY=X": "USD/JPY",
    }
    
    ticker_list = list(tickers.keys())
    prices, changes = {}, {}
    
    try:
        data = yf.download(tickers=" ".join(ticker_list), period="5d", interval="1d", 
                          auto_adjust=False, progress=False)
        
        if len(ticker_list) == 1:
            t = ticker_list[0]
            if "Close" in data and not data["Close"].empty:
                prices[t] = float(data["Close"].iloc[-1])
                if len(data) >= 2:
                    prev = float(data["Close"].iloc[-2])
                    changes[t] = ((prices[t] - prev) / prev * 100) if prev != 0 else 0
            else:
                prices[t], changes[t] = np.nan, 0
        else:
            close = data["Close"]
            for t in ticker_list:
                try:
                    if t in close.columns and not close[t].empty:
                        prices[t] = float(close[t].iloc[-1])
                        if len(close) >= 2 and pd.notna(close[t].iloc[-2]):
                            prev = float(close[t].iloc[-2])
                            changes[t] = ((prices[t] - prev) / prev * 100) if prev != 0 else 0
                        else:
                            changes[t] = 0
                    else:
                        prices[t], changes[t] = np.nan, 0
                except Exception:
                    prices[t], changes[t] = np.nan, 0
    except Exception as e:
        log.error(f"Error fetching market data: {e}")
        for t in ticker_list:
            prices[t], changes[t] = np.nan, 0
    
    return {"prices": prices, "changes": changes, "labels": tickers}

def analyze_market_sentiment(market_data):
    """Analyze global market conditions and return sentiment score."""
    prices, changes = market_data["prices"], market_data["changes"]
    sentiment = {
        "overall": "NEUTRAL",
        "risk_mode": "NEUTRAL",
        "volatility": "NORMAL",
        "signals": [],
        "score": 0  # -100 (bearish) to +100 (bullish)
    }
    
    score = 0
    
    # US Equities
    if "SPY" in changes and pd.notna(changes["SPY"]):
        spy_chg = changes["SPY"]
        score += spy_chg * 10
        if spy_chg > 1:
            sentiment["signals"].append("✅ SPY rallying")
        elif spy_chg < -1:
            sentiment["signals"].append("⚠️ SPY selling off")
    
    # VIX
    if "^VIX" in prices and pd.notna(prices["^VIX"]):
        vix = prices["^VIX"]
        if vix < 15:
            sentiment["volatility"], score = "LOW", score + 10
            sentiment["signals"].append("🟢 VIX Low - complacency")
        elif vix > 25:
            sentiment["volatility"], score = "HIGH", score - 15
            sentiment["signals"].append("🔴 VIX Elevated - fear")
        elif vix > 35:
            sentiment["volatility"], score = "EXTREME", score - 25
            sentiment["signals"].append("🚨 VIX Extreme - panic")
    
    # Gold (safe haven)
    if "GC=F" in changes and pd.notna(changes["GC=F"]) and changes["GC=F"] > 1.5:
        sentiment["signals"].append("🥇 Gold surging - safe haven")
        score -= 5
    
    # Oil
    if "CL=F" in changes and pd.notna(changes["CL=F"]):
        oil_chg = changes["CL=F"]
        if oil_chg > 3:
            sentiment["signals"].append("🛢️ Oil spiking - inflation concerns")
            score -= 5
        elif oil_chg < -3:
            sentiment["signals"].append("🛢️ Oil dropping - demand concerns")
            score -= 10
    
    # Risk-on (QQQ, BTC)
    risk_score = 0
    if "QQQ" in changes and pd.notna(changes["QQQ"]):
        risk_score += changes["QQQ"] * 0.5
    if "BTC-USD" in changes and pd.notna(changes["BTC-USD"]):
        risk_score += changes["BTC-USD"] * 0.2
    
    if risk_score > 2:
        sentiment["risk_mode"] = "RISK ON"
        sentiment["signals"].append("📈 Risk-on: high-beta outperforming")
    elif risk_score < -2:
        sentiment["risk_mode"] = "RISK OFF"
        sentiment["signals"].append("📉 Risk-off: defensive positioning")
    
    # Final score
    sentiment["score"] = max(min(score, 100), -100)
    
    if score > 30:
        sentiment["overall"] = "BULLISH"
    elif score > 10:
        sentiment["overall"] = "MODERATELY BULLISH"
    elif score < -30:
        sentiment["overall"] = "BEARISH"
    elif score < -10:
        sentiment["overall"] = "MODERATELY BEARISH"
    
    return sentiment

def format_market_summary_telegram(market_data, sentiment):
    """Format compact market summary for Telegram."""
    prices, changes = market_data["prices"], market_data["changes"]
    labels = market_data["labels"]
    
    lines = []
    lines.append(hdr("🌍 GLOBAL MARKET SNAPSHOT"))
    
    # Overall sentiment
    emoji = {"BULLISH": "🟢", "MODERATELY BULLISH": "🟢", "NEUTRAL": "🟡",
             "MODERATELY BEARISH": "🔴", "BEARISH": "🔴"}.get(sentiment["overall"], "⚪")
    lines.append(f"{emoji} {sentiment['overall']} (Score: {sentiment['score']:.0f})")
    lines.append(f"📊 {sentiment['risk_mode']} | 🌪️ VIX: {sentiment['volatility']}")
    lines.append("")
    
    # Key prices
    tickers = ["SPY", "QQQ", "^VIX", "GC=F", "CL=F", "BTC-USD"]
    for t in tickers:
        if t in prices and pd.notna(prices[t]):
            chg = changes.get(t, 0)
            chg_str = f"{chg:+.1f}%" if pd.notna(chg) else "N/A"
            name = labels[t]
            val_fmt = f"${prices[t]:,.0f}" if t == "BTC-USD" else f"{prices[t]:.2f}"
            lines.append(f"{name}: {val_fmt} ({chg_str})")
    
    lines.append("")
    
    # Signals
    if sentiment["signals"]:
        lines.append("<b>🔔 Key Signals:</b>")
        for sig in sentiment["signals"][:5]:
            lines.append(f"• {sig}")
    
    return "\n".join(lines)

# ═══════════════════════════════════════════════════════════
#  Helpers — HTML formatting for mobile
# ═══════════════════════════════════════════════════════════
H = ParseMode.HTML  # shorter alias

def hdr(title):
    """Section header bar."""
    return f"<b>◈ {title}</b>\n{'━' * 30}"

def shdr(title):
    """Subsection header."""
    return f"\n<b>▸ {title}</b>"

def mono(text):
    """Monospaced block for tables."""
    return f"<pre>{text}</pre>"


def _pipe_table(header_cols, rows_data, right_cols=None, title=None):
    """NSE allinone-style table in <pre>.
    Format (matches Live PCR bot):
      header1     | header2  | header3
      ------------|----------|--------
      val1        | val2     | val3
    All text is html.escape()-safe (no HTML tags inside).
    right_cols = set of column indices to right-align."""
    import html as _html
    right_cols = right_cols or set()
    all_rows = [list(header_cols)] + [list(r) for r in rows_data]
    widths = [max(len(str(r[c])) for r in all_rows) for c in range(len(header_cols))]
    def _fmt(r):
        cells = [f"{str(r[c]):>{widths[c]}}" if c in right_cols
                 else f"{str(r[c]):<{widths[c]}}" for c in range(len(widths))]
        return " | ".join(cells)
    sep = "-+-".join("-" * w for w in widths)
    lines = []
    if title:
        lines.append(title)
    lines += [_fmt(header_cols), sep] + [_fmt(r) for r in rows_data]
    return "<pre>" + _html.escape("\n".join(lines)) + "</pre>"



_SHORT_CACHE: dict = {}
_SHORT_CACHE_TS: dict = {}

def _get_short_data(ticker: str) -> dict:
    """Fetch short interest & float data from yfinance info.
    Cached 4 hours. Returns: float_shares, shares_short, short_pct_float,
    short_ratio, shares_short_prior, squeeze_score (0-10), squeeze_label.
    """
    import time as _time
    tk = str(ticker).upper()
    now = _time.time()
    if tk in _SHORT_CACHE and now - _SHORT_CACHE_TS.get(tk, 0) < 14400:
        return _SHORT_CACHE[tk]
    empty = {"float_shares": None, "shares_short": None, "short_pct_float": None,
             "short_ratio": None, "shares_short_prior": None,
             "squeeze_score": None, "squeeze_label": "N/A"}
    try:
        info = yf.Ticker(tk).info
        float_s  = info.get("floatShares")
        ss       = info.get("sharesShort")
        spf      = info.get("shortPercentOfFloat")
        sr       = info.get("shortRatio")
        ss_prior = info.get("sharesShortPriorMonth")
        if spf is None and float_s and ss:
            spf = ss / float_s
        pct = (spf * 100 if spf and spf < 1 else spf) if spf is not None else None
        score = 0
        if pct is not None:
            if pct >= 30:    score += 4
            elif pct >= 20:  score += 3
            elif pct >= 10:  score += 2
            elif pct >= 5:   score += 1
        if sr is not None:
            if sr >= 10:     score += 3
            elif sr >= 5:    score += 2
            elif sr >= 3:    score += 1
        if ss and ss_prior and ss > ss_prior * 1.10:
            score += 2
        elif ss and ss_prior and ss < ss_prior * 0.90:
            score -= 1
        score = max(0, min(10, score))
        label = ("HIGH SQUEEZE RISK" if score >= 7
                 else "MODERATE" if score >= 4
                 else "LOW")
        result = {
            "float_shares":       float_s,
            "shares_short":       ss,
            "short_pct_float":    pct,
            "short_ratio":        sr,
            "shares_short_prior": ss_prior,
            "squeeze_score":      score,
            "squeeze_label":      label,
        }
        _SHORT_CACHE[tk] = result
        _SHORT_CACHE_TS[tk] = now
        return result
    except Exception:
        return empty


def _col_arrow(chg: float, strong: float = 0.5, weak: float = 0.1) -> str:
    """Return a colored-emoji arrow based on % change.
    🟢▲ / 🔴▼ / 🟡→ — strong threshold is 0.5%, weak is 0.1%.
    """
    if chg > strong:   return "🟢▲"
    if chg > weak:     return "🟡▲"
    if chg < -strong:  return "🔴▼"
    if chg < -weak:    return "🟡▼"
    return "🟡→"


def _cell_text(v, default="-"):
    """Normalize values for fixed-width table cells used in Telegram <pre> blocks."""
    if v is None:
        return default
    try:
        if isinstance(v, float) and np.isnan(v):
            return default
    except Exception:
        pass
    s = str(v)
    s = s.replace("\n", " ").replace("\r", " ").strip()
    return s if s else default


def _fit_cell(text, width):
    """Trim text to cell width so rows stay aligned on mobile."""
    s = _cell_text(text)
    if width <= 3:
        return s[:width]
    return s if len(s) <= width else (s[: width - 3] + "...")


def sanitize_html(s: str) -> str:
    """Strip unsupported HTML (e.g., <span> and inline styles) before sending to Telegram.

    Telegram only allows a small set of HTML tags; external content may include <span>
    or style attributes which cause parse errors. This function removes those.
    """
    if not isinstance(s, str):
        return s
    # Remove span tags entirely
    s = re.sub(r"<\s*span[^>]*>", "", s, flags=re.IGNORECASE)
    s = re.sub(r"<\s*/\s*span\s*>", "", s, flags=re.IGNORECASE)
    # Remove style and class attributes from remaining tags
    s = re.sub(r"\sstyle=(?:\"[^\"]*\"|'[^']*')", "", s, flags=re.IGNORECASE)
    s = re.sub(r"\sclass=(?:\"[^\"]*\"|'[^']*')", "", s, flags=re.IGNORECASE)
    return s

def row2(label, value, w=12, total=28):
    """Two-column row for mono block — max 28 chars to fit mobile without scroll."""
    left = max(6, min(int(w), total - 8))
    right = max(6, total - left - 1)
    ltxt = _fit_cell(label, left)
    vtxt = _fit_cell(value, right)
    return f"{ltxt:<{left}} {vtxt:>{right}}"

def row3(c1, c2, c3, w1=10, w2=11, w3=11):
    """Three-column row with deterministic width for Telegram monospace blocks."""
    t1 = _fit_cell(c1, w1)
    t2 = _fit_cell(c2, w2)
    t3 = _fit_cell(c3, w3)
    return f"{t1:<{w1}} {t2:>{w2}} {t3:>{w3}}"

def bar(pct, width=10):
    """Tiny progress bar from percentage 0-100."""
    filled = max(0, min(width, int(pct / 100 * width)))
    return '█' * filled + '░' * (width - filled)


DEFAULT_TICKERS = [
    "SPY", "QQQ", "AAPL", "MSFT", "NVDA", "AMZN", "GOOG", "META", "TSLA", "AMD", "AVGO", "NFLX",
]


def _ticker_universe(limit=500):
    """Build a broad ticker universe from DB + defaults for Telegram menus."""
    conn = get_conn()
    out = []
    try:
        q = """
            SELECT DISTINCT ticker FROM us_analytics_daily WHERE ticker IS NOT NULL
            UNION
            SELECT DISTINCT ticker FROM stock_daily WHERE ticker IS NOT NULL
            ORDER BY ticker
        """
        df = pd.read_sql(q, conn)
        out = [str(x).upper().strip() for x in df["ticker"].tolist() if str(x).strip()]
    except Exception:
        out = []
    finally:
        conn.close()

    merged = []
    seen = set()
    for t in DEFAULT_TICKERS + out:
        if not t or t in seen:
            continue
        seen.add(t)
        merged.append(t)
    return merged[:limit]


def _paged_ticker_keyboard(prefix, tickers, page=0, per_page=12, cols=3, include_back=True, back_cb="menu_main"):
    """Create a paginated ticker keyboard so selection is not limited to a fixed grid."""
    total = len(tickers)
    if total == 0:
        rows = [[BACK_BTN]] if include_back else []
        return InlineKeyboardMarkup(rows)

    max_page = max((total - 1) // per_page, 0)
    page = max(0, min(page, max_page))
    start = page * per_page
    end = min(start + per_page, total)
    page_tickers = tickers[start:end]

    rows = []
    for i in range(0, len(page_tickers), cols):
        chunk = page_tickers[i:i + cols]
        rows.append([InlineKeyboardButton(t, callback_data=f"{prefix}_{t}") for t in chunk])

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"{prefix}_page_{page - 1}"))
    nav.append(InlineKeyboardButton(f"{page + 1}/{max_page + 1}", callback_data="noop"))
    if page < max_page:
        nav.append(InlineKeyboardButton("Next ➡️", callback_data=f"{prefix}_page_{page + 1}"))
    rows.append(nav)

    if include_back:
        rows.append([InlineKeyboardButton("⬅️ Back", callback_data=back_cb), BACK_BTN])
    return InlineKeyboardMarkup(rows)

# ═══════════════════════════════════════════════════════════
#  MAIN MENU
# ═══════════════════════════════════════════════════════════
MAIN_MENU_KB = InlineKeyboardMarkup([
    # ── MARKETS ─────────────────────────────────────────────────
    [InlineKeyboardButton("━━  MARKETS  ━━━━━━━━━━━━━━━", callback_data="noop")],
    [InlineKeyboardButton("🌍 Overview",  callback_data="menu_market"),
     InlineKeyboardButton("📰 News",      callback_data="menu_news"),
     InlineKeyboardButton("⚡ Quote",     callback_data="menu_quote")],
    # ── MY PORTFOLIO ─────────────────────────────────────────────
    [InlineKeyboardButton("━━  PORTFOLIO  ━━━━━━━━━━━━━", callback_data="noop")],
    [InlineKeyboardButton("💼 Positions",  callback_data="menu_positions"),
     InlineKeyboardButton("📡 Monitor",   callback_data="menu_pos_monitor"),
     InlineKeyboardButton("📈 History",   callback_data="menu_closed_analytics")],
    [InlineKeyboardButton("⚠️ Risk Report", callback_data="menu_overnight_risk"),
     InlineKeyboardButton("🎯 Exit Plan",   callback_data="menu_exit")],
    # ── ANALYSIS ─────────────────────────────────────────────────
    [InlineKeyboardButton("━━  ANALYSIS  ━━━━━━━━━━━━━━", callback_data="noop")],
    [InlineKeyboardButton("🔥 Signals",    callback_data="menu_signals"),
     InlineKeyboardButton("📡 Analytics",  callback_data="menu_analytics"),
     InlineKeyboardButton("📊 OI",         callback_data="menu_oi")],
    [InlineKeyboardButton("📈 Insider",    callback_data="menu_insider"),
     InlineKeyboardButton("🧩 More",       callback_data="menu_more")],
    # ── MACRO & EVENTS ───────────────────────────────────────────
    [InlineKeyboardButton("━━  MACRO & EVENTS  ━━━━━━━━", callback_data="noop")],
    [InlineKeyboardButton("📡 Macro/Event Hub", callback_data="hub_menu"),
     InlineKeyboardButton("📰 Market Wrap", callback_data="wrap_view"),
     InlineKeyboardButton("📺 TradingView", callback_data="tv_view")],
    # ── AI + SETTINGS ────────────────────────────────────────────
    [InlineKeyboardButton("━━  AI & TOOLS  ━━━━━━━━━━━━", callback_data="noop")],
    [InlineKeyboardButton("🤖 Ask AI",     callback_data="menu_ai_chat"),
     InlineKeyboardButton("🔄 Refresh",   callback_data="menu_refresh")],
])

BACK_BTN = InlineKeyboardButton("⬅️ Menu", callback_data="menu_main")

MENU_TEXT = (
    f"{hdr('📊 RUDRARJUN Options Intelligence')}\n\n"
    "Tap any button below 👇"
)

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    # Ensure group_id column exists
    _ensure_group_id_column()
    await update.message.reply_text(sanitize_html(MENU_TEXT), parse_mode=H, reply_markup=MAIN_MENU_KB)

async def show_main_menu(query):
    await query.message.reply_text(sanitize_html(MENU_TEXT), parse_mode=H, reply_markup=MAIN_MENU_KB)

# ═══════════════════════════════════════════════════════════
#  1) MARKET OVERVIEW — grouped sections with mono tables
# ═══════════════════════════════════════════════════════════
MKT_GROUPS = {
    "📈 Indices": {"S&P 500": "^GSPC", "Nasdaq": "^IXIC", "Dow": "^DJI", "Russell": "^RUT"},
    "🔮 Futures": {"ES": "ES=F", "NQ": "NQ=F"},
    "⚡ Volatility": {"VIX": "^VIX"},
    "🏦 Commodities": {"Gold": "GC=F", "Oil": "CL=F"},
    "💰 Crypto/FX": {"Bitcoin": "BTC-USD", "EUR/USD": "EURUSD=X"},
    "📉 Bonds": {"10Y Yld": "^TNX"},
}

async def market_overview(query):
    _loading = await query.message.reply_text("⏳ Loading market data...", parse_mode=H)

    def _fetch(sym, is_yield=False):
        """Return dict with px, prev, chg, px_s, dir_s, st, em — or None on error."""
        try:
            h = yf.Ticker(sym).history(period="5d")
            if len(h) < 2:
                return None
            px   = float(h["Close"].iloc[-1])
            prev = float(h["Close"].iloc[-2])
            chg  = (px - prev) / prev * 100
            ar    = _col_arrow(chg)
            em    = "🟢" if chg > 0.5 else ("🔴" if chg < -0.5 else "🟡")
            if is_yield:   px_s = f"{px:.3f}"
            elif px > 999: px_s = f"{px:,.0f}"
            else:          px_s = f"{px:.2f}"
            return dict(px=px, chg=chg, px_s=px_s, ar=ar, em=em)
        except Exception:
            return None

    SPECS = [
        # (section, display_name, yf_symbol, is_yield, extra_tag_fn)
        ("INDICES",     "SPX",     "^GSPC",    False, None),
        ("INDICES",     "NDX",     "^IXIC",    False, None),
        ("INDICES",     "DOW",     "^DJI",     False, None),
        ("INDICES",     "RUT",     "^RUT",     False, None),
        ("FUTURES",     "ES",      "ES=F",     False, None),
        ("FUTURES",     "NQ",      "NQ=F",     False, None),
        ("VOLATILITY",  "VIX",     "^VIX",     False,
         lambda px: "EXTREME FEAR" if px>30 else ("HIGH FEAR" if px>25 else ("ELEVATED" if px>20 else "CALM"))),
        ("COMMODITIES", "Gold",    "GC=F",     False, None),
        ("COMMODITIES", "Oil",     "CL=F",     False, None),
        ("CRYPTO/FX",   "BTC",     "BTC-USD",  False, None),
        ("CRYPTO/FX",   "EUR/USD", "EURUSD=X", False, None),
        ("BONDS",       "10Y Yld", "^TNX",     True,  None),
    ]

    all_rows = []   # (section, name, d) — d is _fetch result or None
    for sec, name, sym, is_yld, tag_fn in SPECS:
        d = _fetch(sym, is_yld)
        tag = tag_fn(d["px"]) if (d and tag_fn) else ""
        all_rows.append((sec, name, d, tag))

    # ── Aligned <pre> tables per section ──
    from collections import defaultdict
    sections = defaultdict(list)
    for _sec, name, d, tag in all_rows:
        sections[_sec].append((name, d, tag))

    colour_lines = []
    for sec_name, items in sections.items():
        colour_lines.append(shdr(sec_name))
        _sec_rows = []
        for name, d, tag in items:
            if d:
                arrow = _col_arrow(d["chg"])
                note = tag if tag else "-"
                _sec_rows.append((name, d["px_s"], f"{d['chg']:>+.2f}%", arrow, note))
            else:
                _sec_rows.append((name, "N/A", "-", "-", "-"))
        colour_lines.append(_pipe_table(("Name", "Price", "Chg%", "Dir", "Signal"),
                                        _sec_rows, right_cols={1, 2}))
    colour_block = "\n".join(colour_lines)

    sp500_chart_bytes = None
    try:
        sp500_chart_bytes = make_mini_chart("^GSPC", days=7)
    except Exception:
        pass

    _pulled_ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔄 Refresh", callback_data="menu_market"), BACK_BTN]])
    await query.message.reply_text(
        hdr("🌍 MARKET OVERVIEW") + "\n" + colour_block
        + f"\n\n<i>🕐 {_pulled_ts}</i>",
        parse_mode=H, reply_markup=kb)
    if sp500_chart_bytes:
        try:
            await query.message.reply_photo(sp500_chart_bytes, caption="S&P 500 — 7d mini chart")
        except Exception as e:
            log.warning(f"Failed to send mini chart: {e}")
    try: await _loading.delete()
    except Exception: pass

# ═══════════════════════════════════════════════════════════
#  2) NEWS & SENTIMENT
# ═══════════════════════════════════════════════════════════
WATCHLIST_TICKERS = ["GOOG", "AMZN", "MSFT", "NVDA", "AAPL", "META", "TSLA"]

async def news_menu(query):
    # Trending tickers (simulate with top 3 from watchlist for now)
    trending = WATCHLIST_TICKERS[:3]
    btns = [[InlineKeyboardButton(f"🔥 {t}", callback_data=f"news_{t}") for t in trending]]
    # Search bar (simulate with a button for now)
    btns.append([InlineKeyboardButton("🔍 Search Ticker", callback_data="news_search")])
    # All watchlist tickers
    for i in range(0, len(WATCHLIST_TICKERS), 3):
        row = [InlineKeyboardButton(t, callback_data=f"news_{t}") for t in WATCHLIST_TICKERS[i:i+3]]
        btns.append(row)
    btns.append([InlineKeyboardButton("📰 All Headlines", callback_data="news_ALL")])
    btns.append([BACK_BTN])
    await query.message.reply_text(
        f"{hdr('📰 NEWS & SENTIMENT')}\n\n"
        "<b>Trending:</b> " + ", ".join([f"<b>{t}</b>" for t in trending]) + "\n"
        "Search or tap a ticker for news.",
        parse_mode=H, reply_markup=InlineKeyboardMarkup(btns),
    )

async def news_for_ticker(query, ticker):
    _loading = await query.message.reply_text(f"⏳ Fetching {ticker} news...", parse_mode=H)
    import feedparser
    import html as html_mod
    _neg = ["drop","fall","crash","sell","bear","down","loss","cut","tariff","fear","decline","recession",
            "weak","plunge","tumble","sink","concern","risk","threat","crisis","layoff"]
    _pos = ["rally","surge","bull","up","gain","beat","strong","rise","high","buy","upgrade",
            "record","boost","growth","profit","optimis"]
    bull_c, bear_c = 0, 0
    news_lines = []
    try:
        feed = feedparser.parse(f"https://feeds.finance.yahoo.com/rss/2.0/headline?s={ticker}&region=US&lang=en-US")
        import time as _time
        for entry in feed.entries[:8]:
            title = html_mod.escape(entry.get("title", "")).strip()
            if not title:
                continue
            link  = entry.get("link", "").strip()
            pub_p = entry.get("published_parsed", None)
            dt_str = (_time.strftime("%d%b %H:%M", pub_p).lstrip("0") if pub_p else "")
            tl = title.lower()
            if any(w in tl for w in _neg):
                tag = "🔴"; bear_c += 1
            elif any(w in tl for w in _pos):
                tag = "🟢"; bull_c += 1
            else:
                tag = "🟡"
            short = title[:90] + ("…" if len(title) > 90 else "")
            if link:
                line = f'{tag} <a href="{link}">{short}</a>'
            else:
                line = f"{tag} {short}"
            if dt_str:
                line += f"  <i>· {dt_str}</i>"
            news_lines.append(line)
    except Exception:
        news_lines.append("Could not fetch news")


    # Determine sentiment tone based on counts
    tone = "<b>BEARISH 🔴</b>" if bear_c > bull_c + 1 else \
        "<b>BULLISH 🟢</b>" if bull_c > bear_c + 1 else \
        "<b>MIXED 🟡</b>" if bull_c + bear_c > 0 else \
        "<b>NEUTRAL ⚪</b>"

    parts = [hdr(f"📰 {ticker} NEWS")]
    parts.append("")
    parts.extend(news_lines)
    score_bar = bar(bull_c / max(bull_c + bear_c, 1) * 100)
    parts.append(mono(
        f"Bull {bull_c} {score_bar} Bear {bear_c}"
    ))
    parts.append(f"Sentiment: {tone}")

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Refresh", callback_data=f"news_{ticker}"),
         InlineKeyboardButton("📰 Other", callback_data="menu_news"), BACK_BTN]
    ])
    await _safe_reply(query.message, sanitize_html("\n".join(parts)), reply_markup=kb,
                                   disable_web_page_preview=True)
    try: await _loading.delete()
    except Exception: pass

async def market_headlines(query):
    """Fetch broad market headlines from multiple tickers and display deduplicated."""
    _loading = await query.message.reply_text("⏳ Fetching market headlines...", parse_mode=H)
    import feedparser
    import html as html_mod
    _neg_kw = ["drop","fall","crash","sell","bear","down","loss","cut","tariff","fear","decline",
               "recession","warn","slump","plunge","sink","tumble","worry","concern","weak","crisis"]
    _pos_kw = ["rise","gain","rally","bull","up","beat","surge","strong","record","high","boost",
               "upgrade","growth","jump","soar","buy","bullish","profit","beat"]

    seen_keys: set = set()
    all_items = []
    market_feeds = ["SPY", "QQQ", "^VIX", "^TNX", "AAPL", "NVDA", "TSLA", "AMZN", "MSFT"]
    for sym in market_feeds:
        try:
            feed = feedparser.parse(
                f"https://feeds.finance.yahoo.com/rss/2.0/headline?s={sym}&region=US&lang=en-US")
            for entry in feed.entries[:5]:
                title = html_mod.unescape(entry.get("title", "")).strip()
                if not title or len(title) < 20:
                    continue
                key = title[:55].lower()
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                import time as _time
                link  = entry.get("link", "").strip()
                pub_p = entry.get("published_parsed", None)
                dt_str = (_time.strftime("%d%b %H:%M", pub_p).lstrip("0") if pub_p else "")
                tl = title.lower()
                if any(k in tl for k in _neg_kw):
                    tag = "🔴"
                elif any(k in tl for k in _pos_kw):
                    tag = "🟢"
                else:
                    tag = "🟡"
                all_items.append((tag, title, link, dt_str))
        except Exception:
            continue
        if len(all_items) >= 15:
            break

    parts = [hdr("📰 MARKET HEADLINES")]
    if all_items:
        for tag, title, link, dt_str in all_items[:12]:
            short = html_mod.escape(title[:90] + ("…" if len(title) > 90 else ""))
            if link:
                line = f'{tag} <a href="{link}">{short}</a>'
            else:
                line = f"{tag} {short}"
            if dt_str:
                line += f"  <i>· {dt_str}</i>"
            parts.append(line)
    else:
        parts.append("Could not fetch market headlines.")

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Refresh", callback_data="news_ALL"),
         InlineKeyboardButton("📰 By Ticker", callback_data="menu_news"), BACK_BTN]
    ])
    try: await _loading.delete()
    except Exception: pass
    await _safe_reply(query.message, 
        "\n".join(parts), reply_markup=kb, disable_web_page_preview=True)


# ═══════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════
#  3) EXIT PLANNER (MC simulation)
# ═══════════════════════════════════════════════════════════
async def exit_planner_menu(query):
    """Show mode selection: Individual / One Stock / All Positions"""
    conn = get_conn()
    open_trades = pd.read_sql("SELECT * FROM trades WHERE status='OPEN'", conn)
    conn.close()

    if open_trades.empty:
        tickers = ["GOOG", "AMZN", "MSFT", "NVDA", "AAPL", "TSLA"]
        _def_exp = (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d")
        btns = [[InlineKeyboardButton(f"🎯 {t}", callback_data=f"exitmc|{t}|call|0|0|{_def_exp}") for t in tickers[:3]]]
        btns.append([InlineKeyboardButton(f"🎯 {t}", callback_data=f"exitmc|{t}|call|0|0|{_def_exp}") for t in tickers[3:]])
        btns.append([BACK_BTN])
        await query.message.reply_text(
            hdr("🎯 EXIT PLANNER") + "\n\nNo open positions.\nPick a ticker for quick analysis:",
            parse_mode=H, reply_markup=InlineKeyboardMarkup(btns),
        )
        return

    n_pos = len(open_trades)
    tickers_open = sorted(open_trades["ticker"].unique())
    n_tk = len(tickers_open)

    btns = [
        [InlineKeyboardButton("🎯 Individual Position Analysis", callback_data="exit_mode_indiv")],
        [InlineKeyboardButton("🏢 One Stock — All Positions", callback_data="exit_mode_stock")],
        [InlineKeyboardButton("📊 All Positions Portfolio Report", callback_data="exit_batch_all")],
        [BACK_BTN],
    ]
    await query.message.reply_text(
        hdr("🎯 EXIT PLANNER") + f"\n\n{n_pos} position(s) across {n_tk} stock(s)\n\nChoose analysis type:",
        parse_mode=H, reply_markup=InlineKeyboardMarkup(btns),
    )


async def exit_mode_indiv(query):
    """Show individual position list."""
    conn = get_conn()
    open_trades = pd.read_sql("SELECT * FROM trades WHERE status='OPEN'", conn)
    conn.close()
    if open_trades.empty:
        await query.message.reply_text("No open positions.", reply_markup=InlineKeyboardMarkup([[BACK_BTN]]))
        return
    btns = []
    for _, tr in open_trades.iterrows():
        tk = str(tr["ticker"]).upper()
        ot = str(tr["option_type"]).lower()
        st = float(tr["strike"])
        ep = float(tr["entry_price"])
        ex = str(tr["expiry"])
        qty = int(tr.get("quantity", 1) or 1)
        side_s = "S" if qty < 0 else "B"
        label = f"🎯 {tk} {ot.upper()} ${st:.0f} [{side_s}] entry ${ep:.2f}"
        btns.append([InlineKeyboardButton(label, callback_data=f"exitmc|{tk}|{ot}|{st}|{ep}|{ex}|{qty}")])
    btns.append([InlineKeyboardButton("⬅️ Mode Select", callback_data="menu_exit"), BACK_BTN])
    await query.message.reply_text(
        hdr("🎯 INDIVIDUAL ANALYSIS") + "\n\nSelect a position:",
        parse_mode=H, reply_markup=InlineKeyboardMarkup(btns),
    )


async def exit_mode_stock(query):
    """Show ticker selection for one-stock summary."""
    conn = get_conn()
    open_trades = pd.read_sql("SELECT * FROM trades WHERE status='OPEN'", conn)
    conn.close()
    if open_trades.empty:
        await query.message.reply_text("No open positions.", reply_markup=InlineKeyboardMarkup([[BACK_BTN]]))
        return
    tickers_open = sorted(open_trades["ticker"].unique())
    btns = []
    for i in range(0, len(tickers_open), 3):
        row = [InlineKeyboardButton(f"🏢 {t}", callback_data=f"exit_batch_tk|{t}") for t in tickers_open[i:i+3]]
        btns.append(row)
    btns.append([InlineKeyboardButton("⬅️ Mode Select", callback_data="menu_exit"), BACK_BTN])
    await query.message.reply_text(
        hdr("🏢 ONE STOCK SUMMARY") + "\n\nSelect stock:",
        parse_mode=H, reply_markup=InlineKeyboardMarkup(btns),
    )

async def run_exit_analysis(query, ticker, opt_type, strike, entry, expiry_str, qty=1):
    _loading = await query.message.reply_text(f"⏳ Running MC simulation for {ticker} {opt_type.upper()} ${strike:.0f}...",
                                   parse_mode=H)
    try:
        expiry = datetime.strptime(expiry_str, "%Y-%m-%d").date()
    except Exception:
        expiry = (datetime.now() + timedelta(days=20)).date()

    # Fetch price data
    tk_obj = yf.Ticker(ticker)
    hist = tk_obj.history(period="3mo")
    if len(hist) < 2:
        await query.message.reply_text(f"❌ Could not fetch data for {ticker}", reply_markup=InlineKeyboardMarkup([[BACK_BTN]]))
        try: await _loading.delete()
        except Exception: pass
        return
    spot = float(hist["Close"].iloc[-1])
    prev = float(hist["Close"].iloc[-2])
    closes = hist["Close"].dropna().values
    hist_returns = np.diff(np.log(closes))
    hv = float(np.std(hist_returns)) * np.sqrt(252) if len(hist_returns) >= 20 else 0.25
    day_chg = (spot - prev) / prev * 100

    # IV — try live, fallback to VIX-derived
    iv = 0.30
    iv_src = "Default"
    iv_raw = 0
    try:
        chain = tk_obj.option_chain(expiry.strftime("%Y-%m-%d"))
        oc = chain.puts if opt_type == "put" else chain.calls
        m = oc[oc["strike"] == float(strike)]
        if not m.empty:
            fiv = float(m.iloc[0].get("impliedVolatility", 0))
            if fiv >= 0.05:
                iv = fiv
                iv_src = f"Live {iv:.0%}"
            else:
                iv_raw = fiv
    except Exception:
        pass

    # VIX-derived fallback if IV is garbage
    vix_val = 20.0
    vix_pct = 0.0
    try:
        vix_h = yf.Ticker("^VIX").history(period="5d")
        if len(vix_h) >= 2:
            vix_val = float(vix_h["Close"].iloc[-1])
            vix_pct = (vix_val - float(vix_h["Close"].iloc[-2])) / float(vix_h["Close"].iloc[-2]) * 100
    except Exception:
        pass

    if iv_src == "Default" or (iv_raw > 0 and iv_raw < 0.05):
        vix_iv = vix_val / 100.0 * 1.3
        iv = max(vix_iv, hv, 0.15)
        iv_src = f"VIX-derived {iv:.0%}"

    # Futures
    es_pct, nq_pct = 0.0, 0.0
    try:
        for sym, label in [("ES=F", "ES"), ("NQ=F", "NQ")]:
            fh = yf.Ticker(sym).history(period="5d")
            if len(fh) >= 2:
                pct = (float(fh["Close"].iloc[-1]) - float(fh["Close"].iloc[-2])) / float(fh["Close"].iloc[-2]) * 100
                if label == "ES": es_pct = pct
                else: nq_pct = pct
    except Exception:
        pass
    predicted_gap = (es_pct + nq_pct) / 2

    # MC Simulation
    dte = max((datetime.combine(expiry, datetime.min.time()) - datetime.now()).days, 1)
    T_tomorrow = max(dte - 1, 1) / 365.0
    n_sims = 10000

    # Vol calibration
    mc_vix_vol = vix_val / 100.0 * 1.3 if vix_val > 15 else 0
    if mc_vix_vol > 0:
        mc_vol = 0.4 * iv + 0.3 * hv + 0.3 * mc_vix_vol
    else:
        mc_vol = 0.6 * iv + 0.4 * hv
    if vix_pct > 10 and mc_vix_vol > mc_vol:
        mc_vol = max(mc_vol, mc_vix_vol * 0.85)
    mc_vol = max(mc_vol, 0.15)

    # Drift
    futures_drift = predicted_gap / 100.0
    overnight_drift = futures_drift - 0.001  # slight negative bias

    # Simulate
    dt = 1.0 / 252.0
    np.random.seed(42)
    Z = np.random.standard_normal(n_sims)
    sim_returns = overnight_drift + (-0.5 * mc_vol**2 * dt) + mc_vol * np.sqrt(dt) * Z
    sim_prices = spot * np.exp(sim_returns)

    # IV for pricing
    iv_base = iv
    if vix_val > 20:
        iv_base = max(iv_base, vix_val / 100.0 * 1.2)
    iv_vix_adj = 0.02 + (0.03 if abs(predicted_gap) > 1 else 0)
    if vix_pct > 10:
        iv_vix_adj += 0.05 + max(0, (vix_pct - 10) * 0.002)
    sim_ivs = np.clip(iv_base + iv_vix_adj + np.random.normal(0, 0.03, n_sims), 0.05, 2.0)

    # Fully vectorized Black-Scholes (no Python loop — ~200x faster)
    r = 0.045
    K = float(strike)
    sqrt_T = np.sqrt(max(T_tomorrow, 1e-6))
    _d1 = (np.log(sim_prices / K) + (r + 0.5 * sim_ivs**2) * T_tomorrow) / (sim_ivs * sqrt_T)
    _d2 = _d1 - sim_ivs * sqrt_T
    if opt_type == "put":
        option_vals = K * np.exp(-r * T_tomorrow) * norm.cdf(-_d2) - sim_prices * norm.cdf(-_d1)
    else:
        option_vals = sim_prices * norm.cdf(_d1) - K * np.exp(-r * T_tomorrow) * norm.cdf(_d2)
    option_vals = np.maximum(option_vals, 0.0)

    # Aggregate MC statistics
    exp_stock = float(np.mean(sim_prices)) if len(sim_prices) else spot
    exp_val = float(np.mean(option_vals)) if len(option_vals) else 0.0
    p10 = float(np.percentile(option_vals, 10)) if len(option_vals) else 0.0
    p90 = float(np.percentile(option_vals, 90)) if len(option_vals) else 0.0

    pos_sign = -1 if qty < 0 else 1
    pnl_array = (option_vals - float(entry)) * 100.0 * pos_sign
    exp_pnl = float(np.mean(pnl_array)) if len(pnl_array) else 0.0
    prob_profit = float(np.mean(option_vals < float(entry)) * 100.0) if (len(option_vals) and qty < 0) else float(np.mean(option_vals > float(entry)) * 100.0) if len(option_vals) else 0.0
    var_95 = float(np.percentile(pnl_array, 5)) if len(pnl_array) else 0.0

    # Current theoretical value and greeks (use full remaining T)
    T_now = max(dte, 1) / 365.0
    cur_val = bs_price(spot, K, T_now, r, iv, opt=opt_type)
    greeks = bs_greeks(spot, K, T_now, r, iv, opt=opt_type)

    # Recommendation with color (flip sign for short positions)
    pos_sign = -1 if qty < 0 else 1
    pnl_pct = (exp_val - float(entry)) / float(entry) * 100 * pos_sign if float(entry) > 0 else 0
    # Tomorrow's P&L vs today's current value (not vs entry)
    tmrw_pnl_vs_today = (exp_val - cur_val) * 100.0 * pos_sign
    tmrw_pct_vs_today = (exp_val - cur_val) / cur_val * 100 * pos_sign if cur_val > 0 else 0

    if qty < 0:
        # SHORT: profit when option value drops; target = buy back at 50% of sold price
        target_price = float(entry) * 0.5
        if prob_profit > 55 and pnl_pct > 10:
            rec = "<b>🟢 BUY TO CLOSE — Take Profit</b>"
            rec_detail = f"MC: {prob_profit:.0f}% profit chance. Target close ≤ ${target_price:.2f} (50% of entry)"
        elif prob_profit > 55:
            rec = "<b>🟡 HOLD — Profit likely, let decay work</b>"
            rec_detail = f"MC: {prob_profit:.0f}% profit chance. Theta is your friend — hold."
        elif prob_profit > 40:
            rec = "<b>🟠 SET STOP — Risk is rising</b>"
            rec_detail = f"MC: {prob_profit:.0f}% profit. Stop-buy at ${float(entry)*1.5:.2f} (cut if option spikes)"
        else:
            rec = "<b>🔴 BUY TO CLOSE — Exit Now</b>"
            rec_detail = f"MC: only {prob_profit:.0f}% profit. Option may spike against you. Close now."
    else:
        # LONG: profit when option value rises
        target_price = float(entry) * 1.3
        if prob_profit > 55 and pnl_pct > 10:
            rec = "<b>🟢 SET LIMIT SELL — Take Profit</b>"
            rec_detail = f"MC: {prob_profit:.0f}% profit chance. Target sell ≥ ${target_price:.2f} (+30%)"
        elif prob_profit > 55:
            rec = "<b>🟡 HOLD WITH STOP</b>"
            rec_detail = f"MC: {prob_profit:.0f}% profit chance. Stop at ${float(entry)*0.80:.2f}"
        elif prob_profit > 40:
            rec = "<b>🟠 TIGHT STOP-LOSS</b>"
            rec_detail = f"MC: {prob_profit:.0f}% profit. Stop at ${float(entry)*0.80:.2f}, VaR: ${var_95:+,.0f}"
        else:
            rec = "<b>🔴 EXIT AT OPEN</b>"
            rec_detail = f"MC: only {prob_profit:.0f}% profit. Expected loss ${exp_pnl:+,.0f}. Cut losses."

    # Build message — structured HTML
    pnl_emoji = "🟢" if exp_pnl >= 0 else "🔴"
    tmrw_emoji = "🟢" if tmrw_pnl_vs_today >= 0 else "🔴"
    profit_bar = bar(prob_profit)

    side_label = "SHORT (Sold)" if qty < 0 else "LONG (Bought)"
    msg = (
        f"{hdr(f'🎯 {ticker} {opt_type.upper()} ${K:.0f} · {side_label}')}\n\n"
        f"📊 <b>Market Snapshot</b>\n"
        + mono(
            f"{row2(ticker, f'${spot:.2f} ({day_chg:+.2f}%)')}\n"
            f"{row2('VIX', f'{vix_val:.1f} ({vix_pct:+.1f}%)')}\n"
            f"{row2('ES / NQ', f'{es_pct:+.2f}% / {nq_pct:+.2f}%')}\n"
            f"{row2('Gap Est.', f'{predicted_gap:+.2f}%')}"
        )
        + "\n📖 <b>Parameters</b>\n"
        + mono(
            f"{row2('Strike', f'${K:.0f}')}\n"
            f"{row2('DTE', f'{dte} days')}\n"
            f"{row2('IV Source', iv_src)}\n"
            f"{row2('Entry', f'${entry:.2f}')}\n"
            f"{row2('Now (Theo)', f'${cur_val:.2f}')}\n"
            f"{row2('MC Vol', f'{mc_vol:.0%}')}"
        )
        + "\n🎲 <b>Monte Carlo · 10K Sims</b>\n"
        + mono(
            f"{row2('Exp. Stock', f'${exp_stock:.2f}')}\n"
            f"{row2('Exp. Option', f'${exp_val:.2f}')}\n"
            f"{row2('Range', f'${p10:.2f} – ${p90:.2f}')}\n"
            f"{'─' * 27}\n"
            f"{pnl_emoji} {row2('P&L vs Entry', f'${exp_pnl:+,.0f} ({pnl_pct:+.0f}%)')}\n"
            f"{tmrw_emoji} {row2('P&L Tomorrow', f'${tmrw_pnl_vs_today:+,.0f} ({tmrw_pct_vs_today:+.0f}%)')}\n"
            f"{row2('P(Profit)', f'{prob_profit:.0f}%  {profit_bar}')}\n"
            f"{row2('VaR 95%', f'${var_95:+,.0f}')}"
        )
        + "\n📊 <b>Greeks (Current)</b>\n"
        + mono(
            row2('Theo Value', f'${cur_val:.2f}') + "\n"
            + row2('Delta', f'{greeks.get("delta", 0):.3f}') + "\n"
            + row2('Theta', f'-${abs(greeks.get("theta", 0))*100:.2f}/day') + "\n"
            + row2('Vega', f'${greeks.get("vega", 0)*100:.2f}')
        )
        + f"\n💡 <b>Recommendation</b>\n{rec}\n{rec_detail}\n"
        + f"\n<i>Updated {datetime.now().strftime('%H:%M:%S')}</i>"
    )

    cb_data = f"exitmc|{ticker}|{opt_type}|{strike}|{entry}|{expiry_str}|{qty}"
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Refresh", callback_data=cb_data)],
        [InlineKeyboardButton("📊 Scenarios", callback_data=f"scenarios|{ticker}|{opt_type}|{strike}|{entry}|{expiry_str}|{qty}")],
        [InlineKeyboardButton("🎯 Exit Planner", callback_data="menu_exit"), BACK_BTN],
    ])
    await _safe_reply(query.message, msg, reply_markup=kb)
    try: await _loading.delete()
    except Exception: pass

async def show_scenarios(query, ticker, opt_type, strike, entry, expiry_str, qty=1):
    """Show price scenario table"""
    try:
        expiry = datetime.strptime(expiry_str, "%Y-%m-%d").date()
    except Exception:
        expiry = (datetime.now() + timedelta(days=20)).date()

    tk_obj = yf.Ticker(ticker)
    hist = tk_obj.history(period="5d")
    spot = float(hist["Close"].iloc[-1]) if len(hist) >= 1 else 300.0
    dte = max((datetime.combine(expiry, datetime.min.time()) - datetime.now()).days, 1)
    T = max(dte - 1, 1) / 365.0

    # Get IV
    iv = 0.35
    try:
        vix_h = yf.Ticker("^VIX").history(period="5d")
        vix_val = float(vix_h["Close"].iloc[-1]) if len(vix_h) >= 1 else 20
        iv = max(vix_val / 100 * 1.3, 0.20)
    except Exception:
        pass

    K = float(strike)
    r = 0.045
    moves = [-3, -2, -1, -0.5, 0, 0.5, 1, 2, 3]

    # Build mono table

    tbl_rows = [f"{'Move':>6}  {'Stock':>5}  {'Val':>5}  {'P&L':>7}"]
    tbl_rows.append("─" * 30)
    pos_sign = -1 if qty < 0 else 1
    for mv in moves:
        s = spot * (1 + mv / 100)
        val = bs_price(s, K, T, r, iv, opt_type)
        pnl = (val - entry) * 100 * pos_sign
        sign_s = "+" if pnl >= 0 else "-"
        tbl_rows.append(f"{mv:>+5.1f}%  ${s:>5.0f}  ${val:>4.2f}  {sign_s}${abs(pnl):>5.0f}")

    side_lbl = "SHORT" if qty < 0 else "LONG"
    parts = [
        hdr(f"📊 SCENARIOS · {ticker} {opt_type.upper()} ${K:.0f} [{side_lbl}]"),
        mono(
            f"Spot: ${spot:.2f}  DTE: {dte}  IV: {iv:.0%}\n\n"
            + "\n".join(tbl_rows)
        ),
    ]

    cb_data = f"exitmc|{ticker}|{opt_type}|{strike}|{entry}|{expiry_str}|{qty}"
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("⬅️ Analysis", callback_data=cb_data), BACK_BTN]
    ])
    await _safe_reply(query.message, "\n".join(parts), reply_markup=kb)

# ═══════════════════════════════════════════════════════════
#  4) MY POSITIONS — card-style per trade
# ═══════════════════════════════════════════════════════════
async def positions_view(query):
    _close_expired_positions()
    conn = get_conn()
    trades = pd.read_sql(
        "SELECT * FROM trades WHERE status='OPEN' ORDER BY ticker, created_at DESC LIMIT 50", conn)
    conn.close()

    if trades.empty:
        await query.message.reply_text(
            hdr('💼 OPEN POSITIONS') + '\n\nNo open positions found.',
            parse_mode=H, reply_markup=InlineKeyboardMarkup([[BACK_BTN]]))
        return

    tickers_order = list(dict.fromkeys(trades['ticker'].astype(str).tolist()))
    parts = [hdr(f'💼 OPEN POSITIONS  ({len(trades)} legs / {len(tickers_order)} stocks)')]

    for tk in tickers_order:
        grp     = trades[trades['ticker'].astype(str) == tk]
        n_legs  = len(grp)
        n_calls = int((grp['option_type'].str.upper() == 'CALL').sum())
        n_puts  = int((grp['option_type'].str.upper() == 'PUT').sum())
        n_long  = int((grp['quantity'].fillna(0).astype(float) > 0).sum())
        n_short = int((grp['quantity'].fillna(0).astype(float) < 0).sum())
        exp_vals = sorted(grp['expiry'].dropna().astype(str).tolist())
        next_exp = exp_vals[0][:10] if exp_vals else '?'

        _leg_tbl_rows = []
        for _, tr in grp.iterrows():
            tid  = _safe_int(tr.get('trade_id', 0), 0)
            ot   = str(tr.get('option_type', '?'))[:4].upper()
            st   = _safe_float(tr.get('strike', 0), 0)
            ep   = _safe_float(tr.get('entry_price', 0), 0)
            qty  = _safe_int(tr.get('quantity', 0), 0)
            exp  = str(tr.get('expiry', ''))[:10]
            side = 'L' if qty >= 0 else 'S'
            _leg_tbl_rows.append((f'#{tid}', side, ot, f'${st:.0f}', f'${ep:.2f}', exp))
        s_mark = 's' if n_legs > 1 else ''
        parts.append(
            chr(10) + f"<b>{tk}</b>  {n_legs} leg{s_mark}"
            + f"  ({n_calls}C / {n_puts}P  •  {n_long}L / {n_short}S)" + chr(10)
            + f"Next exp: {next_exp}" + chr(10)
            + _pipe_table(("#", "Side", "Type", "Strk", "Entry", "Expiry"),
                          _leg_tbl_rows, right_cols={3, 4})
        )

    btn_rows = []
    for tk in tickers_order:
        btn_rows.append([
            InlineKeyboardButton(f'📊 {tk} Exit Plan', callback_data=f'exit_batch_tk|{tk}'),
            InlineKeyboardButton(f'🌙 {tk} AH Pred',   callback_data=f'ah_pred_tk|{tk}'),
        ])

    leg_btns = []
    for _, tr in trades.head(6).iterrows():
        tid_ = _safe_int(tr.get('trade_id', 0), 0)
        tk_  = str(tr.get('ticker', '?'))
        ot_  = str(tr.get('option_type', '?')).upper()
        st_  = _safe_float(tr.get('strike', 0), 0)
        leg_btns.append(InlineKeyboardButton(f'#{tid_} {tk_} {ot_[:3]} ${st_:.0f}', callback_data=f'pos_{tid_}'))
    for i in range(0, len(leg_btns), 2):
        btn_rows.append(leg_btns[i:i+2])

    btn_rows.append([InlineKeyboardButton('📊 All Positions Exit Plan', callback_data='exit_batch_all'),
                     InlineKeyboardButton('🌙 All AH Predictor',        callback_data='menu_aftermarket_predict')])
    btn_rows.append([InlineKeyboardButton('➕ Add Position', callback_data='posadd_start'),
                     InlineKeyboardButton('📦 Groups',       callback_data='menu_groups')])
    btn_rows.append([InlineKeyboardButton('🎨 Strategy Builder', callback_data='menu_strategy_builder'),
                     InlineKeyboardButton('🤖 MiroFish',         callback_data='menu_mirofish')])
    btn_rows.append([InlineKeyboardButton('🔄 Refresh', callback_data='menu_positions'), BACK_BTN])

    await query.message.reply_text('\n'.join(parts), parse_mode=H,
                                   reply_markup=InlineKeyboardMarkup(btn_rows))


async def position_detail(query, trade_id, notice=None):
    tr = _fetch_trade(trade_id)
    if not tr:
        await query.message.reply_text("❌ Position not found.", reply_markup=InlineKeyboardMarkup([[BACK_BTN]]))
        return

    tid = _safe_int(tr.get("trade_id", 0), 0)
    tk = str(tr.get("ticker", "?"))
    ot = str(tr.get("option_type", "?")).upper()
    st = _safe_float(tr.get("strike", 0), 0)
    ep = _safe_float(tr.get("entry_price", 0), 0)
    qty = _safe_int(tr.get("quantity", 0), 0)
    exp = str(tr.get("expiry", "?"))
    status = str(tr.get("status", "?"))
    acct = str(tr.get("account_type", ""))
    note = str(tr.get("notes", "") or "")[:60]

    side_lbl = "SELL" if qty < 0 else "BUY"
    msg = [hdr(f"🛠 POSITION #{tid}")]
    if notice:
        msg.append(f"\n{notice}")
    msg.append(
        mono(
            f"{row2('Ticker', tk)}\n"
            f"{row2('Side', side_lbl)}\n"
            f"{row2('Type', ot)}\n"
            f"{row2('Strike', f'${st:.2f}')}\n"
            f"{row2('Expiry', exp)}\n"
            f"{row2('Entry', f'${ep:.2f}')}\n"
            f"{row2('Qty', str(qty))}\n"
            f"{row2('Status', status)}\n"
            f"{row2('Account', acct)}\n"
            f"{row2('Notes', note if note else '—')}"
        )
    )

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("Qty -1", callback_data=f"posedit_{tid}_qty_m1"),
         InlineKeyboardButton("Qty +1", callback_data=f"posedit_{tid}_qty_p1")],
        [InlineKeyboardButton("Entry -0.10", callback_data=f"posedit_{tid}_ent_m"),
         InlineKeyboardButton("Entry +0.10", callback_data=f"posedit_{tid}_ent_p")],
        [InlineKeyboardButton("Strike -5", callback_data=f"posedit_{tid}_stk_m5"),
         InlineKeyboardButton("Strike +5", callback_data=f"posedit_{tid}_stk_p5")],
        [InlineKeyboardButton("Expiry -7d", callback_data=f"posedit_{tid}_exp_m7"),
         InlineKeyboardButton("Expiry +7d", callback_data=f"posedit_{tid}_exp_p7")],
        [InlineKeyboardButton("Toggle CALL/PUT", callback_data=f"postog_{tid}"),
         InlineKeyboardButton("Toggle BUY/SELL", callback_data=f"postogside_{tid}")],
        [InlineKeyboardButton("✅ Quick Exit", callback_data=f"posexit_{tid}"),
         InlineKeyboardButton("🟢 Pair Buy", callback_data=f"pospair_{tid}_buy")],
        [InlineKeyboardButton("🧾 Pair Sell (Credit)", callback_data=f"pospair_{tid}_sell")],
        [InlineKeyboardButton("⬅️ Positions", callback_data="menu_positions"), BACK_BTN],
    ])
    await _safe_reply(query.message, "\n".join(msg), reply_markup=kb)


def _single_leg_risk_text(side, opt_type, strike, premium, qty):
    q = max(1, int(abs(qty)))
    st = float(strike)
    prem = max(0.0, float(premium))
    side = str(side).lower()
    ot = str(opt_type).lower()
    if side == "buy":
        max_loss = prem * q * 100
        max_gain = "Unlimited" if ot == "call" else f"${(st - prem) * q * 100:,.0f}"
        be = st + prem if ot == "call" else st - prem
    else:
        max_gain = f"${prem * q * 100:,.0f}"
        max_loss = "Unlimited" if ot == "call" else f"${max(0, (st - prem) * q * 100):,.0f}"
        be = st + prem if ot == "call" else st - prem
    return (
        f"{row2('Max Gain', str(max_gain))}\n"
        f"{row2('Max Loss', str(max_loss))}\n"
        f"{row2('Breakeven', f'${be:.2f}') }"
    )


def _parent_child_payoff(parent_trade, child_cfg):
    tk = str(child_cfg.get("ticker", "")).upper()
    spot = 100.0
    try:
        h = yf.Ticker(tk).history(period="5d")
        if len(h) >= 1:
            spot = float(h["Close"].iloc[-1])
    except Exception:
        pass
    smin = max(1.0, spot * 0.65)
    smax = spot * 1.35
    spots = np.linspace(smin, smax, 81)

    p_qty = abs(_safe_int(parent_trade.get("quantity", 1), 1))
    p_side = "buy" if _safe_int(parent_trade.get("quantity", 1), 1) > 0 else "sell"
    p_pay = _option_leg_payoff(
        p_side,
        str(parent_trade.get("option_type", "call")).lower(),
        _safe_float(parent_trade.get("strike", 0), 0),
        _safe_float(parent_trade.get("entry_price", 0), 0),
        p_qty,
        spots,
    )

    c_pay = _option_leg_payoff(
        child_cfg.get("side", "buy"),
        child_cfg.get("opt_type", "call"),
        child_cfg.get("strike", 0),
        child_cfg.get("entry_price", 0),
        child_cfg.get("qty", 1),
        spots,
    )
    total = p_pay + c_pay
    be = _breakeven_points(spots, total)
    return spots, total, be


    return ok, msg


# ═══════════════════════════════════════════════════════════
#  POSITION GROUPS MENU
# ═══════════════════════════════════════════════════════════
async def groups_menu(query):
    """Show all position groups with total P&L."""
    conn = get_conn()
    # Group open positions by ticker (base stock), not group_id
    conn = get_conn()
    try:
        trades_df = pd.read_sql("SELECT * FROM trades WHERE status='OPEN'", conn)
    except Exception:
        trades_df = pd.DataFrame()
    finally:
        conn.close()

    if trades_df.empty:
        await query.message.reply_text(
            f"{hdr('📦 POSITION GROUPS')}\n\nNo open option positions.",
            parse_mode=H,
            reply_markup=InlineKeyboardMarkup([[BACK_BTN]])
        )
        return

    # Group by ticker (base stock)
    grouped = trades_df.groupby('ticker')
    parts = [hdr("📦 POSITION GROUPS (by Stock)")]
    btn_rows = []
    for tkr, group in grouped:
        num_legs = len(group)
        total_pnl = group['unrealized_pnl'].sum() if 'unrealized_pnl' in group else 0
        pnl_emoji = "🟢" if total_pnl >= 0 else "🔴"
        strikes = ', '.join([str(x) for x in sorted(set(group['strike']))])
        # Show all option types for this stock
        types = ', '.join(sorted(set(group['option_type'].str.upper())))
        # Show expiry range
        exps = sorted(set(group['expiry']))
        exp_range = f"{exps[0]}" if len(exps) == 1 else f"{exps[0]} → {exps[-1]}"
        parts.append(
            f"\n{pnl_emoji} <b>{tkr}</b>  ({num_legs} legs)\n"
            + mono(
                f"{row2('Types', types)}\n"
                f"{row2('Strikes', strikes[:30])}\n"
                f"{row2('Expiries', exp_range)}\n"
                f"{row2('Unrealized P&L', f'${total_pnl:+,.0f}')}")
        )
        btn_rows.append([InlineKeyboardButton(f"📦 {tkr} Details", callback_data=f"grpstock_{tkr}")])

    btn_rows.append([InlineKeyboardButton("⬅️ Positions", callback_data="menu_positions"), BACK_BTN])
    await _safe_reply(query.message, "\n".join(parts), reply_markup=InlineKeyboardMarkup(btn_rows))


async def group_detail(query, group_id):
    """Show detailed view of a position group with P&L chart."""
    conn = get_conn()
    try:
        trades_df = pd.read_sql("SELECT * FROM trades WHERE group_id = ? AND status='OPEN'", 
                               conn, params=(int(group_id),))
    except Exception:
        trades_df = pd.DataFrame()
    finally:
        conn.close()
    
    if trades_df.empty:
        await query.message.reply_text(
            "❌ Group not found or no active positions.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Groups", callback_data="menu_groups")]])
        )
        return
    
    # Calculate metrics
    metrics = _calculate_group_pnl(group_id)
    
    parts = [hdr(f"📦 GROUP #{group_id} DETAIL")]
    parts.append(
        mono(
            f"{row2('Num Legs', str(metrics['num_legs']))}\n"
            f"{row2('Total Cost', f'${metrics['total_cost']:,.0f}')}\n"
            f"{row2('Current Value', f'${metrics['current_value']:,.0f}')}\n"
            f"{'─' * 27}\n"
            f"{row2('Unrealized P&L', f'${metrics['unrealized_pnl']:+,.0f}')}\n"
            f"{row2('Max Profit', f'${metrics['max_profit']:,.0f}')}\n"
            f"{row2('Max Loss', f'${metrics['max_loss']:,.0f}')}\n"
            f"{row2('Breakevens', ', '.join([f'${b:.2f}' for b in metrics['breakevens'][:3]]))}"
        )
    )
    
    parts.append("\n📋 <b>Legs:</b>")
    for _, trade in trades_df.iterrows():
        tid = int(trade["trade_id"])
        tk = trade["ticker"]
        ot = str(trade["option_type"]).upper()
        st = float(trade["strike"])
        qty = int(trade["quantity"])
        side_emoji = "🟢" if qty > 0 else "🔴"
        parts.append(f"{side_emoji} #{tid} {tk} {ot} ${st:.0f} x{qty}")
    
    btn_rows = [
        [InlineKeyboardButton("📊 Show P&L Chart", callback_data=f"grpchart_{group_id}")],
        [InlineKeyboardButton("📦 Add Leg to Group", callback_data=f"grpadd_{group_id}")],
        [InlineKeyboardButton("🗑️ Dissolve Group", callback_data=f"grpdel_{group_id}")],
        [InlineKeyboardButton("⬅️ Groups", callback_data="menu_groups"), BACK_BTN]
    ]
    
    await _safe_reply(query.message, "\n".join(parts), reply_markup=InlineKeyboardMarkup(btn_rows))


# ═══════════════════════════════════════════════════════════
#  STRATEGY BUILDER (OptionProfitCalculator.com style)
# ═══════════════════════════════════════════════════════════
async def strategy_builder_menu(query):
    """Main strategy builder menu with pre-defined templates."""
    templates = [
        ("Call Spread", "call_spread"),
        ("Put Spread", "put_spread"),
        ("Iron Condor", "iron_condor"),
        ("Butterfly", "butterfly"),
        ("Straddle", "straddle"),
        ("Strangle", "strangle"),
        ("Covered Call", "covered_call"),
        ("Protective Put", "protective_put"),
        ("Custom Multi-Leg", "custom_builder")
    ]
    
    parts = [hdr("🎨 STRATEGY BUILDER")]
    parts.append("Select an options strategy template:\n")
    
    btn_rows = []
    for i in range(0, len(templates), 2):
        row = []
        for j in range(2):
            if i + j < len(templates):
                name, cb = templates[i + j]
                row.append(InlineKeyboardButton(name, callback_data=f"strat_{cb}"))
        btn_rows.append(row)
    
    btn_rows.append([InlineKeyboardButton("⬅️ Positions", callback_data="menu_positions"), BACK_BTN])
    
    await _safe_reply(query.message, "\n".join(parts), reply_markup=InlineKeyboardMarkup(btn_rows))


# ═══════════════════════════════════════════════════════════
#  MULTI-LEG GROUP TRADE WIZARD
# ═══════════════════════════════════════════════════════════

# Strategy templates: (name, legs_spec)
# legs_spec = list of dicts: {opt_type, side, strike_offset, qty_ratio, note}
STRATEGY_TEMPLATES = {
    "call_spread":    {"name": "📈 Bull Call Spread",   "legs": [
        {"opt": "call", "side": "buy",  "skoff": 0,  "note": "Buy call (long leg)"},
        {"opt": "call", "side": "sell", "skoff": +5, "note": "Sell call (short leg, reduces cost)"},
    ]},
    "put_spread":     {"name": "📉 Bear Put Spread",    "legs": [
        {"opt": "put",  "side": "buy",  "skoff": 0,  "note": "Buy put (long leg)"},
        {"opt": "put",  "side": "sell", "skoff": -5, "note": "Sell put (short leg, reduces cost)"},
    ]},
    "straddle":       {"name": "⚡ Straddle",           "legs": [
        {"opt": "call", "side": "buy",  "skoff": 0,  "note": "Buy call"},
        {"opt": "put",  "side": "buy",  "skoff": 0,  "note": "Buy put"},
    ]},
    "strangle":       {"name": "⚡ Strangle",           "legs": [
        {"opt": "call", "side": "buy",  "skoff": +5, "note": "Buy OTM call"},
        {"opt": "put",  "side": "buy",  "skoff": -5, "note": "Buy OTM put"},
    ]},
    "iron_condor":    {"name": "🦅 Iron Condor",        "legs": [
        {"opt": "put",  "side": "buy",  "skoff": -10, "note": "Buy OTM put (wing)"},
        {"opt": "put",  "side": "sell", "skoff": -5,  "note": "Sell put (credit)"},
        {"opt": "call", "side": "sell", "skoff": +5,  "note": "Sell call (credit)"},
        {"opt": "call", "side": "buy",  "skoff": +10, "note": "Buy OTM call (wing)"},
    ]},
    "collar":         {"name": "🛡 Collar (Hedge)",     "legs": [
        {"opt": "call", "side": "sell", "skoff": +5,  "note": "Sell call (cap upside, fund put)"},
        {"opt": "put",  "side": "buy",  "skoff": -5,  "note": "Buy put (downside protection)"},
    ]},
    "custom":         {"name": "✏️ Custom Multi-Leg",   "legs": []},
}


async def grp_strategy_menu(query):
    """Show strategy templates for group trade entry."""
    parts = [hdr("📦 ADD GROUP TRADE")]
    parts.append(
        "Build a multi-leg options strategy.\n"
        "Each leg is saved together as a group so you can track net cost, P&L & payoff.\n\n"
        "<b>Select a template — or build custom leg by leg:</b>"
    )
    rows = []
    for key, tmpl in STRATEGY_TEMPLATES.items():
        rows.append([InlineKeyboardButton(tmpl["name"], callback_data=f"grpstrat_{key}")])
    rows.append([InlineKeyboardButton("⬅️ Positions", callback_data="menu_positions"), BACK_BTN])
    await _safe_reply(query.message, "\n".join(parts), reply_markup=InlineKeyboardMarkup(rows))


async def grp_strategy_ticker(query, ctx, strat_key):
    """Step 1 — pick ticker for the group strategy."""
    ctx.user_data["grpwiz"] = {
        "strat_key": strat_key,
        "strat_name": STRATEGY_TEMPLATES[strat_key]["name"],
        "legs_template": STRATEGY_TEMPLATES[strat_key]["legs"],
        "legs_done": [],      # completed leg dicts
        "current_leg": 0,     # index into legs_template (for templates) or free count
    }
    tickers = _ticker_universe(limit=1000)
    kb = _paged_ticker_keyboard("grptk", tickers, page=0, per_page=12, cols=3,
                                include_back=True, back_cb="grp_strategy_menu")
    tmpl_name = STRATEGY_TEMPLATES[strat_key]["name"]
    await query.message.reply_text(
        f"{hdr(f'📦 {tmpl_name}')}\n\nStep 1: Select underlying ticker",
        parse_mode=H, reply_markup=kb
    )


async def grp_leg_expiry(query, ctx):
    """Expiry selection for current group leg."""
    st = ctx.user_data.get("grpwiz", {})
    tk = st.get("ticker", "")
    leg_idx = st.get("current_leg", 0)
    legs_tmpl = st.get("legs_template", [])
    leg_note = legs_tmpl[leg_idx]["note"] if leg_idx < len(legs_tmpl) else f"Leg {leg_idx+1}"

    exps = _get_option_expiries(tk)
    st["expiries"] = exps
    ctx.user_data["grpwiz"] = st
    if not exps:
        await query.message.reply_text(f"❌ No expiries for {tk}.", reply_markup=InlineKeyboardMarkup([[BACK_BTN]]))
        return

    rows = []
    for i in range(0, min(len(exps), 12), 3):
        chunk = exps[i:i+3]
        rows.append([InlineKeyboardButton(x, callback_data=f"grpexp_{i+j}") for j, x in enumerate(chunk)])
    rows.append([InlineKeyboardButton("⬅️ Back", callback_data="grp_strategy_menu"), BACK_BTN])
    _grp_strat_name = st.get('strat_name', 'Group')
    await query.message.reply_text(
        f"{hdr(f'📦 {_grp_strat_name}')}\n\n"
        f"<b>Leg {leg_idx+1}:</b> {leg_note}\nSelect expiry for <b>{tk}</b>:",
        parse_mode=H, reply_markup=InlineKeyboardMarkup(rows)
    )


async def grp_leg_strike(query, ctx):
    """Strike selection for current group leg."""
    st = ctx.user_data.get("grpwiz", {})
    tk = st.get("ticker", "")
    leg_idx = st.get("current_leg", 0)
    legs_tmpl = st.get("legs_template", [])
    exp = st.get("current_exp", "")
    leg_note = legs_tmpl[leg_idx]["note"] if leg_idx < len(legs_tmpl) else f"Leg {leg_idx+1}"

    # Determine opt_type for this leg
    if leg_idx < len(legs_tmpl):
        opt_type = legs_tmpl[leg_idx]["opt"]
    else:
        opt_type = st.get("current_opt", "call")

    # Get spot and compute suggested strike offset
    spot = 100.0
    try:
        h = yf.Ticker(tk).history(period="5d")
        if not h.empty:
            spot = float(h["Close"].iloc[-1])
    except Exception:
        pass

    strikes = _get_option_strikes(tk, exp, opt_type)
    if not strikes:
        await query.message.reply_text("❌ No strikes found.", reply_markup=InlineKeyboardMarkup([[BACK_BTN]]))
        return
    st["strikes"] = strikes
    st["current_opt"] = opt_type
    st["spot"] = spot
    ctx.user_data["grpwiz"] = st

    # Suggest ATM or offset strike
    skoff = legs_tmpl[leg_idx]["skoff"] if leg_idx < len(legs_tmpl) else 0
    suggested = spot + skoff
    # Mark suggested strike with ★
    rows = []
    for i in range(0, min(len(strikes), 12), 3):
        chunk = strikes[i:i+3]
        btns = []
        for j, x in enumerate(chunk):
            label = f"${x:.0f}" if x % 1 == 0 else f"${x:.2f}"
            if abs(x - suggested) <= 2.5:
                label = "★" + label
            btns.append(InlineKeyboardButton(label, callback_data=f"grpsk_{i+j}"))
        rows.append(btns)
    rows.append([InlineKeyboardButton("⬅️ Back", callback_data="grp_strategy_menu"), BACK_BTN])

    side_label = legs_tmpl[leg_idx]["side"].upper() if leg_idx < len(legs_tmpl) else "?"
    _grp_strat_name2 = st.get('strat_name', 'Group')
    await query.message.reply_text(
        f"{hdr(f'📦 {_grp_strat_name2}')}\n\n"
        f"<b>Leg {leg_idx+1} [{side_label} {opt_type.upper()}]:</b> {leg_note}\n"
        f"Spot: <b>${spot:.2f}</b> · Suggested near <b>${suggested:.0f}</b> (★)\n"
        f"Select strike for {exp}:",
        parse_mode=H, reply_markup=InlineKeyboardMarkup(rows)
    )


async def grp_leg_confirm(query, ctx):
    """Show leg summary and ask to add next leg or finish."""
    st = ctx.user_data.get("grpwiz", {})
    leg_idx = st.get("current_leg", 0)
    legs_tmpl = st.get("legs_template", [])
    done = st.get("legs_done", [])
    tk = st.get("ticker", "")
    exp = st.get("current_exp", "")
    opt = st.get("current_opt", "call")
    strike = _safe_float(st.get("current_strike", 0), 0)
    side = legs_tmpl[leg_idx]["side"] if leg_idx < len(legs_tmpl) else st.get("current_side", "buy")
    qty = _safe_int(st.get("current_qty", 1), 1)
    signed_qty = qty if side == "buy" else -qty

    # Price
    px = _option_price_by_mode(tk, opt, strike, exp, mode="mid", fallback=1.0)
    cost = signed_qty * px * 100  # net cost for this leg

    leg_dict = {
        "ticker": tk, "opt_type": opt.upper(), "strike": strike,
        "expiry": exp, "qty": signed_qty, "side": side,
        "entry_price": px, "cost": cost,
        "note": legs_tmpl[leg_idx]["note"] if leg_idx < len(legs_tmpl) else f"Custom leg {leg_idx+1}"
    }
    done.append(leg_dict)
    st["legs_done"] = done
    st["current_leg"] = leg_idx + 1
    ctx.user_data["grpwiz"] = st

    # Running net cost & summary
    net_cost = sum(l["cost"] for l in done)
    sign = "+" if net_cost >= 0 else ""
    parts = [hdr(f"📦 {st.get('strat_name','Group')} · Legs so far")]
    _leg_rows = []
    for i, l in enumerate(done):
        side_lbl = "BUY" if l["qty"] > 0 else "SELL"
        _leg_rows.append((f"L{i+1}", l["opt_type"], side_lbl,
                          f"${l['strike']:.0f}", f"${l['entry_price']:.2f}", f"{l['cost']:+.0f}"))
    _leg_rows.append(("", "", "", "", "Net", f"{net_cost:+.0f}"))
    parts.append(_pipe_table(("Leg", "Type", "Side", "Strike", "Px", "Cost$"),
                              _leg_rows, right_cols={3, 4, 5}))

    net_label = f"💰 Net cost: ${abs(net_cost):.0f}" if net_cost > 0 else f"💰 Net credit: ${abs(net_cost):.0f}"
    parts.append(net_label)

    # Determine next action
    total_legs = len(legs_tmpl)
    has_more_template = leg_idx + 1 < total_legs

    btns = []
    if has_more_template:
        next_leg = legs_tmpl[leg_idx + 1]
        btns.append([InlineKeyboardButton(
            f"➡️ Next: {next_leg['side'].upper()} {next_leg['opt'].upper()} ({next_leg['note']})",
            callback_data="grp_next_leg"
        )])
    btns.append([InlineKeyboardButton("➕ Add Another Leg", callback_data="grp_add_custom_leg")])
    btns.append([InlineKeyboardButton(
        f"✅ Done — Save {len(done)}-Leg Group",
        callback_data="grp_save_all"
    )])
    btns.append([InlineKeyboardButton("❌ Cancel", callback_data="menu_positions")])

    await _safe_reply(query.message, "\n".join(parts), reply_markup=InlineKeyboardMarkup(btns))


async def grp_save_all(query, ctx):
    """Save all legs as a group."""
    st = ctx.user_data.get("grpwiz", {})
    done = st.get("legs_done", [])
    if not done:
        await query.message.reply_text("❌ No legs to save.", reply_markup=InlineKeyboardMarkup([[BACK_BTN]]))
        return

    saved_ids = []
    for leg in done:
        ok, new_id, note = _insert_new_trade(
            leg["ticker"], leg["opt_type"], leg["strike"], leg["expiry"],
            leg["qty"], strategy="group_trade",
            entry_price=leg["entry_price"],
            notes=leg["note"],
        )
        if ok and new_id:
            saved_ids.append(new_id)

    if not saved_ids:
        await query.message.reply_text("❌ Failed to save trades.", reply_markup=InlineKeyboardMarkup([[BACK_BTN]]))
        return

    # Link into group
    ok_g, msg_g = _create_group_from_trades(saved_ids)
    ctx.user_data.pop("grpwiz", None)

    # Get the group_id
    conn = get_conn()
    grp_row = pd.read_sql(
        f"SELECT group_id FROM trades WHERE trade_id = {saved_ids[0]}", conn
    )
    conn.close()
    gid = int(grp_row["group_id"].iloc[0]) if not grp_row.empty else "?"

    # Summary payoff
    net_cost = sum(l["cost"] for l in done)
    net_label = f"Net Cost: ${abs(net_cost):.0f}" if net_cost > 0 else f"Net Credit: ${abs(net_cost):.0f}"

    parts = [hdr(f"✅ GROUP #{gid} SAVED · {len(done)} LEGS")]
    rows_txt = [f"{'#':<3} {'Type':<5} {'Side':<5} {'Strike':>6} {'Px':>5}"]
    rows_txt.append("─" * 27)
    for i, l in enumerate(done):
        side_lbl = "BUY" if l["qty"] > 0 else "SELL"
        rows_txt.append(f"L{i+1:<2} {l['opt_type']:<5} {side_lbl:<5} ${l['strike']:>5.0f} ${l['entry_price']:>4.2f}")
    parts.append(mono("\n".join(rows_txt)))
    parts.append(f"\n💰 <b>{net_label}</b>")
    parts.append(f"🆔 Trade IDs: {', '.join(f'#{i}' for i in saved_ids)}")

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(f"📦 View Group #{gid}", callback_data=f"grp_{gid}")],
        [InlineKeyboardButton("💼 Positions", callback_data="menu_positions"), BACK_BTN]
    ])
    await _safe_reply(query.message, "\n".join(parts), reply_markup=kb)


async def grp_chart(query, group_id):
    """Generate payoff chart for a position group."""
    conn = get_conn()
    try:
        trades_df = pd.read_sql(
            "SELECT * FROM trades WHERE group_id = ? AND status='OPEN'",
            conn, params=(int(group_id),)
        )
    except Exception:
        trades_df = pd.DataFrame()
    conn.close()

    if trades_df.empty:
        await query.message.reply_text("❌ No positions in group.", reply_markup=InlineKeyboardMarkup([[BACK_BTN]]))
        return

    _loading = await query.message.reply_text("⏳ Generating payoff chart...", parse_mode=H)

    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        # Get spot from first ticker
        tk = str(trades_df["ticker"].iloc[0]).upper()
        spot = 100.0
        try:
            h = yf.Ticker(tk).history(period="5d")
            if not h.empty:
                spot = float(h["Close"].iloc[-1])
        except Exception:
            pass

        prices = np.linspace(spot * 0.7, spot * 1.3, 200)
        total_payoff = np.zeros(len(prices))
        net_cost = 0.0
        leg_labels = []

        for _, tr in trades_df.iterrows():
            ot = str(tr.get("option_type", "call")).lower()
            sk = _safe_float(tr.get("strike", spot), spot)
            ep = _safe_float(tr.get("entry_price", 0), 0)
            qty = _safe_int(tr.get("quantity", 1), 1)
            payoff = _option_leg_payoff("buy" if qty > 0 else "sell", ot, sk, ep, abs(qty), prices)
            total_payoff += payoff
            net_cost += qty * ep * 100
            side_lbl = "Buy" if qty > 0 else "Sell"
            leg_labels.append(f"{side_lbl} {ot.upper()} ${sk:.0f}")

        fig, ax = plt.subplots(figsize=(10, 5))
        ax.plot(prices, total_payoff, color="#1565C0", linewidth=2.5, label="Net Payoff")
        ax.axhline(0, color="gray", linewidth=0.8, linestyle="--")
        ax.axvline(spot, color="orange", linewidth=1.2, linestyle=":", label=f"Spot ${spot:.1f}")
        ax.fill_between(prices, total_payoff, 0,
                        where=(total_payoff >= 0), alpha=0.25, color="green", label="Profit")
        ax.fill_between(prices, total_payoff, 0,
                        where=(total_payoff < 0), alpha=0.25, color="red", label="Loss")

        # Break-even points
        bes = _breakeven_points(prices, total_payoff)
        for be in bes:
            ax.axvline(be, color="purple", linewidth=1, linestyle="--")
            ax.text(be, ax.get_ylim()[0] * 0.95, f"BE ${be:.1f}", color="purple", fontsize=7, ha="center")

        net_lbl = f"Net Cost ${abs(net_cost):.0f}" if net_cost > 0 else f"Net Credit ${abs(net_cost):.0f}"
        ax.set_title(f"Group #{group_id} · {tk} · {' | '.join(leg_labels[:3])}\n{net_lbl}", fontsize=10)
        ax.set_xlabel("Stock Price at Expiry")
        ax.set_ylabel("P&L ($)")
        ax.legend(fontsize=8)
        ax.grid(True, alpha=0.3)

        # Stats box
        max_p = float(total_payoff.max())
        max_l = float(total_payoff.min())
        ax.text(0.98, 0.97,
                f"Max Profit: ${max_p:+,.0f}\nMax Loss: ${max_l:+,.0f}\n{net_lbl}",
                transform=ax.transAxes, verticalalignment="top", horizontalalignment="right",
                bbox=dict(boxstyle="round", facecolor="lightyellow", alpha=0.85), fontsize=8)

        plt.tight_layout()
        buf = BytesIO()
        fig.savefig(buf, format="png", dpi=110, bbox_inches="tight")
        plt.close(fig)
        buf.seek(0)

        try:
            await _loading.delete()
        except Exception:
            pass
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton(f"📦 Group Detail", callback_data=f"grp_{group_id}"),
             InlineKeyboardButton("📦 All Groups", callback_data="menu_groups")],
            [BACK_BTN]
        ])
        await query.message.reply_photo(buf, caption=f"📊 Group #{group_id} payoff chart · {net_lbl}", reply_markup=kb)

    except Exception as e:
        log.error(f"grp_chart error: {e}")
        try:
            await _loading.delete()
        except Exception:
            pass
        await query.message.reply_text(f"❌ Chart failed: {e}", reply_markup=InlineKeyboardMarkup([[BACK_BTN]]))


# ═══════════════════════════════════════════════════════════
#  MIROFISH INTEGRATION PLACEHOLDER
# ═══════════════════════════════════════════════════════════
def _mirofish_score_position(tr):
    """Score a single open position. Returns dict with signal, score, reasons."""
    tk = str(tr.get("ticker", "")).upper()
    ot = str(tr.get("option_type", "")).upper()
    strike = _safe_float(tr.get("strike", 0), 0)
    expiry_str = str(tr.get("expiry", ""))
    entry_px = _safe_float(tr.get("entry_price", 0), 0)
    qty = _safe_int(tr.get("quantity", 1), 1)
    side = "BUY" if qty > 0 else "SELL"

    score = 0  # positive = bullish/hold, negative = exit/hedge
    reasons = []

    # ── 1. Days to expiry ──────────────────────────────────────────
    dte = 999
    try:
        exp_dt = datetime.strptime(expiry_str, "%Y-%m-%d").date()
        dte = (exp_dt - datetime.now().date()).days
    except Exception:
        pass

    if dte < 0:
        return {"signal": "EXPIRED", "score": -99, "reasons": ["Position expired"], "dte": dte,
                "live_px": 0, "pnl_pct": 0, "tk": tk, "ot": ot, "strike": strike, "expiry": expiry_str}
    elif dte <= 3:
        score -= 3
        reasons.append(f"⏰ Only {dte}d left — high theta burn")
    elif dte <= 7:
        score -= 1
        reasons.append(f"⚠️ {dte}d to expiry — monitor closely")
    else:
        reasons.append(f"📅 {dte}d to expiry")

    # ── 2. Live price & P&L ────────────────────────────────────────
    live_px = entry_px
    pnl_pct = 0.0
    try:
        tk_obj = yf.Ticker(tk)
        opt_chain = tk_obj.option_chain(expiry_str)
        chain = opt_chain.calls if ot == "CALL" else opt_chain.puts
        row = chain[abs(chain["strike"] - strike) < 0.01]
        if not row.empty:
            bid = float(row["bid"].iloc[0])
            ask = float(row["ask"].iloc[0])
            live_px = (bid + ask) / 2 if bid > 0 and ask > 0 else float(row["lastPrice"].iloc[0])
        if entry_px > 0:
            pnl_pct = (live_px - entry_px) / entry_px * 100 * (1 if side == "BUY" else -1)
    except Exception:
        pass

    if pnl_pct >= 50:
        score += 2
        reasons.append(f"✅ Up {pnl_pct:.0f}% — consider booking partial profit")
    elif pnl_pct >= 25:
        score += 1
        reasons.append(f"🟢 Up {pnl_pct:.0f}% — in profit zone")
    elif pnl_pct <= -50:
        score -= 3
        reasons.append(f"🔴 Down {abs(pnl_pct):.0f}% — near max loss, consider exit")
    elif pnl_pct <= -25:
        score -= 1
        reasons.append(f"🟠 Down {abs(pnl_pct):.0f}% — watch stop loss")
    else:
        reasons.append(f"⚪ P&L: {pnl_pct:+.1f}%")

    # ── 3. OI trend from options_change ───────────────────────────
    try:
        conn = get_conn()
        oc = pd.read_sql("""
            SELECT change_OI_Call, change_OI_Put, pct_change_OI_Call, pct_change_OI_Put,
                   vol_Call_now, vol_Put_now, R1, S1
            FROM options_change
            WHERE ticker = ? AND ABS(strike - ?) < 1
            ORDER BY substr(trade_date_now,7,4)||substr(trade_date_now,1,2)||substr(trade_date_now,4,2) DESC
            LIMIT 1
        """, conn, params=(tk, strike))
        conn.close()

        if not oc.empty:
            r = oc.iloc[0]
            call_oi_chg = float(r.get("change_OI_Call") or 0)
            put_oi_chg = float(r.get("change_OI_Put") or 0)
            r1 = float(r.get("R1") or 0)
            s1 = float(r.get("S1") or 0)

            if ot == "CALL" and side == "BUY":
                if call_oi_chg > 500:
                    score += 2
                    reasons.append(f"🟢 Call OI building +{call_oi_chg:,.0f} — smart money agrees")
                elif call_oi_chg < -500:
                    score -= 1
                    reasons.append(f"🟡 Call OI dropping {call_oi_chg:,.0f} — sellers unwinding")
            elif ot == "PUT" and side == "BUY":
                if put_oi_chg > 500:
                    score += 2
                    reasons.append(f"🟢 Put OI building +{put_oi_chg:,.0f} — hedges increasing")
                elif put_oi_chg < -500:
                    score -= 1
                    reasons.append(f"🟡 Put OI dropping — hedge unwind")

            if r1 > 0 and strike > r1:
                score -= 1
                reasons.append(f"⚠️ Strike ${strike:.0f} above R1 ${r1:.1f} — resistance zone")
            elif s1 > 0 and strike < s1:
                score -= 1
                reasons.append(f"⚠️ Strike ${strike:.0f} below S1 ${s1:.1f} — support zone")
    except Exception:
        pass

    # ── 4. PCR for the ticker ──────────────────────────────────────
    try:
        conn = get_conn()
        sd = pd.read_sql("""
            SELECT pcr_oi FROM stock_daily WHERE ticker = ?
            ORDER BY substr(trade_date,7,4)||substr(trade_date,1,2)||substr(trade_date,4,2) DESC LIMIT 1
        """, conn, params=(tk,))
        conn.close()
        if not sd.empty:
            pcr = float(sd["pcr_oi"].iloc[0] or 0)
            if ot == "CALL" and side == "BUY":
                if pcr > 1.3:
                    score -= 1
                    reasons.append(f"🔴 PCR {pcr:.2f} — bearish market sentiment")
                elif pcr < 0.7:
                    score += 1
                    reasons.append(f"🟢 PCR {pcr:.2f} — bullish sentiment supports call")
            elif ot == "PUT" and side == "BUY":
                if pcr > 1.3:
                    score += 1
                    reasons.append(f"🟢 PCR {pcr:.2f} — hedging activity supports put")
                elif pcr < 0.7:
                    score -= 1
                    reasons.append(f"🟡 PCR {pcr:.2f} — bullish market, put may lag")
    except Exception:
        pass

    # ── 5. Final signal ───────────────────────────────────────────
    if score >= 3:
        signal = "⚡ ADD / HOLD STRONG"
    elif score >= 1:
        signal = "✅ HOLD"
    elif score == 0:
        signal = "⏸ NEUTRAL — WATCH"
    elif score >= -2:
        signal = "⚠️ REDUCE / HEDGE"
    else:
        signal = "🔴 EXIT NOW"

    return {
        "signal": signal, "score": score, "reasons": reasons,
        "dte": dte, "live_px": live_px, "pnl_pct": pnl_pct,
        "tk": tk, "ot": ot, "strike": strike, "expiry": expiry_str, "qty": qty
    }


async def mirofish_menu(query):
    """MiroFish: multi-factor signal engine applied to open positions + top OI signals."""
    _loading = await query.message.reply_text("🤖 MiroFish scanning positions & OI data...", parse_mode=H)

    conn = get_conn()
    open_trades = pd.read_sql("SELECT * FROM trades WHERE status='OPEN'", conn)
    conn.close()

    parts = [hdr("🤖 MIROFISH SIGNAL ENGINE")]
    btn_rows = []

    # ── Section 1: Open Positions ─────────────────────────────────
    if open_trades.empty:
        parts.append("\n💼 <b>No open positions</b> — showing top OI signals below\n")
    else:
        parts.append(f"\n💼 <b>YOUR POSITIONS ({len(open_trades)})</b>")
        tbl_rows = [f"{'Ticker':<6} {'Tp':<3} {'Stk':>5} {'P&L%':>5} {'Sig':<4}"]
        tbl_rows.append("─" * 27)
        for _, tr in open_trades.iterrows():
            result = _mirofish_score_position(tr)
            sig_short = result['signal'].replace('HOLD', 'HLD').replace('EXIT', 'EXT').replace('ADD', 'ADD')[:4]
            pnl = result['pnl_pct']
            pnl_s = f"{pnl:>+.0f}%"
            tk6 = result['tk'][:6]
            tp3 = str(result['ot'])[:3]
            tbl_rows.append(f"{tk6:<6} {tp3:<3} {result['strike']:>5.0f} {pnl_s:>5} {sig_short:<4}")
            tid = _safe_int(tr.get("trade_id", 0), 0)
            btn_rows.append([InlineKeyboardButton(
                f"📋 {result['tk']} {result['ot']} ${result['strike']:.0f} — {result['signal']}",
                callback_data=f"miro_pos_{tid}"
            )])
        parts.append(mono("\n".join(tbl_rows)))

    # ── Section 2: Top OI signals from options_change ─────────────
    try:
        conn = get_conn()
        signals_df = pd.DataFrame()
        dt = ""
        latest = pd.read_sql("""
            SELECT DISTINCT trade_date_now FROM options_change
            ORDER BY substr(trade_date_now,7,4)||substr(trade_date_now,1,2)||substr(trade_date_now,4,2) DESC LIMIT 1
        """, conn)
        if not latest.empty:
            dt = latest["trade_date_now"].iloc[0]
            signals_df = pd.read_sql("""
                SELECT ticker,
                       SUM(change_OI_Call) as call_oi_chg,
                       SUM(change_OI_Put)  as put_oi_chg,
                       SUM(vol_Call_now)   as call_vol,
                       SUM(vol_Put_now)    as put_vol,
                       AVG(pct_change_OI_Call) as call_pct,
                       AVG(pct_change_OI_Put)  as put_pct
                FROM options_change
                WHERE trade_date_now = ?
                GROUP BY ticker
                HAVING (ABS(SUM(change_OI_Call)) + ABS(SUM(change_OI_Put))) > 100
                ORDER BY (ABS(SUM(change_OI_Call)) + ABS(SUM(change_OI_Put))) DESC
                LIMIT 20
            """, conn, params=(dt,))

        bull_tickers = []
        bear_tickers = []
        if not signals_df.empty:
            for _, sr in signals_df.iterrows():
                tk = str(sr["ticker"])
                c_chg = float(sr["call_oi_chg"] or 0)
                p_chg = float(sr["put_oi_chg"] or 0)
                c_pct = float(sr["call_pct"] or 0)
                p_pct = float(sr["put_pct"] or 0)
                _msig, _ = _oi_signal_light(c_chg, p_chg)
                if _msig == "BULLISH":
                    bull_tickers.append((tk, c_chg, c_pct))
                elif _msig in ("BEARISH", "MILD BEAR"):
                    bear_tickers.append((tk, p_chg, p_pct))
                # HEDGE / STRADDLE excluded from directional lists intentionally

        # Query top put strikes for top 3 bearish tickers while conn still open
        bear_strikes = {}
        for _btk, _, _ in bear_tickers[:3]:
            try:
                _sp_df = pd.read_sql(
                    "SELECT close FROM stock_daily WHERE ticker=? ORDER BY substr(trade_date,7,4)||substr(trade_date,1,2)||substr(trade_date,4,2) DESC LIMIT 1",
                    conn, params=(_btk,))
                _spot = float(_sp_df["close"].iloc[0]) if not _sp_df.empty else 0
                _sk_df = pd.read_sql("""
                    SELECT strike, change_OI_Put, openInt_Put_now
                    FROM options_change
                    WHERE ticker=? AND trade_date_now=? AND change_OI_Put > 0
                    ORDER BY change_OI_Put DESC LIMIT 3
                """, conn, params=(_btk, dt))
                rows_out = []
                for _, _skr in _sk_df.iterrows():
                    _st   = float(_skr["strike"] or 0)
                    _pd_  = float(_skr["change_OI_Put"] or 0)
                    _pfs  = (_st - _spot) / _spot * 100 if _spot > 0 else 0
                    if abs(_pfs) <= 3:
                        _zone = "ATM"
                    elif _pfs < -3 and _pfs >= -10:
                        _zone = "NEAR"
                    elif _pfs < -10:
                        _zone = "DEEP"
                    else:
                        _zone = "OTM↑"
                    _nt = _fmt_notional(_pd_ * _st * 100)
                    _why = {"ATM": "directional short", "NEAR": "hedge/short", "DEEP": "tail hedge", "OTM↑": "OTM put"}.get(_zone, "")
                    rows_out.append((_st, _pd_, _zone, _nt, _why))
                bear_strikes[_btk] = rows_out
            except Exception:
                pass
        conn.close()

        if not signals_df.empty:
            parts.append(f"\n📡 <b>TOP OI SIGNALS · {dt}</b>")

            if bull_tickers:
                parts.append("\n🟢 <b>Bullish (Call OI Building)</b>")
                rows = [f"{'Ticker':<7} {'OI Δ':>8} {'%Chg':>6}"]
                rows.append("─" * 24)
                for tk, chg, pct in bull_tickers[:5]:
                    rows.append(f"{tk:<7} {chg:>+8,.0f} {pct:>+6.1f}%")
                parts.append(mono("\n".join(rows)))
                # buttons for top 3
                for tk, _, _ in bull_tickers[:3]:
                    btn_rows.append([InlineKeyboardButton(
                        f"🟢 {tk} signal detail", callback_data=f"miro_ticker_{tk}"
                    )])

            if bear_tickers:
                parts.append("\n🔴 <b>Bearish (Put OI Building)</b>")
                rows = [f"{'Ticker':<7} {'OI Δ':>8} {'%Chg':>6}"]
                rows.append("─" * 24)
                for tk, chg, pct in bear_tickers[:5]:
                    rows.append(f"{tk:<7} {chg:>+8,.0f} {pct:>+6.1f}%")
                parts.append(mono("\n".join(rows)))
                # Strike breakdown for top 3 bearish tickers
                for _btk, _, _ in bear_tickers[:3]:
                    if bear_strikes.get(_btk):
                        parts.append(f"\n📍 <b>{_btk}</b> top put strikes:")
                        for _st, _pd_, _zone, _nt, _why in bear_strikes[_btk]:
                            parts.append(f"  ${_st:.0f} <b>{_zone}</b> · {_why} · +{_pd_/1000:.1f}K · {_nt}")
                for tk, _, _ in bear_tickers[:3]:
                    btn_rows.append([InlineKeyboardButton(
                        f"🔴 {tk} signal detail", callback_data=f"miro_ticker_{tk}"
                    )])
    except Exception as e:
        log.warning(f"MiroFish OI signals failed: {e}")

    btn_rows.append([InlineKeyboardButton("🔄 Refresh", callback_data="menu_mirofish"),
                     InlineKeyboardButton("💼 Positions", callback_data="menu_positions")])
    btn_rows.append([BACK_BTN])

    try:
        await _loading.delete()
    except Exception:
        pass
    await query.message.reply_text("\n".join(parts), parse_mode=H,
                                   reply_markup=InlineKeyboardMarkup(btn_rows))


async def mirofish_position_detail(query, trade_id):
    """Full MiroFish analysis for one position."""
    tr = _fetch_trade(trade_id)
    if not tr:
        await query.message.reply_text("❌ Trade not found.", reply_markup=InlineKeyboardMarkup([[BACK_BTN]]))
        return

    _loading = await query.message.reply_text(
        f"🤖 Analyzing {tr.get('ticker')} {tr.get('option_type')} ${tr.get('strike'):.0f}...",
        parse_mode=H
    )

    result = _mirofish_score_position(tr)
    tk = result["tk"]
    ot = result["ot"]
    st = result["strike"]
    qty = result["qty"]
    side = "BUY" if qty > 0 else "SELL"

    parts = [hdr(f"🤖 MIROFISH · {tk} {ot} ${st:.0f}")]
    parts.append(
        mono(
            f"{'Signal':<12} {result['signal'][:20]}\n"
            f"{'Score':<12} {result['score']:+d} / 10\n"
            f"{'Live Px':<12} ${result['live_px']:.2f}\n"
            f"{'P&L':<12} {result['pnl_pct']:+.1f}%\n"
            f"{'DTE':<12} {result['dte']}d\n"
            f"{'Side':<12} {side}"
        )
    )

    parts.append("\n📋 <b>Analysis Factors:</b>")
    for r in result["reasons"]:
        parts.append(f"  • {r}")

    # Recommendation
    score = result["score"]
    parts.append("\n💡 <b>Recommendation:</b>")
    if score >= 3:
        parts.append("  Add to position or hold with confidence.\n  Momentum & OI support your side.")
    elif score >= 1:
        parts.append("  Hold current position.\n  Trail stop to protect gains.")
    elif score == 0:
        parts.append("  No strong edge. Set tight stop.\n  Wait for catalyst before adding.")
    elif score >= -2:
        parts.append("  Reduce size or add a hedge.\n  Consider selling half to reduce risk.")
    else:
        parts.append("  Exit recommended.\n  OI trend / theta / P&L all against you.")

    try:
        await _loading.delete()
    except Exception:
        pass
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🤖 All Signals", callback_data="menu_mirofish"),
         InlineKeyboardButton("🛠 Position", callback_data=f"pos_{trade_id}")],
        [BACK_BTN]
    ])
    await _safe_reply(query.message, "\n".join(parts), reply_markup=kb)



def _oi_signal_light(call_chg: float, put_chg: float, pcr: float = 1.0):
    """
    Hedge-aware OI signal for aggregate (ticker-level) data without per-strike info.
    Returns (signal_label, hex_color).

    If PCR is already >1.5 AND new put OI is building BUT call side is stable,
    the new puts are likely institutional protection -- not directional shorts.
    Both sides building strongly = straddle / event play (NOT purely bearish).
    """
    c, p   = float(call_chg or 0), float(put_chg or 0)
    pcr    = float(pcr or 1.0)
    both   = c > 200 and p > 200

    if c > abs(p) * 1.2 and c > 0:
        return ("BULLISH", "#2E7D32")
    if p > abs(c) * 1.2 and p > 0:
        if pcr > 1.5 and c >= -200:          # already defensive + call side stable
            return ("HEDGE", "#1565C0")
        return ("BEARISH", "#C62828")
    if both:
        if c > p * 1.4:  return ("BULL+HEDGE", "#388E3C")
        return ("STRADDLE", "#6A1B9A")
    if c < 0 and p < 0:
        return ("UNWIND", "#757575")
    return ("NEUTRAL", "#455A64")


def _fmt_notional(n: float) -> str:
    """Format dollar notional: 1234567 -> $1.2M"""
    if n >= 1_000_000_000: return f"${n/1_000_000_000:.1f}B"
    if n >= 1_000_000:     return f"${n/1_000_000:.1f}M"
    if n >= 1_000:         return f"${n/1_000:.0f}K"
    return f"${n:.0f}"


def _explain_oi_flow(call_chg: float, put_chg: float,
                     call_oi: float, put_oi: float,
                     spot: float, strike: float,
                     pcr: float = 1.0) -> str:
    """Rule-based explanation of OI activity at a single strike, with notional money."""
    c = float(call_chg or 0)
    p = float(put_chg or 0)
    c_oi = float(call_oi or 0)
    p_oi = float(put_oi or 0)
    pcr  = float(pcr or 1.0)
    pct_from_spot = (strike - spot) / spot * 100 if spot > 0 else 0
    if abs(pct_from_spot) <= 3:
        zone = "ATM"
    elif pct_from_spot > 3:
        zone = "OTM-C"
    else:
        zone = "OTM-P"

    # Notional money: contracts × strike × 100 shares
    c_notional = abs(c) * strike * 100
    p_notional = abs(p) * strike * 100
    c_nt = _fmt_notional(c_notional) if c_notional > 0 else ""
    p_nt = _fmt_notional(p_notional) if p_notional > 0 else ""

    # Both sides building — straddle / event play
    if c > 300 and p > 300:
        return (f"⇔ STRADDLE ${strike:.0f} [{zone}]: calls +{c:,.0f}({c_nt}) "
                f"puts +{p:,.0f}({p_nt}) — big move expected")

    parts = []
    # Call side
    if c > 500:
        if zone == "OTM-C" and pct_from_spot > 7:
            parts.append(f"🚀 Far-OTM call build ${strike:.0f}[+{pct_from_spot:.0f}%]: "
                         f"+{c:,.0f}({c_nt}) — spec breakout / CC writing")
        elif c_oi > 0 and c > c_oi * 0.15:
            parts.append(f"📈 Aggressive call accum ${strike:.0f}[{zone}]: "
                         f"+{c:,.0f}({c_nt}) = {c/c_oi*100:.0f}% of OI — institutional LONGS")
        else:
            parts.append(f"🟢 Call build ${strike:.0f}[{zone}]: +{c:,.0f}({c_nt}) — buyers adding")
    elif c < -300:
        label = "short-covering (bears closing)" if c_oi > 3000 else "longs exiting"
        parts.append(f"🔄 Call unwind ${strike:.0f}: {c:,.0f}({c_nt}) — {label}")

    # Put side
    if p > 500:
        if pcr > 1.5 and zone == "OTM-P" and pct_from_spot < -7:
            parts.append(f"🔵 Inst HEDGE ${strike:.0f}[{pct_from_spot:.0f}%]: "
                         f"+{p:,.0f}({p_nt}) deep-OTM put, PCR={pcr:.1f} — NOT directional short")
        elif zone == "ATM":
            parts.append(f"⚠️ ATM put surge ${strike:.0f}: +{p:,.0f}({p_nt}) — directional SHORTS entering")
        elif zone == "OTM-P":
            parts.append(f"📉 Near-OTM put build ${strike:.0f}[{pct_from_spot:.0f}%]: "
                         f"+{p:,.0f}({p_nt}) — SHORTS positioning for downside")
        else:
            parts.append(f"🛡️ Put hedge ${strike:.0f}: +{p:,.0f}({p_nt}) — protection/hedge activity")
    elif p < -300:
        label = "hedges removed = BULLISH" if p_oi > 3000 else "puts closing / expiry rolloff"
        parts.append(f"🔄 Put unwind ${strike:.0f}: {p:,.0f}({p_nt}) — {label}")

    if not parts:
        parts.append(f"⚪ ${strike:.0f}[{zone}] C:{c:+.0f} P:{p:+.0f} — no clear signal")
    return "\n".join(parts)


def _oi_expiry_flow_table(ticker: str, conn, latest_date: str) -> str:
    """<pre> table of call/put OI delta per expiry for latest date. Skips expired."""
    try:
        edf = pd.read_sql("""
            SELECT expiry_date,
                   SUM(change_OI_Call) as c_chg,
                   SUM(change_OI_Put)  as p_chg
            FROM options_change
            WHERE ticker=? AND trade_date_now=?
            GROUP BY expiry_date
            ORDER BY substr(expiry_date,7,4)||substr(expiry_date,1,2)||substr(expiry_date,4,2)
        """, conn, params=(ticker, latest_date))
    except Exception:
        return ""
    if edf.empty:
        return ""
    _today = datetime.now().date()
    def _fk(n):
        n = float(n or 0); s = "+" if n >= 0 else ""; a = abs(n)
        if a >= 1_000: return f"{s}{a/1_000:.0f}K"
        return f"{s}{n:.0f}"
    def _bias(c, p):
        c, p = float(c or 0), float(p or 0)
        if c > 300 and p > 300:       return "STRD"
        if c > abs(p)*1.5 and c > 0:  return "BULL"
        if p > abs(c)*1.5 and p > 0:  return "BEAR"
        if c < -200 and p < -200:     return "UNWD"
        return "FLAT"
    rows = []
    for _, r in edf.iterrows():
        try:
            if datetime.strptime(str(r["expiry_date"]), "%m-%d-%Y").date() < _today:
                continue
        except Exception:
            pass
        exp_s = str(r["expiry_date"])[:5]
        rows.append("{:<5} {:>6} {:>6}  {:<4}".format(
            exp_s, _fk(r["c_chg"]), _fk(r["p_chg"]), _bias(r["c_chg"], r["p_chg"])))
    if not rows:
        return ""
    hdr_l = "{:<5} {:>6} {:>6}  {:<4}".format("Exp","CΔ","PΔ","Bias")
    return "<pre>" + "\n".join([hdr_l, "-"*26] + rows[:6]) + "</pre>"


def _get_earnings_dte(ticker: str) -> "int | None":
    """Returns days to next earnings, or None if unknown/past."""
    try:
        cal = yf.Ticker(ticker).calendar
        ed = None
        if isinstance(cal, dict):
            ed = cal.get("Earnings Date")
        elif hasattr(cal, "columns"):
            row = cal.get("Earnings Date")
            ed = row.iloc[0] if row is not None and not row.empty else None
        if ed is None:
            return None
        if hasattr(ed, '__iter__') and not isinstance(ed, str):
            ed = list(ed)[0]
        import pandas as _pd
        ed_dt = _pd.Timestamp(ed).date()
        dte_e = (ed_dt - datetime.now().date()).days
        return dte_e if dte_e >= 0 else None
    except Exception:
        return None


def _compute_gex(ticker: str, conn, spot: float, expiry=None) -> dict:
    """
    Gamma Exposure (GEX) for the nearest LIQUID expiry, with per-strike implied
    vol backed out from stored option last-prices (HV fallback when unavailable).
    Net GEX/strike = gamma x (call_OI - put_OI) x spot^2 x 0.01   (calls +, puts -).
    Positive GEX: dealers long gamma -> suppress vol (pinning / mean-revert).
    Negative GEX: dealers short gamma -> amplify moves (trending / volatile).
    Gamma flip (zero_gamma) = price where TOTAL GEX crosses zero, found by
    recomputing GEX across a price grid (not a crude cumulative-sum crossing).
    Returns: total_gex, total_gex_m, zero_gamma, gex_signal, regime, top_strikes,
             call_wall, put_wall, expiry, dte.
    """
    result = {"total_gex": 0.0, "zero_gamma": None, "gex_signal": "UNKNOWN",
              "top_strikes": [], "regime": "UNKNOWN", "total_gex_m": 0.0,
              "call_wall": None, "put_wall": None, "expiry": None, "dte": None}
    if not spot or spot <= 0:
        return result

    # Latest snapshot date for this ticker
    try:
        _ld = pd.read_sql(
            "SELECT trade_date_now FROM options_change WHERE ticker=?"
            " ORDER BY substr(trade_date_now,7,4)||substr(trade_date_now,1,2)||substr(trade_date_now,4,2) DESC LIMIT 1",
            conn, params=(ticker,))
        if _ld.empty:
            return result
        date_str = _ld["trade_date_now"].iloc[0]
        ref_dt = datetime.strptime(date_str, "%m-%d-%Y")
    except Exception:
        return result

    # Pick nearest LIQUID expiry: max OI among 0..60 DTE expiries, else nearest future
    try:
        edf = pd.read_sql(
            "SELECT expiry_date, SUM(openInt_Call_now)+SUM(openInt_Put_now) AS oi"
            " FROM options_change WHERE ticker=? AND trade_date_now=? GROUP BY expiry_date",
            conn, params=(ticker, date_str))
    except Exception:
        return result
    cand = []
    for _, e in edf.iterrows():
        try:
            _es = str(e["expiry_date"])
            try:
                ed = datetime.strptime(_es, "%m-%d-%Y")
            except ValueError:
                ed = datetime.strptime(_es, "%Y-%m-%d")
        except Exception:
            continue
        dte = (ed - ref_dt).days
        if dte >= 0:
            cand.append((dte, float(e["oi"] or 0), str(e["expiry_date"])))
    if not cand:
        return result
    if expiry:
        _m = [c for c in cand if c[2] == expiry]
        if _m:
            dte_days, _oi_e, expiry_s = _m[0]
        else:
            _pool = [c for c in cand if c[0] >= 1] or cand
            dte_days, _oi_e, expiry_s = min(_pool, key=lambda c: c[0])
    else:
        _pool = [c for c in cand if c[0] >= 1] or cand
        near = [c for c in _pool if c[0] <= 60]
        dte_days, _oi_e, expiry_s = (max(near, key=lambda c: c[1]) if near
                                     else min(_pool, key=lambda c: c[0]))
    T = max(dte_days / 365.0, 1.0 / 365.0)
    result["expiry"] = expiry_s
    result["dte"] = dte_days

    # HV fallback (annualised) for strikes whose IV can't be backed out
    hv = 0.30
    try:
        _hsd = pd.read_sql(
            "SELECT close FROM stock_daily WHERE ticker=?"
            " ORDER BY substr(trade_date,7,4)||substr(trade_date,1,2)||substr(trade_date,4,2) DESC LIMIT 25",
            conn, params=(ticker,))
        if len(_hsd) >= 10:
            _rets = _hsd["close"].astype(float).pct_change().dropna()
            hv = max(0.10, min(float(_rets.std() * (252 ** 0.5)), 2.0))
    except Exception:
        pass

    # Per-strike OI + call last-price for the chosen expiry
    try:
        df = pd.read_sql(
            "SELECT strike,"
            " SUM(openInt_Call_now) AS c_oi, SUM(openInt_Put_now) AS p_oi,"
            " AVG(CASE WHEN lastPrice_Call_now>0 THEN lastPrice_Call_now END) AS c_px"
            " FROM options_change WHERE ticker=? AND trade_date_now=? AND expiry_date=?"
            " GROUP BY strike ORDER BY strike",
            conn, params=(ticker, date_str, expiry_s))
    except Exception:
        return result
    if df.empty:
        return result

    # Build per-strike (strike, call_oi, put_oi, sigma) with real IV from call price
    strikes = []
    for _, row in df.iterrows():
        K = float(row["strike"])
        c_oi = float(row["c_oi"] or 0)
        p_oi = float(row["p_oi"] or 0)
        if c_oi <= 0 and p_oi <= 0:
            continue
        c_px = float(row["c_px"] or 0)
        sigma = hv
        mny = abs(K - spot) / spot
        # Back out IV from a non-deep-ITM call (has real time value); else HV proxy
        if c_px > 0.10 and mny < 0.30 and K >= spot * 0.85:
            try:
                iv = _implied_vol_hp(c_px, spot, K, T)
                if iv and 0.03 < iv < 3.0:
                    sigma = iv
            except Exception:
                pass
        sigma = max(0.05, min(sigma, 3.0))
        strikes.append((K, c_oi, p_oi, sigma))

    if not strikes:
        return result

    # GEX at current spot, per strike
    gex_by_strike = []
    for K, c_oi, p_oi, sig in strikes:
        g = _bs_gamma_hp(spot, K, T, sig)
        net = (c_oi - p_oi) * g * spot * spot * 0.01
        c_g = c_oi * g * spot * spot * 0.01
        p_g = p_oi * g * spot * spot * 0.01
        gex_by_strike.append((K, net, c_g, p_g))

    total_gex = sum(g[1] for g in gex_by_strike)
    result["total_gex"] = total_gex
    result["total_gex_m"] = total_gex / 1e6

    # Walls: most positive net GEX = call wall (resistance); most negative = put wall (support)
    cw = max(gex_by_strike, key=lambda x: x[1])
    pw = min(gex_by_strike, key=lambda x: x[1])
    result["call_wall"] = cw[0] if cw[1] > 0 else None
    result["put_wall"]  = pw[0] if pw[1] < 0 else None

    # Gamma flip: recompute TOTAL gex across a price grid, find the zero crossing
    lo, hi = spot * 0.80, spot * 1.20
    n = 80
    zero_g = None
    prev = None
    for i in range(n + 1):
        S = lo + (hi - lo) * i / n
        tot = 0.0
        for K, c_oi, p_oi, sig in strikes:
            tot += (c_oi - p_oi) * _bs_gamma_hp(S, K, T, sig) * S * S * 0.01
        if prev is not None and (tot >= 0) != (prev[1] >= 0):
            zero_g = round((prev[0] + S) / 2, 2)
            break
        prev = (S, tot)
    result["zero_gamma"] = zero_g

    # Regime from sign of total GEX at spot
    if total_gex > 0:
        result["gex_signal"] = "PINNING"
        result["regime"] = "Low vol - dealers suppress moves, mean revert"
    else:
        result["gex_signal"] = "TRENDING"
        result["regime"] = "High vol - dealers amplify direction, trend follow"

    # Top 5 strikes by |net GEX|
    top = sorted(gex_by_strike, key=lambda x: -abs(x[1]))[:5]
    result["top_strikes"] = [{"strike": s, "gex_m": g / 1e6, "c_gex": cg / 1e6, "p_gex": pg / 1e6}
                             for s, g, cg, pg in top]
    return result


def _oi_opportunity_table(ticker: str, conn, df, spot: float) -> str:
    """
    Buy/sell opportunity cards with investment needed, P&L in $, time to monitor.
    Each card is 2 lines to stay ≤28 chars on mobile.
    """
    if df.empty or spot <= 0:
        return ""

    # ── HV20 ──────────────────────────────────────────────────────────
    hv = 0.30
    try:
        _hsd = pd.read_sql("""SELECT close FROM stock_daily WHERE ticker=?
            ORDER BY substr(trade_date,7,4)||substr(trade_date,1,2)||substr(trade_date,4,2) DESC
            LIMIT 25""", conn, params=(ticker,))
        if len(_hsd) >= 10:
            _rets = _hsd["close"].astype(float).pct_change().dropna()
            hv = float(_rets.std() * (252**0.5))
            hv = max(0.10, min(hv, 2.0))
    except Exception:
        pass

    # ── Nearest DTE ────────────────────────────────────────────────────
    dte = 21
    exp_str = ""
    try:
        _edt = pd.read_sql("""SELECT DISTINCT expiry_date FROM options_change WHERE ticker=?
            AND substr(expiry_date,7,4)||substr(expiry_date,1,2)||substr(expiry_date,4,2) > ?
            ORDER BY substr(expiry_date,7,4)||substr(expiry_date,1,2)||substr(expiry_date,4,2) LIMIT 1""",
            conn, params=(ticker, datetime.now().strftime("%Y%m%d")))
        if not _edt.empty:
            exp_str = _edt["expiry_date"].iloc[0]
            dte = max(1, (datetime.strptime(str(exp_str), "%m-%d-%Y") - datetime.now()).days)
    except Exception:
        pass

    # ── Earnings flag ─────────────────────────────────────────────────
    earn_dte = _get_earnings_dte(ticker)
    earn_flag = ""
    if earn_dte is not None and 0 <= earn_dte <= 14:
        earn_flag = f" ⚡EARN{earn_dte}d"

    T = max(dte, 1) / 365.0
    r = 0.045
    sigma = max(hv, 0.15)

    # Monitor cadence based on DTE
    if dte <= 7:
        monitor = "daily — gamma burning"
    elif dte <= 21:
        monitor = "every 2-3d"
    else:
        monitor = "weekly"

    def _fmt_d(n):
        """Format dollar amount compactly."""
        a = abs(float(n or 0))
        if a >= 1_000_000: return f"${a/1e6:.1f}M"
        if a >= 1_000:     return f"${a/1e3:.1f}K"
        return f"${a:.0f}"

    buy_opps  = []  # (strat, strike, pw, rr, invest$, max_profit$, max_loss$)
    sell_opps = []

    spread_width = max(spot * 0.03, 2.0)   # ~3% of spot as spread width assumption

    for _, row in df.iterrows():
        strike = float(row["strike"])
        c_chg  = float(row.get("call_chg", 0) or 0)
        p_chg  = float(row.get("put_chg",  0) or 0)
        c_oi   = float(row.get("call_oi",  0) or 0)
        p_oi   = float(row.get("put_oi",   0) or 0)
        pct    = (strike - spot) / spot * 100 if spot > 0 else 0
        try:
            g_c = bs_greeks(spot, strike, T, r, sigma, "call")
            g_p = bs_greeks(spot, strike, T, r, sigma, "put")
            px_c   = float(g_c["price"])
            px_p   = float(g_p["price"])
            delta_c = float(g_c["delta"])
            delta_p = abs(float(g_p["delta"]))
        except Exception:
            px_c = max(0, spot - strike) / 100; px_p = max(0, strike - spot) / 100
            delta_c = 0.5; delta_p = 0.5
        pw_c = int(delta_c * 100)
        pw_p = int(delta_p * 100)
        inv_c = max(px_c * 100, 1.0)   # cost per 1 contract ($)
        inv_p = max(px_p * 100, 1.0)
        inv_strd = inv_c + inv_p
        be_c = inv_c / 100   # break-even move needed on stock
        be_p = inv_p / 100

        # ── BUY opportunities ──────────────────────────────────────────
        if c_chg > 500 and pct > -8:
            target_profit = inv_c * 2   # 2:1 R:R target
            buy_opps.append(("BUY CALL", strike, pw_c, "2:1", inv_c, target_profit, inv_c))
        if p_chg > 500 and pct < 8:
            target_profit = inv_p * 2
            buy_opps.append(("BUY PUT ", strike, pw_p, "2:1", inv_p, target_profit, inv_p))
        if c_chg > 300 and p_chg > 300:
            buy_opps.append(("STRADDLE", strike, 55, "2:1", inv_strd, inv_strd * 2, inv_strd))

        # ── SELL / Income opportunities ────────────────────────────────
        if p_oi > 2000 and abs(pct) > 5 and p_chg >= -300:
            # Sell put spread: receive premium, risk = spread_width - premium
            rcv = px_p * 100
            risk = max(spread_width * 100 - rcv, rcv * 0.5)
            sell_opps.append(("SELL PUT", strike, int((1-delta_p)*100), "1:3", rcv, rcv, risk))
        if c_oi > 2000 and pct > 5:
            rcv = px_c * 100
            risk = max(spread_width * 100 - rcv, rcv * 0.5)
            sell_opps.append(("SELL CALL", strike, int((1-delta_c)*100), "1:3", rcv, rcv, risk))
        if c_chg > 300 and p_chg > 300 and abs(pct) > 2:
            # Iron condor: collect both premiums, risk = spread - collected
            rcv_ic = (px_c + px_p) * 100 * 0.5   # rough: one side only
            risk_ic = max(spread_width * 100 - rcv_ic, rcv_ic)
            sell_opps.append(("IRON COND", strike, max(55, int((1-delta_c)*100)), "1:2",
                              rcv_ic, rcv_ic, risk_ic))

    def _dedup(lst):
        seen = set(); out = []
        for item in lst:
            k = (item[0][:6], int(item[1]))
            if k not in seen:
                seen.add(k); out.append(item)
        return out

    buy_opps  = sorted(_dedup(buy_opps),  key=lambda x: -x[2])[:4]
    sell_opps = sorted(_dedup(sell_opps), key=lambda x: -x[2])[:4]

    if not buy_opps and not sell_opps:
        return ""

    lines = []
    exp_label = f" exp {exp_str[:5]}" if exp_str else ""
    lines.append(f"\n<b>Trade Ideas{exp_label}{earn_flag}</b>")
    lines.append(f"<i>HV:{hv*100:.0f}%  DTE:{dte}d  Monitor:{monitor}</i>")

    def _fk(n):
        a = abs(float(n or 0))
        if a >= 1_000_000: return f"{a/1e6:.1f}M"
        if a >= 1_000:     return f"{a/1e3:.0f}K"
        return f"{a:.0f}"

    # ── BUY table ──────────────────────────────────────────────────
    if buy_opps:
        # cols: Strat(4) Stk(4) Win(3) In$(5) P$(5) L$(5)  total=~28
        _bh = "{:<5} {:>4} {:>3}%  {:>5} {:>5} {:>5}".format("Strat","Stk","Win","In$","P$","L$")
        _bsep = "-" * 28
        _brows = [_bh, _bsep]
        for strat, strike, pw, rr, invest, profit, loss in buy_opps:
            _s = strat.replace("BUY ","").replace("STRADDLE","STRD")[:5]
            _brows.append("{:<5} {:>4} {:>3}%  {:>5} {:>5} {:>5}".format(
                _s, f"{strike:.0f}", pw, _fk(invest), _fk(profit), _fk(loss)))
        lines.append("\n<b>BUY (pay premium)</b>\n<pre>" + "\n".join(_brows) + "</pre>")

    # ── SELL table ─────────────────────────────────────────────────
    if sell_opps:
        # cols: Strat(5) Stk(4) Win(3) Rcv$(5) Risk$(5)  total=~26
        _sh = "{:<5} {:>4} {:>3}%  {:>5} {:>5}".format("Strat","Stk","Win","Rcv$","Risk")
        _ssep = "-" * 26
        _srows = [_sh, _ssep]
        for strat, strike, pw, rr, invest, profit, loss in sell_opps:
            _s = strat.replace("SELL ","S").replace("IRON COND","ICOND")[:5]
            _srows.append("{:<5} {:>4} {:>3}%  {:>5} {:>5}".format(
                _s, f"{strike:.0f}", pw, _fk(invest), _fk(loss)))
        lines.append("\n<b>SELL (collect premium)</b>\n<pre>" + "\n".join(_srows) + "</pre>")

    if earn_flag:
        lines.append(f"<i>Earnings in {earn_dte}d — IV crush after event. Buy straddle before, sell spread after.</i>")
    lines.append("<i>In=invest P=profit L=loss · 1 contract=100sh</i>")
    return "\n".join(lines)


def _oi_week_heatmap(ticker: str, conn, spot: float, latest_date: str):
    """
    OI Heatmap: 5d / 10d / 30d timeframes × Calls / Puts = 6 panels.
    Color = OI change direction: green=building(BUY), red=fading(SELL), gray=neutral.
    Cell text = absolute OI (K/M). Gold dashed line = ATM.
    Returns BytesIO PNG or None.
    """
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import matplotlib.colors as mcolors
        import numpy as np

        BG    = "#0D1117"
        PANEL = "#161B22"
        TXT   = "#E6EDF3"
        GRID  = "#30363D"

        # ── 1. Pull last 30 trade dates ────────────────────────────────
        _dates_df = pd.read_sql("""
            SELECT DISTINCT trade_date_now FROM options_change WHERE ticker=?
            ORDER BY substr(trade_date_now,7,4)||substr(trade_date_now,1,2)||substr(trade_date_now,4,2) DESC
            LIMIT 30
        """, conn, params=(ticker,))
        if _dates_df.empty or len(_dates_df) < 2:
            return None
        all_dates = list(reversed(_dates_df["trade_date_now"].tolist()))  # oldest first

        dates_5d  = all_dates[-5:]  if len(all_dates) >= 5  else all_dates
        dates_10d = all_dates[-10:] if len(all_dates) >= 10 else all_dates

        # 30d: bucket into ~6 weekly groups (label = newest date in bucket, MM-DD)
        def _make_buckets(dates, n=6):
            if len(dates) <= n:
                return [(d[0:5], [d]) for d in dates]
            chunk = max(1, len(dates) // n)
            bkts = []
            for i in range(0, len(dates), chunk):
                grp = dates[i:i+chunk]
                bkts.append((grp[-1][0:5], grp))
            return bkts[-n:]
        bkts_30 = _make_buckets(all_dates, n=6)

        # ── 2. Active strikes: top 16 by total OI within ±15% of spot ─
        _top_stk = pd.read_sql("""
            SELECT strike,
                   SUM(openInt_Call_now) AS c_oi,
                   SUM(openInt_Put_now)  AS p_oi
            FROM options_change
            WHERE ticker=? AND trade_date_now=?
              AND strike BETWEEN ? AND ?
            GROUP BY strike
            ORDER BY (SUM(openInt_Call_now)+SUM(openInt_Put_now)) DESC
            LIMIT 16
        """, conn, params=(ticker, latest_date, spot * 0.85, spot * 1.15))
        if _top_stk.empty:
            return None
        strikes    = sorted(_top_stk["strike"].tolist())
        n_strikes  = len(strikes)
        atm_idx    = min(range(n_strikes), key=lambda i: abs(strikes[i] - spot))
        strike_lbl = [f"${s:.0f}" for s in strikes]

        # ── 3. Fetch all OI in one query ───────────────────────────────
        all_needed = sorted(set(all_dates))
        _oi_df = pd.read_sql("""
            SELECT trade_date_now, strike,
                   SUM(openInt_Call_now) AS c_oi,
                   SUM(openInt_Put_now)  AS p_oi
            FROM options_change
            WHERE ticker=? AND trade_date_now IN ({}) AND strike IN ({})
            GROUP BY trade_date_now, strike
        """.format(",".join(["?"]*len(all_needed)), ",".join(["?"]*len(strikes))),
        conn, params=([ticker] + all_needed + [float(s) for s in strikes]))

        # ── 4. Matrix builders ─────────────────────────────────────────
        def _daily_matrix(date_list):
            n_d = len(date_list)
            cm = np.zeros((n_strikes, n_d))
            pm = np.zeros((n_strikes, n_d))
            sub = _oi_df[_oi_df["trade_date_now"].isin(date_list)]
            for _, row in sub.iterrows():
                d, s = str(row["trade_date_now"]), float(row["strike"])
                if d in date_list and s in strikes:
                    cm[strikes.index(s), date_list.index(d)] = float(row["c_oi"] or 0)
                    pm[strikes.index(s), date_list.index(d)] = float(row["p_oi"] or 0)
            return cm, pm

        def _bucket_matrix(bkts):
            n_b = len(bkts)
            cm = np.zeros((n_strikes, n_b))
            pm = np.zeros((n_strikes, n_b))
            for bi, (lbl, date_grp) in enumerate(bkts):
                sub = _oi_df[_oi_df["trade_date_now"].isin(date_grp)]
                for _, row in sub.iterrows():
                    s = float(row["strike"])
                    if s in strikes:
                        si = strikes.index(s)
                        cm[si, bi] = max(cm[si, bi], float(row["c_oi"] or 0))
                        pm[si, bi] = max(pm[si, bi], float(row["p_oi"] or 0))
            return cm, pm

        c5,  p5  = _daily_matrix(dates_5d)
        c10, p10 = _daily_matrix(dates_10d)
        c30, p30 = _bucket_matrix(bkts_30)

        # ── 5. Change-based normalization (diverging) ──────────────────
        # +1 = max OI build (green/BUY), -1 = max OI fade (red/SELL), 0 = neutral
        def _chg_norm(mat):
            if mat.shape[1] < 2:
                return np.zeros_like(mat)
            delta = np.diff(mat, axis=1)
            delta = np.hstack([np.zeros((mat.shape[0], 1)), delta])
            mx = np.abs(delta).max()
            return delta / mx if mx > 0 else delta

        # Diverging colormap: dark red -> gray -> dark green
        cmap_div = mcolors.LinearSegmentedColormap.from_list(
            "oi_sig", ["#8B0000", "#CC4444", "#3A3A4A", "#2D8B2D", "#006600"], N=256)

        # ── 6. Figure: 2 rows (Call/Put) × 3 cols (5d/10d/30d) ────────
        fig_h = max(7, n_strikes * 0.40 + 3.0)
        fig, axes = plt.subplots(2, 3, figsize=(15, fig_h), facecolor=BG,
                                 gridspec_kw={"wspace": 0.06, "hspace": 0.40})

        timeframes = [
            (c5,  p5,  [d[0:5] for d in dates_5d],   "5-DAY"),
            (c10, p10, [d[0:5] for d in dates_10d],  "10-DAY"),
            (c30, p30, [b[0]   for b in bkts_30],    "30-DAY (wk)"),
        ]

        def _draw_panel(ax, mat, col_labels, tf_title, side_label, show_yticks):
            ax.set_facecolor(PANEL)
            n_d = mat.shape[1]
            chg  = _chg_norm(mat)           # in [-1, 1]
            norm = (chg + 1) / 2            # map to [0, 1] for colormap
            ax.imshow(norm, aspect="auto", cmap=cmap_div,
                      vmin=0, vmax=1, interpolation="nearest", origin="lower")

            # Cell text = absolute OI + grid lines
            for si in range(n_strikes):
                for di in range(n_d):
                    v = mat[si, di]
                    txt = (f"{v/1e6:.1f}M" if v >= 1e6 else
                           f"{v/1e3:.0f}K"  if v >= 1e3 else
                           f"{v:.0f}"        if v > 0   else "")
                    # text contrast: white on dark, dark on light cells
                    c_val = abs(chg[si, di])
                    fc = TXT if c_val < 0.6 else ("#FFFFFF" if chg[si, di] > 0 else "#FFCCCC")
                    ax.text(di, si, txt, ha="center", va="center",
                            fontsize=5.5, color=fc, fontweight="bold")

            # Grid lines between cells (box effect)
            for xi in range(n_d + 1):
                ax.axvline(xi - 0.5, color=GRID, linewidth=0.6)
            for yi in range(n_strikes + 1):
                ax.axhline(yi - 0.5, color=GRID, linewidth=0.6)

            # ATM dashed line
            ax.axhline(atm_idx, color="#FFD700", linewidth=1.8, linestyle="--", alpha=0.9)

            # Buy/Sell/Neutral badge on right (based on last column change)
            for si in range(n_strikes):
                c_last = chg[si, -1]
                if c_last > 0.25:
                    mk, col = "BUY", "#00DD66"
                elif c_last < -0.25:
                    mk, col = "SEL", "#FF4444"
                else:
                    mk, col = "NEU", "#777777"
                ax.text(n_d - 0.3, si, mk, ha="left", va="center",
                        fontsize=5.5, color=col, fontweight="bold")

            ax.set_xticks(range(n_d))
            ax.set_xticklabels(col_labels, fontsize=7, color=TXT, rotation=30)
            ax.tick_params(colors=TXT, length=0)
            ax.set_title(f"{tf_title} — {side_label}", color=TXT,
                         fontsize=9, fontweight="bold", pad=5)
            for spine in ax.spines.values():
                spine.set_edgecolor(GRID)

            if show_yticks:
                ax.set_yticks(range(n_strikes))
                ax.set_yticklabels(strike_lbl, fontsize=7.5, color=TXT)
                ax.text(-0.65, atm_idx, "ATM>", va="center", ha="right",
                        fontsize=7, color="#FFD700", fontweight="bold")
            else:
                ax.set_yticks([])

        for ci, (cm, pm, lbls, tft) in enumerate(timeframes):
            _draw_panel(axes[0, ci], cm, lbls, tft, "CALLS", ci == 0)
            _draw_panel(axes[1, ci], pm, lbls, tft, "PUTS",  ci == 0)

        # ── 7. Legend ─────────────────────────────────────────────────
        fig.text(0.5, 0.995,
                 f"{ticker}  OI Signal Heatmap  |  Spot ${spot:.1f}  |  Green=BUY  Red=SELL  Gray=NEU  Gold=ATM",
                 ha="center", va="top", fontsize=9, color=TXT, fontweight="bold")
        fig.text(0.5, 0.003,
                 "Color=OI change vs prev session  |  Cell=total OI  |  BUY=accumulating  SEL=closing/rolling",
                 ha="center", va="bottom", fontsize=7, color="#8B949E")

        plt.tight_layout(rect=[0, 0.02, 1, 0.975])
        buf = BytesIO()
        plt.savefig(buf, format="png", dpi=105, bbox_inches="tight", facecolor=BG)
        plt.close(fig)
        buf.seek(0)
        return buf
    except Exception as _e:
        log.warning(f"_oi_week_heatmap failed: {_e}")
        return None


def _oi_money_flow_chart(ticker: str, conn, spot: float, latest_date: str):
    """
    Money flow chart: call/put notional per strike (buyers vs sellers/closers).
    Returns BytesIO PNG or None.
    """
    try:
        df = pd.read_sql("""
            SELECT strike,
                   SUM(change_OI_Call) AS call_chg,
                   SUM(change_OI_Put)  AS put_chg
            FROM options_change
            WHERE ticker=? AND trade_date_now=?
            GROUP BY strike
            HAVING ABS(SUM(change_OI_Call)) + ABS(SUM(change_OI_Put)) > 100
            ORDER BY ABS(strike - ?) ASC
            LIMIT 30
        """, conn, params=(ticker, latest_date, spot))
    except Exception:
        return None
    if df.empty or spot <= 0:
        return None
    df = df[(df["strike"] >= spot * 0.80) & (df["strike"] <= spot * 1.20)].copy()
    if df.empty:
        return None
    df["_act"] = df["call_chg"].abs() + df["put_chg"].abs()
    df = df.nlargest(12, "_act").sort_values("strike").reset_index(drop=True)
    if df.empty:
        return None

    df["call_not"] = df["call_chg"] * df["strike"] * 100 / 1e6   # $M
    df["put_not"]  = df["put_chg"]  * df["strike"] * 100 / 1e6
    df["net_not"]  = df["call_not"] - df["put_not"]

    # Key levels
    try:
        _conn_kl = get_conn()
        _kl = _oi_key_levels(ticker, _conn_kl)
        _conn_kl.close()
    except Exception:
        _kl = {}
    call_wall = _kl.get("call_wall", 0)
    put_wall  = _kl.get("put_wall",  0)
    max_pain  = _kl.get("max_pain",  0)

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.patches as mpatches

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 8),
                                    gridspec_kw={"height_ratios": [3, 1.2]},
                                    facecolor="#0D1117")
    for ax in [ax1, ax2]:
        ax.set_facecolor("#161B22")
        ax.tick_params(colors="#8B949E", labelsize=8)
        for sp in ax.spines.values():
            sp.set_color("#30363D")

    strikes = df["strike"].values
    x       = list(range(len(strikes)))
    labels  = [f"${int(s)}" for s in strikes]
    bw      = 0.38

    # Separate buyers (chg>0) from closers/sellers (chg<0)
    c_buy  = df["call_not"].clip(lower=0).values
    c_sell = df["call_not"].clip(upper=0).values     # negative, shown as downward lighter bar
    p_buy  = (-df["put_not"].clip(lower=0)).values   # flip: put buyers shown below x-axis
    p_sell = (-df["put_not"].clip(upper=0)).values   # put closers shown above x-axis

    # Call bars
    ax1.bar([i - bw/2 for i in x], c_buy,  bw*0.9, color="#3FB950", alpha=0.88, label="Call Buyers")
    ax1.bar([i - bw/2 for i in x], c_sell, bw*0.9, color="#3FB950", alpha=0.30, hatch="//",  label="Call Close")
    # Put bars
    ax1.bar([i + bw/2 for i in x], p_buy,  bw*0.9, color="#F85149", alpha=0.88, label="Put Buyers")
    ax1.bar([i + bw/2 for i in x], p_sell, bw*0.9, color="#F85149", alpha=0.30, hatch="\\\\", label="Put Close")

    # Zero line
    ax1.axhline(0, color="#8B949E", linewidth=0.8, alpha=0.5)

    # Spot + wall lines
    def _strike_to_x(sv):
        if sv <= 0:
            return None
        dists = [(abs(strikes[i] - sv), i) for i in range(len(strikes))]
        _, idx = min(dists)
        return float(idx)

    spot_x = _strike_to_x(spot)
    if spot_x is not None:
        ax1.axvline(spot_x, color="#FFFFFF", linestyle="--", linewidth=1.2, alpha=0.7)
        ax1.text(spot_x + 0.1, ax1.get_ylim()[1] * 0.02 if ax1.get_ylim()[1] else 0.1,
                 f" ${spot:.0f}", color="#FFFFFF", fontsize=7)
    for wall_v, col, lbl in [(call_wall, "#58A6FF", "CWall"), (put_wall, "#FF7B00", "PWall"),
                              (max_pain, "#FFD700", "MaxPain")]:
        wx = _strike_to_x(wall_v)
        if wx is not None:
            ax1.axvline(wx, color=col, linestyle=":", linewidth=1.3, alpha=0.85, label=lbl)

    # Signal annotations
    sig_map = {}
    for _, r in df.iterrows():
        c = float(r["call_chg"]); p = float(r["put_chg"])
        if c > 300 and p > 300:    sig_map[float(r["strike"])] = ("STRD", "#BB86FC")
        elif c > 500 and p <= 100: sig_map[float(r["strike"])] = ("BULL", "#3FB950")
        elif p > 500 and c <= 100: sig_map[float(r["strike"])] = ("BEAR", "#F85149")
        elif c < -300 or p < -300: sig_map[float(r["strike"])] = ("UNWD", "#8B949E")
    for i, s in enumerate(strikes):
        if s in sig_map:
            lbl, col = sig_map[s]
            ymax = ax1.get_ylim()[1] if ax1.get_ylim()[1] else 0.5
            ax1.text(i, ymax * 0.85, lbl, color=col, fontsize=6, ha="center",
                     fontweight="bold",
                     bbox=dict(boxstyle="round,pad=0.1", facecolor="#1C2128", alpha=0.7, edgecolor=col))

    ax1.set_xticks(x)
    ax1.set_xticklabels(labels, rotation=45, ha="right", fontsize=7)
    ax1.set_ylabel("Notional $M", color="#8B949E", fontsize=8)
    ax1.set_title(f"\U0001f4b0 {ticker} Money Flow by Strike  ({latest_date})",
                  color="#FFFFFF", fontsize=10, pad=8)
    ax1.legend(loc="upper left", fontsize=6, ncol=3,
               facecolor="#1C2128", edgecolor="#30363D", labelcolor="#C9D1D9")

    # Bottom panel: net flow
    net  = df["net_not"].values
    cols = ["#3FB950" if n >= 0 else "#F85149" for n in net]
    ax2.bar(x, net, width=0.6, color=cols, alpha=0.82)
    ax2.axhline(0, color="#8B949E", linewidth=0.8, alpha=0.5)
    ax2.set_xticks(x)
    ax2.set_xticklabels(labels, rotation=45, ha="right", fontsize=7)
    ax2.set_ylabel("Net $M", color="#8B949E", fontsize=8)
    ax2.set_title("Net Flow (Call$ − Put$)  ▲=Bull bias  ▼=Bear bias",
                  color="#8B949E", fontsize=8, pad=3)

    plt.tight_layout(pad=0.9)
    buf = BytesIO()
    plt.savefig(buf, format="png", dpi=110, bbox_inches="tight",
                facecolor="#0D1117", edgecolor="none")
    plt.close(fig)
    buf.seek(0)
    return buf


def _oi_strike_breakdown(ticker: str, conn, spot: float, latest_date: str,
                         n_strikes: int = 20) -> str:
    """
    Query ±n_strikes around spot for ticker on latest_date.
    Returns formatted strike-level analysis with notional money.
    """
    try:
        df = pd.read_sql("""
            SELECT strike,
                   SUM(change_OI_Call) AS call_chg,
                   SUM(change_OI_Put)  AS put_chg,
                   SUM(openInt_Call_now) AS call_oi,
                   SUM(openInt_Put_now)  AS put_oi
            FROM options_change
            WHERE ticker = ? AND trade_date_now = ?
            GROUP BY strike
            ORDER BY ABS(strike - ?) ASC
            LIMIT ?
        """, conn, params=(ticker, latest_date, spot, n_strikes * 2))
    except Exception:
        return ""

    if df.empty or spot <= 0:
        return ""

    # Filter to ±20% of spot
    df = df[(df["strike"] >= spot * 0.80) & (df["strike"] <= spot * 1.20)]
    # Sort by total activity
    df["_act"] = df["call_chg"].abs() + df["put_chg"].abs()
    df = df[df["_act"] > 100].nlargest(8, "_act")

    # ── HV20 for BS option pricing ─────────────────────────────────
    # This gives us ACTUAL premium paid per contract, not just notional
    _hv = 0.30
    _dte_days = 21  # assume nearest expiry ~3 weeks out
    try:
        _hsd = pd.read_sql("""SELECT close FROM stock_daily WHERE ticker=?
            ORDER BY substr(trade_date,7,4)||substr(trade_date,1,2)||substr(trade_date,4,2) DESC LIMIT 25""",
            conn, params=(ticker,))
        if len(_hsd) >= 10:
            _rets = _hsd["close"].astype(float).pct_change().dropna()
            _hv = max(0.10, min(float(_rets.std() * (252**0.5)), 2.0))
        # Nearest expiry DTE
        _edt = pd.read_sql("""SELECT DISTINCT expiry_date FROM options_change WHERE ticker=?
            AND substr(expiry_date,7,4)||substr(expiry_date,1,2)||substr(expiry_date,4,2) > ?
            ORDER BY substr(expiry_date,7,4)||substr(expiry_date,1,2)||substr(expiry_date,4,2) LIMIT 1""",
            conn, params=(ticker, datetime.now().strftime("%Y%m%d")))
        if not _edt.empty:
            _dte_days = max(1, (datetime.strptime(str(_edt["expiry_date"].iloc[0]), "%m-%d-%Y") - datetime.now()).days)
    except Exception:
        pass
    _T = max(_dte_days, 1) / 365.0

    if df.empty:
        return ""

    # Aggregate PCR for hedge detection
    tot_call_oi = df["call_oi"].sum()
    tot_put_oi  = df["put_oi"].sum()
    agg_pcr = tot_put_oi / tot_call_oi if tot_call_oi > 0 else 1.0

    # ── Helpers ───────────────────────────────────────────────────────
    def _fk2(n):
        """OI change: right-aligned 5 chars."""
        if abs(n) < 10:   return "    -"
        if abs(n) >= 1e6: return f"{'+' if n>0 else '-'}{abs(n)/1e6:.1f}M"
        if abs(n) >= 1e3: return f"{'+' if n>0 else '-'}{abs(n)/1e3:.0f}K"
        return f"{n:+5.0f}"

    def _fmn(n):
        a = abs(float(n or 0))
        if a >= 1e9: return f"{a/1e9:.1f}B"
        if a >= 1e6: return f"{a/1e6:.0f}M"
        if a >= 1e3: return f"{a/1e3:.0f}K"
        return f"{a:.0f}"

    def _px_fmt(p):
        """Option price: 4-char fixed width ($ in column header)."""
        if p >= 100: return f"{p:4.0f}"
        if p >= 10:  return f"{p:4.1f}"
        return f"{p:4.2f}"

    def _sig_short(c, p, spot, strike, pcr):
        pct = (strike - spot) / spot * 100 if spot > 0 else 0
        if c > 300 and p > 300:       return "STRD"
        if c > 500 and pct > 7:       return "SPEC"
        if c > 500:                   return "BULL"
        if c < -300:                  return "UNWD"
        if p > 500 and abs(pct) <= 3: return "SHRT"
        if p > 500 and pct < -3:      return "BEAR"
        if p < -300:                  return "HOFF"
        return "FLAT"

    def _pat_short(c, p, c_oi, p_oi, spot, strike, pcr):
        pct = (strike - spot) / spot * 100 if spot > 0 else 0
        zone = "ATM" if abs(pct) <= 3 else ("OTM+" if pct > 3 else "OTM-")
        if c > 300 and p > 300:                        return "STRD", zone
        if c > 500 and c_oi > 0 and c > c_oi * 0.15:  return "INST", zone
        if c > 500 and pct > 7:                        return "SPEC", zone
        if c > 500:                                    return "BULL", zone
        if c < -300:                                   return "UNWD", zone
        if p > 500 and zone == "ATM":                  return "SHRT", zone
        if p > 500 and pct < -7 and pcr > 1.5:        return "HEDG", zone
        if p > 500:                                    return "PUT",  zone
        if p < -300:                                   return "HOFF", zone
        return "FLAT", zone

    # ── Box-table helpers (pipe + plus borders, mobile-safe) ──────────
    # All widths are fixed so every column starts at the same index.
    #
    # Table A: |Stk(5)|C-Chg(6)|P-Chg(6)|Sig(4)|  → 26 chars
    _A_SEP = "+-----+------+------+----+"
    _A_HDR = "|{:<5}|{:>6}|{:>6}|{:<4}|".format("Stk", "C-Chg", "P-Chg", "Sig")
    _A_ROW = "|{:<5}|{:>6}|{:>6}|{:<4}|"
    #
    # Table B: |Stk(5)|Zone(4)|C-Px$(5)|P-Px$(5)|  → 24 chars
    _B_SEP = "+-----+----+-----+-----+"
    _B_HDR = "|{:<5}|{:<4}|{:>5}|{:>5}|".format("Stk", "Zone", "C-Px$", "P-Px$")
    _B_ROW = "|{:<5}|{:<4}|{:>5}|{:>5}|"
    #
    # Table C: |Stk(5)|Pat(4)|C-$Paid(7)|P-$Paid(7)|  → 28 chars
    _C_SEP = "+-----+----+-------+-------+"
    _C_HDR = "|{:<5}|{:<4}|{:>7}|{:>7}|".format("Stk", "Pat", "C-$Paid", "P-$Paid")
    _C_ROW = "|{:<5}|{:<4}|{:>7}|{:>7}|"

    _t1_rows = [_A_SEP, _A_HDR, _A_SEP]
    _t2_rows = [_B_SEP, _B_HDR, _B_SEP]
    _t3_rows = [_C_SEP, _C_HDR, _C_SEP]

    for _, r in df.sort_values("strike").iterrows():
        _c   = float(r["call_chg"] or 0)
        _p   = float(r["put_chg"]  or 0)
        _sk  = float(r["strike"])
        _stk_lbl = f"${_sk:.0f}"

        # Table A: OI change + signal
        _sig = _sig_short(_c, _p, spot, _sk, agg_pcr)
        _t1_rows.append(_A_ROW.format(_stk_lbl, _fk2(_c), _fk2(_p), _sig))

        # BS option prices
        try:
            _gc = bs_greeks(spot, _sk, _T, 0.045, _hv, "call")
            _gp = bs_greeks(spot, _sk, _T, 0.045, _hv, "put")
            _cpx = float(_gc["price"]); _ppx = float(_gp["price"])
        except Exception:
            _cpx = max(0.01, spot - _sk) if spot > _sk else 0.01
            _ppx = max(0.01, _sk - spot) if _sk > spot else 0.01

        # Table B: prices
        _pat, _zone = _pat_short(_c, _p, float(r["call_oi"] or 0),
                                 float(r["put_oi"] or 0), spot, _sk, agg_pcr)
        _t2_rows.append(_B_ROW.format(_stk_lbl, _zone, _px_fmt(_cpx), _px_fmt(_ppx)))

        # Table C: actual $ premium paid today
        def _prm_fmt(v):
            if abs(v) < 500: return "      -"
            sign = "+" if v >= 0 else "-"
            return f"{sign}${_fmn(abs(v)):>5}"
        _c_prm = _c * _cpx * 100
        _p_prm = _p * _ppx * 100
        _t3_rows.append(_C_ROW.format(_stk_lbl, _pat, _prm_fmt(_c_prm), _prm_fmt(_p_prm)))

    # Close box borders
    _t1_rows.append(_A_SEP)
    _t2_rows.append(_B_SEP)
    _t3_rows.append(_C_SEP)

    _sig_legend = (
        "<i>BULL=calls  BEAR=puts  STRD=straddle\n"
        "SPEC=OTM-spec  SHRT=ATM-short\n"
        "UNWD=closing  HEDG=hedge  HOFF=hedge-off</i>"
    )
    table_str = "<pre>" + "\n".join(_t1_rows) + "</pre>\n" + _sig_legend

    detail_tbl = (
        "\n<b>Option Prices (BS est.)</b>\n"
        "<pre>" + "\n".join(_t2_rows) + "</pre>"
        "\n<b>$ Paid Today (real premium)</b>\n"
        "<pre>" + "\n".join(_t3_rows) + "</pre>\n"
        "<i>+$=new money IN  -$=money leaving  qty x price x 100</i>"
    )

    # ── OI Timeline: 5d / 10d / 30d per strike ─────────────────────
    # Three timeframes let you see short/medium/long-term accumulation.
    # Call table + Put table, each with aligned pipe-box columns.
    # Sig column: BUY/SEL/UNW/NEU — based on 5d call+put direction.
    # Emoji color line below tables: easy visual scan on mobile.
    week_tbl = ""
    try:
        _wk_dates = pd.read_sql("""
            SELECT DISTINCT trade_date_now FROM options_change WHERE ticker=?
            ORDER BY substr(trade_date_now,7,4)||substr(trade_date_now,1,2)||substr(trade_date_now,4,2) DESC
            LIMIT 30
        """, conn, params=(ticker,))
        if len(_wk_dates) >= 2:
            _all_wk    = _wk_dates["trade_date_now"].tolist()
            _active_sk = df["strike"].tolist()
            _wk_df = pd.read_sql("""
                SELECT strike, trade_date_now,
                       SUM(openInt_Call_now) AS c_oi,
                       SUM(openInt_Put_now)  AS p_oi
                FROM options_change
                WHERE ticker=? AND trade_date_now IN ({})
                  AND strike IN ({})
                GROUP BY strike, trade_date_now
            """.format(
                ",".join(["?"]*len(_all_wk)),
                ",".join(["?"]*len(_active_sk))
            ), conn, params=([ticker] + _all_wk + _active_sk))

            if not _wk_df.empty:
                def _anch(dl, n):
                    return dl[n-1] if len(dl) >= n else dl[-1]
                _d5  = _anch(_all_wk, 5);  _d10 = _anch(_all_wk, 10)
                _d30 = _anch(_all_wk, 30); _today = _all_wk[0]

                def _pct(now, old):
                    return (now - old) / max(old, 1) * 100

                def _ps4(v):
                    if v == 0.0: return "   -"
                    if abs(v) >= 999: return "+big" if v > 0 else "-big"
                    s = f"{v:+.0f}%"
                    return s[:4].rjust(4)

                def _ps5(v):
                    if v == 0.0: return "    -"
                    if abs(v) >= 9999: return "+BIG" if v > 0 else "-BIG"
                    s = f"{v:+.0f}%"
                    return s[:5].rjust(5)

                def _sig3(c5, p5):
                    if c5 > 20 and p5 > 20:  return "HDG"
                    if c5 > 15:               return "BUY"
                    if p5 > 15:               return "SEL"
                    if c5 < -15 or p5 < -15: return "UNW"
                    return "NEU"

                def _trend3(sk_data, col):
                    series = sk_data.sort_values("trade_date_now")[col].fillna(0).tolist()
                    if len(series) < 2: return " -- "
                    delta_5  = series[-1] - series[max(0, len(series)-5)]
                    delta_all = series[-1] - series[0]
                    if delta_5 > series[-1] * 0.10:  return " UP "
                    if delta_5 < -series[-1] * 0.10: return " DN "
                    if delta_all > series[0] * 0.20: return "~UP "
                    if delta_all < -series[0]*0.20:  return "~DN "
                    return " -- "

                # Table format: |Stk(5)|5d%(4)|10d%(5)|30d%(5)|Sig(3)|Trd(4)| = 1+5+1+4+1+5+1+5+1+3+1+4+1 = 32 too wide
                # Split: Call table and Put table separately
                # |Stk(5)|5d%(4)|10d%(5)|30d%(5)|Trd(3)| = 1+5+1+4+1+5+1+5+1+3+1 = 27 chars
                _WT_SEP = "+-----+----+-----+-----+---+"
                _WT_CHR = "|{:<5}|{:>4}|{:>5}|{:>5}|{:<3}|"
                _WT_PHR = "|{:<5}|{:>4}|{:>5}|{:>5}|{:<3}|"
                _WT_CHDR = _WT_CHR.format("Stk"," 5d%"," 10d%"," 30d%","Trd")
                _WT_PHDR = _WT_PHR.format("Stk"," 5d%"," 10d%"," 30d%","Trd")

                _c_rows = [_WT_SEP, _WT_CHDR, _WT_SEP]
                _p_rows = [_WT_SEP, _WT_PHDR, _WT_SEP]
                _sig_em_parts = []

                for _sk3 in sorted(_active_sk):
                    _sk_data = _wk_df[_wk_df["strike"] == _sk3]
                    if _sk_data.empty: continue
                    def _v(d, col):
                        r = _sk_data[_sk_data["trade_date_now"] == d]
                        return float(r[col].iloc[0] or 0) if not r.empty else 0.0
                    _c_now = _v(_today,"c_oi"); _p_now = _v(_today,"p_oi")
                    _c5  = _v(_d5,"c_oi");  _p5  = _v(_d5,"p_oi")
                    _c10 = _v(_d10,"c_oi"); _p10 = _v(_d10,"p_oi")
                    _c30 = _v(_d30,"c_oi"); _p30 = _v(_d30,"p_oi")
                    _stk4 = (f"")[:5]
                    _c5p = _pct(_c_now,_c5); _c10p = _pct(_c_now,_c10); _c30p = _pct(_c_now,_c30)
                    _p5p = _pct(_p_now,_p5); _p10p = _pct(_p_now,_p10); _p30p = _pct(_p_now,_p30)
                    _ct  = _trend3(_sk_data, "c_oi"); _pt  = _trend3(_sk_data, "p_oi")
                    _sg3 = _sig3(_c5p, _p5p)
                    _em  = {"BUY":"GRN","SEL":"RED","HDG":"YLW","UNW":"RED","NEU":"GRY"}.get(_sg3,"GRY")
                    _c_rows.append(_WT_CHR.format(_stk4, _ps4(_c5p), _ps5(_c10p), _ps5(_c30p), _ct.strip()[:3]))
                    _p_rows.append(_WT_PHR.format(_stk4, _ps4(_p5p), _ps5(_p10p), _ps5(_p30p), _pt.strip()[:3]))
                    _sig_em_parts.append(f"{_em}:{_stk4}")

                _c_rows.append(_WT_SEP); _p_rows.append(_WT_SEP)

                _em_map = {"GRN":"Green","RED":"Red","YLW":"Yellow","GRY":"Gray"}
                _em_line = "  ".join(_sig_em_parts)

                if len(_c_rows) > 4:
                    week_tbl = (
                        chr(10) + chr(10) + "<b>CALL OI Timeline</b>" + chr(10)
                        + "<i>5d/10d/30d % change  Trd=UP/DN/-- trend</i>" + chr(10)
                        + "<pre>" + chr(10).join(_c_rows) + "</pre>"
                        + chr(10) + "<b>PUT OI Timeline</b>" + chr(10)
                        + "<pre>" + chr(10).join(_p_rows) + "</pre>"
                        + chr(10) + "<i>GRN=calls up  RED=puts up  YLW=both  GRY=flat</i>" + chr(10)
                        + "<b>Signal: </b>" + _em_line
                    )
    except Exception as _wk_e:
        pass  # week trend is optional

    bullets = []  # kept empty — replaced by detail table above

    # ── Plain-English summary ────────────────────────────────────────
    total_call_build = df[df["call_chg"] > 0]["call_chg"].sum()
    total_put_build  = df[df["put_chg"]  > 0]["put_chg"].sum()
    total_call_unwd  = df[df["call_chg"] < 0]["call_chg"].sum()
    total_put_unwd   = df[df["put_chg"]  < 0]["put_chg"].sum()

    call_notional = (df[df["call_chg"] > 0]["call_chg"] * df[df["call_chg"] > 0]["strike"] * 100).sum()
    put_notional  = (df[df["put_chg"]  > 0]["put_chg"]  * df[df["put_chg"]  > 0]["strike"] * 100).sum()
    net_flow = call_notional - put_notional

    straddle_rows  = df[(df["call_chg"] > 300) & (df["put_chg"] > 300)]
    bear_atm_rows  = df[(df["put_chg"]  > 500) & ((df["strike"] - spot).abs() / spot <= 0.03)]
    spec_call_rows = df[(df["call_chg"] > 500) & ((df["strike"] - spot) / spot > 0.07)]

    # Determine dominant theme
    if len(straddle_rows) > 0 and net_flow > 0:
        theme = "EVENT PLAY leaning BULL"
        theme_detail = (f"Straddle activity at {len(straddle_rows)} strike(s) — market pricing a big move. "
                        f"Call flow ({_fmt_notional(call_notional)}) > Put flow ({_fmt_notional(put_notional)}) "
                        f"suggests bulls have the edge if move happens.")
    elif net_flow > 0 and call_notional > put_notional * 1.5:
        theme = "BULLISH"
        theme_detail = (f"Call money dominates: {_fmt_notional(call_notional)} vs "
                        f"{_fmt_notional(put_notional)} in puts. "
                        f"Net +{total_call_build:,.0f} calls added. "
                        + (f"Spec OTM calls (+{spec_call_rows['call_chg'].sum():,.0f}) suggest "
                           f"breakout bets above ${spec_call_rows['strike'].max():.0f}."
                           if not spec_call_rows.empty else "Institutional call accumulation near ATM."))
    elif put_notional > call_notional * 1.5:
        theme = "BEARISH / HEDGING"
        theme_detail = (f"Put money dominates: {_fmt_notional(put_notional)} vs "
                        f"{_fmt_notional(call_notional)} in calls. "
                        f"Net +{total_put_build:,.0f} puts added. "
                        + (f"ATM put surge at ${bear_atm_rows['strike'].iloc[0]:.0f} = directional shorts entering."
                           if not bear_atm_rows.empty else "Put hedging activity building."))
    elif total_call_unwd < -1000 and total_put_unwd < -1000:
        theme = "UNWINDING"
        theme_detail = ("Both calls and puts being closed — position liquidation. "
                        "No strong directional signal; participants reducing exposure.")
    else:
        theme = "MIXED / NEUTRAL"
        theme_detail = (f"Call flow {_fmt_notional(call_notional)} ≈ Put flow {_fmt_notional(put_notional)}. "
                        "No dominant side. Watch for breakout direction.")

    # Best trade idea from the data
    if not spec_call_rows.empty and net_flow > 0:
        best_strike = int(spec_call_rows.nlargest(1, "call_chg")["strike"].iloc[0])
        trade_idea = f"Breakout call above ${best_strike:.0f} (highest spec call build)"
    elif not bear_atm_rows.empty and put_notional > call_notional:
        best_strike = int(bear_atm_rows.nlargest(1, "put_chg")["strike"].iloc[0])
        trade_idea = f"ATM put at ${best_strike:.0f} (directional short entry)"
    elif not straddle_rows.empty:
        best_strike = int(straddle_rows.nlargest(1, "_act")["strike"].iloc[0])
        trade_idea = f"Straddle at ${best_strike:.0f} (both sides loading, event play)"
    else:
        best_strike = int(df.nlargest(1, "call_chg")["strike"].iloc[0]) if total_call_build > total_put_build else int(df.nlargest(1, "put_chg")["strike"].iloc[0])
        trade_idea = f"${best_strike:.0f} highest-activity strike — wait for confirmation"

    def _fk_k(n):
        a = abs(float(n or 0))
        if a >= 1e9: return f"{a/1e9:.1f}B"
        if a >= 1e6: return f"{a/1e6:.0f}M"
        if a >= 1e3: return f"{a/1e3:.0f}K"
        return f"{a:.0f}"
    _net_dir = "BULL" if net_flow >= 0 else "BEAR"
    summary_lines = [
        "",
        f"<b>📋 {theme}</b>",
        "<pre>",
        "{:<5} {:>7} {:>7}".format("Side","Amount","Ctrs"),
        "-" * 22,
        "{:<5} {:>7} {:>7}".format("Bull", _fmt_notional(call_notional), f"+{_fk_k(total_call_build)}c"),
        "{:<5} {:>7} {:>7}".format("Bear", _fmt_notional(put_notional),  f"+{_fk_k(total_put_build)}p"),
        "{:<5} {:>7} {:>7}".format("Net", _fmt_notional(abs(net_flow)),  _net_dir),
        "</pre>",
        f"<i>{theme_detail}</i>",
        f"\n💡 <b>Idea:</b> {trade_idea}",
    ]
    summary_str = "\n".join(summary_lines)

    # ── Expiry-level flow table ──────────────────────────────────────
    try:
        _exp_tbl = _oi_expiry_flow_table(ticker, conn, latest_date)
    except Exception:
        _exp_tbl = ""

    # ── Opportunity table ────────────────────────────────────────────
    try:
        _opp_tbl = _oi_opportunity_table(ticker, conn, df, spot)
    except Exception:
        _opp_tbl = ""

    extra = ""
    if _exp_tbl:
        extra += f"\n\n<b>📅 By Expiry:</b>\n{_exp_tbl}"
    if _opp_tbl:
        extra += f"\n\n{_opp_tbl}"

    return f"{table_str}\n\n{detail_tbl}{week_tbl}" + summary_str + extra


def _oi_trend_summary(ticker: str, conn, latest_date: str) -> str:
    """
    Compute 1-week (5 days) and 1-month (20 days) OI build trend.
    Returns a 2-line summary string.
    """
    try:
        # Get sorted dates
        dates_df = pd.read_sql("""
            SELECT DISTINCT trade_date_now FROM options_change WHERE ticker = ?
            ORDER BY substr(trade_date_now,7,4)||substr(trade_date_now,1,2)||substr(trade_date_now,4,2) DESC
            LIMIT 25
        """, conn, params=(ticker,))
    except Exception:
        return ""

    if dates_df.empty:
        return ""

    all_dates = dates_df["trade_date_now"].tolist()
    week_dates  = all_dates[:5]   # last 5 trading days
    month_dates = all_dates[:20]  # last 20 trading days

    def _sum_oi(dates):
        if not dates:
            return 0.0, 0.0
        placeholders = ",".join("?" * len(dates))
        try:
            r = pd.read_sql(f"""
                SELECT SUM(change_OI_Call) AS c, SUM(change_OI_Put) AS p
                FROM options_change WHERE ticker = ? AND trade_date_now IN ({placeholders})
            """, conn, params=[ticker] + dates)
            return float(r["c"].iloc[0] or 0), float(r["p"].iloc[0] or 0)
        except Exception:
            return 0.0, 0.0

    wc, wp  = _sum_oi(week_dates)
    mc, mp  = _sum_oi(month_dates)

    def _trend(c, p):
        if c > 0 and p > 0:
            return "⇔ Both sides building (straddle/vol)"
        if c > abs(p) * 1.5:
            return f"📈 Call-dominant (+{_fmt_notional(c*100):})"  # rough notional
        if p > abs(c) * 1.5:
            return f"📉 Put-dominant (+{_fmt_notional(p*100):})"
        if c > 0:
            return f"🟡 Mixed, slight call bias (C:{_fk_static(c)} P:{_fk_static(p)})"
        return f"🟡 Mixed (C:{_fk_static(c)} P:{_fk_static(p)})"

    def _fk_static(n):
        n = float(n or 0); s = "+" if n >= 0 else ""; a = abs(n)
        if a >= 1_000_000: return f"{s}{a/1_000_000:.1f}M"
        if a >= 1_000:     return f"{s}{a/1_000:.0f}K"
        return f"{s}{n:.0f}"

    def _trend_short(c, p):
        if c > 0 and p > 0:   return "STRD/VOL"
        if c > abs(p) * 1.5:  return "CALL-DOM"
        if p > abs(c) * 1.5:  return "PUT-DOM"
        if c > 0:              return "MIXED-C"
        return "MIXED-P"

    rows = []
    if len(week_dates) >= 2:
        rows.append(("1W", _fk_static(wc), _fk_static(wp), _trend_short(wc, wp)))
    if len(month_dates) >= 5:
        rows.append(("1M", _fk_static(mc), _fk_static(mp), _trend_short(mc, mp)))
    if not rows:
        return ""
    hdr_line = "{:<3} {:>6} {:>6}  {:<8}".format("Per","Call","Put","Signal")
    sep      = "-" * 27
    data_lines = ["{:<3} {:>6} {:>6}  {:<8}".format(*r) for r in rows]
    return "<pre>" + "\n".join([hdr_line, sep] + data_lines) + "</pre>"



def _oi_intent_algo(df, spot):
    """
    Classify each strike's OI delta by market intent and score overall direction.

    Zones relative to spot
        ATM        +/-3%      directional order flow
        NEAR_PUT   3-10% blow near-OTM directional bears
        DEEP_PUT   >10% below portfolio hedgers (NOT bearish direction)
        NEAR_CALL  3-7% above breakout / momentum buyers
        FAR_CALL   >7%  above covered-call writers / spec breakout

    Intent labels
        BULLISH       call build in ATM zone
        BEARISH       put build in ATM zone
        STRADDLE      both call+put at ATM (vol / event)
        NEAR_BEARISH  put build 3-10% OTM (directional short)
        HEDGE         put build >10% OTM (protective, NOT directional)
        HEDGE_UNWIND  hedge unwinding (deep-OTM put declining)
        BULLISH_BREAK call build in near/far OTM (breakout)
        COVERED_CALL  call build >7% OTM (income writing)
        UNWIND        both sides declining

    Score: (+)=bullish, (-)=bearish
        ATM calls  x2.0 | ATM puts  x-2.0
        near puts  x-1.5 (directional) | deep puts x-0.3 (hedge 70% discount)
        OTM calls  x+0.8
    """
    import numpy as np
    df = df.copy()
    df["_pct"] = (df["strike"] - spot) / spot

    ATM_BAND = 0.03
    NEAR_BAND = 0.10
    FAR_CALL  = 0.07

    _COLORS = {
        "BULLISH":       "#2E7D32",
        "BEARISH":       "#C62828",
        "STRADDLE":      "#6A1B9A",
        "NEAR_BEARISH":  "#BF360C",
        "HEDGE":         "#1565C0",
        "HEDGE_UNWIND":  "#42A5F5",
        "BULLISH_BREAK": "#388E3C",
        "COVERED_CALL":  "#F57F17",
        "UNWIND":        "#757575",
        "NEUTRAL":       "#90A4AE",
    }

    def _classify(row):
        pct = row["_pct"]
        cd  = float(row.get("call_oi_change", 0))
        pd_ = float(row.get("put_oi_change",  0))
        if abs(pct) <= ATM_BAND:
            zone = "ATM"
        elif pct < -ATM_BAND and pct >= -NEAR_BAND:
            zone = "NEAR_PUT"
        elif pct < -NEAR_BAND:
            zone = "DEEP_PUT"
        elif pct > ATM_BAND and pct <= FAR_CALL:
            zone = "NEAR_CALL"
        else:
            zone = "FAR_CALL"

        if zone == "ATM":
            if cd > 0 and pd_ > 0 and min(cd, pd_) / (abs(cd) + abs(pd_) + 1) > 0.25:
                return "STRADDLE"
            if cd > 0:  return "BULLISH"
            if pd_ > 0: return "BEARISH"
            if cd < 0 and pd_ < 0: return "UNWIND"
        elif zone == "NEAR_PUT":
            if pd_ > 0: return "NEAR_BEARISH"
            if cd > 0:  return "BULLISH_BREAK"
        elif zone == "DEEP_PUT":
            if pd_ > 0: return "HEDGE"
            if pd_ < 0: return "HEDGE_UNWIND"
            if cd > 0:  return "BULLISH_BREAK"
        elif zone == "NEAR_CALL":
            if cd > 0:  return "BULLISH_BREAK"
            if pd_ > 0: return "NEAR_BEARISH"
        elif zone == "FAR_CALL":
            if cd > 0:  return "COVERED_CALL"
        return "NEUTRAL"

    df["intent"]  = df.apply(_classify, axis=1)
    df["bar_col"] = df["intent"].map(_COLORS).fillna("#90A4AE")

    m_atm = abs(df["_pct"]) <= ATM_BAND
    m_np  = (df["_pct"] < -ATM_BAND) & (df["_pct"] >= -NEAR_BAND)
    m_dp  = df["_pct"] < -NEAR_BAND
    m_oc  = df["_pct"] > ATM_BAND

    atm_cd  = float(df.loc[m_atm, "call_oi_change"].sum())
    atm_pd  = float(df.loc[m_atm, "put_oi_change"].sum())
    nput_pd = float(df.loc[m_np,  "put_oi_change"].sum())
    dput_pd = float(df.loc[m_dp,  "put_oi_change"].sum())
    otm_cd  = float(df.loc[m_oc,  "call_oi_change"].sum())

    score  = atm_cd * 2.0 - atm_pd * 2.0 - nput_pd * 1.5 - dput_pd * 0.3 + otm_cd * 0.8
    total  = abs(atm_cd) + abs(atm_pd) + abs(nput_pd) + abs(dput_pd) + abs(otm_cd)
    thresh = max(total * 0.25, 500)
    h_ratio = dput_pd / (abs(dput_pd) + abs(nput_pd) + abs(atm_pd) + 1)

    if dput_pd > 0 and h_ratio > 0.5 and atm_cd >= 0:
        sig, sc, desc = "HEDGED BULL", "#1B5E20", "Institutions hedging longs\nCall side accumulating"
    elif score > thresh:
        sig, sc, desc = "BULLISH",    "#2E7D32", "Net call build at ATM\nBuyers entering directional longs"
    elif score > 0:
        sig, sc, desc = "MILD BULL",  "#558B2F", "Slight call bias -- watch for follow-through"
    elif score < -thresh:
        sig, sc, desc = "BEARISH",    "#B71C1C", "Net put build at ATM\nDirectional shorts increasing"
    elif score < 0:
        sig, sc, desc = "MILD BEAR",  "#BF360C", "Slight put bias -- monitor for acceleration"
    elif atm_cd > 0 and atm_pd > 0:
        sig, sc, desc = "STRADDLE",   "#6A1B9A", "Both sides building at ATM\nVol/event play expected"
    elif total < 200:
        sig, sc, desc = "QUIET",      "#455A64", "Low OI change -- no strong conviction"
    else:
        sig, sc, desc = "NEUTRAL",    "#455A64", "Balanced activity -- no directional edge"

    details = dict(atm_cd=atm_cd, atm_pd=atm_pd, nput_pd=nput_pd,
                   dput_pd=dput_pd, otm_cd=otm_cd, score=score, hedge_pct=h_ratio * 100)
    return df, sig, sc, desc, details

async def mirofish_ticker_detail(query, ticker):
    """MiroFish signal detail for a ticker from OI data."""
    _loading = await query.message.reply_text(f"🤖 Deep scan: {ticker}...", parse_mode=H)
    tk = str(ticker).upper()

    conn = get_conn()
    try:
        oc = pd.read_sql("""
            SELECT strike, expiry_date, trade_date_now,
                   change_OI_Call, change_OI_Put,
                   openInt_Call_now, openInt_Put_now,
                   pct_change_OI_Call, pct_change_OI_Put,
                   vol_Call_now, vol_Put_now,
                   lastPrice_Call_now, lastPrice_Put_now,
                   R1, S1
            FROM options_change
            WHERE ticker = ?
            ORDER BY substr(trade_date_now,7,4)||substr(trade_date_now,1,2)||substr(trade_date_now,4,2) DESC,
                     (ABS(change_OI_Call) + ABS(change_OI_Put)) DESC
            LIMIT 40
        """, conn, params=(tk,))

        sd = pd.read_sql("""
            SELECT close, pcr_oi FROM stock_daily WHERE ticker = ?
            ORDER BY substr(trade_date,7,4)||substr(trade_date,1,2)||substr(trade_date,4,2) DESC LIMIT 1
        """, conn, params=(tk,))

        _latest_oc_date = oc["trade_date_now"].iloc[0] if not oc.empty else ""
        _mw_trend = _oi_trend_summary(tk, conn, _latest_oc_date) if _latest_oc_date else ""
    except Exception as e:
        log.warning(f"mirofish_ticker_detail failed: {e}")
        oc = pd.DataFrame()
        sd = pd.DataFrame()
        _mw_trend = ""
    conn.close()
    # Keep only latest date rows for per-strike display
    if not oc.empty and "trade_date_now" in oc.columns:
        _ld = oc["trade_date_now"].iloc[0]
        oc = oc[oc["trade_date_now"] == _ld]

    parts = [hdr(f"🤖 MIROFISH · {tk}")]

    if not sd.empty:
        close = float(sd["close"].iloc[0] or 0)
        pcr = float(sd["pcr_oi"].iloc[0] or 0)
        pcr_bias_text = "Bearish" if pcr > 1.3 else ("Bullish" if pcr < 0.7 else "Neutral")
        pcr_em = "🔴" if pcr > 1.3 else ("🟢" if pcr < 0.7 else "⚪")
        parts.append(f"💲 Close: <b>${close:.2f}</b>")
        parts.append(f"{pcr_em} PCR: <b>{pcr:.2f}</b>  —  <b>{pcr_bias_text}</b>")

    if _mw_trend:
        parts.append(f"\n📅 <b>OI Build Trend (1W / 1M):</b>\n{_mw_trend}")

    if not oc.empty:
        # Top strike activity — per-expiry OI flow explanation
        parts.append("\n📊 <b>Top Strike Activity:</b>")
        _oc_sorted = oc.copy()
        _oc_sorted["_exp_key"] = _oc_sorted["expiry_date"].apply(
            lambda d: d[6:10]+d[0:2]+d[3:5] if isinstance(d, str) and len(d)==10 else "99999999")
        _expiries_s = sorted(_oc_sorted["_exp_key"].unique())
        close_for_expl = float(sd["close"].iloc[0] or 0) if not sd.empty else 0
        pcr_for_expl = float(sd["pcr_oi"].iloc[0] or 1.0) if not sd.empty and "pcr_oi" in sd.columns else 1.0
        for _eidx, _ekey in enumerate(_expiries_s[:2]):
            _elabel = "📅 Current expiry" if _eidx == 0 else "📅 Next expiry"
            _edf = _oc_sorted[_oc_sorted["_exp_key"] == _ekey]
            _edate = _edf["expiry_date"].iloc[0] if not _edf.empty else ""
            parts.append(f"\n<b>{_elabel} ({_edate}):</b>")
            for _, r in _edf.head(6).iterrows():
                _cc  = float(r.get("change_OI_Call") or 0)
                _pc  = float(r.get("change_OI_Put") or 0)
                _coi = float(r.get("openInt_Call_now") or 0)
                _poi = float(r.get("openInt_Put_now") or 0)
                _st  = float(r.get("strike") or 0)
                if abs(_cc) < 50 and abs(_pc) < 50:
                    continue
                _ins = _explain_oi_flow(_cc, _pc, _coi, _poi, close_for_expl, _st, pcr_for_expl)
                parts.append(f"  • {_ins}")


        # Aggregate direction -- hedge-aware via intent algo
        total_call_chg = oc["change_OI_Call"].sum()
        total_put_chg  = oc["change_OI_Put"].sum()
        r1 = float(oc["R1"].dropna().iloc[0]) if not oc["R1"].dropna().empty else 0
        s1 = float(oc["S1"].dropna().iloc[0]) if not oc["S1"].dropna().empty else 0
        close = float(sd["close"].iloc[0] or 0) if not sd.empty else 0

        if close > 0 and "strike" in oc.columns and len(oc) >= 2:
            _oc2 = oc.rename(columns={"change_OI_Call": "call_oi_change",
                                       "change_OI_Put":  "put_oi_change"})
            _, _sig, _sc, _desc, _dets = _oi_intent_algo(_oc2, close)
            h_pct = _dets.get("hedge_pct", 0)
            _sig_em = {"BULLISH": "📈", "MILD BULL": "📈", "BEARISH": "📉", "MILD BEAR": "📉",
                       "HEDGED BULL": "🛡", "STRADDLE": "⚡", "HEDGE": "🛡", "UNWIND": "🔄"}.get(_sig, "⚪")
            bias = f"{_sig_em} <b>{_sig}</b>  <i>hedge {h_pct:.0f}%</i>"
            _rec_map = {
                "BULLISH":     f"📈 Call buy near ${s1:.0f}" if s1 > 0 else "📈 Consider calls",
                "MILD BULL":   "📈 Mild call bias -- wait for confirmation",
                "BEARISH":     f"📉 Put buy near ${r1:.0f}" if r1 > 0 else "📉 Consider puts",
                "MILD BEAR":   "📉 Mild put bias -- watch for acceleration",
                "HEDGED BULL": "Institutions hedging longs -- OI bullish ex-hedge",
                "STRADDLE":    "Vol play -- straddle if event upcoming",
                "HEDGE":       "Deep OTM put build = protective, not directional",
                "UNWIND":      "Positions closing -- wait for fresh signal",
            }
            rec = _rec_map.get(_sig, "Monitor for clearer direction")
        else:
            _sig_l, _ = _oi_signal_light(total_call_chg, total_put_chg)
            _sig_em = "📈" if "BULL" in _sig_l else ("📉" if "BEAR" in _sig_l else "⚪")
            bias = f"{_sig_em} <b>{_sig_l}</b>"
            rec = ("📈 Consider calls" if _sig_l == "BULLISH" else
                   "📉 Consider puts"  if _sig_l == "BEARISH" else
                   "Monitor for breakout")

        if r1 > 0 and s1 > 0:
            parts.append(f"🎯 R1 <b>${r1:.1f}</b>  ·  S1 <b>${s1:.1f}</b>")
        parts.append(f"📌 Bias: {bias}")
        parts.append(f"💡 <i>{rec}</i>")

    try:
        await _loading.delete()
    except Exception:
        pass
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🤖 All Signals", callback_data="menu_mirofish"),
         InlineKeyboardButton("📊 OI Detail", callback_data=f"oi_detail_{tk}")],
        [InlineKeyboardButton("🎲 OI Rolls",     callback_data=f"oi_roll_{tk}"),
         InlineKeyboardButton("🏦 Inst. Signals", callback_data=f"inst_sig_{tk}")],
        [InlineKeyboardButton("📉 Mean Rev",     callback_data=f"mean_rev_{tk}"),
         InlineKeyboardButton("📐 Tech",         callback_data=f"tech_sig_{tk}")],
        [InlineKeyboardButton("🎯 High-Prob Engine", callback_data=f"high_prob_{tk}")],
        [BACK_BTN],
    ])
    await _safe_reply(query.message, "\n".join(parts), reply_markup=kb)



# ── OI Roll Detector ─────────────────────────────────────────
def analyze_oi_rolls(ticker, conn):
    """Detect OI position rolls across strikes and expiries.
    Returns list of dicts: velocity spikes, strike rolls, calendar rolls, risk reversals.
    Works on options_change table: one day of per-strike deltas.
    """
    tk = str(ticker).upper()
    MIN_QTY = 300

    try:
        df = pd.read_sql("""
            SELECT strike, expiry_date,
                   change_OI_Call, change_OI_Put,
                   openInt_Call_now, openInt_Call_prev,
                   openInt_Put_now,  openInt_Put_prev
            FROM options_change
            WHERE ticker = ?
              AND trade_date_now = (
                  SELECT trade_date_now FROM options_change WHERE ticker = ?
                  ORDER BY substr(trade_date_now,7,4)||substr(trade_date_now,1,2)||substr(trade_date_now,4,2) DESC
                  LIMIT 1)
            ORDER BY expiry_date, strike
        """, conn, params=(tk, tk))
    except Exception:
        return []

    if df.empty:
        return []

    df["expiry_sort"] = df["expiry_date"].apply(
        lambda d: (str(d)[6:10] + str(d)[0:2] + str(d)[3:5]) if len(str(d)) >= 10 else str(d))
    df = df.sort_values(["expiry_sort", "strike"]).reset_index(drop=True)

    for col in ["change_OI_Call", "change_OI_Put", "openInt_Call_prev", "openInt_Put_prev",
                "openInt_Call_now", "openInt_Put_now"]:
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)

    detections = []

    # 1. Velocity spikes — >100% single-day change at a strike
    for _, r in df.iterrows():
        c_prev = float(r["openInt_Call_prev"])
        p_prev = float(r["openInt_Put_prev"])
        c_chg  = float(r["change_OI_Call"])
        p_chg  = float(r["change_OI_Put"])
        st     = float(r["strike"])
        exp    = str(r["expiry_date"])
        if c_prev > 50 and abs(c_chg) / c_prev > 1.0 and abs(c_chg) >= MIN_QTY:
            detections.append({
                "type": "VELOCITY", "option": "CALL",
                "strike": st, "expiry": exp, "qty": int(c_chg),
                "pct": c_chg / c_prev * 100,
                "desc": "CALL ${:.0f} exp {}: {:+,.0f} ({:+.0f}% spike)".format(st, exp, c_chg, c_chg / c_prev * 100),
            })
        if p_prev > 50 and abs(p_chg) / p_prev > 1.0 and abs(p_chg) >= MIN_QTY:
            detections.append({
                "type": "VELOCITY", "option": "PUT",
                "strike": st, "expiry": exp, "qty": int(p_chg),
                "pct": p_chg / p_prev * 100,
                "desc": "PUT  ${:.0f} exp {}: {:+,.0f} ({:+.0f}% spike)".format(st, exp, p_chg, p_chg / p_prev * 100),
            })

    # 2. Strike rolls — within same expiry, one strike drops & another rises by similar qty
    for exp_val, grp in df.groupby("expiry_date"):
        grp = grp.reset_index(drop=True)
        for opt, col in [("CALL", "change_OI_Call"), ("PUT", "change_OI_Put")]:
            drops = grp[grp[col] < -MIN_QTY]
            rises = grp[grp[col] > MIN_QTY]
            for _, dr in drops.iterrows():
                ds = float(dr["strike"]); dq = abs(float(dr[col]))
                for _, rr in rises.iterrows():
                    rs = float(rr["strike"]); rq = float(rr[col])
                    if rs == ds:
                        continue
                    strike_pct = abs(rs - ds) / max(ds, 1)
                    qty_match  = min(dq, rq) / max(dq, rq)
                    if strike_pct <= 0.10 and qty_match >= 0.50:
                        direction = "UP" if rs > ds else "DOWN"
                        qty_matched = int(min(dq, rq))
                        detections.append({
                            "type": "STRIKE_ROLL", "option": opt,
                            "from_strike": ds, "to_strike": rs, "expiry": str(exp_val),
                            "qty": qty_matched,
                            "desc": "{} roll {}: ${:.0f}\u2192${:.0f} exp {}  ~{:,}c".format(
                                opt, direction, ds, rs, exp_val, qty_matched),
                        })

    # 3. Calendar rolls — same strike, OI drops at near-expiry, rises at far-expiry
    for st_val, sg in df.groupby("strike"):
        sg = sg.sort_values("expiry_sort").reset_index(drop=True)
        if len(sg) < 2:
            continue
        for opt, col in [("CALL", "change_OI_Call"), ("PUT", "change_OI_Put")]:
            for i in range(len(sg) - 1):
                near_chg = float(sg.iloc[i][col])
                if near_chg >= -MIN_QTY:
                    continue
                for j in range(i + 1, len(sg)):
                    far_chg = float(sg.iloc[j][col])
                    if far_chg > MIN_QTY:
                        qty = int(min(abs(near_chg), far_chg))
                        ne  = sg.iloc[i]["expiry_date"]
                        fe  = sg.iloc[j]["expiry_date"]
                        detections.append({
                            "type": "CALENDAR_ROLL", "option": opt,
                            "strike": float(st_val), "near_expiry": str(ne), "far_expiry": str(fe),
                            "qty": qty,
                            "desc": "{} cal roll: ${:.0f}  {}\u2192{}  ~{:,}c".format(
                                opt, st_val, ne, fe, qty),
                        })

    # 4. Risk reversals — per expiry: calls rise + puts fall (or vice versa)
    for exp_val, grp in df.groupby("expiry_date"):
        c_up   = float(grp[grp["change_OI_Call"] > 0]["change_OI_Call"].sum())
        c_down = float(grp[grp["change_OI_Call"] < 0]["change_OI_Call"].sum())
        p_up   = float(grp[grp["change_OI_Put"] > 0]["change_OI_Put"].sum())
        p_down = float(grp[grp["change_OI_Put"] < 0]["change_OI_Put"].sum())
        if c_up > MIN_QTY * 2 and abs(p_down) > MIN_QTY * 2:
            detections.append({
                "type": "RISK_REVERSAL", "direction": "BULL", "expiry": str(exp_val),
                "call_chg": int(c_up), "put_chg": int(p_down),
                "desc": "BULL rev exp {}: calls {:+,} / puts {:+,}".format(exp_val, int(c_up), int(p_down)),
            })
        elif p_up > MIN_QTY * 2 and abs(c_down) > MIN_QTY * 2:
            detections.append({
                "type": "RISK_REVERSAL", "direction": "BEAR", "expiry": str(exp_val),
                "call_chg": int(c_down), "put_chg": int(p_up),
                "desc": "BEAR rev exp {}: puts {:+,} / calls {:+,}".format(exp_val, int(p_up), int(c_down)),
            })

    # Deduplicate — same desc can appear if both up and down sides hit threshold
    seen = set()
    unique = []
    for d in detections:
        k = d["desc"]
        if k not in seen:
            seen.add(k)
            unique.append(d)
    return unique


async def oi_roll_detail(query, ticker):
    """OI Roll Detector Telegram handler — shows rolls, spikes & reversals."""
    tk = str(ticker).upper()
    _loading = await query.message.reply_text(f"Scanning OI rolls: {tk}...", parse_mode=H)
    conn = get_conn()
    try:
        detections = analyze_oi_rolls(tk, conn)
    except Exception as exc:
        log.warning(f"oi_roll_detail {tk}: {exc}")
        detections = []
    conn.close()

    parts = [hdr(f"OI ROLL DETECTOR -- {tk}")]

    TYPE_META = [
        ("VELOCITY",       "VELOCITY SPIKES",    ">100% single-day OI change -- new block trade"),
        ("STRIKE_ROLL",    "STRIKE ROLLS",        "OI transfers between strikes within same expiry"),
        ("CALENDAR_ROLL",  "CALENDAR ROLLS",      "OI rolled to further expiry -- duration extending"),
        ("RISK_REVERSAL",  "RISK REVERSALS",      "Calls up + puts down (or reverse) -- direction flip"),
    ]

    any_found = False
    for t_key, t_label, t_desc in TYPE_META:
        items = [d for d in detections if d["type"] == t_key]
        if not items:
            continue
        any_found = True
        parts.append(f"\n<b>{t_label}</b>")
        parts.append(f"<i>{t_desc}</i>")
        rows = ["  " + d["desc"] for d in items[:6]]
        parts.append(mono("\n".join(rows)))

    if not any_found:
        parts.append("\n<i>No significant roll activity today.</i>")
        parts.append("<i>Rolls require min 300 contracts moved.</i>")

    try:
        await _loading.delete()
    except Exception:
        pass

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("MiroFish", callback_data=f"miro_ticker_{tk}"),
         InlineKeyboardButton("OI Detail", callback_data=f"oi_detail_{tk}")],
        [BACK_BTN],
    ])
    await _safe_reply(query.message, "\n".join(parts), reply_markup=kb)


# ── Institutional Signals ─────────────────────────────────────────────────────
def analyze_inst_signals(ticker, conn):
    """6 institutional signals derived from OI data:
    1. Max Pain  2. Gamma Walls  3. Smart Money Flow
    4. Notional Conviction  5. Put Skew (Fear Gauge)  6. Pin Risk
    Returns dict with each signal's computed data.
    """
    from datetime import datetime as _dt
    tk = str(ticker).upper()
    result = {"max_pain": [], "gamma_walls": [], "smart_flow": {},
              "notional": {}, "put_skew": {}, "pin_risk": [],
              "wall_df": None, "wall_td": None}

    try:
        df = pd.read_sql("""
            SELECT strike, expiry_date,
                   openInt_Call_now, openInt_Put_now,
                   change_OI_Call, change_OI_Put,
                   lastPrice_Call_now, lastPrice_Put_now,
                   vol_Call_now, vol_Put_now,
                   trade_date_now
            FROM options_change
            WHERE ticker = ?
              AND trade_date_now = (
                  SELECT trade_date_now FROM options_change WHERE ticker = ?
                  ORDER BY substr(trade_date_now,7,4)||substr(trade_date_now,1,2)||substr(trade_date_now,4,2) DESC
                  LIMIT 1)
        """, conn, params=(tk, tk))
    except Exception:
        return result

    if df.empty:
        return result

    for c in ["openInt_Call_now", "openInt_Put_now", "change_OI_Call", "change_OI_Put",
              "lastPrice_Call_now", "lastPrice_Put_now", "vol_Call_now", "vol_Put_now"]:
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0)
    df["strike"] = pd.to_numeric(df["strike"], errors="coerce")
    df = df.dropna(subset=["strike"]).reset_index(drop=True)
    df["expiry_sort"] = df["expiry_date"].apply(
        lambda d: (str(d)[6:10] + str(d)[0:2] + str(d)[3:5]) if len(str(d)) >= 10 else str(d))
    df["total_oi"] = df["openInt_Call_now"] + df["openInt_Put_now"]

    td_str = str(df["trade_date_now"].iloc[0])
    try:
        td = _dt.strptime(td_str, "%m-%d-%Y")
    except Exception:
        td = None

    # Stash a slim copy for the compute_walls() engine used in the presentation
    # layer (call/put walls + strength, max pain, PCR, GEX regime, OI trend).
    try:
        result["wall_df"] = df[["strike", "expiry_date", "expiry_sort",
                                 "openInt_Call_now", "openInt_Put_now",
                                 "change_OI_Call", "change_OI_Put"]].copy()
        result["wall_td"] = td
    except Exception:
        pass

    # ── 1. MAX PAIN ──
    # Strike where aggregate ITM dollar-loss for options holders is MINIMISED
    # Market makers profit when options expire worthless → price gravitates here near expiry
    for exp_sort, grp in df.groupby("expiry_sort"):
        grp = grp.sort_values("strike").reset_index(drop=True)
        exp_label = str(grp["expiry_date"].iloc[0])
        dte = None
        if td is not None:
            try:
                dte = max(0, (_dt.strptime(exp_label, "%m-%d-%Y") - td).days)
            except Exception:
                pass
        best_s, best_pain = None, float("inf")
        for s in grp["strike"].values:
            itm_c = float(sum((float(s) - float(r["strike"])) * float(r["openInt_Call_now"])
                              for _, r in grp.iterrows() if float(r["strike"]) < float(s)))
            itm_p = float(sum((float(r["strike"]) - float(s)) * float(r["openInt_Put_now"])
                              for _, r in grp.iterrows() if float(r["strike"]) > float(s)))
            pain = itm_c + itm_p
            if pain < best_pain:
                best_pain = pain
                best_s = float(s)
        if best_s is not None:
            result["max_pain"].append(
                {"expiry": exp_label, "expiry_sort": str(exp_sort), "strike": best_s, "dte": dte})
    result["max_pain"].sort(key=lambda x: x["expiry_sort"])

    # ── 2. GAMMA WALLS ──
    # OI concentration strikes: dealers who sold options here delta-hedge aggressively →
    # price either gravitates toward wall (support/resistance) or gets "pinned"
    by_s = df.groupby("strike").agg(
        call_oi=("openInt_Call_now", "sum"),
        put_oi=("openInt_Put_now", "sum"),
    ).reset_index()
    by_s["total_oi"] = by_s["call_oi"] + by_s["put_oi"]
    # Filter to ±20% of OI-weighted ATM before computing mean — prevents deep-ITM
    # hedges (far from spot) from corrupting the threshold and misidentifying walls
    _atm_anchor = float((by_s["strike"] * by_s["total_oi"]).sum() / by_s["total_oi"].sum()) \
                  if by_s["total_oi"].sum() > 0 else float(by_s["strike"].median())
    _by_s_near = by_s[by_s["strike"].between(_atm_anchor * 0.80, _atm_anchor * 1.20)]
    if _by_s_near.empty:
        _by_s_near = by_s
    mean_oi = _by_s_near["total_oi"].mean()
    walls = _by_s_near[_by_s_near["total_oi"] >= mean_oi * 2.0].sort_values("total_oi", ascending=False).head(6)
    for _, w in walls.iterrows():
        c, p = float(w["call_oi"]), float(w["put_oi"])
        wtype = "CALL" if c > p * 1.5 else ("PUT" if p > c * 1.5 else "BOTH")
        result["gamma_walls"].append({
            "strike": float(w["strike"]), "call_oi": int(c), "put_oi": int(p),
            "total_oi": int(c + p), "type": wtype,
        })

    # ── 3. SMART MONEY FLOW ──
    # OI build + low vol = quiet accumulation (dark pool / off-exchange)
    # OI build + high vol = active visible entry
    # OI decline + high vol = distribution / exit
    def _flow_verdict(oi_chg, vol):
        if oi_chg > 500 and vol > 0:
            ratio = vol / max(abs(oi_chg), 1)
            return "QUIET ACCUM" if ratio < 2.0 else "ACTIVE ACCUM"
        if oi_chg < -500:
            return "DISTRIBUTION"
        if oi_chg > 200:
            return "MILD BUILD"
        if oi_chg < -200:
            return "MILD UNWIND"
        return "NEUTRAL"

    c_chg = float(df["change_OI_Call"].sum())
    p_chg = float(df["change_OI_Put"].sum())
    c_vol = float(df["vol_Call_now"].sum())
    p_vol = float(df["vol_Put_now"].sum())
    result["smart_flow"] = {
        "call_oi_chg": int(c_chg), "put_oi_chg": int(p_chg),
        "call_vol": int(c_vol),    "put_vol": int(p_vol),
        "call_verdict": _flow_verdict(c_chg, c_vol),
        "put_verdict":  _flow_verdict(p_chg, p_vol),
    }

    # ── 4. NOTIONAL CONVICTION ──
    # Institutions think in dollars not contracts.
    # $500M notional call OI outweighs $100M put OI even if put contract count is higher.
    try:
        an = pd.read_sql("""
            SELECT call_notional_oi, put_notional_oi, net_notional_oi,
                   bull_score, bear_score, avg_spot, avg_dte
            FROM us_analytics_daily WHERE ticker = ?
            ORDER BY substr(trade_date,7,4)||substr(trade_date,1,2)||substr(trade_date,4,2) DESC
            LIMIT 1
        """, conn, params=(tk,))
        if not an.empty:
            c_not  = float(an["call_notional_oi"].iloc[0] or 0)
            p_not  = float(an["put_notional_oi"].iloc[0] or 0)
            net    = float(an["net_notional_oi"].iloc[0] or 0)
            bs     = float(an["bull_score"].iloc[0] or 0)
            brs    = float(an["bear_score"].iloc[0] or 0)
            avg_sp = float(an["avg_spot"].iloc[0] or 0)
            n_rat  = c_not / p_not if p_not > 0 else 0
            bias = ("STRONG BULL" if n_rat > 1.5 else
                    "MILD BULL"   if n_rat > 1.1 else
                    "STRONG BEAR" if 0 < n_rat < 0.67 else
                    "MILD BEAR"   if 0 < n_rat < 0.9  else "NEUTRAL")
            result["notional"] = {
                "call_m": round(c_not / 1e6, 1), "put_m": round(p_not / 1e6, 1),
                "net_m": round(net / 1e6, 1),     "ratio": round(n_rat, 2),
                "bias": bias, "bull_score": round(bs, 1), "bear_score": round(brs, 1),
                "avg_spot": round(avg_sp, 2),
            }
    except Exception:
        pass

    # ── 5. PUT SKEW TERM STRUCTURE ──
    # For each expiry (nearest 4), compute ~5% OTM put/call price ratio.
    # Skips expiries where call < $0.50 (near-expiry artifact).
    spot = result["notional"].get("avg_spot", 0) if result["notional"] else 0
    if spot <= 0:
        spot = float(df["strike"].median())
    if spot > 0:
        _skew_term = []
        for exp_sort_val in sorted(df["expiry_sort"].unique()):
            if len(_skew_term) >= 4:
                break
            exp_df = df[df["expiry_sort"] == exp_sort_val].copy()
            if exp_df.empty:
                continue
            exp_df["c_dist"] = (exp_df["strike"] - spot * 1.05).abs()
            exp_df["p_dist"] = (exp_df["strike"] - spot * 0.95).abs()
            cr = exp_df.nsmallest(1, "c_dist").iloc[0]
            pr = exp_df.nsmallest(1, "p_dist").iloc[0]
            c_px = float(cr["lastPrice_Call_now"])
            p_px = float(pr["lastPrice_Put_now"])
            if c_px < 0.50 or p_px <= 0:
                continue
            skew = round(p_px / c_px, 2)
            fear = ("XFEAR" if skew > 3.0 else
                    "HFEAR" if skew > 2.0 else
                    "ELEV"  if skew > 1.2 else
                    "NORM"  if skew > 0.8 else
                    "COMPL" if skew > 0.5 else "INV")
            _skew_term.append({
                "expiry": str(exp_df["expiry_date"].iloc[0]),
                "call_strike": float(cr["strike"]), "put_strike": float(pr["strike"]),
                "call_px": c_px, "put_px": p_px, "skew": skew, "fear": fear,
            })
        if _skew_term:
            result["put_skew"] = _skew_term[0]          # keep for backward compat
            result["put_skew_term"] = _skew_term

    # ── 6. PIN RISK ──
    # Strikes with 2× average OI within 7 days of expiry.
    # Dealers hedging that OI act as a gravitational pull on price.
    if td is not None:
        mean_oi_val = float(df["total_oi"].mean())
        for _, pr in df[df["total_oi"] >= mean_oi_val * 2.0].iterrows():
            exp_str = str(pr["expiry_date"])
            try:
                dte_v = max(0, (_dt.strptime(exp_str, "%m-%d-%Y") - td).days)
            except Exception:
                dte_v = 99
            if dte_v <= 7:
                result["pin_risk"].append({
                    "expiry": exp_str, "strike": float(pr["strike"]),
                    "call_oi": int(pr["openInt_Call_now"]),
                    "put_oi":  int(pr["openInt_Put_now"]),
                    "total_oi": int(pr["total_oi"]), "dte": dte_v,
                })
        result["pin_risk"].sort(key=lambda x: (-x["dte"], -x["total_oi"]))

    return result



def _build_verdict_block(ticker, sig_data: dict) -> str:
    """
    Build analyst-style Final Verdict. Format:
    1. Verdict (UP/DOWN/MIXED) stated first
    2. Bullish Evidence bullets
    3. Bearish Evidence bullets
    4. Key Strike Levels (if provided)
    5. Earnings/Event Risk flag (if IV-HV spread >20%)
    6. Simple Summary Table (bull target vs bear target)
    7. One-line bottom line

    Accepted sig_data keys: oi_sig, pcr, notional_bias, mp_bias,
    comp_score, tech_score, iv_pct, hv_pct, spot,
    bull_target, bear_target, key_strikes (list of dicts),
    bottom_line (override string).
    """
    bull_pts   = []
    bear_pts   = []
    key_stks   = []
    event_flag = ""

    # OI signal
    oi_sig = sig_data.get("oi_sig", "")
    if "BULLISH" in oi_sig or "BULL" in oi_sig:
        bull_pts.append("Call OI building — bullish institutional flow")
    elif "BEARISH" in oi_sig or "BEAR" in oi_sig:
        bear_pts.append("Put OI building — bearish/hedge flow dominant")
    elif "STRADDLE" in oi_sig:
        bear_pts.append("Both calls & puts growing — event play, no directional conviction")
    elif "HEDGE" in oi_sig:
        bear_pts.append("Deep OTM put hedges — institutions protecting downside (not directional short)")

    # PCR
    pcr_val = float(sig_data.get("pcr", 0) or 0)
    if pcr_val > 1.3:
        bear_pts.append(f"PCR {pcr_val:.2f} — put-heavy = bearish market lean")
    elif 0 < pcr_val < 0.7:
        bull_pts.append(f"PCR {pcr_val:.2f} — call-dominated = bullish bias")
    elif 0.7 <= pcr_val <= 1.3 and pcr_val > 0:
        # Neutral PCR but check if contradicts OI signal
        if oi_sig and ("BULL" in oi_sig) and pcr_val > 1.0:
            bull_pts.append(f"⚠️ PCR {pcr_val:.2f} contradicts bullish OI build — verify direction")

    # Notional bias
    nb = sig_data.get("notional_bias", "")
    if nb == "BULL":
        bull_pts.append("Call notional OI > Put notional OI — smart money leaning long")
    elif nb == "BEAR":
        bear_pts.append("Put notional OI > Call notional OI — smart money leaning short")

    # Max Pain
    mp_bias = sig_data.get("mp_bias", "")
    mp_strike = sig_data.get("mp_strike", 0)
    if mp_bias == "above" and mp_strike:
        bull_pts.append(f"Max Pain ${mp_strike:.0f} above spot — expiry gravity pulls price UP")
        key_stks.append(f"Max Pain target: ${mp_strike:.0f} (upside magnet)")
    elif mp_bias == "below" and mp_strike:
        bear_pts.append(f"Max Pain ${mp_strike:.0f} below spot — expiry gravity pulls price DOWN")
        key_stks.append(f"Max Pain target: ${mp_strike:.0f} (downside magnet)")

    # Key gamma/call/put walls
    for ks in (sig_data.get("key_strikes") or []):
        label = ks.get("label", "")
        strike = ks.get("strike", 0)
        if strike:
            key_stks.append(f"{label}: ${strike:.0f}")

    # Mean reversion composite
    comp = float(sig_data.get("comp_score", 0) or 0)
    if comp >= 3.0:
        bull_pts.append(f"Mean Rev score +{comp:.1f} — oversold, contrarian LONG zone")
    elif comp <= -3.0:
        bear_pts.append(f"Mean Rev score {comp:.1f} — overbought, contrarian SHORT zone")

    # Tech
    ts = sig_data.get("tech_score", -1)
    if ts >= 4:
        bull_pts.append(f"Tech [{ts}/5] bullish — RSI/MACD/BB aligned upward")
    elif 0 <= ts <= 1:
        bear_pts.append(f"Tech [{ts}/5] bearish — momentum weakening")

    # IV / event risk
    iv_pct = float(sig_data.get("iv_pct", 0) or 0)
    hv_pct = float(sig_data.get("hv_pct", 0) or 0)
    if iv_pct > 0 and hv_pct > 0:
        spread = iv_pct - hv_pct
        if spread > 20:
            event_flag = (f"⚠️ <b>Event Risk:</b> IV {iv_pct:.0f}% vs HV {hv_pct:.0f}% "
                          f"(+{spread:.0f}% premium) — options are expensive. "
                          "Big move priced in; avoid buying premium unless directional conviction is high.")
    elif iv_pct > 60:
        event_flag = (f"⚠️ <b>Event Risk:</b> IV {iv_pct:.0f}% — earnings/catalyst risk. "
                      "Large move expected; direction = the key question.")

    # Verdict
    n_bull = len(bull_pts)
    n_bear = len(bear_pts)
    if n_bull == 0 and n_bear == 0 and not event_flag:
        return ""

    if n_bull > n_bear + 1:
        verdict, vem = "UP — Bullish bias", "📈"
        qualifier = " but monitor any put hedges" if any("hedge" in b.lower() for b in bear_pts) else ""
    elif n_bear > n_bull + 1:
        verdict, vem = "DOWN — Bearish bias", "📉"
        qualifier = " but call builds suggest a bounce zone" if bull_pts else ""
    elif n_bull > 0 and n_bear > 0:
        verdict, vem = "MIXED — Both sides active, caution", "↔️"
        qualifier = ""
    else:
        verdict, vem = "NEUTRAL — Insufficient signals", "⚪"
        qualifier = ""

    spot = float(sig_data.get("spot", 0) or 0)
    bull_tgt = sig_data.get("bull_target", 0)
    bear_tgt = sig_data.get("bear_target", 0)

    lines = [
        f"\n{'═'*28}",
        f"{vem} <b>FINAL VERDICT — {ticker} Direction</b>",
        f"<b>{verdict}</b>" + (f"<i>{qualifier}</i>" if qualifier else ""),
    ]

    if bull_pts:
        lines.append("\n<b>Bullish Evidence:</b>")
        lines += [f"✅ {p}" for p in bull_pts]
    if bear_pts:
        lines.append("\n<b>Bearish Evidence (can't ignore):</b>")
        lines += [f"❌ {p}" for p in bear_pts]
    if key_stks:
        lines.append("\n<b>Key Strike Levels:</b>")
        lines += [f"🎯 {s}" for s in key_stks]
    if event_flag:
        lines.append(f"\n{event_flag}")

    # Summary table
    if bull_tgt or bear_tgt or spot > 0:
        lines.append("\n<b>🎯 Simple Answer</b>")
        if bull_tgt:
            lines.append(f"✅ Bullish scenario → <b>${bull_tgt:.0f}</b> target")
        elif spot > 0 and n_bull > n_bear:
            lines.append(f"✅ Bullish scenario → <b>${spot*1.05:.0f}</b> (+5% from spot)")
        if bear_tgt:
            lines.append(f"❌ Bearish scenario → <b>${bear_tgt:.0f}</b> target")
        elif spot > 0 and n_bear >= n_bull:
            lines.append(f"❌ Bearish scenario → <b>${spot*0.95:.0f}</b> (-5% from spot)")

    # Bottom line
    bottom = sig_data.get("bottom_line", "")
    if not bottom:
        if verdict.startswith("UP"):
            bottom = f"{ticker} call flow is dominant — bias is UP, but watch put hedge levels as floor support."
        elif verdict.startswith("DOWN"):
            bottom = f"{ticker} put flow building — directional shorts increasing, lean bearish near term."
        elif verdict.startswith("MIXED"):
            bottom = f"{ticker} has two-sided flow — no clean directional bet; manage risk tightly."
        else:
            bottom = f"Insufficient {ticker} signal data for a directional call."
    lines.append(f"\n<b>Bottom Line:</b> <i>{bottom}</i>")

    return "\n".join(lines)

async def inst_signals_detail(query, ticker):
    """Institutional Signals Telegram handler."""
    tk = str(ticker).upper()
    _loading = await query.message.reply_text(f"Running institutional scan: {tk}...", parse_mode=H)
    conn = get_conn()
    try:
        sig = analyze_inst_signals(tk, conn)
    except Exception as exc:
        log.warning(f"inst_signals_detail {tk}: {exc}")
        sig = {}
    conn.close()

    spot = sig.get("notional", {}).get("avg_spot", 0) if sig.get("notional") else 0
    parts = [hdr(f"INSTITUTIONAL SIGNALS -- {tk}")]
    parts.append("<i>Max Pain = strike where most options expire worthless. Gamma Walls = high-OI strikes where dealers hedge hard, acting as price magnets/barriers.</i>")

    # 1. Max Pain
    mp_list = sig.get("max_pain", [])
    if mp_list:
        parts.append("\n<b>MAX PAIN  (Expiry Price Magnet)</b>")
        _mp_rows = []
        for mp in mp_list[:4]:
            dte_s = f"{mp['dte']}d" if mp.get("dte") is not None else "-"
            dist  = f"{(spot - mp['strike']) / spot * 100:+.1f}%" if spot > 0 else "-"
            _mp_rows.append((mp["expiry"][:8], f"${mp['strike']:.0f}", dte_s, dist))
        parts.append("\n<b>MAX PAIN  (Expiry Price Magnet)</b>\n"
                     + _pipe_table(("Expiry", "Strike", "DTE", "vs Spot"), _mp_rows, right_cols={1, 2, 3}))
        parts.append("<i>Fade moves away from max pain as expiry nears</i>")

    # 2. Gamma Walls
    walls = sig.get("gamma_walls", [])
    if walls:
        def _fk_w(n):
            return f"{n/1000:.0f}K" if n >= 1000 else str(int(n))
        _gw_rows = []
        for w in walls[:6]:
            label = "CEILING" if w["type"] == "CALL" else ("FLOOR" if w["type"] == "PUT" else "WALL")
            _gw_rows.append((f"${w['strike']:.0f}", label, _fk_w(w["total_oi"]),
                             _fk_w(w["call_oi"]), _fk_w(w["put_oi"])))
        parts.append("\n<b>GAMMA WALLS  (Dealer Hedging Levels)</b>\n"
                     + _pipe_table(("Strike", "Type", "Total OI", "C-OI", "P-OI"), _gw_rows, right_cols={2, 3, 4}))
        parts.append("<i>Price gravitates toward / stalls at these strikes</i>")

    # 3. Smart Money Flow
    sf = sig.get("smart_flow", {})
    if sf:
        def _fk_sf(n):
            a = abs(n); s = "+" if n >= 0 else "-"
            if a >= 1_000_000: return f"{s}{a/1_000_000:.1f}M"
            if a >= 1_000: return f"{s}{a/1_000:.0f}K"
            return f"{s}{a:.0f}"
        _sf_rows = [
            ("CALLS", _fk_sf(sf.get("call_oi_chg", 0)), _fk_sf(sf.get("call_vol", 0)), sf.get("call_verdict", "-")),
            ("PUTS",  _fk_sf(sf.get("put_oi_chg",  0)), _fk_sf(sf.get("put_vol",  0)), sf.get("put_verdict",  "-")),
        ]
        parts.append("\n<b>SMART MONEY FLOW</b>\n"
                     + _pipe_table(("Side", "OI Chg", "Volume", "Verdict"), _sf_rows, right_cols={1, 2}))
        cv, pv = sf.get("call_verdict", ""), sf.get("put_verdict", "")
        if "ACCUM" in cv and "DISTRIB" in pv:
            interp = "BULLISH — calls building, puts unwinding"
        elif "DISTRIB" in cv and "ACCUM" in pv:
            interp = "BEARISH — puts building, calls unwinding"
        elif "ACCUM" in cv and "ACCUM" in pv:
            interp = "EVENT / STRADDLE — both sides building"
        elif "DISTRIB" in cv and "DISTRIB" in pv:
            interp = "FULL UNWIND — institutions exiting all positions"
        else:
            interp = "Mixed activity — no clear directional conviction"
        parts.append(f"<i>{interp}</i>")

    # 4. Notional Conviction
    nt = sig.get("notional", {})
    if nt:
        _nt_rows = [
            ("Call",  f"${nt.get('call_m', 0):.1f}M", f"{nt.get('bull_score', 0):,.0f}"),
            ("Put",   f"${nt.get('put_m',  0):.1f}M", f"{nt.get('bear_score', 0):,.0f}"),
            ("Net",   f"${nt.get('net_m',  0):+.1f}M", f"{nt.get('ratio', 0):.2f}x"),
        ]
        parts.append("\n<b>NOTIONAL CONVICTION  (Dollar Weight)</b>\n"
                     + _pipe_table(("Side", "Notional", "Score"), _nt_rows, right_cols={1, 2}))
        parts.append(f"<i>Dollar bias: <b>{nt.get('bias', '')}</b></i>")

    # 5. Put Skew Term Structure
    _skew_term = sig.get("put_skew_term") or ([sig["put_skew"]] if sig.get("put_skew") else [])
    if _skew_term:
        _ps_rows = []
        for _se in _skew_term:
            _ps_rows.append((
                _se["expiry"],
                f"${_se['call_strike']:.0f}/${_se['put_strike']:.0f}",
                f"{_se['skew']:.2f}x {_se['fear']}",
            ))
        parts.append("\n<b>PUT-CALL SKEW  (Fear Gauge — Term Structure)</b>\n"
                     + _pipe_table(("Expiry", "C/P Strike", "Skew / Sentiment"), _ps_rows, right_cols={2}))
        _top_fear = _skew_term[0].get("fear", "")
        if _top_fear in ("XFEAR", "HFEAR"):
            hint = "Heavy put-premium demand — institutions hedging; often near bottoms"
        elif _top_fear in ("COMPL", "INV"):
            hint = "Cheap puts — complacency or call blow-off; watch for reversal"
        else:
            hint = "Normal cost of protection"
        parts.append(f"<i>{hint}</i>")

    # 6. Pin Risk
    pins = sig.get("pin_risk", [])
    if pins:
        _pin_rows = []
        for pin in pins[:5]:
            oi_k = f"{pin['total_oi']//1000}K" if pin['total_oi'] >= 1000 else str(pin['total_oi'])
            _pin_rows.append((f"${pin['strike']:.0f}", pin["expiry"][:8], str(pin["dte"]), oi_k))
        parts.append("\n<b>PIN RISK  (DTE \u2264 7)</b>\n"
                     + _pipe_table(("Strike", "Expiry", "DTE", "Total OI"), _pin_rows, right_cols={2, 3}))
        parts.append("<i>High OI near expiry = gravitational price pin</i>")

    if not any([mp_list, walls, sf, nt, ps, pins]):
        parts.append("\n<i>No institutional data available for this ticker.</i>")

    # ── Multi-week OI trend + strike breakdown ──────────────────────
    try:
        conn_inst = get_conn()
        _oc_dt = pd.read_sql("""SELECT DISTINCT trade_date_now FROM options_change WHERE ticker=?
            ORDER BY substr(trade_date_now,7,4)||substr(trade_date_now,1,2)||substr(trade_date_now,4,2) DESC
            LIMIT 1""", conn_inst, params=(tk,))
        _inst_latest = _oc_dt["trade_date_now"].iloc[0] if not _oc_dt.empty else ""
        _inst_spot = sig.get("notional", {}).get("avg_spot", 0) if sig.get("notional") else spot
        if _inst_latest:
            _inst_trend = _oi_trend_summary(tk, conn_inst, _inst_latest)
            if _inst_trend:
                parts.append(f"\n<b>📅 OI Build Trend (1W/1M):</b>\n{_inst_trend}")
            if _inst_spot and _inst_spot > 0:
                _inst_bd = _oi_strike_breakdown(tk, conn_inst, float(_inst_spot), _inst_latest, n_strikes=10)
                if _inst_bd:
                    parts.append(f"\n<b>🔍 Key Strike Flows:</b>\n{_inst_bd}")
        conn_inst.close()
    except Exception as _inst_ex:
        log.warning(f"inst_signals OI trend failed: {_inst_ex}")

    try:
        await _loading.delete()
    except Exception:
        pass

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("MiroFish",    callback_data=f"miro_ticker_{tk}"),
         InlineKeyboardButton("OI Rolls",   callback_data=f"oi_roll_{tk}")],
        [InlineKeyboardButton("📉 Mean Rev", callback_data=f"mean_rev_{tk}"),
         InlineKeyboardButton("OI Detail",  callback_data=f"oi_detail_{tk}")],
        [BACK_BTN],
    ])
    # Build final verdict from available signals
    _nt      = sig.get('notional', {}) or {}
    _pcr_v   = float(_nt.get('pcr', 0) or 0)
    _nb      = ('BULL' if float(_nt.get('call_notional_oi', 0) or 0) > float(_nt.get('put_notional_oi', 0) or 0)
                else ('BEAR' if float(_nt.get('put_notional_oi', 0) or 0) > 0 else ''))
    _mp_list = sig.get('max_pain', [])
    _mp_str  = float(_mp_list[0].get('strike', 0)) if _mp_list else 0
    _mp_bias = ('above' if (_mp_str and spot > 0 and _mp_str > spot) else
                ('below' if (_mp_str and spot > 0 and _mp_str < spot) else ''))
    _oi_sig_v = (_nt.get('bias', '') or '') if _nt else ''
    # Gamma walls as key strikes
    _gw_list = sig.get('gamma_walls', []) or []
    _key_stk = [{"label": f"Gamma Wall {'Call' if gw.get('side','') == 'call' else 'Put'}", "strike": float(gw.get('strike', 0))} for gw in _gw_list[:3] if gw.get('strike')]
    _vblock = _build_verdict_block(tk, {
        'oi_sig': _oi_sig_v, 'pcr': _pcr_v, 'notional_bias': _nb,
        'mp_bias': _mp_bias, 'mp_strike': _mp_str, 'spot': spot,
        'key_strikes': _key_stk,
    })
    if _vblock:
        parts.append(_vblock)
    await _safe_reply(query.message, "\n".join(parts), reply_markup=kb)


# ── Mean Reversion & Z-Score Signals ─────────────────────────────────────────
def analyze_mean_reversion(ticker, conn):
    """5 mean-reversion / z-score signals:
    1. PCR Z-Score   2. Price Z-Score   3. PCR Trend (5d rolling)
    4. Net OI Extreme (us_analytics_daily)   5. Composite oversold/overbought score
    Lookback: 20 days for z-scores, 5 days for trend.
    Returns dict with each signal populated.
    """
    tk = str(ticker).upper()
    N  = 20
    result = {"pcr_z": {}, "price_z": {}, "pcr_trend": {}, "oi_extreme": {}, "composite": {}}

    try:
        sd = pd.read_sql("""
            SELECT trade_date, close, pcr_oi FROM stock_daily WHERE ticker = ?
            ORDER BY substr(trade_date,7,4)||substr(trade_date,1,2)||substr(trade_date,4,2) DESC
            LIMIT 60
        """, conn, params=(tk,))
    except Exception:
        return result

    if sd.empty or len(sd) < 5:
        return result

    sd["close"]  = pd.to_numeric(sd["close"],  errors="coerce")
    sd["pcr_oi"] = pd.to_numeric(sd["pcr_oi"], errors="coerce")
    sd = sd.dropna(subset=["close", "pcr_oi"]).reset_index(drop=True)

    # ── 1. PCR Z-Score ──
    # High PCR = too many puts = everyone hedged = contrarian LONG signal (fade the fear)
    # Low PCR  = too many calls = complacency    = contrarian SHORT signal
    if len(sd) >= N + 1:
        pcr_today = float(sd["pcr_oi"].iloc[0])
        pcr_hist  = sd["pcr_oi"].iloc[1:N + 1]
        pcr_mean  = float(pcr_hist.mean())
        pcr_std   = float(pcr_hist.std())
        if pcr_std > 0:
            z = (pcr_today - pcr_mean) / pcr_std
            level  = ("EXTREME OVERSOLD"  if z >= 2.5 else
                      "OVERSOLD"          if z >= 1.5 else
                      "EXTREME OVERBOUGHT" if z <= -2.5 else
                      "OVERBOUGHT"        if z <= -1.5 else "NEUTRAL")
            action = ("Contrarian LONG -- put premium rich, institutions done hedging" if z >= 2.0 else
                      "Contrarian SHORT -- call premium rich, complacency high"        if z <= -2.0 else
                      "No clear mean-reversion signal from PCR")
            result["pcr_z"] = {
                "today": round(pcr_today, 3), "mean": round(pcr_mean, 3),
                "std": round(pcr_std, 3), "z": round(z, 2),
                "level": level, "action": action, "lookback": N,
            }

    # ── 2. Price Z-Score ──
    # Price deviation from 20d mean in standard deviations
    if len(sd) >= N + 1:
        px_today = float(sd["close"].iloc[0])
        px_hist  = sd["close"].iloc[1:N + 1]
        px_mean  = float(px_hist.mean())
        px_std   = float(px_hist.std())
        if px_std > 0:
            z = (px_today - px_mean) / px_std
            level  = ("OVERSOLD"    if z <= -2.0 else
                      "BELOW MEAN"  if z <= -1.0 else
                      "OVERBOUGHT"  if z >= 2.0  else
                      "ABOVE MEAN"  if z >= 1.0  else "NEAR MEAN")
            target = round(px_mean, 2)
            stop   = round(px_today - px_std, 2) if z < 0 else round(px_today + px_std, 2)
            result["price_z"] = {
                "today": round(px_today, 2), "mean20": round(px_mean, 2),
                "std20": round(px_std, 2),   "z": round(z, 2),
                "level": level, "target1": target, "stop": stop,
            }

    # ── 3. PCR Trend  (5-day rolling) ──
    # Spike up = sudden fear / event hedge. Spike down = sudden call buying / complacency.
    if len(sd) >= 6:
        pcr5_today = float(sd["pcr_oi"].iloc[0])
        pcr5_prior = float(sd["pcr_oi"].iloc[1:6].mean())
        pct_chg    = (pcr5_today - pcr5_prior) / pcr5_prior * 100 if pcr5_prior > 0 else 0
        trend = ("SPIKE UP"    if pct_chg > 50  else
                 "RISING"      if pct_chg > 20  else
                 "SPIKE DOWN"  if pct_chg < -50 else
                 "FALLING"     if pct_chg < -20 else "STABLE")
        result["pcr_trend"] = {
            "today": round(pcr5_today, 3), "avg5": round(pcr5_prior, 3),
            "pct_chg": round(pct_chg, 1), "trend": trend,
            "last5": [round(float(x), 2) for x in sd["pcr_oi"].iloc[:5].tolist()],
        }

    # ── 4. Net OI Extreme (us_analytics_daily) ──
    # Net OI = call_oi - put_oi. Extreme negative = peak bearish positioning = floor likely
    try:
        an = pd.read_sql("""
            SELECT trade_date, net_notional_oi as net_oi,
                   call_notional_oi as call_oi, put_notional_oi as put_oi
            FROM us_analytics_daily WHERE ticker = ?
            ORDER BY substr(trade_date,7,4)||substr(trade_date,1,2)||substr(trade_date,4,2) DESC
            LIMIT 30
        """, conn, params=(tk,))
        if len(an) >= 10:
            an["net_oi"] = pd.to_numeric(an["net_oi"], errors="coerce").fillna(0)
            net_today = float(an["net_oi"].iloc[0])
            net_hist  = an["net_oi"].iloc[1:21]
            n_mean    = float(net_hist.mean())
            n_std     = float(net_hist.std())
            if n_std > 0:
                z = (net_today - n_mean) / n_std
                level = ("PEAK BEARISH"  if z <= -2.0 else
                         "BEARISH LEAN"  if z <= -1.0 else
                         "PEAK BULLISH"  if z >= 2.0  else
                         "BULLISH LEAN"  if z >= 1.0  else "NEUTRAL")
                result["oi_extreme"] = {
                    "net_oi_today": int(net_today), "net_oi_mean": int(n_mean),
                    "z": round(z, 2), "level": level,
                    "call_oi": int(float(an["call_oi"].iloc[0] or 0)),
                    "put_oi":  int(float(an["put_oi"].iloc[0]  or 0)),
                }
    except Exception:
        pass

    # ── 5. Composite Score ──
    # PCR z × 1.5 (strongest signal) + inverted price z + inverted net OI z
    # Positive composite = oversold = potential LONG. Negative = overbought = potential SHORT.
    composite = 0.0
    factors   = []
    if result["pcr_z"]:
        z = result["pcr_z"]["z"]
        composite += z * 1.5
        factors.append(f"PCR z={z:+.1f}(×1.5)")
    if result["price_z"]:
        z = result["price_z"]["z"]
        composite -= z * 1.0       # invert: price below mean adds to oversold score
        factors.append(f"Price z={z:+.1f}")
    if result["oi_extreme"]:
        z = result["oi_extreme"]["z"]
        composite -= z * 1.0       # invert: negative net OI adds to oversold score
        factors.append(f"NetOI z={z:+.1f}")

    comp_level = ("STRONG OVERSOLD"   if composite >= 5.0  else
                  "OVERSOLD"          if composite >= 3.0  else
                  "STRONG OVERBOUGHT" if composite <= -5.0 else
                  "OVERBOUGHT"        if composite <= -3.0 else "NEUTRAL")

    comp_action = ""
    if composite >= 3.0 and result.get("price_z"):
        tgt  = result["price_z"]["target1"]
        stop = result["price_z"]["stop"]
        comp_action = f"LONG / CALL entry -- target ${tgt:.2f}  stop ${stop:.2f}"
    elif composite <= -3.0 and result.get("price_z"):
        tgt  = result["price_z"]["target1"]
        stop = result["price_z"]["stop"]
        comp_action = f"SHORT / PUT entry -- target ${tgt:.2f}  stop ${stop:.2f}"

    result["composite"] = {
        "score": round(composite, 2), "level": comp_level,
        "action": comp_action, "factors": factors,
    }
    return result


async def mean_rev_detail(query, ticker):
    """Mean Reversion & Z-Score Signals Telegram handler."""
    tk = str(ticker).upper()
    _loading = await query.message.reply_text(f"Computing mean reversion: {tk}...", parse_mode=H)
    conn = get_conn()
    try:
        sig = analyze_mean_reversion(tk, conn)
    except Exception as exc:
        log.warning(f"mean_rev_detail {tk}: {exc}")
        sig = {}
    conn.close()

    parts = [hdr(f"MEAN REVERSION / Z-SCORE -- {tk}")]
    parts.append("<i>PCR = Put/Call Ratio. Z-score = how far from normal (|Z|>2 = extreme). Composite &gt;+3 \u2192 consider LONG, &lt;-3 \u2192 consider SHORT.</i>")
    any_data = False

    # 1. PCR Z-Score
    pz = sig.get("pcr_z", {})
    if pz:
        any_data = True
        z   = pz["z"]
        bar = "\u2588" * min(int(abs(z) * 2), 8)
        _pz_rows = [
            ("Today PCR",  f"{pz['today']:.3f}"),
            (f"{pz['lookback']}d mean", f"{pz['mean']:.3f}"),
            ("Std Dev",    f"{pz['std']:.3f}"),
            ("Z-score",    f"{z:+.2f}  {bar}"),
            ("Level",      pz["level"]),
        ]
        parts.append("\n<b>PCR Z-SCORE</b>\n"
                     + _pipe_table(("Metric", "Value"), _pz_rows, right_cols={1}))
        parts.append(f"<i>{pz['action']}</i>")

    # 2. Price Z-Score
    prz = sig.get("price_z", {})
    if prz:
        any_data = True
        _prz_rows = [
            ("Today",   f"${prz['today']:.2f}"),
            ("20d Mean", f"${prz['mean20']:.2f}"),
            ("Std Dev",  f"${prz['std20']:.2f}"),
            ("Z-score",  f"{prz['z']:+.2f}  [{prz['level']}]"),
            ("Target",   f"${prz['target1']:.2f}"),
            ("Stop",     f"${prz['stop']:.2f}"),
        ]
        parts.append("\n<b>PRICE Z-SCORE  (20d)</b>\n"
                     + _pipe_table(("Metric", "Value"), _prz_rows, right_cols={1}))

    # 3. PCR Trend
    pt = sig.get("pcr_trend", {})
    if pt:
        any_data = True
        last5 = " \u2192 ".join(str(x) for x in pt["last5"])
        _pt_rows = [
            ("5d Avg",  f"{pt['avg5']:.3f}"),
            ("Today",   f"{pt['today']:.3f}"),
            ("Chg%",    f"{pt['pct_chg']:+.1f}%"),
            ("Trend",   pt["trend"]),
            ("Last 5",  last5),
        ]
        parts.append("\n<b>PCR TREND  (5d rolling)</b>\n"
                     + _pipe_table(("Metric", "Value"), _pt_rows, right_cols={1}))
        if "SPIKE" in pt["trend"]:
            parts.append("<i>Sudden spike \u2014 may be expiry distortion or event hedge</i>")

    # 4. Net OI Extreme
    oi = sig.get("oi_extreme", {})
    if oi:
        any_data = True
        _oi_rows = [
            ("Net OI Today", f"{oi['net_oi_today']:+,}"),
            ("20d Mean",     f"{oi['net_oi_mean']:+,}"),
            ("Z-score",      f"{oi['z']:+.2f}  [{oi['level']}]"),
            ("Call OI",      f"{oi['call_oi']:,}"),
            ("Put OI",       f"{oi['put_oi']:,}"),
        ]
        parts.append("\n<b>NET OI EXTREME  (20d)</b>\n"
                     + _pipe_table(("Metric", "Value"), _oi_rows, right_cols={1}))
        if "PEAK" in oi["level"]:
            note = ("Too many puts \u2014 peak bearish positioning, contrarian BUY zone"
                    if "BEARISH" in oi["level"] else
                    "Too many calls \u2014 peak bullish positioning, contrarian SELL zone")
            parts.append(f"<i>{note}</i>")

    # 5. Composite
    comp = sig.get("composite", {})
    if comp:
        any_data = True
        sc    = comp["score"]
        arrow = "\u25b2" if sc > 0 else "\u25bc"
        _comp_rows = [
            ("Score",   f"{sc:+.2f}  {arrow}"),
            ("Level",   comp["level"]),
            ("Inputs",  ", ".join(comp["factors"]) if comp["factors"] else "-"),
        ]
        parts.append("\n<b>COMPOSITE SCORE</b>\n"
                     + _pipe_table(("Metric", "Value"), _comp_rows))
        if comp["action"]:
            parts.append(f"<b>Trade idea:</b>  {comp['action']}")

    if not any_data:
        parts.append("\n<i>Not enough historical data for mean reversion analysis.</i>")

    try:
        await _loading.delete()
    except Exception:
        pass

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("Inst. Signals", callback_data=f"inst_sig_{tk}"),
         InlineKeyboardButton("OI Rolls",      callback_data=f"oi_roll_{tk}")],
        [InlineKeyboardButton("📐 Tech",        callback_data=f"tech_sig_{tk}"),
         InlineKeyboardButton("MiroFish",       callback_data=f"miro_ticker_{tk}")],
        [BACK_BTN],
    ])
    # Build final verdict from mean reversion composite + price z-score targets
    _comp   = sig.get('composite', {}) or {}
    _prz    = sig.get('price_z', {}) or {}
    _pcrz   = sig.get('pcr_z', {}) or {}
    _comp_score = float(_comp.get('score', 0) or 0)
    _spot_now   = float(_prz.get('today', 0) or 0)
    _bull_tgt   = float(_prz.get('target1', 0) or 0) if _comp_score >= 3 else 0
    _bear_tgt   = float(_prz.get('target1', 0) or 0) if _comp_score <= -3 else 0
    _pcr_now    = float(_pcrz.get('today', 0) or 0)
    _vblock = _build_verdict_block(tk, {
        'comp_score': _comp_score, 'comp_level': _comp.get('level', ''),
        'pcr': _pcr_now, 'spot': _spot_now,
        'bull_target': _bull_tgt, 'bear_target': _bear_tgt,
    })
    if _vblock:
        parts.append(_vblock)
    await _safe_reply(query.message, "\n".join(parts), reply_markup=kb)


# ── Technical Signals (RBI Beat component) ───────────────────────────────────
def analyze_technical_signals(ticker, conn):
    """RSI(14), MACD(12,26,9), Bollinger Bands(20,2), EMA(20).
    Computed from stock_daily close prices — no external library needed.
    Forms the 'Beat' component of the RBI trading methodology.
    Returns dict with each indicator + composite 0-5 score.
    """
    tk = str(ticker).upper()
    result = {"rsi": {}, "macd": {}, "bb": {}, "ema": {}, "composite": {}, "ticker": tk}

    try:
        sd = pd.read_sql("""
            SELECT trade_date, close FROM stock_daily WHERE ticker = ?
            ORDER BY substr(trade_date,7,4)||substr(trade_date,1,2)||substr(trade_date,4,2) ASC
            LIMIT 90
        """, conn, params=(tk,))
    except Exception:
        return result

    sd["close"] = pd.to_numeric(sd["close"], errors="coerce")
    sd = sd.dropna(subset=["close"]).reset_index(drop=True)
    if len(sd) < 30:
        return result

    closes   = sd["close"]
    px_today = float(closes.iloc[-1])
    px_prev  = float(closes.iloc[-2])
    day_chg  = (px_today - px_prev) / px_prev * 100

    # ── RSI(14) ──
    delta = closes.diff()
    gain  = delta.clip(lower=0).rolling(14).mean()
    loss  = (-delta.clip(upper=0)).rolling(14).mean()
    rsi   = float((100 - 100 / (1 + gain / loss.replace(0, 1e-10))).iloc[-1])
    rsi_level  = ("OVERBOUGHT" if rsi > 70 else "OVERSOLD" if rsi < 30 else
                  "HIGH"       if rsi > 60 else "LOW"      if rsi < 40 else "NEUTRAL")
    rsi_action = ("Fade rally -- exit longs / buy puts"  if rsi > 70 else
                  "Buy dip -- entry zone for longs"       if rsi < 30 else
                  "Watch for confirmation signal")
    result["rsi"] = {"value": round(rsi, 1), "level": rsi_level, "action": rsi_action}

    # ── MACD(12,26,9) ──
    ema12   = closes.ewm(span=12, adjust=False).mean()
    ema26   = closes.ewm(span=26, adjust=False).mean()
    macd    = ema12 - ema26
    sig_ln  = macd.ewm(span=9, adjust=False).mean()
    hist    = macd - sig_ln
    macd_v  = float(macd.iloc[-1]);  sig_v = float(sig_ln.iloc[-1])
    hist_v  = float(hist.iloc[-1]);  hist_prev = float(hist.iloc[-2])
    cross    = "BULL" if macd_v > sig_v else "BEAR"
    hist_dir = "expanding" if abs(hist_v) > abs(hist_prev) else "contracting"
    result["macd"] = {
        "macd": round(macd_v, 3), "signal": round(sig_v, 3),
        "hist": round(hist_v, 3), "cross": cross, "hist_dir": hist_dir,
    }

    # ── Bollinger Bands(20, 2σ) ──
    sma20    = closes.rolling(20).mean()
    std20    = closes.rolling(20).std()
    bb_upper = float((sma20 + 2 * std20).iloc[-1])
    bb_mid   = float(sma20.iloc[-1])
    bb_lower = float((sma20 - 2 * std20).iloc[-1])
    bb_width = bb_upper - bb_lower
    bb_pct   = (px_today - bb_lower) / bb_width * 100 if bb_width > 0 else 50
    bb_pos   = ("TOP" if px_today >= bb_upper * 0.995
                else "BOT" if px_today <= bb_lower * 1.005 else "MID")
    result["bb"] = {
        "upper": round(bb_upper, 2), "mid": round(bb_mid, 2),
        "lower": round(bb_lower, 2), "pos": bb_pos,
        "pct": round(bb_pct, 1), "price": round(px_today, 2),
    }

    # ── EMA(20) ──
    ema20   = float(closes.ewm(span=20, adjust=False).mean().iloc[-1])
    ema_rel = "ABOVE" if px_today > ema20 else "BELOW"
    ema_pct = (px_today - ema20) / ema20 * 100
    result["ema"] = {
        "ema20": round(ema20, 2), "price": round(px_today, 2),
        "rel": ema_rel, "pct": round(ema_pct, 2), "day_chg": round(day_chg, 2),
    }

    # ── Composite RBI Beat Score (0–5 bullish criteria) ──
    rsi_ok  = rsi < 70
    rsi_mo  = rsi > 50        # above midline = bullish momentum
    macd_ok = cross == "BULL"
    bb_ok   = bb_pos != "TOP"
    ema_ok  = ema_rel == "ABOVE"
    pts     = sum([rsi_ok, rsi_mo, macd_ok, bb_ok, ema_ok])
    sig_str = "BULL" if pts >= 4 else ("BEAR" if pts <= 1 else "NEUT")
    sig_conf = "STRONG" if pts in (5, 0) else ("MODERATE" if pts in (4, 1) else "WEAK")
    result["composite"] = {
        "pts": pts, "signal": sig_str, "conf": sig_conf,
        "rsi_ok": rsi_ok, "rsi_mo": rsi_mo,
        "macd_ok": macd_ok, "bb_ok": bb_ok, "ema_ok": ema_ok,
    }
    return result


async def tech_signals_detail(query, ticker):
    """Technical Signals Telegram handler — RSI, MACD, BB, EMA20 (RBI Beat)."""
    tk = str(ticker).upper()
    _loading = await query.message.reply_text(f"Computing technical signals: {tk}...", parse_mode=H)
    conn = get_conn()
    try:
        sig = analyze_technical_signals(tk, conn)
    except Exception as exc:
        log.warning(f"tech_signals_detail {tk}: {exc}")
        sig = {}
    conn.close()

    parts = [hdr(f"TECHNICAL SIGNALS (RBI) -- {tk}")]
    any_data = False

    # RSI(14)
    rsi = sig.get("rsi", {})
    if rsi:
        any_data = True
        v   = rsi["value"]
        bar = "\u2588" * int(v / 10) + "\u2591" * (10 - int(v / 10))
        parts.append("\n<b>RSI(14)</b>")
        rows = [
            "  Value:  {:.1f}  [{}]".format(v, bar),
            "  Level:  {}".format(rsi["level"]),
        ]
        parts.append(mono("\n".join(rows)))
        parts.append("<i>{}</i>".format(rsi["action"]))

    # MACD(12,26,9)
    mc = sig.get("macd", {})
    if mc:
        any_data = True
        parts.append("\n<b>MACD(12,26,9)</b>")
        rows = [
            "  MACD line: {:>+9.3f}".format(mc["macd"]),
            "  Signal:    {:>+9.3f}".format(mc["signal"]),
            "  Histogram: {:>+9.3f}  ({})".format(mc["hist"], mc["hist_dir"]),
            "  Cross:     {}".format(mc["cross"]),
        ]
        parts.append(mono("\n".join(rows)))
        if mc["cross"] == "BULL" and mc["hist_dir"] == "expanding":
            parts.append("<i>Bullish momentum accelerating</i>")
        elif mc["cross"] == "BEAR" and mc["hist_dir"] == "expanding":
            parts.append("<i>Bearish momentum accelerating</i>")
        else:
            parts.append("<i>Momentum {}</i>".format(
                "fading" if mc["hist_dir"] == "contracting" else "building"))

    # Bollinger Bands
    bb = sig.get("bb", {})
    if bb:
        any_data = True
        parts.append("\n<b>BOLLINGER BANDS(20, 2\u03c3)</b>")
        rows = [
            "  Upper: ${:>9.2f}".format(bb["upper"]),
            "  Mid:   ${:>9.2f}  (20d SMA)".format(bb["mid"]),
            "  Lower: ${:>9.2f}".format(bb["lower"]),
            "  Price: ${:>9.2f}  {:.0f}% of band  [{}]".format(
                bb["price"], bb["pct"], bb["pos"]),
        ]
        parts.append(mono("\n".join(rows)))
        if bb["pos"] == "TOP":
            parts.append("<i>At upper band -- mean reversion risk, consider fading</i>")
        elif bb["pos"] == "BOT":
            parts.append("<i>At lower band -- potential bounce, watch for reversal</i>")
        else:
            parts.append("<i>Inside bands -- normal range</i>")

    # EMA(20)
    ema = sig.get("ema", {})
    if ema:
        any_data = True
        parts.append("\n<b>EMA(20)  -- Trend Filter</b>")
        rows = [
            "  EMA20:  ${:>9.2f}".format(ema["ema20"]),
            "  Price:  ${:>9.2f}  ({:+.2f}% vs EMA)".format(
                ema["price"], ema["pct"]),
            "  Trend:  {} EMA20   Day {:+.2f}%".format(
                ema["rel"], ema["day_chg"]),
        ]
        parts.append(mono("\n".join(rows)))

    # Composite
    comp = sig.get("composite", {})
    if comp:
        any_data = True
        parts.append("\n<b>RBI BEAT SCORE  ({}/5 bullish criteria)</b>".format(comp["pts"]))
        rows = [
            "  {} RSI not overbought (<70)".format(
                "YES" if comp.get("rsi_ok") else "NO "),
            "  {} RSI above midline (>50)".format(
                "YES" if comp.get("rsi_mo") else "NO "),
            "  {} MACD bull cross".format(
                "YES" if comp.get("macd_ok") else "NO "),
            "  {} BB not at ceiling".format(
                "YES" if comp.get("bb_ok") else "NO "),
            "  {} Price above EMA20".format(
                "YES" if comp.get("ema_ok") else "NO "),
        ]
        parts.append(mono("\n".join(rows)))
        parts.append("<b>Signal: {} {}</b>".format(comp["conf"], comp["signal"]))


    # Short Interest & Float
    try:
        sd = _get_short_data(tk)
        if sd.get("squeeze_score") is not None:
            any_data = True
            spf  = sd["short_pct_float"]
            sr   = sd["short_ratio"]
            ss   = sd["shares_short"]
            flt  = sd["float_shares"]
            ssp  = sd["shares_short_prior"]
            sc   = sd["squeeze_score"]
            slbl = sd["squeeze_label"]
            def _fmt_M(n):
                if n is None: return "N/A"
                if n >= 1e9: return f"{n/1e9:.2f}B"
                if n >= 1e6: return f"{n/1e6:.1f}M"
                return f"{n:,.0f}"
            chg_s = ""
            if ss and ssp:
                chg_pct = (ss - ssp) / ssp * 100
                chg_s = f"({chg_pct:+.1f}% vs prev)"
            sq_em = "🔴" if sc >= 7 else ("🟡" if sc >= 4 else "🟢")
            rows = [
                ("Float",          _fmt_M(flt)),
                ("Shares Short",   f"{_fmt_M(ss)} {chg_s}".strip()),
                ("Short % Float",  f"{spf:.1f}%" if spf else "N/A"),
                ("Days to Cover",  f"{sr:.1f}d" if sr else "N/A"),
                ("Squeeze Score",  f"{sc}/10  {sq_em} {slbl}"),
            ]
            parts.append(chr(10) + "<b>SHORT INTEREST & FLOAT</b>")
            parts.append(_pipe_table(("Metric", "Value"), rows, right_cols={1}))
            if sc >= 7:
                parts.append("<i>High short interest — squeeze risk if bullish catalyst hits</i>")
            elif sc >= 4:
                parts.append("<i>Moderate short interest — watch for covering rallies</i>")
    except Exception:
        pass

    if not any_data:
        parts.append("\n<i>Not enough price history (need 30+ days in stock_daily).</i>")

    try:
        await _loading.delete()
    except Exception:
        pass

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("Mean Rev",    callback_data=f"mean_rev_{tk}"),
         InlineKeyboardButton("Inst. Sig",   callback_data=f"inst_sig_{tk}")],
        [InlineKeyboardButton("MiroFish",    callback_data=f"miro_ticker_{tk}"),
         BACK_BTN],
    ])
    await _safe_reply(query.message, "\n".join(parts), reply_markup=kb)


async def posadd_ticker_menu(query, ctx, page=0, reset=False):
    if reset:
        ctx.user_data["posadd"] = {}
    tickers = _ticker_universe(limit=1000)
    kb = _paged_ticker_keyboard("posaddtk", tickers, page=page, per_page=12, cols=3, include_back=True, back_cb="menu_positions")
    await query.message.reply_text(f"{hdr('➕ ADD POSITION')}\n\nStep 1/8: Select ticker", parse_mode=H, reply_markup=kb)


async def posadd_option_type_menu(query, ctx, ticker):
    tk = str(ticker).upper().strip()
    st = ctx.user_data.get("posadd", {})
    st["ticker"] = tk
    ctx.user_data["posadd"] = st
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("CALL", callback_data="posaddot_call"), InlineKeyboardButton("PUT", callback_data="posaddot_put")],
        [InlineKeyboardButton("⬅️ Tickers", callback_data="posadd_start"), BACK_BTN],
    ])
    await query.message.reply_text(f"{hdr('➕ ADD POSITION')}\n\nStep 2/8: {tk} · Option type", parse_mode=H, reply_markup=kb)


async def posadd_expiry_menu(query, ctx, page=0):
    st = ctx.user_data.get("posadd", {})
    tk = st.get("ticker")
    ot = st.get("opt_type")
    if not tk or not ot:
        await query.message.reply_text("⚠️ Restart Add Position.", reply_markup=InlineKeyboardMarkup([[BACK_BTN]]))
        return
    exps = _get_option_expiries(tk)
    st["expiries"] = exps
    ctx.user_data["posadd"] = st
    if not exps:
        await query.message.reply_text(f"❌ No option expiries found for {tk}.", reply_markup=InlineKeyboardMarkup([[BACK_BTN]]))
        return

    per_page = 12
    max_page = max((len(exps) - 1) // per_page, 0)
    page = max(0, min(page, max_page))
    cur = exps[page * per_page : (page + 1) * per_page]
    rows = []
    for i in range(0, len(cur), 3):
        chunk = cur[i : i + 3]
        rows.append([InlineKeyboardButton(x, callback_data=f"posaddexp_{page * per_page + i + j}") for j, x in enumerate(chunk)])
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"posaddexpg_{page - 1}"))
    nav.append(InlineKeyboardButton(f"{page + 1}/{max_page + 1}", callback_data="noop"))
    if page < max_page:
        nav.append(InlineKeyboardButton("Next ➡️", callback_data=f"posaddexpg_{page + 1}"))
    rows.append(nav)
    rows.append([InlineKeyboardButton("⬅️ Type", callback_data="posadd_back_type"), BACK_BTN])
    await query.message.reply_text(
        f"{hdr('➕ ADD POSITION')}\n\nStep 3/8: {tk} {ot.upper()} · Expiry date",
        parse_mode=H,
        reply_markup=InlineKeyboardMarkup(rows),
    )


async def posadd_strike_menu(query, ctx, page=0):
    st = ctx.user_data.get("posadd", {})
    tk = st.get("ticker")
    ot = st.get("opt_type")
    exp = st.get("expiry")
    if not tk or not ot or not exp:
        await query.message.reply_text("⚠️ Restart Add Position.", reply_markup=InlineKeyboardMarkup([[BACK_BTN]]))
        return

    spot = 100.0
    try:
        h = yf.Ticker(tk).history(period="5d")
        if len(h) >= 1:
            spot = float(h["Close"].iloc[-1])
    except Exception:
        pass

    strikes = _get_option_strikes(tk, exp, ot)
    if not strikes:
        await query.message.reply_text("❌ No strikes found for selected expiry.", reply_markup=InlineKeyboardMarkup([[BACK_BTN]]))
        return
    st["strikes"] = strikes
    ctx.user_data["posadd"] = st

    per_page = 12
    max_page = max((len(strikes) - 1) // per_page, 0)
    page = max(0, min(page, max_page))
    cur = strikes[page * per_page : (page + 1) * per_page]
    rows = []
    for i in range(0, len(cur), 3):
        chunk = cur[i : i + 3]
        rows.append([
            InlineKeyboardButton(f"${x:.2f}" if x % 1 else f"${x:.0f}", callback_data=f"posaddsk_{page * per_page + i + j}")
            for j, x in enumerate(chunk)
        ])
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"posaddskpg_{page - 1}"))
    nav.append(InlineKeyboardButton(f"{page + 1}/{max_page + 1}", callback_data="noop"))
    if page < max_page:
        nav.append(InlineKeyboardButton("Next ➡️", callback_data=f"posaddskpg_{page + 1}"))
    rows.append(nav)
    rows.append([InlineKeyboardButton("⬅️ Expiry", callback_data="posadd_back_expiry"), BACK_BTN])
    await query.message.reply_text(
        f"{hdr('➕ ADD POSITION')}\n\nStep 4/8: {tk} {ot.upper()} · Strike\nSpot: ${spot:.2f}",
        parse_mode=H,
        reply_markup=InlineKeyboardMarkup(rows),
    )


async def posadd_side_menu(query):
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🟢 BUY", callback_data="posaddsd_buy"), InlineKeyboardButton("🔴 SELL", callback_data="posaddsd_sell")],
        [InlineKeyboardButton("⬅️ Strike", callback_data="posadd_back_strike"), BACK_BTN],
    ])
    await query.message.reply_text(f"{hdr('➕ ADD POSITION')}\n\nStep 5/8: Buy or Sell?", parse_mode=H, reply_markup=kb)


async def posadd_qty_menu(query):
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("x1", callback_data="posaddqty_1"), InlineKeyboardButton("x2", callback_data="posaddqty_2"), InlineKeyboardButton("x5", callback_data="posaddqty_5")],
        [InlineKeyboardButton("x10", callback_data="posaddqty_10"), InlineKeyboardButton("x20", callback_data="posaddqty_20")],
        [InlineKeyboardButton("⬅️ Side", callback_data="posadd_back_side"), BACK_BTN],
    ])
    await query.message.reply_text(f"{hdr('➕ ADD POSITION')}\n\nStep 6/8: Quantity", parse_mode=H, reply_markup=kb)


async def posadd_day_menu(query):
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("Today", callback_data="posaddday_0"), InlineKeyboardButton("1d Ago", callback_data="posaddday_1"), InlineKeyboardButton("2d Ago", callback_data="posaddday_2")],
        [InlineKeyboardButton("5d Ago", callback_data="posaddday_5")],
        [InlineKeyboardButton("⬅️ Qty", callback_data="posadd_back_qty"), BACK_BTN],
    ])
    await query.message.reply_text(f"{hdr('➕ ADD POSITION')}\n\nStep 7/8: Entry day", parse_mode=H, reply_markup=kb)


async def posadd_price_menu(query, ctx=None):
    """Show price step with actual bid/ask values. Highlights recommended price for BUY vs SELL."""
    st_data = (ctx.user_data.get("posadd", {}) if ctx else {})
    tk = st_data.get("ticker", "")
    ot = st_data.get("opt_type", "call")
    strike = _safe_float(st_data.get("strike", 0), 0)
    exp = st_data.get("expiry", "")
    side = st_data.get("side", "buy")

    # Fetch live bid/ask/mid
    bid_v = mid_v = ask_v = None
    try:
        if tk and exp and strike > 0:
            oc = _option_chain_snapshot(tk, exp, ot)
            if oc is not None and not oc.empty:
                m = oc[oc["strike"] == strike]
                if m.empty:
                    oc["_d"] = (oc["strike"] - strike).abs()
                    m = oc.nsmallest(1, "_d")
                if not m.empty:
                    row = m.iloc[0]
                    bid_v = _safe_float(row.get("bid", 0), 0) or None
                    ask_v = _safe_float(row.get("ask", 0), 0) or None
                    if bid_v and ask_v:
                        mid_v = round((bid_v + ask_v) / 2, 2)
    except Exception:
        pass

    # Labels with actual prices
    bid_lbl = f"Bid ${bid_v:.2f}" if bid_v else "Bid"
    mid_lbl = f"Mid ${mid_v:.2f}" if mid_v else "Mid"
    ask_lbl = f"Ask ${ask_v:.2f}" if ask_v else "Ask"

    # For SELL: default is Bid. For BUY: default is Ask.
    if side == "sell":
        bid_lbl = bid_lbl + " [*]"
        hint = "SELL position — you receive the Bid price [*]"
    else:
        ask_lbl = ask_lbl + " [*]"
        hint = "BUY position — you pay the Ask price [*]"

    kb_rows = [
        [InlineKeyboardButton(bid_lbl, callback_data="posaddpx_bid"),
         InlineKeyboardButton(mid_lbl, callback_data="posaddpx_mid"),
         InlineKeyboardButton(ask_lbl, callback_data="posaddpx_ask")],
    ]
    if mid_v:
        adj_row = []
        for delta in [-0.25, -0.10, +0.10, +0.25]:
            adj_px = max(0.01, round(mid_v + delta, 2))
            adj_row.append(InlineKeyboardButton(f"${adj_px:.2f}", callback_data=f"posaddpx_custom_{adj_px}"))
        kb_rows.append(adj_row)
    kb_rows.append([InlineKeyboardButton("⬅️ Day", callback_data="posadd_back_day"), BACK_BTN])

    await query.message.reply_text(
        f"{hdr('➕ ADD POSITION')}\n\nStep 8/8: Entry price\n<i>{hint}</i>",
        parse_mode=H, reply_markup=InlineKeyboardMarkup(kb_rows)
    )


async def posadd_confirm_menu(query, ctx):
    st = ctx.user_data.get("posadd", {})
    tk = st.get("ticker")
    ot = st.get("opt_type")
    exp = st.get("expiry")
    strike = _safe_float(st.get("strike", 0), 0)
    qty = _safe_int(st.get("qty", 1), 1)
    side = st.get("side", "buy")
    day_offset = _safe_int(st.get("day_offset", 0), 0)
    px_mode = st.get("px_mode", "mid")
    if not tk or not ot or not exp or strike <= 0:
        await query.message.reply_text("⚠️ Incomplete position config. Restart Add Position.", reply_markup=InlineKeyboardMarkup([[BACK_BTN]]))
        return

    # Custom price already set by custom button, otherwise fetch from market
    if px_mode == "custom" and st.get("entry_price", 0) > 0:
        est = _safe_float(st["entry_price"], 1.0)
    else:
        est = _option_price_by_mode(tk, ot, strike, exp, mode=px_mode, fallback=1.00)
        st["entry_price"] = est
    st["entry_date"] = (datetime.now().date() - timedelta(days=max(0, day_offset))).strftime("%Y-%m-%d")
    ctx.user_data["posadd"] = st

    signed_qty = qty if side == "buy" else -qty
    px_src = f"${est:.2f} (custom)" if px_mode == "custom" else f"${est:.2f} ({px_mode})"
    msg = (
        f"{hdr('✅ CONFIRM NEW POSITION')}\n\n"
        + mono(
            f"{row2('Ticker', tk)}\n"
            f"{row2('Type', ot.upper())}\n"
            f"{row2('Side', side.upper())}\n"
            f"{row2('Strike', f'${strike:.2f}')}\n"
            f"{row2('Expiry', exp)}\n"
            f"{row2('Qty', str(signed_qty))}\n"
            f"{row2('Entry Day', st['entry_date'])}\n"
            f"{row2('Entry Px', px_src)}\n"
            f"{'─'*27}\n"
            + _single_leg_risk_text(side, ot, strike, est, qty)
        )
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Add", callback_data="posaddgo"), InlineKeyboardButton("❌ Cancel", callback_data="menu_positions")],
        [InlineKeyboardButton("⬅️ Price", callback_data="posadd_back_price"), BACK_BTN],
    ])
    await _safe_reply(query.message, msg, reply_markup=kb)


async def pair_ticker_menu(query, ctx, page=0):
    st = ctx.user_data.get("pairwiz", {})
    tickers = _ticker_universe(limit=1000)
    kb = _paged_ticker_keyboard("pairtk", tickers, page=page, per_page=12, cols=3, include_back=True, back_cb=f"pos_{_safe_int(st.get('parent_id', 0), 0)}")
    await query.message.reply_text(f"{hdr('🧩 PAIR LEG BUILDER')}\n\nStep 1/8: Select ticker", parse_mode=H, reply_markup=kb)


async def pair_option_type_menu(query):
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("CALL", callback_data="pairot_call"), InlineKeyboardButton("PUT", callback_data="pairot_put")],
        [InlineKeyboardButton("⬅️ Ticker", callback_data="pair_back_ticker"), BACK_BTN],
    ])
    await query.message.reply_text(f"{hdr('🧩 PAIR LEG BUILDER')}\n\nStep 2/8: Option type", parse_mode=H, reply_markup=kb)


async def pair_expiry_menu(query, ctx, page=0):
    st = ctx.user_data.get("pairwiz", {})
    tk = st.get("ticker")
    if not tk:
        await query.message.reply_text("⚠️ Restart Pair Builder.", reply_markup=InlineKeyboardMarkup([[BACK_BTN]]))
        return
    exps = _get_option_expiries(tk)
    st["expiries"] = exps
    ctx.user_data["pairwiz"] = st
    if not exps:
        await query.message.reply_text(f"❌ No option expiries found for {tk}.", reply_markup=InlineKeyboardMarkup([[BACK_BTN]]))
        return
    per_page = 12
    max_page = max((len(exps) - 1) // per_page, 0)
    page = max(0, min(page, max_page))
    cur = exps[page * per_page : (page + 1) * per_page]
    rows = []
    for i in range(0, len(cur), 3):
        chunk = cur[i : i + 3]
        rows.append([InlineKeyboardButton(x, callback_data=f"pairexp_{page * per_page + i + j}") for j, x in enumerate(chunk)])
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"pairexpg_{page - 1}"))
    nav.append(InlineKeyboardButton(f"{page + 1}/{max_page + 1}", callback_data="noop"))
    if page < max_page:
        nav.append(InlineKeyboardButton("Next ➡️", callback_data=f"pairexpg_{page + 1}"))
    rows.append(nav)
    rows.append([InlineKeyboardButton("⬅️ Type", callback_data="pair_back_type"), BACK_BTN])
    await query.message.reply_text(f"{hdr('🧩 PAIR LEG BUILDER')}\n\nStep 3/8: Expiry date", parse_mode=H, reply_markup=InlineKeyboardMarkup(rows))


async def pair_strike_menu(query, ctx, page=0):
    st = ctx.user_data.get("pairwiz", {})
    tk, ot, exp = st.get("ticker"), st.get("opt_type"), st.get("expiry")
    if not tk or not ot or not exp:
        await query.message.reply_text("⚠️ Restart Pair Builder.", reply_markup=InlineKeyboardMarkup([[BACK_BTN]]))
        return
    spot = 100.0
    try:
        h = yf.Ticker(tk).history(period="5d")
        if len(h) >= 1:
            spot = float(h["Close"].iloc[-1])
    except Exception:
        pass

    strikes = _get_option_strikes(tk, exp, ot)
    if not strikes:
        await query.message.reply_text("❌ No strikes found for selected expiry.", reply_markup=InlineKeyboardMarkup([[BACK_BTN]]))
        return
    st["strikes"] = strikes
    ctx.user_data["pairwiz"] = st

    per_page = 12
    max_page = max((len(strikes) - 1) // per_page, 0)
    page = max(0, min(page, max_page))
    cur = strikes[page * per_page : (page + 1) * per_page]
    rows = []
    for i in range(0, len(cur), 3):
        chunk = cur[i : i + 3]
        rows.append([
            InlineKeyboardButton(f"${x:.2f}" if x % 1 else f"${x:.0f}", callback_data=f"pairsk_{page * per_page + i + j}")
            for j, x in enumerate(chunk)
        ])
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"pairskpg_{page - 1}"))
    nav.append(InlineKeyboardButton(f"{page + 1}/{max_page + 1}", callback_data="noop"))
    if page < max_page:
        nav.append(InlineKeyboardButton("Next ➡️", callback_data=f"pairskpg_{page + 1}"))
    rows.append(nav)
    rows.append([InlineKeyboardButton("⬅️ Expiry", callback_data="pair_back_expiry"), BACK_BTN])
    await query.message.reply_text(
        f"{hdr('🧩 PAIR LEG BUILDER')}\n\nStep 4/8: Strike\nSpot: ${spot:.2f}",
        parse_mode=H,
        reply_markup=InlineKeyboardMarkup(rows),
    )


async def pair_side_menu(query):
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🟢 BUY", callback_data="pairside_buy"), InlineKeyboardButton("🔴 SELL", callback_data="pairside_sell")],
        [InlineKeyboardButton("⬅️ Strike", callback_data="pair_back_strike"), BACK_BTN],
    ])
    await query.message.reply_text(f"{hdr('🧩 PAIR LEG BUILDER')}\n\nStep 5/8: Pair leg side", parse_mode=H, reply_markup=kb)


async def pair_qty_menu(query):
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("x1", callback_data="pairqty_1"), InlineKeyboardButton("x2", callback_data="pairqty_2"), InlineKeyboardButton("x5", callback_data="pairqty_5")],
        [InlineKeyboardButton("x10", callback_data="pairqty_10")],
        [InlineKeyboardButton("⬅️ Side", callback_data="pair_back_side"), BACK_BTN],
    ])
    await query.message.reply_text(f"{hdr('🧩 PAIR LEG BUILDER')}\n\nStep 6/8: Quantity", parse_mode=H, reply_markup=kb)


async def pair_day_menu(query):
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("Today", callback_data="pairday_0"), InlineKeyboardButton("1d Ago", callback_data="pairday_1"), InlineKeyboardButton("2d Ago", callback_data="pairday_2")],
        [InlineKeyboardButton("5d Ago", callback_data="pairday_5")],
        [InlineKeyboardButton("⬅️ Qty", callback_data="pair_back_qty"), BACK_BTN],
    ])
    await query.message.reply_text(f"{hdr('🧩 PAIR LEG BUILDER')}\n\nStep 7/8: Entry day", parse_mode=H, reply_markup=kb)


async def pair_price_menu(query):
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("Bid", callback_data="pairpx_bid"), InlineKeyboardButton("Mid", callback_data="pairpx_mid"), InlineKeyboardButton("Ask", callback_data="pairpx_ask")],
        [InlineKeyboardButton("⬅️ Day", callback_data="pair_back_day"), BACK_BTN],
    ])
    await query.message.reply_text(f"{hdr('🧩 PAIR LEG BUILDER')}\n\nStep 8/8: Entry price source", parse_mode=H, reply_markup=kb)


async def pair_confirm_menu(query, ctx):
    st = ctx.user_data.get("pairwiz", {})
    parent_id = _safe_int(st.get("parent_id", 0), 0)
    tr = _fetch_trade(parent_id)
    if not tr:
        await query.message.reply_text("❌ Parent position not found.", reply_markup=InlineKeyboardMarkup([[BACK_BTN]]))
        return

    tk = st.get("ticker")
    ot = st.get("opt_type")
    exp = st.get("expiry")
    strike = _safe_float(st.get("strike", 0), 0)
    side = st.get("side", "buy")
    qty = _safe_int(st.get("qty", 1), 1)
    day_offset = _safe_int(st.get("day_offset", 0), 0)
    px_mode = st.get("px_mode", "mid")
    if not tk or not ot or not exp or strike <= 0:
        await query.message.reply_text("⚠️ Incomplete pair-leg config.", reply_markup=InlineKeyboardMarkup([[BACK_BTN]]))
        return

    est = _option_price_by_mode(tk, ot, strike, exp, mode=px_mode, fallback=1.00)
    st["entry_price"] = est
    st["entry_date"] = (datetime.now().date() - timedelta(days=max(0, day_offset))).strftime("%Y-%m-%d")
    ctx.user_data["pairwiz"] = st

    spots, total_payoff, breakevens = _parent_child_payoff(
        tr,
        {
            "ticker": tk,
            "opt_type": ot,
            "strike": strike,
            "entry_price": est,
            "qty": qty,
            "side": side,
        },
    )
    max_gain = float(np.max(total_payoff))
    max_loss = float(np.min(total_payoff))
    be_txt = ", ".join([f"${x:.2f}" for x in breakevens[:4]]) if breakevens else "None in shown range"

    msg = (
        f"{hdr('✅ CONFIRM PAIR LEG')}\n\n"
        + mono(
            f"{row2('Parent #', str(parent_id))}\n"
            f"{row2('Ticker', tk)}\n"
            f"{row2('Type', ot.upper())}\n"
            f"{row2('Side', side.upper())}\n"
            f"{row2('Strike', f'${strike:.2f}')}\n"
            f"{row2('Expiry', exp)}\n"
            f"{row2('Qty', str(qty if side == 'buy' else -qty))}\n"
            f"{row2('Entry Day', st['entry_date'])}\n"
            f"{row2('Entry Px', f'${est:.2f} ({px_mode})')}\n"
            f"{'─' * 27}\n"
            f"{row2('Max Gain*', f'${max_gain:,.0f}')}\n"
            f"{row2('Max Loss*', f'${max_loss:,.0f}')}\n"
            f"{row2('Breakeven*', be_txt[:26])}"
        )
        + "\n*Approx over charted price range"
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Add Pair Leg", callback_data="pairgo"), InlineKeyboardButton("❌ Cancel", callback_data=f"pos_{parent_id}")],
        [InlineKeyboardButton("📉 Payoff Chart", callback_data="pairchart"), BACK_BTN],
    ])
    await _safe_reply(query.message, msg, reply_markup=kb)


async def pair_send_chart(query, ctx):
    st = ctx.user_data.get("pairwiz", {})
    parent_id = _safe_int(st.get("parent_id", 0), 0)
    tr = _fetch_trade(parent_id)
    if not tr:
        await query.message.reply_text("❌ Parent position missing.", reply_markup=InlineKeyboardMarkup([[BACK_BTN]]))
        return
    cfg = {
        "ticker": st.get("ticker"),
        "opt_type": st.get("opt_type"),
        "strike": _safe_float(st.get("strike", 0), 0),
        "entry_price": _safe_float(st.get("entry_price", 0), 0),
        "qty": _safe_int(st.get("qty", 1), 1),
        "side": st.get("side", "buy"),
    }
    spots, total_payoff, _ = _parent_child_payoff(tr, cfg)
    title = f"Payoff: Parent #{parent_id} + {cfg['side'].upper()} {cfg['opt_type'].upper()}"
    img = _render_payoff_chart(spots, total_payoff, title)
    if img is None:
        await query.message.reply_text("⚠️ Could not render payoff chart.", reply_markup=InlineKeyboardMarkup([[BACK_BTN]]))
        return
    await query.message.reply_photo(photo=img, caption="Pair strategy payoff at expiry (approx)", parse_mode=H)

# ═══════════════════════════════════════════════════════════
#  5) OI ANALYTICS — table format
# ═══════════════════════════════════════════════════════════
async def oi_menu(query, expiry=None):
    """Show top tickers by OI, with expiry_date selection (trade_date = latest always)."""
    conn = get_conn()

    # Step 1: Get the latest data collection date (trade_date)
    try:
        latest_td_df = pd.read_sql("""
            SELECT DISTINCT trade_date FROM options_daily
            ORDER BY substr(trade_date,7,4)||substr(trade_date,1,2)||substr(trade_date,4,2) DESC
            LIMIT 1
        """, conn)
        if latest_td_df.empty:
            await query.message.reply_text("📊 No OI data in database.", reply_markup=InlineKeyboardMarkup([[BACK_BTN]]))
            conn.close()
            return
        latest_trade_date = latest_td_df["trade_date"].iloc[0]
    except Exception as e:
        log.warning("oi_menu trade_date fetch failed: %s", e)
        conn.close()
        await query.message.reply_text("📊 OI data unavailable.", reply_markup=InlineKeyboardMarkup([[BACK_BTN]]))
        return

    # Step 2: Get expiry_dates with ticker counts from the latest trade_date
    try:
        all_exp_df = pd.read_sql("""
            SELECT expiry_date, COUNT(DISTINCT ticker) as n_tickers
            FROM options_daily WHERE trade_date = ?
            GROUP BY expiry_date
        """, conn, params=(latest_trade_date,))
        all_expiry_dates = all_exp_df["expiry_date"].tolist()
        exp_ticker_count = dict(zip(all_exp_df["expiry_date"], all_exp_df["n_tickers"]))
    except Exception as e:
        log.warning("oi_menu expiry_date fetch failed: %s", e)
        all_expiry_dates = []
        exp_ticker_count = {}

    # Step 3: Separate future vs expired expiry_dates
    today = datetime.now().date()
    future_expiries = []
    expired_expiries = []
    for d in all_expiry_dates:
        try:
            dt = datetime.strptime(str(d), "%m-%d-%Y").date()
            if dt >= today:
                future_expiries.append((dt, d))
            else:
                expired_expiries.append((dt, d))
        except Exception:
            continue
    future_expiries.sort(key=lambda x: x[0])   # nearest first
    expired_expiries.sort(key=lambda x: x[0], reverse=True)  # most recent first

    future_dates = [d[1] for d in future_expiries]
    expired_dates = [d[1] for d in expired_expiries]

    # Default expiry: nearest future with ≥10 tickers (skip weekly expiries with only 2)
    def _best_default(dates):
        for d in dates:
            if exp_ticker_count.get(d, 0) >= 10:
                return d
        return dates[0] if dates else None

    chosen_expiry = expiry if expiry else (_best_default(future_dates) or (expired_dates[0] if expired_dates else None))
    if not chosen_expiry:
        await query.message.reply_text("📊 No option expiries found.", reply_markup=InlineKeyboardMarkup([[BACK_BTN]]))
        conn.close()
        return

    # Step 4: Query OI aggregated by ticker for chosen expiry_date
    try:
        df = pd.read_sql("""
            SELECT ticker,
                   SUM(openInt_Call) as total_call_oi,
                   SUM(openInt_Put)  as total_put_oi,
                   COUNT(DISTINCT expiry_date) as num_expiries
            FROM options_daily
            WHERE trade_date = ? AND expiry_date = ?
            GROUP BY ticker
            ORDER BY (SUM(openInt_Call) + SUM(openInt_Put)) DESC
        """, conn, params=(latest_trade_date, chosen_expiry))
        df["pcr"] = (df["total_put_oi"] / df["total_call_oi"].replace(0, np.nan)).fillna(0)
    except Exception as e:
        log.warning("oi_menu ticker query failed: %s", e)
        df = pd.DataFrame()
    conn.close()

    if df.empty:
        await query.message.reply_text(
            f"📊 No OI data for expiry <b>{chosen_expiry}</b>.\nData as of: <b>{latest_trade_date}</b>",
            parse_mode=H, reply_markup=InlineKeyboardMarkup([[BACK_BTN]]))
        return

    parts = [hdr(f"📊 OI ANALYTICS · Exp {chosen_expiry}")]
    parts.append(f"Data as of: <b>{latest_trade_date}</b> · <b>{len(df)} tickers</b>")

    # Top by total OI — dynamic column widths (NSE-style: measure data first, then render)
    top_oi = df.head(8)
    def _fk_oi(n):
        n = float(n or 0)
        if n >= 1_000_000_000: return f"{n/1_000_000_000:.1f}B"
        if n >= 100_000_000:   return f"{n/1_000_000:.0f}M"
        if n >= 10_000_000:    return f"{n/1_000_000:.1f}M"
        if n >= 1_000_000:     return f"{n/1_000_000:.2f}M"
        if n >= 1_000:         return f"{n/1_000:.0f}K"
        return f"{n:.0f}"
    _oi_tbl_data = [
        (str(r["ticker"]), _fk_oi(r["total_call_oi"]), _fk_oi(r["total_put_oi"]),
         f"{min(float(r['pcr'] or 0), 9.99):.2f}")
        for _, r in top_oi.iterrows()
    ]
    parts.append("\n<b>Top by Open Interest</b>\n"
                 + _pipe_table(("Ticker", "Call OI", "Put OI", "PCR"), _oi_tbl_data, right_cols={1, 2, 3}))

    # Highest PCR
    high_pcr = df[df["pcr"] > 0].nlargest(5, "pcr")
    if not high_pcr.empty:
        _pcr_tbl = []
        for _, r in high_pcr.iterrows():
            bias = "Bearish" if r["pcr"] > 1.3 else ("Bullish" if r["pcr"] < 0.7 else "Neutral")
            _pcr_tbl.append((str(r["ticker"]), f"{min(float(r['pcr'] or 0), 9.99):.2f}", bias))
        parts.append("\n<b>Highest Put/Call Ratio</b>\n"
                     + _pipe_table(("Ticker", "PCR", "Bias"), _pcr_tbl, right_cols={1}))

    # Build expiry selection buttons (expiry_date values)
    exp_btns = []
    for d in future_dates[:8]:
        label = f">{d}" if d == chosen_expiry else d
        exp_btns.append(InlineKeyboardButton(label, callback_data=f"oi_expiry_{d}"))

    exp_rows = []
    for i in range(0, len(exp_btns), 3):
        exp_rows.append(exp_btns[i:i+3])

    if expired_dates:
        exp_rows.append([InlineKeyboardButton("── Expired ──", callback_data="noop")])
        old_btns = []
        for d in expired_dates[:4]:
            label = f">{d}" if d == chosen_expiry else d
            old_btns.append(InlineKeyboardButton(label, callback_data=f"oi_expiry_{d}"))
        for i in range(0, len(old_btns), 3):
            exp_rows.append(old_btns[i:i+3])

    # Ticker buttons
    tickers = sorted(df["ticker"].dropna().astype(str).str.upper().unique().tolist())
    paged = _paged_ticker_keyboard("oi_detail", tickers, page=0, per_page=12, cols=3, include_back=False)
    btns = exp_rows + [list(r) for r in paged.inline_keyboard]
    btns.append([InlineKeyboardButton("📊 OI Change Chart", callback_data="oi_change_menu")])
    btns.append([InlineKeyboardButton("🔀 Compare 2 Expiries", callback_data="oi_compare_select1")])
    btns.append([InlineKeyboardButton("🔄 Refresh", callback_data="menu_oi"), BACK_BTN])
    await _safe_reply(query.message, "\n".join(parts), reply_markup=InlineKeyboardMarkup(btns))

async def oi_detail(query, ticker):
    conn = get_conn()
    try:
        # Get latest trade date for this ticker from options_daily
        latest_date_df = pd.read_sql("""
            SELECT DISTINCT trade_date 
            FROM options_daily 
            WHERE ticker = ?
            ORDER BY substr(trade_date,7,4)||substr(trade_date,1,2)||substr(trade_date,4,2) DESC 
            LIMIT 1
        """, conn, params=(str(ticker).upper(),))
        
        if latest_date_df.empty:
            await query.message.reply_text(f"No data for {ticker}", reply_markup=InlineKeyboardMarkup([[BACK_BTN]]))
            conn.close()
            return
        
        trade_date = latest_date_df['trade_date'].iloc[0]
        
        # Get aggregated stats for this ticker
        df = pd.read_sql("""
            SELECT 
                ticker,
                trade_date,
                SUM(openInt_Call) as call_oi,
                SUM(openInt_Put) as put_oi,
                COUNT(DISTINCT expiry_date) as num_expiries
            FROM options_daily
            WHERE ticker = ? AND trade_date = ?
            GROUP BY ticker, trade_date
        """, conn, params=(str(ticker).upper(), trade_date))
    except Exception as e:
        log.warning(f"oi_detail failed for {ticker}: {e}")
        df = pd.DataFrame()
    conn.close()

    if df.empty:
        await query.message.reply_text(f"No OI data for {ticker} on latest date.",
                                       reply_markup=InlineKeyboardMarkup([[BACK_BTN]]))
        return

    r = df.iloc[0]
    call_oi = float(r.get("call_oi") or 0)
    put_oi = float(r.get("put_oi") or 0)
    pcr = put_oi / call_oi if call_oi > 0 else 0
    dt = r.get('trade_date', '?')
    net_oi = call_oi - put_oi
    num_exp = int(r.get('num_expiries') or 0)

    # Simple bias based on PCR
    if pcr > 1.3:
        bias = "BEARISH 🔴 (High PCR)"
    elif pcr < 0.7:
        bias = "BULLISH 🟢 (Low PCR)"
    else:
        bias = "NEUTRAL ⚪ (Balanced)"

    # Visual bar based on call vs put
    total_oi = call_oi + put_oi
    call_pct = (call_oi / total_oi * 100) if total_oi > 0 else 50
    oi_bar = bar(call_pct)

    msg = (
        f"{hdr(f'📊 {ticker} OI · {dt}')}\n\n"
        + mono(
            f"{row2('Total Expiries', f'{num_exp}')}\n"
            f"{'─' * 27}\n"
            f"{row2('Call OI', f'{call_oi:>12,.0f}')}\n"
            f"{row2('Put OI', f'{put_oi:>12,.0f}')}\n"
            f"{row2('Net OI', f'{net_oi:>12,.0f}')}\n"
            f"{row2('P/C Ratio', f'{pcr:.2f}')}\n"
            f"{'─' * 27}\n"
            f"Call {oi_bar} Put\n"
        )
        + f"\n\nBias: <b>{bias}</b>"
    )

    # ── Per-expiry breakdown ──────────────────────────────────────
    try:
        exp_df = pd.read_sql("""
            SELECT expiry_date,
                   SUM(openInt_Call) as c_oi, SUM(openInt_Put) as p_oi
            FROM options_daily
            WHERE ticker = ? AND trade_date = ?
            GROUP BY expiry_date
            ORDER BY substr(expiry_date,7,4)||substr(expiry_date,1,2)||substr(expiry_date,4,2)
        """, get_conn(), params=(str(ticker).upper(), str(dt)))
        if not exp_df.empty:
            rows = [f"{'Expiry':<8} {'Call':>6} {'Put':>6} {'PCR':>5}"]
            rows.append("─" * 28)
            def _fkoi(n):
                n = float(n or 0)
                if n >= 1_000_000: return f"{n/1_000_000:.1f}M"
                if n >= 1_000: return f"{n/1_000:.0f}K"
                return f"{n:.0f}"
            for _, er in exp_df.iterrows():
                c = float(er.get('c_oi') or 0)
                p = float(er.get('p_oi') or 0)
                ep = p / c if c > 0 else 0
                rows.append(f"{str(er['expiry_date'])[:8]:<8} {_fkoi(c):>6} {_fkoi(p):>6} {ep:>5.2f}")
            msg += f"\n\n<b>By Expiry:</b>\n{mono(chr(10).join(rows))}"
    except Exception as ex:
        log.warning(f"oi_detail expiry breakdown failed: {ex}")

    # ── IV, volatility & strategy suggestions ────────────────────
    try:
        tk_obj = yf.Ticker(str(ticker).upper())
        hist = tk_obj.history(period="30d")
        iv_section = ""
        strat_section = ""
        if len(hist) >= 10:
            # Historical volatility (20d annualised)
            rets = hist["Close"].pct_change().dropna()
            hv20 = float(rets.tail(20).std() * (252 ** 0.5) * 100)

            # Get IV from nearest expiry option
            iv_pct = None
            try:
                exps = tk_obj.options
                if exps:
                    chain = tk_obj.option_chain(exps[0])
                    atm_calls = chain.calls.dropna(subset=["impliedVolatility"])
                    spot = float(hist["Close"].iloc[-1])
                    atm_calls["dist"] = (atm_calls["strike"] - spot).abs()
                    nearest = atm_calls.nsmallest(1, "dist")
                    if not nearest.empty:
                        iv_pct = float(nearest["impliedVolatility"].iloc[0]) * 100
            except Exception:
                pass

            iv_vs_hv = ""
            cheap_signal = ""
            if iv_pct is not None:
                diff = iv_pct - hv20
                if diff < -5:
                    iv_vs_hv = "🟢 IV < HV — options CHEAP"
                    cheap_signal = "cheap"
                elif diff > 5:
                    iv_vs_hv = "🔴 IV > HV — options EXPENSIVE"
                    cheap_signal = "expensive"
                else:
                    iv_vs_hv = "⚪ IV ≈ HV — fairly priced"
                iv_section = mono(
                    f"{'HV (20d)':<12} {hv20:>6.1f}%\n"
                    f"{'IV (ATM)':<12} {iv_pct:>6.1f}%\n"
                    f"{'IV-HV':<12} {diff:>+6.1f}%"
                ) + f"\n{iv_vs_hv}"
            else:
                iv_section = mono(f"{'HV (20d)':<12} {hv20:>6.1f}%")

            # Strategy suggestions based on PCR + IV + bias
            strats = []
            is_bull = pcr < 0.8
            is_bear = pcr > 1.2

            if cheap_signal == "cheap":
                if is_bull:
                    strats = [
                        ("🟢 Buy Call (cheap IV)", f"Long call — low cost, unlimited upside"),
                        ("🟢 Bull Call Spread", f"Buy call + sell higher call — cheap & defined risk"),
                        ("🟢 LEAPS Call", f"Long-dated call — low theta, time to be right"),
                    ]
                elif is_bear:
                    strats = [
                        ("🔴 Buy Put (cheap IV)", f"Long put — low cost, profits on downside"),
                        ("🔴 Bear Put Spread", f"Buy put + sell lower put — cheap & defined risk"),
                        ("🔴 Straddle", f"Buy call+put — cheap vol, profit on big move either way"),
                    ]
                else:
                    strats = [
                        ("⚡ Straddle", f"Buy call+put ATM — cheap IV, bet on big move"),
                        ("⚡ Strangle", f"Buy OTM call+put — even cheaper, wider break-even"),
                    ]
            elif cheap_signal == "expensive":
                if is_bull:
                    strats = [
                        ("🟢 Covered Call / Cash-Secured Put", f"Sell premium — collect high IV"),
                        ("🟢 Bull Put Spread", f"Sell put spread — collect rich premium, bullish"),
                        ("🟢 Call Spread", f"Reduce cost vs outright call by selling higher strike"),
                    ]
                elif is_bear:
                    strats = [
                        ("🔴 Bear Call Spread", f"Sell call spread — collect premium, bearish"),
                        ("🔴 Iron Condor", f"Sell both sides — profit from vol crush, range-bound"),
                    ]
                else:
                    strats = [
                        ("⚖️ Iron Condor", f"Sell OTM call+put spreads — profit from vol crush"),
                        ("⚖️ Iron Butterfly", f"Sell ATM straddle + wings — max profit at current price"),
                    ]
            else:
                strats = [
                    ("📊 Vertical Spread", f"Defined risk, defined reward — good in any environment"),
                ]

            if strats:
                s_rows = []
                for name, desc in strats[:3]:
                    s_rows.append(f"<b>{name}</b>")
                    s_rows.append(f"  {desc}")
                strat_section = "\n".join(s_rows)

        if iv_section:
            msg += f"\n\n<b>📉 Volatility:</b>\n{iv_section}"
        if strat_section:
            msg += f"\n\n<b>💡 Suggested Strategies:</b>\n{strat_section}"

    except Exception as ex:
        log.warning(f"oi_detail IV/strat failed: {ex}")

    # ── Strike-level OI breakdown + multi-week trend ─────────────────
    try:
        conn3 = get_conn()
        _sd3 = pd.read_sql("""SELECT close FROM stock_daily WHERE ticker=?
            ORDER BY substr(trade_date,7,4)||substr(trade_date,1,2)||substr(trade_date,4,2) DESC
            LIMIT 1""", conn3, params=(str(ticker).upper(),))
        _spot3 = float(_sd3["close"].iloc[0]) if not _sd3.empty else 0.0
        _oc_date3 = pd.read_sql("""SELECT DISTINCT trade_date_now FROM options_change WHERE ticker=?
            ORDER BY substr(trade_date_now,7,4)||substr(trade_date_now,1,2)||substr(trade_date_now,4,2) DESC
            LIMIT 1""", conn3, params=(str(ticker).upper(),))
        _latest3 = _oc_date3["trade_date_now"].iloc[0] if not _oc_date3.empty else ""
        if _latest3 and _spot3 > 0:
            _bd3 = _oi_strike_breakdown(str(ticker).upper(), conn3, _spot3, _latest3)
            _tr3 = _oi_trend_summary(str(ticker).upper(), conn3, _latest3)
            if _tr3:
                msg += f"\n\n<b>📅 OI Build Trend (1W/1M):</b>\n{_tr3}"
            if _bd3:
                msg += f"\n\n<b>🔍 Strike-Level OI (±20% spot):</b>\n{_bd3}"
        conn3.close()
    except Exception as _ex3:
        log.warning(f"oi_detail strike breakdown failed: {_ex3}")

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 OI Change Chart", callback_data=f"oi_change_{ticker}"),
         InlineKeyboardButton("🤖 MiroFish", callback_data=f"miro_ticker_{ticker}")],
        [InlineKeyboardButton("📊 OI Overview", callback_data="menu_oi"), BACK_BTN],
    ])
    await _safe_reply(query.message, msg, reply_markup=kb)


async def oi_compare_select_expiry(query, ctx, step=1):
    """Select expiry for comparison (step 1 or 2)"""
    conn = get_conn()
    # Don't use ORDER BY - MM-DD-YYYY format sorts incorrectly as strings
    try:
        all_expiries_raw = pd.read_sql("SELECT DISTINCT trade_date FROM options_daily", conn)["trade_date"].tolist()
    except Exception as e:
        log.warning("oi_compare expiry fetch failed: %s", e)
        all_expiries_raw = []
    conn.close()
    
    if not all_expiries_raw:
        await query.message.reply_text("📊 No OI data available.", reply_markup=InlineKeyboardMarkup([[BACK_BTN]]))
        return
    
    # Parse and sort by actual date (DESC - newest first)
    today = datetime.now().date()
    parsed_expiries = []
    for d in all_expiries_raw:
        try:
            dt = datetime.strptime(str(d), "%m-%d-%Y").date()
            parsed_expiries.append((dt, d))
        except Exception:
            continue
    
    # Sort DESC (newest first)
    parsed_expiries.sort(key=lambda x: x[0], reverse=True)
    
    # Build buttons with future/expired indicators
    expiry_buttons = []
    for dt, d in parsed_expiries[:10]:  # Show up to 10 dates
        is_future = dt >= today
        label = f"{'🟢' if is_future else '🔴'}{d}"
        
        if step == 1:
            expiry_buttons.append(InlineKeyboardButton(label, callback_data=f"oi_cmp1_{d}"))
        else:
            expiry1 = ctx.user_data.get("oi_compare_exp1", "")
            if d == expiry1:
                continue  # Skip same date
            expiry_buttons.append(InlineKeyboardButton(label, callback_data=f"oi_cmp2_{d}"))
    
    expiry_rows = [expiry_buttons[i:i+2] for i in range(0, len(expiry_buttons), 2)]
    expiry_rows.append([InlineKeyboardButton("⬅️ Back", callback_data="menu_oi")])
    
    step_text = "1st" if step == 1 else "2nd"
    await query.message.reply_text(
        f"{hdr('🔀 COMPARE OI EXPIRIES')}\n\nSelect {step_text} expiry date:",
        parse_mode=H,
        reply_markup=InlineKeyboardMarkup(expiry_rows)
    )


async def oi_compare_view(query, ctx, exp1, exp2):
    """Show side-by-side comparison of two trade dates using options_daily"""
    conn = get_conn()
    try:
        # Aggregate OI per ticker for each date from options_daily
        df1 = pd.read_sql("""
            SELECT ticker,
                   SUM(openInt_Call) as call_oi,
                   SUM(openInt_Put) as put_oi,
                   CAST(SUM(openInt_Put) AS REAL) / NULLIF(SUM(openInt_Call), 0) as pcr
            FROM options_daily WHERE trade_date = ?
            GROUP BY ticker
        """, conn, params=(exp1,))

        df2 = pd.read_sql("""
            SELECT ticker,
                   SUM(openInt_Call) as call_oi,
                   SUM(openInt_Put) as put_oi,
                   CAST(SUM(openInt_Put) AS REAL) / NULLIF(SUM(openInt_Call), 0) as pcr
            FROM options_daily WHERE trade_date = ?
            GROUP BY ticker
        """, conn, params=(exp2,))
    except Exception as e:
        log.warning("oi_compare query failed: %s", e)
        df1 = df2 = pd.DataFrame()
    conn.close()

    if df1.empty or df2.empty:
        await query.message.reply_text("📊 Insufficient data for comparison.", reply_markup=InlineKeyboardMarkup([[BACK_BTN]]))
        return

    # Merge on ticker to compare
    merged = df1.merge(df2, on="ticker", how="inner", suffixes=("_1", "_2"))
    if merged.empty:
        await query.message.reply_text("📊 No common tickers found.", reply_markup=InlineKeyboardMarkup([[BACK_BTN]]))
        return

    # Calculate changes
    merged["call_chg"] = merged["call_oi_2"] - merged["call_oi_1"]
    merged["put_chg"] = merged["put_oi_2"] - merged["put_oi_1"]
    merged["pcr_chg"] = merged["pcr_2"] - merged["pcr_1"]
    merged["total_oi_2"] = merged["call_oi_2"] + merged["put_oi_2"]

    parts = [hdr(f"🔀 OI COMPARE: {exp1} vs {exp2}")]
    parts.append(f"\n<b>{len(merged)} tickers compared</b>")

    # Biggest call OI increases
    top_call_gain = merged.nlargest(5, "call_chg")
    if not top_call_gain.empty:
        _cg_rows = [("🟢", r["ticker"], f"{r['call_chg']:+,.0f}", f"{r['pcr_2']:.2f}")
                    for _, r in top_call_gain.iterrows()]
        parts.append("\n🟢 <b>Biggest Call OI Increases</b>\n"
                     + _pipe_table(("ST", "Ticker", "Call OI Chg", "PCR"), _cg_rows, right_cols={2, 3}))

    # Biggest put OI increases
    top_put_gain = merged.nlargest(5, "put_chg")
    if not top_put_gain.empty:
        _pg_rows = [("🔴", r["ticker"], f"{r['put_chg']:+,.0f}", f"{r['pcr_2']:.2f}")
                    for _, r in top_put_gain.iterrows()]
        parts.append("\n🔴 <b>Biggest Put OI Increases</b>\n"
                     + _pipe_table(("ST", "Ticker", "Put OI Chg", "PCR"), _pg_rows, right_cols={2, 3}))

    # PCR changes
    top_pcr_inc = merged.dropna(subset=["pcr_chg"]).nlargest(5, "pcr_chg")
    if not top_pcr_inc.empty:
        _pcr_ch_rows = [(r["ticker"], f"{r['pcr_chg']:+.2f}", f"{r['pcr_2']:.2f}")
                        for _, r in top_pcr_inc.iterrows()]
        parts.append("\n📈 <b>Biggest PCR Increases (More Bearish)</b>\n"
                     + _pipe_table(("Ticker", "PCR Δ", "New PCR"), _pcr_ch_rows, right_cols={1, 2}))
    
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔀 New Compare", callback_data="oi_compare_select1")],
        [InlineKeyboardButton("📊 OI Menu", callback_data="menu_oi"), BACK_BTN]
    ])
    await _safe_reply(query.message, "\n".join(parts), reply_markup=kb)


def _get_prev_trade_date(trade_date_str):
    """Get previous trade date in MM-DD-YYYY format"""
    try:
        dt = datetime.strptime(trade_date_str, "%m-%d-%Y").date()
        # Go back 1 day (assuming daily data, could be improved with calendar)
        prev = dt - timedelta(days=1)
        return prev.strftime("%m-%d-%Y")
    except Exception:
        return None


def _generate_oi_change_chart(ticker, today_date, prev_date):
    """Generate OI change chart for next 2 expiries comparing prev vs today"""
    conn = get_conn()
    
    # Get next 2 expiries for this ticker
    try:
        # Sort expiry_date chronologically (MM-DD-YYYY format)
        expiries_df = pd.read_sql("""
            SELECT DISTINCT expiry_date FROM options_daily 
            WHERE ticker = ? AND trade_date = ?
            ORDER BY substr(expiry_date,7,4)||substr(expiry_date,1,2)||substr(expiry_date,4,2)
        """, conn, params=(ticker.upper(), today_date))
        all_expiries = expiries_df["expiry_date"].tolist()[:2]  # Only next 2
    except Exception as e:
        log.warning(f"Failed to fetch expiries for {ticker}: {e}")
        conn.close()
        return None
    
    if len(all_expiries) < 1:
        conn.close()
        return None
    
    try:
        import matplotlib
        matplotlib.use('Agg')  # Use non-interactive backend
        import matplotlib.pyplot as plt
        
        fig, axes = plt.subplots(len(all_expiries), 1, figsize=(10, 5 * len(all_expiries)), squeeze=False)
        
        for idx, expiry in enumerate(all_expiries):
            ax = axes[idx, 0]
            
            # Fetch today's OI
            df_today = pd.read_sql("""
                SELECT strike, openInt_Call, openInt_Put
                FROM options_daily
                WHERE ticker = ? AND trade_date = ? AND expiry_date = ?
                ORDER BY strike
            """, conn, params=(ticker.upper(), today_date, expiry))
            
            # Fetch yesterday's OI
            df_prev = pd.read_sql("""
                SELECT strike, openInt_Call AS openInt_Call_prev, openInt_Put AS openInt_Put_prev
                FROM options_daily
                WHERE ticker = ? AND trade_date = ? AND expiry_date = ?
                ORDER BY strike
            """, conn, params=(ticker.upper(), prev_date, expiry))
            
            if df_today.empty:
                ax.text(0.5, 0.5, f"No data for {expiry}", ha='center', va='center')
                ax.set_title(f"{expiry} - No Data")
                continue
            
            # Merge and calculate changes
            df = df_today.merge(df_prev, on="strike", how="left")
            df["openInt_Call_prev"] = df["openInt_Call_prev"].fillna(0)
            df["openInt_Put_prev"] = df["openInt_Put_prev"].fillna(0)
            df["call_oi_change"] = df["openInt_Call"] - df["openInt_Call_prev"]
            df["put_oi_change"] = df["openInt_Put"] - df["openInt_Put_prev"]
            
            # Plot
            strikes = df["strike"].values
            if len(strikes) < 2:
                ax.text(0.5, 0.5, "Insufficient strike data", ha='center', va='center')
                ax.set_title(f"{ticker} - Expiry: {expiry} - No Data")
                continue
            # Adaptive bar width — 40% of strike spacing so bars don't touch
            width = float(strikes[1] - strikes[0]) * 0.40

            # ── Bars: light = yesterday, dark = today ──────────────────
            ax.bar(strikes,  df["openInt_Call_prev"], width=width, alpha=0.22,
                   color='#43A047', label='Calls Yesterday')
            ax.bar(strikes, -df["openInt_Put_prev"],  width=width, alpha=0.22,
                   color='#E53935', label='Puts Yesterday')
            ax.bar(strikes,  df["openInt_Call"],      width=width, alpha=0.80,
                   color='#1B5E20', label='Calls Today')
            ax.bar(strikes, -df["openInt_Put"],       width=width, alpha=0.80,
                   color='#B71C1C', label='Puts Today')

            ax.axhline(y=0, color='#555', linestyle='-', linewidth=0.8)

            # ── Legend explaining bar colors ──────────────────────────
            ax.legend(loc='upper left', fontsize=7, ncol=2, framealpha=0.85,
                      title="▐ Green=Calls  Red=Puts  Dark=Today  Faded=Yesterday",
                      title_fontsize=6.5)
            ax.grid(True, alpha=0.20, axis='y')

            # ── Metrics ───────────────────────────────────────────────
            total_call_chg = df["call_oi_change"].sum()
            total_put_chg  = df["put_oi_change"].sum()
            total_call_oi  = df["openInt_Call"].sum()
            total_put_oi   = df["openInt_Put"].sum()
            call_pct_chg = (total_call_chg / df["openInt_Call_prev"].sum() * 100) if df["openInt_Call_prev"].sum() > 0 else 0
            put_pct_chg  = (total_put_chg  / df["openInt_Put_prev"].sum()  * 100) if df["openInt_Put_prev"].sum()  > 0 else 0
            pcr_today = (total_put_oi / total_call_oi) if total_call_oi > 0 else 0

            # ── Intent signal ──────────────────────────────────────────
            try:
                _spot = float(yf.Ticker(ticker).history(period="2d")["Close"].iloc[-1])
            except Exception:
                _spot = None

            if _spot and len(df) >= 2:
                df["call_oi_change"] = df["call_oi_change"]
                df["put_oi_change"]  = df["put_oi_change"]
                _, _isig, _isc, _idesc, _idet = _oi_intent_algo(df, _spot)
                hedge_pct = _idet.get("hedge_pct", 0)
            else:
                _isig, _isc = _oi_signal_light(total_call_chg, total_put_chg, pcr_today)
                _idesc = ""
                hedge_pct = 0

            # Plain-English signal descriptions
            _signal_plain = {
                "BULLISH":       "Buyers are adding call positions — bullish bet on a price rise.",
                "MILD BULL":     "Slightly more calls than puts — modest bullish lean, not aggressive.",
                "BEARISH":       "Puts being added near current price — traders betting on a drop.",
                "MILD BEAR":     "Slight put bias — watch for more selling to confirm.",
                "HEDGED BULL":   "Institutions buying calls AND deep puts — they own the stock and are protecting it. Not a bearish signal.",
                "STRADDLE":      "Both calls and puts growing at ATM — traders expect a BIG move but don't know which direction (could be earnings/event).",
                "COVERED_CALL":  "Far-OTM calls being written — likely stock owners selling covered calls for income. Capped upside.",
                "BULLISH_BREAK": "OTM call build — traders speculating on a breakout above current price.",
                "NEAR_BEARISH":  "Near-OTM puts accumulating — directional shorts positioning for a modest drop.",
                "HEDGE":         "Deep-OTM puts added — institutional portfolio protection. This is NOT a directional short bet.",
                "HEDGE_UNWIND":  "Deep put hedges being removed — institutions feel less need for protection. Mildly bullish signal.",
                "UNWIND":        "Both calls and puts declining — positions being closed, low conviction on either side.",
                "QUIET":         "Very little OI change — market has no strong view on this expiry.",
                "NEUTRAL":       "Activity is balanced — no clear directional edge from options market.",
            }
            plain_desc = _signal_plain.get(_isig, _idesc or _isig)

            # ── Title ──────────────────────────────────────────────────
            ax.set_title(
                f"{ticker}  |  Expiry: {expiry}  |  Spot: ${_spot:.2f}" if _spot else f"{ticker}  |  Expiry: {expiry}",
                fontsize=10, fontweight="bold"
            )
            ax.set_ylabel("Open Interest  (↑ Calls, ↓ Puts)")

            # Strike labels on x-axis
            _step = max(1, len(strikes) // 14)
            ax.set_xticks(strikes[::_step])
            ax.set_xticklabels([f"${s:.0f}" for s in strikes[::_step]],
                               rotation=45, ha='right', fontsize=7)
            ax.set_xlabel('Strike Price', fontsize=8)
            ax.set_xlim(strikes[0] - width * 2.5, strikes[-1] + width * 2.5)
            # Autoscale y-axis: data-driven min/max with 15% padding
            _y_vals = list(df["openInt_Call"]) + list(df["openInt_Call_prev"]) + \
                      list(-df["openInt_Put"]) + list(-df["openInt_Put_prev"])
            _y_pos = max((v for v in _y_vals if v >= 0), default=1)
            _y_neg = min((v for v in _y_vals if v <= 0), default=-1)
            ax.set_ylim(_y_neg * 1.18, _y_pos * 1.18)
            ax.yaxis.set_major_formatter(
                plt.FuncFormatter(lambda x, _: f"{abs(x)/1000:.0f}K" if abs(x) >= 1000 else f"{x:.0f}"))

            # ── Bottom-left stats box ──────────────────────────────────
            _c_arrow = "▲" if total_call_chg > 0 else ("▼" if total_call_chg < 0 else "→")
            _p_arrow = "▲" if total_put_chg  > 0 else ("▼" if total_put_chg  < 0 else "→")
            _pcr_note = "Bearish lean" if pcr_today > 1.3 else ("Bullish lean" if pcr_today < 0.7 else "Neutral")
            _hedge_line = f"\nHedge flow: {hedge_pct:.0f}% of put OI" if hedge_pct > 20 else ""
            ax.text(0.01, 0.02,
                    f"TODAY vs YESTERDAY\n"
                    f"Calls {_c_arrow} {total_call_chg:+,.0f}  ({call_pct_chg:+.1f}%)\n"
                    f"Puts  {_p_arrow} {total_put_chg:+,.0f}  ({put_pct_chg:+.1f}%)\n"
                    f"PCR: {pcr_today:.2f}  ({_pcr_note}){_hedge_line}",
                    transform=ax.transAxes, va="bottom", fontsize=7.5,
                    bbox=dict(boxstyle="round,pad=0.4", facecolor="#FFFDE7",
                              edgecolor="#F9A825", alpha=0.92))

            # ── Suggested strategy box (bottom-right) ─────────────────
            _strat_map = {
                "BULLISH":      "Strategies:\n• Long Call\n• Bull Call Spread\n• Sell Cash-Secured Put",
                "MILD BULL":    "Strategies:\n• Bull Call Spread\n• Sell OTM Put\n• Covered Call write",
                "BEARISH":      "Strategies:\n• Long Put\n• Bear Put Spread\n• Short Call (OTM)",
                "MILD BEAR":    "Strategies:\n• Bear Put Spread\n• Sell OTM Call\n• Protective Put",
                "HEDGED BULL":  "Strategies:\n• Hold with hedge\n• Sell covered call for income",
                "STRADDLE":     "Strategies:\n• Long Straddle (ATM)\n• Long Strangle (OTM)\n• Calendar Spread",
                "COVERED_CALL": "Strategies:\n• Covered Call write\n• Sell near-ATM call\n• Collar",
                "BULLISH_BREAK":"Strategies:\n• OTM Call Debit Spread\n• Long Call (breakout bet)",
                "NEAR_BEARISH": "Strategies:\n• Near-ATM Put\n• Bear Put Spread\n• Risk Reversal",
                "HEDGE":        "Strategies:\n• Ignore put flow (hedge)\n• Stay with long bias\n• Sell puts for income",
                "UNWIND":       "Strategies:\n• Wait for new direction\n• Small Iron Condor\n• Reduce size",
                "NEUTRAL":      "Strategies:\n• Iron Condor\n• Butterfly\n• Calendar Spread",
            }
            _strat_txt = _strat_map.get(_isig, "Strategies:\n• Iron Condor\n• Butterfly")
            ax.text(0.99, 0.02, _strat_txt,
                    transform=ax.transAxes, va="bottom", ha="right",
                    fontsize=7.0,
                    bbox=dict(boxstyle="round,pad=0.4", facecolor="#E8F5E9",
                              edgecolor="#388E3C", alpha=0.92))

            # ── Top-right signal box — plain English ───────────────────
            ax.text(0.99, 0.98,
                    f"SIGNAL: {_isig}\n{'─'*30}\n{plain_desc}",
                    transform=ax.transAxes, va="top", ha="right",
                    fontsize=7.5, fontweight="bold", color="white",
                    wrap=True,
                    bbox=dict(boxstyle="round,pad=0.5", facecolor=_isc,
                              edgecolor="white", alpha=0.93))

        plt.tight_layout()
        buf = BytesIO()
        fig.savefig(buf, format='png', dpi=100, bbox_inches='tight')
        plt.close(fig)
        buf.seek(0)
        conn.close()
        return buf
        
    except Exception as e:
        log.error(f"OI chart generation error for {ticker}: {e}")
        conn.close()
        return None


async def oi_change_ticker_menu(query):
    """Show ticker selection for OI change chart"""
    conn = get_conn()
    try:
        # Get latest trade date (MM-DD-YYYY format, sort chronologically)
        latest_date_df = pd.read_sql("""
            SELECT DISTINCT trade_date FROM options_daily 
            ORDER BY substr(trade_date,7,4)||substr(trade_date,1,2)||substr(trade_date,4,2) DESC LIMIT 1
        """, conn)
        if latest_date_df.empty:
            await query.message.reply_text("📊 No options data available.", reply_markup=InlineKeyboardMarkup([[BACK_BTN]]))
            conn.close()
            return
        
        latest_date = latest_date_df["trade_date"].iloc[0]
        
        # Get tickers with data for latest date
        tickers_df = pd.read_sql("""
            SELECT DISTINCT ticker FROM options_daily 
            WHERE trade_date = ?
            ORDER BY ticker
        """, conn, params=(latest_date,))
        tickers = tickers_df["ticker"].tolist()
    except Exception as e:
        log.warning(f"oi_change_ticker_menu query failed: {e}")
        tickers = []
    conn.close()
    
    if not tickers:
        await query.message.reply_text("📊 No tickers found.", reply_markup=InlineKeyboardMarkup([[BACK_BTN]]))
        return
    
    kb = _paged_ticker_keyboard("oi_change", tickers, page=0, per_page=12, cols=3, include_back=True, back_cb="menu_oi")
    await query.message.reply_text(
        f"{hdr('📊 OI CHANGE CHART')}\n\nSelect ticker for OI comparison:\n\n"
        "Options will be shown after ticker selection.",
        parse_mode=H,
        reply_markup=kb
    )


async def oi_change_chart_view(query, ticker):
    """Show options for OI change chart type"""
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 EOD vs EOD (Historical)", callback_data=f"oi_change_eod_{ticker}")],
        [InlineKeyboardButton("🔴 Live vs Last EOD", callback_data=f"oi_change_live_{ticker}")],
        [InlineKeyboardButton("⬅️ Back to Tickers", callback_data="oi_change_menu"), BACK_BTN]
    ])
    
    await query.message.reply_text(
        f"{hdr(f'📊 {ticker} OI CHANGE CHART')}\n\n"
        "Select comparison type:\n\n"
        "• <b>EOD vs EOD</b>: Compare last 2 end-of-day snapshots\n"
        "• <b>Live vs Last EOD</b>: Pull current live OI from Yahoo Finance vs yesterday's EOD",
        parse_mode=H,
        reply_markup=kb
    )


async def oi_change_chart_eod_view(query, ticker):
    """Show EOD vs EOD OI change chart (existing functionality)"""
    _loading = await query.message.reply_text(f"⏳ Generating EOD comparison chart for {ticker}...", parse_mode=H)
    
    # Get latest and previous trade dates (MM-DD-YYYY format, sort chronologically)
    conn = get_conn()
    try:
        dates_df = pd.read_sql("""
            SELECT DISTINCT trade_date FROM options_daily 
            ORDER BY substr(trade_date,7,4)||substr(trade_date,1,2)||substr(trade_date,4,2) DESC LIMIT 2
        """, conn)
        if len(dates_df) < 2:
            await query.message.reply_text("📊 Insufficient historical data.", reply_markup=InlineKeyboardMarkup([[BACK_BTN]]))
            try: await _loading.delete()
            except Exception: pass
            conn.close()
            return
        
        today_date = dates_df["trade_date"].iloc[0]
        prev_date = dates_df["trade_date"].iloc[1]
    except Exception as e:
        log.error(f"Failed to get trade dates: {e}")
        await query.message.reply_text("❌ Failed to fetch trade dates.", reply_markup=InlineKeyboardMarkup([[BACK_BTN]]))
        try: await _loading.delete()
        except Exception: pass
        conn.close()
        return
    conn.close()
    
    # Generate chart
    chart_buf = _generate_oi_change_chart(ticker, today_date, prev_date)
    
    if chart_buf is None:
        await query.message.reply_text(
            f"❌ No OI data available for {ticker}.",
            reply_markup=InlineKeyboardMarkup([[BACK_BTN]]))
        try: await _loading.delete()
        except Exception: pass
        return
    
    # Send chart
    try:
        await query.message.reply_photo(
            photo=chart_buf,
            caption=f"📊 <b>{ticker} OI Change Analysis (EOD)</b>\n{prev_date} → {today_date}\n\n🟦 Blue=Calls above 0 · 🟥 Red=Puts below 0\nTaller bars = more contracts. Call OI rising = bullish flow. Put OI rising = bearish/hedge flow.",
            parse_mode=H
        )
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔴 Try Live vs EOD", callback_data=f"oi_change_live_{ticker}")],
            [InlineKeyboardButton("📊 Other Ticker", callback_data="oi_change_menu")],
            [InlineKeyboardButton("📊 OI Menu", callback_data="menu_oi"), BACK_BTN]
        ])
        await query.message.reply_text("Select another action:", parse_mode=H, reply_markup=kb)
    except Exception as e:
        log.error(f"Failed to send OI chart: {e}")
        await query.message.reply_text(f"❌ Failed to send chart: {e}", reply_markup=InlineKeyboardMarkup([[BACK_BTN]]))
    
    try: await _loading.delete()
    except Exception: pass


def _fetch_live_oi_data(ticker):
    """Fetch live OI data from yfinance for next 2 expiries"""
    try:
        tk = yf.Ticker(str(ticker).upper())
        expiries = list(tk.options or [])
        
        if not expiries or len(expiries) < 1:
            return None
        
        # Get next 2 expiries
        expiries = expiries[:2]
        
        live_data = []
        for exp in expiries:
            try:
                chain = tk.option_chain(exp)
                calls = chain.calls[['strike', 'openInterest']].rename(columns={'openInterest': 'openInt_Call'})
                puts = chain.puts[['strike', 'openInterest']].rename(columns={'openInterest': 'openInt_Put'})
                
                df = pd.merge(calls, puts, on='strike', how='outer').fillna(0)
                # Convert YYYY-MM-DD (from yfinance) to MM-DD-YYYY (database format)
                df['expiry'] = datetime.strptime(exp, "%Y-%m-%d").strftime("%m-%d-%Y")
                live_data.append(df)
            except Exception as e:
                log.warning(f"Failed to fetch live OI for {ticker} expiry {exp}: {e}")
                continue
        
        if not live_data:
            return None
        
        return live_data
    except Exception as e:
        log.error(f"Failed to fetch live OI for {ticker}: {e}")
        return None


def _generate_live_vs_eod_chart(ticker, live_data_list, eod_date):
    """
    Enhanced Live vs EOD OI chart -- 2-panel per expiry.
    Top  : EOD ghost bars + Live solid bars + ATM zone + spot line.
    Bottom: OI delta bars coloured by _oi_intent_algo classification.
    Returns (buf, signal_list) where signal_list has one dict per expiry.
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot    as plt
    import matplotlib.patches   as mpatches
    import matplotlib.gridspec  as gridspec

    try:
        _sh  = yf.Ticker(ticker).history(period="2d")
        spot = float(_sh["Close"].iloc[-1]) if len(_sh) >= 1 else None
    except Exception:
        spot = None

    conn = get_conn()
    n    = len(live_data_list)
    fig  = plt.figure(figsize=(12, 7 * n))
    buf  = BytesIO()
    signal_list = []  # collect per-expiry signal data for the text write-up

    try:
        outer_gs = gridspec.GridSpec(n, 1, figure=fig, hspace=0.35)

        for idx, live_df in enumerate(live_data_list):
            expiry = live_df["expiry"].iloc[0]

            try:
                df_eod = pd.read_sql(
                    "SELECT strike, openInt_Call AS openInt_Call_eod, "
                    "openInt_Put AS openInt_Put_eod FROM options_daily "
                    "WHERE ticker=? AND trade_date=? AND expiry_date=? ORDER BY strike",
                    conn, params=(ticker.upper(), eod_date, expiry))
            except Exception as e:
                log.warning(f"EOD fetch {ticker}/{expiry}: {e}")
                df_eod = pd.DataFrame()

            gs      = gridspec.GridSpecFromSubplotSpec(
                2, 1, subplot_spec=outer_gs[idx],
                height_ratios=[3, 1.4], hspace=0.08)
            ax_main  = fig.add_subplot(gs[0])
            ax_delta = fig.add_subplot(gs[1])

            if df_eod.empty:
                ax_main.text(0.5, 0.5, f"No EOD data for {expiry}", ha="center", va="center")
                ax_main.set_title(f"{ticker}  {expiry}  -- No EOD Data")
                ax_delta.set_visible(False)
                signal_list.append({"expiry": expiry, "sig": "N/A", "sig_desc": "No EOD data"})
                continue

            df = live_df.merge(df_eod, on="strike", how="outer").fillna(0)
            df = df.sort_values("strike").reset_index(drop=True)
            df["call_oi_change"] = df["openInt_Call"]     - df["openInt_Call_eod"]
            df["put_oi_change"]  = df["openInt_Put"]      - df["openInt_Put_eod"]

            if spot:
                df = df[(df["strike"] >= spot * 0.70) & (df["strike"] <= spot * 1.30)].reset_index(drop=True)

            strikes = df["strike"].values
            if len(strikes) < 2:
                ax_main.text(0.5, 0.5, "Insufficient strike data", ha="center", va="center")
                ax_delta.set_visible(False)
                signal_list.append({"expiry": expiry, "sig": "N/A", "sig_desc": "Insufficient data"})
                continue
            # Bar width: 4 sub-bars per strike clearly separated
            # Layout: [EOD-call][Live-call]  gap  [EOD-put][Live-put]
            strike_step = float(strikes[1] - strikes[0])
            bw  = strike_step * 0.20   # each sub-bar width
            gap = bw * 0.30            # gap between call group and put group

            # Call group left of strike center; put group right
            c_eod_x  = strikes - gap/2 - 1.5*bw   # EOD call (leftmost)
            c_live_x = strikes - gap/2 - 0.5*bw   # Live call
            p_eod_x  = strikes + gap/2 + 0.5*bw   # EOD put
            p_live_x = strikes + gap/2 + 1.5*bw   # Live put (rightmost)

            # Shared axis limits and tick positions (used by both top and delta panels)
            xlim_lo = strikes[0]  - strike_step * 0.9
            xlim_hi = strikes[-1] + strike_step * 0.9
            _step_x = max(1, len(strikes) // 12)
            xtick_pos    = strikes[::_step_x]
            xtick_labels = [f"${s:.0f}" for s in xtick_pos]

            if spot and len(df):
                df, sig, sig_col, sig_desc, dets = _oi_intent_algo(df, spot)
            else:
                sig, sig_col, sig_desc, dets = "N/A", "#455A64", "Spot unavailable", {}
                df["bar_col"] = "#90A4AE"
                df["intent"]  = "NEUTRAL"

            # ── Top panel: 4 distinct sub-bars per strike ──
            # Light green = EOD calls, Dark green = Live calls
            # Light red   = EOD puts,  Dark red   = Live puts
            ax_main.bar(c_eod_x,  df["openInt_Call_eod"], bw, color="#81C784", alpha=0.95, label="Call EOD")
            ax_main.bar(c_live_x, df["openInt_Call"],      bw, color="#1B5E20", alpha=0.95, label="Call LIVE")
            ax_main.bar(p_eod_x,  -df["openInt_Put_eod"], bw, color="#EF9A9A", alpha=0.95, label="Put EOD")
            ax_main.bar(p_live_x, -df["openInt_Put"],      bw, color="#B71C1C", alpha=0.95, label="Put LIVE")
            ax_main.axhline(0, color="#212121", linewidth=0.8)

            if spot:
                ax_main.axvspan(spot*0.97, spot*1.03, alpha=0.07, color="yellow", label="ATM \u00b13%")
                ax_main.axvline(spot, color="#FFD600", linewidth=1.5, linestyle="--", label=f"Spot ${spot:.1f}")

            ax_main.set_title(
                f"{ticker}  |  Expiry: {expiry}  |  LIVE vs EOD {eod_date}\n"
                f"\u2592 Light = Yesterday (EOD)  \u2588 Dark = Today (LIVE)",
                fontsize=10, fontweight="bold")
            ax_main.set_ylabel("Open Interest", fontsize=9)
            ax_main.set_xlim(xlim_lo, xlim_hi)
            ax_main.set_xticks(xtick_pos)
            ax_main.set_xticklabels(xtick_labels, rotation=45, ha="right", fontsize=7)
            ax_main.tick_params(axis="x", bottom=True, labelbottom=True)
            ax_main.legend(loc="upper left", fontsize=7, ncol=2, framealpha=0.85)
            ax_main.yaxis.set_major_formatter(
                plt.FuncFormatter(lambda x, _: f"{abs(x)/1000:.0f}K" if abs(x) >= 1000 else f"{x:.0f}"))
            ax_main.grid(True, alpha=0.25, axis="y")
            total_call_chg = float(df["call_oi_change"].sum())
            total_put_chg  = float(df["put_oi_change"].sum())
            pcr_eod  = df["openInt_Put_eod"].sum() / max(df["openInt_Call_eod"].sum(), 1)
            pcr_live = df["openInt_Put"].sum()      / max(df["openInt_Call"].sum(),     1)
            call_pct = total_call_chg / max(df["openInt_Call_eod"].sum(), 1) * 100
            put_pct  = total_put_chg  / max(df["openInt_Put_eod"].sum(),  1) * 100

            stats = ("OI CHANGES\n"
                     f"Call: {total_call_chg:+,.0f}  ({call_pct:+.1f}%)\n"
                     f"Put:  {total_put_chg:+,.0f}  ({put_pct:+.1f}%)\n"
                     f"PCR:  {pcr_eod:.2f} -> {pcr_live:.2f}"
                     + (f"\nHedge %: {dets.get('hedge_pct',0):.0f}%" if dets else ""))
            ax_main.text(0.01, 0.04, stats, transform=ax_main.transAxes, va="bottom", fontsize=7.5,
                         bbox=dict(boxstyle="round,pad=0.4", facecolor="#FFFDE7", edgecolor="#F9A825", alpha=0.92))
            _sig_label = "SIGNAL: " + sig + "\n" + "--"*11 + "\n" + sig_desc
            ax_main.text(0.99, 0.98, _sig_label,
                         transform=ax_main.transAxes, va="top", ha="right",
                         fontsize=8, fontweight="bold", color="white",
                         bbox=dict(boxstyle="round,pad=0.5", facecolor=sig_col, edgecolor="white", alpha=0.93))

            # Per-strike analysis for write-up (gamma walls + max pain proxy)
            # Restrict to ±15% of spot so deep-ITM institutional hedges
            # (e.g. $310 puts on Jun-18 with spot at $376) don't hijack the wall
            _df_near = df[df["strike"].between(spot * 0.85, spot * 1.15)] if spot else df
            if _df_near.empty:
                _df_near = df
            _top_call_row = _df_near.loc[_df_near["openInt_Call"].idxmax()] if len(_df_near) > 0 else None
            _top_put_row  = _df_near.loc[_df_near["openInt_Put"].idxmax()]  if len(_df_near) > 0 else None
            _call_wall    = float(_top_call_row["strike"]) if _top_call_row is not None else 0
            _put_wall     = float(_top_put_row["strike"])  if _top_put_row is not None else 0
            _call_wall_oi = int(_top_call_row["openInt_Call"]) if _top_call_row is not None else 0
            _put_wall_oi  = int(_top_put_row["openInt_Put"])   if _top_put_row is not None else 0
            # Max pain = strike minimising sum of ITM losses
            try:
                _mp_candidates = df["strike"].values
                _mp_losses = []
                for _s in _mp_candidates:
                    _loss = ((_mp_candidates[_mp_candidates > _s]  - _s) * df.loc[df["strike"] > _s,  "openInt_Call"].values).sum() + \
                            ((_s - _mp_candidates[_mp_candidates < _s]) * df.loc[df["strike"] < _s, "openInt_Put"].values).sum()
                    _mp_losses.append(_loss)
                _mp_strike = float(_mp_candidates[int(len(_mp_losses) > 0 and _mp_losses.index(min(_mp_losses)))])
            except Exception:
                _mp_strike = 0

            # Store signal data for text write-up
            signal_list.append({
                "expiry": expiry, "sig": sig, "sig_col": sig_col, "sig_desc": sig_desc,
                "call_chg": total_call_chg, "put_chg": total_put_chg,
                "call_pct": call_pct, "put_pct": put_pct,
                "pcr_eod": pcr_eod, "pcr_live": pcr_live,
                "hedge_pct": dets.get("hedge_pct", 0) if dets else 0,
                "score": dets.get("score", 0) if dets else 0,
                "call_wall": _call_wall, "call_wall_oi": _call_wall_oi,
                "put_wall": _put_wall,   "put_wall_oi":  _put_wall_oi,
                "mp_strike": _mp_strike, "spot": spot,
            })

            # Bottom delta panel -- skip if deltas are all near-zero (e.g. near-expiry noise)
            max_delta = max(abs(df["call_oi_change"]).max(), abs(df["put_oi_change"]).max())
            if max_delta < 10:
                ax_delta.text(0.5, 0.5, "OI delta too small to display (near-expiry)",
                              ha="center", va="center", fontsize=8, color="#888")
                ax_delta.set_xlim(xlim_lo, xlim_hi)
                ax_delta.set_xticks(xtick_pos)
                ax_delta.set_xticklabels(xtick_labels, rotation=45, ha="right", fontsize=7)
                ax_delta.set_xlabel("Strike Price", fontsize=9, fontweight="bold")
                ax_delta.set_ylabel("OI \u0394", fontsize=9)
                ax_delta.grid(False)
                continue

            # Delta bars aligned to SAME positions as top-panel bars:
            # Call delta centered at same x as live-call bar (c_live_x)
            # Put delta  centered at same x as live-put  bar (p_live_x)
            # Bar width = 2*bw so it covers the full call/put group width
            delta_bar_w = bw * 2.0

            # Color call delta by intent (from _oi_intent_algo)
            # Put delta: green if put OI fell (unwinding), red if put OI grew (bearish build)
            for s, cd, pd_, col, intent in zip(
                    strikes, df["call_oi_change"], df["put_oi_change"], df["bar_col"], df["intent"]):
                # call delta: same center as live-call bar group
                cx = s - gap/2 - bw   # midpoint of [c_eod_x, c_live_x] group
                ax_delta.bar(cx, cd, delta_bar_w, color=col, alpha=0.88, linewidth=0.3, edgecolor="#333")
                # put delta: same center as live-put bar group; inverted (put growth = negative)
                px_ = s + gap/2 + bw  # midpoint of [p_eod_x, p_live_x] group
                put_col = "#C62828" if pd_ > 0 else "#43A047"  # red if puts grew, green if fell
                ax_delta.bar(px_, -pd_, delta_bar_w, color=put_col, alpha=0.88, linewidth=0.3, edgecolor="#333")

            ax_delta.axhline(0, color="#212121", linewidth=0.9)
            if spot:
                ax_delta.axvspan(spot*0.97, spot*1.03, alpha=0.09, color="yellow")
                ax_delta.axvline(spot, color="#FFD600", linewidth=1.2, linestyle="--")

            # Shared x-axis ticks — same positions as top panel
            ax_delta.set_xlim(xlim_lo, xlim_hi)
            ax_delta.set_xticks(xtick_pos)
            ax_delta.set_xticklabels(xtick_labels, rotation=45, ha="right", fontsize=7)
            ax_delta.set_xlabel("Strike Price", fontsize=9, fontweight="bold")
            ax_delta.set_ylabel("OI \u0394 (Change)", fontsize=9)
            ax_delta.yaxis.set_major_formatter(
                plt.FuncFormatter(lambda x, _: f"{abs(x)/1000:.0f}K" if abs(x) >= 1000 else f"{x:.0f}"))
            ax_delta.grid(True, alpha=0.2, axis="y")

            # Legend
            _IC = {"Call\u0394 BULL":"#2E7D32","Call\u0394 BEAR":"#C62828",
                   "Call\u0394 HEDGE":"#1565C0","Call\u0394 STRADDLE":"#6A1B9A",
                   "Put\u0394 grew":"#C62828","Put\u0394 fell":"#43A047"}
            ax_delta.legend(
                handles=[mpatches.Patch(color=c, label=l) for l,c in _IC.items()],
                loc="lower right", fontsize=6, ncol=3, framealpha=0.85)
        fig.savefig(buf, format="png", dpi=110, bbox_inches="tight")
        plt.close(fig)
        buf.seek(0)
        conn.close()
        return buf, signal_list

    except Exception as e:
        log.error(f"Live chart error for {ticker}: {e}", exc_info=True)
        try: plt.close(fig)
        except Exception: pass
        conn.close()
        return None, []



async def oi_change_chart_live_view(query, ticker):
    """Show Live OI vs Last EOD comparison chart"""
    _loading = await query.message.reply_text(
        f"⏳ Fetching LIVE OI data for {ticker} from Yahoo Finance...\n\n"
        "This may take 10-30 seconds.",
        parse_mode=H
    )
    
    # Get last EOD date (MM-DD-YYYY format, sort chronologically)
    conn = get_conn()
    try:
        latest_date_df = pd.read_sql("""
            SELECT DISTINCT trade_date FROM options_daily 
            ORDER BY substr(trade_date,7,4)||substr(trade_date,1,2)||substr(trade_date,4,2) DESC LIMIT 1
        """, conn)
        if latest_date_df.empty:
            await query.message.reply_text("📊 No EOD data available.", reply_markup=InlineKeyboardMarkup([[BACK_BTN]]))
            try: await _loading.delete()
            except Exception: pass
            conn.close()
            return
        
        eod_date = latest_date_df['trade_date'].iloc[0]
    except Exception as e:
        log.error(f"Failed to get EOD date: {e}")
        await query.message.reply_text("❌ Failed to fetch EOD date.", reply_markup=InlineKeyboardMarkup([[BACK_BTN]]))
        try: await _loading.delete()
        except Exception: pass
        conn.close()
        return
    conn.close()
    
    # Fetch live OI data
    live_data = _fetch_live_oi_data(ticker)
    
    if live_data is None or len(live_data) == 0:
        await query.message.reply_text(
            f"❌ Failed to fetch live OI data for {ticker}.\n\n"
            "Possible reasons:\n"
            "• Ticker not found on Yahoo Finance\n"
            "• No options available\n"
            "• Market closed / data temporarily unavailable",
            reply_markup=InlineKeyboardMarkup([[BACK_BTN]])
        )
        try: await _loading.delete()
        except Exception: pass
        return
    
    await _loading.edit_text(f"⏳ Generating live comparison chart for {ticker}...", parse_mode=H)

    # Generate chart -- returns (buf, signal_list)
    chart_buf, signal_list = _generate_live_vs_eod_chart(ticker, live_data, eod_date)

    if chart_buf is None:
        await query.message.reply_text(
            f"❌ Failed to generate chart for {ticker}.",
            reply_markup=InlineKeyboardMarkup([[BACK_BTN]])
        )
        try: await _loading.delete()
        except Exception: pass
        return

    # Send chart photo
    try:
        from datetime import datetime as dt
        now_time = dt.now().strftime("%Y-%m-%d %H:%M")

        await query.message.reply_photo(
            photo=chart_buf,
            caption=f"🔴 <b>{ticker} LIVE OI vs Last EOD</b>\n"
                   f"EOD: {eod_date} · Live: {now_time}\n\n"
                   f"🟦 Blue=Calls(above 0) · 🟥 Red=Puts(below 0)\n"
                   f"Striped=yesterday · Solid=live now\n"
                   f"Call OI growing = bullish flow. Put OI growing = bearish/hedge.",
            parse_mode=H
        )

        # Fetch open trades for this ticker to show position P&L context
        _conn_t = get_conn()
        try:
            import pandas as _pd_t
            _trades_df = _pd_t.read_sql_query(
                "SELECT * FROM trades WHERE ticker=? AND status='OPEN'",
                _conn_t, params=(ticker,))
        except Exception:
            _trades_df = None
        finally:
            _conn_t.close()

        _ah_data = _get_spot_with_ah(ticker)
        _spot_now = _ah_data["spot_reg"]
        _spot_ext = _ah_data["spot_ext"] if _ah_data["is_extended"] else _spot_now

        # Build and send signal write-up for each expiry
        for sd in signal_list:
            if sd.get("sig") in (None, "N/A"):
                continue
            sig        = sd["sig"]
            sig_desc   = sd.get("sig_desc", "")
            call_chg   = sd.get("call_chg", 0)
            put_chg    = sd.get("put_chg", 0)
            call_pct   = sd.get("call_pct", 0)
            put_pct    = sd.get("put_pct", 0)
            pcr_eod    = sd.get("pcr_eod", 0)
            pcr_live   = sd.get("pcr_live", 0)
            hedge_pct  = sd.get("hedge_pct", 0)
            expiry     = sd.get("expiry", "?")
            call_wall  = sd.get("call_wall", 0)
            put_wall   = sd.get("put_wall", 0)
            call_wall_oi = sd.get("call_wall_oi", 0)
            put_wall_oi  = sd.get("put_wall_oi", 0)
            mp_strike  = sd.get("mp_strike", 0)
            spot       = sd.get("spot", 0) or _spot_now

            sig_em = {"BULLISH":"📈","MILD BULL":"📈","BEARISH":"📉","MILD BEAR":"📉",
                      "STRADDLE":"↔️","HEDGE":"🛡","NEAR_BEARISH":"📉","COVERED_CALL":"📋",
                      "BULLISH_BREAK":"🚀","UNWIND":"⬇️"}.get(sig, "⚪")

            reasons = []
            if put_chg > 0 and abs(put_chg) > abs(call_chg):
                reasons.append(f"• Put OI grew {put_chg:+,.0f} ({put_pct:+.1f}%) — traders adding downside bets")
            if call_chg < 0:
                reasons.append(f"• Call OI fell {call_chg:+,.0f} ({call_pct:+.1f}%) — bulls reducing exposure")
            elif call_chg > 0 and call_chg > abs(put_chg):
                reasons.append(f"• Call OI grew {call_chg:+,.0f} ({call_pct:+.1f}%) — bullish positioning increasing")
            if pcr_live > pcr_eod * 1.05:
                reasons.append(f"• PCR rose {pcr_eod:.2f} → {pcr_live:.2f} (more puts vs calls = bearish lean)")
            elif pcr_live < pcr_eod * 0.95:
                reasons.append(f"• PCR fell {pcr_eod:.2f} → {pcr_live:.2f} (fewer puts vs calls = bullish lean)")
            if hedge_pct > 30:
                reasons.append(f"• {hedge_pct:.0f}% of puts are deep OTM hedges — institutional protection, not directional shorts")
            if sig == "STRADDLE":
                reasons.append("• Both calls AND puts building — market bracing for a big move (event play)")
            if not reasons:
                reasons.append(f"• {sig_desc}")

            # Strike levels block
            strike_lines = []
            if call_wall and spot:
                _cw_dist = (call_wall - spot) / spot * 100
                _cw_oi_k = f"{call_wall_oi/1000:.0f}K" if call_wall_oi >= 1000 else str(call_wall_oi)
                strike_lines.append(f"  Call Wall: ${call_wall:.0f} ({_cw_dist:+.1f}% from spot) OI:{_cw_oi_k} — CEILING")
            if put_wall and spot:
                _pw_dist = (put_wall - spot) / spot * 100
                _pw_oi_k = f"{put_wall_oi/1000:.0f}K" if put_wall_oi >= 1000 else str(put_wall_oi)
                strike_lines.append(f"  Put Wall:  ${put_wall:.0f} ({_pw_dist:+.1f}% from spot) OI:{_pw_oi_k} — FLOOR")
            if mp_strike and spot:
                _mp_dist = (mp_strike - spot) / spot * 100
                _mp_dir  = "above" if mp_strike > spot else "below"
                strike_lines.append(f"  Max Pain:  ${mp_strike:.0f} ({_mp_dist:+.1f}%) {_mp_dir} spot — expiry magnet")

            # Simple Answer targets
            bull_target = call_wall if call_wall > spot else (spot * 1.03 if spot else 0)
            bear_target = put_wall  if put_wall  < spot else (spot * 0.97 if spot else 0)
            simple_ans = ""
            if spot:
                simple_ans = (
                    f"\n🎯 <b>Simple Answer</b>\n"
                    f"{'✅' if sig not in ('BEARISH','NEAR_BEARISH','MILD BEAR') else '⚠️'} "
                    f"Bullish scenario → ${bull_target:.0f} target"
                    + (f" (call wall)" if call_wall > spot else " (+3% est)")
                    + f"\n{'⚠️' if sig not in ('BEARISH','NEAR_BEARISH','MILD BEAR') else '❌'} "
                    f"Bearish scenario → ${bear_target:.0f} target"
                    + (f" (put wall)" if put_wall < spot else " (-3% est)")
                    + (f"\n🧲 Max Pain ${mp_strike:.0f} — price may drift here by expiry" if mp_strike else "")
                )

            # Open position P&L for this expiry
            pos_lines = []
            if _trades_df is not None and len(_trades_df) > 0:
                _r_rate = 0.045
                for _, tr in _trades_df.iterrows():
                    try:
                        _ot   = str(tr.get("option_type", "")).lower()
                        _strk = _safe_float(tr.get("strike", 0), 0)
                        _ep   = _safe_float(tr.get("entry_price", 0), 0)
                        _qty  = _safe_int(tr.get("quantity", 1), 1)
                        _exp  = str(tr.get("expiry", ""))[:10]
                        _dte  = max((datetime.strptime(_exp, "%Y-%m-%d").date() - datetime.now().date()).days, 1)
                        _T_now   = max(_dte, 1) / 365.0
                        _T_tmrw  = max(_dte - 1, 0.5) / 365.0
                        _T_expiry = 0.001  # near-zero at expiry
                        _iv   = 0.35
                        _sign = 1 if _qty > 0 else -1
                        _side = "LONG" if _qty > 0 else "SHORT"
                        _contracts = abs(_qty)
                        _val_now    = bs_price(_spot_now, _strk, _T_now,    _r_rate, _iv, opt=_ot)
                        _val_tmrw   = bs_price(_spot_ext, _strk, _T_tmrw,   _r_rate, _iv, opt=_ot)
                        _val_expiry = bs_price(_spot_ext, _strk, _T_expiry, _r_rate, _iv, opt=_ot)
                        _pnl_now    = (_val_now    - _ep) * 100 * _contracts * _sign
                        _pnl_tmrw   = (_val_tmrw   - _ep) * 100 * _contracts * _sign
                        _pnl_expiry = (_val_expiry - _ep) * 100 * _contracts * _sign
                        _em_now    = "🟢" if _pnl_now    >= 0 else "🔴"
                        _em_tmrw   = "🟢" if _pnl_tmrw   >= 0 else "🔴"
                        _em_expiry = "🟢" if _pnl_expiry  >= 0 else "🔴"
                        _expiry_label = "at expiry" if _dte > 1 else "⚠️ EXPIRING SOON"
                        pos_lines.append(
                            f"  {_side} {_ot.upper()} ${_strk:.0f} x{_contracts} [{_dte}d to exp]\n"
                            f"    {_em_now}  Now:       ${_val_now:.2f}  P&amp;L ${_pnl_now:+,.0f}\n"
                            f"    {_em_tmrw}  Tomorrow:  ${_val_tmrw:.2f}  P&amp;L ${_pnl_tmrw:+,.0f}\n"
                            f"    {_em_expiry}  {_expiry_label}: ${_val_expiry:.2f}  P&amp;L ${_pnl_expiry:+,.0f}"
                        )
                    except Exception:
                        pass

            writeup = (
                f"{sig_em} <b>{ticker} OI SIGNAL — {expiry}</b>\n"
                f"<b>Verdict: {sig}</b>\n\n"
                f"<b>Why {sig.title()}?</b>\n"
                + "\n".join(reasons)
                + (f"\n\n<b>Key Strike Levels</b>\n<pre>" + "\n".join(strike_lines) + "</pre>" if strike_lines else "")
                + simple_ans
                + (f"\n\n<b>Your Open Positions — P&amp;L Impact</b>\n<pre>" + "\n\n".join(pos_lines) + "</pre>" if pos_lines else "")
                + f"\n\n<i>{sig_desc}</i>"
            )
            await query.message.reply_text(writeup, parse_mode=H)

        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 Refresh Live Data", callback_data=f"oi_change_live_{ticker}")],
            [InlineKeyboardButton("📊 See EOD vs EOD", callback_data=f"oi_change_eod_{ticker}")],
            [InlineKeyboardButton("📊 Other Ticker", callback_data="oi_change_menu")],
            [InlineKeyboardButton("📊 OI Menu", callback_data="menu_oi"), BACK_BTN]
        ])
        await query.message.reply_text("Select another action:", parse_mode=H, reply_markup=kb)
    except Exception as e:
        log.error(f"Failed to send live OI chart: {e}")
        await query.message.reply_text(f"❌ Failed to send chart: {e}", reply_markup=InlineKeyboardMarkup([[BACK_BTN]]))

    try: await _loading.delete()
    except Exception: pass


async def nyse_daily_report_menu(query):
    """Show NYSE Daily Report generation menu"""
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 Top 10 Tickers", callback_data="nyse_report_top10"),
         InlineKeyboardButton("📊 Top 20 Tickers", callback_data="nyse_report_top20")],
        [InlineKeyboardButton("📊 All Tickers (Slow)", callback_data="nyse_report_all")],
        [InlineKeyboardButton("⬅️ Back", callback_data="menu_more"), BACK_BTN],
    ])
    await query.message.reply_text(
        f"{hdr('📊 NYSE DAILY REPORT')}\n\n"
        "Generate full OI analysis with charts and strategies.\n\n"
        "⚠️ This may take several minutes depending on ticker count.\n\n"
        "Select number of tickers to analyze:",
        parse_mode=H,
        reply_markup=kb,
    )


async def generate_nyse_report(query, max_symbols=10):
    """Generate NYSE Daily Report using subprocess"""
    _status = await query.message.reply_text(
        f"⏳ Generating NYSE Daily Report for top {max_symbols} tickers...\n\n"
        "This will take a few minutes. Please wait.",
        parse_mode=H
    )
    
    import subprocess
    import sys
    
    try:
        # Set environment variable for MAX_SYMBOLS
        env = os.environ.copy()
        env['MAX_SYMBOLS'] = str(max_symbols)
        env['GENERATE_OI_PNG'] = '1'
        env['GENERATE_EXCEL'] = '1'
        env['SEND_TELEGRAM'] = '0'  # We'll send through bot, not NYSE_Telegram script
        env['DRY_RUN'] = '0'
        
        # Run NYSE_Telegram.py as subprocess
        nyse_script = os.path.join(NYSE_DIR, "NYSE_Telegram.py")
        
        await _status.edit_text(
            f"⏳ Running analysis...\n\n"
            f"Processing {max_symbols} tickers with OI charts and strategies.\n\n"
            f"Status: Starting...",
            parse_mode=H
        )
        
        result = subprocess.run(
            [sys.executable, nyse_script],
            env=env,
            capture_output=True,
            text=True,
            timeout=600  # 10 minute timeout
        )
        
        if result.returncode != 0:
            error_msg = result.stderr[-500:] if result.stderr else "Unknown error"
            await _status.edit_text(
                f"❌ Report generation failed.\n\n"
                f"Error: {error_msg}",
                parse_mode=H
            )
            await query.message.reply_text(
                "Failed to generate report. Check logs.",
                reply_markup=InlineKeyboardMarkup([[BACK_BTN]])
            )
            return
        
        # Find generated files (MM-DD-YYYY format, sort chronologically)
        conn = get_conn()
        try:
            latest_date_df = pd.read_sql(
                "SELECT DISTINCT trade_date_now FROM options_change ORDER BY substr(trade_date_now,7,4)||substr(trade_date_now,1,2)||substr(trade_date_now,4,2) DESC LIMIT 1",
                conn
            )
            if latest_date_df.empty:
                await _status.edit_text("❌ No trade date found.", parse_mode=H)
                conn.close()
                return
            
            latest_date = latest_date_df["trade_date_now"].iloc[0]
        except Exception as e:
            await _status.edit_text(f"❌ Error fetching trade date: {e}", parse_mode=H)
            conn.close()
            return
        conn.close()
        
        # Path to generated files
        charts_dir = os.path.join(DATA_DIR, "US_CHARTS", latest_date)
        excel_file = os.path.join(charts_dir, f"Summary_{latest_date}.xlsx")
        
        if not os.path.exists(charts_dir):
            await _status.edit_text(
                f"❌ Charts directory not found: {charts_dir}",
                parse_mode=H
            )
            return
        
        # Get all chart files
        chart_files = []
        try:
            for fname in sorted(os.listdir(charts_dir)):
                if fname.endswith('_OI.png'):
                    chart_files.append(os.path.join(charts_dir, fname))
        except Exception as e:
            await _status.edit_text(f"❌ Error listing charts: {e}", parse_mode=H)
            return
        
        await _status.edit_text(
            f"✅ Analysis complete!\n\n"
            f"📊 Generated {len(chart_files)} charts\n"
            f"📄 Excel summary ready\n\n"
            f"Sending files...",
            parse_mode=H
        )
        
        # Send summary message
        summary_text = (
            f"📊 <b>NYSE Daily Report - {latest_date}</b>\n\n"
            f"Analyzed {max_symbols} tickers\n"
            f"Generated {len(chart_files)} OI charts\n\n"
            f"Charts and summary incoming..."
        )
        await query.message.reply_text(summary_text, parse_mode=H)
        
        # Send Excel first
        if os.path.exists(excel_file):
            try:
                with open(excel_file, 'rb') as f:
                    await query.message.reply_document(
                        document=f,
                        filename=f"NYSE_Summary_{latest_date}.xlsx",
                        caption=f"📊 Strategy Summary - {latest_date}"
                    )
            except Exception as e:
                log.error(f"Failed to send Excel: {e}")
        
        # Send charts (limit to first 10 to avoid flooding)
        charts_to_send = chart_files[:10]
        for idx, chart_path in enumerate(charts_to_send, 1):
            try:
                ticker = os.path.basename(chart_path).replace('_OI.png', '')
                with open(chart_path, 'rb') as f:
                    await query.message.reply_photo(
                        photo=f,
                        caption=f"📊 {ticker} OI Analysis ({idx}/{len(charts_to_send)})"
                    )
            except Exception as e:
                log.error(f"Failed to send chart {chart_path}: {e}")
        
        if len(chart_files) > 10:
            await query.message.reply_text(
                f"ℹ️ {len(chart_files) - 10} more charts available in:\n{charts_dir}",
                parse_mode=H
            )
        
        await _status.edit_text(
            f"✅ <b>Report Complete!</b>\n\n"
            f"Sent {min(len(chart_files), 10)} charts and Excel summary.",
            parse_mode=H
        )
        
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 Generate Again", callback_data="menu_nyse_report")],
            [InlineKeyboardButton("🧩 More Features", callback_data="menu_more"), BACK_BTN]
        ])
        await query.message.reply_text("What's next?", parse_mode=H, reply_markup=kb)
        
    except subprocess.TimeoutExpired:
        await _status.edit_text(
            "❌ Report generation timed out (>10 minutes).\n\n"
            "Try using fewer tickers or check if the script is running correctly.",
            parse_mode=H
        )
    except Exception as e:
        log.error(f"NYSE report generation error: {e}")
        await _status.edit_text(
            f"❌ Error generating report:\n{str(e)[:200]}",
            parse_mode=H
        )

# ═══════════════════════════════════════════════════════════
#  6) SIGNAL SCANNER — tabulated
# ═══════════════════════════════════════════════════════════
async def signal_scanner(query):
    _loading = await query.message.reply_text("⏳ Scanning signals...", parse_mode=H)
    conn = get_conn()
    try:
        # Get latest date using proper MM-DD-YYYY sort
        latest_row = pd.read_sql("""
            SELECT DISTINCT trade_date_now FROM options_change
            ORDER BY substr(trade_date_now,7,4)||substr(trade_date_now,1,2)||substr(trade_date_now,4,2) DESC
            LIMIT 1
        """, conn)
        if latest_row.empty:
            raise ValueError("No options_change data")
        latest_date = latest_row["trade_date_now"].iloc[0]

        # Aggregate per ticker for the latest date
        df = pd.read_sql("""
            SELECT ticker,
                   SUM(change_OI_Call)      AS call_oi_chg,
                   SUM(change_OI_Put)       AS put_oi_chg,
                   SUM(vol_Call_now)        AS call_vol,
                   SUM(vol_Put_now)         AS put_vol,
                   AVG(pct_change_OI_Call)  AS call_pct,
                   AVG(pct_change_OI_Put)   AS put_pct,
                   SUM(openInt_Call_now)    AS call_oi_total,
                   SUM(openInt_Put_now)     AS put_oi_total
            FROM options_change
            WHERE trade_date_now = ?
            GROUP BY ticker
            HAVING (ABS(SUM(change_OI_Call)) + ABS(SUM(change_OI_Put))) > 50
        """, conn, params=(latest_date,))
    except Exception as e:
        log.warning(f"signal_scanner failed: {e}")
        df = pd.DataFrame()
    conn.close()

    if df.empty:
        await query.message.reply_text("🔥 No signals available.",
                                       reply_markup=InlineKeyboardMarkup([[BACK_BTN]]))
        try: await _loading.delete()
        except Exception: pass
        return

    df["pcr"] = df["put_oi_total"] / df["call_oi_total"].replace(0, float("nan"))
    df["net_chg"] = df["call_oi_chg"] - df["put_oi_chg"]
    latest_date_str = latest_date if 'latest_date' in dir() else "?"

    parts = [hdr(f"🔥 OI SIGNALS · {latest_date_str}")]

    def _fk(n):
        n = float(n or 0); s = "+" if n >= 0 else ""
        a = abs(n)
        if a >= 1_000_000: return f"{s}{a/1_000_000:.1f}M"
        if a >= 1_000:     return f"{s}{a/1_000:.0f}K"
        return f"{s}{n:.0f}"

    def signal_table(label, sub_df, badge=""):
        """Narrow 5-col <pre> table — target ≤33 chars per row for mobile."""
        # ST(3) | Tkr(4) | C-OI(4) | P-OI(4) | PCR(4)  =  ~31 chars
        _hdrs  = ["ST", "Tkr", "C-OI", "P-OI", "PCR"]
        _RIGHT = {2, 3, 4}
        _rows  = []
        for _, r in sub_df.head(6).iterrows():
            c   = float(r["call_oi_chg"] or 0)
            p   = float(r["put_oi_chg"]  or 0)
            pcr = float(r["pcr"]) if r["pcr"] == r["pcr"] else 0.0
            _rows.append([badge, str(r["ticker"])[:5], _fk(c), _fk(p), f"{pcr:.2f}"])
        if not _rows:
            return ""
        _cw = [max(len(_hdrs[i]), max(len(rr[i]) for rr in _rows)) for i in range(len(_hdrs))]
        _jn = lambda i, v: v.rjust(_cw[i]) if i in _RIGHT else v.ljust(_cw[i])
        _sep = "-+-".join("-" * w for w in _cw)
        lines = [" | ".join(_jn(i, _hdrs[i]) for i in range(len(_hdrs))), _sep]
        for rr in _rows:
            lines.append(" | ".join(_jn(i, rr[i]) for i in range(len(_hdrs))))
        return f"\n<b>{label}</b>\n<pre>" + "\n".join(lines) + "</pre>"

    # Classify each ticker using hedge-aware algorithm
    def _scan_sig(row):
        lbl, _ = _oi_signal_light(row["call_oi_chg"], row["put_oi_chg"], row.get("pcr", 1.0))
        return lbl
    df["oi_sig"] = df.apply(_scan_sig, axis=1)
    df["total_chg"] = df["call_oi_chg"].abs() + df["put_oi_chg"].abs()

    bulls   = df[df["oi_sig"] == "BULLISH"].nlargest(6, "call_oi_chg")
    bears   = df[df["oi_sig"] == "BEARISH"].nlargest(6, "put_oi_chg")
    hedges  = df[df["oi_sig"] == "HEDGE"].nlargest(4, "put_oi_chg")
    unusual = df[df["oi_sig"].isin(["STRADDLE", "BULL+HEDGE"])].nlargest(4, "total_chg")

    if not bulls.empty:
        parts.append(signal_table("🟢 BULLISH — Call OI Building", bulls, badge="[B]"))
    if not bears.empty:
        parts.append(signal_table("🔴 BEARISH — Put OI Directional", bears, badge="[S]"))
    if not hedges.empty:
        parts.append(signal_table("🔵 HEDGE/PROTECT — Deep OTM Puts", hedges, badge="[H]"))
    if not unusual.empty:
        parts.append(signal_table("🟡 STRADDLE/EVENT — Both Sides Up", unusual, badge="[?]"))

    mixed = len(df) - len(bulls) - len(bears)
    parts.append(f"\n📊 <b>{len(df)} tickers</b> scanned · {mixed} mixed/neutral")

    # ── Per-ticker strike breakdown (top 3 bulls + top 3 bears) ──
    conn2 = get_conn()
    _strike_parts = []
    for _tk_row in list(bulls.head(3).itertuples()) + list(bears.head(3).itertuples()):
        _tk = str(_tk_row.ticker)
        _sd2 = pd.read_sql("""SELECT close FROM stock_daily WHERE ticker=?
            ORDER BY substr(trade_date,7,4)||substr(trade_date,1,2)||substr(trade_date,4,2) DESC
            LIMIT 1""", conn2, params=(_tk,))
        _spot2 = float(_sd2["close"].iloc[0]) if not _sd2.empty else 0.0
        _breakdown = _oi_strike_breakdown(_tk, conn2, _spot2, latest_date)
        _trend = _oi_trend_summary(_tk, conn2, latest_date)
        if _breakdown or _trend:
            _strike_parts.append(f"\n<b>🔍 {_tk}</b> spot=${_spot2:.2f}")
            if _trend:
                _strike_parts.append(f"<b>OI Trend:</b>\n{_trend}")
            if _breakdown:
                _strike_parts.append(f"<b>Strikes (±20% of spot):</b>\n{_breakdown}")
    conn2.close()

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Refresh", callback_data="menu_signals"),
         InlineKeyboardButton("🤖 MiroFish", callback_data="menu_mirofish")],
        [BACK_BTN]
    ])
    # Send main signals, then strike breakdown as separate messages to avoid 4096 limit
    main_msg = "\n".join(parts)
    if len(main_msg) > 4000:
        main_msg = main_msg[:4000] + "\n<i>…truncated</i>"
    await query.message.reply_text(main_msg, parse_mode=H, reply_markup=kb)

    if _strike_parts:
        strike_body = "\n".join(_strike_parts)
        for i, chunk_start in enumerate(range(0, len(strike_body), 3500)):
            chunk = strike_body[chunk_start:chunk_start + 3500]
            prefix = "📊 <b>STRIKE-LEVEL OI ANALYSIS</b>\n" if i == 0 else ""
            await query.message.reply_text(prefix + chunk, parse_mode=H)

    try: await _loading.delete()
    except Exception: pass


# ═══════════════════════════════════════════════════════════
#  7) INSIDER / CONGRESS — table format
# ═══════════════════════════════════════════════════════════
async def insider_menu(query):
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🏛 Congress", callback_data="insider_congress"),
         InlineKeyboardButton("👔 Insider", callback_data="insider_insider")],
        [BACK_BTN],
    ])
    await query.message.reply_text(
        f"{hdr('📈 INSIDER / CONGRESS')}\n\nSelect a category:",
        parse_mode=H, reply_markup=kb)

async def congress_trades(query):
    conn = get_conn()
    try:
        df = pd.read_sql("SELECT * FROM congress_trades ORDER BY rowid DESC LIMIT 10", conn)
    except Exception:
        df = pd.DataFrame()
    conn.close()

    if df.empty:
        await query.message.reply_text("🏛 No congress trades found.",
                                       reply_markup=InlineKeyboardMarkup([[BACK_BTN]]))
        return

    parts = [hdr("🏛 CONGRESS TRADES")]
    for _, r in df.iterrows():
        action = str(r.get("action", "?"))
        emoji = "🟢" if "buy" in action.lower() or "purchase" in action.lower() else "🔴"
        tk = r.get("ticker", "?")
        pol = r.get("politician_name", "?")
        party = r.get("party", "?")
        shares = r.get("shares", "?")
        parts.append(
            f"\n{emoji} <b>{tk}</b> · {action}\n"
            + mono(f"{pol} ({party})\n{shares} shares")
        )

    kb = InlineKeyboardMarkup([[InlineKeyboardButton("📈 Menu", callback_data="menu_insider"), BACK_BTN]])
    await _safe_reply(query.message, "\n".join(parts), reply_markup=kb)

async def insider_trades(query):
    conn = get_conn()
    try:
        df = pd.read_sql("SELECT * FROM insider_trades ORDER BY rowid DESC LIMIT 10", conn)
    except Exception:
        df = pd.DataFrame()
    conn.close()

    if df.empty:
        await query.message.reply_text("👔 No insider trades found.",
                                       reply_markup=InlineKeyboardMarkup([[BACK_BTN]]))
        return

    parts = [hdr("👔 INSIDER TRADES")]
    for _, r in df.iterrows():
        tx_type = str(r.get("transaction_type", "?"))
        emoji = "🟢" if "buy" in tx_type.lower() or "purchase" in tx_type.lower() else "🔴"
        tk = r.get("ticker", "?")
        name = r.get("insider_name", "?")
        title = r.get("position_title", "?")
        shares = r.get("shares", "?")
        dt = r.get("transaction_date", "?")
        parts.append(
            f"\n{emoji} <b>{tk}</b> · {tx_type}\n"
            + mono(f"{name}\n{title}\n{shares} shares · {dt}")
        )

    kb = InlineKeyboardMarkup([[InlineKeyboardButton("📈 Menu", callback_data="menu_insider"), BACK_BTN]])
    await _safe_reply(query.message, "\n".join(parts), reply_markup=kb)


# ═══════════════════════════════════════════════════════════
#  8b) EXTRA FEATURES (from extended dashboard updates)
# ═══════════════════════════════════════════════════════════
async def more_features_menu(query):
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🧠 Smart Money Hub", callback_data="menu_smart_money")],
        [InlineKeyboardButton("🎯 Gamma Advisor", callback_data="ga_positions"),
         InlineKeyboardButton("📐 Edge Lab",      callback_data="menu_edge_lab")],
        [InlineKeyboardButton("🔮 Live Predictor", callback_data="menu_livepred"),
         InlineKeyboardButton("🐋 Whale Holdings", callback_data="menu_whales")],
        [InlineKeyboardButton("🖥 Dashboard URL", callback_data="menu_streamlit_link")],
        [InlineKeyboardButton("📡 Market Analytics", callback_data="menu_analytics"),
         InlineKeyboardButton("🌍 Global Market", callback_data="menu_global_market")],
        [InlineKeyboardButton("📊 NYSE Daily Report", callback_data="menu_nyse_report"),
         InlineKeyboardButton("🤖 MiroFish Signals", callback_data="menu_mirofish")],
        [InlineKeyboardButton("🌙 AH Predictor", callback_data="menu_aftermarket_predict"),
         InlineKeyboardButton("⚠️ Overnight Risk", callback_data="menu_overnight_risk")],
        [InlineKeyboardButton("🎯 Recommend Engine", callback_data="menu_recommend"),
         InlineKeyboardButton("🎲 Monte Carlo Sim", callback_data="menu_exit")],
        [BACK_BTN],
    ])
    await query.message.reply_text(
        f"{hdr('🧩 MORE FEATURES')}\n\n",
        parse_mode=H,
        reply_markup=kb,
    )


async def market_analytics_report(query):
    """Full market analytics report: futures, OI signals, strategy playbook — InsiderFinance style."""

    def _fmt_k(n):
        """Compact OI number: +2.8M / +752K / +845  (max ~7 chars)."""
        n = float(n or 0)
        s = "+" if n >= 0 else ""
        a = abs(n)
        if a >= 1_000_000: return f"{s}{a/1_000_000:.1f}M"
        if a >= 1_000:     return f"{s}{a/1_000:.0f}K"
        return f"{s}{n:.0f}"

    _loading = await query.message.reply_text("⏳ Building market analytics...", parse_mode=H)
    now_et = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=5)
    parts = [hdr(f"📊 MARKET ANALYTICS  {now_et.strftime('%m-%d  %H:%M ET')}")]

    # ── 1. Futures & Indices ────────────────────────────────────────
    fut_syms = [
        ("ES",      "ES=F"),     ("NQ",      "NQ=F"),
        ("VIX",     "^VIX"),     ("Gold",    "GC=F"),
        ("Oil",     "CL=F"),     ("BTC",     "BTC-USD"),
        ("EUR/USD", "EURUSD=X"), ("10Y",     "^TNX"),
    ]
    # Each data row — compact 4-col, mobile-safe ≈28 chars (no Dir)
    _f_hdrs = ["ST", "Name", "Price", "Chg%"]
    _f_RIGHT = {2, 3, 4}
    _f_data  = []
    _f_chgs  = {}   # sym -> chg for macro correlations
    sentiment_score = 0
    vix_val = 0
    for name, sym in fut_syms:
        try:
            h = yf.Ticker(sym).history(period="5d")
            if len(h) >= 2:
                px   = float(h["Close"].iloc[-1])
                prev = float(h["Close"].iloc[-2])
                chg  = (px - prev) / prev * 100
                st    = "[+]" if chg > 0.5 else ("[!]" if chg < -0.5 else "[ ]")
                if sym in ("ES=F", "^GSPC"): sentiment_score += chg * 8
                if sym == "^VIX":            vix_val = px
                px_s = f"{px:,.2f}" if px < 1000 else f"{px:,.0f}"
                _f_data.append([st, name, px_s, f"{chg:+.2f}%", _col_arrow(chg)])
                _f_chgs[name] = chg
            else:
                _f_data.append(["[?]", name, "N/A", "---", ""])
        except Exception:
            _f_data.append(["[?]", name, "ERR", "---", ""])

    if vix_val > 30:   vol_lbl = "EXTREME FEAR"
    elif vix_val > 25: vol_lbl = "HIGH FEAR"
    elif vix_val > 20: vol_lbl = "ELEVATED"
    else:              vol_lbl = "CALM"
    sent_label = "BULLISH" if sentiment_score > 10 else "BEARISH" if sentiment_score < -10 else "NEUTRAL"
    risk_label = "RISK-ON" if sentiment_score > 5  else "RISK-OFF" if sentiment_score < -5 else "MIXED"

    if _f_data:
        # Render arrow column outside <pre> to avoid emoji width issues
        _f_pre_data  = [r[:4] for r in _f_data]
        _f_pre_hdrs  = _f_hdrs[:4]
        _f_pre_RIGHT = {2, 3}
        _fw  = [max(len(_f_pre_hdrs[i]), max(len(r[i]) for r in _f_pre_data)) for i in range(4)]
        _fj  = lambda i, v: v.rjust(_fw[i]) if i in _f_pre_RIGHT else v.ljust(_fw[i])
        _fsep = "-+-".join("-" * w for w in _fw)
        _flines = [" | ".join(_fj(i, _f_pre_hdrs[i]) for i in range(4)), _fsep]
        for r in _f_pre_data:
            _flines.append(" | ".join(_fj(i, r[i]) for i in range(4)))
        _flines.append(f"Sent:{sent_label[:4]}  {risk_label[:4]}  VIX:{vol_lbl[:4]}")
        parts.append("<pre>" + "\n".join(_flines) + "</pre>")

    # ── 2. OI Signal Summary ───────────────────────────────────────
    conn = get_conn()
    bull_tickers, bear_tickers, unusual_tickers = [], [], []
    bull_rows_data, bear_rows_data = [], []
    latest_date = "?"
    try:
        lr = pd.read_sql("""
            SELECT DISTINCT trade_date_now FROM options_change
            ORDER BY substr(trade_date_now,7,4)||substr(trade_date_now,1,2)||substr(trade_date_now,4,2) DESC LIMIT 1
        """, conn)
        if not lr.empty:
            latest_date = lr["trade_date_now"].iloc[0]
            sig_df = pd.read_sql("""
                SELECT ticker,
                       SUM(change_OI_Call)   AS call_chg,
                       SUM(change_OI_Put)    AS put_chg,
                       SUM(openInt_Call_now) AS call_oi,
                       SUM(openInt_Put_now)  AS put_oi
                FROM options_change WHERE trade_date_now=?
                GROUP BY ticker
                HAVING (ABS(SUM(change_OI_Call)) + ABS(SUM(change_OI_Put))) > 100
                ORDER BY (ABS(SUM(change_OI_Call)) + ABS(SUM(change_OI_Put))) DESC
                LIMIT 30
            """, conn, params=(latest_date,))
            for _, r in sig_df.iterrows():
                tk   = str(r["ticker"])
                c    = float(r["call_chg"] or 0)
                p    = float(r["put_chg"]  or 0)
                c_oi = float(r["call_oi"]  or 1)
                p_oi = float(r["put_oi"]   or 0)
                pcr  = p_oi / c_oi if c_oi > 0 else 0
                _sig, _ = _oi_signal_light(c, p, pcr)
                if _sig == "BULLISH":
                    bull_tickers.append(tk);    bull_rows_data.append((tk, c, p, pcr))
                elif _sig in ("BEARISH", "MILD BEAR"):
                    bear_tickers.append(tk);    bear_rows_data.append((tk, c, p, pcr))
                elif _sig == "HEDGE":
                    unusual_tickers.append(f"{tk}[H]")   # hedge not directional
                elif _sig in ("STRADDLE", "BULL+HEDGE"):
                    unusual_tickers.append(tk)
    except Exception as ex:
        log.warning(f"market_analytics OI query failed: {ex}")
    conn.close()

    # OI signals — single <pre> table
    _oi_hdr  = ["Sig", "Ticker", "Call OI", "Put OI", "PCR"]
    _oi_RGHT = {2, 3, 4}
    _oi_rows = []
    for tk, c, p, pcr in bull_rows_data[:5]:
        _oi_rows.append(["[B]", tk[:7], _fmt_k(c), _fmt_k(p), f"{pcr:.2f}"])
    for tk, c, p, pcr in bear_rows_data[:5]:
        _oi_rows.append(["[S]", tk[:7], _fmt_k(c), _fmt_k(p), f"{pcr:.2f}"])
    if unusual_tickers:
        _oi_rows.append(["[?]", ", ".join(unusual_tickers[:3])[:7], "", "", ""])
    if _oi_rows:
        parts.append(f"<b>OI FLOW  {latest_date}</b>\n"
                     + _pipe_table(tuple(_oi_hdr), _oi_rows, right_cols=_oi_RGHT))

    # ── 3. Technical Signals (moondevonyt / Harvard RBI) ──────────
    try:
        import pandas_ta as pta
        _scan = ["SPY", "QQQ", "IWM", "NVDA", "AAPL", "TSLA", "AMZN", "META"]
        _t_hdr  = ["Sym ", "Sig ", "Sc ", "RSI", "MACD", "BB ", "EMA%  "]
        _t_RGHT = {2, 3, 6}
        _t_data = []
        for sym in _scan:
            try:
                _h = yf.Ticker(sym).history(period="60d", interval="1d")
                if len(_h) < 26: continue
                _cl    = _h["Close"]
                px_now = float(_cl.iloc[-1])
                rsi_s  = pta.rsi(_cl, length=14)
                rsi_v  = float(rsi_s.iloc[-1]) if rsi_s is not None and not rsi_s.empty else 50.0
                macd_df = pta.macd(_cl, fast=12, slow=26, signal=9)
                cd = "BUY" if (macd_df is not None and not macd_df.empty and
                               float(macd_df.iloc[-1,0]) > float(macd_df.iloc[-1,1])) else "SELL"
                bb_pos = "MID"
                bb_df  = pta.bbands(_cl, length=20, std=2)
                if bb_df is not None and not bb_df.empty:
                    if px_now >= float(bb_df.iloc[-1,0])*0.995:   bb_pos = "TOP"
                    elif px_now <= float(bb_df.iloc[-1,2])*1.005: bb_pos = "BOT"
                ema_s   = pta.ema(_cl, length=20)
                ema_v   = float(ema_s.iloc[-1]) if ema_s is not None and not ema_s.empty else px_now
                ema_pct = (px_now - ema_v) / ema_v * 100
                bull_pts = sum([rsi_v < 70, rsi_v > 50, cd == "BUY", bb_pos != "TOP", ema_pct > 0])
                sig = "BULL" if bull_pts >= 4 else ("BEAR" if bull_pts <= 1 else "NEUT")
                rsi_flag = "OB" if rsi_v > 70 else ("OS" if rsi_v < 30 else "  ")
                _t_data.append([sym, sig, f"{bull_pts}/5", f"{rsi_v:.0f}", f"{cd}{'+'if cd=='BUY' else'-'}",
                                 bb_pos, f"{ema_pct:+.1f}%"])
            except Exception:
                continue
        if _t_data:
            parts.append("<b>TECH SIGNALS  RSI/MACD/BB/EMA</b>\n"
                         + _pipe_table(tuple(_t_hdr), _t_data, right_cols=_t_RGHT))
    except Exception as te_ex:
        log.warning(f"market_analytics tech signals failed: {te_ex}")

    # ── 3b. Market Regime (VIX Term Structure · Sector Rotation · Fear/Greed) ─
    try:
        _regime = ["<b>🎯 MARKET REGIME</b>"]

        # VIX Term Structure: spot vs 3-month
        try:
            _vix_s = float(yf.Ticker("^VIX").history(period="5d")["Close"].iloc[-1])
            _vx3m  = float(yf.Ticker("^VIX3M").history(period="5d")["Close"].iloc[-1])
            _vt_r  = _vix_s / _vx3m
            _vt_lb = "BACKWDTN ⚠️" if _vt_r > 1.05 else ("CONTANGO ✓" if _vt_r < 0.95 else "FLAT")
            _regime.append(f"🌡 <b>VIX Term:</b>  {_vix_s:.1f} / {_vx3m:.1f}  ({_vt_r:.2f}x)  {_vt_lb}")
        except Exception:
            pass

        # Sector Rotation: 5d leaders vs laggards
        try:
            _secs = [("XLK","Tech"),("XLF","Finl"),("XLE","Engy"),("XLV","Hlth"),
                     ("XLI","Inds"),("XLC","Comm"),("XLU","Util")]
            _sp = []
            for _sym, _lbl in _secs:
                _sh = yf.Ticker(_sym).history(period="7d")
                if len(_sh) >= 2:
                    _p = (float(_sh["Close"].iloc[-1]) - float(_sh["Close"].iloc[-2])) / float(_sh["Close"].iloc[-2]) * 100
                    _sp.append((_lbl, _p))
            if _sp:
                _sp.sort(key=lambda x: x[1], reverse=True)
                _top = " · ".join(f"{l} {p:+.1f}%" for l, p in _sp[:2])
                _bot = " · ".join(f"{l} {p:+.1f}%" for l, p in _sp[-2:])
                _regime.append(f"🏆 <b>Leaders:</b>  {_top}")
                _regime.append(f"⬇ <b>Laggards:</b>  {_bot}")
        except Exception:
            pass

        # Fear & Greed Composite (0-100)
        try:
            _fg = 50
            # VIX: VIX=10→+25pts, VIX=20→neutral, VIX=30→-25pts
            if vix_val > 0:
                _fg += max(-25, min(25, int((20 - vix_val) * 2.5)))
            # Market momentum (ES sentiment_score proxy)
            _fg += max(-20, min(20, int(sentiment_score)))
            _fg = max(0, min(100, _fg))
            if _fg >= 75:   _fg_lb = "EXTREME GREED 🤑"
            elif _fg >= 55: _fg_lb = "GREED 😀"
            elif _fg >= 45: _fg_lb = "NEUTRAL 😐"
            elif _fg >= 25: _fg_lb = "FEAR 😨"
            else:           _fg_lb = "EXTREME FEAR 😱"
            _bar = "█" * (_fg // 10) + "░" * (10 - _fg // 10)
            _regime.append(f"😱 <b>Fear/Greed:</b>  {_fg}/100  {_fg_lb}")
            _regime.append(f"   <code>{_bar}</code>")
        except Exception:
            pass

        # Market Breadth: SPY vs IWM vs QQQ 5d momentum
        try:
            _breadth = []
            for _bs, _bn in [("SPY","SPY"),("QQQ","QQQ"),("IWM","IWM"),("MDY","MDY")]:
                _bh = yf.Ticker(_bs).history(period="7d")
                if len(_bh) >= 2:
                    _bp = (float(_bh["Close"].iloc[-1]) - float(_bh["Close"].iloc[-2])) / float(_bh["Close"].iloc[-2]) * 100
                    _be = "🟢" if _bp > 0 else "🔴"
                    _breadth.append(f"{_bn} {_bp:+.1f}%")
            if _breadth:
                _regime.append(f"📊 <b>Breadth:</b>  {' · '.join(_breadth)}")
        except Exception:
            pass

        if len(_regime) > 1:
            parts.append("\n".join(_regime))
    except Exception as _re_ex:
        log.warning(f"market_analytics regime signals failed: {_re_ex}")

    # ── 4. Market News ─────────────────────────────────────────────
    try:
        import feedparser, html as html_mod
        _neg = ["drop","fall","crash","sell","bear","down","loss","cut","tariff","fear",
                "decline","recession","warn","slump","plunge","sink","tumble"]
        _pos = ["rise","gain","rally","bull","up","beat","surge","strong","record",
                "high","boost","upgrade","growth","jump","soar"]
        news_items = []
        for feed_sym in ["SPY", "^VIX", "^TNX", "AAPL", "NVDA"]:
            try:
                feed = feedparser.parse(
                    f"https://feeds.finance.yahoo.com/rss/2.0/headline?s={feed_sym}&region=US&lang=en-US")
                for entry in feed.entries[:4]:
                    title = html_mod.unescape(entry.get("title", "")).strip()
                    link  = entry.get("link", "")
                    if not title or len(title) < 15: continue
                    tl   = title.lower()
                    tone = "Bear" if any(k in tl for k in _neg) else ("Bull" if any(k in tl for k in _pos) else "Neut")
                    if not any(title[:55] == x[1][:55] for x in news_items):
                        news_items.append((tone, title, link))
                    if len(news_items) >= 10: break
            except Exception: continue
            if len(news_items) >= 10: break

        if news_items:
            tone_em = {"Bull": "🟢", "Bear": "🔴", "Neut": "🟡"}
            n_lines = [f"<b>HEADLINES  {now_et.strftime('%m-%d')}</b>"]
            for tone, title, link in news_items[:7]:
                em2   = tone_em.get(tone, "🟡")
                short = html_mod.escape(title[:65] + ("…" if len(title) > 65 else ""))
                line  = f'{em2} <a href="{link}">{short}</a>' if link else f"{em2} {short}"
                n_lines.append(line)
            parts.append("\n".join(n_lines))
    except Exception as news_ex:
        log.warning(f"market_analytics news fetch failed: {news_ex}")

    # ── 5. Macro Cross-Asset Correlations & Trade Ideas ────────────
    try:
        macro_lines = ["<b>MACRO CORRELATIONS &amp; TRADE IDEAS</b>"]
        oil_chg  = _f_chgs.get("Oil",    0.0)
        gold_chg = _f_chgs.get("Gold",   0.0)
        vix_chg  = _f_chgs.get("VIX",    0.0)
        tnx_chg  = _f_chgs.get("10Y",    0.0)
        dxy_chg  = _f_chgs.get("EUR/USD",0.0) * -1   # EUR/USD inverse of DXY
        btc_chg  = _f_chgs.get("BTC",    0.0)

        macro_ideas = []   # (emoji, trade idea, rationale)

        # Oil relationships
        if oil_chg > 1.5:
            macro_ideas.append(("🟢", "LONG XLE / CVX / XOM", f"Oil +{oil_chg:.1f}% → Energy stocks benefit directly"))
            macro_ideas.append(("🔴", "SHORT DAL / UAL / AAL", f"Oil +{oil_chg:.1f}% → Airline fuel costs spike"))
            macro_ideas.append(("🔴", "CAUTION on XRT / AMZN", f"Higher oil → consumer spending pressure"))
        elif oil_chg < -1.5:
            macro_ideas.append(("🟢", "LONG DAL / UAL / LUV", f"Oil {oil_chg:.1f}% → Airline margins expand"))
            macro_ideas.append(("🟢", "LONG XLY / AMZN / TGT", f"Lower fuel → consumer discretionary benefits"))
            macro_ideas.append(("🔴", "SHORT XLE / CVX", f"Oil {oil_chg:.1f}% → Energy earnings pressure"))

        # Gold & DXY relationships
        if gold_chg > 1.0:
            macro_ideas.append(("🟢", "LONG GDX / NEM / AEM", f"Gold +{gold_chg:.1f}% → Gold miners leveraged to price"))
            macro_ideas.append(("🔴", "DXY likely WEAK — watch EEM/FXI", f"Gold up = USD down = EM outperform"))
        elif gold_chg < -1.0:
            macro_ideas.append(("🟢", "DXY STRONG — LONG UUP", f"Gold {gold_chg:.1f}% → Dollar strengthening"))
            macro_ideas.append(("🔴", "SHORT GDX miners", f"Gold {gold_chg:.1f}% → Miner earnings compress"))

        # 10Y yield relationships
        if tnx_chg > 0.5:
            macro_ideas.append(("🟢", "LONG KBE / JPM / BAC", f"10Y +{tnx_chg:.1f}% → Bank NIMs expand, steepening curve"))
            macro_ideas.append(("🔴", "CAUTION on XLU / XLRE", f"Rising rates → utilities & REITs compress"))
            macro_ideas.append(("🔴", "CAUTION on long-duration tech (ARKK)", f"Higher discount rate = lower NPV on growth"))
        elif tnx_chg < -0.5:
            macro_ideas.append(("🟢", "LONG XLU / VNQ / TLT", f"10Y {tnx_chg:.1f}% → Rate-sensitive sectors rally"))
            macro_ideas.append(("🟢", "LONG ARKK / PLTR / growth", f"Lower rates = multiple expansion for growth"))

        # VIX relationships
        if vix_chg > 5 or vix_val > 25:
            macro_ideas.append(("🔴", "Reduce naked short premium", f"VIX elevated — hedge with calls or close short puts"))
            macro_ideas.append(("🟢", "LONG UVXY / VXX hedge", f"VIX spike protection if portfolio is long"))
        elif vix_val < 15:
            macro_ideas.append(("🟢", "Sell premium — low VIX = cheap insurance", f"VIX {vix_val:.1f} → Condors, covered calls attractive"))

        # BTC / Risk-on signal
        if btc_chg > 3.0:
            macro_ideas.append(("🟢", "Risk-ON signal: LONG MSTR / COIN / RIOT", f"BTC +{btc_chg:.1f}% → Crypto proxies outperform"))
        elif btc_chg < -3.0:
            macro_ideas.append(("🔴", "Risk-OFF signal: reduce spec positions", f"BTC {btc_chg:.1f}% → Risk assets under pressure"))

        if macro_ideas:
            # Tabular format with fixed columns
            _mi_hdr  = ["Dir", "Trade Idea", "Rationale"]
            _mi_rows = [(em, idea, note) for em, idea, note in macro_ideas[:6]]
            max_idea = max(len(r[1]) for r in _mi_rows)
            max_note = min(40, max(len(r[2]) for r in _mi_rows))
            for em, idea, note in _mi_rows:
                short_note = note[:40] + "…" if len(note) > 40 else note
                macro_lines.append(f"{em} <b>{idea}</b>\n   <i>{short_note}</i>")
            parts.append("\n".join(macro_lines))
    except Exception as _mc_ex:
        log.warning(f"macro correlations failed: {_mc_ex}")

    # ── 6. Strategy Playbook ───────────────────────────────────────
    plays = []
    if sent_label == "BEARISH":
        plays += ["Puts / bear put spreads on SPY/QQQ", "Sell covered calls on longs"]
    elif sent_label == "BULLISH":
        plays += ["Bull call spreads / buy calls", "Sell CSPs on pullbacks"]
    else:
        plays.append("Iron condors / butterflies — range bound")
    if vix_val > 25:
        plays.append(f"VIX {vix_val:.1f} — premium EXPENSIVE → sell spreads")
    elif vix_val < 15:
        plays.append(f"VIX {vix_val:.1f} — premium CHEAP → buy debit spreads")
    if bull_tickers:
        plays.append(f"Call OI: {', '.join(bull_tickers[:3])} → bull spreads")
    if bear_tickers:
        plays.append(f"Put OI:  {', '.join(bear_tickers[:3])} → bear spreads")

    p_lines = [f"<b>STRATEGY PLAYBOOK</b>"]
    play_em = {"BULLISH": "🟢", "BEARISH": "🔴", "NEUTRAL": "🟡"}
    em_p = play_em.get(sent_label, "🟡")
    for p in plays[:5]:
        p_lines.append(f"{em_p} {p}")
    parts.append("\n".join(p_lines))

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Refresh", callback_data="menu_analytics"),
         InlineKeyboardButton("🔥 OI Signals", callback_data="menu_signals")],
        [InlineKeyboardButton("🌍 Market", callback_data="menu_market"), BACK_BTN],
    ])
    _pulled_ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    parts.append(f"<i>🕐 Data pulled at: {_pulled_ts}</i>")
    try: await _loading.delete()
    except Exception: pass
    await _safe_reply(query.message, "\n".join(parts), reply_markup=kb)


async def position_monitor(ctx: ContextTypes.DEFAULT_TYPE):
    """10-min position table — ALL open positions every cycle during market hours."""
    now_utc  = datetime.now(timezone.utc).replace(tzinfo=None)
    if now_utc.weekday() >= 5:
        return
    hour_min = now_utc.hour * 60 + now_utc.minute
    if not (14 * 60 + 30 <= hour_min <= 21 * 60):   # 9:30 AM – 4:00 PM ET
        return

    _close_expired_positions()   # auto-close anything past expiry before showing
    _, chat_id = load_creds()
    conn = get_conn()
    try:
        trades = pd.read_sql(
            "SELECT * FROM trades WHERE status='OPEN' ORDER BY trade_id", conn)
    except Exception:
        conn.close(); return
    conn.close()

    if trades.empty:
        return

    now_et = now_utc - timedelta(hours=5)
    today  = now_et.date()
    now_s  = now_et.strftime("%H:%M ET")

    # ── Prefetch OI signals for all tickers ──────────────────────
    oi_sigs = {}
    try:
        _oi_conn = get_conn()
        _lr = pd.read_sql("""
            SELECT DISTINCT trade_date_now FROM options_change
            ORDER BY substr(trade_date_now,7,4)||substr(trade_date_now,1,2)||substr(trade_date_now,4,2) DESC LIMIT 1
        """, _oi_conn)
        if not _lr.empty:
            _ltd = _lr["trade_date_now"].iloc[0]
            for _sym in trades["ticker"].str.upper().unique():
                _oi = pd.read_sql("""
                    SELECT SUM(change_OI_Call) as cc, SUM(change_OI_Put) as pc
                    FROM options_change WHERE ticker=? AND trade_date_now=?
                """, _oi_conn, params=(_sym, _ltd))
                if not _oi.empty:
                    _cc = float(_oi["cc"].iloc[0] or 0)
                    _pc = float(_oi["pc"].iloc[0] or 0)
                    oi_sigs[_sym] = "BUL" if _cc > abs(_pc)*1.2 else ("BEA" if _pc > abs(_cc)*1.2 else "NEU")
        _oi_conn.close()
    except Exception:
        pass

    # ── Per-position data collection ─────────────────────────────
    rows       = []
    total_pnl  = 0.0
    urgent_lines = []

    for _, tr in trades.iterrows():
        tid      = int(tr.get("trade_id", 0))
        tk       = str(tr.get("ticker", "?")).upper()
        otype    = str(tr.get("option_type", "call")).upper()
        strike   = _safe_float(tr.get("strike", 0), 0)
        entry    = _safe_float(tr.get("entry_price", 0), 0)
        qty      = _safe_int(tr.get("quantity", 1), 1)
        expiry_s = str(tr.get("expiry", ""))

        # DTE
        dte = None
        try:
            dte = (datetime.strptime(expiry_s[:10], "%Y-%m-%d").date() - today).days
        except Exception:
            try:
                dte = (datetime.strptime(expiry_s[:10], "%m-%d-%Y").date() - today).days
            except Exception:
                pass

        # Live option price + delta (probability) from chain
        cur_px = entry
        prob   = None
        stock_px = None
        try:
            _tkr = yf.Ticker(tk)
            _sh  = _tkr.history(period="1d", interval="5m")
            if not _sh.empty:
                stock_px = float(_sh["Close"].iloc[-1])
            # normalise expiry to YYYY-MM-DD for yfinance
            try:
                _exp_yf = datetime.strptime(expiry_s[:10], "%Y-%m-%d").strftime("%Y-%m-%d")
            except Exception:
                try:
                    _exp_yf = datetime.strptime(expiry_s[:10], "%m-%d-%Y").strftime("%Y-%m-%d")
                except Exception:
                    _exp_yf = None
            if _exp_yf:
                _chain = _tkr.option_chain(_exp_yf)
                _df    = _chain.calls if otype == "CALL" else _chain.puts
                _near  = _df[abs(_df["strike"] - strike) < 0.01]
                if not _near.empty:
                    _lp = _near["lastPrice"].iloc[0]
                    if _lp and float(_lp) > 0:
                        cur_px = float(_lp)
                    if "delta" in _near.columns:
                        _dv = _near["delta"].iloc[0]
                        if _dv is not None and not pd.isna(_dv):
                            prob = abs(float(_dv)) * 100
        except Exception:
            pass

        # Rough moneyness-based probability if delta unavailable
        if prob is None and stock_px and strike:
            mono_pct = (stock_px - strike) / strike * 100
            if otype == "CALL":
                prob = max(5, min(95, 50 + mono_pct * 2.5))
            else:
                prob = max(5, min(95, 50 - mono_pct * 2.5))

        pnl     = (cur_px - entry) * qty * 100
        pnl_pct = (pnl / abs(entry * qty * 100) * 100) if entry > 0 else 0
        total_pnl += pnl

        oi_s = oi_sigs.get(tk, "?")
        oi_align = not ((otype == "CALL" and oi_s == "BEA") or (otype == "PUT" and oi_s == "BUL"))

        # ── Action logic ──────────────────────────────────────────
        if dte is not None and dte <= 2:
            action = "EXIT NOW"
            em     = "🚨"
            urgent_lines.append(f"🚨 #{tid} {tk} {otype[:1]} ${strike:.0f} — {dte}d left, exit immediately")
        elif pnl_pct <= -50:
            action = "CUT LOSS"
            em     = "🔴"
            urgent_lines.append(f"🔴 #{tid} {tk} {otype[:1]} ${strike:.0f} — down {pnl_pct:.0f}%, cut loss")
        elif pnl_pct <= -40:
            action = "CUT LOSS"
            em     = "🔴"
        elif pnl_pct >= 70:
            action = "TAKE PROFIT"
            em     = "🟢"
            urgent_lines.append(f"🟢 #{tid} {tk} {otype[:1]} ${strike:.0f} — up {pnl_pct:.0f}%, take profit")
        elif pnl_pct >= 50:
            action = "TAKE PROFIT"
            em     = "🟢"
        elif dte is not None and dte <= 5:
            action = "ROLL/EXIT" if pnl_pct >= 0 else "CUT"
            em     = "⚠️"
        elif dte is not None and dte <= 10:
            action = "ROLL SOON" if pnl_pct >= 0 else "REVIEW"
            em     = "🟡"
        elif not oi_align:
            action = "REVIEW"
            em     = "🟡"
        elif pnl_pct > 15:
            action = "HOLD"
            em     = "🟢"
        elif pnl_pct < -15:
            action = "HOLD"
            em     = "🔴"
        else:
            action = "HOLD"
            em     = "🟡"

        dte_s  = f"D{dte}"  if dte  is not None else "D?"
        prob_s = f"{prob:.0f}%" if prob is not None else "?"
        rows.append((em, tk, otype[:4], strike, entry, cur_px, pnl_pct, pnl, dte_s, prob_s, oi_s, action))

    # ── Action emoji map (replaces single-char badge + legend) ──────
    _action_em = {
        "EXIT NOW":    "🚨",
        "CUT LOSS":    "✂️",
        "TAKE PROFIT": "💰",
        "ROLL/EXIT":   "🔄",
        "ROLL SOON":   "🔄",
        "REVIEW":      "👁",
        "HOLD":        "✅",
    }
    # Plain-English action advice (no jargon)
    _action_advice = {
        "EXIT NOW":    "Exit immediately — this position is at critical risk.",
        "CUT LOSS":    "Close this trade. The loss is large enough to cut now rather than risk more.",
        "TAKE PROFIT": "Lock in your profit — sell to close and bank the gain.",
        "ROLL/EXIT":   "Expiry is very close. Either close it now or move to a later date.",
        "ROLL SOON":   "Expiry approaching in ~1 week. Plan to roll or close before it decays further.",
        "REVIEW":      "OI flow is not supporting this trade. Review and decide whether to hold or exit.",
        "HOLD":        "Trade is on track. Keep holding and monitor.",
    }

    # ── Pipe-table layout ─────────────────────────────────────────────
    # Row data lists for both tables
    _tbl1_rows = []  # #|Tk|Type|Strike|DTE|Entry|Now
    _tbl2_rows = []  # #|PnL$|P%|Win|OI|Action
    html_cards  = []  # per-position advice cards (kept below tables)

    for idx, (em, tk, otype, strike, entry, cur_px, pnl_pct, pnl, dte_s, prob_s, oi_s, action) in enumerate(rows, 1):
        buy_s = f"{entry:.2f}"  if entry  < 100 else f"{entry:.0f}"
        cur_s = f"{cur_px:.2f}" if cur_px < 100 else f"{cur_px:.0f}"
        pct_s = f"{pnl_pct:+.1f}%"
        pnl_s = f"${pnl:+,.0f}"
        a_em  = _action_em.get(action, "✅")
        advice = _action_advice.get(action, "Monitor position.")

        dte_num = int(dte_s[1:]) if dte_s.startswith("D") and dte_s[1:].isdigit() else None
        dte_disp = f"{dte_num}d" if dte_num is not None else dte_s
        urg_flag = "⚠" if dte_num is not None and dte_num <= 3 else ""

        oi_disp = oi_s if oi_s and oi_s != "?" else "-"
        act_disp = f"{a_em}{action[:8]}"

        _tbl1_rows.append((str(idx), tk[:5], otype[:1], f"{int(strike)}", f"{dte_disp}{urg_flag}", buy_s, cur_s))
        _tbl2_rows.append((str(idx), pnl_s, pct_s, prob_s[:6], oi_disp[:8], act_disp[:12]))

        # Advice card — kept for context below tables
        html_cards.append(
            f"{em} <b>{tk} {otype} ${int(strike)}</b>"
            f"  {a_em} <b>{action}</b> — {advice}"
        )

    t1_hdr = ("#", "Ticker", "T", "Strike", "DTE", "Entry", "Now")
    t2_hdr = ("#", "PnL$", "P%", "Win", "OI", "Action")
    table1 = _pipe_table(t1_hdr, _tbl1_rows) if _tbl1_rows else ""
    table2 = _pipe_table(t2_hdr, _tbl2_rows) if _tbl2_rows else ""

    advice_section = "\n".join(html_cards)
    colour_section = f"{table1}\n{table2}\n\n{advice_section}"

    urgent_section = ""
    if urgent_lines:
        urgent_section = "\n\n<b>⚡ ACTION REQUIRED</b>\n" + "\n".join(urgent_lines)

    net_em  = "🟢" if total_pnl >= 0 else "🔴"
    n_pos   = len(html_cards)
    footer  = f"\n{net_em} <b>Portfolio total: ${total_pnl:+,.0f}</b>  ({n_pos} open position{'s' if n_pos != 1 else ''})"

    # ── High-Prob Engine — one card per ticker ─────────────────────────
    hp_section = ""
    _SIG_ICON = {"BULL":"🟢","BEAR":"🔴","SELL_PREMIUM":"💰","NEUTRAL":"⚪"}
    try:
        conn_hp_pm = get_conn()
        _hp_cards = []
        for _htk_pm in trades["ticker"].str.upper().unique().tolist()[:5]:
            try:
                _sp_pm = _get_spot_with_ah(_htk_pm).get("spot_ext", 0.0)
                _hr_pm = high_prob_signals_engine(_htk_pm, conn_hp_pm, _sp_pm)
                _ic_pm = _SIG_ICON.get(_hr_pm["signal"], "⚪")
                _cf_pm = _hr_pm.get("confidence","")
                _pb_pm = _hr_pm["prob"]
                _bv   = _hr_pm["bull_v"]; _rv = _hr_pm["bear_v"]
                _sv   = _hr_pm.get("sell_v", 0)
                # compact card ≤28 chars per line
                _card = [
                    f"<b>{_htk_pm}</b> ${_sp_pm:.0f}",
                    f"{_ic_pm} {_hr_pm['signal']}  {_pb_pm:.0f}%  {_cf_pm}",
                    f"🟢{_bv} 🔴{_rv} 💰{_sv}/24",
                ]
                _vb = _hr_pm.get("vrvp_box", {})
                if _vb.get("lo"):
                    _card.append(f"📦 ${_vb['lo']:.0f}–${_vb['hi']:.0f} POC${_vb.get('poc',0):.0f}")
                _wl = _hr_pm.get("models", {}).get("put_call_wall", {})
                if _wl.get("call_wall") and _wl.get("prob", 0) >= 65:
                    _card.append(f"🧱 P${_wl['put_wall']:.0f} C${_wl['call_wall']:.0f}")
                _hp_cards.append("\n".join(_card))
            except Exception as _e_pm:
                log.debug(f"pos_mon hp {_htk_pm}: {_e_pm}")
        if _hp_cards:
            hp_section = "\n\n<b>🧠 HP Engine</b>\n" + "\n\n".join(_hp_cards)
        conn_hp_pm.close()
    except Exception as _e_hp_pm:
        log.debug(f"pos_mon hp block: {_e_hp_pm}")

    full_msg = (
        f"{hdr(f'💼 POSITIONS · {now_s}')}\n\n"
        + colour_section
        + urgent_section
        + hp_section
        + footer
    )

    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("💼 Positions", callback_data="menu_positions"),
        InlineKeyboardButton("🎯 Exit Plan", callback_data="menu_exit"),
        InlineKeyboardButton("🧠 HP Engine", callback_data=f"high_prob_{_first_tk}"),
    ]])
    try:
        if len(full_msg) <= 4000:
            await ctx.bot.send_message(chat_id=int(chat_id), text=full_msg,
                                       parse_mode=H, reply_markup=kb)
        else:
            # Split: header + cards, then urgent + hp + footer
            header_cards = f"{hdr(f'💼 POSITIONS · {now_s}')}\n\n{colour_section}"
            await ctx.bot.send_message(chat_id=int(chat_id), text=header_cards, parse_mode=H)
            await ctx.bot.send_message(chat_id=int(chat_id),
                                       text=urgent_section + hp_section + footer,
                                       parse_mode=H, reply_markup=kb)
    except Exception as e:
        log.warning(f"position_monitor send failed: {e}")


def _ensure_alert_dedup_table(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS alert_dedup (
            alert_date TEXT NOT NULL,
            grp_key    TEXT NOT NULL,
            atype      TEXT NOT NULL,
            PRIMARY KEY (alert_date, grp_key, atype)
        )
    """)
    conn.commit()


def _alert_already_sent(conn, today_str, grp_key, atype):
    """Return True if alert was already sent today; insert the key if not."""
    try:
        conn.execute(
            "INSERT INTO alert_dedup (alert_date, grp_key, atype) VALUES (?, ?, ?)",
            (today_str, grp_key, atype)
        )
        conn.commit()
        return False
    except sqlite3.IntegrityError:
        return True


async def position_alerts(ctx: ContextTypes.DEFAULT_TYPE):
    """Smart alert job — fires ONLY when a trigger condition is hit.
    Runs every 5 min during market hours. Deduplicates via SQLite so
    each alert fires at most once per calendar day (survives restarts)."""
    now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
    if now_utc.weekday() >= 5:
        return
    hour_min = now_utc.hour * 60 + now_utc.minute
    if not (14 * 60 + 30 <= hour_min <= 21 * 60):
        return
    _, chat_id = load_creds()
    conn = get_conn()
    try:
        _ensure_alert_dedup_table(conn)
        trades = pd.read_sql("SELECT * FROM trades WHERE status='OPEN'", conn)
    except Exception:
        conn.close(); return
    if trades.empty:
        conn.close()
        return

    now_et   = now_utc - timedelta(hours=5)
    today    = now_et.date()
    today_str = today.isoformat()

    # Group legs by ticker + expiry — same group = one strategy
    trades["_grp"] = trades["ticker"].str.upper() + "|" + trades["expiry"].astype(str).str[:10]
    alert_msgs = []

    for grp_key, grp in trades.groupby("_grp"):
        tk       = grp_key.split("|")[0]
        expiry_s = grp_key.split("|")[1]

        dte = None
        try: dte = (datetime.strptime(expiry_s, "%Y-%m-%d").date() - today).days
        except Exception:
            try: dte = (datetime.strptime(expiry_s, "%m-%d-%Y").date() - today).days
            except Exception: pass
        if dte is not None and dte < 0:
            continue  # skip expired

        stock_px = None
        try:
            _sh = yf.Ticker(tk).history(period="1d", interval="5m")
            if not _sh.empty:
                stock_px = float(_sh["Close"].iloc[-1])
        except Exception:
            pass

        net_pnl  = 0.0
        leg_lines = []
        for _, tr in grp.iterrows():
            otype  = str(tr.get("option_type", "call")).upper()
            strike = _safe_float(tr.get("strike", 0), 0)
            entry  = _safe_float(tr.get("entry_price", 0), 0)
            qty    = _safe_int(tr.get("quantity", 1), 1)
            cur_px = entry
            try:
                try:    _exp_yf = datetime.strptime(expiry_s, "%Y-%m-%d").strftime("%Y-%m-%d")
                except: _exp_yf = datetime.strptime(expiry_s, "%m-%d-%Y").strftime("%Y-%m-%d")
                _chain = yf.Ticker(tk).option_chain(_exp_yf)
                _df    = _chain.calls if otype == "CALL" else _chain.puts
                _near  = _df[abs(_df["strike"] - strike) < 0.01]
                if not _near.empty and float(_near["lastPrice"].iloc[0]) > 0:
                    cur_px = float(_near["lastPrice"].iloc[0])
            except Exception:
                pass
            leg_pnl  = (cur_px - entry) * qty * 100
            net_pnl += leg_pnl
            _dir = "SELL" if qty < 0 else "BUY"
            leg_lines.append(
                f"  {_dir} {otype[:1]} ${strike:.0f}"
                f" @${entry:.2f} now ${cur_px:.2f}"
                f" P&L ${leg_pnl:+.0f}"
            )

        entry_cost  = sum(
            abs(_safe_float(tr.get("entry_price", 0), 0)
                * _safe_int(tr.get("quantity", 1), 1) * 100)
            for _, tr in grp.iterrows()
        )
        net_pnl_pct = (net_pnl / entry_cost * 100) if entry_cost > 0 else 0
        n_legs      = len(grp)
        strat_label = f"{n_legs}-leg strategy" if n_legs > 1 else "single leg"
        legs_text   = "\n".join(leg_lines)
        dte_s       = str(dte) if dte is not None else "?"
        spot_line   = f"Spot: ${stock_px:.2f}" if stock_px else ""

        def _alert(atype, icon, headline, detail,
                   _gk=grp_key, _tk=tk, _es=expiry_s, _ds=dte_s, _sl=strat_label,
                   _sp=spot_line, _lt=legs_text, _nl=net_pnl, _np=net_pnl_pct):
            if _alert_already_sent(conn, today_str, _gk, atype):
                return
            alert_msgs.append(
                f"{icon} <b>ALERT - {_tk}</b> | {_sl}\n"
                f"Expiry: {_es} | DTE: {_ds} | {_sp}\n"
                f"<b>{headline}</b>\n"
                f"{detail}\n"
                f"{_lt}\n"
                f"Net P&L: <b>${_nl:+.0f} ({_np:+.1f}%)</b>"
            )

        if net_pnl_pct >= 50:
            _alert("profit50", "💰", "50% PROFIT TARGET HIT",
                   "Consider closing - lock in gains.")
        if net_pnl_pct >= 70:
            _alert("profit70", "🏆", "70% PROFIT - STRONG CLOSE SIGNAL",
                   "High conviction close now.")
        if net_pnl_pct <= -100:
            _alert("loss2x", "🚨", "2x LOSS - STOP TRIGGERED",
                   "Close now to protect capital.")
        if dte is not None and 3 <= dte <= 7:
            _alert("dte7", "⏰", f"FINAL WEEK - {dte}d TO EXPIRY",
                   "Close or roll - theta decay accelerates in final week.")
        if dte is not None and dte <= 2:
            _alert("dte2", "🚨", f"EXPIRES IN {dte} DAY(S) - ACT NOW",
                   "Expiry and assignment risk imminent.")
        if stock_px:
            for _, tr in grp.iterrows():
                if _safe_int(tr.get("quantity", 1), 1) < 0:
                    _ss = _safe_float(tr.get("strike", 0), 0)
                    if _ss > 0 and abs(stock_px - _ss) / stock_px * 100 < 3.0:
                        _alert(
                            f"near_{_ss}", "⚠️",
                            f"SPOT WITHIN 3% OF SHORT STRIKE ${_ss:.0f}",
                            f"Stock ${stock_px:.2f} - your short strike is at risk."
                        )

        # Short-squeeze risk — only for legs hurt by a rally (short calls / long puts)
        has_bearish = any(
            (str(tr.get("option_type", "")).upper().startswith("C") and _safe_int(tr.get("quantity", 1), 1) < 0)
            or (str(tr.get("option_type", "")).upper().startswith("P") and _safe_int(tr.get("quantity", 1), 1) > 0)
            for _, tr in grp.iterrows()
        )
        if has_bearish:
            try:
                _sq = short_squeeze_signal(tk, conn)
            except Exception:
                _sq = None
            if _sq and _sq.get("score", 0) >= 3:
                _det = " | ".join(_sq.get("reasons", [])[:3])
                _alert(
                    "squeeze", "🔥",
                    f"SHORT-SQUEEZE RISK [{_sq['score']}/5] — {_sq['stage']}",
                    f"{tk} {_sq['label']}. Your bearish leg(s) fight a rally — "
                    f"consider trimming/hedging.\n{_det}"
                )

    conn.close()
    for _msg in alert_msgs:
        try:
            await ctx.bot.send_message(chat_id=int(chat_id), text=_msg, parse_mode=H)
        except Exception as _ae:
            log.warning(f"position_alerts send failed: {_ae}")


async def position_monitor_adhoc(query, ctx):
    """On-demand position monitor triggered by button press."""
    await query.answer("Fetching live positions…")
    class _MockCtx:
        bot      = ctx.bot
        bot_data = {}
    try:
        await position_monitor(_MockCtx())
    except Exception as e:
        await query.message.reply_text(f"Position monitor error: {e}", parse_mode=H)



# ═══════════════════════════════════════════════════════════════════
# ── LIVE MOMENTUM SCANNER ENGINE
# ═══════════════════════════════════════════════════════════════════

SCAN_UNIVERSE = [
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


def _live_momentum_scanner(top_n: int = 5):
    """Scan SCAN_UNIVERSE for bull runners and breakdown stocks. Returns (bull_list, bear_list)."""
    import warnings; warnings.filterwarnings("ignore")
    try:
        raw = yf.download(
            tickers=" ".join(SCAN_UNIVERSE),
            period="60d", interval="1d",
            group_by="ticker", auto_adjust=True,
            progress=False, threads=True,
        )
    except Exception as e:
        log.warning(f"momentum scanner download failed: {e}")
        return [], []

    results = []
    for tk in SCAN_UNIVERSE:
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
            results.append(dict(
                ticker=tk, close=close, ret_5d=ret_5d, ret_10d=ret_10d,
                ret_20d=ret_20d, vol_rat=vol_rat, consec_up=consec_up,
                consec_dn=consec_dn, momentum=momentum, atr=max(atr, close * 0.01),
                high_20d=high_20d, low_20d=low_20d, pcr=1.0,
            ))
        except Exception:
            continue

    if not results:
        return [], []
    df = pd.DataFrame(results)
    try:
        conn = get_conn()
        for idx, row in df.iterrows():
            _p = pd.read_sql(
                "SELECT pcr_oi FROM stock_daily WHERE ticker=? ORDER BY substr(trade_date,7,4)||substr(trade_date,1,2)||substr(trade_date,4,2) DESC LIMIT 1",
                conn, params=(row["ticker"],))
            if not _p.empty and _p["pcr_oi"].iloc[0]:
                df.at[idx, "pcr"] = float(_p["pcr_oi"].iloc[0])
        conn.close()
    except Exception:
        pass
    df = df.sort_values("momentum", ascending=False)
    bull = df[df["momentum"] > 15].head(top_n).to_dict("records")
    bear = df[df["momentum"] < -15].tail(top_n).iloc[::-1].to_dict("records")
    return bull, bear


def _signal_strength(rec: dict, direction: str) -> int:
    r5   = abs(rec.get("ret_5d", 0))
    r20  = abs(rec.get("ret_20d", 0))
    vr   = rec.get("vol_rat", 1.0)
    pcr  = rec.get("pcr", 1.0)
    cons = rec.get("consec_up" if direction == "BULL" else "consec_dn", 0)
    s = int(r5 / 2 + r20 / 5 +
            (3 if vr >= 2.5 else 2 if vr >= 2.0 else 1 if vr >= 1.5 else 0) +
            (1 if (direction == "BULL" and pcr < 0.7) or (direction == "BEAR" and pcr > 1.3) else 0) +
            (1 if cons >= 3 else 0))
    return max(1, min(10, s))


def _format_trade_signal(rec: dict, direction: str) -> str:
    """Format scanner pick as portfolio-manager trade signal card (Telegram HTML)."""
    tk    = rec["ticker"]
    close = rec["close"]
    atr   = rec.get("atr", close * 0.02)
    r5    = rec.get("ret_5d", 0)
    r10   = rec.get("ret_10d", 0)
    r20   = rec.get("ret_20d", 0)
    vr    = rec.get("vol_rat", 1.0)
    pcr   = rec.get("pcr", 1.0)
    cons  = rec.get("consec_up" if direction == "BULL" else "consec_dn", 0)
    h20   = rec.get("high_20d", close)
    l20   = rec.get("low_20d", close)
    ss    = _signal_strength(rec, direction)
    stars = "⭐" * min(ss, 5) + ("+" if ss > 5 else "")
    pcr_s = f"PCR {pcr:.2f} {'🟢' if pcr < 0.7 else '🔴' if pcr > 1.3 else '⚪'}"
    if direction == "BULL":
        stop  = round(max(close - atr * 1.8, l20 * 0.99), 2)
        t1    = round(close + atr * 2.0, 2)
        t2    = round(close + atr * 4.5, 2)
        entry = f"${close - atr*0.3:.1f}–${close + atr*0.2:.1f}"
        ph    = round(close * 0.97, 0)
        pl    = round(stop  * 0.97, 0)
        rr    = (t1 - close) / max(close - stop, 0.01)
        em    = "🚀" if ss >= 8 else ("📈" if ss >= 5 else "↗️")
        tag   = "UNSTOPPABLE" if r5 > 15 else ("BULL RUNNER" if r5 > 7 else "BUILDING")
        return "\n".join([
            f"{em} <b>{tk}  ${close:.2f}</b>  ·  <b>{tag}</b>  [{stars}]",
            f"  Momentum · 5d {r5:+.1f}%  10d {r10:+.1f}%  20d {r20:+.1f}%",
            f"  Vol {vr:.1f}×avg · {cons}d consec up · {pcr_s}",
            f"  20d range ${l20:.1f}–${h20:.1f}",
            f"  📥 Entry {entry}",
            f"  🛑 Stop  ${stop:.2f} ({(stop/close-1)*100:.1f}%)  ·  R:R 1:{rr:.1f}",
            f"  🎯 T1 ${t1:.2f} ({(t1/close-1)*100:+.1f}%)  ·  T2 ${t2:.2f} ({(t2/close-1)*100:+.1f}%)",
            f"  🛡 Hedge: Buy ${ph:.0f}p/Sell ${pl:.0f}p put spread (≥21DTE)",
            f"  ⚠️ ≤2% NAV · Trail stop after T1 · Reduce if VIX>25",
        ])
    else:
        stop  = round(min(close + atr * 1.8, h20 * 1.01), 2)
        t1    = round(close - atr * 2.0, 2)
        t2    = round(close - atr * 4.5, 2)
        entry = f"${close - atr*0.2:.1f}–${close + atr*0.3:.1f} (bounce)"
        ch    = round(close * 1.03, 0)
        cl_   = round(stop  * 1.01, 0)
        rr    = (close - t1) / max(stop - close, 0.01)
        em    = "🔥" if ss >= 8 else ("📉" if ss >= 5 else "↘️")
        tag   = "FALLING KNIFE" if r5 < -15 else ("BREAKDOWN" if r5 < -7 else "WEAKENING")
        return "\n".join([
            f"{em} <b>{tk}  ${close:.2f}</b>  ·  <b>{tag}</b>  [{stars}]",
            f"  Momentum · 5d {r5:+.1f}%  10d {r10:+.1f}%  20d {r20:+.1f}%",
            f"  Vol {vr:.1f}×avg · {cons}d consec dn · {pcr_s}",
            f"  20d range ${l20:.1f}–${h20:.1f}",
            f"  📥 Short entry {entry}",
            f"  🛑 Stop  ${stop:.2f} ({(stop/close-1)*100:+.1f}%)  ·  R:R 1:{rr:.1f}",
            f"  🎯 T1 ${t1:.2f} ({(t1/close-1)*100:+.1f}%)  ·  T2 ${t2:.2f} ({(t2/close-1)*100:+.1f}%)",
            f"  🛡 Hedge: Buy ${ch:.0f}c/Sell ${cl_:.0f}c call spread (≥21DTE)",
            f"  ⚠️ Puts ≥21DTE · Avoid expiry pins · Cover on gap-down open",
        ])


async def scanner_menu(query):
    """On-demand live momentum scanner from Telegram button."""
    _loading = await query.message.reply_text("🔍 Scanning 60+ tickers (~30s)...", parse_mode=H)
    try:
        import asyncio as _asyncio
        loop = _asyncio.get_event_loop()
        bull, bear = await loop.run_in_executor(None, lambda: _live_momentum_scanner(top_n=4))
    except Exception as e:
        try: await _loading.delete()
        except Exception: pass
        await query.message.reply_text(f"❌ Scanner error: {e}", parse_mode=H)
        return

    now_et = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=5)
    parts  = [hdr(f"🚀 MOMENTUM SCANNER · {now_et.strftime('%H:%M ET')}")]
    parts.append("<i>Live data · 60+ tickers · yfinance</i>")
    if bull:
        parts.append(f"\n{'━'*28}\n🟢 <b>BULLS / UNSTOPPABLE ({len(bull)})</b>")
        for rec in bull:
            parts.append("\n" + _format_trade_signal(rec, "BULL"))
    else:
        parts.append("\n🟢 No strong bull momentum right now.")
    if bear:
        parts.append(f"\n{'━'*28}\n🔴 <b>BEARS / FALLING ({len(bear)})</b>")
        for rec in bear:
            parts.append("\n" + _format_trade_signal(rec, "BEAR"))
    else:
        parts.append("\n🔴 No strong breakdown signals right now.")
    parts.append("\n<i>⚠️ Algo signals only — verify with OI + news before trading.</i>")
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Refresh", callback_data="menu_scanner"),
         InlineKeyboardButton("📡 MiroFish", callback_data="menu_mirofish")],
        [BACK_BTN],
    ])
    try: await _loading.delete()
    except Exception: pass
    full = "\n".join(parts)
    if len(full) > 4000:
        chunks, cur = [], ""
        for p in parts:
            if len(cur) + len(p) + 1 > 3800:
                chunks.append(cur); cur = p
            else:
                cur += "\n" + p
        if cur: chunks.append(cur)
        for i, chunk in enumerate(chunks):
            _kb = kb if i == len(chunks) - 1 else InlineKeyboardMarkup([])
            await query.message.reply_text(chunk, parse_mode=H, reply_markup=_kb)
    else:
        await query.message.reply_text(full, parse_mode=H, reply_markup=kb)


async def intraday_alert(ctx: ContextTypes.DEFAULT_TYPE):
    """15-min scheduled alert: futures snapshot + OI changes for open position tickers."""
    # Only fire Mon-Fri during US market hours (14:30-21:00 UTC = 9:30 AM - 4:00 PM ET)
    now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
    if now_utc.weekday() >= 5:  # Saturday=5, Sunday=6
        return
    hour_min = now_utc.hour * 60 + now_utc.minute
    if not (14 * 60 + 30 <= hour_min <= 21 * 60):
        return

    _, chat_id = load_creds()
    now_et = now_utc - timedelta(hours=5)
    parts = [hdr(f"⚡ INTRADAY ALERT · {now_et.strftime('%H:%M ET')}")]

    # ── Futures snapshot ──────────────────────────────────────────
    fut_symbols = [("ES", "ES=F"), ("NQ", "NQ=F"), ("VIX", "^VIX"),
                   ("Gold", "GC=F"), ("Oil", "CL=F")]
    _fhdr  = ["ST", "Name", "Price", "Chg%"]
    _frows = []
    for name, sym in fut_symbols:
        try:
            h = yf.Ticker(sym).history(period="1d", interval="5m")
            if len(h) >= 2:
                px  = float(h["Close"].iloc[-1])
                op  = float(h["Open"].iloc[0])
                chg = (px - op) / op * 100
                st  = "[+]" if chg > 0.3 else ("[!]" if chg < -0.3 else "[ ]")
                px_s = f"{px:,.1f}" if px < 10000 else f"{px:,.0f}"
                _frows.append([st, name, px_s, f"{chg:+.2f}%"])
        except Exception:
            _frows.append(["[?]", name, "ERR", "---"])
    if _frows:
        parts.append("<b>FUTURES</b>\n" + _pipe_table(tuple(_fhdr), _frows, right_cols={2, 3}))

    # ── Volume Spike / Whale Alert (moondevonyt WhaleAgent concept) ─
    try:
        import pandas_ta as pta
        _watch = ["SPY", "QQQ", "NVDA", "AAPL", "TSLA", "AMZN", "META", "MSFT"]
        # Fetch open position tickers to prepend them
        _conn_v = get_conn()
        try:
            _pos_tks = pd.read_sql("SELECT DISTINCT ticker FROM trades WHERE status='OPEN'", _conn_v)
            _watch = [str(t).upper() for t in _pos_tks["ticker"].tolist() if t] + _watch
            _watch = list(dict.fromkeys(_watch))[:10]  # dedup, cap at 10
        except Exception: pass
        finally: _conn_v.close()

        spike_lines = []
        for sym in _watch[:8]:
            try:
                _h = yf.Ticker(sym).history(period="10d", interval="1d")
                if len(_h) < 6:
                    continue
                vol_today = float(_h["Volume"].iloc[-1])
                vol_avg = float(_h["Volume"].iloc[-6:-1].mean())
                if vol_avg <= 0:
                    continue
                vol_ratio = vol_today / vol_avg
                if vol_ratio >= 1.5:  # 1.5x avg volume = notable
                    tag = "SPIKE" if vol_ratio >= 2.0 else "HIGH"
                    px = float(_h["Close"].iloc[-1])
                    chg = (px - float(_h["Close"].iloc[-2])) / float(_h["Close"].iloc[-2]) * 100
                    spike_lines.append(f"{sym:<6} {vol_ratio:>4.1f}x  {chg:>+5.1f}%  {tag}")
            except Exception:
                continue
        if spike_lines:
            # Parse spike_lines back into table rows
            _vs_rows = []
            for sl in spike_lines:
                # format: f"{sym:<6} {vol_ratio:>4.1f}x  {chg:>+5.1f}%  {tag}"
                parts_sl = sl.split()
                if len(parts_sl) >= 4:
                    _vs_rows.append((parts_sl[0], parts_sl[1], parts_sl[2], parts_sl[3]))
            if _vs_rows:
                parts.append("\n<b>VOLUME SPIKES</b>\n"
                             + _pipe_table(("Ticker", "Vol Ratio", "Chg%", "Signal"), _vs_rows, right_cols={1, 2}))
    except Exception as ve:
        log.warning(f"intraday volume spike check failed: {ve}")

    # ── Open positions OI for next expiry ─────────────────────────
    conn = get_conn()
    try:
        trades = pd.read_sql("SELECT DISTINCT ticker FROM trades WHERE status='OPEN'", conn)
        if not trades.empty:
            tickers = [str(r).upper() for r in trades["ticker"].tolist() if r]
            # Latest OI date
            latest_row = pd.read_sql("""
                SELECT DISTINCT trade_date_now FROM options_change
                ORDER BY substr(trade_date_now,7,4)||substr(trade_date_now,1,2)||substr(trade_date_now,4,2) DESC LIMIT 1
            """, conn)
            if not latest_row.empty:
                latest_dt = latest_row["trade_date_now"].iloc[0]
                _oi_hdrs = ["ST", "Tkr", "Exp", "C-OI", "P-OI"]
                _oi_RIGHT = {3, 4}
                _oi_data  = []
                key_moves = []
                def _fk2(n):
                    a = abs(n); sg = "+" if n >= 0 else "-"
                    if a >= 1_000_000: return f"{sg}{a/1_000_000:.1f}M"
                    if a >= 1_000:     return f"{sg}{a/1_000:.0f}K"
                    return f"{sg}{a:.0f}"
                # Fetch open position trades for context (strike, qty)
                pos_map = {}  # ticker -> list of {strike, qty, option_type}
                try:
                    pos_df = pd.read_sql("SELECT ticker, strike, quantity, option_type FROM trades WHERE status='OPEN'", conn)
                    for _, pr in pos_df.iterrows():
                        pt = str(pr["ticker"]).upper()
                        pos_map.setdefault(pt, []).append({
                            "strike": float(pr.get("strike", 0) or 0),
                            "qty": int(pr.get("quantity", 1) or 1),
                            "otype": str(pr.get("option_type", "")).lower(),
                        })
                except Exception:
                    pass

                for tk in tickers:
                    try:
                        today_ymd = datetime.now().strftime("%Y%m%d")
                        # Get live spot price
                        spot_tk = 0.0
                        try:
                            _th = yf.Ticker(tk).history(period="2d")
                            if len(_th) >= 1:
                                spot_tk = float(_th["Close"].iloc[-1])
                        except Exception:
                            pass

                        df = pd.read_sql("""
                            SELECT expiry_date,
                                   SUM(change_OI_Call) AS c_chg,
                                   SUM(change_OI_Put)  AS p_chg
                            FROM options_change
                            WHERE ticker=? AND trade_date_now=?
                              AND substr(expiry_date,7,4)||substr(expiry_date,1,2)||substr(expiry_date,4,2) > ?
                            GROUP BY expiry_date
                            ORDER BY substr(expiry_date,7,4)||substr(expiry_date,1,2)||substr(expiry_date,4,2) ASC
                            LIMIT 2
                        """, conn, params=(tk, latest_dt, today_ymd))
                        if not df.empty:
                            r    = df.iloc[0]
                            c    = float(r["c_chg"] or 0)
                            p    = float(r["p_chg"] or 0)
                            exp  = str(r["expiry_date"])[:5]
                            bias = "BULL" if c > abs(p)*1.1 else ("BEAR" if p > abs(c)*1.1 else "FLAT")
                            st   = "[B]" if bias=="BULL" else ("[S]" if bias=="BEAR" else "[ ]")
                            _oi_data.append([st, tk, exp, _fk2(c), _fk2(p)])

                            # Top active strikes for this expiry
                            top_df = pd.read_sql("""
                                SELECT strike, change_OI_Call, change_OI_Put,
                                       openInt_Call_now, openInt_Put_now,
                                       openInt_Call_prev, openInt_Put_prev
                                FROM options_change
                                WHERE ticker=? AND trade_date_now=? AND expiry_date=?
                                ORDER BY (ABS(change_OI_Call)+ABS(change_OI_Put)) DESC LIMIT 3
                            """, conn, params=(tk, latest_dt, str(r["expiry_date"])))

                            if not top_df.empty:
                                strike_lines = []
                                for _, sr in top_df.iterrows():
                                    s_strike = float(sr["strike"] or 0)
                                    c2 = float(sr["change_OI_Call"] or 0)
                                    p2 = float(sr["change_OI_Put"]  or 0)
                                    c_oi_now = float(sr["openInt_Call_now"] or 0)
                                    p_oi_now = float(sr["openInt_Put_now"]  or 0)
                                    if abs(c2) == 0 and abs(p2) == 0:
                                        continue

                                    # Is this strike one of our positions?
                                    my_pos = next((x for x in pos_map.get(tk, []) if abs(x["strike"] - s_strike) < 1), None)
                                    my_flag = " 📍<b>YOUR STRIKE</b>" if my_pos else ""

                                    # Strike location vs spot
                                    if spot_tk > 0:
                                        pct_from_spot = (s_strike - spot_tk) / spot_tk * 100
                                        if abs(pct_from_spot) <= 1.5:
                                            zone = "ATM"
                                        elif pct_from_spot > 0:
                                            zone = f"OTM +{pct_from_spot:.0f}%"
                                        else:
                                            zone = f"ITM {pct_from_spot:.0f}%"
                                    else:
                                        zone = "?"

                                    # Dollar notional (rough: OI_change × strike × 100)
                                    dominant_chg = c2 if abs(c2) >= abs(p2) else p2
                                    opt_type_s = "CALL" if abs(c2) >= abs(p2) else "PUT"
                                    notional = abs(dominant_chg) * s_strike * 100
                                    notional_s = f"${notional/1_000_000:.1f}M" if notional >= 1_000_000 else f"${notional/1_000:.0f}K"

                                    # Is it unusual? Compare to total standing OI
                                    standing_oi = c_oi_now if opt_type_s == "CALL" else p_oi_now
                                    unusual = standing_oi > 0 and abs(dominant_chg) / standing_oi > 0.15

                                    # Direction interpretation
                                    if dominant_chg > 0:
                                        # OI added — new positions opened
                                        if opt_type_s == "CALL":
                                            if zone.startswith("OTM"):
                                                direction_txt = "Traders opened new bullish bets — buying upside calls"
                                                action_hint = "Bullish speculation or a hedge against a short position"
                                            elif zone == "ATM":
                                                direction_txt = "Significant ATM call buying — directional bullish trade"
                                                action_hint = "High conviction bullish play or market maker delta hedge"
                                            else:
                                                direction_txt = "ITM call buying — very high delta, strong bullish conviction"
                                                action_hint = "Could be stock replacement strategy or covered call unwinding"
                                        else:
                                            if zone.startswith("OTM"):
                                                direction_txt = "New put contracts opened — bearish bets or downside protection"
                                                action_hint = "Fund hedging their long stock or outright bearish speculation"
                                            elif zone == "ATM":
                                                direction_txt = "ATM put buying — traders protecting against near-term drop"
                                                action_hint = "Defensive hedge; watch for follow-through selling in stock"
                                            else:
                                                direction_txt = "Deep ITM puts added — likely closing a covered put or rolling"
                                                action_hint = "Institutional roll or complex strategy, not simple bearish bet"
                                    else:
                                        # OI dropped — positions closed or expired
                                        if opt_type_s == "CALL":
                                            direction_txt = "Call positions being closed — bulls taking profits or cutting losses"
                                            action_hint = "Profit-taking if stock rallied; surrender if it dropped"
                                        else:
                                            direction_txt = "Put positions closed — bearish bets or hedges removed"
                                            action_hint = "Risk being lifted; could signal near-term bottom or hedge expiry"

                                    unusual_tag = " ⚠️ <b>UNUSUAL SIZE</b>" if unusual else ""
                                    pos_tag = ""
                                    if my_pos:
                                        my_qty = my_pos["qty"]
                                        my_ot  = my_pos["otype"].upper()
                                        if opt_type_s == my_ot:
                                            if dominant_chg > 0:
                                                pos_tag = "\n   💡 <i>Same direction as your position — supportive flow</i>"
                                            else:
                                                pos_tag = "\n   ⚠️ <i>Flow reducing OI in your strike — watch for exit pressure</i>"

                                    strike_lines.append(
                                        f"\n🔹 <b>${s_strike:.0f} {opt_type_s}</b> [{zone}]{my_flag}{unusual_tag}\n"
                                        f"   Change: <b>{_fk2(dominant_chg)} contracts</b>  |  Notional ≈ <b>{notional_s}</b>\n"
                                        f"   {direction_txt}.\n"
                                        f"   → <i>{action_hint}</i>{pos_tag}"
                                    )

                                if strike_lines:
                                    key_moves.append((tk, exp, c, p, bias, strike_lines))
                    except Exception:
                        pass

                if _oi_data:
                    _oi_t_hdr = ("ST", "Ticker", "Exp", "C-OI", "P-OI", "Bias")
                    _oi_t_rows = []
                    for _od in _oi_data:
                        _st_badge, _tk2, _exp2, _c2s, _p2s = _od
                        _bias_s = "BULL" if _st_badge == "[B]" else ("BEAR" if _st_badge == "[S]" else "FLAT")
                        _em2 = "🟢" if _st_badge == "[B]" else ("🔴" if _st_badge == "[S]" else "🟡")
                        _oi_t_rows.append((_em2, _tk2, _exp2, _c2s, _p2s, _bias_s))
                    _oi_all = [_oi_t_hdr] + _oi_t_rows
                    _oi_ws = [max(len(str(r[c])) for r in _oi_all) for c in range(len(_oi_t_hdr))]
                    _oi_sep = "+" + "+".join("-" * (w + 2) for w in _oi_ws) + "+"
                    def _oi_fmt(r):
                        return "|" + "|".join(f" {str(r[c]):<{_oi_ws[c]}} " for c in range(len(_oi_ws))) + "|"
                    _oi_table_lines = [_oi_sep, _oi_fmt(_oi_t_hdr), _oi_sep] + [_oi_fmt(r) for r in _oi_t_rows] + [_oi_sep]
                    parts.append("\n<b>YOUR POSITIONS — NEXT EXPIRY OI</b>\n<pre>" + "\n".join(_oi_table_lines) + "</pre>")

                if key_moves:
                    parts.append("\n<b>⚡ OI ACTIVITY — WHAT IS THE MARKET DOING?</b>")
                    for (tk_km, exp_km, c_km, p_km, bias_km, slines) in key_moves[:3]:
                        bias_em = "🟢 Bullish flow" if bias_km == "BULL" else ("🔴 Bearish flow" if bias_km == "BEAR" else "🟡 Mixed/Neutral flow")
                        net_c = _fk2(c_km); net_p = _fk2(p_km)
                        parts.append(
                            f"\n<b>{tk_km}</b>  exp {exp_km}  |  {bias_em}\n"
                            f"Overall: Calls {net_c}  Puts {net_p}"
                        )
                        for sl in slines[:2]:
                            parts.append(sl)
    except Exception as e:
        log.warning(f"intraday_alert OI query failed: {e}")
    finally:
        conn.close()

    # ── Momentum scanner: top bull + bear signals ─────────────────
    try:
        import asyncio as _aio
        _scan_bull, _scan_bear = await _aio.get_event_loop().run_in_executor(
            None, lambda: _live_momentum_scanner(top_n=3))
        if _scan_bull or _scan_bear:
            parts.append("\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
            parts.append("🚀 <b>TOP MOMENTUM SIGNALS</b>")
        if _scan_bull:
            parts.append("🟢 <b>BULL RUNNERS</b>")
            for _sr in _scan_bull:
                parts.append(_format_trade_signal(_sr, "BULL"))
        if _scan_bear:
            parts.append("🔴 <b>FALLING / BREAKDOWN</b>")
            for _sr in _scan_bear:
                parts.append(_format_trade_signal(_sr, "BEAR"))
        if _scan_bull or _scan_bear:
            parts.append("<i>⚠️ Algo signals — verify with OI + news before trading.</i>")
    except Exception as _se:
        log.warning(f"intraday scanner failed: {_se}")

    parts.append(f"\n<i>🕐 Data pulled at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</i>")
    msg = "\n".join(parts)
    try:
        await ctx.bot.send_message(chat_id=int(chat_id), text=msg, parse_mode=H)
    except Exception as e:
        log.warning(f"intraday_alert send failed: {e}")


async def global_market_view(query):
    """Display comprehensive global market context with sentiment analysis."""
    _loading = await query.message.reply_text("⏳ Fetching global market data...", parse_mode=H)
    
    try:
        # Fetch market data
        market_data = get_global_market_context()
        sentiment = analyze_market_sentiment(market_data)
        
        # Format summary
        summary = format_market_summary_telegram(market_data, sentiment)
        
        # Add recommendations
        recommendations = []
        if sentiment["volatility"] in ["HIGH", "EXTREME"]:
            recommendations.append("⚠️ High volatility → Credit spreads attractive")
            recommendations.append("⚠️ Options premiums expensive - favor selling")
        elif sentiment["volatility"] == "LOW":
            recommendations.append("✅ Low volatility → Debit spreads cheaper")
            recommendations.append("⚠️ Limited premium for credit strategies")
        
        if sentiment["risk_mode"] == "RISK ON":
            recommendations.append("📈 Risk-on → Bullish strategies favored")
        elif sentiment["risk_mode"] == "RISK OFF":
            recommendations.append("📉 Risk-off → Defensive positioning")
        
        if sentiment["overall"] in ["BULLISH", "MODERATELY BULLISH"]:
            recommendations.append("🟢 Bullish macro → Favor call strategies")
        elif sentiment["overall"] in ["BEARISH", "MODERATELY BEARISH"]:
            recommendations.append("🔴 Bearish macro → Favor put strategies, hedges")
        else:
            recommendations.append("🟡 Neutral macro → Iron condors, butterflies")
        
        rec_text = "\n".join([f"• {r}" for r in recommendations[:6]])
        
        # Build macro cascade section
        cascade_lines = []
        _md_ch = market_data.get("changes", {})
        _oil_c  = _md_ch.get("CL=F",    0) or 0
        _gld_c  = _md_ch.get("GC=F",    0) or 0
        _tnx_c  = _md_ch.get("^TNX",    0) or 0
        _btc_c  = _md_ch.get("BTC-USD", 0) or 0
        _vix_p  = _md_ch.get("^VIX",    0) or 0
        _vix_v  = market_data.get("prices", {}).get("^VIX", 20.0) or 20.0
        if abs(_oil_c) > 0.8:
            cascade_lines.append(f"🛢 Oil{"↑" if _oil_c>0 else "↓"}{_oil_c:+.1f}%  →  "
                                 f"{"▼DAL/UAL/AAL  ▲XLE/XOM/CVX" if _oil_c>0 else "▲DAL/UAL  ▼XLE/CVX"}")
        if abs(_gld_c) > 0.5:
            cascade_lines.append(f"🥇 Gold{"↑" if _gld_c>0 else "↓"}{_gld_c:+.1f}% →  "
                                 f"{"▲GDX/NEM  ▼DXY  ▲EEM" if _gld_c>0 else "▲DXY  ▼GDX/NEM"}")
        if abs(_tnx_c) > 0.3:
            cascade_lines.append(f"📈 10Y{"↑" if _tnx_c>0 else "↓"}{_tnx_c:+.1f}% →  "
                                 f"{"▲JPM/BAC  ▼VNQ/XLU  ▼Growth" if _tnx_c>0 else "▲VNQ/TLT  ▲Growth  ▼Banks"}")
        if abs(_btc_c) > 3.0:
            cascade_lines.append(f"₿ BTC{"↑" if _btc_c>0 else "↓"}{_btc_c:+.1f}% →  "
                                 f"{"Risk-ON: ▲MSTR/COIN/RIOT" if _btc_c>0 else "Risk-OFF: ▼spec"}")
        if _vix_v > 25:
            cascade_lines.append(f"😨 VIX {_vix_v:.1f} ELEVATED → Sell premium, buy hedges")
        elif _vix_v < 15:
            cascade_lines.append(f"😴 VIX {_vix_v:.1f} LOW → Iron condors / covered calls")
        cascade_text = ""
        if cascade_lines:
            cascade_text = (f"\n\n{hdr('🌐 MACRO CASCADE MAP')}\n"
                            + "\n".join(cascade_lines))
        # Stock relations for context
        rel_text = (f"\n\n{hdr('📡 KEY STOCK RELATIONS')}\n"
                    "NVDA↑ → SMCI/ANET/AMD (AI infra spend)\n"
                    "AAPL↑ → TSM/QCOM/AVGO (supply chain)\n"
                    "GOOGL↑ → META/TTD/MGNI (ad-tech floor)\n"
                    "Oil↑ → ▼DAL/UAL  ▲XLE/CVX  ▼XRT\n"
                    "Gold↑ → ▲GDX  ▼DXY → ▲EEM/FXI\n"
                    "Rates↑ → ▲Banks  ▼REITs  ▼Long-tech")
        full_message = (
            f"{summary}\n\n"
            f"{hdr('💡 OPTIONS STRATEGY IMPLICATIONS')}\n"
            f"{rec_text}"
            f"{cascade_text}"
            f"{rel_text}"
        )
        
        await _loading.delete()
        
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 Refresh", callback_data="menu_global_market")],
            [InlineKeyboardButton("🧩 More Features", callback_data="menu_more"), BACK_BTN]
        ])
        
        await query.message.reply_text(full_message, parse_mode=H, reply_markup=kb)
        
    except Exception as e:
        log.error(f"Global market view error: {e}")
        await _loading.edit_text(
            f"❌ Error fetching global market data:\n{str(e)[:200]}",
            parse_mode=H
        )


async def prop_trading_view(query):
    conn = get_conn()
    try:
        df = pd.read_sql(
            """
            SELECT ticker, trade_date, bull_score, bear_score, avg_spot,
                   (bull_score - bear_score) AS net_signal,
                   (CAST(put_oi AS REAL) / NULLIF(call_oi, 0)) as pcr
            FROM us_analytics_daily
            WHERE trade_date = {max_dt}
            """.format(max_dt=_max_date_sql('trade_date', 'us_analytics_daily')),
            conn,
        )
    except Exception:
        df = pd.DataFrame()
    conn.close()

    if df.empty:
        await query.message.reply_text("🏦 No prop setups available.", reply_markup=InlineKeyboardMarkup([[BACK_BTN]]))
        return

    top = df.assign(abs_signal=lambda x: x["net_signal"].abs()).sort_values("abs_signal", ascending=False).head(10)
    tbl_rows = [f"{'Ticker':<6} {'Side':<7} {'Score':>6} {'PCR':>5}"]
    tbl_rows.append("─" * 28)
    for _, r in top.iterrows():
        side = "LONG" if r["net_signal"] > 2 else "SHORT" if r["net_signal"] < -2 else "MIXED"
        pcr = r["pcr"] if r["pcr"] == r["pcr"] else 0
        tbl_rows.append(f"{r['ticker']:<6} {side:<7} {r['net_signal']:+6.0f} {pcr:>5.2f}")
    lines = [hdr("🏦 PROP TRADING SETUPS"), "", mono("\n".join(tbl_rows))]

    kb = InlineKeyboardMarkup([[InlineKeyboardButton("🧩 More", callback_data="menu_more"), BACK_BTN]])
    await query.message.reply_text("\n".join(lines), parse_mode=H, reply_markup=kb)


async def backtest_lab_view(query):
    conn = get_conn()
    try:
        sig = pd.read_sql(
            "SELECT ticker, trade_date, bull_score, bear_score FROM us_analytics_daily",
            conn,
        )
        px = pd.read_sql("SELECT ticker, trade_date, close FROM stock_daily", conn)
    except Exception:
        sig = pd.DataFrame()
        px = pd.DataFrame()
    conn.close()

    if sig.empty or px.empty:
        await query.message.reply_text("📈 Backtest data unavailable.", reply_markup=InlineKeyboardMarkup([[BACK_BTN]]))
        return

    sig = sig.dropna(subset=["ticker", "trade_date"]).copy()
    sig["net_signal"] = sig["bull_score"].fillna(0) - sig["bear_score"].fillna(0)
    sig["signal"] = np.where(sig["net_signal"] > 2, 1, np.where(sig["net_signal"] < -2, -1, 0))

    px = px.dropna(subset=["ticker", "trade_date", "close"]).copy()
    px["close"] = pd.to_numeric(px["close"], errors="coerce")
    # Parse MM-DD-YYYY dates for proper chronological sorting
    px["_date_sort"] = pd.to_datetime(px["trade_date"], format="%m-%d-%Y", errors="coerce")
    px = px.sort_values(["ticker", "_date_sort"])
    px["next_close"] = px.groupby("ticker")["close"].shift(-1)
    px["next_ret"] = (px["next_close"] - px["close"]) / px["close"]

    bt = sig.merge(px[["ticker", "trade_date", "next_ret"]], on=["ticker", "trade_date"], how="inner")
    bt = bt[(bt["signal"] != 0) & bt["next_ret"].notna()]
    if bt.empty:
        await query.message.reply_text("📈 Not enough overlap for backtest.", reply_markup=InlineKeyboardMarkup([[BACK_BTN]]))
        return

    bt["hit"] = ((bt["signal"] == 1) & (bt["next_ret"] > 0)) | ((bt["signal"] == -1) & (bt["next_ret"] < 0))
    acc = bt["hit"].mean() * 100
    rows = len(bt)
    avg_abs_move = (bt["next_ret"].abs().mean() * 100) if rows else 0
    msg = (
        f"{hdr('📈 BACKTEST LAB')}\n\n"
        + mono(
            f"{row2('Signals Tested', str(rows))}\n"
            f"{row2('Accuracy', f'{acc:.1f}%')}\n"
            f"{row2('Avg Next Move', f'{avg_abs_move:.2f}%')}"
        )
    )
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("🧩 More", callback_data="menu_more"), BACK_BTN]])
    await _safe_reply(query.message, msg, reply_markup=kb)


async def live_predictor_view(query):
    try:
        es = yf.Ticker("ES=F").history(period="5d")
        vix = yf.Ticker("^VIX").history(period="5d")
        es_ret = (float(es["Close"].iloc[-1]) / float(es["Close"].iloc[-2]) - 1) * 100 if len(es) >= 2 else 0
        vix_px = float(vix["Close"].iloc[-1]) if len(vix) >= 1 else 0
    except Exception:
        es_ret = 0
        vix_px = 0

    regime = "BULLISH" if es_ret > 0.4 and vix_px < 20 else "BEARISH" if es_ret < -0.4 or vix_px > 25 else "NEUTRAL"
    msg = (
        f"{hdr('🔮 LIVE POSITION PREDICTOR')}\n\n"
        + mono(
            f"{row2('ES Futures', f'{es_ret:+.2f}%')}\n"
            f"{row2('VIX', f'{vix_px:.2f}')}\n"
            f"{row2('Regime', regime)}"
        )
        + "\nUse OI + futures + VIX together before taking new entries."
    )
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("🧩 More", callback_data="menu_more"), BACK_BTN]])
    await _safe_reply(query.message, msg, reply_markup=kb)


async def whales_view(query):
    conn = get_conn()
    try:
        df = pd.read_sql(
            """
            SELECT filer_name, ticker, value_usd, report_date
            FROM institutional_holdings
            ORDER BY value_usd DESC
            LIMIT 10
            """,
            conn,
        )
    except Exception:
        df = pd.DataFrame()
    conn.close()

    if df.empty:
        await query.message.reply_text("🐋 No whale holdings data available.", reply_markup=InlineKeyboardMarkup([[BACK_BTN]]))
        return

    tbl_rows = [f"{'Ticker':<6} {'Value($B)':>9} {'Holder':<12}"]
    tbl_rows.append("─" * 30)
    for _, r in df.iterrows():
        v = pd.to_numeric(r.get("value_usd", 0), errors="coerce")
        v_b = (float(v) / 1e9) if pd.notna(v) else 0
        holder = str(r.get("filer_name", "?"))[:12]
        tbl_rows.append(f"{str(r.get('ticker', '?')):<6} {v_b:>9.2f} {holder:<12}")
    lines = [hdr("🐋 WHALE HOLDINGS"), "", mono("\n".join(tbl_rows))]

    kb = InlineKeyboardMarkup([[InlineKeyboardButton("🧩 More", callback_data="menu_more"), BACK_BTN]])
    await query.message.reply_text("\n".join(lines), parse_mode=H, reply_markup=kb)

# ═══════════════════════════════════════════════════════════
#  8) QUICK QUOTE — full OHLCV, 52W, fundamentals
# ═══════════════════════════════════════════════════════════
async def quote_menu(query):
    tickers = _ticker_universe()
    kb = _paged_ticker_keyboard("quote", tickers, page=0, per_page=12, cols=3, include_back=True, back_cb="menu_quote")
    await query.message.reply_text(
        f"{hdr('⚡ QUICK QUOTE')}\n\nSelect a ticker or search:",
        parse_mode=H, reply_markup=kb)

async def quick_quote(query, ticker):
    _loading = await query.message.reply_text(f"⏳ Fetching {ticker}...", parse_mode=H)
    try:
        tk = yf.Ticker(ticker)
        h = tk.history(period="7d")
        info = tk.info

        # Latest bar OHLCV
        last = h.iloc[-1]
        px  = float(last["Close"])
        opn = float(last["Open"])
        hi  = float(last["High"])
        lo  = float(last["Low"])
        vol = float(last["Volume"])
        prev = float(h["Close"].iloc[-2]) if len(h) >= 2 else px
        chg = (px - prev) / prev * 100
        chg_abs = px - prev

        # 52-week
        hi52 = info.get("fiftyTwoWeekHigh", 0)
        lo52 = info.get("fiftyTwoWeekLow", 0)
        from52hi = (px - hi52) / hi52 * 100 if hi52 else 0
        from52lo = (px - lo52) / lo52 * 100 if lo52 else 0

        # Fundamentals
        mktcap = info.get("marketCap", 0)
        cap_str = f"${mktcap/1e12:.2f}T" if mktcap > 1e12 else f"${mktcap/1e9:.0f}B" if mktcap > 1e9 else f"${mktcap/1e6:.0f}M" if mktcap > 1e6 else "—"
        pe = info.get("trailingPE", None)
        pe_str = f"{pe:.1f}" if pe else "—"
        fwd_pe = info.get("forwardPE", None)
        fwd_pe_str = f"{fwd_pe:.1f}" if fwd_pe else "—"
        eps = info.get("trailingEps", None)
        eps_str = f"${eps:.2f}" if eps else "—"
        div_yield = info.get("dividendYield", None)
        div_str = f"{div_yield*100:.2f}%" if div_yield else "—"
        beta_val = info.get("beta", None)
        beta_str = f"{beta_val:.2f}" if beta_val else "—"
        avg_vol = info.get("averageVolume", 0)
        name = info.get("shortName", ticker)

        # Volume bar
        vol_ratio = vol / avg_vol if avg_vol > 0 else 1
        vol_bar_str = bar(min(vol_ratio * 50, 100))  # 2x avg = full bar

        arrow = _col_arrow(chg)
        color_emoji = "🟢" if chg > 0.5 else "🔴" if chg < -0.5 else "⚪"
        color = "#2ecc40" if chg > 0.5 else "#ff4136" if chg < -0.5 else "#888"

        def fmt_vol(v):
            if v >= 1e6: return f"{v/1e6:.1f}M"
            if v >= 1e3: return f"{v/1e3:.0f}K"
            return f"{v:.0f}"

        msg = (
            f"{color_emoji} <b>{ticker} · {name}</b>\n"
            f"{hdr('')}\n\n"

            f"<b>💰 Price Action</b>\n"
            + mono(
                f"{row2('Last', f'${px:.2f}  {arrow} {chg:+.2f}%')}\n"
                f"{row2('Change', f'${chg_abs:+.2f}')}\n"
                f"{'─' * 27}\n"
                f"{row2('Open', f'${opn:.2f}')}\n"
                f"{row2('High', f'${hi:.2f}')}\n"
                f"{row2('Low', f'${lo:.2f}')}\n"
                f"{row2('Close', f'${px:.2f}')}\n"
                f"{'─' * 27}\n"
                f"{row2('Volume', fmt_vol(vol))}\n"
                f"{row2('Avg Vol', fmt_vol(avg_vol))}\n"
                f"Vol {vol_bar_str} {vol_ratio:.1f}x avg"
            )

            + "\n\n📏 <b>52-Week Range</b>\n"
            + mono(
                f"{row2('52W High', f'${hi52:.2f}  ({from52hi:+.1f}%)')}\n"
                f"{row2('52W Low', f'${lo52:.2f}  ({from52lo:+.1f}%)')}\n"
                f"Lo ├{'█' * max(0,min(20,int((px-lo52)/(hi52-lo52)*20) if hi52>lo52 else 10))}{'░' * max(0,20-int((px-lo52)/(hi52-lo52)*20) if hi52>lo52 else 10)}┤ Hi"
            )

            + "\n\n📊 <b>Fundamentals</b>\n"
            + mono(
                f"{row2('Mkt Cap', cap_str)}\n"
                f"{row2('P/E (TTM)', pe_str)}\n"
                f"{row2('P/E (Fwd)', fwd_pe_str)}\n"
                f"{row2('EPS', eps_str)}\n"
                f"{row2('Div Yield', div_str)}\n"
                f"{row2('Beta', beta_str)}"
            )

            + f"\n\n<i>Updated {datetime.now().strftime('%H:%M:%S')}</i>"
        )

        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 Refresh", callback_data=f"quote_{ticker}"),
             InlineKeyboardButton("📰 News", callback_data=f"news_{ticker}")],
            [InlineKeyboardButton("📊 OI", callback_data=f"oi_detail_{ticker}"),
             InlineKeyboardButton("⚡ Other", callback_data="menu_quote"), BACK_BTN],
        ])
        await _safe_reply(query.message, msg, reply_markup=kb)
        # Mini chart
        try:
            chart_bytes = make_mini_chart(ticker, days=7)
            await query.message.reply_photo(chart_bytes, caption=f"{ticker} — 7d mini chart", parse_mode=H)
        except Exception as e:
            log.warning(f"Mini chart error: {e}")
        try: await _loading.delete()
        except Exception: pass
    except Exception as e:
        await query.message.reply_text(f"❌ Error: {e}",
                                       reply_markup=InlineKeyboardMarkup([[BACK_BTN]]))
        try: await _loading.delete()
        except Exception: pass

# ═══════════════════════════════════════════════════════════
#  CALLBACK ROUTER
# ═══════════════════════════════════════════════════════════
async def button_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    try:
        if data == "menu_main":
            await show_main_menu(query)
        elif data == "brief_refresh":
            await briefing_view(query)
        elif data == "opex_refresh":
            await opex_view(query)
        elif data.startswith("event_refresh|"):
            await event_view(query, data.split("|", 1)[1])
        elif data.startswith("event_mon|"):
            await event_monitor_btn(query, data.split("|", 1)[1])
        elif data.startswith("bm|"):
            _bp = data.split("|")
            await bookmark_btn(query, _bp[1], _bp[2] if len(_bp) > 2 else "")
        elif data == "show_bookmarks":
            await bookmarks_view(query)
        elif data == "gex_refresh":
            await gex_view(query)
        elif data == "hub_menu":
            await query.message.reply_text("📡 <b>Macro / Event Hub</b> — pick one:", parse_mode=H, reply_markup=HUB_MENU_KB)
        elif data == "sq_scan":
            await squeeze_view(query)
        elif data == "ev_menu":
            await query.message.reply_text("🌍 <b>Pick an event:</b>", parse_mode=H, reply_markup=_events_kb())
        elif data == "jr_view":
            await journal_view(query)
        elif data == "macro_view":
            await macro_view(query)
        elif data == "mom_view":
            await mom_view(query)
        elif data == "plan_view":
            await plan_view(query)
        elif data == "wrap_view":
            await wrap_view(query)
        elif data == "tv_view":
            await tv_view(query)
        elif data.startswith("tvc_"):
            await tv_view(query, data.split("tvc_", 1)[1])
        elif data == "plan_port_chart":
            await plan_port_chart_view(query)
        elif data.startswith("plan_chart_"):
            await plan_chart_view(query, data.split("plan_chart_", 1)[1])
        elif data == "mom_recompute":
            await mom_recompute_view(query)
        elif data == "mom_help":
            await mom_help_view(query)
        elif data == "regime_view":
            await regime_view(query)
        elif data == "vanna_view":
            await vanna_view(query)
        elif data == "noop":
            return
        elif data == "menu_market" or data == "menu_refresh":
            await market_overview(query)
        elif data.startswith("grpstock_"):
            tkr = data.split("_", 1)[1]
            await group_stock_detail(query, tkr)
        elif data == "menu_news":
            await news_menu(query)
        elif data == "news_ALL":
            await market_headlines(query)
        elif data.startswith("news_"):
            ticker = data.split("_", 1)[1]
            await news_for_ticker(query, ticker)
        elif data == "menu_exit":
            await exit_planner_menu(query)
        elif data == "exit_mode_indiv":
            await exit_mode_indiv(query)
        elif data == "exit_mode_stock":
            await exit_mode_stock(query)
        elif data == "exit_batch_all":
            await exit_batch_all(query)
        elif data == "exit_batch_all_cards":
            await exit_batch_all_cards(query)
        elif data.startswith("exit_batch_tk|"):
            await exit_batch_ticker(query, data.split("|", 1)[1])
        elif data.startswith("exit_tk_cards|"):
            await exit_ticker_cards(query, data.split("|", 1)[1])
        elif data.startswith("exitmc|"):
            # exitmc|TICKER|type|strike|entry|expiry[|qty]
            parts_mc = data.split("|", 6)
            _, ticker, opt_type, strike, entry, expiry_str = parts_mc[:6]
            qty_mc = int(parts_mc[6]) if len(parts_mc) > 6 else 1
            strike = float(strike)
            entry = float(entry)
            await run_exit_analysis(query, ticker, opt_type, strike, entry, expiry_str, qty=qty_mc)
        elif data.startswith("exit_"):
            # legacy format fallback: exit_TICKER_type_strike_entry_YYYY-MM-DD
            parts = data.split("_")
            ticker = parts[1]
            opt_type = parts[2]
            strike = float(parts[3])
            entry = float(parts[4])
            expiry_str = "_".join(parts[5:])  # rejoin date parts
            await run_exit_analysis(query, ticker, opt_type, strike, entry, expiry_str, qty=1)
        elif data.startswith("scenarios|"):
            parts_sc = data.split("|", 6)
            _, ticker, opt_type, strike, entry, expiry_str = parts_sc[:6]
            qty_sc = int(parts_sc[6]) if len(parts_sc) > 6 else 1
            strike = float(strike); entry = float(entry)
            await show_scenarios(query, ticker, opt_type, strike, entry, expiry_str, qty=qty_sc)
        elif data.startswith("scenarios_"):
            parts = data.split("_")
            ticker = parts[1]; opt_type = parts[2]
            strike = float(parts[3]); entry = float(parts[4])
            expiry_str = "_".join(parts[5:])
            await show_scenarios(query, ticker, opt_type, strike, entry, expiry_str)
        elif data == "menu_positions":
            await positions_view(query)
        elif data == "menu_pos_monitor":
            await position_monitor_adhoc(query, ctx)
        elif data == "posadd_start":
            await posadd_ticker_menu(query, ctx, page=0, reset=True)
        elif data.startswith("posaddtk_page_"):
            page = _safe_int(data.split("_")[-1], 0)
            await posadd_ticker_menu(query, ctx, page=page)
        elif data.startswith("posaddtk_"):
            tk = data.split("_", 1)[1]
            await posadd_option_type_menu(query, ctx, tk)
        elif data.startswith("posaddot_"):
            ot = data.split("_", 1)[1]
            st = ctx.user_data.get("posadd", {})
            st["opt_type"] = str(ot).lower()
            ctx.user_data["posadd"] = st
            await posadd_expiry_menu(query, ctx, page=0)
        elif data.startswith("posaddexpg_"):
            page = _safe_int(data.split("_")[-1], 0)
            await posadd_expiry_menu(query, ctx, page=page)
        elif data.startswith("posaddexp_"):
            idx = _safe_int(data.split("_")[-1], -1)
            st = ctx.user_data.get("posadd", {})
            exps = st.get("expiries", [])
            if 0 <= idx < len(exps):
                st["expiry"] = exps[idx]
                ctx.user_data["posadd"] = st
                await posadd_strike_menu(query, ctx, page=0)
            else:
                await query.message.reply_text("❌ Invalid expiry selection.")
        elif data.startswith("posaddskpg_"):
            page = _safe_int(data.split("_")[-1], 0)
            await posadd_strike_menu(query, ctx, page=page)
        elif data.startswith("posaddsk_"):
            idx = _safe_int(data.split("_")[-1], -1)
            st = ctx.user_data.get("posadd", {})
            strikes = st.get("strikes", [])
            if 0 <= idx < len(strikes):
                st["strike"] = float(strikes[idx])
                ctx.user_data["posadd"] = st
                await posadd_side_menu(query)
            else:
                await query.message.reply_text("❌ Invalid strike selection.")
        elif data.startswith("posaddsd_"):
            side = data.split("_")[-1]
            st = ctx.user_data.get("posadd", {})
            st["side"] = side if side in ("buy", "sell") else "buy"
            ctx.user_data["posadd"] = st
            await posadd_qty_menu(query)
        elif data.startswith("posaddqty_"):
            q = _safe_int(data.split("_")[-1], 1)
            st = ctx.user_data.get("posadd", {})
            st["qty"] = max(1, q)
            ctx.user_data["posadd"] = st
            await posadd_day_menu(query)
        elif data.startswith("posaddday_"):
            d = _safe_int(data.split("_")[-1], 0)
            st = ctx.user_data.get("posadd", {})
            st["day_offset"] = max(0, d)
            ctx.user_data["posadd"] = st
            await posadd_price_menu(query, ctx)
        elif data.startswith("posaddpx_custom_"):
            # User tapped a custom price button ($X.XX)
            px_str = data.replace("posaddpx_custom_", "")
            custom_px = _safe_float(px_str, 0)
            st = ctx.user_data.get("posadd", {})
            st["px_mode"] = "custom"
            st["entry_price"] = custom_px
            ctx.user_data["posadd"] = st
            await posadd_confirm_menu(query, ctx)
        elif data.startswith("posaddpx_"):
            pm = data.split("_")[-1]
            st = ctx.user_data.get("posadd", {})
            st["px_mode"] = pm if pm in ("bid", "mid", "ask") else "mid"
            ctx.user_data["posadd"] = st
            await posadd_confirm_menu(query, ctx)
        elif data == "posaddgo":
            st = ctx.user_data.get("posadd", {})
            tk = st.get("ticker")
            ot = st.get("opt_type")
            strike = _safe_float(st.get("strike", 0), 0)
            exp = st.get("expiry")
            side = st.get("side", "buy")
            qty = _safe_int(st.get("qty", 1), 1)
            entry_price = _safe_float(st.get("entry_price", 0), 0)
            entry_date = st.get("entry_date")
            signed_qty = qty if side == "buy" else -qty
            ok, new_id, note = _insert_new_trade(
                tk,
                ot,
                strike,
                exp,
                signed_qty,
                strategy="telegram_manual_add",
                entry_price=entry_price,
                entry_date=entry_date,
                notes=f"Added via Telegram wizard ({side.upper()})",
            )
            if ok and new_id is not None:
                ctx.user_data.pop("posadd", None)
                await position_detail(query, new_id, notice=f"✅ {note}")
            else:
                await query.message.reply_text(f"❌ {note}", reply_markup=InlineKeyboardMarkup([[BACK_BTN]]))
        elif data == "posadd_back_type":
            st = ctx.user_data.get("posadd", {})
            tk = st.get("ticker", "")
            await posadd_option_type_menu(query, ctx, tk)
        elif data == "posadd_back_expiry":
            await posadd_expiry_menu(query, ctx, page=0)
        elif data == "posadd_back_strike":
            await posadd_strike_menu(query, ctx, page=0)
        elif data == "posadd_back_side":
            await posadd_side_menu(query)
        elif data == "posadd_back_qty":
            await posadd_qty_menu(query)
        elif data == "posadd_back_day":
            await posadd_day_menu(query)
        elif data == "posadd_back_price":
            await posadd_price_menu(query, ctx)
        elif data.startswith("pos_"):
            tid = _safe_int(data.split("_")[1], 0)
            await position_detail(query, tid)
        elif data.startswith("posedit_"):
            # posedit_{id}_{field}_{op}
            parts = data.split("_")
            tid = _safe_int(parts[1], 0)
            field = parts[2]
            op = parts[3]
            tr = _fetch_trade(tid)
            if not tr:
                await query.message.reply_text("❌ Trade not found.", reply_markup=InlineKeyboardMarkup([[BACK_BTN]]))
                return

            notice = ""
            if field == "qty":
                cur = _safe_int(tr.get("quantity", 1), 1)
                new = cur + (1 if op == "p1" else -1)
                if new == 0:
                    new = 1
                ok = _update_trade_field(tid, "quantity", int(new))
                notice = f"{'✅' if ok else '❌'} Quantity updated to {new}"
            elif field == "ent":
                cur = _safe_float(tr.get("entry_price", 0), 0)
                new = max(0.01, cur + (0.10 if op == "p" else -0.10))
                ok = _update_trade_field(tid, "entry_price", float(round(new, 2)))
                notice = f"{'✅' if ok else '❌'} Entry updated to ${new:.2f}"
            elif field == "stk":
                cur = _safe_float(tr.get("strike", 0), 0)
                delta = 5 if op == "p5" else -5
                new = max(0.5, cur + delta)
                ok = _update_trade_field(tid, "strike", float(round(new, 2)))
                notice = f"{'✅' if ok else '❌'} Strike updated to ${new:.2f}"
            elif field == "exp":
                cur = str(tr.get("expiry", ""))
                try:
                    dt = datetime.strptime(cur, "%Y-%m-%d").date()
                except Exception:
                    dt = datetime.now().date() + timedelta(days=30)
                dt = dt + timedelta(days=(7 if op == "p7" else -7))
                if dt <= datetime.now().date():
                    dt = datetime.now().date() + timedelta(days=1)
                new = dt.strftime("%Y-%m-%d")
                ok = _update_trade_field(tid, "expiry", new)
                notice = f"{'✅' if ok else '❌'} Expiry updated to {new}"
            else:
                notice = "❌ Unsupported edit operation"

            await position_detail(query, tid, notice=notice)
        elif data.startswith("postog_"):
            tid = _safe_int(data.split("_")[1], 0)
            tr = _fetch_trade(tid)
            if not tr:
                await query.message.reply_text("❌ Trade not found.", reply_markup=InlineKeyboardMarkup([[BACK_BTN]]))
                return
            cur = str(tr.get("option_type", "CALL")).upper()
            new = "PUT" if cur == "CALL" else "CALL"
            ok = _update_trade_field(tid, "option_type", new)
            await position_detail(query, tid, notice=f"{'✅' if ok else '❌'} Option type switched to {new}")
        elif data.startswith("postogside_"):
            tid = _safe_int(data.split("_")[-1], 0)
            tr = _fetch_trade(tid)
            if not tr:
                await query.message.reply_text("❌ Trade not found.", reply_markup=InlineKeyboardMarkup([[BACK_BTN]]))
                return
            cur_qty = _safe_int(tr.get("quantity", 1), 1)
            new_qty = -cur_qty  # flip sign = flip buy/sell
            ok = _update_trade_field(tid, "quantity", new_qty)
            new_side = "SELL" if new_qty < 0 else "BUY"
            await position_detail(query, tid, notice=f"{'✅' if ok else '❌'} Side switched to {new_side}")
        elif data.startswith("posexit_"):
            tid = _safe_int(data.split("_")[1], 0)
            ok, note = _close_trade_now(tid)
            await position_detail(query, tid, notice=f"{'✅' if ok else '❌'} {note}")
        elif data.startswith("pospair_"):
            parts = data.split("_")
            tid = _safe_int(parts[1], 0)
            mode = parts[2] if len(parts) > 2 else "buy"
            tr = _fetch_trade(tid)
            if not tr:
                await query.message.reply_text("❌ Parent position not found.")
            else:
                ctx.user_data["pairwiz"] = {
                    "parent_id": tid,
                    "ticker": str(tr.get("ticker", "")).upper(),
                    "opt_type": str(tr.get("option_type", "CALL")).lower(),
                    "side": "sell" if mode == "sell" else "buy",
                    "qty": max(1, abs(_safe_int(tr.get("quantity", 1), 1))),
                }
                await pair_ticker_menu(query, ctx, page=0)
        elif data.startswith("pairtkpg_"):
            page = _safe_int(data.split("_")[-1], 0)
            await pair_ticker_menu(query, ctx, page=page)
        elif data.startswith("pairtk_"):
            tk = data.split("_", 1)[1]
            st = ctx.user_data.get("pairwiz", {})
            st["ticker"] = str(tk).upper()
            ctx.user_data["pairwiz"] = st
            await pair_option_type_menu(query)
        elif data.startswith("pairot_"):
            ot = data.split("_", 1)[1]
            st = ctx.user_data.get("pairwiz", {})
            st["opt_type"] = str(ot).lower()
            ctx.user_data["pairwiz"] = st
            await pair_expiry_menu(query, ctx, page=0)
        elif data.startswith("pairexpg_"):
            page = _safe_int(data.split("_")[-1], 0)
            await pair_expiry_menu(query, ctx, page=page)
        elif data.startswith("pairexp_"):
            idx = _safe_int(data.split("_")[-1], -1)
            st = ctx.user_data.get("pairwiz", {})
            exps = st.get("expiries", [])
            if 0 <= idx < len(exps):
                st["expiry"] = exps[idx]
                ctx.user_data["pairwiz"] = st
                await pair_strike_menu(query, ctx, page=0)
            else:
                await query.message.reply_text("❌ Invalid expiry selection.")
        elif data.startswith("pairskpg_"):
            page = _safe_int(data.split("_")[-1], 0)
            await pair_strike_menu(query, ctx, page=page)
        elif data.startswith("pairsk_"):
            idx = _safe_int(data.split("_")[-1], -1)
            st = ctx.user_data.get("pairwiz", {})
            strikes = st.get("strikes", [])
            if 0 <= idx < len(strikes):
                st["strike"] = float(strikes[idx])
                ctx.user_data["pairwiz"] = st
                await pair_side_menu(query)
            else:
                await query.message.reply_text("❌ Invalid strike selection.")
        elif data.startswith("pairside_"):
            side = data.split("_")[-1]
            st = ctx.user_data.get("pairwiz", {})
            st["side"] = side if side in ("buy", "sell") else "buy"
            ctx.user_data["pairwiz"] = st
            await pair_qty_menu(query)
        elif data.startswith("pairqty_"):
            q = _safe_int(data.split("_")[-1], 1)
            st = ctx.user_data.get("pairwiz", {})
            st["qty"] = max(1, q)
            ctx.user_data["pairwiz"] = st
            await pair_day_menu(query)
        elif data.startswith("pairday_"):
            d = _safe_int(data.split("_")[-1], 0)
            st = ctx.user_data.get("pairwiz", {})
            st["day_offset"] = max(0, d)
            ctx.user_data["pairwiz"] = st
            await pair_price_menu(query)
        elif data.startswith("pairpx_"):
            pm = data.split("_")[-1]
            st = ctx.user_data.get("pairwiz", {})
            st["px_mode"] = pm if pm in ("bid", "mid", "ask") else "mid"
            ctx.user_data["pairwiz"] = st
            await pair_confirm_menu(query, ctx)
        elif data == "pairchart":
            await pair_send_chart(query, ctx)
        elif data == "pairgo":
            st = ctx.user_data.get("pairwiz", {})
            parent_id = _safe_int(st.get("parent_id", 0), 0)
            parent = _fetch_trade(parent_id)
            if not parent:
                await query.message.reply_text("❌ Parent position not found.")
            else:
                side = st.get("side", "buy")
                qty = _safe_int(st.get("qty", 1), 1)
                signed_qty = qty if side == "buy" else -qty
                ok, new_id, note = _insert_new_trade(
                    st.get("ticker"),
                    st.get("opt_type"),
                    _safe_float(st.get("strike", 0), 0),
                    st.get("expiry"),
                    signed_qty,
                    strategy="paired_leg_manual",
                    entry_price=_safe_float(st.get("entry_price", 0), 0),
                    entry_date=st.get("entry_date"),
                    notes=f"Paired with trade #{parent_id} ({side.upper()})",
                    account_type=str(parent.get("account_type", "Taxable")),
                )
                ctx.user_data.pop("pairwiz", None)
                await position_detail(query, parent_id, notice=f"{'✅' if ok else '❌'} {note}")
        elif data == "pair_back_ticker":
            await pair_ticker_menu(query, ctx, page=0)
        elif data == "pair_back_type":
            await pair_option_type_menu(query)
        elif data == "pair_back_expiry":
            await pair_expiry_menu(query, ctx, page=0)
        elif data == "pair_back_strike":
            await pair_strike_menu(query, ctx, page=0)
        elif data == "pair_back_side":
            await pair_side_menu(query)
        elif data == "pair_back_qty":
            await pair_qty_menu(query)
        elif data == "pair_back_day":
            await pair_day_menu(query)
        elif data == "menu_oi":
            await oi_menu(query)
        elif data.startswith("oi_expiry_"):
            expiry = data.replace("oi_expiry_", "")
            await oi_menu(query, expiry=expiry)
        elif data == "oi_change_menu":
            await oi_change_ticker_menu(query)
        elif data.startswith("oi_change_page_"):
            page = int(data.split("_")[-1])
            conn = get_conn()
            try:
                latest_date_df = pd.read_sql("SELECT DISTINCT trade_date FROM options_daily ORDER BY substr(trade_date,7,4)||substr(trade_date,1,2)||substr(trade_date,4,2) DESC LIMIT 1", conn)
                latest_date = latest_date_df["trade_date"].iloc[0] if not latest_date_df.empty else None
                if latest_date:
                    tickers_df = pd.read_sql("SELECT DISTINCT ticker FROM options_daily WHERE trade_date = ? ORDER BY ticker", conn, params=(latest_date,))
                    tickers = tickers_df["ticker"].tolist()
                else:
                    tickers = []
            except Exception:
                tickers = []
            conn.close()
            kb = _paged_ticker_keyboard("oi_change", tickers, page=page, per_page=12, cols=3, include_back=True, back_cb="menu_oi")
            await query.message.reply_text(f"{hdr('📊 OI CHANGE CHART')}\n\nSelect ticker:", parse_mode=H, reply_markup=kb)
        elif data.startswith("oi_change_live_"):
            ticker = data.replace("oi_change_live_", "")
            await oi_change_chart_live_view(query, ticker)
        elif data.startswith("oi_change_eod_"):
            ticker = data.replace("oi_change_eod_", "")
            await oi_change_chart_eod_view(query, ticker)
        elif data.startswith("oi_change_"):
            ticker = data.replace("oi_change_", "")
            await oi_change_chart_view(query, ticker)
        elif data == "oi_compare_select1":
            await oi_compare_select_expiry(query, ctx, step=1)
        elif data.startswith("oi_cmp1_"):
            exp1 = data.replace("oi_cmp1_", "")
            ctx.user_data["oi_compare_exp1"] = exp1
            await oi_compare_select_expiry(query, ctx, step=2)
        elif data.startswith("oi_cmp2_"):
            exp2 = data.replace("oi_cmp2_", "")
            exp1 = ctx.user_data.get("oi_compare_exp1", "")
            if exp1:
                await oi_compare_view(query, ctx, exp1, exp2)
                ctx.user_data.pop("oi_compare_exp1", None)
            else:
                await query.message.reply_text("⚠️ Comparison session expired. Start again.", reply_markup=InlineKeyboardMarkup([[BACK_BTN]]))
        elif data.startswith("oi_detail_page_"):
            page = int(data.split("_")[-1])
            conn = get_conn()
            try:
                dfx = pd.read_sql(
                    """
                    SELECT DISTINCT ticker FROM options_daily
                    WHERE trade_date = (
                        SELECT trade_date FROM options_daily
                        ORDER BY substr(trade_date,7,4)||substr(trade_date,1,2)||substr(trade_date,4,2) DESC LIMIT 1
                    )
                    """,
                    conn,
                )
            except Exception:
                dfx = pd.DataFrame(columns=["ticker"])
            conn.close()

            tks = sorted(dfx["ticker"].dropna().astype(str).str.upper().unique().tolist())
            paged = _paged_ticker_keyboard("oi_detail", tks, page=page, per_page=12, cols=3, include_back=False)
            rows = [list(r) for r in paged.inline_keyboard]
            rows.append([InlineKeyboardButton("🔄 Refresh", callback_data="menu_oi"), BACK_BTN])
            await query.message.reply_text("Select ticker page:", parse_mode=H, reply_markup=InlineKeyboardMarkup(rows))
        elif data.startswith("oi_detail_"):
            ticker = data.replace("oi_detail_", "")
            await oi_detail(query, ticker)
        elif data == "menu_mirofish":
            await mirofish_menu(query)
        elif data == "menu_scanner":
            await scanner_menu(query)
        elif data.startswith("miro_pos_"):
            tid = _safe_int(data.replace("miro_pos_", ""), 0)
            await mirofish_position_detail(query, tid)
        elif data.startswith("miro_ticker_"):
            tk = data.replace("miro_ticker_", "")
            await mirofish_ticker_detail(query, tk)
        elif data.startswith("high_prob_"):
            tk = data.replace("high_prob_", "")
            await high_prob_detail(query, tk)
        elif data.startswith("oi_roll_"):
            tk = data.replace("oi_roll_", "")
            await oi_roll_detail(query, tk)
        elif data.startswith("inst_sig_"):
            tk = data.replace("inst_sig_", "")
            await inst_signals_detail(query, tk)
        elif data.startswith("mean_rev_"):
            tk = data.replace("mean_rev_", "")
            await mean_rev_detail(query, tk)
        elif data.startswith("tech_sig_"):
            tk = data.replace("tech_sig_", "")
            await tech_signals_detail(query, tk)
        # ── Group / Strategy Builder ──────────────────────────────
        elif data == "menu_groups":
            await groups_menu(query)
        elif data == "menu_strategy_builder":
            await grp_strategy_menu(query)
        elif data == "grp_strategy_menu":
            await grp_strategy_menu(query)
        elif data.startswith("grpstrat_"):
            strat_key = data.replace("grpstrat_", "")
            await grp_strategy_ticker(query, ctx, strat_key)
        elif data.startswith("grptk_page_"):
            page = int(data.split("_")[-1])
            st = ctx.user_data.get("grpwiz", {})
            strat_key = st.get("strat_key", "custom")
            tickers = _ticker_universe(limit=1000)
            kb = _paged_ticker_keyboard("grptk", tickers, page=page, per_page=12, cols=3,
                                        include_back=True, back_cb="grp_strategy_menu")
            _grp_tmpl_name = STRATEGY_TEMPLATES[strat_key]["name"] if strat_key in STRATEGY_TEMPLATES else "Group Trade"
            await query.message.reply_text(
                f"{hdr(f'📦 {_grp_tmpl_name}')}\n\nStep 1: Select underlying ticker",
                parse_mode=H, reply_markup=kb
            )
        elif data.startswith("grptk_"):
            tk = data.replace("grptk_", "")
            st = ctx.user_data.get("grpwiz", {})
            st["ticker"] = tk
            ctx.user_data["grpwiz"] = st
            await grp_leg_expiry(query, ctx)
        elif data.startswith("grpexp_"):
            idx = _safe_int(data.replace("grpexp_", ""), 0)
            st = ctx.user_data.get("grpwiz", {})
            exps = st.get("expiries", [])
            if idx < len(exps):
                st["current_exp"] = exps[idx]
                ctx.user_data["grpwiz"] = st
            await grp_leg_strike(query, ctx)
        elif data.startswith("grpsk_"):
            idx = _safe_int(data.replace("grpsk_", ""), 0)
            st = ctx.user_data.get("grpwiz", {})
            strikes = st.get("strikes", [])
            if idx < len(strikes):
                st["current_strike"] = strikes[idx]
                ctx.user_data["grpwiz"] = st
            await grp_leg_confirm(query, ctx)
        elif data == "grp_next_leg":
            await grp_leg_expiry(query, ctx)
        elif data == "grp_add_custom_leg":
            # Add a free-form extra leg using call_spread template style but untyped
            st = ctx.user_data.get("grpwiz", {})
            st["legs_template"].append({"opt": "call", "side": "buy", "skoff": 0, "note": f"Custom leg {st['current_leg']+1}"})
            ctx.user_data["grpwiz"] = st
            await grp_leg_expiry(query, ctx)
        elif data == "grp_save_all":
            await grp_save_all(query, ctx)
        elif data.startswith("grpchart_"):
            gid = _safe_int(data.replace("grpchart_", ""), 0)
            await grp_chart(query, gid)
        elif data.startswith("grpadd_"):
            # Add another leg to existing group — reuse wizard with existing group
            gid = _safe_int(data.replace("grpadd_", ""), 0)
            ctx.user_data["grpwiz"] = {
                "strat_key": "custom", "strat_name": f"Group #{gid}",
                "legs_template": [{"opt": "call", "side": "buy", "skoff": 0, "note": "New leg"}],
                "legs_done": [], "current_leg": 0, "existing_group_id": gid
            }
            await grp_leg_expiry(query, ctx)
        elif data.startswith("grpdel_"):
            gid = _safe_int(data.replace("grpdel_", ""), 0)
            conn = get_conn()
            try:
                conn.execute("UPDATE trades SET group_id=NULL WHERE group_id=?", (gid,))
                conn.commit()
                await query.message.reply_text(
                    f"✅ Group #{gid} dissolved (trades kept as individual).",
                    reply_markup=InlineKeyboardMarkup([[
                        InlineKeyboardButton("📦 Groups", callback_data="menu_groups"), BACK_BTN
                    ]])
                )
            except Exception as e:
                await query.message.reply_text(f"❌ Error dissolving group: {e}")
            finally:
                conn.close()
        elif data.startswith("grp_"):
            gid = _safe_int(data.replace("grp_", ""), 0)
            await group_detail(query, gid)
        elif data == "menu_signals":
            await signal_scanner(query)
        elif data == "menu_insider":
            await insider_menu(query)
        elif data == "menu_more":
            await more_features_menu(query)
        elif data == "menu_streamlit_link":
            local_url = "http://localhost:8502"
            lan_url = f"http://{get_local_lan_ip()}:8502"
            await query.message.reply_text(
                f"{hdr('🖥 STREAMLIT DASHBOARD')}\n\n"
                f"Open in browser (same WiFi):\n\n"
                f"• On your <b>PC</b>: <code>{local_url}</code>\n"
                f"• On your <b>phone</b>: <code>{lan_url}</code>\n\n"
                f"<b>Pages:</b>\n"
                f"🌍 Market Overview · 🔬 OI Charts\n"
                f"🔥 OI Analytics · 💼 Portfolio\n"
                f"⚡ Trade Risk Calc · 🎯 Exit Planner\n\n"
                f"<i>If not running, it will auto-launch with the bot.</i>",
                parse_mode=H,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🌐 Open Dashboard", url=lan_url)],
                    [BACK_BTN]
                ])
            )
        elif data == "menu_analytics":
            await market_analytics_report(query)
        elif data == "menu_global_market":
            await global_market_view(query)
        elif data == "menu_nyse_report":
            await nyse_daily_report_menu(query)
        elif data == "nyse_report_top10":
            await generate_nyse_report(query, max_symbols=10)
        elif data == "nyse_report_top20":
            await generate_nyse_report(query, max_symbols=20)
        elif data == "nyse_report_all":
            await generate_nyse_report(query, max_symbols=999)
        elif data == "menu_prop":
            await prop_trading_view(query)
        elif data == "menu_backtest":
            await backtest_lab_view(query)
        elif data == "menu_livepred":
            await live_predictor_view(query)
        elif data == "menu_whales":
            await whales_view(query)
        elif data == "insider_congress":
            await congress_trades(query)
        elif data == "insider_insider":
            await insider_trades(query)
        elif data == "insider_shorts":
            await short_sellers_view(query)
        elif data == "menu_quote":
            await quote_menu(query)
        elif data.startswith("quote_page_"):
            page = int(data.split("_")[-1])
            tickers = _ticker_universe()
            kb = _paged_ticker_keyboard("quote", tickers, page=page, per_page=12, cols=3, include_back=True, back_cb="menu_quote")
            await query.message.reply_text(f"{hdr('⚡ QUICK QUOTE')}\n\nSelect a ticker:", parse_mode=H, reply_markup=kb)
        elif data.startswith("quote_"):
            ticker = data.replace("quote_", "")
            await quick_quote(query, ticker)
        # ── New features ────────────────────────────────────────────
        elif data == "menu_closed_analytics":
            await closed_positions_analytics(query)
        elif data == "menu_overnight_risk":
            await overnight_risk_report(query)
        elif data == "menu_aftermarket_predict":
            await aftermarket_predict(query)
        elif data.startswith("ah_pred_tk|"):
            await aftermarket_predict(query, ticker=data.split("|", 1)[1])
        elif data == "menu_ai_chat":
            await ai_chat_menu(query)
        elif data == "noop":
            await query.answer()   # section divider buttons — do nothing
    except Exception as e:
        log.error(f"Button handler error: {e}")
        try:
            await query.message.reply_text(f"❌ Error: {e}",
                                           reply_markup=InlineKeyboardMarkup([[BACK_BTN]]))
        except Exception:
            pass

# ═══════════════════════════════════════════════════════════
#  SCHEDULED MORNING ALERT
# ═══════════════════════════════════════════════════════════
async def morning_alert(ctx: ContextTypes.DEFAULT_TYPE):
    """Sends automatic morning briefing at 9:00 AM ET"""
    _, chat_id = load_creds()
    parts = [hdr("☀️ MORNING BRIEFING")]

    # Market overview — <pre> table: ST | Name | Price | Chg%
    _mkt_specs = [("ES", "ES=F"), ("NQ", "NQ=F"), ("VIX", "^VIX"),
                  ("Gold", "GC=F"), ("Oil", "CL=F")]
    _mhdr = ["ST", "Name", "Price", "Chg%"]
    _mrows = []
    for short, sym in _mkt_specs:
        try:
            h = yf.Ticker(sym).history(period="5d")
            if len(h) >= 2:
                px  = float(h["Close"].iloc[-1])
                pct = (px - float(h["Close"].iloc[-2])) / float(h["Close"].iloc[-2]) * 100
                st_m = "[+]" if pct > 0.5 else ("[!]" if pct < -0.5 else "[ ]")
                px_s = f"{px:,.2f}" if px < 1000 else f"{px:,.0f}"
                _mrows.append([st_m, short, px_s, f"{pct:+.2f}%"])
        except Exception:
            pass
    if _mrows:
        _mfw = [max(len(_mhdr[i]), max(len(r[i]) for r in _mrows)) for i in range(4)]
        _mj  = lambda i, v: v.rjust(_mfw[i]) if i in {2, 3} else v.ljust(_mfw[i])
        _msep = "-+-".join("-" * w for w in _mfw)
        _ml  = [" | ".join(_mj(i, _mhdr[i]) for i in range(4)), _msep]
        for r in _mrows:
            _ml.append(" | ".join(_mj(i, r[i]) for i in range(4)))
        parts.append("<pre>" + "\n".join(_ml) + "</pre>")

    # Open positions check
    conn = get_conn()
    open_trades = pd.read_sql("SELECT * FROM trades WHERE status='OPEN'", conn)
    conn.close()

    if not open_trades.empty:
        _phdr  = ["ST", "Tkr", "Side", "Stk", "P&L%"]
        _prows = []
        for _, tr in open_trades.iterrows():
            ot  = str(tr.get("option_type", "?"))[:3].upper()
            qty = int(tr.get("quantity", 1) or 1)
            side_s = "B" + ot if qty >= 0 else "S" + ot
            stk = float(tr.get("strike", 0) or 0)
            ep  = float(tr.get("entry_price", 0) or 0)
            try:
                _h5 = yf.Ticker(tr["ticker"]).history(period="7d")
                spot = float(_h5["Close"].iloc[-1])
                spot_prev = float(_h5["Close"].iloc[0])
                stock_ret = (spot - spot_prev) / spot_prev * 100 if spot_prev > 0 else 0
                # delta-neutral estimate: calls gain with rising stock, puts with falling
                pnl_pct = stock_ret * 0.5 if not ot.startswith("PUT") else -stock_ret * 0.5
                st_p = "[+]" if pnl_pct > 0 else "[!]"
                pnl_s = f"~{pnl_pct:+.1f}%"
            except Exception:
                st_p = "[ ]"; pnl_s = "N/A"
            _prows.append([st_p, str(tr.get("ticker","?"))[:6], side_s, f"{stk:.0f}", pnl_s])
        if _prows:
            _pfw = [max(len(_phdr[i]), max(len(r[i]) for r in _prows)) for i in range(5)]
            _pj  = lambda i, v: v.rjust(_pfw[i]) if i in {3, 4} else v.ljust(_pfw[i])
            _psep = "-+-".join("-" * w for w in _pfw)
            _pl  = ["POSITIONS", " | ".join(_pj(i, _phdr[i]) for i in range(5)), _psep]
            for r in _prows:
                _pl.append(" | ".join(_pj(i, r[i]) for i in range(5)))
            parts.append("<pre>" + "\n".join(_pl) + "</pre>")

    # ── Fear & Greed + Sector Rotation ─────────────────────────────
    try:
        _ma_fg = 50
        _ma_vix = 20.0
        try:
            _ma_vh = yf.Ticker("^VIX").history(period="5d")
            _ma_vix = float(_ma_vh["Close"].iloc[-1]) if len(_ma_vh) >= 1 else 20.0
            _ma_fg += max(-25, min(25, int((20 - _ma_vix) * 2.5)))
        except Exception: pass
        try:
            _ma_sh = yf.Ticker("SPY").history(period="7d")
            if len(_ma_sh) >= 3:
                _ma_mkt = (float(_ma_sh["Close"].iloc[-1]) - float(_ma_sh["Close"].iloc[-3])) / float(_ma_sh["Close"].iloc[-3]) * 100
                _ma_fg += max(-20, min(20, int(_ma_mkt * 5)))
        except Exception: pass
        _ma_fg = max(0, min(100, _ma_fg))
        if _ma_fg >= 75:   _ma_fg_lb = "EXTREME GREED 🤑"
        elif _ma_fg >= 55: _ma_fg_lb = "GREED 😀"
        elif _ma_fg >= 45: _ma_fg_lb = "NEUTRAL 😐"
        elif _ma_fg >= 25: _ma_fg_lb = "FEAR 😨"
        else:              _ma_fg_lb = "EXTREME FEAR 😱"
        _ma_bar = "█" * (_ma_fg // 10) + "░" * (10 - _ma_fg // 10)
        _fg_ico = "🤑" if _ma_fg >= 75 else ("😀" if _ma_fg >= 55 else ("😐" if _ma_fg >= 45 else ("😨" if _ma_fg >= 25 else "😱")))
        parts.append(f"\n{_fg_ico} <b>Fear/Greed:</b> {_ma_fg}/100 — {_ma_fg_lb}\n   <code>{_ma_bar}</code>")
        parts.append(f"🌡 <b>VIX:</b> {_ma_vix:.1f}  {'EXTREME FEAR' if _ma_vix > 30 else 'HIGH FEAR' if _ma_vix > 25 else 'ELEVATED' if _ma_vix > 20 else 'CALM'}")
    except Exception: pass

    try:
        _ma_secs = [("XLK","Tech"),("XLF","Finl"),("XLE","Engy"),("XLV","Hlth"),("XLI","Inds")]
        _ma_sp = []
        for _ms, _ml in _ma_secs:
            _msh = yf.Ticker(_ms).history(period="5d")
            if len(_msh) >= 2:
                _mp = (float(_msh["Close"].iloc[-1]) - float(_msh["Close"].iloc[-2])) / float(_msh["Close"].iloc[-2]) * 100
                _ma_sp.append((_ml, _mp))
        if len(_ma_sp) >= 2:
            _ma_sp.sort(key=lambda x: x[1], reverse=True)
            _top2 = _ma_sp[:2]; _bot2 = _ma_sp[-2:]
            parts.append(f"🏆 <b>Leading:</b> {_top2[0][0]} {_top2[0][1]:+.1f}%  {_top2[1][0]} {_top2[1][1]:+.1f}%")
            parts.append(f"⬇ <b>Lagging:</b> {_bot2[-1][0]} {_bot2[-1][1]:+.1f}%  {_bot2[-2][0]} {_bot2[-2][1]:+.1f}%")
        elif len(_ma_sp) == 1:
            parts.append(f"🏆 <b>Sector:</b> {_ma_sp[0][0]} {_ma_sp[0][1]:+.1f}%")
    except Exception: pass

    # ── Macro Calendar — key weekly indicators with trade impact ───
    try:
        import urllib.request, json as _json
        _today_w = datetime.now().weekday()  # 0=Mon … 4=Fri
        # Economic events published weekly/bi-weekly — use FRED-style proxy via yfinance proxies
        # We fetch current-week proxies to derive direction signals
        _macro_events = []

        # Jobless Claims proxy — ^IRX (13-week T-Bill) as stress proxy
        try:
            _jc_h = yf.Ticker("^IRX").history(period="10d")
            if len(_jc_h) >= 2:
                _jc_chg = float(_jc_h["Close"].iloc[-1]) - float(_jc_h["Close"].iloc[-2])
                _jc_sig = "Def↑" if _jc_chg < -0.05 else ("Grwth↑" if _jc_chg > 0.05 else "Stable")
                _macro_events.append(("Claims", f"{float(_jc_h['Close'].iloc[-1]):.2f}%", _jc_sig))
        except Exception: pass

        # DXY — dollar index
        try:
            _dxy_h = yf.Ticker("DX=F").history(period="5d")
            if len(_dxy_h) >= 2:
                _dxy_v = float(_dxy_h["Close"].iloc[-1])
                _dxy_c = (_dxy_v - float(_dxy_h["Close"].iloc[-2])) / float(_dxy_h["Close"].iloc[-2]) * 100
                _dxy_sig = ("EM/Au↓" if _dxy_c > 0.3 else ("EM/Au↑" if _dxy_c < -0.3 else "Neutral"))
                _macro_events.append(("DXY", f"{_dxy_v:.1f}{_dxy_c:+.1f}%", _dxy_sig))
        except Exception: pass

        # 10Y yield
        try:
            _tnx_h = yf.Ticker("^TNX").history(period="5d")
            if len(_tnx_h) >= 2:
                _tnx_v = float(_tnx_h["Close"].iloc[-1])
                _tnx_c = _tnx_v - float(_tnx_h["Close"].iloc[-2])
                _tnx_sig = ("Bnk↑REIT↓" if _tnx_c > 0.05 else ("REIT↑Bnk↓" if _tnx_c < -0.05 else "Stable"))
                _macro_events.append(("10Y", f"{_tnx_v:.2f}%{_tnx_c:+.2f}", _tnx_sig))
        except Exception: pass

        # Crude Oil
        try:
            _oil_h = yf.Ticker("CL=F").history(period="5d")
            if len(_oil_h) >= 2:
                _oil_v = float(_oil_h["Close"].iloc[-1])
                _oil_c = (_oil_v - float(_oil_h["Close"].iloc[-2])) / float(_oil_h["Close"].iloc[-2]) * 100
                _oil_sig = ("XLE↑DAL↓" if _oil_c > 1.5 else ("DAL↑XLE↓" if _oil_c < -1.5 else "Neutral"))
                _macro_events.append(("Oil", f"${_oil_v:.1f}{_oil_c:+.1f}%", _oil_sig))
        except Exception: pass

        # VIX vol regime
        try:
            _vix_h = yf.Ticker("^VIX").history(period="5d")
            if len(_vix_h) >= 1:
                _vix_v = float(_vix_h["Close"].iloc[-1])
                _vix_sig = ("BuyDips" if _vix_v > 30 else ("Hedge" if _vix_v > 20 else "SellPrm"))
                _macro_events.append(("VIX", f"{_vix_v:.1f}", _vix_sig))
        except Exception: pass

        if _macro_events:
            # Compact 3-col table: Ind(7) | Val(9) | Signal(9) = ~30 chars total
            _mhdr2 = ["Ind", "Val", "Signal"]
            _mfw2 = [
                max(len(_mhdr2[0]), max(len(r[0]) for r in _macro_events)),
                max(len(_mhdr2[1]), max(len(r[1]) for r in _macro_events)),
                max(len(_mhdr2[2]), max(len(r[2]) for r in _macro_events)),
            ]
            _mj2 = lambda i, v: v.ljust(_mfw2[i]) if i == 0 else v.rjust(_mfw2[i]) if i == 1 else v.ljust(_mfw2[i])
            _msep2 = "-+-".join("-" * w for w in _mfw2)
            _ml2 = ["MACRO SIGNALS",
                    " | ".join(_mj2(i, _mhdr2[i]) for i in range(3)), _msep2]
            for r in _macro_events:
                _ml2.append(" | ".join(_mj2(i, r[i]) for i in range(3)))
            parts.append("<pre>" + "\n".join(_ml2) + "</pre>")
    except Exception as _mce:
        log.warning(f"morning macro section failed: {_mce}")

    # ── High-Prob Engine per open position ───────────────────────────────
    _SIG_ICON_MA = {"BULL":"🟢","BEAR":"🔴","NEUTRAL":"⚪","SELL_PREMIUM":"💰"}
    try:
        conn_hp_ma = get_conn()
        _hp_tks = pd.read_sql("SELECT DISTINCT ticker FROM trades WHERE status='OPEN'", conn_hp_ma)
        _hp_out = []
        for _htk in _hp_tks["ticker"].tolist()[:5]:
            try:
                _sp_ma = _get_spot_with_ah(str(_htk).upper()).get("spot_ext", 0.0)
                _hr = high_prob_signals_engine(str(_htk).upper(), conn_hp_ma, _sp_ma)
                _he = _SIG_ICON_MA.get(_hr["signal"], "⚪")
                _cf = _hr.get("confidence", "")
                _pb = _hr["prob"]
                _bv = _hr["bull_v"]; _rv = _hr["bear_v"]; _sv = _hr.get("sell_v", 0)
                _hp_out.append(f"<b>{_htk}</b> ${_sp_ma:.0f}")
                _hp_out.append(f"{_he} {_hr['signal']}  {_pb:.0f}%  {_cf}")
                _hp_out.append(f"🟢{_bv} 🔴{_rv} 💰{_sv}/24")
                _vb = _hr.get("vrvp_box", {})
                if _vb.get("lo"):
                    _hp_out.append(f"📦 ${_vb['lo']:.0f}–${_vb['hi']:.0f} POC${_vb.get('poc',0):.0f}")
                _wl = _hr.get("models", {}).get("put_call_wall", {})
                if _wl.get("call_wall") and _wl.get("prob", 0) >= 65:
                    _hp_out.append(f"🧱 P${_wl['put_wall']:.0f} C${_wl['call_wall']:.0f}")
                _hp_out.append("")
            except Exception as _e_hp:
                log.debug(f"ma hp {_htk}: {_e_hp}")
        if _hp_out:
            parts.append("\n<b>🧠 HP Engine — Positions:</b>")
            parts.extend(_hp_out)
        conn_hp_ma.close()
    except Exception as _e_hp_ma: log.debug(f"ma hp block: {_e_hp_ma}")

    parts.append(f"\n<i>Sent: {datetime.now().strftime('%Y-%m-%d %H:%M')}</i>")
    parts.append("Tap /start for full menu")

    await ctx.bot.send_message(chat_id=chat_id, text="\n".join(parts), parse_mode=H)

# ═══════════════════════════════════════════════════════════
#  TABLE → IMAGE helper
# ═══════════════════════════════════════════════════════════
import io
def _tbl_img(title: str, headers: list, rows: list,
             right_cols: set = None, highlight: dict = None,
             subtitle: str = "") -> "io.BytesIO":
    """Render a data table as a PNG image.
    Returns BytesIO ready for reply_photo().
    highlight: {row_index: 'green'|'red'|'yellow'} for colour-coding rows.
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.patches as mpatches

    right_cols = right_cols or set()
    highlight  = highlight  or {}

    n_rows = len(rows)
    n_cols = len(headers)
    row_h  = 0.38          # inches per row
    fig_h  = max(2.2, row_h * (n_rows + 2.5))
    fig_w  = max(5, n_cols * 1.4)

    fig, ax = plt.subplots(figsize=(fig_w, fig_h))
    ax.axis("off")
    fig.patch.set_facecolor("#0d1117")

    # col widths proportional to max content
    col_w = []
    for i in range(n_cols):
        mx = max(len(str(headers[i])), max((len(str(r[i])) for r in rows), default=0))
        col_w.append(max(mx, 3))
    total_w = sum(col_w)
    col_widths = [w / total_w for w in col_w]

    # escape $ so matplotlib doesn't treat them as LaTeX math delimiters
    def _mpl(s): return str(s).replace('$', r'\$')
    safe_rows    = [[_mpl(c) for c in r] for r in rows]
    safe_headers = [_mpl(h) for h in headers]
    safe_title   = _mpl(title)
    safe_subtitle = _mpl(subtitle) if subtitle else ""

    # draw table
    tbl = ax.table(
        cellText   = safe_rows,
        colLabels  = safe_headers,
        loc        = "center",
        cellLoc    = "center",
        colWidths  = col_widths,
    )
    tbl.auto_set_font_size(False)
    tbl.set_fontsize(9)
    tbl.scale(1.0, 1.6)

    # header style
    for j in range(n_cols):
        cell = tbl[0, j]
        cell.set_facecolor("#1f6feb")
        cell.set_text_props(color="white", fontweight="bold")
        cell.set_edgecolor("#30363d")

    # row styles
    row_colors = {"green": "#0d3320", "red": "#3d0d0d", "yellow": "#2d2700",
                  "blue": "#0d1f33", "default0": "#161b22", "default1": "#0d1117"}
    for i, row in enumerate(rows):
        hl  = highlight.get(i)
        bg  = row_colors.get(hl, row_colors[f"default{i % 2}"])
        for j in range(n_cols):
            cell = tbl[i + 1, j]
            cell.set_facecolor(bg)
            cell.set_edgecolor("#30363d")
            align = "right" if j in right_cols else "left"
            cell.set_text_props(
                color="#e6edf3", ha=align,
                fontweight="bold" if hl else "normal"
            )

    ax.set_title(safe_title, color="white", fontsize=11, fontweight="bold", pad=8)
    if safe_subtitle:
        ax.text(0.5, 0.01, safe_subtitle, transform=ax.transAxes,
                color="#8b949e", ha="center", fontsize=8)

    buf = BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight", dpi=130,
                facecolor=fig.get_facecolor())
    buf.seek(0)
    plt.close(fig)
    return buf


# ═══════════════════════════════════════════════════════════
#  CLOSED POSITION ANALYTICS
# ═══════════════════════════════════════════════════════════
async def closed_positions_analytics(query):
    """Analytics for closed/sold positions — rendered as image. (Dashboard code unified)"""
    _loading = await query.message.reply_text("📈 Analysing closed positions…", parse_mode=H)
    conn = get_conn()
    try:
        trades = pd.read_sql(
            "SELECT * FROM trades WHERE status='CLOSED' ORDER BY rowid DESC LIMIT 100", conn)
    except Exception:
        trades = pd.DataFrame()
    conn.close()

    if trades.empty:
        await query.message.reply_text(
            f"{hdr('📈 CLOSED POSITIONS')}\n\nNo closed positions found.",
            parse_mode=H, reply_markup=InlineKeyboardMarkup([[BACK_BTN]]))
        try: await _loading.delete()
        except Exception: pass
        return

    # ── Enrich with P&L ───────────────────────────────────────────
    rows_data = []
    total_pnl  = 0.0
    wins = losses = 0

    for _, tr in trades.iterrows():
        tk    = str(tr.get("ticker", "?"))
        ot    = str(tr.get("option_type", "?")).upper()[:4]
        strk  = _safe_float(tr.get("strike", 0), 0)
        entry = _safe_float(tr.get("entry_price", 0), 0)
        qty   = _safe_int(tr.get("quantity", 0), 0)
        close_px = _safe_float(tr.get("close_price") if "close_price" in tr.index else 0, 0)
        if close_px == 0:
            # fallback: exit_price or last recorded price
            close_px = _safe_float(tr.get("exit_price") if "exit_price" in tr.index else 0, 0)

        cost  = entry * abs(qty) * 100
        proceeds = close_px * abs(qty) * 100
        pnl   = (proceeds - cost) if qty > 0 else (cost - proceeds)
        pnl_pct = (pnl / cost * 100) if cost > 0 else 0
        total_pnl += pnl

        hold_days = "?"
        try:
            created = str(tr.get("created_at", ""))[:10]
            updated = str(tr.get("updated_at", ""))[:10]
            if created and updated and created != "?":
                d1 = datetime.strptime(created[:10], "%Y-%m-%d")
                d2 = datetime.strptime(updated[:10], "%Y-%m-%d")
                hold_days = str((d2 - d1).days) + "d"
        except Exception:
            pass

        if pnl >= 0: wins   += 1
        else:         losses += 1

        rows_data.append({
            "tk": tk, "ot": ot, "strike": strk, "entry": entry,
            "close": close_px, "pnl": pnl, "pnl_pct": pnl_pct,
            "hold": hold_days, "qty": qty
        })

    total_trades = len(rows_data)
    win_rate     = wins / total_trades * 100 if total_trades > 0 else 0
    avg_win      = sum(r["pnl"] for r in rows_data if r["pnl"] >= 0) / max(wins, 1)
    avg_loss     = sum(r["pnl"] for r in rows_data if r["pnl"] < 0)  / max(losses, 1)
    rr_ratio     = abs(avg_win / avg_loss) if avg_loss != 0 else 0

    # ── Image 1: Per-trade table ──────────────────────────────────
    tbl_headers = ["Tkr", "Type", "Strk", "Entry", "Exit", "P&L", "P&L%", "Hold"]
    tbl_rows    = []
    highlight   = {}
    for i, r in enumerate(rows_data[:20]):   # cap at 20 for image height
        tbl_rows.append([
            r["tk"], r["ot"], f"{r['strike']:.0f}",
            f"${r['entry']:.2f}", f"${r['close']:.2f}",
            f"${r['pnl']:+,.0f}", f"{r['pnl_pct']:+.1f}%",
            r["hold"]
        ])
        highlight[i] = "green" if r["pnl"] >= 0 else "red"

    subtitle = (f"Total P&L: ${total_pnl:+,.0f}  |  "
                f"Win Rate: {win_rate:.0f}%  |  "
                f"Trades: {total_trades}  |  "
                f"R:R = {rr_ratio:.1f}x")
    buf1 = _tbl_img(
        f"CLOSED POSITIONS — {datetime.now().strftime('%Y-%m-%d')}",
        tbl_headers, tbl_rows,
        right_cols={2, 3, 4, 5, 6},
        highlight=highlight,
        subtitle=subtitle
    )

    # ── Image 2: P&L by ticker summary ────────────────────────────
    by_tk = {}
    for r in rows_data:
        by_tk.setdefault(r["tk"], {"pnl": 0, "n": 0, "wins": 0})
        by_tk[r["tk"]]["pnl"]  += r["pnl"]
        by_tk[r["tk"]]["n"]    += 1
        by_tk[r["tk"]]["wins"] += 1 if r["pnl"] >= 0 else 0

    summary_rows = []
    sum_hl       = {}
    for i, (tk, v) in enumerate(sorted(by_tk.items(), key=lambda x: -x[1]["pnl"])):
        wr = v["wins"] / v["n"] * 100
        summary_rows.append([tk, str(v["n"]), f"${v['pnl']:+,.0f}", f"{wr:.0f}%"])
        sum_hl[i] = "green" if v["pnl"] >= 0 else "red"

    buf2 = _tbl_img(
        "P&L BY TICKER",
        ["Ticker", "Trades", "Net P&L", "Win%"],
        summary_rows,
        right_cols={1, 2, 3},
        highlight=sum_hl
    )

    # ── Text summary card ─────────────────────────────────────────
    em_pnl  = "🟢" if total_pnl >= 0 else "🔴"
    summary = (
        f"{hdr('📈 CLOSED POSITION ANALYTICS')}\n\n"
        f"{em_pnl} <b>Total Realized P&L: ${total_pnl:+,.0f}</b>\n\n"
        f"🏆 <b>Win Rate:</b>  {win_rate:.0f}%  ({wins}W / {losses}L)\n"
        f"📊 <b>Avg Win:</b>   ${avg_win:+,.0f}\n"
        f"📉 <b>Avg Loss:</b>  ${avg_loss:+,.0f}\n"
        f"⚖️ <b>R:R Ratio:</b> {rr_ratio:.2f}x\n"
        f"📋 <b>Trades Closed:</b> {total_trades}"
    )

    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("⚠️ Overnight Risk", callback_data="menu_overnight_risk"),
        InlineKeyboardButton("💼 Open Positions", callback_data="menu_positions"),
        BACK_BTN
    ]])

    await query.message.reply_text(summary, parse_mode=H)
    await query.message.reply_photo(buf1, caption="Per-trade breakdown")
    await query.message.reply_photo(buf2, caption="P&L by ticker", reply_markup=kb)
    try: await _loading.delete()
    except Exception: pass


# ═══════════════════════════════════════════════════════════
#  OVERNIGHT RISK REPORT
# ═══════════════════════════════════════════════════════════
async def overnight_risk_report(query):
    """Overnight risk analysis for all open positions — rendered as image."""
    _loading = await query.message.reply_text("⚠️ Calculating overnight risk…", parse_mode=H)
    conn = get_conn()
    try:
        trades = pd.read_sql("SELECT * FROM trades WHERE status='OPEN'", conn)
    except Exception:
        trades = pd.DataFrame()
    conn.close()

    if trades.empty:
        await query.message.reply_text(
            f"{hdr('⚠️ OVERNIGHT RISK')}\n\nNo open positions.",
            parse_mode=H, reply_markup=InlineKeyboardMarkup([[BACK_BTN]]))
        try: await _loading.delete()
        except Exception: pass
        return

    # ── Fetch spot prices + VIX ───────────────────────────────────
    try:
        vix_h   = yf.Ticker("^VIX").history(period="5d")
        vix_val = float(vix_h["Close"].iloc[-1]) if len(vix_h) >= 1 else 20.0
    except Exception:
        vix_val = 20.0

    iv_base = vix_val / 100 * 1.2   # approximate IV from VIX

    r_rate = 0.045
    total_theta_day  = 0.0
    total_delta_1pct = 0.0
    total_value      = 0.0
    risk_rows = []   # (tk, type, strk, dte, spot_tag, theta_d, delta_1, risk_lvl, pnl_pct)

    for idx, tr in trades.iterrows():
        tk    = str(tr.get("ticker", "?"))
        ot    = str(tr.get("option_type", "?")).upper()
        strk  = _safe_float(tr.get("strike", 0), 0)
        entry = _safe_float(tr.get("entry_price", 0), 0)
        qty   = _safe_int(tr.get("quantity", 1), 1)
        exp_s = str(tr.get("expiry", ""))[:10]
        try:
            dte = max((datetime.strptime(exp_s, "%Y-%m-%d").date() - datetime.now().date()).days, 1)
        except Exception:
            dte = 30

        px = _get_spot_with_ah(tk)
        spot_reg = px["spot_reg"] if px["spot_reg"] > 0 else strk
        spot_ext = px["spot_ext"] if px["spot_ext"] > 0 else spot_reg
        spot     = spot_ext
        ah_tag   = f"AH:{spot_ext:.0f}" if px["is_extended"] else f"${spot_reg:.0f}"

        T      = max(dte, 1) / 365.0
        opt_lc = ot.lower() if ot.lower() in ("call", "put") else "put"
        greeks = bs_greeks(spot, strk, T, r_rate, iv_base, opt=opt_lc)
        theo   = bs_price(spot, strk, T, r_rate, iv_base, opt=opt_lc)

        delta = greeks.get("delta", 0)
        theta = greeks.get("theta", 0)
        contracts = abs(qty)
        pos_sign  = 1 if qty > 0 else -1
        side_s    = "S" if qty < 0 else "B"

        theta_day   = theta * 100 * contracts * pos_sign
        delta_1pct  = delta * spot * 0.01 * 100 * contracts * pos_sign
        pos_value   = theo * 100 * contracts

        total_theta_day   += theta_day
        total_delta_1pct  += delta_1pct
        total_value       += pos_value * pos_sign

        pnl_pct  = (theo - entry) / entry * 100 * pos_sign if entry > 0 else 0
        risk_lvl = "HIGH" if dte <= 3 or pnl_pct < -40 else ("MED" if dte <= 7 else "LOW")
        risk_rows.append((tk, f"{ot[:4]}{side_s}", strk, dte, ah_tag,
                          theta_day, delta_1pct, risk_lvl))

    gap_dn = total_delta_1pct * -2
    gap_up = total_delta_1pct *  2

    # ── Portfolio summary (HTML bold, no table) ───────────────────
    vix_em   = "🔴" if vix_val > 25 else ("🟡" if vix_val > 18 else "🟢")
    theta_em = "🔴" if total_theta_day < -50 else "🟡"
    delta_em = "🟢" if total_delta_1pct > 0 else "🔴"

    summary = (
        f"<b>⚠️ OVERNIGHT RISK REPORT</b>  <i>{datetime.now().strftime('%H:%M ET')}</i>\n\n"
        f"{vix_em} VIX <b>{vix_val:.1f}</b>  "
        f"({'High Fear' if vix_val > 25 else 'Elevated' if vix_val > 18 else 'Calm'})\n"
        f"{theta_em} Theta tonight  <b>${total_theta_day:+,.0f}</b>\n"
        f"{delta_em} Delta (mkt+1%) <b>${total_delta_1pct:+,.0f}</b>\n"
        f"🔴 Gap-down 2%  <b>${gap_dn:+,.0f}</b>\n"
        f"🟢 Gap-up 2%    <b>${gap_up:+,.0f}</b>\n"
        f"💼 Portfolio    <b>${abs(total_value):,.0f}</b>\n\n"
        f"<i>AH/PM prices used where available</i>"
    )
    await query.message.reply_text(summary, parse_mode=H)

    # ── Flat <pre> table ──────────────────────────────────────────
    C = [5, 6, 6, 4, 8, 6, 6, 4]   # col widths
    def _cell(v, w, right=False):
        s = str(v)[:w]
        return s.rjust(w) if right else s.ljust(w)

    HDR = ("Tkr", "Type", "Strk", "DTE", "Spot", "Th/d", "D1%", "Risk")
    sep = "─" * (sum(C) + len(C) - 1)
    rows_pre = [
        "  ".join(_cell(h, w) for h, w in zip(HDR, C)),
        sep,
    ]
    for (tk2, typ, strk2, dte2, spot_tag, theta_d, delta_1, risk_lv) in risk_rows:
        rs = {"HIGH": "HI!", "MED": "MED", "LOW": "ok"}.get(risk_lv, "   ")
        rows_pre.append("  ".join([
            _cell(tk2,            C[0]),
            _cell(typ,            C[1]),
            _cell(f"${strk2:.0f}", C[2], right=True),
            _cell(f"{dte2}d",     C[3], right=True),
            _cell(spot_tag,       C[4], right=True),
            _cell(f"${theta_d:+.0f}", C[5], right=True),
            _cell(f"${delta_1:+.0f}", C[6], right=True),
            _cell(rs,             C[7]),
        ]))
    rows_pre += [
        sep,
        f"Theta: ${total_theta_day:+,.0f}   Delta+1%: ${total_delta_1pct:+,.0f}",
        "HI!=DTE<=3/PnL<=-40%  MED=DTE<=7",
    ]
    detail_msg = f"<b>📋 Position Detail</b>\n<pre>{chr(10).join(rows_pre)}</pre>"

    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("📡 Position Monitor", callback_data="menu_pos_monitor"),
        InlineKeyboardButton("🌙 AH Predictor", callback_data="menu_aftermarket_predict"),
        BACK_BTN
    ]])
    await query.message.reply_text(detail_msg, parse_mode=H, reply_markup=kb)
    try: await _loading.delete()
    except Exception: pass


# ═══════════════════════════════════════════════════════════
#  AFTER-MARKET PORTFOLIO OPTION PREDICTOR
# ═══════════════════════════════════════════════════════════
async def aftermarket_predict(query, ticker: str = None):
    """After-hours stock price → predict tomorrow option value. Grouped by ticker, text cards."""
    scope = f"{ticker} " if ticker else "all positions"
    _loading = await query.message.reply_text(
        f"🌙 Fetching AH prices for {scope}…", parse_mode="HTML")

    conn = get_conn()
    try:
        if ticker:
            trades = pd.read_sql(
                "SELECT * FROM trades WHERE status='OPEN' AND ticker=? ORDER BY ticker", conn, params=(ticker,))
        else:
            trades = pd.read_sql(
                "SELECT * FROM trades WHERE status='OPEN' ORDER BY ticker", conn)
    except Exception:
        trades = pd.DataFrame()
    conn.close()

    if trades.empty:
        await query.message.reply_text(
            "🌙 <b>AFTER-MARKET PREDICTOR</b>\n\nNo open positions.",
            parse_mode="HTML", reply_markup=InlineKeyboardMarkup([[BACK_BTN]]))
        try: await _loading.delete()
        except: pass
        return

    try:
        vix_h   = yf.Ticker("^VIX").history(period="5d")
        vix_val = float(vix_h["Close"].iloc[-1]) if len(vix_h) >= 1 else 20.0
    except Exception:
        vix_val = 20.0
    iv_base = vix_val / 100 * 1.3
    r_rate  = 0.045
    vix_em  = "🔴" if vix_val > 25 else ("🟡" if vix_val > 18 else "🟢")

    tickers_order = list(dict.fromkeys(trades["ticker"].astype(str).tolist()))
    portfolio_pnl_now   = 0.0
    portfolio_pnl_tmrw  = 0.0
    all_orders = []   # collect pre-mkt orders for final summary table

    for tk in tickers_order:
        grp = trades[trades["ticker"].astype(str) == tk]
        ev  = _get_event_risk(tk, vix_val=vix_val)

        px       = _get_spot_with_ah(tk)
        spot_reg = px["spot_reg"] if px["spot_reg"] > 0 else 0.0
        spot_ext = px["spot_ext"] if px["spot_ext"] > 0 else spot_reg
        ah_src   = px["ext_src"]
        ah_chg   = px["ext_chg_pct"]
        is_ext   = px["is_extended"]

        stock_em = "🟢" if ah_chg >= 0 else "🔴"
        ext_tag  = f"({ah_chg:+.1f}%)" if is_ext else "(EOD)"

        # IV adjustment for event proximity
        ev_mult = 1.25 if ev["has_event"] and (ev["event_days"] or 99) <= 3 else 1.0
        iv_pos  = iv_base * ev_mult

        tk_pnl_now  = 0.0
        tk_pnl_tmrw = 0.0
        leg_lines   = []
        order_lines = []

        for _, tr in grp.iterrows():
            ot    = str(tr.get("option_type", "")).lower()
            strk  = _safe_float(tr.get("strike", 0), 0)
            entry = _safe_float(tr.get("entry_price", 0), 0)
            qty   = _safe_int(tr.get("quantity", 1), 1)
            exp_s = str(tr.get("expiry", ""))[:10]
            try:
                dte = max((datetime.strptime(exp_s, "%Y-%m-%d").date() - datetime.now().date()).days, 1)
            except Exception:
                dte = 30

            T_now   = max(dte, 1) / 365.0
            T_tmrw  = max(dte - 1, 0.5) / 365.0
            opt_lc  = ot if ot in ("call", "put") else "put"
            pos_sign = 1 if qty > 0 else -1
            contracts = abs(qty)
            side_s    = "LONG" if qty > 0 else "SHORT"
            ot_s      = ot.upper()[:4]

            # Option price at EOD spot and at AH spot — both shown
            val_eod   = bs_price(spot_reg, strk, T_now,  r_rate, iv_pos, opt=opt_lc)
            val_ah    = bs_price(spot_ext, strk, T_now,  r_rate, iv_pos, opt=opt_lc)
            val_now   = val_ah if is_ext else val_eod   # "now" = AH-adjusted if available
            val_tmrw  = bs_price(spot_ext, strk, T_tmrw, r_rate, iv_pos, opt=opt_lc)
            val_post  = bs_price(spot_ext, strk, T_tmrw, r_rate, iv_pos * 0.70, opt=opt_lc) \
                        if ev["has_event"] and (ev["event_days"] or 99) <= 1 else None

            pnl_vs_entry = (val_now  - entry) * 100 * contracts * pos_sign
            pnl_tmrw_dol = (val_tmrw - entry) * 100 * contracts * pos_sign
            pnl_chg_pct  = (val_tmrw - val_now) / val_now * 100 * pos_sign if val_now > 0 else 0
            pnl_eod_dol  = (val_eod  - entry) * 100 * contracts * pos_sign  # EOD-based P&L for reference

            tk_pnl_now   += pnl_vs_entry
            tk_pnl_tmrw  += pnl_tmrw_dol
            portfolio_pnl_now  += pnl_vs_entry
            portfolio_pnl_tmrw += pnl_tmrw_dol

            # Pre-mkt order logic
            pnl_pct = (val_now - entry) / entry * 100 * pos_sign if entry > 0 else 0
            if dte <= 2:
                action = "CLOSE"
                limit  = round(val_tmrw * 0.95, 2)
                reason = f"Expiry in {dte}d"
            elif pnl_pct >= 50:
                action = "TAKE PROFIT"
                limit  = round(val_tmrw * 0.90, 2)
                reason = f"Up {pnl_pct:.0f}% vs entry"
            elif pnl_pct <= -40 or pnl_chg_pct <= -10:
                action = "STOP LOSS"
                limit  = round(max(val_tmrw * 1.05, entry * 0.55), 2)
                reason = f"Down {pnl_pct:.0f}%" if pnl_pct <= -40 else f"AH-{abs(pnl_chg_pct):.0f}%dn"
            elif pnl_chg_pct >= 8:
                action = "HOLD"
                limit  = None
                reason = f"+{pnl_chg_pct:.0f}% tmrw"
            else:
                action = "WATCH"
                limit  = None
                reason = "Neutral"

            crush_sfx = ""
            if val_post is not None:
                pnl_post = (val_post - entry) * 100 * contracts * pos_sign
                crush_sfx = f"crush→${val_post:.2f} P&L${pnl_post:+,.0f}"

            leg_lines.append((
                side_s, ot_s, strk, dte, entry,
                val_eod, val_ah, is_ext,
                pnl_eod_dol, pnl_vs_entry, pnl_pct,
                val_tmrw, pnl_chg_pct, action, limit, reason, crush_sfx
            ))
            all_orders.append({"tk": tk, "ot_s": ot_s, "strk": strk, "action": action,
                                "limit": limit, "pnl_pct": pnl_pct, "reason": reason})

        # Event risk line
        ev_line = f"\n{ev['iv_crush_warning']}" if ev.get("iv_crush_warning") else ""

        tk_pnl_em  = "🟢" if tk_pnl_now >= 0 else "🔴"
        tk_tmrw_em = "🟢" if tk_pnl_tmrw >= 0 else "🔴"

        # Short interest context for AH predictor
        try:
            _sd = _get_short_data(tk)
            _spf = _sd.get("short_pct_float")
            _sr  = _sd.get("short_ratio")
            _sc  = _sd.get("squeeze_score")
            _sq_parts = []
            if _spf is not None: _sq_parts.append(f"SI:{_spf:.1f}%")
            if _sr  is not None: _sq_parts.append(f"DTC:{_sr:.1f}d")
            if _sc  is not None:
                _sq_em = "🔴" if _sc >= 7 else ("🟡" if _sc >= 4 else "🟢")
                _sq_parts.append(f"Sq:{_sc}/10{_sq_em}")
            _si_line = "  " + " | ".join(_sq_parts) if _sq_parts else ""
        except Exception:
            _si_line = ""

        stock_line = (
            f"{stock_em} <b>{tk}</b>  EOD <b>${spot_reg:.2f}</b> → {ah_src} <b>${spot_ext:.2f}</b> {ext_tag}\n"
            f"{tk_pnl_em} P&amp;L now <b>${tk_pnl_now:+,.0f}</b>  "
            f"{tk_tmrw_em} Tmrw est <b>${tk_pnl_tmrw:+,.0f}</b>"
            + (_si_line if _si_line else "")
            + ev_line
        )

        # ── Flat <pre> leg table — EOD + AH prices side by side ─────
        # Cols: Side Type Strk DTE Entry  EOD-val  AH-val  P&L%  Tmrw  Act
        _C = [5, 4, 6, 3, 6, 6, 6, 5, 6, 5]
        _H = ("Side","Type","Strk","DTE","Entry","EOD$","AH$","P&L%","Tmrw$","Act")
        _sep = "─" * (sum(_C) + len(_C) - 1)
        _rows = [
            "  ".join(str(h).ljust(w) for h, w in zip(_H, _C)),
            _sep,
        ]
        for (sd, ot2, st2, dt2, en2, v_eod, v_ah, _is_ext,
             pnl_eod, pnl_d, pnl_p, vt, pct_t, act, lim, rsn, csfx) in leg_lines:
            act_s = act[:5] if act else "WATCH"
            # highlight AH col with * when extended hours
            ah_s = f"${v_ah:.2f}" + ("*" if _is_ext else " ")
            _rows.append("  ".join([
                str(sd)[:_C[0]].ljust(_C[0]),
                str(ot2)[:_C[1]].ljust(_C[1]),
                f"${st2:.0f}".rjust(_C[2]),
                f"{dt2}d".rjust(_C[3]),
                f"${en2:.2f}".rjust(_C[4]),
                f"${v_eod:.2f}".rjust(_C[5]),
                ah_s.rjust(_C[6]),
                f"{pnl_p:+.0f}%".rjust(_C[7]),
                f"${vt:.2f}".rjust(_C[8]),
                str(act_s).ljust(_C[9]),
            ]))
            if csfx:
                _rows.append(f"  🔥 {csfx}")
        _rows.append(_sep)
        _rows.append("* = AH/PM price used for premium calc")
        for (sd, ot2, st2, dt2, en2, v_eod, v_ah, _is_ext,
             pnl_eod, pnl_d, pnl_p, vt, pct_t, act, lim, rsn, csfx) in leg_lines:
            if lim:
                _rows.append(f"  -> {act}: limit ${lim:.2f}  ({rsn})")

        tk_card = (
            stock_line + "\n"
            + f"<pre>{chr(10).join(_rows)}</pre>"
        )
        await query.message.reply_text(tk_card, parse_mode="HTML")

    # Final summary card
    net_em   = "🟢" if portfolio_pnl_tmrw >= 0 else "🔴"
    close_orders = [o for o in all_orders if o["action"] in ("CLOSE", "STOP LOSS", "TAKE PROFIT")]

    summary_lines = [
        hdr("🌙 AH PREDICTOR — SUMMARY"),
        f"{vix_em} <b>VIX:</b> {vix_val:.1f}  |  <b>IV est:</b> {iv_base*100:.0f}%",
        f"{net_em} <b>Portfolio P&amp;L now:</b> ${portfolio_pnl_now:+,.0f}",
        f"{net_em} <b>Portfolio P&amp;L tmrw:</b> ${portfolio_pnl_tmrw:+,.0f}",
    ]

    if close_orders:
        summary_lines.append("\n<b>📋 Pre-Market GTC Orders</b>")
        _C2 = [5, 5, 6, 11, 7, 6]
        _H2 = ("Tkr","Type","Strk","Action","Limit","Why")
        _sep2 = "─" * (sum(_C2) + len(_C2) - 1)
        ord_rows = [
            "  ".join(str(h).ljust(w) for h, w in zip(_H2, _C2)),
            _sep2,
        ]
        for o in close_orders:
            lim_s = f"${o['limit']:.2f}" if o["limit"] else "MKT"
            why_s = (o["reason"] or "")[:_C2[5]]
            ord_rows.append("  ".join([
                str(o["tk"])[:_C2[0]].ljust(_C2[0]),
                str(o["ot_s"])[:_C2[1]].ljust(_C2[1]),
                f"${o['strk']:.0f}".rjust(_C2[2]),
                str(o["action"])[:_C2[3]].ljust(_C2[3]),
                str(lim_s).rjust(_C2[4]),
                str(why_s).ljust(_C2[6] if len(_C2) > 6 else _C2[5]),
            ]))
        ord_rows.append(_sep2)
        summary_lines.append(mono("\n".join(ord_rows)))
        summary_lines.append("<i>Place as GTC limit orders before market open</i>")
    else:
        summary_lines.append("\n<i>No urgent pre-market orders needed — all positions HOLD/WATCH</i>")

    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("⚠️ Overnight Risk", callback_data="menu_overnight_risk"),
        InlineKeyboardButton("💼 Positions",       callback_data="menu_positions"),
        BACK_BTN
    ]])
    await query.message.reply_text("\n".join(summary_lines), parse_mode="HTML", reply_markup=kb)
    try: await _loading.delete()
    except: pass


# ═══════════════════════════════════════════════════════════
#  AI CHAT — Claude API
# ═══════════════════════════════════════════════════════════
def _load_ai_key() -> str:
    """Load Anthropic API key from file or env."""
    key_file = os.path.join(os.path.dirname(__file__), "anthropic_key.txt")
    if os.path.exists(key_file):
        return open(key_file).read().strip()
    return os.environ.get("ANTHROPIC_API_KEY", "")


async def ai_chat_menu(query):
    """Show AI chat instructions."""
    kb = InlineKeyboardMarkup([[BACK_BTN]])
    await query.message.reply_text(
        f"{hdr('🤖 AI TRADING ASSISTANT')}\n\n"
        "Just <b>type any question</b> in the chat and I'll reply!\n\n"
        "<b>Examples:</b>\n"
        "• What is delta hedging?\n"
        "• Should I roll my NVDA call to next month?\n"
        "• Explain IV crush after earnings\n"
        "• What is a good stop loss for a 30 DTE option?\n"
        "• How does theta decay accelerate near expiry?\n\n"
        "<i>I have context of your current positions and can give personalised advice.</i>",
        parse_mode=H, reply_markup=kb
    )


async def ai_chat_handler(update, context):
    """Handle plain text messages — answer with Claude AI."""
    _, auth_chat_id = load_creds()
    if str(update.effective_chat.id) != str(auth_chat_id):
        return   # ignore messages from unknown chats

    text = (update.message.text or "").strip()
    if not text or text.startswith("/"):
        return

    api_key = _load_ai_key()
    if not api_key:
        await update.message.reply_text(
            "⚠️ AI key not configured.\n"
            "Create <code>anthropic_key.txt</code> with your Anthropic API key.",
            parse_mode=H)
        return

    # ── Build context: open positions ────────────────────────────
    pos_ctx = ""
    try:
        conn = get_conn()
        open_pos = pd.read_sql("SELECT * FROM trades WHERE status='OPEN' LIMIT 20", conn)
        conn.close()
        if not open_pos.empty:
            lines = []
            for _, tr in open_pos.iterrows():
                lines.append(
                    f"{tr.get('ticker')} {tr.get('option_type','').upper()} "
                    f"${tr.get('strike','')} exp:{tr.get('expiry','')} "
                    f"entry:${tr.get('entry_price','')} qty:{tr.get('quantity','')}"
                )
            pos_ctx = "User's current open positions:\n" + "\n".join(lines)
    except Exception:
        pass

    system_prompt = (
        "You are an expert options trader and quantitative analyst embedded in a Telegram trading bot. "
        "You help the user understand options trading, risk management, greeks, strategies, and market analysis. "
        "Be concise (max 300 words), direct, and use simple language. "
        "Format for Telegram HTML: use <b>bold</b> for key terms. No markdown, only HTML tags. "
        "If the user asks about their positions, use the context provided.\n\n"
        + pos_ctx
    )

    typing_msg = await update.message.reply_text("🤖 Thinking…")

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=600,
            system=system_prompt,
            messages=[{"role": "user", "content": text}]
        )
        answer = resp.content[0].text.strip()
        log.info(f"AI chat: Q='{text[:60]}' → {len(answer)} chars")
    except Exception as e:
        log.warning(f"AI chat error: {e}")
        answer = f"⚠️ AI error: {e}"

    try: await typing_msg.delete()
    except Exception: pass

    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("🤖 AI Help", callback_data="menu_ai_chat"),
        BACK_BTN
    ]])
    await update.message.reply_text(answer, parse_mode=H, reply_markup=kb)


# ═══════════════════════════════════════════════════════════
#  BATCH EXIT ANALYSIS (must be defined before main())
# ═══════════════════════════════════════════════════════════
async def _batch_fetch_market():
    """Fetch VIX + ES/NQ futures once for all batch legs."""
    vix_val, vix_pct, es_pct, nq_pct = 20.0, 0.0, 0.0, 0.0
    try:
        vix_h = yf.Ticker("^VIX").history(period="5d")
        if len(vix_h) >= 2:
            vix_val = float(vix_h["Close"].iloc[-1])
            vix_pct = (vix_val - float(vix_h["Close"].iloc[-2])) / float(vix_h["Close"].iloc[-2]) * 100
    except Exception:
        pass
    try:
        for sym, lbl in [("ES=F", "es"), ("NQ=F", "nq")]:
            fh = yf.Ticker(sym).history(period="5d")
            if len(fh) >= 2:
                pct = (float(fh["Close"].iloc[-1]) - float(fh["Close"].iloc[-2])) / float(fh["Close"].iloc[-2]) * 100
                if lbl == "es":
                    es_pct = pct
                else:
                    nq_pct = pct
    except Exception:
        pass
    return vix_val, vix_pct, es_pct, nq_pct


def _batch_build_leg_card(ticker, opt_type, strike, entry, expiry_str, qty,
                           spot, hv, day_chg, tk_obj,
                           vix_val, vix_pct, es_pct, nq_pct,
                           spot_ext=None, ext_src="EOD"):
    """
    Run MC simulation for one leg and return (msg_html, exp_pnl, var_95).
    Uses bs_price() for current theo value — never returns $0.
    spot_ext: after/pre-market price; if provided, shows a second AH scenario row.
    """
    try:
        expiry = datetime.strptime(expiry_str, "%Y-%m-%d").date()
    except Exception:
        expiry = (datetime.now() + timedelta(days=20)).date()

    K = float(strike)
    r = 0.045
    dte = max((datetime.combine(expiry, datetime.min.time()) - datetime.now()).days, 1)

    # IV: try live chain, fallback to VIX-derived
    iv, iv_src, iv_raw = 0.30, "Default", 0.0
    try:
        chain = tk_obj.option_chain(expiry_str)
        oc = chain.puts if opt_type == "put" else chain.calls
        m = oc[oc["strike"] == K]
        if not m.empty:
            fiv = float(m.iloc[0].get("impliedVolatility", 0))
            if fiv >= 0.05:
                iv = fiv
                iv_src = f"Live {iv:.0%}"
            else:
                iv_raw = fiv
    except Exception:
        pass

    if iv_src == "Default" or (iv_raw > 0 and iv_raw < 0.05):
        vix_iv = vix_val / 100.0 * 1.3
        iv = max(vix_iv, hv, 0.15)
        iv_src = f"VIX-derived {iv:.0%}"

    predicted_gap = (es_pct + nq_pct) / 2

    # Vol calibration
    mc_vix_vol = vix_val / 100.0 * 1.3 if vix_val > 15 else 0
    mc_vol = (0.4 * iv + 0.3 * hv + 0.3 * mc_vix_vol) if mc_vix_vol > 0 else (0.6 * iv + 0.4 * hv)
    if vix_pct > 10 and mc_vix_vol > mc_vol:
        mc_vol = max(mc_vol, mc_vix_vol * 0.85)
    mc_vol = max(mc_vol, 0.15)

    # MC simulation
    T_tomorrow = max(dte - 1, 1) / 365.0
    dt = 1.0 / 252.0
    futures_drift = predicted_gap / 100.0
    overnight_drift = futures_drift - 0.001
    np.random.seed(42)
    Z = np.random.standard_normal(10000)
    sim_returns = overnight_drift + (-0.5 * mc_vol**2 * dt) + mc_vol * np.sqrt(dt) * Z
    sim_prices = spot * np.exp(sim_returns)

    iv_base = max(iv, vix_val / 100.0 * 1.2) if vix_val > 20 else iv
    iv_vix_adj = 0.02 + (0.03 if abs(predicted_gap) > 1 else 0) + (0.05 + max(0, (vix_pct - 10) * 0.002) if vix_pct > 10 else 0)
    sim_ivs = np.clip(iv_base + iv_vix_adj + np.random.normal(0, 0.03, 10000), 0.05, 2.0)

    sqrt_T = np.sqrt(max(T_tomorrow, 1e-6))
    _d1 = (np.log(sim_prices / K) + (r + 0.5 * sim_ivs**2) * T_tomorrow) / (sim_ivs * sqrt_T)
    _d2 = _d1 - sim_ivs * sqrt_T
    if opt_type == "put":
        option_vals = K * np.exp(-r * T_tomorrow) * norm.cdf(-_d2) - sim_prices * norm.cdf(-_d1)
    else:
        option_vals = sim_prices * norm.cdf(_d1) - K * np.exp(-r * T_tomorrow) * norm.cdf(_d2)
    option_vals = np.maximum(option_vals, 0.0)

    exp_stock = float(np.mean(sim_prices))
    exp_val   = float(np.mean(option_vals))
    p10       = float(np.percentile(option_vals, 10))
    p90       = float(np.percentile(option_vals, 90))

    pos_sign  = -1 if qty < 0 else 1
    pnl_array = (option_vals - float(entry)) * 100.0 * pos_sign
    exp_pnl   = float(np.mean(pnl_array))
    prob_profit = float(np.mean(option_vals < float(entry)) * 100.0) if qty < 0 else float(np.mean(option_vals > float(entry)) * 100.0)
    var_95    = float(np.percentile(pnl_array, 5))

    # Current theo value via BS (never $0)
    T_now   = max(dte, 1) / 365.0
    cur_val = bs_price(spot, K, T_now, r, iv, opt=opt_type)
    greeks  = bs_greeks(spot, K, T_now, r, iv, opt=opt_type)

    pnl_pct = (exp_val - float(entry)) / float(entry) * 100 * pos_sign if float(entry) > 0 else 0
    tmrw_pnl = (exp_val - cur_val) * 100.0 * pos_sign
    tmrw_pct = (exp_val - cur_val) / cur_val * 100 * pos_sign if cur_val > 0 else 0

    # Recommendation
    if qty < 0:
        target_price = float(entry) * 0.5
        if prob_profit > 55 and pnl_pct > 10:
            rec = "<b>🟢 BUY TO CLOSE — Take Profit</b>"
            rec_detail = f"MC: {prob_profit:.0f}% profit chance. Target ≤ ${target_price:.2f}"
        elif prob_profit > 55:
            rec = "<b>🟡 HOLD — let decay work</b>"
            rec_detail = f"MC: {prob_profit:.0f}% profit. Theta working — hold."
        elif prob_profit > 40:
            rec = "<b>🟠 SET STOP — Risk rising</b>"
            rec_detail = f"MC: {prob_profit:.0f}% profit. Stop-buy at ${float(entry)*1.5:.2f}"
        else:
            rec = "<b>🔴 BUY TO CLOSE — Exit Now</b>"
            rec_detail = f"MC: only {prob_profit:.0f}% profit. Close now."
    else:
        target_price = float(entry) * 1.3
        if prob_profit > 55 and pnl_pct > 10:
            rec = "<b>🟢 SET LIMIT SELL — Take Profit</b>"
            rec_detail = f"MC: {prob_profit:.0f}% profit. Target ≥ ${target_price:.2f} (+30%)"
        elif prob_profit > 55:
            rec = "<b>🟡 HOLD WITH STOP</b>"
            rec_detail = f"MC: {prob_profit:.0f}% profit. Stop at ${float(entry)*0.80:.2f}"
        elif prob_profit > 40:
            rec = "<b>🟠 TIGHT STOP-LOSS</b>"
            rec_detail = f"MC: {prob_profit:.0f}% profit. Stop at ${float(entry)*0.80:.2f}"
        else:
            rec = "<b>🔴 EXIT AT OPEN</b>"
            rec_detail = f"MC: only {prob_profit:.0f}% profit. Expected loss ${exp_pnl:+,.0f}. Cut losses."

    pnl_emoji  = "🟢" if exp_pnl >= 0 else "🔴"
    tmrw_emoji = "🟢" if tmrw_pnl >= 0 else "🔴"
    side_label = "SHORT (Sold)" if qty < 0 else "LONG (Bought)"

    # After/pre-market scenario (second price line shown when extended-hours data available)
    ah_row = ""
    ah_scenario_block = ""
    if spot_ext and spot_ext != spot and spot_ext > 0:
        ah_chg_pct = (spot_ext - spot) / spot * 100
        ah_val = bs_price(spot_ext, K, max(dte - 1, 1) / 365.0, r, iv, opt=opt_type)
        ah_pnl = (ah_val - float(entry)) * 100.0 * pos_sign
        ah_em  = "🟢" if ah_pnl >= 0 else "🔴"
        ah_row = f"\n{row2(ext_src, f'${spot_ext:.2f} ({ah_chg_pct:+.2f}%)')}"
        ah_scenario_block = (
            "\n🌙 <b>After-Market Scenario</b>\n"
            + mono(
                f"{row2(ext_src, f'${spot_ext:.2f} ({ah_chg_pct:+.2f}%)')}\n"
                f"{row2('AH Option Theo', f'${ah_val:.2f}')}\n"
                f"{ah_em} {row2('AH P&L vs Entry', f'${ah_pnl:+,.0f}')}"
            )
        )

    msg = (
        f"{hdr(f'🎯 {ticker} {opt_type.upper()} ${K:.0f} · {side_label}')}\n\n"
        f"📊 <b>Market Snapshot</b>\n"
        + mono(
            f"{row2(ticker + ' Close', f'${spot:.2f} ({day_chg:+.2f}%)')}"
            + ah_row + "\n"
            f"{row2('VIX', f'{vix_val:.1f} ({vix_pct:+.1f}%)')}\n"
            f"{row2('ES / NQ', f'{es_pct:+.2f}% / {nq_pct:+.2f}%')}\n"
            f"{row2('Gap Est.', f'{predicted_gap:+.2f}%')}"
        )
        + "\n📖 <b>Parameters</b>\n"
        + mono(
            f"{row2('Strike', f'${K:.0f}')}\n"
            f"{row2('DTE', f'{dte} days')}\n"
            f"{row2('IV Source', iv_src)}\n"
            f"{row2('Entry', f'${entry:.2f}')}\n"
            f"{row2('Now (Theo)', f'${cur_val:.2f}')}\n"
            f"{row2('MC Vol', f'{mc_vol:.0%}')}"
        )
        + "\n🎲 <b>Monte Carlo · 10K Sims</b>\n"
        + mono(
            f"{row2('Exp. Stock', f'${exp_stock:.2f}')}\n"
            f"{row2('Exp. Option', f'${exp_val:.2f}')}\n"
            f"{row2('Range', f'${p10:.2f} – ${p90:.2f}')}\n"
            f"{'─' * 27}\n"
            f"{pnl_emoji} {row2('P&L vs Entry', f'${exp_pnl:+,.0f} ({pnl_pct:+.0f}%)')}\n"
            f"{tmrw_emoji} {row2('P&L Tomorrow', f'${tmrw_pnl:+,.0f} ({tmrw_pct:+.0f}%)')}\n"
            f"{row2('P(Profit)', f'{prob_profit:.0f}%  {bar(prob_profit)}')}\n"
            f"{row2('VaR 95%', f'${var_95:+,.0f}')}"
        )
        + ah_scenario_block
        + "\n📊 <b>Greeks (Current)</b>\n"
        + mono(
            row2('Theo Value', f'${cur_val:.2f}') + "\n"
            + row2('Delta', f'{greeks.get("delta", 0):.3f}') + "\n"
            + row2('Theta', f'-${abs(greeks.get("theta", 0))*100:.2f}/day') + "\n"
            + row2('Vega', f'${greeks.get("vega", 0)*100:.2f}')
        )
        + f"\n💡 <b>Recommendation</b>\n{rec}\n{rec_detail}\n"
    )
    return msg, exp_pnl, var_95


async def _batch_run_ticker_legs(query, ticker_trades, vix_val, vix_pct, es_pct, nq_pct, send_cards=True):
    """
    Compute MC analysis for all legs of a ticker.
    send_cards=True  → send individual leg cards to Telegram (original behaviour).
    send_cards=False → compute silently, return data only (used for summary-first flow).
    Returns (total_pnl, total_var95, legs_data).
    """
    ticker = str(ticker_trades.iloc[0]['ticker']).upper()
    tk_obj = yf.Ticker(ticker)
    hist = tk_obj.history(period="3mo")
    if len(hist) < 2:
        if send_cards:
            await query.message.reply_text(f"❌ Could not fetch data for {ticker}", parse_mode=H)
        return 0.0, 0.0, []

    # Fetch both regular close and after/pre-market price
    ah_data  = _get_spot_with_ah(ticker)
    spot     = ah_data["spot_reg"] if ah_data["spot_reg"] > 0 else float(hist["Close"].iloc[-1])
    spot_ext = ah_data["spot_ext"] if ah_data["is_extended"] else None
    ext_src  = ah_data["ext_src"]

    prev    = float(hist["Close"].iloc[-2])
    closes  = hist["Close"].dropna().values
    hv      = float(np.std(np.diff(np.log(closes)))) * np.sqrt(252) if len(closes) >= 21 else 0.25
    day_chg = (spot - prev) / prev * 100

    ticker_total = 0.0
    total_var95  = 0.0
    legs_data    = []
    for _, tr in ticker_trades.iterrows():
        ot  = str(tr['option_type']).lower()
        st  = float(tr['strike'])
        ep  = float(tr['entry_price'])
        qty = int(tr.get('quantity', 1) or 1)
        ex  = str(tr['expiry'])
        try:
            msg, exp_pnl, var_95 = _batch_build_leg_card(
                ticker, ot, st, ep, ex, qty,
                spot, hv, day_chg, tk_obj,
                vix_val, vix_pct, es_pct, nq_pct,
                spot_ext=spot_ext, ext_src=ext_src,
            )
            ticker_total += exp_pnl
            total_var95  += var_95
            legs_data.append({
                "ticker": ticker, "ot": ot, "strike": st, "pnl": exp_pnl,
                "var95": var_95, "qty": qty, "ep": ep, "ex": ex, "msg": msg,
            })
            if send_cards:
                cb_refresh   = f"exitmc|{ticker}|{ot}|{st}|{ep}|{ex}|{qty}"
                cb_scenarios = f"scenarios|{ticker}|{ot}|{st}|{ep}|{ex}|{qty}"
                kb = InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔄 Refresh", callback_data=cb_refresh),
                     InlineKeyboardButton("📊 Scenarios", callback_data=cb_scenarios)],
                    [InlineKeyboardButton("🎯 Exit Planner", callback_data="menu_exit"), BACK_BTN],
                ])
                await _safe_reply(query.message, msg, reply_markup=kb)
        except Exception as e:
            if send_cards:
                await query.message.reply_text(f"❌ {ticker} leg error: {e}", parse_mode=H)
    return ticker_total, total_var95, legs_data


def _build_portfolio_summary(all_legs, ticker_pnl_map, tickers):
    """Build the portfolio risk summary inner text block."""
    n       = len(all_legs)
    n_calls = sum(1 for l in all_legs if l['ot'] == 'call')
    n_puts  = sum(1 for l in all_legs if l['ot'] == 'put')
    n_long  = sum(1 for l in all_legs if l['qty'] > 0)
    n_short = sum(1 for l in all_legs if l['qty'] < 0)
    pnl     = sum(l['pnl']  for l in all_legs)
    var95   = sum(l['var95'] for l in all_legs)
    em      = '🟢' if pnl >= 0 else '🔴'
    breakdown = "\n".join(
        f"{'🟢' if v>=0 else '🔴'} {k:<6} {v:>+9,.0f}"
        for k, v in sorted(ticker_pnl_map.items())
    )
    inner = (
        f"{'Positions':<14} {n} legs  ({n_calls}C / {n_puts}P)\n"
        f"{'Long / Short':<14} {n_long}L / {n_short}S\n"
        f"{'Stocks':<14} {len(tickers)}\n"
        f"{'─' * 27}\n"
        f"{em} {'Total Exp P&L':<13} {pnl:>+9,.0f}\n"
        f"🔴 {'Max Loss VaR95':<13} {var95:>+9,.0f}\n"
        f"{'─' * 27}\n"
        "Per-Stock:\n" + breakdown
    )
    return inner, pnl, var95


def _classify_leg_role(leg: dict, all_legs: list) -> str:
    """Classify a leg's role in the portfolio: DIRECTIONAL, HEDGE, SPREAD, or COVERED."""
    ot    = leg["ot"]
    qty   = leg["qty"]
    strk  = leg["strike"]
    # Short call with a long call at lower strike = bull spread / covered call leg
    if ot == "call" and qty < 0:
        if any(l["ot"] == "call" and l["qty"] > 0 and l["strike"] < strk for l in all_legs):
            return "SPREAD (short leg)"
        return "SHORT CALL (hedge/income)"
    if ot == "call" and qty > 0:
        if any(l["ot"] == "call" and l["qty"] < 0 and l["strike"] > strk for l in all_legs):
            return "SPREAD (long leg)"
        return "LONG CALL (directional)"
    if ot == "put" and qty > 0:
        if any(l["ot"] == "call" and l["qty"] > 0 for l in all_legs):
            return "PUT HEDGE (downside protection)"
        return "LONG PUT (bearish/hedge)"
    if ot == "put" and qty < 0:
        if any(l["ot"] == "call" and l["qty"] > 0 for l in all_legs):
            return "SHORT PUT (premium income)"
        return "SHORT PUT (income)"
    return "DIRECTIONAL"


def _build_exit_verdict(ticker: str, legs_data: list, total_pnl: float, total_var95: float,
                         ev: dict = None) -> str:
    """
    Strategy-aware Final Verdict: detects hedges, spreads, income legs.
    Gives per-leg role, action, and a portfolio-level recommendation.
    """
    if not legs_data:
        return ""

    n = len(legs_data)
    profit_pnl = sum(l["pnl"] for l in legs_data if l["pnl"] >= 0)
    loss_pnl   = sum(l["pnl"] for l in legs_data if l["pnl"] < 0)

    # Overall direction
    if total_pnl >= 0:
        dir_em, direction = "📈", "NET PROFITABLE"
    else:
        dir_em, direction = "📉", "NET LOSS"

    # Per-leg analysis with role detection
    leg_lines = []
    action_needed = []
    for l in legs_data:
        role   = _classify_leg_role(l, legs_data)
        side   = "Short" if l["qty"] < 0 else "Long"
        pnl_em = "🟢" if l["pnl"] >= 0 else "🔴"
        dte_s  = ""
        try:
            dte_days = (datetime.strptime(str(l.get("ex",""))[:10], "%Y-%m-%d").date() - datetime.now().date()).days
            dte_s = f" {dte_days}d"
        except Exception:
            pass

        # Action per leg based on role + P&L
        if "HEDGE" in role or "SHORT PUT" in role:
            if l["pnl"] < 0:
                act = "HOLD — hedge doing its job (cost is the price of protection)"
            else:
                act = "HOLD — income leg working"
        elif "SPREAD" in role:
            act = "HOLD — part of spread, don't close in isolation"
        elif l["pnl"] >= abs(total_var95) * 0.5 and l["pnl"] > 0:
            act = "CONSIDER TAKING PROFIT"
            action_needed.append(f"{l['ot'].upper()} ${l['strike']:.0f}")
        elif l["pnl"] < 0 and abs(l["pnl"]) > abs(l.get("var95", 0)) * 0.6:
            act = "REVIEW — approaching max loss level"
            action_needed.append(f"{l['ot'].upper()} ${l['strike']:.0f}")
        else:
            act = "HOLD"

        _leg_em = ("📈" if l['ot']=='call' else "🛡️") if l['qty']>0 else ("⚡" if l['ot']=='call' else "💰")
        leg_lines.append(
            f"{pnl_em} {_leg_em} {side} {l['ot'].upper()} ${l['strike']:.0f}{dte_s}  "
            f"P&amp;L <b>${l['pnl']:+,.0f}</b>\n"
            f"   Role: <i>{role}</i>\n"
            f"   → {act}"
        )

    # Portfolio-level recommendation
    var_ratio = abs(total_var95) / max(abs(total_pnl), 1) if total_pnl != 0 else 99
    if total_pnl >= 0 and var_ratio <= 3:
        rec = f"Position is profitable and well-controlled. Hold unless event risk is imminent."
    elif total_pnl >= 0 and var_ratio > 3:
        rec = (f"Profitable but risk ({abs(total_var95):,.0f}) is {var_ratio:.0f}x the gain. "
               f"Consider locking in profits or reducing size.")
    elif total_pnl < 0 and loss_pnl < 0 and abs(loss_pnl) > abs(profit_pnl):
        rec = (f"Net loss driven by directional legs. Hedge legs may offset — "
               f"don't exit hedges; review the losing directional legs first.")
    else:
        rec = (f"Mixed result. Net P&L {total_pnl:+,.0f}. "
               f"Focus on legs marked 'REVIEW' — rest can hold.")

    # Upside/downside targets
    upside  = total_pnl * 1.5 if total_pnl > 0 else abs(profit_pnl) * 0.8
    lines = [
        f"\n{'═'*28}",
        f"{dir_em} <b>FINAL VERDICT — {ticker}</b>  <b>{direction}</b>",
        f"Net P&amp;L: <b>${total_pnl:+,.0f}</b>  |  Max Risk VaR95: <b>${total_var95:+,.0f}</b>",
        "\n<b>Per-Leg Breakdown</b>",
    ] + leg_lines + [
        f"\n🎯 <b>Simple Answer</b>",
        f"✅ Best case  → ${upside:+,.0f} (take profit target)",
        f"❌ Worst case → ${total_var95:+,.0f} (VaR 95% stop)",
        f"\n<b>Recommendation</b>\n{rec}",
    ]

    if action_needed:
        lines.append(f"<b>Action needed on:</b> {', '.join(action_needed)}")

    # Event risk
    if ev and ev.get("has_event") and ev.get("iv_crush_warning"):
        lines.append(f"\n{ev['iv_crush_warning']}")
        if ev.get("fomc_days") is not None and ev.get("event_type") != "FOMC":
            lines.append(f"📅 FOMC in {ev['fomc_days']}d — macro vol risk.")
    elif ev and ev.get("vix_regime") in ("high_fear", "elevated") and ev.get("summary_line"):
        lines.append(f"\n⚠️ {ev['summary_line']}")

    return "\n".join(lines)


async def exit_batch_all(query):
    """Compute MC for all open positions, show portfolio summary first, then offer individual cards."""
    conn = get_conn()
    open_trades = pd.read_sql(
        "SELECT * FROM trades WHERE status='OPEN' ORDER BY ticker, expiry", conn)
    conn.close()
    if open_trades.empty:
        await query.message.reply_text('No open positions.', reply_markup=InlineKeyboardMarkup([[BACK_BTN]]))
        return

    n = len(open_trades)
    tickers = open_trades['ticker'].unique().tolist()
    _msg = await query.message.reply_text(
        f"⏳ Computing MC for {n} leg(s) across {len(tickers)} stock(s)…", parse_mode=H)

    vix_val, vix_pct, es_pct, nq_pct = await _batch_fetch_market()

    all_legs       = []
    ticker_pnl_map = {}
    for ticker in tickers:
        legs = open_trades[open_trades['ticker'] == ticker]
        # send_cards=False — compute silently so summary comes first
        tk_pnl, _, tk_legs = await _batch_run_ticker_legs(
            query, legs, vix_val, vix_pct, es_pct, nq_pct, send_cards=False)
        all_legs       += tk_legs
        ticker_pnl_map[ticker] = tk_pnl

    try: await _msg.delete()
    except Exception: pass

    inner, port_pnl, port_var = _build_portfolio_summary(all_legs, ticker_pnl_map, tickers)
    # Portfolio-level event: use first ticker with an event, or FOMC (macro affects all)
    _ev_port = {}
    for _tk in tickers:
        _ev_tmp = _get_event_risk(_tk, vix_val=vix_val)
        if _ev_tmp["has_event"]:
            _ev_port = _ev_tmp
            break
    if not _ev_port:
        _ev_port = _get_event_risk(tickers[0], vix_val=vix_val) if tickers else {}
    _verdict = _build_exit_verdict("PORTFOLIO", all_legs, port_pnl, port_var, ev=_ev_port)
    summary = hdr("📊 PORTFOLIO RISK SUMMARY") + "\n\n" + mono(inner) + (_verdict if _verdict else "")
    await query.message.reply_text(
        summary,
        parse_mode=H,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("📋 Show Individual Leg Cards", callback_data="exit_batch_all_cards")],
            [InlineKeyboardButton("🔄 Refresh Summary", callback_data="exit_batch_all")],
            [InlineKeyboardButton("🎯 Exit Planner", callback_data="menu_exit"), BACK_BTN],
        ])
    )


async def exit_batch_all_cards(query):
    """Send individual MC leg cards for every open position (triggered after seeing portfolio summary)."""
    conn = get_conn()
    open_trades = pd.read_sql(
        "SELECT * FROM trades WHERE status='OPEN' ORDER BY ticker, expiry", conn)
    conn.close()
    if open_trades.empty:
        await query.message.reply_text('No open positions.', reply_markup=InlineKeyboardMarkup([[BACK_BTN]]))
        return

    tickers = open_trades['ticker'].unique().tolist()
    _msg = await query.message.reply_text(
        f"⏳ Sending detailed cards for {len(open_trades)} leg(s)…", parse_mode=H)

    vix_val, vix_pct, es_pct, nq_pct = await _batch_fetch_market()
    for ticker in tickers:
        legs = open_trades[open_trades['ticker'] == ticker]
        await query.message.reply_text(f"<b>━━ {ticker} · {len(legs)} leg(s) ━━</b>", parse_mode=H)
        await _batch_run_ticker_legs(query, legs, vix_val, vix_pct, es_pct, nq_pct, send_cards=True)

    try: await _msg.delete()
    except Exception: pass

    await query.message.reply_text(
        "✅ All leg cards sent.",
        parse_mode=H,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("📊 Back to Summary", callback_data="exit_batch_all")],
            [InlineKeyboardButton("🎯 Exit Planner", callback_data="menu_exit"), BACK_BTN],
        ])
    )


async def exit_batch_ticker(query, ticker):
    """Compute MC for a single ticker, show per-stock summary first, then offer individual cards."""
    conn = get_conn()
    open_trades = pd.read_sql(
        "SELECT * FROM trades WHERE status='OPEN' AND ticker=? ORDER BY expiry",
        conn, params=(ticker,))
    conn.close()
    if open_trades.empty:
        await query.message.reply_text(
            f'No open positions for {ticker}.', reply_markup=InlineKeyboardMarkup([[BACK_BTN]]))
        return

    n = len(open_trades)
    _msg = await query.message.reply_text(
        f"⏳ Computing MC for {ticker} ({n} leg(s))…", parse_mode=H)

    vix_val, vix_pct, es_pct, nq_pct = await _batch_fetch_market()
    # send_cards=False so summary appears first
    ticker_pnl, ticker_var95, legs_data = await _batch_run_ticker_legs(
        query, open_trades, vix_val, vix_pct, es_pct, nq_pct, send_cards=False)

    try: await _msg.delete()
    except Exception: pass

    em      = '🟢' if ticker_pnl >= 0 else '🔴'
    n_calls = sum(1 for l in legs_data if l['ot'] == 'call')
    n_puts  = sum(1 for l in legs_data if l['ot'] == 'put')
    n_long  = sum(1 for l in legs_data if l['qty'] > 0)
    n_short = sum(1 for l in legs_data if l['qty'] < 0)
    leg_lines = [
        f"{'🟢' if l['pnl']>=0 else '🔴'} {('+' if l['qty']>0 else '-') + ('C' if l['ot']=='call' else 'P')}"
        f" ${l['strike']:.0f}"
        f" {l['pnl']:>+8,.0f}  VaR:{l['var95']:>+7,.0f}"
        for l in legs_data
    ]
    inner = (
        f"{'Positions':<14} {n} legs  ({n_calls}C / {n_puts}P)\n"
        f"{'Long / Short':<14} {n_long}L / {n_short}S\n"
        f"{'─' * 27}\n"
        f"{em} {'Total Exp P&L':<13} {ticker_pnl:>+9,.0f}\n"
        f"🔴 {'Max Loss VaR95':<13} {ticker_var95:>+9,.0f}\n"
        f"{'─' * 27}\n"
        "Per-Leg:\n" + "\n".join(leg_lines)
    )
    _ev_tk = _get_event_risk(ticker, vix_val=vix_val)
    _verdict = _build_exit_verdict(ticker, legs_data, ticker_pnl, ticker_var95, ev=_ev_tk)
    summary = hdr(f"🏢 {ticker} RISK SUMMARY") + "\n\n" + mono(inner) + (_verdict if _verdict else "")
    await query.message.reply_text(
        summary,
        parse_mode=H,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("📋 Show Individual Leg Cards", callback_data=f"exit_tk_cards|{ticker}")],
            [InlineKeyboardButton("🔄 Refresh Summary", callback_data=f"exit_batch_tk|{ticker}")],
            [InlineKeyboardButton("🎯 Exit Planner", callback_data="menu_exit"), BACK_BTN],
        ])
    )


async def exit_ticker_cards(query, ticker):
    """Send individual MC leg cards for a single ticker (triggered after seeing ticker summary)."""
    conn = get_conn()
    open_trades = pd.read_sql(
        "SELECT * FROM trades WHERE status='OPEN' AND ticker=? ORDER BY expiry",
        conn, params=(ticker,))
    conn.close()
    if open_trades.empty:
        await query.message.reply_text(f'No open positions for {ticker}.', reply_markup=InlineKeyboardMarkup([[BACK_BTN]]))
        return

    _msg = await query.message.reply_text(
        f"⏳ Sending detailed cards for {ticker} ({len(open_trades)} leg(s))…", parse_mode=H)
    vix_val, vix_pct, es_pct, nq_pct = await _batch_fetch_market()
    await _batch_run_ticker_legs(query, open_trades, vix_val, vix_pct, es_pct, nq_pct, send_cards=True)

    try: await _msg.delete()
    except Exception: pass

    await query.message.reply_text(
        f"✅ {ticker} leg cards sent.",
        parse_mode=H,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("📊 Back to Summary", callback_data=f"exit_batch_tk|{ticker}")],
            [InlineKeyboardButton("🎯 Exit Planner", callback_data="menu_exit"), BACK_BTN],
        ])
    )


# ═══════════════════════════════════════════════════════════════════
# ── SHORT-SQUEEZE / SHORT-COVERING DETECTOR  (free data: yfinance + OI)
# ═══════════════════════════════════════════════════════════════════
# US single-name method (NOT the futures OI matrix):
#   1. FUEL    — short interest % float + days-to-cover (Ortex/S3 method)
#   2. TRIGGER — price breakout on a volume spike (>1.5x avg)
#   3. CONFIRM — call writers buying back (call OI falling on an up-day)
# Scored 0–5; >=3 = active signal. CTB/Utilization need a paid feed.

def short_squeeze_signal(ticker, conn=None):
    """US short-squeeze / short-covering detector. Returns a scored dict (0–5)."""
    tk = str(ticker).upper().strip()
    out = {"ticker": tk, "score": 0, "max": 5, "reasons": [],
           "stage": "NONE", "label": "No squeeze", "emoji": "⚪",
           "short_pct": None, "dtc": None, "si_chg_pct": None,
           "price_chg": None, "vol_ratio": None,
           "call_oi_chg": None, "put_oi_chg": None}
    score = 0

    # ---- 1+2+3. Short-interest fuel (yfinance .info) ----
    short_pct = dtc = si_now = si_prev = None
    try:
        info = yf.Ticker(tk).info or {}
        short_pct = info.get("shortPercentOfFloat")   # fraction, e.g. 0.18
        dtc       = info.get("shortRatio")             # days to cover
        si_now    = info.get("sharesShort")
        si_prev   = info.get("sharesShortPriorMonth")
    except Exception:
        pass

    spct = (short_pct or 0) * 100.0
    if spct >= 25:
        score += 1; out["reasons"].append(f"🔴 Short {spct:.0f}% of float — EXTREME squeeze fuel")
    elif spct >= 15:
        score += 1; out["reasons"].append(f"🟠 Short {spct:.0f}% of float — high squeeze fuel")
    elif spct > 0:
        out["reasons"].append(f"Short {spct:.0f}% of float — low fuel")

    if dtc and dtc >= 5:
        score += 1; out["reasons"].append(f"⏳ Days-to-cover {dtc:.1f} — shorts hard to exit")
    elif dtc:
        out["reasons"].append(f"Days-to-cover {dtc:.1f} — easy exit")

    si_chg_pct = None
    if si_now and si_prev and si_prev > 0:
        si_chg_pct = (si_now - si_prev) / si_prev * 100.0
        if si_now < si_prev:
            score += 1; out["reasons"].append(f"📉 Short interest {si_chg_pct:+.0f}% MoM — shorts covering")
        else:
            out["reasons"].append(f"📈 Short interest {si_chg_pct:+.0f}% MoM — shorts still building")

    # ---- 4. Trigger: price + volume ----
    price_chg = vol_ratio = None
    try:
        h = yf.Ticker(tk).history(period="40d")
        if len(h) >= 5:
            price_now  = float(h["Close"].iloc[-1])
            price_prev = float(h["Close"].iloc[-2])
            price_chg  = (price_now - price_prev) / price_prev * 100.0 if price_prev else 0.0
            tvol = float(h["Volume"].iloc[-1])
            avol = float(h["Volume"].iloc[-21:-1].mean()) if len(h) >= 21 else float(h["Volume"].mean())
            vol_ratio = tvol / avol if avol > 0 else 1.0
    except Exception:
        pass

    trigger = (price_chg is not None and vol_ratio is not None
               and price_chg >= 2.0 and vol_ratio >= 1.5)
    if trigger:
        score += 1
        out["reasons"].append(f"🚀 +{price_chg:.1f}% on {vol_ratio:.1f}x volume — squeeze firing")
    elif price_chg is not None and vol_ratio is not None:
        out["reasons"].append(f"Px {price_chg:+.1f}% on {vol_ratio:.1f}x vol — no ignition yet")

    # ---- 5. Confirmation: call writers covering (options OI) ----
    call_oi_chg = put_oi_chg = None
    own = conn is None
    if own:
        try: conn = get_conn()
        except Exception: conn = None
    if conn is not None:
        try:
            row = pd.read_sql(
                "SELECT SUM(change_OI_Call) c, SUM(change_OI_Put) p FROM options_change "
                "WHERE ticker=? AND trade_date_now=("
                "  SELECT trade_date_now FROM options_change WHERE ticker=? "
                "  ORDER BY substr(trade_date_now,7,4)||substr(trade_date_now,1,2)||substr(trade_date_now,4,2) DESC LIMIT 1)",
                conn, params=(tk, tk))
            if not row.empty:
                _c = row["c"].iloc[0]; _p = row["p"].iloc[0]
                call_oi_chg = _safe_float(_c, None) if _c is not None else None
                put_oi_chg  = _safe_float(_p, None) if _p is not None else None
        except Exception:
            pass
    if own and conn is not None:
        try: conn.close()
        except Exception: pass

    if call_oi_chg is not None and call_oi_chg < 0 and (price_chg or 0) > 0:
        score += 1
        out["reasons"].append(f"🔄 Call OI {call_oi_chg:+,.0f} on up-day — call writers buying back")
    elif call_oi_chg is not None:
        out["reasons"].append(f"Call OI {call_oi_chg:+,.0f} — no writer covering")

    # ---- Finalise ----
    score = min(score, 5)
    out["score"]      = score
    out["short_pct"]  = round(spct, 1) if short_pct is not None else None
    out["dtc"]        = round(dtc, 1) if dtc else None
    out["si_chg_pct"] = round(si_chg_pct, 1) if si_chg_pct is not None else None
    out["price_chg"]  = round(price_chg, 2) if price_chg is not None else None
    out["vol_ratio"]  = round(vol_ratio, 2) if vol_ratio is not None else None
    out["call_oi_chg"] = call_oi_chg
    out["put_oi_chg"]  = put_oi_chg

    fuel = (spct >= 15) or bool(dtc and dtc >= 5)
    if trigger and fuel:
        out["stage"] = "FIRING"
    elif fuel:
        out["stage"] = "SETUP"
    else:
        out["stage"] = "NONE"

    if score >= 4:
        out["emoji"] = "🔴"; out["label"] = "HIGH squeeze / strong covering"
    elif score == 3:
        out["emoji"] = "🟠"; out["label"] = "MODERATE squeeze building"
    elif score == 2:
        out["emoji"] = "🟡"; out["label"] = "Low — watch"
    else:
        out["emoji"] = "⚪"; out["label"] = "No squeeze"
    return out


def _fmt_squeeze_report(sig) -> str:
    """Mobile-friendly HTML report for one ticker's squeeze signal."""
    if not sig:
        return "❌ No squeeze data available."
    tk = sig["ticker"]; sc = sig["score"]
    bars = "█" * sc + "░" * (5 - sc)
    lines = [f"{sig['emoji']} <b>{tk} SHORT-SQUEEZE</b>",
             f"Score: <b>{sc}/5</b> [{bars}]",
             f"Stage: <b>{sig['stage']}</b> — {sig['label']}", ""]
    t = []
    if sig.get("short_pct")  is not None: t.append(f"Short%Flt {sig['short_pct']:>5.1f}%")
    if sig.get("dtc")        is not None: t.append(f"DaysCover {sig['dtc']:>5.1f}")
    if sig.get("si_chg_pct") is not None: t.append(f"SI MoM    {sig['si_chg_pct']:>+5.0f}%")
    if sig.get("price_chg")  is not None: t.append(f"Price     {sig['price_chg']:>+5.1f}%")
    if sig.get("vol_ratio")  is not None: t.append(f"Volume    {sig['vol_ratio']:>5.1f}x")
    if t:
        lines.append("<pre>" + "\n".join(t) + "</pre>")
    lines.append("<b>Why:</b>")
    lines.extend("• " + r for r in sig["reasons"])
    lines.append("")
    if sc >= 3:
        lines.append("<i>↗ Bullish for the stock. Short-side legs (long puts / short calls) "
                     "fight a rally — consider trimming/hedging. Longs can run with a stop "
                     "under the breakout.</i>")
    else:
        lines.append("<i>No actionable squeeze. Re-check on a volume-backed up-day.</i>")
    return "\n".join(lines)


async def squeeze_command(update, ctx):
    """/squeeze [TICKER] — one-ticker report, or scan the watchlist (score >=3)."""
    import asyncio
    args = list(getattr(ctx, "args", []) or [])
    if args:
        tk = str(args[0]).upper().strip()
        loading = await update.message.reply_text(f"⏳ Analyzing {tk} short-squeeze…", parse_mode=H)
        try:
            sig = await asyncio.to_thread(short_squeeze_signal, tk)
        except Exception:
            sig = short_squeeze_signal(tk)
        try: await loading.delete()
        except Exception: pass
        await update.message.reply_text(_fmt_squeeze_report(sig), parse_mode=H)
        return

    loading = await update.message.reply_text(
        "⏳ Scanning watchlist for short squeezes… (~30s)", parse_mode=H)
    results = []
    for tk in DEFAULT_TICKERS:
        try:
            sig = await asyncio.to_thread(short_squeeze_signal, tk)
            if sig and sig.get("score", 0) >= 3:
                results.append(sig)
        except Exception:
            continue
    try: await loading.delete()
    except Exception: pass

    if not results:
        await update.message.reply_text(
            "⚪ No active short-squeeze signals (score ≥3) in the watchlist right now.\n"
            "<i>Send /squeeze TICKER to check any symbol.</i>", parse_mode=H)
        return
    results.sort(key=lambda s: s["score"], reverse=True)
    rows = ["🔥 <b>SHORT-SQUEEZE SCAN</b>", "Active signals (score ≥3/5):", ""]
    for s in results:
        line = f"{s['emoji']} <b>{s['ticker']}</b> {s['score']}/5 — {s['stage']}"
        if s.get("short_pct") is not None: line += f" | SI {s['short_pct']:.0f}%"
        if s.get("price_chg") is not None: line += f" | {s['price_chg']:+.1f}%"
        rows.append(line)
    rows += ["", "<i>Send /squeeze TICKER for full detail.</i>"]
    await update.message.reply_text("\n".join(rows), parse_mode=H)


# ═══════════════════════════════════════════════════════════════════
# ── 23-MODEL HIGH-PROBABILITY SIGNAL ENGINE  (ported into live copy)
# ── Models 1-22 (research ensemble) + Model 23 short-squeeze.
# ═══════════════════════════════════════════════════════════════════

def _bs_gamma_hp(S, K, T, sigma, r=0.05):
    """Black-Scholes gamma."""
    if sigma <= 0 or T <= 0 or S <= 0 or K <= 0:
        return 0.0
    try:
        d1 = (_math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * _math.sqrt(T))
        return float(_spnorm.pdf(d1)) / (S * sigma * _math.sqrt(T))
    except Exception:
        return 0.0


def _bs_call_hp(S, K, T, sigma, r=0.05):
    """Black-Scholes call price."""
    if sigma <= 0 or T <= 0:
        return max(S - K, 0.0)
    try:
        d1 = (_math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * _math.sqrt(T))
        d2 = d1 - sigma * _math.sqrt(T)
        return S * float(_spnorm.cdf(d1)) - K * _math.exp(-r * T) * float(_spnorm.cdf(d2))
    except Exception:
        return max(S - K, 0.0)


def _implied_vol_hp(price, S, K, T, r=0.05, tol=1e-4):
    """Bisection IV solver — returns IV or 0.30 default."""
    if price <= 0 or T <= 0 or S <= 0 or K <= 0:
        return 0.30
    intrinsic = max(S - K, 0.0)
    if price <= intrinsic + 1e-5:
        return 0.001
    lo, hi = 0.01, 5.0
    for _ in range(60):
        mid = (lo + hi) / 2
        val = _bs_call_hp(S, K, T, mid, r)
        if abs(val - price) < tol:
            return mid
        if val < price:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def _setup_hp_tables(conn):
    """Create signal_accuracy and signal_weights tables if not present."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS signal_accuracy (
            ticker TEXT,
            trade_date TEXT,
            model_name TEXT,
            signal TEXT,
            prob REAL,
            actual_ret REAL,
            correct INTEGER DEFAULT -1,
            PRIMARY KEY (ticker, trade_date, model_name)
        )""")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS signal_weights (
            ticker TEXT,
            model_name TEXT,
            accuracy_20d REAL DEFAULT 0.5,
            weight REAL DEFAULT 1.0,
            last_updated TEXT,
            PRIMARY KEY (ticker, model_name)
        )""")
    conn.commit()


def _update_hp_outcomes(ticker, conn):
    """Fill actual_ret + correct for prior predictions; recalc adaptive weights."""
    try:
        _setup_hp_tables(conn)
        px = pd.read_sql(
            "SELECT trade_date, close FROM stock_daily WHERE ticker=?"
            " ORDER BY substr(trade_date,7,4)||substr(trade_date,1,2)||substr(trade_date,4,2)",
            conn, params=(ticker.upper(),))
        if len(px) < 2:
            return
        px["close"] = pd.to_numeric(px["close"], errors="coerce")
        px["next_ret"] = px["close"].shift(-1) / px["close"] - 1

        preds = pd.read_sql(
            "SELECT ticker, trade_date, model_name, signal FROM signal_accuracy"
            " WHERE ticker=? AND correct=-1",
            conn, params=(ticker.upper(),))
        for _, row in preds.iterrows():
            m = px[px["trade_date"] == row["trade_date"]]
            if m.empty or pd.isna(m.iloc[0]["next_ret"]):
                continue
            ret = float(m.iloc[0]["next_ret"])
            if row["signal"] == "BULL":
                correct = 1 if ret > 0.003 else 0
            elif row["signal"] == "BEAR":
                correct = 1 if ret < -0.003 else 0
            elif row["signal"] == "NEUTRAL":
                correct = 1 if abs(ret) < 0.012 else 0
            else:
                correct = 0
            conn.execute(
                "UPDATE signal_accuracy SET actual_ret=?, correct=?"
                " WHERE ticker=? AND trade_date=? AND model_name=?",
                (round(ret, 5), correct, ticker.upper(), row["trade_date"], row["model_name"]))
        conn.commit()

        for model in ("gex", "pcr_z", "oi_momentum", "gamma_pin", "vol_flow", "iv_skew",
                      "rv_iv", "oi_term_struct", "maxpain_vel", "iv_rank",
                      "pcp_dev", "vol_regime", "multi_expiry", "smart_uoa", "hhi_pin", "pcr_vel",
                      "vrvp", "vwap_dev", "expected_move", "left_skew", "vrp", "put_call_wall"):
            rows = pd.read_sql(
                "SELECT correct FROM signal_accuracy WHERE ticker=? AND model_name=?"
                " AND correct>=0 ORDER BY"
                " substr(trade_date,7,4)||substr(trade_date,1,2)||substr(trade_date,4,2) DESC LIMIT 20",
                conn, params=(ticker.upper(), model))
            if len(rows) >= 5:
                acc = float(rows["correct"].mean())
                weight = max(0.2, min(1.8, (acc - 0.5) * 2.0 + 1.0))
                today_s = datetime.now(timezone.utc).replace(tzinfo=None).strftime("%m-%d-%Y")
                conn.execute(
                    "INSERT OR REPLACE INTO signal_weights"
                    " (ticker, model_name, accuracy_20d, weight, last_updated)"
                    " VALUES (?,?,?,?,?)",
                    (ticker.upper(), model, round(acc, 3), round(weight, 3), today_s))
        conn.commit()
    except Exception as e:
        log.debug(f"_update_hp_outcomes {ticker}: {e}")


def _get_hp_weights(ticker, conn):
    """Adaptive per-model weights based on recent 20-day accuracy."""
    defaults = {m: 1.0 for m in (
        "gex", "pcr_z", "oi_momentum", "gamma_pin", "vol_flow", "iv_skew",
        "rv_iv", "oi_term_struct", "maxpain_vel", "iv_rank",
        "pcp_dev", "vol_regime", "multi_expiry", "smart_uoa", "hhi_pin", "pcr_vel",
        "vrvp", "vwap_dev", "expected_move", "left_skew", "vrp", "put_call_wall",
    )}
    try:
        df = pd.read_sql("SELECT model_name, weight FROM signal_weights WHERE ticker=?",
                         conn, params=(ticker.upper(),))
        for _, r in df.iterrows():
            defaults[r["model_name"]] = float(r["weight"])
    except Exception:
        pass
    return defaults


def _hp_model_gex(ticker, conn, spot):
    try:
        dates = pd.read_sql(
            "SELECT DISTINCT trade_date_now FROM options_change WHERE ticker=?"
            " ORDER BY substr(trade_date_now,7,4)||substr(trade_date_now,1,2)||substr(trade_date_now,4,2) DESC LIMIT 3",
            conn, params=(ticker.upper(),))
        if len(dates) < 2 or spot <= 0:
            return {"signal": "NEUTRAL", "prob": 50, "gex": 0, "regime": "unknown",
                    "reason": "Insufficient data for GEX"}

        today_d = dates.iloc[0]["trade_date_now"]
        prev_d  = dates.iloc[1]["trade_date_now"]

        def _gex(date_str):
            df = pd.read_sql(
                "SELECT strike, expiry_date,"
                " SUM(openInt_Call_now) AS c_oi, SUM(openInt_Put_now) AS p_oi,"
                " AVG(CASE WHEN lastPrice_Call_now>0 THEN lastPrice_Call_now END) AS c_px"
                " FROM options_change WHERE ticker=? AND trade_date_now=?"
                " GROUP BY strike, expiry_date",
                conn, params=(ticker.upper(), date_str))
            if df.empty:
                return 0.0
            ref_dt = datetime.strptime(date_str, "%m-%d-%Y")
            total = 0.0
            for _, r in df.iterrows():
                K = float(r["strike"])
                c_oi = float(r["c_oi"] or 0)
                p_oi = float(r["p_oi"] or 0)
                c_px = float(r["c_px"] or 0)
                try:
                    exp_dt = datetime.strptime(str(r["expiry_date"]), "%m-%d-%Y")
                except Exception:
                    continue
                T = max((exp_dt - ref_dt).days / 365.0, 1 / 365.0)
                moneyness = abs(K - spot) / spot
                sigma = (_implied_vol_hp(c_px, spot, K, T)
                         if (moneyness < 0.06 and c_px > 0.05) else 0.30)
                sigma = max(0.05, min(sigma, 3.0))
                gamma = _bs_gamma_hp(spot, K, T, sigma)
                total += (c_oi - p_oi) * gamma * 100 * spot
            return total

        gex_now  = _gex(today_d)
        gex_prev = _gex(prev_d)
        flip = (gex_prev > 0) != (gex_now > 0)

        if gex_now > 0:
            regime = "POSITIVE"
            signal, prob = ("BEAR", 71) if flip else ("NEUTRAL", 62)
        else:
            regime = "NEGATIVE"
            signal, prob = ("BULL", 72) if flip else ("BULL", 58)

        reason = (
            f"GEX {gex_now/1e6:+.1f}M (prev {gex_prev/1e6:+.1f}M) | regime: {regime} GEX"
            + (" | ⚡ FLIP — direction change!" if flip else "")
        )
        return {"signal": signal, "prob": prob, "gex": gex_now, "gex_prev": gex_prev,
                "regime": regime, "flip": flip, "reason": reason}
    except Exception as e:
        log.debug(f"_hp_model_gex {ticker}: {e}")
        return {"signal": "NEUTRAL", "prob": 50, "gex": 0, "regime": "error", "reason": str(e)[:60]}


def _hp_model_pcr_z(ticker, conn):
    try:
        sd = pd.read_sql(
            "SELECT trade_date, pcr_oi FROM stock_daily WHERE ticker=?"
            " ORDER BY substr(trade_date,7,4)||substr(trade_date,1,2)||substr(trade_date,4,2) DESC LIMIT 25",
            conn, params=(ticker.upper(),))
        if len(sd) < 10:
            return {"signal": "NEUTRAL", "prob": 50, "reason": "Insufficient PCR history"}
        sd["pcr_oi"] = pd.to_numeric(sd["pcr_oi"], errors="coerce")
        sd = sd.dropna(subset=["pcr_oi"])
        if len(sd) < 8:
            return {"signal": "NEUTRAL", "prob": 50, "reason": "Too many null PCRs"}

        pcr_today = float(sd["pcr_oi"].iloc[0])
        if ticker.upper() == "SPY" and pcr_today > 5.0:
            return {"signal": "NEUTRAL", "prob": 50, "reason": "SPY expiry spike ignored"}

        hist = sd["pcr_oi"].iloc[1:21]
        mean, std = float(hist.mean()), float(hist.std())
        if std < 0.001:
            return {"signal": "NEUTRAL", "prob": 50, "reason": "PCR variance too low"}

        z = (pcr_today - mean) / std
        top3 = sd["pcr_oi"].iloc[:3].tolist()
        trend = "falling" if top3[0] < top3[2] else "rising"

        if z >= 2.5 and trend == "falling":
            signal, prob = "BULL", 79
            reason = f"PCR z={z:+.2f} EXTREME FEAR + trend falling = Peak Fear reversal"
        elif z >= 1.8 and trend == "falling":
            signal, prob = "BULL", 69
            reason = f"PCR z={z:+.2f} oversold + PCR unwinding"
        elif z >= 1.5:
            signal, prob = "BULL", 60
            reason = f"PCR z={z:+.2f} elevated fear — early bull signal"
        elif z <= -2.5 and trend == "rising":
            signal, prob = "BEAR", 77
            reason = f"PCR z={z:+.2f} EXTREME COMPLACENCY + trend rising = top warning"
        elif z <= -1.8 and trend == "rising":
            signal, prob = "BEAR", 68
            reason = f"PCR z={z:+.2f} overbought + PCR rising again"
        elif z <= -1.5:
            signal, prob = "BEAR", 59
            reason = f"PCR z={z:+.2f} low PCR = complacency"
        else:
            signal, prob = "NEUTRAL", 50
            reason = f"PCR z={z:+.2f} — neutral zone (need |z|>1.5)"

        return {"signal": signal, "prob": prob, "z": round(z, 2),
                "pcr": round(pcr_today, 3), "trend": trend, "reason": reason}
    except Exception as e:
        log.debug(f"_hp_model_pcr_z {ticker}: {e}")
        return {"signal": "NEUTRAL", "prob": 50, "reason": str(e)[:60]}


def _hp_model_oi_momentum(ticker, conn, spot):
    try:
        dates = pd.read_sql(
            "SELECT DISTINCT trade_date_now FROM options_change WHERE ticker=?"
            " ORDER BY substr(trade_date_now,7,4)||substr(trade_date_now,1,2)||substr(trade_date_now,4,2) DESC LIMIT 5",
            conn, params=(ticker.upper(),))
        if len(dates) < 4:
            return {"signal": "NEUTRAL", "prob": 50, "reason": "Need 4 days"}

        px = pd.read_sql(
            "SELECT trade_date, close FROM stock_daily WHERE ticker=?"
            " ORDER BY substr(trade_date,7,4)||substr(trade_date,1,2)||substr(trade_date,4,2) DESC LIMIT 5",
            conn, params=(ticker.upper(),))
        px["close"] = pd.to_numeric(px["close"], errors="coerce")

        daily = []
        for _, dr in dates.iterrows():
            d = dr["trade_date_now"]
            r = pd.read_sql(
                "SELECT SUM(openInt_Call_now) AS c, SUM(openInt_Put_now) AS p"
                " FROM options_change WHERE ticker=? AND trade_date_now=?",
                conn, params=(ticker.upper(), d))
            daily.append({"date": d, "c": float(r["c"].iloc[0] or 0), "p": float(r["p"].iloc[0] or 0)})

        extreme = False
        for i in range(min(3, len(px) - 1)):
            try:
                ret = abs(float(px["close"].iloc[i]) / float(px["close"].iloc[i + 1]) - 1)
                if ret > 0.03:
                    extreme = True; break
            except Exception:
                pass
        if extreme:
            return {"signal": "NEUTRAL", "prob": 50, "reason": "Extreme day (>3%) in window — filtered"}

        changes = [{"c_chg": daily[i]["c"] - daily[i+1]["c"],
                    "p_chg": daily[i]["p"] - daily[i+1]["p"]} for i in range(3)]
        c_bull = sum(1 for c in changes if c["c_chg"] > c["p_chg"] and c["c_chg"] > 0)
        p_bear = sum(1 for c in changes if c["p_chg"] > c["c_chg"] and c["p_chg"] > 0)
        net_c = sum(c["c_chg"] for c in changes)
        net_p = sum(c["p_chg"] for c in changes)

        price_move = 0.0
        try:
            price_move = (float(px["close"].iloc[0]) - float(px["close"].iloc[3])) / float(px["close"].iloc[3])
        except Exception:
            pass

        if c_bull >= 3 and net_c > abs(net_p) * 1.3:
            if price_move <= 0.01:
                signal, prob = "BULL", 76
                reason = f"3/3 days calls +{net_c:,.0f} while price {price_move*100:+.1f}% — SMART MONEY LONG"
            else:
                signal, prob = "BULL", 64
                reason = f"3/3 days calls +{net_c:,.0f} + price up {price_move*100:.1f}% — momentum confirm"
        elif p_bear >= 3 and net_p > abs(net_c) * 1.3:
            if price_move >= -0.01:
                signal, prob = "BEAR", 75
                reason = f"3/3 days puts +{net_p:,.0f} while price {price_move*100:+.1f}% — SMART MONEY SHORT"
            else:
                signal, prob = "BEAR", 63
                reason = f"3/3 days puts +{net_p:,.0f} + price down {abs(price_move)*100:.1f}% — confirm"
        elif c_bull >= 2:
            signal, prob = "BULL", 57
            reason = f"2/3 days call OI building (+{net_c:,.0f}) — moderate positioning"
        elif p_bear >= 2:
            signal, prob = "BEAR", 57
            reason = f"2/3 days put OI building (+{net_p:,.0f}) — moderate bear positioning"
        else:
            signal, prob = "NEUTRAL", 50
            reason = f"Mixed OI (C:{net_c:+,.0f} P:{net_p:+,.0f}) — no clear 3d pattern"

        return {"signal": signal, "prob": prob, "c_bull": c_bull, "p_bear": p_bear,
                "price_3d": round(price_move * 100, 2), "reason": reason}
    except Exception as e:
        log.debug(f"_hp_model_oi_momentum {ticker}: {e}")
        return {"signal": "NEUTRAL", "prob": 50, "reason": str(e)[:60]}


def _hp_model_gamma_pin(ticker, conn, spot):
    try:
        expiries = pd.read_sql(
            "SELECT DISTINCT expiry_date FROM options_change WHERE ticker=? ORDER BY expiry_date ASC",
            conn, params=(ticker.upper(),))
        if expiries.empty:
            return {"signal": "NEUTRAL", "prob": 50, "reason": "No expiry data"}

        today_dt = datetime.now(timezone.utc).replace(tzinfo=None)
        nearest, min_dte = None, 999
        for _, er in expiries.iterrows():
            try:
                exp_dt = datetime.strptime(str(er["expiry_date"]), "%m-%d-%Y")
                dte = (exp_dt - today_dt).days
                if 0 < dte < min_dte:
                    min_dte, nearest = dte, er["expiry_date"]
            except Exception:
                pass
        if nearest is None:
            return {"signal": "NEUTRAL", "prob": 50, "reason": "No valid expiry"}

        latest = pd.read_sql(
            "SELECT trade_date_now FROM options_change WHERE ticker=?"
            " ORDER BY substr(trade_date_now,7,4)||substr(trade_date_now,1,2)||substr(trade_date_now,4,2) DESC LIMIT 1",
            conn, params=(ticker.upper(),))
        if latest.empty:
            return {"signal": "NEUTRAL", "prob": 50, "reason": "No latest date"}
        lat_d = latest.iloc[0]["trade_date_now"]

        df = pd.read_sql(
            "SELECT strike, SUM(openInt_Call_now) AS c_oi, SUM(openInt_Put_now) AS p_oi"
            " FROM options_change WHERE ticker=? AND expiry_date=? AND trade_date_now=?"
            " GROUP BY strike ORDER BY strike",
            conn, params=(ticker.upper(), nearest, lat_d))
        if len(df) < 3:
            return {"signal": "NEUTRAL", "prob": 50, "reason": "Too few strikes"}

        df["c_oi"] = pd.to_numeric(df["c_oi"], errors="coerce").fillna(0)
        df["p_oi"] = pd.to_numeric(df["p_oi"], errors="coerce").fillna(0)
        df["tot"]  = df["c_oi"] + df["p_oi"]

        strikes = sorted(df["strike"].unique())
        mp_pain, mp_k = float("inf"), spot
        for tk in strikes:
            pain = (df[df["strike"] < tk]["c_oi"] * (tk - df[df["strike"] < tk]["strike"])).sum() + \
                   (df[df["strike"] > tk]["p_oi"] * (df[df["strike"] > tk]["strike"] - tk)).sum()
            if pain < mp_pain:
                mp_pain, mp_k = pain, tk

        mu, sig2 = float(df["tot"].mean()), float(df["tot"].std())
        walls = df[df["tot"] >= mu + 2.5 * sig2]["strike"].tolist()
        dist_mp = (mp_k - spot) / spot * 100

        if min_dte <= 7:
            ad = abs(dist_mp)
            if ad <= 1.5:
                signal, prob = "NEUTRAL", 83
                reason = f"DTE={min_dte} | MP ${mp_k:.0f} ({dist_mp:+.1f}%) STRONG PIN — sell straddle"
            elif ad <= 3.0:
                signal = "BULL" if dist_mp > 0 else "BEAR"
                prob = 73
                reason = f"DTE={min_dte} | MP ${mp_k:.0f} ({dist_mp:+.1f}%) — drift toward max pain"
            else:
                signal = "BULL" if dist_mp > 0 else "BEAR"
                prob = 59
                reason = f"DTE={min_dte} | MP ${mp_k:.0f} ({dist_mp:+.1f}%) — moderate pull"
        else:
            above = [w for w in walls if w > spot]
            below = [w for w in walls if w < spot]
            if above and below:
                ceil_w = min(above); floor_w = max(below)
                dc = (ceil_w - spot) / spot * 100
                df2 = (spot - floor_w) / spot * 100
                if dc < 2.0 and df2 > 3.0:
                    signal, prob = "BEAR", 66
                    reason = f"DTE={min_dte} | Gamma ceiling ${ceil_w:.0f} (+{dc:.1f}%) — resistance"
                elif df2 < 2.0 and dc > 3.0:
                    signal, prob = "BULL", 65
                    reason = f"DTE={min_dte} | Gamma floor ${floor_w:.0f} (-{df2:.1f}%) — support"
                else:
                    signal, prob = "NEUTRAL", 54
                    reason = f"DTE={min_dte} | Ceil ${ceil_w:.0f}(+{dc:.1f}%) Floor ${floor_w:.0f}(-{df2:.1f}%)"
            else:
                signal, prob = "NEUTRAL", 52
                reason = f"DTE={min_dte} | MP ${mp_k:.0f} ({dist_mp:+.1f}%)"

        return {"signal": signal, "prob": prob, "dte": min_dte, "max_pain": round(mp_k, 2),
                "dist_mp": round(dist_mp, 2), "walls": [round(w, 2) for w in walls[:4]], "reason": reason}
    except Exception as e:
        log.debug(f"_hp_model_gamma_pin {ticker}: {e}")
        return {"signal": "NEUTRAL", "prob": 50, "reason": str(e)[:60]}


def _hp_model_vol_flow(ticker, conn):
    try:
        dates = pd.read_sql(
            "SELECT DISTINCT trade_date_now FROM options_change WHERE ticker=?"
            " ORDER BY substr(trade_date_now,7,4)||substr(trade_date_now,1,2)||substr(trade_date_now,4,2) DESC LIMIT 8",
            conn, params=(ticker.upper(),))
        if len(dates) < 6:
            return {"signal": "NEUTRAL", "prob": 50, "reason": "Need 6 days for vol avg"}

        daily = []
        for _, dr in dates.iterrows():
            d = dr["trade_date_now"]
            r = pd.read_sql(
                "SELECT SUM(vol_Call_now) AS cv, SUM(vol_Put_now) AS pv,"
                " SUM(openInt_Call_now) AS co, SUM(openInt_Put_now) AS po"
                " FROM options_change WHERE ticker=? AND trade_date_now=?",
                conn, params=(ticker.upper(), d))
            daily.append({"cv": float(r["cv"].iloc[0] or 0), "pv": float(r["pv"].iloc[0] or 0),
                           "co": float(r["co"].iloc[0] or 0), "po": float(r["po"].iloc[0] or 0)})

        today, hist = daily[0], daily[1:6]
        avg_cv = sum(d["cv"] for d in hist) / max(len(hist), 1)
        avg_pv = sum(d["pv"] for d in hist) / max(len(hist), 1)
        cr = today["cv"] / max(avg_cv, 1)
        pr = today["pv"] / max(avg_pv, 1)
        c_chg = today["co"] - daily[1]["co"] if len(daily) >= 2 else 0
        p_chg = today["po"] - daily[1]["po"] if len(daily) >= 2 else 0
        vpcr = today["pv"] / max(today["cv"], 1)

        if cr >= 2.0 and c_chg > 0 and cr > pr * 1.3:
            signal, prob = "BULL", (74 if cr >= 3.0 else 64)
            reason = f"Call vol {cr:.1f}×avg + OI +{c_chg:,.0f} — institutional CALL buying"
        elif pr >= 2.0 and p_chg > 0 and pr > cr * 1.3:
            signal, prob = "BEAR", (73 if pr >= 3.0 else 63)
            reason = f"Put vol {pr:.1f}×avg + OI +{p_chg:,.0f} — institutional PUT buying"
        elif cr >= 2.0 and c_chg < 0:
            signal, prob = "BEAR", 61
            reason = f"Call vol {cr:.1f}×avg BUT OI falling {c_chg:,.0f} — longs EXITING"
        elif pr >= 2.0 and p_chg < 0:
            signal, prob = "BULL", 60
            reason = f"Put vol {pr:.1f}×avg BUT OI falling {p_chg:,.0f} — bears COVERING"
        elif vpcr < 0.33:
            signal, prob = "BULL", 57
            reason = f"Vol PCR {vpcr:.2f} very low (call-heavy flow) — bullish tilt"
        elif vpcr > 1.6:
            signal, prob = "BEAR", 57
            reason = f"Vol PCR {vpcr:.2f} elevated (put-heavy flow) — bearish tilt"
        else:
            signal, prob = "NEUTRAL", 50
            reason = f"Vol PCR {vpcr:.2f} | C:{cr:.1f}× P:{pr:.1f}× — no extreme flow"

        return {"signal": signal, "prob": prob, "cr": round(cr, 2), "pr": round(pr, 2),
                "vpcr": round(vpcr, 3), "c_chg": int(c_chg), "p_chg": int(p_chg), "reason": reason}
    except Exception as e:
        log.debug(f"_hp_model_vol_flow {ticker}: {e}")
        return {"signal": "NEUTRAL", "prob": 50, "reason": str(e)[:60]}


def _hp_model_iv_skew(ticker, conn, spot):
    try:
        latest = pd.read_sql(
            "SELECT trade_date_now FROM options_change WHERE ticker=?"
            " ORDER BY substr(trade_date_now,7,4)||substr(trade_date_now,1,2)||substr(trade_date_now,4,2) DESC LIMIT 1",
            conn, params=(ticker.upper(),))
        if latest.empty or spot <= 0:
            return {"signal": "NEUTRAL", "prob": 50, "reason": "No data"}
        lat_d = latest.iloc[0]["trade_date_now"]

        expiries = pd.read_sql(
            "SELECT DISTINCT expiry_date FROM options_change WHERE ticker=? ORDER BY expiry_date",
            conn, params=(ticker.upper(),))
        today_dt = datetime.now(timezone.utc).replace(tzinfo=None)
        nearest, min_dte = None, 999
        for _, er in expiries.iterrows():
            try:
                exp_dt = datetime.strptime(str(er["expiry_date"]), "%m-%d-%Y")
                dte = (exp_dt - today_dt).days
                if 1 < dte < min_dte:
                    min_dte, nearest = dte, er["expiry_date"]
            except Exception:
                pass
        if nearest is None or min_dte < 2:
            return {"signal": "NEUTRAL", "prob": 50, "reason": "No valid expiry for IV"}

        df = pd.read_sql(
            "SELECT strike,"
            " AVG(CASE WHEN lastPrice_Call_now>0 THEN lastPrice_Call_now END) AS c_px,"
            " AVG(CASE WHEN lastPrice_Put_now>0 THEN lastPrice_Put_now END) AS p_px"
            " FROM options_change WHERE ticker=? AND expiry_date=? AND trade_date_now=?"
            " GROUP BY strike ORDER BY strike",
            conn, params=(ticker.upper(), nearest, lat_d))
        if df.empty:
            return {"signal": "NEUTRAL", "prob": 50, "reason": "No strike data"}

        T = max(min_dte / 365.0, 1 / 365.0)
        df["c_px"] = pd.to_numeric(df["c_px"], errors="coerce").fillna(0)
        df["p_px"] = pd.to_numeric(df["p_px"], errors="coerce").fillna(0)
        df["dist"]  = (df["strike"] - spot).abs()

        atm_ivs = []
        for _, r in df.nsmallest(3, "dist").iterrows():
            K = float(r["strike"])
            if float(r["c_px"]) > 0.1:
                iv = _implied_vol_hp(float(r["c_px"]), spot, K, T)
                if 0.05 < iv < 3.0:
                    atm_ivs.append(iv)
        atm_iv = float(np.mean(atm_ivs)) if atm_ivs else 0.30

        otm_c_iv, otm_p_iv = [], []
        for _, r in df.iterrows():
            K = float(r["strike"])
            if spot * 1.05 <= K <= spot * 1.13 and float(r["c_px"]) > 0.05:
                iv = _implied_vol_hp(float(r["c_px"]), spot, K, T)
                if 0.05 < iv < 3.0: otm_c_iv.append(iv)
            if spot * 0.87 <= K <= spot * 0.95 and float(r["p_px"]) > 0.05:
                iv = _implied_vol_hp(float(r["p_px"]), spot, K, T)
                if 0.05 < iv < 3.0: otm_p_iv.append(iv)

        avg_civ = float(np.mean(otm_c_iv)) if otm_c_iv else atm_iv
        avg_piv = float(np.mean(otm_p_iv)) if otm_p_iv else atm_iv
        skew = avg_piv / max(avg_civ, 0.01)
        iv_pct = atm_iv * 100

        if skew >= 2.0:
            signal, prob = "BULL", 72
            reason = f"Put/Call skew {skew:.2f}× EXTREME FEAR — contrarian bull (sell puts)"
        elif skew >= 1.5:
            signal, prob = "BULL", 62
            reason = f"Put/Call skew {skew:.2f}× elevated fear — mild bull lean"
        elif skew <= 0.7:
            signal, prob = "BEAR", 66
            reason = f"Put/Call skew {skew:.2f}× (call skew) — complacency → bear/sell calls"
        elif iv_pct >= 40:
            signal, prob = "NEUTRAL", 70
            reason = f"ATM IV {iv_pct:.0f}% HIGH → sell premium (IC/straddle) | skew {skew:.2f}"
        elif iv_pct <= 15:
            signal, prob = "NEUTRAL", 55
            reason = f"ATM IV {iv_pct:.0f}% low → buy options cheap | skew {skew:.2f}"
        else:
            signal, prob = "NEUTRAL", 52
            reason = f"ATM IV {iv_pct:.0f}% normal | skew {skew:.2f} — no IV edge"

        return {"signal": signal, "prob": prob, "atm_iv": round(iv_pct, 1),
                "skew": round(skew, 2), "put_iv": round(avg_piv * 100, 1),
                "call_iv": round(avg_civ * 100, 1), "reason": reason}
    except Exception as e:
        log.debug(f"_hp_model_iv_skew {ticker}: {e}")
        return {"signal": "NEUTRAL", "prob": 50, "reason": str(e)[:60]}


def _hp_walk_forward_backtest(ticker, conn, extreme_filter=0.03):
    """
    Walk-forward backtest on historical data.
    PCR-Z, Vol-Flow, OI-Trend computable from daily aggregates.
    Removes extreme days (>3% next-day move) per user guidance.
    """
    try:
        sd = pd.read_sql(
            "SELECT trade_date, close, pcr_oi FROM stock_daily WHERE ticker=?"
            " ORDER BY substr(trade_date,7,4)||substr(trade_date,1,2)||substr(trade_date,4,2) ASC",
            conn, params=(ticker.upper(),))
        oi = pd.read_sql(
            "SELECT trade_date_now, SUM(vol_Call_now) AS cv, SUM(vol_Put_now) AS pv,"
            " SUM(openInt_Call_now) AS co, SUM(openInt_Put_now) AS po"
            " FROM options_change WHERE ticker=? GROUP BY trade_date_now"
            " ORDER BY substr(trade_date_now,7,4)||substr(trade_date_now,1,2)||substr(trade_date_now,4,2) ASC",
            conn, params=(ticker.upper(),))

        sd["close"]    = pd.to_numeric(sd["close"],  errors="coerce")
        sd["pcr_oi"]   = pd.to_numeric(sd["pcr_oi"], errors="coerce")
        sd["next_ret"] = sd["close"].shift(-1) / sd["close"] - 1
        merged = sd.merge(oi, left_on="trade_date", right_on="trade_date_now", how="inner")
        merged = merged.dropna(subset=["close", "next_ret", "pcr_oi"]).reset_index(drop=True)

        n_total   = len(merged)
        ext_mask  = merged["next_ret"].abs() > extreme_filter
        n_extreme = int(ext_mask.sum())
        clean     = merged[~ext_mask].reset_index(drop=True)

        if len(clean) < 15:
            return {"meta": {"n_total": n_total, "n_extreme": n_extreme, "n_clean": len(clean)}}

        N = 20
        res = {m: [] for m in ("pcr_z", "vol_flow", "oi_trend", "ensemble")}

        for i in range(N, len(clean)):
            row  = clean.iloc[i]
            hist = clean.iloc[max(0, i - N):i]
            actual = 1 if float(row["next_ret"]) > 0.003 else (-1 if float(row["next_ret"]) < -0.003 else 0)
            if actual == 0:
                continue
            sigs = []

            # PCR-Z
            pm, ps = float(hist["pcr_oi"].mean()), float(hist["pcr_oi"].std())
            if ps > 0:
                z = (float(row["pcr_oi"]) - pm) / ps
                if z >= 1.5:
                    sigs.append(1); res["pcr_z"].append(1 == actual)
                elif z <= -1.5:
                    sigs.append(-1); res["pcr_z"].append(-1 == actual)

            # Vol flow
            acv = float(hist["cv"].mean()) or 1
            apv = float(hist["pv"].mean()) or 1
            cr  = float(row["cv"]) / acv
            pr  = float(row["pv"]) / apv
            c_chg = float(row["co"]) - float(hist["co"].iloc[-1])
            p_chg = float(row["po"]) - float(hist["po"].iloc[-1])
            if cr >= 2.0 and c_chg > 0 and cr > pr * 1.3:
                sigs.append(1); res["vol_flow"].append(1 == actual)
            elif pr >= 2.0 and p_chg > 0 and pr > cr * 1.3:
                sigs.append(-1); res["vol_flow"].append(-1 == actual)

            # OI 3-day trend
            if i >= N + 2:
                c3 = [float(clean["co"].iloc[j]) for j in (i, i-1, i-2, i-3)]
                p3 = [float(clean["po"].iloc[j]) for j in (i, i-1, i-2, i-3)]
                cc = [c3[k] - c3[k+1] for k in range(3)]
                pc = [p3[k] - p3[k+1] for k in range(3)]
                cb = sum(1 for k in range(3) if cc[k] > pc[k] and cc[k] > 0)
                pb = sum(1 for k in range(3) if pc[k] > cc[k] and pc[k] > 0)
                if cb >= 3:
                    sigs.append(1); res["oi_trend"].append(1 == actual)
                elif pb >= 3:
                    sigs.append(-1); res["oi_trend"].append(-1 == actual)

            if len(sigs) >= 2:
                vote = sum(sigs)
                es = 1 if vote > 0 else (-1 if vote < 0 else 0)
                if es != 0:
                    res["ensemble"].append(es == actual)

        out = {}
        for m, hits in res.items():
            if len(hits) >= 5:
                out[m] = {"acc": round(sum(hits) / len(hits) * 100, 1), "n": len(hits)}
        out["meta"] = {"n_total": n_total, "n_extreme": n_extreme, "n_clean": len(clean)}
        return out
    except Exception as e:
        log.debug(f"_hp_walk_forward {ticker}: {e}")
        return {}


def _hp_model_rv_iv(ticker, conn, spot):
    try:
        from datetime import datetime as _dt2
        prices = pd.read_sql(
            "SELECT close FROM stock_daily WHERE ticker=?"
            " ORDER BY substr(trade_date,7,4)||substr(trade_date,1,2)||substr(trade_date,4,2) DESC LIMIT 35",
            conn, params=(ticker.upper(),))
        if len(prices) < 22 or spot <= 0:
            return {"signal": "NEUTRAL", "prob": 50, "reason": "Insufficient history"}
        rets = prices["close"].pct_change().dropna()
        rv30 = float(rets.std() * _math.sqrt(252) * 100)

        latest = pd.read_sql(
            "SELECT trade_date_now FROM options_change WHERE ticker=?"
            " ORDER BY substr(trade_date_now,7,4)||substr(trade_date_now,1,2)||substr(trade_date_now,4,2) DESC LIMIT 1",
            conn, params=(ticker.upper(),))
        if latest.empty:
            return {"signal": "NEUTRAL", "prob": 50, "reason": "No options data"}
        ld = latest.iloc[0, 0]
        today = _dt2.now().date()

        opts = pd.read_sql(
            "SELECT strike, expiry_date, lastPrice_Call_now FROM options_change"
            " WHERE ticker=? AND trade_date_now=? AND lastPrice_Call_now>0.5"
            " AND ABS(strike-?)/? < 0.04",
            conn, params=(ticker.upper(), ld, spot, spot))
        if opts.empty:
            return {"signal": "NEUTRAL", "prob": 50, "reason": "No ATM options", "rv": round(rv30, 1)}

        ivs = []
        for _, row in opts.iterrows():
            try:
                exp = _dt2.strptime(row["expiry_date"], "%m-%d-%Y").date()
                T   = max((exp - today).days / 365.0, 1/365.0)
                iv  = _implied_vol_hp(float(row["lastPrice_Call_now"]), spot, float(row["strike"]), T)
                if 0.05 < iv < 3.0:
                    ivs.append(iv * 100)
            except Exception:
                pass
        if not ivs:
            return {"signal": "NEUTRAL", "prob": 50, "reason": "IV calc failed", "rv": round(rv30, 1)}

        atm_iv = float(np.median(ivs))
        spread = atm_iv - rv30

        if spread >= 10:
            return {"signal": "NEUTRAL", "prob": 74,
                    "atm_iv": round(atm_iv, 1), "rv": round(rv30, 1), "spread": round(spread, 1),
                    "reason": f"IV {atm_iv:.0f}% >> RV {rv30:.0f}% (+{spread:.0f}pp) → SELL PREMIUM (Bali 2009)"}
        elif spread >= 5:
            return {"signal": "NEUTRAL", "prob": 63,
                    "atm_iv": round(atm_iv, 1), "rv": round(rv30, 1), "spread": round(spread, 1),
                    "reason": f"IV {atm_iv:.0f}% > RV {rv30:.0f}% (+{spread:.0f}pp) → mild premium edge"}
        elif spread <= -8:
            return {"signal": "BULL", "prob": 65,
                    "atm_iv": round(atm_iv, 1), "rv": round(rv30, 1), "spread": round(spread, 1),
                    "reason": f"RV {rv30:.0f}% >> IV {atm_iv:.0f}% → options cheap, buy gamma/direction"}
        else:
            return {"signal": "NEUTRAL", "prob": 50,
                    "atm_iv": round(atm_iv, 1), "rv": round(rv30, 1), "spread": round(spread, 1),
                    "reason": f"IV {atm_iv:.0f}% ≈ RV {rv30:.0f}% (spread {spread:+.0f}pp)"}
    except Exception as e:
        log.debug(f"_hp_model_rv_iv {ticker}: {e}")
        return {"signal": "NEUTRAL", "prob": 50, "reason": str(e)[:60]}


def _hp_model_oi_term_structure(ticker, conn):
    try:
        from datetime import datetime as _dt2
        today = _dt2.now().date()
        ld = pd.read_sql(
            "SELECT trade_date_now FROM options_change WHERE ticker=?"
            " ORDER BY substr(trade_date_now,7,4)||substr(trade_date_now,1,2)||substr(trade_date_now,4,2) DESC LIMIT 1",
            conn, params=(ticker.upper(),))
        if ld.empty:
            return {"signal": "NEUTRAL", "prob": 50, "reason": "No options data"}
        latest = ld.iloc[0, 0]

        df = pd.read_sql(
            "SELECT expiry_date, SUM(openInt_Call_now) AS co, SUM(openInt_Put_now) AS po"
            " FROM options_change WHERE ticker=? AND trade_date_now=?"
            " GROUP BY expiry_date",
            conn, params=(ticker.upper(), latest))
        if df.empty:
            return {"signal": "NEUTRAL", "prob": 50, "reason": "No expiry data"}

        near_c = near_p = far_c = far_p = 0.0
        for _, r in df.iterrows():
            try:
                exp = _dt2.strptime(r["expiry_date"], "%m-%d-%Y").date()
                dte = (exp - today).days
                if dte < 1:
                    continue
                if dte <= 21:
                    near_c += float(r["co"] or 0); near_p += float(r["po"] or 0)
                elif dte >= 45:
                    far_c  += float(r["co"] or 0); far_p  += float(r["po"] or 0)
            except Exception:
                pass

        near_pcr = (near_p / near_c) if near_c > 0 else 1.0
        far_pcr  = (far_p  / far_c)  if far_c  > 0 else 1.0
        ts_ratio = near_pcr / far_pcr if far_pcr > 0 else 1.0

        if near_pcr > 1.8 and ts_ratio > 1.5:
            return {"signal": "BEAR", "prob": 68, "near_pcr": round(near_pcr, 2), "far_pcr": round(far_pcr, 2),
                    "reason": f"Near PCR {near_pcr:.2f} >> Far {far_pcr:.2f} → panic hedging, BEAR"}
        elif near_pcr < 0.6 and ts_ratio < 0.7:
            return {"signal": "BULL", "prob": 65, "near_pcr": round(near_pcr, 2), "far_pcr": round(far_pcr, 2),
                    "reason": f"Near PCR {near_pcr:.2f} << Far {far_pcr:.2f} → near-term call buying, BULL"}
        elif far_pcr > 1.5 and near_pcr < 1.0:
            return {"signal": "BULL", "prob": 62, "near_pcr": round(near_pcr, 2), "far_pcr": round(far_pcr, 2),
                    "reason": f"Far-term put build ({far_pcr:.2f}) with calm near ({near_pcr:.2f}) → institutional BULL"}
        else:
            return {"signal": "NEUTRAL", "prob": 50, "near_pcr": round(near_pcr, 2), "far_pcr": round(far_pcr, 2),
                    "reason": f"Term struct balanced: near PCR {near_pcr:.2f} / far {far_pcr:.2f}"}
    except Exception as e:
        log.debug(f"_hp_model_oi_term_structure {ticker}: {e}")
        return {"signal": "NEUTRAL", "prob": 50, "reason": str(e)[:60]}


def _hp_model_maxpain_velocity(ticker, conn, spot):
    try:
        from datetime import datetime as _dt2
        dates = pd.read_sql(
            "SELECT DISTINCT trade_date_now FROM options_change WHERE ticker=?"
            " ORDER BY substr(trade_date_now,7,4)||substr(trade_date_now,1,2)||substr(trade_date_now,4,2) DESC LIMIT 4",
            conn, params=(ticker.upper(),))
        if len(dates) < 3 or spot <= 0:
            return {"signal": "NEUTRAL", "prob": 50, "reason": "Need ≥3 days data"}

        def _mp(dt_str):
            df = pd.read_sql(
                "SELECT strike, SUM(openInt_Call_now) AS co, SUM(openInt_Put_now) AS po"
                " FROM options_change WHERE ticker=? AND trade_date_now=? GROUP BY strike",
                conn, params=(ticker.upper(), dt_str))
            if df.empty:
                return None
            strikes = df["strike"].tolist()
            best_s, best_loss = strikes[0], float("inf")
            for test in strikes:
                loss = sum(max(test - s, 0) * float(row_r["co"]) + max(s - test, 0) * float(row_r["po"])
                           for s, row_r in zip(strikes, df.to_dict("records")))
                if loss < best_loss:
                    best_loss = loss; best_s = test
            return float(best_s)

        mps = [_mp(d) for d in dates.iloc[:, 0].tolist()]
        mps = [m for m in mps if m is not None]
        if len(mps) < 3:
            return {"signal": "NEUTRAL", "prob": 50, "reason": "Max pain calc insufficient"}

        v1 = mps[0] - mps[1]   # most recent change
        v2 = mps[1] - mps[2]   # prior change
        trend_up   = v1 > 0 and v2 > 0
        trend_down = v1 < 0 and v2 < 0
        pct_move   = abs(v1) / spot * 100 if spot > 0 else 0

        if trend_up and pct_move >= 0.3:
            return {"signal": "BULL", "prob": 66, "mp_now": mps[0], "mp_vel": round(v1, 2),
                    "reason": f"Max pain rising {mps[2]:.0f}→{mps[1]:.0f}→{mps[0]:.0f} → dealer hedge BULL"}
        elif trend_down and pct_move >= 0.3:
            return {"signal": "BEAR", "prob": 66, "mp_now": mps[0], "mp_vel": round(v1, 2),
                    "reason": f"Max pain falling {mps[2]:.0f}→{mps[1]:.0f}→{mps[0]:.0f} → dealer hedge BEAR"}
        else:
            return {"signal": "NEUTRAL", "prob": 50, "mp_now": mps[0], "mp_vel": round(v1, 2),
                    "reason": f"Max pain stable ≈${mps[0]:.0f} (vel {v1:+.2f})"}
    except Exception as e:
        log.debug(f"_hp_model_maxpain_velocity {ticker}: {e}")
        return {"signal": "NEUTRAL", "prob": 50, "reason": str(e)[:60]}


def _hp_model_iv_rank(ticker, conn, spot):
    try:
        from datetime import datetime as _dt2
        today = _dt2.now().date()

        dates = pd.read_sql(
            "SELECT DISTINCT trade_date_now FROM options_change WHERE ticker=?"
            " ORDER BY substr(trade_date_now,7,4)||substr(trade_date_now,1,2)||substr(trade_date_now,4,2) DESC LIMIT 65",
            conn, params=(ticker.upper(),))
        if len(dates) < 20 or spot <= 0:
            return {"signal": "NEUTRAL", "prob": 50, "reason": "Need 20+ days for IV rank"}

        def _atm_iv(dt_str):
            opts = pd.read_sql(
                "SELECT strike, expiry_date, lastPrice_Call_now FROM options_change"
                " WHERE ticker=? AND trade_date_now=? AND lastPrice_Call_now>0.3"
                " AND ABS(strike-?)/? < 0.05",
                conn, params=(ticker.upper(), dt_str, spot, spot))
            ivs = []
            for _, r in opts.iterrows():
                try:
                    exp = _dt2.strptime(r["expiry_date"], "%m-%d-%Y").date()
                    T   = max((exp - today).days / 365.0, 1/365.0)
                    iv  = _implied_vol_hp(float(r["lastPrice_Call_now"]), spot, float(r["strike"]), T)
                    if 0.05 < iv < 3.0:
                        ivs.append(iv * 100)
                except Exception:
                    pass
            return float(np.median(ivs)) if ivs else None

        iv_series = []
        for dt_str in dates.iloc[:, 0].tolist():
            iv = _atm_iv(dt_str)
            if iv is not None:
                iv_series.append(iv)
            if len(iv_series) >= 60:
                break

        if len(iv_series) < 15:
            return {"signal": "NEUTRAL", "prob": 50, "reason": "Insufficient IV history"}

        curr_iv = iv_series[0]
        hist_iv = iv_series[1:]
        iv_rank = (sum(1 for x in hist_iv if x < curr_iv) / len(hist_iv)) * 100

        if iv_rank >= 80:
            return {"signal": "NEUTRAL", "prob": 75, "iv_rank": round(iv_rank, 0), "atm_iv": round(curr_iv, 1),
                    "reason": f"IV Rank {iv_rank:.0f}% (top decile) → SELL PREMIUM (tastytrade filter)"}
        elif iv_rank >= 60:
            return {"signal": "NEUTRAL", "prob": 63, "iv_rank": round(iv_rank, 0), "atm_iv": round(curr_iv, 1),
                    "reason": f"IV Rank {iv_rank:.0f}% (above avg) → mild premium-sell edge"}
        elif iv_rank <= 20:
            return {"signal": "BULL", "prob": 62, "iv_rank": round(iv_rank, 0), "atm_iv": round(curr_iv, 1),
                    "reason": f"IV Rank {iv_rank:.0f}% (bottom quintile) → options cheap, buy direction"}
        else:
            return {"signal": "NEUTRAL", "prob": 50, "iv_rank": round(iv_rank, 0), "atm_iv": round(curr_iv, 1),
                    "reason": f"IV Rank {iv_rank:.0f}% — normal range, no premium edge"}
    except Exception as e:
        log.debug(f"_hp_model_iv_rank {ticker}: {e}")
        return {"signal": "NEUTRAL", "prob": 50, "reason": str(e)[:60]}


def _hp_model_pcp_deviation(ticker, conn, spot):
    try:
        from datetime import datetime as _dt2
        today = _dt2.now().date()
        ld = pd.read_sql(
            "SELECT trade_date_now FROM options_change WHERE ticker=?"
            " ORDER BY substr(trade_date_now,7,4)||substr(trade_date_now,1,2)||substr(trade_date_now,4,2) DESC LIMIT 1",
            conn, params=(ticker.upper(),))
        if ld.empty or spot <= 0:
            return {"signal": "NEUTRAL", "prob": 50, "reason": "No data"}
        latest = ld.iloc[0, 0]

        opts = pd.read_sql(
            "SELECT strike, expiry_date, lastPrice_Call_now, lastPrice_Put_now"
            " FROM options_change WHERE ticker=? AND trade_date_now=?"
            " AND lastPrice_Call_now>0.3 AND lastPrice_Put_now>0.3"
            " AND ABS(strike-?)/? BETWEEN 0.01 AND 0.08",
            conn, params=(ticker.upper(), latest, spot, spot))
        if opts.empty:
            return {"signal": "NEUTRAL", "prob": 50, "reason": "No near-ATM pairs found"}

        deviations = []
        for _, r in opts.iterrows():
            try:
                K   = float(r["strike"]); C = float(r["lastPrice_Call_now"]); P = float(r["lastPrice_Put_now"])
                exp = _dt2.strptime(r["expiry_date"], "%m-%d-%Y").date()
                T   = max((exp - today).days / 365.0, 1/365.0)
                r_  = 0.05
                parity = spot - K * _math.exp(-r_ * T)   # C - P should equal this
                dev    = (C - P) - parity                  # positive = calls expensive
                deviations.append(dev / spot * 100)        # as % of spot
            except Exception:
                pass

        if not deviations:
            return {"signal": "NEUTRAL", "prob": 50, "reason": "PCP calc failed"}

        avg_dev = float(np.mean(deviations))
        pct_pos = sum(1 for d in deviations if d > 0) / len(deviations)

        if avg_dev > 0.4 and pct_pos > 0.65:
            return {"signal": "BULL", "prob": 67, "pcp_dev": round(avg_dev, 3),
                    "reason": f"PCP dev +{avg_dev:.2f}% ({pct_pos:.0%} pairs) → informed call buying (Cremers 2010)"}
        elif avg_dev < -0.4 and pct_pos < 0.35:
            return {"signal": "BEAR", "prob": 67, "pcp_dev": round(avg_dev, 3),
                    "reason": f"PCP dev {avg_dev:.2f}% ({1-pct_pos:.0%} pairs) → informed put buying (Cremers 2010)"}
        else:
            return {"signal": "NEUTRAL", "prob": 50, "pcp_dev": round(avg_dev, 3),
                    "reason": f"PCP deviation {avg_dev:+.3f}% — within normal bounds"}
    except Exception as e:
        log.debug(f"_hp_model_pcp_deviation {ticker}: {e}")
        return {"signal": "NEUTRAL", "prob": 50, "reason": str(e)[:60]}


def _hp_model_vol_regime(ticker, conn):
    try:
        prices = pd.read_sql(
            "SELECT close FROM stock_daily WHERE ticker=?"
            " ORDER BY substr(trade_date,7,4)||substr(trade_date,1,2)||substr(trade_date,4,2) DESC LIMIT 30",
            conn, params=(ticker.upper(),))
        if len(prices) < 22:
            return {"signal": "NEUTRAL", "prob": 50, "reason": "Need 22+ days for HV regime"}

        rets = prices["close"].pct_change().dropna().tolist()
        hv5  = float(np.std(rets[:5])  * _math.sqrt(252) * 100) if len(rets) >= 5  else 0
        hv20 = float(np.std(rets[:20]) * _math.sqrt(252) * 100) if len(rets) >= 20 else 0

        if hv20 < 0.1:
            return {"signal": "NEUTRAL", "prob": 50, "reason": "Near-zero 20d HV"}

        ratio = hv5 / hv20

        if ratio < 0.6:
            return {"signal": "NEUTRAL", "prob": 68, "hv5": round(hv5, 1), "hv20": round(hv20, 1), "ratio": round(ratio, 2),
                    "reason": f"5d HV {hv5:.1f}% << 20d {hv20:.1f}% (ratio {ratio:.2f}) → vol coiling → SELL STRADDLE (Sinclair)"}
        elif ratio > 1.5:
            return {"signal": "BULL", "prob": 62, "hv5": round(hv5, 1), "hv20": round(hv20, 1), "ratio": round(ratio, 2),
                    "reason": f"5d HV {hv5:.1f}% >> 20d {hv20:.1f}% (ratio {ratio:.2f}) → vol expanding → buy direction"}
        elif ratio > 1.2:
            return {"signal": "NEUTRAL", "prob": 55, "hv5": round(hv5, 1), "hv20": round(hv20, 1), "ratio": round(ratio, 2),
                    "reason": f"5d/20d HV {ratio:.2f} — mild vol expansion, monitor breakout"}
        else:
            return {"signal": "NEUTRAL", "prob": 50, "hv5": round(hv5, 1), "hv20": round(hv20, 1), "ratio": round(ratio, 2),
                    "reason": f"Vol regime normal: 5d {hv5:.1f}% / 20d {hv20:.1f}% (ratio {ratio:.2f})"}
    except Exception as e:
        log.debug(f"_hp_model_vol_regime {ticker}: {e}")
        return {"signal": "NEUTRAL", "prob": 50, "reason": str(e)[:60]}


def _hp_model_multi_expiry_oi(ticker, conn):
    try:
        from datetime import datetime as _dt2
        today = _dt2.now().date()
        dates = pd.read_sql(
            "SELECT DISTINCT trade_date_now FROM options_change WHERE ticker=?"
            " ORDER BY substr(trade_date_now,7,4)||substr(trade_date_now,1,2)||substr(trade_date_now,4,2) DESC LIMIT 3",
            conn, params=(ticker.upper(),))
        if len(dates) < 2:
            return {"signal": "NEUTRAL", "prob": 50, "reason": "Need 2+ days"}

        today_d = dates.iloc[0, 0]; prev_d = dates.iloc[1, 0]

        now_exp = pd.read_sql(
            "SELECT expiry_date, SUM(openInt_Call_now) AS co, SUM(openInt_Put_now) AS po"
            " FROM options_change WHERE ticker=? AND trade_date_now=? GROUP BY expiry_date",
            conn, params=(ticker.upper(), today_d))
        prv_exp = pd.read_sql(
            "SELECT expiry_date, SUM(openInt_Call_now) AS co, SUM(openInt_Put_now) AS po"
            " FROM options_change WHERE ticker=? AND trade_date_now=? GROUP BY expiry_date",
            conn, params=(ticker.upper(), prev_d))
        if now_exp.empty or prv_exp.empty:
            return {"signal": "NEUTRAL", "prob": 50, "reason": "Missing expiry data"}

        merged = now_exp.merge(prv_exp, on="expiry_date", suffixes=("_n", "_p"))
        bull_exp = bear_exp = 0
        for _, r in merged.iterrows():
            try:
                exp = _dt2.strptime(r["expiry_date"], "%m-%d-%Y").date()
                if (exp - today).days < 3:
                    continue
                dc = float(r["co_n"] or 0) - float(r["co_p"] or 0)
                dp = float(r["po_n"] or 0) - float(r["po_p"] or 0)
                if dc > 0 and dc > abs(dp) * 1.3:
                    bull_exp += 1
                elif dp > 0 and dp > abs(dc) * 1.3:
                    bear_exp += 1
            except Exception:
                pass

        if bull_exp >= 2 and bull_exp > bear_exp:
            return {"signal": "BULL", "prob": 70, "bull_exp": bull_exp, "bear_exp": bear_exp,
                    "reason": f"Call OI building in {bull_exp} expiries simultaneously → institutional BULL (Ni 2008)"}
        elif bear_exp >= 2 and bear_exp > bull_exp:
            return {"signal": "BEAR", "prob": 70, "bull_exp": bull_exp, "bear_exp": bear_exp,
                    "reason": f"Put OI building in {bear_exp} expiries simultaneously → institutional BEAR (Ni 2008)"}
        else:
            return {"signal": "NEUTRAL", "prob": 50, "bull_exp": bull_exp, "bear_exp": bear_exp,
                    "reason": f"OI mixed across expiries (bull:{bull_exp} / bear:{bear_exp})"}
    except Exception as e:
        log.debug(f"_hp_model_multi_expiry_oi {ticker}: {e}")
        return {"signal": "NEUTRAL", "prob": 50, "reason": str(e)[:60]}


def _hp_model_smart_money_uoa(ticker, conn):
    try:
        dates = pd.read_sql(
            "SELECT DISTINCT trade_date_now FROM options_change WHERE ticker=?"
            " ORDER BY substr(trade_date_now,7,4)||substr(trade_date_now,1,2)||substr(trade_date_now,4,2) DESC LIMIT 12",
            conn, params=(ticker.upper(),))
        if len(dates) < 5:
            return {"signal": "NEUTRAL", "prob": 50, "reason": "Need 5+ days for UOA baseline"}

        today_d = dates.iloc[0, 0]
        hist_dates = dates.iloc[1:, 0].tolist()

        def _voi(dt_str):
            r = pd.read_sql(
                "SELECT SUM(vol_Call_now) AS cv, SUM(vol_Put_now) AS pv,"
                " SUM(openInt_Call_now) AS co, SUM(openInt_Put_now) AS po"
                " FROM options_change WHERE ticker=? AND trade_date_now=?",
                conn, params=(ticker.upper(), dt_str))
            if r.empty or r.iloc[0]["co"] is None:
                return None
            row = r.iloc[0]
            co = float(row["co"] or 1); po = float(row["po"] or 1)
            cv = float(row["cv"] or 0); pv = float(row["pv"] or 0)
            return {"c_voi": cv / co, "p_voi": pv / po, "cv": cv, "pv": pv, "co": co, "po": po}

        curr = _voi(today_d)
        if curr is None:
            return {"signal": "NEUTRAL", "prob": 50, "reason": "No today data"}

        hist = [_voi(d) for d in hist_dates]
        hist = [h for h in hist if h is not None]
        if len(hist) < 4:
            return {"signal": "NEUTRAL", "prob": 50, "reason": "Insufficient baseline"}

        avg_c_voi = float(np.mean([h["c_voi"] for h in hist]))
        avg_p_voi = float(np.mean([h["p_voi"] for h in hist]))
        c_surge = curr["c_voi"] / avg_c_voi if avg_c_voi > 0 else 1.0
        p_surge = curr["p_voi"] / avg_p_voi if avg_p_voi > 0 else 1.0
        c_oi_growing = curr["co"] > float(np.mean([h["co"] for h in hist[:3]]))
        p_oi_growing = curr["po"] > float(np.mean([h["po"] for h in hist[:3]]))

        if c_surge >= 2.5 and c_oi_growing and c_surge > p_surge * 1.5:
            return {"signal": "BULL", "prob": 73, "c_surge": round(c_surge, 1), "p_surge": round(p_surge, 1),
                    "reason": f"Call Vol/OI {c_surge:.1f}x avg + OI growing → smart money calls (Amin 2004)"}
        elif p_surge >= 2.5 and p_oi_growing and p_surge > c_surge * 1.5:
            return {"signal": "BEAR", "prob": 73, "c_surge": round(c_surge, 1), "p_surge": round(p_surge, 1),
                    "reason": f"Put Vol/OI {p_surge:.1f}x avg + OI growing → smart money puts (Amin 2004)"}
        elif c_surge >= 1.8 and c_surge > p_surge:
            return {"signal": "BULL", "prob": 60, "c_surge": round(c_surge, 1), "p_surge": round(p_surge, 1),
                    "reason": f"Moderate call activity surge {c_surge:.1f}x — watch for confirmation"}
        elif p_surge >= 1.8 and p_surge > c_surge:
            return {"signal": "BEAR", "prob": 60, "c_surge": round(c_surge, 1), "p_surge": round(p_surge, 1),
                    "reason": f"Moderate put activity surge {p_surge:.1f}x — watch for confirmation"}
        else:
            return {"signal": "NEUTRAL", "prob": 50, "c_surge": round(c_surge, 1), "p_surge": round(p_surge, 1),
                    "reason": f"Normal activity: call {c_surge:.1f}x / put {p_surge:.1f}x baseline"}
    except Exception as e:
        log.debug(f"_hp_model_smart_money_uoa {ticker}: {e}")
        return {"signal": "NEUTRAL", "prob": 50, "reason": str(e)[:60]}


def _hp_model_hhi_pin(ticker, conn, spot):
    try:
        ld = pd.read_sql(
            "SELECT trade_date_now FROM options_change WHERE ticker=?"
            " ORDER BY substr(trade_date_now,7,4)||substr(trade_date_now,1,2)||substr(trade_date_now,4,2) DESC LIMIT 1",
            conn, params=(ticker.upper(),))
        if ld.empty or spot <= 0:
            return {"signal": "NEUTRAL", "prob": 50, "reason": "No data"}
        latest = ld.iloc[0, 0]

        df = pd.read_sql(
            "SELECT strike, SUM(openInt_Call_now+openInt_Put_now) AS total_oi"
            " FROM options_change WHERE ticker=? AND trade_date_now=?"
            " GROUP BY strike ORDER BY strike",
            conn, params=(ticker.upper(), latest))
        if df.empty or df["total_oi"].sum() == 0:
            return {"signal": "NEUTRAL", "prob": 50, "reason": "No OI data"}

        tot = float(df["total_oi"].sum())
        shares = [(float(r["total_oi"]) / tot) for _, r in df.iterrows()]
        hhi = sum(s * s for s in shares)  # 0=max dispersion, 1=all OI in one strike

        # Check if high-OI strikes are near spot
        near = df[df["strike"].between(spot * 0.97, spot * 1.03)]
        near_oi_pct = float(near["total_oi"].sum()) / tot * 100 if not near.empty else 0

        if hhi >= 0.20 and near_oi_pct >= 30:
            return {"signal": "NEUTRAL", "prob": 76, "hhi": round(hhi, 3), "near_pct": round(near_oi_pct, 0),
                    "reason": f"HHI {hhi:.3f} concentrated: {near_oi_pct:.0f}% OI within ±3% spot → strong pin (Kiema 2019)"}
        elif hhi >= 0.12 and near_oi_pct >= 20:
            return {"signal": "NEUTRAL", "prob": 64, "hhi": round(hhi, 3), "near_pct": round(near_oi_pct, 0),
                    "reason": f"HHI {hhi:.3f}: moderate concentration near spot → mild pin potential"}
        else:
            return {"signal": "NEUTRAL", "prob": 50, "hhi": round(hhi, 3), "near_pct": round(near_oi_pct, 0),
                    "reason": f"OI dispersed (HHI {hhi:.3f}, near-spot {near_oi_pct:.0f}%) — no pin signal"}
    except Exception as e:
        log.debug(f"_hp_model_hhi_pin {ticker}: {e}")
        return {"signal": "NEUTRAL", "prob": 50, "reason": str(e)[:60]}


def _hp_model_pcr_velocity(ticker, conn):
    try:
        df = pd.read_sql(
            "SELECT trade_date, pcr_oi FROM stock_daily WHERE ticker=?"
            " ORDER BY substr(trade_date,7,4)||substr(trade_date,1,2)||substr(trade_date,4,2) DESC LIMIT 10",
            conn, params=(ticker.upper(),))
        if len(df) < 5:
            return {"signal": "NEUTRAL", "prob": 50, "reason": "Need 5+ days for PCR velocity"}

        df["pcr_oi"] = pd.to_numeric(df["pcr_oi"], errors="coerce")
        df = df.dropna(subset=["pcr_oi"]).reset_index(drop=True)
        if len(df) < 4:
            return {"signal": "NEUTRAL", "prob": 50, "reason": "Not enough valid PCR rows"}

        # Velocity = linear slope of PCR over last 5 days
        y = df["pcr_oi"].iloc[:5].tolist()[::-1]  # oldest→newest
        n = len(y)
        x = list(range(n))
        xm, ym = sum(x) / n, sum(y) / n
        slope = sum((xi - xm) * (yi - ym) for xi, yi in zip(x, y)) / (sum((xi - xm) ** 2 for xi in x) + 1e-9)

        curr_pcr = float(df["pcr_oi"].iloc[0])
        avg_pcr  = float(df["pcr_oi"].iloc[:10].mean())
        z = (curr_pcr - avg_pcr) / (float(df["pcr_oi"].std()) + 1e-9)

        if slope <= -0.08 and curr_pcr < avg_pcr:
            return {"signal": "BULL", "prob": 68, "slope": round(slope, 3), "pcr": round(curr_pcr, 2),
                    "reason": f"PCR dropping fast (slope {slope:.3f}, z={z:.1f}) → call buying surge → BULL"}
        elif slope >= 0.08 and curr_pcr > avg_pcr:
            return {"signal": "BEAR", "prob": 68, "slope": round(slope, 3), "pcr": round(curr_pcr, 2),
                    "reason": f"PCR rising fast (slope {slope:.3f}, z={z:.1f}) → put buying surge → BEAR"}
        elif slope <= -0.04:
            return {"signal": "BULL", "prob": 58, "slope": round(slope, 3), "pcr": round(curr_pcr, 2),
                    "reason": f"PCR moderately declining (slope {slope:.3f}) — mild bullish momentum"}
        elif slope >= 0.04:
            return {"signal": "BEAR", "prob": 58, "slope": round(slope, 3), "pcr": round(curr_pcr, 2),
                    "reason": f"PCR moderately rising (slope {slope:.3f}) — mild bearish momentum"}
        else:
            return {"signal": "NEUTRAL", "prob": 50, "slope": round(slope, 3), "pcr": round(curr_pcr, 2),
                    "reason": f"PCR flat (slope {slope:.3f}, curr {curr_pcr:.2f})"}
    except Exception as e:
        log.debug(f"_hp_model_pcr_velocity {ticker}: {e}")
        return {"signal": "NEUTRAL", "prob": 50, "reason": str(e)[:60]}


def _hp_model_vrvp(ticker, conn, spot):
    try:
        px = pd.read_sql(
            "SELECT high, low, close, volume FROM stock_daily WHERE ticker=?"
            " ORDER BY substr(trade_date,7,4)||substr(trade_date,1,2)||substr(trade_date,4,2) DESC LIMIT 60",
            conn, params=(ticker.upper(),))
        if len(px) < 20 or spot <= 0:
            return {"signal": "NEUTRAL", "prob": 50, "reason": "Need 20+ days for VRVP"}

        for col in ['high', 'low', 'close', 'volume']:
            px[col] = pd.to_numeric(px[col], errors='coerce')
        px = px.dropna().reset_index(drop=True)

        price_min = float(px['low'].min())
        price_max = float(px['high'].max())
        if price_max <= price_min:
            return {"signal": "NEUTRAL", "prob": 50, "reason": "VRVP: zero price range"}

        # Build 40-bucket volume profile
        N_BINS = 40
        bucket_size = (price_max - price_min) / N_BINS
        vol_profile = np.zeros(N_BINS)

        for _, row in px.iterrows():
            h = float(row['high']); l = float(row['low']); v = float(row['volume'])
            if h <= l or v <= 0: continue
            # Distribute volume uniformly across the day's high-low range
            lo_bin = max(0, int((l - price_min) / bucket_size))
            hi_bin = min(N_BINS - 1, int((h - price_min) / bucket_size))
            n_bins = hi_bin - lo_bin + 1
            for b in range(lo_bin, hi_bin + 1):
                vol_profile[b] += v / n_bins

        # Identify HVN, LVN, POC
        mean_vol  = float(np.mean(vol_profile[vol_profile > 0]))
        std_vol   = float(np.std(vol_profile[vol_profile > 0]))
        hvn_thresh = mean_vol + 0.8 * std_vol    # HVN = thick bars
        lvn_thresh = mean_vol - 0.5 * std_vol    # LVN = thin bars
        poc_bin   = int(np.argmax(vol_profile))
        poc_price = price_min + (poc_bin + 0.5) * bucket_size

        # Value area (70% of total volume)
        total_vol = vol_profile.sum()
        va_target = total_vol * 0.70
        va_lo = va_hi = poc_bin
        va_accum = vol_profile[poc_bin]
        while va_accum < va_target and (va_lo > 0 or va_hi < N_BINS - 1):
            expand_lo = vol_profile[va_lo - 1] if va_lo > 0 else 0
            expand_hi = vol_profile[va_hi + 1] if va_hi < N_BINS - 1 else 0
            if expand_lo >= expand_hi and va_lo > 0:
                va_lo -= 1; va_accum += expand_lo
            elif va_hi < N_BINS - 1:
                va_hi += 1; va_accum += expand_hi
            else:
                break
        val_price = price_min + (va_lo + 0.5) * bucket_size   # Value Area Low
        vah_price = price_min + (va_hi + 0.5) * bucket_size   # Value Area High

        # Find nearest HVN to current spot
        spot_bin = max(0, min(N_BINS - 1, int((spot - price_min) / bucket_size)))
        curr_vol = vol_profile[spot_bin]

        # Search for nearest HVN within ±8%
        search_radius = max(1, int(N_BINS * 0.08))
        nearby_hvn = []
        for b in range(max(0, spot_bin - search_radius), min(N_BINS, spot_bin + search_radius + 1)):
            if vol_profile[b] >= hvn_thresh:
                bp = price_min + (b + 0.5) * bucket_size
                dist_pct = abs(bp - spot) / spot * 100
                nearby_hvn.append((dist_pct, bp, vol_profile[b]))
        nearby_hvn.sort()

        hvn_zone  = curr_vol >= hvn_thresh
        lvn_zone  = curr_vol <= lvn_thresh
        in_va     = val_price <= spot <= vah_price
        poc_dist  = abs(spot - poc_price) / spot * 100

        # Signal logic
        if hvn_zone and poc_dist <= 2.5:
            return {
                "signal": "SELL_PREMIUM", "prob": 79,
                "poc": round(poc_price, 2), "val": round(val_price, 2), "vah": round(vah_price, 2),
                "hvn_level": round(poc_price, 2),
                "box_lo": round(val_price, 2), "box_hi": round(vah_price, 2),
                "reason": (f"Price at POC ${poc_price:.2f} (HVN thickness"
                           f" {curr_vol/mean_vol:.1f}x avg) — strong pin zone. "
                           f"Sell strangle: put at ${val_price:.0f} / call at ${vah_price:.0f}")
            }
        elif hvn_zone:
            return {
                "signal": "SELL_PREMIUM", "prob": 72,
                "poc": round(poc_price, 2), "val": round(val_price, 2), "vah": round(vah_price, 2),
                "hvn_level": round(spot, 2),
                "box_lo": round(spot * 0.985, 2), "box_hi": round(spot * 1.015, 2),
                "reason": (f"Price in HVN zone ({curr_vol/mean_vol:.1f}x avg vol). "
                           f"VA box ${val_price:.0f}-${vah_price:.0f}. "
                           f"SELL PREMIUM — IC strikes outside VA.")
            }
        elif nearby_hvn and nearby_hvn[0][0] <= 1.5:
            dist, hvn_p, hvn_v = nearby_hvn[0]
            direction = "approaching from below" if hvn_p > spot else "approaching from above"
            return {
                "signal": "SELL_PREMIUM", "prob": 68,
                "poc": round(poc_price, 2), "val": round(val_price, 2), "vah": round(vah_price, 2),
                "hvn_level": round(hvn_p, 2),
                "box_lo": round(min(spot, hvn_p) * 0.99, 2),
                "box_hi": round(max(spot, hvn_p) * 1.01, 2),
                "reason": (f"HVN at ${hvn_p:.2f} ({hvn_v/mean_vol:.1f}x avg) only {dist:.1f}% away "
                           f"({direction}). POC=${poc_price:.2f}. Price will stall here — SELL.")
            }
        elif lvn_zone:
            # Price in thin zone — expect fast move
            nearest_hvn_above = next((hp for (d, hp, hv) in nearby_hvn if hp > spot), None)
            nearest_hvn_below = next((hp for (d, hp, hv) in sorted(nearby_hvn) if hp < spot), None)
            target = nearest_hvn_above or (spot * 1.03)
            return {
                "signal": "BULL", "prob": 62,
                "poc": round(poc_price, 2), "val": round(val_price, 2), "vah": round(vah_price, 2),
                "hvn_level": round(target, 2),
                "reason": (f"Price in LVN (thin zone {curr_vol/mean_vol:.1f}x avg) — "
                           f"expect fast move toward HVN ${target:.2f}. POC=${poc_price:.2f}.")
            }
        elif in_va:
            return {
                "signal": "SELL_PREMIUM", "prob": 65,
                "poc": round(poc_price, 2), "val": round(val_price, 2), "vah": round(vah_price, 2),
                "hvn_level": round(poc_price, 2),
                "box_lo": round(val_price, 2), "box_hi": round(vah_price, 2),
                "reason": (f"Price inside Value Area ${val_price:.0f}-${vah_price:.0f} "
                           f"(70% of volume). POC=${poc_price:.2f}. Sell IC with strikes at VA edges.")
            }
        elif spot > vah_price:
            # Price broke above value area → bullish breakout
            dist_above = (spot - vah_price) / spot * 100
            if dist_above <= 4.0:
                return {
                    "signal": "BULL", "prob": 64,
                    "poc": round(poc_price, 2), "val": round(val_price, 2), "vah": round(vah_price, 2),
                    "reason": (f"Price {dist_above:.1f}% above VA top ${vah_price:.0f}. "
                               f"Breakout above HVN — BULL momentum. POC=${poc_price:.2f}.")
                }
            else:
                # Far above VA — look for near HVN as short-term sell zone
                return {
                    "signal": "SELL_PREMIUM", "prob": 62,
                    "poc": round(poc_price, 2), "val": round(val_price, 2), "vah": round(vah_price, 2),
                    "box_lo": round(spot * 0.97, 2), "box_hi": round(spot * 1.02, 2),
                    "reason": (f"Price {dist_above:.1f}% above 60d VA — extended. "
                               f"SELL OTM strangle near current range, POC=${poc_price:.2f}.")
                }
        elif spot < val_price:
            dist_below = (val_price - spot) / spot * 100
            if dist_below <= 4.0:
                return {
                    "signal": "BEAR", "prob": 64,
                    "poc": round(poc_price, 2), "val": round(val_price, 2), "vah": round(vah_price, 2),
                    "reason": (f"Price {dist_below:.1f}% below VA bottom ${val_price:.0f}. "
                               f"Breakdown below HVN — BEAR momentum.")
                }
            else:
                return {
                    "signal": "NEUTRAL", "prob": 50,
                    "poc": round(poc_price, 2), "val": round(val_price, 2), "vah": round(vah_price, 2),
                    "reason": (f"Price ${spot:.0f} far below 60d VA ${val_price:.0f}-${vah_price:.0f}. "
                               f"POC=${poc_price:.2f}.")
                }
        else:
            return {
                "signal": "NEUTRAL", "prob": 50,
                "poc": round(poc_price, 2), "val": round(val_price, 2), "vah": round(vah_price, 2),
                "reason": (f"No clear VRVP signal. VA ${val_price:.0f}-${vah_price:.0f} "
                           f"POC=${poc_price:.2f}")
            }
    except Exception as e:
        log.debug(f"_hp_model_vrvp {ticker}: {e}")
        return {"signal": "NEUTRAL", "prob": 50, "reason": str(e)[:60]}


def _hp_model_vwap_dev(ticker, conn, spot):
    try:
        px = pd.read_sql(
            "SELECT high, low, close, volume FROM stock_daily WHERE ticker=?"
            " ORDER BY substr(trade_date,7,4)||substr(trade_date,1,2)||substr(trade_date,4,2) DESC LIMIT 25",
            conn, params=(ticker.upper(),))
        if len(px) < 15 or spot <= 0:
            return {"signal": "NEUTRAL", "prob": 50, "reason": "Need 15+ days for VWAP"}
        for col in ['high','low','close','volume']:
            px[col] = pd.to_numeric(px[col], errors='coerce')
        px = px.dropna()
        typical = (px['high'] + px['low'] + px['close']) / 3
        vwap    = float((typical * px['volume']).sum() / px['volume'].sum())
        # Rolling σ of (close - vwap)
        diffs   = px['close'] - vwap
        sigma   = float(diffs.std())
        if sigma <= 0:
            return {"signal": "NEUTRAL", "prob": 50, "reason": "VWAP sigma = 0"}
        z = (spot - vwap) / sigma
        upper2 = vwap + 2 * sigma; lower2 = vwap - 2 * sigma
        upper1 = vwap + 1 * sigma; lower1 = vwap - 1 * sigma

        if abs(z) >= 2.5:
            return {"signal": "SELL_PREMIUM", "prob": 76,
                    "vwap": round(vwap, 2), "z": round(z, 2), "sigma": round(sigma, 2),
                    "box_lo": round(lower1, 2), "box_hi": round(upper1, 2),
                    "reason": (f"Price {z:+.1f}σ from 20d VWAP ${vwap:.2f} — extreme stretch. "
                               f"SELL PREMIUM: IC strikes at ±1σ (${lower1:.0f}-${upper1:.0f})")}
        elif abs(z) >= 1.8:
            return {"signal": "SELL_PREMIUM", "prob": 68,
                    "vwap": round(vwap, 2), "z": round(z, 2), "sigma": round(sigma, 2),
                    "box_lo": round(lower2, 2), "box_hi": round(upper2, 2),
                    "reason": (f"Price {z:+.1f}σ from VWAP ${vwap:.2f} — stretched. "
                               f"SELL premium outside ±2σ band (${lower2:.0f}-${upper2:.0f})")}
        elif abs(z) <= 0.4:
            return {"signal": "SELL_PREMIUM", "prob": 64,
                    "vwap": round(vwap, 2), "z": round(z, 2), "sigma": round(sigma, 2),
                    "box_lo": round(lower2, 2), "box_hi": round(upper2, 2),
                    "reason": (f"Price at VWAP equilibrium (z={z:.2f}). "
                               f"IC sell zone: ${lower2:.0f}-${upper2:.0f} (±2σ)")}
        else:
            return {"signal": "NEUTRAL", "prob": 50,
                    "vwap": round(vwap, 2), "z": round(z, 2), "sigma": round(sigma, 2),
                    "reason": f"VWAP z={z:.2f} — neutral zone ${lower1:.0f}-${upper1:.0f}"}
    except Exception as e:
        log.debug(f"_hp_model_vwap_dev {ticker}: {e}")
        return {"signal": "NEUTRAL", "prob": 50, "reason": str(e)[:60]}


def _hp_model_expected_move(ticker, conn, spot):
    try:
        from datetime import datetime as _dt2
        today = _dt2.now().date()
        ld = pd.read_sql(
            "SELECT trade_date_now FROM options_change WHERE ticker=?"
            " ORDER BY substr(trade_date_now,7,4)||substr(trade_date_now,1,2)||substr(trade_date_now,4,2) DESC LIMIT 1",
            conn, params=(ticker.upper(),))
        if ld.empty or spot <= 0:
            return {"signal": "NEUTRAL", "prob": 50, "reason": "No options data"}
        latest = ld.iloc[0, 0]

        # ATM straddle price = call + put at nearest-to-spot strike, shortest expiry
        atm = pd.read_sql(
            "SELECT strike, expiry_date, lastPrice_Call_now, lastPrice_Put_now"
            " FROM options_change WHERE ticker=? AND trade_date_now=?"
            " AND lastPrice_Call_now > 0.2 AND lastPrice_Put_now > 0.2"
            " AND ABS(strike - ?) / ? < 0.025"
            " ORDER BY ABS(strike - ?) ASC LIMIT 10",
            conn, params=(ticker.upper(), latest, spot, spot, spot))
        if atm.empty:
            return {"signal": "NEUTRAL", "prob": 50, "reason": "No ATM options for EM calc"}

        # Prefer shortest DTE
        best_row = None; best_dte = 9999
        for _, r in atm.iterrows():
            try:
                exp = _dt2.strptime(r["expiry_date"], "%m-%d-%Y").date()
                dte = (exp - today).days
                if 1 <= dte < best_dte:
                    best_dte = dte; best_row = r
            except Exception:
                pass
        if best_row is None:
            return {"signal": "NEUTRAL", "prob": 50, "reason": "No valid expiry for EM"}

        straddle  = float(best_row["lastPrice_Call_now"]) + float(best_row["lastPrice_Put_now"])
        em_pct    = straddle / spot * 100   # 1σ expected move %

        # Check actual prior day return
        px2 = pd.read_sql(
            "SELECT close FROM stock_daily WHERE ticker=?"
            " ORDER BY substr(trade_date,7,4)||substr(trade_date,1,2)||substr(trade_date,4,2) DESC LIMIT 2",
            conn, params=(ticker.upper(),))
        if len(px2) < 2:
            return {"signal": "NEUTRAL", "prob": 50, "reason": "Need 2 price days"}
        actual_move = abs(float(px2["close"].iloc[0]) / float(px2["close"].iloc[1]) - 1) * 100

        breach_ratio = actual_move / em_pct if em_pct > 0 else 0

        if breach_ratio >= 1.5:
            return {"signal": "SELL_PREMIUM", "prob": 74,
                    "em_pct": round(em_pct, 1), "actual_move": round(actual_move, 1),
                    "straddle": round(straddle, 2), "dte": best_dte,
                    "reason": (f"Prior move {actual_move:.1f}% = {breach_ratio:.1f}x EM ({em_pct:.1f}%). "
                               f"Vol likely to compress — SELL straddle ${straddle:.2f} ({best_dte}DTE)")}
        elif breach_ratio >= 1.0:
            return {"signal": "SELL_PREMIUM", "prob": 64,
                    "em_pct": round(em_pct, 1), "actual_move": round(actual_move, 1),
                    "reason": (f"Move {actual_move:.1f}% at EM boundary ({em_pct:.1f}%). "
                               f"Mild reversion signal — sell OTM strangle")}
        elif em_pct >= 3.0:
            return {"signal": "SELL_PREMIUM", "prob": 60,
                    "em_pct": round(em_pct, 1), "actual_move": round(actual_move, 1),
                    "straddle": round(straddle, 2), "dte": best_dte,
                    "reason": (f"Straddle ${straddle:.2f} implies {em_pct:.1f}% EM ({best_dte}DTE). "
                               f"High IV environment — sell premium")}
        else:
            return {"signal": "NEUTRAL", "prob": 50,
                    "em_pct": round(em_pct, 1), "actual_move": round(actual_move, 1),
                    "reason": (f"EM {em_pct:.1f}% / actual {actual_move:.1f}% — no EM breach")}
    except Exception as e:
        log.debug(f"_hp_model_expected_move {ticker}: {e}")
        return {"signal": "NEUTRAL", "prob": 50, "reason": str(e)[:60]}


def _hp_model_left_skew(ticker, conn, spot):
    try:
        from datetime import datetime as _dt2
        today = _dt2.now().date()
        ld = pd.read_sql(
            "SELECT trade_date_now FROM options_change WHERE ticker=?"
            " ORDER BY substr(trade_date_now,7,4)||substr(trade_date_now,1,2)||substr(trade_date_now,4,2) DESC LIMIT 1",
            conn, params=(ticker.upper(),))
        if ld.empty or spot <= 0:
            return {"signal": "NEUTRAL", "prob": 50, "reason": "No data"}
        latest = ld.iloc[0, 0]

        # OTM puts: 5-10% below spot
        puts = pd.read_sql(
            "SELECT strike, expiry_date, lastPrice_Put_now FROM options_change"
            " WHERE ticker=? AND trade_date_now=? AND lastPrice_Put_now > 0.3"
            " AND (? - strike) / ? BETWEEN 0.04 AND 0.12",
            conn, params=(ticker.upper(), latest, spot, spot))
        # OTM calls: 5-10% above spot
        calls = pd.read_sql(
            "SELECT strike, expiry_date, lastPrice_Call_now FROM options_change"
            " WHERE ticker=? AND trade_date_now=? AND lastPrice_Call_now > 0.3"
            " AND (strike - ?) / ? BETWEEN 0.04 AND 0.12",
            conn, params=(ticker.upper(), latest, spot, spot))
        if puts.empty or calls.empty:
            return {"signal": "NEUTRAL", "prob": 50, "reason": "Insufficient OTM options"}

        p_ivs = []; c_ivs = []
        for _, r in puts.iterrows():
            try:
                exp = _dt2.strptime(r["expiry_date"], "%m-%d-%Y").date()
                T   = max((exp - today).days / 365.0, 1/365.0)
                K   = float(r["strike"]); P = float(r["lastPrice_Put_now"])
                # Put IV via call-put parity: P = C + K*e^(-rT) - S
                c_equiv = P - K * _math.exp(-0.05 * T) + spot
                if c_equiv > 0:
                    iv = _implied_vol_hp(max(c_equiv, 0.01), spot, K, T)
                    if 0.05 < iv < 4.0: p_ivs.append(iv * 100)
            except Exception: pass
        for _, r in calls.iterrows():
            try:
                exp = _dt2.strptime(r["expiry_date"], "%m-%d-%Y").date()
                T   = max((exp - today).days / 365.0, 1/365.0)
                iv  = _implied_vol_hp(float(r["lastPrice_Call_now"]), spot, float(r["strike"]), T)
                if 0.05 < iv < 4.0: c_ivs.append(iv * 100)
            except Exception: pass

        if not p_ivs or not c_ivs:
            return {"signal": "NEUTRAL", "prob": 50, "reason": "IV calc failed for skew"}

        avg_put_iv  = float(np.mean(p_ivs))
        avg_call_iv = float(np.mean(c_ivs))
        skew_ratio  = avg_put_iv / avg_call_iv if avg_call_iv > 0 else 1.0

        if skew_ratio >= 1.8:
            return {"signal": "BULL", "prob": 71, "skew": round(skew_ratio, 2),
                    "put_iv": round(avg_put_iv, 1), "call_iv": round(avg_call_iv, 1),
                    "reason": (f"Left skew {skew_ratio:.2f} (put IV {avg_put_iv:.0f}% / "
                               f"call IV {avg_call_iv:.0f}%) — extreme fear = contrarian BULL (Xing 2010)")}
        elif skew_ratio >= 1.4:
            return {"signal": "BULL", "prob": 61, "skew": round(skew_ratio, 2),
                    "put_iv": round(avg_put_iv, 1), "call_iv": round(avg_call_iv, 1),
                    "reason": f"Elevated put skew {skew_ratio:.2f} — mild contrarian BULL signal"}
        elif skew_ratio <= 0.8:
            return {"signal": "BEAR", "prob": 62, "skew": round(skew_ratio, 2),
                    "put_iv": round(avg_put_iv, 1), "call_iv": round(avg_call_iv, 1),
                    "reason": (f"Reverse skew {skew_ratio:.2f} (call IV > put IV) — "
                               f"complacency / gamma squeeze risk = BEAR")}
        else:
            return {"signal": "NEUTRAL", "prob": 50, "skew": round(skew_ratio, 2),
                    "reason": f"Normal skew {skew_ratio:.2f} (put {avg_put_iv:.0f}%/call {avg_call_iv:.0f}%)"}
    except Exception as e:
        log.debug(f"_hp_model_left_skew {ticker}: {e}")
        return {"signal": "NEUTRAL", "prob": 50, "reason": str(e)[:60]}


def _hp_model_vrp(ticker, conn, spot):
    try:
        from datetime import datetime as _dt2
        today = _dt2.now().date()

        # Realized vol: 10-day HV
        px = pd.read_sql(
            "SELECT close FROM stock_daily WHERE ticker=?"
            " ORDER BY substr(trade_date,7,4)||substr(trade_date,1,2)||substr(trade_date,4,2) DESC LIMIT 15",
            conn, params=(ticker.upper(),))
        if len(px) < 11 or spot <= 0:
            return {"signal": "NEUTRAL", "prob": 50, "reason": "Need 11+ days for VRP"}
        rets = px["close"].pct_change().dropna()
        rv10 = float(rets.std() * _math.sqrt(252) * 100)

        # IV from nearest ATM straddle
        ld = pd.read_sql(
            "SELECT trade_date_now FROM options_change WHERE ticker=?"
            " ORDER BY substr(trade_date_now,7,4)||substr(trade_date_now,1,2)||substr(trade_date_now,4,2) DESC LIMIT 1",
            conn, params=(ticker.upper(),))
        if ld.empty:
            return {"signal": "NEUTRAL", "prob": 50, "reason": "No options data"}
        latest = ld.iloc[0, 0]

        atm = pd.read_sql(
            "SELECT strike, expiry_date, lastPrice_Call_now, lastPrice_Put_now"
            " FROM options_change WHERE ticker=? AND trade_date_now=?"
            " AND lastPrice_Call_now > 0.3 AND lastPrice_Put_now > 0.3"
            " AND ABS(strike - ?) / ? < 0.025"
            " ORDER BY ABS(strike - ?) ASC LIMIT 6",
            conn, params=(ticker.upper(), latest, spot, spot, spot))
        if atm.empty:
            return {"signal": "NEUTRAL", "prob": 50, "reason": "No ATM data for VRP"}

        ivs = []
        for _, r in atm.iterrows():
            try:
                exp = _dt2.strptime(r["expiry_date"], "%m-%d-%Y").date()
                T   = max((exp - today).days / 365.0, 1/365.0)
                iv  = _implied_vol_hp(float(r["lastPrice_Call_now"]), spot, float(r["strike"]), T)
                if 0.05 < iv < 3.0: ivs.append(iv * 100)
            except Exception: pass
        if not ivs:
            return {"signal": "NEUTRAL", "prob": 50, "reason": "IV calc failed"}

        iv30  = float(np.median(ivs))
        vrp   = iv30 - rv10  # positive = sellers' edge

        if vrp >= 8:
            return {"signal": "SELL_PREMIUM", "prob": 77, "vrp": round(vrp, 1),
                    "iv": round(iv30, 1), "rv": round(rv10, 1),
                    "reason": (f"VRP +{vrp:.0f}pp (IV {iv30:.0f}% vs RV {rv10:.0f}%) — "
                               f"fat sellers' edge. SELL PREMIUM (Carr & Wu 2009)")}
        elif vrp >= 4:
            return {"signal": "SELL_PREMIUM", "prob": 66, "vrp": round(vrp, 1),
                    "iv": round(iv30, 1), "rv": round(rv10, 1),
                    "reason": f"VRP +{vrp:.0f}pp → moderate premium-selling edge"}
        elif vrp <= -5:
            return {"signal": "BULL", "prob": 63, "vrp": round(vrp, 1),
                    "iv": round(iv30, 1), "rv": round(rv10, 1),
                    "reason": (f"Negative VRP {vrp:.0f}pp (RV {rv10:.0f}% >> IV {iv30:.0f}%) — "
                               f"options cheap relative to realized vol, buy gamma")}
        else:
            return {"signal": "NEUTRAL", "prob": 50, "vrp": round(vrp, 1),
                    "reason": f"VRP {vrp:+.0f}pp (IV {iv30:.0f}% / RV {rv10:.0f}%) — normal"}
    except Exception as e:
        log.debug(f"_hp_model_vrp {ticker}: {e}")
        return {"signal": "NEUTRAL", "prob": 50, "reason": str(e)[:60]}


def _hp_model_put_call_wall(ticker, conn, spot):
    try:
        ld = pd.read_sql(
            "SELECT trade_date_now FROM options_change WHERE ticker=?"
            " ORDER BY substr(trade_date_now,7,4)||substr(trade_date_now,1,2)||substr(trade_date_now,4,2) DESC LIMIT 1",
            conn, params=(ticker.upper(),))
        if ld.empty or spot <= 0:
            return {"signal": "NEUTRAL", "prob": 50, "reason": "No data"}
        latest = ld.iloc[0, 0]

        df = pd.read_sql(
            "SELECT strike, SUM(openInt_Call_now) AS co, SUM(openInt_Put_now) AS po"
            " FROM options_change WHERE ticker=? AND trade_date_now=?"
            " GROUP BY strike ORDER BY strike",
            conn, params=(ticker.upper(), latest))
        if df.empty:
            return {"signal": "NEUTRAL", "prob": 50, "reason": "No OI data"}

        df['co'] = pd.to_numeric(df['co'], errors='coerce').fillna(0)
        df['po'] = pd.to_numeric(df['po'], errors='coerce').fillna(0)
        mean_co = float(df['co'].mean()); mean_po = float(df['po'].mean())

        # Call wall: max call OI above spot
        above = df[df['strike'] > spot]
        below = df[df['strike'] < spot]
        if above.empty or below.empty:
            return {"signal": "NEUTRAL", "prob": 50, "reason": "Not enough strikes above/below"}

        call_wall_idx = above['co'].idxmax()
        put_wall_idx  = below['po'].idxmax()
        call_wall = float(df.loc[call_wall_idx, 'strike'])
        put_wall  = float(df.loc[put_wall_idx,  'strike'])
        call_oi   = float(df.loc[call_wall_idx, 'co'])
        put_oi    = float(df.loc[put_wall_idx,  'po'])

        dist_cw = (call_wall - spot) / spot * 100
        dist_pw = (spot - put_wall)  / spot * 100

        cw_strength = call_oi / mean_co if mean_co > 0 else 1
        pw_strength = put_oi  / mean_po if mean_po > 0 else 1

        # Near put wall + strong OI = support → sell put credit spread
        if dist_pw <= 1.5 and pw_strength >= 2.5:
            return {"signal": "SELL_PREMIUM", "prob": 74,
                    "put_wall": round(put_wall, 2), "call_wall": round(call_wall, 2),
                    "pw_str": round(pw_strength, 1), "cw_str": round(cw_strength, 1),
                    "reason": (f"Put Wall at ${put_wall:.0f} ({pw_strength:.1f}x avg, {dist_pw:.1f}% away). "
                               f"SELL PUT CREDIT SPREAD: short ${put_wall:.0f}P / long ${put_wall*0.97:.0f}P")}
        # Near call wall = resistance → sell call credit spread
        elif dist_cw <= 1.5 and cw_strength >= 2.5:
            return {"signal": "SELL_PREMIUM", "prob": 72,
                    "put_wall": round(put_wall, 2), "call_wall": round(call_wall, 2),
                    "pw_str": round(pw_strength, 1), "cw_str": round(cw_strength, 1),
                    "reason": (f"Call Wall at ${call_wall:.0f} ({cw_strength:.1f}x avg, {dist_cw:.1f}% away). "
                               f"SELL CALL SPREAD: short ${call_wall:.0f}C / long ${call_wall*1.03:.0f}C")}
        # Price well inside walls = IC zone
        elif dist_pw >= 2 and dist_cw >= 2 and pw_strength >= 2.0 and cw_strength >= 2.0:
            return {"signal": "SELL_PREMIUM", "prob": 66,
                    "put_wall": round(put_wall, 2), "call_wall": round(call_wall, 2),
                    "pw_str": round(pw_strength, 1), "cw_str": round(cw_strength, 1),
                    "reason": (f"Walls: Put ${put_wall:.0f} ({dist_pw:.1f}% below) / "
                               f"Call ${call_wall:.0f} ({dist_cw:.1f}% above). "
                               f"SELL IC: P/C walls as outer strikes")}
        else:
            return {"signal": "NEUTRAL", "prob": 50,
                    "put_wall": round(put_wall, 2), "call_wall": round(call_wall, 2),
                    "reason": (f"Walls thin: Put ${put_wall:.0f} ({pw_strength:.1f}x) / "
                               f"Call ${call_wall:.0f} ({cw_strength:.1f}x) — no strong wall signal")}
    except Exception as e:
        log.debug(f"_hp_model_put_call_wall {ticker}: {e}")
        return {"signal": "NEUTRAL", "prob": 50, "reason": str(e)[:60]}


def high_prob_signals_engine(ticker, conn, spy_ret=0.0):
    """
    Run all 24 models (incl. VRVP, VWAP, VRP, Put/Call Wall, Left Skew, EM),
    apply adaptive weights, return calibrated ensemble signal.
    Votes: ≥6/24 agree → MEDIUM CONF; ≥9/24 → HIGH CONF.
    """
    _setup_hp_tables(conn)
    _update_hp_outcomes(ticker, conn)
    weights = _get_hp_weights(ticker, conn)

    spot_df = pd.read_sql(
        "SELECT close FROM stock_daily WHERE ticker=?"
        " ORDER BY substr(trade_date,7,4)||substr(trade_date,1,2)||substr(trade_date,4,2) DESC LIMIT 1",
        conn, params=(ticker.upper(),))
    spot = float(spot_df["close"].iloc[0]) if not spot_df.empty else 0.0

    models = {
        # Original 6 models
        "gex":          _hp_model_gex(ticker, conn, spot),
        "pcr_z":        _hp_model_pcr_z(ticker, conn),
        "oi_momentum":  _hp_model_oi_momentum(ticker, conn, spot),
        "gamma_pin":    _hp_model_gamma_pin(ticker, conn, spot),
        "vol_flow":     _hp_model_vol_flow(ticker, conn),
        "iv_skew":      _hp_model_iv_skew(ticker, conn, spot),
        # Research models 7-16
        "rv_iv":        _hp_model_rv_iv(ticker, conn, spot),
        "oi_term_struct": _hp_model_oi_term_structure(ticker, conn),
        "maxpain_vel":  _hp_model_maxpain_velocity(ticker, conn, spot),
        "iv_rank":      _hp_model_iv_rank(ticker, conn, spot),
        "pcp_dev":      _hp_model_pcp_deviation(ticker, conn, spot),
        "vol_regime":   _hp_model_vol_regime(ticker, conn),
        "multi_expiry": _hp_model_multi_expiry_oi(ticker, conn),
        "smart_uoa":    _hp_model_smart_money_uoa(ticker, conn),
        "hhi_pin":      _hp_model_hhi_pin(ticker, conn, spot),
        "pcr_vel":      _hp_model_pcr_velocity(ticker, conn),
        # Volume Profile + high-prob add-ons 17-22
        "vrvp":         _hp_model_vrvp(ticker, conn, spot),
        "vwap_dev":     _hp_model_vwap_dev(ticker, conn, spot),
        "expected_move": _hp_model_expected_move(ticker, conn, spot),
        "left_skew":    _hp_model_left_skew(ticker, conn, spot),
        "vrp":          _hp_model_vrp(ticker, conn, spot),
        "put_call_wall": _hp_model_put_call_wall(ticker, conn, spot),
        # Model 23: short-squeeze / short-covering (Ortex/S3 method)
        "short_squeeze": _hp_model_short_squeeze(ticker, conn, spot),
        # Model 24: time-series momentum (12-1)
        "momentum": _hp_model_momentum(ticker, conn, spot),
    }

    bull_w = bear_w = 0.0
    for name, res in models.items():
        w    = weights.get(name, 1.0)
        s    = res.get("signal", "NEUTRAL")
        p    = res.get("prob", 50)
        edge = (p - 50) / 50.0
        if s == "BULL":
            bull_w += w * edge
        elif s == "BEAR":
            bear_w += w * abs(edge)

    bull_v  = sum(1 for r in models.values() if r.get("signal") == "BULL")
    bear_v  = sum(1 for r in models.values() if r.get("signal") == "BEAR")
    sell_v  = sum(1 for r in models.values() if r.get("signal") == "SELL_PREMIUM")
    neut_v  = sum(1 for r in models.values() if r.get("signal") == "NEUTRAL")
    total   = len(models)
    mkt     = 2 if spy_ret > 0.5 else (-2 if spy_ret < -0.5 else 0)

    # Scaled thresholds for 24 models
    if bull_v >= 9 and bull_w > bear_w * 1.2:
        ens_sig = "BULL";  ens_prob = min(91, 58 + bull_w * 7 + mkt); conf = "HIGH"
    elif bull_v >= 6 and bull_w > bear_w * 1.1:
        ens_sig = "BULL";  ens_prob = min(83, 54 + bull_w * 5 + mkt); conf = "MEDIUM"
    elif bear_v >= 9 and bear_w > bull_w * 1.2:
        ens_sig = "BEAR";  ens_prob = min(91, 58 + bear_w * 7 - mkt); conf = "HIGH"
    elif bear_v >= 6 and bear_w > bull_w * 1.1:
        ens_sig = "BEAR";  ens_prob = min(83, 54 + bear_w * 5 - mkt); conf = "MEDIUM"
    elif bull_v >= 4 and bull_w > bear_w:
        ens_sig = "BULL";  ens_prob = min(70, 52 + bull_w * 4);        conf = "LOW"
    elif bear_v >= 4 and bear_w > bull_w:
        ens_sig = "BEAR";  ens_prob = min(70, 52 + bear_w * 4);        conf = "LOW"
    else:
        ens_sig = "NEUTRAL"; ens_prob = 50.0; conf = "LOW"

    # ── Premium-selling overlays (now with VRVP, VWAP, VRP, Walls) ──
    gp       = models["gamma_pin"]
    iv_m     = models["iv_skew"]
    rv_m     = models["rv_iv"]
    ivr_m    = models["iv_rank"]
    hhi_m    = models["hhi_pin"]
    vrvp_m   = models["vrvp"]
    vwap_m   = models["vwap_dev"]
    vrp_m    = models["vrp"]
    wall_m   = models["put_call_wall"]
    em_m     = models["expected_move"]

    pin_ok     = (gp.get("signal") == "NEUTRAL" and gp.get("dte", 99) <= 7 and gp.get("prob", 0) >= 78)
    high_iv    = (iv_m.get("atm_iv", 0) >= 35 or ivr_m.get("iv_rank", 0) >= 80
                  or rv_m.get("spread", 0) >= 8)
    strong_pin = hhi_m.get("prob", 0) >= 76
    vrvp_sell  = vrvp_m.get("signal") == "SELL_PREMIUM"
    vwap_sell  = vwap_m.get("signal") == "SELL_PREMIUM"
    vrp_sell   = vrp_m.get("signal")  == "SELL_PREMIUM"
    wall_sell  = wall_m.get("signal") == "SELL_PREMIUM"
    em_sell    = em_m.get("signal")   == "SELL_PREMIUM"

    # Count convergent premium signals
    sell_signals = sum([pin_ok, strong_pin, high_iv, vrvp_sell, vwap_sell, vrp_sell, wall_sell, em_sell])

    if ens_sig in ("BULL", "BEAR") and conf in ("HIGH", "MEDIUM"):
        side     = "calls / bull spread" if ens_sig == "BULL" else "puts / bear spread"
        strategy = f"BUY {side} — {conf} conf | Prob {ens_prob:.0f}%"
    elif sell_signals >= 3:
        # Build specific box from VRVP if available, fallback to VWAP/walls
        if vrvp_sell and vrvp_m.get("box_lo"):
            box_lo = vrvp_m["box_lo"]; box_hi = vrvp_m["box_hi"]
            box_src = f"VRVP box ${box_lo:.0f}-${box_hi:.0f}"
        elif vwap_sell and vwap_m.get("box_lo"):
            box_lo = vwap_m["box_lo"]; box_hi = vwap_m["box_hi"]
            box_src = f"VWAP ±1σ ${box_lo:.0f}-${box_hi:.0f}"
        elif wall_sell:
            box_lo = wall_m.get("put_wall", spot * 0.96)
            box_hi = wall_m.get("call_wall", spot * 1.04)
            box_src = f"Walls ${box_lo:.0f}-${box_hi:.0f}"
        else:
            box_lo = spot * 0.96; box_hi = spot * 1.04
            box_src = f"±4% ${box_lo:.0f}-${box_hi:.0f}"
        vrp_str = f" VRP+{vrp_m.get('vrp', 0):.0f}pp" if vrp_sell else ""
        strategy = (f"SELL PREMIUM — {sell_signals}/8 signals align. "
                    f"{box_src}{vrp_str}. IC/Straddle near spot.")
        ens_sig = "SELL_PREMIUM"
        ens_prob = min(84, 60 + sell_signals * 3)
    elif sell_signals >= 1:
        best_sell = max(
            [(vrvp_m, "VRVP"), (vwap_m, "VWAP"), (vrp_m, "VRP"), (wall_m, "Walls"), (em_m, "EM")],
            key=lambda x: x[0].get("prob", 0) if x[0].get("signal") == "SELL_PREMIUM" else 0)
        src_m, src_n = best_sell
        strategy = (f"SELL PREMIUM ({src_n}) — {src_m.get('reason','')[:55]}…"
                    if src_m.get("signal") == "SELL_PREMIUM"
                    else "WAIT — weak sell signal, need ≥3 to confirm")
        ens_sig = "SELL_PREMIUM"
    else:
        strategy = "WAIT — no high-conf edge. Consider IC if IV ≥30."

    below80 = ens_prob < 80
    warn    = "⚠️ Prob <80% — reduce size or wait." if below80 else ""

    try:
        tod = datetime.now(timezone.utc).replace(tzinfo=None).strftime("%m-%d-%Y")
        for name, res in models.items():
            conn.execute(
                "INSERT OR REPLACE INTO signal_accuracy (ticker, trade_date, model_name, signal, prob)"
                " VALUES (?,?,?,?,?)",
                (ticker.upper(), tod, name, res.get("signal", "NEUTRAL"), res.get("prob", 50)))
        conn.commit()
    except Exception:
        pass

    return {
        "ticker": ticker.upper(), "spot": round(spot, 2),
        "signal": ens_sig, "prob": round(ens_prob, 1), "conf": conf,
        "bull_v": bull_v, "bear_v": bear_v, "neut_v": neut_v,
        "sell_v": sell_v, "total_m": total,
        "strategy": strategy, "warn": warn, "below80": below80,
        "models": models, "weights": weights,
        # VRVP box for display
        "vrvp_box": {
            "lo": vrvp_m.get("box_lo"), "hi": vrvp_m.get("box_hi"),
            "poc": vrvp_m.get("poc"), "val": vrvp_m.get("val"), "vah": vrvp_m.get("vah"),
        } if vrvp_sell or vrvp_m.get("poc") else {},
    }


async def high_prob_detail(query, ticker):
    """Telegram handler — 24-model High-Probability Signal Engine."""
    tk  = str(ticker).upper()
    _ld = await query.message.reply_text(f"⚙️ Running 24-model engine for {tk}…", parse_mode=H)
    conn = get_conn()
    try:
        try:
            spy_px = pd.read_sql(
                "SELECT close FROM stock_daily WHERE ticker='SPY'"
                " ORDER BY substr(trade_date,7,4)||substr(trade_date,1,2)||substr(trade_date,4,2) DESC LIMIT 2",
                conn)
            spy_ret = (float(spy_px["close"].iloc[0]) / float(spy_px["close"].iloc[1]) - 1) * 100 \
                      if len(spy_px) >= 2 else 0.0
        except Exception:
            spy_ret = 0.0

        res = high_prob_signals_engine(tk, conn, spy_ret)
        bt  = _hp_walk_forward_backtest(tk, conn)
    except Exception as exc:
        log.warning(f"high_prob_detail {tk}: {exc}")
        try: await _ld.delete()
        except Exception: pass
        await query.message.reply_text(f"❌ Error: {exc}", reply_markup=InlineKeyboardMarkup([[BACK_BTN]]))
        return
    conn.close()

    sig  = res["signal"]
    prob = res["prob"]
    conf = res["conf"]
    total_m = res.get("total_m", 22)
    s_em = {"BULL": "🟢", "BEAR": "🔴", "NEUTRAL": "⚪", "SELL_PREMIUM": "💰"}.get(sig, "⚪")
    c_em = {"HIGH": "🔥", "MEDIUM": "✅", "LOW": "⚠️"}.get(conf, "⚠️")

    _ML = {
        "gex":           "GEX",        "pcr_z":        "PCR-Z",
        "oi_momentum":   "OI-Mom",     "gamma_pin":    "GammaPin",
        "vol_flow":      "VolFlow",    "iv_skew":      "IV-Skew",
        "rv_iv":         "RV/IV",      "oi_term_struct":"OI-TS",
        "maxpain_vel":   "MPVel",      "iv_rank":      "IV-Rank",
        "pcp_dev":       "PCP-Dev",    "vol_regime":   "VolReg",
        "multi_expiry":  "MultiExp",   "smart_uoa":    "SmartUOA",
        "hhi_pin":       "HHI-Pin",    "pcr_vel":      "PCR-Vel",
        "vrvp":          "VRVP",       "vwap_dev":     "VWAP-Dev",
        "expected_move": "ExpMove",    "left_skew":    "LeftSkew",
        "vrp":           "VRP",        "put_call_wall": "P/C-Wall",
        "short_squeeze": "ShortSqz",
        "momentum": "Mom12-1",
    }
    _ME = {"BULL": "🟢", "BEAR": "🔴", "NEUTRAL": "⚪", "SELL_PREMIUM": "💰"}

    lines = [
        hdr(f"🎯 HIGH-PROB ENGINE · {tk}"),
        "",
        f"{s_em} <b>{sig}</b>  {c_em} <b>{conf} CONF</b>  Prob: <b>{prob:.0f}%</b>",
        (f"Votes 🟢{res['bull_v']} 🔴{res['bear_v']} "
         f"💰{res.get('sell_v',0)} ⚪{res['neut_v']} /{total_m}"),
        "",
        f"<b>Strategy:</b> {res['strategy']}",
    ]
    if res["warn"]:
        lines.append(f"<i>{res['warn']}</i>")

    # VRVP Premium Box (if applicable)
    vbox = res.get("vrvp_box", {})
    if vbox.get("lo") and vbox.get("hi"):
        lines += [
            "",
            "<b>📦 VRVP Premium Collection Box:</b>",
            mono(
                f"POC  ${vbox.get('poc', 0):>7.2f}  (max volume)\n"
                f"VAH  ${vbox.get('vah', 0):>7.2f}  (sell call above)\n"
                f"VAL  ${vbox.get('val', 0):>7.2f}  (sell put below)\n"
                f"Box  ${vbox['lo']:>7.2f} - ${vbox['hi']:<7.2f}\n"
                f"Spot ${tk:>4} @ ${res['spot']:<8.2f}"
            ),
            "<i>Sell IC/Strangle: put at VAL, call at VAH when price enters box.</i>",
        ]

    # Wall levels
    wall = res["models"].get("put_call_wall", {})
    if wall.get("put_wall") and wall.get("call_wall"):
        lines += [
            "",
            "<b>🧱 OI Walls:</b>",
            mono(
                f"Put Wall  ${wall['put_wall']:>7.2f} ({wall.get('pw_str',1):.1f}x)\n"
                f"Spot      ${res['spot']:>7.2f}\n"
                f"Call Wall ${wall['call_wall']:>7.2f} ({wall.get('cw_str',1):.1f}x)"
            ),
        ]

    lines.append("")

    # Split model table into 3 rows of ~7-8 for mobile
    all_models = list(_ML.items())
    chunk_labels = ["Models 1-8:", "Models 9-16:", "Models 17-24:"]
    chunks = [all_models[:8], all_models[8:16], all_models[16:]]
    for lbl, chunk in zip(chunk_labels, chunks):
        if not chunk: continue
        lines.append(f"<b>{lbl}</b>")
        tbl = []
        for nm, short in chunk:
            r  = res["models"].get(nm, {})
            ms = r.get("signal", "NEUTRAL")
            mp = r.get("prob", 50)
            mw = res["weights"].get(nm, 1.0)
            tbl.append(f"{short[:8]:<8} {_ME.get(ms,'⚪')}{ms[:4]:<4} {mp:>3}% {mw:.1f}")
        lines.append(mono("\n".join(tbl)))
        lines.append("")

    # Top signals across all 24 models (sorted by prob, exclude NEUTRAL<55)
    ranked = sorted(
        [(nm, r) for nm, r in res["models"].items() if r.get("prob", 50) >= 60],
        key=lambda x: x[1].get("prob", 50), reverse=True)
    if ranked:
        lines.append("<b>Top Signals (prob>=60%):</b>")
        for nm, r in ranked[:5]:
            ms  = r.get("signal", "?"); mp = r.get("prob", 50)
            rsn = r.get("reason", "")[:80]
            lines.append(f"{_ME.get(ms,'⚪')} <b>{_ML.get(nm, nm)}</b> {mp}%")
            lines.append(f"  <i>{rsn}</i>")
    lines.append("")

    meta  = bt.get("meta", {})
    n_ext = meta.get("n_extreme", 0)
    n_cln = meta.get("n_clean", 0)
    bt_rows = [(nm, v) for nm, v in bt.items() if nm != "meta" and isinstance(v, dict) and "acc" in v]
    if bt_rows:
        lines.append("<b>Walk-Forward Backtest:</b>")
        btl = []
        for nm, v in bt_rows:
            bar = "█" * min(int(v["acc"] / 10), 10)
            btl.append(f"{nm:<10} {v['acc']:>5.1f}%  n={v['n']}  {bar}")
        lines.append(mono("\n".join(btl)))
        lines.append(f"<i>Removed {n_ext} extreme days · {n_cln} clean</i>")
    else:
        lines.append("<i>Backtest: need ≥15 clean days</i>")

    lines += [
        "",
        "<i>🧠 24-model ensemble · weights auto-calibrate daily.</i>",
        f"<i>SPY {spy_ret:+.2f}% · VRVP·VWAP·VRP·Walls·LeftSkew·VRP added.</i>",
        "<i>Sources: Bali·Cremers·Ni·Amin·Sinclair·Xing·Carr·SpotGamma·Dalton</i>",
    ]

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Refresh", callback_data=f"high_prob_{tk}"),
         InlineKeyboardButton("📉 Mean Rev",  callback_data=f"mean_rev_{tk}")],
        [InlineKeyboardButton("🏦 Inst Sig",  callback_data=f"inst_sig_{tk}"),
         InlineKeyboardButton("📊 OI Menu",   callback_data="menu_oi")],
        [BACK_BTN],
    ])
    try: await _ld.delete()
    except Exception: pass
    await _safe_reply(query.message, "\n".join(lines), reply_markup=kb)


def _hp_model_short_squeeze(ticker, conn, spot):
    """Model 23 - short-squeeze / short-covering. BULL when shorts are trapped
    (high short interest + days-to-cover) and covering/igniting. Free data
    (yfinance short interest + price/volume + options OI). See short_squeeze_signal."""
    try:
        sq = short_squeeze_signal(ticker, conn)
    except Exception:
        return {"signal": "NEUTRAL", "prob": 50, "reason": "short-squeeze: data n/a"}
    sc = int(sq.get("score", 0) or 0)
    stage = sq.get("stage", ""); lbl = sq.get("label", "")
    if sc >= 4:
        return {"signal": "BULL", "prob": min(88, 58 + sc * 7),
                "reason": f"Short-squeeze {sc}/5 [{stage}] - {lbl}"}
    if sc == 3:
        return {"signal": "BULL", "prob": 68,
                "reason": f"Short-squeeze building {sc}/5 [{stage}]"}
    if sc == 2:
        return {"signal": "NEUTRAL", "prob": 55,
                "reason": f"Squeeze fuel present {sc}/5 - no trigger yet"}
    return {"signal": "NEUTRAL", "prob": 50, "reason": f"No squeeze ({sc}/5)"}



# ═══════════════════════════════════════════════════════════════════
# ── OPEX RADAR  +  MACRO EVENT->TRADE MAP
# ═══════════════════════════════════════════════════════════════════
_QUARTER_MONTHS = {3, 6, 9, 12}

def _opex_latest_date(conn, ticker):
    try:
        d = pd.read_sql("SELECT trade_date_now FROM options_change WHERE ticker=?"
            " ORDER BY substr(trade_date_now,7,4)||substr(trade_date_now,1,2)||substr(trade_date_now,4,2) DESC LIMIT 1",
            conn, params=(ticker,))
        return d["trade_date_now"].iloc[0] if not d.empty else None
    except Exception:
        return None

def _opex_spot(conn, ticker):
    try:
        s = pd.read_sql("SELECT close FROM stock_daily WHERE ticker=?"
            " ORDER BY substr(trade_date,7,4)||substr(trade_date,1,2)||substr(trade_date,4,2) DESC LIMIT 1",
            conn, params=(ticker,))
        if not s.empty and float(s["close"].iloc[0]) > 0:
            return float(s["close"].iloc[0])
    except Exception:
        pass
    return 0.0

def _opex_parse_date(s):
    for fmt in ("%m-%d-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(str(s), fmt).date()
        except ValueError:
            continue
    return None

def opex_radar(conn, tickers=None):
    """Options-expiration radar: notional rolling off per upcoming expiry + the
    current dealer-gamma regime. Flags the dominant near-term OpEx (quad-witching
    in Mar/Jun/Sep/Dec) and emits the post-OpEx vol-expansion playbook.
    Uses the ETFs/equities you store (no SPX index) -> the equity/ETF slice only."""
    tickers = tickers or ["SPY", "QQQ", "IWM"]
    today = datetime.now().date()
    agg = {}
    for tk in tickers:
        d = _opex_latest_date(conn, tk)
        if not d:
            continue
        spot = _opex_spot(conn, tk)
        try:
            df = pd.read_sql("SELECT expiry_date, SUM(openInt_Call_now+openInt_Put_now) AS oi"
                " FROM options_change WHERE ticker=? AND trade_date_now=? GROUP BY expiry_date",
                conn, params=(tk, d))
        except Exception:
            continue
        if spot <= 0:
            try:
                spot = float(pd.read_sql("SELECT AVG(strike) s FROM options_change WHERE ticker=? AND trade_date_now=?",
                    conn, params=(tk, d))["s"].iloc[0] or 0)
            except Exception:
                spot = 0.0
        if spot <= 0:
            continue
        for _, r in df.iterrows():
            ed = _opex_parse_date(r["expiry_date"])
            if not ed:
                continue
            dte = (ed - today).days
            if dte < 0:
                continue
            oi = float(r["oi"] or 0)
            a = agg.setdefault(ed, {"notional": 0.0, "oi": 0.0, "dte": dte})
            a["notional"] += oi * 100.0 * spot
            a["oi"] += oi
    if not agg:
        return {}
    rows = sorted(({"dt": k, "date": k.strftime("%m-%d-%Y"), **v} for k, v in agg.items()),
                  key=lambda x: x["dt"])
    near = [r for r in rows if r["dte"] <= 45] or rows
    major = dict(max(near, key=lambda x: x["notional"]))
    major["is_quarterly"] = major["dt"].month in _QUARTER_MONTHS
    gex = {}
    try:
        gex = _compute_gex("SPY", conn, _opex_spot(conn, "SPY"))
    except Exception:
        pass
    return {"rows": rows[:8], "major": major, "gex": gex, "tickers": tickers}

def _opex_notional_str(n):
    if n >= 1e12:
        return f"${n/1e12:.2f}T"
    if n >= 1e9:
        return f"${n/1e9:.1f}B"
    if n >= 1e6:
        return f"${n/1e6:.0f}M"
    return f"${n:,.0f}"

def _fmt_opex_report(rad):
    if not rad or not rad.get("rows"):
        return "No OpEx data found in DB (need SPY/QQQ/IWM options_change rows)."
    mj = rad["major"]
    gex = rad.get("gex") or {}
    lines = ["\U0001F5D3 <b>OPEX RADAR</b>  (" + "+".join(rad["tickers"]) + ")",
             "<i>ETF/equity slice you store - excludes SPX index headline.</i>", ""]
    tbl = []
    for r in rad["rows"]:
        star = "*" if r["date"] == mj["date"] else " "
        tbl.append(f"{star}{r['date']} {r['dte']:>3}d {_opex_notional_str(r['notional']):>7}")
    lines.append("<pre>" + "\n".join(tbl) + "</pre>")
    q = "QUAD-WITCHING" if mj.get("is_quarterly") else "monthly OpEx"
    lines.append(f"\U0001F3AF <b>Major: {mj['date']}</b> ({mj['dte']}d) - {q}")
    lines.append(f"Notional rolling off: <b>{_opex_notional_str(mj['notional'])}</b>")
    if gex:
        flip = gex.get("zero_gamma")
        cw = gex.get("call_wall")
        pw = gex.get("put_wall")
        lines.append("")
        lines.append(f"SPY gamma regime: <b>{gex.get('gex_signal','?')}</b>"
                     + (f" | flip ${flip:.0f}" if flip else ""))
        if cw or pw:
            lines.append(f"Walls: Put ${pw or 0:.0f}  ..  Call ${cw or 0:.0f}")
    lines += ["",
        "<b>Post-OpEx playbook:</b>",
        "- Dealer gamma rolls off after the * date -> pin releases -> realized vol tends to RISE next week.",
        "- <b>Trade:</b> buy a 1-week-out SPY strangle / VIX call spread on the * day (long vol).",
        "- <b>Seasonality:</b> week after monthly/quad OpEx is historically weak -> tactical SPY put spread.",
        "- <b>Hedge/size:</b> vol can stay crushed if macro is calm; keep size small or finance with a spread.",
        "<i>This is a ~55-60% tendency, not a certainty - size for being wrong.</i>"]
    return "\n".join(lines)

async def opex_command(update, ctx):
    """/opex [TICKERS...] - options-expiration radar + post-OpEx playbook."""
    args = list(getattr(ctx, "args", []) or [])
    conn = get_conn()
    try:
        rad = opex_radar(conn, [a.upper() for a in args] if args else None)
    finally:
        conn.close()
    await update.message.reply_text(_fmt_opex_report(rad), parse_mode=H, reply_markup=_kb_opex())


# Macro event -> liquid-trade map. Same idea as _DOWNSTREAM_MAP but for
# geopolitical/macro events: event -> 1st/2nd/3rd-order effects -> liquid
# instruments -> defined-risk structure -> hedge. NOT financial advice.
MACRO_EVENT_MAP = {
  "iran": {
    "title": "Iran sanctions relief / reintegration",
    "thesis": "A deal lifts sanctions -> Iranian crude (~1-1.5M bpd) returns -> MORE global oil supply.",
    "chain": [
      ("1st", "More crude supply -> lower oil price", "USO / CL futures / XLE", "SHORT oil, SHORT US E&P",
       "Long USO put spread or short CL; defined risk"),
      ("2nd", "Cheaper fuel -> airlines and transports win", "JETS, DAL, LUV, XTN", "LONG airlines",
       "Long call spread on JETS or DAL"),
      ("2nd", "Regional trade reopens (Turkey is top partner)", "TUR, Gulf/UAE names", "LONG Turkey/MENA",
       "Long TUR shares, small size"),
      ("3rd", "Lower energy -> EM importers relief, disinflation", "EEM, INDA", "LONG EM importers",
       "Long EEM call spread"),
    ],
    "hedge": "Hold a few OTM oil calls - deals collapse often and oil spikes on failure.",
    "caveat": "DO NOT buy Iranian rial: sanctioned, non-convertible, chronic inflation. Binary headline risk -> use options spreads, size small.",
  },
  "oil_spike": {
    "title": "Oil supply shock / Hormuz disruption (oil spikes)",
    "thesis": "Supply disruption or war premium -> oil price jumps.",
    "chain": [
      ("1st", "Oil up", "XLE, XOM, CVX, OIH, tankers (FRO)", "LONG energy", "Long XLE or call spread"),
      ("2nd", "Fuel costs hit airlines/transports", "JETS, DAL", "SHORT airlines", "Put spread on JETS"),
      ("2nd", "Higher inflation -> rate-cut hopes fade", "TLT", "SHORT long bonds", "TLT put spread"),
      ("3rd", "Risk-off if shock is large", "SPY, VIX", "HEDGE equities", "SPY put / VIX calls"),
    ],
    "hedge": "Pair long energy with SPY puts; if shock fades, oil mean-reverts fast.",
    "caveat": "Oil also driven by OPEC+, demand, SPR releases - spikes can reverse quickly.",
  },
  "fed_cut": {
    "title": "Fed rate cuts / dovish pivot",
    "thesis": "Lower rates -> cheaper money -> long-duration and rate-sensitive assets rally.",
    "chain": [
      ("1st", "Yields fall", "TLT, gold (GLD)", "LONG duration + gold", "Long TLT, GLD"),
      ("1st", "Growth/small caps re-rate", "QQQ, IWM, ARKK", "LONG growth + small caps", "Long IWM call spread"),
      ("2nd", "Weaker USD", "DXY, EEM, FXI", "SHORT USD, LONG EM", "Long EEM"),
      ("3rd", "Housing/REITs relief", "XHB, VNQ", "LONG housing/REITs", "Long XHB"),
    ],
    "hedge": "If cuts are because of recession, equities can still fall -> keep some SPY puts.",
    "caveat": "Distinguish a good cut (soft landing) from a panic cut (recession) - opposite equity outcomes.",
  },
  "fed_hike": {
    "title": "Fed rate hikes / hawkish surprise",
    "thesis": "Higher rates -> discount rate up -> long-duration and growth de-rate.",
    "chain": [
      ("1st", "Yields rise", "TLT", "SHORT long bonds", "TLT put spread"),
      ("1st", "Growth/small caps fall", "QQQ, IWM", "SHORT growth/small caps", "Put spread on IWM"),
      ("2nd", "Stronger USD", "UUP, EEM", "LONG USD, SHORT EM", "Long UUP / short EEM"),
      ("2nd", "Banks net-interest-margin up", "XLF, KRE", "LONG banks (early)", "Long XLF call spread"),
    ],
    "hedge": "Banks can also fall if hikes break credit (2023 regional crisis) - watch spreads.",
    "caveat": "Markets front-run the Fed - position vs the SURPRISE, not the known path.",
  },
  "war": {
    "title": "Middle East / geopolitical escalation",
    "thesis": "Conflict escalation -> risk-off + energy and defense bid.",
    "chain": [
      ("1st", "Oil and gold spike", "XLE, GLD", "LONG energy + gold", "Long GLD, XLE"),
      ("1st", "Defense names bid", "ITA, LMT, RTX, NOC", "LONG defense", "Long ITA call spread"),
      ("2nd", "Equities sell, vol spikes", "SPY, VIX", "HEDGE / LONG vol", "SPY put / VIX call spread"),
      ("2nd", "Flight to USD", "UUP", "LONG USD", "Long UUP"),
    ],
    "hedge": "Escalations often de-escalate fast -> use defined-risk spreads, not linear shorts.",
    "caveat": "The biggest moves come on the SURPRISE; once headlined, much is priced.",
  },
  "china_stimulus": {
    "title": "China stimulus / reopening",
    "thesis": "Large stimulus -> China demand up -> commodities and China equities rally.",
    "chain": [
      ("1st", "China equities re-rate", "FXI, KWEB, MCHI", "LONG China", "Long FXI call spread"),
      ("1st", "Industrial metals demand", "FCX, copper, BHP, RIO", "LONG metals/miners", "Long FCX"),
      ("2nd", "Commodity currencies / EM", "EWA, EEM", "LONG commodity EM", "Long EEM"),
      ("3rd", "China-revenue / luxury names", "China-exposed exporters", "LONG China-exposed", "Selective longs"),
    ],
    "hedge": "China stimulus often disappoints in scale -> size small, use call spreads.",
    "caveat": "Policy follow-through is the key risk - headlines without money move little.",
  },
  "usd_up": {
    "title": "Strong US dollar (DXY breakout)",
    "thesis": "Rising USD is a global tightening -> headwind for commodities, EM, US multinationals.",
    "chain": [
      ("1st", "USD up", "UUP", "LONG USD", "Long UUP"),
      ("1st", "Commodities and gold pressured", "GLD, GDX, oil", "SHORT commodities", "Put spread on GDX"),
      ("2nd", "EM under pressure", "EEM, EWZ", "SHORT EM", "EEM put spread"),
      ("3rd", "US large-cap exporters hit on FX", "multinational-heavy names", "trim exporters", "Hedge with SPY puts"),
    ],
    "hedge": "USD reverses hard on a dovish Fed -> watch rate expectations.",
    "caveat": "USD strength from growth (risk-on) vs from fear (risk-off) leads to different equity outcomes.",
  },
}

def event_trade_map(key):
    if not key:
        return None
    k = str(key).lower().strip()
    if k in MACRO_EVENT_MAP:
        return MACRO_EVENT_MAP[k]
    for name, ev in MACRO_EVENT_MAP.items():
        if k in name or k in ev["title"].lower() or k in ev["thesis"].lower():
            return ev
    return None

def _fmt_event_report(ev):
    if not ev:
        keys = ", ".join(sorted(MACRO_EVENT_MAP))
        return f"Unknown event. Available: <code>{keys}</code>\nUsage: <code>/event iran</code>"
    lines = [f"\U0001F30D <b>{ev['title']}</b>", f"<i>{ev['thesis']}</i>", "",
             "<b>Causal chain -> trades:</b>"]
    for order, effect, instr, direction, structure in ev["chain"]:
        lines.append(f"[{order}] {effect}")
        lines.append(f"   -> <b>{direction}</b>: {instr}")
        lines.append(f"   <i>{structure}</i>")
    lines += ["", f"\U0001F6E1 <b>Hedge:</b> {ev['hedge']}",
              f"⚠ <b>Caveat:</b> {ev['caveat']}",
              "", "<i>Educational framework, not financial advice. Let options price the odds.</i>"]
    return "\n".join(lines)

async def event_command(update, ctx):
    """/event [name] - macro/geopolitical event -> liquid-trade map."""
    args = list(getattr(ctx, "args", []) or [])
    if not args:
        keys = ", ".join(sorted(MACRO_EVENT_MAP))
        await update.message.reply_text(
            "\U0001F30D <b>Macro Event -> Trade Map</b>\nUsage: <code>/event iran</code>\n\n"
            f"Available: <code>{keys}</code>", parse_mode=H)
        return
    await update.message.reply_text(_fmt_event_report(event_trade_map(args[0])), parse_mode=H, reply_markup=_kb_event(args[0]))


# ═══════════════════════════════════════════════════════════════════
# ── MORNING EVENT BRIEFING  (optimistic / pessimistic / balanced)
# ═══════════════════════════════════════════════════════════════════
# Ordered list of the events surfaced in the daily brief. Edit to taste.
_BRIEFING_EVENTS = ["iran", "war", "oil_spike", "fed_cut", "china_stimulus"]

def morning_briefing(conn, event_keys=None):
    """Assemble the daily brief: OpEx/gamma regime + a 3-view read on each major
    macro event (optimistic / pessimistic / balanced) with the lead defined-risk
    trade and hedge. Pure composition over opex_radar + MACRO_EVENT_MAP."""
    keys = event_keys or _BRIEFING_EVENTS
    rad = {}
    try:
        rad = opex_radar(conn)
    except Exception:
        rad = {}
    evs = [(k, MACRO_EVENT_MAP[k]) for k in keys if k in MACRO_EVENT_MAP]
    news = {}
    try:
        news = _event_news_google(keys)
    except Exception:
        news = {}
    regime = {}
    try:
        regime = _risk_regime()
    except Exception:
        regime = {}
    mom = {}
    try:
        _mdf, _masof = load_momentum_ranks(conn)
        if _mdf is not None and not _mdf.empty:
            mom = {"asof": _masof,
                   "top": _mdf.head(3)[["ticker", "ret_12_1"]].values.tolist(),
                   "bottom": _mdf.tail(3).iloc[::-1][["ticker", "ret_12_1"]].values.tolist()}
    except Exception:
        mom = {}
    return {"opex": rad, "events": evs, "news": news, "regime": regime, "momentum": mom}

def _fmt_briefing(b):
    from datetime import datetime as _dt
    news = b.get("news") or {}
    lines = ["☀️ <b>MORNING BRIEF</b> — " + _dt.now().strftime("%a %d %b %Y"), ""]
    _rg = b.get("regime") or {}
    if _rg:
        lines.append(f"{_rg.get('emoji','')} <b>Regime: {_rg.get('label','?')}</b> (score {_rg.get('score',0):+d})")
    try:
        _fc = _fomc_context()
    except Exception:
        _fc = None
    if _fc and _fc.get("pre_drift"):
        lines.append(f"📅 Pre-FOMC drift (FOMC {_fc['next']}) — historically bullish.")
    if _rg or (_fc and _fc.get("pre_drift")):
        lines.append("")

    rad = b.get("opex") or {}
    mj = rad.get("major") or {}
    gex = rad.get("gex") or {}
    if mj:
        q = "QUAD-WITCHING" if mj.get("is_quarterly") else "monthly OpEx"
        lines.append(f"\U0001F5D3 Next major OpEx: <b>{mj.get('date','?')}</b> ({mj.get('dte','?')}d, {q})")
    if gex:
        flip = gex.get("zero_gamma")
        lines.append("\U0001F4C8 SPY gamma: <b>" + str(gex.get("gex_signal", "?")) + "</b>"
                     + (f" | flip ${flip:.0f}" if flip else ""))
        if gex.get("gex_signal") == "TRENDING":
            lines.append("<i>Negative gamma -> dealers amplify moves; expect bigger swings.</i>")
        elif gex.get("gex_signal") == "PINNING":
            lines.append("<i>Positive gamma -> dealers dampen moves; range/mean-revert bias.</i>")
    _mm = b.get("momentum") or {}
    if _mm.get("top"):
        _tp = " · ".join(f"{t} {r:+.0f}%" for t, r in _mm["top"])
        _bt = " · ".join(f"{t} {r:+.0f}%" for t, r in _mm["bottom"])
        lines.append("🚀 <b>Momentum leaders:</b> " + _tp)
        lines.append("🐌 <b>Laggards:</b> " + _bt)
    lines.append("")
    lines.append("<b>\U0001F30D Events to watch</b>  (optimistic / pessimistic / balanced)")

    for key, ev in b.get("events", []):
        lead = ev["chain"][0] if ev.get("chain") else None
        lead_trade = (f"{lead[3]} ({lead[2]})" if lead else "")
        lines.append("")
        lines.append(f"<b>{ev['title']}</b>  <code>/event {key}</code>")
        if key in news:
            lines.append(f"🔥 <i>{news[key][0][:90]}</i>")
        lines.append(f"\U0001F7E2 <b>Bull:</b> {ev['thesis']}")
        lines.append(f"\U0001F534 <b>Bear:</b> {ev['caveat']}")
        if lead:
            lines.append(f"⚖️ <b>Play:</b> {lead_trade} — <i>{lead[4]}</i>")
        lines.append(f"\U0001F6E1 <b>Hedge:</b> {ev['hedge']}")

    lines += ["",
        "<i>Tap /event NAME for the full 1st/2nd/3rd-order chain, or /opex for the expiration radar.</i>",
        "<i>Educational - not advice. Size for being wrong; let options price the odds.</i>"]
    return "\n".join(lines)

# ═══════════════════════════════════════════════════════════════════
# ── NEXT-DAY GAME PLAN (/plan) — condensed portfolio plan for Telegram
# ═══════════════════════════════════════════════════════════════════
_ST_CACHE = {}

def _stocktwits_sentiment(tk):
    """Free StockTwits crowd sentiment for a ticker (Bullish/Bearish tags). Cached 10 min."""
    import time as _t
    now = _t.time()
    c = _ST_CACHE.get(tk)
    if c and now - c[0] < 600:
        return c[1]
    res = None
    try:
        import urllib.request, json as _j
        req = urllib.request.Request(
            f"https://api.stocktwits.com/api/2/streams/symbol/{tk}.json",
            headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=6) as r:
            d = _j.loads(r.read().decode())
        bull = bear = 0
        for m in d.get("messages", []):
            b = ((m.get("entities") or {}).get("sentiment") or {}).get("basic")
            if b == "Bullish": bull += 1
            elif b == "Bearish": bear += 1
        if bull + bear > 0:
            res = {"bull": bull, "bear": bear,
                   "label": "BULLISH" if bull > bear * 1.3 else "BEARISH" if bear > bull * 1.3 else "MIXED"}
    except Exception:
        res = None
    _ST_CACHE[tk] = (now, res)
    return res

_FH_CACHE = {}
_TONE_NEG_OVR = ("paused", "pause", "halt", "stall", "collapse", "delay", "fail", "fell through",
                 "off the table", "blocked", "breakdown", "no deal", "scrapped", "called off", "closure",
                 "closed", "shut", "blockade", "hormuz", "strait", "embargo", "sanction", "escalat",
                 "conflict", " war", "attack", "strike on", "missile", "disrupt", "shortage", "glut",
                 "probe", "lawsuit", "fraud", "recall", "downgrade", "plunge", "crash", "selloff",
                 "sell-off", "tariff", "ban ", "slump", "tumble")
_TONE_POS = ("rally", "surge", "bull", "gain", "beat", "strong", "rise", "record", "upgrade", "boost",
             "growth", "profit", "optimis", "soar", "jump", "outperform", "tops", "win", "rebound",
             "expand", "demand")
_TONE_NEG = ("drop", "fall", "sell", "bear", "loss", "cut", "slash", "warn", "fear", "decline",
             "recession", "weak", "miss", "layoff", "sink", "dump", "concern", "risk", "threat", "crisis")

def _headline_tone(title):
    t = str(title).lower()
    if any(w in t for w in _TONE_NEG_OVR):
        return -1
    p = sum(1 for w in _TONE_POS if w in t)
    n = sum(1 for w in _TONE_NEG if w in t)
    return 1 if p > n else (-1 if n > p else 0)

def _finnhub_sentiment(tk):
    """Finnhub sentiment: news-sentiment endpoint, else the free company-news endpoint scored
    locally. Needs FINNHUB_API_KEY env var. Cached 15 min; None if no key / nothing usable."""
    import os, time as _t
    key = os.environ.get("FINNHUB_API_KEY") or os.environ.get("FINNHUB_KEY")
    if not key:
        return None
    now = _t.time()
    c = _FH_CACHE.get(tk)
    if c and now - c[0] < 900:
        return c[1]
    res = None
    import urllib.request, json as _j
    try:
        with urllib.request.urlopen(
                f"https://finnhub.io/api/v1/news-sentiment?symbol={tk}&token={key}", timeout=6) as r:
            d = _j.loads(r.read().decode())
        bp = (d.get("sentiment") or {}).get("bullishPercent")
        if bp is not None:
            res = {"bull_pct": bp * 100,
                   "label": "BULLISH" if bp >= 0.6 else "BEARISH" if bp <= 0.4 else "MIXED"}
    except Exception:
        res = None
    if res is None:
        try:
            from datetime import date, timedelta
            _to = date.today(); _from = _to - timedelta(days=7)
            with urllib.request.urlopen(
                    f"https://finnhub.io/api/v1/company-news?symbol={tk}&from={_from}&to={_to}&token={key}",
                    timeout=6) as r:
                arts = _j.loads(r.read().decode())
            bull = bear = 0
            for a in (arts or [])[:40]:
                tt = _headline_tone((a.get("headline", "") + " " + a.get("summary", "")))
                if tt > 0: bull += 1
                elif tt < 0: bear += 1
            if bull + bear > 0:
                res = {"bull_pct": bull / (bull + bear) * 100,
                       "label": "BULLISH" if bull > bear * 1.3 else "BEARISH" if bear > bull * 1.3 else "MIXED"}
        except Exception:
            res = None
    _FH_CACHE[tk] = (now, res)
    return res

_IVR_CACHE = {}

def _iv_rank(conn, tk):
    """ATM IV rank over stored ~6mo premium history. Cached 30 min."""
    import time as _t
    now = _t.time()
    c = _IVR_CACHE.get(tk)
    if c and now - c[0] < 1800:
        return c[1]
    res = None
    try:
        df = pd.read_sql("SELECT trade_date_now, strike, expiry_date, lastPrice_Call_now "
                         "FROM options_change WHERE UPPER(ticker)=?", conn, params=(tk.upper(),))
        sd = pd.read_sql("SELECT trade_date, close FROM stock_daily WHERE UPPER(ticker)=?",
                         conn, params=(tk.upper(),))
        if not df.empty and not sd.empty:
            spot_by = {str(d): float(x) for d, x in zip(sd["trade_date"], sd["close"])}

            def _pdt(x):
                for f in ("%m-%d-%Y", "%Y-%m-%d"):
                    try:
                        return datetime.strptime(str(x), f)
                    except Exception:
                        pass
                return None

            ivs = []
            for d, g in df.groupby("trade_date_now"):
                spot = spot_by.get(str(d)); dd = _pdt(d)
                if not spot or dd is None:
                    continue
                best = None
                for _, row in g.iterrows():
                    ed = _pdt(row["expiry_date"])
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
                    iv = _implied_vol_hp(prem, spot, K, dte / 365.0)
                    if 0.01 < iv < 5:
                        ivs.append(iv)
            if len(ivs) >= 10:
                cur, lo, hi = ivs[-1], min(ivs), max(ivs)
                res = {"iv": cur, "rank": (cur - lo) / (hi - lo) * 100 if hi > lo else 50.0}
    except Exception:
        res = None
    _IVR_CACHE[tk] = (now, res)
    return res

_EARN_CACHE = {}

def _next_earnings(tk):
    """Next earnings date + days away via yfinance. Cached 2h. None if unavailable."""
    import time as _t
    now = _t.time()
    c = _EARN_CACHE.get(tk)
    if c and now - c[0] < 21600:
        return c[1]
    res = None
    try:
        import pandas as _pd
        t = yf.Ticker(tk)
        nowd = _pd.Timestamp.now().normalize()
        dts = []
        try:                                    # fast path: .calendar
            cal = t.calendar
            e = cal.get("Earnings Date") if isinstance(cal, dict) else None
            for x in (e if isinstance(e, (list, tuple)) else [e]) if e else []:
                dts.append(_pd.Timestamp(x).normalize())
        except Exception:
            pass
        if not dts:                             # slow fallback only if needed
            try:
                ed = t.get_earnings_dates(limit=12)
                if ed is not None and len(ed):
                    for ix in ed.index:
                        ts = _pd.Timestamp(ix)
                        ts = ts.tz_localize(None) if ts.tzinfo else ts
                        dts.append(ts.normalize())
            except Exception:
                pass
        fut = sorted(d for d in dts if d >= nowd)
        if fut:
            res = {"date": fut[0].strftime("%b %d"), "days": int((fut[0] - nowd).days)}
    except Exception:
        res = None
    _EARN_CACHE[tk] = (now, res)
    return res

def _plan_prem(conn, tk, K, exp, typ):
    col = "lastPrice_Call_now" if typ == "call" else "lastPrice_Put_now"
    mdy = _to_mdy(exp)
    try:
        pr = pd.read_sql(
            f"SELECT {col} AS last FROM options_change WHERE UPPER(ticker)=? AND strike=? AND expiry_date=? "
            "ORDER BY substr(trade_date_now,7,4)||substr(trade_date_now,1,2)||substr(trade_date_now,4,2) DESC LIMIT 1",
            conn, params=(tk.upper(), float(K), mdy))
        if not pr.empty and pr.iloc[0]["last"] and float(pr.iloc[0]["last"]) > 0:
            return float(pr.iloc[0]["last"])
    except Exception:
        pass
    return None

def _kb_plan(conn=None):
    rows = []
    if conn is not None:
        try:
            tks = [r[0] for r in conn.execute(
                "SELECT DISTINCT UPPER(ticker) FROM trades WHERE status='OPEN' ORDER BY 1").fetchall()]
            btns = [InlineKeyboardButton(f"📈 {t}", callback_data=f"plan_chart_{t}") for t in tks[:9]]
            for i in range(0, len(btns), 3):
                rows.append(btns[i:i + 3])
            tvbtns = [InlineKeyboardButton(f"📺 {t}", callback_data=f"tvc_{t}") for t in tks[:9]]
            for i in range(0, len(tvbtns), 3):
                rows.append(tvbtns[i:i + 3])
            if tks:
                rows.append([InlineKeyboardButton("📊 Portfolio chart", callback_data="plan_port_chart")])
        except Exception:
            pass
    rows.append([InlineKeyboardButton("🔄 Refresh", callback_data="plan_view"),
                 InlineKeyboardButton("⬅️ Hub", callback_data="hub_menu")])
    return InlineKeyboardMarkup(rows)

def _plan_oi_flow(conn, tk, spot):
    """Compact OI-flow line for /plan: net build, vol-PCR, buy/sell/hedge split, calendar tilt."""
    try:
        df = pd.read_sql(
            "SELECT strike, expiry_date, change_OI_Call, change_OI_Put, vol_Call_now, vol_Put_now "
            "FROM options_change WHERE UPPER(ticker)=? AND trade_date_now=(SELECT trade_date_now "
            "FROM options_change WHERE UPPER(ticker)=? ORDER BY substr(trade_date_now,7,4)||"
            "substr(trade_date_now,1,2)||substr(trade_date_now,4,2) DESC LIMIT 1)",
            conn, params=(tk.upper(), tk.upper()))
    except Exception:
        return None
    if df is None or df.empty or not spot:
        return None
    for c in df.columns:
        if c != "expiry_date":
            df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0.0)
    coi = df["change_OI_Call"].sum(); poi = df["change_OI_Put"].sum()
    cv = df["vol_Call_now"].sum(); pv = df["vol_Put_now"].sum()
    pcrv = pv / cv if cv else 0.0; net = coi - poi
    e = "🟢" if net > 0 and pcrv < 1 else "🔴" if (net < 0 or pcrv > 1.3) else "🟡"
    split = ""
    try:
        bystrike = df.groupby("strike", as_index=False).agg(
            call_oi_change=("change_OI_Call", "sum"), put_oi_change=("change_OI_Put", "sum"))
        enr, _, _, _, _ = _oi_intent_algo(bystrike, spot)
        enr["wt"] = enr["call_oi_change"].abs() + enr["put_oi_change"].abs()
        BMAP = {"BULLISH": "buy", "BULLISH_BREAK": "buy", "BEARISH": "bear", "NEAR_BEARISH": "bear",
                "HEDGE": "hedge", "HEDGE_UNWIND": "hedge", "COVERED_CALL": "cc-sell",
                "STRADDLE": "vol", "UNWIND": "unwind", "NEUTRAL": "neut"}
        agg = {}
        for _, r in enr.iterrows():
            b = BMAP.get(r["intent"], "neut"); agg[b] = agg.get(b, 0) + r["wt"]
        tot = sum(agg.values()) or 1
        top = sorted(agg.items(), key=lambda x: -x[1])[:3]
        split = " · ".join(f"{int(v/tot*100)}% {k}" for k, v in top if v > 0)
    except Exception:
        pass

    def _ek(s):
        for f in ("%m-%d-%Y", "%Y-%m-%d"):
            try:
                return datetime.strptime(str(s), f)
            except Exception:
                pass
        return datetime.max
    tilt = ""
    try:
        cal = df.groupby("expiry_date").agg(c=("change_OI_Call", "sum"), p=("change_OI_Put", "sum"))
        cal["k"] = [_ek(i) for i in cal.index]; cal = cal.sort_values("k")
        if len(cal) >= 2:
            med = cal["k"].median()
            fn = (cal[cal["k"] <= med]["c"] - cal[cal["k"] <= med]["p"]).sum()
            bn = (cal[cal["k"] > med]["c"] - cal[cal["k"] > med]["p"]).sum()
            tilt = "near-dated" if abs(fn) > abs(bn) else "later-dated"
    except Exception:
        pass
    lines = [f"  💧 OI {e} net {net:+,.0f} (C{coi:+,.0f}/P{poi:+,.0f}) PCRv {pcrv:.2f}"]
    if split:
        lines.append(f"     flow: {split}")
    if tilt:
        lines.append(f"     new OI → {tilt}")
    return "\n".join(lines)


def _plan_patterns(tk, spot, pw=None, cw=None):
    """Compact pattern/regime line for /plan: EMA sequence, golden/death cross, flag, gamma."""
    parts = []
    try:
        c = yf.Ticker(tk).history(period="1y")["Close"].dropna()
    except Exception:
        c = pd.Series(dtype=float)
    if len(c) >= 30:
        px = float(c.iloc[-1])
        e8 = float(c.ewm(span=8).mean().iloc[-1]); e21 = float(c.ewm(span=21).mean().iloc[-1])
        e50 = float(c.ewm(span=50).mean().iloc[-1])
        if e8 > e21 > e50 and px >= e8:
            parts.append("EMA 8>21>50 🟢")
        elif e8 < e21 < e50 and px <= e8:
            parts.append("EMA 8<21<50 🔴")
        if len(c) >= 200:
            a50 = c.rolling(50).mean().iloc[-1]; a200 = c.rolling(200).mean().iloc[-1]
            parts.append("golden-cross 🟢" if a50 > a200 else "death-cross 🔴")
        pole = px / float(c.iloc[-21]) - 1
        last5 = c.iloc[-5:]; drift = float(last5.iloc[-1] / last5.iloc[0] - 1)
        rng = float((last5.max() - last5.min()) / last5.mean())
        if pole > 0.08 and -0.04 < drift <= 0.01 and rng < 0.06:
            parts.append("bull-flag 🟢")
        elif pole < -0.08 and -0.01 <= drift < 0.04 and rng < 0.06:
            parts.append("bear-flag 🔴")
    if pw and cw and spot:
        flip = (float(pw) + float(cw)) / 2.0
        if spot < min(float(pw), flip):
            parts.append(f"neg-gamma<${flip:.0f} 🔴")
        elif spot > max(float(cw), flip):
            parts.append("above-walls 🟢")
        else:
            parts.append(f"pinned~${flip:.0f} ⚪")
    return ("  🔺 " + " · ".join(parts)) if parts else None


def _plan_trust(conn, tk, hold=5, thr=0.005):
    """Inline signal track-record for /plan: per-signal hit-rate so the user can prioritize.
    Backtests OI-bias / momentum / RSI from DB and pulls the 24-model's resolved accuracy."""
    out = []
    try:
        sk = "substr(trade_date,7,4)||substr(trade_date,1,2)||substr(trade_date,4,2)"
        sd = pd.read_sql(f"SELECT trade_date, close FROM stock_daily WHERE ticker=? ORDER BY {sk}",
                         conn, params=(tk.upper(),))
        if len(sd) >= 30:
            sd["close"] = pd.to_numeric(sd["close"], errors="coerce")
            oi = pd.read_sql(
                "SELECT trade_date_now AS trade_date, SUM(COALESCE(change_OI_Call,0)) cb, "
                "SUM(COALESCE(change_OI_Put,0)) pb FROM options_change WHERE ticker=? "
                "GROUP BY trade_date_now", conn, params=(tk.upper(),))
            sd = sd.merge(oi, on="trade_date", how="left")
            sd["oin"] = sd["cb"].fillna(0) - sd["pb"].fillna(0)
            sd["oiz"] = (sd["oin"] - sd["oin"].rolling(20, min_periods=8).mean()) \
                / (sd["oin"].rolling(20, min_periods=8).std() + 1e-9)
            sd["mom"] = sd["close"] / sd["close"].shift(10) - 1
            d = sd["close"].diff()
            up = d.clip(lower=0).rolling(14).mean(); dn = (-d.clip(upper=0)).rolling(14).mean()
            sd["rsi"] = 100 - 100 / (1 + up / dn.replace(0, np.nan))
            sd["fwd"] = sd["close"].shift(-hold) / sd["close"] - 1
            d2 = sd.dropna(subset=["fwd"])

            def _hit(mask, direction):
                s = d2[mask]
                if len(s) < 5:
                    return None
                dr = direction(s)
                return float(((dr > 0) & (s["fwd"] > thr)).sum()
                             + ((dr < 0) & (s["fwd"] < -thr)).sum()) / len(s) * 100
            for nm, h in (("OI", _hit(d2["oiz"].abs() > 0.5, lambda s: np.sign(s["oiz"]))),
                          ("Mom", _hit(d2["mom"].abs() > 0.01, lambda s: np.sign(s["mom"]))),
                          ("RSI", _hit((d2["rsi"] < 35) | (d2["rsi"] > 65),
                                       lambda s: np.where(s["rsi"] < 35, 1, -1)))):
                if h is not None:
                    out.append((nm, h))
    except Exception:
        pass
    try:
        m = pd.read_sql("SELECT AVG(correct)*100 h, COUNT(*) n FROM signal_accuracy "
                        "WHERE ticker=? AND correct>=0", conn, params=(tk.upper(),))
        if not m.empty and m["n"].iloc[0] and int(m["n"].iloc[0]) >= 5:
            out.append(("24M", float(m["h"].iloc[0])))
    except Exception:
        pass
    if not out:
        return None
    best = max(out, key=lambda x: x[1])
    body = " · ".join(f"{k} {v:.0f}%" for k, v in out)
    return f"  🎯 Track record: {body} → trust {best[0]}"


def _kfb(k):
    """Strike formatter that keeps half-dollar strikes (385→'385', 617.5→'617.5')."""
    try:
        k = float(k)
    except Exception:
        return str(k)
    return f"{k:.0f}" if k == int(k) else f"{k:.2f}".rstrip("0").rstrip(".")


def _bar(pct, n=10):
    """Tiny emoji gauge for Telegram, e.g. 60% → 🟩🟩🟩🟩🟩🟩⬜⬜⬜⬜."""
    f = int(round(max(0.0, min(100.0, pct)) / 100 * n))
    return "🟩" * f + "⬜" * (n - f)


def _bs_vec(S, K, T, r, sig, typ):
    S = np.maximum(S, 1e-9)
    if T <= 0:
        return np.maximum(S - K, 0.0) if typ == "call" else np.maximum(K - S, 0.0)
    d1 = (np.log(S / K) + (r + 0.5 * sig * sig) * T) / (sig * np.sqrt(T)); d2 = d1 - sig * np.sqrt(T)
    if typ == "call":
        return S * norm.cdf(d1) - K * np.exp(-r * T) * norm.cdf(d2)
    return K * np.exp(-r * T) * norm.cdf(-d2) - S * norm.cdf(-d1)


def _pl_bounds(legs, spot, n=400):
    """Max profit / max loss to expiry over a price grid; flags unbounded up-profit / up-loss."""
    if not legs or not spot or spot <= 0:
        return None
    ks = [float(l["K"]) for l in legs]; hi = max(3 * spot, max(ks) * 1.5, spot + 1)
    grid = np.linspace(0.01, hi, n); pnl = np.zeros(n)
    for l in legs:
        intr = np.maximum(grid - l["K"], 0.0) if l["typ"] == "call" else np.maximum(l["K"] - grid, 0.0)
        pnl += (intr - l["entry"]) * l["m"]
    s = float(pnl[-1] - pnl[-2])
    return {"maxp": float(pnl.max()), "maxl": float(pnl.min()), "up": s > 1e-6, "dn": s < -1e-6}


def _pl_mp(b):
    return "∞" if (b and b["up"]) else (f"${b['maxp']:,.0f}" if b else "—")


def _pl_ml(b):
    return "∞" if (b and b["dn"]) else (f"${b['maxl']:,.0f}" if b else "—")


def _pl_analytics(legs, spot, r=0.045):
    """Breakeven(s), POP and EV at the nearest expiry via an IV-implied lognormal."""
    if not legs or not spot or spot <= 0:
        return None
    iv = float(np.median([max(l["iv"], .01) for l in legs])); h = max(min(l["dte"] for l in legs), 0)
    T = max(h, .5) / 365.0; sig = max(iv * np.sqrt(T), 1e-4)
    grid = np.linspace(max(spot * np.exp(-4 * sig), .01), spot * np.exp(4 * sig), 401); pnl = np.zeros_like(grid)
    for l in legs:
        rem = max(l["dte"] - h, 0) / 365.0
        val = (np.maximum(grid - l["K"], 0.0) if l["typ"] == "call" else np.maximum(l["K"] - grid, 0.0)) \
            if rem <= 0 else _bs_vec(grid, l["K"], rem, r, l["iv"], l["typ"])
        pnl += (val - l["entry"]) * l["m"]
    mu = np.log(spot) + (r - .5 * iv * iv) * T
    pdf = np.exp(-(np.log(grid) - mu) ** 2 / (2 * sig * sig)) / (grid * sig * np.sqrt(2 * np.pi)); w = pdf / pdf.sum()
    bes = [float(grid[i - 1] - pnl[i - 1] * (grid[i] - grid[i - 1]) / (pnl[i] - pnl[i - 1]))
           for i in range(1, len(grid)) if (pnl[i - 1] < 0) != (pnl[i] < 0)]
    return {"pop": float(w[pnl > 0].sum()) * 100, "ev": float((w * pnl).sum()), "be": bes, "h": h}


def _pl_exit(legs, spot):
    """Rank legs by urgency to close first (assignment / expiry / profit captured / cut-loss)."""
    rows = []
    for l in legs:
        b = _pl_bounds([l], spot) or {"maxp": 0.0}
        cap = (l["pnl"] / b["maxp"]) if b["maxp"] > 1e-6 else (1.0 if l["pnl"] > 0 else 0.0)
        itm = (spot > l["K"]) if l["typ"] == "call" else (spot < l["K"])
        pnlp = ((l["cur"] - l["entry"]) / l["entry"] * 100 * (1 if l["qty"] > 0 else -1)) if l["entry"] else 0
        sc = 0.0; why = []
        if l["side"] == "short" and itm and l["dte"] <= 10: sc += 4; why.append("short ITM assign-risk")
        if l["dte"] <= 5: sc += 2.5; why.append(f"{l['dte']}DTE expiry")
        if l["pnl"] > 0 and cap >= 0.6: sc += 2.5; why.append(f"{cap*100:.0f}% captured")
        elif l["pnl"] > 0 and l["dte"] <= 10: sc += 1.5; why.append("profit into expiry")
        if pnlp <= -50 and l["dte"] <= 21: sc += 1.5; why.append("down≥50% cut/roll")
        rows.append({"l": l, "sc": sc, "why": "; ".join(why) or "hold"})
    rows.sort(key=lambda r: r["sc"], reverse=True)
    return rows


def _pl_tickets(legs, spot, cw, pw, r=0.045):
    """Ready-to-place order ideas: sell-to-cut-cost overlays at the wall, max value, buy-to-close."""
    tips = []
    iv = float(np.median([max(l["iv"], .01) for l in legs])); h = min(max(min(l["dte"] for l in legs), 1), 10)
    em = spot * iv * np.sqrt(h / 365.0)
    for l in legs:
        K, typ, ivl, dte, q = l["K"], l["typ"], max(l["iv"], .01), l["dte"], abs(l["qty"])
        T, Trem = max(dte, 0) / 365.0, max(dte - h, 0) / 365.0
        if l["side"] == "long":
            if typ == "call":
                k2 = cw or round(spot + em); tgt = max(cw or spot * 1.06, spot + em)
                if k2 and k2 > K:
                    c = bs_greeks(spot, k2, T, r, ivl, "call").get("price", 0)
                    if c > 0.02:
                        tips.append(f"SELL {q}× {_kfb(k2)}C ≈${c:.2f} → spread, cost ${max(l['entry']-c,0):.2f}")
                mv = bs_greeks(tgt, K, Trem, r, ivl, "call").get("price", max(tgt - K, 0)) if Trem > 0 else max(tgt - K, 0)
                tips.append(f"{_kfb(K)}C max ≈${mv:.2f} @ ${tgt:.0f} (now ${l['cur']:.2f})")
            else:
                k2 = pw or round(spot - em); tgt = min(pw or spot * 0.94, spot - em)
                if k2 and k2 < K:
                    c = bs_greeks(spot, k2, T, r, ivl, "put").get("price", 0)
                    if c > 0.02:
                        tips.append(f"SELL {q}× {_kfb(k2)}P ≈${c:.2f} → spread, cost ${max(l['entry']-c,0):.2f}")
                mv = bs_greeks(tgt, K, Trem, r, ivl, "put").get("price", max(K - tgt, 0)) if Trem > 0 else max(K - tgt, 0)
                tips.append(f"{_kfb(K)}P max ≈${mv:.2f} @ ${tgt:.0f} (now ${l['cur']:.2f})")
        else:
            tips.append(f"BUY {q}× {_kfb(K)}{typ[0].upper()} ≈${l['cur']:.2f} close (P&L ${l['pnl']:,.0f})")
    return tips


def _pl_beta(conn, tk, lookback=60):
    """Beta of ticker vs SPY from stock_daily (defaults to 1.0)."""
    try:
        sk = "substr(trade_date,7,4)||substr(trade_date,1,2)||substr(trade_date,4,2)"
        a = pd.read_sql(f"SELECT trade_date,close FROM stock_daily WHERE ticker=? ORDER BY {sk}", conn, params=(tk.upper(),))
        s = pd.read_sql(f"SELECT trade_date,close FROM stock_daily WHERE ticker='SPY' ORDER BY {sk}", conn)
    except Exception:
        return 1.0
    if len(a) < 20 or len(s) < 20:
        return 1.0
    m = a.merge(s, on="trade_date", suffixes=("", "_s")).tail(lookback + 1)
    if len(m) < 20:
        return 1.0
    ra = pd.to_numeric(m["close"], errors="coerce").pct_change().dropna().values
    rs = pd.to_numeric(m["close_s"], errors="coerce").pct_change().dropna().values
    n = min(len(ra), len(rs))
    if n < 15 or np.var(rs[-n:]) <= 0:
        return 1.0
    return float(np.cov(ra[-n:], rs[-n:])[0, 1] / np.var(rs[-n:]))


def _next_day_plan(conn):
    """Condensed whole-portfolio next-day plan: regime + Greeks + per-stock levels,
    expected move, StockTwits sentiment, per-leg actions, and a morning checklist."""
    L = ["🌅 <b>NEXT-DAY GAME PLAN</b>"]
    try:
        rg = _risk_regime()
        L.append(f"{rg['emoji']} Regime <b>{rg['label']}</b> ({rg['score']:+d})")
    except Exception:
        pass
    try:
        tr = pd.read_sql("SELECT ticker,option_type,strike,quantity,expiry,entry_price,entry_iv "
                         "FROM trades WHERE status='OPEN'", conn)
    except Exception:
        tr = pd.DataFrame()
    if tr is None or tr.empty:
        L.append("No open positions.")
        return "\n".join(L)
    R = 0.045
    by = {}
    for _, t in tr.iterrows():
        by.setdefault(str(t["ticker"]).upper(), []).append(t)
    net_dd = net_th = 0.0
    port_maxp = port_maxl = port_ev = spy_dd = 0.0
    port_up = port_dn = False
    gross_by = {}
    checklist, blocks = [], []
    for tk, legs in by.items():
        spot = _gex_spot(conn, tk) or _last_price(tk)
        if not spot:
            continue
        g = {}
        try:
            g = _compute_gex(tk, conn, spot)
        except Exception:
            pass
        cw, pw = g.get("call_wall"), g.get("put_wall")
        tk_dd = tk_th = 0.0
        ivs, leglines, tk_legs = [], [], []
        for t in legs:
            typ = "call" if str(t["option_type"]).lower().startswith("c") else "put"
            K = float(t["strike"] or 0); qty = int(t["quantity"] or 0); exp = str(t["expiry"])
            dte = None
            for fmt in ("%Y-%m-%d", "%m-%d-%Y"):
                try:
                    dte = (datetime.strptime(exp, fmt) - datetime.now()).days; break
                except Exception:
                    pass
            if dte is None or K <= 0 or qty == 0:
                continue
            T = max(dte, 0) / 365.0
            entry = float(t["entry_price"] or 0)
            prem = _plan_prem(conn, tk, K, exp, typ)
            iv = _implied_vol_hp(prem, spot, K, T, R) if (prem and T > 0) else (float(t["entry_iv"] or 0) or 0.30)
            ivs.append(iv)
            gg = bs_greeks(spot, K, T, R, iv, typ) if T > 0 else {"delta": 0, "theta": 0, "price": (prem or entry)}
            cur = prem if prem else gg.get("price", entry)
            m = qty * 100
            tk_dd += gg["delta"] * m * spot * 0.01
            tk_th += gg["theta"] * m
            side = "short" if qty < 0 else "long"
            money = "ITM" if ((spot > K) if typ == "call" else (spot < K)) else "OTM"
            pnlp = ((cur - entry) / entry * 100 * (1 if qty > 0 else -1)) if entry else 0
            acts = []
            if dte <= 7: acts.append(f"{dte}DTE decide")
            elif dte <= 21: acts.append(f"{dte}DTE roll-plan")
            if side == "short" and money == "ITM": acts.append("ITM assign-risk")
            if pnlp >= 50: acts.append("take profit")
            elif pnlp <= -50: acts.append("cut/roll")
            a = "; ".join(acts) if acts else "hold"
            leglines.append(f"  {'🔻' if side == 'short' else '🔹'} {side} ${K:.0f}{typ[0].upper()} "
                            f"{dte}d {money} {pnlp:+.0f}% → {a}")
            if acts:
                checklist.append(f"{tk} ${K:.0f}{typ[0].upper()}: {a}")
            tk_legs.append({"K": K, "typ": typ, "entry": entry, "m": m, "iv": iv,
                            "dte": dte, "cur": cur, "side": side, "qty": qty, "exp": exp,
                            "ticker": tk, "spot": spot, "pnl": (cur - entry) * m})
        net_dd += tk_dd; net_th += tk_th
        ivm = sorted(ivs)[len(ivs) // 2] if ivs else 0.30
        em = spot * ivm * (1 / 252.0) ** 0.5
        head = f"<b>{tk}</b> ${spot:.2f} · ±${em:.2f}/1d"
        lv = []
        if pw: lv.append(f"PW${pw:.0f}")
        if cw: lv.append(f"CW${cw:.0f}")
        if lv:
            head += " · " + " ".join(lv)
        stt = _stocktwits_sentiment(tk)
        if stt:
            head += f"\n  💬 StockTwits {stt['label']} ({stt['bull']}🟢/{stt['bear']}🔴)"
        fh = _finnhub_sentiment(tk)
        if fh:
            head += f"\n  🛰 Finnhub {fh['label']} ({fh['bull_pct']:.0f}% bull)"
        rd = _reddit_sentiment(tk)
        if rd:
            head += f"\n  👽 r/WSB {rd['label']} ({rd['bull']}🟢/{rd['bear']}🔴 of {rd['n']})"
        ivr = _iv_rank(conn, tk)
        if ivr:
            hint = "cheap→buy" if ivr["rank"] < 30 else "rich→sell" if ivr["rank"] > 70 else "mid"
            head += f"\n  🌡️ IV Rank {ivr['rank']:.0f} ({ivr['iv']*100:.0f}%) {hint}"
        ea = _next_earnings(tk)
        if ea and ea["days"] <= 14:
            head += f"\n  📅 ⚠️ Earnings {ea['date']} ({ea['days']}d) — gap risk"
        extra = []
        _ofl = _plan_oi_flow(conn, tk, spot)
        if _ofl:
            extra.append(_ofl)
        _ptl = _plan_patterns(tk, spot, pw, cw)
        if _ptl:
            extra.append(_ptl)
        _trl = _plan_trust(conn, tk)
        if _trl:
            extra.append(_trl)
        _b = _pl_bounds(tk_legs, spot)
        _a = _pl_analytics(tk_legs, spot)
        if _b:
            port_maxp += _b["maxp"]; port_maxl += _b["maxl"]
            port_up = port_up or _b["up"]; port_dn = port_dn or _b["dn"]
        if _a:
            port_ev += _a["ev"]
        try:
            spy_dd += tk_dd * _pl_beta(conn, tk)
        except Exception:
            spy_dd += tk_dd
        for _l in tk_legs:
            _g = abs(_l["cur"] * _l["m"]) if _l["side"] == "long" else _l["entry"] * abs(_l["m"])
            gross_by[tk] = gross_by.get(tk, 0.0) + _g
        ana = []
        if _b:
            ana.append(f"  💰 MaxP {_pl_mp(_b)} · MaxL {_pl_ml(_b)}")
        if _a:
            _be = ", ".join(f"${x:.0f}" for x in _a["be"]) or "—"
            ana.append(f"  📈 POP {_a['pop']:.0f}% {_bar(_a['pop'])} · EV ${_a['ev']:,.0f} · B/E {_be}")
        _xs = [r for r in _pl_exit(tk_legs, spot) if r["sc"] > 0][:2]
        if _xs:
            ana.append("  🎯 Close first: " + " | ".join(
                f"{_kfb(r['l']['K'])}{r['l']['typ'][0].upper()} ({r['why']})" for r in _xs))
        for _t in _pl_tickets(tk_legs, spot, cw, pw)[:4]:
            ana.append("  🧾 " + _t)
        blocks.append(head + "\n" + ("\n".join(extra) + "\n" if extra else "")
                      + "\n".join(leglines) + ("\n" + "\n".join(ana) if ana else ""))
    L.append(f"Net Δ/+1% <b>${net_dd:,.0f}</b> · Θ/day <b>${net_th:,.0f}</b>")
    L.append(f"📊 Port MaxP <b>{'∞' if port_up else f'${port_maxp:,.0f}'}</b> · "
             f"MaxL <b>{'∞' if port_dn else f'${port_maxl:,.0f}'}</b> · EV <b>${port_ev:,.0f}</b>")
    _conc = (f" · top {max(gross_by, key=gross_by.get)} "
             f"{gross_by[max(gross_by, key=gross_by.get)]/sum(gross_by.values())*100:.0f}%"
             if gross_by and sum(gross_by.values()) > 0 else "")
    L.append(f"📐 SPY-Δ <b>${spy_dd:,.0f}</b>/+1% SPY{_conc}")
    L.append("")
    L += blocks
    if checklist:
        L.append("")
        L.append("✅ <b>Tomorrow:</b>")
        for c in checklist[:8]:
            L.append("• " + c)
    L.append("")
    L.append("<i>Educational, not advice.</i>")
    return "\n".join(L)

# ═══════════════════════════════════════════════════════════════════
# ── MARKET WRAP — "what just happened" narrator (data-driven, offline)
#    Builds a Kobeissi-style write-up from the DB + yfinance. Only ever
#    states numbers the system actually computes — it never invents figures
#    (no fabricated liquidation $, ETF AUM or econ prints).
# ═══════════════════════════════════════════════════════════════════
_WRAP_IDX = [("^GSPC", "S&P 500", 52e12), ("^NDX", "Nasdaq 100", 32e12),
             ("^IXIC", "Nasdaq Comp", None), ("^DJI", "Dow", None), ("^RUT", "Russell 2000", None)]
_WRAP_CROSS = [("BTC-USD", "Bitcoin", "crypto"), ("ETH-USD", "Ethereum", "crypto"),
               ("GC=F", "Gold", "metal"), ("CL=F", "Crude oil", "energy"),
               ("DX-Y.NYB", "US Dollar", "fx"), ("^TNX", "10Y yield", "rates")]
_WRAP_LEV = [("TQQQ", "3x Nasdaq", 3), ("SOXL", "3x Semis", 3), ("SPXL", "3x S&P", 3),
             ("TSLL", "2x Tesla", 2), ("SOXS", "-3x Semis", -3), ("SQQQ", "-3x Nasdaq", -3)]


def _wrap_hist(sym, period="6d", interval="1d"):
    try:
        h = yf.Ticker(sym).history(period=period, interval=interval)
        return h if h is not None and not h.empty else None
    except Exception:
        return None


def _wrap_quote(sym):
    """{last, prev, pct, pts} from daily history (last row = current/partial day)."""
    h = _wrap_hist(sym, "6d", "1d")
    if h is None or len(h) < 2:
        return None
    c = h["Close"].dropna()
    if len(c) < 2:
        return None
    last, prev = float(c.iloc[-1]), float(c.iloc[-2])
    if prev == 0:
        return None
    return {"last": last, "prev": prev, "pct": (last / prev - 1) * 100, "pts": last - prev}


def _wrap_intraday_shape(sym):
    """Opening behaviour + sharpest peak→trough drawdown window from 5m bars."""
    h = _wrap_hist(sym, "1d", "5m")
    if h is None or len(h) < 3:
        return None
    highs = h["High"].cummax()
    dd = (h["Low"] - highs) / highs
    i_tr = dd.idxmin()
    i_pk = h.loc[:i_tr, "High"].idxmax()
    mins = max(int((i_tr - i_pk).total_seconds() // 60), 0)
    return {"open": float(h["Open"].iloc[0]), "cur": float(h["Close"].iloc[-1]),
            "hi": float(h["High"].max()), "lo": float(h["Low"].min()),
            "dd_pct": float(dd.min()) * 100, "dd_mins": mins,
            "peak": float(highs.loc[i_tr]), "trough": float(h["Low"].loc[i_tr]),
            "t_peak": i_pk, "t_trough": i_tr}


def _wrap_fmt_t(ts):
    try:
        return ts.strftime("%I:%M %p ET").lstrip("0")
    except Exception:
        return str(ts)


def _wrap_money(x):
    a = abs(x)
    if a >= 1e12:
        return f"${x/1e12:.2f}T"
    if a >= 1e9:
        return f"${x/1e9:.1f}B"
    if a >= 1e6:
        return f"${x/1e6:.0f}M"
    return f"${x:,.0f}"


def _wrap_pick(seed, opts):
    """Deterministic-but-varied phrasing pick (no RNG dependency)."""
    return opts[int(seed) % len(opts)]


_WRAP_BLS = {"CUUR0000SA0": "CPI", "CUUR0000SA0L1E": "Core CPI"}
_WRAP_MACRO_CACHE = {"ts": 0.0, "data": None}


def _wrap_macro():
    """Latest CPI / Core CPI YoY from the BLS public API (keyless, free, cached 6h).
    Returns {label: {yoy, month}} or None — the macro print line, no fabrication."""
    import time as _t, json as _json, urllib.request as _ur, datetime as _dtm
    now = _t.time()
    if _WRAP_MACRO_CACHE["data"] is not None and now - _WRAP_MACRO_CACHE["ts"] < 21600:
        return _WRAP_MACRO_CACHE["data"]
    try:
        yr = _dtm.datetime.now().year
        body = _json.dumps({"seriesid": list(_WRAP_BLS), "startyear": str(yr - 2),
                            "endyear": str(yr)}).encode()
        req = _ur.Request("https://api.bls.gov/publicAPI/v1/timeseries/data/", data=body,
                          headers={"Content-Type": "application/json", "User-Agent": "nyse-data/1.0"})
        j = _json.load(_ur.urlopen(req, timeout=15))
        out = {}
        for s in j.get("Results", {}).get("series", []):
            sid = s.get("seriesID")
            pts = []
            for d in s.get("data", []):
                if not str(d.get("period", "")).startswith("M"):
                    continue
                try:
                    pts.append((d["year"], d["period"], float(d["value"])))
                except Exception:
                    pass
            if len(pts) < 13:
                continue
            latest = pts[0]
            prior = next((p for p in pts if p[1] == latest[1] and int(p[0]) == int(latest[0]) - 1), None)
            if not prior or prior[2] == 0:
                continue
            mname = _dtm.date(2000, int(latest[1][1:]), 1).strftime("%b")
            out[_WRAP_BLS[sid]] = {"yoy": (latest[2] / prior[2] - 1) * 100, "month": f"{mname} {latest[0]}"}
        if out:  # only cache on success so transient failures retry next call
            _WRAP_MACRO_CACHE["ts"] = now
            _WRAP_MACRO_CACHE["data"] = out
        return out or None
    except Exception:
        return None


def wrap_facts(conn, universe_cap=120):
    """Compute the structured fact pack for the market wrap."""
    F = {"ts": datetime.now(), "indices": [], "lead": None, "shape": None,
         "vix": None, "cross": [], "lev": [], "movers_up": [], "movers_dn": [],
         "breadth": None, "options": None, "book": None, "catalyst": None, "macro": None}

    for sym, name, cap in _WRAP_IDX:
        q = _wrap_quote(sym)
        if not q:
            continue
        d = {"sym": sym, "name": name, "cap": cap, **q}
        if cap:
            d["dollars"] = cap * (q["pct"] / 100.0)
        F["indices"].append(d)
    if F["indices"]:
        F["lead"] = max(F["indices"], key=lambda d: abs(d["pct"]))
        F["shape"] = _wrap_intraday_shape(F["lead"]["sym"])

    vq = _wrap_quote("^VIX")
    if vq:
        F["vix"] = vq

    F["macro"] = _wrap_macro()

    for sym, name, kind in _WRAP_CROSS:
        q = _wrap_quote(sym)
        if q:
            F["cross"].append({"name": name, "kind": kind, **q})

    for sym, name, mult in _WRAP_LEV:
        q = _wrap_quote(sym)
        if q:
            F["lev"].append({"sym": sym, "name": name, "mult": mult, **q})

    try:
        tks = [r[0] for r in conn.execute("SELECT DISTINCT ticker FROM stock_daily").fetchall()][:universe_cap]
    except Exception:
        tks = []
    rows = []
    if tks:
        try:
            data = yf.download(tks, period="6d", interval="1d", group_by="ticker",
                               threads=True, progress=False)
            for t in tks:
                try:
                    c = data[t]["Close"].dropna() if len(tks) > 1 else data["Close"].dropna()
                    if len(c) >= 2 and float(c.iloc[-2]) > 0:
                        rows.append({"t": t, "pct": (float(c.iloc[-1]) / float(c.iloc[-2]) - 1) * 100,
                                     "last": float(c.iloc[-1])})
                except Exception:
                    continue
        except Exception:
            rows = []
    if not rows:  # DB EOD fallback
        sk = "substr(trade_date,7,4)||substr(trade_date,1,2)||substr(trade_date,4,2)"
        for t in tks:
            try:
                c = pd.read_sql(f"SELECT close FROM stock_daily WHERE ticker=? ORDER BY {sk} DESC LIMIT 2",
                                conn, params=(t,))
                v = pd.to_numeric(c["close"], errors="coerce").dropna().tolist()
                if len(v) >= 2 and v[1] > 0:
                    rows.append({"t": t, "pct": (v[0] / v[1] - 1) * 100, "last": v[0]})
            except Exception:
                continue
    if rows:
        up = sum(1 for r in rows if r["pct"] > 0); dn = sum(1 for r in rows if r["pct"] < 0)
        F["breadth"] = {"up": up, "dn": dn, "n": len(rows)}
        rows.sort(key=lambda r: r["pct"])
        F["movers_dn"] = rows[:5]
        F["movers_up"] = rows[-5:][::-1]

    cand = None
    if F["movers_dn"] or F["movers_up"]:
        cand = max(F["movers_dn"] + F["movers_up"], key=lambda r: abs(r["pct"]))["t"]
    if cand:
        try:
            for n in (yf.Ticker(cand).news or []):
                title = (n.get("content", {}) or {}).get("title") or n.get("title")
                if title:
                    F["catalyst"] = {"ticker": cand, "title": title}
                    break
        except Exception:
            pass

    try:
        sk = "substr(trade_date_now,7,4)||substr(trade_date_now,1,2)||substr(trade_date_now,4,2)"
        snap = conn.execute(f"SELECT trade_date_now FROM options_change ORDER BY {sk} DESC LIMIT 1").fetchone()
        if snap:
            d0 = snap[0]
            agg = pd.read_sql(
                "SELECT ticker, SUM(change_OI_Call) cc, SUM(change_OI_Put) cp, "
                "SUM(openInt_Call_now) oc, SUM(openInt_Put_now) op "
                "FROM options_change WHERE trade_date_now=? GROUP BY ticker", conn, params=(d0,))
            if not agg.empty:
                agg = agg[~agg["ticker"].astype(str).str.startswith("^")]
                tc = float(agg["cc"].sum()); tp = float(agg["cp"].sum())
                oc = float(agg["oc"].sum()); op = float(agg["op"].sum())
                agg["net"] = agg["cp"].fillna(0) - agg["cc"].fillna(0)
                put_heavy = agg.reindex(agg["net"].sort_values(ascending=False).index).head(3)
                F["options"] = {"date": d0, "call_chg": tc, "put_chg": tp,
                                "pcr": (op / oc) if oc else None,
                                "put_heavy": list(put_heavy["ticker"])}
    except Exception:
        pass

    try:
        tr = pd.read_sql("SELECT ticker,option_type,quantity FROM trades WHERE status='OPEN'", conn)
        if not tr.empty:
            mv = {r["t"]: r["pct"] for r in rows}
            bt = {}
            for _, t in tr.iterrows():
                tk = str(t["ticker"]).upper()
                pc = mv.get(tk)
                if pc is None:
                    q = _wrap_quote(tk); pc = q["pct"] if q else 0.0
                typ = "call" if str(t["option_type"]).lower().startswith("c") else "put"
                qty = int(t["quantity"] or 0)
                bt.setdefault(tk, {"pct": pc, "dir": 0})
                bt[tk]["dir"] += (1 if (typ == "call") == (qty > 0) else -1)
            F["book"] = {"tickers": [{"tk": k, "pct": v["pct"], "dir": v["dir"]} for k, v in bt.items()]}
    except Exception:
        pass

    return F


def wrap_narrative(F, html=True):
    """Render the fact pack into a punchy 'what just happened' write-up."""
    b = (lambda s: f"<b>{s}</b>") if html else (lambda s: s)
    esc = (lambda s: str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")) if html else (lambda s: s)
    L = []
    lead = F["lead"]; shape = F["shape"]
    seed = int(F["ts"].strftime("%Y%m%d"))

    if lead:
        d = lead["pct"]
        big_swing = bool(shape and abs(shape["dd_pct"]) >= 1.2 and shape["dd_mins"] > 0)
        if big_swing:
            op = (shape["open"] / lead["prev"] - 1) * 100
            dollar = f" — an estimated {_wrap_money(abs(lead['dollars']))} in value" if lead.get("dollars") else ""
            hooks = [
                f"In just {shape['dd_mins']} minutes, the {lead['name']} cratered {shape['dd_pct']:.1f}% from its "
                f"intraday peak{dollar}. It opened {op:+.1f}% and is now {d:+.1f}% — a violent round trip on the day.",
                f"A textbook volatility shock: the {lead['name']} ran to {op:+.1f}% at the open, then dumped "
                f"{shape['dd_pct']:.1f}% in {shape['dd_mins']} minutes{dollar}, settling {d:+.1f}% on the day."]
            L.append("⚡ " + b("WHAT JUST HAPPENED") + "\n" + _wrap_pick(seed, hooks))
        elif lead.get("dollars"):
            verb = "erased" if d < 0 else "added"
            hooks = [
                f"The {lead['name']} {verb} an estimated {_wrap_money(abs(lead['dollars']))} in market value today, "
                f"moving {d:+.1f}% ({lead['pts']:+,.0f} pts).",
                f"An estimated {_wrap_money(abs(lead['dollars']))} {('vanished from' if d<0 else 'flowed into')} the "
                f"{lead['name']} today as it moved {d:+.1f}% ({lead['pts']:+,.0f} pts)."]
            L.append("⚡ " + b("WHAT JUST HAPPENED") + "\n" + _wrap_pick(seed, hooks))
        else:
            risk = "sell-off" if d < 0 else "rally"
            L.append("⚡ " + b("WHAT JUST HAPPENED") + "\n"
                     f"The {lead['name']} is {('down' if d<0 else 'up')} {abs(d):.1f}% "
                     f"({lead['pts']:+,.0f} pts) in a session {risk}.")

    if shape and lead:
        op = (shape["open"] / lead["prev"] - 1) * 100
        L.append("\n🕘 " + b("THE TIMELINE") + "\n"
                 f"The {lead['name']} opened {op:+.1f}% vs the prior close, peaked near {shape['peak']:,.0f} at "
                 f"{_wrap_fmt_t(shape['t_peak'])}, then slid to {shape['trough']:,.0f} by {_wrap_fmt_t(shape['t_trough'])} "
                 f"({shape['dd_pct']:+.1f}%). It now trades at {shape['cur']:,.0f}.")

    if F["catalyst"]:
        big = next((m for m in (F["movers_dn"] + F["movers_up"]) if m["t"] == F["catalyst"]["ticker"]), None)
        if big:
            line = (f"{big['t']} moved {big['pct']:+.1f}% — the session's standout — on the headline: "
                    f"“{esc(F['catalyst']['title'])}”.")
        else:
            line = f"A key catalyst: {F['catalyst']['ticker']} — “{esc(F['catalyst']['title'])}”."
        L.append("\n📰 " + b("THE CATALYST") + "\n" + line)

    if F["vix"]:
        v = F["vix"]
        tone = "spiking — fear is back" if v["pct"] > 8 else "rising" if v["pct"] > 0 else "easing"
        L.append("\n🌪 " + b("VOLATILITY") + "\n"
                 f"The VIX is at {v['last']:.1f} ({v['pct']:+.1f}%), {tone}. "
                 + ("Above 20 signals real stress." if v["last"] >= 20 else "Still contained below 20."))

    if F.get("macro"):
        m = F["macro"]
        bits = [f"{k} {m[k]['yoy']:.1f}% YoY ({m[k]['month']})" for k in ("CPI", "Core CPI") if k in m]
        if bits:
            hot = m.get("CPI", {}).get("yoy", 0)
            vs = "well above" if hot >= 3 else "above" if hot > 2.3 else "near"
            L.append("\n📅 " + b("MACRO BACKDROP") + "\n"
                     f"Inflation backdrop: {', '.join(bits)} — {vs} the Fed's 2% target.")

    if F["cross"]:
        parts = []
        for c in F["cross"]:
            if c["kind"] == "rates":
                parts.append(f"the 10Y yield {('rose' if c['pct']>0 else 'fell')} to {c['last']:.2f}%")
            else:
                parts.append(f"{c['name']} {c['pct']:+.1f}%")
        L.append("\n🌐 " + b("CROSS-ASSET") + "\nContagion check — " + "; ".join(parts) + ".")

    lev_lines = []
    if F["lev"]:
        worst = max(F["lev"], key=lambda x: abs(x["pct"]))
        lev_lines.append(f"Leveraged ETFs are amplifying the move: {worst['name']} ({worst['sym']}) "
                         f"{worst['pct']:+.1f}%. 3x funds magnify every swing — and the crowd is in them.")
    if F["options"]:
        o = F["options"]
        flow = ("put-heavy (hedging/bearish)" if (o["put_chg"] or 0) > (o["call_chg"] or 0)
                else "call-heavy (bullish/chasing)")
        pcr = f", market PCR {o['pcr']:.2f}" if o.get("pcr") else ""
        ph = ", ".join(o["put_heavy"]) if o.get("put_heavy") else "—"
        lev_lines.append(f"Options flow is {flow}: net ΔOI calls {o['call_chg']:+,.0f} / puts {o['put_chg']:+,.0f}{pcr}. "
                         f"Heaviest put builds: {ph}.")
    if lev_lines:
        L.append("\n🎰 " + b("LEVERAGE & POSITIONING") + "\n" + " ".join(lev_lines))

    if F["breadth"]:
        br = F["breadth"]
        tilt = "risk-off" if br["dn"] > br["up"] else "risk-on" if br["up"] > br["dn"] else "mixed"
        ups = ", ".join(f"{m['t']} {m['pct']:+.1f}%" for m in F["movers_up"][:3])
        dns = ", ".join(f"{m['t']} {m['pct']:+.1f}%" for m in F["movers_dn"][:3])
        L.append("\n📊 " + b("BREADTH & MOVERS") + "\n"
                 f"{br['up']}/{br['n']} names green, {br['dn']} red — a {tilt} tape. "
                 f"Leaders: {ups}. Laggards: {dns}.")

    if F["book"] and F["book"]["tickers"]:
        bl = []
        helped_n = 0
        for t in F["book"]["tickers"]:
            helped = (t["pct"] > 0) == (t["dir"] >= 0)
            helped_n += helped
            bl.append(f"{t['tk']} {t['pct']:+.1f}% ({'tailwind' if helped else 'headwind'})")
        L.append("\n💼 " + b("YOUR BOOK") + "\n"
                 f"Your open names today: {', '.join(bl)}. Net directional exposure is being "
                 + ("helped" if helped_n >= len(F["book"]["tickers"]) / 2 else "pressured") + " by the move.")

    closers = ["Volatility always comes with change.",
               "Record leverage + rising uncertainty = swings are here to stay.",
               "When everyone is positioned one way, the exit gets narrow.",
               "Opportunity lives in the broadening swings — if you respect the risk."]
    L.append("\n— " + _wrap_pick(seed + 2, closers))
    return "\n".join(L)


def _wrap_chart_png(F):
    """Intraday line of the lead index with open / peak / trough markers."""
    lead = F.get("lead")
    if not lead:
        return None
    h = _wrap_hist(lead["sym"], "1d", "5m")
    if h is None or len(h) < 3:
        return None
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    sh = F.get("shape") or {}
    fig, ax = plt.subplots(figsize=(9, 4.4))
    ax.plot(h.index, h["Close"], color="#3d8bff", lw=1.8)
    ax.axhline(lead["prev"], color="gray", ls="--", lw=0.9, label=f"Prev close {lead['prev']:,.0f}")
    if sh.get("t_peak") is not None:
        ax.scatter([sh["t_peak"]], [sh["peak"]], color="#2e7d32", zorder=5, label=f"Peak {sh['peak']:,.0f}")
    if sh.get("t_trough") is not None:
        ax.scatter([sh["t_trough"]], [sh["trough"]], color="#c62828", zorder=5, label=f"Trough {sh['trough']:,.0f}")
    ax.fill_between(h.index, h["Close"], lead["prev"],
                    where=(h["Close"] >= lead["prev"]), alpha=0.15, color="green")
    ax.fill_between(h.index, h["Close"], lead["prev"],
                    where=(h["Close"] < lead["prev"]), alpha=0.15, color="red")
    ax.set_title(f"{lead['name']} — today ({lead['pct']:+.1f}%)", fontsize=11)
    ax.set_ylabel("Index level"); ax.grid(True, alpha=0.3); ax.legend(fontsize=8)
    import matplotlib.dates as mdates
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
    plt.tight_layout()
    buf = BytesIO(); fig.savefig(buf, format="png", dpi=110, bbox_inches="tight")
    plt.close(fig); buf.seek(0)
    return buf


def _kb_wrap():
    return InlineKeyboardMarkup([[InlineKeyboardButton("🔄 Refresh", callback_data="wrap_view"),
                                  InlineKeyboardButton("⬅️ Menu", callback_data="menu_main")]])


async def wrap_command(update, ctx):
    """/wrap - 'what just happened' market write-up from your own data."""
    conn = get_conn()
    try:
        F = wrap_facts(conn)
    finally:
        conn.close()
    txt = wrap_narrative(F, html=True)
    try:
        buf = _wrap_chart_png(F)
    except Exception:
        buf = None
    if buf:
        await update.message.reply_photo(buf, caption=txt[:1024], parse_mode=H)
        if len(txt) > 1024:
            await update.message.reply_text(txt[1024:], parse_mode=H, reply_markup=_kb_wrap())
    else:
        await update.message.reply_text(txt[:4096], parse_mode=H, reply_markup=_kb_wrap())


async def wrap_view(query):
    conn = get_conn()
    try:
        F = wrap_facts(conn)
    finally:
        conn.close()
    txt = wrap_narrative(F, html=True)
    try:
        buf = _wrap_chart_png(F)
    except Exception:
        buf = None
    if buf:
        await query.message.reply_photo(buf, caption=txt[:1024], parse_mode=H)
        if len(txt) > 1024:
            await query.message.reply_text(txt[1024:], parse_mode=H, reply_markup=_kb_wrap())
    else:
        await query.message.reply_text(txt[:4096], parse_mode=H, reply_markup=_kb_wrap())


# ═══════════════════════════════════════════════════════════════════
# ── TRADINGVIEW BRIDGE (Chrome DevTools Protocol) — inlined, no separate module
#    Unofficial & fragile: automates the TradingView *web* UI over CDP. Keep the
#    debug port on localhost. Drawings & external Pine backtests aren't feasible
#    on web TV (private canvas) — we capture the chart + overlay our levels.
# ═══════════════════════════════════════════════════════════════════
import os as _tvb_os, json as _tvb_json, subprocess as _tvb_sub, base64 as _tvb_b64
import time as _tvb_time, urllib.request as _tvb_url

_TVB_CHROME_CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
]
_TVB_PORT = 9222
_TVB_CHART_URL = "https://www.tradingview.com/chart/"


def _tvb_user_data():
    return _tvb_os.path.join(_tvb_os.environ.get("LOCALAPPDATA", _tvb_os.getcwd()), "tv_bridge_profile")


def _tvb_chrome_path():
    for p in _TVB_CHROME_CANDIDATES:
        if _tvb_os.path.exists(p):
            return p
    raise FileNotFoundError("Chrome/Edge not found in the standard locations.")


def launch_chrome(url=_TVB_CHART_URL, port=_TVB_PORT, headless=False):
    """Start a dedicated Chrome with the CDP debug port open, in the background.
    Default is a NON-headless window parked far off-screen: TradingView's chart
    canvas renders properly via the real compositor (headless leaves it blank),
    but you never see the window. Detached so it survives app restarts."""
    ud = _tvb_user_data()
    _tvb_os.makedirs(ud, exist_ok=True)
    args = [_tvb_chrome_path(), f"--remote-debugging-port={port}", f"--user-data-dir={ud}",
            "--remote-allow-origins=*", "--no-first-run", "--no-default-browser-check",
            "--window-size=1680,950", "--window-position=-2600,-2600",
            "--force-device-scale-factor=1",
            "--disable-backgrounding-occluded-windows", "--disable-renderer-backgrounding",
            "--disable-background-timer-throttling", "--disable-features=CalculateNativeWinOcclusion"]
    if headless:
        args += ["--headless=new", "--disable-gpu"]
    args.append(url)
    flags = getattr(_tvb_sub, "DETACHED_PROCESS", 0)
    try:
        return _tvb_sub.Popen(args, creationflags=flags) if flags else _tvb_sub.Popen(args)
    except Exception:
        return _tvb_sub.Popen(args)


def _tvb_targets(port=_TVB_PORT):
    with _tvb_url.urlopen(f"http://127.0.0.1:{port}/json", timeout=5) as r:
        return _tvb_json.loads(r.read().decode())


class TV:
    """Thin CDP client attached to one Chrome page (the TradingView tab)."""

    def __init__(self, port=_TVB_PORT, match="tradingview.com"):
        self.port = port; self.match = match; self.ws = None; self._id = 0; self.target = None

    def connect(self, timeout=20):
        import websocket  # lazy: a missing dep only disables TV, not the whole app
        deadline = _tvb_time.time() + timeout; last = None
        while _tvb_time.time() < deadline:
            try:
                pages = [t for t in _tvb_targets(self.port) if t.get("type") == "page"]
                tv = [t for t in pages if self.match in (t.get("url") or "")]
                self.target = (tv or pages or [None])[0]
                if self.target and self.target.get("webSocketDebuggerUrl"):
                    self.ws = websocket.create_connection(self.target["webSocketDebuggerUrl"],
                                                          max_size=None, suppress_origin=True, timeout=15)
                    for dom in ("Page", "Runtime", "DOM"):
                        try:
                            self._cmd(f"{dom}.enable")
                        except Exception:
                            pass
                    return True
            except Exception as e:
                last = e
            _tvb_time.sleep(0.5)
        raise RuntimeError(f"Could not attach to Chrome on :{self.port} ({last}).")

    def close(self):
        try:
            if self.ws:
                self.ws.close()
        finally:
            self.ws = None

    def _cmd(self, method, **params):
        self._id += 1; mid = self._id
        self.ws.send(_tvb_json.dumps({"id": mid, "method": method, "params": params}))
        while True:
            msg = _tvb_json.loads(self.ws.recv())
            if msg.get("id") == mid:
                if "error" in msg:
                    raise RuntimeError(msg["error"])
                return msg.get("result", {})

    def evaluate(self, expression, await_promise=False):
        r = self._cmd("Runtime.evaluate", expression=expression, returnByValue=True, awaitPromise=await_promise)
        if r.get("exceptionDetails"):
            raise RuntimeError(r["exceptionDetails"].get("text", "JS error"))
        return r.get("result", {}).get("value")

    def screenshot_bytes(self):
        r = self._cmd("Page.captureScreenshot", format="png")
        return _tvb_b64.b64decode(r["data"])

    def navigate(self, url, wait=2.0):
        self._cmd("Page.navigate", url=url); _tvb_time.sleep(wait)

    def _key(self, etype, key=None, code=None, vk=None, text=None):
        p = {"type": etype}
        if key:
            p["key"] = key
        if code:
            p["code"] = code
        if vk is not None:
            p["windowsVirtualKeyCode"] = vk
        if text is not None:
            p["text"] = text
        self._cmd("Input.dispatchKeyEvent", **p)

    def key_combo(self, modifiers, key, code, vk):
        for et in ("keyDown", "keyUp"):
            self._cmd("Input.dispatchKeyEvent", type=et, modifiers=modifiers,
                      key=key, code=code, windowsVirtualKeyCode=vk)

    def type_text(self, text, delay=0.03):
        for ch in str(text):
            self._key("keyDown", text=ch); self._key("keyUp", text=ch); _tvb_time.sleep(delay)

    def press_enter(self):
        self._key("keyDown", key="Enter", code="Enter", vk=13)
        self._key("keyUp", key="Enter", code="Enter", vk=13)

    def get_symbol(self):
        try:
            return (self.evaluate("document.title") or "").split(" ")[0]
        except Exception:
            return None

    def set_timeframe(self, tf):
        self.type_text(str(tf)); _tvb_time.sleep(0.4); self.press_enter(); _tvb_time.sleep(0.6)

    def open_chart(self, symbol, tf=None):
        url = f"https://www.tradingview.com/chart/?symbol={symbol}"
        if tf:
            url += f"&interval={tf}"          # set interval via URL — avoids the typed-interval popup
        self.navigate(url, wait=3.0)

    def screenshot_symbol(self, symbol, tf=None, settle=4.5):
        self.open_chart(symbol, tf)
        try:
            self._key("keyDown", key="Escape", code="Escape", vk=27)    # dismiss any stray popup
            self._key("keyUp", key="Escape", code="Escape", vk=27)
            self.evaluate("window.dispatchEvent(new Event('resize'))")  # nudge TV to repaint
        except Exception:
            pass
        _tvb_time.sleep(settle)
        return self.screenshot_bytes()

    def click_text(self, text, tags="button,[role=button],a,span,div"):
        js = ("(function(t,sel){var w=t.trim().toLowerCase();"
              "var els=Array.prototype.slice.call(document.querySelectorAll(sel));"
              "var el=els.find(function(e){return (e.innerText||'').trim().toLowerCase()===w "
              "||(e.getAttribute&&((e.getAttribute('aria-label')||'').trim().toLowerCase()===w));});"
              "if(el){el.click();return true;}return false;})(%s,%s)"
              % (_tvb_json.dumps(text), _tvb_json.dumps(tags)))
        try:
            return bool(self.evaluate(js))
        except Exception:
            return False

    # best-effort deep actions (WILL break on TV UI updates)
    def replay_mode(self):
        return self.click_text("Replay")

    def open_alert_dialog(self):
        try:
            self.key_combo(1, "a", "KeyA", 65); _tvb_time.sleep(0.6); return True
        except Exception:
            return False

    def open_pine_editor(self):
        return self.click_text("Pine Editor") or self.click_text("Pine")

    def write_pine(self, code):
        self.key_combo(2, "a", "KeyA", 65); _tvb_time.sleep(0.2)
        self.type_text(code, delay=0.005); return True

    def run_pine(self):
        return self.click_text("Add to chart") or self.click_text("Update on chart")

    def health(self):
        out = {"chrome_up": False, "attached": bool(self.ws), "port": self.port}
        try:
            ts = _tvb_targets(self.port)
            pages = [x for x in ts if x.get("type") == "page"]
            tv = [x for x in pages if self.match in (x.get("url") or "")]
            out.update(chrome_up=True, pages=len(pages), tv_pages=len(tv), url=(self.target or {}).get("url"))
            if self.ws:
                try:
                    out["js_ok"] = (self.evaluate("1+1") == 2)
                    out["title"] = self.evaluate("document.title")
                except Exception as e:
                    out["js_ok"] = False; out["js_error"] = str(e)
        except Exception as e:
            out["error"] = str(e)
        return out


_TV_SINGLETON = {"tv": None}


def _tv_ensure(timeout=25):
    """Ensure the bridge Chrome is up (headless, background) and return a live TV.
    Fully automatic: no terminal command and no login needed for chart screenshots
    (TradingView charts render for anonymous users). Reuses one persistent connection."""
    tvobj = _TV_SINGLETON.get("tv")
    if tvobj is not None and tvobj.ws is not None:
        try:
            if tvobj.evaluate("1+1") == 2:
                return tvobj
        except Exception:
            try:
                tvobj.close()
            except Exception:
                pass
            _TV_SINGLETON["tv"] = None
    try:
        _tvb_targets()                       # already running?
    except Exception:
        try:
            launch_chrome()                  # spin it up off-screen in the background
        except Exception:
            pass
    tv = TV()
    tv.connect(timeout=timeout)
    _TV_SINGLETON["tv"] = tv
    return tv


def _tv_capture(symbol, tf=None, settle=2.5):
    """Capture a TradingView chart via the inlined CDP bridge. Returns (png, err).
    The bridge Chrome is started automatically in the background on first use."""
    try:
        tv = _tv_ensure()
    except Exception as e:
        return None, f"📺 Couldn't start the TradingView bridge automatically: {e}"
    try:
        return (tv.screenshot_symbol(symbol, tf, settle=settle) if symbol else tv.screenshot_bytes()), None
    except Exception as e:
        _TV_SINGLETON["tv"] = None           # drop a stale connection so next call reconnects
        try:
            tv.close()
        except Exception:
            pass
        return None, f"📺 TV capture error: {e}"


def _tv_levels_line(symbol):
    """Our computed levels for the symbol (spot / gamma walls) as a caption line."""
    sym = str(symbol).split(":")[-1].upper()
    conn = get_conn()
    spot = cw = pw = None
    try:
        try:
            spot = _gex_spot(conn, sym) or _last_price(sym)
        except Exception:
            pass
        if spot:
            try:
                g = _compute_gex(sym, conn, spot); cw, pw = g.get("call_wall"), g.get("put_wall")
            except Exception:
                pass
    finally:
        conn.close()
    bits = [f"📺 <b>{sym}</b>"]
    if spot:
        bits.append(f"spot ${spot:,.2f}")
    if cw:
        bits.append(f"call wall ${cw:g}")
    if pw:
        bits.append(f"put wall ${pw:g}")
    return " · ".join(bits)


def _kb_tv(symbol):
    return InlineKeyboardMarkup([[InlineKeyboardButton("🔄 Refresh", callback_data=f"tvc_{symbol}"),
                                  InlineKeyboardButton("⬅️ Menu", callback_data="menu_main")]])


def _tv_default_symbol():
    conn = get_conn()
    try:
        r = conn.execute("SELECT UPPER(ticker) FROM trades WHERE status='OPEN' ORDER BY 1 LIMIT 1").fetchone()
    finally:
        conn.close()
    return r[0] if r else "SPY"


async def tv_command(update, ctx):
    """/tv SYMBOL [TF] — capture a TradingView chart (needs the local CDP bridge)."""
    args = list(getattr(ctx, "args", []) or [])
    symbol = args[0].upper() if args else _tv_default_symbol()
    tf = args[1] if len(args) > 1 else None
    await update.message.reply_text(f"📺 Capturing <b>{symbol}</b> from TradingView…", parse_mode=H)
    png, err = _tv_capture(symbol, tf)
    if err:
        await update.message.reply_text(err, parse_mode=H)
        return
    cap = _tv_levels_line(symbol) + (f" · {tf}" if tf else "")
    await update.message.reply_photo(BytesIO(png), caption=cap, parse_mode=H, reply_markup=_kb_tv(symbol))


async def tv_view(query, symbol=None):
    symbol = (symbol or _tv_default_symbol()).upper()
    await query.message.reply_text(f"📺 Capturing <b>{symbol}</b> from TradingView…", parse_mode=H)
    png, err = _tv_capture(symbol)
    if err:
        await query.message.reply_text(err, parse_mode=H)
        return
    await query.message.reply_photo(BytesIO(png), caption=_tv_levels_line(symbol),
                                    parse_mode=H, reply_markup=_kb_tv(symbol))


async def plan_command(update, ctx):
    """/plan - condensed next-day game plan for your open positions."""
    conn = get_conn()
    try:
        txt = _next_day_plan(conn)
        kb = _kb_plan(conn)
    finally:
        conn.close()
    await update.message.reply_text(txt, parse_mode=H, reply_markup=kb)

async def plan_view(query):
    conn = get_conn()
    try:
        txt = _next_day_plan(conn)
        kb = _kb_plan(conn)
    finally:
        conn.close()
    await query.message.reply_text(txt, parse_mode=H, reply_markup=kb)


def _plan_legs_for(conn, tk):
    """Rebuild structured legs (+ spot, walls) for one ticker's open positions."""
    R = 0.045
    try:
        tr = pd.read_sql("SELECT ticker,option_type,strike,quantity,expiry,entry_price,entry_iv "
                         "FROM trades WHERE status='OPEN' AND UPPER(ticker)=?", conn, params=(tk.upper(),))
    except Exception:
        return [], None, None, None
    if tr is None or tr.empty:
        return [], None, None, None
    spot = _gex_spot(conn, tk) or _last_price(tk)
    if not spot:
        return [], None, None, None
    cw = pw = None
    try:
        g = _compute_gex(tk, conn, spot); cw, pw = g.get("call_wall"), g.get("put_wall")
    except Exception:
        pass
    legs = []
    for _, t in tr.iterrows():
        typ = "call" if str(t["option_type"]).lower().startswith("c") else "put"
        K = float(t["strike"] or 0); qty = int(t["quantity"] or 0); exp = str(t["expiry"])
        dte = None
        for fmt in ("%Y-%m-%d", "%m-%d-%Y"):
            try:
                dte = (datetime.strptime(exp, fmt) - datetime.now()).days; break
            except Exception:
                pass
        if dte is None or K <= 0 or qty == 0:
            continue
        T = max(dte, 0) / 365.0; entry = float(t["entry_price"] or 0)
        prem = _plan_prem(conn, tk, K, exp, typ)
        iv = _implied_vol_hp(prem, spot, K, T, R) if (prem and T > 0) else (float(t["entry_iv"] or 0) or 0.30)
        gg = bs_greeks(spot, K, T, R, iv, typ) if T > 0 else {"price": (prem or entry)}
        cur = prem if prem else gg.get("price", entry); m = qty * 100
        legs.append({"K": K, "typ": typ, "entry": entry, "m": m, "iv": iv, "dte": dte, "cur": cur,
                     "side": "short" if qty < 0 else "long", "qty": qty, "exp": exp,
                     "ticker": tk, "spot": spot, "pnl": (cur - entry) * m})
    return legs, spot, cw, pw


def _plan_price_hist(tk, n=40):
    """Last n daily closes for a ticker from stock_daily (oldest→newest)."""
    sk = "substr(trade_date,7,4)||substr(trade_date,1,2)||substr(trade_date,4,2)"
    conn = get_conn()
    try:
        d = pd.read_sql(f"SELECT close FROM stock_daily WHERE ticker=? ORDER BY {sk} DESC LIMIT ?",
                        conn, params=(tk.upper(), int(n)))
    except Exception:
        return []
    finally:
        conn.close()
    return list(pd.to_numeric(d["close"], errors="coerce").dropna())[::-1]


def _plan_payoff_png(tk, legs, spot, cw, pw):
    """2-panel PNG: top = recent price sparkline with spot/walls/breakevens; bottom = payoff
    at expiry (profit/loss zones, spot, breakevens, walls)."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    ks = [l["K"] for l in legs]
    lo = max(min(min(ks), spot) * 0.7, 0.01); hi = max(max(ks), spot) * 1.3
    prices = np.linspace(lo, hi, 300)
    payoff = np.zeros_like(prices)
    for l in legs:
        intr = np.maximum(prices - l["K"], 0) if l["typ"] == "call" else np.maximum(l["K"] - prices, 0)
        payoff += (intr - l["entry"]) * l["m"]
    a = _pl_analytics(legs, spot)
    hist = _plan_price_hist(tk, 40)
    fig, (ax0, ax) = plt.subplots(2, 1, figsize=(9, 6.2), height_ratios=[1, 2])
    # ── price + levels sparkline ──
    if hist:
        ax0.plot(range(len(hist)), hist, color="#9db8ff", lw=1.7)
        ax0.axhline(spot, color="orange", ls=":", lw=1.1)
        if cw:
            ax0.axhline(cw, color="#c62828", ls="-.", lw=0.8, alpha=0.7)
        if pw:
            ax0.axhline(pw, color="#2e7d32", ls="-.", lw=0.8, alpha=0.7)
        for be in (a["be"] if a else []):
            ax0.axhline(be, color="purple", ls="--", lw=0.7, alpha=0.6)
        ax0.set_title(f"{tk} ${spot:.2f} · last {len(hist)}d price + levels", fontsize=9)
        ax0.set_xticks([]); ax0.grid(True, alpha=0.2)
    else:
        ax0.text(0.5, 0.5, "no price history", ha="center", va="center"); ax0.axis("off")
    # ── payoff ──
    ax.plot(prices, payoff, color="#3d8bff", lw=2.2)
    ax.axhline(0, color="gray", ls="--", lw=0.8)
    ax.axvline(spot, color="orange", ls=":", lw=1.3, label=f"Spot ${spot:.0f}")
    ax.fill_between(prices, payoff, 0, where=(payoff >= 0), alpha=0.25, color="green")
    ax.fill_between(prices, payoff, 0, where=(payoff < 0), alpha=0.25, color="red")
    for be in (a["be"] if a else []):
        ax.axvline(be, color="purple", ls="--", lw=1)
        ax.text(be, ax.get_ylim()[0] * 0.92, f"BE ${be:.0f}", color="purple", fontsize=7, ha="center")
    if cw:
        ax.axvline(cw, color="#c62828", ls="-.", lw=0.9, alpha=0.6, label=f"Call wall ${cw:.0f}")
    if pw:
        ax.axvline(pw, color="#2e7d32", ls="-.", lw=0.9, alpha=0.6, label=f"Put wall ${pw:.0f}")
    sub = f"Max P ${payoff.max():+,.0f} · Max L ${payoff.min():+,.0f}"
    if a:
        sub += f" · POP {a['pop']:.0f}% · EV ${a['ev']:,.0f}"
    ax.set_title(f"{tk} payoff at expiry\n{sub}", fontsize=10)
    ax.set_xlabel("Price at expiry"); ax.set_ylabel("P&L ($)")
    ax.grid(True, alpha=0.3); ax.legend(fontsize=8)
    plt.tight_layout()
    buf = BytesIO(); fig.savefig(buf, format="png", dpi=110, bbox_inches="tight")
    plt.close(fig); buf.seek(0)
    return buf


async def plan_chart_view(query, tk):
    """Send a per-ticker payoff chart + breakeven/POP/EV + order tickets."""
    conn = get_conn()
    try:
        legs, spot, cw, pw = _plan_legs_for(conn, tk)
    finally:
        conn.close()
    if not legs or not spot:
        await query.message.reply_text(f"No open {tk} position.", parse_mode=H)
        return
    try:
        buf = _plan_payoff_png(tk, legs, spot, cw, pw)
    except Exception as e:
        await query.message.reply_text(f"Chart error for {tk}: {e}")
        return
    b = _pl_bounds(legs, spot); a = _pl_analytics(legs, spot)
    cap = [f"📈 <b>{tk}</b> ${spot:.2f} · payoff at expiry"]
    if b:
        cap.append(f"💰 Max P {_pl_mp(b)} · Max L {_pl_ml(b)}")
    if a:
        be = ", ".join(f"${x:.0f}" for x in a["be"]) or "—"
        cap.append(f"📊 POP {a['pop']:.0f}% · EV ${a['ev']:,.0f} · B/E {be}")
    tix = _pl_tickets(legs, spot, cw, pw)
    if tix:
        cap.append("\n🧾 <b>Order ideas:</b>")
        for t in tix[:5]:
            cap.append("• " + t)
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back to plan", callback_data="plan_view")]])
    await query.message.reply_photo(buf, caption="\n".join(cap)[:1000], parse_mode=H, reply_markup=kb)


def _beta_to_spy_bot(conn, tk, lookback=60):
    """Beta of a ticker vs SPY from stock_daily daily returns (or 1.0 fallback)."""
    sk = "substr(trade_date,7,4)||substr(trade_date,1,2)||substr(trade_date,4,2)"
    try:
        a = pd.read_sql(f"SELECT trade_date,close FROM stock_daily WHERE ticker=? ORDER BY {sk}",
                        conn, params=(tk.upper(),))
        s = pd.read_sql(f"SELECT trade_date,close FROM stock_daily WHERE ticker='SPY' ORDER BY {sk}", conn)
    except Exception:
        return None
    if len(a) < 20 or len(s) < 20:
        return None
    mg = a.merge(s, on="trade_date", suffixes=("", "_spy")).tail(lookback + 1)
    if len(mg) < 20:
        return None
    ra = pd.to_numeric(mg["close"], errors="coerce").pct_change().dropna().values
    rs = pd.to_numeric(mg["close_spy"], errors="coerce").pct_change().dropna().values
    n = min(len(ra), len(rs))
    if n < 15 or np.var(rs[-n:]) <= 0:
        return None
    return float(np.cov(ra[-n:], rs[-n:])[0, 1] / np.var(rs[-n:]))


_RED_CACHE = {}
_RED_TOKEN = {"tok": None, "exp": 0.0}


def _reddit_sentiment(tk):
    """r/wallstreetbets crowd tone via Reddit OAuth (needs env REDDIT_CLIENT_ID / _SECRET).
    Returns {label,bull,bear,n} or None (dormant until creds are set)."""
    import os
    cid = os.environ.get("REDDIT_CLIENT_ID"); csec = os.environ.get("REDDIT_CLIENT_SECRET")
    if not cid or not csec:
        return None
    import time as _t, json, base64, urllib.request, urllib.parse
    key = tk.upper(); now = _t.time()
    if key in _RED_CACHE and _RED_CACHE[key][0] > now:
        return _RED_CACHE[key][1]
    try:
        if not _RED_TOKEN["tok"] or _RED_TOKEN["exp"] < now:
            auth = base64.b64encode(f"{cid}:{csec}".encode()).decode()
            data = urllib.parse.urlencode({"grant_type": "client_credentials"}).encode()
            req = urllib.request.Request("https://www.reddit.com/api/v1/access_token", data=data,
                                         headers={"Authorization": f"Basic {auth}",
                                                  "User-Agent": "nyse-data-sentiment/1.0"})
            j = json.load(urllib.request.urlopen(req, timeout=10))
            _RED_TOKEN["tok"] = j["access_token"]; _RED_TOKEN["exp"] = now + j.get("expires_in", 3600) - 60
        url = (f"https://oauth.reddit.com/r/wallstreetbets/search?q={urllib.parse.quote(key)}"
               "&restrict_sr=1&sort=new&limit=25&t=week")
        req = urllib.request.Request(url, headers={"Authorization": f"bearer {_RED_TOKEN['tok']}",
                                                   "User-Agent": "nyse-data-sentiment/1.0"})
        posts = json.load(urllib.request.urlopen(req, timeout=10)).get("data", {}).get("children", [])
    except Exception:
        return None
    if not posts:
        return None
    POS = ("call", "calls", "moon", "buy", "long", "squeeze", "rip", "bull", "green", "up")
    NEG = ("put", "puts", "short", "sell", "crash", "dump", "bear", "red", "down", "drill")
    bull = bear = 0
    for p in posts:
        t = (p.get("data", {}).get("title") or "").lower()
        b = sum(w in t for w in POS); s = sum(w in t for w in NEG)
        if b > s:
            bull += 1
        elif s > b:
            bear += 1
    if bull + bear == 0:
        return None
    lbl = "BULLISH" if bull > bear * 1.2 else "BEARISH" if bear > bull * 1.2 else "MIXED"
    res = {"label": lbl, "bull": bull, "bear": bear, "n": len(posts)}
    _RED_CACHE[key] = (now + 600, res)
    return res


def _plan_portfolio_png(conn):
    """Beta-weighted whole-book P&L vs a SPY move (next session). Returns (buf, stats)."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    R = 0.045
    tks = [r[0] for r in conn.execute(
        "SELECT DISTINCT UPPER(ticker) FROM trades WHERE status='OPEN'").fetchall()]
    all_legs = []
    for tk in tks:
        legs, spot, cw, pw = _plan_legs_for(conn, tk)
        if not legs or not spot:
            continue
        beta = _beta_to_spy_bot(conn, tk) or 1.0
        for l in legs:
            all_legs.append(dict(l, beta=beta))
    if not all_legs:
        return None, None
    moves = np.linspace(-0.10, 0.10, 41)
    pnl = np.zeros_like(moves)
    for i, s in enumerate(moves):
        tot = 0.0
        for l in all_legs:
            us = max(l["spot"] * (1 + l["beta"] * s), 0.01)
            rem = max(l["dte"] - 1, 0) / 365.0
            val = (bs_greeks(us, l["K"], rem, R, l["iv"], l["typ"])["price"] if rem > 0
                   else (max(us - l["K"], 0) if l["typ"] == "call" else max(l["K"] - us, 0)))
            tot += (val - l["entry"]) * l["m"]
        pnl[i] = tot
    mid = len(moves) // 2
    per1 = (pnl[mid + 5] - pnl[mid - 5]) / (moves[mid + 5] - moves[mid - 5]) / 100.0  # $ per +1% SPY
    d2 = float(np.interp(-0.02, moves, pnl)); u2 = float(np.interp(0.02, moves, pnl))
    fig, ax = plt.subplots(figsize=(9, 4.4))
    ax.plot(moves * 100, pnl, color="#3d8bff", lw=2.2)
    ax.axhline(0, color="gray", ls="--", lw=0.8)
    ax.axvline(0, color="orange", ls=":", lw=1.2, label="flat")
    ax.fill_between(moves * 100, pnl, 0, where=(pnl >= 0), alpha=0.25, color="green")
    ax.fill_between(moves * 100, pnl, 0, where=(pnl < 0), alpha=0.25, color="red")
    ax.set_title(f"Portfolio P&L vs SPY move (next session, beta-weighted)\n"
                 f"≈${per1:,.0f}/+1% SPY · −2%→${d2:,.0f} · +2%→${u2:,.0f}", fontsize=10)
    ax.set_xlabel("SPY move %"); ax.set_ylabel("Portfolio P&L ($)")
    ax.grid(True, alpha=0.3); ax.legend(fontsize=8)
    plt.tight_layout()
    buf = BytesIO(); fig.savefig(buf, format="png", dpi=110, bbox_inches="tight")
    plt.close(fig); buf.seek(0)
    return buf, {"per1": per1, "d2": d2, "u2": u2, "n": len(all_legs), "tks": len(tks)}


async def plan_port_chart_view(query):
    """Send the beta-weighted portfolio exposure chart."""
    conn = get_conn()
    try:
        buf, stats = _plan_portfolio_png(conn)
    finally:
        conn.close()
    if not buf:
        await query.message.reply_text("No open positions to chart.", parse_mode=H)
        return
    cap = ["📊 <b>Portfolio</b> — P&L vs SPY move (beta-weighted, next session)",
           f"≈ <b>${stats['per1']:,.0f}</b> per +1% SPY · −2% → ${stats['d2']:,.0f} · +2% → ${stats['u2']:,.0f}",
           f"<i>{stats['n']} legs · {stats['tks']} tickers</i>"]
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back to plan", callback_data="plan_view")]])
    await query.message.reply_photo(buf, caption="\n".join(cap), parse_mode=H, reply_markup=kb)


async def plan_alert(ctx):
    """Pre-market auto-send of the next-day game plan."""
    try:
        _, chat_id = load_creds()
        conn = get_conn()
        try:
            txt = _next_day_plan(conn)
        finally:
            conn.close()
        await ctx.bot.send_message(chat_id=int(chat_id), text=txt, parse_mode=H)
    except Exception as e:
        log.warning(f"plan_alert failed: {e}")


async def wrap_alert(ctx):
    """Daily post-close auto-send of the 'what just happened' market wrap."""
    try:
        _, chat_id = load_creds()
        conn = get_conn()
        try:
            F = wrap_facts(conn)
            txt = wrap_narrative(F, html=True)
        finally:
            conn.close()
        try:
            buf = _wrap_chart_png(F)
        except Exception:
            buf = None
        await ctx.bot.send_message(chat_id=int(chat_id), text="📰 <b>Daily Market Wrap</b>", parse_mode=H)
        if buf:
            await ctx.bot.send_photo(chat_id=int(chat_id), photo=buf, caption=txt[:1024], parse_mode=H)
            if len(txt) > 1024:
                await ctx.bot.send_message(chat_id=int(chat_id), text=txt[1024:], parse_mode=H)
        else:
            await ctx.bot.send_message(chat_id=int(chat_id), text=txt[:4096], parse_mode=H)
    except Exception as e:
        log.warning(f"wrap_alert failed: {e}")


async def briefing_command(update, ctx):
    """/briefing - daily macro event brief with optimistic/pessimistic/balanced views."""
    try:
        await asyncio.get_event_loop().run_in_executor(None, compute_universe_momentum, False)
    except Exception:
        pass
    conn = get_conn()
    try:
        b = morning_briefing(conn)
    finally:
        conn.close()
    await update.message.reply_text(_fmt_briefing(b), parse_mode=H, reply_markup=_kb_brief())


# ═══════════════════════════════════════════════════════════════════
# ── EVENT NEWS TAGGING  +  AUTO MORNING BRIEF  +  EVENT JOURNAL
# ═══════════════════════════════════════════════════════════════════
# Keywords that map a live headline to a macro event in MACRO_EVENT_MAP.
_EVENT_NEWS_KW = {
    "iran":           ["iran", "tehran", "sanction", "nuclear deal", "jcpoa"],
    "war":            ["war", "missile", "airstrike", "attack", "conflict", "escalat",
                       "invasion", "gaza", "israel", "ukraine", "strike on"],
    "oil_spike":      ["oil", "crude", "opec", "hormuz", "brent", "wti", "barrel"],
    "fed_cut":        ["rate cut", "dovish", "fed cut", "cut rates", "easing", "pivot"],
    "fed_hike":       ["rate hike", "hawkish", "hike rates", "raise rates", "tightening"],
    "china_stimulus": ["china", "beijing", "stimulus", "pboc", "yuan", "reopening"],
    "usd_up":         ["dollar", "dxy", "greenback"],
}

# Primary tracking instrument + expected direction per event (for the journal).
_EVENT_TRACK = {
    "iran":           ("USO", "SHORT"),
    "oil_spike":      ("XLE", "LONG"),
    "fed_cut":        ("TLT", "LONG"),
    "fed_hike":       ("TLT", "SHORT"),
    "war":            ("GLD", "LONG"),
    "china_stimulus": ("FXI", "LONG"),
    "usd_up":         ("UUP", "LONG"),
}

def _last_price(ticker):
    try:
        h = yf.Ticker(ticker).history(period="5d")
        if len(h) >= 1 and float(h["Close"].iloc[-1]) > 0:
            return float(h["Close"].iloc[-1])
    except Exception:
        pass
    return _stooq_price(ticker)

def _fetch_macro_headlines(limit=40):
    """Pull recent macro headlines from Yahoo RSS (no API key). Returns list of
    (title, link, when)."""
    try:
        import feedparser, html as _h, time as _t
    except Exception:
        return []
    feeds = ["CL=F", "^VIX", "SPY", "GLD", "^TNX", "DX=F", "XLE", "FXI"]
    seen, out = set(), []
    for sym in feeds:
        try:
            fp = feedparser.parse(
                f"https://feeds.finance.yahoo.com/rss/2.0/headline?s={sym}&region=US&lang=en-US")
            for e in fp.entries[:6]:
                title = _h.unescape(e.get("title", "")).strip()
                if not title or len(title) < 20:
                    continue
                k = title[:55].lower()
                if k in seen:
                    continue
                seen.add(k)
                pp = e.get("published_parsed", None)
                when = (_t.strftime("%d%b %H:%M", pp).lstrip("0") if pp else "")
                out.append((title, e.get("link", "").strip(), when))
        except Exception:
            continue
        if len(out) >= limit:
            break
    return out

def _event_news_matches(headlines, keys=None):
    """Map each event key to its most recent matching headline. {key: (title, link, when)}."""
    keys = keys or list(_EVENT_NEWS_KW)
    res = {}
    for title, link, when in headlines:
        tl = title.lower()
        for k in keys:
            if k in res:
                continue
            if any(kw in tl for kw in _EVENT_NEWS_KW.get(k, [])):
                res[k] = (title, link, when)
    return res

def _setup_event_journal(conn):
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS event_journal ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, event_key TEXT, ticker TEXT,"
            " direction TEXT, entry_price REAL, entry_date TEXT,"
            " status TEXT DEFAULT 'OPEN', exit_price REAL, note TEXT)")
        conn.commit()
    except Exception:
        pass

def event_journal_log(conn, event_key, ticker=None, direction=None, note=""):
    """Log an event-driven idea so its outcome can be tracked. Defaults to the
    event's primary tracking instrument."""
    _setup_event_journal(conn)
    ev = event_trade_map(event_key)
    if not ev:
        return None
    real_key = next((k for k in MACRO_EVENT_MAP if MACRO_EVENT_MAP[k] is ev), event_key)
    if not ticker or not direction:
        t_def = _EVENT_TRACK.get(real_key, (None, "LONG"))
        ticker = ticker or t_def[0]
        direction = (direction or t_def[1]).upper()
    if not ticker:
        return None
    px = _last_price(ticker)
    today = datetime.now().strftime("%m-%d-%Y")
    try:
        conn.execute(
            "INSERT INTO event_journal (event_key, ticker, direction, entry_price, entry_date, status, note)"
            " VALUES (?,?,?,?,?, 'OPEN', ?)",
            (real_key, ticker.upper(), direction.upper(), px, today, note))
        conn.commit()
    except Exception:
        return None
    return {"event": real_key, "ticker": ticker.upper(), "direction": direction.upper(),
            "entry": px, "date": today}

def _fmt_journal_review(conn):
    _setup_event_journal(conn)
    try:
        df = pd.read_sql("SELECT * FROM event_journal ORDER BY id DESC", conn)
    except Exception:
        return "Event journal empty. Log one with <code>/logevent iran</code>."
    if df.empty:
        return ("\U0001F4D3 <b>EVENT JOURNAL</b>\nNo entries yet.\n"
                "Log an event idea: <code>/logevent iran</code>")
    lines = ["\U0001F4D3 <b>EVENT JOURNAL REVIEW</b>", ""]
    wins = total = 0
    for _, r in df.iterrows():
        tkr = str(r["ticker"]); direction = str(r["direction"])
        entry = float(r["entry_price"] or 0)
        cur = _last_price(tkr)
        move = (cur - entry) / entry * 100 if entry > 0 else 0.0
        working = (move > 0 and direction == "LONG") or (move < 0 and direction == "SHORT")
        edge = move if direction == "LONG" else -move
        total += 1
        if edge > 0:
            wins += 1
        ico = "\U0001F7E2" if working else "\U0001F534"
        lines.append(f"{ico} <b>{str(r['event_key'])}</b> {direction} {tkr}")
        lines.append(f"   entry ${entry:.2f} → ${cur:.2f}  ({move:+.1f}%, edge {edge:+.1f}%)  {str(r['entry_date'])}")
    hit = (wins / total * 100) if total else 0
    _k = _kelly_fraction(wins / total, 1.0) if total else 0.0
    lines += ["", f"<b>Track record:</b> {wins}/{total} working ({hit:.0f}%)",
              f"<b>Suggested size:</b> ~{_k*50:.0f}% of risk budget (half-Kelly)",
              "<i>Edge = move in your direction. This is YOUR realised history, not a prediction.</i>"]
    return "\n".join(lines)

async def journal_command(update, ctx):
    """/journal - review your logged event trades + running hit-rate."""
    conn = get_conn()
    try:
        msg = _fmt_journal_review(conn)
    finally:
        conn.close()
    await update.message.reply_text(msg, parse_mode=H)

async def logevent_command(update, ctx):
    """/logevent EVENT [TICKER] [LONG|SHORT] - log an event-driven trade idea."""
    args = list(getattr(ctx, "args", []) or [])
    if not args:
        await update.message.reply_text(
            "Usage: <code>/logevent iran</code> or <code>/logevent iran USO SHORT</code>\n"
            f"Events: <code>{', '.join(sorted(MACRO_EVENT_MAP))}</code>", parse_mode=H)
        return
    ek = args[0]
    tkr = args[1] if len(args) > 1 else None
    direction = args[2] if len(args) > 2 else None
    conn = get_conn()
    try:
        rec = event_journal_log(conn, ek, tkr, direction)
    finally:
        conn.close()
    if not rec:
        await update.message.reply_text(
            f"Unknown event '{ek}'. Try: <code>{', '.join(sorted(MACRO_EVENT_MAP))}</code>", parse_mode=H)
        return
    await update.message.reply_text(
        f"✅ Logged <b>{rec['event']}</b>: {rec['direction']} {rec['ticker']} @ ${rec['entry']:.2f} ({rec['date']}).\n"
        "Track it any time with /journal.", parse_mode=H)

async def briefing_alert(ctx):
    """Daily auto-send of the morning event brief (scheduled)."""
    try:
        try:
            await asyncio.get_event_loop().run_in_executor(None, compute_universe_momentum, False)
        except Exception:
            pass
        _, chat_id = load_creds()
        conn = get_conn()
        try:
            msg = _fmt_briefing(morning_briefing(conn))
        finally:
            conn.close()
        await ctx.bot.send_message(chat_id=int(chat_id), text=msg, parse_mode=H)
    except Exception as e:
        log.warning(f"briefing_alert failed: {e}")


# ═══════════════════════════════════════════════════════════════════
# ── REFRESH / MONITOR(FREEZE) / BOOKMARK  buttons + handlers
# ═══════════════════════════════════════════════════════════════════
def _kb_brief():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Refresh", callback_data="brief_refresh"),
         InlineKeyboardButton("📌 Bookmark", callback_data="bm|brief|latest")],
        [InlineKeyboardButton("🔖 Bookmarks", callback_data="show_bookmarks")]])

def _kb_opex():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Refresh", callback_data="opex_refresh"),
         InlineKeyboardButton("📌 Bookmark", callback_data="bm|opex|latest")]])

def _kb_event(key):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Refresh", callback_data=f"event_refresh|{key}"),
         InlineKeyboardButton("❄️ Monitor", callback_data=f"event_mon|{key}")],
        [InlineKeyboardButton("📌 Bookmark", callback_data=f"bm|event|{key}")]])

def _setup_bookmarks(conn):
    try:
        conn.execute("CREATE TABLE IF NOT EXISTS bookmarks (id INTEGER PRIMARY KEY AUTOINCREMENT,"
                     " kind TEXT, label TEXT, content TEXT, created TEXT)")
        conn.commit()
    except Exception:
        pass

def bookmark_save(conn, kind, label, content):
    _setup_bookmarks(conn)
    try:
        conn.execute("INSERT INTO bookmarks (kind,label,content,created) VALUES (?,?,?,?)",
                     (kind, label, content, datetime.now().strftime("%m-%d-%Y %H:%M")))
        conn.commit()
        return True
    except Exception:
        return False

def _fmt_bookmarks(conn):
    _setup_bookmarks(conn)
    try:
        df = pd.read_sql("SELECT * FROM bookmarks ORDER BY id DESC LIMIT 20", conn)
    except Exception:
        df = None
    if df is None or df.empty:
        return "🔖 No bookmarks yet. Tap 📌 Bookmark on any brief/event/opex."
    lines = ["🔖 <b>BOOKMARKS</b>", ""]
    for _, r in df.iterrows():
        lines.append(f"#{int(r['id'])} <b>{r['kind']}:{r['label']}</b>  <i>{r['created']}</i>")
    lines += ["", "Open one: <code>/bookmarks ID</code>"]
    return "\n".join(lines)

async def bookmarks_command(update, ctx):
    """/bookmarks [ID] - list saved bookmarks, or open one by id."""
    args = list(getattr(ctx, "args", []) or [])
    conn = get_conn()
    try:
        if args and str(args[0]).isdigit():
            _setup_bookmarks(conn)
            df = pd.read_sql("SELECT content FROM bookmarks WHERE id=?", conn, params=(int(args[0]),))
            msg = df["content"].iloc[0] if not df.empty else "Bookmark not found."
        else:
            msg = _fmt_bookmarks(conn)
    finally:
        conn.close()
    await update.message.reply_text(msg, parse_mode=H)

async def bookmarks_view(query):
    conn = get_conn()
    try:
        msg = _fmt_bookmarks(conn)
    finally:
        conn.close()
    await query.message.reply_text(msg, parse_mode=H)

async def briefing_view(query):
    conn = get_conn()
    try:
        msg = _fmt_briefing(morning_briefing(conn))
    finally:
        conn.close()
    await query.message.reply_text(msg, parse_mode=H, reply_markup=_kb_brief())

async def opex_view(query):
    conn = get_conn()
    try:
        msg = _fmt_opex_report(opex_radar(conn))
    finally:
        conn.close()
    await query.message.reply_text(msg, parse_mode=H, reply_markup=_kb_opex())

async def event_view(query, key):
    await query.message.reply_text(_fmt_event_report(event_trade_map(key)),
                                   parse_mode=H, reply_markup=_kb_event(key))

async def event_monitor_btn(query, key):
    conn = get_conn()
    try:
        rec = event_journal_log(conn, key)
    finally:
        conn.close()
    if rec:
        await query.message.reply_text(
            f"❄️ Monitoring <b>{rec['event']}</b>: {rec['direction']} {rec['ticker']} @ ${rec['entry']:.2f}.\n"
            "Track it any time with /journal.", parse_mode=H)
    else:
        await query.message.reply_text("Could not monitor that event.", parse_mode=H)

async def bookmark_btn(query, kind, key):
    conn = get_conn()
    try:
        if kind == "brief":
            content = _fmt_briefing(morning_briefing(conn))
        elif kind == "opex":
            content = _fmt_opex_report(opex_radar(conn))
        elif kind == "event":
            content = _fmt_event_report(event_trade_map(key))
        elif kind == "gex":
            try:
                _df = pd.read_sql("SELECT DISTINCT ticker FROM trades WHERE status='OPEN'", conn)
                _tks = [str(t).upper() for t in _df["ticker"].tolist()] if not _df.empty else ["SPY"]
            except Exception:
                _tks = ["SPY"]
            content = (chr(10) + chr(10)).join(_gex_reports(conn, _tks))
        else:
            content = str(key)
        ok = bookmark_save(conn, kind, key or kind, content)
    finally:
        conn.close()
    await query.message.reply_text(
        "📌 Saved to bookmarks. View with /bookmarks." if ok else "Bookmark failed.", parse_mode=H)


# ═══════════════════════════════════════════════════════════════════
# ── /gex POSITION-AWARE GAMMA  +  GOOGLE-NEWS PER-EVENT
# ═══════════════════════════════════════════════════════════════════
_EVENT_NEWS_QUERY = {
    "iran":           "Iran sanctions oil nuclear deal",
    "war":            "Middle East war conflict escalation",
    "oil_spike":      "oil price OPEC crude Hormuz",
    "fed_cut":        "Federal Reserve interest rate cut",
    "fed_hike":       "Federal Reserve interest rate hike",
    "china_stimulus": "China stimulus economy PBOC",
    "usd_up":         "US dollar DXY strength",
}

def _event_news_google(keys=None):
    """Targeted per-event headlines from Google News RSS (no key). {key:(title,link,when)}."""
    keys = keys or list(_EVENT_NEWS_QUERY)
    try:
        import feedparser, html as _h, time as _t, urllib.parse as _u
    except Exception:
        return {}
    res = {}
    for k in keys:
        q = _EVENT_NEWS_QUERY.get(k) or k
        try:
            fp = feedparser.parse(
                "https://news.google.com/rss/search?q=" + _u.quote(q) + "&hl=en-US&gl=US&ceid=US:en")
            for e in fp.entries[:1]:
                title = _h.unescape(e.get("title", "")).strip()
                if not title:
                    continue
                pp = e.get("published_parsed", None)
                when = _t.strftime("%d%b %H:%M", pp).lstrip("0") if pp else ""
                res[k] = (title, e.get("link", "").strip(), when)
        except Exception:
            continue
    return res

def _gex_spot(conn, tk):
    s = _opex_spot(conn, tk)
    if s <= 0:
        s = _last_price(tk)
    return s

def _fmt_gex_report(g, tk, spot, pos=None):
    if not g or not g.get("total_gex"):
        return f"📐 <b>{tk} GEX</b>: no options data in DB for this ticker."
    reg = g.get("gex_signal", "?")
    flip = g.get("zero_gamma"); cw = g.get("call_wall"); pw = g.get("put_wall")
    gm = g.get("total_gex_m", 0.0)
    lines = [f"📐 <b>{tk} GAMMA / GEX</b>  spot ${spot:.2f}",
             f"Regime: <b>{reg}</b>  · exp {g.get('expiry','?')} ({g.get('dte','?')}d)",
             f"Total GEX: {gm:+.1f}M"]
    wl = []
    if pw:   wl.append(f"Put wall (support)  ${pw:.0f}")
    if flip: wl.append(f"Gamma flip          ${flip:.0f}")
    if cw:   wl.append(f"Call wall (resist)  ${cw:.0f}")
    if wl:
        lines.append("<pre>" + "\n".join(wl) + "</pre>")
    if reg == "TRENDING":
        lines.append("⚡ <b>Negative gamma:</b> dealers amplify moves — expect bigger swings/trends. "
                     "Favors LONG options & breakouts; risky to sell premium.")
    elif reg == "PINNING":
        lines.append("🧲 <b>Positive gamma:</b> dealers dampen moves — range-bound/pinned. "
                     "Favors selling premium (iron condor / credit spreads) near the walls.")
    if flip and spot:
        lines.append(f"Spot is <b>{'above' if spot >= flip else 'below'}</b> the flip "
                     + ("→ stabilising bias." if spot >= flip else "→ volatile / trend bias."))
    if pos is not None and not pos.empty:
        dte = g.get("dte")
        lines.append("<b>Your legs vs gamma — suggested actions:</b>")
        for _, p in pos.iterrows():
            ot = str(p["option_type"]).upper()[:1]
            k = float(p["strike"] or 0)
            q = int(p.get("quantity", 1) or 1)
            side = "short" if q < 0 else "long"
            ref = cw if ot == "C" else pw
            loc = ((("above" if k >= ref else "below") + f" {'call' if ot=='C' else 'put'} wall ${ref:.0f}")
                   if ref else "no wall nearby")
            acts = []
            if ot == "C" and side == "short":
                acts.append("low assignment risk — let theta work, take ~50% profit" if (cw and k >= cw)
                            else (f"capped near wall — watch for breakout, roll up if spot clears ${cw:.0f}" if cw
                                  else "watch upside"))
                if reg == "TRENDING":
                    acts.append("neg-gamma can spike through; a cheap long-call hedge caps risk")
            elif ot == "C" and side == "long":
                acts.append("trend tailwind above flip — hold/trail" if (reg == "TRENDING" and flip and spot >= flip)
                            else ("pinning caps upside — take profit or roll out" if reg == "PINNING"
                                  else "hold vs call wall"))
            elif ot == "P" and side == "long":
                acts.append("spot at/below put wall — hedge paying, monetize part" if (pw and spot <= pw)
                            else (f"protection intact; support at put wall ${pw:.0f}" if pw else "protection intact"))
            elif ot == "P" and side == "short":
                acts.append("below flip — downside risk, roll down or close" if (reg == "TRENDING" and flip and spot < flip)
                            else ("lower assignment risk — hold" if (pw and k <= pw) else "monitor downside"))
            if isinstance(dte, (int, float)) and dte <= 21:
                acts.append(f"{int(dte)}DTE — gamma rising, manage per 21-DTE rule")
            lines.append(f"• {side} {ot} ${k:.0f} ({loc})")
            lines.append(f"   ↳ {'; '.join(acts) if acts else 'hold & monitor'}")
        ov = []
        if reg == "TRENDING":
            ov.append("neg-gamma regime → bigger swings; favor defined-risk longs, avoid naked shorts")
        elif reg == "PINNING":
            ov.append("pos-gamma regime → range/pin; premium-selling near walls favored")
        if flip and spot:
            ov.append(("spot above flip" if spot >= flip else "spot below flip") + f" ${flip:.0f}")
        if ov:
            lines.append("📋 <b>Overall:</b> " + "; ".join(ov) + ".")
    return "\n".join(lines)

def _kb_gex():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Refresh", callback_data="gex_refresh"),
         InlineKeyboardButton("📌 Bookmark", callback_data="bm|gex|positions")]])

def _fmt_squeeze_inline(sq):
    """One-line short-interest / days-to-cover / covering tag for position views."""
    if not sq or sq.get("short_pct") is None:
        return "‍Short interest: n/a (no float data)"
    parts = [f"Short {sq['short_pct']:.0f}% float"]
    if sq.get("dtc"):
        parts.append(f"DTC {sq['dtc']:.1f}")
    if sq.get("si_chg_pct") is not None:
        parts.append(f"SI {sq['si_chg_pct']:+.0f}% MoM")
    tag = f"{sq.get('emoji','')} squeeze {sq.get('score',0)}/5 {sq.get('stage','')}"
    rec = ""
    if sq.get("score", 0) >= 3:
        rec = " - covering/squeeze risk: bullish, trim shorts"
    elif sq.get("si_chg_pct") is not None and sq["si_chg_pct"] < -5:
        rec = " - shorts already covering"
    return "🩳 <b>" + " . ".join(parts) + "</b>  [" + tag + "]" + rec


def _to_mdy(s):
    for f in ("%Y-%m-%d", "%m-%d-%Y"):
        try:
            return datetime.strptime(str(s)[:10], f).strftime("%m-%d-%Y")
        except ValueError:
            continue
    return None


def _gex_reports(conn, tickers=None, position_aware=True):
    out = []
    tks = list(tickers) if tickers else []
    if not tks:
        try:
            _pf = pd.read_sql("SELECT DISTINCT ticker FROM trades WHERE status='OPEN'", conn)
            tks = [str(t).upper() for t in _pf["ticker"].tolist()] if not _pf.empty else []
        except Exception:
            tks = []
        if not tks:
            tks = ["SPY"]
    for tk in tks[:6]:
        spot = _gex_spot(conn, tk)
        pos = None
        if position_aware:
            try:
                pos = pd.read_sql(
                    "SELECT option_type, strike, quantity, expiry FROM trades"
                    " WHERE status='OPEN' AND UPPER(ticker)=?", conn, params=(tk.upper(),))
            except Exception:
                pos = None
        exps = [None]
        if pos is not None and not pos.empty:
            _e = []
            for ev in sorted(pos["expiry"].dropna().astype(str).unique()):
                m = _to_mdy(ev)
                if m and m not in _e:
                    _e.append(m)
            if _e:
                exps = _e
        for exp in exps:
            g = _compute_gex(tk, conn, spot, expiry=exp)
            legs = pos
            if pos is not None and not pos.empty and exp is not None:
                legs = pos[pos["expiry"].astype(str).apply(lambda x: _to_mdy(x) == exp)]
            rep = _fmt_gex_report(g, tk, spot, legs)
            try:
                rep += chr(10) + _fmt_squeeze_inline(short_squeeze_signal(tk, conn))
            except Exception:
                pass
            try:
                _vc = _compute_vanna_charm(tk, conn, spot, want_exp=exp)
                if _vc.get("vex"):
                    rep += chr(10) + "🌀 Vanna " + ("+" if _vc["vex"] > 0 else "") + f"{_vc['vex']/1e6:.1f}M — " + _vc["note"]
            except Exception:
                pass
            out.append(rep)
    return out

async def gex_command(update, ctx):
    """/gex [TICKERS] - gamma walls/flip/regime + position-aware notes. No arg = open positions."""
    args = list(getattr(ctx, "args", []) or [])
    conn = get_conn()
    try:
        if args:
            tks = [a.upper() for a in args]
        else:
            try:
                df = pd.read_sql("SELECT DISTINCT ticker FROM trades WHERE status='OPEN'", conn)
                tks = [str(t).upper() for t in df["ticker"].tolist()] if not df.empty else []
            except Exception:
                tks = []
            if not tks:
                tks = ["SPY"]
        msgs = _gex_reports(conn, tks)
    finally:
        conn.close()
    await update.message.reply_text("\n\n".join(msgs) if msgs else "No GEX data.",
                                    parse_mode=H, reply_markup=_kb_gex())

async def gex_view(query):
    conn = get_conn()
    try:
        try:
            df = pd.read_sql("SELECT DISTINCT ticker FROM trades WHERE status='OPEN'", conn)
            tks = [str(t).upper() for t in df["ticker"].tolist()] if not df.empty else ["SPY"]
        except Exception:
            tks = ["SPY"]
        msgs = _gex_reports(conn, tks)
    finally:
        conn.close()
    await query.message.reply_text("\n\n".join(msgs) if msgs else "No GEX data.",
                                   parse_mode=H, reply_markup=_kb_gex())


# ═══════════════════════════════════════════════════════════════════
# ── DATA FEEDS: Stooq price fallback + FRED macro + AlphaVantage sentiment
# ═══════════════════════════════════════════════════════════════════
def _stooq_price(ticker):
    """EOD close from Stooq (free, no key) - yfinance fallback."""
    try:
        import urllib.request, csv, io
        url = f"https://stooq.com/q/l/?s={str(ticker).lower()}.us&f=sd2t2ohlcv&h&e=csv"
        with urllib.request.urlopen(url, timeout=8) as r:
            txt = r.read().decode("utf-8", "ignore")
        rows = list(csv.DictReader(io.StringIO(txt)))
        if rows:
            v = rows[0].get("Close")
            if v not in (None, "", "N/D"):
                return float(v)
    except Exception:
        pass
    return 0.0

_FRED_SERIES = [
    ("DGS10",       "10Y Yield",   "%"),
    ("DFF",         "Fed Funds",   "%"),
    ("T10Y2Y",      "10Y-2Y",      "%"),
    ("DCOILWTICO",  "WTI Oil",     "$"),
    ("UNRATE",      "Unemploy",    "%"),
    ("VIXCLS",      "VIX",         ""),
    ("DTWEXBGS",    "USD Index",   ""),
]

def _fred_latest(series_id, api_key=None):
    key = api_key or os.environ.get("FRED_API_KEY", "")
    if not key:
        return None
    try:
        import urllib.request, json as _j
        url = (f"https://api.stlouisfed.org/fred/series/observations?series_id={series_id}"
               f"&api_key={key}&file_type=json&sort_order=desc&limit=2")
        with urllib.request.urlopen(url, timeout=8) as r:
            d = _j.loads(r.read().decode())
        obs = [o for o in d.get("observations", []) if o.get("value") not in (".", "", None)]
        if obs:
            latest = float(obs[0]["value"])
            prev = float(obs[1]["value"]) if len(obs) > 1 else latest
            return {"value": latest, "prev": prev, "date": obs[0]["date"]}
    except Exception:
        return None
    return None

def _av_sentiment(tickers="SPY,QQQ", api_key=None):
    """AlphaVantage NEWS_SENTIMENT average (free key). Returns {avg,label,n,top} or None."""
    key = api_key or os.environ.get("ALPHAVANTAGE_KEY", "")
    if not key:
        return None
    try:
        import urllib.request, json as _j
        url = (f"https://www.alphavantage.co/query?function=NEWS_SENTIMENT&tickers={tickers}"
               f"&apikey={key}&limit=20")
        with urllib.request.urlopen(url, timeout=10) as r:
            d = _j.loads(r.read().decode())
        feed = d.get("feed", [])
        if not feed:
            return None
        scores = [float(a.get("overall_sentiment_score", 0)) for a in feed]
        avg = sum(scores) / len(scores) if scores else 0.0
        label = "Bullish" if avg > 0.15 else ("Bearish" if avg < -0.15 else "Neutral")
        return {"avg": avg, "label": label, "n": len(feed), "top": feed[0].get("title", "")}
    except Exception:
        return None

def _macro_keyless():
    """Free, keyless macro snapshot — BLS (CPI/Core/Unemployment/Payrolls) + market yields
    (yfinance). Returns display rows so the macro dashboard works without any API key."""
    rows = []
    try:
        import urllib.request as _u, json as _j, datetime as _dt
        yr = _dt.datetime.now().year
        body = _j.dumps({"seriesid": ["CUUR0000SA0", "CUUR0000SA0L1E", "LNS14000000", "CES0000000001"],
                         "startyear": str(yr - 2), "endyear": str(yr)}).encode()
        req = _u.Request("https://api.bls.gov/publicAPI/v1/timeseries/data/", data=body,
                         headers={"Content-Type": "application/json", "User-Agent": "nyse-data/1.0"})
        j = _j.load(_u.urlopen(req, timeout=15))
        S = {}
        for s in j.get("Results", {}).get("series", []):
            pts = []
            for d in s.get("data", []):
                if str(d.get("period", "")).startswith("M"):
                    try:
                        pts.append((int(d["year"]), d["period"], float(d["value"])))
                    except Exception:
                        pass
            S[s["seriesID"]] = pts

        def _yoy(sid):
            p = S.get(sid, [])
            if len(p) < 13:
                return None
            latest = p[0]
            prior = next((x for x in p if x[1] == latest[1] and x[0] == latest[0] - 1), None)
            return (latest[2] / prior[2] - 1) * 100 if (prior and prior[2]) else None

        c = _yoy("CUUR0000SA0"); cc = _yoy("CUUR0000SA0L1E")
        if c is not None:
            rows.append(f"CPI YoY   {c:>6.1f}%")
        if cc is not None:
            rows.append(f"Core CPI  {cc:>6.1f}%")
        un = S.get("LNS14000000", [])
        if un:
            rows.append(f"Unemploy  {un[0][2]:>6.1f}%")
        nfp = S.get("CES0000000001", [])
        if len(nfp) >= 2:
            rows.append(f"NFP chg  {(nfp[0][2] - nfp[1][2]):>+6.0f}k")
    except Exception:
        pass
    try:
        for sym, name in [("^IRX", "3M yld"), ("^FVX", "5Y yld"), ("^TNX", "10Y yld"), ("^TYX", "30Y yld")]:
            try:
                h = yf.Ticker(sym).history(period="5d")["Close"].dropna()
                if len(h):
                    rows.append(f"{name:<9}{float(h.iloc[-1]):>6.2f}%")
            except Exception:
                pass
    except Exception:
        pass
    return rows


_AV_MACRO_SERIES = [
    ("REAL_GDP", "Real GDP", "&interval=annual", "$B"),
    ("INFLATION", "Inflation", "", "%"),
    ("RETAIL_SALES", "Retail Sales", "", "$M"),
    ("DURABLES", "Durables", "", "$M"),
    ("FEDERAL_FUNDS_RATE", "Fed Funds", "&interval=monthly", "%"),
]
_AV_MACRO_CACHE = {"day": None, "rows": None}


def _av_macro():
    """Alpha Vantage economic indicators. Free tier = 25 req/day & 1/sec, so fetched at most once
    per calendar day (spaced) and cached to a file that survives restarts."""
    key = os.environ.get("ALPHAVANTAGE_KEY", "")
    if not key:
        return []
    import time as _t, json as _j, datetime as _dt, urllib.request as _u
    today = _dt.date.today().isoformat()
    if _AV_MACRO_CACHE["rows"] is not None and _AV_MACRO_CACHE["day"] == today:
        return _AV_MACRO_CACHE["rows"]
    cache_f = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".av_macro_cache.json")
    try:
        if os.path.exists(cache_f):
            c = _j.load(open(cache_f, encoding="utf-8"))
            if c.get("day") == today and c.get("rows"):
                _AV_MACRO_CACHE.update(day=today, rows=c["rows"]); return c["rows"]
    except Exception:
        pass
    rows = []; ok = False
    for fn, name, extra, unit in _AV_MACRO_SERIES:
        try:
            url = f"https://www.alphavantage.co/query?function={fn}&apikey={key}{extra}"
            d = _j.loads(_u.urlopen(url, timeout=12).read().decode())
            data = d.get("data", [])
            if data:
                rows.append(f"{name:<13}{float(data[0]['value']):>12,.1f} {unit}"); ok = True
            _t.sleep(1.3)
        except Exception:
            continue
    if ok:
        _AV_MACRO_CACHE.update(day=today, rows=rows)
        try:
            _j.dump({"day": today, "rows": rows}, open(cache_f, "w", encoding="utf-8"))
        except Exception:
            pass
    return rows


def _fed_soma_line():
    """Compact Fed balance-sheet line via keyless NY Fed SOMA. Returns (text, direction)."""
    try:
        import urllib.request as _u, json as _j
        s = _j.loads(_u.urlopen(_u.Request("https://markets.newyorkfed.org/api/soma/summary.json",
                                           headers={"User-Agent": "nyse-data/1.0"}), timeout=15).read().decode())
        rows = s["soma"]["summary"]
        tot = float(rows[-1]["total"])
        chg = tot - float(rows[-14]["total"]) if len(rows) > 14 else 0.0
        lab = "expanding 🟢" if chg > 25e9 else "QT 🔴" if chg < -50e9 else "flat 🟡"
        dirn = "expanding" if chg > 25e9 else "qt" if chg < -50e9 else "flat"
        return f"Fed BS ${tot/1e12:.2f}T ({chg/1e9:+.0f}B/13wk · {lab})", dirn
    except Exception:
        return None, None


def _jpm_collar_line():
    """Compact current JPMorgan collar (JHEQX) line from SEC N-PORT (keyless)."""
    try:
        import urllib.request as _u, re as _re
        UA = {"User-Agent": "nyse-data research srinivas.analystsas@gmail.com"}

        def _g(url):
            return _u.urlopen(_u.Request(url, headers=UA), timeout=20).read().decode("utf-8", "ignore")

        feed = _g("https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=S000043249"
                  "&type=NPORT-P&count=2&output=atom")
        acc = _re.findall(r"<accession-number>(.*?)</accession-number>", feed)
        if not acc:
            return None
        x = _g(f"https://www.sec.gov/Archives/edgar/data/1217286/{acc[0].replace('-', '')}/primary_doc.xml")
        per = _re.search(r"<repPdDate>(.*?)</repPdDate>", x)
        legs = {}
        for b in x.split("<invstOrSec>"):
            if "optionSwaption" not in b or not any(s in b for s in ("S&P 500", "SPX", "S&amp;P 500")):
                continue
            pc = _re.search(r"<putOrCall>(.*?)</putOrCall>", b)
            wp = _re.search(r"<writtenOrPur>(.*?)</writtenOrPur>", b)
            ep = _re.search(r"<exercisePrice>(.*?)</exercisePrice>", b)
            if pc and wp and ep:
                legs[f"{wp.group(1)} {pc.group(1)}"] = round(float(ep.group(1)))
        if legs.get("Purchased Put") and legs.get("Written Call"):
            return (f"JPM collar ({per.group(1) if per else '?'}): SPX put {legs['Purchased Put']} / "
                    f"call {legs['Written Call']} · floor {legs.get('Written Put')}")
    except Exception:
        return None
    return None


_MACRO_REPORT_CACHE = {"ts": 0.0, "txt": None}


def _fmt_macro_report():
    import time as _t
    if _MACRO_REPORT_CACHE["txt"] is not None and _t.time() - _MACRO_REPORT_CACHE["ts"] < 21600:
        return _MACRO_REPORT_CACHE["txt"]   # 6h cache — protects free-tier API quotas
    lines = ["📊 <b>MACRO DASHBOARD</b>", ""]
    if not os.environ.get("FRED_API_KEY"):
        rows = _macro_keyless()
        if rows:
            lines.append("<pre>" + "\n".join(rows) + "</pre>")
            lines.append("<i>Free keyless data (BLS prints + market yields). "
                         "Set FRED_API_KEY for the full FRED series.</i>")
        else:
            lines.append("<i>Macro data unavailable right now — try again shortly.</i>")
    else:
        rows = []
        for sid, name, unit in _FRED_SERIES:
            d = _fred_latest(sid)
            if not d:
                continue
            chg = d["value"] - d["prev"]
            arrow = "UP" if chg > 0 else ("DN" if chg < 0 else "--")
            rows.append(f"{name:<10}{d['value']:>8.2f}{unit:<1} {arrow}")
        if rows:
            lines.append("<pre>" + "\n".join(rows) + "</pre>")
        else:
            lines.append("Could not fetch FRED data.")
    av = _av_macro()
    if av:
        lines += ["", "🏦 <b>AlphaVantage indicators:</b>", "<pre>" + "\n".join(av) + "</pre>"]
    _fl, _fdir = _fed_soma_line()
    if _fl:
        lines += ["", "🏦 " + _fl]
    _jl = _jpm_collar_line()
    if _jl:
        lines += ["🛡️ " + _jl]
    sent = _av_sentiment("SPY,QQQ")
    if sent:
        lines += ["", f"📰 <b>News sentiment:</b> {sent['label']} ({sent['avg']:+.2f}, {sent['n']} articles)"]
    elif not os.environ.get("ALPHAVANTAGE_KEY"):
        lines += ["", "<i>Set ALPHAVANTAGE_KEY for news-sentiment (free at alphavantage.co).</i>"]
    txt = "\n".join(lines)
    _MACRO_REPORT_CACHE.update(ts=_t.time(), txt=txt)
    return txt

async def macro_command(update, ctx):
    """/macro - FRED macro indicators + AlphaVantage news sentiment (keys optional)."""
    await update.message.reply_text(_fmt_macro_report(), parse_mode=H)


# ═══════════════════════════════════════════════════════════════════
# ── MACRO/EVENT HUB — button menu (no typing needed)
# ═══════════════════════════════════════════════════════════════════
HUB_MENU_KB = InlineKeyboardMarkup([
    [InlineKeyboardButton("☀️ Briefing", callback_data="brief_refresh"),
     InlineKeyboardButton("🗓️ OpEx", callback_data="opex_refresh")],
    [InlineKeyboardButton("📐 GEX (positions)", callback_data="gex_refresh"),
     InlineKeyboardButton("🩳 Short Interest", callback_data="sq_scan")],
    [InlineKeyboardButton("🌍 Events", callback_data="ev_menu"),
     InlineKeyboardButton("📓 Journal", callback_data="jr_view")],
    [InlineKeyboardButton("📊 Macro", callback_data="macro_view"),
     InlineKeyboardButton("🔖 Bookmarks", callback_data="show_bookmarks")],
    [InlineKeyboardButton("🚀 Momentum", callback_data="mom_view"),
     InlineKeyboardButton("🧭 Regime", callback_data="regime_view"),
     InlineKeyboardButton("🌀 Vanna", callback_data="vanna_view")],
    [InlineKeyboardButton("🌅 Next-Day Plan", callback_data="plan_view")],
    [BACK_BTN],
])

def _events_kb():
    rows = []
    keys = sorted(MACRO_EVENT_MAP)
    for i in range(0, len(keys), 2):
        rows.append([InlineKeyboardButton(MACRO_EVENT_MAP[k]["title"][:20],
                                          callback_data="event_refresh|" + k)
                     for k in keys[i:i+2]])
    rows.append([InlineKeyboardButton("⬅️ Hub", callback_data="hub_menu")])
    return InlineKeyboardMarkup(rows)

async def squeeze_view(query):
    conn = get_conn()
    try:
        try:
            df = pd.read_sql("SELECT DISTINCT ticker FROM trades WHERE status='OPEN'", conn)
            tks = [str(t).upper() for t in df["ticker"].tolist()] if not df.empty else []
        except Exception:
            tks = []
        if not tks:
            tks = DEFAULT_TICKERS[:8]
        rows = ["🩳 <b>SHORT INTEREST / SQUEEZE</b>",
                "<i>(open positions, or watchlist if none)</i>", ""]
        for tk in tks[:8]:
            try:
                s = short_squeeze_signal(tk, conn)
                sp = s.get("short_pct"); dtc = s.get("dtc")
                if sp is not None:
                    rows.append(f"{s['emoji']} <b>{tk}</b> {s['score']}/5 {s['stage']} "
                                f"| SI {sp:.0f}% DTC {dtc or 0:.1f}")
                else:
                    rows.append(f"⚪ <b>{tk}</b> — no short-interest data")
            except Exception:
                pass
    finally:
        conn.close()
    rows += ["", "<i>Full detail: /squeeze TICKER</i>"]
    await query.message.reply_text("\n".join(rows), parse_mode=H, reply_markup=HUB_MENU_KB)

async def journal_view(query):
    conn = get_conn()
    try:
        msg = _fmt_journal_review(conn)
    finally:
        conn.close()
    await query.message.reply_text(msg, parse_mode=H, reply_markup=HUB_MENU_KB)

async def macro_view(query):
    await query.message.reply_text(_fmt_macro_report(), parse_mode=H, reply_markup=HUB_MENU_KB)


# ── ported from copy2 for dedup: short sellers + OI key levels ──
async def short_sellers_view(query):
    """Top shorted stocks using yfinance short interest data."""
    _loading = await query.message.reply_text("Fetching short interest data...", parse_mode=H)

    _WATCH = [
        "TSLA","NVDA","PLTR","COIN","AMZN","META","NFLX","AMD","SMCI","CRWD",
        "RIVN","LCID","NIO","BYND","GME","UPST","AFRM","MRNA","RXRX","CRSP",
        "AI","SOUN","SOFI","W","PTON","BE","VST","SNOW","HOOD","RBLX",
    ]

    rows = []
    for tk in _WATCH:
        try:
            info = yf.Ticker(tk).info
            spf  = info.get("shortPercentOfFloat")
            sr   = info.get("shortRatio")
            ss   = info.get("sharesShort")
            ssp  = info.get("sharesShortPriorMonth")
            if spf and spf < 1:
                spf = spf * 100
            if spf is None:
                continue
            mom = None
            if ss and ssp and ssp > 0:
                mom = (ss - ssp) / ssp * 100
            sc = 0
            if spf:
                sc += 4 if spf >= 30 else (3 if spf >= 20 else (2 if spf >= 10 else 1))
            if sr:
                sc += 3 if sr >= 10 else (2 if sr >= 5 else 1)
            if ss and ssp and ss > ssp * 1.10:
                sc += 2
            sc = min(10, sc)
            rows.append({"tk": tk, "spf": spf, "sr": sr, "mom": mom, "sc": sc})
        except Exception:
            pass

    rows.sort(key=lambda x: x["spf"] or 0, reverse=True)

    if not rows:
        await _loading.edit_text("No short data available.")
        return

    parts = [hdr("SHORT SELLERS TOP SHORTED")]
    parts.append(f"<i>{len(rows)} stocks scanned</i>\n")

    parts.append(shdr("SHORT % FLOAT RANKING"))
    tbl = [f"{'Tkr':<6} {'Shrt%':>5} {'Days':>5} {'MoM':>6} {'Sqz':>3}"]
    tbl.append("-" * 32)
    for r in rows[:12]:
        spf_s = f"{r['spf']:.1f}%" if r['spf'] else "  -- "
        sr_s  = f"{r['sr']:.0f}d"  if r['sr']  else "  -- "
        mom_s = f"{r['mom']:+.0f}%" if r['mom'] is not None else "   -- "
        em    = "[HOT]" if r['sc'] >= 7 else ("[!]" if r['sc'] >= 4 else "   ")
        tbl.append(f"{r['tk']:<6} {spf_s:>5} {sr_s:>5} {mom_s:>6} {r['sc']:>2}{em}")
    parts.append(mono("\n".join(tbl)))

    squeeze = [r for r in rows if r["sc"] >= 6]
    if squeeze:
        parts.append("\n" + shdr("SQUEEZE CANDIDATES (Score 6+/10)"))
        for r in squeeze[:4]:
            mom_tag = ""
            if r["mom"] is not None:
                if r["mom"] > 10:
                    mom_tag = f" RISING +{r['mom']:.0f}%"
                elif r["mom"] < -10:
                    mom_tag = f" COVERING {r['mom']:.0f}% -- TRIGGER"
            parts.append(
                "[HOT] <b>" + r["tk"] + "</b> "
                + mono(f"Short:{r['spf']:.1f}% Days:{(r['sr'] or 0):.0f}d Sqz:{r['sc']}/10{mom_tag}")
            )

    covering = sorted([r for r in rows if r["mom"] is not None and r["mom"] < -10], key=lambda x: x["mom"])
    if covering:
        parts.append("\n" + shdr("COVERING -- Watch for Rally"))
        for r in covering[:4]:
            parts.append("[UP] <b>" + r["tk"] + f"</b> {r['spf']:.1f}% | {r['mom']:+.0f}% MoM")

    rising = sorted([r for r in rows if r["mom"] is not None and r["mom"] > 15],
                    key=lambda x: x["mom"] or 0, reverse=True)
    if rising:
        parts.append("\n" + shdr("RISING SHORTS -- Bearish Conviction"))
        for r in rising[:4]:
            parts.append("[DN] <b>" + r["tk"] + f"</b> {r['spf']:.1f}% | +{r['mom']:.0f}% MoM")

    parts.append("\n<i>Shrt%=float sold short | Days=days-to-cover\nSqz=squeeze risk 0-10 | Source:Yahoo bi-monthly</i>")

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("Insider Menu", callback_data="menu_insider")],
        [BACK_BTN],
    ])
    try:
        await _loading.delete()
    except Exception:
        pass
    await _safe_reply(query.message, "\n".join(parts), reply_markup=kb)


def _oi_key_levels(ticker: str, conn, trade_date: str = None) -> dict:
    """OI walls: call_wall, put_wall, max_pain, gamma_walls from options_change."""
    tk = str(ticker).upper()
    try:
        # Get latest trade date if not provided
        if not trade_date:
            r = pd.read_sql("""SELECT DISTINCT trade_date_now FROM options_change WHERE ticker=?
                ORDER BY substr(trade_date_now,7,4)||substr(trade_date_now,1,2)||substr(trade_date_now,4,2) DESC
                LIMIT 1""", conn, params=(tk,))
            if r.empty: return {}
            trade_date = r["trade_date_now"].iloc[0]
        # Aggregate by strike across nearest future expiry
        df = pd.read_sql("""
            SELECT strike, expiry_date,
                   SUM(openInt_Call_now) as call_oi, SUM(openInt_Put_now) as put_oi
            FROM options_change WHERE ticker=? AND trade_date_now=?
            GROUP BY strike, expiry_date
        """, conn, params=(tk, trade_date))
        if df.empty: return {}
        df["strike"] = pd.to_numeric(df["strike"], errors="coerce")
        df["call_oi"] = pd.to_numeric(df["call_oi"], errors="coerce").fillna(0)
        df["put_oi"]  = pd.to_numeric(df["put_oi"],  errors="coerce").fillna(0)
        # Use nearest future expiry
        today_s = datetime.now().strftime("%Y%m%d")
        def _exp_sort(e):
            s = str(e)
            return s[6:10]+s[0:2]+s[3:5] if len(s)>=10 else s
        df["_es"] = df["expiry_date"].apply(_exp_sort)
        future = df[df["_es"] >= today_s]
        if future.empty: future = df
        near_exp = future["_es"].min()
        near_df = future[future["_es"] == near_exp].copy()
        near_agg = near_df.groupby("strike").agg({"call_oi":"sum","put_oi":"sum"}).reset_index()
        # Call wall = strike with max call OI
        call_wall_row = near_agg.loc[near_agg["call_oi"].idxmax()]
        put_wall_row  = near_agg.loc[near_agg["put_oi"].idxmax()]
        call_wall = float(call_wall_row["strike"])
        put_wall  = float(put_wall_row["strike"])
        # Max pain = strike minimizing sum of ITM losses
        strikes = sorted(near_agg["strike"].tolist())
        min_pain = float("inf"); max_pain_strike = strikes[0]
        for test in strikes:
            pain = sum(max(0, test - s) * float(near_agg.loc[near_agg["strike"]==s,"call_oi"].iloc[0]) for s in strikes if near_agg.loc[near_agg["strike"]==s,"call_oi"].iloc[0] > 0)
            pain += sum(max(0, s - test) * float(near_agg.loc[near_agg["strike"]==s,"put_oi"].iloc[0]) for s in strikes if near_agg.loc[near_agg["strike"]==s,"put_oi"].iloc[0] > 0)
            if pain < min_pain:
                min_pain = pain; max_pain_strike = test
        # Gamma walls = strikes where call+put OI >= 2x mean
        near_agg["total"] = near_agg["call_oi"] + near_agg["put_oi"]
        mean_oi = near_agg["total"].mean()
        gamma_walls = sorted(near_agg[near_agg["total"] >= 2 * mean_oi]["strike"].tolist())
        near_exp_str = near_df["expiry_date"].iloc[0] if not near_df.empty else ""
        return {"call_wall": call_wall, "put_wall": put_wall, "max_pain": max_pain_strike,
                "gamma_walls": gamma_walls, "expiry": near_exp_str}
    except Exception as e:
        return {}


# ═══════════════════════════════════════════════════════════════════
# ── ADVANCED SIGNALS: Vanna/Charm · Momentum(12-1) · Risk Regime · FOMC · Kelly · PEAD
# ═══════════════════════════════════════════════════════════════════

def _bs_vanna_charm(S, K, T, sigma, r=0.045):
    if S <= 0 or K <= 0 or T <= 0 or sigma <= 0:
        return (0.0, 0.0)
    try:
        srt = sigma * _math.sqrt(T)
        d1 = (_math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / srt
        d2 = d1 - srt
        pdf = float(_spnorm.pdf(d1))
        vanna = -pdf * d2 / sigma
        charm = -pdf * (2 * r * T - d2 * srt) / (2 * T * srt)
        return (vanna, charm)
    except Exception:
        return (0.0, 0.0)

def _compute_vanna_charm(ticker, conn, spot, want_exp=None):
    """Net dealer vanna/charm exposure for the nearest liquid expiry."""
    out = {"vex": 0.0, "charm": 0.0, "note": "", "expiry": None}
    if not spot or spot <= 0:
        return out
    try:
        ld = pd.read_sql(
            "SELECT trade_date_now FROM options_change WHERE ticker=?"
            " ORDER BY substr(trade_date_now,7,4)||substr(trade_date_now,1,2)||substr(trade_date_now,4,2) DESC LIMIT 1",
            conn, params=(ticker,))
        if ld.empty:
            return out
        date_str = ld["trade_date_now"].iloc[0]
        ref = datetime.strptime(date_str, "%m-%d-%Y").date()
    except Exception:
        return out
    try:
        edf = pd.read_sql(
            "SELECT expiry_date, SUM(openInt_Call_now)+SUM(openInt_Put_now) AS oi"
            " FROM options_change WHERE ticker=? AND trade_date_now=? GROUP BY expiry_date",
            conn, params=(ticker, date_str))
    except Exception:
        return out
    cand = []
    for _, e in edf.iterrows():
        ed = _opex_parse_date(e["expiry_date"])
        if ed:
            dte = (ed - ref).days
            if dte >= 0:
                cand.append((dte, float(e["oi"] or 0), str(e["expiry_date"])))
    if not cand:
        return out
    if want_exp:
        _m = [c for c in cand if c[2] == want_exp]
        if _m:
            dte, _oi, expiry = _m[0]
        else:
            _pool = [c for c in cand if c[0] >= 1] or cand
            dte, _oi, expiry = min(_pool, key=lambda c: c[0])
    else:
        _pool = [c for c in cand if c[0] >= 1] or cand
        near = [c for c in _pool if c[0] <= 60]
        dte, _oi, expiry = (max(near, key=lambda c: c[1]) if near else min(_pool, key=lambda c: c[0]))
    T = max(dte / 365.0, 1.0 / 365.0)
    out["expiry"] = expiry
    try:
        df = pd.read_sql(
            "SELECT strike, SUM(openInt_Call_now) AS c_oi, SUM(openInt_Put_now) AS p_oi,"
            " AVG(CASE WHEN lastPrice_Call_now>0 THEN lastPrice_Call_now END) AS c_px"
            " FROM options_change WHERE ticker=? AND trade_date_now=? AND expiry_date=? GROUP BY strike",
            conn, params=(ticker, date_str, expiry))
    except Exception:
        return out
    vex = charm = 0.0
    for _, r in df.iterrows():
        K = float(r["strike"]); c_oi = float(r["c_oi"] or 0); p_oi = float(r["p_oi"] or 0)
        if c_oi <= 0 and p_oi <= 0:
            continue
        c_px = float(r["c_px"] or 0); sigma = 0.30
        if c_px > 0.10 and abs(K - spot) / spot < 0.30 and K >= spot * 0.85:
            try:
                iv = _implied_vol_hp(c_px, spot, K, T)
                if iv and 0.03 < iv < 3.0:
                    sigma = iv
            except Exception:
                pass
        sigma = max(0.05, min(sigma, 3.0))
        va, ch = _bs_vanna_charm(spot, K, T, sigma)
        vex += va * (c_oi - p_oi) * spot * 0.01
        charm += ch * (c_oi - p_oi) * spot * 0.01
    out["vex"] = vex; out["charm"] = charm
    out["note"] = ("a vol DROP makes dealers BUY (vanna tailwind / melt-up bias)."
                   if vex > 0 else "a vol drop makes dealers SELL (vanna headwind).")
    return out

def _momentum_signal(ticker):
    try:
        h = yf.Ticker(ticker).history(period="13mo")
        c = h["Close"].dropna()
        if len(c) < 210:
            return None
        p_skip = float(c.iloc[-21]); p_year = float(c.iloc[-252]) if len(c) >= 252 else float(c.iloc[0])
        ret_12_1 = (p_skip - p_year) / p_year * 100 if p_year > 0 else 0.0
        ret_1m = (float(c.iloc[-1]) - p_skip) / p_skip * 100 if p_skip > 0 else 0.0
        ma200 = float(c.rolling(200).mean().iloc[-1])
        above200 = float(c.iloc[-1]) > ma200
        return {"ret_12_1": ret_12_1, "ret_1m": ret_1m, "above200": above200}
    except Exception:
        return None

# ═══════════════════════════════════════════════════════════════════
# ── FULL-UNIVERSE CROSS-SECTIONAL MOMENTUM (12-1) — precompute & store
#    Jegadeesh-Titman / AQR factor. Ranks the whole DB universe daily and
#    caches to momentum_ranks so Telegram/Streamlit reads are instant.
# ═══════════════════════════════════════════════════════════════════
def _ensure_momentum_table(conn):
    conn.execute(
        "CREATE TABLE IF NOT EXISTS momentum_ranks ("
        "asof TEXT, ticker TEXT, ret_12_1 REAL, ret_6_1 REAL, ret_1m REAL, "
        "above200 INTEGER, mom_rank INTEGER, pct_rank REAL, decile INTEGER, zscore REAL, "
        "PRIMARY KEY (asof, ticker))")
    conn.commit()

# Leveraged / inverse / vol ETFs distort a momentum ranking (a 3x ETF mechanically
# tops any up-market list), so they are excluded by default. Index symbols (^VIX)
# are not directly investable and are dropped too.
_LEV_INV_VOL_ETFS = frozenset({
    "SOXL", "SOXS", "SPXL", "SPXS", "SPXU", "UPRO", "SQQQ", "TQQQ", "QID", "QLD",
    "UVXY", "VXX", "VIXY", "UVIX", "SVXY", "SVIX", "UDOW", "SDOW", "TNA", "TZA",
    "LABU", "LABD", "FAS", "FAZ", "NUGT", "DUST", "JNUG", "JDST", "BOIL", "KOLD",
    "YINN", "YANG", "TMF", "TMV", "GUSH", "DRIP", "ERX", "ERY", "TECL", "TECS",
    "WEBL", "WEBS", "DPST", "NAIL", "CURE", "DFEN", "FNGU", "FNGD", "BULZ",
    "TSLL", "TSLQ", "TSLS", "NVDL", "NVDU", "NVDD", "CONL", "MSTU", "MSTX", "MSTZ",
})

def _universe_tickers(conn, exclude_leveraged=True):
    try:
        df = pd.read_sql("SELECT DISTINCT ticker FROM stock_daily", conn)
        tks = sorted({str(t).strip().upper() for t in df["ticker"].tolist() if str(t).strip()})
        if exclude_leveraged:
            tks = [t for t in tks if t not in _LEV_INV_VOL_ETFS and not t.startswith("^")]
        return tks
    except Exception:
        return []

def _batch_closes(tickers):
    """{ticker: adjusted-close Series} via batched, threaded yf.download (fast)."""
    out = {}
    CH = 45
    for i in range(0, len(tickers), CH):
        chunk = tickers[i:i + CH]
        try:
            data = yf.download(chunk, period="13mo", interval="1d",
                               auto_adjust=True, progress=False, threads=True,
                               group_by="ticker")
        except Exception:
            data = None
        if data is None or getattr(data, "empty", True):
            continue
        for tk in chunk:
            try:
                s = data["Close"] if len(chunk) == 1 else data[tk]["Close"]
                s = s.dropna()
                if len(s) >= 210:
                    out[tk] = s
            except Exception:
                continue
    return out

def compute_universe_momentum(force=False):
    """Compute 12-1 cross-sectional momentum for the whole DB universe and store in
    momentum_ranks. Opens its own connection (executor-safe). Returns (status,count,asof)."""
    conn = get_conn()
    try:
        _ensure_momentum_table(conn)
        asof = datetime.now().strftime("%Y-%m-%d")
        if not force:
            try:
                ex = conn.execute("SELECT COUNT(*) FROM momentum_ranks WHERE asof=?", (asof,)).fetchone()[0]
            except Exception:
                ex = 0
            if ex:
                return ("cached", ex, asof)
        tks = _universe_tickers(conn)
        if not tks:
            return ("no-universe", 0, asof)
        closes = _batch_closes(tks)
        rows = []
        for tk, c in closes.items():
            try:
                p_skip = float(c.iloc[-21])
                p_year = float(c.iloc[-252]) if len(c) >= 252 else float(c.iloc[0])
                p_6 = float(c.iloc[-126]) if len(c) >= 126 else float(c.iloc[0])
                if p_year <= 0 or p_skip <= 0:
                    continue
                r121 = (p_skip - p_year) / p_year * 100
                r61 = (p_skip - p_6) / p_6 * 100 if p_6 > 0 else 0.0
                r1m = (float(c.iloc[-1]) - p_skip) / p_skip * 100
                ma200 = float(c.rolling(200).mean().iloc[-1])
                above = 1 if float(c.iloc[-1]) > ma200 else 0
                rows.append([tk, r121, r61, r1m, above])
            except Exception:
                continue
        if not rows:
            return ("no-data", 0, asof)
        rows.sort(key=lambda x: x[1], reverse=True)
        n = len(rows)
        vals = [r[1] for r in rows]
        mean = sum(vals) / n
        std = (sum((v - mean) ** 2 for v in vals) / n) ** 0.5 or 1.0
        recs = []
        for i, r in enumerate(rows):
            rank = i + 1
            pct = (n - rank) / (n - 1) * 100 if n > 1 else 100.0
            dec = min(10, int(i * 10 / n) + 1)
            z = (r[1] - mean) / std
            recs.append((asof, r[0], r[1], r[2], r[3], r[4], rank, pct, dec, z))
        conn.execute("DELETE FROM momentum_ranks WHERE asof=?", (asof,))
        conn.executemany(
            "INSERT INTO momentum_ranks (asof,ticker,ret_12_1,ret_6_1,ret_1m,above200,"
            "mom_rank,pct_rank,decile,zscore) VALUES (?,?,?,?,?,?,?,?,?,?)", recs)
        conn.commit()
        return ("computed", len(recs), asof)
    finally:
        conn.close()

def load_momentum_ranks(conn):
    """Latest stored snapshot → (DataFrame ordered by rank, asof) or (None,None)."""
    try:
        _ensure_momentum_table(conn)
        asof = conn.execute("SELECT MAX(asof) FROM momentum_ranks").fetchone()[0]
        if not asof:
            return None, None
        df = pd.read_sql("SELECT * FROM momentum_ranks WHERE asof=? ORDER BY mom_rank",
                         conn, params=(asof,))
        return df, asof
    except Exception:
        return None, None

def _mom_asof_disp(asof):
    try:
        return datetime.strptime(asof, "%Y-%m-%d").strftime("%d%b").lstrip("0")
    except Exception:
        return asof or "?"

def _fmt_momentum_leaderboard(conn, n=8, highlight=None):
    df, asof = load_momentum_ranks(conn)
    if df is None or df.empty:
        return ("🚀 <b>MOMENTUM 12-1 — UNIVERSE</b>\n\n"
                "No snapshot yet. Tap <b>Recompute</b> to build it (~1 min).")
    highlight = {str(h).upper() for h in (highlight or set())}
    stale = (asof != datetime.now().strftime("%Y-%m-%d"))
    total = len(df)

    def _block(sub, title):
        out = [title, "<pre>", "rk tkr    12-1  1m"]
        for _, r in sub.iterrows():
            tk = str(r["ticker"])[:5]
            star = "*" if tk in highlight else " "
            tr = "↑" if int(r["above200"]) else "↓"
            out.append(f"{int(r['mom_rank']):<2}{star}{tk:<5}{r['ret_12_1']:>+5.0f}%{r['ret_1m']:>+4.0f}%{tr}")
        out.append("</pre>")
        return "\n".join(out)

    parts = ["🚀 <b>MOMENTUM 12-1 — UNIVERSE</b>",
             f"<i>as of {_mom_asof_disp(asof)} · {total} names · 12-mo ret, skip 1m</i>"]
    if stale:
        parts.append("⚠️ <i>snapshot not from today — tap Recompute</i>")
    parts.append(_block(df.head(n), "🟢 <b>TOP — momentum longs</b>"))
    parts.append(_block(df.tail(n).iloc[::-1], "🔴 <b>BOTTOM — momentum shorts</b>"))
    if highlight:
        mine = df[df["ticker"].isin(highlight)].sort_values("mom_rank")
        if not mine.empty:
            ml = ["⭐ <b>Your positions ranked</b>", "<pre>", "tkr    12-1  rk/dec"]
            for _, r in mine.iterrows():
                ml.append(f"{str(r['ticker'])[:5]:<5}{r['ret_12_1']:>+5.0f}% {int(r['mom_rank'])}/{int(r['decile'])}")
            ml.append("</pre>")
            parts.append("\n".join(ml))
    parts.append("<i>Top decile = strongest trend (long bias); bottom decile = weakest "
                 "(short/avoid). Leveraged/inverse/vol ETFs excluded. Best when aligned "
                 "with Risk Regime — press longs in RISK-ON.</i>")
    return "\n".join(parts)

def _kb_momentum():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Recompute (today)", callback_data="mom_recompute"),
         InlineKeyboardButton("ℹ️ How to read", callback_data="mom_help")],
        [InlineKeyboardButton("⬅️ Hub", callback_data="hub_menu")]])

_MOM_HELP = (
    "🚀 <b>MOMENTUM 12-1 — HOW TO READ IT</b>\n\n"
    "<b>What it is:</b> ranks every name in your DB by trend strength, so you see "
    "leaders to ride and laggards to avoid.\n\n"
    "<b>Columns</b>\n"
    "• <b>rk</b> — rank of all names (#1 = strongest trend)\n"
    "• <b>tkr</b> — ticker (<b>*</b> = you hold it)\n"
    "• <b>12-1</b> — 12-month return, <i>skipping the last month</i>. The trend that "
    "tends to persist — the core score.\n"
    "• <b>1m</b> — last month's return; health-check (still going or rolling over?)\n"
    "• <b>↑/↓</b> — above/below the 200-day average (long-term uptrend intact?)\n\n"
    "<b>Sections</b>\n"
    "🟢 <b>TOP</b> — strongest trends (top decile) → ride-winners longs\n"
    "🔴 <b>BOTTOM</b> — weakest names (bottom decile) → avoid longs / careful shorts\n"
    "⭐ <b>Your positions</b> — where your trades rank, shown as <code>12-1% rank/decile</code>\n"
    "<b>Decile</b> — 1 = top 10% (best), 10 = bottom 10% (worst)\n\n"
    "<b>How to use</b>\n"
    "• Longs work best in RISK-ON — check 🧭 Regime.\n"
    "• Don't chase a name with a huge 1m pop — wait for a pullback.\n"
    "• Bottom list = mostly \"don't go long\"; short only with defined risk.\n"
    "• Leveraged/inverse/vol ETFs are excluded for a cleaner signal.\n\n"
    "<i>Educational, not advice. Size for being wrong.</i>")

async def mom_help_view(query):
    await query.message.reply_text(_MOM_HELP, parse_mode=H, reply_markup=_kb_momentum())

def _hp_model_momentum(ticker, conn, spot):
    m = _momentum_signal(ticker)
    if not m:
        return {"signal": "NEUTRAL", "prob": 50, "reason": "momentum: n/a"}
    r = m["ret_12_1"]
    if r > 20 and m["above200"]:
        return {"signal": "BULL", "prob": min(80, 58 + r * 0.3), "reason": f"12-1 momentum +{r:.0f}% & >200DMA"}
    if r < -15 and not m["above200"]:
        return {"signal": "BEAR", "prob": min(80, 58 + abs(r) * 0.3), "reason": f"12-1 momentum {r:.0f}% & <200DMA"}
    return {"signal": "NEUTRAL", "prob": 52, "reason": f"12-1 momentum {r:+.0f}%"}

async def momentum_command(update, ctx):
    """/momentum [TICKERS] - 12-1 momentum. No arg = full-universe leaderboard."""
    args = list(getattr(ctx, "args", []) or [])
    conn = get_conn()
    try:
        if args:
            tks = [a.upper() for a in args]
            res = []
            for tk in tks[:15]:
                m = _momentum_signal(tk)
                if m:
                    res.append((tk, m))
            res.sort(key=lambda x: x[1]["ret_12_1"], reverse=True)
            rows = ["🚀 <b>MOMENTUM (12-1)</b>", "<i>12-mo return, skip last month</i>", ""]
            for tk, m in res:
                ic = "🟢" if (m["ret_12_1"] > 20 and m["above200"]) else ("🔴" if m["ret_12_1"] < -15 else "⚪")
                rows.append(f"{ic} <b>{tk}</b> {m['ret_12_1']:+.0f}%  1m {m['ret_1m']:+.0f}%  {'>200' if m['above200'] else '<200'}DMA")
            await update.message.reply_text("\n".join(rows), parse_mode=H)
            return
        df, _asof = load_momentum_ranks(conn)
        if df is None or df.empty:
            await update.message.reply_text("⏳ Building universe momentum (~1 min, first run)…", parse_mode=H)
            await asyncio.get_event_loop().run_in_executor(None, compute_universe_momentum, True)
        try:
            hl = set(pd.read_sql("SELECT DISTINCT UPPER(ticker) tk FROM trades WHERE status='OPEN'", conn)["tk"].tolist())
        except Exception:
            hl = set()
        txt = _fmt_momentum_leaderboard(conn, n=8, highlight=hl)
    finally:
        conn.close()
    await update.message.reply_text(txt, parse_mode=H, reply_markup=_kb_momentum())

_FOMC_DATES = ["01-28-2026", "03-18-2026", "04-29-2026", "06-17-2026",
               "07-29-2026", "09-16-2026", "10-28-2026", "12-09-2026"]
def _fomc_context():
    today = datetime.now().date()
    nxt = None
    for d in _FOMC_DATES:
        try:
            dd = datetime.strptime(d, "%m-%d-%Y").date()
        except Exception:
            continue
        if dd >= today:
            nxt = dd; break
    if not nxt:
        return None
    days = (nxt - today).days
    return {"next": nxt.strftime("%b %d"), "days": days, "pre_drift": days in (0, 1)}

def _risk_regime():
    score = 0; parts = []
    def _last(sym, period="1y"):
        try:
            return yf.Ticker(sym).history(period=period)["Close"].dropna()
        except Exception:
            return None
    spy = _last("SPY")
    if spy is not None and len(spy) >= 200:
        above = float(spy.iloc[-1]) > float(spy.rolling(200).mean().iloc[-1])
        score += 1 if above else -1
        parts.append(("SPY vs 200DMA", "above ✅" if above else "below ❌"))
    hyg = _last("HYG", "3mo"); lqd = _last("LQD", "3mo")
    if hyg is not None and lqd is not None and len(hyg) >= 21 and len(lqd) >= 21:
        ratio = (hyg / lqd).dropna()
        up = float(ratio.iloc[-1]) > float(ratio.iloc[-21])
        score += 1 if up else -1
        parts.append(("Credit HYG/LQD", "improving ✅" if up else "weakening ❌"))
    try:
        tnx = float(yf.Ticker("^TNX").history(period="5d")["Close"].iloc[-1])
        irx = float(yf.Ticker("^IRX").history(period="5d")["Close"].iloc[-1])
        curve = tnx - irx
        score += 1 if curve > 0 else -1
        parts.append(("Curve 10y-3m", f"{curve:+.2f} {'normal ✅' if curve > 0 else 'inverted ❌'}"))
    except Exception:
        pass
    try:
        vix = float(yf.Ticker("^VIX").history(period="5d")["Close"].iloc[-1])
        vix3 = float(yf.Ticker("^VIX3M").history(period="5d")["Close"].iloc[-1])
        contango = vix < vix3
        score += 1 if contango else -1
        parts.append(("VIX term", "contango ✅" if contango else "backwardation ❌"))
    except Exception:
        pass
    if score >= 2:
        label, emoji = "RISK-ON", "🟢"
    elif score <= -2:
        label, emoji = "RISK-OFF", "🔴"
    else:
        label, emoji = "NEUTRAL", "🟡"
    return {"score": score, "label": label, "emoji": emoji, "parts": parts}

def _fmt_regime():
    r = _risk_regime()
    f = _fomc_context()
    lines = [f"{r['emoji']} <b>RISK REGIME: {r['label']}</b>  (score {r['score']:+d})", ""]
    for k, v in r["parts"]:
        lines.append(f"• {k}: {v}")
    lines.append("")
    if r["label"] == "RISK-ON":
        lines.append("<i>Favors longs/breakouts; size up high-conviction signals.</i>")
    elif r["label"] == "RISK-OFF":
        lines.append("<i>Favors hedges/cash; fade rallies, cut risk, buy protection.</i>")
    else:
        lines.append("<i>Mixed — be selective, trade smaller.</i>")
    if f:
        if f["pre_drift"]:
            lines.append(f"📅 <b>Pre-FOMC drift window</b> (FOMC {f['next']}) — historically bullish into the meeting.")
        else:
            lines.append(f"📅 Next FOMC: {f['next']} ({f['days']}d).")
    return "\n".join(lines)

async def regime_command(update, ctx):
    """/regime - risk-on/off master read (breadth, credit, curve, VIX term) + FOMC."""
    await update.message.reply_text(_fmt_regime(), parse_mode=H)

def _kelly_fraction(win_rate, payoff):
    """Kelly f* = W - (1-W)/R. Returns fraction (0..1)."""
    try:
        w = float(win_rate); b = float(payoff)
        if b <= 0:
            return 0.0
        f = w - (1 - w) / b
        return max(0.0, min(f, 1.0))
    except Exception:
        return 0.0

def _earnings_signal(ticker):
    """PEAD: next earnings date/days + last surprise + drift lean."""
    out = {"next": None, "days_to": None, "surprise": None, "lean": "NEUTRAL"}
    try:
        tk = yf.Ticker(ticker)
        nd = None
        try:
            cal = tk.calendar
            if isinstance(cal, dict):
                ed = cal.get("Earnings Date")
                nd = (ed[0] if isinstance(ed, (list, tuple)) and ed else ed)
        except Exception:
            pass
        if nd is not None:
            try:
                nd2 = pd.Timestamp(nd).date()
                out["next"] = nd2.strftime("%b %d")
                out["days_to"] = (nd2 - datetime.now().date()).days
            except Exception:
                pass
        try:
            edf = tk.get_earnings_dates(limit=8)
            if edf is not None and len(edf):
                col = next((c for c in edf.columns if "Surprise" in c), None)
                if col:
                    past = edf.dropna(subset=[col])
                    if len(past):
                        out["surprise"] = float(past[col].iloc[0])
        except Exception:
            pass
        s = out["surprise"]
        if s is not None:
            out["lean"] = "BULLISH drift" if s > 2 else ("BEARISH drift" if s < -2 else "NEUTRAL")
    except Exception:
        return out
    return out

async def earnings_command(update, ctx):
    """/earnings TICKER - next earnings, last surprise, PEAD drift lean."""
    args = list(getattr(ctx, "args", []) or [])
    if not args:
        await update.message.reply_text("Usage: <code>/earnings NVDA</code>", parse_mode=H)
        return
    tk = args[0].upper()
    e = _earnings_signal(tk)
    lines = [f"📅 <b>{tk} EARNINGS (PEAD)</b>", ""]
    if e["next"]:
        lines.append(f"Next: <b>{e['next']}</b>" + (f" ({e['days_to']}d)" if e['days_to'] is not None else ""))
    if e["surprise"] is not None:
        lines.append(f"Last surprise: <b>{e['surprise']:+.1f}%</b> → {e['lean']}")
        lines.append("<i>PEAD: stocks tend to drift in the direction of the surprise for weeks.</i>")
    else:
        lines.append("<i>No recent surprise data.</i>")
    await update.message.reply_text("\n".join(lines), parse_mode=H)

async def mom_view(query):
    conn = get_conn()
    try:
        df, _asof = load_momentum_ranks(conn)
        if df is None or df.empty:
            await query.message.reply_text("⏳ Building universe momentum (~1 min, first run)…", parse_mode=H)
            await asyncio.get_event_loop().run_in_executor(None, compute_universe_momentum, True)
        try:
            hl = set(pd.read_sql("SELECT DISTINCT UPPER(ticker) tk FROM trades WHERE status='OPEN'", conn)["tk"].tolist())
        except Exception:
            hl = set()
        txt = _fmt_momentum_leaderboard(conn, n=8, highlight=hl)
    finally:
        conn.close()
    await query.message.reply_text(txt, parse_mode=H, reply_markup=_kb_momentum())

async def mom_recompute_view(query):
    await query.message.reply_text("⏳ Recomputing universe momentum…", parse_mode=H)
    await asyncio.get_event_loop().run_in_executor(None, compute_universe_momentum, True)
    conn = get_conn()
    try:
        try:
            hl = set(pd.read_sql("SELECT DISTINCT UPPER(ticker) tk FROM trades WHERE status='OPEN'", conn)["tk"].tolist())
        except Exception:
            hl = set()
        txt = _fmt_momentum_leaderboard(conn, n=8, highlight=hl)
    finally:
        conn.close()
    await query.message.reply_text(txt, parse_mode=H, reply_markup=_kb_momentum())

async def regime_view(query):
    await query.message.reply_text(_fmt_regime(), parse_mode=H, reply_markup=HUB_MENU_KB)


# ── Dedicated Vanna / Charm view ──
def _fmt_vanna_report(ticker, conn, spot):
    vc = _compute_vanna_charm(ticker, conn, spot)
    if not vc or not vc.get("vex"):
        return f"🌀 <b>{ticker} VANNA/CHARM</b>: no options data in DB."
    lines = [f"🌀 <b>{ticker} VANNA / CHARM</b>  spot ${spot:.2f}",
             f"<i>nearest expiry {vc.get('expiry','?')}</i>", "",
             f"Vanna exposure: <b>{vc['vex']/1e6:+.1f}M</b>",
             f"Charm exposure: <b>{vc['charm']/1e6:+.1f}M</b>", ""]
    lines.append("🌀 <b>Vanna:</b> " + vc["note"])
    lines.append("⏳ <b>Charm:</b> " + ("positive — dealer hedging adds upward drift into expiry (OpEx melt-up)."
                                        if vc["charm"] > 0 else
                                        "negative — charm flow pressures the downside into expiry."))
    return "\n".join(lines)

def _vanna_reports(conn, tickers=None):
    out = []
    for tk in (tickers or ["SPY"])[:6]:
        spot = _gex_spot(conn, tk)
        out.append(_fmt_vanna_report(tk, conn, spot))
    return out

async def vanna_command(update, ctx):
    """/vanna [TICKERS] - dealer vanna/charm exposure (blank = open positions)."""
    args = list(getattr(ctx, "args", []) or [])
    conn = get_conn()
    try:
        if args:
            tks = [a.upper() for a in args]
        else:
            try:
                df = pd.read_sql("SELECT DISTINCT ticker FROM trades WHERE status='OPEN'", conn)
                tks = [str(t).upper() for t in df["ticker"].tolist()] if not df.empty else ["SPY"]
            except Exception:
                tks = ["SPY"]
        msgs = _vanna_reports(conn, tks)
    finally:
        conn.close()
    await update.message.reply_text("\n\n".join(msgs), parse_mode=H, reply_markup=HUB_MENU_KB)

async def vanna_view(query):
    conn = get_conn()
    try:
        try:
            df = pd.read_sql("SELECT DISTINCT ticker FROM trades WHERE status='OPEN'", conn)
            tks = [str(t).upper() for t in df["ticker"].tolist()] if not df.empty else ["SPY"]
        except Exception:
            tks = ["SPY"]
        msgs = _vanna_reports(conn, tks)
    finally:
        conn.close()
    await query.message.reply_text("\n\n".join(msgs), parse_mode=H, reply_markup=HUB_MENU_KB)


# ═══════════════════════════════════════════════════════════
#  MAIN
# ═══════════════════════════════════════════════════════════
def main():
    _acquire_lock()
    token, chat_id = load_creds()
    log.info(f"Starting bot... Chat ID: {chat_id} (PID: {os.getpid()})")

    app = Application.builder().token(token).build()

    # Auto-open local dashboard URL on bot startup.
    open_dashboard_on_startup()

    # Commands
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("menu", cmd_start))
    app.add_handler(CommandHandler("opex", opex_command))
    app.add_handler(CommandHandler("event", event_command))
    app.add_handler(CommandHandler("briefing", briefing_command))
    app.add_handler(CommandHandler("plan", plan_command))
    app.add_handler(CommandHandler("wrap", wrap_command))
    app.add_handler(CommandHandler("tv", tv_command))
    app.add_handler(CommandHandler("journal", journal_command))
    app.add_handler(CommandHandler("logevent", logevent_command))
    app.add_handler(CommandHandler("bookmarks", bookmarks_command))
    app.add_handler(CommandHandler("gex", gex_command))
    app.add_handler(CommandHandler("macro", macro_command))
    app.add_handler(CommandHandler("momentum", momentum_command))
    app.add_handler(CommandHandler("regime", regime_command))
    app.add_handler(CommandHandler("earnings", earnings_command))
    app.add_handler(CommandHandler("vanna", vanna_command))
    app.add_handler(CommandHandler("squeeze", squeeze_command))

    # Button callbacks
    app.add_handler(CallbackQueryHandler(button_handler))

    # AI chat — plain text messages from the authorised chat
    from telegram.ext import MessageHandler, filters as tg_filters
    app.add_handler(MessageHandler(tg_filters.TEXT & ~tg_filters.COMMAND, ai_chat_handler))

    # Schedule morning alert at 9:00 AM (UTC-5 = 14:00 UTC)
    job_queue = app.job_queue
    if job_queue:
        from datetime import time as dt_time
        job_queue.run_daily(morning_alert, time=dt_time(14, 0, 0))  # 9 AM ET = 14:00 UTC
        job_queue.run_daily(briefing_alert, time=dt_time(14, 5, 0))  # daily brief 9:05 AM ET
        job_queue.run_daily(plan_alert, time=dt_time(13, 30, 0))     # next-day game plan ~8:30 AM ET pre-market
        job_queue.run_daily(wrap_alert, time=dt_time(21, 15, 0))     # daily market wrap ~4:15 PM ET post-close
        log.info("Scheduled morning alert at 9:00 AM ET daily")
        # 15-min intraday alert (fires every 15 min; function checks market hours internally)
        job_queue.run_repeating(intraday_alert, interval=900, first=30)
        log.info("Scheduled 15-min intraday OI alert")
        # 10-min position monitor (fires during market hours; deduplicates via bot_data state)
        job_queue.run_repeating(position_monitor, interval=600, first=60)
        job_queue.run_repeating(position_alerts, interval=300, first=90)
        log.info("Scheduled 5-min smart position alerts")
        log.info("Scheduled 10-min position health monitor")

    # Auto-close any positions that expired before bot started
    expired = _close_expired_positions()
    if expired:
        log.info(f"Startup: auto-closed {len(expired)} expired position(s): "
                 f"{', '.join(f'{tk}#{tid}' for tid,tk in expired)}")

    log.info("Bot is running! Press Ctrl+C to stop.")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
