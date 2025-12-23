import os
from datetime import datetime, time as dt_time
import numpy as np
import pandas as pd
import yfinance as yf


# =========================
# CONFIG
# =========================


DATA_DIR = r"C:\Users\srini\Options_chain_data"
BASE_OUT_DIR = os.path.join(DATA_DIR, "NYSE_DATA", "US_CHARTS")

TD_NOW = "22Dec2025"  # <-- change to your actual date, e.g. "23Dec2025"

MIN_PREMIUM_COLLECTED = 0.0
MAX_MAX_LOSS = None

PSTRONG = 200.0  # threshold for strong green/red per spread PnL


# =========================
# Helpers
# =========================


def expiry_to_yyyy_mm_dd(exp_str):
    s = str(exp_str)
    for fmt in ["%d%b%Y", "%Y-%m-%d", "%m/%d/%Y"]:
        try:
            dt = datetime.strptime(s, fmt)
            return dt.strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def get_live_option_price(ticker, expiry_yyyy_mm_dd, strike, call_put="call"):
    """Live-ish option price via option_chain (mid of bid/ask or last)."""
    try:
        t = yf.Ticker(ticker)
        opt = t.option_chain(expiry_yyyy_mm_dd)
    except Exception:
        return np.nan

    df = opt.calls if call_put.lower() == "call" else opt.puts
    if df.empty:
        return np.nan

    df = df.copy()
    df["strike"] = df["strike"].astype(float)
    row = df.iloc[(df["strike"] - float(strike)).abs().argsort()[:1]]
    if row.empty:
        return np.nan

    row = row.iloc[0]
    bid = row.get("bid", np.nan)
    ask = row.get("ask", np.nan)
    last = row.get("lastPrice", np.nan)

    if not np.isnan(bid) and not np.isnan(ask) and (bid > 0 or ask > 0):
        return float((bid + ask) / 2.0)
    if not np.isnan(last):
        return float(last)
    return np.nan


def get_underlying_intraday_prices(ticker):
    """
    Underlying today open and ~9:45 using 1m intraday from Yahoo.
    Open: first bar Open, 9:45: bar Close closest to 09:45.[web:102][web:88]
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
        abs((dt_obj.time().hour - target_time.hour) * 60 +
            (dt_obj.time().minute - target_time.minute))
        for dt_obj in times
    ]
    idx_min = int(np.argmin(diffs))
    price_945 = float(hist["Close"].iloc[idx_min])

    return open_price, price_945


# =========================
# Live PnL from consolidated
# =========================


def build_live_pnl_from_consolidated(td_now):
    day_folder = os.path.join(BASE_OUT_DIR, td_now)
    cons_file = os.path.join(day_folder, f"{td_now}_Consolidated_Spreads.xlsx")

    if not os.path.exists(cons_file):
        print(f"[LIVE] Consolidated file not found: {cons_file}")
        return None, None

    df = pd.read_excel(cons_file)
    if df.empty:
        print("[LIVE] Consolidated file is empty.")
        return None, None

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

    # per-leg live PnL with qty = 1 by default
    df_live["Short_PnL_Live"] = np.nan
    df_live["Long_PnL_Live"] = np.nan
    df_live["Combined_PnL_Live"] = np.nan
    df_live["Short_Investment"] = 0.0
    df_live["Long_Investment"] = np.nan
    df_live["Combined_Investment"] = np.nan

    # combined PnL if entered at open (approx) to fill later
    df_live["Combined_PnL_Open_Approx"] = np.nan

    print(f"[LIVE] Computing live PnL for {len(df_live)} spreads from {cons_file} ...")

    for idx, row in df_live.iterrows():
        ticker = row["Ticker"]
        expiry_raw = row["Expiry"]
        short_strike = row["Short_Strike"]
        long_strike = row["Long_Strike"]
        entry_premium = row["Premium_Collected"]

        exp_yyyy_mm_dd = expiry_to_yyyy_mm_dd(expiry_raw)
        if exp_yyyy_mm_dd is None:
            continue

        try:
            short_live = get_live_option_price(ticker, exp_yyyy_mm_dd, short_strike, "call")
            long_live = get_live_option_price(ticker, exp_yyyy_mm_dd, long_strike, "call")
        except Exception:
            continue

        if np.isnan(short_live) or np.isnan(long_live) or np.isnan(entry_premium):
            continue

        multiplier = 100.0
        spread_value_live = (short_live - long_live) * multiplier
        pnl_live = entry_premium - spread_value_live

        # qty = 1 for each leg; entry = yesterday EOD option close
        short_entry = row["Short_Call_Close"]
        long_entry = row["Long_Call_Close"]

        short_pnl = (short_entry - short_live) * multiplier
        long_pnl = (long_live - long_entry) * multiplier
        combined_pnl = short_pnl + long_pnl

        short_inv = 0.0  # short is credit leg
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

    out_file = os.path.join(day_folder, f"{td_now}_Consolidated_Spreads_Live.xlsx")
    return df_live, out_file


# =========================
# Entry comparison (underlying-based)
# =========================


def build_entry_comparison(df_live):
    """
    Approximate effect of entering at close vs open vs 9:45 using underlying move.
    Also fills Combined_PnL_Open_Approx in df_live (for current_account_open).
    """
    if df_live is None or df_live.empty:
        return pd.DataFrame()
    if "Underlying_Close" not in df_live.columns:
        return pd.DataFrame()

    tickers = sorted(df_live["Ticker"].unique())
    open_prices = {}
    p945_prices = {}

    print("[LIVE] Fetching underlying intraday for open/9:45 ...")
    for tkr in tickers:
        op, p945 = get_underlying_intraday_prices(tkr)
        open_prices[tkr] = op
        p945_prices[tkr] = p945

    rows = []
    multiplier = 100.0

    for idx, r in df_live.iterrows():
        tkr = r["Ticker"]
        y_close = r.get("Underlying_Close", np.nan)
        op = open_prices.get(tkr, np.nan)
        p945 = p945_prices.get(tkr, np.nan)
        pnl_close_live = r.get("PnL_Live", np.nan)

        if np.isnan(y_close) or np.isnan(op) or np.isnan(p945) or np.isnan(pnl_close_live):
            continue

        move_open = op - y_close
        move_945 = p945 - y_close

        pnl_shift_open = move_open * multiplier
        pnl_shift_945 = move_945 * multiplier

        pnl_open_live = pnl_close_live - pnl_shift_open
        pnl_945_live = pnl_close_live - pnl_shift_945

        combined_close = r.get("Combined_PnL_Live", np.nan)
        if not np.isnan(combined_close):
            combined_open = combined_close - pnl_shift_open
            df_live.at[idx, "Combined_PnL_Open_Approx"] = round(float(combined_open), 2)

        rows.append({
            "Ticker": tkr,
            "Expiry": r["Expiry"],
            "Short_Strike": r["Short_Strike"],
            "Long_Strike": r["Long_Strike"],
            "Underlying_Close": round(float(y_close), 2),
            "Underlying_Open": round(float(op), 2),
            "Underlying_945": round(float(p945), 2),
            "Move_Open_vs_Close": round(float(move_open), 2),
            "Move_945_vs_Close": round(float(move_945), 2),
            "PnL_Live_Entry_Close": round(float(pnl_close_live), 2),
            "PnL_Live_Entry_Open_Approx": round(float(pnl_open_live), 2),
            "PnL_Live_Entry_945_Approx": round(float(pnl_945_live), 2),
        })

    return pd.DataFrame(rows)


# =========================
# Summary tables
# =========================


def make_summary_tables(df_live, td_now, use_open=False):
    df = df_live.copy()

    if MIN_PREMIUM_COLLECTED is not None and MIN_PREMIUM_COLLECTED > 0:
        df = df[df["Premium_Collected"] >= MIN_PREMIUM_COLLECTED]
    if MAX_MAX_LOSS is not None and MAX_MAX_LOSS > 0 and "Max_Loss" in df.columns:
        df = df[df["Max_Loss"] <= MAX_MAX_LOSS]

    if df.empty:
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), pd.DataFrame()

    if "Combined_Investment" in df.columns:
        investment_series = df["Combined_Investment"].clip(lower=0)
    elif "Max_Loss" in df.columns:
        investment_series = df["Max_Loss"].clip(lower=0)
    else:
        investment_series = df["Premium_Collected"].abs()

    if use_open and "Combined_PnL_Open_Approx" in df.columns:
        pnl_series = df["Combined_PnL_Open_Approx"]
    else:
        pnl_series = df["Combined_PnL_Live"] if "Combined_PnL_Live" in df.columns else df["PnL_Live"]

    pos_pnl = pnl_series[pnl_series > 0].sum()
    neg_pnl = pnl_series[pnl_series < 0].sum()

    total_investment = investment_series.sum()
    total_loss = -neg_pnl

    total_trades = len(df)
    win_trades = (pnl_series > 0).sum()
    loss_trades = (pnl_series < 0).sum()

    if total_investment > 0:
        total_return_pct = (pos_pnl + neg_pnl) / total_investment * 100.0
    else:
        total_return_pct = np.nan

    label = "Open_Entry" if use_open else "EOD_Entry"
    summary_rows = [
        ["Metric", "Value"],
        ["Trade_Date_Now", td_now],
        ["PnL_View", label],
        ["Total_Trades", total_trades],
        ["Winning_Trades", win_trades],
        ["Losing_Trades", loss_trades],
        ["Total_PnL_Positive", round(float(pos_pnl), 2)],
        ["Total_PnL_Negative", round(float(neg_pnl), 2)],
        ["Total_Investment", round(float(total_investment), 2)],
        ["Total_Loss", round(float(total_loss), 2)],
        ["Net_PnL", round(float(pos_pnl + neg_pnl), 2)],
        ["Net_PnL_%_of_Investment", round(float(total_return_pct), 2) if not np.isnan(total_return_pct) else ""],
    ]
    df_summary = pd.DataFrame(summary_rows[1:], columns=summary_rows[0])

    combo_cols = [
        "Ticker", "Expiry",
        "Short_Strike", "Long_Strike",
        "Short_Call_Close", "Long_Call_Close",
        "Short_PnL_Live", "Long_PnL_Live", "Combined_PnL_Live",
        "Short_Investment", "Long_Investment", "Combined_Investment",
        "Short_Price_Live", "Long_Price_Live",
        "Combined_PnL_Open_Approx"
    ]
    for c in combo_cols:
        if c not in df.columns:
            df[c] = np.nan

    df_combo = df[combo_cols].copy()

    grp_ticker = pd.DataFrame()
    grp_exp_ticker = pd.DataFrame()

    return df_summary, grp_ticker, grp_exp_ticker, df_combo


def make_expiry_and_ticker_combo_blocks(df_combo, use_open=False):
    """
    Build 3-line combo blocks grouped by Expiry then Ticker.
    Adds % PnL column.
    """
    rows = []
    cols = [
        "Expiry", "Ticker",
        "Strike", "Entry_Price", "Live_Price",
        "Leg_Type", "CallPut",
        "PnL", "Investment", "Pct_PnL"
    ]

    df_sorted = df_combo.sort_values(["Expiry", "Ticker", "Short_Strike", "Long_Strike"])

    for exp, sub_exp in df_sorted.groupby("Expiry"):
        rows.append({
            "Expiry": f"Expiry: {exp}",
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

        for _, r in sub_exp.iterrows():
            tkr = r["Ticker"]
            short_strike = r["Short_Strike"]
            long_strike = r["Long_Strike"]
            short_entry = r["Short_Call_Close"]
            long_entry = r["Long_Call_Close"]
            short_pnl = r["Short_PnL_Live"]
            long_pnl = r["Long_PnL_Live"]
            combined_close = r["Combined_PnL_Live"]
            combined_open = r["Combined_PnL_Open_Approx"]
            short_inv = r["Short_Investment"]
            long_inv = r["Long_Investment"]
            combined_inv = r["Combined_Investment"]

            combined_pnl_used = combined_open if use_open and not np.isnan(combined_open) else combined_close

            short_live = r["Short_Price_Live"]
            long_live = r["Long_Price_Live"]

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
                "PnL": combined_pnl_used,
                "Investment": combined_inv,
                "Pct_PnL": pct(combined_pnl_used, combined_inv)
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


def make_by_ticker_combo_blocks(df_combo):
    rows = []
    cols = [
        "Ticker",
        "Expiry",
        "Strike", "Entry_Price", "Live_Price",
        "Leg_Type", "CallPut",
        "PnL_Close", "PnL_Open_Approx", "Investment"
    ]

    df_sorted = df_combo.sort_values(["Ticker", "Expiry", "Short_Strike", "Long_Strike"])

    for tkr, sub_tkr in df_sorted.groupby("Ticker"):
        rows.append({
            "Ticker": f"Ticker: {tkr}",
            "Expiry": "",
            "Strike": "",
            "Entry_Price": "",
            "Live_Price": "",
            "Leg_Type": "",
            "CallPut": "",
            "PnL_Close": "",
            "PnL_Open_Approx": "",
            "Investment": ""
        })

        for _, r in sub_tkr.iterrows():
            exp = r["Expiry"]
            short_strike = r["Short_Strike"]
            long_strike = r["Long_Strike"]
            short_entry = r["Short_Call_Close"]
            long_entry = r["Long_Call_Close"]
            short_pnl = r["Short_PnL_Live"]
            long_pnl = r["Long_PnL_Live"]
            combined_pnl = r["Combined_PnL_Live"]
            combined_pnl_open = r["Combined_PnL_Open_Approx"]
            short_inv = r["Short_Investment"]
            long_inv = r["Long_Investment"]
            combined_inv = r["Combined_Investment"]

            short_live = r["Short_Price_Live"]
            long_live = r["Long_Price_Live"]

            rows.append({
                "Ticker": tkr,
                "Expiry": exp,
                "Strike": short_strike,
                "Entry_Price": short_entry,
                "Live_Price": short_live,
                "Leg_Type": "SHORT",
                "CallPut": "CALL",
                "PnL_Close": short_pnl,
                "PnL_Open_Approx": "",
                "Investment": short_inv
            })

            rows.append({
                "Ticker": tkr,
                "Expiry": exp,
                "Strike": long_strike,
                "Entry_Price": long_entry,
                "Live_Price": long_live,
                "Leg_Type": "LONG",
                "CallPut": "CALL",
                "PnL_Close": long_pnl,
                "PnL_Open_Approx": "",
                "Investment": long_inv
            })

            rows.append({
                "Ticker": tkr,
                "Expiry": exp,
                "Strike": "",
                "Entry_Price": "",
                "Live_Price": "",
                "Leg_Type": "COMBINED",
                "CallPut": "",
                "PnL_Close": combined_pnl,
                "PnL_Open_Approx": combined_pnl_open,
                "Investment": combined_inv
            })

        rows.append({
            "Ticker": "",
            "Expiry": "",
            "Strike": "",
            "Entry_Price": "",
            "Live_Price": "",
            "Leg_Type": "",
            "CallPut": "",
            "PnL_Close": "",
            "PnL_Open_Approx": "",
            "Investment": ""
        })

    return pd.DataFrame(rows, columns=cols)


# =========================
# MAIN
# =========================


if __name__ == "__main__":
    td_now = TD_NOW
    print(f"[LIVE] Using trade_date_now = {td_now}")
    df_live, out_path = build_live_pnl_from_consolidated(td_now)
    if df_live is None:
        raise SystemExit(1)

    df_entry_cmp = build_entry_comparison(df_live)

    # map ticker -> underlying open for current_account_open Entry_Price
    ticker_open_map = {}
    if not df_entry_cmp.empty:
        tmp = df_entry_cmp.drop_duplicates(subset=["Ticker"])[["Ticker", "Underlying_Open"]]
        ticker_open_map = dict(zip(tmp["Ticker"], tmp["Underlying_Open"]))

    # EOD view
    df_summary_eod, _, _, df_combo_eod = make_summary_tables(df_live, td_now, use_open=False)
    df_exp_blocks_eod = make_expiry_and_ticker_combo_blocks(df_combo_eod, use_open=False) if not df_combo_eod.empty else pd.DataFrame()
    df_ticker_blocks_eod = make_by_ticker_combo_blocks(df_combo_eod) if not df_combo_eod.empty else pd.DataFrame()

    # Open-entry view
    df_summary_open, _, _, df_combo_open = make_summary_tables(df_live, td_now, use_open=True)
    df_exp_blocks_open = make_expiry_and_ticker_combo_blocks(df_combo_open, use_open=True) if not df_combo_open.empty else pd.DataFrame()
    df_ticker_blocks_open = make_by_ticker_combo_blocks(df_combo_open) if not df_combo_open.empty else pd.DataFrame()

    with pd.ExcelWriter(out_path, engine="xlsxwriter") as writer:
        df_live.to_excel(writer, sheet_name="Live_Spreads", index=False)

        workbook = writer.book

        fmt_header = workbook.add_format({"bold": True, "bg_color": "#D9D9D9", "border": 1})
        fmt_default = workbook.add_format({"border": 1, "num_format": "0.00"})
        fmt_default_int = workbook.add_format({"border": 1, "num_format": "0"})
        fmt_expiry_header = workbook.add_format({"bold": True, "bg_color": "#BDD7EE", "border": 1})
        fmt_ticker_header = workbook.add_format({"bold": True, "bg_color": "#FFE699", "border": 1})
        fmt_percent = workbook.add_format({"border": 1, "num_format": "0.0%"})

        fmt_light_green = workbook.add_format({"bg_color": "#C6EFCE", "font_color": "#006100", "border": 1, "num_format": "0.00"})
        fmt_strong_green = workbook.add_format({"bg_color": "#00B050", "font_color": "#FFFFFF", "border": 1, "num_format": "0.00"})
        fmt_light_red = workbook.add_format({"bg_color": "#F8CBAD", "font_color": "#9C0006", "border": 1, "num_format": "0.00"})
        fmt_strong_red = workbook.add_format({"bg_color": "#FF0000", "font_color": "#FFFFFF", "border": 1, "num_format": "0.00"})

        # ---------- Account_stat_EOD ----------
        sheet_eod = workbook.add_worksheet("Account_stat_EOD")
        writer.sheets["Account_stat_EOD"] = sheet_eod

        row_cursor = 0

        sheet_eod.write(row_cursor, 0, "Metric", fmt_header)
        sheet_eod.write(row_cursor, 1, "Value", fmt_header)
        row_cursor += 1
        for _, r in df_summary_eod.iterrows():
            sheet_eod.write(row_cursor, 0, r["Metric"], fmt_default)
            val = r["Value"]
            if isinstance(val, (int, float)) and str(val) != "":
                sheet_eod.write(row_cursor, 1, float(val), fmt_default)
            else:
                sheet_eod.write(row_cursor, 1, val)
            row_cursor += 1

        row_cursor += 2

        sheet_eod.write(row_cursor, 0, "By Expiry and Ticker (3-Line Combo)", fmt_header)
        row_cursor += 1

        cols_exp = ["Expiry", "Ticker", "Strike", "Entry_Price", "Live_Price",
                    "Leg_Type", "CallPut", "PnL", "Investment", "Pct_PnL"]
        for c_idx, col_name in enumerate(cols_exp):
            sheet_eod.write(row_cursor, c_idx, col_name, fmt_header)
        row_cursor += 1
        exp_data_start_eod = row_cursor

        if not df_exp_blocks_eod.empty:
            for _, r in df_exp_blocks_eod.iterrows():
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

                if (exp_val == "" or pd.isna(exp_val)) and (tkr_val == "" or pd.isna(tkr_val)) and leg_type_val == "":
                    row_cursor += 1
                    continue

                sheet_eod.write(row_cursor, 0, exp_val if exp_val != "" else "", fmt_default)
                sheet_eod.write(row_cursor, 1, tkr_val if tkr_val != "" else "", fmt_default)

                if strike_val != "" and not pd.isna(strike_val):
                    sheet_eod.write(row_cursor, 2, float(strike_val), fmt_default)
                else:
                    sheet_eod.write(row_cursor, 2, "", fmt_default)

                if entry_val != "" and not pd.isna(entry_val):
                    sheet_eod.write(row_cursor, 3, float(entry_val), fmt_default)
                else:
                    sheet_eod.write(row_cursor, 3, "", fmt_default)

                if live_val != "" and not pd.isna(live_val):
                    sheet_eod.write(row_cursor, 4, float(live_val), fmt_default)
                else:
                    sheet_eod.write(row_cursor, 4, "", fmt_default)

                sheet_eod.write(row_cursor, 5, leg_type_val if leg_type_val != "" else "", fmt_default)
                sheet_eod.write(row_cursor, 6, cp_val if cp_val != "" else "", fmt_default)

                if pnl_val != "" and not pd.isna(pnl_val):
                    sheet_eod.write(row_cursor, 7, float(pnl_val), fmt_default)
                else:
                    sheet_eod.write(row_cursor, 7, "", fmt_default)

                if inv_val != "" and not pd.isna(inv_val):
                    sheet_eod.write(row_cursor, 8, float(inv_val), fmt_default)
                else:
                    sheet_eod.write(row_cursor, 8, "", fmt_default)

                if isinstance(pct_val, (int, float)) and pct_val != "":
                    sheet_eod.write(row_cursor, 9, float(pct_val), fmt_percent)
                else:
                    sheet_eod.write(row_cursor, 9, "", fmt_default)

                row_cursor += 1

        exp_data_end_eod = row_cursor - 1

        row_cursor += 2

        sheet_eod.write(row_cursor, 0, "By Ticker (3-Line Combo)", fmt_header)
        row_cursor += 1

        cols_ticker = ["Ticker", "Expiry", "Strike", "Entry_Price", "Live_Price",
                       "Leg_Type", "CallPut", "PnL_Close", "PnL_Open_Approx", "Investment"]
        for c_idx, col_name in enumerate(cols_ticker):
            sheet_eod.write(row_cursor, c_idx, col_name, fmt_header)
        row_cursor += 1
        ticker_data_start_eod = row_cursor

        if not df_ticker_blocks_eod.empty:
            for _, r in df_ticker_blocks_eod.iterrows():
                tkr_val = r["Ticker"]
                exp_val = r["Expiry"]
                strike_val = r["Strike"]
                entry_val = r["Entry_Price"]
                live_val = r["Live_Price"]
                leg_type_val = r["Leg_Type"]
                cp_val = r["CallPut"]
                pnl_close_val = r["PnL_Close"]
                pnl_open_val = r["PnL_Open_Approx"]
                inv_val = r["Investment"]

                if isinstance(tkr_val, str) and tkr_val.startswith("Ticker:"):
                    sheet_eod.write(row_cursor, 0, tkr_val, fmt_ticker_header)
                    for c_idx in range(1, len(cols_ticker)):
                        sheet_eod.write(row_cursor, c_idx, "", fmt_ticker_header)
                    row_cursor += 1
                    continue

                if (tkr_val == "" or pd.isna(tkr_val)) and (exp_val == "" or pd.isna(exp_val)) and leg_type_val == "":
                    row_cursor += 1
                    continue

                sheet_eod.write(row_cursor, 0, tkr_val if tkr_val != "" else "", fmt_default)
                sheet_eod.write(row_cursor, 1, exp_val if exp_val != "" else "", fmt_default)

                if strike_val != "" and not pd.isna(strike_val):
                    sheet_eod.write(row_cursor, 2, float(strike_val), fmt_default)
                else:
                    sheet_eod.write(row_cursor, 2, "", fmt_default)

                if entry_val != "" and not pd.isna(entry_val):
                    sheet_eod.write(row_cursor, 3, float(entry_val), fmt_default)
                else:
                    sheet_eod.write(row_cursor, 3, "", fmt_default)

                if live_val != "" and not pd.isna(live_val):
                    sheet_eod.write(row_cursor, 4, float(live_val), fmt_default)
                else:
                    sheet_eod.write(row_cursor, 4, "", fmt_default)

                sheet_eod.write(row_cursor, 5, leg_type_val if leg_type_val != "" else "", fmt_default)
                sheet_eod.write(row_cursor, 6, cp_val if cp_val != "" else "", fmt_default)

                if pnl_close_val != "" and not pd.isna(pnl_close_val):
                    sheet_eod.write(row_cursor, 7, float(pnl_close_val), fmt_default)
                else:
                    sheet_eod.write(row_cursor, 7, "", fmt_default)

                if pnl_open_val != "" and not pd.isna(pnl_open_val):
                    sheet_eod.write(row_cursor, 8, float(pnl_open_val), fmt_default)
                else:
                    sheet_eod.write(row_cursor, 8, "", fmt_default)

                if inv_val != "" and not pd.isna(inv_val):
                    sheet_eod.write(row_cursor, 9, float(inv_val), fmt_default)
                else:
                    sheet_eod.write(row_cursor, 9, "", fmt_default)

                row_cursor += 1

        ticker_data_end_eod = row_cursor - 1

        if ticker_data_start_eod <= ticker_data_end_eod:
            for r in range(ticker_data_start_eod, ticker_data_end_eod + 1):
                excel_row = r + 1
                formula_strong_green = f'=AND($F{excel_row}="COMBINED",$H{excel_row}>={PSTRONG})'
                formula_light_green = f'=AND($F{excel_row}="COMBINED",$H{excel_row}>0,$H{excel_row}<{PSTRONG})'
                formula_strong_red = f'=AND($F{excel_row}="COMBINED",$H{excel_row}<=-{PSTRONG})'
                formula_light_red = f'=AND($F{excel_row}="COMBINED",$H{excel_row}<0,$H{excel_row}>-{PSTRONG})'

                sheet_eod.conditional_format(r, 0, r, len(cols_ticker) - 1, {
                    "type": "formula",
                    "criteria": formula_strong_green,
                    "format": fmt_strong_green
                })
                sheet_eod.conditional_format(r, 0, r, len(cols_ticker) - 1, {
                    "type": "formula",
                    "criteria": formula_light_green,
                    "format": fmt_light_green
                })
                sheet_eod.conditional_format(r, 0, r, len(cols_ticker) - 1, {
                    "type": "formula",
                    "criteria": formula_strong_red,
                    "format": fmt_strong_red
                })
                sheet_eod.conditional_format(r, 0, r, len(cols_ticker) - 1, {
                    "type": "formula",
                    "criteria": formula_light_red,
                    "format": fmt_light_red
                })

        if exp_data_start_eod <= exp_data_end_eod:
            for r in range(exp_data_start_eod, exp_data_end_eod + 1):
                excel_row = r + 1
                formula_strong_green = f'=AND($F{excel_row}="COMBINED",$H{excel_row}>={PSTRONG})'
                formula_light_green = f'=AND($F{excel_row}="COMBINED",$H{excel_row}>0,$H{excel_row}<{PSTRONG})'
                formula_strong_red = f'=AND($F{excel_row}="COMBINED",$H{excel_row}<=-{PSTRONG})'
                formula_light_red = f'=AND($F{excel_row}="COMBINED",$H{excel_row}<0,$H{excel_row}>-{PSTRONG})'

                sheet_eod.conditional_format(r, 0, r, len(cols_exp) - 1, {
                    "type": "formula",
                    "criteria": formula_strong_green,
                    "format": fmt_strong_green
                })
                sheet_eod.conditional_format(r, 0, r, len(cols_exp) - 1, {
                    "type": "formula",
                    "criteria": formula_light_green,
                    "format": fmt_light_green
                })
                sheet_eod.conditional_format(r, 0, r, len(cols_exp) - 1, {
                    "type": "formula",
                    "criteria": formula_strong_red,
                    "format": fmt_strong_red
                })
                sheet_eod.conditional_format(r, 0, r, len(cols_exp) - 1, {
                    "type": "formula",
                    "criteria": formula_light_red,
                    "format": fmt_light_red
                })

        # ---------- current_account_open ----------
        sheet_open = workbook.add_worksheet("current_account_open")
        writer.sheets["current_account_open"] = sheet_open

        row_cursor = 0

        sheet_open.write(row_cursor, 0, "Metric", fmt_header)
        sheet_open.write(row_cursor, 1, "Value", fmt_header)
        row_cursor += 1
        for _, r in df_summary_open.iterrows():
            sheet_open.write(row_cursor, 0, r["Metric"], fmt_default)
            val = r["Value"]
            if isinstance(val, (int, float)) and str(val) != "":
                sheet_open.write(row_cursor, 1, float(val), fmt_default)
            else:
                sheet_open.write(row_cursor, 1, val)
            row_cursor += 1

        row_cursor += 2

        sheet_open.write(row_cursor, 0, "By Expiry and Ticker (3-Line Combo)", fmt_header)
        row_cursor += 1

        for c_idx, col_name in enumerate(cols_exp):
            sheet_open.write(row_cursor, c_idx, col_name, fmt_header)
        row_cursor += 1
        exp_data_start_open = row_cursor

        if not df_exp_blocks_open.empty:
            for _, r in df_exp_blocks_open.iterrows():
                exp_val = r["Expiry"]
                tkr_val = r["Ticker"]
                strike_val = r["Strike"]
                entry_val_original = r["Entry_Price"]
                live_val = r["Live_Price"]
                leg_type_val = r["Leg_Type"]
                cp_val = r["CallPut"]
                pnl_val = r["PnL"]
                inv_val = r["Investment"]
                pct_val = r["Pct_PnL"]

                if isinstance(exp_val, str) and exp_val.startswith("Expiry:"):
                    sheet_open.write(row_cursor, 0, exp_val, fmt_expiry_header)
                    for c_idx in range(1, len(cols_exp)):
                        sheet_open.write(row_cursor, c_idx, "", fmt_expiry_header)
                    row_cursor += 1
                    continue

                if (exp_val == "" or pd.isna(exp_val)) and (tkr_val == "" or pd.isna(tkr_val)) and leg_type_val == "":
                    row_cursor += 1
                    continue

                sheet_open.write(row_cursor, 0, exp_val if exp_val != "" else "", fmt_default)
                sheet_open.write(row_cursor, 1, tkr_val if tkr_val != "" else "", fmt_default)

                if strike_val != "" and not pd.isna(strike_val):
                    sheet_open.write(row_cursor, 2, float(strike_val), fmt_default)
                else:
                    sheet_open.write(row_cursor, 2, "", fmt_default)

                # use today's underlying open as Entry_Price for open view
                open_val = ticker_open_map.get(tkr_val, np.nan)
                if not pd.isna(open_val):
                    sheet_open.write(row_cursor, 3, float(open_val), fmt_default)
                elif entry_val_original != "" and not pd.isna(entry_val_original):
                    sheet_open.write(row_cursor, 3, float(entry_val_original), fmt_default)
                else:
                    sheet_open.write(row_cursor, 3, "", fmt_default)

                if live_val != "" and not pd.isna(live_val):
                    sheet_open.write(row_cursor, 4, float(live_val), fmt_default)
                else:
                    sheet_open.write(row_cursor, 4, "", fmt_default)

                sheet_open.write(row_cursor, 5, leg_type_val if leg_type_val != "" else "", fmt_default)
                sheet_open.write(row_cursor, 6, cp_val if cp_val != "" else "", fmt_default)

                if pnl_val != "" and not pd.isna(pnl_val):
                    sheet_open.write(row_cursor, 7, float(pnl_val), fmt_default)
                else:
                    sheet_open.write(row_cursor, 7, "", fmt_default)

                if inv_val != "" and not pd.isna(inv_val):
                    sheet_open.write(row_cursor, 8, float(inv_val), fmt_default)
                else:
                    sheet_open.write(row_cursor, 8, "", fmt_default)

                if isinstance(pct_val, (int, float)) and pct_val != "":
                    sheet_open.write(row_cursor, 9, float(pct_val), fmt_percent)
                else:
                    sheet_open.write(row_cursor, 9, "", fmt_default)

                row_cursor += 1

        exp_data_end_open = row_cursor - 1

        row_cursor += 2

        sheet_open.write(row_cursor, 0, "By Ticker (3-Line Combo)", fmt_header)
        row_cursor += 1

        for c_idx, col_name in enumerate(cols_ticker):
            sheet_open.write(row_cursor, c_idx, col_name, fmt_header)
        row_cursor += 1
        ticker_data_start_open = row_cursor

        if not df_ticker_blocks_open.empty:
            for _, r in df_ticker_blocks_open.iterrows():
                tkr_val = r["Ticker"]
                exp_val = r["Expiry"]
                strike_val = r["Strike"]
                entry_val = r["Entry_Price"]
                live_val = r["Live_Price"]
                leg_type_val = r["Leg_Type"]
                cp_val = r["CallPut"]
                pnl_close_val = r["PnL_Close"]
                pnl_open_val = r["PnL_Open_Approx"]
                inv_val = r["Investment"]

                if isinstance(tkr_val, str) and tkr_val.startswith("Ticker:"):
                    sheet_open.write(row_cursor, 0, tkr_val, fmt_ticker_header)
                    for c_idx in range(1, len(cols_ticker)):
                        sheet_open.write(row_cursor, c_idx, "", fmt_ticker_header)
                    row_cursor += 1
                    continue

                if (tkr_val == "" or pd.isna(tkr_val)) and (exp_val == "" or pd.isna(exp_val)) and leg_type_val == "":
                    row_cursor += 1
                    continue

                sheet_open.write(row_cursor, 0, tkr_val if tkr_val != "" else "", fmt_default)
                sheet_open.write(row_cursor, 1, exp_val if exp_val != "" else "", fmt_default)

                if strike_val != "" and not pd.isna(strike_val):
                    sheet_open.write(row_cursor, 2, float(strike_val), fmt_default)
                else:
                    sheet_open.write(row_cursor, 2, "", fmt_default)

                sheet_open.write(row_cursor, 3, entry_val if entry_val != "" else "", fmt_default)

                if live_val != "" and not pd.isna(live_val):
                    sheet_open.write(row_cursor, 4, float(live_val), fmt_default)
                else:
                    sheet_open.write(row_cursor, 4, "", fmt_default)

                sheet_open.write(row_cursor, 5, leg_type_val if leg_type_val != "" else "", fmt_default)
                sheet_open.write(row_cursor, 6, cp_val if cp_val != "" else "", fmt_default)

                if pnl_close_val != "" and not pd.isna(pnl_close_val):
                    sheet_open.write(row_cursor, 7, float(pnl_close_val), fmt_default)
                else:
                    sheet_open.write(row_cursor, 7, "", fmt_default)

                if pnl_open_val != "" and not pd.isna(pnl_open_val):
                    sheet_open.write(row_cursor, 8, float(pnl_open_val), fmt_default)
                else:
                    sheet_open.write(row_cursor, 8, "", fmt_default)

                if inv_val != "" and not pd.isna(inv_val):
                    sheet_open.write(row_cursor, 9, float(inv_val), fmt_default)
                else:
                    sheet_open.write(row_cursor, 9, "", fmt_default)

                row_cursor += 1

        ticker_data_end_open = row_cursor - 1

        if ticker_data_start_open <= ticker_data_end_open:
            for r in range(ticker_data_start_open, ticker_data_end_open + 1):
                excel_row = r + 1
                formula_strong_green = f'=AND($F{excel_row}="COMBINED",$I{excel_row}>={PSTRONG})'
                formula_light_green = f'=AND($F{excel_row}="COMBINED",$I{excel_row}>0,$I{excel_row}<{PSTRONG})'
                formula_strong_red = f'=AND($F{excel_row}="COMBINED",$I{excel_row}<=-{PSTRONG})'
                formula_light_red = f'=AND($F{excel_row}="COMBINED",$I{excel_row}<0,$I{excel_row}>-{PSTRONG})'

                sheet_open.conditional_format(r, 0, r, len(cols_ticker) - 1, {
                    "type": "formula",
                    "criteria": formula_strong_green,
                    "format": fmt_strong_green
                })
                sheet_open.conditional_format(r, 0, r, len(cols_ticker) - 1, {
                    "type": "formula",
                    "criteria": formula_light_green,
                    "format": fmt_light_green
                })
                sheet_open.conditional_format(r, 0, r, len(cols_ticker) - 1, {
                    "type": "formula",
                    "criteria": formula_strong_red,
                    "format": fmt_strong_red
                })
                sheet_open.conditional_format(r, 0, r, len(cols_ticker) - 1, {
                    "type": "formula",
                    "criteria": formula_light_red,
                    "format": fmt_light_red
                })

        if exp_data_start_open <= exp_data_end_open:
            for r in range(exp_data_start_open, exp_data_end_open + 1):
                excel_row = r + 1
                formula_strong_green = f'=AND($F{excel_row}="COMBINED",$H{excel_row}>={PSTRONG})'
                formula_light_green = f'=AND($F{excel_row}="COMBINED",$H{excel_row}>0,$H{excel_row}<{PSTRONG})'
                formula_strong_red = f'=AND($F{excel_row}="COMBINED",$H{excel_row}<=-{PSTRONG})'
                formula_light_red = f'=AND($F{excel_row}="COMBINED",$H{excel_row}<0,$H{excel_row}>-{PSTRONG})'

                sheet_open.conditional_format(r, 0, r, len(cols_exp) - 1, {
                    "type": "formula",
                    "criteria": formula_strong_green,
                    "format": fmt_strong_green
                })
                sheet_open.conditional_format(r, 0, r, len(cols_exp) - 1, {
                    "type": "formula",
                    "criteria": formula_light_green,
                    "format": fmt_light_green
                })
                sheet_open.conditional_format(r, 0, r, len(cols_exp) - 1, {
                    "type": "formula",
                    "criteria": formula_strong_red,
                    "format": fmt_strong_red
                })
                sheet_open.conditional_format(r, 0, r, len(cols_exp) - 1, {
                    "type": "formula",
                    "criteria": formula_light_red,
                    "format": fmt_light_red
                })

        # ---------- Entry_Comparison ----------
        if not df_entry_cmp.empty:
            df_entry_cmp = df_entry_cmp.copy()
            for c in df_entry_cmp.columns:
                if pd.api.types.is_numeric_dtype(df_entry_cmp[c]):
                    df_entry_cmp[c] = df_entry_cmp[c].astype(float).round(2)

            sheet_ec = workbook.add_worksheet("Entry_Comparison")
            writer.sheets["Entry_Comparison"] = sheet_ec

            for col_idx, col in enumerate(df_entry_cmp.columns):
                sheet_ec.write(0, col_idx, col, fmt_header)

            for r_idx, (_, r) in enumerate(df_entry_cmp.iterrows(), start=1):
                for c_idx, col in enumerate(df_entry_cmp.columns):
                    val = r[col]
                    if isinstance(val, (int, float)) and not pd.isna(val):
                        sheet_ec.write(r_idx, c_idx, float(val), fmt_default)
                    else:
                        sheet_ec.write(r_idx, c_idx, val)

            n_rows, n_cols = df_entry_cmp.shape
            if n_rows > 0:
                pnl_col_idx = df_entry_cmp.columns.get_loc("PnL_Live_Entry_Open_Approx")
                first_row = 1
                last_row = n_rows
                col_letter = chr(ord("A") + pnl_col_idx)

                sheet_ec.conditional_format(first_row, 0, last_row, n_cols - 1, {
                    "type": "formula",
                    "criteria": f"=${col_letter}{first_row+1}>={PSTRONG}",
                    "format": fmt_strong_green
                })
                sheet_ec.conditional_format(first_row, 0, last_row, n_cols - 1, {
                    "type": "formula",
                    "criteria": f"=AND(${col_letter}{first_row+1}>0,${col_letter}{first_row+1}<{PSTRONG})",
                    "format": fmt_light_green
                })
                sheet_ec.conditional_format(first_row, 0, last_row, n_cols - 1, {
                    "type": "formula",
                    "criteria": f"=${col_letter}{first_row+1}<=-{PSTRONG}",
                    "format": fmt_strong_red
                })
                sheet_ec.conditional_format(first_row, 0, last_row, n_cols - 1, {
                    "type": "formula",
                    "criteria": f"=AND(${col_letter}{first_row+1}<0,${col_letter}{first_row+1}>-{PSTRONG})",
                    "format": fmt_light_red
                })

    print(f"[LIVE] Wrote Live_Spreads, Account_stat_EOD, current_account_open, Entry_Comparison to {out_path}")
