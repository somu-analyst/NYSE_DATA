"""Tests for event writeup engine (no network required for unit parts)."""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "_lib"))

from event_writeup_engine import (
    EventWriteupEngine,
    MarketEvent,
    _parse_finnhub_event,
    _slug,
)


class TestEventParsing(unittest.TestCase):
    def test_slug(self):
        self.assertEqual(_slug("PCE Inflation"), "pce_inflation")

    def test_parse_finnhub_pce(self):
        raw = {
            "event": "Core PCE Price Index YoY",
            "date": "2026-06-25",
            "time": "2026-06-25 12:30:00",
            "estimate": 2.5,
            "actual": 2.7,
            "prev": 2.6,
            "impact": "high",
        }
        ev = _parse_finnhub_event(raw)
        self.assertIsNotNone(ev)
        self.assertEqual(ev.name, "Core PCE")
        self.assertEqual(ev.category, "Inflation")
        self.assertEqual(ev.actual, 2.7)


class TestWriteupBuilder(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.engine = EventWriteupEngine(db_path=self.tmp.name)

    def tearDown(self):
        if hasattr(self.engine, "_conn"):
            pass
        import gc
        gc.collect()
        try:
            os.unlink(self.tmp.name)
        except PermissionError:
            pass

    def test_pre_writeup_structure(self):
        ev = MarketEvent(
            event_id="2026-06-25_pce",
            name="PCE",
            category="Inflation",
            event_date="2026-06-25",
            release_time="08:30",
            impact="HIGH",
            source="test",
            estimate=2.5,
            prior=2.6,
        )
        self.engine._upsert_event(ev)
        ctx = {
            "event": {
                "name": "PCE",
                "event_date": "2026-06-25",
                "release_time": "08:30",
                "impact": "HIGH",
                "category": "Inflation",
                "estimate": 2.5,
                "prior": 2.6,
                "unit": "%",
            },
            "snapshot": {
                "prices": {
                    "nq_fut": {"price": 21500, "day_chg_pct": 0.4},
                    "vix": {"price": 18.5, "day_chg_pct": -2.0},
                },
                "news_headlines": ["Apple raises Mac prices"],
                "metadata": {"leverage_etfs": {}},
            },
            "inflation": {"vs_fed_target": 2.1, "series": {"label": "PCE", "value": 4.1}},
        }
        text = self.engine.build_pre_writeup(ctx)
        self.assertIn("PRE-EVENT BRIEF", text)
        self.assertIn("PCE", text)
        self.assertIn("Scenarios", text)

    def test_post_writeup_structure(self):
        ctx = {
            "event": {
                "name": "PCE",
                "event_date": "2026-06-25",
                "release_time": "08:30",
                "impact": "HIGH",
                "category": "Inflation",
                "actual": 4.1,
                "estimate": 2.8,
                "prior": 2.6,
                "unit": "%",
            },
            "nq_window": {
                "open": 21000,
                "now": 20500,
                "open_to_now_pct": -2.4,
                "max_rally_from_open_pct": 1.0,
                "max_drop_from_open_pct": -2.8,
            },
            "es_window": {"open_to_now_pct": -1.2},
            "btc_window": {"open_to_now_pct": -3.0, "now": 58000},
            "snapshot": {"prices": {"vix": {"price": 22, "day_chg_pct": 8}}, "metadata": {}},
            "anomalies": [{"description": "NQ opened +1% then reversed", "severity": "HIGH"}],
            "matched_news": ["Apple raises prices on Macs"],
            "ticker_moves": {"AAPL": -5.8},
            "inflation": {"vs_fed_target": 2.1},
        }
        text = self.engine.build_post_writeup(ctx)
        self.assertIn("WHAT JUST HAPPENED", text)
        self.assertIn("TIMELINE", text)
        self.assertIn("AAPL", text)
        self.assertIn("Bitcoin", text)


if __name__ == "__main__":
    unittest.main()
