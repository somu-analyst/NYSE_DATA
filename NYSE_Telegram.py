import os
import sqlite3
from datetime import datetime, time
import numpy as np
import pandas as pd

import plotly.graph_objects as go
from plotly.subplots import make_subplots
import yfinance as yf

# =========================
# CONFIG
# =========================

DATA_DIR = r"C:\Users\srini\Options_chain_data"
DB_PATH = os.path.join(DATA_DIR, "US_data.db")

TABLE_OPTIONS_DAILY  = "options_daily"
TABLE_OPTIONS_CHANGE = "options_change"
TABLE_STOCK_DAILY    = "stock_daily"

BASE_OUT_DIR = os.path.join(DATA_DIR, "NYSE_DATA", "US_CHARTS")
os.makedirs(BASE_OUT_DIR, exist_ok=True)

SUMMARY_EXCEL_PATH = os.path.join(DATA_DIR, "Daily_Spread_Summary.xlsx")

MIN_PREMIUM_COLLECTED = 0.0
MAX_MAX_LOSS = None

PREV_BAR_WIDTH = 0.45  # thickness of previous OI/Price inner bars

# =========================
# DB helpers
# =========================

def get_conn():
    return sqlite3.connect(DB_PATH)

def latest_trade_date_now():
    with get_conn() as conn:
        df = pd.read_sql(
            f"SELECT MAX(trade_date_now) AS td FROM {TABLE_OPTIONS_CHANGE}",
            conn
        )
    if df.empty or df["td"].iloc[0] is None:
        return None
    return df["td"].iloc[0]

def previous_trade_date_now(curr_trade_date_now):
    with get_conn() as conn:
        df = pd.read_sql(
            f"""
            SELECT DISTINCT trade_date_now
            FROM {TABLE_OPTIONS_CHANGE}
            ORDER BY trade_date_now DESC
            """,
            conn
        )
    if df.empty:
        return None
    dates = df["trade_date_now"].tolist()
    if curr_trade_date_now not in dates:
        return dates[1] if len(dates) > 1 else None
    idx = dates.index(curr_trade_date_now)
    if idx + 1 < len(dates):
        return dates[idx + 1]
    return None

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
            params=(td_now,)
        )
    return sorted(df["ticker"].tolist())

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
            params=(symbol, trade_date_now)
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
            params=(symbol, trade_date_now)
        )
    return df["expiry_date"].tolist()

def get_expiries_for_td(td_now):
    with get_conn() as conn:
        df = pd.read_sql(
            f"""
            SELECT DISTINCT expiry_date
            FROM {TABLE_OPTIONS_CHANGE}
            WHERE trade_date_now = ?
            ORDER BY expiry_date
            """,
            conn,
            params=(td_now,)
        )
    return df["expiry_date"].tolist()

def get_daily_stock(symbol, trade_date_now):
    td_mmddyyyy = trade_date_now_to_mmddyyyy(trade_date_now)
    with get_conn() as conn:
        df = pd.read_sql(
            f"""
            SELECT *
            FROM {TABLE_STOCK_DAILY}
            WHERE ticker = ? AND trade_date = ?
            """,
            conn,
            params=(symbol, td_mmddyyyy)
        )
    return df

# ===== options slice (daily + change) =====

def get_options_slice(symbol, trade_date_now, expiry):
    with get_conn() as conn:
        df_daily = pd.read_sql(
            f"""
            SELECT *
            FROM {TABLE_OPTIONS_DAILY}
            WHERE ticker = ? AND trade_date = ? AND expiry_date = ?
            """,
            conn,
            params=(symbol, trade_date_now, expiry)
        )
        df_chg = pd.read_sql(
            f"""
            SELECT *
            FROM {TABLE_OPTIONS_CHANGE}
            WHERE ticker = ? AND trade_date_now = ? AND expiry_date = ?
            """,
            conn,
            params=(symbol, trade_date_now, expiry)
        )
    if df_daily.empty or df_chg.empty:
        return None

    key = ["ticker", "strike", "expiry_date"]
    df = pd.merge(
        df_daily,
        df_chg,
        on=key,
        how="inner",
        suffixes=("_d", "_c")
    )

    num_cols = [
        "openInt_Call", "openInt_Put",
        "vol_Call", "vol_Put",
        "openInt_Call_now", "openInt_Put_now",
        "vol_Call_now", "vol_Put_now",
        "change_OI_Call", "change_OI_Put",
        "change_vol_Call", "change_vol_Put",
        "R1", "S1", "R12", "S12",
        "call_close_now", "put_close_now",
        "call_open_now", "put_open_now",
    ]
    for col in num_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    for col in [
        "openInt_Call", "openInt_Put",
        "openInt_Call_now", "openInt_Put_now",
        "vol_Call", "vol_Put",
        "vol_Call_now", "vol_Put_now",
    ]:
        if col in df.columns:
            df[col] = df[col].fillna(0.0)

    df["strike"] = pd.to_numeric(df["strike"], errors="coerce")
    df = df.dropna(subset=["strike"])
    return df

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
    return prices

def mapping_ratio(ticker, prices):
    t = str(ticker).upper()

    def ratio(etf_sym, underlying_sym):
        etf_p = prices.get(etf_sym, np.nan)
        und_p = prices.get(underlying_sym, np.nan)
        if np.isnan(etf_p) or np.isnan(und_p) or etf_p <= 0:
            return 1.0
        return und_p / etf_p

    if t == "IBIT":
        return ratio("IBIT", "BTC-USD")
    if t == "GLD":
        return ratio("GLD", "GC=F")
    if t == "SLV":
        return ratio("SLV", "SI=F")
    if t in ("SPY", "VOO"):
        return ratio(t, "^GSPC")
    if t == "QQQ":
        return ratio("QQQ", "^NDX")
    return 1.0

def map_spot_for_display(ticker, raw_spot, prices):
    if raw_spot is None or np.isnan(raw_spot):
        return raw_spot
    r = mapping_ratio(ticker, prices)
    return raw_spot * r

# =========================
# NEW FIGURE 1: OI + Price mirrored chart
# =========================

def make_oi_chart(symbol, trade_date_now, yahoo_prices):
    sym = symbol.upper()
    company_name = get_company_name(sym, trade_date_now)
    expiries = get_all_expiries(sym, trade_date_now)
    if not expiries:
        print(f"[WARN] {sym}: no expiries for {trade_date_now}")
        return None, None, None

    stock_df = get_daily_stock(sym, trade_date_now)
    if stock_df.empty:
        print(f"[WARN] {sym}: no stock_daily for {trade_date_now}")
        return None, None, None
    spot = float(stock_df["close"].iloc[0])

    ratio = mapping_ratio(sym, yahoo_prices)
    spot_conv = spot * ratio
    display_spot = spot_conv

    trade_date_prev = previous_trade_date_now(trade_date_now)
    if not trade_date_prev:
        print(f"[WARN] {sym}: no previous trade_date_now found; cannot compute previous OI/Price")
        return None, None, None

    with get_conn() as conn:
        df_prev_all = pd.read_sql(
            f"""
            SELECT ticker, expiry_date, strike,
                   openInt_Call AS openInt_Call_prev,
                   openInt_Put  AS openInt_Put_prev,
                   call_close   AS call_close_prev,
                   put_close    AS put_close_prev
            FROM {TABLE_OPTIONS_DAILY}
            WHERE ticker = ? AND trade_date = ?
            """,
            conn,
            params=(sym, trade_date_prev)
        )

    slices = []
    min_x = None
    max_x = None

    for expiry in expiries:
        df_now = get_options_slice(sym, trade_date_now, expiry)
        if df_now is None or df_now.empty:
            slices.append(None)
            continue

        df_prev = df_prev_all[df_prev_all["expiry_date"] == expiry]
        key = ["ticker", "expiry_date", "strike"]
        df = df_now.merge(df_prev, on=key, how="left")

        df = df.copy()
        df["strike_conv"] = df["strike"] * ratio

        # OI + prev OI + ΔOI
        df["call_oi"] = df["openInt_Call"]
        df["put_oi"]  = df["openInt_Put"]
        df["call_oi_prev"] = df.get("openInt_Call_prev", 0.0)
        df["put_oi_prev"]  = df.get("openInt_Put_prev", 0.0)

        df["put_oi_plot"]      = -df["put_oi"]
        df["put_oi_prev_plot"] = -df["put_oi_prev"]

        df["call_coi"] = df["change_OI_Call"]
        df["put_coi"]  = -df["change_OI_Put"]

        # Price + prev Price + ΔPrice
        df["call_price"]      = df["call_close_now"]
        df["put_price"]       = -df["put_close_now"]
        df["call_price_prev"] = df["call_close_prev"]
        df["put_price_prev"]  = -df["put_close_prev"]

        df["call_price_change_raw"] = df["call_close_now"] - df["call_close_prev"]
        df["put_price_change_raw"]  = df["put_close_now"] - df["put_close_prev"]

        df["call_price_change"] = df["call_price_change_raw"]
        df["put_price_change"]  = -df["put_price_change_raw"]

        x_min_here = df["strike_conv"].min()
        x_max_here = df["strike_conv"].max()
        min_x = x_min_here if min_x is None else min(min_x, x_min_here)
        max_x = x_max_here if max_x is None else max(max_x, x_max_here)

        slices.append(df)

    if min_x is None or max_x is None:
        print(f"[WARN] {sym}: no usable slices")
        return None, None, None

    x_margin = (max_x - min_x) * 0.05 if max_x > min_x else 1.0
    x_min_global = min_x - x_margin
    x_max_global = max_x + x_margin

    n = len(expiries)
    fig = make_subplots(
        rows=n,
        cols=2,
        shared_xaxes=True,
        vertical_spacing=0.06,
        horizontal_spacing=0.06,
        subplot_titles=[
            f"{e} – OI/ΔOI" if j == 0 else f"{e} – Price/ΔPrice"
            for e in expiries for j in range(2)
        ],
        specs=[
            [{"secondary_y": True}, {"secondary_y": True}]
            for _ in range(n)
        ],
    )

    fig.update_yaxes(showticklabels=True, title_font=dict(size=11))

    for i, (expiry, df) in enumerate(zip(expiries, slices), start=1):
        if df is None or df.empty:
            continue

        show_legend = (i == 1)

        # LEFT: OI + prev OI + ΔOI
        fig.add_trace(
            go.Bar(
                x=df["strike_conv"],
                y=df["call_oi"],
                name="Call OI",
                marker_color="royalblue",
                opacity=0.6,
                legendgroup="OI",
                showlegend=show_legend,
            ),
            row=i, col=1, secondary_y=False
        )
        fig.add_trace(
            go.Bar(
                x=df["strike_conv"],
                y=df["put_oi_plot"],
                name="Put OI",
                marker_color="firebrick",
                opacity=0.6,
                legendgroup="OI",
                showlegend=show_legend,
            ),
            row=i, col=1, secondary_y=False
        )

        fig.add_trace(
            go.Bar(
                x=df["strike_conv"],
                y=df["call_oi_prev"],
                name="Call OI prev",
                marker_color="white",           # unified
                opacity=1.0,
                width=PREV_BAR_WIDTH,
                marker_line_color="royalblue",
                marker_line_width=1,
                legendgroup="OI prev",
                showlegend=show_legend,
            ),
            row=i, col=1, secondary_y=False
        )
        fig.add_trace(
            go.Bar(
                x=df["strike_conv"],
                y=df["put_oi_prev_plot"],
                name="Put OI prev",
                marker_color="white",           # unified
                opacity=1.0,
                width=PREV_BAR_WIDTH,
                marker_line_color="firebrick",
                marker_line_width=1,
                legendgroup="OI prev",
                showlegend=show_legend,
            ),
            row=i, col=1, secondary_y=False
        )

        fig.add_trace(
            go.Scatter(
                x=df["strike_conv"],
                y=df["call_coi"],
                name="Call ΔOI",
                mode="lines+markers",
                line=dict(color="lightskyblue", width=2),
                marker=dict(size=5),
                legendgroup="ΔOI",
                showlegend=show_legend,
            ),
            row=i, col=1, secondary_y=True
        )
        fig.add_trace(
            go.Scatter(
                x=df["strike_conv"],
                y=df["put_coi"],
                name="Put ΔOI",
                mode="lines+markers",
                line=dict(color="lightcoral", width=2),
                marker=dict(size=5),
                legendgroup="ΔOI",
                showlegend=show_legend,
            ),
            row=i, col=1, secondary_y=True
        )

        # RIGHT: Price + prev Price + ΔPrice
        fig.add_trace(
            go.Bar(
                x=df["strike_conv"],
                y=df["call_price"],
                name="Call Price",
                marker_color="royalblue",
                opacity=0.7,
                legendgroup="Price",
                legend="legend2",
                showlegend=show_legend,
            ),
            row=i, col=2, secondary_y=False
        )
        fig.add_trace(
            go.Bar(
                x=df["strike_conv"],
                y=df["put_price"],
                name="Put Price",
                marker_color="firebrick",
                opacity=0.7,
                legendgroup="Price",
                legend="legend2",
                showlegend=show_legend,
            ),
            row=i, col=2, secondary_y=False
        )

        fig.add_trace(
            go.Bar(
                x=df["strike_conv"],
                y=df["call_price_prev"],
                name="Call Price prev",
                marker_color="white",          # unified
                opacity=1.0,
                width=PREV_BAR_WIDTH,
                marker_line_color="royalblue",
                marker_line_width=1,
                legendgroup="Price prev",
                legend="legend2",
                showlegend=show_legend,
            ),
            row=i, col=2, secondary_y=False
        )
        fig.add_trace(
            go.Bar(
                x=df["strike_conv"],
                y=df["put_price_prev"],
                name="Put Price prev",
                marker_color="white",          # unified
                opacity=1.0,
                width=PREV_BAR_WIDTH,
                marker_line_color="firebrick",
                marker_line_width=1,
                legendgroup="Price prev",
                legend="legend2",
                showlegend=show_legend,
            ),
            row=i, col=2, secondary_y=False
        )

        fig.add_trace(
            go.Scatter(
                x=df["strike_conv"],
                y=df["call_price_change"],
                name="Call ΔPrice",
                mode="lines+markers",
                line=dict(color="lightskyblue", width=2),
                marker=dict(size=6, symbol="circle-open"),
                legendgroup="ΔPrice",
                legend="legend2",
                showlegend=show_legend,
            ),
            row=i, col=2, secondary_y=True
        )
        fig.add_trace(
            go.Scatter(
                x=df["strike_conv"],
                y=df["put_price_change"],
                name="Put ΔPrice",
                mode="lines+markers",
                line=dict(color="lightcoral", width=2),
                marker=dict(size=6, symbol="circle-open"),
                legendgroup="ΔPrice",
                legend="legend2",
                showlegend=show_legend,
            ),
            row=i, col=2, secondary_y=True
        )

        max_oi_here = max(
            abs(df["call_oi"]).max(),
            abs(df["put_oi_plot"]).max(),
            abs(df["call_oi_prev"]).max(),
            abs(df["put_oi_prev_plot"]).max(),
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
            abs(df["call_price"]).max(),
            abs(df["put_price"]).max(),
            abs(df["call_price_prev"]).max(),
            abs(df["put_price_prev"]).max(),
        )
        if not np.isfinite(max_price_here) or max_price_here <= 0:
            max_price_here = 1.0

        max_dprice_here = max(
            abs(df["call_price_change"]).max(),
            abs(df["put_price_change"]).max(),
        )
        if not np.isfinite(max_dprice_here) or max_dprice_here <= 0:
            max_dprice_here = 1.0

        fig.update_yaxes(
            range=[-max_oi_here * 1.2, max_oi_here * 1.2],
            title_text="OI",
            showticklabels=True,
            row=i, col=1, secondary_y=False,
        )
        fig.update_yaxes(
            range=[-max_coi_here * 1.2, max_coi_here * 1.2],
            title_text="ΔOI",
            showgrid=False,
            showticklabels=True,
            row=i, col=1, secondary_y=True,
        )

        fig.update_yaxes(
            range=[-max_price_here * 1.2, max_price_here * 1.2],
            title_text="Price",
            showticklabels=True,
            row=i, col=2, secondary_y=False,
        )
        fig.update_yaxes(
            range=[-max_dprice_here * 1.2, max_dprice_here * 1.2],
            title_text="ΔPrice",
            showgrid=False,
            showticklabels=True,
            row=i, col=2, secondary_y=True,
        )

        strikes_this_expiry = sorted(df["strike_conv"].unique().tolist())
        ticktext_this = [f"{v:.1f}" for v in strikes_this_expiry]

        fig.update_xaxes(
            tickmode="array",
            tickvals=strikes_this_expiry,
            ticktext=ticktext_this,
            row=i,
            col=1,
        )
        fig.update_xaxes(
            tickmode="array",
            tickvals=strikes_this_expiry,
            ticktext=ticktext_this,
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

    fig.update_xaxes(
        type="linear",
        range=[x_min_global, x_max_global],
        tickformat=".1f",
        automargin=True,
        matches="x",
        showticklabels=True,
        title_text="Strike",
    )

    fig.update_traces(cliponaxis=False)

    fig.update_layout(
        title=(
            f"{company_name} ({sym}) – OI & Price (mirrored) "
            f"{trade_date_now} (vs {trade_date_prev}), spot {display_spot:.2f}"
        ),
        height=260 * max(1, len(expiries)),
        width=1800,
        barmode="overlay",
        bargap=0.02,
        bargroupgap=0.05,
        legend=dict(
            title_text="OI / ΔOI",
            orientation="h",
            yanchor="bottom",
            y=1.02,
            xanchor="right",
            x=1.0,
        ),
        legend2=dict(
            title_text="Price / ΔPrice",
            orientation="h",
            yanchor="top",
            y=-0.15,
            xanchor="right",
            x=1.0,
        ),
        legend_tracegroupgap=10,
        margin=dict(l=60, r=60, t=80, b=120),
    )

    return fig, company_name, spot

# =========================
# Combined numeric + English summary
# =========================

def make_combined_tables(symbol, trade_date_now, company_name, spot, yahoo_prices):
    sym = symbol.upper()
    ratio = mapping_ratio(sym, yahoo_prices)
    display_spot = spot * ratio if spot is not None and not np.isnan(spot) else np.nan
    expiries = get_all_expiries(sym, trade_date_now)
    if not expiries:
        return None

    rows_num = []
    for expiry in expiries:
        df_slice = get_options_slice(sym, trade_date_now, expiry)
        if df_slice is None or df_slice.empty:
            continue

        total_call_oi = df_slice["openInt_Call_now"].sum()
        total_put_oi  = df_slice["openInt_Put_now"].sum()
        pcr_oi = total_put_oi / total_call_oi if total_call_oi > 0 else np.nan

        agg = df_slice.groupby("strike", as_index=False).agg({
            "openInt_Call_now": "sum",
            "openInt_Put_now": "sum",
            "change_OI_Call": "sum",
            "change_OI_Put": "sum",
        })
        agg["total_doi"] = agg["change_OI_Call"].abs() + agg["change_OI_Put"].abs()
        top = agg.sort_values("total_doi", ascending=False).head(1)

        sr = df_slice[["R1", "S1", "R12", "S12"]].drop_duplicates()

        def pick(series):
            vc = series.value_counts()
            return vc.index[0] if not vc.empty else None

        s1  = pick(sr["S1"])  if "S1"  in sr else None
        r1  = pick(sr["R1"])  if "R1"  in sr else None
        s12 = pick(sr["S12"]) if "S12" in sr else None
        r12 = pick(sr["R12"]) if "R12" in sr else None

        if not top.empty:
            t = top.iloc[0]
            key_strike = t["strike"]
            dcall = int(t["change_OI_Call"])
            dput  = int(t["change_OI_Put"])
            abs_doi = int(t["total_doi"])
        else:
            key_strike = np.nan
            dcall = dput = abs_doi = 0

        rows_num.append({
            "Expiry": expiry,
            "Key_Strike": f"{key_strike * ratio:.0f}" if not np.isnan(key_strike) else "",
            "ΔCall_OI": f"{dcall:+}",
            "ΔPut_OI": f"{dput:+}",
            "|ΔOI|": f"{abs_doi}",
            "Call_OI": f"{int(total_call_oi):,}",
            "Put_OI": f"{int(total_put_oi):,}",
            "PCR_OI": f"{pcr_oi:.2f}" if not np.isnan(pcr_oi) else "NA",
            "S1":  f"{s1*ratio:.0f}"  if s1  is not None else "",
            "R1":  f"{r1*ratio:.0f}"  if r1  is not None else "",
            "S12": f"{s12*ratio:.0f}" if s12 is not None else "",
            "R12": f"{r12*ratio:.0f}" if r12 is not None else "",
        })

    if not rows_num:
        return None

    df_num = pd.DataFrame(rows_num)

    rows_txt = []
    bull_count = bear_count = 0
    up_moves = []
    dn_moves = []

    for expiry in expiries:
        df_slice = get_options_slice(sym, trade_date_now, expiry)
        if df_slice is None or df_slice.empty:
            continue

        total_call_oi = df_slice["openInt_Call_now"].sum()
        total_put_oi  = df_slice["openInt_Put_now"].sum()
        pcr_oi = total_put_oi / total_call_oi if total_call_oi > 0 else np.nan

        if not np.isnan(spot) and spot > 0:
            band_low = spot * 0.95
            band_high = spot * 1.05
            near = df_slice[(df_slice["strike"] >= band_low) & (df_slice["strike"] <= band_high)]
        else:
            near = df_slice

        dcall = near["change_OI_Call"].sum()
        dput  = near["change_OI_Put"].sum()

        if np.isnan(pcr_oi):
            bias = "Neutral"
        elif pcr_oi > 1.2 and dput > 0 and dcall <= 0:
            bias = "Bearish"
        elif pcr_oi < 0.8 and dcall > 0 and dput >= 0:
            bias = "Bullish"
        else:
            bias = "Neutral"

        if bias == "Bullish":
            bull_count += 1
        elif bias == "Bearish":
            bear_count += 1

        if bias == "Bullish":
            bias_mark = "↑↑"
        elif bias == "Bearish":
            bias_mark = "↓↓"
        else:
            bias_mark = "●"

        if np.isnan(spot) or np.isnan(pcr_oi):
            exp_move_pct = np.nan
        else:
            dev = abs(pcr_oi - 1.0)
            base = 0.02 + min(dev, 0.5) * 0.04
            exp_move_pct = base

        if bias == "Bearish" and not np.isnan(exp_move_pct):
            exp_down_pct = exp_move_pct
            exp_up_pct = exp_move_pct / 2
        elif bias == "Bullish" and not np.isnan(exp_move_pct):
            exp_up_pct = exp_move_pct
            exp_down_pct = exp_move_pct / 2
        else:
            exp_up_pct = exp_move_pct / 2 if not np.isnan(exp_move_pct) else np.nan
            exp_down_pct = exp_move_pct / 2 if not np.isnan(exp_move_pct) else np.nan

        if not np.isnan(exp_up_pct):
            up_moves.append(exp_up_pct * 100)
        if not np.isnan(exp_down_pct):
            dn_moves.append(exp_down_pct * 100)

        sr = df_slice[["S1", "R1"]].dropna(how="all")

        def pick_closest(series, is_support=True):
            s = series.dropna()
            if s.empty or np.isnan(spot):
                return None
            if is_support:
                s = s[s <= spot]
            else:
                s = s[s >= spot]
            if s.empty:
                return None
            return float(s.iloc[(s - spot).abs().argmin()])

        s1 = pick_closest(sr["S1"], True) if "S1" in sr else None
        r1 = pick_closest(sr["R1"], False) if "R1" in sr else None

        if "vol_Call_now" in df_slice.columns:
            vol_call = df_slice["vol_Call_now"].fillna(0)
        else:
            vol_call = df_slice["vol_Call"].fillna(0)

        if "vol_Put_now" in df_slice.columns:
            vol_put = df_slice["vol_Put_now"].fillna(0)
        else:
            vol_put = df_slice["vol_Put"].fillna(0)

        tot_vol = vol_call.sum() + vol_put.sum()

        parts = [bias_mark]
        if not np.isnan(pcr_oi):
            parts.append(f"PCR {pcr_oi:.2f}")
        parts.append(f"Vol {int(tot_vol):,}")
        if s1 is not None:
            parts.append(f"S1 {s1:.0f}")
        if r1 is not None:
            parts.append(f"R1 {r1:.0f}")
        if not np.isnan(exp_up_pct) and not np.isnan(exp_down_pct):
            parts.append(f"Move ~ +{exp_up_pct*100:.1f}% / -{exp_down_pct*100:.1f}%")

        rows_txt.append({"Expiry": expiry, "Summary": " ; ".join(parts)})

    if not rows_txt:
        return None

    df_txt = pd.DataFrame(rows_txt)

    if up_moves and dn_moves:
        avg_up = np.mean(up_moves)
        avg_dn = np.mean(dn_moves)
        overall = f"{bull_count} Bullish expiries, {bear_count} Bearish. Typical move ~ +{avg_up:.1f}% / -{avg_dn:.1f}%."
    else:
        overall = f"{bull_count} Bullish expiries, {bear_count} Bearish."

    fig = make_subplots(
        rows=2,
        cols=1,
        specs=[[{"type": "table"}], [{"type": "table"}]],
        vertical_spacing=0.10,
        subplot_titles=(
            "Numeric Options Summary",
            "English Expiry Summary",
        ),
    )

    header_num = list(df_num.columns)
    cells_num = [df_num[col].tolist() for col in header_num]
    fig.add_trace(
        go.Table(
            header=dict(
                values=header_num,
                fill_color="lightgrey",
                align="center",
                font=dict(size=11, color="black"),
            ),
            cells=dict(
                values=cells_num,
                align="center",
                font=dict(size=10),
            ),
        ),
        row=1, col=1
    )

    header_txt = ["Expiry", "Summary"]
    cells_txt = [df_txt["Expiry"].tolist(), df_txt["Summary"].tolist()]
    fig.add_trace(
        go.Table(
            header=dict(
                values=header_txt,
                fill_color="lightgrey",
                align="left",
                font=dict(size=11, color="black"),
            ),
            cells=dict(
                values=cells_txt,
                align="left",
                font=dict(size=10),
            ),
        ),
        row=2, col=1
    )

    fig.update_layout(
        title=(
            f"{company_name} ({sym}) – Summary & English View "
            f"({trade_date_now}, spot {display_spot:.2f})"
        ),
        width=1500,
        height=450 + 25 * (len(df_num) + len(df_txt)),
        margin=dict(l=40, r=40, t=80, b=60),
    )

    fig.add_annotation(
        x=0,
        y=0.48,
        xref="paper",
        yref="paper",
        text=overall,
        showarrow=False,
        align="left",
        font=dict(size=11),
    )

    return fig

# =========================
# Consolidated spread table
# =========================

def build_consolidated_spread_table(trade_date_now):
    td_mmddyyyy = trade_date_now_to_mmddyyyy(trade_date_now)
    with get_conn() as conn:
        df_stock = pd.read_sql(
            f"""
            SELECT ticker, trade_date, close
            FROM {TABLE_STOCK_DAILY}
            WHERE trade_date = ?
            """,
            conn,
            params=(td_mmddyyyy,)
        )
    if df_stock.empty:
        print("[WARN] No stock_daily data; cannot build consolidated spreads.")
        return pd.DataFrame()

    stock_map = df_stock.set_index("ticker")["close"].to_dict()
    symbols = get_symbols_for_trade_date(trade_date_now)
    all_rows = []
    for sym in symbols:
        spot = stock_map.get(sym, np.nan)
        if np.isnan(spot):
            continue
        expiries = get_all_expiries(sym, trade_date_now)
        if not expiries:
            continue
        for expiry in expiries:
            df_slice = get_options_slice(sym, trade_date_now, expiry)
            if df_slice is None or df_slice.empty:
                continue
            if "call_close_now" not in df_slice.columns:
                continue
            df_calls = df_slice.copy()
            df_calls = df_calls[df_calls["call_close_now"] >= 0.10]
            df_calls = df_calls[df_calls["strike"] >= spot]
            if df_calls.empty or len(df_calls) < 2:
                continue
            df_calls = df_calls.sort_values("call_close_now", ascending=False)
            top2 = df_calls.head(2).reset_index(drop=True)
            short_row = top2.iloc[0]
            long_row  = top2.iloc[1]
            short_strike = float(short_row["strike"])
            long_strike  = float(long_row["strike"])
            short_price  = float(short_row["call_close_now"])
            long_price   = float(long_row["call_close_now"])
            multiplier = 100.0
            premium_collected = (short_price - long_price) * multiplier
            max_profit = premium_collected
            max_loss = (long_strike - short_strike) * multiplier - premium_collected
            all_rows.append({
                "Trade_Date_Now": trade_date_now,
                "Ticker": sym,
                "Expiry": expiry,
                "Underlying_Close": spot,
                "Short_Strike": short_strike,
                "Short_Call_Close": short_price,
                "Long_Strike": long_strike,
                "Long_Call_Close": long_price,
                "Premium_Collected": premium_collected,
                "Max_Profit": max_profit,
                "Max_Loss": max_loss,
                "Premium_EOD": np.nan,
                "PnL_EndOfDay": np.nan,
            })
    if not all_rows:
        print("[WARN] No valid spreads built.")
        return pd.DataFrame()
    return pd.DataFrame(all_rows)

def save_consolidated_spreads_to_excel_and_png(df_spreads, out_dir_today, trade_date_now):
    if df_spreads.empty:
        return
    excel_path = os.path.join(out_dir_today, f"{trade_date_now}_Consolidated_Spreads.xlsx")
    df_spreads.to_excel(excel_path, index=False)
    print(f"[OK] Saved consolidated spreads Excel: {excel_path}")
    df_show = df_spreads.copy()
    max_rows = 200
    if len(df_show) > max_rows:
        df_show = df_show.head(max_rows)
    header = list(df_show.columns)
    cells = [df_show[col].tolist() for col in header]
    fig = go.Figure(
        data=[
            go.Table(
                header=dict(
                    values=header,
                    fill_color="lightgrey",
                    align="center",
                    font=dict(size=10, color="black"),
                ),
                cells=dict(
                    values=cells,
                    align="center",
                    font=dict(size=9),
                ),
            )
        ]
    )
    fig.update_layout(
        title=f"Consolidated Call Spreads – {trade_date_now}",
        width=1800,
        height=400 + 20 * len(df_show),
        margin=dict(l=40, r=40, t=60, b=40),
    )
    png_path = os.path.join(out_dir_today, f"{trade_date_now}_Consolidated_Spreads.png")
    fig.write_image(png_path, scale=2)
    print(f"[OK] Saved consolidated spreads PNG: {png_path}")

# =========================
# Previous‑day update (call_close alias)
# =========================

def update_previous_day_spreads_with_next_day_prices(prev_trade_date_now, curr_trade_date_now_mmddyyyy):
    prev_dir = os.path.join(BASE_OUT_DIR, prev_trade_date_now)
    prev_excel = os.path.join(prev_dir, f"{prev_trade_date_now}_Consolidated_Spreads.xlsx")
    if not os.path.exists(prev_excel):
        print(f"[WARN] Previous day spreads file not found: {prev_excel}")
        return None

    df_prev = pd.read_excel(prev_excel)

    with get_conn() as conn:
        df_opt_next = pd.read_sql(
            f"""
            SELECT ticker, expiry_date, strike,
                   call_close AS call_close_now
            FROM {TABLE_OPTIONS_DAILY}
            WHERE trade_date = ?
            """,
            conn,
            params=(curr_trade_date_now_mmddyyyy,)
        )

    if df_opt_next.empty:
        print("[WARN] No options_daily for next day; cannot update comparison PnL.")
        return None

    df_opt_next["call_open_now"] = df_opt_next["call_close_now"]

    df_prev["Expiry_str"] = df_prev["Expiry"].astype(str)
    df_opt_next["Expiry_str"] = df_opt_next["expiry_date"].astype(str)

    short_merge = df_prev.merge(
        df_opt_next,
        left_on=["Ticker", "Expiry_str", "Short_Strike"],
        right_on=["ticker", "Expiry_str", "strike"],
        how="left",
        suffixes=("", "_short_next")
    )
    short_merge = short_merge.rename(columns={
        "call_open_now": "Short_Call_Open_Next",
        "call_close_now": "Short_Call_Close_Next"
    })

    long_merge = short_merge.merge(
        df_opt_next,
        left_on=["Ticker", "Expiry_str", "Long_Strike"],
        right_on=["ticker", "Expiry_str", "strike"],
        how="left",
        suffixes=("", "_long_next")
    )
    long_merge = long_merge.rename(columns={
        "call_open_now": "Long_Call_Open_Next",
        "call_close_now": "Long_Call_Close_Next"
    })

    multiplier = 100.0

    long_merge["Premium_Entry_Close"] = (long_merge["Short_Call_Close"] - long_merge["Long_Call_Close"]) * multiplier
    long_merge["Premium_Exit_Close"] = (long_merge["Short_Call_Close_Next"] - long_merge["Long_Call_Close_Next"]) * multiplier
    long_merge["PnL_if_Entry_Close"] = long_merge["Premium_Entry_Close"] - long_merge["Premium_Exit_Close"]

    long_merge["Premium_Entry_Open"] = (long_merge["Short_Call_Open_Next"] - long_merge["Long_Call_Open_Next"]) * multiplier
    long_merge["Premium_Exit_Close_2"] = long_merge["Premium_Exit_Close"]
    long_merge["PnL_if_Entry_Open"] = long_merge["Premium_Entry_Open"] - long_merge["Premium_Exit_Close_2"]

    long_merge["Premium_EOD"] = long_merge["Premium_Exit_Close"]
    long_merge["PnL_EndOfDay"] = long_merge["PnL_if_Entry_Close"]

    out_cols = [
        "Trade_Date_Now",
        "Ticker",
        "Expiry",
        "Underlying_Close",
        "Short_Strike",
        "Short_Call_Close",
        "Long_Strike",
        "Long_Call_Close",
        "Premium_Collected",
        "Max_Profit",
        "Max_Loss",
        "Short_Call_Open_Next",
        "Long_Call_Open_Next",
        "Short_Call_Close_Next",
        "Long_Call_Close_Next",
        "Premium_Entry_Close",
        "Premium_Entry_Open",
        "Premium_Exit_Close",
        "PnL_if_Entry_Close",
        "PnL_if_Entry_Open",
        "Premium_EOD",
        "PnL_EndOfDay",
    ]
    df_out = long_merge[out_cols].copy()

    out_excel = os.path.join(prev_dir, f"{prev_trade_date_now}_Consolidated_Spreads_EntryClose_vs_Open.xlsx")
    df_out.to_excel(out_excel, index=False)
    print(f"[OK] Saved comparison file (entry close vs entry open): {out_excel}")
    return df_out

# =========================
# Daily summary workbook
# =========================

def append_daily_summary_sheet(df_eod_spreads, trade_date_now):
    if df_eod_spreads is None or df_eod_spreads.empty:
        print("[INFO] No EOD spreads for summary.")
        return

    df = df_eod_spreads.copy()

    if MIN_PREMIUM_COLLECTED is not None and MIN_PREMIUM_COLLECTED > 0:
        df = df[df["Premium_Collected"] >= MIN_PREMIUM_COLLECTED]
    if MAX_MAX_LOSS is not None and MAX_MAX_LOSS > 0:
        df = df[df["Max_Loss"] <= MAX_MAX_LOSS]

    if df.empty:
        print("[INFO] All trades filtered out by summary filters; nothing to summarize.")
        return

    pos_pnl = df["PnL_EndOfDay"][df["PnL_EndOfDay"] > 0].sum()
    neg_pnl = df["PnL_EndOfDay"][df["PnL_EndOfDay"] < 0].sum()

    total_investment = df["Max_Loss"].clip(lower=0).sum()
    total_loss = -neg_pnl

    total_trades = len(df)
    win_trades = (df["PnL_EndOfDay"] > 0).sum()
    loss_trades = (df["PnL_EndOfDay"] < 0).sum()

    if total_investment > 0:
        total_return_pct = (pos_pnl + neg_pnl) / total_investment * 100.0
    else:
        total_return_pct = np.nan

    summary_rows = [
        ["Metric", "Value"],
        ["Trade_Date_Now", trade_date_now],
        ["Total_Trades", total_trades],
        ["Winning_Trades", win_trades],
        ["Losing_Trades", loss_trades],
        ["Total_PnL_Positive", float(pos_pnl)],
        ["Total_PnL_Negative", float(neg_pnl)],
        ["Total_Investment", float(total_investment)],
        ["Total_Loss", float(total_loss)],
        ["Net_PnL", float(pos_pnl + neg_pnl)],
        ["Net_PnL_%_of_Investment", float(total_return_pct) if not np.isnan(total_return_pct) else ""],
        ["Min_Premium_Filter", MIN_PREMIUM_COLLECTED if MIN_PREMIUM_COLLECTED is not None else ""],
        ["Max_MaxLoss_Filter", MAX_MAX_LOSS if MAX_MAX_LOSS is not None else ""],
    ]
    df_summary = pd.DataFrame(summary_rows[1:], columns=summary_rows[0])

    grp_ticker = df.groupby("Ticker").agg(
        Trades=("Ticker", "count"),
        Win_Trades=("PnL_EndOfDay", lambda x: (x > 0).sum()),
        Loss_Trades=("PnL_EndOfDay", lambda x: (x < 0).sum()),
        PnL_Total=("PnL_EndOfDay", "sum"),
        Investment=("Max_Loss", lambda x: x.clip(lower=0).sum())
    ).reset_index()
    grp_ticker["PnL_%_of_Investment"] = np.where(
        grp_ticker["Investment"] > 0,
        grp_ticker["PnL_Total"] / grp_ticker["Investment"] * 100.0,
        np.nan
    )

    grp_expiry = df.groupby("Expiry").agg(
        Trades=("Expiry", "count"),
        Win_Trades=("PnL_EndOfDay", lambda x: (x > 0).sum()),
        Loss_Trades=("PnL_EndOfDay", lambda x: (x < 0).sum()),
        PnL_Total=("PnL_EndOfDay", "sum"),
        Investment=("Max_Loss", lambda x: x.clip(lower=0).sum())
    ).reset_index()
    grp_expiry["PnL_%_of_Investment"] = np.where(
        grp_expiry["Investment"] > 0,
        grp_expiry["PnL_Total"] / grp_expiry["Investment"] * 100.0,
        np.nan
    )

    mode = "a" if os.path.exists(SUMMARY_EXCEL_PATH) else "w"
    with pd.ExcelWriter(SUMMARY_EXCEL_PATH, engine="openpyxl",
                        mode=mode, if_sheet_exists="replace") as writer:
        start_row = 0
        df_summary.to_excel(writer, sheet_name=trade_date_now, index=False, startrow=start_row)
        start_row += len(df_summary) + 2

        pd.DataFrame([["By Ticker", ""]], columns=df_summary.columns)\
            .to_excel(writer, sheet_name=trade_date_now, index=False, header=False, startrow=start_row)
        start_row += 1
        grp_ticker.to_excel(writer, sheet_name=trade_date_now, index=False, startrow=start_row)
        start_row += len(grp_ticker) + 2

        pd.DataFrame([["By Expiry", ""]], columns=df_summary.columns)\
            .to_excel(writer, sheet_name=trade_date_now, index=False, header=False, startrow=start_row)
        start_row += 1
        grp_expiry.to_excel(writer, sheet_name=trade_date_now, index=False, startrow=start_row)

    print(f"[OK] Appended stacked daily summary for {trade_date_now} -> {SUMMARY_EXCEL_PATH}")

# =========================
# MAIN
# =========================

if __name__ == "__main__":
    td_now_raw = latest_trade_date_now()
    if not td_now_raw:
        print("No trade_date_now found.")
        raise SystemExit(1)

    now = datetime.now()
    if is_today(td_now_raw) and now.time() < time(9, 30):
        prev_td = previous_trade_date_now(td_now_raw)
        if prev_td:
            td_now = prev_td
            print(f"[INFO] Latest trade_date_now in DB is today ({td_now_raw}), but before 09:30 -> using previous trading day {td_now}")
        else:
            td_now = td_now_raw
            print(f"[WARN] Today is {td_now_raw} and no previous trading day found; using {td_now}")
    else:
        td_now = td_now_raw
    print(f"[INFO] Using trade_date_now: {td_now}")

    out_dir_today = os.path.join(BASE_OUT_DIR, td_now)
    os.makedirs(out_dir_today, exist_ok=True)

    symbols = get_symbols_for_trade_date(td_now)
    if not symbols:
        print(f"No symbols for {td_now}")
        raise SystemExit(1)

    yahoo_prices = get_yahoo_prices()

    print(f"[INFO] Creating per‑stock charts and summaries for {td_now}")
    for sym in symbols:
        try:
            sym_upper = sym.upper()
            print(f"[INFO] {sym}: OI + Price chart...")
            oi_fig, full_name, spot = make_oi_chart(sym, td_now, yahoo_prices)
            if oi_fig is not None and full_name is not None:
                oi_name = f"{full_name}_{sym_upper}_OI_Price_CHART.png"
                oi_path = os.path.join(out_dir_today, oi_name)
                oi_fig.write_image(oi_path)
                print(f"   [OK] {oi_path}")

            if full_name is None:
                continue

            print(f"[INFO] {sym}: Combined summary + English table...")
            combo_fig = make_combined_tables(sym, td_now, full_name, spot, yahoo_prices)
            if combo_fig is not None:
                combo_name = f"{full_name}_{sym_upper}_Summary_and_English.png"
                combo_path = os.path.join(out_dir_today, combo_name)
                combo_fig.write_image(combo_path)
                print(f"   [OK] {combo_path}")

        except Exception as e:
            print(f"[WARN] Failed for {sym}: {e}")

    print(f"[INFO] Building consolidated call spread table for {td_now}")
    df_spreads_today = build_consolidated_spread_table(td_now)
    save_consolidated_spreads_to_excel_and_png(df_spreads_today, out_dir_today, td_now)

    prev_td = previous_trade_date_now(td_now)
    if prev_td:
        print(f"[INFO] Updating previous trading day's spreads ({prev_td}) with next-day prices ({td_now})")
        curr_trade_date_for_options_daily = trade_date_now_to_mmddyyyy(td_now)
        df_comp_prev = update_previous_day_spreads_with_next_day_prices(prev_td, curr_trade_date_for_options_daily)
        if df_comp_prev is not None:
            append_daily_summary_sheet(df_comp_prev, prev_td)
    else:
        print("[INFO] No previous trading day found; skipping comparison and summary.")
