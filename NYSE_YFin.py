import os
import sys
import time
import json
import requests
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
import yfinance as yf
import pytz
import concurrent.futures
from concurrent.futures import ThreadPoolExecutor, as_completed
from io import StringIO  # for pd.read_html on HTML string
import pandas_market_calendars as mcal  # NYSE calendar
from curl_cffi import requests as curl_requests  # curl_cffi session

# ============= RUNTIME START =============
SCRIPT_START_TIME = time.time()

# ================== CONFIG ==================

DATA_DIR = r"C:\Users\srini\Options_chain_data"
UNIVERSE_FILE = os.path.join(DATA_DIR, "ticker_universe.csv")
LOG_DIR = DATA_DIR

CATEGORY_SP500     = "sp500"
CATEGORY_NON_SP500 = "non_s&p"
CATEGORY_INDEX     = "index"
CATEGORY_METAL     = "metal"
CATEGORY_COMMODITY = "commodity"
CATEGORY_BOND      = "bond"
CATEGORY_CRYPTO    = "crypto"
CATEGORY_OTHER     = "other"

INDEX_TICKERS = [
    "QQQ", "SPY", "IWM", "DIA", "IVV", "VOO", "SPLG", "SPYG", "SPYV", "IBIT"
]

METALS_TICKERS = ["GLD", "IAU", "SGOL", "PHYS", "SLV", "SIVR", "PSLV", "SIL"]
COMMODITY_TICKERS = ["USO", "CPER"]
BOND_TICKERS = ["AGG", "BND", "SCHZ", "FBND", "IUSB", "SPAB", "VTEB"]
CRYPTO_TICKERS = ["BTC-USD", "ETH-USD", "SOL-USD", "BNB-USD",
                  "XRP-USD", "ADA-USD", "DOGE-USD", "TRX-USD"]

EXTRA_STOCKS = ["SOFI"]

# ========= HELPER: ticker normalization & progress bar ==========

def yf_ticker_fix(ticker):
    return ticker.replace('.', '-')

def print_progress_bar(current, total, bar_length=50, prefix="Progress"):
    percent = (current / total) * 100 if total > 0 else 0
    filled_length = int(bar_length * current // total) if total > 0 else 0
    bar = '█' * filled_length + '-' * (bar_length - filled_length)
    sys.stdout.write(f'\r{prefix}: |{bar}| {percent:.1f}% ({current}/{total})')
    sys.stdout.flush()
    if current == total:
        print()

# ========== UNIVERSE CSV HELPERS ==========

def load_universe(path):
    if not os.path.exists(path):
        return pd.DataFrame(columns=["ticker", "name", "category"])
    try:
        df = pd.read_csv(path, dtype=str)
        for col in ["ticker", "name", "category"]:
            if col not in df.columns:
                df[col] = ""
        df["ticker"] = df["ticker"].astype(str)
        df["name"] = df["name"].astype(str)
        df["category"] = df["category"].astype(str)
        return df[["ticker", "name", "category"]]
    except Exception:
        return pd.DataFrame(columns=["ticker", "name", "category"])

def save_universe(df, path):
    if df.empty:
        return
    df = df.sort_values("ticker")
    df.to_csv(path, index=False)

def yahoo_name_from_ticker(symbol):
    url = "https://query2.finance.yahoo.com/v1/finance/search"
    headers = {"User-Agent": "Mozilla/5.0"}
    params = {"q": symbol, "quotes_count": 1, "lang": "en-US"}
    try:
        r = requests.get(url, headers=headers, params=params, timeout=5)
        r.raise_for_status()
        data = r.json()
        for q in data.get("quotes", []):
            if q.get("symbol") == symbol:
                return q.get("shortname") or q.get("longname")
    except Exception:
        pass
    return None

def get_sp500_name_map():
    url = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        )
    }
    resp = requests.get(url, headers=headers, timeout=10)
    resp.raise_for_status()
    tables = pd.read_html(StringIO(resp.text))

    target = None
    for t in tables:
        cols = {c.lower() for c in t.columns.astype(str)}
        if "symbol" in cols and "security" in cols:
            target = t
            break
    if target is None:
        raise RuntimeError("Could not find S&P 500 table with Symbol/Security columns")

    col_map = {}
    for c in target.columns:
        lc = str(c).lower()
        if lc == "symbol":
            col_map[c] = "ticker"
        elif lc == "security":
            col_map[c] = "name"
    target = target.rename(columns=col_map)
    df = target[["ticker", "name"]]
    return dict(zip(df["ticker"], df["name"]))

def classify_category(ticker, current_sp500_set):
    if ticker in [yf_ticker_fix(t) for t in INDEX_TICKERS]:
        return CATEGORY_INDEX
    if ticker in [yf_ticker_fix(t) for t in METALS_TICKERS]:
        return CATEGORY_METAL
    if ticker in [yf_ticker_fix(t) for t in COMMODITY_TICKERS]:
        return CATEGORY_COMMODITY
    if ticker in [yf_ticker_fix(t) for t in BOND_TICKERS]:
        return CATEGORY_BOND
    if ticker in [yf_ticker_fix(t) for t in CRYPTO_TICKERS]:
        return CATEGORY_CRYPTO
    if ticker in current_sp500_set:
        return CATEGORY_SP500
    return CATEGORY_NON_SP500

def prepare_universe_and_name_map():
    print("📁 Loading existing ticker universe ...")
    universe_df = load_universe(UNIVERSE_FILE)
    existing_tickers = set(universe_df["ticker"])

    current_sp500_set = set(universe_df.loc[universe_df["category"] == CATEGORY_SP500, "ticker"])

    base_tickers = (
        [yf_ticker_fix(t) for t in INDEX_TICKERS] +
        [yf_ticker_fix(t) for t in METALS_TICKERS] +
        [yf_ticker_fix(t) for t in COMMODITY_TICKERS] +
        [yf_ticker_fix(t) for t in BOND_TICKERS] +
        [yf_ticker_fix(t) for t in CRYPTO_TICKERS] +
        [yf_ticker_fix(t) for t in EXTRA_STOCKS]
    )
    target_set = set(base_tickers) | existing_tickers

    new_tickers = sorted(target_set - existing_tickers)
    if new_tickers:
        print(f"🆕 Found {len(new_tickers)} new tickers to add to universe")

    sp500_wiki_map = {}
    try:
        if new_tickers:
            sp500_wiki_map = get_sp500_name_map()
    except Exception as e:
        print("⚠️ Could not load S&P 500 Wikipedia table for universe enrichment:", e)
        sp500_wiki_map = {}

    new_rows = []
    total = len(new_tickers)
    for i, t in enumerate(new_tickers, 1):
        name = yahoo_name_from_ticker(t)
        if not name and sp500_wiki_map:
            base = t.replace('-', '.')
            name = sp500_wiki_map.get(base)
        if not name:
            name = t

        cat = classify_category(t, current_sp500_set)
        new_rows.append({"ticker": t, "name": name, "category": cat})
        print_progress_bar(i, total, prefix="🏢 Universe (new)")

    if new_rows:
        universe_df = pd.concat([universe_df, pd.DataFrame(new_rows)], ignore_index=True)

    save_universe(universe_df, UNIVERSE_FILE)
    print(f"\n💾 Universe saved with {len(universe_df)} total tickers")

    name_map = dict(zip(universe_df["ticker"], universe_df["name"]))
    all_tickers = sorted(universe_df["ticker"])
    return name_map, all_tickers

# ============= TRADING DAY USING NYSE CALENDAR =============

def get_eod_trading_day(max_back=10):
    print("📅 Determining end-of-day trading date using NYSE calendar ...")
    eastern = pytz.timezone('US/Eastern')
    now = datetime.now(eastern)

    market_close_hour = 16
    market_close_minute = 10

    nyse = mcal.get_calendar('NYSE')
    end = now.date()
    start = end - timedelta(days=max_back + 7)
    sched = nyse.schedule(start_date=start, end_date=end)

    sched_index = sched.index.tz_localize(nyse.tz)
    trading_days = sched_index.tz_convert(eastern).date

    if len(trading_days) == 0:
        raise Exception("No NYSE trading days in range; check dates/calendar.")

    today_date = now.date()
    after_close = (now.hour > market_close_hour or
                   (now.hour == market_close_hour and now.minute >= market_close_minute))

    if today_date in trading_days and after_close:
        use_day = today_date
    else:
        valid_days = [d for d in trading_days if d < today_date or (d == today_date and after_close)]
        if not valid_days:
            use_day = trading_days[-1]
        else:
            use_day = valid_days[-1]

    print(f"✅ Using NYSE trading day: {use_day.isoformat()}")
    return eastern.localize(datetime(use_day.year, use_day.month, use_day.day))

# ============= ENRICHMENT WITH OHLC (PARALLEL, UNIQUE CONTRACTS) =============

def enrich_with_option_ohlc_parallel(df: pd.DataFrame,
                                     call_symbol_col="contractSymbol_Call",
                                     put_symbol_col="contractSymbol_Put",
                                     max_workers=32) -> pd.DataFrame:
    call_syms = df[call_symbol_col].dropna().astype(str).unique() if call_symbol_col in df.columns else []
    put_syms  = df[put_symbol_col].dropna().astype(str).unique() if put_symbol_col in df.columns else []
    all_syms = np.unique(np.concatenate([call_syms, put_syms])) if len(call_syms) + len(put_syms) > 0 else []
    print(f"🔁 Enriching {len(all_syms)} unique option contracts with OHLC snapshot...")

    # Use single curl_cffi session for all contract lookups (issue 2422 fix)
    session = curl_requests.Session(impersonate="chrome")

    def fetch_info(sym):
        try:
            tk = yf.Ticker(sym, session=session)
            info = tk.info
            return sym, {
                "open": info.get("regularMarketOpen"),
                "high": info.get("regularMarketDayHigh"),
                "low":  info.get("regularMarketDayLow"),
                "close": info.get("regularMarketPrice"),
                "bid":  info.get("bid"),
                "ask":  info.get("ask"),
                "volume": info.get("regularMarketVolume"),
                "openInterest": info.get("openInterest"),
            }
        except Exception:
            # On any crumb/rate-limit/HTTP error, skip this symbol
            return sym, None

    info_map = {}
    if len(all_syms) > 0:
        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            futures = {ex.submit(fetch_info, s): s for s in all_syms}
            total = len(futures)
            for i, fut in enumerate(as_completed(futures), 1):
                sym, data = fut.result()
                info_map[sym] = data
                print_progress_bar(i, total, prefix="📊 OHLC Snapshots")

    new_cols = [
        "call_open", "call_high", "call_low", "call_close",
        "call_bid_info", "call_ask_info",
        "call_volume_info", "call_openInterest_info",
        "put_open", "put_high", "put_low", "put_close",
        "put_bid_info", "put_ask_info",
        "put_volume_info", "put_openInterest_info",
    ]
    for c in new_cols:
        if c not in df.columns:
            df[c] = np.nan

    if call_symbol_col in df.columns:
        for idx, cs in df[call_symbol_col].dropna().items():
            data = info_map.get(str(cs))
            if not data:
                continue
            df.at[idx, "call_open"]              = data["open"]
            df.at[idx, "call_high"]              = data["high"]
            df.at[idx, "call_low"]               = data["low"]
            df.at[idx, "call_close"]             = data["close"]
            df.at[idx, "call_bid_info"]          = data["bid"]
            df.at[idx, "call_ask_info"]          = data["ask"]
            df.at[idx, "call_volume_info"]       = data["volume"]
            df.at[idx, "call_openInterest_info"] = data["openInterest"]

    if put_symbol_col in df.columns:
        for idx, ps in df[put_symbol_col].dropna().items():
            data = info_map.get(str(ps))
            if not data:
                continue
            df.at[idx, "put_open"]               = data["open"]
            df.at[idx, "put_high"]               = data["high"]
            df.at[idx, "put_low"]                = data["low"]
            df.at[idx, "put_close"]              = data["close"]
            df.at[idx, "put_bid_info"]           = data["bid"]
            df.at[idx, "put_ask_info"]           = data["ask"]
            df.at[idx, "put_volume_info"]        = data["volume"]
            df.at[idx, "put_openInterest_info"]  = data["openInterest"]

    return df

# ============= OPTIONS FETCH & MERGE =============

def fetch_option_chain(ticker, company_name, asset_type, trade_day_str):
    # curl_cffi session to reduce rate limits (same pattern as issue 2422)
    session = curl_requests.Session(impersonate="chrome")
    tk = yf.Ticker(ticker, session=session)

    results = []
    try:
        if hasattr(tk, 'options') and tk.options:
            for exp in tk.options:
                oc = tk.option_chain(exp)

                calls = oc.calls[['contractSymbol', 'strike', 'openInterest', 'lastPrice', 'volume']].rename(columns={
                    'contractSymbol': 'contractSymbol_Call',
                    'openInterest': 'openInt_Call',
                    'lastPrice': 'lastPrice_Call',
                    'volume': 'vol_Call'
                })

                puts = oc.puts[['contractSymbol', 'strike', 'openInterest', 'lastPrice', 'volume']].rename(columns={
                    'contractSymbol': 'contractSymbol_Put',
                    'openInterest': 'openInt_Put',
                    'lastPrice': 'lastPrice_Put',
                    'volume': 'vol_Put'
                })

                calls['expiry_date'] = exp
                puts['expiry_date'] = exp

                merged = pd.merge(calls, puts, on=['strike', 'expiry_date'], how='outer')
                merged['ticker'] = ticker
                merged['asset_type'] = asset_type
                merged['company_name'] = company_name
                merged['trade_date'] = trade_day_str
                results.append(merged)
        else:
            results.append(pd.DataFrame({
                'ticker': [ticker],
                'company_name': [company_name],
                'asset_type': [asset_type],
                'vol_Call': [np.nan], 'lastPrice_Call': [np.nan], 'openInt_Call': [np.nan], 'strike': [np.nan],
                'openInt_Put': [np.nan], 'lastPrice_Put': [np.nan], 'vol_Put': [np.nan],
                'expiry_date': [np.nan], 'trade_date': [trade_day_str]
            }))
    except Exception:
        results.append(pd.DataFrame({
            'ticker': [ticker],
            'company_name': [company_name],
            'asset_type': [asset_type],
            'vol_Call': [np.nan], 'lastPrice_Call': [np.nan], 'openInt_Call': [np.nan], 'strike': [np.nan],
            'openInt_Put': [np.nan], 'lastPrice_Put': [np.nan], 'vol_Put': [np.nan],
            'expiry_date': [np.nan], 'trade_date': [trade_day_str]
        }))
    return results

def merge_calls_puts_per_strike_parallel(trade_day, company_name_map, all_tickers):
    print(f"🔄 Starting options chain collection with threading for {trade_day.strftime('%Y-%m-%d')}")
    trade_day_str = trade_day.strftime('%d%b%Y')

    def infer_asset_type(ticker):
        if ticker in [yf_ticker_fix(t) for t in INDEX_TICKERS]:
            return "index"
        if ticker in [yf_ticker_fix(t) for t in METALS_TICKERS]:
            return "gold" if "G" in ticker else "silver"
        if ticker in [yf_ticker_fix(t) for t in COMMODITY_TICKERS]:
            return "crude" if ticker.endswith("USO") else "copper"
        if ticker in [yf_ticker_fix(t) for t in BOND_TICKERS]:
            return "bond"
        if ticker in [yf_ticker_fix(t) for t in CRYPTO_TICKERS]:
            return "crypto"
        return "stock"

    args = [
        (ticker, company_name_map.get(ticker, ticker), infer_asset_type(ticker), trade_day_str)
        for ticker in all_tickers
    ]

    all_rows = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=16) as executor:
        futures = [executor.submit(fetch_option_chain, *a) for a in args]
        total = len(futures)
        for i, f in enumerate(futures, 1):
            all_rows += f.result()
            print_progress_bar(i, total, prefix="📈 Options Data")

    print(f"\n📊 All tickers processed. Saving file ...")
    if all_rows:
        df_final = pd.concat(all_rows, ignore_index=True)

        before = len(df_final)
        df_final = df_final.drop_duplicates()
        after = len(df_final)
        print(f"🧹 Removed {before - after} exact duplicate rows")

        print("📈 Enriching options data with per‑contract OHLC via yfinance.info ...")
        df_final = enrich_with_option_ohlc_parallel(df_final)

        # Reorder columns: ticker, asset_type, company_name first; contractSymbol_Call/Put last
        cols = list(df_final.columns)
        first_cols = [c for c in ["ticker", "asset_type", "company_name"] if c in cols]
        last_cols = [c for c in ["contractSymbol_Call", "contractSymbol_Put"] if c in cols]
        middle_cols = [c for c in cols if c not in first_cols + last_cols]
        new_order = first_cols + middle_cols + last_cols
        df_final = df_final[new_order]

        out_file = os.path.join(DATA_DIR, f"Options_Strike_CallPut_{trade_day_str}.csv")
        df_final.to_csv(out_file, index=False)
        print(f"✅ Output file saved: {out_file}")
        print(f"📈 Total records: {len(df_final)}")
        print("Sample record for today:\n", df_final.head(1).to_string(index=False))
        return df_final, out_file
    print("❌ No call/put pairs to merge.")
    return None, None

# ============= AUDIT BLANK / EMPTY ROWS =============

def audit_empty_option_rows(df: pd.DataFrame, trade_day_str: str):
    """
    Audit rows where both call and put OI and volume are NaN or zero and
    write them to a timestamped CSV log.
    """
    if df is None or df.empty:
        print("🔍 Audit: DataFrame is empty, nothing to audit.")
        return

    oi_call = df.get("openInt_Call", pd.Series(index=df.index, data=np.nan)).fillna(0)
    oi_put  = df.get("openInt_Put",  pd.Series(index=df.index, data=np.nan)).fillna(0)
    vol_call = df.get("vol_Call",   pd.Series(index=df.index, data=np.nan)).fillna(0)
    vol_put  = df.get("vol_Put",    pd.Series(index=df.index, data=np.nan)).fillna(0)

    mask_empty = (oi_call == 0) & (oi_put == 0) & (vol_call == 0) & (vol_put == 0)
    empty_rows = df[mask_empty].copy()

    total_empty = len(empty_rows)
    total_rows = len(df)
    print(f"\n🔍 Audit: Found {total_empty} rows with no call/put OI and volume "
          f"out of {total_rows} total rows.")

    if total_empty == 0:
        return

    ts = datetime.now().strftime("%Y%m%d_%H%M")
    log_name = f"Options_Audit_EmptyRows_{trade_day_str}_{ts}.csv"
    log_path = os.path.join(LOG_DIR, log_name)

    if "company_name" in empty_rows.columns:
        empty_rows["company_name"] = empty_rows["company_name"].astype(str).str.replace('"', "")

    empty_rows.to_csv(log_path, index=False)
    print(f"📝 Audit log saved: {log_path}")

# ============= CHANGE CALCULATION =============

def ensure_columns(df, required):
    for c in required:
        if c not in df.columns:
            df[c] = np.nan
    return df

def compute_oi_vol_change(trade_day):
    print(f"🔍 Computing open interest and volume changes for {trade_day.strftime('%Y-%m-%d')}...")
    trade_day_str = trade_day.strftime('%d%b%Y')
    prev_day = trade_day - timedelta(days=1)
    prev_day_str = None
    for _ in range(7):
        if prev_day.weekday() < 5:
            prev_day_str = prev_day.strftime('%d%b%Y')
            prev_file = os.path.join(DATA_DIR, f"Options_Strike_CallPut_{prev_day_str}.csv")
            if os.path.exists(prev_file):
                print(f"✅ Found previous day file: {prev_day_str}")
                break
        prev_day -= timedelta(days=1)
    else:
        print("❌ No previous trading file found for OI/volume change.")
        return None

    today_file = os.path.join(DATA_DIR, f"Options_Strike_CallPut_{trade_day_str}.csv")
    if not os.path.exists(today_file):
        print("❌ Cannot compute OI/volume change; today's file missing.")
        return None

    print("📖 Loading data for change computation ...")
    df_now = pd.read_csv(today_file)
    df_prev = pd.read_csv(prev_file)

    required_columns = [
        'ticker','company_name','asset_type','strike','expiry_date','trade_date',
        'openInt_Call','openInt_Put','vol_Call','vol_Put','lastPrice_Call','lastPrice_Put'
    ]
    df_now = ensure_columns(df_now, required_columns)
    df_prev = ensure_columns(df_prev, required_columns)

    df_now['expiry_date'] = df_now['expiry_date'].astype(str)
    df_prev['expiry_date'] = df_prev['expiry_date'].astype(str)
    df_now['strike'] = df_now['strike'].astype(float)
    df_prev['strike'] = df_prev['strike'].astype(float)

    key_cols = ['ticker', 'strike', 'expiry_date']
    merged = pd.merge(df_now, df_prev, on=key_cols, suffixes=('_now', '_prev'), how='inner')

    merged['change_OI_Call'] = merged['openInt_Call_now'] - merged['openInt_Call_prev']
    merged['change_OI_Put']  = merged['openInt_Put_now']  - merged['openInt_Put_prev']
    merged['change_vol_Call'] = merged['vol_Call_now'] - merged['vol_Call_prev']
    merged['change_vol_Put']  = merged['vol_Put_now']  - merged['vol_Put_prev']

    def pct_change(now, prev):
        return np.where(prev == 0, np.nan, (now - prev) / prev * 100)

    merged['pct_change_OI_Call'] = pct_change(merged['openInt_Call_now'], merged['openInt_Call_prev'])
    merged['pct_change_OI_Put']  = pct_change(merged['openInt_Put_now'],  merged['openInt_Put_prev'])
    merged['pct_change_vol_Call'] = pct_change(merged['vol_Call_now'], merged['vol_Call_prev'])
    merged['pct_change_vol_Put']  = pct_change(merged['vol_Put_now'],  merged['vol_Put_prev'])

    cols_out = [
        'ticker','company_name_now','asset_type_now','strike','expiry_date','trade_date_now',
        'openInt_Call_now','openInt_Call_prev','change_OI_Call','pct_change_OI_Call',
        'openInt_Put_now','openInt_Put_prev','change_OI_Put','pct_change_OI_Put',
        'vol_Call_now','vol_Call_prev','change_vol_Call','pct_change_vol_Call',
        'vol_Put_now','vol_Put_prev','change_vol_Put','pct_change_vol_Put',
        'lastPrice_Call_now','lastPrice_Put_now'
    ]
    merged = ensure_columns(merged, cols_out)

    if "company_name_now" in merged.columns:
        merged["company_name_now"] = merged["company_name_now"].astype(str).str.replace('"', "")

    out_file = os.path.join(DATA_DIR, f"Options_Strike_CallPut_Change_{trade_day_str}.csv")
    merged[cols_out].to_csv(out_file, index=False)
    print(f"✅ OI/Volume change file: {out_file}")
    print(f"📊 Change records saved: {len(merged)}")
    print("Sample percentage change record:\n", merged[cols_out].head(2).to_string(index=False))
    return out_file

# ============= CLEANUP =============

def cleanup_old_files(data_dir, days=90):
    print(f"🗑️  Cleaning up files older than {days} days...")
    cutoff = time.time() - days * 86400
    files_to_delete = []
    for fname in os.listdir(data_dir):
        fpath = os.path.join(data_dir, fname)
        if os.path.isfile(fpath) and os.path.getmtime(fpath) < cutoff:
            files_to_delete.append((fname, fpath))
    if not files_to_delete:
        print("   No old files found to delete")
        return
    for i, (fname, fpath) in enumerate(files_to_delete, 1):
        try:
            os.remove(fpath)
        except Exception:
            continue
        print_progress_bar(i, len(files_to_delete), prefix="🗑️  Cleanup")
    print(f"\n✅ Cleanup completed: {len(files_to_delete)} files deleted.")

# ============= MAIN (daily run) =============

if __name__ == "__main__":
    print("🚀 Starting Options Chain Data Collection Script")
    print(f"📁 Data directory: {DATA_DIR}")
    if not os.path.exists(DATA_DIR):
        print(f"📁 Creating data directory: {DATA_DIR}")
        os.makedirs(DATA_DIR)
    else:
        print("📁 Data directory exists")

    print("\n🧹 Phase 1: Cleanup old files")
    cleanup_old_files(DATA_DIR, 90)

    print("\n🏢 Phase 2: Universe & name map")
    company_name_map, all_tickers = prepare_universe_and_name_map()

    print("\n📅 Phase 3: Determine trading day")
    eod_day = get_eod_trading_day()

    print("\n📈 Phase 4: Collect options data")
    df, today_file = merge_calls_puts_per_strike_parallel(eod_day, company_name_map, all_tickers)

    trade_day_str = eod_day.strftime('%d%b%Y')

    if df is not None:
        print("\n📝 Phase 4b: Audit empty option rows")
        audit_empty_option_rows(df, trade_day_str)

        print("\n📊 Phase 5: Compute changes")
        compute_oi_vol_change(eod_day)
        print("\n🎉 Script completed successfully!")
    else:
        print("\n❌ Script ended - no data to process")
