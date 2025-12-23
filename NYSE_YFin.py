import os
import sys
import time
import json
import requests
import requests as py_requests  # for catching HTTPError in enrichment
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
import yfinance as yf
import pytz
from concurrent.futures import ThreadPoolExecutor, as_completed
from io import StringIO  # for pd.read_html on HTML string
import pandas_market_calendars as mcal  # NYSE calendar
from curl_cffi import requests as curl_requests  # curl_cffi session
import sqlite3

# ============= RUNTIME START =============
SCRIPT_START_TIME = time.time()

# ================== CONFIG ==================

DATA_DIR = r"C:\Users\srini\Options_chain_data"
UNIVERSE_FILE = os.path.join(DATA_DIR, "ticker_universe.xlsx")
UNIVERSE_SHEET_ACTIVE = "ticker_universe"   # tickers to process
UNIVERSE_SHEET_WHOLE  = "Whole_universe"    # full / history
LOG_DIR = DATA_DIR

# SQLite DB and tables
DB_PATH = os.path.join(DATA_DIR, "US_data.db")
TABLE_OPTIONS_RAW = "options_raw"
TABLE_OPTIONS = "options_daily"
TABLE_OPTIONS_CHANGE = "options_change"
TABLE_STOCK_DAILY = "stock_daily"
WEEK_TABLES = ["week1_options", "week2_options", "week3_options", "week4_options", "week5_options"]
MONTH_TABLES = ["month1_options", "month2_options"]

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

# ========= HELPER: dates, ticker normalization, progress bar ==========

def yf_ticker_fix(ticker):
    return ticker.replace('.', '-')

def current_load_date():
    # Global load timestamp for all tables, MM-DD-YYYY
    return datetime.now().strftime("%m-%d-%Y")

def print_progress_bar(current, total, bar_length=50, prefix="Progress"):
    percent = (current / total) * 100 if total > 0 else 0
    filled_length = int(bar_length * current // total) if total > 0 else 0
    bar = '█' * filled_length + '-' * (bar_length - filled_length)
    sys.stdout.write(f'\r{prefix}: |{bar}| {percent:.1f}% ({current}/{total})')
    sys.stdout.flush()
    if current == total:
        print()

# ========== UNIVERSE EXCEL HELPERS ==========

def _normalize_universe_df(df):
    if df is None or df.empty:
        return pd.DataFrame(columns=["ticker", "name", "category"])
    for col in ["ticker", "name", "category"]:
        if col not in df.columns:
            df[col] = ""
    df["ticker"] = df["ticker"].astype(str)
    df["name"] = df["name"].astype(str)
    df["category"] = df["category"].astype(str)
    return df[["ticker", "name", "category"]]

def load_universe_sheets(path):
    if not os.path.exists(path):
        active = pd.DataFrame(columns=["ticker", "name", "category"])
        whole  = pd.DataFrame(columns=["ticker", "name", "category"])
        return active, whole

    try:
        all_sheets = pd.read_excel(path, sheet_name=None, dtype=str, engine="openpyxl")
    except Exception:
        active = pd.DataFrame(columns=["ticker", "name", "category"])
        whole  = pd.DataFrame(columns=["ticker", "name", "category"])
        return active, whole

    active = _normalize_universe_df(all_sheets.get(UNIVERSE_SHEET_ACTIVE, None))
    whole  = _normalize_universe_df(all_sheets.get(UNIVERSE_SHEET_WHOLE,  None))
    return active, whole

def save_universe_sheets(active_df, whole_df, path):
    if not active_df.empty:
        active_df = active_df.sort_values("ticker")
    if not whole_df.empty:
        whole_df = whole_df.sort_values("ticker")

    existing_sheets = {}
    if os.path.exists(path):
        try:
            existing = pd.read_excel(path, sheet_name=None, engine="openpyxl")
            for k in list(existing.keys()):
                if k in (UNIVERSE_SHEET_ACTIVE, UNIVERSE_SHEET_WHOLE):
                    existing.pop(k, None)
            existing_sheets = existing
        except Exception:
            existing_sheets = {}

    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        for name, sdf in existing_sheets.items():
            sdf.to_excel(writer, sheet_name=name, index=False)
        active_df.to_excel(writer, sheet_name=UNIVERSE_SHEET_ACTIVE, index=False)
        whole_df.to_excel(writer,  sheet_name=UNIVERSE_SHEET_WHOLE,  index=False)

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
    print("📁 Loading existing universe sheets ...")
    active_df, whole_df = load_universe_sheets(UNIVERSE_FILE)

    whole_tickers = set(whole_df["ticker"])

    base_tickers = (
        [yf_ticker_fix(t) for t in INDEX_TICKERS] +
        [yf_ticker_fix(t) for t in METALS_TICKERS] +
        [yf_ticker_fix(t) for t in COMMODITY_TICKERS] +
        [yf_ticker_fix(t) for t in BOND_TICKERS] +
        [yf_ticker_fix(t) for t in CRYPTO_TICKERS] +
        [yf_ticker_fix(t) for t in EXTRA_STOCKS]
    )

    target_set_for_whole = set(base_tickers) | whole_tickers

    current_sp500_set = set(
        whole_df.loc[whole_df["category"] == CATEGORY_SP500, "ticker"]
    )

    new_tickers_for_whole = sorted(target_set_for_whole - whole_tickers)
    if new_tickers_for_whole:
        print(f"🆕 Found {len(new_tickers_for_whole)} new tickers to add to Whole_universe")

    sp500_wiki_map = {}
    try:
        if new_tickers_for_whole:
            sp500_wiki_map = get_sp500_name_map()
    except Exception as e:
        print("⚠️ Could not load S&P 500 Wikipedia table for enrichment:", e)
        sp500_wiki_map = {}

    new_rows_whole = []
    total = len(new_tickers_for_whole)
    for i, t in enumerate(new_tickers_for_whole, 1):
        name = yahoo_name_from_ticker(t)
        if not name and sp500_wiki_map:
            base = t.replace('-', '.')
            name = sp500_wiki_map.get(base)
        if not name:
            name = t
        cat = classify_category(t, current_sp500_set)
        new_rows_whole.append({"ticker": t, "name": name, "category": cat})
        print_progress_bar(i, total, prefix="🌍 Whole_universe (new)")

    if new_rows_whole:
        whole_df = pd.concat([whole_df, pd.DataFrame(new_rows_whole)], ignore_index=True)

    if not active_df.empty and not whole_df.empty:
        whole_map = whole_df.set_index("ticker")[["name", "category"]]
        active_df = active_df.set_index("ticker")
        active_df["name"] = active_df.index.to_series().map(whole_map["name"]).fillna(active_df["name"])
        active_df["category"] = active_df.index.to_series().map(whole_map["category"]).fillna(active_df["category"])
        active_df = active_df.reset_index()

    save_universe_sheets(active_df, whole_df, UNIVERSE_FILE)
    print(f"\n💾 Whole_universe tickers: {len(whole_df)}, active ticker_universe tickers: {len(active_df)}")

    name_map = dict(zip(active_df["ticker"], active_df["name"]))
    all_tickers = sorted(active_df["ticker"])
    print(f"🔎 Active tickers: {all_tickers}")
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

# ============= ENRICHMENT WITH OHLC (THROTTLED) =============

def enrich_with_option_ohlc_parallel(df: pd.DataFrame,
                                     call_symbol_col="contractSymbol_Call",
                                     put_symbol_col="contractSymbol_Put",
                                     max_workers=1,
                                     max_retries=1) -> pd.DataFrame:
    call_syms = df[call_symbol_col].dropna().astype(str).unique() if call_symbol_col in df.columns else []
    put_syms  = df[put_symbol_col].dropna().astype(str).unique() if put_symbol_col in df.columns else []
    all_syms = np.unique(np.concatenate([call_syms, put_syms])) if len(call_syms) + len(put_syms) > 0 else []
    print(f"🔁 Enriching {len(all_syms)} unique option contracts with OHLC snapshot...")

    if len(all_syms) == 0:
        return df

    session = curl_requests.Session(impersonate="chrome")

    def fetch_info(sym):
        for attempt in range(max_retries + 1):
            try:
                tk = yf.Ticker(sym, session=session)
                info = tk.info
                time.sleep(0.2)  # 200 ms pause per contract
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
            except py_requests.exceptions.HTTPError as e:
                msg = str(e)
                if "Too Many Requests" in msg or "rate limit" in msg:
                    print(f"\n⚠️ Rate limited on {sym}, skipping OHLC for this contract")
                    return sym, None
                if attempt >= max_retries:
                    print(f"\n⚠️ HTTP error on {sym} after retries, skipping: {e}")
                    return sym, None
                continue
            except Exception as e:
                msg = str(e)
                if "Too Many Requests" in msg or "rate limit" in msg:
                    print(f"\n⚠️ Rate limited on {sym}, skipping OHLC")
                else:
                    print(f"\n⚠️ Error in OHLC for {sym}: {e}")
                return sym, None

    info_map = {}
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

# ============= DB HELPERS: WEEKLY & MONTHLY TABLES =============

def refresh_weekly_tables(conn):
    """
    Build rolling week1_options .. week5_options from options_daily.
    week1 = nearest future expiry, week2 = next, ... up to 5.
    Each row stamped with load_date (MM-DD-YYYY).
    """
    print("🛠 Refreshing weekly tables from options_daily...")
    q = """
    SELECT DISTINCT expiry_date
    FROM options_daily
    ORDER BY expiry_date
    """
    expiries_df = pd.read_sql(q, conn)
    expiries = expiries_df["expiry_date"].tolist()

    if not expiries:
        print("⚠️ No expiries in options_daily; weekly tables not updated.")
        for tbl in WEEK_TABLES:
            conn.execute(f"DROP TABLE IF EXISTS {tbl}")
        conn.commit()
        return

    load_date = current_load_date()

    for i, exp in enumerate(expiries[:len(WEEK_TABLES)]):
        table_name = WEEK_TABLES[i]
        print(f"📆 Refreshing {table_name} for expiry {exp}")
        df_week = pd.read_sql(
            "SELECT * FROM options_daily WHERE expiry_date = ?",
            conn,
            params=(exp,)
        )
        df_week["load_date"] = load_date
        df_week.to_sql(table_name, conn, if_exists="replace", index=False)

    for j in range(len(expiries), len(WEEK_TABLES)):
        table_name = WEEK_TABLES[j]
        print(f"🗑 Dropping stale weekly table {table_name} (no matching expiry)")
        conn.execute(f"DROP TABLE IF EXISTS {table_name}")
        conn.commit()

def refresh_monthly_tables(conn):
    """
    Build rolling month1_options and month2_options from options_daily.
    month1 = nearest future monthly expiry (per calendar month),
    month2 = next month's expiry.
    Each row stamped with load_date.
    """
    print("🛠 Refreshing monthly tables from options_daily...")
    q = """
    WITH monthly_min AS (
        SELECT
            strftime('%Y', expiry_date) AS y,
            strftime('%m', expiry_date) AS m,
            MIN(expiry_date) AS month_expiry
        FROM options_daily
        GROUP BY y, m
    )
    SELECT month_expiry
    FROM monthly_min
    ORDER BY month_expiry
    """
    expiries_df = pd.read_sql(q, conn)
    expiries = expiries_df["month_expiry"].tolist()

    if not expiries:
        print("⚠️ No monthly expiries found; monthly tables not updated.")
        for tbl in MONTH_TABLES:
            conn.execute(f"DROP TABLE IF EXISTS {tbl}")
        conn.commit()
        return

    load_date = current_load_date()

    for i, exp in enumerate(expiries[:len(MONTH_TABLES)]):
        table_name = MONTH_TABLES[i]
        print(f"📆 Refreshing {table_name} for monthly expiry {exp}")
        df_month = pd.read_sql(
            "SELECT * FROM options_daily WHERE expiry_date = ?",
            conn,
            params=(exp,)
        )
        df_month["load_date"] = load_date
        df_month.to_sql(table_name, conn, if_exists="replace", index=False)

    for j in range(len(expiries), len(MONTH_TABLES)):
        table_name = MONTH_TABLES[j]
        print(f"🗑 Dropping stale monthly table {table_name} (no matching expiry)")
        conn.execute(f"DROP TABLE IF EXISTS {table_name}")
        conn.commit()

# ============= OPTIONS FETCH (YAHOO, ±10 STRIKES, NEXT 45 DAYS) =============

def fetch_option_chain(ticker, company_name, asset_type, trade_day_str):
    session = curl_requests.Session(impersonate="chrome")
    tk = yf.Ticker(ticker, session=session)
    results = []
    try:
        spot = None
        try:
            hist = tk.history(period="1d")
            if not hist.empty and "Close" in hist.columns:
                spot = hist["Close"].iloc[-1]
        except Exception:
            pass

        max_horizon_days = 45

        if hasattr(tk, 'options') and tk.options:
            for exp in tk.options:
                try:
                    exp_dt = datetime.strptime(exp, "%Y-%m-%d").date()
                except Exception:
                    continue
                trade_dt = datetime.strptime(trade_day_str, "%d%b%Y").date()
                if exp_dt > trade_dt + timedelta(days=max_horizon_days):
                    continue

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

                if spot is not None and (not calls.empty or not puts.empty):
                    strikes_series = pd.concat([
                        calls['strike'] if 'strike' in calls.columns else pd.Series([], dtype=float),
                        puts['strike'] if 'strike' in puts.columns else pd.Series([], dtype=float)
                    ])
                    all_strikes = strikes_series.dropna().unique()
                    all_strikes = np.sort(all_strikes)
                    if len(all_strikes) > 0:
                        nearest_idx = (np.abs(all_strikes - spot)).argmin()
                        low_idx = max(nearest_idx - 10, 0)
                        high_idx = min(nearest_idx + 10, len(all_strikes) - 1)
                        keep_strikes = set(all_strikes[low_idx:high_idx + 1])
                        if not calls.empty:
                            calls = calls[calls['strike'].isin(keep_strikes)].copy()
                        if not puts.empty:
                            puts  = puts[puts['strike'].isin(keep_strikes)].copy()

                if calls.empty and puts.empty:
                    continue

                calls['expiry_date'] = exp
                puts['expiry_date'] = exp

                merged = pd.merge(calls, puts, on=['strike', 'expiry_date'], how='outer')
                merged['ticker'] = ticker
                merged['asset_type'] = asset_type
                merged['company_name'] = company_name
                merged['trade_date'] = trade_day_str
                results.append(merged)
    except Exception as e:
        msg = str(e)
        if "Too Many Requests" in msg or "rate limit" in msg or "Too many requests" in msg:
            print(f"⚠️ Rate limited while fetching options for {ticker}, skipping this ticker for now")
            return []
        print(f"⚠️ Error for {ticker}: {e}")
        return []

    if not results:
        print(f"ℹ No options data returned for {ticker}")
    else:
        print(f"✅ Options fetched for {ticker}: {sum(len(r) for r in results)} rows")
    return results

# ============= MERGE CALLS/PUTS: ONE TICKER AT A TIME =============

def merge_calls_puts_per_strike_parallel(trade_day, company_name_map, all_tickers):
    SECS_BETWEEN_TICKERS = 1  # adjust 20–60 as needed

    print(f"🔄 Starting options chain collection (Yahoo, 1 ticker at a time) for {trade_day.strftime('%Y-%m-%d')}")
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

    total = len(all_tickers)
    all_rows = []

    for i, ticker in enumerate(all_tickers, 1):
        company_name = company_name_map.get(ticker, ticker)
        asset_type = infer_asset_type(ticker)

        print(f"\n▶ [{i}/{total}] Fetching options for {ticker} ({company_name})")
        try:
            rows_list = fetch_option_chain(ticker, company_name, asset_type, trade_day_str)
        except Exception as e:
            print(f"⚠️ Error fetching {ticker}: {e}")
            rows_list = []

        if rows_list:
            all_rows += rows_list
            print(f"✅ {ticker}: added {sum(len(r) for r in rows_list)} rows")
        else:
            print(f"ℹ {ticker}: no data (no options or skipped)")

        print_progress_bar(i, total, prefix="📈 Options Data (Yahoo)")

        if i < total:
            print(f"⏸ Waiting {SECS_BETWEEN_TICKERS} seconds before next ticker...")
            time.sleep(SECS_BETWEEN_TICKERS)

    print(f"\n📊 All tickers processed. Saving file ...")
    if not all_rows:
        print("❌ No call/put pairs to merge.")
        return None, None

    df_final = pd.concat(all_rows, ignore_index=True)

    before = len(df_final)
    df_final = df_final.drop_duplicates()
    after = len(df_final)
    print(f"🧹 Removed {before - after} exact duplicate rows")

    print("📈 Enriching options data with per‑contract OHLC via yfinance.info ...")
    df_final = enrich_with_option_ohlc_parallel(df_final)

    cols = list(df_final.columns)
    first_cols = [c for c in ["ticker", "asset_type", "company_name"] if c in cols]
    last_cols = [c for c in ["contractSymbol_Call", "contractSymbol_Put"] if c in cols]
    middle_cols = [c for c in cols if c not in first_cols + last_cols]
    new_order = first_cols + middle_cols + last_cols
    df_final = df_final[new_order]

    trade_day_str = trade_day.strftime('%d%b%Y')
    out_file = os.path.join(DATA_DIR, f"Options_Strike_CallPut_{trade_day_str}.csv")
    df_final.to_csv(out_file, index=False)
    print(f"✅ Output file saved: {out_file}")
    print(f"📈 Total records: {len(df_final)}")

    # Write into SQLite with duplicate-safe logic
    load_date = current_load_date()
    df_raw = df_final.copy()
    df_raw["load_date"] = load_date

    df_daily = df_final.copy()
    df_daily["load_date"] = load_date

    conn = sqlite3.connect(DB_PATH)
    print("🛢 Writing to DB:", DB_PATH)

    # delete existing rows for this trade_date (safe rerun)
    if not df_daily.empty and "trade_date" in df_daily.columns:
        trade_date_db = df_daily["trade_date"].iloc[0]
        try:
            conn.execute(f"DELETE FROM {TABLE_OPTIONS_RAW} WHERE trade_date = ?", (trade_date_db,))
        except Exception:
            pass
        try:
            conn.execute(f"DELETE FROM {TABLE_OPTIONS} WHERE trade_date = ?", (trade_date_db,))
        except Exception:
            pass
        conn.commit()

    df_raw.to_sql(TABLE_OPTIONS_RAW, conn, if_exists="append", index=False)
    print(f"✅ Appended {len(df_raw)} rows to {TABLE_OPTIONS_RAW}")
    df_daily.to_sql(TABLE_OPTIONS, conn, if_exists="append", index=False)
    print(f"✅ Appended {len(df_daily)} rows to {TABLE_OPTIONS}")

    # Refresh weekly/monthly from options_daily
    refresh_weekly_tables(conn)
    refresh_monthly_tables(conn)

    conn.close()
    print(f"✅ DB write and weekly/monthly refresh completed for {trade_day_str}")

    print("Sample record for today:\n", df_final.head(1).to_string(index=False))
    return df_final, out_file

# ============= AUDIT BLANK / EMPTY ROWS =============

def audit_empty_option_rows(df: pd.DataFrame, trade_day_str: str):
    if df is None or df.empty:
        print("🔍 Audit: DataFrame is empty, nothing to audit.")
        return

    oi_call = df.get("openInt_Call", pd.Series(index=df.index, data=np.nan)).fillna(0)
    oi_put  = df.get("openInt_Put",  pd.Series(index=df.index, data=np.nan)).fillna(0)
    vol_call = df.get("vol_Call",   pd.Series(index=df.index, data=np.nan)).fillna(0)
    vol_put  = df.get("vol_Put",    pd.Series(index=df.index, data=np.nan)).fillna(0)

    mask_empty = (oi_call == 0) & (oi_put == 0) & (vol_call == 0) & (vol_put == 0)
    empty_rows = df.loc[mask_empty, ["ticker", "asset_type", "company_name",
                                     "strike", "expiry_date", "trade_date"]].copy()

    total_empty = len(empty_rows)
    total_rows = len(df)
    print(f"\n🔍 Audit: Found {total_empty} rows with no call/put OI and volume "
          f"out of {total_rows} total rows.")

    if empty_rows.empty:
        return

    ts = datetime.now().strftime("%Y%m%d_%H%M")
    log_name = f"Options_Audit_EmptyRows_{trade_day_str}_{ts}.csv"
    log_path = os.path.join(LOG_DIR, log_name)

    empty_rows.to_csv(log_path, index=False)
    print(f"📝 Simple audit log saved: {log_path}")

# ============= CHANGE CALCULATION (UPDATED) =============

def ensure_columns(df, required):
    for c in required:
        if c not in df.columns:
            df[c] = np.nan
    return df

def compute_oi_vol_change(trade_day):
    print(f"🔍 Computing open interest and volume changes for {trade_day.strftime('%Y-%m-%d')}...")
    trade_day_str = trade_day.strftime('%d%b%Y')

    # ---------- find previous trading day file ----------
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

    # ---------- ensure base columns ----------
    required_columns = [
        'ticker','company_name','asset_type','strike','expiry_date','trade_date',
        'openInt_Call','openInt_Put','vol_Call','vol_Put',
        'lastPrice_Call','lastPrice_Put'
    ]
    df_now = ensure_columns(df_now, required_columns)
    df_prev = ensure_columns(df_prev, required_columns)

    # ---------- ensure OHLC ----------
    ohlc_cols = [
        "call_open","call_high","call_low","call_close",
        "put_open","put_high","put_low","put_close"
    ]
    df_now = ensure_columns(df_now, ohlc_cols)
    df_prev = ensure_columns(df_prev, ohlc_cols)

    df_now['expiry_date'] = df_now['expiry_date'].astype(str)
    df_prev['expiry_date'] = df_prev['expiry_date'].astype(str)
    df_now['strike'] = df_now['strike'].astype(float)
    df_prev['strike'] = df_prev['strike'].astype(float)

    key_cols = ['ticker', 'strike', 'expiry_date']
    merged = pd.merge(
        df_now,
        df_prev,
        on=key_cols,
        suffixes=('_now', '_prev'),
        how='inner'
    )

    # ---------- OI / volume changes ----------
    for c in [
        'openInt_Call_now','openInt_Call_prev',
        'openInt_Put_now','openInt_Put_prev',
        'vol_Call_now','vol_Call_prev',
        'vol_Put_now','vol_Put_prev'
    ]:
        if c in merged.columns:
            merged[c] = merged[c].fillna(0)

    merged['change_OI_Call']  = merged['openInt_Call_now'] - merged['openInt_Call_prev']
    merged['change_OI_Put']   = merged['openInt_Put_now']  - merged['openInt_Put_prev']
    merged['change_vol_Call'] = merged['vol_Call_now']     - merged['vol_Call_prev']
    merged['change_vol_Put']  = merged['vol_Put_now']      - merged['vol_Put_prev']

    def pct_change(now, prev):
        return np.where(prev == 0, np.nan, (now - prev) / prev * 100)

    merged['pct_change_OI_Call']  = pct_change(merged['openInt_Call_now'], merged['openInt_Call_prev'])
    merged['pct_change_OI_Put']   = pct_change(merged['openInt_Put_now'],  merged['openInt_Put_prev'])
    merged['pct_change_vol_Call'] = pct_change(merged['vol_Call_now'],     merged['vol_Call_prev'])
    merged['pct_change_vol_Put']  = pct_change(merged['vol_Put_now'],      merged['vol_Put_prev'])

    # ---------- levels and today's OHLC ----------
    merged["lastPrice_Call_now"] = merged["lastPrice_Call_now"].fillna(0)
    merged["lastPrice_Put_now"]  = merged["lastPrice_Put_now"].fillna(0)

    # if *_now highs missing, fall back to plain highs
    merged["call_high_now"] = merged.get("call_high_now", merged.get("call_high")).fillna(0)
    merged["put_high_now"]  = merged.get("put_high_now",  merged.get("put_high")).fillna(0)

    merged["R1"]  = merged["strike"] + merged["lastPrice_Call_now"]
    merged["S1"]  = merged["strike"] - merged["lastPrice_Put_now"]
    merged["R12"] = merged["strike"] + merged["call_high_now"]
    merged["S12"] = merged["strike"] - merged["put_high_now"]

    merged["call_open_now"]  = merged.get("call_open_now",  merged.get("call_open"))
    merged["call_low_now"]   = merged.get("call_low_now",   merged.get("call_low"))
    merged["call_close_now"] = merged.get("call_close_now", merged.get("call_close"))
    merged["put_open_now"]   = merged.get("put_open_now",   merged.get("put_open"))
    merged["put_low_now"]    = merged.get("put_low_now",    merged.get("put_low"))
    merged["put_close_now"]  = merged.get("put_close_now",  merged.get("put_close"))

    # ---------- output columns ----------
    cols_out = [
        'ticker','company_name_now','asset_type_now','strike','expiry_date','trade_date_now',
        'openInt_Call_now','openInt_Call_prev','change_OI_Call','pct_change_OI_Call',
        'openInt_Put_now','openInt_Put_prev','change_OI_Put','pct_change_OI_Put',
        'vol_Call_now','vol_Call_prev','change_vol_Call','pct_change_vol_Call',
        'vol_Put_now','vol_Put_prev','change_vol_Put','pct_change_vol_Put',
        'lastPrice_Call_now','lastPrice_Put_now',
        'call_open_now','call_high_now','call_low_now','call_close_now',
        'put_open_now','put_high_now','put_low_now','put_close_now',
        'R1','S1','R12','S12'
    ]
    merged = ensure_columns(merged, cols_out)

    if "company_name_now" in merged.columns:
        merged["company_name_now"] = merged["company_name_now"].astype(str).str.replace('"', "")

    out_file = os.path.join(DATA_DIR, f"Options_Strike_CallPut_Change_{trade_day_str}.csv")
    load_date = current_load_date()
    df_out = merged[cols_out].copy()
    df_out["load_date"] = load_date

    df_out.to_csv(out_file, index=False)
    print(f"✅ OI/Volume change file: {out_file}")
    print(f"📊 Change records saved: {len(df_out)}")

    conn = sqlite3.connect(DB_PATH)

    # delete existing change rows for this trade_date_now (safe rerun)
    if not df_out.empty and "trade_date_now" in df_out.columns:
        trade_date_now = df_out["trade_date_now"].iloc[0]
        try:
            conn.execute(f"DELETE FROM {TABLE_OPTIONS_CHANGE} WHERE trade_date_now = ?", (trade_date_now,))
            conn.commit()
        except Exception:
            pass

    df_out.to_sql(TABLE_OPTIONS_CHANGE, conn, if_exists="append", index=False)
    conn.close()
    print(f"✅ Appended {len(df_out)} change rows into {TABLE_OPTIONS_CHANGE}")

    print("Sample percentage change record:\n", df_out.head(2).to_string(index=False))
    return out_file

# ============= STOCK_DAILY (OHLC + PCR) =============

def build_stock_daily(trade_day, all_tickers):
    """
    For each ticker and trade_date, store OHLC, volume from Yahoo,
    and basic PCR (using options_daily), plus load_date stamp.
    Dates stored as MM-DD-YYYY strings.
    """
    trade_day_str_db = trade_day.strftime("%m-%d-%Y")
    print(f"📊 Building stock_daily for {trade_day_str_db}...")

    session = curl_requests.Session(impersonate="chrome")
    records = []

    for ticker in all_tickers:
        try:
            trade_day_iso = trade_day.strftime("%Y-%m-%d")
            tk = yf.Ticker(ticker, session=session)
            hist = tk.history(start=trade_day_iso, end=trade_day_iso, interval="1d")
            if hist.empty:
                hist = tk.history(period="1d")
            if hist.empty:
                print(f"ℹ stock_daily: no price data for {ticker}")
                continue

            row = hist.iloc[-1]
            o = float(row.get("Open", np.nan))
            h = float(row.get("High", np.nan))
            l = float(row.get("Low", np.nan))
            c = float(row.get("Close", np.nan))
            v = float(row.get("Volume", np.nan))

            conn = sqlite3.connect(DB_PATH)
            df_opt = pd.read_sql(
                """
                SELECT openInt_Call, openInt_Put
                FROM options_daily
                WHERE ticker = ? AND trade_date = ?
                """,
                conn,
                params=(ticker, trade_day.strftime("%d%b%Y")),
            )
            conn.close()

            total_call_oi = df_opt["openInt_Call"].fillna(0).sum() if not df_opt.empty else 0
            total_put_oi  = df_opt["openInt_Put"].fillna(0).sum()  if not df_opt.empty else 0
            pcr_oi = total_put_oi / total_call_oi if total_call_oi > 0 else np.nan

            load_date_str = current_load_date()

            records.append({
                "ticker": ticker,
                "trade_date": trade_day_str_db,
                "open": o,
                "high": h,
                "low": l,
                "close": c,
                "volume": v,
                "pcr_oi": pcr_oi,
                "load_date": load_date_str,
            })
        except Exception as e:
            print(f"⚠️ stock_daily: error for {ticker}: {e}")
            continue

    if not records:
        print("⚠️ stock_daily: no records created")
        return None

    df_stock = pd.DataFrame(records)

    conn = sqlite3.connect(DB_PATH)

    # delete existing rows for this trade_date (safe rerun)
    try:
        conn.execute(f"DELETE FROM {TABLE_STOCK_DAILY} WHERE trade_date = ?", (trade_day_str_db,))
        conn.commit()
    except Exception:
        pass

    df_stock.to_sql(TABLE_STOCK_DAILY, conn, if_exists="append", index=False)
    conn.close()
    print(f"✅ stock_daily: appended {len(df_stock)} rows into {TABLE_STOCK_DAILY}")

    return df_stock

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
    print("🚀 Starting Options Chain Data Collection Script (Yahoo, single-ticker mode)")
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

    print("\n📈 Phase 4: Collect options data (Yahoo)")
    df, today_file = merge_calls_puts_per_strike_parallel(eod_day, company_name_map, all_tickers)

    trade_day_str = eod_day.strftime('%d%b%Y')

    if df is not None:
        print("\n📝 Phase 4b: Audit empty option rows")
        audit_empty_option_rows(df, trade_day_str)

        print("\n📊 Phase 5: Compute changes")
        compute_oi_vol_change(eod_day)

        print("\n📊 Phase 6: Build stock_daily")
        build_stock_daily(eod_day, all_tickers)

        print("\n🎉 Script completed successfully!")
    else:
        print("\n❌ Script ended - no data to process")

    elapsed = time.time() - SCRIPT_START_TIME
    print(f"\n⏱ Total runtime: {elapsed:.1f} seconds")
