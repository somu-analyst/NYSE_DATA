---
description: Long-form working principles (Graphify, Karpathy, usage/limits, multi-provider, continuity). The condensed version lives in CLAUDE.md "Working method".
paths: ["**/*"]
---

# Working principles (long form)

> CLAUDE.md carries the condensed bullets that load every session. This file holds the full
> prose so it's available when relevant without spending the always-loaded budget.

## 1. Knowledge graph first (Graphify)
- Before reaching for `Glob`/`Grep`, consult `graphify-out/GRAPH_REPORT.md` if it exists.
- Use the graph to locate god-files, subsystem boundaries, and reusable abstractions instead of
  rediscovering them by hand.
- If the Graphify output is **absent**, generate it via the `/graphify` slash command or the
  `graphifyy` CLI — do not pretend a graph exists or invent its contents.

## 2. Coding behavior (Karpathy-style)
- Think before coding: restate the task in one line so intent is explicit.
- Consider ~2 approaches and pick the simplest that fits; avoid speculative generality.
- Make surgical, local changes — touch the minimum needed; match surrounding style.
- State assumptions out loud when the request is underspecified.
- Define success criteria up front and verify against them before declaring done.
- Stop and ask when genuinely confused rather than guessing through it.

## 3. Token & usage awareness
- Assume usage/cost is tracked (ccusage / claude-monitor). Work as if every run is metered.
- Prefer short, focused iterations and tight, file-scoped, spec-style prompts.
- Avoid re-running expensive scans/builds; reuse cached context; disable unused tools/MCP servers.
- For big jobs, outline the plan + rough token impact and stage the work rather than one-shotting.

## 4. Project-specific rules win
- Preserve existing conventions in the codebase. When a request conflicts with the project rules
  in CLAUDE.md, follow the project rules and flag the conflict.

## 5. Usage limits & VS Code workflow
- During limit lockout windows: write a fresh-start summary to `LOG.md`/`NEXT.md` and resume cold
  from those files instead of replaying the whole thread.
- Avoid hitting limits: spec-style prompts, start fresh sessions with summaries, lean on cached
  context, and disable tools you're not using.
- VS Code habits: keep prompts focused and file-scoped; maintain `LOG.md`; plan before prompting.

## 6. Community-optimized workflow
- Small, scoped tasks; ~2–3 working sessions per day rather than one marathon.
- Recap progress to `LOG.md` every ~10–20 messages.
- Keep CLAUDE.md under ~200 lines; move deep specialized rules into `.claude/rules/*.md` with
  `paths:` frontmatter (this file is an example).
- Use slash commands and existing skills instead of ad-hoc multi-step asks.
- Manual `/compact` around ~50% context; keep subtasks under half the context window.

## 7. Multi-provider workflow (Claude + Gemini + local agents)
- **Claude** — hard reasoning, multi-file refactors, security-sensitive work.
- **Gemini** — overflow capacity, broad research, long-document summarizing.
- **Local agents** (Cline / Continue / Tabby / Aider) — routine, mechanical, well-specified edits.
- When a task fits a cheaper lane, say so and suggest handing it off to conserve limits.

## 8. Cross-model continuity
Use three root files as the handoff contract (templates already created):
- **PLAN.md** — remaining work; the source of truth for what's left.
- **LOG.md** — completed work, decisions, blockers (newest on top).
- **NEXT.md** — the single most useful note for whoever/whatever picks this up next.
Update them before a context reset or when handing off to another model/session.
