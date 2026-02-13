import os
import sqlite3
from datetime import datetime, time

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import yfinance as yf
import pandas_market_calendars as mcal

# =========================
# CONFIG
# =========================
DATA_DIR = r"C:\\Users\\srini\\Options_chain_data"
DB_PATH = os.path.join(DATA_DIR, "US_data.db")

TABLE_OPTIONS_DAILY = "options_daily"
TABLE_OPTIONS_CHANGE = "options_change"
TABLE_STOCK_DAILY = "stock_daily"

BASE_OUT_DIR = os.path.join(DATA_DIR, "US_CHARTS")
os.makedirs(BASE_OUT_DIR, exist_ok=True)

MIN_PREMIUM_COLLECTED = 0.0
MAX_MAX_LOSS = None

# colors
CALL_CURR_COLOR = "royalblue"
PUT_CURR_COLOR = "firebrick"
CALL_PREV_LINE = "#1f4e9d"  # dark blue for outer
PUT_PREV_LINE = "#9b1c1c"   # dark red for outer

# NYSE calendar
nyse_cal = mcal.get_calendar("NYSE")  # [web:5]

# =========================
# DB helpers
# =========================
def get_conn():
    return sqlite3.connect(DB_PATH)

def latest_trade_date_now():
    with get_conn() as conn:
        df = pd.read_sql(
            f"SELECT MAX(trade_date_now) AS td FROM {TABLE_OPTIONS_CHANGE}", conn
        )
    if df.empty or df["td"].iloc[0] is None:
        return None
    return df["td"].iloc[0]

def parse_ddmmmyyyy(d):
    return datetime.strptime(d, "%d%b%Y")

def get_prev_trade_date(trade_date_now):
    dt_now = pd.to_datetime(trade_date_now, format="%d%b%Y")
    sched = nyse_cal.schedule(
        start_date=dt_now - pd.Timedelta(days=15), end_date=dt_now
    )
    days = sched.index
    if len(days) == 0:
        return None
    prev_days = days[days < dt_now]
    if len(prev_days) == 0:
        return None
    prev = prev_days[-1]
    return prev.strftime("%d%b%Y")

def previous_trade_date_now(curr_trade_date_now):
    return get_prev_trade_date(curr_trade_date_now)

def is_today(td_now):
    dt = datetime.strptime(td_now, "%d%b%Y").date()
    return dt == datetime.today().date()

def trade_date_now_to_mmddyyyy(td_now):
    dt = datetime.strptime(td_now, "%d%b%Y")
    return dt.strftime("%m-%d-%Y")

def get_symbols_for_trade_date(td_now):
    with get_conn() as conn:
        df = pd.read_sql(
            f"""
            SELECT DISTINCT ticker
            FROM {TABLE_OPTIONS_CHANGE}
            WHERE trade_date_now = ?
            """,
            conn,
            params=(td_now,),
        )
    return sorted(df["ticker"].tolist())

def get_stock_close(symbol, trade_date_now):
    dt = parse_ddmmmyyyy(trade_date_now)
    td_mmddyyyy = dt.strftime("%m-%d-%Y")
    with get_conn() as conn:
        df = pd.read_sql(
            f"""
            SELECT close
            FROM {TABLE_STOCK_DAILY}
            WHERE ticker = ? AND trade_date = ?
            """,
            conn,
            params=(symbol, td_mmddyyyy),
        )
    if df.empty:
        return None
    return float(df["close"].iloc[0])

def get_company_name(symbol, trade_date_now):
    with get_conn() as conn:
        df = pd.read_sql(
            f"""
            SELECT company_name_now
            FROM {TABLE_OPTIONS_CHANGE}
            WHERE ticker = ? AND trade_date_now = ?
            LIMIT 1
            """,
            conn,
            params=(symbol, trade_date_now),
        )
    if df.empty:
        return symbol.upper()
    return str(df["company_name_now"].iloc[0])

def get_all_expiries(symbol, trade_date_now):
    with get_conn() as conn:
        df = pd.read_sql(
            f"""
            SELECT DISTINCT expiry_date
            FROM {TABLE_OPTIONS_CHANGE}
            WHERE ticker = ? AND trade_date_now = ?
            ORDER BY expiry_date
            """,
            conn,
            params=(symbol, trade_date_now),
        )
    return df["expiry_date"].tolist()

# =========================
# Yahoo helpers
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
    return prices  # [web:1]

def mapping_ratio(ticker, prices=None):
    return 1.0

# =========================
# 60-day volume averages (exclude current day)
# =========================
def get_60d_volume_avgs(symbol, trade_date_now):
    """
    Returns ['expiry_date','strike','vol_call_avg60','vol_put_avg60']
    for trade_date_now, where avg is over previous 60 calendar days
    excluding current day, based on vol_Call / vol_Put. [web:6]
    """
    current_dt = parse_ddmmmyyyy(trade_date_now)
    start_dt = current_dt - pd.Timedelta(days=80)

    with get_conn() as conn:
        df = pd.read_sql(
            f"""
            SELECT ticker, trade_date, expiry_date, strike,
                   vol_Call, vol_Put
            FROM {TABLE_OPTIONS_DAILY}
            WHERE ticker = ?
              AND trade_date BETWEEN ? AND ?
            """,
            conn,
            params=(
                symbol,
                start_dt.strftime("%d%b%Y"),
                trade_date_now,
            ),
        )

    if df.empty:
        return pd.DataFrame(
            columns=["expiry_date", "strike", "vol_call_avg60", "vol_put_avg60"]
        )

    df["trade_date_dt"] = pd.to_datetime(
        df["trade_date"], format="%d%b%Y", errors="coerce"
    )
    df = df.dropna(subset=["trade_date_dt"])
    df = df.sort_values(["expiry_date", "strike", "trade_date_dt"])

    def _rolling_60(s):
        return s.shift(1).rolling(60, min_periods=1).mean()

    df["vol_call_avg60"] = (
        df.groupby(["expiry_date", "strike"], group_keys=False)["vol_Call"]
          .apply(_rolling_60)
    )
    df["vol_put_avg60"] = (
        df.groupby(["expiry_date", "strike"], group_keys=False)["vol_Put"]
          .apply(_rolling_60)
    )

    mask_today = df["trade_date"] == trade_date_now
    df_today = df.loc[
        mask_today,
        ["expiry_date", "strike", "vol_call_avg60", "vol_put_avg60"]
    ].copy()

    if df_today.empty:
        return pd.DataFrame(
            columns=["expiry_date", "strike", "vol_call_avg60", "vol_put_avg60"]
        )
    return df_today

# =========================
# One-expiry slice with prev values + volume
# =========================
def get_options_with_prev_values(symbol, trade_date_now, expiry):
    trade_date_prev = get_prev_trade_date(trade_date_now)

    with get_conn() as conn:
        df_today = pd.read_sql(
            f"""
            SELECT ticker, expiry_date, strike, trade_date,
                   openInt_Call, openInt_Put,
                   call_open AS call_open_now,
                   call_high AS call_high_now,
                   call_low  AS call_low_now,
                   call_close AS call_close_now,
                   put_open  AS put_open_now,
                   put_high  AS put_high_now,
                   put_low   AS put_low_now,
                   put_close AS put_close_now,
                   vol_Call  AS vol_call_now,
                   vol_Put   AS vol_put_now
            FROM {TABLE_OPTIONS_DAILY}
            WHERE ticker = ?
              AND trade_date = ?
              AND expiry_date = ?
            """,
            conn,
            params=(symbol, trade_date_now, expiry),
        )

        df_prev = pd.read_sql(
            f"""
            SELECT ticker, expiry_date, strike,
                   openInt_Call AS openInt_Call_prev,
                   openInt_Put  AS openInt_Put_prev,
                   call_open AS call_open_prev,
                   call_high AS call_high_prev,
                   call_low  AS call_low_prev,
                   call_close AS call_close_prev,
                   put_open  AS put_open_prev,
                   put_high  AS put_high_prev,
                   put_low   AS put_low_prev,
                   put_close AS put_close_prev
            FROM {TABLE_OPTIONS_DAILY}
            WHERE ticker = ?
              AND trade_date = ?
              AND expiry_date = ?
            """,
            conn,
            params=(symbol, trade_date_prev, expiry),
        )

        df_chg = pd.read_sql(
            f"""
            SELECT ticker, expiry_date, strike,
                   change_OI_Call, change_OI_Put
            FROM {TABLE_OPTIONS_CHANGE}
            WHERE ticker = ?
              AND trade_date_now = ?
              AND expiry_date = ?
            """,
            conn,
            params=(symbol, trade_date_now, expiry),
        )

    if df_today.empty:
        print(f"[WARN] {symbol} {trade_date_now} {expiry}: no today rows")
        return None
    if df_prev.empty:
        print(f"[WARN] {symbol} prev {trade_date_prev} {expiry}: no prev rows")
    if df_chg.empty:
        print(f"[WARN] {symbol} {trade_date_now} {expiry}: no options_change rows")

    key = ["ticker", "expiry_date", "strike"]
    df = (
        df_today
        .merge(df_prev, on=key, how="left")
        .merge(df_chg, on=key, how="left")
    )

    # attach 60-day avg volumes
    df_vol60 = get_60d_volume_avgs(symbol, trade_date_now)
    df = df.merge(df_vol60, on=["expiry_date", "strike"], how="left")

    num_cols = [
        "strike",
        "openInt_Call", "openInt_Put",
        "openInt_Call_prev", "openInt_Put_prev",
        "call_open_now", "call_high_now", "call_low_now", "call_close_now",
        "put_open_now",  "put_high_now",  "put_low_now",  "put_close_now",
        "call_open_prev", "call_high_prev", "call_low_prev", "call_close_prev",
        "put_open_prev",  "put_high_prev",  "put_low_prev",  "put_close_prev",
        "change_OI_Call", "change_OI_Put",
        "vol_call_now", "vol_put_now",
        "vol_call_avg60", "vol_put_avg60",
    ]
    for col in num_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    for col in [
        "openInt_Call", "openInt_Put",
        "openInt_Call_prev", "openInt_Put_prev",
    ]:
        if col in df.columns:
            df[col] = df[col].fillna(0.0)

    # ΔOI
    df["change_OI_Call"] = (df["openInt_Call"] - df["openInt_Call_prev"]).fillna(0.0)
    df["change_OI_Put"] = (df["openInt_Put"] - df["openInt_Put_prev"]).fillna(0.0)

    # Effective prices
    df["call_close_eff"] = df["call_close_now"].fillna(df["call_close_prev"])
    df["put_close_eff"] = df["put_close_now"].fillna(df["put_close_prev"])

    # Effective OI
    df["openInt_Call_eff"] = (
        df["openInt_Call"].replace(0, np.nan)
          .fillna(df["openInt_Call_prev"])
          .fillna(0.0)
    )
    df["openInt_Put_eff"] = (
        df["openInt_Put"].replace(0, np.nan)
          .fillna(df["openInt_Put_prev"])
          .fillna(0.0)
    )

    # Flags for missing OI
    df["has_prev_OI_call"] = df["openInt_Call_prev"].fillna(0) > 0
    df["has_prev_OI_put"]  = df["openInt_Put_prev"].fillna(0) > 0
    df["has_now_OI_call"]  = df["openInt_Call"].fillna(0) > 0
    df["has_now_OI_put"]   = df["openInt_Put"].fillna(0) > 0

    df = df.dropna(subset=["strike"])
    df = df.sort_values("strike").reset_index(drop=True)
    return df

# =========================
# Dynamic inner width helper
# =========================
def compute_inner_bar_width(strikes_conv, fraction=0.35):
    """
    Compute inner bar width as a fraction of the minimum distance between
    distinct strikes on this subplot. [web:11]
    """
    unique_strikes = np.sort(np.unique(strikes_conv))
    if len(unique_strikes) < 2:
        return 0.2
    diffs = np.diff(unique_strikes)
    diffs = diffs[diffs != 0]
    if len(diffs) == 0:
        min_step = 1.0
    else:
        min_step = np.min(diffs)
    return float(min_step * fraction)

# =========================
# OI + Price & Volume mirrored chart
# =========================
def make_oi_chart(symbol, trade_date_now, yahoo_prices):
    sym = symbol.upper()
    company_name = get_company_name(sym, trade_date_now)
    expiries = get_all_expiries(sym, trade_date_now)
    if not expiries:
        print(f"[WARN] {sym}: no expiries on {trade_date_now}")
        return None, None, None

    spot = get_stock_close(sym, trade_date_now)
    if spot is None:
        print(f"[WARN] {sym}: no stock close on {trade_date_now}")
        return None, None, None

    ratio = mapping_ratio(sym, yahoo_prices)
    spot_conv = spot * ratio

    slices = []
    for expiry in expiries:
        df = get_options_with_prev_values(sym, trade_date_now, expiry)
        if df is None or df.empty:
            slices.append(None)
            continue

        df = df.copy()
        df["strike_conv"] = df["strike"] * ratio

        # OI
        df["call_oi_prev"] = df["openInt_Call_prev"]
        df["put_oi_prev"]  = df["openInt_Put_prev"]

        df["call_oi_now"] = df["openInt_Call_eff"]
        df["put_oi_now"]  = df["openInt_Put_eff"]

        df["call_oi"] = df["call_oi_prev"]
        df["put_oi"]  = df["put_oi_prev"]

        label_gap_factor = 1.10
        df["put_oi_plot"]     = -df["put_oi"] * label_gap_factor
        df["put_oi_now_plot"] = -df["put_oi_now"] * label_gap_factor

        df["call_coi"] = df["change_OI_Call"]
        df["put_coi"]  = -df["change_OI_Put"]

        # Price (prev & current as values; labels only current)
        df["call_price_prev_close"] = df["call_close_prev"]
        df["put_price_prev_close"]  = -df["put_close_prev"]
        df["call_close_now_val"]    = df["call_close_eff"]
        df["put_close_now_val"]     = -df["put_close_eff"]

        # -------- ONLY CURRENT PRICE LABELS --------
        call_label = df["call_close_eff"].copy()
        put_label = df["put_close_eff"].abs().copy()

        call_label = call_label.where(call_label.notna(), "N")
        put_label  = put_label.where(put_label.notna(),  "N")

        df["call_text_color"] = "black"
        df["put_text_color"]  = "black"

        mask_call_zero = (~df["has_prev_OI_call"]) & (~df["has_now_OI_call"])
        mask_put_zero  = (~df["has_prev_OI_put"])  & (~df["has_now_OI_put"])

        df.loc[mask_call_zero, "call_text_color"] = "red"
        df.loc[mask_put_zero,  "put_text_color"]  = "red"

        df["call_label"] = call_label
        df["put_label"]  = put_label
        # -------------------------------------------

        slices.append(df)

    n = len(expiries)
    fig = make_subplots(
        rows=n,
        cols=2,
        shared_xaxes=False,
        vertical_spacing=0.06,
        horizontal_spacing=0.06,
        subplot_titles=[
            f"{e} – OI (prev vs now) / ΔOI" if j == 0
            else f"{e} – Price (prev vs now) / Vol (last + 60d avg ex‑today)"
            for e in expiries for j in range(2)
        ],
        specs=[
            [{"secondary_y": True}, {"secondary_y": True}]
            for _ in range(n)
        ],
    )

    fig.update_yaxes(showticklabels=True, title_font=dict(size=11))
    fig.update_layout(barmode="overlay")

    for i, (expiry, df) in enumerate(zip(expiries, slices), start=1):
        if df is None or df.empty:
            continue

        show_legend = (i == 1)

        # Precompute ranges
        max_oi_here = max(
            abs(df["call_oi"]).max(),
            abs(df["put_oi_plot"]).max(),
            abs(df["call_oi_now"]).max(),
            abs(df["put_oi_now_plot"]).max(),
        )
        if not np.isfinite(max_oi_here) or max_oi_here <= 0:
            max_oi_here = 1.0

        max_coi_here = max(
            abs(df["call_coi"]).max(),
            abs(df["put_coi"]).max(),
        )
        if not np.isfinite(max_coi_here) or max_coi_here <= 0:
            max_coi_here = 1.0

        max_price_here = max(
            abs(df["call_price_prev_close"]).max(),
            abs(df["put_price_prev_close"]).max(),
            abs(df["call_close_now_val"]).max(),
            abs(df["put_close_now_val"]).max(),
        )
        if not np.isfinite(max_price_here) or max_price_here <= 0:
            max_price_here = 1.0

        max_vol_here = max(
            abs(df["vol_call_now"]).max(),
            abs(df["vol_put_now"]).max(),
            abs(df["vol_call_avg60"]).max(),
            abs(df["vol_put_avg60"]).max(),
        )
        if not np.isfinite(max_vol_here) or max_vol_here <= 0:
            max_vol_here = 1.0

        # x ticks and inner width
        strikes_this_expiry = sorted(df["strike_conv"].unique().tolist())
        ticktext_this = [f"{v:.1f}" for v in strikes_this_expiry]
        inner_width = compute_inner_bar_width(strikes_this_expiry, fraction=0.35)

        # ----- LEFT: OI -----
        # prev OI – outer bars (NO price labels)
        fig.add_trace(
            go.Bar(
                x=df["strike_conv"],
                y=df["call_oi"],
                name="Call OI prev",
                marker_color=CALL_PREV_LINE,
                opacity=0.9,
                showlegend=show_legend,
                cliponaxis=False,
            ),
            row=i,
            col=1,
            secondary_y=False
        )
        fig.add_trace(
            go.Bar(
                x=df["strike_conv"],
                y=df["put_oi_plot"],
                name="Put OI prev",
                marker_color=PUT_PREV_LINE,
                opacity=0.9,
                showlegend=show_legend,
                cliponaxis=False,
            ),
            row=i,
            col=1,
            secondary_y=False
        )

        # now OI – inner bars (no embedded text; labels via scatter)
        fig.add_trace(
            go.Bar(
                x=df["strike_conv"],
                y=df["call_oi_now"],
                name="Call OI now",
                marker_color="white",
                marker_line_color=CALL_CURR_COLOR,
                marker_line_width=1,
                opacity=1.0,
                width=inner_width,
                showlegend=show_legend,
                text=None,
                cliponaxis=False,
            ),
            row=i,
            col=1,
            secondary_y=False
        )
        fig.add_trace(
            go.Bar(
                x=df["strike_conv"],
                y=df["put_oi_now_plot"],
                name="Put OI now",
                marker_color="white",
                marker_line_color=PUT_CURR_COLOR,
                marker_line_width=1,
                opacity=1.0,
                width=inner_width,
                showlegend=show_legend,
                text=None,
                cliponaxis=False,
            ),
            row=i,
            col=1,
            secondary_y=False
        )

        # ---- label positions based on tops of prev+now ----
        max_call_oi_here = max(
            abs(df["call_oi"]).max(),
            abs(df["call_oi_now"]).max(),
        )
        if not np.isfinite(max_call_oi_here) or max_call_oi_here <= 0:
            max_call_oi_here = max_oi_here

        max_put_oi_here = max(
            abs(df["put_oi_plot"]).max(),
            abs(df["put_oi_now_plot"]).max(),
        )
        if not np.isfinite(max_put_oi_here) or max_put_oi_here <= 0:
            max_put_oi_here = max_oi_here

        # Put label on top of both prev+now put bars (negative side)
        put_label_y_offset = -max_put_oi_here * 1.05
        fig.add_trace(
            go.Scatter(
                x=df["strike_conv"],
                y=[put_label_y_offset] * len(df),
                mode="text",
                text=df["put_label"],
                textfont=dict(size=11, color=df["put_text_color"]),
                showlegend=False,
            ),
            row=i,
            col=1,
            secondary_y=False
        )

        # Call label on top of both prev+now call bars (positive side)
        call_label_y_offset = max_call_oi_here * 1.05
        fig.add_trace(
            go.Scatter(
                x=df["strike_conv"],
                y=[call_label_y_offset] * len(df),
                mode="text",
                text=df["call_label"],
                textfont=dict(size=11, color=df["call_text_color"]),
                showlegend=False,
            ),
            row=i,
            col=1,
            secondary_y=False
        )

        # ΔOI lines
        fig.add_trace(
            go.Scatter(
                x=df["strike_conv"],
                y=df["call_coi"],
                name="Call ΔOI",
                mode="lines+markers",
                line=dict(color="lightskyblue", width=2),
                marker=dict(size=6),
                showlegend=show_legend,
            ),
            row=i,
            col=1,
            secondary_y=True
        )
        fig.add_trace(
            go.Scatter(
                x=df["strike_conv"],
                y=df["put_coi"],
                name="Put ΔOI",
                mode="lines+markers",
                line=dict(color="lightcoral", width=2),
                marker=dict(size=6),
                showlegend=show_legend,
            ),
            row=i,
            col=1,
            secondary_y=True
        )

        # ----- RIGHT: Price (primary y) & Volume (secondary y) -----
        # Prev price – outer bars
        fig.add_trace(
            go.Bar(
                x=df["strike_conv"],
                y=df["call_price_prev_close"],
                name="Call Prev Price",
                marker_color=CALL_PREV_LINE,
                opacity=0.9,
                showlegend=show_legend,
            ),
            row=i,
            col=2,
            secondary_y=False
        )
        fig.add_trace(
            go.Bar(
                x=df["strike_conv"],
                y=df["put_price_prev_close"],
                name="Put Prev Price",
                marker_color=PUT_PREV_LINE,
                opacity=0.9,
                showlegend=show_legend,
            ),
            row=i,
            col=2,
            secondary_y=False
        )

        # Current price – inner bars
        fig.add_trace(
            go.Bar(
                x=df["strike_conv"],
                y=df["call_close_now_val"],
                name="Call Price Now",
                marker_color="white",
                marker_line_color=CALL_CURR_COLOR,
                marker_line_width=1,
                opacity=1.0,
                width=inner_width,
                showlegend=show_legend,
            ),
            row=i,
            col=2,
            secondary_y=False
        )
        fig.add_trace(
            go.Bar(
                x=df["strike_conv"],
                y=df["put_close_now_val"],
                name="Put Price Now",
                marker_color="white",
                marker_line_color=PUT_CURR_COLOR,
                marker_line_width=1,
                opacity=1.0,
                width=inner_width,
                showlegend=show_legend,
            ),
            row=i,
            col=2,
            secondary_y=False
        )

        # Last-day volume
        fig.add_trace(
            go.Scatter(
                x=df["strike_conv"],
                y=df["vol_call_now"],
                name="Call Vol (last)",
                mode="lines+markers",
                line=dict(color="lightskyblue", width=1.5),
                marker=dict(size=5),
                showlegend=show_legend,
            ),
            row=i,
            col=2,
            secondary_y=True
        )
        fig.add_trace(
            go.Scatter(
                x=df["strike_conv"],
                y=-df["vol_put_now"],
                name="Put Vol (last)",
                mode="lines+markers",
                line=dict(color="lightcoral", width=1.5),
                marker=dict(size=5),
                showlegend=show_legend,
            ),
            row=i,
            col=2,
            secondary_y=True
        )

        # 60d avg volume
        fig.add_trace(
            go.Scatter(
                x=df["strike_conv"],
                y=df["vol_call_avg60"],
                name="Call Vol (60d avg)",
                mode="lines",
                line=dict(color="royalblue", width=1.2, dash="dot"),
                showlegend=show_legend,
            ),
            row=i,
            col=2,
            secondary_y=True
        )
        fig.add_trace(
            go.Scatter(
                x=df["strike_conv"],
                y=-df["vol_put_avg60"],
                name="Put Vol (60d avg)",
                mode="lines",
                line=dict(color="firebrick", width=1.2, dash="dot"),
                showlegend=show_legend,
            ),
            row=i,
            col=2,
            secondary_y=True
        )

        # Axes ranges
        fig.update_yaxes(
            range=[-max_oi_here * 1.2, max_oi_here * 1.2],
            title_text="OI",
            row=i,
            col=1,
            secondary_y=False,
        )
        fig.update_yaxes(
            range=[-max_coi_here * 1.2, max_coi_here * 1.2],
            title_text="ΔOI",
            showgrid=False,
            row=i,
            col=1,
            secondary_y=True,
        )
        fig.update_yaxes(
            range=[-max_price_here * 1.2, max_price_here * 1.2],
            title_text="Price",
            row=i,
            col=2,
            secondary_y=False,
        )
        fig.update_yaxes(
            range=[-max_vol_here * 1.2, max_vol_here * 1.2],
            title_text="Volume",
            row=i,
            col=2,
            secondary_y=True,
        )

        fig.update_xaxes(
            tickmode="array",
            tickvals=strikes_this_expiry,
            ticktext=ticktext_this,
            title_text="Strike",
            row=i,
            col=1,
        )
        fig.update_xaxes(
            tickmode="array",
            tickvals=strikes_this_expiry,
            ticktext=ticktext_this,
            title_text="Strike",
            row=i,
            col=2,
        )

        fig.add_vline(
            x=spot_conv,
            line_dash="dash",
            line_color="black",
            row=i,
            col=1,
        )
        fig.add_vline(
            x=spot_conv,
            line_dash="dash",
            line_color="grey",
            row=i,
            col=2,
        )

        # PCR + bias summary box
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

        fig.add_annotation(
            xref="x domain",
            yref="y domain",
            x=0.02,
            y=0.95,
            row=i,
            col=2,
            text=(
                f"<b>PCR:</b> {pcr_oi:.2f}<br>"
                f"<b>Bias:</b> {bias}"
            ),
            showarrow=False,
            align="left",
            bgcolor="rgba(255,255,255,0.7)",
            bordercolor="black",
            borderwidth=1,
            font=dict(size=10),
        )

    fig.update_traces(cliponaxis=False)
    fig.update_layout(
        title=(
            f"{company_name} ({sym}) – OI, Price & Volume (mirrored) "
            f"{trade_date_now} (prev vs now), spot {spot_conv:.2f}"
        ),
        height=260 * max(1, len(expiries)),
        width=1800,
        bargap=0.02,
        bargroupgap=0.05,
        legend=dict(
            orientation="v",
            yanchor="top",
            y=1.0,
            xanchor="right",
            x=1.02,
        ),
        margin=dict(l=60, r=220, t=80, b=40),
    )
    return fig, company_name, spot

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

    now = datetime.now()
    print("[DEBUG] now:", now, " is_today(td_now_raw):", is_today(td_now_raw))

    if is_today(td_now_raw) and now.time() < time(9, 30):
        prev_td = previous_trade_date_now(td_now_raw)
        if prev_td:
            td_now = prev_td
            print(
                f"[INFO] Latest trade_date_now in DB is today ({td_now_raw}), "
                f"but before 09:30 -> using previous trading day {td_now}"
            )
        else:
            td_now = td_now_raw
            print(
                f"[WARN] Today is {td_now_raw} and no previous trading day found; "
                f"using {td_now}"
            )
    else:
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

    print(f"[DEBUG] ordered_symbols (indexes first): {ordered_symbols[:10]}")
    print(f"[INFO] Creating per‑symbol charts for {td_now}")

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
                oi_name = f"{idx:03d}_{full_name}_{sym_upper}_OI_Price_Volume.png"
                oi_path = os.path.join(out_dir_today, oi_name)
                print(f"[DEBUG] writing OI fig to {oi_path}")
                oi_fig.write_image(oi_path)
                print(f" [OK] {oi_path}")
            else:
                print(f"[WARN] Chart not created for {sym} (#{idx})")
        except Exception as e:
            print(f"[WARN] Failed for {sym} (#{idx}): {e}")

    print("=== NYSE_Telegram finished ===")
