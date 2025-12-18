import os
import sqlite3
from datetime import datetime
import numpy as np
import pandas as pd

import plotly.graph_objects as go
from plotly.subplots import make_subplots

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

    for col in [
        "openInt_Call", "openInt_Put",
        "vol_Call", "vol_Put",
        "openInt_Call_now", "openInt_Put_now",
        "vol_Call_now", "vol_Put_now",
        "change_OI_Call", "change_OI_Put",
        "change_vol_Call", "change_vol_Put",
        "R1", "S1", "R12", "S12",
    ]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)

    df["strike"] = pd.to_numeric(df["strike"], errors="coerce")
    df = df.dropna(subset=["strike"])

    return df

# =========================
# FIGURE 1: OI chart (±5% band, OI bars + ΔOI lines, legend top-right)
# =========================

def make_oi_chart(symbol, trade_date_now):
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

    n = len(expiries)
    fig = make_subplots(
        rows=n,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.05,
        subplot_titles=[f"Expiry: {e}" for e in expiries],
        specs=[[{"secondary_y": True}] for _ in range(n)]
    )

    for i, expiry in enumerate(expiries, start=1):
        df_slice = get_options_slice(sym, trade_date_now, expiry)
        if df_slice is None or df_slice.empty:
            print(f"[WARN] {sym}: no data for expiry {expiry}")
            continue

        # limit strikes to ±5% around spot
        df = df_slice.copy()
        band_low = spot * 0.95
        band_high = spot * 1.05
        df = df[(df["strike"] >= band_low) & (df["strike"] <= band_high)].copy()
        if df.empty:
            continue
        df = df.sort_values("strike")

        df["call_oi"] = df["openInt_Call"]
        df["put_oi"]  = df["openInt_Put"]
        df["put_oi_plot"] = -df["put_oi"]

        df["call_coi"] = df["change_OI_Call"]
        df["put_coi"]  = -df["change_OI_Put"]

        max_oi_here = max(
            abs(df["call_oi"]).max(),
            abs(df["put_oi_plot"]).max(),
            abs(df["call_coi"]).max(),
            abs(df["put_coi"]).max(),
        )

        show_legend = (i == 1)

        # OI bars
        fig.add_trace(
            go.Bar(
                x=df["strike"],
                y=df["call_oi"],
                name="Call OI",
                marker_color="royalblue",
                opacity=0.6,
                showlegend=show_legend,
            ),
            row=i, col=1, secondary_y=False
        )
        fig.add_trace(
            go.Bar(
                x=df["strike"],
                y=df["put_oi_plot"],
                name="Put OI",
                marker_color="firebrick",
                opacity=0.6,
                showlegend=show_legend,
            ),
            row=i, col=1, secondary_y=False
        )

        # ΔOI lines on secondary axis
        fig.add_trace(
            go.Scatter(
                x=df["strike"],
                y=df["call_coi"],
                name="Call ΔOI",
                mode="lines+markers",
                line=dict(color="lightskyblue", width=2),
                marker=dict(size=5),
                showlegend=show_legend,
            ),
            row=i, col=1, secondary_y=True
        )
        fig.add_trace(
            go.Scatter(
                x=df["strike"],
                y=df["put_coi"],
                name="Put ΔOI",
                mode="lines+markers",
                line=dict(color="lightcoral", width=2),
                marker=dict(size=5),
                showlegend=show_legend,
            ),
            row=i, col=1, secondary_y=True
        )

        # x-axis ticks = all strikes, numeric axis
        strike_vals = df["strike"].tolist()
        strike_text = [str(int(s)) for s in strike_vals]

        fig.update_xaxes(
            row=i,
            col=1,
            tickmode="array",
            tickvals=strike_vals,
            ticktext=strike_text,
            type="linear",
            showticklabels=True,
        )

        if max_oi_here > 0:
            fig.update_yaxes(
                range=[-max_oi_here * 1.2, max_oi_here * 1.2],
                row=i, col=1, secondary_y=False
            )

        fig.update_yaxes(
            title_text="OI (Calls ↑ / Puts ↓)",
            row=i, col=1, secondary_y=False
        )
        fig.update_yaxes(
            title_text="ΔOI (lines)",
            row=i, col=1, secondary_y=True
        )

        fig.add_vline(
            x=spot,
            line_dash="dash",
            line_color="black",
            row=i,
            col=1,
        )

    fig.update_xaxes(
        title_text="Strike",
        matches="x",
        tickangle=0,
        automargin=True,
    )

    fig.update_layout(
        title=(
            f"{company_name} ({sym}) – OI & ΔOI by Strike (±5% around spot)"
            f"<br>trade_date_now {trade_date_now}, spot {spot:.2f}"
        ),
        height=280 * max(1, len(expiries)),
        width=1500,
        barmode="overlay",
        bargap=0.02,
        bargroupgap=0.05,
        legend=dict(
            orientation="v",
            yanchor="top",
            y=1.0,
            xanchor="right",
            x=1.02,  # right top outside plot
        ),
        margin=dict(l=60, r=160, t=80, b=40),
    )

    return fig, company_name, spot

# =========================
# FIGURE 2: per‑stock numeric summary TABLE
# =========================

def make_summary_table(symbol, trade_date_now, company_name, spot):
    sym = symbol.upper()
    expiries = get_all_expiries(sym, trade_date_now)
    if not expiries:
        return None

    rows = []
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

        rows.append({
            "Expiry": expiry,
            "Key_Strike": f"{key_strike:.0f}" if not np.isnan(key_strike) else "",
            "ΔCall_OI": f"{dcall:+}",
            "ΔPut_OI": f"{dput:+}",
            "|ΔOI|": f"{abs_doi}",
            "Call_OI": f"{int(total_call_oi):,}",
            "Put_OI": f"{int(total_put_oi):,}",
            "PCR_OI": f"{pcr_oi:.2f}" if not np.isnan(pcr_oi) else "NA",
            "S1": f"{s1:.0f}" if s1 is not None else "",
            "R1": f"{r1:.0f}" if r1 is not None else "",
            "S12": f"{s12:.0f}" if s12 is not None else "",
            "R12": f"{r12:.0f}" if r12 is not None else "",
        })

    if not rows:
        return None

    df_tab = pd.DataFrame(rows)

    header = list(df_tab.columns)
    cells = [df_tab[col].tolist() for col in header]

    fig = go.Figure(
        data=[
            go.Table(
                header=dict(
                    values=header,
                    fill_color="lightgrey",
                    align="center",
                    font=dict(size=11, color="black"),
                ),
                cells=dict(
                    values=cells,
                    align="center",
                    font=dict(size=10),
                ),
            )
        ]
    )

    fig.update_layout(
        title=(
            f"{company_name} ({sym}) – Options Summary Table "
            f"({trade_date_now}, spot {spot:.2f})"
        ),
        width=1500,
        height=300 + 25 * len(df_tab),
        margin=dict(l=40, r=40, t=60, b=40),
    )

    return fig

# =========================
# OVERVIEW + per‑expiry S/R
# =========================

def build_overview_df(trade_date_now):
    td_mmddyyyy = trade_date_now_to_mmddyyyy(trade_date_now)

    with get_conn() as conn:
        df_opt = pd.read_sql(
            f"""
            SELECT
                ticker,
                company_name_now,
                openInt_Call_now,
                openInt_Put_now,
                change_OI_Call,
                change_OI_Put,
                strike,
                S1, R1, S12, R12
            FROM {TABLE_OPTIONS_CHANGE}
            WHERE trade_date_now = ?
            """,
            conn,
            params=(trade_date_now,)
        )
        df_stock = pd.read_sql(
            f"""
            SELECT ticker, trade_date, close
            FROM {TABLE_STOCK_DAILY}
            WHERE trade_date = ?
            """,
            conn,
            params=(td_mmddyyyy,)
        )

    if df_opt.empty or df_stock.empty:
        return None

    df_stock = df_stock.rename(columns={"close": "spot"})
    stock_map = df_stock.set_index("ticker")["spot"].to_dict()

    records = []

    for ticker, grp in df_opt.groupby("ticker"):
        spot = stock_map.get(ticker, np.nan)
        total_call_oi = grp["openInt_Call_now"].fillna(0).sum()
        total_put_oi  = grp["openInt_Put_now"].fillna(0).sum()
        pcr_oi = total_put_oi / total_call_oi if total_call_oi > 0 else np.nan

        if not np.isnan(spot) and spot > 0:
            band_low = spot * 0.95
            band_high = spot * 1.05
            near = grp[(grp["strike"] >= band_low) & (grp["strike"] <= band_high)]
        else:
            near = grp

        dcall_near = near["change_OI_Call"].fillna(0).sum()
        dput_near  = near["change_OI_Put"].fillna(0).sum()

        sr = grp[["S1", "R1", "S12", "R12"]].dropna(how="all").copy()

        def pick_closest(col, is_support=True):
            if col not in sr.columns:
                return None
            series = sr[col].dropna()
            if series.empty or np.isnan(spot):
                return None
            if is_support:
                series = series[series <= spot]
            else:
                series = series[series >= spot]
            if series.empty:
                return None
            return float(series.iloc[(series - spot).abs().argmin()])

        s1 = pick_closest("S1", True)
        r1 = pick_closest("R1", False)

        if np.isnan(pcr_oi):
            bias = "Neutral"
        elif pcr_oi > 1.2 and dput_near > 0 and dcall_near <= 0:
            bias = "Down"
        elif pcr_oi < 0.8 and dcall_near > 0 and dput_near >= 0:
            bias = "Up"
        else:
            bias = "Range"

        if np.isnan(spot) or np.isnan(pcr_oi):
            exp_move_pct = np.nan
        else:
            dev = abs(pcr_oi - 1.0)
            base = 0.02 + min(dev, 0.5) * 0.04
            exp_move_pct = base

        if bias == "Down" and not np.isnan(exp_move_pct):
            exp_down_pct = exp_move_pct
            exp_up_pct = exp_move_pct / 2
        elif bias == "Up" and not np.isnan(exp_move_pct):
            exp_up_pct = exp_move_pct
            exp_down_pct = exp_move_pct / 2
        else:
            exp_up_pct = exp_move_pct / 2 if not np.isnan(exp_move_pct) else np.nan
            exp_down_pct = exp_move_pct / 2 if not np.isnan(exp_move_pct) else np.nan

        exp_up_price = spot * (1 + exp_up_pct) if not np.isnan(spot) and not np.isnan(exp_up_pct) else np.nan
        exp_down_price = spot * (1 - exp_down_pct) if not np.isnan(spot) and not np.isnan(exp_down_pct) else np.nan

        name = str(grp["company_name_now"].iloc[0]) if "company_name_now" in grp.columns else ticker

        records.append({
            "Ticker": ticker,
            "Name": name,
            "Spot": round(spot, 2) if not np.isnan(spot) else np.nan,
            "PCR_OI": round(pcr_oi, 2) if not np.isnan(pcr_oi) else np.nan,
            "Bias": bias,
            "Exp_Up_%": round(exp_up_pct * 100, 1) if not np.isnan(exp_up_pct) else np.nan,
            "Exp_Down_%": round(exp_down_pct * 100, 1) if not np.isnan(exp_down_pct) else np.nan,
            "Nearest_S1": round(s1, 2) if s1 is not None else np.nan,
            "Nearest_R1": round(r1, 2) if r1 is not None else np.nan,
        })

    return pd.DataFrame(records).sort_values("Ticker")

def build_expiry_sr_df(trade_date_now):
    with get_conn() as conn:
        df = pd.read_sql(
            f"""
            SELECT ticker, expiry_date, S1, R1, S12, R12
            FROM {TABLE_OPTIONS_CHANGE}
            WHERE trade_date_now = ?
            """,
            conn,
            params=(trade_date_now,)
        )
    for c in ["S1","R1","S12","R12"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    g = df.groupby(["ticker","expiry_date"])
    out_rows = []
    for (t,e), grp in g:
        def pick(series):
            vc = series.dropna().value_counts()
            return vc.index[0] if not vc.empty else np.nan
        out_rows.append({
            "Ticker": t,
            "Expiry": e,
            "S1":  pick(grp["S1"]),
            "R1":  pick(grp["R1"]),
            "S12": pick(grp["S12"]),
            "R12": pick(grp["R12"]),
        })
    return pd.DataFrame(out_rows)

# =========================
# English summary per stock (with arrows and volume)
# =========================

def make_english_summary_for_stock(symbol, trade_date_now, company_name, spot):
    sym = symbol.upper()
    expiries = get_all_expiries(sym, trade_date_now)
    if not expiries:
        return None

    rows = []
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

        # classify bias
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

        # arrow / circle symbol
        if bias == "Bullish":
            bias_mark = "<b><span style='color:green;font-size:14px'>↑↑</span></b>"
        elif bias == "Bearish":
            bias_mark = "<b><span style='color:red;font-size:14px'>↓↓</span></b>"
        else:
            bias_mark = "<b><span style='color:goldenrod;font-size:14px'>●</span></b>"

        # expected move
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

        # nearest S1 / R1
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

        # total option volume for this expiry
        vol_call = df_slice["vol_Call_now"] if "vol_Call_now" in df_slice.columns else df_slice["vol_Call"]
        vol_put  = df_slice["vol_Put_now"] if "vol_Put_now"  in df_slice.columns else df_slice["vol_Put"]
        tot_vol  = vol_call.sum() + vol_put.sum()

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

        rows.append({"Expiry": expiry, "Summary": " ; ".join(parts)})

    if not rows:
        return None

    df_sum = pd.DataFrame(rows)

    if up_moves and dn_moves:
        avg_up = np.mean(up_moves)
        avg_dn = np.mean(dn_moves)
    else:
        avg_up = avg_dn = np.nan

    overall = f"{bull_count} Bullish expiries, {bear_count} Bearish."
    if not np.isnan(avg_up) and not np.isnan(avg_dn):
        overall += f" Typical move ~ +{avg_up:.1f}% / -{avg_dn:.1f}%."

    header = ["Expiry", "Summary"]
    cells = [df_sum["Expiry"].tolist(), df_sum["Summary"].tolist()]

    fig = go.Figure(
        data=[
            go.Table(
                header=dict(
                    values=header,
                    fill_color="lightgrey",
                    align="left",
                    font=dict(size=11, color="black"),
                ),
                cells=dict(
                    values=cells,
                    align="left",
                    font=dict(size=10),
                ),
            )
        ]
    )

    fig.update_layout(
        title=f"{company_name} ({sym}) – English Summary Across Expiries ({trade_date_now}, spot {spot:.2f})",
        margin=dict(l=40, r=40, t=90, b=40),
        width=1500,
        height=250 + 25 * len(df_sum),
    )

    fig.add_annotation(
        x=0,
        y=1.08,
        xref="paper",
        yref="paper",
        text=overall,
        showarrow=False,
        align="left",
        font=dict(size=11),
    )

    return fig

# =========================
# Sentiment grid helpers
# =========================

def sentiment_score(bias_str):
    if not isinstance(bias_str, str):
        return 0.0
    b = bias_str.lower()
    if b == "up":
        return 1.0
    if b == "down":
        return -1.0
    return 0.0

def score_to_color(score):
    s = max(-1.0, min(1.0, score))
    if s > 0:
        g = int(180 + 60 * s)
        return f"rgb(0,{g},0)"
    elif s < 0:
        r = int(180 + 60 * (-s))
        return f"rgb({r},0,0)"
    else:
        return "rgb(230,230,230)"

# =========================
# MAIN
# =========================

if __name__ == "__main__":
    # pip install -U "plotly[kaleido]"
    td_now = latest_trade_date_now()
    if not td_now:
        print("No trade_date_now found.")
        raise SystemExit(1)

    out_dir_today = os.path.join(BASE_OUT_DIR, td_now)
    os.makedirs(out_dir_today, exist_ok=True)

    symbols = get_symbols_for_trade_date(td_now)
    if not symbols:
        print(f"No symbols for {td_now}")
        raise SystemExit(1)

    print(f"[INFO] Creating per‑stock charts and summaries for {td_now}")

    for sym in symbols:
        try:
            print(f"[INFO] {sym}: OI chart...")
            oi_fig, full_name, spot = make_oi_chart(sym, td_now)
            if oi_fig is not None:
                oi_name = f"{full_name}_OI_CHART.png"
                oi_path = os.path.join(out_dir_today, oi_name)
                oi_fig.write_image(oi_path)
                print(f"   [OK] {oi_path}")

            if full_name is None:
                continue

            print(f"[INFO] {sym}: Summary table...")
            sum_fig = make_summary_table(sym, td_now, full_name, spot)
            if sum_fig is not None:
                sum_name = f"{full_name}_Summary_analysis.png"
                sum_path = os.path.join(out_dir_today, sum_name)
                sum_fig.write_image(sum_path)
                print(f"   [OK] {sum_path}")

            print(f"[INFO] {sym}: English expiry summary...")
            eng_fig = make_english_summary_for_stock(sym, td_now, full_name, spot)
            if eng_fig is not None:
                eng_name = f"{full_name}_Expiries_English_Summary.png"
                eng_path = os.path.join(out_dir_today, eng_name)
                eng_fig.write_image(eng_path)
                print(f"   [OK] {eng_path}")

        except Exception as e:
            print(f"[WARN] Failed for {sym}: {e}")

    # ---- Big all‑stocks sentiment table ----
    print(f"[INFO] Building all‑stocks sentiment table for {td_now}")
    df_over = build_overview_df(td_now)
    if df_over is None or df_over.empty:
        print("[WARN] Overview empty; skipping sentiment table.")
        raise SystemExit(0)

    df_sr = build_expiry_sr_df(td_now)
    expiries = get_expiries_for_td(td_now)
    if not expiries:
        print("[WARN] No expiries; skipping sentiment table.")
        raise SystemExit(0)

    tickers = df_over["Ticker"].astype(str).tolist()

    header_labels = ["Ticker<br>(Spot)"] + expiries

    table_rows = []
    color_rows = []

    for _, r in df_over.iterrows():
        t = str(r["Ticker"])
        spot = r.get("Spot", np.nan)

        row_vals = []
        row_colors = []

        if not np.isnan(spot):
            row_vals.append(f"{t}<br>({spot:.2f})")
        else:
            row_vals.append(t)
        row_colors.append("white")

        for expiry in expiries:
            sr_row = df_sr[(df_sr["Ticker"] == t) & (df_sr["Expiry"] == expiry)]
            if sr_row.empty:
                s1 = r1 = s12 = r12 = np.nan
            else:
                sr_row = sr_row.iloc[0]
                s1  = sr_row.get("S1", np.nan)
                r1  = sr_row.get("R1", np.nan)
                s12 = sr_row.get("S12", np.nan)
                r12 = sr_row.get("R12", np.nan)

            bias = str(r.get("Bias", ""))  # Up / Down / Range / Neutral
            pcr  = r.get("PCR_OI", np.nan)

            # compact PCR + S/R formatting, no arrows
            line1_parts = []
            if not np.isnan(pcr):
                line1_parts.append(f"PCR {pcr:.2f}")
            line1 = " | ".join(line1_parts) if line1_parts else ""

            s1_txt  = f"S1 {s1:.2f}"   if not np.isnan(s1)  else "S1 -"
            r1_txt  = f"R1 {r1:.2f}"   if not np.isnan(r1)  else "R1 -"
            line2 = f"{s1_txt}  |  {r1_txt}"

            s12_txt = f"S12 {s12:.2f}" if not np.isnan(s12) else "S12 -"
            r12_txt = f"R12 {r12:.2f}" if not np.isnan(r12) else "R12 -"
            line3 = f"{s12_txt}  |  {r12_txt}"

            cell_lines = [line for line in [line1, line2, line3] if line]
            cell_text = "<br>".join(cell_lines)

            row_vals.append(cell_text)

            score = sentiment_score(bias)
            row_colors.append(score_to_color(score))

        table_rows.append(row_vals)
        color_rows.append(row_colors)

    values = [list(col) for col in zip(*table_rows)]
    fill_colors = [list(col) for col in zip(*color_rows)]

    fig_big = go.Figure(
        data=[
            go.Table(
                header=dict(
                    values=header_labels,
                    fill_color="lightgrey",
                    align="center",
                    font=dict(size=11, color="black"),
                ),
                cells=dict(
                    values=values,
                    align="left",
                    font=dict(size=9, color="black"),
                    fill_color=fill_colors,
                    height=26,  # slightly shorter rows
                ),
            )
        ]
    )

    all_bias = df_over["Bias"].astype(str).tolist()
    up_count   = sum(b == "Up"   for b in all_bias)
    down_count = sum(b == "Down" for b in all_bias)

    summary_line = f"Across all stocks: {up_count} Up bias, {down_count} Down bias."

    fig_big.update_layout(
        title=f"All Stocks Options Sentiment – {td_now}",
        margin=dict(l=20, r=20, t=90, b=20),
        width=min(1900, 220 + 140 * len(expiries)),
        height=220 + 28 * len(tickers),   # smaller per-row height to fit more stocks
    )

    fig_big.add_annotation(
        x=0,
        y=1.05,
        xref="paper",
        yref="paper",
        text=summary_line,
        showarrow=False,
        align="left",
        font=dict(size=11),
    )

    out_png_big = os.path.join(out_dir_today, f"{td_now}_All_Stocks_Sentiment_Table.png")
    fig_big.write_image(
        out_png_big,
        width=fig_big.layout.width,
        height=fig_big.layout.height,
        scale=2,  # higher DPI
    )
    print(f"[OK] Saved sentiment table: {out_png_big}")
