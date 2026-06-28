---
description: Locate code via the CLAUDE.md map instead of grepping the 23k-line bot
argument-hint: <feature or function>
allowed-tools: Read, Grep
---
Find where to work on: `$ARGUMENTS`

1. Consult CLAUDE.md first (Repo map · `_lib/` modules · Key functions · Bot commands) and
   `core/README.md` to narrow to the right file/section.
2. Only then `Grep` with a targeted pattern, and `Read` with `offset`/`limit`. Never read whole
   large files (`telegram_bot_optimized.py` / `dashboard.py`).
3. Report the exact `file:line` locations to edit, plus which canonical file owns the logic.
