# NYSE_DATA — Claude Code Reference

## ⚡ Efficiency rules (read first — saves tokens / avoids limits)
- **Canonical files:** `telegram_bot_optimized.py` (~23k lines, THE running bot) · `dashboard.py` (Streamlit). Edit these directly — no patch/helper scripts.
- **NEVER read whole big files.** `telegram_bot_optimized.py`/`dashboard.py` are huge → use `Grep` to locate, then `Read` with `offset`/`limit`. Don't re-read a file you just edited.
- `telegram_bot_optimized.py` is now the **sole** bot source — edit it directly. The original `telegram_bot.py` + `build_optimized.py` (rebuild path) are retired under `archive/`; only revive them for a full dedup rebuild.
- Dates are `MM-DD-YYYY` strings → sort with `substr(d,7,4)||substr(d,1,2)||substr(d,4,2)`.
- `datetime.utcnow()` → `datetime.now(timezone.utc).replace(tzinfo=None)` (3.12+).
- Dead/NULL cols: `vol_rank_call/put`, `money_coi_*`. SPY PCR can spike 11+ on expiry (not signal).
- Secrets: `token.txt`, `us_bot_*.txt`, `api_keys.env/.enc` are gitignored — never commit/print. `*.db`, `logs/`, `*.log` ignored too.
- Git: commit to `main` directly (no feature branches) only when asked.

## 🧭 Working method (think → act, save tokens)
<!-- Condensed; full long-form detail in .claude/rules/workflow.md (loads contextually). -->
- **Graph first:** if `graphify-out/GRAPH_REPORT.md` exists, read it before `Glob`/`Grep` to locate god-files, subsystems, and reusable code. If absent, generate it via `/graphify` (or `graphifyy` CLI) — don't assume a graph exists.
- **Think before coding:** restate the task in one line, weigh ≤2 approaches and pick the simplest, state assumptions, make surgical/local edits, define how you'll verify, and stop to ask when genuinely confused.
- **Token-aware:** usage is tracked (ccusage / claude-monitor). Prefer short, file-scoped spec-style prompts; avoid re-running expensive scans/builds; reuse cached context; disable unused tools/MCP servers; for big jobs outline the plan + token impact before staging.
- **Scope small:** one focused task per prompt, ~2–3 sessions/day; lean on slash commands and existing skills instead of ad-hoc multi-step asks.
- **Conventions win:** match existing patterns; if a request conflicts with the rules above, follow the project rules and flag the conflict.
- **Multi-provider lanes:** Claude = hard/multi-file/security work; offload bulk research, summarizing, and routine mechanical edits to cheaper lanes (Gemini / local agents) when it conserves limits.
- **Continuity files (repo root):** `PLAN.md` = remaining work (source of truth) · `LOG.md` = done/decisions/blockers · `NEXT.md` = short switch-over notes. Update before a context reset or handoff to another model/session.
- **Session hygiene:** keep this file < ~200 lines (move deep specialized rules to `.claude/rules/*.md` with `paths:` frontmatter); manual `/compact` near ~50% context; recap to `LOG.md` every ~10–20 messages; keep subtasks under half the context window.
- **Limit lockouts:** when usage is throttled, write a fresh-start summary to `LOG.md`/`NEXT.md` and resume cold from those rather than replaying the whole thread.

## ▶️ Run · build · test
- **Bot (runtime):** `python telegram_bot_optimized.py` → `main()` (≈L23203) → `app.run_polling`; token from `token.txt`. This is the live process — edit it directly for runtime fixes.
- **Rebuild (retired):** `telegram_bot_optimized.py` was originally generated from `telegram_bot.py` by `build_optimized.py` — both now in `archive/`. Edit `telegram_bot_optimized.py` directly; only revive the pair (run under WSL: `python3 archive/build_optimized.py`, after fixing its hardcoded `/mnt/c/...` paths) for a one-off full dedup rebuild.
- **Dashboard:** `streamlit run dashboard.py`.
- **EOD pipeline:** `run_all_offhours.py` = NY-time-gated scheduler (pre-mkt 00:00–09:00 → prev trading day; post-close 17:00+ → today; keeps Windows awake). Launches JOB1 `NYSE_YFin.py` (yfinance/curl_cffi fetch → writes `US_data.db`) then JOB2 `NYSE_Telegram.py` (OI/price/vol PNGs + Excel + send).
- **Tests / parallel `core/` / migrations:** moved to `archive/` (see below). Run from there, e.g. `cd archive && python -m core.validate --ticker SPY` or `pytest archive/tests/`.

## 🗺️ Repo map
- **Entrypoints (root):** `telegram_bot_optimized.py` (bot) · `dashboard.py` (Streamlit, launched by bot) · `run_all_offhours.py` (EOD scheduler → `NYSE_YFin.py` + `NYSE_Telegram.py`).
- **Data layer:** `NYSE_YFin.py` (fetch/enrich → DB) · `NYSE_Telegram.py` (daily report + charts).
- **`archive/`** (not wired into the running bot): original build pair `telegram_bot.py` + `build_optimized.py`; duplicates/standalone `bot_optimized.py`, `streamlit_dashboard.py`, `send_organized_report.py`, `run_event_writeups.py`, `run_eod_pipeline.py`+`eod_pipeline/`, `NSE.py`; tools `core/`, `tests/`, `migrations/`; and `_lib/{abnormal_activity_detector,market_events_db,options_flow_detector,telegram_rich_formatter}`.

### `_lib/` modules (root — the 7 the bot actually loads)
- `event_writeup_engine` — automated pre/post-market event narratives (macro releases, earnings, intraday regime breaks). `event_writeup_bot_hooks` — Telegram scheduling hooks (ET times) for those writeups.
- `news_and_earnings` — Finnhub news/earnings/dividends/events. `market_news_aggregator` — news+data across stocks/indices/commodities/FX/crypto. `market_news_enhanced` — premium-source news with links.
- `options_tracker` — open-positions/Greeks helper; dynamically imported by the bot for `get_open_positions`.
- (Archived `_lib`: `market_events_db`, `options_flow_detector`, `abnormal_activity_detector`, `telegram_rich_formatter` — used only by archived apps.)

### Bot commands (registered in `telegram_bot_optimized.py`)
- `/start` `/menu` entry + command list · `/gex` signed GEX profile (walls, zero-gamma flip) · `/vanna` vanna exposure · `/opex` OPEX / max pain · `/regime` market regime (VIX term structure) · `/squeeze` squeeze scan.
- Scanners: `/spreads` · `/wheel` (CSP) · `/hiprob` high-prob ensemble · `/momentum` momentum ranks.
- Narratives/data: `/wrap` market wrap · `/briefing` daily briefing · `/macro` macro (BLS+yields) · `/earnings` earnings/news · `/event` event writeup · `/logevent` add event.
- Tools: `/plan` trade planner · `/journal` trade/event journal · `/bookmarks` saved items · `/tv` TradingView chart bridge.

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

## 🔬 Signal validation ("test" = prove it would've been right)
Running ≠ tested. To validate a signal, backtest it against DB history and report hit-rate + avg forward return, not just "no crash."
1. **Pull historical fires** of the signal from `options_change`/`stock_daily` (or recompute it per past `trade_date`). Date sort key: `substr(d,7,4)||substr(d,1,2)||substr(d,4,2)` (dates are `MM-DD-YYYY`).
2. **Join forward return:** for each fire on day *t*, get `stock_daily.close` at *t+N* (N≈3/5/10) for the same ticker → `fwd_ret = close_{t+N}/close_t - 1`.
3. **Score by bucket:** hit-rate = % of fires where `sign(fwd_ret)` matches the call (LONG→up); also avg `fwd_ret`. Compare vs the unconditional baseline over the same window.
4. **Persist** results to `signal_accuracy` and let adaptive weights flow to `signal_weights` (the ensemble reads these).
5. **Report** as a `_pipe_table` (signal · N · hit% · avg fwd · vs base). Flag thin samples — current ~6-mo hit-rates are weak/low-N, so don't over-claim.

## Streamlit caching
- `@st.cache_data(ttl=60)` yfinance: `_cached_history/_cached_price/load_oi_for_date/load_stock_daily`; `ttl=30` `_cached_trades`. Auto-close OPEN trades where expiry < today on portfolio load.
