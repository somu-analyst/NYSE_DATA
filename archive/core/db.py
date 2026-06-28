"""Read-only access to US_data.db for the parallel core/ system.

Opens the database in SQLite read-only mode so nothing in core/ can ever mutate
the live data the bot/pipeline depend on. Paths are overridable via env vars.
"""
from __future__ import annotations

import os
import sqlite3

DATA_DIR = os.environ.get("NYSE_DATA_DIR", r"C:\Users\srini\Options_chain_data")
DB_PATH = os.environ.get("NYSE_DB_PATH", os.path.join(DATA_DIR, "US_data.db"))


def get_conn(read_only: bool = True) -> sqlite3.Connection:
    """Return a connection to US_data.db. Read-only by default (mode=ro)."""
    if read_only:
        return sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    return sqlite3.connect(DB_PATH)
