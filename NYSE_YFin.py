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
from concurrent.futures import ThreadPoolExecutor
from io import StringIO  # for pd.read_html on HTML string
import pandas_market_calendars as mcal  # NYSE calendar
from curl_cffi import requests as curl_requests  # curl_cffi session
import sqlite3


# ============= RUNTIME START =============
SCRIPT_START_TIME = time.time()


# ================== CONFIG ==================
DATA_DIR = r"C:\Users\srini\Options_chain_data"

US_CHARTS_DIR = os.path.join(DATA_DIR, "US_CHARTS")
ARCHIVE_DIR = os.path.join(US_CHARTS_DIR, "archive")
os.makedirs(US_CHARTS_DIR, exist_ok=True)
os.makedirs(ARCHIVE_DIR, exist_ok=True)

UNIVERSE_FILE = os.path.join(US_CHARTS_DIR, "ticker_universe.xlsx")
UNIVERSE_SHEET_ACTIVE = "ticker_universe"
UNIVERSE_SHEET_WHOLE = "Whole_universe"
LOG_DIR = DATA_DIR

DB_PATH = os.path.join(DATA_DIR, "US_data.db")
TABLE_OPTIONS_RAW = "options_raw"
TABLE_OPTIONS = "options_daily"
TABLE_OPTIONS_CHANGE = "options_change"
TABLE_STOCK_DAILY = "stock_daily"
WEEK_TABLES = ["week1_options", "week2_options", "week3_options", "week4_options", "week5_options"]
MONTH_TABLES = ["month1_options", "month2_options"]

CATEGORY_SP500 = "sp500"
CATEGORY_NON_SP500 = "non_s&p"
CATEGORY_INDEX = "index"
CATEGORY_METAL = "metal"
CATEGORY_COMMODITY = "commodity"
CATEGORY_BOND = "bond"
CATEGORY_CRYPTO = "crypto"
CATEGORY_OTHER = "other"

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
    return datetime.now().strftime("%Y-%m-%d")


def print_progress_bar(current, total, bar_length=50, prefix="Progress"):
    percent = (current / total) * 100 if total > 0 else 0
    filled_length = int(bar_length * current // total) if total > 0 else 0
    # ASCII-only bar
    bar = "#" * filled_length + "-" * (bar_length - filled_length)
    sys.stdout.write(f"\r{prefix}: |{bar}| {percent:.1f}% ({current}/{total})")
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
        whole = pd.DataFrame(columns=["ticker", "name", "category"])
        return active, whole

    try:
        all_sheets = pd.read_excel(path, sheet_name=None, dtype=str, engine="openpyxl")
    except Exception:
        active = pd.DataFrame(columns=["ticker", "name", "category"])
        whole = pd.DataFrame(columns=["ticker", "name", "category"])
        return active, whole

    active = _normalize_universe_df(all_sheets.get(UNIVERSE_SHEET_ACTIVE, None))
    whole = _normalize_universe_df(all_sheets.get(UNIVERSE_SHEET_WHOLE, None))
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
        whole_df.to_excel(writer, sheet_name=UNIVERSE_SHEET_WHOLE, index=False)


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
    print("Loading existing universe sheets ...")
    active_df, whole_df = load_universe_sheets(UNIVERSE_FILE)

    whole_tickers = set(whole_df["ticker"])

    base_tickers = (
        [yf_ticker_fix(t) for t in INDEX_TICKERS]
        + [yf_ticker_fix(t) for t in METALS_TICKERS]
        + [yf_ticker_fix(t) for t in COMMODITY_TICKERS]
        + [yf_ticker_fix(t) for t in BOND_TICKERS]
        + [yf_ticker_fix(t) for t in CRYPTO_TICKERS]
        + [yf_ticker_fix(t) for t in EXTRA_STOCKS]
    )

    target_set_for_whole = set(base_tickers) | whole_tickers

    current_sp500_set = set(
        whole_df.loc[whole_df["category"] == CATEGORY_SP500, "ticker"]
    )

    new_tickers_for_whole = sorted(target_set_for_whole - whole_tickers)
    if new_tickers_for_whole:
        print(f"Found {len(new_tickers_for_whole)} new tickers to add to Whole_universe")

    sp500_wiki_map = {}
    try:
        if new_tickers_for_whole:
            sp500_wiki_map = get_sp500_name_map()
    except Exception as e:
        print("Could not load S&P 500 Wikipedia table for enrichment:", e)
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
        print_progress_bar(i, total, prefix="Whole_universe (new)")

    if new_rows_whole:
        whole_df = pd.concat([whole_df, pd.DataFrame(new_rows_whole)], ignore_index=True)

    if not active_df.empty and not whole_df.empty:
        whole_map = whole_df.set_index("ticker")[["name", "category"]]
        active_df = active_df.set_index("ticker")
        active_df["name"] = active_df.index.to_series().map(whole_map["name"]).fillna(active_df["name"])
        active_df["category"] = active_df.index.to_series().map(whole_map["category"]).fillna(active_df["category"])
        active_df = active_df.reset_index()

    save_universe_sheets(active_df, whole_df, UNIVERSE_FILE)
    print(f"\nWhole_universe tickers: {len(whole_df)}, active ticker_universe tickers: {len(active_df)}")

    name_map = dict(zip(active_df["ticker"], active_df["name"]))
    all_tickers = sorted(active_df["ticker"])
    print(f"Active tickers: {all_tickers}")
    return name_map, all_tickers


# ============= TRADING DAY USING NYSE CALENDAR =============
def get_eod_trading_day(max_back=10):
    print("Determining end-of-day trading date using NYSE calendar ...")
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

    print(f"Using NYSE trading day: {use_day.isoformat()}")
    return eastern.localize(datetime(use_day.year, use_day.month, use_day.day))


# ============= ENRICHMENT WITH OHLC (THROTTLED + SMART ATM FILTER) =============
# Config: tune these to balance coverage vs speed
OHLC_ATM_PCT      = 0.20   # only enrich contracts within +-20% of spot price
OHLC_BASE_SLEEP   = 0.35   # base seconds between calls (increased from 0.2)
OHLC_MAX_RETRIES  = 2      # retries per contract
OHLC_BACKOFF_BASE = 2.0    # exponential backoff base (seconds * 2^attempt)
OHLC_CIRCUIT_N    = 6      # consecutive rate-limit hits before circuit break
OHLC_CIRCUIT_WAIT = 45     # seconds to sleep when circuit breaks
OHLC_CHECKPOINT   = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ohlc_checkpoint.json")

def enrich_with_option_ohlc_parallel(df: pd.DataFrame,
                                     call_symbol_col="contractSymbol_Call",
                                     put_symbol_col="contractSymbol_Put",
                                     max_retries=OHLC_MAX_RETRIES,
                                     trade_day=None) -> pd.DataFrame:
    # --- Build ATM-filtered symbol set ---
    # Parse spot price from the contract symbols where possible,
    # but use the dataframe's strike column as a proxy for ATM filtering.
    call_syms = df[call_symbol_col].dropna().astype(str).unique() if call_symbol_col in df.columns else []
    put_syms  = df[put_symbol_col].dropna().astype(str).unique()  if put_symbol_col  in df.columns else []
    all_syms_full = list(np.unique(np.concatenate([call_syms, put_syms]))) if len(call_syms) + len(put_syms) > 0 else []

    if not all_syms_full:
        return df

    # ATM filter: decode strike from OCC symbol (format: ROOT + YYMMDD + C/P + 8-digit-strike)
    # e.g. CRWD260529C00500000 -> strike = 500.000
    def _occ_strike(sym):
        try:
            # last 8 chars before the option type digit block = strike * 1000
            # OCC: root(variable) + 6-digit date + C/P + 8-digit strike (*1000)
            cp_idx = max(sym.rfind("C"), sym.rfind("P"))
            return int(sym[cp_idx + 1:]) / 1000.0
        except Exception:
            return None

    # Get approximate current spot per ticker from df
    spot_by_ticker = {}
    if "ticker" in df.columns and "strike" in df.columns:
        df["strike"] = pd.to_numeric(df["strike"], errors="coerce")
        for tk, grp in df.groupby("ticker"):
            spot_by_ticker[tk] = grp["strike"].median()  # median strike ~= ATM proxy

    def _is_atm(sym):
        """True if symbol is within ATM_PCT of spot (or no spot available)."""
        k = _occ_strike(sym)
        if k is None:
            return True  # can't parse - include it
        # find ticker prefix (everything before the 6-digit date)
        for tk, spot in spot_by_ticker.items():
            # match by prefix: sym starts with ticker (allow hyphens removed)
            tk_clean = tk.replace("-", "")
            if sym.upper().startswith(tk_clean.upper()):
                return abs(k - spot) / spot <= OHLC_ATM_PCT if spot > 0 else True
        return True  # no ticker match - include

    all_syms = [s for s in all_syms_full if _is_atm(s)]
    skipped_otm = len(all_syms_full) - len(all_syms)
    print(f"\nOHLC enrichment: {len(all_syms_full)} contracts total -> "
          f"{len(all_syms)} within +/-{OHLC_ATM_PCT*100:.0f}% of spot "
          f"({skipped_otm} deep-OTM skipped, not needed for signals)")

    if not all_syms:
        return df

    # Load checkpoint: skip already-fetched symbols
    # Use trade_day (the market date being processed) not wall-clock date,
    # so overnight restarts before next market open still resume correctly.
    today_str = trade_day.strftime("%Y-%m-%d") if trade_day else datetime.now().strftime("%Y-%m-%d")
    info_map = {}
    if os.path.exists(OHLC_CHECKPOINT):
        try:
            with open(OHLC_CHECKPOINT, "r") as f:
                ckpt = json.load(f)
            checkpoint_date = ckpt.get("date")
            if checkpoint_date == today_str:
                info_map = ckpt.get("data", {})
                resumed = sum(1 for s in all_syms if s in info_map)
                print(f"  Resuming OHLC from checkpoint: {resumed}/{len(all_syms)} already done")
            else:
                print(f"  Checkpoint is from {checkpoint_date}, starting fresh for {today_str}")
        except Exception as e:
            print(f"  Could not load OHLC checkpoint: {e}")

    all_syms_todo = [s for s in all_syms if s not in info_map]

    session = curl_requests.Session(impersonate="chrome")
    consecutive_rl  = 0
    adaptive_sleep  = OHLC_BASE_SLEEP

    def fetch_info(sym):
        for attempt in range(OHLC_MAX_RETRIES + 1):
            try:
                tk   = yf.Ticker(sym, session=session)
                info = tk.info
                return sym, {
                    "open":         info.get("regularMarketOpen"),
                    "high":         info.get("regularMarketDayHigh"),
                    "low":          info.get("regularMarketDayLow"),
                    "close":        info.get("regularMarketPrice"),
                    "bid":          info.get("bid"),
                    "ask":          info.get("ask"),
                    "volume":       info.get("regularMarketVolume"),
                    "openInterest": info.get("openInterest"),
                }
            except Exception as e:
                msg = str(e)
                is_rl = "Too Many Requests" in msg or "429" in msg or "rate limit" in msg.lower()
                if is_rl:
                    wait = OHLC_BACKOFF_BASE ** (attempt + 1)
                    time.sleep(wait)
                    if attempt >= OHLC_MAX_RETRIES:
                        return sym, "RATE_LIMITED"
                    continue
                return sym, None
        return sym, None

    total = len(all_syms)
    rl_count_total = 0
    already_done = total - len(all_syms_todo)

    def _save_checkpoint():
        try:
            with open(OHLC_CHECKPOINT, "w") as f:
                json.dump({"date": today_str, "data": info_map}, f)
        except Exception:
            pass

    for i, sym in enumerate(all_syms_todo, already_done + 1):
        _, data = fetch_info(sym)

        if data == "RATE_LIMITED":
            consecutive_rl += 1
            rl_count_total += 1
            info_map[sym] = None
            if consecutive_rl >= OHLC_CIRCUIT_N:
                print(f"\n  [{i}/{total}] Circuit breaker: {consecutive_rl} consecutive rate limits. "
                      f"Sleeping {OHLC_CIRCUIT_WAIT}s ...")
                time.sleep(OHLC_CIRCUIT_WAIT)
                adaptive_sleep = min(adaptive_sleep * 1.5, 3.0)
                consecutive_rl = 0
            else:
                time.sleep(OHLC_BACKOFF_BASE * consecutive_rl)
        else:
            info_map[sym] = data
            if consecutive_rl > 0:
                consecutive_rl = max(0, consecutive_rl - 1)
                adaptive_sleep = max(OHLC_BASE_SLEEP, adaptive_sleep * 0.9)

        time.sleep(adaptive_sleep)
        print_progress_bar(i, total, prefix="OHLC Snapshots")

        # Save checkpoint every 50 symbols
        if i % 50 == 0:
            _save_checkpoint()

    _save_checkpoint()
    fetched_now = len(all_syms_todo) - rl_count_total
    print(f"\n  OHLC complete: {already_done} from checkpoint + {fetched_now} fetched, "
          f"{rl_count_total} rate-limited ({skipped_otm} deep-OTM pre-skipped)")

    new_cols = [
        "call_open", "call_high", "call_low", "call_close",
        "call_bid_info", "call_ask_info", "call_volume_info", "call_openInterest_info",
        "put_open",  "put_high",  "put_low",  "put_close",
        "put_bid_info",  "put_ask_info",  "put_volume_info",  "put_openInterest_info",
    ]
    for c in new_cols:
        if c not in df.columns:
            df[c] = np.nan

    if call_symbol_col in df.columns:
        for idx, cs in df[call_symbol_col].dropna().items():
            data = info_map.get(str(cs))
            if not data or not isinstance(data, dict):
                continue
            df.at[idx, "call_open"]             = data["open"]
            df.at[idx, "call_high"]             = data["high"]
            df.at[idx, "call_low"]              = data["low"]
            df.at[idx, "call_close"]            = data["close"]
            df.at[idx, "call_bid_info"]         = data["bid"]
            df.at[idx, "call_ask_info"]         = data["ask"]
            df.at[idx, "call_volume_info"]      = data["volume"]
            df.at[idx, "call_openInterest_info"]= data["openInterest"]

    if put_symbol_col in df.columns:
        for idx, ps in df[put_symbol_col].dropna().items():
            data = info_map.get(str(ps))
            if not data or not isinstance(data, dict):
                continue
            df.at[idx, "put_open"]              = data["open"]
            df.at[idx, "put_high"]              = data["high"]
            df.at[idx, "put_low"]               = data["low"]
            df.at[idx, "put_close"]             = data["close"]
            df.at[idx, "put_bid_info"]          = data["bid"]
            df.at[idx, "put_ask_info"]          = data["ask"]
            df.at[idx, "put_volume_info"]       = data["volume"]
            df.at[idx, "put_openInterest_info"] = data["openInterest"]

    return df


# ============= DB HELPERS: WEEKLY & MONTHLY TABLES =============
def refresh_weekly_tables(conn):
    print("Refreshing weekly tables from options_daily...")
    q = """
    SELECT DISTINCT expiry_date
    FROM options_daily
    ORDER BY expiry_date
    """
    expiries_df = pd.read_sql(q, conn)
    expiries = expiries_df["expiry_date"].tolist()

    if not expiries:
        print("No expiries in options_daily; weekly tables not updated.")
        for tbl in WEEK_TABLES:
            conn.execute(f"DROP TABLE IF EXISTS {tbl}")
        conn.commit()
        return

    load_date = current_load_date()

    for i, exp in enumerate(expiries[:len(WEEK_TABLES)]):
        table_name = WEEK_TABLES[i]
        print(f"Refreshing {table_name} for expiry {exp}")
        df_week = pd.read_sql(
            "SELECT * FROM options_daily WHERE expiry_date = ?",
            conn,
            params=(exp,)
        )
        df_week["load_date"] = load_date
        df_week.to_sql(table_name, conn, if_exists="replace", index=False)

    for j in range(len(expiries), len(WEEK_TABLES)):
        table_name = WEEK_TABLES[j]
        print(f"Dropping stale weekly table {table_name} (no matching expiry)")
        conn.execute(f"DROP TABLE IF EXISTS {table_name}")
        conn.commit()


def refresh_monthly_tables(conn):
    print("Refreshing monthly tables from options_daily...")
    q = """
    WITH monthly_min AS (
        SELECT
            substr(expiry_date, 7, 4) AS y,   -- YYYY
            substr(expiry_date, 1, 2) AS m,   -- MM
            MIN(expiry_date) AS month_expiry
        FROM options_daily
        GROUP BY y, m
    )
    SELECT month_expiry
    FROM monthly_min
    ORDER BY substr(month_expiry, 7, 4), substr(month_expiry, 1, 2), substr(month_expiry, 4, 2)
    """
    expiries_df = pd.read_sql(q, conn)
    expiries = expiries_df["month_expiry"].tolist()

    if not expiries:
        print("No monthly expiries found; monthly tables not updated.")
        for tbl in MONTH_TABLES:
            conn.execute(f"DROP TABLE IF EXISTS {tbl}")
        conn.commit()
        return

    load_date = current_load_date()

    for i, exp in enumerate(expiries[:len(MONTH_TABLES)]):
        table_name = MONTH_TABLES[i]
        print(f"Refreshing {table_name} for monthly expiry {exp}")
        df_month = pd.read_sql(
            "SELECT * FROM options_daily WHERE expiry_date = ?",
            conn,
            params=(exp,)
        )
        df_month["load_date"] = load_date
        df_month.to_sql(table_name, conn, if_exists="replace", index=False)

    for j in range(len(expiries), len(MONTH_TABLES)):
        table_name = MONTH_TABLES[j]
        print(f"Dropping stale monthly table {table_name} (no matching expiry)")
        conn.execute(f"DROP TABLE IF EXISTS {table_name}")
        conn.commit()


# ============= OPTIONS FETCH (YAHOO, +/-20 STRIKES, NEXT 45 DAYS) =============
def fetch_option_chain(ticker, company_name, asset_type, trade_day_str_db, trade_day_str_file):
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
                trade_dt = datetime.strptime(trade_day_str_file, "%d%b%Y").date()
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
                        window = 25
                        nearest_idx = (np.abs(all_strikes - spot)).argmin()
                        low_idx = max(nearest_idx - window, 0)
                        high_idx = min(nearest_idx + window, len(all_strikes) - 1)
                        keep_strikes = set(all_strikes[low_idx:high_idx + 1])
                        if not calls.empty:
                            calls = calls[calls['strike'].isin(keep_strikes)].copy()
                        if not puts.empty:
                            puts = puts[puts['strike'].isin(keep_strikes)].copy()

                if calls.empty and puts.empty:
                    continue

                # expiry_date as MM-DD-YYYY
                expiry_mmddyyyy = datetime.strptime(exp, "%Y-%m-%d").strftime("%Y-%m-%d")
                calls['expiry_date'] = expiry_mmddyyyy
                puts['expiry_date'] = expiry_mmddyyyy

                merged = pd.merge(calls, puts, on=['strike', 'expiry_date'], how='outer')
                merged['ticker'] = ticker
                merged['asset_type'] = asset_type
                merged['company_name'] = company_name
                merged['trade_date'] = trade_day_str_db  # MM-DD-YYYY for DB
                results.append(merged)
    except Exception as e:
        msg = str(e)
        if "Too Many Requests" in msg or "rate limit" in msg or "Too many requests" in msg:
            print(f"Rate limited while fetching options for {ticker}, skipping this ticker for now")
            return []
        print(f"Error for {ticker}: {e}")
        return []

    if not results:
        print(f"No options data returned for {ticker}")
    else:
        print(f"Options fetched for {ticker}: {sum(len(r) for r in results)} rows")
    return results


# ============= MERGE CALLS/PUTS: ONE TICKER AT A TIME =============
def merge_calls_puts_per_strike_parallel(trade_day, company_name_map, all_tickers):
    SECS_BETWEEN_TICKERS = 1

    print(f"Starting options chain collection (Yahoo, 1 ticker at a time) for {trade_day.strftime('%Y-%m-%d')}")
    trade_day_str_file = trade_day.strftime('%d%b%Y')   # for filenames
    trade_day_str_db = trade_day.strftime('%Y-%m-%d')   # for DB/CSV

    # Checkpoint files for resume support
    checkpoint_json = os.path.join(US_CHARTS_DIR, f"Options_Strike_CallPut_{trade_day_str_file}_checkpoint.json")
    partial_csv     = os.path.join(US_CHARTS_DIR, f"Options_Strike_CallPut_{trade_day_str_file}_partial.csv")

    # Load existing checkpoint
    done_tickers = set()
    all_rows = []
    if os.path.exists(checkpoint_json) and os.path.exists(partial_csv):
        try:
            with open(checkpoint_json, "r") as f:
                done_tickers = set(json.load(f).get("done", []))
            partial_df = pd.read_csv(partial_csv, dtype=str)
            if not partial_df.empty:
                all_rows = [partial_df]
            print(f"Resuming from checkpoint: {len(done_tickers)} tickers already done, "
                  f"{len(partial_df)} rows loaded.")
        except Exception as e:
            print(f"Checkpoint load failed ({e}), starting fresh.")
            done_tickers = set()
            all_rows = []

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

    def _save_checkpoint(done_set, rows_list):
        with open(checkpoint_json, "w") as f:
            json.dump({"done": sorted(done_set)}, f)
        if rows_list:
            pd.concat(rows_list, ignore_index=True).to_csv(partial_csv, index=False)

    total = len(all_tickers)
    done_count = len(done_tickers)

    for i, ticker in enumerate(all_tickers, 1):
        if ticker in done_tickers:
            print(f"\n[{i}/{total}] Skipping {ticker} (already done)")
            print_progress_bar(i, total, prefix="Options Data (Yahoo)")
            continue

        company_name = company_name_map.get(ticker, ticker)
        asset_type = infer_asset_type(ticker)

        print(f"\n[{i}/{total}] Fetching options for {ticker} ({company_name})")
        try:
            rows_list = fetch_option_chain(ticker, company_name, asset_type,
                                           trade_day_str_db, trade_day_str_file)
        except Exception as e:
            print(f"Error fetching {ticker}: {e}")
            rows_list = []

        if rows_list:
            all_rows += rows_list
            print(f"{ticker}: added {sum(len(r) for r in rows_list)} rows")
        else:
            print(f"{ticker}: no data (no options or skipped)")

        done_tickers.add(ticker)
        done_count += 1
        _save_checkpoint(done_tickers, all_rows)

        print_progress_bar(i, total, prefix="Options Data (Yahoo)")

        if i < total:
            print(f"Waiting {SECS_BETWEEN_TICKERS} seconds before next ticker...")
            time.sleep(SECS_BETWEEN_TICKERS)

    print("\nAll tickers processed. Saving file ...")
    if not all_rows:
        print("No call/put pairs to merge.")
        return None, None

    df_final = pd.concat(all_rows, ignore_index=True)
    before = len(df_final)
    df_final = df_final.drop_duplicates()
    after = len(df_final)
    print(f"Removed {before - after} exact duplicate rows")

    print("Enriching options data with per-contract OHLC via yfinance.info ...")
    df_final = enrich_with_option_ohlc_parallel(df_final, trade_day=trade_day)

    # expiry_date already MM-DD-YYYY from fetch_option_chain; ensure consistent format
    df_final["expiry_date"] = pd.to_datetime(
        df_final["expiry_date"].astype(str),
        format="%Y-%m-%d",
        errors="coerce"
    ).dt.strftime("%Y-%m-%d")

    cols = list(df_final.columns)
    first_cols = [c for c in ["ticker", "asset_type", "company_name"] if c in cols]
    last_cols = [c for c in ["contractSymbol_Call", "contractSymbol_Put"] if c in cols]
    middle_cols = [c for c in cols if c not in first_cols + last_cols]
    new_order = first_cols + middle_cols + last_cols
    df_final = df_final[new_order]

    out_file = os.path.join(US_CHARTS_DIR, f"Options_Strike_CallPut_{trade_day_str_file}.csv")
    df_final.to_csv(out_file, index=False)
    print(f"Output file saved: {out_file}")
    print(f"Total records: {len(df_final)}")

    load_date = current_load_date()
    df_raw = df_final.copy()
    df_raw["load_date"] = load_date

    df_daily = df_final.copy()
    df_daily["load_date"] = load_date

    conn = sqlite3.connect(DB_PATH)
    print("Writing to DB:", DB_PATH)

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
    print(f"Appended {len(df_raw)} rows to {TABLE_OPTIONS_RAW}")
    df_daily.to_sql(TABLE_OPTIONS, conn, if_exists="append", index=False)
    print(f"Appended {len(df_daily)} rows to {TABLE_OPTIONS}")

    refresh_weekly_tables(conn)
    refresh_monthly_tables(conn)

    conn.close()
    print(f"DB write and weekly/monthly refresh completed for {trade_day_str_file}")

    # Clean up checkpoint files now that everything is committed
    for _cp in [checkpoint_json, partial_csv]:
        try:
            if os.path.exists(_cp):
                os.remove(_cp)
        except Exception:
            pass

    print("Sample record for today:\n", df_final.head(1).to_string(index=False))
    return df_final, out_file


# ============= AUDIT BLANK / EMPTY ROWS =============
def audit_empty_option_rows(df: pd.DataFrame, trade_day_str_file: str):
    if df is None or df.empty:
        print("Audit: DataFrame is empty, nothing to audit.")
        return

    oi_call = df.get("openInt_Call", pd.Series(index=df.index, data=np.nan)).fillna(0)
    oi_put = df.get("openInt_Put", pd.Series(index=df.index, data=np.nan)).fillna(0)
    vol_call = df.get("vol_Call", pd.Series(index=df.index, data=np.nan)).fillna(0)
    vol_put = df.get("vol_Put", pd.Series(index=df.index, data=np.nan)).fillna(0)

    mask_empty = (oi_call == 0) & (oi_put == 0) & (vol_call == 0) & (vol_put == 0)
    empty_rows = df.loc[mask_empty, ["ticker", "asset_type", "company_name",
                                     "strike", "expiry_date", "trade_date"]].copy()

    total_empty = len(empty_rows)
    total_rows = len(df)
    print(f"\nAudit: Found {total_empty} rows with no call/put OI and volume "
          f"out of {total_rows} total rows.")

    if empty_rows.empty:
        return

    ts = datetime.now().strftime("%Y%m%d_%H%M")
    log_name = f"Options_Audit_EmptyRows_{trade_day_str_file}_{ts}.csv"
    log_path = os.path.join(LOG_DIR, log_name)

    empty_rows.to_csv(log_path, index=False)
    print(f"Simple audit log saved: {log_path}")


# ============= CHANGE CALCULATION (DAILY) =============
def ensure_columns(df, required):
    for c in required:
        if c not in df.columns:
            df[c] = np.nan
    return df

def compute_oi_vol_change(trade_day):
    """
    Compute OI/volume changes using data in the DB (options_daily),
    not from CSV files.
    """
    print(f"Computing open interest and volume changes for {trade_day.strftime('%Y-%m-%d')}...")

    trade_date_now_db = trade_day.strftime("%Y-%m-%d")  # matches trade_date in options_daily

    conn = sqlite3.connect(DB_PATH)

    # 1) Find last previous trade_date in options_daily
    q_prev_date = """
    SELECT DISTINCT trade_date
    FROM options_daily
    WHERE trade_date < ?
    ORDER BY trade_date DESC
    LIMIT 1
    """
    row = conn.execute(q_prev_date, (trade_date_now_db,)).fetchone()
    if not row:
        print("No previous trading date found in DB; cannot compute change.")
        conn.close()
        return None

    prev_trade_date = row[0]
    print(f"Previous trading date in DB: {prev_trade_date}")

    # 2) Load "today" and "previous" from DB
    df_now = pd.read_sql(
        "SELECT * FROM options_daily WHERE trade_date = ?",
        conn,
        params=(trade_date_now_db,),
    )
    df_prev = pd.read_sql(
        "SELECT * FROM options_daily WHERE trade_date = ?",
        conn,
        params=(prev_trade_date,),
    )

    if df_now.empty:
        print("Today's options_daily is empty; nothing to compute.")
        conn.close()
        return None
    if df_prev.empty:
        print("Previous day's options_daily is empty; nothing to compute.")
        conn.close()
        return None

    print(f"Rows today: {len(df_now)}, rows previous: {len(df_prev)}")

    # 3) Ensure required columns exist
    required_columns = [
        'ticker', 'company_name', 'asset_type', 'strike', 'expiry_date', 'trade_date',
        'openInt_Call', 'openInt_Put', 'vol_Call', 'vol_Put',
        'lastPrice_Call', 'lastPrice_Put'
    ]
    df_now = ensure_columns(df_now, required_columns)
    df_prev = ensure_columns(df_prev, required_columns)

    ohlc_cols = [
        "call_open", "call_high", "call_low", "call_close",
        "put_open", "put_high", "put_low", "put_close"
    ]
    df_now = ensure_columns(df_now, ohlc_cols)
    df_prev = ensure_columns(df_prev, ohlc_cols)

    # expiry_date normalized as MM-DD-YYYY
    df_now['expiry_date'] = pd.to_datetime(
        df_now['expiry_date'].astype(str),
        errors='coerce'
    ).dt.strftime("%Y-%m-%d")
    df_prev['expiry_date'] = pd.to_datetime(
        df_prev['expiry_date'].astype(str),
        errors='coerce'
    ).dt.strftime("%Y-%m-%d")

    df_now['strike'] = pd.to_numeric(df_now['strike'], errors='coerce')
    df_prev['strike'] = pd.to_numeric(df_prev['strike'], errors='coerce')

    # 4) Merge on ticker + strike + expiry_date
    key_cols = ['ticker', 'strike', 'expiry_date']
    merged = pd.merge(
        df_now,
        df_prev,
        on=key_cols,
        suffixes=('_now', '_prev'),
        how='inner'
    )
    print("Merged rows:", len(merged))
    if merged.empty:
        print("No overlapping strikes/expiries in DB; nothing to do.")
        conn.close()
        return None

    # 5) Fill NaNs and compute deltas
    for c in [
        'openInt_Call_now', 'openInt_Call_prev',
        'openInt_Put_now', 'openInt_Put_prev',
        'vol_Call_now', 'vol_Call_prev',
        'vol_Put_now', 'vol_Put_prev'
    ]:
        if c in merged.columns:
            merged[c] = merged[c].fillna(0)

    merged['change_OI_Call'] = merged['openInt_Call_now'] - merged['openInt_Call_prev']
    merged['change_OI_Put'] = merged['openInt_Put_now'] - merged['openInt_Put_prev']
    merged['change_vol_Call'] = merged['vol_Call_now'] - merged['vol_Call_prev']
    merged['change_vol_Put'] = merged['vol_Put_now'] - merged['vol_Put_prev']

    def pct_change(now, prev):
        return np.where(prev == 0, np.nan, (now - prev) / prev * 100)

    merged['pct_change_OI_Call'] = pct_change(merged['openInt_Call_now'], merged['openInt_Call_prev'])
    merged['pct_change_OI_Put'] = pct_change(merged['openInt_Put_now'], merged['openInt_Put_prev'])
    merged['pct_change_vol_Call'] = pct_change(merged['vol_Call_now'], merged['vol_Call_prev'])
    merged['pct_change_vol_Put'] = pct_change(merged['vol_Put_now'], merged['vol_Put_prev'])

    # 6) Price / OHLC fields
    merged["lastPrice_Call_now"] = merged["lastPrice_Call_now"].fillna(0)
    merged["lastPrice_Put_now"] = merged["lastPrice_Put_now"].fillna(0)

    merged["call_high_now"] = merged.get("call_high_now", merged.get("call_high")).fillna(0)
    merged["put_high_now"] = merged.get("put_high_now", merged.get("put_high")).fillna(0)

    merged["R1"] = merged["strike"] + merged["lastPrice_Call_now"]
    merged["S1"] = merged["strike"] - merged["lastPrice_Put_now"]
    merged["R12"] = merged["strike"] + merged["call_high_now"]
    merged["S12"] = merged["strike"] - merged["put_high_now"]

    merged["call_open_now"] = merged.get("call_open_now", merged.get("call_open"))
    merged["call_low_now"] = merged.get("call_low_now", merged.get("call_low"))
    merged["call_close_now"] = merged.get("call_close_now", merged.get("call_close"))
    merged["put_open_now"] = merged.get("put_open_now", merged.get("put_open"))
    merged["put_low_now"] = merged.get("put_low_now", merged.get("put_low"))
    merged["put_close_now"] = merged.get("put_close_now", merged.get("put_close"))

    cols_out = [
        'ticker', 'company_name_now', 'asset_type_now', 'strike', 'expiry_date', 'trade_date_now',
        'openInt_Call_now', 'openInt_Call_prev', 'change_OI_Call', 'pct_change_OI_Call',
        'openInt_Put_now', 'openInt_Put_prev', 'change_OI_Put', 'pct_change_OI_Put',
        'vol_Call_now', 'vol_Call_prev', 'change_vol_Call', 'pct_change_vol_Call',
        'vol_Put_now', 'vol_Put_prev', 'change_vol_Put', 'pct_change_vol_Put',
        'lastPrice_Call_now', 'lastPrice_Put_now',
        'call_open_now', 'call_high_now', 'call_low_now', 'call_close_now',
        'put_open_now', 'put_high_now', 'put_low_now', 'put_close_now',
        'R1', 'S1', 'R12', 'S12'
    ]
    merged = ensure_columns(merged, cols_out)

    df_out = merged[cols_out].copy()
    df_out["trade_date_now"] = trade_date_now_db
    df_out["load_date"] = current_load_date()

    # 7) Optional CSV output (same naming as before)
    trade_day_str_file = trade_day.strftime('%d%b%Y')
    out_file = os.path.join(US_CHARTS_DIR, f"Options_Strike_CallPut_Change_{trade_day_str_file}.csv")
    df_out.to_csv(out_file, index=False)
    print(f"OI/Volume change file: {out_file}")
    print(f"Change records saved: {len(df_out)}")

    # 8) Write into DB table options_change
    try:
        conn.execute(f"DELETE FROM {TABLE_OPTIONS_CHANGE} WHERE trade_date_now = ?", (trade_date_now_db,))
        conn.commit()
    except Exception:
        pass
    df_out.to_sql(TABLE_OPTIONS_CHANGE, conn, if_exists="append", index=False)
    conn.close()
    print(f"Appended {len(df_out)} change rows into {TABLE_OPTIONS_CHANGE}")

    print("Sample percentage change record:\n", df_out.head(2).to_string(index=False))
    return out_file

# ============= STOCK_DAILY (OHLC + PCR) =============
def build_stock_daily(trade_day, all_tickers):
    trade_day_str_db = trade_day.strftime("%Y-%m-%d")
    print(f"Building stock_daily for {trade_day_str_db}...")

    session = curl_requests.Session(impersonate="chrome")
    records = []

    for ticker in all_tickers:
        try:
            trade_day_iso = trade_day.strftime("%Y-%m-%d")
            tk = yf.Ticker(ticker, session=session)
            _end_iso = (trade_day + timedelta(days=1)).strftime("%Y-%m-%d")
            hist = tk.history(start=trade_day_iso, end=_end_iso, interval="1d")
            if hist.empty:
                hist = tk.history(period="1d")
            if hist.empty:
                print(f"stock_daily: no price data for {ticker}")
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
                params=(ticker, trade_day_str_db),
            )
            conn.close()

            total_call_oi = df_opt["openInt_Call"].fillna(0).sum() if not df_opt.empty else 0
            total_put_oi = df_opt["openInt_Put"].fillna(0).sum() if not df_opt.empty else 0
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
            print(f"stock_daily: error for {ticker}: {e}")
            continue

    if not records:
        print("stock_daily: no records created")
        return None

    df_stock = pd.DataFrame(records)

    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute(f"DELETE FROM {TABLE_STOCK_DAILY} WHERE trade_date = ?", (trade_date_str_db,))
        conn.commit()
    except Exception:
        pass

    df_stock.to_sql(TABLE_STOCK_DAILY, conn, if_exists="append", index=False)
    conn.close()
    print(f"stock_daily: appended {len(df_stock)} rows into {TABLE_STOCK_DAILY}")

    return df_stock


# ============= CLEANUP & ARCHIVE =============
def cleanup_old_files(data_dir, days=90):
    print(f"Cleaning up files older than {days} days...")
    cutoff = time.time() - days * 86400
    files_to_delete = []
    for fname in os.listdir(data_dir):
        fpath = os.path.join(data_dir, fname)
        if os.path.isfile(fpath) and os.path.getmtime(fpath) < cutoff:
            files_to_delete.append((fname, fpath))
    if not files_to_delete:
        print("No old files found to delete")
        return
    for i, (fname, fpath) in enumerate(files_to_delete, 1):
        try:
            os.remove(fpath)
        except Exception:
            continue
        print_progress_bar(i, len(files_to_delete), prefix="Cleanup")
    print(f"\nCleanup completed: {len(files_to_delete)} files deleted.")


def archive_old_excels(us_charts_dir, archive_dir, keep_days=1):
    print(f"Archiving US_CHARTS files older than {keep_days} days...")
    now = time.time()
    cutoff = now - keep_days * 86400

    for fname in os.listdir(us_charts_dir):
        fpath = os.path.join(us_charts_dir, fname)
        if not os.path.isfile(fpath):
            continue

        if not (
            (fname.startswith("Options_Strike_CallPut_") and (fname.endswith(".csv") or fname.endswith(".xlsx"))) or
            (fname.startswith("Options_Strike_CallPut_Change_") and fname.endswith(".csv"))
        ):
            continue

        if os.path.getmtime(fpath) < cutoff:
            target = os.path.join(archive_dir, fname)
            try:
                os.replace(fpath, target)
                print(f"Archived {fname} -> {target}")
            except Exception as e:
                print(f"Could not archive {fname}: {e}")


# ============= MAIN (daily run) =============
if __name__ == "__main__":
    print("Starting Options Chain Data Collection Script (Yahoo, single-ticker mode)")
    print(f"Data directory: {DATA_DIR}")
    if not os.path.exists(DATA_DIR):
        print(f"Creating data directory: {DATA_DIR}")
        os.makedirs(DATA_DIR)
    else:
        print("Data directory exists")

    os.makedirs(US_CHARTS_DIR, exist_ok=True)
    os.makedirs(ARCHIVE_DIR, exist_ok=True)

    print("\nPhase 1: Cleanup old files under DATA_DIR")
    cleanup_old_files(DATA_DIR, 90)

    # print("\nPhase 1b: Archive old US_CHARTS Excel/CSV")
    # archive_old_excels(US_CHARTS_DIR, ARCHIVE_DIR, keep_days=1)

    print("\nPhase 2: Universe & name map")
    company_name_map, all_tickers = prepare_universe_and_name_map()

    print("\nPhase 3: Determine trading day")
    eod_day = get_eod_trading_day()

    print("\nPhase 4: Collect options data (Yahoo)")
    df, today_file = merge_calls_puts_per_strike_parallel(eod_day, company_name_map, all_tickers)

    trade_day_str_file = eod_day.strftime('%d%b%Y')

    if df is not None:
        print("\nPhase 4b: Audit empty option rows")
        audit_empty_option_rows(df, trade_day_str_file)

        print("\nPhase 5: Compute changes")
        compute_oi_vol_change(eod_day)

        print("\nPhase 6: Build stock_daily")
        build_stock_daily(eod_day, all_tickers)

        print("\nPhase 7: Archive old US_CHARTS Excel/CSV")
        archive_old_excels(US_CHARTS_DIR, ARCHIVE_DIR, keep_days=1)

        print("\nScript completed successfully!")
    else:
        print("\nScript ended - no data to process")
