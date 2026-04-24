# NYSE_DATA — Claude Code Reference

## DB Schema (key tables)
- `options_change`: ticker, strike, expiry_date, trade_date_now (MM-DD-YYYY), change_OI_Call/Put, openInt_Call/Put_now/prev, pct_change_OI_Call/Put, R1, S1
- `stock_daily`: ticker, trade_date (MM-DD-YYYY), close, pcr_oi
- `options_daily`: same structure as options_change (raw daily snapshot)
- `trades`: trade_id, ticker, strategy, entry_date, expiry, status (OPEN/CLOSED), strike, option_type, pnl
- `us_analytics_daily`: call_notional_oi, put_notional_oi, bull_score, bear_score, avg_spot

## Key Functions
- `_oi_signal_light(call_chg, put_chg, pcr)` — aggregate hedge-aware OI signal
- `_oi_intent_algo(df, spot)` — per-strike classification (ATM/NEAR/DEEP zones)
- `analyze_oi_rolls(ticker, conn)` — velocity spikes, strike rolls, calendar rolls, risk reversals
- `analyze_mean_reversion(ticker, conn)` — PCR Z-score, Price Z-score, PCR trend, Net OI extreme, Composite
- `inst_signals_detail(query, ticker)` — Put Skew, Max Pain, Gamma Walls

## Button Routing (telegram_bot.py button_handler)
- `oi_roll_` → `oi_roll_detail`
- `inst_sig_` → `inst_signals_detail`
- `mean_rev_` → `mean_rev_detail`

## Signal Logic
- Mean Rev composite: `PCR_z×1.5 - Price_z - NetOI_z`; score≥+3 → LONG idea; lookback 20d
- Gamma Walls: strikes where call+put OI ≥ 2× mean OI
- Max Pain: minimises sum of ITM loss = (strike - test) × OI per expiry
- Put Skew: skips expiries where call price < $0.50 (near-expiry artifact)
- VIX Term Structure: VIX/VIX3M ratio >1.05 = BACKWARDATION, <0.95 = CONTANGO

## Streamlit Caching
- `@st.cache_data(ttl=60)` — yfinance: `_cached_history()`, `_cached_price()`, `load_oi_for_date()`, `load_stock_daily()`
- `@st.cache_data(ttl=30)` — `_cached_trades()`
- Auto-close on portfolio load: trades WHERE status='OPEN' AND expiry < today

## Mobile Layout (Telegram)
- `<pre>` ≤28 chars wide; use K/M notation (452K not 452,000)
- Wide tables → HTML card format; 2-line cards for gamma walls, max pain
- Tech signals: `🟢 BULL [4/5] / RSI:28🔻 MACD:BUY↑ BB:BOT EMA:+1%`
