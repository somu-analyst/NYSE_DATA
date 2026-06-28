"""MM-DD-YYYY date helpers — the DB stores dates as these strings.

`sort_key` mirrors the SQL idiom used across the repo:
    substr(d,7,4)||substr(d,1,2)||substr(d,4,2)  ->  YYYYMMDD
so Python-side sorting matches DB-side sorting exactly.
"""
from __future__ import annotations

from datetime import datetime

DATE_FMT = "%m-%d-%Y"


def sort_key(d: str) -> str:
    """Return a lexicographically sortable YYYYMMDD key for an MM-DD-YYYY string."""
    return d[6:10] + d[0:2] + d[3:5]


def parse(d: str) -> datetime:
    return datetime.strptime(d, DATE_FMT)


def to_str(dt: datetime) -> str:
    return dt.strftime(DATE_FMT)
