"""Migrate MM-DD-YYYY date columns in US_data.db to ISO YYYY-MM-DD.

Why: the big data tables store dates as US MM-DD-YYYY strings, which forces the
`substr(d,7,4)||substr(d,1,2)||substr(d,4,2)` sort hack everywhere and blocks
native date indexing. ISO sorts chronologically for free and matches the newer
tables (trades, insider_trades, ...) that already use ISO.

Safety:
  - Idempotent: only rows matching the US pattern `__-__-____` are touched;
    already-ISO values (which start `____-`) are skipped, so re-running is a no-op.
  - Transactional: all changes commit together or roll back.
  - Run on a COPY first.  Usage:  python -m migrations.dates_to_iso <db_path> [--apply]
    Without --apply it does a dry run (reports counts, changes nothing).
"""
from __future__ import annotations

import argparse
import sqlite3
from datetime import datetime

# table -> MM-DD-YYYY columns to convert
US_DATE_COLUMNS = {
    "options_change":      ["expiry_date", "trade_date_now", "load_date"],
    "options_daily":       ["expiry_date", "trade_date", "load_date"],
    "options_raw":         ["expiry_date", "trade_date", "load_date"],
    "stock_daily":         ["trade_date", "load_date"],
    "us_analytics_daily":  ["trade_date", "load_date"],
    "signal_accuracy":     ["trade_date"],
    "signal_weights":      ["last_updated"],
    "sentiment_log":       ["trade_date"],
    "month1_options":      ["expiry_date", "trade_date", "load_date"],
    "month2_options":      ["expiry_date", "trade_date", "load_date"],
    "week1_options":       ["expiry_date", "trade_date", "load_date"],
    "week2_options":       ["expiry_date", "trade_date", "load_date"],
    "week3_options":       ["expiry_date", "trade_date", "load_date"],
    "week4_options":       ["expiry_date", "trade_date", "load_date"],
    "week5_options":       ["expiry_date", "trade_date", "load_date"],
}

# helpful composite indexes to add once dates sort natively
INDEXES = [
    ("idx_oc_tkr_trade",  "options_change", "ticker, trade_date_now"),
    ("idx_od_tkr_trade",  "options_daily",  "ticker, trade_date"),
    ("idx_sd_tkr_trade",  "stock_daily",    "ticker, trade_date"),
    ("idx_sa_tkr_trade",  "signal_accuracy", "ticker, trade_date"),
]

# MM-DD-YYYY -> ISO via SQL string slicing (1-indexed substr)
def _iso_expr(col: str) -> str:
    return (f"substr({col},7,4)||'-'||substr({col},1,2)||'-'||substr({col},4,2)")

US_PATTERN = "__-__-____"  # matches MM-DD-YYYY but NOT ISO (ISO has '-' at pos 5, not 3)


def _table_exists(cur, name: str) -> bool:
    return cur.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def _count_us(cur, table: str, col: str) -> int:
    return cur.execute(
        f"SELECT COUNT(*) FROM '{table}' WHERE \"{col}\" LIKE ?", (US_PATTERN,)
    ).fetchone()[0]


def migrate(db_path: str, apply: bool) -> None:
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    total = 0
    print(f"{'APPLY' if apply else 'DRY-RUN'} on {db_path}\n" + "=" * 60)

    for table, cols in US_DATE_COLUMNS.items():
        if not _table_exists(cur, table):
            print(f"  (skip {table}: not present)")
            continue
        for col in cols:
            before = _count_us(cur, table, col)
            total += before
            print(f"  {table}.{col:16s} MM-DD-YYYY rows: {before:>9,}")
            if apply and before:
                cur.execute(
                    f"UPDATE '{table}' SET \"{col}\" = {_iso_expr(col)} "
                    f"WHERE \"{col}\" LIKE ?", (US_PATTERN,)
                )

    # special case: options_trades.entry_date like '08Jan2026' (DDMonYYYY)
    if _table_exists(cur, "options_trades"):
        rows = cur.execute(
            "SELECT rowid, entry_date FROM options_trades "
            "WHERE entry_date IS NOT NULL AND entry_date != '' AND entry_date NOT LIKE '____-%'"
        ).fetchall()
        print(f"  options_trades.entry_date  non-ISO rows: {len(rows):>9,}")
        if apply:
            for rowid, val in rows:
                iso = None
                if len(val) == 10 and val[2] == "-" and val[5] == "-":   # MM-DD-YYYY
                    iso = f"{val[6:10]}-{val[0:2]}-{val[3:5]}"
                else:
                    try:                                                  # DDMonYYYY e.g. 08Jan2026
                        iso = datetime.strptime(val, "%d%b%Y").strftime("%Y-%m-%d")
                    except ValueError:
                        iso = None
                if iso:
                    cur.execute("UPDATE options_trades SET entry_date=? WHERE rowid=?", (iso, rowid))
                else:
                    print(f"    ! could not parse entry_date={val!r} (left as-is)")

    if apply:
        for name, table, cols in INDEXES:
            if _table_exists(cur, table):
                cur.execute(f"CREATE INDEX IF NOT EXISTS {name} ON {table}({cols})")
                print(f"  index {name} on {table}({cols})")
        conn.commit()
        print("-" * 60 + "\nCOMMITTED.")
    else:
        print("-" * 60 + f"\nWould convert ~{total:,} cells. Re-run with --apply to write.")

    conn.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("db_path")
    ap.add_argument("--apply", action="store_true", help="write changes (default: dry run)")
    args = ap.parse_args()
    migrate(args.db_path, args.apply)
