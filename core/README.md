# core/ — parallel modular analytics

A clean, importable, **read-only** counterpart to the ~23k-line
`telegram_bot_optimized.py`. The bot stays the runtime; `core/` is where pure
analytics live so they can be **tested, reused, and reasoned about cheaply**
without loading the monolith.

## Principles
- **Never touches the bot.** No imports of `telegram_bot*`; no Telegram I/O.
- **Read-only DB.** `core.db.get_conn()` opens `US_data.db` with `mode=ro`.
- **Pure where possible.** `core/signals.py` is I/O-free and unit-tested.
- **Small steps.** Port one cohesive function at a time; add a test with it.

## Modules
| File | Purpose |
|------|---------|
| `db.py` | Read-only `US_data.db` connection (env-overridable paths). |
| `dates.py` | MM-DD-YYYY parse + `sort_key` matching the SQL date idiom. |
| `signals.py` | Pure signals (mean-reversion composite today). |
| `validate.py` | Backtester: signal fires → forward returns → hit-rate vs baseline. |

## Use
```bash
python -m core.validate --ticker SPY --horizon 5 --threshold 3
pytest tests/test_core.py
```

## Roadmap (port next, one at a time)
- GEX profile (`_compute_gex`) as a pure function over an options-chain DataFrame.
- OI intent / gamma walls / max-pain as pure functions.
- A signal registry so `validate.py` can backtest any registered signal by name,
  then persist results to the `signal_accuracy` table.
