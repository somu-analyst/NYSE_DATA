---
description: Backtest a signal vs US_data.db history; report hit-rate vs baseline
argument-hint: [ticker] (default SPY)
allowed-tools: Bash(python -m core.validate*)
---
Backtest the mean-reversion signal using the parallel `core/` system (read-only, never the bot).

Ticker: use `$ARGUMENTS` if provided, otherwise `SPY`.

1. Run `cd archive && python -m core.validate --ticker <TICKER>` (the `core/` package lives under `archive/`).
2. Present the output as a compact table: ticker · days · fires · hit% · avg fwd · baseline · edge.
3. Flag low sample counts and do NOT overstate the edge — thin N means weak evidence.
