#!/usr/bin/env python3
"""
Telegram bot hooks for event writeups — import from telegram_bot_optimized.py.

Scheduled times (ET):
  8:25 AM  — pre-event brief (T-5 min before typical 8:30 releases)
  9:35 AM  — post-open reaction writeup
  10:05 AM — post-event follow-up (30 min after 9:30 open)
  Every 15 min during market hours — anomaly scan (deduped)
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timedelta, timezone

log = logging.getLogger(__name__)

DB_PATH = r"C:\Users\srini\Options_chain_data\US_data.db"


def _ensure_dedup(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS alert_dedup (
            alert_date TEXT NOT NULL,
            grp_key    TEXT NOT NULL,
            atype      TEXT NOT NULL,
            PRIMARY KEY (alert_date, grp_key, atype)
        )
    """)
    conn.commit()


def _already_sent(conn, today_str, grp_key, atype) -> bool:
    try:
        conn.execute(
            "INSERT INTO alert_dedup (alert_date, grp_key, atype) VALUES (?, ?, ?)",
            (today_str, grp_key, atype),
        )
        conn.commit()
        return False
    except sqlite3.IntegrityError:
        return True


def _in_market_hours(now_utc) -> bool:
    if now_utc.weekday() >= 5:
        return False
    hm = now_utc.hour * 60 + now_utc.minute
    return 14 * 60 + 25 <= hm <= 21 * 60  # ~9:25 AM - 4:00 PM ET


async def event_pre_brief_alert(ctx):
    """8:25 AM ET — pre-event brief for today's macro releases."""
    from event_writeup_engine import EventWriteupEngine

    now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
    now_et = now_utc - timedelta(hours=5)
    today_str = now_et.date().isoformat()

    engine = EventWriteupEngine()
    events = engine.events_today()
    if not events:
        return

    # Import bot helpers lazily
    from telegram_bot_optimized import get_conn, load_creds, H

    conn = get_conn()
    try:
        _ensure_dedup(conn)
        _, chat_id = load_creds()
        for ev in events:
            if _already_sent(conn, today_str, ev.event_id, "event_pre"):
                continue
            text = engine.generate_writeup(ev.event_id, phase="pre", save=True)
            html = engine.format_telegram(text)
            await ctx.bot.send_message(chat_id=chat_id, text=html, parse_mode=H)
    except Exception as e:
        log.warning(f"event_pre_brief_alert failed: {e}")
    finally:
        conn.close()


async def event_post_writeup_alert(ctx):
    """Post-release / post-open writeup."""
    from event_writeup_engine import EventWriteupEngine

    now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
    if not _in_market_hours(now_utc):
        return
    now_et = now_utc - timedelta(hours=5)
    today_str = now_et.date().isoformat()

    from telegram_bot_optimized import get_conn, load_creds, H

    engine = EventWriteupEngine()
    events = engine.events_today()
    if not events:
        anomalies = engine.detect_intraday_anomalies()
        if not anomalies:
            return
        from event_writeup_engine import MarketEvent
        ev = MarketEvent(
            event_id=f"{today_str}_intraday_anomaly",
            name="Intraday Market Anomaly",
            category="Market Structure",
            event_date=today_str,
            release_time=now_et.strftime("%H:%M"),
            impact="HIGH",
            source="detector",
        )
        engine._upsert_event(ev)
        events = [ev]

    conn = get_conn()
    try:
        _ensure_dedup(conn)
        _, chat_id = load_creds()
        atype = f"event_post_{now_et.hour:02d}{now_et.minute:02d}"
        for ev in events:
            key = f"{ev.event_id}_{atype}"
            if _already_sent(conn, today_str, key, "event_post"):
                continue
            text = engine.generate_writeup(ev.event_id, phase="post", save=True)
            html = engine.format_telegram(text)
            await ctx.bot.send_message(chat_id=chat_id, text=html, parse_mode=H)
    except Exception as e:
        log.warning(f"event_post_writeup_alert failed: {e}")
    finally:
        conn.close()


async def event_anomaly_scan(ctx):
    """Lightweight anomaly scan during market hours — only alerts on HIGH/CRITICAL."""
    from event_writeup_engine import EventWriteupEngine

    now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
    if not _in_market_hours(now_utc):
        return
    now_et = now_utc - timedelta(hours=5)
    today_str = now_et.date().isoformat()

    from telegram_bot_optimized import get_conn, load_creds, H

    engine = EventWriteupEngine()
    alerts = engine.detect_intraday_anomalies()
    severe = [a for a in alerts if a.get("severity") in ("HIGH", "CRITICAL")]
    if not severe:
        return

    conn = get_conn()
    try:
        _ensure_dedup(conn)
        _, chat_id = load_creds()
        for a in severe:
            key = a["type"] + "_" + (a.get("description") or "")[:30]
            if _already_sent(conn, today_str, key, "anomaly"):
                continue
            msg = f"⚠️ <b>MARKET ANOMALY</b>\n\n{a['description']}"
            await ctx.bot.send_message(chat_id=chat_id, text=msg, parse_mode=H)
    except Exception as e:
        log.warning(f"event_anomaly_scan failed: {e}")
    finally:
        conn.close()


def register_event_writeup_jobs(job_queue, log_fn=None):
    """Call from telegram_bot main() after job_queue is created."""
    from datetime import time as dt_time

    if not job_queue:
        return
    try:
        import sys
        import os
        lib = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_lib")
        if lib not in sys.path:
            sys.path.insert(0, lib)

        job_queue.run_daily(event_pre_brief_alert, time=dt_time(13, 25, 0))   # 8:25 AM ET
        job_queue.run_daily(event_post_writeup_alert, time=dt_time(14, 35, 0))  # 9:35 AM ET
        job_queue.run_daily(event_post_writeup_alert, time=dt_time(15, 5, 0))   # 10:05 AM ET
        job_queue.run_repeating(event_anomaly_scan, interval=900, first=120)
        if log_fn:
            log_fn.info("Scheduled event writeup jobs (pre 8:25, post 9:35/10:05, anomaly 15m)")
    except Exception as e:
        if log_fn:
            log_fn.warning(f"Could not register event writeup jobs: {e}")
