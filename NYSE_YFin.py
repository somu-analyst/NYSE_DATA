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
from io import StringIO  # for pd.read_html on HTML string

# ================== CONFIG ==================

DATA_DIR = r"C:\Users\srini\Options_chain_data"

# Include IBIT with ETFs/Indexes
INDEX_TICKERS = [
    "QQQ", "SPY", "IWM", "DIA", "IVV", "VOO", "SPLG", "SPYG", "SPYV", "IBIT"
]

METALS_TICKERS = ["GLD", "IAU", "SGOL", "PHYS", "SLV", "SIVR", "PSLV", "SIL"]
COMMODITY_TICKERS = ["USO", "CPER"]
BOND_TICKERS = ["AGG", "BND", "SCHZ", "FBND", "IUSB", "SPAB", "VTEB"]
CRYPTO_TICKERS = ["BTC-USD", "ETH-USD", "SOL-USD", "BNB-USD", "XRP-USD", "ADA-USD", "DOGE-USD", "TRX-USD"]

# S&P 500 stocks (partial list shown - use full S&P list as in your original)
SP500_TICKERS = [
    "MMM","AOS","ABT","ABBV","ACN","ADBE","AMD","AES","AFL","A","APD","ABNB","AKAM","ALB","ARE","ALGN","ALLE","LNT","ALL",
    "GOOGL","GOOG","MO","AMZN","AMCR","AEE","AEP","AXP","AIG","AMT","AWK","AMP","AME","AMGN","APH","ADI","AON","APA","APO","AAPL",
    "AMAT","APP","APTV","ACGL","ADM","ANET","AJG","AIZ","T","ATO","ADSK","ADP","AZO","AVB","AVY","AXON","BKR","BALL","BAC","BAX",
    "BDX","BRK.B","BBY","TECH","BIIB","BLK","BX","XYZ","BK","BA","BKNG","BSX","BMY","AVGO","BR","BRO","BF.B","BLDR","BG","BXP",
    "CHRW","CDNS","CPT","CPB","COF","CAH","KMX","CCL","CARR","CAT","CBOE","CBRE","CDW","COR","CNC","CNP","CF","CRL","SCHW","CHTR",
    "CVX","CMG","CB","CHD","CI","CINF","CTAS","CSCO","C","CFG","CLX","CME","CMS","KO","CTSH","COIN","CL","CMCSA","CAG","COP","ED",
    "STZ","CEG","COO","CPRT","GLW","CPAY","CTVA","CSGP","COST","CTRA","CRWD","CCI","CSX","CMI","CVS","DHR","DRI","DDOG","DVA","DAY",
    "DECK","DE","DELL","DAL","DVN","DXCM","FANG","DLR","DG","DLTR","D","DPZ","DASH","DOV","DOW","DHI","DTE","DUK","DD","EMN","ETN",
    "EBAY","ECL","EIX","EW","EA","ELV","EME","EMR","ETR","EOG","EPAM","EQT","EFX","EQIX","EQR","ERIE","ESS","EL","EG","EVRG","ES",
    "EXC","EXE","EXPE","EXPD","EXR","XOM","FFIV","FDS","FICO","FAST","FRT","FDX","FIS","FITB","FSLR","FE","FI","F","FTNT","FTV","FOXA",
    "FOX","BEN","FCX","GRMN","IT","GE","GEHC","GEV","GEN","GNRC","GD","GIS","GM","GPC","GILD","GPN","GL","GDDY","GS","HAL","HIG",
    "HAS","HCA","DOC","HSIC","HSY","HPE","HLT","HOLX","HD","HON","HRL","HST","HWM","HPQ","HUBB","HUM","HBAN","HII","IBM","IEX",
    "IDXX","ITW","INCY","IR","PODD","INTC","IBKR","ICE","IFF","IP","IPG","INTU","ISRG","IVZ","INVH","IQV","IRM","JBHT","JBL","JKHY",
    "J","JNJ","JCI","JPM","K","KVUE","KDP","KEY","KEYS","KMB","KIM","KMI","KKR","KLAC","KHC","KR","LHX","LH","LRCX","LW","LVS",
    "LDOS","LEN","LII","LLY","LIN","LYV","LKQ","LMT","L","LOW","LULU","LYB","MTB","MPC","MAR","MMC","MLM","MAS","MA","MTCH","MKC",
    "MCD","MCK","MDT","MRK","META","MET","MTD","MGM","MCHP","MU","MSFT","MAA","MRNA","MHK","MOH","TAP","MDLZ","MPWR","MNST","MCO",
    "MS","MOS","MSI","MSCI","NDAQ","NTAP","NFLX","NEM","NWSA","NWS","NEE","NKE","NI","NDSN","NSC","NTRS","NOC","NCLH","NRG","NUE",
    "NVDA","NVR","NXPI","ORLY","OXY","ODFL","OMC","ON","OKE","ORCL","OTIS","PCAR","PKG","PLTR","PANW","PSKY","PH","PAYX","PAYC",
    "PYPL","PNR","PEP","PFE","PCG","PM","PSX","PNW","PNC","POOL","PPG","PPL","PFG","PG","PGR","PLD","PRU","PEG","PTC","PSA","PHM",
    "PWR","QCOM","DGX","RL","RJF","RTX","O","REG","REGN","RF","RSG","RMD","RVTY","HOOD","ROK","ROL","ROP","ROST","RCL","SPGI","CRM",
    "SBAC","SLB","STX","SRE","NOW","SHW","SPG","SWKS","SJM","SW","SNA","SOLV","SO","LUV","SWK","SBUX","STT","STLD","STE","SYK",
    "SMCI","SYF","SNPS","SYY","TMUS","TROW","TTWO","TPR","TRGP","TGT","TEL","TDY","TER","TSLA","TXN","TPL","TXT","TMO","TJX","TKO",
    "TTD","TSCO","TT","TDG","TRV","TRMB","TFC","TYL","TSN","USB","UBER","UDR","ULTA","UNP","UAL","UPS","URI","UNH","UHS","VLO",
    "VTR","VLTO","VRSN","VRSK","VZ","VRTX","VTRS","VICI","V","VST","VMC","WRB","GWW","WAB","WMT","DIS","WBD","WM","WAT","WEC",
    "WFC","WELL","WST","WDC","WY","WSM","WMB","WTW","WDAY","WYNN","XEL","XYL","YUM","ZBRA","ZBH","ZTS","IBIT"
]

# ========= HELPER: ticker normalization & asset type ==========

def yf_ticker_fix(ticker):
    """Convert dot-tickers to yfinance dash format."""
    return ticker.replace('.', '-')

ALL_TICKERS = [yf_ticker_fix(t) for t in (
    INDEX_TICKERS + METALS_TICKERS + COMMODITY_TICKERS + BOND_TICKERS + CRYPTO_TICKERS + SP500_TICKERS
)]
OPTIONABLE_TICKERS = set([yf_ticker_fix(t) for t in SP500_TICKERS + INDEX_TICKERS])

ASSET_TYPE_MAP = {}
for t in INDEX_TICKERS:     ASSET_TYPE_MAP[yf_ticker_fix(t)] = "index"
for t in METALS_TICKERS:    ASSET_TYPE_MAP[yf_ticker_fix(t)] = "gold" if "G" in t else "silver"
for t in COMMODITY_TICKERS: ASSET_TYPE_MAP[yf_ticker_fix(t)] = "crude" if t == "USO" else "copper"
for t in BOND_TICKERS:      ASSET_TYPE_MAP[yf_ticker_fix(t)] = "bond"
for t in CRYPTO_TICKERS:    ASSET_TYPE_MAP[yf_ticker_fix(t)] = "crypto"
for t in SP500_TICKERS:     ASSET_TYPE_MAP[yf_ticker_fix(t)] = "stock"

# =============== PROGRESS BAR ===============

def print_progress_bar(current, total, bar_length=50, prefix="Progress"):
    percent = (current / total) * 100 if total > 0 else 0
    filled_length = int(bar_length * current // total) if total > 0 else 0
    bar = '█' * filled_length + '-' * (bar_length - filled_length)
    sys.stdout.write(f'\r{prefix}: |{bar}| {percent:.1f}% ({current}/{total})')
    sys.stdout.flush()
    if current == total:
        print()

# ========== NAME RESOLUTION: Yahoo -> Wikipedia -> ticker ==========

def yahoo_name_from_ticker(symbol):
    """Try to get name from Yahoo search/autocomplete API."""
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
    """
    Return a dict {ticker: company_name} from Wikipedia's S&P 500 page.
    """
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
    # Use StringIO to avoid FutureWarning
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

def build_company_name_map_all(ALL_TICKERS):
    """
    Build {ticker: name} for all tickers using:
    1) Yahoo search
    2) S&P 500 Wikipedia
    3) Fallback to ticker
    """
    print("🔍 Building S&P 500 name map from Wikipedia...")
    try:
        sp500_map = get_sp500_name_map()
    except Exception as e:
        print("⚠️ Could not load S&P 500 Wikipedia table:", e)
        sp500_map = {}

    name_map = {}
    total = len(ALL_TICKERS)
    for i, t in enumerate(ALL_TICKERS, 1):
        # 1) Yahoo
        name = yahoo_name_from_ticker(t)
        if not name:
            # 2) Wikipedia S&P 500 (handle dot vs dash)
            base = t.replace('-', '.')
            name = sp500_map.get(base)
        # 3) Fallback
        if not name:
            name = t
        name_map[t] = name
        print_progress_bar(i, total, prefix="🏢 Name Mapping")

    print("\n✅ Completed company name mapping.")
    return name_map

# ============= TRADING DAY (EOD‑safe) =============

def get_eod_trading_day(max_back=10):
    print("📅 Determining end-of-day trading date...")
    eastern = pytz.timezone('US/Eastern')
    now = datetime.now(eastern)
    market_close_hour = 16
    market_close_minute = 10
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    after_close = (now.hour > market_close_hour or (now.hour == market_close_hour and now.minute >= market_close_minute))
    print(f"Current time: {now.strftime('%Y-%m-%d %H:%M:%S %Z')} Market closed: {'Yes' if after_close else 'No'}")

    if today.weekday() < 5 and after_close:
        next_day = today + timedelta(days=1)
        valid = True
        for t in INDEX_TICKERS[:1]:
            df = yf.download(yf_ticker_fix(t), start=today.strftime('%Y-%m-%d'),
                             end=next_day.strftime('%Y-%m-%d'), auto_adjust=False)
            if df.empty:
                valid = False
                break
        if valid:
            print(f"✅ Using today's date: {today.strftime('%Y-%m-%d')}")
            return today

    for delta in range(1, max_back + 1):
        day = today - timedelta(days=delta)
        next_day = day + timedelta(days=1)
        if day.weekday() < 5:
            for t in INDEX_TICKERS[:1]:
                df = yf.download(yf_ticker_fix(t), start=day.strftime('%Y-%m-%d'),
                                 end=next_day.strftime('%Y-%m-%d'), auto_adjust=False)
                if df.empty:
                    break
            else:
                print(f"✅ Using trading day: {day.strftime('%Y-%m-%d')}")
                return day
    raise Exception(f"No EOD found for all tickers in last {max_back} days")

# ============= OPTIONS FETCH & MERGE =============

def fetch_option_chain(ticker, company_name, asset_type, trade_day_str):
    tk = yf.Ticker(ticker)
    results = []
    try:
        if hasattr(tk, 'options') and tk.options:
            for exp in tk.options:
                oc = tk.option_chain(exp)
                calls = oc.calls[['strike', 'openInterest', 'lastPrice', 'volume']].rename(columns={
                    'openInterest': 'openInt_Call', 'lastPrice': 'lastPrice_Call', 'volume': 'vol_Call'})
                puts = oc.puts[['strike', 'openInterest', 'lastPrice', 'volume']].rename(columns={
                    'openInterest': 'openInt_Put', 'lastPrice': 'lastPrice_Put', 'volume': 'vol_Put'})
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

def merge_calls_puts_per_strike_parallel(trade_day, company_name_map):
    print(f"🔄 Starting options chain collection with threading for {trade_day.strftime('%Y-%m-%d')}")
    trade_day_str = trade_day.strftime('%d%b%Y')
    args = [
        (ticker, company_name_map.get(ticker, ticker), ASSET_TYPE_MAP.get(ticker, "other"), trade_day_str)
        for ticker in ALL_TICKERS
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
        out_file = os.path.join(DATA_DIR, f"Options_Strike_CallPut_{trade_day_str}.csv")
        df_final.to_csv(out_file, index=False)
        print(f"✅ Output file saved: {out_file}")
        print(f"📈 Total records: {len(df_final)}")
        print("Sample record for today:\n", df_final.head(1).to_string(index=False))
        return df_final, out_file
    print("❌ No call/put pairs to merge.")
    return None, None

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

# ============= MAIN =============

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

    print("\n🏢 Phase 2: Building company name mapping (Yahoo → Wikipedia → ticker)")
    company_name_map = build_company_name_map_all(ALL_TICKERS)

    print("\n📅 Phase 3: Determine trading day")
    eod_day = get_eod_trading_day()

    print("\n📈 Phase 4: Collect options data")
    df, today_file = merge_calls_puts_per_strike_parallel(eod_day, company_name_map)

    if df is not None:
        print("\n📊 Phase 5: Compute changes")
        compute_oi_vol_change(eod_day)
        print("\n🎉 Script completed successfully!")
    else:
        print("\n❌ Script ended - no data to process")
