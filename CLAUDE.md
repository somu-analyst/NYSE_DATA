# NYSE_DATA — Claude Code Reference

## ⚡ Efficiency rules (read first — saves tokens / avoids limits)
- **Canonical files:** `telegram_bot_optimized.py` (~23k lines, THE running bot) · `dashboard.py` (Streamlit). Edit these directly — no patch/helper scripts.
- **NEVER read whole big files.** `telegram_bot_optimized.py`/`dashboard.py` are huge → use `Grep` to locate, then `Read` with `offset`/`limit`. Don't re-read a file you just edited.
- `telegram_bot.py` = build source for the optimized bot via `build_optimized.py`; also imported by `tests/` + `run_mc_exit_local.py`. Keep it, but runtime edits go to `telegram_bot_optimized.py`.
- Dates are `MM-DD-YYYY` strings → sort with `substr(d,7,4)||substr(d,1,2)||substr(d,4,2)`.
- `datetime.utcnow()` → `datetime.now(timezone.utc).replace(tzinfo=None)` (3.12+).
- Dead/NULL cols: `vol_rank_call/put`, `money_coi_*`. SPY PCR can spike 11+ on expiry (not signal).
- Secrets: `token.txt`, `us_bot_*.txt`, `api_keys.env/.enc` are gitignored — never commit/print. `*.db`, `logs/`, `*.log` ignored too.
- Git: commit to `main` directly (no feature branches) only when asked.

## 🧭 Working method (think → act, save tokens)
- **Graph first:** if `graphify-out/GRAPH_REPORT.md` exists, read it before `Glob`/`Grep` to locate god-files, subsystems, and reusable code. If absent, generate it via `/graphify` (or `graphifyy` CLI) — don't assume a graph exists.
- **Think before coding:** restate the task in one line, weigh ≤2 approaches and pick the simplest, state assumptions, make surgical/local edits, define how you'll verify, and stop to ask when genuinely confused.
- **Token-aware:** usage is tracked (ccusage / claude-monitor). Prefer short focused iterations, avoid re-running expensive scans/builds, and for big jobs outline the plan + token impact before staging the work.
- **Conventions win:** match existing patterns; if a request conflicts with the rules above, follow the project rules and flag the conflict.
- **Multi-provider lanes:** Claude = hard/multi-file/security work; offload bulk research, summarizing, and routine mechanical edits to cheaper lanes (Gemini / local agents) when it conserves limits.
- **Continuity files (repo root):** `PLAN.md` = remaining work (source of truth) · `LOG.md` = done/decisions/blockers · `NEXT.md` = short switch-over notes. Update before a context reset or handoff to another model/session.
- **Session hygiene:** keep this file < ~200 lines (move deep specialized rules to `.claude/rules/*.md` with `paths:` frontmatter); manual `/compact` near ~50% context; recap to `LOG.md` every ~10–20 messages; keep subtasks under half the context window.

## Tables (Telegram) — ALWAYS use the shared helper
- `_pipe_table(headers, rows, right_cols=None, title=None, legend=None)` → Excel-style `<pre>`, **emoji/width-aware** (`_disp_w`: emoji/CJK=2) so columns align at the same index. `title` (bold+stars) and `legend` (italic key) render OUTSIDE `<pre>`.
- Put status emoji in **column 0** only (uniform 🟢/🔴/🟡 family) so it doesn't shift columns. Numbers → `right_cols`. K/M notation (452K not 452,000). Don't hand-roll `mono()` grids — route through `_pipe_table`.
- "test" = validate signal correctness vs DB history, not just that it runs.

## DB Schema (key tables)
- `options_change`: ticker, strike, expiry_date, trade_date_now, change_OI_Call/Put, openInt_Call/Put_now/prev, pct_change_OI_Call/Put, vol_Call/Put_now, lastPrice_Call/Put_now, R1, S1
- `stock_daily`: ticker, trade_date, close, pcr_oi (also high/low/volume)
- `options_daily`: same as options_change (raw daily snapshot)
- `trades`: trade_id, ticker, strategy, entry_date, expiry, status (OPEN/CLOSED), strike, option_type, quantity, entry_price, pnl
- `us_analytics_daily`: call_notional_oi, put_notional_oi, bull_score, bear_score, avg_spot
- self-managed: `signal_accuracy`, `signal_weights`, `momentum_ranks`, `gamma_wall_trades`, `event_journal`, `bookmarks`, `alert_dedup`

## Key functions
- `_oi_signal_light(call_chg, put_chg, pcr)` — hedge-aware aggregate OI signal
- `_oi_intent_algo(df, spot)` — per-strike intent (ATM/NEAR/DEEP zones)
- `_compute_gex(ticker, conn, spot)` — signed GEX, zero-gamma flip, call/put walls
- `high_prob_signals_engine(ticker, conn, spy_ret)` — 24-model ensemble (adaptive weights in `signal_weights`)
- `analyze_oi_rolls / analyze_mean_reversion / analyze_inst_signals / analyze_technical_signals`
- Scanners: `_spreads_scan_bot`, `_wheel_scan_bot`, `_hiprob_scan`, `_live_momentum_scanner`, `compute_universe_momentum`

## Signal logic
- Mean Rev composite: `PCR_z×1.5 - Price_z - NetOI_z`; ≥+3 → LONG; lookback 20d
- Gamma Walls: call+put OI ≥ 2× mean OI · Max Pain: min Σ ITM loss per expiry
- Put Skew: skip expiries where call < $0.50 · VIX/VIX3M >1.05 BACKWARDATION, <0.95 CONTANGO
- Spreads score = 0.40·POP + 0.25·R/R + 0.20·cushion + 0.15·liquidity; drop legs `maxp/maxl≤0.05`, credit `net/width<0.05`, `rr<0.10`. NaN IV is truthy → guard `not (iv>0)`.

## Streamlit caching
- `@st.cache_data(ttl=60)` yfinance: `_cached_history/_cached_price/load_oi_for_date/load_stock_daily`; `ttl=30` `_cached_trades`. Auto-close OPEN trades where expiry < today on portfolio load.
