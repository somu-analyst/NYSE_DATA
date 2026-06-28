#!/usr/bin/env python3
"""
Event Writeup Runner — schedule via Task Scheduler or cron.

Examples:
  python run_event_writeups.py scan              # auto pre/post for today
  python run_event_writeups.py pre               # pre-event briefs only
  python run_event_writeups.py post              # post-event writeups
  python run_event_writeups.py post --event pce  # specific event (partial id match)
  python run_event_writeups.py anomaly           # intraday anomaly scan only
  python run_event_writeups.py discover        # refresh event calendar
  python run_event_writeups.py --telegram scan   # scan + send to Telegram
  python run_event_writeups.py --telegram post --llm  # LLM-enhanced post writeup
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "_lib"))

from event_writeup_engine import EventWriteupEngine, _now_et


def _load_telegram_creds():
    base = os.path.dirname(os.path.abspath(__file__))
    token_path = os.path.join(base, "us_bot_token.txt")
    chat_path = os.path.join(base, "us_chat_id.txt")
    if not os.path.exists(token_path) or not os.path.exists(chat_path):
        return None, None
    with open(token_path) as f:
        token = f.read().strip()
    with open(chat_path) as f:
        chat_id = f.read().strip()
    return token, chat_id


def send_telegram(text: str, html: bool = True) -> bool:
    token, chat_id = _load_telegram_creds()
    if not token or not chat_id:
        print("Telegram credentials not found — printing to stdout only")
        return False
    try:
        from telegram_rich_formatter import send_telegram_message
        send_telegram_message(token, chat_id, text, parse_mode="HTML" if html else "Markdown")
        return True
    except Exception as e:
        print(f"Telegram send failed: {e}")
        return False


def main():
    # Windows console UTF-8 for emoji in writeups
    if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass

    parser = argparse.ArgumentParser(description="Event writeup generator")
    parser.add_argument(
        "mode",
        choices=["scan", "pre", "post", "anomaly", "discover", "list"],
        help="Operation mode",
    )
    parser.add_argument("--event", help="Partial event_id match (e.g. pce, fomc, 2026-06-25)")
    parser.add_argument("--date", help="Target date YYYY-MM-DD (default: today ET)")
    parser.add_argument("--telegram", action="store_true", help="Send results to Telegram")
    parser.add_argument("--llm", action="store_true", help="Optional Anthropic polish")
    parser.add_argument("--no-save", action="store_true", help="Don't persist writeups")
    args = parser.parse_args()

    engine = EventWriteupEngine()
    now = _now_et()
    target_date = args.date or now.date().isoformat()

    if args.mode == "discover":
        evs = engine.discover_events(days_ahead=14, days_back=3)
        print(f"Discovered {len(evs)} events")
        for ev in sorted(evs, key=lambda e: (e.event_date, e.release_time)):
            print(f"  {ev.event_date} {ev.release_time}  {ev.impact:8}  {ev.name}")
        return

    if args.mode == "list":
        engine.discover_events(days_ahead=7, days_back=2)
        evs = engine.events_on_date(target_date) if args.date else engine.events_today()
        if not evs:
            print(f"No events on {target_date}")
        for ev in evs:
            print(f"{ev.event_id}  |  {ev.name}  |  {ev.impact}  |  est={ev.estimate} act={ev.actual}")
        return

    if args.mode == "anomaly":
        alerts = engine.detect_intraday_anomalies()
        if not alerts:
            print("No intraday anomalies detected")
            return
        lines = ["⚡ <b>INTRADAY ANOMALY SCAN</b>", ""]
        for a in alerts:
            lines.append(f"• [{a['severity']}] {a['description']}")
        text = "\n".join(lines)
        print(text.replace("<b>", "").replace("</b>", ""))
        if args.telegram:
            send_telegram(text)
        return

    # Resolve events for pre/post/scan
    engine.discover_events(days_ahead=1 if args.mode != "scan" else 7, days_back=2)
    if args.event:
        with engine._conn() as conn:
            rows = conn.execute("SELECT event_id FROM event_catalog").fetchall()
        event_ids = [r["event_id"] for r in rows if args.event.lower() in r["event_id"].lower()]
        events = [engine.get_event(eid) for eid in event_ids]
        events = [e for e in events if e]
    else:
        events = engine.events_on_date(target_date) if args.date else engine.events_today()

    if args.mode == "scan":
        writeups = engine.scan_today(phase="auto")
        for w in writeups:
            print(w)
            print("\n" + "=" * 60 + "\n")
        if args.telegram and writeups:
            for w in writeups:
                send_telegram(engine.format_telegram(w))
        return

    phase = "pre" if args.mode == "pre" else "post"
    if not events:
        if phase == "post":
            alerts = engine.detect_intraday_anomalies()
            if alerts:
                ev = engine.get_event(f"{target_date}_intraday_anomaly")
                if not ev:
                    from event_writeup_engine import MarketEvent
                    ev = MarketEvent(
                        event_id=f"{target_date}_intraday_anomaly",
                        name="Intraday Market Anomaly",
                        category="Market Structure",
                        event_date=target_date,
                        release_time=now.strftime("%H:%M"),
                        impact="HIGH",
                        source="detector",
                    )
                    engine._upsert_event(ev)
                events = [ev]
        if not events:
            print(f"No events to write up for {target_date}")
            return

    for ev in events:
        text = engine.generate_writeup(
            ev.event_id, phase=phase, use_llm=args.llm, save=not args.no_save
        )
        print(text)
        print("\n" + "=" * 60 + "\n")
        if args.telegram:
            send_telegram(engine.format_telegram(text))


if __name__ == "__main__":
    main()
