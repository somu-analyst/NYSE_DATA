"""Date helpers — format-agnostic during the MM-DD-YYYY -> ISO migration.

The repo is standardizing on ISO `YYYY-MM-DD`, but legacy tables may still hold
US `MM-DD-YYYY` (and a couple of `DDMonYYYY` outliers). `to_iso` normalizes any
of these, and `sort_key` returns an ISO key so Python-side sorting always matches
DB-side sorting regardless of the stored format.
"""
from __future__ import annotations

from datetime import datetime

ISO_FMT = "%Y-%m-%d"
US_FMT = "%m-%d-%Y"


def to_iso(d: str) -> str:
    """Normalize a date string to ISO YYYY-MM-DD. Accepts ISO, MM-DD-YYYY, DDMonYYYY."""
    if not d:
        return d
    s = d.strip()
    if len(s) >= 10 and s[4] == "-" and s[7] == "-":   # already ISO (maybe with time)
        return s[:10]
    if len(s) == 10 and s[2] == "-" and s[5] == "-":   # MM-DD-YYYY
        return f"{s[6:10]}-{s[0:2]}-{s[3:5]}"
    for fmt in ("%d%b%Y", "%m/%d/%Y", US_FMT):          # DDMonYYYY, slash, fallback
        try:
            return datetime.strptime(s, fmt).strftime(ISO_FMT)
        except ValueError:
            continue
    return s  # unknown — return unchanged rather than raise


def sort_key(d: str) -> str:
    """ISO key for chronological sorting of any supported input format."""
    return to_iso(d)


def parse(d: str) -> datetime:
    return datetime.strptime(to_iso(d), ISO_FMT)


def to_str(dt: datetime) -> str:
    """ISO output (the new storage standard)."""
    return dt.strftime(ISO_FMT)
