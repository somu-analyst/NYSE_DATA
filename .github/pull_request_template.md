## What & why
<!-- Short summary of the change and the motivation. -->

## Area
- [ ] Telegram bot (`telegram_bot_optimized.py`)
- [ ] Streamlit dashboard (`dashboard.py`)
- [ ] Data pipeline (`NYSE_YFin.py` / `NYSE_Telegram.py` / `run_all_offhours.py`)
- [ ] `core/` analytics
- [ ] Docs

## Checks
- [ ] Edited the runtime file directly (no patch/codegen scripts)
- [ ] Dates kept ISO `YYYY-MM-DD`; Telegram tables via `_pipe_table`
- [ ] Core tests pass (`cd archive && python -m pytest tests/test_core.py tests/test_core_gex.py`)
- [ ] No secrets committed (`token.txt`, `us_bot_*.txt`, `api_keys.*`, `*.db`)
