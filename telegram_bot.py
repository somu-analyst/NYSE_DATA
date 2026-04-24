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

    for (priority, pnl, advice, tid, ot, st, entry, exp, qty, cur, pnl_pct, dte, side_s, em) in leg_advice:
        pnl_s = f"${pnl:+,.0f} ({pnl_pct:+.0f}%)"
        parts.append(
            f"{em} <b>#{tid} {ot} ${st:.0f} [{side_s}]</b>  DTE:{dte}\n"
            f"   Entry ${entry:.2f} → Now ${cur:.2f}  |  P&L <b>{pnl_s}</b>\n"
            f"   {advice}"
        )

    net_em = "🟢" if total_pnl >= 0 else "🔴"
    parts.append(f"\n{net_em} <b>Net P&L: ${total_pnl:+,.0f}</b>")

    btn_rows = [[InlineKeyboardButton("⬅️ Groups", callback_data="menu_groups"), BACK_BTN]]
    await query.message.reply_text("\n\n".join(parts), parse_mode=H, reply_markup=InlineKeyboardMarkup(btn_rows))
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
    await query.message.reply_text("\n".join(parts), parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(btn_rows))

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

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, Message, Bot
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, ContextTypes,
)
from telegram.constants import ParseMode
import re

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
    tk = yf.Ticker(ticker)
    hist = tk.history(period=f"{days}d")
    fig, ax = plt.subplots(figsize=(4, 2), dpi=100)
    ax.plot(hist.index, hist["Close"], color="#00aaff", linewidth=1.5)
    ax.fill_between(hist.index, hist["Close"], alpha=0.15, color="#00aaff")
    ax.set_title(ticker, fontsize=9, color="white", pad=3)
    ax.tick_params(axis="both", labelsize=6, colors="gray")
    ax.spines[:].set_visible(False)
    ax.set_facecolor("#111111")
    fig.patch.set_facecolor("#111111")
    plt.tight_layout(pad=0.4)
    buf = BytesIO()
    fig.savefig(buf, format="png", facecolor=fig.get_facecolor())
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
        pre_rows = []
        name_w = max(len(n) for n, _, _ in items)
        price_w = max((len(d["px_s"]) if d else 3) for _, d, _ in items)
        for name, d, tag in items:
            if d:
                arrow = _col_arrow(d["chg"])
                note = f"  {tag}" if tag else ""
                pre_rows.append(
                    f"{name:<{name_w}}  {d['px_s']:>{price_w}}  {arrow} {d['chg']:>+6.2f}%{note}")
            else:
                pre_rows.append(f"{name:<{name_w}}  {'N/A':>{price_w}}")
        colour_lines.append(f"<pre>{chr(10).join(pre_rows)}</pre>")
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
        for i, entry in enumerate(feed.entries[:10], 1):
            title = html_mod.escape(entry.get("title", ""))
            link = entry.get("link", "")
            tl = title.lower()
            if any(w in tl for w in _neg):
                tag = "🔴"
                color = "#ff4136"
                bear_c += 1
            elif any(w in tl for w in _pos):
                tag = "🟢"
                color = "#2ecc40"
                bull_c += 1
            else:
                tag = "🟡"
                color = "#ffb400"
            if link:
                news_lines.append(f'{tag} <a href="{link}"><b>{title}</b></a>')
            else:
                news_lines.append(f"{tag} <b>{title}</b>")
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
    await query.message.reply_text(sanitize_html("\n".join(parts)), parse_mode=H, reply_markup=kb,
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
                link = entry.get("link", "")
                tl = title.lower()
                if any(k in tl for k in _neg_kw):
                    tag = "🔴"
                elif any(k in tl for k in _pos_kw):
                    tag = "🟢"
                else:
                    tag = "🟡"
                all_items.append((tag, title, link))
        except Exception:
            continue
        if len(all_items) >= 15:
            break

    parts = [hdr("📰 MARKET HEADLINES")]
    if all_items:
        for tag, title, link in all_items[:12]:
            short = html_mod.escape(title[:85] + ("…" if len(title) > 85 else ""))
            if link:
                parts.append(f'{tag} <a href="{link}">{short}</a>')
            else:
                parts.append(f"{tag} {short}")
    else:
        parts.append("Could not fetch market headlines.")

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Refresh", callback_data="news_ALL"),
         InlineKeyboardButton("📰 By Ticker", callback_data="menu_news"), BACK_BTN]
    ])
    try: await _loading.delete()
    except Exception: pass
    await query.message.reply_text(
        "\n".join(parts), parse_mode=H, reply_markup=kb, disable_web_page_preview=True)


# ═══════════════════════════════════════════════════════════
#  3) EXIT PLANNER (MC simulation)
# ═══════════════════════════════════════════════════════════
async def exit_planner_menu(query):
    """Show open positions from DB to analyze"""
    conn = get_conn()
    open_trades = pd.read_sql("SELECT * FROM trades WHERE status='OPEN'", conn)
    conn.close()

    if open_trades.empty:
        # Show ticker picker for manual analysis
        tickers = ["GOOG", "AMZN", "MSFT", "NVDA", "AAPL", "TSLA"]
        _def_exp = (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d")
        btns = [[InlineKeyboardButton(f"🎯 {t}", callback_data=f"exitmc|{t}|call|0|0|{_def_exp}") for t in tickers[:3]]]
        btns.append([InlineKeyboardButton(f"🎯 {t}", callback_data=f"exitmc|{t}|call|0|0|{_def_exp}") for t in tickers[3:]])
        btns.append([BACK_BTN])
        await query.message.reply_text(
            f"{hdr('🎯 EXIT PLANNER')}\n\nNo open positions. Use the unified dashboard to add trades.\nOr pick a ticker for quick analysis:",
            parse_mode=H, reply_markup=InlineKeyboardMarkup(btns),
        )
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
        label = f"🎯 {tk} {ot.upper()} ${st:.0f} [{side_s}] (entry ${ep:.2f})"
        # Use | as separator — safe with dates and decimals
        data = f"exitmc|{tk}|{ot}|{st}|{ep}|{ex}|{qty}"
        btns.append([InlineKeyboardButton(label, callback_data=data)])
    btns.append([BACK_BTN])
    await query.message.reply_text(
        f"{hdr('🎯 EXIT PLANNER')}\n\nSelect a position to analyze:",
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
    await query.message.reply_text(msg, parse_mode=H, reply_markup=kb)
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
    await query.message.reply_text("\n".join(parts), parse_mode=H, reply_markup=kb)

# ═══════════════════════════════════════════════════════════
#  4) MY POSITIONS — card-style per trade
# ═══════════════════════════════════════════════════════════
async def positions_view(query):
    _close_expired_positions()   # auto-close anything past expiry before showing
    conn = get_conn()
    trades = pd.read_sql("SELECT * FROM trades WHERE status='OPEN' ORDER BY created_at DESC LIMIT 20", conn)
    conn.close()

    if trades.empty:
        await query.message.reply_text(
            f"{hdr('💼 OPEN POSITIONS')}\n\nNo open positions found.",
            parse_mode=H, reply_markup=InlineKeyboardMarkup([[BACK_BTN]]))
        return

    parts = [hdr("💼 OPEN POSITIONS")]
    open_rows = []
    tbl_rows = [f"{'#':<3} {'Tkr':<5} {'Tp':<4} {'Stk':>4} {'Ent':>5}"]
    tbl_rows.append("─" * 25)
    for _, tr in trades.iterrows():
        tid = _safe_int(tr.get("trade_id", 0), 0)
        tk = str(tr.get("ticker", "?"))[:5]
        ot = str(tr.get("option_type", "?"))[:3].upper()
        st = _safe_float(tr.get("strike", 0), 0)
        ep = _safe_float(tr.get("entry_price", 0), 0)
        qty = _safe_int(tr.get("quantity", 0), 0)
        side_s = "B" if qty >= 0 else "S"
        gid = tr.get("group_id")
        g_mark = f"G{int(gid)}" if gid and pd.notna(gid) else f"#{tid}"
        combo = f"{side_s}{ot}"   # e.g. BPUT / BCAL / SPUT / SCAL
        tbl_rows.append(f"{g_mark:<3} {tk:<5} {combo:<4} {st:>4.0f} {ep:>5.2f}")
        open_rows.append(tr)
    parts.append(mono("\n".join(tbl_rows)))
    parts.append(f"\n📋 <b>{len(open_rows)} open positions</b>")

    btn_rows = []
    for tr in open_rows[:8]:
        tid = _safe_int(tr.get("trade_id", 0), 0)
        tk = str(tr.get("ticker", "?"))
        ot = str(tr.get("option_type", "?")).upper()
        st = _safe_float(tr.get("strike", 0), 0)
        gid = tr.get("group_id")
        label = f"🛠 #{tid} {tk} {ot} ${st:.0f}"
        if gid and pd.notna(gid):
            label = f"📦 G{int(gid)} · " + label
        btn_rows.append([InlineKeyboardButton(label, callback_data=f"pos_{tid}")])

    btn_rows.append([InlineKeyboardButton("➕ Add Position", callback_data="posadd_start")])
    btn_rows.append([InlineKeyboardButton("📦 Position Groups", callback_data="menu_groups")])
    btn_rows.append([InlineKeyboardButton("🎨 Strategy Builder", callback_data="menu_strategy_builder")])
    btn_rows.append([InlineKeyboardButton("🤖 MiroFish Signals", callback_data="menu_mirofish")])
    btn_rows.append([InlineKeyboardButton("🔄 Refresh", callback_data="menu_positions"), BACK_BTN])
    kb = InlineKeyboardMarkup(btn_rows)
    await query.message.reply_text("\n".join(parts), parse_mode=H, reply_markup=kb)


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
    await query.message.reply_text("\n".join(msg), parse_mode=H, reply_markup=kb)


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
    await query.message.reply_text("\n".join(parts), parse_mode=H, reply_markup=InlineKeyboardMarkup(btn_rows))


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
    
    await query.message.reply_text("\n".join(parts), parse_mode=H, reply_markup=InlineKeyboardMarkup(btn_rows))


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
    
    await query.message.reply_text("\n".join(parts), parse_mode=H, reply_markup=InlineKeyboardMarkup(btn_rows))


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
    await query.message.reply_text("\n".join(parts), parse_mode=H, reply_markup=InlineKeyboardMarkup(rows))


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
    rows_txt = [f"{'Leg':<4} {'Type':<5} {'Side':<5} {'Strike':>6} {'Px':>5} {'Cost':>8}"]
    rows_txt.append("─" * 38)
    for i, l in enumerate(done):
        side_lbl = "BUY" if l["qty"] > 0 else "SELL"
        rows_txt.append(
            f"L{i+1:<3} {l['opt_type']:<5} {side_lbl:<5} ${l['strike']:>5.0f} "
            f"${l['entry_price']:>4.2f} {l['cost']:>+8.0f}"
        )
    rows_txt.append("─" * 38)
    rows_txt.append(f"{'Net Cost/Credit':>30} {net_cost:>+8.0f}")
    parts.append(mono("\n".join(rows_txt)))

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

    await query.message.reply_text("\n".join(parts), parse_mode=H, reply_markup=InlineKeyboardMarkup(btns))


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
    await query.message.reply_text("\n".join(parts), parse_mode=H, reply_markup=kb)


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
    await query.message.reply_text("\n".join(parts), parse_mode=H, reply_markup=kb)



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

    if df.empty:
        return ""

    # Aggregate PCR for hedge detection
    tot_call_oi = df["call_oi"].sum()
    tot_put_oi  = df["put_oi"].sum()
    agg_pcr = tot_put_oi / tot_call_oi if tot_call_oi > 0 else 1.0

    lines = []
    for _, r in df.iterrows():
        expl = _explain_oi_flow(
            float(r["call_chg"] or 0), float(r["put_chg"] or 0),
            float(r["call_oi"] or 0),  float(r["put_oi"] or 0),
            spot, float(r["strike"] or 0), agg_pcr
        )
        lines.append(f"  • {expl}")
    return "\n".join(lines)


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

    lines = []
    if len(week_dates) >= 2:
        lines.append(f"  1W: {_trend(wc, wp)} [C:{_fk_static(wc)} P:{_fk_static(wp)}]")
    if len(month_dates) >= 5:
        lines.append(f"  1M: {_trend(mc, mp)} [C:{_fk_static(mc)} P:{_fk_static(mp)}]")
    return "\n".join(lines)



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
        [BACK_BTN],
    ])
    await query.message.reply_text("\n".join(parts), parse_mode=H, reply_markup=kb)



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
    await query.message.reply_text("\n".join(parts), parse_mode=H, reply_markup=kb)


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
              "notional": {}, "put_skew": {}, "pin_risk": []}

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
    mean_oi = by_s["total_oi"].mean()
    walls = by_s[by_s["total_oi"] >= mean_oi * 2.0].sort_values("total_oi", ascending=False).head(6)
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

    # ── 5. PUT SKEW (Fear Gauge) ──
    # Equidistant OTM: ~5% above vs ~5% below spot.
    # put_price / call_price ratio = how much extra institutions pay for downside protection.
    # Iterates expiries (nearest first) and uses the first one with tradeable prices (call >= $0.50).
    # Near-expiry calls often price at $0.01 — skip those to get a meaningful ratio.
    spot = result["notional"].get("avg_spot", 0) if result["notional"] else 0
    if spot <= 0:
        spot = float(df["strike"].median())
    if spot > 0:
        for exp_sort_val in sorted(df["expiry_sort"].unique()):
            exp_df = df[df["expiry_sort"] == exp_sort_val].copy()
            exp_df["c_dist"] = (exp_df["strike"] - spot * 1.05).abs()
            exp_df["p_dist"] = (exp_df["strike"] - spot * 0.95).abs()
            if exp_df.empty:
                continue
            cr = exp_df.nsmallest(1, "c_dist").iloc[0]
            pr = exp_df.nsmallest(1, "p_dist").iloc[0]
            c_px = float(cr["lastPrice_Call_now"])
            p_px = float(pr["lastPrice_Put_now"])
            if c_px >= 0.50 and p_px > 0:
                skew = round(p_px / c_px, 2)
                fear = ("EXTREME FEAR" if skew > 3.0 else
                        "HIGH FEAR"    if skew > 2.0 else
                        "ELEVATED"     if skew > 1.2 else
                        "NORMAL"       if skew > 0.8 else
                        "LOW (COMPLACENCY)" if skew > 0.5 else "INVERTED")
                result["put_skew"] = {
                    "call_strike": float(cr["strike"]), "put_strike": float(pr["strike"]),
                    "call_px": c_px, "put_px": p_px, "skew": skew, "fear": fear,
                    "expiry": str(exp_df["expiry_date"].iloc[0]),
                }
                break  # found a usable expiry

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
        rows = []
        for mp in mp_list[:4]:
            dte_s = f"DTE {mp['dte']}" if mp.get("dte") is not None else ""
            dist = ""
            if spot > 0:
                d_pct = (spot - mp["strike"]) / spot * 100
                dist = f"vs spot {d_pct:+.1f}%"
            rows.append(f"  {mp['expiry'][:8]}  ${mp['strike']:.0f}  {dte_s}")
            if dist:
                rows.append(f"    {dist}")
        parts.append(mono("\n".join(rows)))
        parts.append("<i>Fade moves away from max pain as expiry nears</i>")

    # 2. Gamma Walls
    walls = sig.get("gamma_walls", [])
    if walls:
        parts.append("\n<b>GAMMA WALLS  (Dealer Hedging Levels)</b>")
        rows = []
        for w in walls[:5]:
            label = "CEILING" if w["type"] == "CALL" else ("FLOOR" if w["type"] == "PUT" else "WALL")
            tot_k = f"{w['total_oi']/1000:.0f}K" if w['total_oi'] >= 1000 else str(w['total_oi'])
            c_k   = f"{w['call_oi']/1000:.0f}K"  if w['call_oi']  >= 1000 else str(w['call_oi'])
            p_k   = f"{w['put_oi']/1000:.0f}K"   if w['put_oi']   >= 1000 else str(w['put_oi'])
            rows.append(f"  ${w['strike']:.0f}  {label}  tot:{tot_k}")
            rows.append(f"    C:{c_k}  P:{p_k}")
        parts.append(mono("\n".join(rows)))
        parts.append("<i>Price gravitates toward / stalls at these strikes</i>")

    # 3. Smart Money Flow
    sf = sig.get("smart_flow", {})
    if sf:
        parts.append("\n<b>SMART MONEY FLOW</b>")
        def _fk_sf(n):
            a = abs(n); s = "+" if n >= 0 else "-"
            if a >= 1_000_000: return f"{s}{a/1_000_000:.1f}M"
            if a >= 1_000: return f"{s}{a/1_000:.0f}K"
            return f"{s}{a:.0f}"
        rows = [
            "  CALLS  OI:{}  vol:{}".format(
                _fk_sf(sf.get("call_oi_chg", 0)), _fk_sf(sf.get("call_vol", 0))),
            "         {}".format(sf.get("call_verdict", "")),
            "  PUTS   OI:{}  vol:{}".format(
                _fk_sf(sf.get("put_oi_chg", 0)), _fk_sf(sf.get("put_vol", 0))),
            "         {}".format(sf.get("put_verdict", "")),
        ]
        parts.append(mono("\n".join(rows)))
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
        parts.append("\n<b>NOTIONAL CONVICTION  (Dollar Weight)</b>")
        rows = [
            "  Call:  ${:.1f}M  Bull:{:,.0f}".format(nt.get("call_m", 0), nt.get("bull_score", 0)),
            "  Put:   ${:.1f}M  Bear:{:,.0f}".format(nt.get("put_m", 0), nt.get("bear_score", 0)),
            "  Net:   ${:+.1f}M  ratio:{:.2f}x".format(nt.get("net_m", 0), nt.get("ratio", 0)),
        ]
        parts.append(mono("\n".join(rows)))
        parts.append(f"<i>Dollar bias: <b>{nt.get('bias', '')}</b></i>")

    # 5. Put Skew
    ps = sig.get("put_skew", {})
    if ps:
        parts.append("\n<b>PUT SKEW  (Institutional Fear Gauge)</b>")
        rows = [
            "  Exp:  {}".format(ps.get("expiry", "")),
            "  Call ~5%OTM: ${:.0f}  px ${:.2f}".format(
                ps.get("call_strike", 0), ps.get("call_px", 0)),
            "  Put  ~5%OTM: ${:.0f}  px ${:.2f}".format(
                ps.get("put_strike", 0), ps.get("put_px", 0)),
            "  Skew: {:.2f}x  [{}]".format(ps.get("skew", 0), ps.get("fear", "")),
        ]
        parts.append(mono("\n".join(rows)))
        fear = ps.get("fear", "")
        if "EXTREME" in fear or "HIGH" in fear:
            hint = "Heavy put-premium demand — institutions hedging longs; often near bottoms"
        elif "COMPLACENCY" in fear or "INVERTED" in fear:
            hint = "Cheap puts — complacency or call blow-off; watch for reversal"
        else:
            hint = "Normal cost of protection"
        parts.append(f"<i>{hint}</i>")

    # 6. Pin Risk
    pins = sig.get("pin_risk", [])
    if pins:
        parts.append("\n<b>PIN RISK  (DTE \u2264 7)</b>")
        rows = []
        for pin in pins[:4]:
            oi_k = f"{pin['total_oi']//1000}K" if pin['total_oi'] >= 1000 else str(pin['total_oi'])
            rows.append("  ${:.0f}  {}  DTE{}  OI:{}".format(
                pin["strike"], pin["expiry"][:5], pin["dte"], oi_k))
        parts.append(mono("\n".join(rows)))
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
    await query.message.reply_text("\n".join(parts), parse_mode=H, reply_markup=kb)


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
            SELECT trade_date, net_oi, call_oi, put_oi
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
        bar = "\u2588" * min(int(abs(z) * 2), 10)
        parts.append("\n<b>PCR Z-SCORE  ({}d lookback)</b>".format(pz["lookback"]))
        rows = [
            "  Today PCR:  {:.3f}".format(pz["today"]),
            "  {}d mean:   {:.3f}   std: {:.3f}".format(pz["lookback"], pz["mean"], pz["std"]),
            "  Z-score:   {:+.2f}  [{}]  {}".format(z, bar, pz["level"]),
        ]
        parts.append(mono("\n".join(rows)))
        parts.append("<i>{}</i>".format(pz["action"]))

    # 2. Price Z-Score
    prz = sig.get("price_z", {})
    if prz:
        any_data = True
        parts.append("\n<b>PRICE Z-SCORE  (20d lookback)</b>")
        rows = [
            "  Today:    ${:.2f}".format(prz["today"]),
            "  20d mean: ${:.2f}   std: ${:.2f}".format(prz["mean20"], prz["std20"]),
            "  Z-score:  {:+.2f}  [{}]".format(prz["z"], prz["level"]),
            "  Target:   ${:.2f}   Stop: ${:.2f}".format(prz["target1"], prz["stop"]),
        ]
        parts.append(mono("\n".join(rows)))

    # 3. PCR Trend
    pt = sig.get("pcr_trend", {})
    if pt:
        any_data = True
        last5 = " \u2192 ".join(str(x) for x in pt["last5"])
        parts.append("\n<b>PCR TREND  (5-day rolling)</b>")
        rows = [
            "  5d avg: {:.3f}   Today: {:.3f}   ({:+.1f}%)".format(
                pt["avg5"], pt["today"], pt["pct_chg"]),
            "  Trend:  {}".format(pt["trend"]),
            "  Last 5: {}".format(last5),
        ]
        parts.append(mono("\n".join(rows)))
        if "SPIKE" in pt["trend"]:
            parts.append("<i>Sudden spike -- may be expiry distortion or event hedge</i>")

    # 4. Net OI Extreme
    oi = sig.get("oi_extreme", {})
    if oi:
        any_data = True
        parts.append("\n<b>NET OI EXTREME  (20d lookback)</b>")
        rows = [
            "  Net OI today:  {:>+12,}".format(oi["net_oi_today"]),
            "  20d mean:      {:>+12,}".format(oi["net_oi_mean"]),
            "  Z-score:       {:>+12.2f}  [{}]".format(oi["z"], oi["level"]),
            "  Call OI: {:>9,}   Put OI: {:>9,}".format(oi["call_oi"], oi["put_oi"]),
        ]
        parts.append(mono("\n".join(rows)))
        if "PEAK" in oi["level"]:
            note = ("Too many puts -- peak bearish positioning, contrarian BUY zone"
                    if "BEARISH" in oi["level"] else
                    "Too many calls -- peak bullish positioning, contrarian SELL zone")
            parts.append("<i>{}</i>".format(note))

    # 5. Composite
    comp = sig.get("composite", {})
    if comp:
        any_data = True
        sc    = comp["score"]
        arrow = "\u25b2" if sc > 0 else "\u25bc"
        parts.append("\n<b>COMPOSITE MEAN REVERSION SCORE</b>")
        rows = ["  Score:  {:+.2f}  {}  [{}]".format(sc, arrow, comp["level"])]
        if comp["factors"]:
            rows.append("  Inputs: {}".format(",  ".join(comp["factors"])))
        parts.append(mono("\n".join(rows)))
        if comp["action"]:
            parts.append("<b>Trade idea:</b>  {}".format(comp["action"]))

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
    await query.message.reply_text("\n".join(parts), parse_mode=H, reply_markup=kb)


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
    await query.message.reply_text("\n".join(parts), parse_mode=H, reply_markup=kb)


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
    await query.message.reply_text(msg, parse_mode=H, reply_markup=kb)


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
    await query.message.reply_text(msg, parse_mode=H, reply_markup=kb)


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
    _oi_hdrs = ["Ticker", "Call OI", "Put OI", "PCR"]
    _oi_data = []
    for _, r in top_oi.iterrows():
        _oi_data.append([
            str(r['ticker']),
            _fk_oi(r['total_call_oi']),
            _fk_oi(r['total_put_oi']),
            f"{min(float(r['pcr'] or 0), 9.99):.2f}",
        ])
    _oi_w = [max(len(_oi_hdrs[i]), max(len(row[i]) for row in _oi_data)) for i in range(len(_oi_hdrs))]
    oi_rows  = [" | ".join(_oi_hdrs[i].ljust(_oi_w[i]) for i in range(len(_oi_hdrs)))]
    oi_rows += ["-+-".join("-" * w for w in _oi_w)]
    for row in _oi_data:
        oi_rows.append(" | ".join(row[i].ljust(_oi_w[i]) for i in range(len(row))))
    parts.append("\n<b>Top by Open Interest</b>\n" + mono("\n".join(oi_rows)))

    # Highest PCR — same dynamic-width approach
    high_pcr = df[df["pcr"] > 0].nlargest(5, "pcr")
    if not high_pcr.empty:
        _pcr_hdrs = ["Ticker", "PCR", "Bias"]
        _pcr_data = []
        for _, r in high_pcr.iterrows():
            bias = "Bearish" if r["pcr"] > 1.3 else ("Bullish" if r["pcr"] < 0.7 else "Neutral")
            _pcr_data.append([str(r['ticker']), f"{min(float(r['pcr'] or 0), 9.99):.2f}", bias])
        _pcr_w = [max(len(_pcr_hdrs[i]), max(len(row[i]) for row in _pcr_data)) for i in range(len(_pcr_hdrs))]
        pcr_rows  = [" | ".join(_pcr_hdrs[i].ljust(_pcr_w[i]) for i in range(len(_pcr_hdrs)))]
        pcr_rows += ["-+-".join("-" * w for w in _pcr_w)]
        for row in _pcr_data:
            pcr_rows.append(" | ".join(row[i].ljust(_pcr_w[i]) for i in range(len(row))))
        parts.append("\n<b>Highest Put/Call Ratio</b>\n" + mono("\n".join(pcr_rows)))

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
    await query.message.reply_text("\n".join(parts), parse_mode=H, reply_markup=InlineKeyboardMarkup(btns))

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
            if iv_pct:
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
    await query.message.reply_text(msg, parse_mode=H, reply_markup=kb)


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
        parts.append("\n🟢 <b>Biggest Call OI Increases</b>")
        rows = [f"{'ST':<3} {'Ticker':<6} {'Call OI Chg':>11} {'PCR':>5}"]
        rows.append("─" * 29)
        for _, r in top_call_gain.iterrows():
            rows.append(f"[B] {r['ticker']:<6} {r['call_chg']:>+11,.0f} {r['pcr_2']:>5.2f}")
        parts.append(mono("\n".join(rows)))

    # Biggest put OI increases
    top_put_gain = merged.nlargest(5, "put_chg")
    if not top_put_gain.empty:
        parts.append("\n🔴 <b>Biggest Put OI Increases</b>")
        rows = [f"{'ST':<3} {'Ticker':<6} {'Put OI Chg':>10} {'PCR':>5}"]
        rows.append("─" * 28)
        for _, r in top_put_gain.iterrows():
            rows.append(f"[S] {r['ticker']:<6} {r['put_chg']:>+10,.0f} {r['pcr_2']:>5.2f}")
        parts.append(mono("\n".join(rows)))

    # PCR changes
    top_pcr_inc = merged.dropna(subset=["pcr_chg"]).nlargest(5, "pcr_chg")
    if not top_pcr_inc.empty:
        parts.append("\n📈 <b>Biggest PCR Increases (More Bearish)</b>")
        rows = [f"{'Ticker':<6} {'PCR Δ':>6} {'New PCR':>7}"]
        rows.append("─" * 22)
        for _, r in top_pcr_inc.iterrows():
            rows.append(f"{r['ticker']:<6} {r['pcr_chg']:>+6.2f} {r['pcr_2']:>7.2f}")
        parts.append(mono("\n".join(rows)))
    
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔀 New Compare", callback_data="oi_compare_select1")],
        [InlineKeyboardButton("📊 OI Menu", callback_data="menu_oi"), BACK_BTN]
    ])
    await query.message.reply_text("\n".join(parts), parse_mode=H, reply_markup=kb)


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

    try:
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
                2, 1, subplot_spec=gridspec.GridSpec(n, 1)[idx],
                height_ratios=[3, 1.4], hspace=0.08)
            ax_main  = fig.add_subplot(gs[0])
            ax_delta = fig.add_subplot(gs[1])

            if df_eod.empty:
                ax_main.text(0.5, 0.5, f"No EOD data for {expiry}", ha="center", va="center")
                ax_main.set_title(f"{ticker}  {expiry}  -- No EOD Data")
                ax_delta.set_visible(False)
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
                continue
            wd = (strikes[1] - strikes[0]) * 0.4

            if spot and len(df):
                df, sig, sig_col, sig_desc, dets = _oi_intent_algo(df, spot)
            else:
                sig, sig_col, sig_desc, dets = "N/A", "#455A64", "Spot unavailable", {}
                df["bar_col"] = "#90A4AE"
                df["intent"]  = "NEUTRAL"

            # Top panel
            ax_main.bar(strikes - wd/2, df["openInt_Call_eod"], wd*0.9, alpha=0.25, color="#43A047", label=f"Call EOD {eod_date}")
            ax_main.bar(strikes - wd/2, -df["openInt_Put_eod"], wd*0.9, alpha=0.25, color="#E53935", label=f"Put EOD {eod_date}")
            ax_main.bar(strikes + wd/2, df["openInt_Call"],     wd*0.9, alpha=0.75, color="#1B5E20", label="Call LIVE")
            ax_main.bar(strikes + wd/2, -df["openInt_Put"],     wd*0.9, alpha=0.75, color="#B71C1C", label="Put LIVE")
            ax_main.axhline(0, color="#212121", linewidth=0.8)

            if spot:
                ax_main.axvspan(spot*0.97, spot*1.03, alpha=0.08, color="yellow", label="ATM +/-3%")
                ax_main.axvline(spot, color="#FFD600", linewidth=1.4, linestyle="--", label=f"Spot ${spot:.1f}")
                ax_main.axvspan(0, spot*0.90, alpha=0.04, color="#1565C0")

            ax_main.set_title(f"{ticker}  |  Expiry: {expiry}  |  LIVE vs EOD {eod_date}", fontsize=11, fontweight="bold")
            ax_main.set_ylabel("Open Interest")
            ax_main.set_xlim(strikes[0] - wd * 2.5, strikes[-1] + wd * 2.5)
            ax_main.legend(loc="upper left", fontsize=7, ncol=2, framealpha=0.75)
            ax_main.yaxis.set_major_formatter(
                plt.FuncFormatter(lambda x, _: f"{abs(x)/1000:.0f}K" if abs(x) >= 1000 else f"{x:.0f}"))
            ax_main.grid(True, alpha=0.25, axis="y")
            ax_main.tick_params(labelbottom=False)

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

            # Bottom delta panel
            _PL = {"BULLISH":"#A5D6A7","BEARISH":"#FFCDD2","STRADDLE":"#CE93D8",
                   "NEAR_BEARISH":"#FFCCBC","HEDGE":"#BBDEFB","HEDGE_UNWIND":"#E3F2FD",
                   "BULLISH_BREAK":"#C8E6C9","COVERED_CALL":"#FFF9C4","UNWIND":"#EEEEEE","NEUTRAL":"#ECEFF1"}
            for s, cd, pd_, col, intent in zip(
                    strikes, df["call_oi_change"], df["put_oi_change"], df["bar_col"], df["intent"]):
                ax_delta.bar(s - wd/2, cd,    wd*0.9, color=col,                 alpha=0.85)
                ax_delta.bar(s + wd/2, -pd_,  wd*0.9, color=_PL.get(intent,"#ECEFF1"), alpha=0.85)

            ax_delta.axhline(0, color="#212121", linewidth=0.8)
            if spot:
                ax_delta.axvspan(spot*0.97, spot*1.03, alpha=0.10, color="yellow")
                ax_delta.axvline(spot, color="#FFD600", linewidth=1.2, linestyle="--")
            _step_d = max(1, len(strikes) // 14)
            ax_delta.set_xticks(strikes[::_step_d])
            ax_delta.set_xticklabels([f"${s:.0f}" for s in strikes[::_step_d]],
                                     rotation=45, ha='right', fontsize=7)
            ax_delta.set_xlim(strikes[0] - wd * 2.5, strikes[-1] + wd * 2.5)
            ax_delta.set_xlabel("Strike Price", fontsize=8)
            ax_delta.set_ylabel("OI Delta", fontsize=8)
            ax_delta.yaxis.set_major_formatter(
                plt.FuncFormatter(lambda x, _: f"{abs(x)/1000:.0f}K" if abs(x) >= 1000 else f"{x:.0f}"))
            ax_delta.grid(True, alpha=0.2, axis="y")
            _IC = {"BULLISH":"#2E7D32","BEARISH":"#C62828","HEDGE":"#1565C0",
                   "NEAR_BEARISH":"#BF360C","STRADDLE":"#6A1B9A",
                   "COVERED_CALL":"#F57F17","BULLISH_BREAK":"#388E3C","UNWIND":"#757575"}
            ax_delta.legend(handles=[mpatches.Patch(color=c, label=l) for l,c in _IC.items()],
                            loc="lower right", fontsize=6, ncol=4, framealpha=0.8)

        plt.tight_layout(pad=1.5)
        buf = BytesIO()
        fig.savefig(buf, format="png", dpi=110, bbox_inches="tight")
        plt.close(fig)
        buf.seek(0)
        conn.close()
        return buf

    except Exception as e:
        log.error(f"Live chart error for {ticker}: {e}", exc_info=True)
        try: plt.close(fig)
        except Exception: pass
        conn.close()
        return None


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
    
    # Generate chart
    chart_buf = _generate_live_vs_eod_chart(ticker, live_data, eod_date)
    
    if chart_buf is None:
        await query.message.reply_text(
            f"❌ Failed to generate chart for {ticker}.",
            reply_markup=InlineKeyboardMarkup([[BACK_BTN]])
        )
        try: await _loading.delete()
        except Exception: pass
        return
    
    # Send chart
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

    if _strike_parts:
        parts.append("\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        parts.append("📊 <b>STRIKE-LEVEL OI ANALYSIS</b>")
        parts.extend(_strike_parts)

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Refresh", callback_data="menu_signals"),
         InlineKeyboardButton("🤖 MiroFish", callback_data="menu_mirofish")],
        [BACK_BTN]
    ])
    await query.message.reply_text("\n".join(parts), parse_mode=H, reply_markup=kb)
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
    await query.message.reply_text("\n".join(parts), parse_mode=H, reply_markup=kb)

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
    await query.message.reply_text("\n".join(parts), parse_mode=H, reply_markup=kb)


# ═══════════════════════════════════════════════════════════
#  8b) EXTRA FEATURES (from extended dashboard updates)
# ═══════════════════════════════════════════════════════════
async def more_features_menu(query):
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🏦 Prop Trading", callback_data="menu_prop"),
         InlineKeyboardButton("📈 Backtest Lab", callback_data="menu_backtest")],
        [InlineKeyboardButton("🔮 Live Predictor", callback_data="menu_livepred"),
         InlineKeyboardButton("🐋 Whale Holdings", callback_data="menu_whales")],
        [InlineKeyboardButton("🖥 Dashboard URL", callback_data="menu_streamlit_link")],
        [InlineKeyboardButton("📡 Market Analytics", callback_data="menu_analytics"),
         InlineKeyboardButton("🌍 Global Market", callback_data="menu_global_market")],
        [InlineKeyboardButton("📊 NYSE Daily Report", callback_data="menu_nyse_report"),
         InlineKeyboardButton("🤖 MiroFish Signals", callback_data="menu_mirofish")],
        [InlineKeyboardButton("🌙 AH Predictor", callback_data="menu_aftermarket_predict"),
         InlineKeyboardButton("⚠️ Overnight Risk", callback_data="menu_overnight_risk")],
        [InlineKeyboardButton("🎲 Monte Carlo Sim", callback_data="menu_exit")],
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
        _oi_cw  = [max(len(_oi_hdr[i]), max((len(r[i]) for r in _oi_rows), default=0)) for i in range(5)]
        _oi_jn  = lambda i, v: v.rjust(_oi_cw[i]) if i in _oi_RGHT else v.ljust(_oi_cw[i])
        _oi_sep = "-+-".join("-" * w for w in _oi_cw)
        _oi_lines = [f"OI FLOW  {latest_date}",
                     " | ".join(_oi_jn(i, _oi_hdr[i]) for i in range(5)), _oi_sep]
        for r in _oi_rows:
            _oi_lines.append(" | ".join(_oi_jn(i, r[i]) for i in range(5)))
        parts.append("<pre>" + "\n".join(_oi_lines) + "</pre>")

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
            _t_cw   = [max(len(_t_hdr[i]), max(len(r[i]) for r in _t_data)) for i in range(7)]
            _t_jn   = lambda i, v: v.rjust(_t_cw[i]) if i in _t_RGHT else v.ljust(_t_cw[i])
            _t_sep  = "-+-".join("-" * w for w in _t_cw)
            _t_out  = ["TECH SIGNALS  RSI/MACD/BB/EMA",
                       " | ".join(_t_jn(i, _t_hdr[i]) for i in range(7)), _t_sep]
            for r in _t_data:
                _t_out.append(" | ".join(_t_jn(i, r[i]) for i in range(7)))
            parts.append("<pre>" + "\n".join(_t_out) + "</pre>")
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
    await query.message.reply_text("\n".join(parts), parse_mode=H, reply_markup=kb)


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

    html_cards = []

    for (em, tk, otype, strike, entry, cur_px, pnl_pct, pnl, dte_s, prob_s, oi_s, action) in rows:
        buy_s = f"${entry:.2f}"  if entry  < 100 else f"${entry:.0f}"
        cur_s = f"${cur_px:.2f}" if cur_px < 100 else f"${cur_px:.0f}"
        pct_s = f"{pnl_pct:+.1f}%"
        pnl_s = f"${pnl:+,.0f}"
        a_em  = _action_em.get(action, "✅")
        advice = _action_advice.get(action, "Monitor position.")

        # DTE urgency note
        dte_num = int(dte_s[1:]) if dte_s.startswith("D") and dte_s[1:].isdigit() else None
        if dte_num is not None and dte_num <= 3:
            dte_note = f" ⚠️ <b>Only {dte_num} days left!</b>"
        elif dte_num is not None and dte_num <= 7:
            dte_note = f" ({dte_num}d to expiry)"
        else:
            dte_note = f" ({dte_s} to expiry)"

        # OI alignment note
        oi_note = f"  OI: <i>{oi_s}</i>" if oi_s and oi_s != "?" else ""

        side_word = "Sold" if entry < 0 else "Bought"

        html_cards.append(
            f"{em} <b>{tk} {otype} ${int(strike)}</b>{dte_note}\n"
            f"   Entered {buy_s}  →  Now {cur_s}  |  <b>{pct_s} ({pnl_s})</b>\n"
            f"   Win probability: {prob_s}{oi_note}\n"
            f"   {a_em} <b>{action}</b> — {advice}"
        )

    colour_section = "\n\n".join(html_cards)

    urgent_section = ""
    if urgent_lines:
        urgent_section = "\n\n<b>⚡ ACTION REQUIRED</b>\n" + "\n".join(urgent_lines)

    net_em  = "🟢" if total_pnl >= 0 else "🔴"
    n_pos   = len(html_cards)
    footer  = f"\n{net_em} <b>Portfolio total: ${total_pnl:+,.0f}</b>  ({n_pos} open position{'s' if n_pos != 1 else ''})"

    full_msg = (
        f"{hdr(f'💼 POSITIONS · {now_s}')}\n\n"
        + colour_section
        + urgent_section
        + footer
    )

    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("💼 Positions", callback_data="menu_positions"),
        InlineKeyboardButton("🎯 Exit Plan", callback_data="menu_exit"),
    ]])
    try:
        if len(full_msg) <= 4000:
            await ctx.bot.send_message(chat_id=int(chat_id), text=full_msg,
                                       parse_mode=H, reply_markup=kb)
        else:
            # Split: header + cards, then urgent + footer
            header_cards = f"{hdr(f'💼 POSITIONS · {now_s}')}\n\n{colour_section}"
            await ctx.bot.send_message(chat_id=int(chat_id), text=header_cards, parse_mode=H)
            await ctx.bot.send_message(chat_id=int(chat_id),
                                       text=urgent_section + footer,
                                       parse_mode=H, reply_markup=kb)
    except Exception as e:
        log.warning(f"position_monitor send failed: {e}")


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
        _fw = [max(len(_fhdr[i]), max(len(r[i]) for r in _frows)) for i in range(len(_fhdr))]
        _RIGHT_F = {2, 3}
        _fj = lambda i, v: v.rjust(_fw[i]) if i in _RIGHT_F else v.ljust(_fw[i])
        _fsep = "-+-".join("-" * w for w in _fw)
        _flines = [" | ".join(_fj(i, _fhdr[i]) for i in range(len(_fhdr))), _fsep]
        for r in _frows:
            _flines.append(" | ".join(_fj(i, r[i]) for i in range(len(_fhdr))))
        parts.append("<b>FUTURES</b>\n<pre>" + "\n".join(_flines) + "</pre>")

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
            vol_rows = [f"{'Tkr':<6} {'Vol':>5}   {'Chg%':>5}  Note"]
            vol_rows.append("─" * 28)
            vol_rows.extend(spike_lines)
            parts.append("\n<b>VOLUME SPIKES (moondev WhaleAgent)</b>\n" + mono("\n".join(vol_rows)))
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
                              AND substr(expiry_date,7,4)||substr(expiry_date,1,2)||substr(expiry_date,4,2) >= ?
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
                    _oi_lines = ["<b>YOUR POSITIONS — NEXT EXPIRY OI</b>"]
                    for _od in _oi_data:
                        _st_badge, _tk2, _exp2, _c2s, _p2s = _od
                        _em2 = "🟢" if _st_badge == "[B]" else ("🔴" if _st_badge == "[S]" else "🟡")
                        _oi_lines.append(f"{_em2} <b>{_tk2}</b>  {_exp2}  C:{_c2s}  P:{_p2s}")
                    parts.append("\n" + "\n".join(_oi_lines))

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
        
        full_message = (
            f"{summary}\n\n"
            f"{hdr('💡 OPTIONS STRATEGY IMPLICATIONS')}\n"
            f"{rec_text}"
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
    await query.message.reply_text(msg, parse_mode=H, reply_markup=kb)


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
    await query.message.reply_text(msg, parse_mode=H, reply_markup=kb)


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
        await query.message.reply_text(msg, parse_mode=H, reply_markup=kb)
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
        elif data.startswith("miro_pos_"):
            tid = _safe_int(data.replace("miro_pos_", ""), 0)
            await mirofish_position_detail(query, tid)
        elif data.startswith("miro_ticker_"):
            tk = data.replace("miro_ticker_", "")
            await mirofish_ticker_detail(query, tk)
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
    risk_rows = []
    hl        = {}
    total_theta_day = 0.0
    total_delta_1pct = 0.0
    total_value = 0.0

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
        spot     = spot_ext   # use AH/PM price if available for overnight risk
        ah_tag   = f"AH:{spot_ext:.1f}" if px["is_extended"] else f"EOD:{spot_reg:.1f}"

        T = max(dte, 1) / 365.0
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

        pnl_entry = (theo - entry) / entry * 100 * pos_sign if entry > 0 else 0
        risk_lvl = "HIGH" if dte <= 3 or pnl_entry < -40 else ("MED" if dte <= 7 else "LOW")

        risk_rows.append([
            tk, f"{ot[:3]}{side_s}", f"{strk:.0f}", f"{dte}d",
            ah_tag,
            f"${theta_day:+.0f}",
            f"${delta_1pct:+.0f}",
            risk_lvl
        ])
        i = len(risk_rows) - 1
        hl[i] = "red" if risk_lvl == "HIGH" else ("yellow" if risk_lvl == "MED" else "green")

    # ── Overnight scenario: what if SPX gaps -2% at open ─────────
    gap_pnl   = total_delta_1pct * -2
    gap_up_pnl = total_delta_1pct * 2

    try:
        buf = _tbl_img(
            f"OVERNIGHT RISK  {datetime.now().strftime('%H:%M ET')}",
            ["Tkr", "Type", "Strk", "DTE", "Spot(AH)", "Theta/d", "Delta(1%)", "Risk"],
            risk_rows,
            right_cols={2, 3, 4, 5, 6},
            highlight=hl,
            subtitle=(f"Theta tonight: ${total_theta_day:+,.0f}  |  "
                      f"Gap-dn 2%: ${gap_pnl:+,.0f}  |  "
                      f"Gap-up 2%: ${gap_up_pnl:+,.0f}  |  VIX: {vix_val:.1f}")
        )
        send_photo = True
    except Exception as e:
        log.warning("overnight_risk _tbl_img failed: %s", e)
        buf = None
        send_photo = False

    vix_em    = "🔴" if vix_val > 25 else ("🟡" if vix_val > 18 else "🟢")
    tdelta_em = "🟢" if total_delta_1pct > 0 else "🔴"
    theta_em  = "🔴" if total_theta_day < -100 else "🟡"

    summary = (
        f"{hdr('⚠️ OVERNIGHT RISK REPORT')}\n\n"
        f"{vix_em} <b>VIX:</b> {vix_val:.1f}  "
        f"({'High Fear' if vix_val > 25 else 'Elevated' if vix_val > 18 else 'Calm'})\n\n"
        f"<b>📌 Spot prices: AH/PM where available</b>\n\n"
        f"{theta_em} <b>Theta Burn tonight:</b> ${total_theta_day:+,.0f}\n"
        f"{tdelta_em} <b>If market +1%:</b> ${total_delta_1pct:+,.0f}\n"
        f"🔴 <b>If gap-down 2%:</b> ${gap_pnl:+,.0f}\n"
        f"🟢 <b>If gap-up 2%:</b> ${gap_up_pnl:+,.0f}\n\n"
        f"<b>Portfolio Value:</b> ${abs(total_value):,.0f}\n"
        f"<i>🔴 HIGH = DTE≤3 or P&amp;L≤-40%  🟡 MED = DTE≤7</i>"
    )

    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("📡 Position Monitor", callback_data="menu_pos_monitor"),
        InlineKeyboardButton("🌙 AH Predictor", callback_data="menu_aftermarket_predict"),
        BACK_BTN
    ]])

    await query.message.reply_text(summary, parse_mode=H)
    if send_photo:
        await query.message.reply_photo(buf, caption="Per-position risk detail (AH prices used)", reply_markup=kb)
    else:
        await query.message.reply_text("(Chart render failed — see summary above)", parse_mode=H, reply_markup=kb)
    try: await _loading.delete()
    except Exception: pass


# ═══════════════════════════════════════════════════════════
#  AFTER-MARKET PORTFOLIO OPTION PREDICTOR
# ═══════════════════════════════════════════════════════════
async def aftermarket_predict(query):
    """Pull after-hours stock prices and predict tomorrow's option values for open positions."""
    _loading = await query.message.reply_text("🌙 Fetching after-hours prices & predicting tomorrow…", parse_mode=H)
    conn = get_conn()
    try:
        trades = pd.read_sql("SELECT * FROM trades WHERE status='OPEN'", conn)
    except Exception:
        trades = pd.DataFrame()
    conn.close()

    if trades.empty:
        await query.message.reply_text(
            f"{hdr('🌙 AFTER-MARKET PREDICTOR')}\n\nNo open positions.",
            parse_mode=H, reply_markup=InlineKeyboardMarkup([[BACK_BTN]]))
        try: await _loading.delete()
        except Exception: pass
        return

    try:
        vix_h = yf.Ticker("^VIX").history(period="5d")
        vix_val = float(vix_h["Close"].iloc[-1]) if len(vix_h) >= 1 else 20.0
    except Exception:
        vix_val = 20.0
    iv_base = vix_val / 100 * 1.3
    r_rate  = 0.045

    rows      = []
    hl        = {}
    decisions = []
    total_entry_val = 0.0
    total_pred_val  = 0.0

    for _, tr in trades.iterrows():
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
        ah_src   = px["ext_src"]
        ah_chg   = px["ext_chg_pct"]

        T_now  = max(dte, 1) / 365.0
        T_tmrw = max(dte - 1, 0.5) / 365.0
        opt_lc = ot.lower() if ot.lower() in ("call", "put") else "put"

        val_now  = bs_price(spot_reg, strk, T_now,  r_rate, iv_base, opt=opt_lc)
        val_tmrw = bs_price(spot_ext, strk, T_tmrw, r_rate, iv_base, opt=opt_lc)

        contracts = abs(qty)
        pos_sign  = 1 if qty > 0 else -1
        side_s    = "SHORT" if qty < 0 else "LONG"

        # Sign-aware P&L: for SHORT, profit when option value drops
        pnl_entry = (val_now - entry) / entry * 100 * pos_sign if entry > 0 else 0
        pnl_tmrw  = (val_tmrw - val_now) / val_now * 100 * pos_sign if val_now > 0 else 0

        # P&L in dollars vs entry (what has the position made so far)
        pnl_vs_entry_dol = (val_now - entry) * 100 * contracts * pos_sign

        total_entry_val += entry   * 100 * contracts * pos_sign
        total_pred_val  += val_tmrw * 100 * contracts * pos_sign

        if dte <= 2:
            rec = "CLOSE — Expiry!"
            hl_color = "red"
        elif pnl_entry >= 50:
            rec = "TAKE PROFIT"
            hl_color = "green"
        elif pnl_entry <= -40:
            rec = "EXIT — Big Loss"
            hl_color = "red"
        elif pnl_tmrw <= -8:
            rec = "WATCH — AH Risk"
            hl_color = "yellow"
        elif pnl_tmrw >= 8:
            rec = "HOLD — Moving Up"
            hl_color = "green"
        else:
            rec = "HOLD"
            hl_color = None

        row_idx = len(rows)
        if hl_color:
            hl[row_idx] = hl_color

        ext_tag = f"({ah_chg:+.1f}% AH)" if px["is_extended"] else "(EOD)"
        rows.append([
            tk, f"{ot[:3]}{'-S' if qty < 0 else ''}",
            f"{strk:.0f}", f"{dte}d",
            f"{spot_ext:.1f}{ext_tag}",
            f"{val_now:.2f}→{val_tmrw:.2f}({pnl_tmrw:+.0f}%)",
            rec,
        ])
        pnl_em = "🟢" if pnl_entry >= 0 else "🔴"
        tmrw_em = "🟢" if pnl_tmrw >= 0 else "🔴"
        decisions.append(
            f"{pnl_em} <b>{tk}</b> {ot[:3]} ${strk:.0f} [{side_s}, {dte}d]\n"
            f"   AH: <b>${spot_ext:.2f}</b> {ext_tag} | <i>{ah_src}</i>\n"
            f"   Option now <b>${val_now:.2f}</b>  vs entry <b>${entry:.2f}</b>  → P&amp;L vs entry: <b>${pnl_vs_entry_dol:+,.0f} ({pnl_entry:+.0f}%)</b>\n"
            f"   {tmrw_em} Tomorrow: <b>${val_tmrw:.2f}</b> ({pnl_tmrw:+.0f}% from now) | <b>{rec}</b>"
        )

    total_pnl_dol = total_pred_val - total_entry_val
    buf = _tbl_img(
        f"AFTER-MKT PREDICTOR  {datetime.now().strftime('%H:%M ET')}",
        ["Tkr", "Type", "Strk", "DTE", "AH Price", "Now→Tmrw(chg%)", "Action"],
        rows,
        right_cols={2, 3, 4, 5},
        highlight=hl,
        subtitle=(f"VIX: {vix_val:.1f}  |  IV est: {iv_base*100:.0f}%  |  "
                  f"Portfolio P&L tomorrow vs entry: ${total_pnl_dol:+,.0f}")
    )

    net_em = "🟢" if total_pnl_dol >= 0 else "🔴"
    summary = (
        f"{hdr('🌙 AFTER-MARKET PREDICTOR')}\n\n"
        f"<b>VIX:</b> {vix_val:.1f}  |  <b>IV base:</b> {iv_base*100:.0f}%\n\n"
        + "\n\n".join(decisions) +
        f"\n\n{net_em} <b>Portfolio P&amp;L tomorrow vs entry: ${total_pnl_dol:+,.0f}</b>\n"
        f"<i>Extended-hours prices from yfinance. Values = Black-Scholes, T−1 day.</i>"
    )

    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("⚠️ Overnight Risk", callback_data="menu_overnight_risk"),
        InlineKeyboardButton("💼 Positions", callback_data="menu_positions"),
        BACK_BTN
    ]])

    await query.message.reply_text(summary, parse_mode=H)
    await query.message.reply_photo(buf, caption="Per-position after-market prediction", reply_markup=kb)
    try: await _loading.delete()
    except Exception: pass


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
        log.info("Scheduled morning alert at 9:00 AM ET daily")
        # 15-min intraday alert (fires every 15 min; function checks market hours internally)
        job_queue.run_repeating(intraday_alert, interval=900, first=30)
        log.info("Scheduled 15-min intraday OI alert")
        # 10-min position monitor (fires during market hours; deduplicates via bot_data state)
        job_queue.run_repeating(position_monitor, interval=600, first=60)
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

    for (priority, pnl, advice, tid, ot, st, entry, exp, qty, cur, pnl_pct, dte, side_s, em) in leg_advice:
        pnl_s = f"${pnl:+,.0f} ({pnl_pct:+.0f}%)"
        parts.append(
            f"{em} <b>#{tid} {ot} ${st:.0f} [{side_s}]</b>  DTE:{dte}\n"
            f"   Entry ${entry:.2f} → Now ${cur:.2f}  |  P&L <b>{pnl_s}</b>\n"
            f"   {advice}"
        )

    net_em = "🟢" if total_pnl >= 0 else "🔴"
    parts.append(f"\n{net_em} <b>Net P&L: ${total_pnl:+,.0f}</b>")

    btn_rows = [[InlineKeyboardButton("⬅️ Groups", callback_data="menu_groups"), BACK_BTN]]
    await query.message.reply_text("\n\n".join(parts), parse_mode=H, reply_markup=InlineKeyboardMarkup(btn_rows))
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
    await query.message.reply_text("\n".join(parts), parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(btn_rows))

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

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, Message, Bot
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, ContextTypes,
)
from telegram.constants import ParseMode
import re

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
    tk = yf.Ticker(ticker)
    hist = tk.history(period=f"{days}d")
    fig, ax = plt.subplots(figsize=(4, 2), dpi=100)
    ax.plot(hist.index, hist["Close"], color="#00aaff", linewidth=1.5)
    ax.fill_between(hist.index, hist["Close"], alpha=0.15, color="#00aaff")
    ax.set_title(ticker, fontsize=9, color="white", pad=3)
    ax.tick_params(axis="both", labelsize=6, colors="gray")
    ax.spines[:].set_visible(False)
    ax.set_facecolor("#111111")
    fig.patch.set_facecolor("#111111")
    plt.tight_layout(pad=0.4)
    buf = BytesIO()
    fig.savefig(buf, format="png", facecolor=fig.get_facecolor())
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
        pre_rows = []
        name_w = max(len(n) for n, _, _ in items)
        price_w = max((len(d["px_s"]) if d else 3) for _, d, _ in items)
        for name, d, tag in items:
            if d:
                arrow = _col_arrow(d["chg"])
                note = f"  {tag}" if tag else ""
                pre_rows.append(
                    f"{name:<{name_w}}  {d['px_s']:>{price_w}}  {arrow} {d['chg']:>+6.2f}%{note}")
            else:
                pre_rows.append(f"{name:<{name_w}}  {'N/A':>{price_w}}")
        colour_lines.append(f"<pre>{chr(10).join(pre_rows)}</pre>")
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
        for i, entry in enumerate(feed.entries[:10], 1):
            title = html_mod.escape(entry.get("title", ""))
            link = entry.get("link", "")
            tl = title.lower()
            if any(w in tl for w in _neg):
                tag = "🔴"
                color = "#ff4136"
                bear_c += 1
            elif any(w in tl for w in _pos):
                tag = "🟢"
                color = "#2ecc40"
                bull_c += 1
            else:
                tag = "🟡"
                color = "#ffb400"
            if link:
                news_lines.append(f'{tag} <a href="{link}"><b>{title}</b></a>')
            else:
                news_lines.append(f"{tag} <b>{title}</b>")
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
    await query.message.reply_text(sanitize_html("\n".join(parts)), parse_mode=H, reply_markup=kb,
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
                link = entry.get("link", "")
                tl = title.lower()
                if any(k in tl for k in _neg_kw):
                    tag = "🔴"
                elif any(k in tl for k in _pos_kw):
                    tag = "🟢"
                else:
                    tag = "🟡"
                all_items.append((tag, title, link))
        except Exception:
            continue
        if len(all_items) >= 15:
            break

    parts = [hdr("📰 MARKET HEADLINES")]
    if all_items:
        for tag, title, link in all_items[:12]:
            short = html_mod.escape(title[:85] + ("…" if len(title) > 85 else ""))
            if link:
                parts.append(f'{tag} <a href="{link}">{short}</a>')
            else:
                parts.append(f"{tag} {short}")
    else:
        parts.append("Could not fetch market headlines.")

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Refresh", callback_data="news_ALL"),
         InlineKeyboardButton("📰 By Ticker", callback_data="menu_news"), BACK_BTN]
    ])
    try: await _loading.delete()
    except Exception: pass
    await query.message.reply_text(
        "\n".join(parts), parse_mode=H, reply_markup=kb, disable_web_page_preview=True)


# ═══════════════════════════════════════════════════════════
#  3) EXIT PLANNER (MC simulation)
# ═══════════════════════════════════════════════════════════
async def exit_planner_menu(query):
    """Show open positions from DB to analyze"""
    conn = get_conn()
    open_trades = pd.read_sql("SELECT * FROM trades WHERE status='OPEN'", conn)
    conn.close()

    if open_trades.empty:
        # Show ticker picker for manual analysis
        tickers = ["GOOG", "AMZN", "MSFT", "NVDA", "AAPL", "TSLA"]
        _def_exp = (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d")
        btns = [[InlineKeyboardButton(f"🎯 {t}", callback_data=f"exitmc|{t}|call|0|0|{_def_exp}") for t in tickers[:3]]]
        btns.append([InlineKeyboardButton(f"🎯 {t}", callback_data=f"exitmc|{t}|call|0|0|{_def_exp}") for t in tickers[3:]])
        btns.append([BACK_BTN])
        await query.message.reply_text(
            f"{hdr('🎯 EXIT PLANNER')}\n\nNo open positions. Use the unified dashboard to add trades.\nOr pick a ticker for quick analysis:",
            parse_mode=H, reply_markup=InlineKeyboardMarkup(btns),
        )
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
        label = f"🎯 {tk} {ot.upper()} ${st:.0f} [{side_s}] (entry ${ep:.2f})"
        # Use | as separator — safe with dates and decimals
        data = f"exitmc|{tk}|{ot}|{st}|{ep}|{ex}|{qty}"
        btns.append([InlineKeyboardButton(label, callback_data=data)])
    btns.append([BACK_BTN])
    await query.message.reply_text(
        f"{hdr('🎯 EXIT PLANNER')}\n\nSelect a position to analyze:",
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
    await query.message.reply_text(msg, parse_mode=H, reply_markup=kb)
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
    await query.message.reply_text("\n".join(parts), parse_mode=H, reply_markup=kb)

# ═══════════════════════════════════════════════════════════
#  4) MY POSITIONS — card-style per trade
# ═══════════════════════════════════════════════════════════
async def positions_view(query):
    _close_expired_positions()   # auto-close anything past expiry before showing
    conn = get_conn()
    trades = pd.read_sql("SELECT * FROM trades WHERE status='OPEN' ORDER BY created_at DESC LIMIT 20", conn)
    conn.close()

    if trades.empty:
        await query.message.reply_text(
            f"{hdr('💼 OPEN POSITIONS')}\n\nNo open positions found.",
            parse_mode=H, reply_markup=InlineKeyboardMarkup([[BACK_BTN]]))
        return

    parts = [hdr("💼 OPEN POSITIONS")]
    open_rows = []
    tbl_rows = [f"{'#':<3} {'Tkr':<5} {'Tp':<4} {'Stk':>4} {'Ent':>5}"]
    tbl_rows.append("─" * 25)
    for _, tr in trades.iterrows():
        tid = _safe_int(tr.get("trade_id", 0), 0)
        tk = str(tr.get("ticker", "?"))[:5]
        ot = str(tr.get("option_type", "?"))[:3].upper()
        st = _safe_float(tr.get("strike", 0), 0)
        ep = _safe_float(tr.get("entry_price", 0), 0)
        qty = _safe_int(tr.get("quantity", 0), 0)
        side_s = "B" if qty >= 0 else "S"
        gid = tr.get("group_id")
        g_mark = f"G{int(gid)}" if gid and pd.notna(gid) else f"#{tid}"
        combo = f"{side_s}{ot}"   # e.g. BPUT / BCAL / SPUT / SCAL
        tbl_rows.append(f"{g_mark:<3} {tk:<5} {combo:<4} {st:>4.0f} {ep:>5.2f}")
        open_rows.append(tr)
    parts.append(mono("\n".join(tbl_rows)))
    parts.append(f"\n📋 <b>{len(open_rows)} open positions</b>")

    btn_rows = []
    for tr in open_rows[:8]:
        tid = _safe_int(tr.get("trade_id", 0), 0)
        tk = str(tr.get("ticker", "?"))
        ot = str(tr.get("option_type", "?")).upper()
        st = _safe_float(tr.get("strike", 0), 0)
        gid = tr.get("group_id")
        label = f"🛠 #{tid} {tk} {ot} ${st:.0f}"
        if gid and pd.notna(gid):
            label = f"📦 G{int(gid)} · " + label
        btn_rows.append([InlineKeyboardButton(label, callback_data=f"pos_{tid}")])

    btn_rows.append([InlineKeyboardButton("➕ Add Position", callback_data="posadd_start")])
    btn_rows.append([InlineKeyboardButton("📦 Position Groups", callback_data="menu_groups")])
    btn_rows.append([InlineKeyboardButton("🎨 Strategy Builder", callback_data="menu_strategy_builder")])
    btn_rows.append([InlineKeyboardButton("🤖 MiroFish Signals", callback_data="menu_mirofish")])
    btn_rows.append([InlineKeyboardButton("🔄 Refresh", callback_data="menu_positions"), BACK_BTN])
    kb = InlineKeyboardMarkup(btn_rows)
    await query.message.reply_text("\n".join(parts), parse_mode=H, reply_markup=kb)


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
    await query.message.reply_text("\n".join(msg), parse_mode=H, reply_markup=kb)


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
    await query.message.reply_text("\n".join(parts), parse_mode=H, reply_markup=InlineKeyboardMarkup(btn_rows))


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
    
    await query.message.reply_text("\n".join(parts), parse_mode=H, reply_markup=InlineKeyboardMarkup(btn_rows))


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
    
    await query.message.reply_text("\n".join(parts), parse_mode=H, reply_markup=InlineKeyboardMarkup(btn_rows))


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
    await query.message.reply_text("\n".join(parts), parse_mode=H, reply_markup=InlineKeyboardMarkup(rows))


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
    rows_txt = [f"{'Leg':<4} {'Type':<5} {'Side':<5} {'Strike':>6} {'Px':>5} {'Cost':>8}"]
    rows_txt.append("─" * 38)
    for i, l in enumerate(done):
        side_lbl = "BUY" if l["qty"] > 0 else "SELL"
        rows_txt.append(
            f"L{i+1:<3} {l['opt_type']:<5} {side_lbl:<5} ${l['strike']:>5.0f} "
            f"${l['entry_price']:>4.2f} {l['cost']:>+8.0f}"
        )
    rows_txt.append("─" * 38)
    rows_txt.append(f"{'Net Cost/Credit':>30} {net_cost:>+8.0f}")
    parts.append(mono("\n".join(rows_txt)))

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

    await query.message.reply_text("\n".join(parts), parse_mode=H, reply_markup=InlineKeyboardMarkup(btns))


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
    await query.message.reply_text("\n".join(parts), parse_mode=H, reply_markup=kb)


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
    await query.message.reply_text("\n".join(parts), parse_mode=H, reply_markup=kb)



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

    if not oc.empty:
        # Top 5 strike moves
        parts.append(shdr("📊 Top Strike Activity"))
        rows = [f"{'Stk':>5} {'Exp':<8} {'CΔ':>6} {'PΔ':>6}"]
        rows.append("─" * 28)
        def _fkd(n):
            a = abs(n); s = "+" if n >= 0 else "-"
            if a >= 1_000: return f"{s}{a/1_000:.0f}K"
            return f"{s}{a:.0f}"
        for _, r in oc.head(5).iterrows():
            c_chg = float(r.get("change_OI_Call") or 0)
            p_chg = float(r.get("change_OI_Put") or 0)
            exp = str(r.get("expiry_date", ""))[:8]
            st = float(r.get("strike") or 0)
            rows.append(f"${st:>4.0f} {exp:<8} {_fkd(c_chg):>6} {_fkd(p_chg):>6}")
        parts.append(mono("\n".join(rows)))

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
        [BACK_BTN],
    ])
    await query.message.reply_text("\n".join(parts), parse_mode=H, reply_markup=kb)



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
    await query.message.reply_text("\n".join(parts), parse_mode=H, reply_markup=kb)


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
              "notional": {}, "put_skew": {}, "pin_risk": []}

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
    mean_oi = by_s["total_oi"].mean()
    walls = by_s[by_s["total_oi"] >= mean_oi * 2.0].sort_values("total_oi", ascending=False).head(6)
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

    # ── 5. PUT SKEW (Fear Gauge) ──
    # Equidistant OTM: ~5% above vs ~5% below spot.
    # put_price / call_price ratio = how much extra institutions pay for downside protection.
    # Iterates expiries (nearest first) and uses the first one with tradeable prices (call >= $0.50).
    # Near-expiry calls often price at $0.01 — skip those to get a meaningful ratio.
    spot = result["notional"].get("avg_spot", 0) if result["notional"] else 0
    if spot <= 0:
        spot = float(df["strike"].median())
    if spot > 0:
        for exp_sort_val in sorted(df["expiry_sort"].unique()):
            exp_df = df[df["expiry_sort"] == exp_sort_val].copy()
            exp_df["c_dist"] = (exp_df["strike"] - spot * 1.05).abs()
            exp_df["p_dist"] = (exp_df["strike"] - spot * 0.95).abs()
            if exp_df.empty:
                continue
            cr = exp_df.nsmallest(1, "c_dist").iloc[0]
            pr = exp_df.nsmallest(1, "p_dist").iloc[0]
            c_px = float(cr["lastPrice_Call_now"])
            p_px = float(pr["lastPrice_Put_now"])
            if c_px >= 0.50 and p_px > 0:
                skew = round(p_px / c_px, 2)
                fear = ("EXTREME FEAR" if skew > 3.0 else
                        "HIGH FEAR"    if skew > 2.0 else
                        "ELEVATED"     if skew > 1.2 else
                        "NORMAL"       if skew > 0.8 else
                        "LOW (COMPLACENCY)" if skew > 0.5 else "INVERTED")
                result["put_skew"] = {
                    "call_strike": float(cr["strike"]), "put_strike": float(pr["strike"]),
                    "call_px": c_px, "put_px": p_px, "skew": skew, "fear": fear,
                    "expiry": str(exp_df["expiry_date"].iloc[0]),
                }
                break  # found a usable expiry

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
        rows = []
        for mp in mp_list[:4]:
            dte_s = f"DTE {mp['dte']}" if mp.get("dte") is not None else ""
            dist = ""
            if spot > 0:
                d_pct = (spot - mp["strike"]) / spot * 100
                dist = f"vs spot {d_pct:+.1f}%"
            rows.append(f"  {mp['expiry'][:8]}  ${mp['strike']:.0f}  {dte_s}")
            if dist:
                rows.append(f"    {dist}")
        parts.append(mono("\n".join(rows)))
        parts.append("<i>Fade moves away from max pain as expiry nears</i>")

    # 2. Gamma Walls
    walls = sig.get("gamma_walls", [])
    if walls:
        parts.append("\n<b>GAMMA WALLS  (Dealer Hedging Levels)</b>")
        rows = []
        for w in walls[:5]:
            label = "CEILING" if w["type"] == "CALL" else ("FLOOR" if w["type"] == "PUT" else "WALL")
            tot_k = f"{w['total_oi']/1000:.0f}K" if w['total_oi'] >= 1000 else str(w['total_oi'])
            c_k   = f"{w['call_oi']/1000:.0f}K"  if w['call_oi']  >= 1000 else str(w['call_oi'])
            p_k   = f"{w['put_oi']/1000:.0f}K"   if w['put_oi']   >= 1000 else str(w['put_oi'])
            rows.append(f"  ${w['strike']:.0f}  {label}  tot:{tot_k}")
            rows.append(f"    C:{c_k}  P:{p_k}")
        parts.append(mono("\n".join(rows)))
        parts.append("<i>Price gravitates toward / stalls at these strikes</i>")

    # 3. Smart Money Flow
    sf = sig.get("smart_flow", {})
    if sf:
        parts.append("\n<b>SMART MONEY FLOW</b>")
        def _fk_sf(n):
            a = abs(n); s = "+" if n >= 0 else "-"
            if a >= 1_000_000: return f"{s}{a/1_000_000:.1f}M"
            if a >= 1_000: return f"{s}{a/1_000:.0f}K"
            return f"{s}{a:.0f}"
        rows = [
            "  CALLS  OI:{}  vol:{}".format(
                _fk_sf(sf.get("call_oi_chg", 0)), _fk_sf(sf.get("call_vol", 0))),
            "         {}".format(sf.get("call_verdict", "")),
            "  PUTS   OI:{}  vol:{}".format(
                _fk_sf(sf.get("put_oi_chg", 0)), _fk_sf(sf.get("put_vol", 0))),
            "         {}".format(sf.get("put_verdict", "")),
        ]
        parts.append(mono("\n".join(rows)))
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
        parts.append("\n<b>NOTIONAL CONVICTION  (Dollar Weight)</b>")
        rows = [
            "  Call:  ${:.1f}M  Bull:{:,.0f}".format(nt.get("call_m", 0), nt.get("bull_score", 0)),
            "  Put:   ${:.1f}M  Bear:{:,.0f}".format(nt.get("put_m", 0), nt.get("bear_score", 0)),
            "  Net:   ${:+.1f}M  ratio:{:.2f}x".format(nt.get("net_m", 0), nt.get("ratio", 0)),
        ]
        parts.append(mono("\n".join(rows)))
        parts.append(f"<i>Dollar bias: <b>{nt.get('bias', '')}</b></i>")

    # 5. Put Skew
    ps = sig.get("put_skew", {})
    if ps:
        parts.append("\n<b>PUT SKEW  (Institutional Fear Gauge)</b>")
        rows = [
            "  Exp:  {}".format(ps.get("expiry", "")),
            "  Call ~5%OTM: ${:.0f}  px ${:.2f}".format(
                ps.get("call_strike", 0), ps.get("call_px", 0)),
            "  Put  ~5%OTM: ${:.0f}  px ${:.2f}".format(
                ps.get("put_strike", 0), ps.get("put_px", 0)),
            "  Skew: {:.2f}x  [{}]".format(ps.get("skew", 0), ps.get("fear", "")),
        ]
        parts.append(mono("\n".join(rows)))
        fear = ps.get("fear", "")
        if "EXTREME" in fear or "HIGH" in fear:
            hint = "Heavy put-premium demand — institutions hedging longs; often near bottoms"
        elif "COMPLACENCY" in fear or "INVERTED" in fear:
            hint = "Cheap puts — complacency or call blow-off; watch for reversal"
        else:
            hint = "Normal cost of protection"
        parts.append(f"<i>{hint}</i>")

    # 6. Pin Risk
    pins = sig.get("pin_risk", [])
    if pins:
        parts.append("\n<b>PIN RISK  (DTE \u2264 7)</b>")
        rows = []
        for pin in pins[:4]:
            oi_k = f"{pin['total_oi']//1000}K" if pin['total_oi'] >= 1000 else str(pin['total_oi'])
            rows.append("  ${:.0f}  {}  DTE{}  OI:{}".format(
                pin["strike"], pin["expiry"][:5], pin["dte"], oi_k))
        parts.append(mono("\n".join(rows)))
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
    await query.message.reply_text("\n".join(parts), parse_mode=H, reply_markup=kb)


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
            SELECT trade_date, net_oi, call_oi, put_oi
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
        bar = "\u2588" * min(int(abs(z) * 2), 10)
        parts.append("\n<b>PCR Z-SCORE  ({}d lookback)</b>".format(pz["lookback"]))
        rows = [
            "  Today PCR:  {:.3f}".format(pz["today"]),
            "  {}d mean:   {:.3f}   std: {:.3f}".format(pz["lookback"], pz["mean"], pz["std"]),
            "  Z-score:   {:+.2f}  [{}]  {}".format(z, bar, pz["level"]),
        ]
        parts.append(mono("\n".join(rows)))
        parts.append("<i>{}</i>".format(pz["action"]))

    # 2. Price Z-Score
    prz = sig.get("price_z", {})
    if prz:
        any_data = True
        parts.append("\n<b>PRICE Z-SCORE  (20d lookback)</b>")
        rows = [
            "  Today:    ${:.2f}".format(prz["today"]),
            "  20d mean: ${:.2f}   std: ${:.2f}".format(prz["mean20"], prz["std20"]),
            "  Z-score:  {:+.2f}  [{}]".format(prz["z"], prz["level"]),
            "  Target:   ${:.2f}   Stop: ${:.2f}".format(prz["target1"], prz["stop"]),
        ]
        parts.append(mono("\n".join(rows)))

    # 3. PCR Trend
    pt = sig.get("pcr_trend", {})
    if pt:
        any_data = True
        last5 = " \u2192 ".join(str(x) for x in pt["last5"])
        parts.append("\n<b>PCR TREND  (5-day rolling)</b>")
        rows = [
            "  5d avg: {:.3f}   Today: {:.3f}   ({:+.1f}%)".format(
                pt["avg5"], pt["today"], pt["pct_chg"]),
            "  Trend:  {}".format(pt["trend"]),
            "  Last 5: {}".format(last5),
        ]
        parts.append(mono("\n".join(rows)))
        if "SPIKE" in pt["trend"]:
            parts.append("<i>Sudden spike -- may be expiry distortion or event hedge</i>")

    # 4. Net OI Extreme
    oi = sig.get("oi_extreme", {})
    if oi:
        any_data = True
        parts.append("\n<b>NET OI EXTREME  (20d lookback)</b>")
        rows = [
            "  Net OI today:  {:>+12,}".format(oi["net_oi_today"]),
            "  20d mean:      {:>+12,}".format(oi["net_oi_mean"]),
            "  Z-score:       {:>+12.2f}  [{}]".format(oi["z"], oi["level"]),
            "  Call OI: {:>9,}   Put OI: {:>9,}".format(oi["call_oi"], oi["put_oi"]),
        ]
        parts.append(mono("\n".join(rows)))
        if "PEAK" in oi["level"]:
            note = ("Too many puts -- peak bearish positioning, contrarian BUY zone"
                    if "BEARISH" in oi["level"] else
                    "Too many calls -- peak bullish positioning, contrarian SELL zone")
            parts.append("<i>{}</i>".format(note))

    # 5. Composite
    comp = sig.get("composite", {})
    if comp:
        any_data = True
        sc    = comp["score"]
        arrow = "\u25b2" if sc > 0 else "\u25bc"
        parts.append("\n<b>COMPOSITE MEAN REVERSION SCORE</b>")
        rows = ["  Score:  {:+.2f}  {}  [{}]".format(sc, arrow, comp["level"])]
        if comp["factors"]:
            rows.append("  Inputs: {}".format(",  ".join(comp["factors"])))
        parts.append(mono("\n".join(rows)))
        if comp["action"]:
            parts.append("<b>Trade idea:</b>  {}".format(comp["action"]))

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
    await query.message.reply_text("\n".join(parts), parse_mode=H, reply_markup=kb)


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
    await query.message.reply_text("\n".join(parts), parse_mode=H, reply_markup=kb)


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
    await query.message.reply_text(msg, parse_mode=H, reply_markup=kb)


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
    await query.message.reply_text(msg, parse_mode=H, reply_markup=kb)


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
    _oi_hdrs = ["Ticker", "Call OI", "Put OI", "PCR"]
    _oi_data = []
    for _, r in top_oi.iterrows():
        _oi_data.append([
            str(r['ticker']),
            _fk_oi(r['total_call_oi']),
            _fk_oi(r['total_put_oi']),
            f"{min(float(r['pcr'] or 0), 9.99):.2f}",
        ])
    _oi_w = [max(len(_oi_hdrs[i]), max(len(row[i]) for row in _oi_data)) for i in range(len(_oi_hdrs))]
    oi_rows  = [" | ".join(_oi_hdrs[i].ljust(_oi_w[i]) for i in range(len(_oi_hdrs)))]
    oi_rows += ["-+-".join("-" * w for w in _oi_w)]
    for row in _oi_data:
        oi_rows.append(" | ".join(row[i].ljust(_oi_w[i]) for i in range(len(row))))
    parts.append("\n<b>Top by Open Interest</b>\n" + mono("\n".join(oi_rows)))

    # Highest PCR — same dynamic-width approach
    high_pcr = df[df["pcr"] > 0].nlargest(5, "pcr")
    if not high_pcr.empty:
        _pcr_hdrs = ["Ticker", "PCR", "Bias"]
        _pcr_data = []
        for _, r in high_pcr.iterrows():
            bias = "Bearish" if r["pcr"] > 1.3 else ("Bullish" if r["pcr"] < 0.7 else "Neutral")
            _pcr_data.append([str(r['ticker']), f"{min(float(r['pcr'] or 0), 9.99):.2f}", bias])
        _pcr_w = [max(len(_pcr_hdrs[i]), max(len(row[i]) for row in _pcr_data)) for i in range(len(_pcr_hdrs))]
        pcr_rows  = [" | ".join(_pcr_hdrs[i].ljust(_pcr_w[i]) for i in range(len(_pcr_hdrs)))]
        pcr_rows += ["-+-".join("-" * w for w in _pcr_w)]
        for row in _pcr_data:
            pcr_rows.append(" | ".join(row[i].ljust(_pcr_w[i]) for i in range(len(row))))
        parts.append("\n<b>Highest Put/Call Ratio</b>\n" + mono("\n".join(pcr_rows)))

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
    await query.message.reply_text("\n".join(parts), parse_mode=H, reply_markup=InlineKeyboardMarkup(btns))

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
            if iv_pct:
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
    await query.message.reply_text(msg, parse_mode=H, reply_markup=kb)


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
        parts.append("\n🟢 <b>Biggest Call OI Increases</b>")
        rows = [f"{'ST':<3} {'Ticker':<6} {'Call OI Chg':>11} {'PCR':>5}"]
        rows.append("─" * 29)
        for _, r in top_call_gain.iterrows():
            rows.append(f"[B] {r['ticker']:<6} {r['call_chg']:>+11,.0f} {r['pcr_2']:>5.2f}")
        parts.append(mono("\n".join(rows)))

    # Biggest put OI increases
    top_put_gain = merged.nlargest(5, "put_chg")
    if not top_put_gain.empty:
        parts.append("\n🔴 <b>Biggest Put OI Increases</b>")
        rows = [f"{'ST':<3} {'Ticker':<6} {'Put OI Chg':>10} {'PCR':>5}"]
        rows.append("─" * 28)
        for _, r in top_put_gain.iterrows():
            rows.append(f"[S] {r['ticker']:<6} {r['put_chg']:>+10,.0f} {r['pcr_2']:>5.2f}")
        parts.append(mono("\n".join(rows)))

    # PCR changes
    top_pcr_inc = merged.dropna(subset=["pcr_chg"]).nlargest(5, "pcr_chg")
    if not top_pcr_inc.empty:
        parts.append("\n📈 <b>Biggest PCR Increases (More Bearish)</b>")
        rows = [f"{'Ticker':<6} {'PCR Δ':>6} {'New PCR':>7}"]
        rows.append("─" * 22)
        for _, r in top_pcr_inc.iterrows():
            rows.append(f"{r['ticker']:<6} {r['pcr_chg']:>+6.2f} {r['pcr_2']:>7.2f}")
        parts.append(mono("\n".join(rows)))
    
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔀 New Compare", callback_data="oi_compare_select1")],
        [InlineKeyboardButton("📊 OI Menu", callback_data="menu_oi"), BACK_BTN]
    ])
    await query.message.reply_text("\n".join(parts), parse_mode=H, reply_markup=kb)


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

    try:
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
                2, 1, subplot_spec=gridspec.GridSpec(n, 1)[idx],
                height_ratios=[3, 1.4], hspace=0.08)
            ax_main  = fig.add_subplot(gs[0])
            ax_delta = fig.add_subplot(gs[1])

            if df_eod.empty:
                ax_main.text(0.5, 0.5, f"No EOD data for {expiry}", ha="center", va="center")
                ax_main.set_title(f"{ticker}  {expiry}  -- No EOD Data")
                ax_delta.set_visible(False)
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
                continue
            wd = (strikes[1] - strikes[0]) * 0.4

            if spot and len(df):
                df, sig, sig_col, sig_desc, dets = _oi_intent_algo(df, spot)
            else:
                sig, sig_col, sig_desc, dets = "N/A", "#455A64", "Spot unavailable", {}
                df["bar_col"] = "#90A4AE"
                df["intent"]  = "NEUTRAL"

            # Top panel
            ax_main.bar(strikes - wd/2, df["openInt_Call_eod"], wd*0.9, alpha=0.25, color="#43A047", label=f"Call EOD {eod_date}")
            ax_main.bar(strikes - wd/2, -df["openInt_Put_eod"], wd*0.9, alpha=0.25, color="#E53935", label=f"Put EOD {eod_date}")
            ax_main.bar(strikes + wd/2, df["openInt_Call"],     wd*0.9, alpha=0.75, color="#1B5E20", label="Call LIVE")
            ax_main.bar(strikes + wd/2, -df["openInt_Put"],     wd*0.9, alpha=0.75, color="#B71C1C", label="Put LIVE")
            ax_main.axhline(0, color="#212121", linewidth=0.8)

            if spot:
                ax_main.axvspan(spot*0.97, spot*1.03, alpha=0.08, color="yellow", label="ATM +/-3%")
                ax_main.axvline(spot, color="#FFD600", linewidth=1.4, linestyle="--", label=f"Spot ${spot:.1f}")
                ax_main.axvspan(0, spot*0.90, alpha=0.04, color="#1565C0")

            ax_main.set_title(f"{ticker}  |  Expiry: {expiry}  |  LIVE vs EOD {eod_date}", fontsize=11, fontweight="bold")
            ax_main.set_ylabel("Open Interest")
            ax_main.set_xlim(strikes[0] - wd * 2.5, strikes[-1] + wd * 2.5)
            ax_main.legend(loc="upper left", fontsize=7, ncol=2, framealpha=0.75)
            ax_main.yaxis.set_major_formatter(
                plt.FuncFormatter(lambda x, _: f"{abs(x)/1000:.0f}K" if abs(x) >= 1000 else f"{x:.0f}"))
            ax_main.grid(True, alpha=0.25, axis="y")
            ax_main.tick_params(labelbottom=False)

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

            # Bottom delta panel
            _PL = {"BULLISH":"#A5D6A7","BEARISH":"#FFCDD2","STRADDLE":"#CE93D8",
                   "NEAR_BEARISH":"#FFCCBC","HEDGE":"#BBDEFB","HEDGE_UNWIND":"#E3F2FD",
                   "BULLISH_BREAK":"#C8E6C9","COVERED_CALL":"#FFF9C4","UNWIND":"#EEEEEE","NEUTRAL":"#ECEFF1"}
            for s, cd, pd_, col, intent in zip(
                    strikes, df["call_oi_change"], df["put_oi_change"], df["bar_col"], df["intent"]):
                ax_delta.bar(s - wd/2, cd,    wd*0.9, color=col,                 alpha=0.85)
                ax_delta.bar(s + wd/2, -pd_,  wd*0.9, color=_PL.get(intent,"#ECEFF1"), alpha=0.85)

            ax_delta.axhline(0, color="#212121", linewidth=0.8)
            if spot:
                ax_delta.axvspan(spot*0.97, spot*1.03, alpha=0.10, color="yellow")
                ax_delta.axvline(spot, color="#FFD600", linewidth=1.2, linestyle="--")
            _step_d = max(1, len(strikes) // 14)
            ax_delta.set_xticks(strikes[::_step_d])
            ax_delta.set_xticklabels([f"${s:.0f}" for s in strikes[::_step_d]],
                                     rotation=45, ha='right', fontsize=7)
            ax_delta.set_xlim(strikes[0] - wd * 2.5, strikes[-1] + wd * 2.5)
            ax_delta.set_xlabel("Strike Price", fontsize=8)
            ax_delta.set_ylabel("OI Delta", fontsize=8)
            ax_delta.yaxis.set_major_formatter(
                plt.FuncFormatter(lambda x, _: f"{abs(x)/1000:.0f}K" if abs(x) >= 1000 else f"{x:.0f}"))
            ax_delta.grid(True, alpha=0.2, axis="y")
            _IC = {"BULLISH":"#2E7D32","BEARISH":"#C62828","HEDGE":"#1565C0",
                   "NEAR_BEARISH":"#BF360C","STRADDLE":"#6A1B9A",
                   "COVERED_CALL":"#F57F17","BULLISH_BREAK":"#388E3C","UNWIND":"#757575"}
            ax_delta.legend(handles=[mpatches.Patch(color=c, label=l) for l,c in _IC.items()],
                            loc="lower right", fontsize=6, ncol=4, framealpha=0.8)

        plt.tight_layout(pad=1.5)
        buf = BytesIO()
        fig.savefig(buf, format="png", dpi=110, bbox_inches="tight")
        plt.close(fig)
        buf.seek(0)
        conn.close()
        return buf

    except Exception as e:
        log.error(f"Live chart error for {ticker}: {e}", exc_info=True)
        try: plt.close(fig)
        except Exception: pass
        conn.close()
        return None


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
    
    # Generate chart
    chart_buf = _generate_live_vs_eod_chart(ticker, live_data, eod_date)
    
    if chart_buf is None:
        await query.message.reply_text(
            f"❌ Failed to generate chart for {ticker}.",
            reply_markup=InlineKeyboardMarkup([[BACK_BTN]])
        )
        try: await _loading.delete()
        except Exception: pass
        return
    
    # Send chart
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

    if _strike_parts:
        parts.append("\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        parts.append("📊 <b>STRIKE-LEVEL OI ANALYSIS</b>")
        parts.extend(_strike_parts)

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Refresh", callback_data="menu_signals"),
         InlineKeyboardButton("🤖 MiroFish", callback_data="menu_mirofish")],
        [BACK_BTN]
    ])
    await query.message.reply_text("\n".join(parts), parse_mode=H, reply_markup=kb)
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
    await query.message.reply_text("\n".join(parts), parse_mode=H, reply_markup=kb)

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
    await query.message.reply_text("\n".join(parts), parse_mode=H, reply_markup=kb)


# ═══════════════════════════════════════════════════════════
#  8b) EXTRA FEATURES (from extended dashboard updates)
# ═══════════════════════════════════════════════════════════
async def more_features_menu(query):
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🏦 Prop Trading", callback_data="menu_prop"),
         InlineKeyboardButton("📈 Backtest Lab", callback_data="menu_backtest")],
        [InlineKeyboardButton("🔮 Live Predictor", callback_data="menu_livepred"),
         InlineKeyboardButton("🐋 Whale Holdings", callback_data="menu_whales")],
        [InlineKeyboardButton("🖥 Dashboard URL", callback_data="menu_streamlit_link")],
        [InlineKeyboardButton("📡 Market Analytics", callback_data="menu_analytics"),
         InlineKeyboardButton("🌍 Global Market", callback_data="menu_global_market")],
        [InlineKeyboardButton("📊 NYSE Daily Report", callback_data="menu_nyse_report"),
         InlineKeyboardButton("🤖 MiroFish Signals", callback_data="menu_mirofish")],
        [InlineKeyboardButton("🌙 AH Predictor", callback_data="menu_aftermarket_predict"),
         InlineKeyboardButton("⚠️ Overnight Risk", callback_data="menu_overnight_risk")],
        [InlineKeyboardButton("🎲 Monte Carlo Sim", callback_data="menu_exit")],
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
        _oi_cw  = [max(len(_oi_hdr[i]), max((len(r[i]) for r in _oi_rows), default=0)) for i in range(5)]
        _oi_jn  = lambda i, v: v.rjust(_oi_cw[i]) if i in _oi_RGHT else v.ljust(_oi_cw[i])
        _oi_sep = "-+-".join("-" * w for w in _oi_cw)
        _oi_lines = [f"OI FLOW  {latest_date}",
                     " | ".join(_oi_jn(i, _oi_hdr[i]) for i in range(5)), _oi_sep]
        for r in _oi_rows:
            _oi_lines.append(" | ".join(_oi_jn(i, r[i]) for i in range(5)))
        parts.append("<pre>" + "\n".join(_oi_lines) + "</pre>")

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
            _t_cw   = [max(len(_t_hdr[i]), max(len(r[i]) for r in _t_data)) for i in range(7)]
            _t_jn   = lambda i, v: v.rjust(_t_cw[i]) if i in _t_RGHT else v.ljust(_t_cw[i])
            _t_sep  = "-+-".join("-" * w for w in _t_cw)
            _t_out  = ["TECH SIGNALS  RSI/MACD/BB/EMA",
                       " | ".join(_t_jn(i, _t_hdr[i]) for i in range(7)), _t_sep]
            for r in _t_data:
                _t_out.append(" | ".join(_t_jn(i, r[i]) for i in range(7)))
            parts.append("<pre>" + "\n".join(_t_out) + "</pre>")
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
    await query.message.reply_text("\n".join(parts), parse_mode=H, reply_markup=kb)


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

    html_cards = []

    for (em, tk, otype, strike, entry, cur_px, pnl_pct, pnl, dte_s, prob_s, oi_s, action) in rows:
        buy_s = f"${entry:.2f}"  if entry  < 100 else f"${entry:.0f}"
        cur_s = f"${cur_px:.2f}" if cur_px < 100 else f"${cur_px:.0f}"
        pct_s = f"{pnl_pct:+.1f}%"
        pnl_s = f"${pnl:+,.0f}"
        a_em  = _action_em.get(action, "✅")
        advice = _action_advice.get(action, "Monitor position.")

        # DTE urgency note
        dte_num = int(dte_s[1:]) if dte_s.startswith("D") and dte_s[1:].isdigit() else None
        if dte_num is not None and dte_num <= 3:
            dte_note = f" ⚠️ <b>Only {dte_num} days left!</b>"
        elif dte_num is not None and dte_num <= 7:
            dte_note = f" ({dte_num}d to expiry)"
        else:
            dte_note = f" ({dte_s} to expiry)"

        # OI alignment note
        oi_note = f"  OI: <i>{oi_s}</i>" if oi_s and oi_s != "?" else ""

        side_word = "Sold" if entry < 0 else "Bought"

        html_cards.append(
            f"{em} <b>{tk} {otype} ${int(strike)}</b>{dte_note}\n"
            f"   Entered {buy_s}  →  Now {cur_s}  |  <b>{pct_s} ({pnl_s})</b>\n"
            f"   Win probability: {prob_s}{oi_note}\n"
            f"   {a_em} <b>{action}</b> — {advice}"
        )

    colour_section = "\n\n".join(html_cards)

    urgent_section = ""
    if urgent_lines:
        urgent_section = "\n\n<b>⚡ ACTION REQUIRED</b>\n" + "\n".join(urgent_lines)

    net_em  = "🟢" if total_pnl >= 0 else "🔴"
    n_pos   = len(html_cards)
    footer  = f"\n{net_em} <b>Portfolio total: ${total_pnl:+,.0f}</b>  ({n_pos} open position{'s' if n_pos != 1 else ''})"

    full_msg = (
        f"{hdr(f'💼 POSITIONS · {now_s}')}\n\n"
        + colour_section
        + urgent_section
        + footer
    )

    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("💼 Positions", callback_data="menu_positions"),
        InlineKeyboardButton("🎯 Exit Plan", callback_data="menu_exit"),
    ]])
    try:
        if len(full_msg) <= 4000:
            await ctx.bot.send_message(chat_id=int(chat_id), text=full_msg,
                                       parse_mode=H, reply_markup=kb)
        else:
            # Split: header + cards, then urgent + footer
            header_cards = f"{hdr(f'💼 POSITIONS · {now_s}')}\n\n{colour_section}"
            await ctx.bot.send_message(chat_id=int(chat_id), text=header_cards, parse_mode=H)
            await ctx.bot.send_message(chat_id=int(chat_id),
                                       text=urgent_section + footer,
                                       parse_mode=H, reply_markup=kb)
    except Exception as e:
        log.warning(f"position_monitor send failed: {e}")


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
        _fw = [max(len(_fhdr[i]), max(len(r[i]) for r in _frows)) for i in range(len(_fhdr))]
        _RIGHT_F = {2, 3}
        _fj = lambda i, v: v.rjust(_fw[i]) if i in _RIGHT_F else v.ljust(_fw[i])
        _fsep = "-+-".join("-" * w for w in _fw)
        _flines = [" | ".join(_fj(i, _fhdr[i]) for i in range(len(_fhdr))), _fsep]
        for r in _frows:
            _flines.append(" | ".join(_fj(i, r[i]) for i in range(len(_fhdr))))
        parts.append("<b>FUTURES</b>\n<pre>" + "\n".join(_flines) + "</pre>")

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
            vol_rows = [f"{'Tkr':<6} {'Vol':>5}   {'Chg%':>5}  Note"]
            vol_rows.append("─" * 28)
            vol_rows.extend(spike_lines)
            parts.append("\n<b>VOLUME SPIKES (moondev WhaleAgent)</b>\n" + mono("\n".join(vol_rows)))
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
                              AND substr(expiry_date,7,4)||substr(expiry_date,1,2)||substr(expiry_date,4,2) >= ?
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
                    _oi_lines = ["<b>YOUR POSITIONS — NEXT EXPIRY OI</b>"]
                    for _od in _oi_data:
                        _st_badge, _tk2, _exp2, _c2s, _p2s = _od
                        _em2 = "🟢" if _st_badge == "[B]" else ("🔴" if _st_badge == "[S]" else "🟡")
                        _oi_lines.append(f"{_em2} <b>{_tk2}</b>  {_exp2}  C:{_c2s}  P:{_p2s}")
                    parts.append("\n" + "\n".join(_oi_lines))

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
        
        full_message = (
            f"{summary}\n\n"
            f"{hdr('💡 OPTIONS STRATEGY IMPLICATIONS')}\n"
            f"{rec_text}"
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
    await query.message.reply_text(msg, parse_mode=H, reply_markup=kb)


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
    await query.message.reply_text(msg, parse_mode=H, reply_markup=kb)


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
        await query.message.reply_text(msg, parse_mode=H, reply_markup=kb)
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
        elif data.startswith("miro_pos_"):
            tid = _safe_int(data.replace("miro_pos_", ""), 0)
            await mirofish_position_detail(query, tid)
        elif data.startswith("miro_ticker_"):
            tk = data.replace("miro_ticker_", "")
            await mirofish_ticker_detail(query, tk)
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
    risk_rows = []
    hl        = {}
    total_theta_day = 0.0
    total_delta_1pct = 0.0
    total_value = 0.0

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
        spot     = spot_ext   # use AH/PM price if available for overnight risk
        ah_tag   = f"AH:{spot_ext:.1f}" if px["is_extended"] else f"EOD:{spot_reg:.1f}"

        T = max(dte, 1) / 365.0
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

        pnl_entry = (theo - entry) / entry * 100 * pos_sign if entry > 0 else 0
        risk_lvl = "HIGH" if dte <= 3 or pnl_entry < -40 else ("MED" if dte <= 7 else "LOW")

        risk_rows.append([
            tk, f"{ot[:3]}{side_s}", f"{strk:.0f}", f"{dte}d",
            ah_tag,
            f"${theta_day:+.0f}",
            f"${delta_1pct:+.0f}",
            risk_lvl
        ])
        i = len(risk_rows) - 1
        hl[i] = "red" if risk_lvl == "HIGH" else ("yellow" if risk_lvl == "MED" else "green")

    # ── Overnight scenario: what if SPX gaps -2% at open ─────────
    gap_pnl   = total_delta_1pct * -2
    gap_up_pnl = total_delta_1pct * 2

    try:
        buf = _tbl_img(
            f"OVERNIGHT RISK  {datetime.now().strftime('%H:%M ET')}",
            ["Tkr", "Type", "Strk", "DTE", "Spot(AH)", "Theta/d", "Delta(1%)", "Risk"],
            risk_rows,
            right_cols={2, 3, 4, 5, 6},
            highlight=hl,
            subtitle=(f"Theta tonight: ${total_theta_day:+,.0f}  |  "
                      f"Gap-dn 2%: ${gap_pnl:+,.0f}  |  "
                      f"Gap-up 2%: ${gap_up_pnl:+,.0f}  |  VIX: {vix_val:.1f}")
        )
        send_photo = True
    except Exception as e:
        log.warning("overnight_risk _tbl_img failed: %s", e)
        buf = None
        send_photo = False

    vix_em    = "🔴" if vix_val > 25 else ("🟡" if vix_val > 18 else "🟢")
    tdelta_em = "🟢" if total_delta_1pct > 0 else "🔴"
    theta_em  = "🔴" if total_theta_day < -100 else "🟡"

    summary = (
        f"{hdr('⚠️ OVERNIGHT RISK REPORT')}\n\n"
        f"{vix_em} <b>VIX:</b> {vix_val:.1f}  "
        f"({'High Fear' if vix_val > 25 else 'Elevated' if vix_val > 18 else 'Calm'})\n\n"
        f"<b>📌 Spot prices: AH/PM where available</b>\n\n"
        f"{theta_em} <b>Theta Burn tonight:</b> ${total_theta_day:+,.0f}\n"
        f"{tdelta_em} <b>If market +1%:</b> ${total_delta_1pct:+,.0f}\n"
        f"🔴 <b>If gap-down 2%:</b> ${gap_pnl:+,.0f}\n"
        f"🟢 <b>If gap-up 2%:</b> ${gap_up_pnl:+,.0f}\n\n"
        f"<b>Portfolio Value:</b> ${abs(total_value):,.0f}\n"
        f"<i>🔴 HIGH = DTE≤3 or P&amp;L≤-40%  🟡 MED = DTE≤7</i>"
    )

    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("📡 Position Monitor", callback_data="menu_pos_monitor"),
        InlineKeyboardButton("🌙 AH Predictor", callback_data="menu_aftermarket_predict"),
        BACK_BTN
    ]])

    await query.message.reply_text(summary, parse_mode=H)
    if send_photo:
        await query.message.reply_photo(buf, caption="Per-position risk detail (AH prices used)", reply_markup=kb)
    else:
        await query.message.reply_text("(Chart render failed — see summary above)", parse_mode=H, reply_markup=kb)
    try: await _loading.delete()
    except Exception: pass


# ═══════════════════════════════════════════════════════════
#  AFTER-MARKET PORTFOLIO OPTION PREDICTOR
# ═══════════════════════════════════════════════════════════
async def aftermarket_predict(query):
    """Pull after-hours stock prices and predict tomorrow's option values for open positions."""
    _loading = await query.message.reply_text("🌙 Fetching after-hours prices & predicting tomorrow…", parse_mode=H)
    conn = get_conn()
    try:
        trades = pd.read_sql("SELECT * FROM trades WHERE status='OPEN'", conn)
    except Exception:
        trades = pd.DataFrame()
    conn.close()

    if trades.empty:
        await query.message.reply_text(
            f"{hdr('🌙 AFTER-MARKET PREDICTOR')}\n\nNo open positions.",
            parse_mode=H, reply_markup=InlineKeyboardMarkup([[BACK_BTN]]))
        try: await _loading.delete()
        except Exception: pass
        return

    try:
        vix_h = yf.Ticker("^VIX").history(period="5d")
        vix_val = float(vix_h["Close"].iloc[-1]) if len(vix_h) >= 1 else 20.0
    except Exception:
        vix_val = 20.0
    iv_base = vix_val / 100 * 1.3
    r_rate  = 0.045

    rows      = []
    hl        = {}
    decisions = []
    total_entry_val = 0.0
    total_pred_val  = 0.0

    for _, tr in trades.iterrows():
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
        ah_src   = px["ext_src"]
        ah_chg   = px["ext_chg_pct"]

        T_now  = max(dte, 1) / 365.0
        T_tmrw = max(dte - 1, 0.5) / 365.0
        opt_lc = ot.lower() if ot.lower() in ("call", "put") else "put"

        val_now  = bs_price(spot_reg, strk, T_now,  r_rate, iv_base, opt=opt_lc)
        val_tmrw = bs_price(spot_ext, strk, T_tmrw, r_rate, iv_base, opt=opt_lc)

        contracts = abs(qty)
        pos_sign  = 1 if qty > 0 else -1
        side_s    = "SHORT" if qty < 0 else "LONG"

        # Sign-aware P&L: for SHORT, profit when option value drops
        pnl_entry = (val_now - entry) / entry * 100 * pos_sign if entry > 0 else 0
        pnl_tmrw  = (val_tmrw - val_now) / val_now * 100 * pos_sign if val_now > 0 else 0

        # P&L in dollars vs entry (what has the position made so far)
        pnl_vs_entry_dol = (val_now - entry) * 100 * contracts * pos_sign

        total_entry_val += entry   * 100 * contracts * pos_sign
        total_pred_val  += val_tmrw * 100 * contracts * pos_sign

        if dte <= 2:
            rec = "CLOSE — Expiry!"
            hl_color = "red"
        elif pnl_entry >= 50:
            rec = "TAKE PROFIT"
            hl_color = "green"
        elif pnl_entry <= -40:
            rec = "EXIT — Big Loss"
            hl_color = "red"
        elif pnl_tmrw <= -8:
            rec = "WATCH — AH Risk"
            hl_color = "yellow"
        elif pnl_tmrw >= 8:
            rec = "HOLD — Moving Up"
            hl_color = "green"
        else:
            rec = "HOLD"
            hl_color = None

        row_idx = len(rows)
        if hl_color:
            hl[row_idx] = hl_color

        ext_tag = f"({ah_chg:+.1f}% AH)" if px["is_extended"] else "(EOD)"
        rows.append([
            tk, f"{ot[:3]}{'-S' if qty < 0 else ''}",
            f"{strk:.0f}", f"{dte}d",
            f"{spot_ext:.1f}{ext_tag}",
            f"{val_now:.2f}→{val_tmrw:.2f}({pnl_tmrw:+.0f}%)",
            rec,
        ])
        pnl_em = "🟢" if pnl_entry >= 0 else "🔴"
        tmrw_em = "🟢" if pnl_tmrw >= 0 else "🔴"
        decisions.append(
            f"{pnl_em} <b>{tk}</b> {ot[:3]} ${strk:.0f} [{side_s}, {dte}d]\n"
            f"   AH: <b>${spot_ext:.2f}</b> {ext_tag} | <i>{ah_src}</i>\n"
            f"   Option now <b>${val_now:.2f}</b>  vs entry <b>${entry:.2f}</b>  → P&amp;L vs entry: <b>${pnl_vs_entry_dol:+,.0f} ({pnl_entry:+.0f}%)</b>\n"
            f"   {tmrw_em} Tomorrow: <b>${val_tmrw:.2f}</b> ({pnl_tmrw:+.0f}% from now) | <b>{rec}</b>"
        )

    total_pnl_dol = total_pred_val - total_entry_val
    buf = _tbl_img(
        f"AFTER-MKT PREDICTOR  {datetime.now().strftime('%H:%M ET')}",
        ["Tkr", "Type", "Strk", "DTE", "AH Price", "Now→Tmrw(chg%)", "Action"],
        rows,
        right_cols={2, 3, 4, 5},
        highlight=hl,
        subtitle=(f"VIX: {vix_val:.1f}  |  IV est: {iv_base*100:.0f}%  |  "
                  f"Portfolio P&L tomorrow vs entry: ${total_pnl_dol:+,.0f}")
    )

    net_em = "🟢" if total_pnl_dol >= 0 else "🔴"
    summary = (
        f"{hdr('🌙 AFTER-MARKET PREDICTOR')}\n\n"
        f"<b>VIX:</b> {vix_val:.1f}  |  <b>IV base:</b> {iv_base*100:.0f}%\n\n"
        + "\n\n".join(decisions) +
        f"\n\n{net_em} <b>Portfolio P&amp;L tomorrow vs entry: ${total_pnl_dol:+,.0f}</b>\n"
        f"<i>Extended-hours prices from yfinance. Values = Black-Scholes, T−1 day.</i>"
    )

    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("⚠️ Overnight Risk", callback_data="menu_overnight_risk"),
        InlineKeyboardButton("💼 Positions", callback_data="menu_positions"),
        BACK_BTN
    ]])

    await query.message.reply_text(summary, parse_mode=H)
    await query.message.reply_photo(buf, caption="Per-position after-market prediction", reply_markup=kb)
    try: await _loading.delete()
    except Exception: pass


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
        log.info("Scheduled morning alert at 9:00 AM ET daily")
        # 15-min intraday alert (fires every 15 min; function checks market hours internally)
        job_queue.run_repeating(intraday_alert, interval=900, first=30)
        log.info("Scheduled 15-min intraday OI alert")
        # 10-min position monitor (fires during market hours; deduplicates via bot_data state)
        job_queue.run_repeating(position_monitor, interval=600, first=60)
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
