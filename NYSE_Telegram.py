import os
import sqlite3
from datetime import datetime

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import yfinance as yf
import pandas_market_calendars as mcal
from textwrap import wrap

# -------------------------
# feature switches
# -------------------------
GENERATE_OI_PNG = True    # turn OI/Price/Vol charts on/off
GENERATE_EXCEL  = True    # turn Excel output on/off

# -------------------------
# CONFIG / PATHS
# -------------------------
import platform

def get_data_dir():
    # WSL/Linux absolute path (works on Linux/WSL)
    linux_path = r"/mnt/c/Users/srini/Options_chain_data"
    # Windows path format (works on native Windows)
    win_path = r"C:\Users\srini\Options_chain_data"
    
    # On Windows native, prefer Windows path
    if platform.system() == "Windows":
        if os.path.exists(win_path):
            return win_path
    
    # On Linux/WSL (or if Windows path doesn't exist), use Linux path
    if os.path.exists(linux_path):
        return linux_path
    
    # Fallback to Windows path
    return win_path

DATA_DIR = get_data_dir()
DB_PATH = os.path.join(DATA_DIR, "US_data.db")

TABLE_OPTIONS_DAILY  = "options_daily"
TABLE_OPTIONS_CHANGE = "options_change"
TABLE_STOCK_DAILY    = "stock_daily"

BASE_OUT_DIR = os.path.join(DATA_DIR, "US_CHARTS")
os.makedirs(BASE_OUT_DIR, exist_ok=True)

CALL_CURR_COLOR = "#005BBB"
PUT_CURR_COLOR  = "#B30000"
CALL_PREV_LINE  = "#1f77b4"
PUT_PREV_LINE   = "#d62728"

nyse_cal = mcal.get_calendar("NYSE")

# =========================
# BASIC HELPERS
# =========================
def parse_mmddyyyy(d: str) -> datetime:
    return datetime.strptime(d, "%m-%d-%Y")

def mmddyyyy_to_str(dt: datetime) -> str:
    return dt.strftime("%m-%d-%Y")

def get_conn():
    return sqlite3.connect(DB_PATH)

def latest_trade_date_now():
    with get_conn() as conn:
        df = pd.read_sql(
            f"SELECT DISTINCT trade_date_now FROM {TABLE_OPTIONS_CHANGE}",
            conn
        )
    if df.empty:
        return None
    dates = pd.to_datetime(df["trade_date_now"], format="%m-%d-%Y", errors="coerce")
    if dates.isna().all():
        return None
    max_idx = dates.idxmax()
    return df.loc[max_idx, "trade_date_now"]

def get_prev_trade_date_mmddyyyy(trade_date_now: str) -> str | None:
    dt_now = pd.to_datetime(trade_date_now, format="%m-%d-%Y")
    sched = nyse_cal.schedule(
        start_date=dt_now - pd.Timedelta(days=15),
        end_date=dt_now
    )
    days = sched.index
    if len(days) == 0:
        return None
    prev_days = days[days < dt_now]
    if len(prev_days) == 0:
        return None
    prev = prev_days[-1]
    return prev.strftime("%m-%d-%Y")

def get_symbols_for_trade_date(td_now: str):
    with get_conn() as conn:
        df = pd.read_sql(
            f"SELECT DISTINCT ticker FROM {TABLE_OPTIONS_CHANGE} WHERE trade_date_now = ?",
            conn,
            params=(td_now,),
        )
    return sorted(df["ticker"].tolist())

def get_stock_close(symbol: str, trade_date_now: str):
    with get_conn() as conn:
        df = pd.read_sql(
            f"SELECT close FROM {TABLE_STOCK_DAILY} WHERE ticker = ? AND trade_date = ?",
            conn,
            params=(symbol, trade_date_now),
        )
    if df.empty:
        return None
    return float(df["close"].iloc[0])

def get_company_name(symbol: str, trade_date_now: str):
    with get_conn() as conn:
        df = pd.read_sql(
            f"SELECT company_name_now FROM {TABLE_OPTIONS_CHANGE} "
            f"WHERE ticker = ? AND trade_date_now = ? LIMIT 1",
            conn,
            params=(symbol, trade_date_now),
        )
    if df.empty:
        return symbol.upper()
    return str(df["company_name_now"].iloc[0])

def get_all_expiries(symbol: str, trade_date_now: str):
    with get_conn() as conn:
        df = pd.read_sql(
            f"SELECT DISTINCT expiry_date FROM {TABLE_OPTIONS_CHANGE} "
            f"WHERE ticker = ? AND trade_date_now = ? ORDER BY expiry_date",
            conn,
            params=(symbol, trade_date_now),
        )
    return df["expiry_date"].tolist()

# =========================
# MARKET / PCR HELPERS
# =========================
def get_yahoo_prices():
    tickers = [
        "IBIT", "GLD", "SLV", "SPY", "VOO", "QQQ",
        "BTC-USD", "GC=F", "SI=F", "^GSPC", "^NDX"
    ]
    data = yf.download(
        tickers=" ".join(tickers),
        period="1d",
        interval="1d",
        progress=False
    )
    prices = {}
    try:
        close = data["Close"]
        for t in tickers:
            if t in close.columns:
                val = close[t].iloc[-1]
            else:
                val = np.nan
            prices[t] = float(val) if pd.notna(val) else np.nan
    except Exception:
        for t in tickers:
            prices[t] = np.nan
    return prices

def mapping_ratio(ticker, prices=None):
    return 1.0

def classify_expiry_type(expiry_str: str) -> str:
    try:
        dt = pd.to_datetime(expiry_str, errors="coerce")
    except Exception:
        return "UNKNOWN"
    if pd.isna(dt):
        return "UNKNOWN"
    if dt.weekday() != 4:
        return "WEEKLY"
    first_day = dt.replace(day=1)
    month_days = pd.date_range(first_day, first_day + pd.Timedelta(days=31))
    fridays = [d for d in month_days if d.month == dt.month and d.weekday() == 4]
    fridays_sorted = sorted(fridays)
    if len(fridays_sorted) >= 3 and dt.date() == fridays_sorted[2].date():
        return "MONTHLY"
    else:
        return "WEEKLY"

def get_last_n_trade_dates_mmddyyyy(trade_date_now: str, n: int = 5) -> list[str]:
    dt_now = pd.to_datetime(trade_date_now, format="%m-%d-%Y")
    sched = nyse_cal.schedule(
        start_date=dt_now - pd.Timedelta(days=20),
        end_date=dt_now
    )
    days = sched.index
    if len(days) == 0:
        return [trade_date_now]
    days = days[days <= dt_now]
    last = days[-n:]
    return [d.strftime("%m-%d-%Y") for d in last]

def get_expiry_level_pcr_series(symbol: str, expiry: str, trade_date_now: str, lookback: int = 5):
    dates = get_last_n_trade_dates_mmddyyyy(trade_date_now, n=lookback)
    with get_conn() as conn:
        df = pd.read_sql(
            f"""
            SELECT trade_date, expiry_date, openInt_Call, openInt_Put
            FROM {TABLE_OPTIONS_DAILY}
            WHERE ticker = ?
              AND expiry_date = ?
              AND trade_date IN ({",".join(["?"] * len(dates))})
            """,
            conn,
            params=(symbol, expiry, *dates),
        )
    if df.empty:
        return pd.DataFrame(columns=["trade_date", "pcr_oi"])
    df["trade_date_dt"] = pd.to_datetime(df["trade_date"], format="%m-%d-%Y", errors="coerce")
    df = df.dropna(subset=["trade_date_dt"])
    grp = df.groupby("trade_date_dt", as_index=False).agg(
        total_call_oi=("openInt_Call", "sum"),
        total_put_oi=("openInt_Put", "sum"),
    )
    grp["pcr_oi"] = grp["total_put_oi"] / grp["total_call_oi"].replace(0, np.nan)
    grp = grp.sort_values("trade_date_dt").tail(lookback)
    grp["trade_date"] = grp["trade_date_dt"].dt.strftime("%m-%d")
    return grp[["trade_date", "pcr_oi"]]

def get_60d_volume_avgs(symbol, trade_date_now: str):
    current_dt = parse_mmddyyyy(trade_date_now)
    start_dt = current_dt - pd.Timedelta(days=80)
    with get_conn() as conn:
        df = pd.read_sql(
            f"SELECT ticker, trade_date, expiry_date, strike, vol_Call, vol_Put "
            f"FROM {TABLE_OPTIONS_DAILY} "
            f"WHERE ticker = ? AND trade_date BETWEEN ? AND ?",
            conn,
            params=(symbol, mmddyyyy_to_str(start_dt), trade_date_now),
        )
    if df.empty:
        return pd.DataFrame(columns=["expiry_date", "strike", "vol_call_avg60", "vol_put_avg60"])
    df["trade_date_dt"] = pd.to_datetime(df["trade_date"], format="%m-%d-%Y", errors="coerce")
    df = df.dropna(subset=["trade_date_dt"])
    df = df.sort_values(["expiry_date", "strike", "trade_date_dt"])
    def _rolling_60(s):
        return s.shift(1).rolling(60, min_periods=1).mean()
    df["vol_call_avg60"] = df.groupby(["expiry_date", "strike"], group_keys=False)["vol_Call"].apply(_rolling_60)
    df["vol_put_avg60"]  = df.groupby(["expiry_date", "strike"], group_keys=False)["vol_Put"].apply(_rolling_60)
    df["vol_call_avg60"] = df["vol_call_avg60"].fillna(0.0)
    df["vol_put_avg60"]  = df["vol_put_avg60"].fillna(0.0)
    mask_today = df["trade_date"] == trade_date_now
    df_today = df.loc[mask_today, ["expiry_date", "strike", "vol_call_avg60", "vol_put_avg60"]].copy()
    if df_today.empty:
        return pd.DataFrame(columns=["expiry_date", "strike", "vol_call_avg60", "vol_put_avg60"])
    return df_today

def get_options_with_prev_values(symbol, trade_date_now, expiry):
    trade_date_prev = get_prev_trade_date_mmddyyyy(trade_date_now)
    with get_conn() as conn:
        df_today = pd.read_sql(
            f"SELECT ticker, expiry_date, strike, trade_date, openInt_Call, openInt_Put, "
            f"call_open AS call_open_now, call_high AS call_high_now, call_low AS call_low_now, call_close AS call_close_now, "
            f"put_open AS put_open_now, put_high AS put_high_now, put_low AS put_low_now, put_close AS put_close_now, "
            f"vol_Call AS vol_call_now, vol_Put AS vol_put_now "
            f"FROM {TABLE_OPTIONS_DAILY} WHERE ticker = ? AND trade_date = ? AND expiry_date = ?",
            conn,
            params=(symbol, trade_date_now, expiry),
        )
        df_prev = pd.read_sql(
            f"SELECT ticker, expiry_date, strike, "
            f"openInt_Call AS openInt_Call_prev, openInt_Put AS openInt_Put_prev, "
            f"call_open AS call_open_prev, call_high AS call_high_prev, call_low AS call_low_prev, call_close AS call_close_prev, "
            f"put_open AS put_open_prev, put_high AS put_high_prev, put_low AS put_low_prev, put_close AS put_close_prev "
            f"FROM {TABLE_OPTIONS_DAILY} WHERE ticker = ? AND trade_date = ? AND expiry_date = ?",
            conn,
            params=(symbol, trade_date_prev, expiry),
        )
        df_chg = pd.read_sql(
            f"SELECT ticker, expiry_date, strike, change_OI_Call, change_OI_Put "
            f"FROM {TABLE_OPTIONS_CHANGE} WHERE ticker = ? AND trade_date_now = ? AND expiry_date = ?",
            conn,
            params=(symbol, trade_date_now, expiry),
        )
    if df_today.empty:
        return None
    key = ["ticker", "expiry_date", "strike"]
    df = df_today.merge(df_prev, on=key, how="left").merge(df_chg, on=key, how="left")
    df_vol60 = get_60d_volume_avgs(symbol, trade_date_now)
    df = df.merge(df_vol60, on=["expiry_date", "strike"], how="left")
    num_cols = [
        "strike", "openInt_Call", "openInt_Put", "openInt_Call_prev", "openInt_Put_prev",
        "call_open_now", "call_high_now", "call_low_now", "call_close_now",
        "put_open_now", "put_high_now", "put_low_now", "put_close_now",
        "call_open_prev", "call_high_prev", "call_low_prev", "call_close_prev",
        "put_open_prev", "put_high_prev", "put_low_prev", "put_close_prev",
        "change_OI_Call", "change_OI_Put",
        "vol_call_now", "vol_put_now", "vol_call_avg60", "vol_put_avg60",
    ]
    for col in num_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    for col in ["openInt_Call", "openInt_Put", "openInt_Call_prev", "openInt_Put_prev"]:
        if col in df.columns:
            df[col] = df[col].fillna(0.0)
    df["change_OI_Call"] = (df["openInt_Call"] - df["openInt_Call_prev"]).fillna(0.0)
    df["change_OI_Put"]  = (df["openInt_Put"]  - df["openInt_Put_prev"]).fillna(0.0)
    df["call_close_eff"] = df["call_close_now"].fillna(df["call_close_prev"])
    df["put_close_eff"]  = df["put_close_now"].fillna(df["put_close_prev"])
    df["openInt_Call_eff"] = df["openInt_Call"].replace(0, np.nan).fillna(df["openInt_Call_prev"]).fillna(0.0)
    df["openInt_Put_eff"]  = df["openInt_Put"].replace(0, np.nan).fillna(df["openInt_Put_prev"]).fillna(0.0)
    df = df.dropna(subset=["strike"])
    df = df.sort_values("strike").reset_index(drop=True)
    return df

def summarize_symbol_view(symbol, trade_date_now, yahoo_prices):
    sym = symbol.upper()
    expiries = get_all_expiries(sym, trade_date_now)
    if not expiries:
        return f"{sym} â€“ {trade_date_now}\nNo expiries for summary."
    spot = get_stock_close(sym, trade_date_now)
    if spot is None:
        return f"{sym} â€“ {trade_date_now}\nNo stock close for summary."

    rows = []
    for expiry in expiries:
        df = get_options_with_prev_values(sym, trade_date_now, expiry)
        if df is None or df.empty:
            continue
        total_call_oi = df["openInt_Call_eff"].sum()
        total_put_oi  = df["openInt_Put_eff"].sum()
        pcr_oi = total_put_oi / total_call_oi if total_call_oi > 0 else np.nan
        expiry_type = classify_expiry_type(expiry)
        rows.append((expiry, expiry_type, pcr_oi))

    if not rows:
        return f"{sym} â€“ {trade_date_now}\nNo OI rows for summary."

    weekly  = [r for r in rows if r[1] == "WEEKLY"  and np.isfinite(r[2])]
    monthly = [r for r in rows if r[1] == "MONTHLY" and np.isfinite(r[2])]

    def summarize_group(name, vals):
        if not vals:
            return f"{name}: no PCR data."
        pcs = [v[2] for v in vals]
        latest = vals[-1][2]
        direction = "flat"
        if len(pcs) >= 2:
            if pcs[-1] > pcs[0] * 1.1:
                direction = "rising"
            elif pcs[-1] < pcs[0] * 0.9:
                direction = "falling"
        if latest > 1.3:
            word = "<span style='color:#CC0000;font-weight:bold;'>bearish</span>"
        elif latest < 0.7:
            word = "<span style='color:#006400;font-weight:bold;'>bullish</span>"
        else:
            word = "<span style='color:#0000CC;font-weight:bold;'>neutral/mixed</span>"
        return f"{name}: PCR ~{latest:.2f}, {direction}, sentiment {word}"

    weekly_txt  = summarize_group("Weekly", weekly)
    monthly_txt = summarize_group("Monthly", monthly)

    text = []
    text.append(f"{sym} â€“ {trade_date_now} (spot ~{spot:.2f})")   # line 1
    text.append(weekly_txt)                                      # line 2
    text.append(monthly_txt)                                     # line 3
    text.append("View: Short-term sideways, watch key range near spot")  # line 4
    text.append("OI Focus: Calls above spot stronger, Puts concentrated below")  # line 5
    text.append("Note: Monthly expiry carries more weight than weeklies")        # line 6

    summary_lines = [line for line in text if str(line).strip() != ""]
    return "\n".join(summary_lines)

def make_oi_chart(symbol, trade_date_now, yahoo_prices):
    sym = symbol.upper()
    company_name = get_company_name(sym, trade_date_now)
    expiries = get_all_expiries(sym, trade_date_now)
    if not expiries:
        return None, None, None
    spot = get_stock_close(sym, trade_date_now)
    if spot is None:
        return None, None, None
    ratio = mapping_ratio(sym, yahoo_prices)
    spot_conv = spot * ratio

    # global OI magnet (optional)
    try:
        with get_conn() as conn:
            df_opt_all = pd.read_sql(
                f"SELECT expiry_date, strike, openInt_Call, openInt_Put "
                f"FROM {TABLE_OPTIONS_DAILY} WHERE ticker = ? AND trade_date = ?",
                conn,
                params=(sym, trade_date_now),
            )
        if not df_opt_all.empty:
            df_opt_all["strike"] = pd.to_numeric(df_opt_all["strike"], errors="coerce")
            df_opt_all["total_oi"] = df_opt_all["openInt_Call"].fillna(0) + df_opt_all["openInt_Put"].fillna(0)
            df_opt_all = df_opt_all.dropna(subset=["strike", "total_oi"])
            max_oi_row = df_opt_all.loc[df_opt_all["total_oi"].idxmax()]
            k_magnet_raw = float(max_oi_row["strike"])
            k_magnet = k_magnet_raw * ratio
        else:
            k_magnet = None
    except Exception:
        k_magnet = None

    slices = []
    for expiry in expiries:
        df = get_options_with_prev_values(sym, trade_date_now, expiry)
        if df is None or df.empty:
            slices.append(None)
            continue
        df = df.copy()
        df["strike"] = pd.to_numeric(df["strike"], errors="coerce")
        df = df.dropna(subset=["strike"])
        df["strike_conv"] = df["strike"] * ratio

        df["call_oi_prev"] = df["openInt_Call_prev"]
        df["put_oi_prev"]  = df["openInt_Put_prev"]
        df["call_oi_now"]  = df["openInt_Call_eff"]
        df["put_oi_now"]   = df["openInt_Put_eff"]

        df["call_oi"] = df["call_oi_prev"]
        df["put_oi"]  = df["put_oi_prev"]

        label_gap_factor = 1.10
        df["put_oi_plot"]     = -df["put_oi"] * label_gap_factor
        df["put_oi_now_plot"] = -df["put_oi_now"] * label_gap_factor

        df["call_price_prev_close"] = df["call_close_prev"]
        df["put_price_prev_close"]  = -df["put_close_prev"]
        df["call_close_now_val"]    = df["call_close_eff"]
        df["put_close_now_val"]     = -df["put_close_eff"]

        slices.append(df)

    def expiry_type_html(e):
        t = classify_expiry_type(e)
        if t == "MONTHLY":
            return "<span style='color:red;font-weight:bold;'>(MONTHLY)</span>"
        elif t == "WEEKLY":
            return "<span style='color:#008B8B;font-weight:bold;'>(WEEKLY)</span>"
        else:
            return f"<span>({t})</span>"

    n = len(expiries)

    extra_blank_row = 1 if 1 <= n <= 3 else 0
    total_rows = n + extra_blank_row

    if 1 <= n <= 3:
        vert_spacing = 0.12
    else:
        vert_spacing = 0.05

    specs = []
    subplot_titles = []
    for row_idx in range(total_rows):
        if extra_blank_row and row_idx == 0:
            specs.append([{"secondary_y": True}, {"secondary_y": True}])
            subplot_titles.extend(["", ""])
        else:
            e = expiries[row_idx - extra_blank_row]
            specs.append([{"secondary_y": True}, {"secondary_y": True}])
            subplot_titles.extend([
                f"{e} {expiry_type_html(e)} – OI (prev vs now)",
                f"{e} {expiry_type_html(e)} – Price (prev vs now) / Vol & 60d avg",
            ])

    fig = make_subplots(
        rows=total_rows,
        cols=2,
        shared_xaxes=False,
        vertical_spacing=vert_spacing,
        horizontal_spacing=0.06,
        subplot_titles=subplot_titles,
        specs=specs,
    )

    fig.update_yaxes(showticklabels=True, title_font=dict(size=11))
    fig.update_layout(barmode="overlay")

    # keep bar text reasonably large
    fig.update_layout(
        uniformtext_minsize=8,
        uniformtext_mode="show",
    )

    for idx, (expiry, df) in enumerate(zip(expiries, slices), start=1):
        row = idx + extra_blank_row
        if df is None or df.empty:
            continue

        show_legend = (idx == 1)
        expiry_type = classify_expiry_type(expiry)

        unique_strikes = sorted(df["strike_conv"].unique().tolist())
        pos_map = {k: ii for ii, k in enumerate(unique_strikes)}
        df["x_pos"] = df["strike_conv"].map(pos_map)

        tickvals_this = list(range(len(unique_strikes)))
        ticktext_this = [f"{v:.1f}" for v in unique_strikes]
        inner_width = 0.4

        max_oi_here = max(
            abs(df["call_oi"]).max(),
            abs(df["put_oi_plot"]).max(),
            abs(df["call_oi_now"]).max(),
            abs(df["put_oi_now_plot"]).max(),
        )
        if not np.isfinite(max_oi_here) or max_oi_here <= 0:
            max_oi_here = 1.0

        price_vals = pd.concat([
            df["call_price_prev_close"],
            df["put_price_prev_close"],
            df["call_close_now_val"],
            df["put_close_now_val"],
        ], axis=0).replace([np.inf, -np.inf], np.nan).dropna()
        max_price_here = abs(price_vals).max() if not price_vals.empty else 1.0

        vol_vals = pd.concat([
            df["vol_call_now"],
            df["vol_put_now"],
            df["vol_call_avg60"],
            df["vol_put_avg60"],
        ], axis=0).replace([np.inf, -np.inf], np.nan).dropna()
        max_vol_here = abs(vol_vals).max() if not vol_vals.empty else 1.0
        vol_max_k = max_vol_here / 1000.0

        # LEFT: OI bars
        fig.add_trace(
            go.Bar(
                x=df["x_pos"],
                y=df["call_oi"],
                name="Call OI prev",
                marker_color=CALL_PREV_LINE,
                opacity=0.9,
                showlegend=show_legend,
            ),
            row=row, col=1, secondary_y=False
        )
        fig.add_trace(
            go.Bar(
                x=df["x_pos"],
                y=df["put_oi_plot"],
                name="Put OI prev",
                marker_color=PUT_PREV_LINE,
                opacity=0.9,
                showlegend=show_legend,
            ),
            row=row, col=1, secondary_y=False
        )
        fig.add_trace(
            go.Bar(
                x=df["x_pos"],
                y=df["call_oi_now"],
                name="Call OI now",
                marker_color="white",
                marker_line_color=CALL_CURR_COLOR,
                marker_line_width=1,
                opacity=1.0,
                width=inner_width,
                showlegend=show_legend,
            ),
            row=row, col=1, secondary_y=False
        )
        fig.add_trace(
            go.Bar(
                x=df["x_pos"],
                y=df["put_oi_now_plot"],
                name="Put OI now",
                marker_color="white",
                marker_line_color=PUT_CURR_COLOR,
                marker_line_width=1,
                opacity=1.0,
                width=inner_width,
                showlegend=show_legend,
            ),
            row=row, col=1, secondary_y=False
        )

        # RIGHT: price + volume + 60d avg
        fig.add_trace(
            go.Bar(
                x=df["x_pos"],
                y=df["call_price_prev_close"],
                name="Call Prev Price",
                marker_color=CALL_PREV_LINE,
                opacity=0.9,
                showlegend=show_legend,
            ),
            row=row, col=2, secondary_y=False
        )
        fig.add_trace(
            go.Bar(
                x=df["x_pos"],
                y=df["put_price_prev_close"],
                name="Put Prev Price",
                marker_color=PUT_PREV_LINE,
                opacity=0.9,
                showlegend=show_legend,
            ),
            row=row, col=2, secondary_y=False
        )

        # CURRENT PRICES: integer labels, black, readable size
        fig.add_trace(
            go.Bar(
                x=df["x_pos"],
                y=df["call_close_now_val"],
                name="Call Price Now",
                marker_color="white",
                marker_line_color=CALL_CURR_COLOR,
                marker_line_width=1,
                opacity=1.0,
                width=inner_width,
                showlegend=show_legend,
                text=[f"{int(round(v))}" if pd.notna(v) else "" for v in df["call_close_now_val"]],
                textposition="outside",
                textfont=dict(size=8, color="black"),
                cliponaxis=False,
            ),
            row=row, col=2, secondary_y=False
        )
        fig.add_trace(
            go.Bar(
                x=df["x_pos"],
                y=df["put_close_now_val"],
                name="Put Price Now",
                marker_color="white",
                marker_line_color=PUT_CURR_COLOR,
                marker_line_width=1,
                opacity=1.0,
                width=inner_width,
                showlegend=show_legend,
                text=[f"{int(round(abs(v)))}" if pd.notna(v) else "" for v in df["put_close_now_val"]],
                textposition="outside",
                textfont=dict(size=8, color="black"),
                cliponaxis=False,
            ),
            row=row, col=2, secondary_y=False
        )

        # volume + 60d avg
        fig.add_trace(
            go.Scatter(
                x=df["x_pos"],
                y=df["vol_call_now"] / 1000.0,
                name="Call Vol (last, k)",
                mode="lines+markers",
                line=dict(color="#1f77b4", width=1.8),
                marker=dict(size=5, color="#1f77b4"),
                showlegend=show_legend,
            ),
            row=row, col=2, secondary_y=True
        )
        fig.add_trace(
            go.Scatter(
                x=df["x_pos"],
                y=-df["vol_put_now"] / 1000.0,
                name="Put Vol (last, k)",
                mode="lines+markers",
                line=dict(color="#d62728", width=1.8),
                marker=dict(size=5, color="#d62728"),
                showlegend=show_legend,
            ),
            row=row, col=2, secondary_y=True
        )
        fig.add_trace(
            go.Scatter(
                x=df["x_pos"],
                y=df["vol_call_avg60"] / 1000.0,
                name="Call Vol (60d avg, k)",
                mode="lines",
                line=dict(color="#2ca02c", width=2, dash="dot"),
                showlegend=show_legend,
            ),
            row=row, col=2, secondary_y=True
        )
        fig.add_trace(
            go.Scatter(
                x=df["x_pos"],
                y=-df["vol_put_avg60"] / 1000.0,
                name="Put Vol (60d avg, k)",
                mode="lines",
                line=dict(color="#9467bd", width=2, dash="dot"),
                showlegend=show_legend,
            ),
            row=row, col=2, secondary_y=True
        )

        fig.update_yaxes(
            range=[-max_oi_here * 1.2, max_oi_here * 1.2],
            title_text="OI",
            row=row, col=1, secondary_y=False,
        )
        fig.update_yaxes(
            range=[-max_price_here * 2.0, max_price_here * 2.0],
            title_text="Price",
            row=row, col=2, secondary_y=False,
        )
        vol_pcr_max = max(vol_max_k * 1.15, 2.0)
        fig.update_yaxes(
            row=row, col=2, secondary_y=True,
            range=[-vol_pcr_max, vol_pcr_max],
            title_text="Volume (k) / PCR",
        )

        fig.update_xaxes(
            tickmode="array",
            tickvals=tickvals_this,
            ticktext=ticktext_this,
            title_text=None,
            row=row, col=1,
        )
        fig.update_xaxes(
            tickmode="array",
            tickvals=tickvals_this,
            ticktext=ticktext_this,
            title_text=None,
            row=row, col=2,
        )

        if len(unique_strikes) > 1:
            spot_x = np.interp(spot_conv, unique_strikes, tickvals_this)
        else:
            spot_x = 0.0
        fig.add_vline(x=spot_x, line_dash="dash", line_color="black", row=row, col=1)
        fig.add_vline(x=spot_x, line_dash="dash", line_color="grey",  row=row, col=2)

        if k_magnet is not None and len(unique_strikes) > 1:
            k_magnet_x = np.interp(k_magnet, unique_strikes, tickvals_this)
            band_half_width = 0.5
            fig.add_vrect(
                x0=k_magnet_x - band_half_width,
                x1=k_magnet_x + band_half_width,
                fillcolor="rgba(200, 200, 255, 0.25)",
                line_width=0,
                row=row, col=1, layer="below",
            )
            fig.add_vrect(
                x0=k_magnet_x - band_half_width,
                x1=k_magnet_x + band_half_width,
                fillcolor="rgba(200, 200, 255, 0.25)",
                line_width=0,
                row=row, col=2, layer="below",
            )

        total_call_oi = df["openInt_Call_eff"].sum()
        total_put_oi  = df["openInt_Put_eff"].sum()
        pcr_oi = total_put_oi / total_call_oi if total_call_oi > 0 else np.nan
        if np.isnan(pcr_oi):
            bias = "Neutral"
        elif pcr_oi > 1.3:
            bias = "Bearish"
        elif pcr_oi < 0.7:
            bias = "Bullish"
        else:
            bias = "Neutral"

        if bias == "Bullish":
            bias_bg = "rgba(0,180,0,0.25)"
            bias_border = "#006400"
        elif bias == "Bearish":
            bias_bg = "rgba(220,0,0,0.25)"
            bias_border = "#8B0000"
        else:
            bias_bg = "rgba(255,215,0,0.35)"
            bias_border = "#B8860B"

        if expiry_type == "MONTHLY":
            type_html = "<span style='color:red;font-weight:bold;'>MONTHLY</span>"
        elif expiry_type == "WEEKLY":
            type_html = "<span style='color:#008B8B;font-weight:bold;'>WEEKLY</span>"
        else:
            type_html = f"<span>({expiry_type})</span>"

        pcr_hist = get_expiry_level_pcr_series(sym, expiry, trade_date_now, lookback=5)
        if not pcr_hist.empty and pcr_hist["pcr_oi"].notna().any():
            fig.add_trace(
                go.Scatter(
                    x=list(range(len(pcr_hist))),
                    y=pcr_hist["pcr_oi"],
                    mode="lines+markers",
                    name=f"{expiry} 5d PCR (OI)",
                    line=dict(color="#000000", width=1.5, dash="dot"),
                    marker=dict(size=5, color="#000000"),
                    showlegend=show_legend,
                ),
                row=row, col=1, secondary_y=True,
            )
            fig.update_yaxes(
                row=row, col=1, secondary_y=True,
                range=[0, 2.5],
                title_text="PCR (5d)",
            )

        fig.add_annotation(
            xref="x domain", yref="y domain",
            x=0.98, y=0.98,
            text=f"{expiry} {type_html}<br>PCR {pcr_oi:.2f} ({bias})",
            showarrow=False,
            align="right",
            bgcolor=bias_bg,
            bordercolor=bias_border,
            borderwidth=1,
            font=dict(size=8),
            row=row, col=2,
        )

    band_y = 1.12

    name_text = f"{company_name} ({sym})"
    if len(name_text) > 40:
        name_text = name_text[:40] + "<br>" + name_text[40:]
    fig.add_annotation(
        xref="paper", yref="paper",
        x=0.01, y=band_y,
        xanchor="left", yanchor="top",
        text=name_text,
        showarrow=False,
        align="left",
        font=dict(
            family="Arial Black, Arial, sans-serif",
            size=22,
            color="black",
        ),
    )

    summary_text = summarize_symbol_view(sym, trade_date_now, yahoo_prices)
    summary_html = "<br>" + "<br>".join(summary_text.split("\n"))
    fig.add_annotation(
        xref="paper", yref="paper",
        x=0.50, y=band_y,
        xanchor="left",
        yanchor="top",
        text=summary_html,
        showarrow=False,
        align="left",
        bgcolor="rgba(255,255,255,0.97)",
        bordercolor="black",
        borderwidth=1,
        font=dict(size=16),
    )

    rows = max(1, total_rows)
    base_height_per_row = 260
    fig.update_layout(
        title=None,
        height=max(base_height_per_row * rows, 400),
        width=1800,
        bargap=0.02,
        bargroupgap=0.05,
        legend=dict(
            orientation="v",
            xref="paper",
            yref="paper",
            x=1.08,
            y=band_y,
            xanchor="right",
            yanchor="top",
            bgcolor="rgba(255,255,255,0.8)",
            bordercolor="black",
            borderwidth=1,
            font=dict(size=9),
        ),
        margin=dict(l=140, r=10, t=260, b=60),
    )

    return fig, company_name, spot

# =========================
# STRATEGY HELPERS
# =========================
def build_strategy_for_symbol(symbol, trade_date_now):
    td_chain = trade_date_now
    td_stock = trade_date_now

    with get_conn() as conn:
        df_stock = pd.read_sql(
            f"SELECT close FROM {TABLE_STOCK_DAILY} WHERE ticker = ? AND trade_date = ?",
            conn,
            params=(symbol, td_stock),
        )
        df_opt = pd.read_sql(
            f"SELECT expiry_date, strike, lastPrice_Call "
            f"FROM {TABLE_OPTIONS_DAILY} WHERE ticker = ? AND trade_date = ?",
            conn,
            params=(symbol, td_chain),
        )

    if df_stock.empty or df_opt.empty:
        return None

    spot = float(df_stock["close"].iloc[0])

    df_opt["expiry_dt"] = pd.to_datetime(df_opt["expiry_date"], errors="coerce")
    trade_dt = datetime.strptime(td_chain, "%m-%d-%Y")
    df_opt["dte"] = (df_opt["expiry_dt"] - trade_dt).dt.days
    df_opt["strike"] = pd.to_numeric(df_opt["strike"], errors="coerce")

    core = df_opt[df_opt["dte"].between(21, 35) & df_opt["strike"].notna()].copy()
    if core.empty:
        return None

    core["moneyness_abs"] = (core["strike"] - spot).abs()
    base = core.sort_values(["moneyness_abs", "dte"]).iloc[0]

    expiry = str(base["expiry_date"])
    dte = int(base["dte"])
    k_long = float(base["strike"])
    width = max(round(spot * 0.05 / 1.0) * 1.0, 1.0)
    k_short = k_long + width

    debit = float(base.get("lastPrice_Call", 0.0)) * 100
    max_profit = max((k_short - k_long) * 100 - debit, 0)
    risk = max(debit, 0.0)
    rr = max_profit / risk if risk > 0 else float("nan")

    return {
        "view": "Bullish",
        "strategy": "Bull Call Spread",
        "expiry": expiry,
        "dte": dte,
        "k_long": round(k_long, 2),
        "k_short": round(k_short, 2),
        "risk": round(risk, 0),
        "max_profit": round(max_profit, 0),
        "rr": rr,
        "spot": round(spot, 2),
        "width": width,
        "breakeven": round(k_long + debit / 100.0, 2),
    }

def wrap_text(s, width=40, max_lines=3):
    if not isinstance(s, str):
        s = str(s)
    parts = wrap(s, width=width)
    if len(parts) > max_lines:
        parts = parts[:max_lines]
        parts[-1] += " ..."
    return "<br>".join(parts)

def build_strategies_for_symbol(symbol, trade_date_now, spot_from_oi):
    base = build_strategy_for_symbol(symbol, trade_date_now)
    if base is None:
        return []

    if spot_from_oi is not None:
        base["spot"] = round(float(spot_from_oi), 2)

    bull_call = {
        "Rank": 1,
        "Stock": symbol.upper(),
        "Price": base["spot"],
        "Change": "+0.0%",
        "View": "ðŸŸ¢ Bull",
        "Strategy": "Bull Call Spread",
        "Type": "Growth",
        "Market": "Bullish Stable",
        "Strikes": f"{base['k_long']}C / {base['k_short']}C",
        "Expiry": base["expiry"],
        "DTE": base["dte"],
        "Width": base["width"],
        "Risk": base["risk"],
        "Profit": base["max_profit"],
        "RR": base["rr"],
        "Assignment": "Low",
        "Liquidity": "High âœ…",
        "Breakeven_or_Range": f"{base['breakeven']:.2f}",
        "Max_Loss": base["risk"],
        "Easy_to_Implement": "Easy",
        "Comment": "Debit spread; capped profit and capped loss. Best if price finishes between breakeven and short strike at expiry.",
    }

    k_short_bp = round(base["k_long"] - 15, 2)
    k_long_bp = round(base["k_long"] - 30, 2)
    breakeven_bp = round(k_short_bp - 2.2, 2)
    bull_put = {
        "Rank": 2,
        "Stock": symbol.upper(),
        "Price": base["spot"],
        "Change": "+0.0%",
        "View": "ðŸŸ¢ Bull",
        "Strategy": "Bull Put Spread",
        "Type": "Income",
        "Market": "Bullish Stable",
        "Strikes": f"{k_short_bp}P / {k_long_bp}P",
        "Expiry": base["expiry"],
        "DTE": base["dte"],
        "Width": 15,
        "Risk": -220,
        "Profit": 220,
        "RR": 1.0,
        "Assignment": "Medium",
        "Liquidity": "High âœ…",
        "Breakeven_or_Range": f"{breakeven_bp:.2f}",
        "Max_Loss": 1780,
        "Easy_to_Implement": "Easy",
        "Comment": "Credit spread; keep full credit if price stays above breakeven at expiry. Loss capped by long put.",
    }

    ic = {
        "Rank": 3,
        "Stock": symbol.upper(),
        "Price": base["spot"],
        "Change": "+0.0%",
        "View": "ðŸŸ¡ Neut",
        "Strategy": "Iron Condor",
        "Type": "Income",
        "Market": "Neutral Stable",
        "Strikes": "650P/640P/720C/730C",
        "Expiry": base["expiry"],
        "DTE": base["dte"],
        "Width": "10/10",
        "Risk": -150,
        "Profit": 850,
        "RR": 5.7,
        "Assignment": "Medium",
        "Liquidity": "High âœ…",
        "Breakeven_or_Range": "650â€“720",
        "Max_Loss": 150,
        "Easy_to_Implement": "Moderate",
        "Comment": "Range-bound income strategy; max profit if price stays between inner short strikes. Loss capped by long wings.",
    }

    return [bull_call, bull_put, ic]

# =========================
# PAYOFF ENGINE
# =========================
def option_leg_payoff(sT, strike, opt_type, side, premium):
    if opt_type == "C":
        intrinsic = np.maximum(sT - strike, 0.0)
    else:
        intrinsic = np.maximum(strike - sT, 0.0)
    if side == "long":
        payoff = intrinsic - premium
    else:
        payoff = -intrinsic + premium
    return payoff

def strategy_payoff(sT, legs):
    total_ps = np.zeros_like(sT, dtype=float)
    for leg in legs:
        leg_pay = option_leg_payoff(
            sT,
            strike=leg["strike"],
            opt_type=leg["type"],
            side="long",
            premium=leg["premium"],
        )
        total_ps += leg["qty"] * leg_pay
    return total_ps * 100.0

def build_strategy_legs(name, spot, base_strike, width=10, credit_debit=2.0):
    k = base_strike
    w = width
    p = credit_debit
    legs = []
    meta = {
        "MarketBias": "Neutral",
        "GrowthIncome": "Growth",
        "RiskStyle": "Capped Loss",
        "ProfitStyle": "Capped Profit",
        "VolView": "Moderate",
        "Text": "",
    }

    if name == "Bull Call Spread":
        legs = [
            {"type": "C", "strike": k,     "premium": p,   "qty": 1},
            {"type": "C", "strike": k + w, "premium": p/2, "qty": -1},
        ]
        meta.update({
            "MarketBias": "Bullish",
            "GrowthIncome": "Growth",
            "Text": "Defined-risk bullish spread; profits if price moves up into the upper strike.",
        })
    elif name == "Bull Put Spread":
        legs = [
            {"type": "P", "strike": k,     "premium": p,   "qty": -1},
            {"type": "P", "strike": k - w, "premium": p/2, "qty": 1},
        ]
        meta.update({
            "MarketBias": "Bullish",
            "GrowthIncome": "Income",
            "Text": "Credit spread; keep premium if price stays above short put strike.",
        })
    elif name == "Iron Condor":
        legs = [
            {"type": "P", "strike": k - w,   "premium": p,   "qty": -1},
            {"type": "P", "strike": k - 2*w, "premium": p/2, "qty": 1},
            {"type": "C", "strike": k + w,   "premium": p,   "qty": -1},
            {"type": "C", "strike": k + 2*w, "premium": p/2, "qty": 1},
        ]
        meta.update({
            "MarketBias": "Neutral",
            "GrowthIncome": "Income",
            "VolView": "Short Vol",
            "Text": "Range-bound income strategy; max profit if price stays between short strikes.",
        })
    else:
        legs = [
            {"type": "C", "strike": k, "premium": p, "qty": 1},
        ]
        meta.update({
            "MarketBias": "Bullish",
            "GrowthIncome": "Growth",
            "Text": "Simple directional payoff template (fallback).",
        })

    return legs, meta

def build_payoff_figure_for_row(row, company_name, spot):
    strategy = row.get("Strategy", "")
    sym = row.get("Stock", "")
    dte = row.get("DTE", "")
    strikes_txt = row.get("Strikes", "")
    breakeven_txt = row.get("Breakeven_or_Range", "")
    base_comment = row.get("Comment", "")

    risk_val = float(row.get("Risk", 0) or 0)
    premium_received = risk_val if risk_val > 0 else -risk_val
    max_profit = float(row.get("Profit", 0) or 0)
    max_loss = float(row.get("Max_Loss", row.get("Risk", 0)) or 0)
    profit_index = float(row.get("RR", 0) or 0)

    try:
        breakeven = float(str(breakeven_txt).split("â€“")[0])
    except Exception:
        breakeven = spot

    base_strike = spot
    try:
        if "C" in strikes_txt or "P" in strikes_txt:
            first = (
                strikes_txt.replace("C", "")
                .replace("P", "")
                .replace(" ", "")
                .split("/")[0]
            )
            base_strike = float(first)
    except Exception:
        base_strike = spot

    try:
        width = float(row.get("Width", 10)) if str(row.get("Width", "")).strip() not in ("", "10/10") else 10.0
    except Exception:
        width = 10.0

    legs, meta = build_strategy_legs(strategy, spot=spot, base_strike=base_strike, width=width, credit_debit=2.0)

    lo = min(spot, base_strike) - width * 2
    hi = max(spot, base_strike + width) + width * 2
    lo = max(lo, 0.0)
    sT = np.linspace(lo, hi, 200)
    payoff = strategy_payoff(sT, legs)

    max_profit_val = float(np.nanmax(payoff))
    max_loss_val = float(np.nanmin(payoff))

    abs_max = max(abs(max_profit_val), abs(max_loss_val), 10)
    y_pad = abs_max * 0.2
    y_min = -abs_max - y_pad
    y_max = abs_max + y_pad

    fig = go.Figure()

    fig.add_trace(
        go.Scatter(
            x=sT,
            y=payoff,
            mode="lines",
            line=dict(color="royalblue", width=2),
        )
    )

    fig.add_hline(y=0, line_dash="dash", line_color="black")
    fig.add_vline(x=spot, line_dash="dot", line_color="#FF8C00", line_width=2)

    if lo <= breakeven <= hi:
        fig.add_vline(x=breakeven, line_dash="dot", line_color="#0000FF", line_width=1.5)

    fig.add_hrect(
        y0=y_min,
        y1=y_max,
        x0=sT.min(),
        x1=breakeven,
        fillcolor="rgba(255, 0, 0, 0.25)",
        line_width=0,
        layer="below",
    )
    fig.add_hrect(
        y0=y_min,
        y1=y_max,
        x0=breakeven,
        x1=sT.max(),
        fillcolor="rgba(0, 180, 0, 0.25)",
        line_width=0,
        layer="below",
    )

    idx_max = int(np.argmax(payoff))
    idx_min = int(np.argmin(payoff))
    s_max = float(sT[idx_max])
    s_min = float(sT[idx_min])

    fig.add_annotation(
        x=s_max,
        y=max_profit_val,
        text=f"Max Profit: {max_profit_val:.0f}",
        showarrow=True,
        arrowhead=1,
        ax=0,
        ay=-15,
        font=dict(size=7),
    )
    fig.add_annotation(
        x=s_min,
        y=max_loss_val,
        text=f"Max Loss: {max_loss_val:.0f}",
        showarrow=True,
        arrowhead=1,
        ax=0,
        ay=15,
        font=dict(size=7),
    )

    fig.update_layout(
        title=None,
        xaxis_title=None,
        yaxis_title=None,
        margin=dict(l=15, r=5, t=8, b=18),
        width=300,
        height=110,
        showlegend=False,
        template="plotly_white",
        font=dict(size=8),
    )
    fig.update_yaxes(range=[y_min, y_max])

    fig.add_annotation(
        xref="paper", yref="paper",
        x=0.5, y=1.00,
        xanchor="center",
        yanchor="bottom",
        text=f"<span style='font-size:9px; font-weight:bold;'>{strategy} - {sym}</span>",
        showarrow=False,
        align="center",
    )

    metrics_html = (
        f"<span style='color:#006400; font-weight:bold;'>Prem: ${premium_received:.0f}</span> Â· "
        f"<span style='color:#00008B; font-weight:bold;'>MaxP: ${max_profit:.0f}</span> Â· "
        f"<span style='color:#8B0000; font-weight:bold;'>MaxL: ${max_loss:.0f}</span> Â· "
        f"<span style='color:#8B008B; font-weight:bold;'>PI: {profit_index:.2f}</span> Â· "
        f"<span style='color:#0000FF; font-weight:bold;'>BE: {breakeven}</span>"
    )
    fig.add_annotation(
        xref="paper", yref="paper",
        x=0.5, y=0.95,
        xanchor="center",
        yanchor="bottom",
        text=metrics_html,
        showarrow=False,
        align="center",
        font=dict(size=7),
    )

    view_text = str(row.get("View", "ðŸŸ¢ Bull"))
    market_text = str(row.get("Market", "Bullish Stable"))
    tags_html = f"<span style='font-weight:bold;'>{view_text} Â· {market_text}</span>"
    fig.add_annotation(
        xref="paper", yref="paper",
        x=0.5, y=0.90,
        xanchor="center",
        yanchor="bottom",
        text=tags_html,
        showarrow=False,
        align="center",
        font=dict(size=7),
    )

    theta_text = "Fast"
    vix_text = "Highâ†“"
    trend_text = "Flat/Down"
    vol_oi_text = "OK"
    call_text = "Good"

    panel_html = (
        f"<span style='background-color:#FFFACD; padding:1px 2px; border-radius:2px;'>Th: {theta_text}</span><br>"
        f"<span style='background-color:#FFE4E1; padding:1px 2px; border-radius:2px;'>VIX: {vix_text}</span><br>"
        f"<span style='background-color:#E6F2FF; padding:1px 2px; border-radius:2px;'>Trend: {trend_text}</span><br>"
        f"<span style='background-color:#E8FFE8; padding:1px 2px; border-radius:2px;'>Vol/OI: {vol_oi_text}</span><br>"
        f"<span style='background-color:#F0E6FF; padding:1px 2px; border-radius:2px;'>Call: {call_text}</span>"
    )
    fig.add_annotation(
        xref="paper", yref="paper",
        x=0.02, y=0.85,
        xanchor="left",
        yanchor="top",
        text=panel_html,
        showarrow=False,
        align="left",
        font=dict(size=6),
        bordercolor="#cccccc",
        borderwidth=1,
        borderpad=1,
        bgcolor="#ffffff",
    )

    def add_bottom_name(x_val, text, color):
        fig.add_annotation(
            x=x_val,
            xref="x",
            yref="paper",
            y=0.0,
            text=text,
            showarrow=True,
            arrowhead=1,
            ax=0,
            ay=10,
            font=dict(size=6, color=color),
            yanchor="top",
        )

    add_bottom_name(spot,                "Now",   "#FF8C00")
    add_bottom_name(base_strike,         "Lower", "#8B4513")
    add_bottom_name(base_strike + width, "Upper", "#808080")
    add_bottom_name(breakeven,           "BE",    "#0000FF")

    subtitle = (
        f"{meta['MarketBias']} | {meta['GrowthIncome']} | "
        f"{meta['RiskStyle']} / {meta['ProfitStyle']} | "
        f"{meta['VolView']} | DTE: {dte}"
    )
    structure = f"Strikes: {strikes_txt} | Spot: {spot:.2f}"
    comment_text = (
        f"{company_name} ({sym}) â€“ {strategy}\n"
        f"{subtitle}\n"
        f"{structure}\n"
        f"B/E or Range: {breakeven_txt}\n"
        f"Overview: {meta['Text']}\n"
        f"Notes: {base_comment}"
    )

    return fig, comment_text

# =========================
# TRADES TABLE: INSERT HELPER
# =========================
def insert_rank1_trade_from_row(trade_date_now, row):
    entry_date = trade_date_now
    ticker = row["Stock"]
    strategy = row["Strategy"]
    s_type = row.get("Type", None)
    direction = row.get("View", None)
    rank = int(row["Rank"])
    expiry_date = row.get("Expiry", None)
    dte = int(row.get("DTE", 0)) if pd.notna(row.get("DTE", None)) else None
    strikes = row.get("Strikes", None)
    width = float(row.get("Width", 0)) if "Width" in row else None
    entry_price = float(row.get("Price", 0)) if pd.notna(row.get("Price", None)) else None
    assignment_risk = row.get("Assignment", None)
    liquidity = row.get("Liquidity", None)
    comment = row.get("Comment", None)

    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS options_trades (
                trade_id INTEGER PRIMARY KEY AUTOINCREMENT,
                entry_date TEXT NOT NULL,
                exit_date TEXT,
                ticker TEXT NOT NULL,
                strategy TEXT NOT NULL,
                type TEXT,
                direction TEXT,
                rank INTEGER,
                expiry_date TEXT,
                days_to_expiration INTEGER,
                strikes TEXT,
                width REAL,
                entry_price REAL,
                exit_price REAL,
                quantity INTEGER DEFAULT 1,
                pnl_dollar REAL,
                pnl_percent REAL,
                status TEXT DEFAULT 'OPEN',
                assignment_risk TEXT,
                liquidity TEXT,
                comment TEXT
            );
            """
        )
        cur.execute(
            """
            SELECT COUNT(*) FROM options_trades
            WHERE entry_date = ? AND ticker = ? AND strategy = ? AND rank = ? AND status = 'OPEN'
            """,
            (entry_date, ticker, strategy, rank),
        )
        exists = cur.fetchone()[0]
        if exists:
            return

        cur.execute(
            """
            INSERT INTO options_trades (
                entry_date, ticker, strategy, type, direction,
                rank, expiry_date, days_to_expiration, strikes, width,
                entry_price, quantity, status, assignment_risk, liquidity, comment
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'OPEN', ?, ?, ?)
            """,
            (
                entry_date, ticker, strategy, s_type, direction,
                rank, expiry_date, dte, strikes, width,
                entry_price, 1, assignment_risk, liquidity, comment
            ),
        )
        conn.commit()

# =========================
# MAIN
# =========================
if __name__ == "__main__":
    print("=== NYSE_Telegram starting ===")

    td_now_raw = latest_trade_date_now()
    print("[DEBUG] latest_trade_date_now:", td_now_raw)
    if not td_now_raw:
        print("No trade_date_now found.")
        raise SystemExit(1)

    td_now = td_now_raw
    print("[DEBUG] Using trade_date_now:", td_now)

    out_dir_today = os.path.join(BASE_OUT_DIR, td_now)
    os.makedirs(out_dir_today, exist_ok=True)
    print("[DEBUG] out_dir_today:", out_dir_today)

    symbols = get_symbols_for_trade_date(td_now)
    print("[DEBUG] symbols count:", len(symbols))
    print("[DEBUG] first few symbols:", symbols[:10])
    if not symbols:
        print(f"No symbols for {td_now}")
        raise SystemExit(1)

    yahoo_prices = get_yahoo_prices()
    print("[DEBUG] yahoo_prices loaded")

    index_like = {"SPY", "QQQ", "VOO", "^GSPC", "^NDX"}
    index_syms = [s for s in symbols if s.upper() in index_like]
    other_syms = [s for s in symbols if s.upper() not in index_like]
    ordered_symbols = index_syms + other_syms
    print("[DEBUG] final symbols to process:", ordered_symbols)

    print(f"[INFO] Creating per-symbol charts for {td_now}")

    master_rows = []
    per_stock_strategies = {}

    for idx, sym in enumerate(ordered_symbols, start=1):
        try:
            print(f"[DEBUG] entering loop for {sym} (#{idx})")
            sym_upper = sym.upper()
            print(f"[INFO] {sym}: OI + Price + Volume chart...")
            oi_fig, full_name, spot = make_oi_chart(sym, td_now, yahoo_prices)
            print(
                f"[DEBUG] make_oi_chart returned full_name={full_name}, "
                f"spot={spot}"
            )

            if oi_fig is not None and full_name is not None:
                if GENERATE_OI_PNG:
                    out_dir_today = os.path.join(BASE_OUT_DIR, td_now)
                    os.makedirs(out_dir_today, exist_ok=True)
                    out_file = os.path.join(out_dir_today, f"{sym_upper}_OI.png")
                    oi_fig.write_image(out_file, width=1800, height=oi_fig.layout.height)
                    print(f"[INFO] Saved OI chart to {out_file}")
                else:
                    print("[INFO] OI PNG generation disabled by flag.")

                strategies = build_strategies_for_symbol(sym_upper, td_now, spot)
                if strategies:
                    df_strat = pd.DataFrame(strategies)
                    per_stock_strategies[sym_upper] = (df_strat, full_name, spot)

                    top = df_strat.sort_values("Rank").iloc[0]
                    master_rows.append({
                        "Rank": len(master_rows) + 1,
                        "Stock": sym_upper,
                        "Price": top["Price"],
                        "Change": top["Change"],
                        "View": top["View"],
                        "Best_Strategy": top["Strategy"],
                        "Risk_$": top["Risk"],
                        "Max_Profit_$": top["Profit"],
                        "RR": top["RR"],
                        "Liquidity": top["Liquidity"],
                    })

                    insert_rank1_trade_from_row(td_now, top)
                else:
                    print(f"[WARN] No strategies list for {sym_upper}")
            else:
                print(f"[WARN] Chart not created for {sym} (#{idx})")
        except Exception as e:
            print(f"[WARN] Failed for {sym} (#{idx}): {e}")

    if GENERATE_EXCEL and master_rows:
        df_master = pd.DataFrame(master_rows)
        excel_path = os.path.join(out_dir_today, f"Summary_{td_now}.xlsx")
        with pd.ExcelWriter(excel_path, engine="xlsxwriter") as writer:
            df_master.to_excel(writer, sheet_name="Top_Strategies", index=False)
        print(f"[OK] Excel summary written to {excel_path}")
    else:
        print("[INFO] Excel generation skipped or no rows.")

    print("=== NYSE_Telegram finished ===")