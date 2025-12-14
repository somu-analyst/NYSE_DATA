import os
import sqlite3
from datetime import datetime
import pandas as pd
import plotly.graph_objects as go

# =========================
# CONFIG
# =========================

US_DB_PATH = r"C:\Users\srini\Options_chain_data\US_data.db"
OUT_DIR = r"C:\Users\srini\Options_chain_data\US_CHARTS"
os.makedirs(OUT_DIR, exist_ok=True)


# =========================
# DB HELPERS
# =========================

def get_conn():
    if not os.path.exists(US_DB_PATH):
        raise FileNotFoundError(f"US_data.db not found at {US_DB_PATH}")
    return sqlite3.connect(US_DB_PATH)


def get_latest_trade_date_for_symbol(symbol: str) -> str | None:
    sym = symbol.upper()
    with get_conn() as conn:
        q = """
        SELECT trade_date
        FROM options_daily
        WHERE ticker = ?
        ORDER BY ROWID DESC
        LIMIT 1
        """
        df = pd.read_sql(q, conn, params=(sym,))
    if df.empty:
        return None
    return df["trade_date"].iloc[0]   # e.g. '12Dec2025'


def get_expiries_for_symbol_trade(symbol: str, trade_date: str) -> list:
    sym = symbol.upper()
    with get_conn() as conn:
        q = """
        SELECT DISTINCT expiry_date
        FROM options_daily
        WHERE ticker = ? AND trade_date = ?
        ORDER BY expiry_date
        """
        df = pd.read_sql(q, conn, params=(sym, trade_date))
    return df["expiry_date"].tolist()


def load_option_slice(symbol: str, trade_date: str, expiry_date: str) -> pd.DataFrame:
    """
    One row per strike with OI, Volume, and changes for Call & Put.
    """
    sym = symbol.upper()

    with get_conn() as conn:
        q_base = """
        SELECT
            ticker,
            trade_date,
            expiry_date,
            strike,
            openInt_Call,
            openInt_Put,
            vol_Call,
            vol_Put
        FROM options_daily
        WHERE ticker = ?
          AND trade_date = ?
          AND expiry_date = ?
        """
        df_base = pd.read_sql(q_base, conn, params=(sym, trade_date, expiry_date))

        q_chg = """
        SELECT
            ticker,
            trade_date_now AS trade_date,
            expiry_date,
            strike,
            change_OI_Call,
            change_OI_Put,
            change_vol_Call,
            change_vol_Put
        FROM options_change
        WHERE ticker = ?
          AND trade_date_now = ?
          AND expiry_date = ?
        """
        df_chg = pd.read_sql(q_chg, conn, params=(sym, trade_date, expiry_date))

    if df_base.empty:
        return df_base

    df_merged = pd.merge(
        df_base,
        df_chg,
        on=["ticker", "trade_date", "expiry_date", "strike"],
        how="left"
    )

    for col in [
        "openInt_Call", "openInt_Put",
        "vol_Call", "vol_Put",
        "change_OI_Call", "change_OI_Put",
        "change_vol_Call", "change_vol_Put"
    ]:
        if col in df_merged.columns:
            df_merged[col] = df_merged[col].fillna(0)
        else:
            df_merged[col] = 0

    return df_merged


def get_spot_from_stock_daily(symbol: str, trade_date: str) -> float | None:
    """
    stock_daily.trade_date is MM-DD-YYYY.
    options_daily.trade_date is like 12Dec2025.
    """
    sym = symbol.upper()
    try:
        dt = datetime.strptime(trade_date, "%d%b%Y")
        td_mmddyyyy = dt.strftime("%m-%d-%Y")
    except Exception:
        return None

    with get_conn() as conn:
        q = """
        SELECT close
        FROM stock_daily
        WHERE ticker = ?
          AND trade_date = ?
        ORDER BY ROWID DESC
        LIMIT 1
        """
        df = pd.read_sql(q, conn, params=(sym, td_mmddyyyy))
    if df.empty:
        return None
    try:
        return float(df["close"].iloc[0])
    except Exception:
        return None


# =========================
# CHART: CALL+PUT IN ONE IMAGE
# =========================

def make_oi_vol_chart_one_image(symbol: str,
                                trade_date: str | None = None,
                                expiry_date: str | None = None):
    sym = symbol.upper()

    # Trade date
    if trade_date is None:
        trade_date = get_latest_trade_date_for_symbol(sym)
        if not trade_date:
            print(f"[ERR] No options_daily data for {sym}")
            return
        print(f"[INFO] Using latest trade_date for {sym}: {trade_date}")
    else:
        print(f"[INFO] Using given trade_date for {sym}: {trade_date}")

    # Expiry
    expiries = get_expiries_for_symbol_trade(sym, trade_date)
    if not expiries:
        print(f"[ERR] No expiries for {sym} on {trade_date}")
        return

    if expiry_date is None:
        expiry_date = expiries[0]
        print(f"[INFO] Using first expiry for {sym} on {trade_date}: {expiry_date}")
    else:
        if expiry_date not in expiries:
            print(f"[WARN] Expiry {expiry_date} not in available expiries; using first: {expiries[0]}")
            expiry_date = expiries[0]
        print(f"[INFO] Using expiry for {sym} on {trade_date}: {expiry_date}")

    df = load_option_slice(sym, trade_date, expiry_date)
    if df.empty:
        print(f"[ERR] No data for {sym}, {trade_date}, {expiry_date}")
        return

    df = df.sort_values("strike")

    # Spot from stock_daily
    spot = get_spot_from_stock_daily(sym, trade_date)
    print(f"[INFO] Spot (close) from stock_daily for {sym} {trade_date}: {spot}")

    # Build figure with 2 rows; each row: bars (OI & ΔOI) + lines (Vol & ΔVol)
    fig = go.Figure()

    # ===== Row 1: CALLS =====
    # Bars: OI and ΔOI
    fig.add_trace(
        go.Bar(
            x=df["strike"],
            y=df["openInt_Call"],
            name="Call OI",
            marker_color="steelblue",
            yaxis="y1"
        )
    )
    fig.add_trace(
        go.Bar(
            x=df["strike"],
            y=df["change_OI_Call"],
            name="Call ΔOI",
            marker_color="orange",
            yaxis="y1"
        )
    )
    # Lines: Volume and ΔVolume on secondary y-axis (y3)
    fig.add_trace(
        go.Scatter(
            x=df["strike"],
            y=df["vol_Call"],
            name="Call Vol",
            mode="lines+markers",
            line=dict(color="slategray", width=2),
            yaxis="y3"
        )
    )
    fig.add_trace(
        go.Scatter(
            x=df["strike"],
            y=df["change_vol_Call"],
            name="Call ΔVol",
            mode="lines+markers",
            line=dict(color="goldenrod", width=2, dash="dot"),
            yaxis="y3"
        )
    )

    # ===== Row 2: PUTS =====
    # Bars: OI and ΔOI
    fig.add_trace(
        go.Bar(
            x=df["strike"],
            y=df["openInt_Put"],
            name="Put OI",
            marker_color="seagreen",
            yaxis="y2"
        )
    )
    fig.add_trace(
        go.Bar(
            x=df["strike"],
            y=df["change_OI_Put"],
            name="Put ΔOI",
            marker_color="crimson",
            yaxis="y2"
        )
    )
    # Lines: Volume and ΔVolume on secondary y-axis (y4)
    fig.add_trace(
        go.Scatter(
            x=df["strike"],
            y=df["vol_Put"],
            name="Put Vol",
            mode="lines+markers",
            line=dict(color="darkolivegreen", width=2),
            yaxis="y4"
        )
    )
    fig.add_trace(
        go.Scatter(
            x=df["strike"],
            y=df["change_vol_Put"],
            name="Put ΔVol",
            mode="lines+markers",
            line=dict(color="firebrick", width=2, dash="dot"),
            yaxis="y4"
        )
    )

    td_short = trade_date
    exp_short = expiry_date

    fig.update_layout(
        title=f"{sym} OI / ΔOI (bars) + Vol / ΔVol (lines) by Strike\n"
              f"Trade: {td_short}  Expiry: {exp_short}",
        barmode="group",
        height=1000,
        width=1500,
        legend=dict(orientation="h", yanchor="bottom", y=1.03, xanchor="left", x=0),
        xaxis=dict(domain=[0.0, 1.0]),
        xaxis2=dict(domain=[0.0, 1.0], anchor="y2"),
        # y1/y3 share top half; y2/y4 share bottom half
        yaxis=dict(domain=[0.55, 1.0], title="CALL OI / ΔOI"),
        yaxis3=dict(domain=[0.55, 1.0], overlaying="y", side="right",
                    title="CALL Vol / ΔVol", showgrid=False),
        yaxis2=dict(domain=[0.0, 0.45], title="PUT OI / ΔOI"),
        yaxis4=dict(domain=[0.0, 0.45], overlaying="y2", side="right",
                    title="PUT Vol / ΔVol", showgrid=False)
    )

    # Assign bottom traces to x2
    for i in range(4, 8):
        fig.data[i].update(xaxis="x2")

    # Spot line (on both OI axes)
    if spot is not None:
        fig.add_hline(
            y=spot,
            line_dash="dash",
            line_color="black",
            annotation_text=f"Spot {spot:.2f}",
            annotation_position="top left",
            row=1, col=1
        )
        fig.add_hline(
            y=spot,
            line_dash="dash",
            line_color="black",
            annotation_text=f"Spot {spot:.2f}",
            annotation_position="bottom left",
            row=2, col=1
        )

    out_name = f"{sym}_OI_VOL_2row_{td_short}_{exp_short}.png"
    out_path = os.path.join(OUT_DIR, out_name)
    fig.write_image(out_path, width=1500, height=1000)
    print(f"[OK] Saved combined CALL+PUT OI/Vol chart: {out_path}")


# =========================
# MAIN
# =========================

if __name__ == "__main__":
    print(f"[INFO] Using DB: {US_DB_PATH}")
    print(f"[INFO] Charts will be saved under: {OUT_DIR}")

    # Example: latest trade_date + nearest expiry
    make_oi_vol_chart_one_image("AVGO")

    # Example explicit:
    # make_oi_vol_chart_one_image("QQQ", trade_date="12Dec2025", expiry_date="2025-12-19")
