import os
from datetime import datetime, time as dt_time
import numpy as np
import pandas as pd
import yfinance as yf  # used only for underlying prices
import sqlite3

# =========================
# CONFIG
# =========================

DATA_DIR = r"C:\Users\srini\Options_chain_data"
BASE_OUT_DIR = os.path.join(DATA_DIR, "NYSE_DATA", "US_CHARTS")
DB_PATH = os.path.join(DATA_DIR, "US_data.db")  # your options DB

def fmt_dmy(dt):
    return dt.strftime("%d%b%Y")  # e.g. 23Dec2025

def parse_dmy(s):
    try:
        return datetime.strptime(s, "%d%b%Y")
    except ValueError:
        return None

def get_latest_market_date_folder(base_dir):
    """
    Use the latest folder named like 22Dec2025, 23Dec2025 etc (<= today)
    as the consolidated date (yesterday leads).
    """
    today = datetime.now()
    candidates = []
    if not os.path.exists(base_dir):
        return None

    for name in os.listdir(base_dir):
        full = os.path.join(base_dir, name)
        if not os.path.isdir(full):
            continue
        dt = parse_dmy(name)
        if dt is None:
            continue
        if dt <= today:
            candidates.append((dt, name))

    if not candidates:
        return None

    candidates.sort()
    return candidates[-1][1]

# TD_NOW = today's trade date (output naming)
TD_NOW = fmt_dmy(datetime.now())

# TD_YEST = last available market date folder under BASE_OUT_DIR (yesterday)
_last = get_latest_market_date_folder(BASE_OUT_DIR)
if _last is None:
    raise RuntimeError(f"No valid market date folders found in {BASE_OUT_DIR}")
TD_YEST = _last

MIN_PREMIUM_COLLECTED = 0.0
MAX_MAX_LOSS = None

PSTRONG = 200.0    # threshold for strong green/red per spread PnL
PROB_STRONG = 0.80 # high-probability threshold (probability proxy)
PROB_LIGHT = 0.60  # medium-probability threshold

# =========================
# Helpers
# =========================

def expiry_to_yyyy_mm_dd(exp_str):
    s = str(exp_str)
    for fmt in ["%d%b%Y", "%Y-%m-%d", "%d-%b-%Y", "%m/%d/%Y"]:
        try:
            dt = datetime.strptime(s, fmt)
            return dt.strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def get_live_option_price_db(ticker, expiry_yyyy_mm_dd, strike, call_put="call"):
    """
    Get 'live' (snapshot) option close price from US_data.db (options_daily),
    instead of calling Yahoo.
    """
    try:
        conn = sqlite3.connect(DB_PATH)
    except Exception:
        return np.nan

    q_date = """
    SELECT trade_date
    FROM options_daily
    WHERE ticker = ?
      AND expiry_date = ?
    ORDER BY date(trade_date) DESC
    LIMIT 1
    """
    try:
        df_date = pd.read_sql(q_date, conn, params=(ticker, expiry_yyyy_mm_dd))
    except Exception:
        conn.close()
        return np.nan

    if df_date.empty:
        conn.close()
        return np.nan

    trade_date_str = df_date["trade_date"].iloc[0]

    cur = conn.cursor()
    try:
        cur.execute("PRAGMA table_info(options_daily)")
        cols_db = [row[1] for row in cur.fetchall()]
    except Exception:
        conn.close()
        return np.nan

    if call_put.lower() == "call":
        preferred_cols = ["call_close_now", "call_close"]
    else:
        preferred_cols = ["put_close_now", "put_close"]

    price_col = None
    for c in preferred_cols:
        if c in cols_db:
            price_col = c
            break

    if price_col is None:
        conn.close()
        return np.nan

    q_price = f"""
    SELECT {price_col} AS px
    FROM options_daily
    WHERE ticker = ?
      AND expiry_date = ?
      AND trade_date = ?
      AND strike = ?
    LIMIT 1
    """
    try:
        df_px = pd.read_sql(
            q_price,
            conn,
            params=(ticker, expiry_yyyy_mm_dd, trade_date_str, float(strike))
        )
    except Exception:
        conn.close()
        return np.nan

    conn.close()

    if df_px.empty or pd.isna(df_px["px"].iloc[0]):
        return np.nan

    return float(df_px["px"].iloc[0])


def get_live_option_price(ticker, expiry_yyyy_mm_dd, strike, call_put="call"):
    """
    Wrapper: use DB snapshot as 'live' option price source.
    """
    return get_live_option_price_db(ticker, expiry_yyyy_mm_dd, strike, call_put)


def get_underlying_intraday_prices(ticker):
    """
    Underlying today open and ~9:45 using 1m intraday from Yahoo.
    """
    try:
        t = yf.Ticker(ticker)
        hist = t.history(period="1d", interval="1m")
    except Exception:
        return np.nan, np.nan

    if hist.empty:
        return np.nan, np.nan

    if getattr(hist.index, "tz", None) is not None:
        hist.index = hist.index.tz_localize(None)

    open_price = float(hist["Open"].iloc[0])

    target_time = dt_time(9, 45)
    times = hist.index.to_pydatetime()
    diffs = [
        abs(
            (dt_obj.time().hour * 60 + dt_obj.time().minute)
            - (target_time.hour * 60 + target_time.minute)
        )
        for dt_obj in times
    ]
    idx_min = int(np.argmin(diffs))
    price_945 = float(hist["Close"].iloc[idx_min])

    return open_price, price_945

# =========================
# Load consolidated spreads from yesterday folder
# =========================

def build_live_pnl_from_consolidated(td_now, td_yest):
    """
    Read yesterday's consolidated spreads from TD_YEST folder,
    compute today's 'live' leg prices and PnL using DB prices.
    """
    in_day_folder = os.path.join(BASE_OUT_DIR, td_yest)
    cons_file = os.path.join(in_day_folder, f"{td_yest}_Consolidated_Spreads.xlsx")

    if not os.path.exists(cons_file):
        print(f"[LIVE] Consolidated file not found: {cons_file}")
        return None, None

    df = pd.read_excel(cons_file)
    if df.empty:
        print("[LIVE] Consolidated file is empty.")
        return None, None

    def norm_exp(val):
        if isinstance(val, (datetime, pd.Timestamp)):
            return val.strftime("%Y-%m-%d")
        s = str(val)
        for fmt in ("%d%b%Y", "%Y-%m-%d", "%d-%b-%Y", "%m/%d/%Y"):
            try:
                return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
            except ValueError:
                continue
        return s

    df["Expiry"] = df["Expiry"].apply(norm_exp)

    df_live = df.copy()

    required_cols = [
        "Ticker", "Expiry",
        "Short_Strike", "Long_Strike",
        "Short_Call_Close", "Long_Call_Close",
        "Premium_Collected"
    ]
    for c in required_cols:
        if c not in df_live.columns:
            print(f"[LIVE] Missing column in consolidated file: {c}")
            return None, None

    df_live["Short_Price_Live"] = np.nan
    df_live["Long_Price_Live"] = np.nan
    df_live["Spread_Value_Live"] = np.nan
    df_live["PnL_Live"] = np.nan
    df_live["Short_PnL_Live"] = np.nan
    df_live["Long_PnL_Live"] = np.nan
    df_live["Combined_PnL_Live"] = np.nan
    df_live["Short_Investment"] = 0.0
    df_live["Long_Investment"] = np.nan
    df_live["Combined_Investment"] = np.nan
    df_live["Combined_PnL_Open_Approx"] = np.nan

    print(f"[LIVE] Computing DB-based live PnL for {len(df_live)} spreads from {cons_file} ...")

    for idx, row in df_live.iterrows():
        ticker = row["Ticker"]
        expiry_raw = row["Expiry"]
        short_strike = row["Short_Strike"]
        long_strike = row["Long_Strike"]
        entry_premium = row["Premium_Collected"]

        exp_yyyy_mm_dd = expiry_to_yyyy_mm_dd(expiry_raw)
        if exp_yyyy_mm_dd is None:
            continue

        short_live = get_live_option_price(ticker, exp_yyyy_mm_dd, short_strike, "call")
        long_live  = get_live_option_price(ticker, exp_yyyy_mm_dd, long_strike, "call")

        if np.isnan(short_live) or np.isnan(long_live) or np.isnan(entry_premium):
            continue

        multiplier = 100.0
        spread_value_live = (short_live - long_live) * multiplier
        pnl_live = entry_premium - spread_value_live

        short_entry = row["Short_Call_Close"]
        long_entry = row["Long_Call_Close"]

        short_pnl = (short_entry - short_live) * multiplier
        long_pnl = (long_live - long_entry) * multiplier
        combined_pnl = short_pnl + long_pnl

        short_inv = 0.0
        long_inv = long_entry * multiplier
        combined_inv = short_inv + long_inv

        df_live.at[idx, "Short_Price_Live"] = short_live
        df_live.at[idx, "Long_Price_Live"] = long_live
        df_live.at[idx, "Spread_Value_Live"] = spread_value_live
        df_live.at[idx, "PnL_Live"] = pnl_live
        df_live.at[idx, "Short_PnL_Live"] = short_pnl
        df_live.at[idx, "Long_PnL_Live"] = long_pnl
        df_live.at[idx, "Combined_PnL_Live"] = combined_pnl
        df_live.at[idx, "Short_Investment"] = short_inv
        df_live.at[idx, "Long_Investment"] = long_inv
        df_live.at[idx, "Combined_Investment"] = combined_inv

    for c in df_live.columns:
        if pd.api.types.is_numeric_dtype(df_live[c]):
            df_live[c] = df_live[c].astype(float).round(2)

    out_day_folder = os.path.join(BASE_OUT_DIR, td_now)
    os.makedirs(out_day_folder, exist_ok=True)
    out_file = os.path.join(out_day_folder, f"{td_now}_Consolidated_Spreads_Live.xlsx")
    return df_live, out_file

# =========================
# Open-performance helper
# =========================

def get_underlying_intraday_map(df_live):
    if df_live is None or df_live.empty:
        return {}
    tickers = sorted(df_live["Ticker"].unique())
    open_prices = {}
    print("[LIVE] Fetching underlying open for open-performance ...")
    for tkr in tickers:
        op, _ = get_underlying_intraday_prices(tkr)
        open_prices[tkr] = op
    return open_prices

# =========================
# Summary / combo builders for Yday_lead_EOD_Performance
# =========================

def make_yday_lead_eod_blocks_from_live(df_live):
    """
    Build 3-line combos (SHORT, LONG, COMBINED) for every spread in df_live,
    grouped by Expiry and Ticker, using only yesterday's consolidated file.
    """
    cols = [
        "Expiry", "Ticker",
        "Strike", "Entry_Price", "Live_Price",
        "Leg_Type", "CallPut",
        "PnL", "Investment", "Pct_PnL"
    ]
    if df_live is None or df_live.empty:
        return pd.DataFrame(columns=cols)

    df = df_live.copy()
    # Clean expiry for grouping (avoid literal 'nan')
    df["Expiry"] = df["Expiry"].astype(str)
    df.loc[df["Expiry"].isin(["nan", "NaT"]), "Expiry"] = ""

    rows = []
    df_sorted = df.sort_values(["Expiry", "Ticker", "Short_Strike", "Long_Strike"])

    for exp, sub in df_sorted.groupby("Expiry", dropna=False):
        label = f"Expiry: {exp}" if exp else "Expiry: (none)"
        rows.append({
            "Expiry": label,
            "Ticker": "",
            "Strike": "",
            "Entry_Price": "",
            "Live_Price": "",
            "Leg_Type": "",
            "CallPut": "",
            "PnL": "",
            "Investment": "",
            "Pct_PnL": ""
        })

        for _, r in sub.iterrows():
            tkr = r["Ticker"]
            short_strike = r["Short_Strike"]
            long_strike = r["Long_Strike"]
            short_entry = r["Short_Call_Close"]
            long_entry = r["Long_Call_Close"]
            short_live = r.get("Short_Price_Live", np.nan)
            long_live = r.get("Long_Price_Live", np.nan)
            short_pnl = r.get("Short_PnL_Live", np.nan)
            long_pnl = r.get("Long_PnL_Live", np.nan)
            combined_pnl = r.get("Combined_PnL_Live", np.nan)
            short_inv = r.get("Short_Investment", 0.0)
            long_inv = r.get("Long_Investment", np.nan)
            combined_inv = r.get("Combined_Investment", np.nan)

            def pct(pnl, inv):
                if isinstance(pnl, (int, float)) and isinstance(inv, (int, float)) and inv != 0:
                    return round(float(pnl) / float(inv), 4)
                return ""

            rows.append({
                "Expiry": "",
                "Ticker": tkr,
                "Strike": short_strike,
                "Entry_Price": short_entry,
                "Live_Price": short_live,
                "Leg_Type": "SHORT",
                "CallPut": "CALL",
                "PnL": short_pnl,
                "Investment": short_inv,
                "Pct_PnL": pct(short_pnl, short_inv)
            })

            rows.append({
                "Expiry": "",
                "Ticker": tkr,
                "Strike": long_strike,
                "Entry_Price": long_entry,
                "Live_Price": long_live,
                "Leg_Type": "LONG",
                "CallPut": "CALL",
                "PnL": long_pnl,
                "Investment": long_inv,
                "Pct_PnL": pct(long_pnl, long_inv)
            })

            rows.append({
                "Expiry": "",
                "Ticker": tkr,
                "Strike": "",
                "Entry_Price": "",
                "Live_Price": "",
                "Leg_Type": "COMBINED",
                "CallPut": "",
                "PnL": combined_pnl,
                "Investment": combined_inv,
                "Pct_PnL": pct(combined_pnl, combined_inv)
            })

        rows.append({
            "Expiry": "",
            "Ticker": "",
            "Strike": "",
            "Entry_Price": "",
            "Live_Price": "",
            "Leg_Type": "",
            "CallPut": "",
            "PnL": "",
            "Investment": "",
            "Pct_PnL": ""
        })

    return pd.DataFrame(rows, columns=cols)

# =========================
# Lead Candidates (for tomorrow, still from df_live)
# =========================

def make_lead_candidates(df_live):
    df = df_live.copy()

    if "Premium_Collected" in df.columns:
        df = df[df["Premium_Collected"] > 0]

    if "Max_Loss" in df.columns:
        df["Reward_Risk"] = df["Premium_Collected"] / df["Max_Loss"].replace(0, np.nan)
        df["Prob_Proxy"] = 1.0 - (df["Premium_Collected"] / df["Max_Loss"].replace(0, np.nan))
        df["Prob_Proxy"] = df["Prob_Proxy"].clip(lower=0.0, upper=1.0)
    else:
        df["Reward_Risk"] = np.nan
        df["Prob_Proxy"] = np.nan

    df_sorted = df.sort_values(
        ["Prob_Proxy", "Reward_Risk", "Premium_Collected"],
        ascending=[False, False, False]
    )

    rows = []
    cols = [
        "Expiry", "Ticker",
        "Strike", "Entry_Price_Close", "Live_Price",
        "Leg_Type", "CallPut",
        "Premium_Collected",
        "PnL_Close_View",
        "Investment", "Pct_PnL",
        "Reward_Risk", "Prob_Proxy"
    ]

    for exp, sub_exp in df_sorted.groupby("Expiry"):
        rows.append({
            "Expiry": f"Expiry: {exp}",
            "Ticker": "",
            "Strike": "",
            "Entry_Price_Close": "",
            "Live_Price": "",
            "Leg_Type": "",
            "CallPut": "",
            "Premium_Collected": "",
            "PnL_Close_View": "",
            "Investment": "",
            "Pct_PnL": "",
            "Reward_Risk": "",
            "Prob_Proxy": ""
        })

        for _, r in sub_exp.iterrows():
            tkr = r["Ticker"]
            short_strike = r["Short_Strike"]
            long_strike = r["Long_Strike"]
            short_entry = r["Short_Call_Close"]
            long_entry = r["Long_Call_Close"]
            short_live = r.get("Short_Price_Live", np.nan)
            long_live = r.get("Long_Price_Live", np.nan)
            premium = r.get("Premium_Collected", np.nan)
            combined_pnl = r.get("Combined_PnL_Live", np.nan)
            combined_inv = r.get("Combined_Investment", np.nan)
            short_pnl = r.get("Short_PnL_Live", np.nan)
            long_pnl = r.get("Long_PnL_Live", np.nan)
            short_inv = r.get("Short_Investment", 0.0)
            long_inv = r.get("Long_Investment", np.nan)
            reward_risk = r.get("Reward_Risk", np.nan)
            prob_proxy = r.get("Prob_Proxy", np.nan)

            def pct(pnl, inv):
                if isinstance(pnl, (int, float)) and isinstance(inv, (int, float)) and inv != 0:
                    return round(float(pnl) / float(inv), 4)
                return ""

            rows.append({
                "Expiry": "",
                "Ticker": tkr,
                "Strike": short_strike,
                "Entry_Price_Close": short_entry,
                "Live_Price": short_live,
                "Leg_Type": "SHORT",
                "CallPut": "CALL",
                "Premium_Collected": premium,
                "PnL_Close_View": short_pnl,
                "Investment": short_inv,
                "Pct_PnL": pct(short_pnl, short_inv),
                "Reward_Risk": reward_risk,
                "Prob_Proxy": prob_proxy
            })

            rows.append({
                "Expiry": "",
                "Ticker": tkr,
                "Strike": long_strike,
                "Entry_Price_Close": long_entry,
                "Live_Price": long_live,
                "Leg_Type": "LONG",
                "CallPut": "CALL",
                "Premium_Collected": "",
                "PnL_Close_View": long_pnl,
                "Investment": long_inv,
                "Pct_PnL": pct(long_pnl, long_inv),
                "Reward_Risk": "",
                "Prob_Proxy": ""
            })

            rows.append({
                "Expiry": "",
                "Ticker": tkr,
                "Strike": "",
                "Entry_Price_Close": "",
                "Live_Price": "",
                "Leg_Type": "COMBINED",
                "CallPut": "",
                "Premium_Collected": premium,
                "PnL_Close_View": combined_pnl,
                "Investment": combined_inv,
                "Pct_PnL": pct(combined_pnl, combined_inv),
                "Reward_Risk": reward_risk,
                "Prob_Proxy": prob_proxy
            })

        rows.append({
            "Expiry": "",
            "Ticker": "",
            "Strike": "",
            "Entry_Price_Close": "",
            "Live_Price": "",
            "Leg_Type": "",
            "CallPut": "",
            "Premium_Collected": "",
            "PnL_Close_View": "",
            "Investment": "",
            "Pct_PnL": "",
            "Reward_Risk": "",
            "Prob_Proxy": ""
        })

    return pd.DataFrame(rows, columns=cols)

# =========================
# MAIN
# =========================

if __name__ == "__main__":
    td_now = TD_NOW
    td_yest = TD_YEST
    print(f"[LIVE] Using trade_date_now = {td_now}, yesterday_from_folder = {td_yest}")

    # 1) Today's DB-based live prices for all spreads from yesterday consolidated file
    df_live, out_path = build_live_pnl_from_consolidated(td_now, td_yest)
    if df_live is None:
        raise SystemExit(1)

    # 2) Build EOD performance directly from df_live (previous-day consolidated file only)
    df_yday_eod_blocks = make_yday_lead_eod_blocks_from_live(df_live)

    # 3) Open-performance map (underlying open prices)
    ticker_open_map = get_underlying_intraday_map(df_live)

    # 4) Build tomorrow leads from today's live df_live
    df_leads = make_lead_candidates(df_live)

    with pd.ExcelWriter(
        out_path,
        engine="xlsxwriter",
        engine_kwargs={"options": {"nan_inf_to_errors": True}}
    ) as writer:
        # Live_Spreads
        df_live.to_excel(writer, sheet_name="Live_Spreads", index=False)

        workbook = writer.book

        fmt_header = workbook.add_format({"bold": True, "bg_color": "#D9D9D9", "border": 1})
        fmt_default = workbook.add_format({"border": 1, "num_format": "0.00"})
        fmt_percent = workbook.add_format({"border": 1, "num_format": "0.0%"})
        fmt_expiry_header = workbook.add_format({"bold": True, "bg_color": "#BDD7EE", "border": 1})

        fmt_light_green = workbook.add_format({"bg_color": "#C6EFCE", "font_color": "#006100", "border": 1, "num_format": "0.00"})
        fmt_strong_green = workbook.add_format({"bg_color": "#00B050", "font_color": "#FFFFFF", "border": 1, "num_format": "0.00"})
        fmt_light_red = workbook.add_format({"bg_color": "#F8CBAD", "font_color": "#9C0006", "border": 1, "num_format": "0.00"})
        fmt_strong_red = workbook.add_format({"bg_color": "#FF0000", "font_color": "#FFFFFF", "border": 1, "num_format": "0.00"})

        cols_exp = ["Expiry", "Ticker", "Strike", "Entry_Price", "Live_Price",
                    "Leg_Type", "CallPut", "PnL", "Investment", "Pct_PnL"]

        # Yday_lead_EOD_Performance
        sheet_eod = workbook.add_worksheet("Yday_lead_EOD_Performance")
        writer.sheets["Yday_lead_EOD_Performance"] = sheet_eod

        row_cursor = 0
        sheet_eod.write(row_cursor, 0, "Metric", fmt_header)
        sheet_eod.write(row_cursor, 1, "Value", fmt_header)
        row_cursor += 1

        if not df_live.empty:
            total_trades = len(df_live)
            net_pnl = float(df_live["Combined_PnL_Live"].sum())
            sheet_eod.write(row_cursor, 0, "Total_Spreads_Yday", fmt_default)
            sheet_eod.write(row_cursor, 1, total_trades, fmt_default)
            row_cursor += 1
            sheet_eod.write(row_cursor, 0, "Net_PnL_Today", fmt_default)
            sheet_eod.write(row_cursor, 1, net_pnl, fmt_default)
            row_cursor += 1
        else:
            sheet_eod.write(row_cursor, 0, "Total_Spreads_Yday", fmt_default)
            sheet_eod.write(row_cursor, 1, 0, fmt_default)
            row_cursor += 1

        row_cursor += 2
        sheet_eod.write(row_cursor, 0, "By Expiry and Ticker (3-Line Combo)", fmt_header)
        row_cursor += 1
        for c_idx, col_name in enumerate(cols_exp):
            sheet_eod.write(row_cursor, c_idx, col_name, fmt_header)
        row_cursor += 1
        data_start_eod = row_cursor

        if not df_yday_eod_blocks.empty:
            for _, r in df_yday_eod_blocks.iterrows():
                exp_val = r["Expiry"]
                tkr_val = r["Ticker"]
                strike_val = r["Strike"]
                entry_val = r["Entry_Price"]
                live_val = r["Live_Price"]
                leg_type_val = r["Leg_Type"]
                cp_val = r["CallPut"]
                pnl_val = r["PnL"]
                inv_val = r["Investment"]
                pct_val = r["Pct_PnL"]

                if isinstance(exp_val, str) and exp_val.startswith("Expiry:"):
                    sheet_eod.write(row_cursor, 0, exp_val, fmt_expiry_header)
                    for c_idx in range(1, len(cols_exp)):
                        sheet_eod.write(row_cursor, c_idx, "", fmt_expiry_header)
                    row_cursor += 1
                    continue

                sheet_eod.write(row_cursor, 0, exp_val if exp_val != "" else "", fmt_default)
                sheet_eod.write(row_cursor, 1, tkr_val if tkr_val != "" else "", fmt_default)

                if strike_val not in ("", None) and not pd.isna(strike_val):
                    sheet_eod.write(row_cursor, 2, float(strike_val), fmt_default)
                else:
                    sheet_eod.write(row_cursor, 2, "", fmt_default)

                if entry_val not in ("", None) and not pd.isna(entry_val):
                    sheet_eod.write(row_cursor, 3, float(entry_val), fmt_default)
                else:
                    sheet_eod.write(row_cursor, 3, "", fmt_default)

                if live_val not in ("", None) and not pd.isna(live_val):
                    sheet_eod.write(row_cursor, 4, float(live_val), fmt_default)
                else:
                    sheet_eod.write(row_cursor, 4, "", fmt_default)

                sheet_eod.write(row_cursor, 5, leg_type_val if leg_type_val != "" else "", fmt_default)
                sheet_eod.write(row_cursor, 6, cp_val if cp_val != "" else "", fmt_default)

                if pnl_val not in ("", None) and not pd.isna(pnl_val):
                    sheet_eod.write(row_cursor, 7, float(pnl_val), fmt_default)
                else:
                    sheet_eod.write(row_cursor, 7, "", fmt_default)

                if inv_val not in ("", None) and not pd.isna(inv_val):
                    sheet_eod.write(row_cursor, 8, float(inv_val), fmt_default)
                else:
                    sheet_eod.write(row_cursor, 8, "", fmt_default)

                if isinstance(pct_val, (int, float)) and not pd.isna(pct_val):
                    sheet_eod.write(row_cursor, 9, float(pct_val), fmt_percent)
                else:
                    sheet_eod.write(row_cursor, 9, "", fmt_default)

                row_cursor += 1

        data_end_eod = row_cursor - 1

        if data_start_eod <= data_end_eod:
            for r in range(data_start_eod, data_end_eod + 1):
                excel_row = r + 1
                sheet_eod.conditional_format(r, 0, r, len(cols_exp) - 1, {
                    "type": "formula",
                    "criteria": f'=AND($F{excel_row}="COMBINED",$H{excel_row}>={PSTRONG})',
                    "format": fmt_strong_green
                })
                sheet_eod.conditional_format(r, 0, r, len(cols_exp) - 1, {
                    "type": "formula",
                    "criteria": f'=AND($F{excel_row}="COMBINED",$H{excel_row}>0,$H{excel_row}<{PSTRONG})',
                    "format": fmt_light_green
                })
                sheet_eod.conditional_format(r, 0, r, len(cols_exp) - 1, {
                    "type": "formula",
                    "criteria": f'=AND($F{excel_row}="COMBINED",$H{excel_row}<=-{PSTRONG})',
                    "format": fmt_strong_red
                })
                sheet_eod.conditional_format(r, 0, r, len(cols_exp) - 1, {
                    "type": "formula",
                    "criteria": f'=AND($F{excel_row}="COMBINED",$H{excel_row}<0,$H{excel_row}>-{PSTRONG})',
                    "format": fmt_light_red
                })

        # Yday_lead_Open_Performance placeholder
        sheet_open = workbook.add_worksheet("Yday_lead_Open_Performance")
        writer.sheets["Yday_lead_Open_Performance"] = sheet_open
        sheet_open.write(0, 0, "To implement: open-entry approximation based on ticker_open_map", fmt_header)

        # Lead_Candidates (for tomorrow) from df_live, not previous Lead tab
        if df_leads is not None and not df_leads.empty:
            sheet_leads = workbook.add_worksheet("Lead_Candidates")
            writer.sheets["Lead_Candidates"] = sheet_leads

            for c_idx, col in enumerate(df_leads.columns):
                sheet_leads.write(0, c_idx, col, fmt_header)

            for r_idx, (_, r) in enumerate(df_leads.iterrows(), start=1):
                for c_idx, col in enumerate(df_leads.columns):
                    val = r[col]
                    if isinstance(val, (int, float)):
                        if pd.isna(val):
                            sheet_leads.write(r_idx, c_idx, "", fmt_default)
                        else:
                            if col == "Pct_PnL":
                                sheet_leads.write(r_idx, c_idx, float(val), fmt_percent)
                            else:
                                sheet_leads.write(r_idx, c_idx, float(val), fmt_default)
                    else:
                        sheet_leads.write(r_idx, c_idx, val)

            n_rows, n_cols = df_leads.shape
            if n_rows > 0 and "Leg_Type" in df_leads.columns and "Prob_Proxy" in df_leads.columns:
                leg_col_idx = df_leads.columns.get_loc("Leg_Type")
                prob_col_idx = df_leads.columns.get_loc("Prob_Proxy")
                leg_col_letter = chr(ord("A") + leg_col_idx)
                prob_col_letter = chr(ord("A") + prob_col_idx)

                sheet_leads.conditional_format(1, 0, n_rows, n_cols - 1, {
                    "type": "formula",
                    "criteria": f'=AND(${leg_col_letter}2="COMBINED",${prob_col_letter}2>={PROB_STRONG})',
                    "format": fmt_strong_green
                })
                sheet_leads.conditional_format(1, 0, n_rows, n_cols - 1, {
                    "type": "formula",
                    "criteria": f'=AND(${leg_col_letter}2="COMBINED",${prob_col_letter}2>={PROB_LIGHT},${prob_col_letter}2<{PROB_STRONG})',
                    "format": fmt_light_green
                })

    print(f"[LIVE] Wrote Live_Spreads, Yday_lead_EOD_Performance, Yday_lead_Open_Performance, Lead_Candidates to {out_path}")
