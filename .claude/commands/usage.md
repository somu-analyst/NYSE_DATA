---
description: Show Claude Code token usage / cost via ccusage
argument-hint: [daily|weekly|monthly]
allowed-tools: Bash(npx ccusage*)
---
Report Claude Code usage and estimated cost.

Run `npx ccusage@latest` (append the view from `$ARGUMENTS` if given, e.g. `daily`/`weekly`/`monthly`;
default is daily). Summarize today's and this week's tokens and cost in 3-4 lines. If usage is high,
suggest deferring routine/mechanical work to a cheaper lane (see .claude/rules/workflow.md).
