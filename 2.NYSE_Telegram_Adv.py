import os
import sqlite3
import pandas as pd
import numpy as np

# ================== CONFIG ==================
US_DB_PATH    = r"C:\Users\srini\Options_chain_data\US_data.db"
SUMMARY_TABLE = "us_analytics_daily"
OPTIONS_TABLE = "options_daily"
CHANGE_TABLE  = "options_change"
STOCK_TABLE   = "stock_daily"

# ================== TABLE SETUP (run once) ==================
def recreate_us_analytics_table():
    if not os.path.exists(US_DB_PATH):
        print("DB not found:", US_DB_PATH)
        return
    conn = sqlite3.connect(US_DB_PATH)
    cur = conn.cursor()

    cur.execute(f"DROP TABLE IF EXISTS {SUMMARY_TABLE}")
    cur.execute(f"""
        CREATE TABLE {SUMMARY_TABLE} (
            trade_date   TEXT NOT NULL,  -- MM-DD-YYYY
            ticker       TEXT NOT NULL,
            expiry_date  TEXT NOT NULL,
            SUMCE        REAL,
            SUMPE        REAL,
            PCR          REAL,

            -- top CE strikes
            SCE1         REAL, OICE1      REAL, SCE1_VOL      REAL,
            SCE2         REAL, OICE2      REAL, SCE2_VOL      REAL,
            SCE3         REAL, OICE3      REAL, SCE3_VOL      REAL,

            -- top PE strikes
            SPE1         REAL, OIPE1      REAL, SPE1_VOL      REAL,
            SPE2         REAL, OIPE2      REAL, SPE2_VOL      REAL,
            SPE3         REAL, OIPE3      REAL, SPE3_VOL      REAL,

            -- Layer-1 (ALL options) S/R
            S1_all       REAL, S12_all    REAL,
            S2_all       REAL, S22_all    REAL,
            S3_all       REAL, S32_all    REAL,
            R1_all       REAL, R12_all    REAL,
            R2_all       REAL, R22_all    REAL,
            R3_all       REAL, R32_all    REAL,

            -- Layer-1 (FILTERED options) S/R
            S1_filt      REAL, S12_filt   REAL,
            S2_filt      REAL, S22_filt   REAL,
            S3_filt      REAL, S32_filt   REAL,
            R1_filt      REAL, R12_filt   REAL,
            R2_filt      REAL, R22_filt   REAL,
            R3_filt      REAL, R32_filt   REAL,

            -- Spot OHLC + PCR from stock_daily
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
    print(f"✅ Recreated {SUMMARY_TABLE}")


# ================== PER-TICKER ANALYTICS BUILDER ==================
def build_us_analytics_for_day(trade_date_opt: str, ticker: str):
    """
    Build analytics for one ticker and one options_daily.trade_date (e.g. '24Dec2025').

    Writes/updates 1 row into us_analytics_daily. No printing.
    """
    if not os.path.exists(US_DB_PATH):
        return

    conn = sqlite3.connect(US_DB_PATH)

    # 1) options_daily base
    df_opt = pd.read_sql(
        f"""
        SELECT *
        FROM {OPTIONS_TABLE}
        WHERE ticker = ? AND trade_date = ?
        """,
        conn,
        params=(ticker, trade_date_opt)
    )
    if df_opt.empty:
        conn.close()
        return

    df_opt["expiry_date"] = df_opt["expiry_date"].astype(str)
    expiries = sorted(df_opt["expiry_date"].unique())
    expiry = expiries[0]
    df_e = df_opt[df_opt["expiry_date"] == expiry].copy()

    df_e["openInt_Call"] = df_e["openInt_Call"].fillna(0)
    df_e["openInt_Put"]  = df_e["openInt_Put"].fillna(0)
    SUMCE = float(df_e["openInt_Call"].sum())
    SUMPE = float(df_e["openInt_Put"].sum())
    PCR   = float(SUMPE / SUMCE) if SUMCE > 0 else np.nan

    top_ce_all = df_e.sort_values("openInt_Call", ascending=False).head(3)
    top_pe_all = df_e.sort_values("openInt_Put",  ascending=False).head(3)

    # 2) options_change for filtered close_now
    df_ch = pd.read_sql(
        f"""
        SELECT strike, expiry_date,
               call_close_now, put_close_now,
               call_high_now,  put_high_now
        FROM {CHANGE_TABLE}
        WHERE ticker = ? AND trade_date_now = ?
        """,
        conn,
        params=(ticker, trade_date_opt)
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

    top_ce_filt = df_all[df_all["call_close_now"].fillna(0) >= 0.2].copy()
    top_pe_filt = df_all[df_all["put_close_now"].fillna(0)  >= 0.2].copy()
    top_ce_filt = top_ce_filt.sort_values("openInt_Call", ascending=False).head(3)
    top_pe_filt = top_pe_filt.sort_values("openInt_Put",  ascending=False).head(3)

    trade_date_db = pd.to_datetime(trade_date_opt, format="%d%b%Y").strftime("%m-%d-%Y")
    data = {
        "trade_date":  trade_date_db,
        "ticker":      ticker,
        "expiry_date": expiry,
        "SUMCE": round(SUMCE, 2),
        "SUMPE": round(SUMPE, 2),
        "PCR":   round(PCR, 2) if not np.isnan(PCR) else None,
    }

    # top CE/PE
    for i in range(3):
        if i < len(top_ce_all):
            r = top_ce_all.iloc[i]
            data[f"SCE{i+1}"]     = float(r["strike"])
            data[f"OICE{i+1}"]    = float(r["openInt_Call"])
            data[f"SCE{i+1}_VOL"] = float(r.get("vol_Call", 0) or 0)
        else:
            data[f"SCE{i+1}"]     = None
            data[f"OICE{i+1}"]    = None
            data[f"SCE{i+1}_VOL"] = None

        if i < len(top_pe_all):
            r = top_pe_all.iloc[i]
            data[f"SPE{i+1}"]     = float(r["strike"])
            data[f"OIPE{i+1}"]    = float(r["openInt_Put"])
            data[f"SPE{i+1}_VOL"] = float(r.get("vol_Put", 0) or 0)
        else:
            data[f"SPE{i+1}"]     = None
            data[f"OIPE{i+1}"]    = None
            data[f"SPE{i+1}_VOL"] = None

    # helper for S/R
    def sr_from_rows(pe_row, ce_row, use_filtered=False):
        # S from put
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

        # R from call
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

    # Line A (all)
    for i in range(3):
        pe_row = top_pe_all.iloc[i] if i < len(top_pe_all) else None
        ce_row = top_ce_all.iloc[i] if i < len(top_ce_all) else None
        S1, S12, R1, R12 = sr_from_rows(pe_row, ce_row, use_filtered=False)
        if i == 0:
            data["S1_all"]  = round(S1, 2)  if S1  is not None else None
            data["S12_all"] = round(S12, 2) if S12 is not None else None
            data["R1_all"]  = round(R1, 2)  if R1  is not None else None
            data["R12_all"] = round(R12, 2) if R12 is not None else None
        elif i == 1:
            data["S2_all"]  = round(S1, 2)  if S1  is not None else None
            data["S22_all"] = round(S12, 2) if S12 is not None else None
            data["R2_all"]  = round(R1, 2)  if R1  is not None else None
            data["R22_all"] = round(R12, 2) if R12 is not None else None
        else:
            data["S3_all"]  = round(S1, 2)  if S1  is not None else None
            data["S32_all"] = round(S12, 2) if S12 is not None else None
            data["R3_all"]  = round(R1, 2)  if R1  is not None else None
            data["R32_all"] = round(R12, 2) if R12 is not None else None

    # Line B (filtered)
    for i in range(3):
        pe_row = top_pe_filt.iloc[i] if i < len(top_pe_filt) else None
        ce_row = top_ce_filt.iloc[i] if i < len(top_ce_filt) else None
        S1, S12, R1, R12 = sr_from_rows(pe_row, ce_row, use_filtered=True)
        if i == 0:
            data["S1_filt"]  = round(S1, 2)  if S1  is not None else None
            data["S12_filt"] = round(S12, 2) if S12 is not None else None
            data["R1_filt"]  = round(R1, 2)  if R1  is not None else None
            data["R12_filt"] = round(R12, 2) if R12 is not None else None
        elif i == 1:
            data["S2_filt"]  = round(S1, 2)  if S1  is not None else None
            data["S22_filt"] = round(S12, 2) if S12 is not None else None
            data["R2_filt"]  = round(R1, 2)  if R1  is not None else None
            data["R22_filt"] = round(R12, 2) if R12 is not None else None
        else:
            data["S3_filt"]  = round(S1, 2)  if S1  is not None else None
            data["S32_filt"] = round(S12, 2) if S12 is not None else None
            data["R3_filt"]  = round(R1, 2)  if R1  is not None else None
            data["R32_filt"] = round(R12, 2) if R12 is not None else None

    # 3) spot from stock_daily
    df_spot = pd.read_sql(
        f"""
        SELECT open, high, low, close, pcr_oi
        FROM {STOCK_TABLE}
        WHERE ticker = ? AND trade_date = ?
        """,
        conn,
        params=(ticker, trade_date_db)
    )
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

    # 4) upsert
    cols = ", ".join(data.keys())
    qmarks = ", ".join(["?"] * len(data))
    update = ", ".join(
        [f"{k}=excluded.{k}" for k in data.keys()
         if k not in ("trade_date", "ticker", "expiry_date")]
    )

    cur = conn.cursor()
    cur.execute(f"""
        INSERT INTO {SUMMARY_TABLE} ({cols})
        VALUES ({qmarks})
        ON CONFLICT(trade_date, ticker, expiry_date)
        DO UPDATE SET {update}
    """, list(data.values()))
    conn.commit()
    conn.close()


# ================== BATCH BUILDER FOR ALL STOCKS/INDEXES ==================
def build_us_analytics_for_all():
    """
    Build us_analytics_daily for ALL tickers and ALL trade_date in options_daily.
    Call this once after loading daily data.
    """
    if not os.path.exists(US_DB_PATH):
        print("DB not found:", US_DB_PATH)
        return

    conn = sqlite3.connect(US_DB_PATH)
    df_pairs = pd.read_sql(
        f"""
        SELECT DISTINCT trade_date, ticker
        FROM {OPTIONS_TABLE}
        ORDER BY trade_date, ticker
        """,
        conn
    )
    conn.close()

    if df_pairs.empty:
        print("No options_daily data to process")
        return

    for _, row in df_pairs.iterrows():
        trade_date_opt = row["trade_date"]   # e.g. '24Dec2025'
        ticker         = row["ticker"]
        build_us_analytics_for_day(trade_date_opt, ticker)

    print("✅ Finished building us_analytics_daily for all tickers/dates")


# ================== LAYER PRINTERS (same as before) ==================
def print_layer1_tcs_style(ticker: str, trade_date_db: str):
    conn = sqlite3.connect(US_DB_PATH)
    df = pd.read_sql(
        f"""
        SELECT *
        FROM {SUMMARY_TABLE}
        WHERE ticker = ? AND trade_date = ?
        """,
        conn,
        params=(ticker, trade_date_db)
    )
    conn.close()
    if df.empty:
        print("No Layer-1 data")
        return

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
        "R2":  row["R2_filt"],  "R22":  row["R22_filt"],
        "R3":  row["R3_filt"],  "R32":  row["R32_filt"],
        "Opn": row["OpnPric"],  "High": row["HghPric"],
        "Low": row["LwPric"],   "Close": row["ClsPric"],
    }

    df_out = pd.DataFrame(
        [line_all, line_filt],
        index=["Layer1_all", "Layer1_filtered"]
    )
    num_cols = df_out.select_dtypes(include=[np.number]).columns
    df_out[num_cols] = df_out[num_cols].round(2)
    print(df_out.to_string())


def get_layer2(ticker: str, trade_date_db: str, lookback_days: int = 5) -> pd.DataFrame:
    conn = sqlite3.connect(US_DB_PATH)

    df_stock = pd.read_sql(
        f"""
        SELECT trade_date, open, high, low, close, volume, pcr_oi
        FROM {STOCK_TABLE}
        WHERE ticker = ?
        ORDER BY trade_date
        """,
        conn,
        params=(ticker,)
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
            WHERE ticker = ? AND trade_date = ?
            """,
            conn,
            params=(ticker, t_opt)
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

    foi_df = pd.DataFrame(foi_list, columns=["trade_date", "expiry_date", "FOI"])
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
        "Dates", "OpnPric", "HghPric", "LwPric", "ClsPric",
        "PCR", "FOI_val", "FCOI_val", "Vol10R", "Vol20R", "PricePct"
    ]
    df_out = df_out[cols].iloc[::-1].reset_index(drop=True)

    num_cols = df_out.select_dtypes(include=[np.number]).columns
    df_out[num_cols] = df_out[num_cols].round(2)
    return df_out


def get_layer3_pivot_cpr(ticker: str, trade_date_db: str) -> pd.DataFrame:
    conn = sqlite3.connect(US_DB_PATH)
    df = pd.read_sql(
        f"""
        SELECT open, high, low, close
        FROM {STOCK_TABLE}
        WHERE ticker = ? AND trade_date = ?
        """,
        conn,
        params=(ticker, trade_date_db)
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

    S1 = 2*P - H
    R1 = 2*P - L
    S2 = P - (H - L)
    R2 = P + (H - L)
    S3 = L - 2*(H - P)
    R3 = H + 2*(P - L)

    data = {
        "S3": S3, "S2": S2, "S1": S1,
        "BC": BC, "P": P, "TC": TC,
        "R1": R1, "R2": R2, "R3": R3
    }
    df_out = pd.DataFrame([data])
    num_cols = df_out.select_dtypes(include=[np.number]).columns
    df_out[num_cols] = df_out[num_cols].round(2)
    return df_out


def get_layer4_options_snapshot(ticker: str, trade_date_opt: str, top_n: int = 3):
    conn = sqlite3.connect(US_DB_PATH)
    df = pd.read_sql(
        f"""
        SELECT ticker, company_name, asset_type, strike, expiry_date,
               openInt_Call, lastPrice_Call, vol_Call,
               openInt_Put,  lastPrice_Put,  vol_Put
        FROM {OPTIONS_TABLE}
        WHERE ticker = ? AND trade_date = ?
        """,
        conn,
        params=(ticker, trade_date_opt)
    )
    conn.close()
    if df.empty:
        return pd.DataFrame(), pd.DataFrame()

    df["expiry_date"] = df["expiry_date"].astype(str)
    expiries = sorted(df["expiry_date"].unique())
    expiry = expiries[0]
    df_e = df[df["expiry_date"] == expiry].copy()

    calls = df_e[df_e["openInt_Call"].notna() & (df_e["openInt_Call"] > 0)].copy()
    calls = calls.sort_values("openInt_Call", ascending=False).head(top_n)
    calls_out = pd.DataFrame({
        "Strike":   calls["strike"],
        "Close":    calls["lastPrice_Call"],
        "OpenInt":  calls["openInt_Call"],
        "TotVol":   calls["vol_Call"],
    })

    puts = df_e[df_e["openInt_Put"].notna() & (df_e["openInt_Put"] > 0)].copy()
    puts = puts.sort_values("openInt_Put", ascending=False).head(top_n)
    puts_out = pd.DataFrame({
        "Strike":   puts["strike"],
        "Close":    puts["lastPrice_Put"],
        "OpenInt":  puts["openInt_Put"],
        "TotVol":   puts["vol_Put"],
    })

    for df_x in (calls_out, puts_out):
        if not df_x.empty:
            num_cols = df_x.select_dtypes(include=[np.number]).columns
            df_x[num_cols] = df_x[num_cols].round(2)

    return calls_out.reset_index(drop=True), puts_out.reset_index(drop=True)


def print_us_layers(ticker: str, trade_date_db: str):
    """
    ticker: e.g. 'GOOG'
    trade_date_db: MM-DD-YYYY
    """
    trade_date_opt = pd.to_datetime(trade_date_db, format="%m-%d-%Y").strftime("%d%b%Y")
    # assumes build_us_analytics_for_all was already run for the day

    print("\n——————— LAYER-1 ———————")
    print_layer1_tcs_style(ticker, trade_date_db)

    print("\n——————— LAYER-2 ———————")
    df_l2 = get_layer2(ticker, trade_date_db, lookback_days=5)
    if df_l2.empty:
        print("No Layer-2 data")
    else:
        print(df_l2.to_string(index=False))

    print("\n——————— LAYER-3 (Pivot/CPR) ———————")
    df_l3 = get_layer3_pivot_cpr(ticker, trade_date_db)
    if df_l3.empty:
        print("No Layer-3 data")
    else:
        print(df_l3.to_string(index=False))

    print("\n——————— LAYER-4 (Options Snapshot) ———————")
    calls, puts = get_layer4_options_snapshot(ticker, trade_date_opt, top_n=3)
    if calls.empty and puts.empty:
        print("No Layer-4 data")
    else:
        print("Top CALLS (nearest expiry)")
        print(calls.to_string(index=False))
        print("\nTop PUTS (nearest expiry)")
        print(puts.to_string(index=False))


# ================== EXAMPLE DAILY FLOW ==================
if __name__ == "__main__":
    # 1) Once (or when schema changes)
    # recreate_us_analytics_table()

    # 2) After loading options_daily/options_change/stock_daily each day:
    build_us_analytics_for_all()

    # 3) Then you can print any stock/index and date:
    # print_us_layers("GOOG", "12-24-2025")
    # print_us_layers("SLV",  "12-22-2025")
