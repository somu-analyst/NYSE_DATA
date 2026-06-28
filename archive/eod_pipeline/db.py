import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import pandas as pd


@dataclass
class RunSummary:
    run_id: int
    total_symbols: int
    success_count: int
    source_breakdown: dict[str, int]


def get_conn(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


def init_db(db_path: Path) -> None:
    with get_conn(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS eod_pipeline_runs (
                run_id INTEGER PRIMARY KEY AUTOINCREMENT,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                requested_symbols INTEGER NOT NULL,
                loaded_symbols INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL,
                notes TEXT
            );

            CREATE TABLE IF NOT EXISTS eod_prices (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id INTEGER NOT NULL,
                source TEXT NOT NULL,
                symbol TEXT NOT NULL,
                trade_date TEXT NOT NULL,
                open REAL,
                high REAL,
                low REAL,
                close REAL,
                adj_close REAL,
                volume REAL,
                vwap REAL,
                bar_count INTEGER,
                currency TEXT,
                exchange TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                UNIQUE(source, symbol, trade_date),
                FOREIGN KEY(run_id) REFERENCES eod_pipeline_runs(run_id)
            );

            CREATE INDEX IF NOT EXISTS idx_eod_prices_symbol_date
                ON eod_prices(symbol, trade_date);
            CREATE INDEX IF NOT EXISTS idx_eod_prices_run
                ON eod_prices(run_id);
            """
        )


def create_run(db_path: Path, requested_symbols: int, notes: str = "") -> int:
    with get_conn(db_path) as conn:
        cur = conn.execute(
            """
            INSERT INTO eod_pipeline_runs (started_at, requested_symbols, status, notes)
            VALUES (datetime('now'), ?, 'running', ?)
            """,
            (requested_symbols, notes),
        )
        return int(cur.lastrowid)


def finalize_run(db_path: Path, run_id: int, loaded_symbols: int, status: str, notes: str = "") -> None:
    with get_conn(db_path) as conn:
        conn.execute(
            """
            UPDATE eod_pipeline_runs
            SET finished_at = datetime('now'), loaded_symbols = ?, status = ?, notes = ?
            WHERE run_id = ?
            """,
            (loaded_symbols, status, notes, run_id),
        )


def upsert_prices(db_path: Path, run_id: int, rows: Iterable[dict]) -> int:
    rows = list(rows)
    if not rows:
        return 0

    with get_conn(db_path) as conn:
        conn.executemany(
            """
            INSERT INTO eod_prices (
                run_id, source, symbol, trade_date, open, high, low, close, adj_close,
                volume, vwap, bar_count, currency, exchange
            ) VALUES (
                :run_id, :source, :symbol, :trade_date, :open, :high, :low, :close, :adj_close,
                :volume, :vwap, :bar_count, :currency, :exchange
            )
            ON CONFLICT(source, symbol, trade_date) DO UPDATE SET
                run_id = excluded.run_id,
                open = excluded.open,
                high = excluded.high,
                low = excluded.low,
                close = excluded.close,
                adj_close = excluded.adj_close,
                volume = excluded.volume,
                vwap = excluded.vwap,
                bar_count = excluded.bar_count,
                currency = excluded.currency,
                exchange = excluded.exchange,
                created_at = datetime('now')
            """,
            [{**row, "run_id": run_id} for row in rows],
        )
        return len(rows)


def latest_prices(db_path: Path, limit_symbols: int = 500) -> pd.DataFrame:
    with get_conn(db_path) as conn:
        return pd.read_sql(
            """
            WITH ranked AS (
                SELECT
                    source,
                    symbol,
                    trade_date,
                    open,
                    high,
                    low,
                    close,
                    adj_close,
                    volume,
                    created_at,
                    ROW_NUMBER() OVER (PARTITION BY source, symbol ORDER BY trade_date DESC) AS rn
                FROM eod_prices
            )
            SELECT *
            FROM ranked
            WHERE rn = 1
            ORDER BY trade_date DESC, symbol
            LIMIT ?
            """,
            conn,
            params=(limit_symbols,),
        )


def runs_history(db_path: Path, limit_rows: int = 20) -> pd.DataFrame:
    with get_conn(db_path) as conn:
        return pd.read_sql(
            """
            SELECT run_id, started_at, finished_at, requested_symbols, loaded_symbols, status, notes
            FROM eod_pipeline_runs
            ORDER BY run_id DESC
            LIMIT ?
            """,
            conn,
            params=(limit_rows,),
        )
