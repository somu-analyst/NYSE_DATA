"""
core/ — parallel, modular analytics extracted from the monolithic bot.

This package is a clean, importable, *read-only* counterpart to
telegram_bot_optimized.py. It exists so signal/analytics logic can be tested and
reused without loading (or risking) the ~23k-line bot. Nothing here imports the
bot, mutates the DB, or sends Telegram messages.

Grow it by porting one cohesive, pure function at a time (see core/README.md).
"""

__all__ = ["db", "dates", "signals", "validate"]
