---
description: Rebuild telegram_bot_optimized.py from telegram_bot.py (WSL paths)
allowed-tools: Bash
---
Rebuild the optimized bot from its build source.

`build_optimized.py` hardcodes WSL `/mnt/c/...` paths, so run it under WSL/bash:
`python3 build_optimized.py` from the repo root. Report the line counts it prints and confirm
`telegram_bot_optimized.py` was regenerated. Never hand-edit `telegram_bot_optimized.py` as part of
this command — source changes belong in `telegram_bot.py`.
