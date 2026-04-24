"""
Unit tests for Telegram bot table formatting.
Run: python tests/test_formatting.py
Prints each table to stdout so you can verify width fits ~33 chars mobile screen.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import logging
log = logging.getLogger("test_fmt")
logging.basicConfig(level=logging.INFO, format="%(message)s")

# ── import helpers from bot ──────────────────────────────────────────────────
from telegram_bot import mono, row2, row3, bar, hdr, _fit_cell

TARGET_WIDTH = 35   # max chars before horizontal scroll needed on mobile
PASS = "PASS"
FAIL = "FAIL *** TOO WIDE ***"

def check(label, text):
    lines = text.split("\n")
    max_w  = max(len(l) for l in lines)
    status = PASS if max_w <= TARGET_WIDTH else FAIL
    log.info(f"\n{'='*50}")
    log.info(f"[{status}] {label}  (max_width={max_w})")
    log.info(f"{'='*50}")
    log.info(text)
    return max_w <= TARGET_WIDTH

all_pass = True

# ── 1. market_overview narrow table ─────────────────────────────────────────
def test_market_overview():
    rows = [
        ("SPX",     "5,234.50", "+0.45%"),
        ("NDX",    "18,120.00", "-0.12%"),
        ("ES",      "5,230.00", "+0.40%"),
        ("NQ",     "18,100.00", "-0.15%"),
        ("VIX",       "21.50",  "-1.20%"),
        ("Gold",    "2,045.00",  "+0.80%"),
        ("Oil",        "83.20",  "-0.30%"),
        ("BTC",    "85,000.00",  "+2.10%"),
        ("EUR/USD",     "1.085",  "+0.05%"),
        ("10Y Yld",     "4.250",  "+0.03%"),
    ]
    hdrs = ["Name", "Price", "Chg%"]
    RIGHT = {1, 2}
    col_w = [max(len(hdrs[i]), max(len(r[i]) for r in rows)) for i in range(3)]
    _j = lambda i, v: v.rjust(col_w[i]) if i in RIGHT else v.ljust(col_w[i])
    sep = "-+-".join("-"*w for w in col_w)
    lines = [" | ".join(_j(i, hdrs[i]) for i in range(3)), sep]
    for r in rows:
        lines.append(" | ".join(_j(i, r[i]) for i in range(3)))
    lines.append(sep)
    return check("market_overview", "\n".join(lines))

# ── 2. position_monitor narrow table ────────────────────────────────────────
def test_position_monitor():
    # New design: S|Tkr|Strk|P&L%|Act — 5 cols, target ≤33 chars
    rows = [
        (".", "NVDA",  "875",  "+42.0%", "HOLD"),
        ("!", "AAPL",  "215",  "-18.5%", "ROLL"),
        ("X", "TSLA",  "300",  "-52.0%", "EXIT"),
        ("+", "META",  "600",  "+65.0%", "PROF"),
        ("!", "AMZN",  "3200", "-38.0%", "CUT!"),
    ]
    hdrs = ["S", "Tkr", "Strk", "P&L%", "Act"]
    RIGHT = {2, 3}
    col_w = [max(len(hdrs[i]), max(len(r[i]) for r in rows)) for i in range(len(hdrs))]
    _j = lambda i, v: v.rjust(col_w[i]) if i in RIGHT else v.ljust(col_w[i])
    sep = "-+-".join("-"*w for w in col_w)
    lines = [" | ".join(_j(i, hdrs[i]) for i in range(len(hdrs))), sep]
    for r in rows:
        lines.append(" | ".join(_j(i, r[i]) for i in range(len(hdrs))))
    lines.append(sep)
    lines.append("P&L: +12,500  (4 pos)")
    lines.append("S: !=EXIT -=CUT +=PROF >=ROLL")
    return check("position_monitor", "\n".join(lines))

# ── 3. intraday_alert futures ────────────────────────────────────────────────
def test_intraday_futures():
    rows = [
        ("ES",   "5,234.0", "+0.3%"),
        ("NQ",  "18,120.0", "-0.1%"),
        ("VIX",     "21.5",  "-0.8%"),
    ]
    hdrs = ["Name", "Price", "Chg%"]
    RIGHT = {1, 2}
    col_w = [max(len(hdrs[i]), max(len(r[i]) for r in rows)) for i in range(3)]
    _j = lambda i, v: v.rjust(col_w[i]) if i in RIGHT else v.ljust(col_w[i])
    sep = "-+-".join("-"*w for w in col_w)
    lines = [" | ".join(_j(i, hdrs[i]) for i in range(3)), sep]
    for r in rows:
        lines.append(" | ".join(_j(i, r[i]) for i in range(3)))
    return check("intraday_alert_futures", "\n".join(lines))

# ── 4. signal_scanner OI table ───────────────────────────────────────────────
def test_signal_scanner():
    # New design: short headers ST|Tkr|C-OI|P-OI|PCR
    rows = [
        ("[B]", "NVDA", "+5K",  "-200", "0.72"),
        ("[B]", "OXY",  "+3K",  "+100", "0.81"),
        ("[S]", "META", "-200",  "+8K", "2.10"),
        ("[S]", "MU",   "-100",  "+6K", "3.40"),
    ]
    hdrs = ["ST", "Tkr", "C-OI", "P-OI", "PCR"]
    RIGHT = {2, 3, 4}
    col_w = [max(len(hdrs[i]), max(len(r[i]) for r in rows)) for i in range(len(hdrs))]
    _j = lambda i, v: v.rjust(col_w[i]) if i in RIGHT else v.ljust(col_w[i])
    sep = "-+-".join("-"*w for w in col_w)
    lines = [" | ".join(_j(i, hdrs[i]) for i in range(len(hdrs))), sep]
    for r in rows:
        lines.append(" | ".join(_j(i, r[i]) for i in range(len(hdrs))))
    return check("signal_scanner", "\n".join(lines))

# ── 5. quick_quote row2 style ─────────────────────────────────────────────────
def test_quick_quote():
    lines = [
        row2("Last",    "$875.50  UP +1.2%"),
        row2("Open",    "$862.00"),
        row2("High",    "$880.00"),
        row2("Low",     "$860.00"),
        "─"*28,
        row2("Volume",  "12.5M"),
        row2("Avg Vol", "9.8M"),
        row2("Mkt Cap", "$2.15T"),
        row2("P/E",     "42.5"),
        row2("EPS",     "$20.62"),
    ]
    return check("quick_quote_row2", "\n".join(lines))

# ── 6. morning_alert market rows ─────────────────────────────────────────────
def test_morning_alert():
    # New design: short names ES/NQ/VIX, 3 cols only
    rows = [
        ("ES",  "5,234.00", "+0.45%"),
        ("NQ", "18,120.00", "-0.12%"),
        ("VIX",    "21.50", "-1.20%"),
    ]
    hdrs = ["Name", "Price", "Chg%"]
    RIGHT = {1, 2}
    col_w = [max(len(hdrs[i]), max(len(r[i]) for r in rows)) for i in range(3)]
    _j = lambda i, v: v.rjust(col_w[i]) if i in RIGHT else v.ljust(col_w[i])
    sep = "-+-".join("-"*w for w in col_w)
    lines = [" | ".join(_j(i, hdrs[i]) for i in range(3)), sep]
    for r in rows:
        lines.append(" | ".join(_j(i, r[i]) for i in range(3)))
    return check("morning_alert_market", "\n".join(lines))

# ── run all ──────────────────────────────────────────────────────────────────
results = {
    "market_overview":    test_market_overview(),
    "position_monitor":   test_position_monitor(),
    "intraday_futures":   test_intraday_futures(),
    "signal_scanner":     test_signal_scanner(),
    "quick_quote":        test_quick_quote(),
    "morning_alert":      test_morning_alert(),
}

log.info("\n" + "="*50)
log.info("SUMMARY")
log.info("="*50)
for name, ok in results.items():
    status = "PASS" if ok else "FAIL"
    log.info(f"  {status:4}  {name}")

all_ok = all(results.values())
log.info(f"\nOverall: {'ALL PASS' if all_ok else 'SOME FAILURES — fix widths'}")
sys.exit(0 if all_ok else 1)
