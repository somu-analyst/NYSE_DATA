---
description: Full working principles — Graphify, Karpathy behavior, token/usage awareness, usage-limits & VS Code, community workflow, multi-provider lanes, cross-model continuity. CLAUDE.md "Working method" is the condensed always-loaded version; this file holds the detail.
paths: ["**/*"]
---

# Working principles (full)

> CLAUDE.md carries the condensed bullets that load every session. This file holds the full
> detail so it's available when relevant without spending the always-loaded token budget on the
> options/analytics work that is this repo's core.

---

## Knowledge Graph First

If a Graphify knowledge graph exists for this project:

- Always consult the knowledge graph report before doing broad file searches.
- Specifically, read `graphify-out/GRAPH_REPORT.md` (or the Graphify-generated report in the
  project root) before calling tools such as `Glob` and `Grep`.
- Use the graph to identify:
  - Key "god" files and central modules.
  - Communities or subsystems relevant to the current task.
  - Existing abstractions that should be reused instead of recreated.

When planning work:

- Start by summarizing the relevant parts of the knowledge graph.
- Use that summary to narrow the search space to specific directories, modules, or components.
- Avoid repeated full-repo scans when the graph already highlights the right areas.

> ⚠️ Status in THIS repo: no `graphify-out/` or `GRAPH_REPORT.md` exists yet. Generate it with the
> `/graphify` command in Claude Code (or `graphifyy` on the CLI) before relying on the graph.

---

## Coding Behavior Principles (Karpathy-style)

When working on this project:

- **Think before coding.**
  - Restate the task in your own words.
  - Identify inputs, outputs, constraints, and success criteria.
  - Consider at least two approaches and pick the simplest viable one.
- **Prefer simplicity and surgical changes.**
  - Make the smallest change that accomplishes the goal.
  - Avoid large refactors unless explicitly requested.
  - Keep edits local to the relevant files and functions.
- **State assumptions explicitly.**
  - If something is ambiguous, list your assumptions.
  - Ask for clarification instead of silently guessing when uncertainty is high.
- **Define success criteria and verify.**
  - Before editing, specify what success looks like (tests passing, specific behavior, target).
  - After editing, verify with tests, checks, or reasoning before declaring the task done.
- **Stop when confused.**
  - If the codebase or requirements are unclear, explain what is confusing.
  - Propose questions or a short investigation plan instead of continuing with low-confidence edits.

---

## Token and Usage Awareness

This project may use local usage-tracking tools (e.g., `claude-monitor`, `ccusage`, or similar):

- Assume that session cost and usage are being tracked.
- Prefer shorter, focused iterations over long, exploratory ones.
- Avoid re-running large, expensive operations when the results are already available in context
  or the knowledge graph.
- When proposing big changes, outline:
  - Estimated impact on tokens (more files, more tests).
  - Ways to stage work into smaller, verifiable steps.

When you notice a task could be completed in fewer steps:

- Suggest a more efficient plan.
- Offer a minimal initial implementation plus optional extensions, instead of building everything
  at once.

---

## Project-Specific Rules

- Keep any existing project conventions (style, architecture, testing patterns).
- When new behavior rules appear to conflict with project conventions, obey project conventions
  unless explicitly told otherwise.
- Document any important project-specific constraints (performance budgets, security restrictions,
  deployment workflows). For this repo those live in `CLAUDE.md` (Efficiency rules, Tables, DB
  schema, Signal logic) — they win on conflict.

---

## Usage Limits and VS Code Workflow

Claude Code in VS Code has rolling session and weekly usage limits. When a limit is reached,
further requests are blocked until the window resets. Treat this as a constrained resource and
optimize how you use each session.

### When a session limit is hit

During a lockout period (e.g., 4–5 hour reset window):

- Continue coding manually in VS Code using local tools (search, refactor, tests) and plan the
  next AI-assisted steps in scratch files.
- Use other local assistants, linters, and static analyzers that do not consume Claude quotas for
  mechanical checks and formatting.
- If the main VS Code quota is exhausted but the web app quota is not, use the web chat only for
  light tasks (planning, high-level design, small snippets), then paste results into VS Code.

The goal is to keep progressing without burning additional Claude Code usage, and to arrive at the
next window with a clear list of focused prompts.

### How to avoid hitting limits quickly

When the session is active:

- **Use short, spec-style prompts.** Specify exact file paths, functions, and constraints instead
  of vague requests like "refactor the whole repo." Batch related questions into one message.
- **Start fresh regularly.** For long conversations, periodically summarize work done into
  `LOG.md` and open new sessions from that summary instead of carrying huge histories.
- **Rely on cached/project context.** Use persistent `.md` files as knowledge bases so recurring
  context is reused. Maintain core docs once and reference them rather than re-describing them.
- **Disable unused tools and integrations.** Keep the environment lean: turn off MCP servers, web
  search, connectors, or extended thinking unless needed. Enable heavier tools only when required.

### VS Code–specific habits

- **Use Claude for focused, high-value tasks.** Prefer "in `_lib/options_tracker.py`, refactor the
  open-positions query" over "review the whole project." Navigate to the relevant files yourself,
  then ask about those files instead of asking Claude to scan the entire repo.
- **Maintain a lightweight project log.** Append brief summaries of major interactions/decisions to
  `LOG.md`; new sessions read it to regain context cheaply.
- **Plan before prompting.** Decide what you need, which files are involved, and what "done" looks
  like before invoking Claude. Use Claude to implement and verify the plan, not to figure out the
  whole problem from a blank slate.

---

## Community-Optimized Workflow

### Conversation and session habits

- **Work in small, well-scoped tasks.** Prefer prompts that target specific files and functions
  over broad "fix everything" requests. Break large goals into subtasks completable in one short
  iteration.
- **Plan sessions and split your day.** Before starting, write a short list of objectives and files
  involved. Aim for 2–3 focused sessions per day instead of one very long conversation.
- **Start fresh with recaps.** Every 10–20 messages or when context feels long, summarize progress
  into `LOG.md` and start a new session using that summary. Don't let conversations grow until
  earlier context becomes irrelevant or contradictory.
- **Maintain a memory file.** Keep `LOG.md` updated with brief notes on what was done and what's
  next. New sessions read it first to regain context efficiently.

### Instruction and file structure

- **Keep CLAUDE.md concise.** Target a readable size so instructions are clear and reliably
  followed. Move highly specialized rules into `.claude/rules/*.md` with `paths:` frontmatter so
  they load only for matching files (this file is an example).
- **Use commands for repeated workflows.** For workflows used multiple times per day (refactor
  pattern, tech-debt cleanup, analytics), define slash commands instead of long prompts each time.
  Favor commands and small skills over giant, one-off instructions.
- **Manage context and task size.** When context usage feels high, explicitly `/compact` or
  summarize before continuing (a common pattern is manual compaction around ~50% of the window).
  Keep subtasks small enough to complete with less than half the context window, leaving room for
  verification and iteration.

---

## Multi-Provider Workflow: Claude + Gemini + Local Agents

We use Claude as the primary deep-reasoning and coding assistant, with Gemini and local/
open-source agents as secondary lanes. The goal is to preserve Claude usage for the hardest work.

### Role split

- **Claude Code (primary)**
  - Multi-file refactors, complex debugging, architecture decisions, security-sensitive changes.
  - Follow all project rules, tests, and verification steps.
- **Gemini or other cloud model (overflow)**
  - Research, documentation drafts, UI ideas, large-context reading when Claude's window is tight.
  - Prefer this lane when we mainly need summarization, explanation, or content generation — not
    direct repo edits.
- **Local / open-source editor agents (routine)**
  - Cline, Continue, Tabby, Aider, or similar for routine edits, small refactors, repetitive
    changes.
  - Prefer these when the work is mechanical and doesn't need Claude's deeper reasoning.

### When Claude should propose another lane

- **Suggest the Gemini / overflow lane when:**
  - The current session is close to usage limits.
  - The task is mostly reading, summarizing, or generating non-critical text.
  - The user asks for long research or many variants of content.
- **Suggest a local/open-source agent lane when:**
  - Changes are small and mechanical (rename, formatting, trivial refactors).
  - The user has appropriate tools installed (Continue, Cline, Tabby) and is comfortable using them.
  - Token usage is already high for the day and we want to preserve Claude for harder work.
- **Stay in the Claude lane when:**
  - Work involves tricky logic, security implications, or multi-service integration.
  - The user explicitly prefers Claude for this task.
  - The change needs careful reasoning, tests, and review.

### How Claude should respond when suggesting another lane

- Briefly explain why (e.g., "this is mostly summarization; Gemini may be cheaper with higher
  usage capacity").
- Outline the steps for the other tool (e.g., "open Cline in VS Code, select the same repo, paste
  this plan and let it implement the mechanical edits").
- Offer to generate a small plan or prompt the user can paste into that tool.

---

## Cross-Model Continuity

If work may be continued in another model or tool:

- Use `PLAN.md` as the current source of truth for what remains.
- Use `LOG.md` for completed steps, decisions, and blockers.
- Before making changes, read both files if they exist.
- After meaningful progress, update `LOG.md` with: what changed, what was verified, what remains,
  and any assumptions or risks.
- Keep summaries short and actionable so another model can continue without re-reading the whole
  chat.
- If a task is likely to move between Claude, Gemini, or another agent, prefer a handoff-friendly
  structure over long chat-based context.

(Templates for these files already exist at the repo root: `PLAN.md`, `LOG.md`, `NEXT.md`.)
