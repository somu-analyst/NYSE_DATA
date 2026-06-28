# Contributing

Thanks for your interest! This is an actively developed personal project — issues,
ideas, and PRs are welcome.

## Project layout
- **Root = runtime only:** `telegram_bot_optimized.py` (the bot), `dashboard.py`
  (Streamlit), `run_all_offhours.py` → `NYSE_YFin.py` + `NYSE_Telegram.py` (data pull),
  and `_lib/` (bot helpers).
- **`archive/`** — non-runtime: the parallel `core/` analytics package, tests, the
  original build source, and superseded apps.
- **`CLAUDE.md`** — the engineering reference (DB schema, key functions, conventions).

## Setup
```bash
pip install -r requirements.txt
streamlit run dashboard.py        # web terminal
python telegram_bot_optimized.py  # bot (token in token.txt)
```
The apps read from `US_data.db` (not committed — it's large/local). The EOD pipeline
populates it from options-chain + yfinance feeds.

## Conventions (please follow)
- **Edit `telegram_bot_optimized.py` directly** — no patch/codegen scripts.
- **Dates are ISO `YYYY-MM-DD`** — sort with a plain `ORDER BY`.
- **Telegram tables** go through the shared `_pipe_table` helper (emoji/width-aware).
- **Never commit secrets** — `token.txt`, `us_bot_*.txt`, `api_keys.*`, or `*.db`.

## Tests
```bash
cd archive && python -m pytest tests/test_core.py tests/test_core_gex.py -q
```
> Note: in this project, **"tested" means a signal is validated against DB history**
> (hit-rate + forward return vs baseline) — not merely that the code runs. See the
> Signal validation section in `CLAUDE.md`.

## Disclaimer
Educational/research use only — **not financial advice**.
