# NYSE_DATA Bot & Dashboard — Architecture Flowchart

## System Overview

```
┌─────────────────────────────────────────────────────────────────────┐
│                        DATA SOURCES                                  │
│  NYSE/CBOE options chain  ·  Yahoo Finance  ·  VIX/VIX3M feeds      │
└─────────────────────────────────────────────────────────────────────┘
           │                        │                      │
           ▼                        ▼                      ▼
┌──────────────────┐  ┌─────────────────────┐  ┌──────────────────────┐
│  EOD Ingest      │  │  Live Ingest        │  │  yfinance (on-demand)│
│  options_daily   │  │  options_change     │  │  stock_daily         │
│  stock_daily     │  │  (live vs EOD)      │  │  (price / history)   │
└──────────────────┘  └─────────────────────┘  └──────────────────────┘
           │                        │                      │
           └──────────────┬─────────┘──────────────────────┘
                          ▼
             ┌────────────────────────┐
             │  SQLite — US_data.db   │
             │  options_change        │
             │  options_daily         │
             │  stock_daily           │
             │  trades                │
             │  us_analytics_daily    │
             └────────────────────────┘
                          │
            ┌─────────────┴──────────────┐
            ▼                            ▼
┌───────────────────────┐   ┌────────────────────────────┐
│  telegram_bot.py      │   │  dashboard.py (Streamlit)  │
│  ~24k lines           │   │  Web UI                    │
└───────────────────────┘   └────────────────────────────┘
```

---

## Telegram Bot Flow

```
User message / button tap
          │
          ▼
┌──────────────────────────────────────────┐
│  PTB Application (python-telegram-bot)   │
│  CommandHandler / CallbackQueryHandler   │
└──────────────────────────────────────────┘
          │
    ┌─────┴──────┐
    │            │
    ▼            ▼
Commands      Button callbacks (callback_data prefix routing)
/start        oi_roll_TICKER  → oi_roll_detail()
/oi           inst_sig_TICKER → inst_signals_detail()
/scan         mean_rev_TICKER → mean_rev_detail()
/trades       chart_TICKER    → oi_change_chart_eod_view()
...           live_TICKER     → oi_change_chart_live_view()
              mf_TICKER       → mirofish_ticker_detail()
```

---

## OI Analysis Pipeline

```
User taps ticker button
          │
          ▼
┌──────────────────────────────────────────────────────────────────┐
│  mirofish_ticker_detail(query, ticker)                           │
│                                                                  │
│  asyncio.gather([                                                │
│    _bg(_oi_key_levels)   ──► Call Wall / Put Wall / Max Pain    │
│    _bg(_compute_gex)     ──► Gamma Exposure (GEX) curve         │
│    _bg(_get_earnings_dte)──► Next earnings date                 │
│  ])                                                              │
│                                                                  │
│  Sequential adds:                                                │
│    _oi_signal_verdict()   ──► BULLISH/BEARISH/NEUTRAL verdict   │
│    _oi_volume_chart()     ──► Volume profile PNG                │
│    _oi_multiday_conviction_text() ──► 5d/10d/20d summary       │
└──────────────────────────────────────────────────────────────────┘
          │
          ▼
   Telegram messages:
   1. Text card (key levels, GEX, earnings)
   2. OI Signal verdict (HTML text)
   3. Volume Profile chart (photo)
   4. Multi-day conviction (text)
```

---

## EOD vs EOD View

```
chart_TICKER button
      │
      ▼
oi_change_chart_eod_view()
      │
      ├──► _oi_week_heatmap()         ──► 6-panel PNG (5d/10d/30d × calls/puts)
      │    color: red=fade, green=build, gray=neutral (change direction)
      │
      ├──► _oi_signal_verdict()       ──► Verdict text (BULLISH/BEARISH/etc)
      │    • Compares today vs prev date OI totals
      │    • Call Wall / Put Wall / Max Pain levels
      │    • "Simple Answer" bull/bear targets
      │
      ├──► _oi_volume_chart()         ──► Volume profile PNG
      │    • Top 20 strikes by volume ±20% of spot
      │    • Calls +ve bars, Puts -ve bars
      │    • OI as dashed outline overlay
      │
      └──► _oi_multiday_conviction_text() ──► 5d/10d/20d conviction summary
```

---

## Live vs EOD View

```
live_TICKER button
      │
      ▼
oi_change_chart_live_view()
      │
      ├──► _oi_live_chart()            ──► Live vs EOD delta PNG
      │
      ├──► _oi_signal_verdict()        ──► Same verdict format as EOD view
      │
      ├──► _oi_volume_chart()          ──► Volume profile (live date)
      │
      └──► _oi_multiday_conviction_text() ──► Multi-day summary
```

---

## Signal Logic Chain

```
Raw OI data (options_change)
          │
          ├──► _oi_signal_light(call_chg, put_chg, pcr)
          │    ► Aggregate hedge-aware signal
          │    ► Returns: BULLISH / BEARISH / STRADDLE / UNWIND / NEUTRAL
          │
          ├──► _oi_intent_algo(df, spot)
          │    ► Per-strike classification
          │    ► ATM zone (±2%) / NEAR (±5%) / DEEP zones
          │    ► Pattern: CALL_BUILD / PUT_UNWIND / GAMMA_WALL etc
          │
          ├──► analyze_oi_rolls(ticker, conn)
          │    ► Velocity spikes (OI change speed)
          │    ► Strike rolls (OI moving up/down strikes)
          │    ► Calendar rolls (near→far expiry shift)
          │    ► Risk reversals (asymmetric call/put build)
          │
          ├──► analyze_mean_reversion(ticker, conn)
          │    ► PCR Z-score (20d lookback)
          │    ► Price Z-score
          │    ► Net OI extreme
          │    ► Composite = PCR_z×1.5 − Price_z − NetOI_z
          │    ► Score ≥+3 → LONG idea
          │
          └──► inst_signals_detail(query, ticker)
               ► Put Skew (OTM put premium vs call premium)
               ► Max Pain (minimise sum of ITM losses)
               ► Gamma Walls (strikes ≥ 2× mean OI)
               ► VIX Term Structure (VIX/VIX3M ratio)
```

---

## Streamlit Dashboard Flow

```
Browser → dashboard.py (Streamlit)
               │
               ├── Market Overview tab
               │   └── us_analytics_daily → bull_score/bear_score/notional OI
               │
               ├── OI Analysis tab
               │   ├── load_oi_for_date()    @cache(ttl=60)
               │   ├── load_stock_daily()    @cache(ttl=60)
               │   └── Charts: OI heatmap, strike breakdown, volume profile
               │
               ├── Trades tab
               │   ├── _cached_trades()      @cache(ttl=30)
               │   └── Auto-close expired OPEN trades
               │
               └── Gamma / GEX tab
                   └── _compute_gex() → gamma exposure curve
```

---

## Key Data Tables

| Table | Key Columns | Update Freq |
|-------|-------------|-------------|
| `options_change` | ticker, strike, expiry_date, trade_date_now, openInt_Call/Put_now/prev, vol_Call/Put_now | Intraday / EOD |
| `options_daily` | same as options_change | EOD snapshot |
| `stock_daily` | ticker, trade_date, close, pcr_oi | EOD |
| `trades` | trade_id, ticker, strategy, entry_date, expiry, status, strike, option_type, pnl | Manual / auto-close |
| `us_analytics_daily` | call_notional_oi, put_notional_oi, bull_score, bear_score, avg_spot | Daily |

---

## Async Performance Pattern

```python
# Slow (sequential — ~6s total):
result1 = sync_db_call_1()   # 2s
result2 = sync_db_call_2()   # 2s
result3 = sync_db_call_3()   # 2s

# Fast (parallel — ~2s total):
r1, r2, r3 = await asyncio.gather(
    _bg(sync_db_call_1),
    _bg(sync_db_call_2),
    _bg(sync_db_call_3),
)
# _bg() = loop.run_in_executor(ThreadPoolExecutor, fn)
```

---

## Message Size Limits

| Type | Limit | Handling |
|------|-------|----------|
| Telegram text | 4096 chars | `[:3500]` truncation / split |
| `<pre>` block width | 28 chars (mobile) | Pipe-box format `\|val\|val\|` |
| Photo caption | 1024 chars | Keep captions short |
| Inline keyboard | 8 buttons/row max | 2–3 per row typical |
