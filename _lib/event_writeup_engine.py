#!/usr/bin/env python3
"""
Event Writeup Engine — automated pre/post market event narratives.

Discovers macro releases, earnings, and intraday regime breaks; captures
market snapshots; generates commentary in the style of institutional morning
notes (timeline → data → causality → cross-asset → structure → outlook).

Usage:
    from event_writeup_engine import EventWriteupEngine
    engine = EventWriteupEngine()
    engine.scan_today()
    print(engine.generate_writeup(event_id, phase="post"))
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

import yfinance as yf

DB_PATH = os.environ.get(
    "US_DATA_DB", r"C:\Users\srini\Options_chain_data\US_data.db"
)
ET = ZoneInfo("America/New_York")

# ── Event taxonomy ────────────────────────────────────────────────────────────

MACRO_KEYWORDS = {
    "pce": ("PCE", "Inflation", "HIGH", "08:30"),
    "core pce": ("Core PCE", "Inflation", "HIGH", "08:30"),
    "personal income": ("Personal Income", "Consumer", "MEDIUM", "08:30"),
    "personal spending": ("Personal Spending", "Consumer", "MEDIUM", "08:30"),
    "cpi": ("CPI", "Inflation", "HIGH", "08:30"),
    "consumer price": ("CPI", "Inflation", "HIGH", "08:30"),
    "ppi": ("PPI", "Inflation", "MEDIUM", "08:30"),
    "producer price": ("PPI", "Inflation", "MEDIUM", "08:30"),
    "nonfarm": ("Jobs Report", "Labor", "HIGH", "08:30"),
    "non-farm": ("Jobs Report", "Labor", "HIGH", "08:30"),
    "payroll": ("Jobs Report", "Labor", "HIGH", "08:30"),
    "unemployment": ("Unemployment Rate", "Labor", "HIGH", "08:30"),
    "jobless claims": ("Jobless Claims", "Labor", "MEDIUM", "08:30"),
    "initial claims": ("Jobless Claims", "Labor", "MEDIUM", "08:30"),
    "gdp": ("GDP", "Growth", "HIGH", "08:30"),
    "gross domestic": ("GDP", "Growth", "HIGH", "08:30"),
    "retail sales": ("Retail Sales", "Consumer", "HIGH", "08:30"),
    "fomc": ("FOMC Decision", "Fed Policy", "CRITICAL", "14:00"),
    "fed interest rate": ("FOMC Decision", "Fed Policy", "CRITICAL", "14:00"),
    "ism manufacturing": ("ISM Manufacturing", "Growth", "MEDIUM", "10:00"),
    "ism services": ("ISM Services", "Growth", "MEDIUM", "10:00"),
    "consumer confidence": ("Consumer Confidence", "Consumer", "MEDIUM", "10:00"),
}

FRED_SERIES = {
    "PCE": ("PCEPI", "PCE Price Index", "yoy"),
    "Core PCE": ("PCEPILFE", "Core PCE Price Index", "yoy"),
    "CPI": ("CPIAUCSL", "CPI All Urban", "yoy"),
    "Jobs Report": ("PAYEMS", "Nonfarm Payrolls", "mom_k"),
    "Unemployment Rate": ("UNRATE", "Unemployment Rate", "level"),
    "GDP": ("GDP", "Real GDP", "qoq"),
}

MARKET_SYMBOLS = {
    "nq_fut": "NQ=F",
    "es_fut": "ES=F",
    "nasdaq": "^IXIC",
    "sp500": "^GSPC",
    "qqq": "QQQ",
    "spy": "SPY",
    "vix": "^VIX",
    "btc": "BTC-USD",
    "tnx": "^TNX",
    "dxy": "DX-Y.NYB",
    "gold": "GC=F",
    "oil": "CL=F",
}

LEVERAGE_ETFS = {
    "TQQQ": "3x Nasdaq 100",
    "SOXL": "3x Semiconductors",
    "UPRO": "3x S&P 500",
    "TECL": "3x Technology",
}

WATCH_TICKERS = ["AAPL", "NVDA", "MSFT", "AMZN", "META", "TSLA", "GOOGL", "MU", "SNDK"]


@dataclass
class MarketEvent:
    event_id: str
    name: str
    category: str
    event_date: str          # YYYY-MM-DD
    release_time: str        # HH:MM ET
    impact: str
    source: str
    estimate: Optional[float] = None
    actual: Optional[float] = None
    prior: Optional[float] = None
    unit: str = "%"
    related_tickers: List[str] = field(default_factory=list)
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class MarketSnapshot:
    captured_at: str
    phase: str               # pre | at_release | post_30 | post_60 | eod | anomaly
    prices: Dict[str, Dict[str, float]]
    news_headlines: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)


# ── Schema ────────────────────────────────────────────────────────────────────

DDL = """
CREATE TABLE IF NOT EXISTS event_catalog (
    event_id        TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    category        TEXT,
    event_date      TEXT NOT NULL,
    release_time    TEXT DEFAULT '08:30',
    impact          TEXT DEFAULT 'MEDIUM',
    source          TEXT,
    estimate        REAL,
    actual          REAL,
    prior           REAL,
    unit            TEXT DEFAULT '%',
    related_tickers TEXT,
    extra_json      TEXT,
    created_at      TEXT DEFAULT (datetime('now')),
    updated_at      TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS event_snapshots (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id        TEXT,
    captured_at     TEXT NOT NULL,
    phase           TEXT NOT NULL,
    prices_json     TEXT,
    news_json       TEXT,
    metadata_json   TEXT,
    FOREIGN KEY (event_id) REFERENCES event_catalog(event_id)
);

CREATE TABLE IF NOT EXISTS event_writeups (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id        TEXT NOT NULL,
    phase           TEXT NOT NULL,
    writeup_text    TEXT NOT NULL,
    data_json       TEXT,
    generated_at    TEXT DEFAULT (datetime('now')),
    UNIQUE(event_id, phase)
);

CREATE TABLE IF NOT EXISTS market_regime_alerts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    alert_date      TEXT NOT NULL,
    alert_type      TEXT NOT NULL,
    severity        TEXT,
    description     TEXT,
    data_json       TEXT,
    created_at      TEXT DEFAULT (datetime('now')),
    UNIQUE(alert_date, alert_type)
);
"""


def _now_et() -> datetime:
    return datetime.now(ET)


def _fmt_pct(v: Optional[float], signed: bool = True) -> str:
    if v is None:
        return "n/a"
    return f"{v:+.2f}%" if signed else f"{v:.2f}%"


def _fmt_num(v: Optional[float], decimals: int = 1) -> str:
    if v is None:
        return "n/a"
    if abs(v) >= 1_000_000_000:
        return f"${v/1e9:.1f}B"
    if abs(v) >= 1_000_000:
        return f"${v/1e6:.0f}M"
    if abs(v) >= 1_000:
        return f"{v:,.0f}"
    return f"{v:.{decimals}f}"


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")[:40]


def _parse_finnhub_event(raw: dict) -> Optional[MarketEvent]:
    """Normalize a Finnhub economic calendar row."""
    event_name = (raw.get("event") or raw.get("title") or "").strip()
    if not event_name:
        return None
    lower = event_name.lower()
    matched = None
    # Longer keywords first (e.g. "core pce" before "pce")
    for kw, meta in sorted(MACRO_KEYWORDS.items(), key=lambda x: -len(x[0])):
        if kw in lower:
            matched = meta
            break
    if not matched:
        if raw.get("impact", "").lower() not in ("high", "medium"):
            return None
        matched = (event_name[:40], "Macro", raw.get("impact", "MEDIUM").upper(), "08:30")

    name, category, impact, default_time = matched
    event_date = raw.get("date") or raw.get("time", "")[:10]
    if not event_date:
        return None

    est = raw.get("estimate")
    act = raw.get("actual")
    prev = raw.get("prev")
    unit = raw.get("unit") or "%"
    release_time = default_time
    if raw.get("time") and len(str(raw.get("time"))) > 10:
        try:
            release_time = datetime.fromisoformat(str(raw["time"]).replace("Z", "+00:00")).astimezone(ET).strftime("%H:%M")
        except Exception:
            pass

    eid = f"{event_date}_{_slug(name)}"
    return MarketEvent(
        event_id=eid,
        name=name,
        category=category,
        event_date=event_date,
        release_time=release_time,
        impact=impact,
        source="finnhub",
        estimate=_safe_float(est),
        actual=_safe_float(act),
        prior=_safe_float(prev),
        unit=unit,
    )


def _safe_float(v) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


class EventWriteupEngine:
    """Discover events, capture market data, generate pre/post writeups."""

    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self._ensure_schema()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self):
        with self._conn() as conn:
            conn.executescript(DDL)
            conn.commit()

    # ── Event discovery ───────────────────────────────────────────────────────

    def discover_events(
        self, days_ahead: int = 7, days_back: int = 2, include_heuristic: bool = True
    ) -> List[MarketEvent]:
        """Merge Finnhub calendar, heuristic calendar, and stored catalog."""
        today = _now_et().date()
        from_date = (today - timedelta(days=days_back)).isoformat()
        to_date = (today + timedelta(days=days_ahead)).isoformat()
        events: Dict[str, MarketEvent] = {}

        # Finnhub
        try:
            from news_and_earnings import get_economic_calendar
            raw = get_economic_calendar(from_date, to_date)
            if isinstance(raw, list):
                for row in raw:
                    ev = _parse_finnhub_event(row)
                    if ev:
                        events[ev.event_id] = ev
        except Exception:
            pass

        # Heuristic fallback (market_news_enhanced)
        if include_heuristic:
            try:
                from market_news_enhanced import get_economic_calendar_detailed
                for row in get_economic_calendar_detailed():
                    days_until = row.get("days_until", 99)
                    if days_until < -days_back or days_until > days_ahead:
                        continue
                    ev_date = (today + timedelta(days=days_until)).isoformat()
                    raw_name = row.get("event", "Event")
                    name = re.sub(r"[^\w\s]", "", raw_name).strip()
                    for emoji in ("🏛️", "💼", "📊", "📈", "🏭", "💰", "🛍️"):
                        name = name.replace(emoji, "").strip()
                    eid = f"{ev_date}_{_slug(name)}"
                    if eid not in events:
                        events[eid] = MarketEvent(
                            event_id=eid,
                            name=name or raw_name,
                            category=row.get("category", "Macro"),
                            event_date=ev_date,
                            release_time="08:30",
                            impact=row.get("impact", "MEDIUM"),
                            source="heuristic",
                        )
            except Exception:
                pass

        # Persist
        for ev in events.values():
            self._upsert_event(ev)
        return list(events.values())

    def _upsert_event(self, ev: MarketEvent):
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO event_catalog (
                    event_id, name, category, event_date, release_time, impact,
                    source, estimate, actual, prior, unit, related_tickers, extra_json,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                ON CONFLICT(event_id) DO UPDATE SET
                    estimate=excluded.estimate, actual=excluded.actual,
                    prior=excluded.prior, updated_at=datetime('now')
                """,
                (
                    ev.event_id, ev.name, ev.category, ev.event_date,
                    ev.release_time, ev.impact, ev.source,
                    ev.estimate, ev.actual, ev.prior, ev.unit,
                    json.dumps(ev.related_tickers), json.dumps(ev.extra),
                ),
            )
            conn.commit()

    def get_event(self, event_id: str) -> Optional[MarketEvent]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM event_catalog WHERE event_id=?", (event_id,)
            ).fetchone()
        if not row:
            return None
        tickers = json.loads(row["related_tickers"] or "[]")
        extra = json.loads(row["extra_json"] or "{}")
        return MarketEvent(
            event_id=row["event_id"],
            name=row["name"],
            category=row["category"],
            event_date=row["event_date"],
            release_time=row["release_time"],
            impact=row["impact"],
            source=row["source"],
            estimate=row["estimate"],
            actual=row["actual"],
            prior=row["prior"],
            unit=row["unit"] or "%",
            related_tickers=tickers,
            extra=extra,
        )

    def events_today(self) -> List[MarketEvent]:
        today = _now_et().date().isoformat()
        self.discover_events(days_ahead=1, days_back=0)
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT event_id FROM event_catalog WHERE event_date=? ORDER BY release_time",
                (today,),
            ).fetchall()
        return [self.get_event(r["event_id"]) for r in rows if self.get_event(r["event_id"])]

    def events_on_date(self, date_str: str) -> List[MarketEvent]:
        self.discover_events(days_ahead=0, days_back=0)
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT event_id FROM event_catalog WHERE event_date=? ORDER BY release_time",
                (date_str,),
            ).fetchall()
        return [self.get_event(r["event_id"]) for r in rows if self.get_event(r["event_id"])]

    # ── Market data capture ───────────────────────────────────────────────────

    def capture_snapshot(
        self,
        phase: str = "pre",
        event_id: Optional[str] = None,
        extra_symbols: Optional[List[str]] = None,
    ) -> MarketSnapshot:
        """Capture cross-asset prices + recent headlines."""
        symbols = dict(MARKET_SYMBOLS)
        if extra_symbols:
            for tk in extra_symbols:
                symbols[tk.lower()] = tk

        prices: Dict[str, Dict[str, float]] = {}
        for label, sym in symbols.items():
            try:
                h = yf.Ticker(sym).history(period="5d", interval="5m")
                if h.empty:
                    h = yf.Ticker(sym).history(period="5d")
                if h.empty:
                    continue
                px = float(h["Close"].iloc[-1])
                op = float(h["Open"].iloc[0]) if phase != "pre" else float(h["Close"].iloc[-2] if len(h) > 1 else h["Open"].iloc[0])
                prev_close = float(h["Close"].iloc[-2]) if len(h) > 1 else px
                chg_pct = (px - op) / op * 100 if op else 0
                day_chg = (px - prev_close) / prev_close * 100 if prev_close else 0
                prices[label] = {
                    "symbol": sym,
                    "price": px,
                    "open_chg_pct": chg_pct,
                    "day_chg_pct": day_chg,
                    "session_open": op,
                }
            except Exception:
                continue

        headlines = self._fetch_headlines(limit=8)
        snap = MarketSnapshot(
            captured_at=_now_et().isoformat(),
            phase=phase,
            prices=prices,
            news_headlines=headlines,
            metadata={"leverage_etfs": self._leverage_etf_stats()},
        )
        self._store_snapshot(snap, event_id)
        return snap

    def capture_intraday_window(
        self, symbol: str = "NQ=F", interval: str = "5m"
    ) -> Dict[str, Any]:
        """Analyze today's session: open → now, and max drawdown/rally."""
        try:
            h = yf.Ticker(symbol).history(period="1d", interval=interval)
            if h.empty or len(h) < 2:
                return {}
            open_px = float(h["Open"].iloc[0])
            now_px = float(h["Close"].iloc[-1])
            high = float(h["High"].max())
            low = float(h["Low"].min())
            open_chg = (now_px - open_px) / open_px * 100
            max_rally = (high - open_px) / open_px * 100
            max_drop = (low - open_px) / open_px * 100
            peak_to_trough = (low - high) / high * 100 if high else 0

            # Find largest 30-min swing
            window_swings = []
            for i in range(len(h)):
                for mins in (6, 12):  # 30min, 60min in 5m bars
                    if i + mins >= len(h):
                        continue
                    start = float(h["Close"].iloc[i])
                    end = float(h["Close"].iloc[i + mins])
                    swing = (end - start) / start * 100
                    window_swings.append((abs(swing), swing, i, mins))
            best_swing = max(window_swings, key=lambda x: x[0]) if window_swings else None

            return {
                "symbol": symbol,
                "open": open_px,
                "now": now_px,
                "high": high,
                "low": low,
                "open_to_now_pct": open_chg,
                "max_rally_from_open_pct": max_rally,
                "max_drop_from_open_pct": max_drop,
                "peak_to_trough_pct": peak_to_trough,
                "largest_swing": {
                    "pct": best_swing[1] if best_swing else None,
                    "bars": best_swing[3] * 5 if best_swing else None,
                },
                "bars": len(h),
            }
        except Exception:
            return {}

    def _leverage_etf_stats(self) -> Dict[str, Dict]:
        out = {}
        for sym, desc in LEVERAGE_ETFS.items():
            try:
                t = yf.Ticker(sym)
                info = t.info or {}
                h = t.history(period="5d")
                chg_5d = None
                if len(h) >= 2:
                    chg_5d = (float(h["Close"].iloc[-1]) - float(h["Close"].iloc[0])) / float(h["Close"].iloc[0]) * 100
                out[sym] = {
                    "description": desc,
                    "aum": info.get("totalAssets"),
                    "day_chg_pct": self._day_chg(h) if not h.empty else None,
                    "chg_5d_pct": chg_5d,
                }
            except Exception:
                continue
        return out

    @staticmethod
    def _day_chg(h) -> Optional[float]:
        if len(h) < 2:
            return None
        return (float(h["Close"].iloc[-1]) - float(h["Close"].iloc[-2])) / float(h["Close"].iloc[-2]) * 100

    def _fetch_headlines(self, limit: int = 8) -> List[str]:
        headlines = []
        try:
            from market_news_enhanced import get_aggregated_news
            for item in get_aggregated_news(limit=limit):
                title = item.get("title") or item.get("headline") or ""
                if title:
                    headlines.append(title[:120])
        except Exception:
            pass
        if not headlines:
            try:
                from market_news_aggregator import get_general_market_news
                for item in get_general_market_news(limit=limit):
                    headlines.append((item.get("headline") or "")[:120])
            except Exception:
                pass
        return headlines[:limit]

    def _store_snapshot(self, snap: MarketSnapshot, event_id: Optional[str]):
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO event_snapshots
                    (event_id, captured_at, phase, prices_json, news_json, metadata_json)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    snap.captured_at,
                    snap.phase,
                    json.dumps(snap.prices),
                    json.dumps(snap.news_headlines),
                    json.dumps(snap.metadata),
                ),
            )
            conn.commit()

    # ── Anomaly detection (flash moves, opening reversals) ────────────────────

    def detect_intraday_anomalies(self) -> List[Dict[str, Any]]:
        """Flag large opening reversals or rapid index moves."""
        alerts = []
        today = _now_et().date().isoformat()
        nq = self.capture_intraday_window("NQ=F")
        es = self.capture_intraday_window("ES=F")

        for label, data, threshold in (
            ("NDX_OPEN_REVERSAL", nq, 2.0),
            ("SPX_OPEN_REVERSAL", es, 1.5),
        ):
            if not data:
                continue
            rally = data.get("max_rally_from_open_pct", 0)
            drop = data.get("max_drop_from_open_pct", 0)
            now = data.get("open_to_now_pct", 0)
            # Opened up then sold off hard, or vice versa
            if rally > 0.5 and now < -threshold:
                alerts.append({
                    "type": label,
                    "severity": "HIGH" if abs(now) > threshold * 1.5 else "MEDIUM",
                    "description": (
                        f"Opened +{rally:.1f}% then reversed to {now:+.1f}% "
                        f"(swing {rally - now:.1f} pts)"
                    ),
                    "data": data,
                })
            elif drop < -0.5 and now > threshold:
                alerts.append({
                    "type": label + "_RECOVERY",
                    "severity": "MEDIUM",
                    "description": f"Dipped {drop:.1f}% from open, now {now:+.1f}%",
                    "data": data,
                })

            swing = (data.get("largest_swing") or {}).get("pct")
            if swing and abs(swing) >= 1.5:
                mins = (data.get("largest_swing") or {}).get("bars") or 30
                alerts.append({
                    "type": "RAPID_SWING",
                    "severity": "HIGH" if abs(swing) >= 2.5 else "MEDIUM",
                    "description": f"{abs(swing):.1f}% move in ~{mins} minutes on {data.get('symbol')}",
                    "data": data,
                })

        # Ticker shock detection (e.g. AAPL -6%)
        for tk in WATCH_TICKERS:
            try:
                h = yf.Ticker(tk).history(period="2d", interval="5m")
                if len(h) < 10:
                    continue
                day_open = float(h["Open"].iloc[0])
                now_px = float(h["Close"].iloc[-1])
                chg = (now_px - day_open) / day_open * 100
                if abs(chg) >= 4.0:
                    mcap_loss = None
                    try:
                        info = yf.Ticker(tk).info or {}
                        mcap = info.get("marketCap")
                        if mcap:
                            mcap_loss = mcap * (chg / 100)
                    except Exception:
                        pass
                    alerts.append({
                        "type": "TICKER_SHOCK",
                        "severity": "CRITICAL" if abs(chg) >= 5 else "HIGH",
                        "description": f"{tk} {chg:+.1f}% from open",
                        "data": {"ticker": tk, "chg_pct": chg, "mcap_impact": mcap_loss},
                    })
            except Exception:
                continue

        with self._conn() as conn:
            for a in alerts:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO market_regime_alerts
                        (alert_date, alert_type, severity, description, data_json)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (today, a["type"], a["severity"], a["description"], json.dumps(a["data"])),
                )
            conn.commit()
        return alerts

    # ── FRED / economic context ───────────────────────────────────────────────

    def _fred_latest(self, series_id: str) -> Optional[Tuple[float, str]]:
        key = os.environ.get("FRED_API_KEY", "")
        if not key:
            return None
        try:
            url = (
                f"https://api.stlouisfed.org/fred/series/observations"
                f"?series_id={series_id}&api_key={key}&file_type=json&sort_order=desc&limit=2"
            )
            with urllib.request.urlopen(url, timeout=8) as resp:
                data = json.loads(resp.read().decode())
            obs = [o for o in data.get("observations", []) if o.get("value") != "."]
            if not obs:
                return None
            return float(obs[0]["value"]), obs[0]["date"]
        except Exception:
            return None

    def _inflation_context(self, event_name: str) -> Dict[str, Any]:
        ctx: Dict[str, Any] = {"fed_target": 2.0}
        for key, (sid, label, kind) in FRED_SERIES.items():
            if key.lower() not in event_name.lower() and event_name.lower() not in key.lower():
                continue
            latest = self._fred_latest(sid)
            if latest:
                val, dt = latest
                ctx["series"] = {"id": sid, "label": label, "value": val, "date": dt, "kind": kind}
                if "pce" in key.lower() or "cpi" in key.lower():
                    ctx["vs_fed_target"] = val - 2.0 if kind == "yoy" else None
        return ctx

    # ── Narrative generation ──────────────────────────────────────────────────

    def gather_context(self, event: MarketEvent, phase: str = "post") -> Dict[str, Any]:
        snap = self.capture_snapshot(phase=phase, event_id=event.event_id)
        nq_win = self.capture_intraday_window("NQ=F")
        es_win = self.capture_intraday_window("ES=F")
        btc_win = self.capture_intraday_window("BTC-USD")
        anomalies = self.detect_intraday_anomalies()
        inflation = self._inflation_context(event.name)

        # Related ticker moves
        ticker_moves = {}
        tickers = event.related_tickers or WATCH_TICKERS[:5]
        for tk in tickers:
            try:
                h = yf.Ticker(tk).history(period="1d", interval="5m")
                if h.empty:
                    continue
                op = float(h["Open"].iloc[0])
                now = float(h["Close"].iloc[-1])
                ticker_moves[tk] = (now - op) / op * 100
            except Exception:
                continue

        # News matching event
        matched_news = []
        keywords = event.name.lower().split() + ["inflation", "fed", "rate", "apple", "price"]
        for h in snap.news_headlines:
            hl = h.lower()
            if any(k in hl for k in keywords if len(k) > 3):
                matched_news.append(h)

        return {
            "event": asdict(event),
            "phase": phase,
            "snapshot": asdict(snap),
            "nq_window": nq_win,
            "es_window": es_win,
            "btc_window": btc_win,
            "anomalies": anomalies,
            "inflation": inflation,
            "ticker_moves": ticker_moves,
            "matched_news": matched_news,
            "generated_at": _now_et().isoformat(),
        }

    def build_pre_writeup(self, ctx: Dict[str, Any]) -> str:
        ev = ctx["event"]
        snap = ctx["snapshot"]
        prices = snap.get("prices", {})
        lines = []

        lines.append(f"📋 PRE-EVENT BRIEF — {ev['name']}")
        lines.append(f"📅 {ev['event_date']} · Release {ev['release_time']} ET · Impact: {ev['impact']}")
        lines.append("")

        # Setup
        nq = prices.get("nq_fut", {})
        vix = prices.get("vix", {})
        if nq:
            lines.append(
                f"Setup: NQ {nq.get('price', 0):,.0f} ({_fmt_pct(nq.get('day_chg_pct'))} on day), "
                f"VIX {vix.get('price', 0):.1f}" if vix else
                f"Setup: NQ {nq.get('price', 0):,.0f} ({_fmt_pct(nq.get('day_chg_pct'))} on day)"
            )

        if ev.get("estimate") is not None:
            lines.append(f"Consensus: {ev['estimate']}{ev.get('unit', '%')}", )
            if ev.get("prior") is not None:
                lines.append(f"Prior: {ev['prior']}{ev.get('unit', '%')}")

        infl = ctx.get("inflation", {})
        if infl.get("vs_fed_target") is not None:
            lines.append(
                f"Context: {infl['series']['label']} at {infl['series']['value']:.1f}% — "
                f"{infl['vs_fed_target']:+.1f}pp vs Fed's 2.0% target"
            )

        lines.append("")
        lines.append("Scenarios:")
        if "inflation" in ev["category"].lower() or "pce" in ev["name"].lower() or "cpi" in ev["name"].lower():
            lines.append("  🟢 Cooler-than-expected → risk-on, yields down, growth/tech bid")
            lines.append("  🔴 Hotter-than-expected → yields up, USD firm, multiple compression")
            lines.append("  ⚖️ In-line → focus shifts to revisions, super-core, and Fed speak")
        elif "labor" in ev["category"].lower():
            lines.append("  🟢 Soft jobs / higher unemployment → cut hopes, duration rally")
            lines.append("  🔴 Strong NFP + firm wages → higher-for-longer, financials vs growth")
        elif "fed" in ev["category"].lower():
            lines.append("  🟢 Dovish hold/cut → bull steepener, small caps, gold")
            lines.append("  🔴 Hawkish hold/hike → USD up, EM stress, vol expansion")
        else:
            lines.append("  🟢 Beat + risk-on backdrop → buy dips in leaders")
            lines.append("  🔴 Miss + fragile positioning → gap risk, hedges pay")

        lev = (snap.get("metadata") or {}).get("leverage_etfs", {})
        if lev:
            lines.append("")
            lines.append("Leverage watch (amplifies reaction):")
            for sym, d in list(lev.items())[:3]:
                aum = _fmt_num(d.get("aum")) if d.get("aum") else "n/a"
                lines.append(f"  {sym} ({d.get('description')}): AUM ~{aum}, day {_fmt_pct(d.get('day_chg_pct'))}")

        if snap.get("news_headlines"):
            lines.append("")
            lines.append("Headlines in play:")
            for h in snap["news_headlines"][:4]:
                lines.append(f"  • {h}")

        lines.append("")
        lines.append("Watch: ES/NQ reaction in first 15 min; VIX term; 2Y yield; Mag7 single-name gaps.")
        return "\n".join(lines)

    def build_post_writeup(self, ctx: Dict[str, Any]) -> str:
        """Generate narrative similar to institutional intraday commentary."""
        ev = ctx["event"]
        nq = ctx.get("nq_window") or {}
        es = ctx.get("es_window") or {}
        btc = ctx.get("btc_window") or {}
        snap = ctx.get("snapshot") or {}
        prices = snap.get("prices", {})
        anomalies = ctx.get("anomalies") or []
        matched_news = ctx.get("matched_news") or []
        ticker_moves = ctx.get("ticker_moves") or {}

        lines = []
        now_et = _now_et()

        # ── Headline hook ──
        nq_pts = None
        if nq.get("open") and nq.get("now"):
            nq_pts = nq["now"] - nq["open"]
        nq_pct = nq.get("open_to_now_pct")
        es_pct = es.get("open_to_now_pct")

        hook_parts = []
        if nq_pct is not None and abs(nq_pct) >= 1.0:
            direction = "rallied" if nq_pct > 0 else "fell"
            hook_parts.append(f"Nasdaq {direction} {_fmt_pct(nq_pct)} from the open")
            if nq_pts and abs(nq_pts) >= 50:
                hook_parts.append(f"({nq_pts:+,.0f} NQ points)")
        if es_pct is not None and abs(es_pct) >= 0.5:
            hook_parts.append(f"S&P {_fmt_pct(es_pct)}")

        lines.append("⚡ WHAT JUST HAPPENED?")
        if hook_parts:
            lines.append("In today's session, " + ", ".join(hook_parts) + ".")
        else:
            lines.append(f"Markets reacting to {ev['name']} — session still developing as of {now_et.strftime('%H:%M ET')}.")
        lines.append("")

        # ── Timeline ──
        lines.append("TIMELINE")
        if ev.get("release_time"):
            lines.append(f"  {ev['release_time']} ET — {ev['name']} release")
            if ev.get("actual") is not None:
                beat = ev.get("estimate") is not None and ev["actual"] < ev["estimate"] if "inflation" in ev["name"].lower() else ev["actual"] > ev.get("estimate")
                surprise = ""
                if ev.get("estimate") is not None:
                    surprise = f" (est {ev['estimate']}{ev.get('unit','')}, {'beat' if beat else 'miss'})"
                lines.append(f"    Print: {ev['actual']}{ev.get('unit','')}{surprise}")
            elif ev.get("estimate") is not None:
                lines.append(f"    Expected: {ev['estimate']}{ev.get('unit','')}")

        lines.append("  09:30 ET — Cash open")
        if nq.get("max_rally_from_open_pct") and nq.get("open_to_now_pct") is not None:
            if nq["max_rally_from_open_pct"] > 0.3 and nq["open_to_now_pct"] < -1:
                lines.append(
                    f"    NQ opened +{nq['max_rally_from_open_pct']:.1f}% then reversed to "
                    f"{nq['open_to_now_pct']:+.1f}% — classic opening fade"
                )
            else:
                lines.append(f"    NQ session: {_fmt_pct(nq.get('open_to_now_pct'))} from open")

        for a in anomalies[:2]:
            lines.append(f"  ⚠ {a['description']}")

        for h in matched_news[:2]:
            lines.append(f"  📰 {h}")

        lines.append("")

        # ── Macro data ──
        infl = ctx.get("inflation", {})
        if ev.get("actual") is not None or infl.get("series"):
            lines.append("THE DATA")
            if ev.get("actual") is not None:
                lines.append(f"  {ev['name']}: {ev['actual']}{ev.get('unit','')}", )
                if ev.get("prior") is not None:
                    lines.append(f"  Prior: {ev['prior']}{ev.get('unit','')}")
            if infl.get("vs_fed_target") is not None:
                lines.append(
                    f"  Inflation is {infl['vs_fed_target']:+.1f}pp vs the Fed's 2.0% target — "
                    f"PCE is the Fed's preferred gauge"
                )
            lines.append("")

        # ── Causal narrative ──
        lines.append("READ-THROUGH")
        if matched_news:
            lead = matched_news[0]
            lines.append(f"  Catalyst headline: \"{lead}\"")
        # Ticker shock narrative
        shocks = sorted(ticker_moves.items(), key=lambda x: abs(x[1]), reverse=True)
        if shocks and abs(shocks[0][1]) >= 3:
            tk, chg = shocks[0]
            lines.append(
                f"  {tk} moved {chg:+.1f}% from the open — large-cap shock that can drag index-weighted "
                f"passive and levered ETF flows"
            )
        if nq.get("max_rally_from_open_pct", 0) > 0.5 and (nq.get("open_to_now_pct") or 0) < -1.5:
            lines.append(
                "  The open was bought, then sold — suggests fragile risk appetite and crowded long "
                "positioning rather than a clean macro read"
            )
        if ev.get("actual") is not None and ev.get("estimate") is not None:
            hot = ev["actual"] > ev["estimate"] if "inflation" in ev["name"].lower() else ev["actual"] < ev["estimate"]
            if not hot and (nq.get("open_to_now_pct") or 0) < -1:
                lines.append(
                    "  Notably, the data did NOT drive the initial risk-on tone — the selloff came later, "
                    "pointing to micro (single-name) or positioning catalysts"
                )
        lines.append("")

        # ── Cross-asset ──
        lines.append("CROSS-ASSET")
        if btc.get("open_to_now_pct") is not None:
            lines.append(f"  Bitcoin {_fmt_pct(btc['open_to_now_pct'])} from open (now ~${btc.get('now', 0):,.0f})")
        vix = prices.get("vix", {})
        if vix:
            lines.append(f"  VIX {vix.get('price', 0):.1f} ({_fmt_pct(vix.get('day_chg_pct'))} on day)")
        tnx = prices.get("tnx", {})
        if tnx:
            lines.append(f"  10Y yield {tnx.get('price', 0):.2f}% ({_fmt_pct(tnx.get('day_chg_pct'))})")
        lines.append("")

        # ── Structure / leverage ──
        lev = (snap.get("metadata") or {}).get("leverage_etfs", {})
        if lev:
            lines.append("MARKET STRUCTURE")
            for sym, d in lev.items():
                aum = _fmt_num(d.get("aum")) if d.get("aum") else "n/a"
                day = _fmt_pct(d.get("day_chg_pct"))
                lines.append(f"  {sym} ({d.get('description')}): ~{aum} AUM, {day} today")
            lines.append("  Record levered ETF AUM amplifies both directions — vol begets vol.")
            lines.append("")

        # ── Outlook ──
        lines.append("OUTLOOK")
        if abs(nq.get("open_to_now_pct") or 0) >= 2:
            lines.append("  Elevated intraday range — expect dip-buying attempts but fragile lows.")
        else:
            lines.append("  Session range expanding around event risk — let price confirm before adding.")
        lines.append("  Volatility is a feature of regime change (AI capex, inflation pass-through, record leverage).")
        lines.append("")
        lines.append(f"_Generated {_now_et().strftime('%Y-%m-%d %H:%M ET')} · Not investment advice_")
        return "\n".join(lines)

    def generate_writeup(
        self,
        event_id: str,
        phase: str = "post",
        use_llm: bool = False,
        save: bool = True,
    ) -> str:
        event = self.get_event(event_id)
        if not event:
            return f"Event not found: {event_id}"

        ctx = self.gather_context(event, phase=phase)
        if phase == "pre":
            text = self.build_pre_writeup(ctx)
        else:
            text = self.build_post_writeup(ctx)

        if use_llm:
            enhanced = self._llm_enhance(text, ctx)
            if enhanced:
                text = enhanced

        if save:
            with self._conn() as conn:
                conn.execute(
                    """
                    INSERT INTO event_writeups (event_id, phase, writeup_text, data_json)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(event_id, phase) DO UPDATE SET
                        writeup_text=excluded.writeup_text,
                        data_json=excluded.data_json,
                        generated_at=datetime('now')
                    """,
                    (event_id, phase, text, json.dumps(ctx, default=str)),
                )
                conn.commit()
        return text

    def _llm_enhance(self, draft: str, ctx: Dict[str, Any]) -> Optional[str]:
        """Optional Anthropic polish — keeps data, improves flow."""
        key_path = os.path.join(
            os.path.dirname(os.path.dirname(__file__)), "anthropic_key.txt"
        )
        if not os.path.exists(key_path):
            return None
        try:
            with open(key_path) as f:
                api_key = f.read().strip()
            import anthropic
            client = anthropic.Anthropic(api_key=api_key)
            prompt = (
                "Rewrite this market event commentary for a Telegram trading desk audience. "
                "Keep ALL numbers and facts exactly. Match tone: urgent, analytical, institutional. "
                "Use short paragraphs. Do not invent data.\n\n"
                f"DRAFT:\n{draft}\n\nDATA:\n{json.dumps(ctx.get('nq_window'), default=str)}"
            )
            msg = client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=1500,
                messages=[{"role": "user", "content": prompt}],
            )
            return msg.content[0].text.strip()
        except Exception:
            return None

    # ── Orchestration ─────────────────────────────────────────────────────────

    def scan_today(self, phase: str = "auto") -> List[str]:
        """Discover today's events, detect anomalies, generate writeups."""
        results = []
        events = self.events_today()
        now = _now_et()

        if not events:
            # Still run anomaly writeup if big move
            anomalies = self.detect_intraday_anomalies()
            if anomalies:
                ev = MarketEvent(
                    event_id=f"{now.date().isoformat()}_intraday_anomaly",
                    name="Intraday Market Anomaly",
                    category="Market Structure",
                    event_date=now.date().isoformat(),
                    release_time=now.strftime("%H:%M"),
                    impact="HIGH",
                    source="detector",
                )
                self._upsert_event(ev)
                events = [ev]

        for ev in events:
            rel = ev.release_time or "08:30"
            try:
                rh, rm = map(int, rel.split(":"))
                release_dt = now.replace(hour=rh, minute=rm, second=0, microsecond=0)
            except Exception:
                release_dt = now.replace(hour=8, minute=30, second=0, microsecond=0)

            if phase == "auto":
                p = "pre" if now < release_dt - timedelta(minutes=5) else "post"
            else:
                p = phase

            text = self.generate_writeup(ev.event_id, phase=p, save=True)
            results.append(text)
        return results

    def format_telegram(self, text: str) -> str:
        """Convert plain writeup to HTML for Telegram."""
        out = []
        for line in text.split("\n"):
            s = line.strip()
            if not s:
                out.append("")
                continue
            if s.startswith("⚡") or s.startswith("📋"):
                out.append(f"<b>{s}</b>")
            elif s.isupper() and len(s) < 30 and not s.startswith("_"):
                out.append(f"<b>{s}</b>")
            elif s.startswith("_") and s.endswith("_"):
                out.append(f"<i>{s.strip('_')}</i>")
            else:
                out.append(s)
        return "\n".join(out)


# ── Module-level helpers (used by send_organized_report) ─────────────────────

def get_recent_economic_releases(days_back: int = 7) -> List[Dict[str, Any]]:
    """Recent macro releases with actual vs expected for report tables."""
    engine = EventWriteupEngine()
    engine.discover_events(days_ahead=0, days_back=days_back)
    cutoff = (_now_et().date() - timedelta(days=days_back)).isoformat()
    with engine._conn() as conn:
        rows = conn.execute(
            """
            SELECT * FROM event_catalog
            WHERE event_date >= ? AND actual IS NOT NULL
            ORDER BY event_date DESC, release_time DESC
            LIMIT 15
            """,
            (cutoff,),
        ).fetchall()
    out = []
    for r in rows:
        est = r["estimate"]
        act = r["actual"]
        beat = None
        if est is not None and act is not None:
            if "inflation" in (r["category"] or "").lower() or "pce" in r["name"].lower() or "cpi" in r["name"].lower():
                beat = act <= est
            else:
                beat = act >= est
        out.append({
            "event": r["name"],
            "date": r["event_date"][5:],  # MM-DD
            "expected": f"{est:.1f}" if est is not None else "—",
            "actual": f"{act:.1f}" if act is not None else "—",
            "beat": beat,
            "impact": r["impact"] or "MED",
        })
    return out


if __name__ == "__main__":
    eng = EventWriteupEngine()
    print("Discovering events…")
    evs = eng.discover_events()
    print(f"Found {len(evs)} events")
    for ev in eng.events_today():
        print(f"\n{'='*60}\n{ev.name} ({ev.event_id})")
        print(eng.generate_writeup(ev.event_id, phase="post", save=False))
