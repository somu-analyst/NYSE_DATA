import os
import io
import sqlite3
import datetime as dt
import pandas as pd
import numpy as np
import telebot
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import tempfile

# ================== CONFIG ==================
US_DB_PATH    = r"C:\Users\srini\Options_chain_data\US_data.db"
SUMMARY_TABLE = "us_analytics_daily"
OPTIONS_TABLE = "options_daily"
CHANGE_TABLE  = "options_change"
STOCK_TABLE   = "stock_daily"

TELEGRAM_TOKEN = "8407478799:AAG1GbQOeUVC-SJmZS0YmXiYyAZRWrdqUWE"
bot = telebot.TeleBot(TELEGRAM_TOKEN)

# ================== TABLE SETUP (optional full recreate) ==================
def recreate_us_analytics_table():
    if not os.path.exists(US_DB_PATH):
        print("DB not found:", US_DB_PATH)
        return
    conn = sqlite3.connect(US_DB_PATH)
    cur = conn.cursor()
    cur.execute(f"DROP TABLE IF EXISTS {SUMMARY_TABLE}")
    cur.execute(f"""
        CREATE TABLE {SUMMARY_TABLE} (
            trade_date   TEXT NOT NULL,
            ticker       TEXT NOT NULL,
            expiry_date  TEXT NOT NULL,
            SUMCE        REAL,
            SUMPE        REAL,
            PCR          REAL,

            SCE1         REAL, OICE1      REAL, SCE1_VOL      REAL,
            SCE2         REAL, OICE2      REAL, SCE2_VOL      REAL,
            SCE3         REAL, OICE3      REAL, SCE3_VOL      REAL,

            SPE1         REAL, OIPE1      REAL, SPE1_VOL      REAL,
            SPE2         REAL, OIPE2      REAL, SPE2_VOL      REAL,
            SPE3         REAL, OIPE3      REAL, SPE3_VOL      REAL,

            S1_all       REAL, S12_all    REAL,
            S2_all       REAL, S22_all    REAL,
            S3_all       REAL, S32_all    REAL,
            R1_all       REAL, R12_all    REAL,
            R2_all       REAL, R22_all    REAL,
            R3_all       REAL, R32_all    REAL,

            S1_filt      REAL, S12_filt   REAL,
            S2_filt      REAL, S22_filt   REAL,
            S3_filt      REAL, S32_filt   REAL,
            R1_filt      REAL, R12_filt   REAL,
            R2_filt      REAL, R22_filt   REAL,
            R3_filt      REAL, R32_filt   REAL,

            -- pivot CPR levels (S1-S3, R1-R3) for chart overlays
            S1_piv       REAL,
            S2_piv       REAL,
            S3_piv       REAL,
            R1_piv       REAL,
            R2_piv       REAL,
            R3_piv       REAL,

            OpnPric      REAL,
            HghPric      REAL,
            LwPric       REAL,
            ClsPric      REAL,
            spot_pcr_oi  REAL,

            PRIMARY KEY (trade_date, ticker, expiry_date)
        )
    """)
    conn.commit()
    conn.close()
    print(f"Recreated {SUMMARY_TABLE}")

# ================== OPTIONS MONEY COLUMNS ==================
def ensure_options_money_columns():
    conn = sqlite3.connect(US_DB_PATH)
    cur = conn.cursor()
    cur.execute(f"PRAGMA table_info({OPTIONS_TABLE})")
    cols = {row[1] for row in cur.fetchall()}

    new_cols = [
        ("money_oi_call",       "REAL"),
        ("vol_rank_call",       "REAL"),
        ("money_oi_put",        "REAL"),
        ("vol_rank_put",        "REAL"),
        ("vol_rank_all_call",   "REAL"),
        ("vol_rank_all_put",    "REAL"),
    ]
    for name, coltype in new_cols:
        if name not in cols:
            cur.execute(f"ALTER TABLE {OPTIONS_TABLE} ADD COLUMN {name} {coltype}")
    conn.commit()
    conn.close()

def update_options_money_fields_and_ranks():
    conn = sqlite3.connect(US_DB_PATH)
    df = pd.read_sql(f"SELECT * FROM {OPTIONS_TABLE}", conn)
    if df.empty:
        conn.close()
        return

    df["expiry_date"] = df["expiry_date"].astype(str)
    df = df.sort_values(["ticker", "expiry_date", "trade_date", "strike"])

    # CALL side
    if {"lastPrice_Call", "openInt_Call", "vol_Call"}.issubset(df.columns):
        df["openInt_Call"]   = df["openInt_Call"].fillna(0)
        df["lastPrice_Call"] = df["lastPrice_Call"].fillna(0)
        df["vol_Call"]       = df["vol_Call"].fillna(0)
        df["money_oi_call"]  = df["lastPrice_Call"] * df["openInt_Call"]
        df["vol_rank_call"]  = df.groupby(
            ["ticker", "trade_date", "expiry_date"]
        )["vol_Call"].rank(method="min", pct=True) * 100
    else:
        df["money_oi_call"] = np.nan
        df["vol_rank_call"] = np.nan

    # PUT side
    if {"lastPrice_Put", "openInt_Put", "vol_Put"}.issubset(df.columns):
        df["openInt_Put"]   = df["openInt_Put"].fillna(0)
        df["lastPrice_Put"] = df["lastPrice_Put"].fillna(0)
        df["vol_Put"]       = df["vol_Put"].fillna(0)
        df["money_oi_put"]  = df["lastPrice_Put"] * df["openInt_Put"]
        df["vol_rank_put"]  = df.groupby(
            ["ticker", "trade_date", "expiry_date"]
        )["vol_Put"].rank(method="min", pct=True) * 100
    else:
        df["money_oi_put"] = np.nan
        df["vol_rank_put"] = np.nan

    # Global volume ranks per trade_date
    df["vol_rank_all_call"] = df.groupby("trade_date")["vol_Call"].rank(
        method="min", pct=True
    ) * 100
    df["vol_rank_all_put"]  = df.groupby("trade_date")["vol_Put"].rank(
        method="min", pct=True
    ) * 100

    df.to_sql(OPTIONS_TABLE, conn, if_exists="replace", index=False)
    conn.close()

# ================== NEAREST STRIKE ==================
def get_nearest_strike_for_us(
    ticker: str,
    requested_strike: float,
    max_back_days: int = 60
) -> float | None:
    conn = sqlite3.connect(US_DB_PATH)
    df = pd.read_sql(
        f"""
        SELECT DISTINCT strike, trade_date
        FROM {OPTIONS_TABLE}
        WHERE ticker = ?
        """,
        conn,
        params=(ticker,)
    )
    conn.close()
    if df.empty:
        return None

    try:
        df["trade_date_dt"] = pd.to_datetime(df["trade_date"], format="%d%b%Y")
    except Exception:
        df["trade_date_dt"] = pd.to_datetime(df["trade_date"], errors="coerce")

    latest_dt = df["trade_date_dt"].max()
    if pd.isna(latest_dt):
        return None

    cutoff = latest_dt - pd.Timedelta(days=max_back_days)
    df = df[df["trade_date_dt"] >= cutoff].copy()
    if df.empty:
        return None

    strikes = df["strike"].dropna().astype(float).unique()
    if strikes.size == 0:
        return None

    arr = np.array(strikes, dtype=float)
    idx = np.abs(arr - float(requested_strike)).argmin()
    return float(arr[idx])

# ================== EXPIRY & DATE HELPERS ==================
def get_nearest_expiry_for_us(ticker: str) -> str | None:
    conn = sqlite3.connect(US_DB_PATH)
    df = pd.read_sql(
        f"""
        SELECT DISTINCT expiry_date
        FROM {OPTIONS_TABLE}
        WHERE ticker = ?
          AND expiry_date IS NOT NULL
        """,
        conn,
        params=(ticker,)
    )
    conn.close()
    if df.empty:
        return None

    df["expiry_date"] = df["expiry_date"].astype(str)
    df["expiry_dt"] = pd.to_datetime(df["expiry_date"], errors="coerce")
    df = df.dropna(subset=["expiry_dt"])
    if df.empty:
        return None

    today = pd.to_datetime(dt.datetime.now().date())
    df_future = df[df["expiry_dt"] >= today]
    if df_future.empty:
        row = df.loc[df["expiry_dt"].idxmin()]
    else:
        row = df_future.loc[df_future["expiry_dt"].idxmin()]

    return str(row["expiry_date"])

def get_latest_trade_for_expiry_us(ticker: str, expiry_date: str) -> str | None:
    conn = sqlite3.connect(US_DB_PATH)
    cur = conn.cursor()
    cur.execute(
        f"""
        SELECT MAX(trade_date)
        FROM {OPTIONS_TABLE}
        WHERE ticker = ?
          AND expiry_date = ?
        """,
        (ticker, expiry_date)
    )
    row = cur.fetchone()
    conn.close()
    return row[0] if row and row[0] else None

def get_nearest_expiry_on_or_after(ticker: str, anchor_date_db: str | None) -> str | None:
    conn = sqlite3.connect(US_DB_PATH)
    df = pd.read_sql(
        f"""
        SELECT DISTINCT expiry_date
        FROM {OPTIONS_TABLE}
        WHERE ticker = ?
          AND expiry_date IS NOT NULL
        """,
        conn,
        params=(ticker,)
    )
    conn.close()
    if df.empty:
        return None

    df["expiry_date"] = df["expiry_date"].astype(str)
    df["expiry_dt"] = pd.to_datetime(df["expiry_date"], errors="coerce")
    df = df.dropna(subset=["expiry_dt"])
    if df.empty:
        return None

    if anchor_date_db:
        anchor_dt = pd.to_datetime(anchor_date_db, format="%m-%d-%Y", errors="coerce")
        if pd.isna(anchor_dt):
            anchor_dt = pd.to_datetime(dt.datetime.now().date())
    else:
        anchor_dt = pd.to_datetime(dt.datetime.now().date())

    df_future = df[df["expiry_dt"] >= anchor_dt]
    if df_future.empty:
        row = df.loc[df["expiry_dt"].idxmin()]
    else:
        row = df_future.loc[df_future["expiry_dt"].idxmin()]

    return str(row["expiry_date"])

def get_nearest_stock_date_on_or_before(anchor_date_db: str) -> str | None:
    conn = sqlite3.connect(US_DB_PATH)
    df = pd.read_sql(
        f"SELECT DISTINCT trade_date FROM {STOCK_TABLE}",
        conn
    )
    conn.close()
    if df.empty:
        return None

    df["trade_date_dt"] = pd.to_datetime(df["trade_date"], format="%m-%d-%Y", errors="coerce")
    anchor_dt = pd.to_datetime(anchor_date_db, format="%m-%d-%Y", errors="coerce")
    if pd.isna(anchor_dt):
        return None

    df = df.dropna(subset=["trade_date_dt"])
    df_le = df[df["trade_date_dt"] <= anchor_dt]
    if df_le.empty:
        row = df.loc[df["trade_date_dt"].idxmin()]
    else:
        row = df_le.loc[df_le["trade_date_dt"].idxmax()]

    return row["trade_date"]

def get_options_trade_on_or_before_anchor(ticker: str, expiry: str, anchor_date_db: str) -> str | None:
    conn = sqlite3.connect(US_DB_PATH)
    df = pd.read_sql(
        f"SELECT DISTINCT trade_date FROM {OPTIONS_TABLE} WHERE ticker=? AND expiry_date=?",
        conn, params=(ticker, expiry)
    )
    conn.close()
    if df.empty:
        return None
    df["trade_date_dt"] = pd.to_datetime(df["trade_date"], format="%d%b%Y", errors="coerce")
    df = df.dropna(subset=["trade_date_dt"])
    if df.empty:
        return None
    anchor_dt = pd.to_datetime(anchor_date_db, format="%m-%d-%Y", errors="coerce")
    if pd.isna(anchor_dt):
        return None
    df_le = df[df["trade_date_dt"] <= anchor_dt]
    if df_le.empty:
        row = df.loc[df["trade_date_dt"].idxmin()]
    else:
        row = df_le.loc[df_le["trade_date_dt"].idxmax()]
    return row["trade_date"]

# extra helper for Layer-3 CPR (nearest stock date <= anchor)
def get_nearest_stock_date_on_or_before_exact(trade_date_db: str) -> str | None:
    conn = sqlite3.connect(US_DB_PATH)
    df = pd.read_sql(
        f"SELECT DISTINCT trade_date FROM {STOCK_TABLE}",
        conn
    )
    conn.close()
    if df.empty:
        return None

    df["trade_date_dt"] = pd.to_datetime(df["trade_date"], format="%m-%d-%Y", errors="coerce")
    anchor_dt = pd.to_datetime(trade_date_db, format="%m-%d-%Y", errors="coerce")
    if pd.isna(anchor_dt):
        return None

    df = df.dropna(subset=["trade_date_dt"])
    df_le = df[df["trade_date_dt"] <= anchor_dt]
    if df_le.empty:
        row = df.loc[df["trade_date_dt"].idxmin()]
    else:
        row = df_le.loc[df_le["trade_date_dt"].idxmax()]
    return row["trade_date"]

# ================== LAYER-3 PIVOT/CPR ==================
def get_layer3_pivot_cpr(ticker: str, trade_date_db: str) -> pd.DataFrame:
    """
    Compute Pivot/CPR using the nearest available stock_daily row
    on or before trade_date_db (MM-DD-YYYY).
    """
    nearest = get_nearest_stock_date_on_or_before_exact(trade_date_db)
    if nearest is None:
        return pd.DataFrame()

    conn = sqlite3.connect(US_DB_PATH)
    df = pd.read_sql(
        f"""
        SELECT open, high, low, close
        FROM {STOCK_TABLE}
        WHERE ticker=? AND trade_date=?
        """,
        conn, params=(ticker, nearest)
    )
    conn.close()
    if df.empty:
        return pd.DataFrame()

    row = df.iloc[0]
    H = float(row["high"])
    L = float(row["low"])
    C = float(row["close"])

    P  = (H + L + C) / 3.0
    BC = (H + L) / 2.0
    TC = 2 * P - BC

    S1 = 2 * P - H
    R1 = 2 * P - L
    S2 = P - (H - L)
    R2 = P + (H - L)
    S3 = L - 2 * (H - P)
    R3 = H + 2 * (P - L)

    data = {
        "S3": S3, "S2": S2, "S1": S1,
        "BC": BC, "P": P, "TC": TC,
        "R1": R1, "R2": R2, "R3": R3,
    }
    df_out = pd.DataFrame([data])
    num_cols = df_out.select_dtypes(include=[np.number]).columns
    df_out[num_cols] = df_out[num_cols].round(2)
    return df_out

# ================== LAYER-1 ANALYTICS ==================
def build_us_analytics_for_day(trade_date_opt: str, ticker: str, expiry_hint: str | None = None):
    if not os.path.exists(US_DB_PATH):
        return

    anchor_db = pd.to_datetime(trade_date_opt, format="%d%b%Y").strftime("%m-%d-%Y")

    conn = sqlite3.connect(US_DB_PATH)

    if expiry_hint:
        expiry = expiry_hint
    else:
        expiry = get_nearest_expiry_for_us(ticker)
    if not expiry:
        conn.close()
        return

    opt_trade = get_options_trade_on_or_before_anchor(ticker, expiry, anchor_db)
    if not opt_trade:
        conn.close()
        return

    df_e = pd.read_sql(
        f"""
        SELECT *
        FROM {OPTIONS_TABLE}
        WHERE ticker=? AND trade_date=? AND expiry_date=?
        """,
        conn,
        params=(ticker, opt_trade, expiry)
    )
    if df_e.empty:
        conn.close()
        return

    df_e["expiry_date"] = df_e["expiry_date"].astype(str)
    df_e["strike"]      = df_e["strike"].astype(float)

    df_e["openInt_Call"] = df_e["openInt_Call"].fillna(0)
    df_e["openInt_Put"]  = df_e["openInt_Put"].fillna(0)
    SUMCE = float(df_e["openInt_Call"].sum())
    SUMPE = float(df_e["openInt_Put"].sum())
    PCR   = float(SUMPE / SUMCE) if SUMCE > 0 else np.nan

    # top 3 by OI across that expiry (ALL)
    top_ce_all = df_e.sort_values("openInt_Call", ascending=False).head(3)
    top_pe_all = df_e.sort_values("openInt_Put",  ascending=False).head(3)

    # merge with change for same trade_date_opt
    df_ch = pd.read_sql(
        f"""
        SELECT strike, expiry_date,
               call_close_now, put_close_now,
               call_high_now,  put_high_now,
               openInt_Call_now, change_OI_Call,
               openInt_Put_now,  change_OI_Put
        FROM {CHANGE_TABLE}
        WHERE ticker = ? AND trade_date_now = ?
        """,
        conn, params=(ticker, trade_date_opt)
    )
    if not df_ch.empty:
        df_ch["expiry_date"] = df_ch["expiry_date"].astype(str)
        df_ch["strike"]      = df_ch["strike"].astype(float)
        df_all = df_e.merge(df_ch, on=["strike", "expiry_date"], how="left")
    else:
        df_all = df_e.copy()
        df_all["call_close_now"] = np.nan
        df_all["put_close_now"]  = np.nan
        df_all["call_high_now"]  = df_all.get("call_high", 0)
        df_all["put_high_now"]   = df_all.get("put_high", 0)

    # filtered: where current close > 0
    top_ce_filt = df_all[df_all["call_close_now"].fillna(0) > 0].copy()
    top_pe_filt = df_all[df_all["put_close_now"].fillna(0)  > 0].copy()
    top_ce_filt = top_ce_filt.sort_values("openInt_Call", ascending=False).head(3)
    top_pe_filt = top_pe_filt.sort_values("openInt_Put",  ascending=False).head(3)

    data = {
        "trade_date":  anchor_db,
        "ticker":      ticker,
        "expiry_date": expiry,
        "SUMCE": round(SUMCE, 2),
        "SUMPE": round(SUMPE, 2),
        "PCR":   round(PCR, 2) if not np.isnan(PCR) else None,
    }

    def sr_from_rows(pe_row, ce_row, use_filtered=False):
        if pe_row is not None:
            strike_p = float(pe_row["strike"])
            if use_filtered and not np.isnan(pe_row.get("put_close_now", np.nan)):
                lp_p = float(pe_row.get("put_close_now", 0) or 0)
                hp_p = float(pe_row.get("put_high_now", 0)  or 0)
            else:
                lp_p = float(pe_row.get("lastPrice_Put", 0) or 0)
                hp_p = float(pe_row.get("put_high", 0)      or 0)
            S1  = strike_p - lp_p
            S12 = strike_p - hp_p
        else:
            S1 = S12 = None

        if ce_row is not None:
            strike_c = float(ce_row["strike"])
            if use_filtered and not np.isnan(ce_row.get("call_close_now", np.nan)):
                lp_c = float(ce_row.get("call_close_now", 0) or 0)
                hc_c = float(ce_row.get("call_high_now", 0)  or 0)
            else:
                lp_c = float(ce_row.get("lastPrice_Call", 0) or 0)
                hc_c = float(ce_row.get("call_high", 0)      or 0)
            R1  = strike_c + lp_c
            R12 = strike_c + hc_c
        else:
            R1 = R12 = None
        return S1, S12, R1, R12

    # ALL levels
    for i in range(3):
        pe_row = top_pe_all.iloc[i] if i < len(top_pe_all) else None
        ce_row = top_ce_all.iloc[i] if i < len(top_ce_all) else None
        S1, S12, R1, R12 = sr_from_rows(pe_row, ce_row, use_filtered=False)
        if i == 0:
            data["S1_all"], data["S12_all"], data["R1_all"], data["R12_all"] = \
                (round(S1,2) if S1 is not None else None,
                 round(S12,2) if S12 is not None else None,
                 round(R1,2) if R1 is not None else None,
                 round(R12,2) if R12 is not None else None)
        elif i == 1:
            data["S2_all"], data["S22_all"], data["R2_all"], data["R22_all"] = \
                (round(S1,2) if S1 is not None else None,
                 round(S12,2) if S12 is not None else None,
                 round(R1,2) if R1 is not None else None,
                 round(R12,2) if R12 is not None else None)
        else:
            data["S3_all"], data["S32_all"], data["R3_all"], data["R32_all"] = \
                (round(S1,2) if S1 is not None else None,
                 round(S12,2) if S12 is not None else None,
                 round(R1,2) if R1 is not None else None,
                 round(R12,2) if R12 is not None else None)

    # FILTERED levels
    for i in range(3):
        pe_row = top_pe_filt.iloc[i] if i < len(top_pe_filt) else None
        ce_row = top_ce_filt.iloc[i] if i < len(top_ce_filt) else None
        S1, S12, R1, R12 = sr_from_rows(pe_row, ce_row, use_filtered=True)
        if i == 0:
            data["S1_filt"], data["S12_filt"], data["R1_filt"], data["R12_filt"] = \
                (round(S1,2) if S1 is not None else None,
                 round(S12,2) if S12 is not None else None,
                 round(R1,2) if R1 is not None else None,
                 round(R12,2) if R12 is not None else None)
        elif i == 1:
            data["S2_filt"], data["S22_filt"], data["R2_filt"], data["R22_filt"] = \
                (round(S1,2) if S1 is not None else None,
                 round(S12,2) if S12 is not None else None,
                 round(R1,2) if R1 is not None else None,
                 round(R12,2) if R12 is not None else None)
        else:
            data["S3_filt"], data["S32_filt"], data["R3_filt"], data["R32_filt"] = \
                (round(S1,2) if S1 is not None else None,
                 round(S12,2) if S12 is not None else None,
                 round(R1,2) if R1 is not None else None,
                 round(R12,2) if R12 is not None else None)

    # Spot OHLC + PCR_OI
    nearest_spot_date = get_nearest_stock_date_on_or_before(anchor_db)
    if nearest_spot_date:
        df_spot = pd.read_sql(
            f"SELECT open, high, low, close, pcr_oi FROM {STOCK_TABLE} "
            f"WHERE ticker=? AND trade_date=?",
            conn, params=(ticker, nearest_spot_date)
        )
    else:
        df_spot = pd.DataFrame()

    if not df_spot.empty:
        sr = df_spot.iloc[0]
        data["OpnPric"]     = round(float(sr["open"]), 2)
        data["HghPric"]     = round(float(sr["high"]), 2)
        data["LwPric"]      = round(float(sr["low"]), 2)
        data["ClsPric"]     = round(float(sr["close"]), 2)
        data["spot_pcr_oi"] = round(float(sr["pcr_oi"]), 4) if not np.isnan(sr["pcr_oi"]) else None
    else:
        data["OpnPric"] = data["HghPric"] = data["LwPric"] = data["ClsPric"] = None
        data["spot_pcr_oi"] = None

    # CPR levels into summary for charts
    df_piv = get_layer3_pivot_cpr(ticker, anchor_db)
    if not df_piv.empty:
        piv = df_piv.iloc[0]
        data["S1_piv"] = float(piv["S1"])
        data["S2_piv"] = float(piv["S2"])
        data["S3_piv"] = float(piv["S3"])
        data["R1_piv"] = float(piv["R1"])
        data["R2_piv"] = float(piv["R2"])
        data["R3_piv"] = float(piv["R3"])
    else:
        data["S1_piv"] = data["S2_piv"] = data["S3_piv"] = None
        data["R1_piv"] = data["R2_piv"] = data["R3_piv"] = None

    cols = ", ".join(data.keys())
    qmarks = ", ".join(["?"] * len(data))
    update = ", ".join(
        [f"{k}=excluded.{k}" for k in data.keys()
         if k not in ("trade_date", "ticker", "expiry_date")]
    )
    cur = conn.cursor()
    cur.execute(
        f"INSERT INTO {SUMMARY_TABLE} ({cols}) VALUES ({qmarks}) "
        f"ON CONFLICT(trade_date,ticker,expiry_date) DO UPDATE SET {update}",
        list(data.values())
    )
    conn.commit()
    conn.close()

# ================== LAYER 1–3 FORMATTERS ==================
def print_layer1_tcs_style(ticker: str, trade_date_db: str) -> str:
    buf = io.StringIO()
    conn = sqlite3.connect(US_DB_PATH)
    df = pd.read_sql(
        f"SELECT * FROM {SUMMARY_TABLE} WHERE ticker=? AND trade_date=?",
        conn, params=(ticker, trade_date_db)
    )
    conn.close()
    if df.empty:
        buf.write("No Layer-1 data\n")
    else:
        row = df.iloc[0]
        line_all = {
            "S1":  row["S1_all"],  "S12":  row["S12_all"],
            "S2":  row["S2_all"],  "S22":  row["S22_all"],
            "S3":  row["S3_all"],  "S32":  row["S32_all"],
            "R1":  row["R1_all"],  "R12":  row["R12_all"],
            "R2":  row["R2_all"],  "R22":  row["R22_all"],
            "R3":  row["R3_all"],  "R32":  row["R32_all"],
            "Opn": row["OpnPric"], "High": row["HghPric"],
            "Low": row["LwPric"],  "Close": row["ClsPric"],
        }
        line_filt = {
            "S1":  row["S1_filt"],  "S12":  row["S12_filt"],
            "S2":  row["S2_filt"],  "S22":  row["S22_filt"],
            "S3":  row["S3_filt"],  "S32":  row["S32_filt"],
            "R1":  row["R1_filt"],  "R12":  row["R12_filt"],
            "R2":  row["R2_filt"],  "R22":  row["R2_filt"],
            "R3":  row["R3_filt"],  "R32":  row["R3_filt"],
            "Opn": row["OpnPric"],  "High": row["HghPric"],
            "Low": row["LwPric"],   "Close": row["ClsPric"],
        }
        df_out = pd.DataFrame([line_all, line_filt], index=["ALL", "FILTER"])
        num_cols = df_out.select_dtypes(include=[np.number]).columns
        df_out[num_cols] = df_out[num_cols].round(2)
        buf.write(df_out.to_string() + "\n")
    return buf.getvalue()

def get_layer2(ticker: str, trade_date_db: str, lookback_days: int = 5) -> pd.DataFrame:
    conn = sqlite3.connect(US_DB_PATH)
    df_stock = pd.read_sql(
        f"""
        SELECT trade_date, open, high, low, close, volume, pcr_oi
        FROM {STOCK_TABLE}
        WHERE ticker = ?
        ORDER BY trade_date
        """,
        conn, params=(ticker,)
    )
    if df_stock.empty:
        conn.close()
        return pd.DataFrame()
    df_stock["trade_date_dt"] = pd.to_datetime(df_stock["trade_date"], format="%m-%d-%Y")
    target_dt = pd.to_datetime(trade_date_db, format="%m-%d-%Y")
    df_stock = df_stock[df_stock["trade_date_dt"] <= target_dt].tail(lookback_days).copy()
    df_stock["Vol10"] = df_stock["volume"].rolling(10, min_periods=1).mean()
    df_stock["Vol20"] = df_stock["volume"].rolling(20, min_periods=1).mean()
    df_stock["Vol10_R"] = df_stock["volume"] / df_stock["Vol10"]
    df_stock["Vol20_R"] = df_stock["volume"] / df_stock["Vol20"]
    df_stock["PricePctChg"] = df_stock["close"].pct_change() * 100

    foi_list = []
    for _, row in df_stock.iterrows():
        t_db  = row["trade_date"]
        t_opt = pd.to_datetime(t_db, format="%m-%d-%Y").strftime("%d%b%Y")
        df_opt = pd.read_sql(
            f"""
            SELECT expiry_date, openInt_Call, openInt_Put
            FROM {OPTIONS_TABLE}
            WHERE ticker=? AND trade_date=?
            """,
            conn, params=(ticker, t_opt)
        )
        if df_opt.empty:
            foi_list.append((t_db, None, None))
            continue
        df_opt["expiry_date"] = df_opt["expiry_date"].astype(str)
        expiries = sorted(df_opt["expiry_date"].unique())
        expiry = expiries[0]
        df_e = df_opt[df_opt["expiry_date"] == expiry].copy()
        df_e["openInt_Call"] = df_e["openInt_Call"].fillna(0)
        df_e["openInt_Put"]  = df_e["openInt_Put"].fillna(0)
        FOI = float(df_e["openInt_Call"].sum() + df_e["openInt_Put"].sum())
        foi_list.append((t_db, expiry, FOI))

    foi_df = pd.DataFrame(foi_list, columns=["trade_date","expiry_date","FOI"])
    df_m = df_stock.merge(foi_df, on="trade_date", how="left")
    df_m["FCOI"] = df_m["FOI"].diff()
    conn.close()

    df_out = df_m.copy()
    df_out["Dates"]    = df_out["trade_date_dt"].dt.strftime("%m-%d")
    df_out["OpnPric"]  = df_out["open"]
    df_out["HghPric"]  = df_out["high"]
    df_out["LwPric"]   = df_out["low"]
    df_out["ClsPric"]  = df_out["close"]
    df_out["PCR"]      = df_out["pcr_oi"]
    df_out["FOI_val"]  = df_out["FOI"]
    df_out["FCOI_val"] = df_out["FCOI"]
    df_out["Vol10R"]   = df_out["Vol10_R"]
    df_out["Vol20R"]   = df_out["Vol20_R"]
    df_out["PricePct"] = df_out["PricePctChg"]

    cols = [
        "Dates","OpnPric","HghPric","LwPric","ClsPric",
        "PCR","FOI_val","FCOI_val","Vol10R","Vol20R","PricePct"
    ]
    df_out = df_out[cols].iloc[::-1].reset_index(drop=True)
    num_cols = df_out.select_dtypes(include=[np.number]).columns
    df_out[num_cols] = df_out[num_cols].round(2)
    return df_out

# ================== LAYER-4 FROM options_change ==================
def get_layer4_options_snapshot(ticker: str, trade_date_opt: str, top_n: int = 3, expiry_hint: str | None = None):
    conn = sqlite3.connect(US_DB_PATH)
    df_ch = pd.read_sql(
        f"""
        SELECT
            strike,
            expiry_date,
            call_close_now,
            call_high_now,
            openInt_Call_now,
            change_OI_Call,
            put_close_now,
            put_high_now,
            openInt_Put_now,
            change_OI_Put
        FROM {CHANGE_TABLE}
        WHERE ticker = ? AND trade_date_now = ?
        """,
        conn,
        params=(ticker, trade_date_opt)
    )
    if df_ch.empty:
        conn.close()
        return pd.DataFrame(), pd.DataFrame(), None

    df_ch["expiry_date"] = df_ch["expiry_date"].astype(str)
    df_ch["strike"]      = df_ch["strike"].astype(float)

    # Force Layer-4 to the same expiry as Layer-1 (/us)
    if not expiry_hint:
        conn.close()
        return pd.DataFrame(), pd.DataFrame(), None

    expiry = str(expiry_hint)
    df_e = df_ch[df_ch["expiry_date"] == expiry].copy()
    if df_e.empty:
        conn.close()
        return pd.DataFrame(), pd.DataFrame(), expiry

    df_e["call_close_now"]   = df_e["call_close_now"].fillna(0)
    df_e["put_close_now"]    = df_e["put_close_now"].fillna(0)
    df_e["openInt_Call_now"] = df_e["openInt_Call_now"].fillna(0)
    df_e["openInt_Put_now"]  = df_e["openInt_Put_now"].fillna(0)
    df_e["change_OI_Call"]   = df_e["change_OI_Call"].fillna(0)
    df_e["change_OI_Put"]    = df_e["change_OI_Put"].fillna(0)

    df_e["MONEYCOI_Call"] = df_e["call_close_now"] * df_e["change_OI_Call"]
    df_e["MONEYOI_Call"]  = df_e["call_close_now"] * df_e["openInt_Call_now"]
    df_e["MONEYCOI_Put"]  = df_e["put_close_now"]  * df_e["change_OI_Put"]
    df_e["MONEYOI_Put"]   = df_e["put_close_now"]  * df_e["openInt_Put_now"]

    calls = df_e[df_e["openInt_Call_now"] > 0].copy()
    if not calls.empty:
        calls["abs_coi"] = calls["change_OI_Call"].abs()
        calls = calls.sort_values(["openInt_Call_now", "abs_coi"], ascending=[False, False]).head(top_n)
        calls_out = pd.DataFrame({
            "Strike":   calls["strike"],
            "Close":    calls["call_close_now"],
            "ChgOI":    calls["change_OI_Call"],
            "OpenInt":  calls["openInt_Call_now"],
            "MONEYCOI": calls["MONEYCOI_Call"],
            "MONEYOI":  calls["MONEYOI_Call"],
            "TotVol":   np.nan,
        })
    else:
        calls_out = pd.DataFrame(columns=["Strike","Close","ChgOI","OpenInt","MONEYCOI","MONEYOI","TotVol"])

    puts = df_e[df_e["openInt_Put_now"] > 0].copy()
    if not puts.empty:
        puts["abs_coi"] = puts["change_OI_Put"].abs()
        puts = puts.sort_values(["openInt_Put_now", "abs_coi"], ascending=[False, False]).head(top_n)
        puts_out = pd.DataFrame({
            "Strike":   puts["strike"],
            "Close":    puts["put_close_now"],
            "ChgOI":    puts["change_OI_Put"],
            "OpenInt":  puts["openInt_Put_now"],
            "MONEYCOI": puts["MONEYCOI_Put"],
            "MONEYOI":  puts["MONEYOI_Put"],
            "TotVol":   np.nan,
        })
    else:
        puts_out = pd.DataFrame(columns=["Strike","Close","ChgOI","OpenInt","MONEYCOI","MONEYOI","TotVol"])

    conn.close()

    for df_x in (calls_out, puts_out):
        if not df_x.empty:
            num_cols = df_x.select_dtypes(include=[np.number]).columns
            df_x[num_cols] = df_x[num_cols].round(2)

    return calls_out.reset_index(drop=True), puts_out.reset_index(drop=True), expiry

def format_layer4_snapshot(calls: pd.DataFrame, puts: pd.DataFrame, expiry: str) -> str:
    buf = io.StringIO()
    buf.write("——————— LAYER-4 (Options Snapshot) ———————\n")
    if expiry:
        buf.write(f"Top CALLS (nearest expiry: {expiry})\n")
    else:
        buf.write("Top CALLS (nearest expiry)\n")

    if calls.empty:
        buf.write("No CALL data\n")
    else:
        buf.write(
            f"{'Strike':<6} | {'Close':<5} | {'ChgOI':<6} | {'OpenInt':<7} | "
            f"{'MONEYCOI':<8} | {'MONEYOI':<8} | {'TotVol':<6}\n"
        )
        for _, r in calls.iterrows():
            buf.write(
                f"{int(r['Strike']):<6} | "
                f"{float(r['Close']):<5.2f} | "
                f"{int(r['ChgOI']):<6} | "
                f"{int(r['OpenInt']):<7} | "
                f"{int(r['MONEYCOI'] or 0):<8} | "
                f"{int(r['MONEYOI'] or 0):<8} | "
                f"{int(r['TotVol'] or 0):<6}\n"
            )

    buf.write("\n")
    if expiry:
        buf.write(f"Top PUTS  (nearest expiry: {expiry})\n")
    else:
        buf.write("Top PUTS  (nearest expiry)\n")

    if puts.empty:
        buf.write("No PUT data\n")
    else:
        buf.write(
            f"{'Strike':<6} | {'Close':<5} | {'ChgOI':<6} | {'OpenInt':<7} | "
            f"{'MONEYCOI':<8} | {'MONEYOI':<8} | {'TotVol':<6}\n"
        )
        for _, r in puts.iterrows():
            buf.write(
                f"{int(r['Strike']):<6} | "
                f"{float(r['Close']):<5.2f} | "
                f"{int(r['ChgOI']):<6} | "
                f"{int(r['OpenInt']):<7} | "
                f"{int(r['MONEYCOI'] or 0):<8} | "
                f"{int(r['MONEYOI'] or 0):<8} | "
                f"{int(r['TotVol'] or 0):<6}\n"
            )

    return buf.getvalue()

# ================== CHART ==================
def draw_us_ohlc_chart(ticker: str, trade_date_db: str, out_path: str) -> str:
    conn = sqlite3.connect(US_DB_PATH)

    df = pd.read_sql(
        f"""
        SELECT trade_date, close
        FROM {STOCK_TABLE}
        WHERE ticker=?
        ORDER BY trade_date
        """,
        conn, params=(ticker,)
    )
    if df.empty:
        conn.close()
        return ""

    df["Date"] = pd.to_datetime(df["trade_date"], format="%m-%d-%Y")
    target_dt = pd.to_datetime(trade_date_db, format="%m-%d-%Y")
    df = df[df["Date"] <= target_dt].tail(30)
    if df.empty:
        conn.close()
        return ""

    df_sr = pd.read_sql(
        f"""
        SELECT S1_all, S12_all, S2_all, S22_all, S3_all, S32_all,
               R1_all, R12_all, R2_all, R22_all, R3_all, R32_all,
               S1_piv, S2_piv, S3_piv,
               R1_piv, R2_piv, R3_piv
        FROM {SUMMARY_TABLE}
        WHERE ticker=? AND trade_date=?
        """,
        conn, params=(ticker, trade_date_db)
    )
    conn.close()

    sr_vals = {}
    if not df_sr.empty:
        row = df_sr.iloc[0]
        sr_vals.update({
            "S1":  row["S1_all"],
            "S12": row["S12_all"],
            "S2":  row["S2_all"],
            "S22": row["S22_all"],
            "S3":  row["S3_all"],
            "S32": row["S32_all"],
            "R1":  row["R1_all"],
            "R12": row["R12_all"],
            "R2":  row["R2_all"],
            "R22": row["R22_all"],
            "R3":  row["R3_all"],
            "R32": row["R32_all"],
        })
        sr_vals.update({
            "S1_piv": row.get("S1_piv"),
            "S2_piv": row.get("S2_piv"),
            "S3_piv": row.get("S3_piv"),
            "R1_piv": row.get("R1_piv"),
            "R2_piv": row.get("R2_piv"),
            "R3_piv": row.get("R3_piv"),
        })

    plt.figure(figsize=(9, 5))
    ax = plt.gca()

    ax.plot(df["Date"], df["close"], marker="o", color="black", label="Close (last 30d)")

    level_colors = {
        "S1":   "#1f77b4",
        "S12":  "#2ca02c",
        "S2":   "#17becf",
        "S22":  "#9467bd",
        "S3":   "#8c564b",
        "S32":  "#e377c2",
        "R1":   "#d62728",
        "R12":  "#ff7f0e",
        "R2":   "#7f7f7f",
        "R22":  "#bcbd22",
        "R3":   "#17becf",
        "R32":  "#aec7e8",
        "S1_piv": "#0000ff",
        "S2_piv": "#004080",
        "S3_piv": "#0080ff",
        "R1_piv": "#ff0000",
        "R2_piv": "#800000",
        "R3_piv": "#ff8080",
    }

    if sr_vals:
        levels = []
        for name, val in sr_vals.items():
            if val is None or (isinstance(val, float) and np.isnan(val)):
                continue
            levels.append((name, float(val)))

        if levels:
            x_min = df["Date"].min()
            x_max = df["Date"].max()

            left_x  = x_min - pd.Timedelta(days=0.5)
            right_x = x_max + pd.Timedelta(days=0.5)

            price_min = df["close"].min()
            price_max = df["close"].max()
            price_range = price_max - price_min

            base_letter_height = price_range * 0.015 if price_range > 0 else 0.1
            label_gap = 2 * base_letter_height

            levels.sort(key=lambda x: x[1])

            for idx, (name, val) in enumerate(levels):
                c = level_colors.get(name, "black")

                ax.axhline(
                    y=val,
                    xmin=0.0,
                    xmax=1.0,
                    color=c,
                    linestyle="--",
                    linewidth=0.9
                )

                y_label = val - label_gap
                va_label = "top"
                label_text = f"{name} = {val:.2f}"

                if idx % 2 == 0:
                    ax.text(
                        left_x,
                        y_label,
                        label_text,
                        color="black",
                        fontsize=7,
                        va=va_label,
                        ha="right",
                        fontweight="bold",
                    )
                else:
                    ax.text(
                        right_x,
                        y_label,
                        label_text,
                        color="black",
                        fontsize=7,
                        va=va_label,
                        ha="left",
                        fontweight="bold",
                    )

    ax.set_title(f"{ticker} (US) - Last 30 days close + SR levels")
    ax.set_xlabel("Date")
    ax.set_ylabel("Price")
    ax.grid(True, alpha=0.3)

    ax.xaxis.set_major_formatter(mdates.DateFormatter('%m-%d'))
    plt.xticks(rotation=45)

    ax.set_xlim(
        df["Date"].min() - pd.Timedelta(days=1),
        df["Date"].max() + pd.Timedelta(days=1),
    )

    plt.tight_layout()
    plt.savefig(out_path)
    plt.close()
    return out_path

# ================== FULL 4-LAYER TEXT ==================
def build_us_layers_text(ticker: str, trade_date_db: str, expiry_hint: str | None = None) -> str:
    buf = io.StringIO()
    trade_date_opt = pd.to_datetime(trade_date_db, format="%m-%d-%Y").strftime("%d%b%Y")
    build_us_analytics_for_day(trade_date_opt, ticker, expiry_hint=expiry_hint)

    buf.write("——————— LAYER-1 ———————\n")
    buf.write(print_layer1_tcs_style(ticker, trade_date_db))

    buf.write("\n——————— LAYER-2 ———————\n")
    df_l2 = get_layer2(ticker, trade_date_db, lookback_days=5)
    if df_l2.empty:
        buf.write("No Layer-2 data\n")
    else:
        buf.write(df_l2.to_string(index=False) + "\n")

    buf.write("\n——————— LAYER-3 (Pivot/CPR) ———————\n")
    df_l3 = get_layer3_pivot_cpr(ticker, trade_date_db)
    if df_l3.empty:
        buf.write("No Layer-3 data\n")
    else:
        buf.write(df_l3.to_string(index=False) + "\n")

    calls, puts, expiry = get_layer4_options_snapshot(
        ticker, trade_date_opt, top_n=3, expiry_hint=expiry_hint
    )
    buf.write("\n")
    buf.write(format_layer4_snapshot(calls, puts, expiry))
    return buf.getvalue()

# ================== DATE HELPERS ==================
def get_latest_us_trade_date() -> str | None:
    conn = sqlite3.connect(US_DB_PATH)
    df = pd.read_sql(
        f"SELECT DISTINCT trade_date FROM {STOCK_TABLE} ORDER BY trade_date",
        conn
    )
    conn.close()
    if df.empty:
        return None
    return df["trade_date"].iloc[-1]

def get_prev_us_trade_date(trade_date_db: str) -> str | None:
    conn = sqlite3.connect(US_DB_PATH)
    df = pd.read_sql(
        f"SELECT DISTINCT trade_date FROM {STOCK_TABLE} ORDER BY trade_date",
        conn
    )
    conn.close()
    if df.empty:
        return None
    dates = list(df["trade_date"])
    if trade_date_db not in dates:
        return None
    idx = dates.index(trade_date_db)
    if idx == 0:
        return None
    return dates[idx - 1]

# ================== SR SCANNER ==================
def scan_us_sr_levels(level: str, date_ddmm: str | None) -> str:
    if date_ddmm:
        d, m = date_ddmm.split("-")
        y = dt.datetime.now().year
        trade_date_db = f"{m.zfill(2)}-{d.zfill(2)}-{y}"
    else:
        trade_date_db = get_latest_us_trade_date()
        if trade_date_db is None:
            return "No US trade dates found."

    lv = level.upper()
    prev_date = get_prev_us_trade_date(trade_date_db)
    if prev_date is None:
        return f"No previous US trade date before {trade_date_db}."

    conn = sqlite3.connect(US_DB_PATH)
    df_y = pd.read_sql(
        f"""
        SELECT ticker, trade_date,
               S1_all, S2_all, S3_all,
               R1_all, R2_all, R3_all
        FROM {SUMMARY_TABLE}
        WHERE trade_date = ?
        """,
        conn, params=(prev_date,)
    )
    df_t = pd.read_sql(
        f"""
        SELECT ticker, trade_date,
               HghPric, LwPric, OpnPric, ClsPric
        FROM {SUMMARY_TABLE}
        WHERE trade_date = ?
        """,
        conn, params=(trade_date_db,)
    )
    conn.close()

    if df_y.empty or df_t.empty:
        return f"No data for SR touch scan on {trade_date_db} (prev {prev_date})."

    df_m = df_t.merge(
        df_y,
        on="ticker",
        how="inner",
        suffixes=("_today", "_yday")
    )

    tol = 0.5

    def format_touch_table(df_sel: pd.DataFrame, title: str) -> str:
        df_sel = df_sel.copy()
        df_sel["Today"] = pd.to_datetime(df_sel["trade_date_today"]).dt.strftime("%m-%d")
        df_sel["Prev"] = pd.to_datetime(prev_date).strftime("%m-%d")
        cols = [
            "ticker", "Today", "Prev",
            "level_val", "OpnPric", "HghPric", "LwPric", "ClsPric", "diff"
        ]
        sub = df_sel[cols].copy()
        num_cols = sub.select_dtypes(include=[np.number]).columns
        sub[num_cols] = sub[num_cols].round(2)
        buf = io.StringIO()
        buf.write(f"{title}\n")
        buf.write(f"Today : {trade_date_db} | Prev : {prev_date}\n")
        buf.write("-" * 120 + "\n")
        buf.write(sub.to_string(index=False))
        buf.write("\n" + "-" * 120 + "\n")
        return buf.getvalue()

    if lv in {"S1", "S2", "S3"}:
        col_map = {"S1": "S1_all", "S2": "S2_all", "S3": "S3_all"}
        col = col_map[lv]
        df_m["level_val"] = df_m[col]
        df_m["diff"] = (df_m["HghPric"] - df_m["level_val"]).abs()
        df_hit = df_m[df_m["diff"] <= tol]
        if df_hit.empty:
            return f"No {lv} touch stocks on {trade_date_db} (prev {prev_date}) within {tol}."
        return format_touch_table(df_hit, f"{lv} TOUCH REPORT (US)")

    if lv in {"R1", "R2", "R3"}:
        col_map = {"R1": "R1_all", "R2": "R2_all", "R3": "R3_all"}
        col = col_map[lv]
        df_m["level_val"] = df_m[col]
        df_m["diff"] = (df_m["LwPric"] - df_m["level_val"]).abs()
        df_hit = df_m[df_m["diff"] <= tol]
        if df_hit.empty:
            return f"No {lv} touch stocks on {trade_date_db} (prev {prev_date}) within {tol}."
        return format_touch_table(df_hit, f"{lv} TOUCH REPORT (US)")

    if lv == "ALLS":
        records = []
        for col in ["S1_all", "S2_all", "S3_all"]:
            tmp = df_m.copy()
            tmp["level_val"] = tmp[col]
            tmp["diff"] = (tmp["HghPric"] - tmp["level_val"]).abs()
            tmp = tmp[tmp["diff"] <= tol]
            tmp["LevelName"] = col.replace("_all", "")
            records.append(tmp)
        if not records:
            return f"No S1/S2/S3 touch stocks on {trade_date_db} (prev {prev_date}) within {tol}."
        df_all = pd.concat(records, ignore_index=True)
        df_all = df_all.sort_values(["ticker", "LevelName"])
        df_all["Today"] = pd.to_datetime(df_all["trade_date_today"]).dt.strftime("%m-%d")
        df_all["Prev"] = pd.to_datetime(prev_date).strftime("%m-%d")
        cols = [
            "ticker", "Today", "Prev", "LevelName",
            "level_val", "OpnPric", "HghPric", "LwPric", "ClsPric", "diff"
        ]
        sub = df_all[cols].copy()
        num_cols = sub.select_dtypes(include=[np.number]).columns
        sub[num_cols] = sub[num_cols].round(2)
        buf = io.StringIO()
        buf.write("ALLS TOUCH REPORT (US) [S1/S2/S3]\n")
        buf.write(f"Today : {trade_date_db} | Prev : {prev_date}\n")
        buf.write("-" * 140 + "\n")
        buf.write(sub.to_string(index=False))
        buf.write("\n" + "-" * 140 + "\n")
        return buf.getvalue()

    if lv == "ALLR":
        records = []
        for col in ["R1_all", "R2_all", "R3_all"]:
            tmp = df_m.copy()
            tmp["level_val"] = tmp[col]
            tmp["diff"] = (tmp["LwPric"] - tmp["level_val"]).abs()
            tmp = tmp[tmp["diff"] <= tol]
            tmp["LevelName"] = col.replace("_all", "")
            records.append(tmp)
        if not records:
            return f"No R1/R2/R3 touch stocks on {trade_date_db} (prev {prev_date}) within {tol}."
        df_all = pd.concat(records, ignore_index=True)
        df_all = df_all.sort_values(["ticker", "LevelName"])
        df_all["Today"] = pd.to_datetime(df_all["trade_date_today"]).dt.strftime("%m-%d")
        df_all["Prev"] = pd.to_datetime(prev_date).strftime("%m-%d")
        cols = [
            "ticker", "Today", "Prev", "LevelName",
            "level_val", "OpnPric", "HghPric", "LwPric", "ClsPric", "diff"
        ]
        sub = df_all[cols].copy()
        num_cols = sub.select_dtypes(include=[np.number]).columns
        sub[num_cols] = sub[num_cols].round(2)
        buf = io.StringIO()
        buf.write("ALLR TOUCH REPORT (US) [R1/R2/R3]\n")
        buf.write(f"Today : {trade_date_db} | Prev : {prev_date}\n")
        buf.write("-" * 140 + "\n")
        buf.write(sub.to_string(index=False))
        buf.write("\n" + "-" * 140 + "\n")
        return buf.getvalue()

    return "Unknown level. Use S1/S2/S3/R1/R2/R3/ALLS/ALLR."

# ================== OPTION 5-DAY SLICE ==================
def build_us_option_slice_text(
    ticker: str,
    strike: float,
    opt_type: str,
    days: int = 5,
    mmdd_anchor: str | None = None
) -> str:
    nearest = get_nearest_strike_for_us(ticker, strike)
    if nearest is None:
        return f"No strikes found for {ticker}."
    strike = nearest

    conn = sqlite3.connect(US_DB_PATH)
    df = pd.read_sql(
        f"""
        SELECT trade_date, strike, expiry_date,
               lastPrice_Call, openInt_Call, vol_Call,
               lastPrice_Put,  openInt_Put,  vol_Put,
               money_oi_call, vol_rank_call,
               money_oi_put,  vol_rank_put
        FROM {OPTIONS_TABLE}
        WHERE ticker = ? AND strike = ?
        ORDER BY trade_date
        """,
        conn,
        params=(ticker, strike)
    )
    conn.close()
    if df.empty:
        return f"No options data for {ticker} @ {strike}."

    try:
        df["trade_date_dt"] = pd.to_datetime(df["trade_date"], format="%d%b%Y")
    except Exception:
        df["trade_date_dt"] = pd.to_datetime(df["trade_date"], errors="coerce")

    df["expiry_dt"] = pd.to_datetime(df["expiry_date"], errors="coerce")

    if mmdd_anchor:
        try:
            m, d = mmdd_anchor.split("-")
            latest_dt = df["trade_date_dt"].max()
            year = latest_dt.year if pd.notna(latest_dt) else dt.datetime.now().year
            anchor_dt = dt.datetime(year, int(m), int(d))
            df = df[df["trade_date_dt"] <= anchor_dt].copy()
        except Exception:
            pass

    today = pd.Timestamp.today().normalize()
    cutoff = today + pd.Timedelta(days=30)
    df = df[(df["expiry_dt"] > today) & (df["expiry_dt"] <= cutoff)].copy()
    if df.empty:
        return f"No expiries in next 1 month for {ticker} @ {strike}."

    df = df.sort_values(["trade_date_dt", "expiry_dt"])

    last_dates = (
        df["trade_date_dt"].dropna().drop_duplicates().sort_values().tail(days)
    )
    df = df[df["trade_date_dt"].isin(last_dates)].copy()
    if df.empty:
        return (
            f"No data for last {days} days for {ticker} @ {strike} "
            f"within next 1 month expiries."
        )

    opt_type = opt_type.upper()
    if opt_type == "C":
        df["ChgOI"] = df.groupby("expiry_dt")["openInt_Call"].diff().fillna(0)
        df["Close"]    = df["lastPrice_Call"]
        df["OpenInt"]  = df["openInt_Call"]
        df["MONEYCOI"] = df["Close"] * df["ChgOI"]
        df["MONEYOI"]  = df["money_oi_call"]
        df["VOL_RANK"] = df["vol_rank_call"]
        df["TotVol"]   = df["vol_Call"]
        otype = "CE"
    else:
        df["ChgOI"] = df.groupby("expiry_dt")["openInt_Put"].diff().fillna(0)
        df["Close"]    = df["lastPrice_Put"]
        df["OpenInt"]  = df["openInt_Put"]
        df["MONEYCOI"] = df["Close"] * df["ChgOI"]
        df["MONEYOI"]  = df["money_oi_put"]
        df["VOL_RANK"] = df["vol_rank_put"]
        df["TotVol"]   = df["vol_Put"]
        otype = "PE"

    df["Open"] = df["Close"]
    df["High"] = df["Close"]
    df["Low"]  = df["Close"]

    df["Date"] = df["trade_date_dt"].dt.strftime("%d-%m")
    df["Expiry"] = df["expiry_dt"].dt.strftime("%d-%m")

    df = df.sort_values(["expiry_dt", "trade_date_dt"], ascending=[False, True])

    buf = io.StringIO()
    title = (
        f"{ticker} {strike} {otype} last {len(last_dates)} days "
        f"(expiries next 1 month)"
    )
    if mmdd_anchor:
        title += f"  | Anchor: {mmdd_anchor}"
    buf.write(title + "\n\n")

    for expiry_key, g in df.groupby("Expiry", sort=False):
        tot_oi    = int(g["OpenInt"].iloc[-1] or 0)
        chg_oi_ld = int(g["ChgOI"].iloc[-1] or 0)
        sum_mcoi  = int(g["MONEYCOI"].sum() or 0)
        avg_vr    = round(float(g["VOL_RANK"].mean() or 0), 1)
        sum_vol   = int(g["TotVol"].sum() or 0)

        buf.write("-" * 80 + "\n")
        buf.write(
            f"Expiry: {expiry_key} | "
            f"TotOI: {tot_oi} | "
            f"ChgOI(last): {chg_oi_ld} | "
            f"ΣMONEYCOI: {sum_mcoi} | "
            f"Avg VOL_RANK: {avg_vr} | "
            f"ΣVol: {sum_vol}\n"
        )
        buf.write("-" * 80 + "\n")
        buf.write(
            f"{'Date':<6} | {'Strike':<6} | {'Type':<4} | "
            f"{'Open':<5} | {'High':<5} | {'Low':<5} | {'Close':<5} | "
            f"{'ChgOI':<6} | {'OpenInt':<7} | "
            f"{'MONEYCOI':<8} | {'MONEYOI':<8} | {'VOL_RANK':<8} | {'TotVol':<6}\n"
        )

        for _, r in g.iterrows():
            buf.write(
                f"{str(r['Date']):<6} | "
                f"{str(int(r['strike'])):<6} | "
                f"{otype:<4} | "
                f"{round((r['Open'] or 0), 2):<5} | "
                f"{round((r['High'] or 0), 2):<5} | "
                f"{round((r['Low'] or 0), 2):<5} | "
                f"{round((r['Close'] or 0), 2):<5} | "
                f"{int(r['ChgOI'] or 0):<6} | "
                f"{int(r['OpenInt'] or 0):<7} | "
                f"{int(r['MONEYCOI'] or 0):<8} | "
                f"{int(r['MONEYOI'] or 0):<8} | "
                f"{int(r['VOL_RANK'] or 0):<8} | "
                f"{int(r['TotVol'] or 0):<6}\n"
            )

        buf.write("\n")

    return buf.getvalue()

# ================== /uscount HELPERS ==================
def get_uscount_dates(ddmm: str | None) -> list[str]:
    conn = sqlite3.connect(US_DB_PATH)
    cur = conn.cursor()
    if ddmm is None:
        cur.execute(
            f"""
            SELECT DISTINCT trade_date_now
            FROM {CHANGE_TABLE}
            ORDER BY trade_date_now DESC
            LIMIT 4
            """
        )
    else:
        cur.execute(f"SELECT MAX(trade_date_now) FROM {CHANGE_TABLE}")
        latest = cur.fetchone()[0]
        if not latest:
            conn.close()
            return []
        try:
            latest_dt = pd.to_datetime(latest, format="%d%b%Y")
            year = latest_dt.year
        except Exception:
            year = dt.datetime.now().year
        try:
            d, m = map(int, ddmm.split("-"))
            anchor_dt = dt.datetime(year, m, d)
        except Exception:
            conn.close()
            return []
        anchor_str = anchor_dt.strftime("%d%b%Y")
        cur.execute(
            f"""
            SELECT DISTINCT trade_date_now
            FROM {CHANGE_TABLE}
            WHERE trade_date_now <= ?
            ORDER BY trade_date_now DESC
            LIMIT 4
            """,
            (anchor_str,)
        )
    rows = cur.fetchall()
    conn.close()
    return [r[0] for r in rows if r[0]]

def build_uscount_table(ddmm: str | None) -> str:
    dates = get_uscount_dates(ddmm)
    if not dates:
        if ddmm:
            return "No US trade dates found for /uscount (check DD-MM input)."
        return "No US trade dates found for /uscount."

    conn = sqlite3.connect(US_DB_PATH)
    placeholders = ",".join("?" * len(dates))

    sql_agg = f"""
        SELECT
            ticker,
            trade_date_now,
            SUM(openInt_Call_now) AS sum_call_oi,
            SUM(openInt_Put_now)  AS sum_put_oi,
            SUM(change_OI_Call)   AS sum_coi_call,
            SUM(change_OI_Put)    AS sum_coi_put
        FROM {CHANGE_TABLE}
        WHERE trade_date_now IN ({placeholders})
        GROUP BY ticker, trade_date_now
    """
    df_agg = pd.read_sql(sql_agg, conn, params=dates)
    if df_agg.empty:
        conn.close()
        return "No aggregated OI/COI rows for /uscount on those dates."

    df_agg["Count"] = df_agg["sum_coi_call"].abs().fillna(0) + df_agg["sum_coi_put"].abs().fillna(0)
    df_agg["side"] = np.where(
        df_agg["sum_coi_call"].abs() >= df_agg["sum_coi_put"].abs(),
        "CE",
        "PE"
    )

    MIN_COUNT = 1
    df_agg = df_agg[df_agg["Count"] >= MIN_COUNT]
    if df_agg.empty:
        conn.close()
        return "No symbols with non-zero Count for /uscount on those dates."

    records = []
    for _, row in df_agg.iterrows():
        ticker = row["ticker"]
        trade_date_now = row["trade_date_now"]
        side = row["side"]
        count_val = float(row["Count"])

        df_ch = pd.read_sql(
            f"""
            SELECT
                strike,
                expiry_date,
                call_close_now,
                call_high_now,
                openInt_Call_now,
                change_OI_Call,
                put_close_now,
                put_high_now,
                openInt_Put_now,
                change_OI_Put
            FROM {CHANGE_TABLE}
            WHERE ticker = ? AND trade_date_now = ?
            """,
            conn,
            params=(ticker, trade_date_now)
        )
        if df_ch.empty:
            continue

        if side == "CE":
            df_ch = df_ch.copy()
            df_ch["abs_coi"] = df_ch["change_OI_Call"].abs()
            df_side = df_ch[df_ch["abs_coi"] > 0]
            if df_side.empty:
                continue
            best = df_side.sort_values("abs_coi", ascending=False).iloc[0]
            high_strike = float(best["strike"])

            c   = float(best["call_close_now"] or 0)
            h   = float(best["call_high_now"]  or c)
            o   = c
            l   = c
            pc  = c
            oi  = int(best["openInt_Call_now"] or 0)
            coi = int(best["change_OI_Call"] or 0)
        else:
            df_ch = df_ch.copy()
            df_ch["abs_coi"] = df_ch["change_OI_Put"].abs()
            df_side = df_ch[df_ch["abs_coi"] > 0]
            if df_side.empty:
                continue
            best = df_side.sort_values("abs_coi", ascending=False).iloc[0]
            high_strike = float(best["strike"])

            c   = float(best["put_close_now"] or 0)
            h   = float(best["put_high_now"]  or c)
            o   = c
            l   = c
            pc  = c
            oi  = int(best["openInt_Put_now"] or 0)
            coi = int(best["change_OI_Put"] or 0)

        try:
            d_dt = pd.to_datetime(trade_date_now, format="%d%b%Y")
            short_dt = d_dt.strftime("%d-%m")
        except Exception:
            short_dt = trade_date_now

        records.append({
            "Symbol":      ticker,
            "Type":        side,
            "Date":        short_dt,
            "Count":       int(count_val),
            "High_Strike": int(high_strike),
            "Open":        round(o, 1),
            "High":        round(h, 1),
            "Low":         round(l, 1),
            "Close":       round(c, 1),
            "PrevClose":   round(pc, 1),
            "OI":          oi,
            "COI":         coi,
            "raw_date":    trade_date_now,
        })

    conn.close()

    if not records:
        return "No symbols with qualifying strikes for /uscount on those dates."

    df_out = pd.DataFrame(records)
    df_out = df_out.sort_values(["raw_date", "Count"], ascending=[False, False])

    headers = [
        "Symbol", "Type", "Date", "Count",
        "High_Strike", "Open", "High", "Low", "Close", "PrevClose", "OI", "COI"
    ]

    col_widths = {}
    for h in headers:
        col_widths[h] = max(
            len(h),
            max(len(str(x)) for x in df_out[h])
        )

    def fmt_row(row):
        return " | ".join(
            str(row[h]).ljust(col_widths[h]) for h in headers
        )

    lines = []
    header_line = " | ".join(h.ljust(col_widths[h]) for h in headers)
    lines.append(header_line)

    last_raw_date = None
    for _, r in df_out.iterrows():
        curr = r["raw_date"]
        if last_raw_date is not None and curr != last_raw_date:
            lines.append("")
        lines.append(fmt_row(r))
        last_raw_date = curr

    return "\n".join(lines)

# ================== HELP TEXT ==================
HELP_TEXT = (
    "US Market Bot\n\n"
    "Commands:\n"
    "/us TICKER [MM-DD-YYYY]\n"
    "  - Full 4 layers (options S/R, OHLC+PCR+FOI/FCOI, CPR, options snapshot).\n"
    "    /us TICKER uses nearest expiry from today and uses that expiry as anchor date.\n"
    "    /us TICKER MM-DD-YYYY uses nearest expiry on/after that date as anchor.\n"
    "    Example: /us GLD\n"
    "    Example: /us GLD 12-26-2025\n\n"
    "/ussr LEVEL [DD-MM]\n"
    "  - SR touch scan across all US tickers (S1/S2/S3/R1/R2/R3/ALLS/ALLR).\n"
    "    DD-MM is day-month; defaults to latest US date if omitted.\n\n"
    "/usopt TICKER STRIKE TYPE [DAYS] [MM-DD]\n"
    "  - SR-style multi-day option slice on US data (chart + table).\n"
    "    TYPE: C (Call) or P (Put).\n\n"
    "/uscount [DD-MM]\n"
    "  - COUNT scan on US options using options_change.\n"
)

# ================== TELEGRAM HANDLERS ==================
@bot.message_handler(commands=['start'])
def handle_start(message):
    bot.reply_to(message, "US bot online.\nUse /help for commands.")

@bot.message_handler(commands=['help'])
def handle_help(message):
    bot.reply_to(message, HELP_TEXT)

@bot.message_handler(commands=['us', 'US'])
def handle_us_command(message):
    try:
        parts = message.text.strip().split()
        if len(parts) < 2:
            bot.reply_to(
                message,
                "Format:\n"
                "/us TICKER\n"
                "/us TICKER MM-DD-YYYY\n"
                "Example: /us GLD\n"
                "         /us GLD 12-26-2025"
            )
            return

        ticker = parts[1].upper()

        if len(parts) == 2:
            expiry_hint = get_nearest_expiry_on_or_after(ticker, None)
            if not expiry_hint:
                bot.reply_to(message, "No expiries found for this ticker.")
                return
            expiry_dt = pd.to_datetime(str(expiry_hint), errors="coerce")
            if pd.isna(expiry_dt):
                bot.reply_to(message, f"Bad expiry format in DB: {expiry_hint}")
                return
            dt_str = expiry_dt.strftime("%m-%d-%Y")
        elif len(parts) == 3:
            user_date = parts[2]
            try:
                pd.to_datetime(user_date, format="%m-%d-%Y")
            except Exception:
                bot.reply_to(
                    message,
                    "Invalid date.\n"
                    "Use MM-DD-YYYY.\n"
                    "Examples:\n"
                    "  /us GLD\n"
                    "  /us GLD 12-26-2025"
                )
                return

            expiry_hint = get_nearest_expiry_on_or_after(ticker, user_date)
            if not expiry_hint:
                bot.reply_to(message, "No expiries found for this ticker near that date.")
                return

            expiry_dt = pd.to_datetime(str(expiry_hint), errors="coerce")
            if pd.isna(expiry_dt):
                bot.reply_to(message, f"Bad expiry format in DB: {expiry_hint}")
                return
            dt_str = expiry_dt.strftime("%m-%d-%Y")
        else:
            bot.reply_to(
                message,
                "Too many arguments for /us.\n"
                "Use:\n"
                "  /us TICKER\n"
                "  /us TICKER MM-DD-YYYY\n"
                "For options slice, use /usopt TICKER STRIKE TYPE [DAYS] [MM-DD]."
            )
            return

        temp_dir = tempfile.gettempdir()
        chart_path = os.path.join(temp_dir, f"us_{ticker}_{dt_str}.png")
        png = draw_us_ohlc_chart(ticker, dt_str, chart_path)
        if png and os.path.exists(png):
            with open(png, "rb") as f:
                bot.send_photo(message.chat.id, f)

        layers = build_us_layers_text(ticker, dt_str, expiry_hint=expiry_hint)
        raw_text = layers

        html = f"<b>US Analytics: {ticker} {dt_str}</b>\n<pre>{raw_text}</pre>"
        if len(html) <= 4096:
            bot.send_message(message.chat.id, html, parse_mode="HTML")
        else:
            bot.send_message(
                message.chat.id,
                f"<b>US Analytics: {ticker} {dt_str}</b>",
                parse_mode="HTML"
            )
            chunk = 3500
            for i in range(0, len(raw_text), chunk):
                part = "<pre>" + raw_text[i:i+chunk] + "</pre>"
                bot.send_message(message.chat.id, part, parse_mode="HTML")
    except Exception as e:
        bot.reply_to(message, f"Error: {e}")

@bot.message_handler(commands=['ussr', 'USSR'])
def handle_ussr_command(message):
    try:
        parts = message.text.strip().split()
        if len(parts) < 2 or len(parts) > 3:
            bot.reply_to(
                message,
                "Format:\n"
                "/ussr LEVEL\n"
                "/ussr LEVEL DD-MM\n"
                "LEVEL: S1,S2,S3,R1,R2,R3,ALLS,ALLR\n"
            )
            return

        level = parts[1]
        date_ddmm = parts[2] if len(parts) == 3 else None

        raw_text = scan_us_sr_levels(level, date_ddmm)
        html = "<pre>" + raw_text + "</pre>"
        if len(html) <= 4096:
            bot.send_message(message.chat.id, html, parse_mode="HTML")
        else:
            chunk = 3500
            for i in range(0, len(raw_text), chunk):
                part = "<pre>" + raw_text[i:i+chunk] + "</pre>"
                bot.send_message(message.chat.id, part, parse_mode="HTML")
    except Exception as e:
        bot.reply_to(message, f"Error: {e}")

@bot.message_handler(commands=['usopt', 'USOPT'])
def handle_usopt_command(message):
    try:
        parts = message.text.strip().split()
        if len(parts) < 4:
            bot.reply_to(
                message,
                "Format:\n"
                "/usopt TICKER STRIKE TYPE [DAYS] [MM-DD]\n"
                "TYPE: C or P\n"
            )
            return

        ticker = parts[1].upper()
        strike = float(parts[2])
        opt_type = parts[3].upper()

        days = 5
        mmdd_anchor = None

        if len(parts) >= 5:
            if "-" in parts[4]:
                mmdd_anchor = parts[4]
            else:
                days = int(parts[4])
        if len(parts) >= 6:
            mmdd_anchor = parts[5]

        if opt_type not in ("C", "P"):
            bot.reply_to(message, "TYPE must be C or P.")
            return

        latest = get_latest_us_trade_date()
        if latest:
            temp_dir = tempfile.gettempdir()
            chart_path = os.path.join(temp_dir, f"usopt_{ticker}_{int(strike)}_{opt_type}.png")
            png = draw_us_ohlc_chart(ticker, latest, chart_path)
            if png and os.path.exists(png):
                with open(png, "rb") as f:
                    bot.send_photo(message.chat.id, f)

        text = build_us_option_slice_text(ticker, strike, opt_type, days, mmdd_anchor)
        html = "<pre>" + text + "</pre>"
        if len(html) <= 4096:
            bot.send_message(message.chat.id, html, parse_mode="HTML")
        else:
            chunk = 3500
            for i in range(0, len(text), chunk):
                part = "<pre>" + text[i:i+chunk] + "</pre>"
                bot.send_message(message.chat.id, part, parse_mode="HTML")
    except Exception as e:
        bot.reply_to(message, f"Error: {e}")

@bot.message_handler(commands=['uscount', 'USCOUNT'])
def handle_uscount_command(message):
    try:
        parts = message.text.strip().split()
        if len(parts) > 2:
            bot.reply_to(
                message,
                "Format:\n"
                "/uscount\n"
                "/uscount DD-MM\n"
            )
            return

        date_ddmm = parts[1] if len(parts) == 2 else None
        text = build_uscount_table(date_ddmm)
        html = "<pre>" + text + "</pre>"
        if len(html) <= 4096:
            bot.send_message(message.chat.id, html, parse_mode="HTML")
        else:
            chunk = 3500
            for i in range(0, len(text), chunk):
                part = "<pre>" + text[i:i+chunk] + "</pre>"
                bot.send_message(message.chat.id, part, parse_mode="HTML")
    except Exception as e:
        bot.reply_to(message, f"Error: {e}")

# ================== MAIN ==================
if __name__ == "__main__":
    ensure_options_money_columns()
    update_options_money_fields_and_ranks()
    bot.infinity_polling()
