from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
import sqlite3
import nest_asyncio
import asyncio
import plotly.graph_objects as go
from datetime import datetime
import numpy as np
from PIL import Image, ImageDraw, ImageFont
import os
import html
import pandas as pd


# =====================================================
# CONFIG
# =====================================================

DB_PATH = r"C:\Users\srini\Options_chain_data\oi_data.db"
BOT_TOKEN = "8018716820:AAEMAtRy6D0B0xt7SJgJB-bj7VF07ld4aVA"

nest_asyncio.apply()

MAX_LEN = 3700
MAX_COL_WIDTH = 18

LOG_DIR = "logs"
IMAGE_BASE = "oi_sr_"

INDEX_TABLES = {
    "niftyfo": "NIFTYFO",
    "niftym": "NIFTYM",
    "sensexfo": "SENSEXFO",
    "bankfo": "BANKFO",
    "midfo": "MIDFO",
}

LOT_SIZES = {
    "niftyfo": 75,
    "niftym": 75,
    "sensexfo": 20,
    "bankfo": 35,
    "midfo": 140,
}

MIN_ROWS = 12

# index lot sizes for /SR index mode
INDEX_LOTS_FOR_SR = {'NIFTY': 75, 'BANKNIFTY': 35, 'SENSEX': 20, 'MIDCPNIFTY': 75}

# =====================================================
# LOGGING
# =====================================================

def ensure_log_dir():
    if not os.path.isdir(LOG_DIR):
        os.makedirs(LOG_DIR, exist_ok=True)

def get_log_path():
    ensure_log_dir()
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    pid = os.getpid()
    return os.path.join(LOG_DIR, f"bot_log_{stamp}_pid{pid}.txt")

LOG_PATH = get_log_path()

def log_line(text: str):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(f"[{ts}] {text}\n")

# =====================================================
# COMMON UTILS
# =====================================================

async def send_in_blocks(text: str, update: Update):
    text = html.escape(text)
    for i in range(0, len(text), MAX_LEN):
        part = text[i:i + MAX_LEN]
        await update.message.reply_text(f"<pre>{part}</pre>", parse_mode="HTML")

def int_comma(x):
    try:
        return '{:,}'.format(int(round(float(x))))
    except Exception:
        return 'NA'

def int_clean(x):
    try:
        return int(round(float(x)))
    except Exception:
        return 0

def get_font(fontsize=17):
    try:
        return ImageFont.truetype("calibri.ttf", fontsize)
    except Exception:
        return ImageFont.truetype("arial.ttf", fontsize)

# =====================================================
# NEW: 5-DAY OPTION SLICE FOR /SR STOCK STRIKE CE DD-MM
# =====================================================

def get_5day_option_slice(symbol: str, strike: float, opt_type: str, anchor_date: str | None):
    sym = symbol.upper()
    opt_type = opt_type.upper()

    index_map = {
        "NIFTY": (
            "NIFTYFO",
            "TckrSymb", "TradDt", "StrkPric", "OptnTp",
            "OpnPric", "LwPric", "HghPric", "ClsPric",
            "OpnIntrst", "ChngInOpnIntrst",
            None, None,
            "UndrlygPric"
        ),
        "BANKNIFTY": (
            "BANKFO",
            "TckrSymb", "TradDt", "StrkPric", "OptnTp",
            "OpnPric", "LwPric", "HghPric", "ClsPric",
            "OpnIntrst", "ChngInOpnIntrst",
            None, None,
            "UndrlygPric"
        ),
        "SENSEX": (
            "SENSEXFO",
            "TckrSymb", "TradDt", "StrkPric", "OptnTp",
            "OpnPric", "LwPric", "HghPric", "ClsPric",
            "OpnIntrst", "ChngInOpnIntrst",
            None, None,
            "UndrlygPric"
        ),
        "MIDCPNIFTY": (
            "MIDFO",
            "TckrSymb", "TradDt", "StrkPric", "OptnTp",
            "OpnPric", "LwPric", "HghPric", "ClsPric",
            "OpnIntrst", "ChngInOpnIntrst",
            None, None,
            "UndrlygPric"
        ),
    }

    is_index = sym in index_map

    if is_index:
        (table, sym_col, date_col, strike_col, type_col,
         open_col, low_col, high_col, close_col,
         oi_col, chg_oi_col,
         volrank_col, vol_col,
         und_col) = index_map[sym]
    else:
        table       = "STO1"
        sym_col     = "Symbol"
        date_col    = "Trade_Date"
        strike_col  = "StrkPric"
        type_col    = "Option_Type"
        open_col    = "OpnPric"
        low_col     = "LwPric"
        high_col    = "HghPric"
        close_col   = "ClsPric"
        oi_col      = "Open_Interest"
        chg_oi_col  = "Change_in_OI"
        volrank_col = "VOL_RANK"
        vol_col     = "Total_Trading_Volume"
        und_col     = "UndrlygPric"

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    # dates
    if anchor_date:
        cur.execute(
            f"""
            SELECT DISTINCT {date_col}
            FROM {table}
            WHERE {sym_col} = ?
              AND {date_col} <= ?
            ORDER BY {date_col} DESC
            LIMIT 5
            """,
            (sym, anchor_date)
        )
    else:
        cur.execute(
            f"""
            SELECT DISTINCT {date_col}
            FROM {table}
            WHERE {sym_col} = ?
            ORDER BY {date_col} DESC
            LIMIT 5
            """,
            (sym,)
        )

    date_rows = cur.fetchall()
    if not date_rows:
        conn.close()
        return "", f"No dates found for {sym} in {table}."

    dates = [r[0] for r in date_rows]

    # strikes
    strike_val = float(strike)
    cur.execute(
        f"""
        SELECT DISTINCT {strike_col}
        FROM {table}
        WHERE {sym_col} = ?
          AND {date_col} IN ({",".join(["?"]*len(dates))})
          AND {type_col} = ?
        """,
        (sym, *dates, opt_type)
    )
    avail_strikes = [float(r[0]) for r in cur.fetchall()]

    if not avail_strikes:
        conn.close()
        return "", f"No {opt_type} data for {sym} in given dates."

    if strike_val in avail_strikes:
        chosen_strike = strike_val
    else:
        chosen_strike = min(avail_strikes, key=lambda x: abs(x - strike_val))

    # rows
    cur.execute(
        f"""
        SELECT {date_col}, {strike_col}, {type_col},
               {open_col}, {low_col}, {high_col}, {close_col},
               {chg_oi_col}, {oi_col},
               {volrank_col if volrank_col else 'NULL'},
               {vol_col if vol_col else 'NULL'}
        FROM {table}
        WHERE {sym_col} = ?
          AND {date_col} IN ({",".join(["?"]*len(dates))})
          AND {type_col} = ?
          AND {strike_col} = ?
        ORDER BY {date_col} DESC
        """,
        (sym, *dates, opt_type, chosen_strike)
    )
    rows = cur.fetchall()
    conn.close()

    if not rows:
        return "", f"No rows for {sym} {opt_type} at strike {chosen_strike}."

    headers = ["Date", "Strike", "Type", "Open", "Low", "High", "Close",
               "ChgOI", "OpenInt", "MONEYCOI", "MONEYOI", "VOL_RANK", "TotVol"]
    str_rows = []
    for dt, strk, t, op, lw, hi, cls, chg_oi, oi, vr, vol in rows:
        op_v   = int_clean(op)
        lw_v   = int_clean(lw)
        hi_v   = int_clean(hi)
        cls_v  = int_clean(cls)
        chg_v  = int_clean(chg_oi)
        oi_v   = int_clean(oi)
        moneycoi = chg_v * cls_v
        moneyoi  = oi_v * cls_v
        vr_s   = str(int_clean(vr)) if vr is not None else "NA"
        vol_s  = int_comma(vol) if vol is not None else "NA"
        date_short = dt[8:10] + "-" + dt[5:7]

        str_rows.append([
            date_short,
            str(int(round(float(strk)))),
            t,
            int_comma(op_v),
            int_comma(lw_v),
            int_comma(hi_v),
            int_comma(cls_v),
            int_comma(chg_v),
            int_comma(oi_v),
            int_comma(moneycoi),
            int_comma(moneyoi),
            vr_s,
            vol_s
        ])

    col_widths = [
        max(len(headers[i]), max(len(r[i]) for r in str_rows))
        for i in range(len(headers))
    ]
    header_line = " | ".join(headers[i].ljust(col_widths[i]) for i in range(len(headers)))
    lines = [header_line]
    for r in str_rows:
        lines.append(" | ".join(r[i].ljust(col_widths[i]) for i in range(len(headers))))

    text = "\n".join(lines)
    return text, ""

# =====================================================
# PART 1: OLD SR SCANNER -> /srsr
# =====================================================

def resolve_ddmm(cur, ddmm: str):
    try:
        d, m = map(int, ddmm.split("-"))
    except Exception:
        return None

    cur.execute("SELECT MAX(TradDt) FROM PCR2")
    latest = cur.fetchone()[0]
    if not latest:
        return None

    year = int(latest[:4])
    return f"{year:04d}-{m:02d}-{d:02d}"

def fetch_level_data(levels, bias_type, user_ddmm=None):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    if user_ddmm:
        target_dt = resolve_ddmm(cur, user_ddmm)
        if not target_dt:
            conn.close()
            return "Invalid date format. Use DD-MM."
    else:
        cur.execute("SELECT MAX(TradDt) FROM PCR2")
        target_dt = cur.fetchone()[0]

    cur.execute("SELECT MAX(TradDt) FROM PCR2")
    latest_dt = cur.fetchone()[0]

    conds = " AND ".join(
        [f"c.LwPric <= p.{l} AND c.HghPric >= p.{l}" for l in levels]
    )
    select_lvls = ", ".join([f"p.{l}" for l in levels])

    query = f"""
        SELECT
            c.TckrSymb,
            c.TradDt,
            {select_lvls},
            c.OpnPric, c.HghPric, c.LwPric, c.ClsPric,
            l.OpnPric, l.HghPric, l.LwPric, l.ClsPric
        FROM PCR2 c
        JOIN PCR2 p ON p.TckrSymb = c.TckrSymb
         AND p.TradDt = (
            SELECT MAX(x.TradDt)
            FROM PCR2 x
            WHERE x.TckrSymb = c.TckrSymb
              AND x.TradDt < c.TradDt
         )
        JOIN PCR2 l ON l.TckrSymb = c.TckrSymb
         AND l.TradDt = ?
        WHERE c.TradDt = ?
          AND {conds}
    """

    cur.execute(query, (latest_dt, target_dt))
    rows = cur.fetchall()
    conn.close()

    if not rows:
        return "No matching stocks found."

    results = []
    for r in rows:
        sym = r[0]
        dt = r[1]
        lvl_vals = r[2:2 + len(levels)]
        o, h, l, c = r[2 + len(levels):6 + len(levels)]
        lo, lh, ll, lc = r[6 + len(levels):]

        score = 1 if c < lc else 0
        ref_lvl = lvl_vals[0]
        if bias_type == "SUPPORT":
            lscore = "UP" if c > ref_lvl else "DOWN"
        else:
            lscore = "DOWN" if c < ref_lvl else "UP"

        results.append((score, sym, dt, lvl_vals, o, h, l, c, lo, lh, ll, lc, lscore))

    results.sort(key=lambda x: (-x[0], x[1]))

    title = f"{'+'.join(levels)} TOUCH REPORT"
    out = []
    out.append(f"📌 {title}")
    out.append(f"📅 Trade Date : {datetime.strptime(target_dt,'%Y-%m-%d').strftime('%d-%m')}")
    out.append("─" * 180)

    header = (
        f"{'STOCK':<12}{'DATE':<8}"
        + "".join([f"{l:>8}" for l in levels]) +
        f"{'OPEN':>9}{'HIGH':>9}{'LOW':>9}{'CLOSE':>9}"
        f"{'LOPEN':>9}{'LHIGH':>9}{'LLOW':>9}{'LCLOSE':>9}"
        f"{'SCORE':>7}{'LSCORE':>9}"
    )
    out.append(header)
    out.append("─" * 180)

    for row in results:
        score, sym, dt, lvls, o, h, l, c, lo, lh, ll, lc, lscore = row
        dtf = datetime.strptime(dt, "%Y-%m-%d").strftime("%d-%m")

        line = f"{sym:<12}{dtf:<8}"
        for v in lvls:
            line += f"{v:>8.2f}"
        line += (
            f"{o:>9.2f}{h:>9.2f}{l:>9.2f}{c:>9.2f}"
            f"{lo:>9.2f}{lh:>9.2f}{ll:>9.2f}{lc:>9.2f}"
            f"{score:>7}{lscore:>9}"
        )
        out.append(line)

    out.append("─" * 180)
    return "\n".join(out)

async def srscan_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /srsr S1 | /srsr ALLS | /srsr R2 08-12")
        return

    cmd = context.args[0].upper()
    date_arg = context.args[1] if len(context.args) == 2 else None

    level_map = {
        "S1": (("S1",), "SUPPORT"),
        "S2": (("S2",), "SUPPORT"),
        "S3": (("S3",), "SUPPORT"),
        "R1": (("R1",), "RESISTANCE"),
        "R2": (("R2",), "RESISTANCE"),
        "R3": (("R3",), "RESISTANCE"),
        "ALLS": (("S1", "S2", "S3"), "SUPPORT"),
        "ALLR": (("R1", "R2", "R3"), "RESISTANCE"),
    }

    if cmd not in level_map:
        await update.message.reply_text("Invalid SRSR command.")
        return

    levels, bias = level_map[cmd]
    text = fetch_level_data(levels, bias, date_arg)
    await send_in_blocks(text, update)

# =====================================================
# PART 2: INDEX OI STRUCTURE IMAGES -> /niftyfo, etc.
# =====================================================

def get_index_data(conn, table_key: str):
    table = INDEX_TABLES[table_key]
    date = conn.execute(f"SELECT MAX(TradDt) FROM {table}").fetchone()[0]
    if not date:
        return None

    df = pd.read_sql_query(
        f"""
        SELECT StrkPric, OptnTp, UndrlygPric,
               OpnIntrst, ChngInOpnIntrst,
               OpnPric, HghPric, LwPric, ClsPric
        FROM {table}
        WHERE TradDt = ?
        """,
        conn,
        params=(date,)
    )
    if df.empty:
        return None

    spot = float(df["UndrlygPric"].iloc[0])

    opp = {
        (float(r.StrkPric), r.OptnTp):
        (r.OpnPric, r.HghPric, r.LwPric, r.ClsPric)
        for r in df.itertuples()
    }

    top10_coi = df[df["ChngInOpnIntrst"] > 0].sort_values(
        "ChngInOpnIntrst", ascending=False
    ).head(10)
    top10_oi = df.sort_values("OpnIntrst", ascending=False).head(10)

    rows = []

    pe_pos = df[
        (df["OptnTp"] == "PE") &
        (df["StrkPric"] < spot) &
        (df["ChngInOpnIntrst"] > 0)
    ].copy()
    pe_pos["dist"] = spot - pe_pos["StrkPric"]
    pe_pos = pe_pos.sort_values("dist").head(2)

    ce_pos = df[
        (df["OptnTp"] == "CE") &
        (df["StrkPric"] > spot) &
        (df["ChngInOpnIntrst"] > 0)
    ].copy()
    ce_pos["dist"] = ce_pos["StrkPric"] - spot
    ce_pos = ce_pos.sort_values("dist").head(2)

    pe_neg = df[
        (df["OptnTp"] == "PE") &
        (df["StrkPric"] > spot) &
        (df["ChngInOpnIntrst"] < 0)
    ].copy()
    pe_neg["dist"] = pe_neg["StrkPric"] - spot
    pe_neg = pe_neg.sort_values("dist").head(2)

    ce_neg = df[
        (df["OptnTp"] == "CE") &
        (df["StrkPric"] < spot) &
        (df["ChngInOpnIntrst"] < 0)
    ].copy()
    ce_neg["dist"] = spot - ce_neg["StrkPric"]
    ce_neg = ce_neg.sort_values("dist").head(2)

    if not pe_pos.empty:
        rows.append(("SUPPORT (+COI)", pe_pos))
    if not ce_pos.empty:
        rows.append(("RESIST (+COI)", ce_pos))
    if not pe_neg.empty:
        rows.append(("UPSIDE UNWIND (-COI)", pe_neg))
    if not ce_neg.empty:
        rows.append(("DOWNSIDE UNWIND (-COI)", ce_neg))

    return {
        "date": date,
        "spot": spot,
        "rows": rows,
        "opp": opp,
        "coi_ce": (top10_coi["OptnTp"] == "CE").sum(),
        "coi_pe": (top10_coi["OptnTp"] == "PE").sum(),
        "oi_ce": (top10_oi["OptnTp"] == "CE").sum(),
        "oi_pe": (top10_oi["OptnTp"] == "PE").sum(),
    }

def create_image(symbol: str, d: dict, path: str, table_key: str):
    lot = LOT_SIZES[table_key]
    img = Image.new("RGB", (1220, 650), "white")
    draw = ImageDraw.Draw(img)

    try:
        title = ImageFont.truetype("arialbd.ttf", 28)
        head  = ImageFont.truetype("arialbd.ttf", 16)
        text  = ImageFont.truetype("arial.ttf", 15)
    except Exception:
        title = head = text = ImageFont.load_default()

    headers = [
        "ZN","T","STRK","COI(L)","OI(L)",
        "O","H","L","C",
        "IMP1","IMP2","OO","OH","OL","OC"
    ]
    xs = [20 + i*60 for i in range(len(headers)+1)]
    row_h = 28

    draw.text((280,10), f"{symbol} OI / COI STRUCTURE + UNWIND", font=title, fill="black")
    draw.text(
        (380,45),
        f"Date: {d['date']}   Spot: {d['spot']:.2f}   Lot:{lot}",
        font=head, fill="black"
    )

    draw.text((20,80), f"TOP 10 COI → CE:{d['coi_ce']} PE:{d['coi_pe']}", font=text, fill="black")
    draw.text((20,100), f"TOP 10 OI  → CE:{d['oi_ce']} PE:{d['oi_pe']}", font=text, fill="black")

    top = 130
    bottom = top + row_h * (MIN_ROWS + 1)

    draw.rectangle((xs[0], top, xs[-1], bottom), outline="black", width=2)
    draw.line((xs[0], top+row_h, xs[-1], top+row_h), fill="black", width=2)

    for x in xs[1:-1]:
        draw.line((x, top, x, bottom), fill="black")

    for i,h in enumerate(headers):
        draw.text((xs[i]+4, top+6), h, font=head, fill="black")

    zone_map = {
        "SUPPORT (+COI)": "SUP+",
        "RESIST (+COI)": "RES+",
        "UPSIDE UNWIND (-COI)": "UP−",
        "DOWNSIDE UNWIND (-COI)": "DN−",
    }

    yy = top + row_h + 4

    for zone, dfz in d["rows"]:
        for r in dfz.itertuples():
            coi_lot = int(r.ChngInOpnIntrst // lot)
            oi_lot  = int(r.OpnIntrst // lot)

            if r.OptnTp == "PE":
                imp1 = int(r.StrkPric - r.ClsPric)
                imp2 = int(r.StrkPric - r.HghPric)
                opp_type = "CE"
            else:
                imp1 = int(r.StrkPric + r.ClsPric)
                imp2 = int(r.StrkPric + r.HghPric)
                opp_type = "PE"

            oo,oh,ol,oc = d["opp"].get((float(r.StrkPric), opp_type), ("-","-","-","-"))

            vals = [
                zone_map[zone], r.OptnTp, int(r.StrkPric),
                coi_lot, oi_lot,
                f"{r.OpnPric:.1f}", f"{r.HghPric:.1f}",
                f"{r.LwPric:.1f}", f"{r.ClsPric:.1f}",
                imp1, imp2, oo, oh, ol, oc
            ]

            for i,v in enumerate(vals):
                draw.text((xs[i]+4, yy), str(v), font=text, fill="black")

            yy += row_h

    img.save(path)

async def send_img(update: Update, ctx: ContextTypes.DEFAULT_TYPE, table_key: str):
    with sqlite3.connect(DB_PATH) as conn:
        d = get_index_data(conn, table_key)

    if not d or not d["rows"]:
        await update.message.reply_text("No valid structure / unwind data")
        return

    path = f"{IMAGE_BASE}{table_key}.png"
    create_image(INDEX_TABLES[table_key], d, path, table_key)
    await update.message.reply_photo(open(path, "rb"))

async def niftyfo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await send_img(update, ctx, "niftyfo")

async def niftym(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await send_img(update, ctx, "niftym")

async def sensexfo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await send_img(update, ctx, "sensexfo")

async def bankfo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await send_img(update, ctx, "bankfo")

async def midfo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await send_img(update, ctx, "midfo")

# =====================================================
# PART 3: MULTI-LAYER / OPTIONS ANALYTICS -> /sr
# =====================================================

def render_one_table_image(
    headers, rows, nearest_idx,
    top3_openint, imp_openint, strikes_openint, impact_openint, avg_impact_openint,
    top3_chgoi, imp_chgoi, strikes_chgoi, impact_chgoi, avg_impact_chgoi,
    heading, filename
):
    ncols = len(headers)
    nrows = len(rows)
    font = get_font(13)

    def cell_text_width(text, font):
        bbox = font.getbbox(str(text))
        return bbox[2] - bbox[0]

    def cell_text_height(font):
        bbox = font.getbbox("X")
        return bbox[3] - bbox[1]

    cell_widths = [
        max([cell_text_width(headers[c], font)] + [cell_text_width(r[c], font) for r in rows]) + 18
        for c in range(ncols)
    ]
    col_sum = sum(cell_widths)
    row_h = cell_text_height(font) + 20
    pad_top = 50
    summary_h = 280
    table_h = row_h * (nrows + 1)

    img_w = min(2100, col_sum + 32)
    img_h = pad_top + table_h + summary_h + 20
    img = Image.new('RGB', (img_w, img_h), 'white')
    draw = ImageDraw.Draw(img)

    blue = (21, 99, 199)
    hf = get_font(24)
    bbox = draw.textbbox((0, 0), heading, font=hf)
    w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    draw.text(((img_w - w) // 2, 12), heading, font=hf, fill=blue)

    y = pad_top
    for c, h_ in enumerate(headers):
        x = sum(cell_widths[:c]) + 8
        draw.rectangle([x, y, x + cell_widths[c], y + row_h], fill='#e6f5ff')
        draw.rectangle([x, y, x + cell_widths[c], y + row_h], outline="black", width=2)
        draw.text((x + 8, y + 9), str(h_)[:MAX_COL_WIDTH], font=font, fill='black')
    y += row_h

    for r, vals in enumerate(rows):
        is_nearest = r in nearest_idx
        for c, val in enumerate(vals):
            x = sum(cell_widths[:c]) + 8
            cell_col = '#fff'
            fill = 'black'
            bold = False
            if r in top3_openint and headers[c] == "MONEYOI":
                cell_col = '#ffe6e8'
                fill = 'red'
                bold = True
            if r in top3_chgoi and headers[c] == "MONEYCOI":
                cell_col = '#e6edff'
                fill = 'red'
                bold = True

            draw.rectangle([x, y, x + cell_widths[c], y + row_h], fill=cell_col)
            draw.rectangle([x, y, x + cell_widths[c], y + row_h], outline="black", width=1)
            vtxt = str(val) if len(str(val)) < MAX_COL_WIDTH else str(val)[:MAX_COL_WIDTH]
            fontrow = get_font(18) if bold or is_nearest else font
            draw.text((x + 8, y + 9), vtxt, font=fontrow, fill=fill)
        y += row_h

    summary_y = pad_top + table_h + 17
    sumf = get_font(18)
    bigf = get_font(36)

    draw.text((38, summary_y), "Top 3 MONEYOI:", font=sumf, fill='#1543b0')
    draw.text(
        (56, summary_y + 32),
        ", ".join([f"{v:,}({s})" for v, s in zip(imp_openint, strikes_openint)]),
        font=sumf, fill='#1543b0'
    )
    draw.text(
        (38, summary_y + 68),
        f"AVG IMPACT (OpenInt): {avg_impact_openint:,}",
        font=bigf, fill='#1749e3'
    )

    draw.text((38, summary_y + 135), "Top 3 MONEYCOI:", font=sumf, fill='#2451aa')
    draw.text(
        (56, summary_y + 167),
        ", ".join([f"{v:,}({s})" for v, s in zip(imp_chgoi, strikes_chgoi)]),
        font=sumf, fill='#2451aa'
    )
    draw.text(
        (38, summary_y + 203),
        f"AVG IMPACT (ChgOI): {avg_impact_chgoi:,}",
        font=bigf, fill='#a10ae3'
    )

    img.save(filename)

def render_both_tables_stacked(
    headers,
    ce_rows, ce_nearest,
    ce_top3idx_oi, ce_top3_impacts_oi, ce_top3_strikes_oi, ce_top3_impact_openint, ce_avg_impact_oi,
    ce_top3idx_cg, ce_top3_impacts_cg, ce_top3_strikes_cg, ce_top3_impact_chgoi, ce_avg_impact_cg,
    pe_rows, pe_nearest,
    pe_top3idx_oi, pe_top3_impacts_oi, pe_top3_strikes_oi, pe_top3_impact_openint, pe_avg_impact_oi,
    pe_top3idx_cg, pe_top3_impacts_cg, pe_top3_strikes_cg, pe_top3_impact_chgoi, pe_avg_impact_cg,
    supheading, filename
):
    render_one_table_image(
        headers, ce_rows, ce_nearest,
        ce_top3idx_oi, ce_top3_impacts_oi, ce_top3_strikes_oi, ce_top3_impact_openint, ce_avg_impact_oi,
        ce_top3idx_cg, ce_top3_impacts_cg, ce_top3_strikes_cg, ce_top3_impact_chgoi, ce_avg_impact_cg,
        "CALLS", "ce_table.png"
    )
    render_one_table_image(
        headers, pe_rows, pe_nearest,
        pe_top3idx_oi, pe_top3_impacts_oi, pe_top3_strikes_oi, pe_top3_impact_openint, pe_avg_impact_oi,
        pe_top3idx_cg, pe_top3_impacts_cg, pe_top3_strikes_cg, pe_top3_impact_chgoi, pe_avg_impact_cg,
        "PUTS", "pe_table.png"
    )

    img_ce = Image.open("ce_table.png")
    img_pe = Image.open("pe_table.png")

    img_w = max(img_ce.width, img_pe.width)
    img_h = img_ce.height + img_pe.height + 70

    combo = Image.new('RGB', (img_w, img_h), 'white')
    draw = ImageDraw.Draw(combo)
    font = get_font(28)
    bbox = draw.textbbox((0, 0), supheading, font=font)
    w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    draw.text(((img_w - w) // 2, 12), supheading, fill='#153099', font=font)

    combo.paste(img_ce, (0, 47))
    combo.paste(img_pe, (0, 47 + img_ce.height))

    combo.save(filename)

def prepare_table_data_for_plot(symbol, lot_size, target_strike):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    index_tables = {
        "NIFTY": "niftyfo",
        "BANKNIFTY": "banfo",
        "SENSEX": "sensexfo",
        "MIDCPNIFTY": "midfo",
    }
    symbol_upper = symbol.upper()

    if symbol_upper in index_tables:
        table_name = index_tables[symbol_upper]
        cursor.execute(f"""
            SELECT MAX(TradDt)
            FROM {table_name}
            WHERE TckrSymb = ?
        """, (symbol_upper,))
        latest_dt_row = cursor.fetchone()
        latest_dt = latest_dt_row[0] if latest_dt_row and latest_dt_row[0] is not None else None

        if not latest_dt:
            conn.close()
            return (
                [], [], [], [], [], [], 0,
                [], [], [], [], 0,
                [], [], [], [], [], 0,
                [], [], [], [], 0
            )

        cursor.execute(f"""
            SELECT DISTINCT StrkPric
            FROM {table_name}
            WHERE TckrSymb = ? AND TradDt = ? AND StrkPric IS NOT NULL
        """, (symbol_upper, latest_dt))
        strikes = sorted(float(row[0]) for row in cursor.fetchall() if row[0] is not None)

        if not strikes:
            conn.close()
            return (
                [], [], [], [], [], [], 0,
                [], [], [], [], 0,
                [], [], [], [], [], 0,
                [], [], [], [], 0
            )

        closest = min(strikes, key=lambda x: abs(x - target_strike))
        closest_int = int(round(closest))
        idx = strikes.index(closest)
        start = max(0, idx - 6)
        end = min(len(strikes), idx + 7)
        strikes_sel = strikes[start:end]

        phs = ",".join("?" * len(strikes_sel))
        query = f"""
            SELECT TradDt, StrkPric, OptnTp,
                   OpnPric, HghPric, LwPric, ClsPric, PrvsClsgPric,
                   OpnIntrst, ChngInOpnIntrst
            FROM {table_name}
            WHERE TckrSymb=? AND TradDt=? AND StrkPric IN ({phs})
            ORDER BY StrkPric ASC, OptnTp
        """
        cursor.execute(query, [symbol_upper, latest_dt] + strikes_sel)

    else:
        table_name = "STO1"
        symbol_col = "Symbol"
        opttype_col = "Option_Type"
        date_col = "Trade_Date"
        strike_col = "StrkPric"
        open_col = "OpnPric"
        high_col = "HghPric"
        low_col = "LwPric"
        close_col = "ClsPric"
        prevclose_col = "PrvsClsgPric"
        oi_col = "Open_Interest"
        chg_oi_col = "Change_in_OI"

        cursor.execute(f"""
            SELECT MIN(FininstrmActlXpryDt)
            FROM {table_name}
            WHERE {symbol_col} = ?
              AND FininstrmActlXpryDt IS NOT NULL
        """, (symbol_upper,))
        exp_row = cursor.fetchone()
        nearest_exp = exp_row[0] if exp_row and exp_row[0] is not None else None

        if not nearest_exp:
            conn.close()
            return (
                [], [], [], [], [], [], 0,
                [], [], [], [], 0,
                [], [], [], [], [], 0,
                [], [], [], [], 0
            )

        cursor.execute(f"""
            SELECT MAX({date_col})
            FROM {table_name}
            WHERE {symbol_col} = ?
              AND FininstrmActlXpryDt = ?
        """, (symbol_upper, nearest_exp))
        dt_row = cursor.fetchone()
        latest_dt = dt_row[0] if dt_row and dt_row[0] is not None else None

        if not latest_dt:
            conn.close()
            return (
                [], [], [], [], [], [], 0,
                [], [], [], [], 0,
                [], [], [], [], [], 0,
                [], [], [], [], 0
            )

        cursor.execute(f"""
            SELECT DISTINCT {strike_col}
            FROM {table_name}
            WHERE {symbol_col} = ?
              AND FininstrmActlXpryDt = ?
              AND {date_col} = ?
              AND {strike_col} IS NOT NULL
        """, (symbol_upper, nearest_exp, latest_dt))
        strikes = sorted(float(row[0]) for row in cursor.fetchall() if row[0] is not None)

        if not strikes:
            conn.close()
            return (
                [], [], [], [], [], [], 0,
                [], [], [], [], 0,
                [], [], [], [], [], 0,
                [], [], [], [], 0
            )

        closest = min(strikes, key=lambda x: abs(x - target_strike))
        closest_int = int(round(closest))
        idx = strikes.index(closest)
        start = max(0, idx - 6)
        end = min(len(strikes), idx + 7)
        strikes_sel = strikes[start:end]

        phs = ",".join("?" * len(strikes_sel))
        query = f"""
            SELECT {date_col}, {strike_col}, {opttype_col},
                   {open_col}, {high_col}, {low_col}, {close_col}, {prevclose_col},
                   {oi_col}, {chg_oi_col}
            FROM {table_name}
            WHERE {symbol_col} = ?
              AND FininstrmActlXpryDt = ?
              AND {date_col} = ?
              AND {strike_col} IN ({phs})
            ORDER BY {strike_col} ASC, {opttype_col}
        """
        cursor.execute(query, [symbol_upper, nearest_exp, latest_dt] + strikes_sel)

    rows = cursor.fetchall()
    conn.close()

    headers = [
        "Date", "Strike", "Type", "Open", "High", "Low", "Close",
        "PrevClose", "OpenInt", "ChgOI", "Impact", "MONEYCOI", "MONEYOI"
    ]

    def prep_rows(option_type, sort_desc):
        filtered = [r for r in rows if r[2] == option_type]
        filtered_sorted = sorted(filtered, key=lambda r: float(r[1]), reverse=sort_desc)

        data_rows, nearest_idx = [], []
        openint_list = []
        chgoi_list = []

        for i, row in enumerate(filtered_sorted):
            trad_dt_fmt = datetime.strptime(row[0], '%Y-%m-%d').strftime('%d-%m') if row[0] else row[0]
            strike_val = row[1]
            strike_int = int(round(strike_val))
            typ = row[2] if row[2] else "NA"

            open_ = int_comma(row[3])
            high = int_comma(row[4])
            low = int_comma(row[5])

            close_val = int_clean(row[6])
            close = int_comma(close_val)
            prev = int_comma(row[7])

            oi_val = int_clean(row[8]) // lot_size if (row[8] not in (None, 'nan') and lot_size) else 0
            oi = int_comma(oi_val)

            chg_oi_val = int_clean(row[9]) // lot_size if (row[9] not in (None, 'nan') and lot_size) else 0
            chg_oi = int_comma(chg_oi_val)

            impact_val = (
                int(round(strike_val + close_val)) if typ == 'CE'
                else int(round(strike_val - close_val)) if typ == 'PE'
                else 0
            )
            impact = int_comma(impact_val)

            chgoi_close = chg_oi_val * close_val
            openint_close = oi_val * close_val

            vals = [
                trad_dt_fmt, int_comma(strike_val), typ,
                open_, high, low, close, prev,
                oi, chg_oi, impact,
                int_comma(chgoi_close), int_comma(openint_close)
            ]
            data_rows.append(vals)
            if strike_int == closest_int:
                nearest_idx.append(i)

            openint_list.append((openint_close, strike_int, int(impact_val)))
            chgoi_list.append((chgoi_close, strike_int, int(impact_val)))

        pos_openint = [x for x in openint_list if x[0] > 0]
        top3_openint = sorted(pos_openint, key=lambda x: x[0], reverse=True)[:3]
        imp_openint = [x[0] for x in top3_openint]
        strikes_openint_v = [x[1] for x in top3_openint]
        impact_openint = [x[2] for x in top3_openint]
        avg_impact_openint = int(round(np.mean(impact_openint))) if impact_openint else 0

        pos_chgoi = [x for x in chgoi_list if x[0] > 0]
        top3_chgoi = sorted(pos_chgoi, key=lambda x: x[0], reverse=True)[:3]
        imp_chgoi = [x[0] for x in top3_chgoi]
        strikes_chgoi_v = [x[1] for x in top3_chgoi]
        impact_chgoi = [x[2] for x in top3_chgoi]
        avg_impact_chgoi = int(round(np.mean(impact_chgoi))) if impact_chgoi else 0

        idxs_openint = [
            i for i, row in enumerate(filtered_sorted)
            if int(round(float(row[1]))) in strikes_openint_v
        ]
        idxs_chgoi = [
            i for i, row in enumerate(filtered_sorted)
            if int(round(float(row[1]))) in strikes_chgoi_v
        ]

        return (
            data_rows, nearest_idx,
            idxs_openint, imp_openint, strikes_openint_v, impact_openint, avg_impact_openint,
            idxs_chgoi, imp_chgoi, strikes_chgoi_v, impact_chgoi, avg_impact_chgoi
        )

    ce = prep_rows('CE', sort_desc=False)
    pe = prep_rows('PE', sort_desc=True)

    return headers, *ce, *pe

def table_with_summary(
    headers, rows, nearest_idx,
    top3idx_oi, imp_openint, strikes_openint, impact_openint, avg_impact_openint,
    top3idx_cg, imp_chgoi, strikes_chgoi, impact_chgoi, avg_impact_chgoi
):
    show_width = [
        min(MAX_COL_WIDTH, max(len(str(row[i])) for row in [headers] + rows))
        for i in range(len(headers))
    ]

    header_line = " | ".join(headers[i].ljust(show_width[i]) for i in range(len(headers)))
    lines = [header_line]

    for idx, vals in enumerate(rows):
        pre = "→" if idx in nearest_idx else " "
        line = " | ".join(vals[i].ljust(show_width[i]) for i in range(len(headers)))
        lines.append(pre + line)

    s_oi = "Top 3 OpenInt*Close: " + ", ".join(
        [f"{v:,}({s})" for v, s in zip(imp_openint, strikes_openint)]
    )
    s_oi_avg = f"AVG Impact (OpenInt): {avg_impact_openint:,}"

    s_cg = "Top 3 ChgOI*Close: " + ", ".join(
        [f"{v:,}({s})" for v, s in zip(imp_chgoi, strikes_chgoi)]
    )
    s_cg_avg = f"AVG Impact (ChgOI): {avg_impact_chgoi:,}"

    lines += ["", s_oi, s_oi_avg, "", s_cg, s_cg_avg]
    return "\n".join(lines)

def get_top3_from_sto1(symbol):
    symbol_upper = symbol.upper()
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    table_name = "STO1"
    symbol_col = "Symbol"
    opttype_col = "Option_Type"
    date_col = "Trade_Date"
    strike_col = "StrkPric"
    close_col = "ClsPric"
    chg_oi_col = "Change_in_OI"
    oi_col = "Open_Interest"
    und_col = "UndrlygPric"
    volrank_col = "VOL_RANK"
    vol_col = "Total_Trading_Volume"

    cursor.execute(f"""
        SELECT MIN(FininstrmActlXpryDt)
        FROM {table_name}
        WHERE {symbol_col} = ?
          AND FininstrmActlXpryDt IS NOT NULL
    """, (symbol_upper,))
    exp_row = cursor.fetchone()
    nearest_exp = exp_row[0] if exp_row and exp_row[0] is not None else None
    if not nearest_exp:
        conn.close()
        return "", "No expiry found in STO1 for this symbol."

    cursor.execute(f"""
        SELECT MAX({date_col})
        FROM {table_name}
        WHERE {symbol_col} = ?
          AND FininstrmActlXpryDt = ?
    """, (symbol_upper, nearest_exp))
    dt_row = cursor.fetchone()
    latest_dt = dt_row[0] if dt_row and dt_row[0] is not None else None
    if not latest_dt:
        conn.close()
        return "", "No trade date found in STO1 for this expiry."

    cursor.execute(f"""
        SELECT {opttype_col}, {strike_col}, {close_col}, {chg_oi_col}, {oi_col},
               {und_col}, {volrank_col}, {vol_col}
        FROM {table_name}
        WHERE {symbol_col} = ?
          AND FininstrmActlXpryDt = ?
          AND {date_col} = ?
          AND {strike_col} IS NOT NULL
        ORDER BY {strike_col}, {opttype_col}
    """, (symbol_upper, nearest_exp, latest_dt))
    rows = cursor.fetchall()
    conn.close()

    if not rows:
        return "", "No STO1 rows found for this symbol/expiry/date."

    und_values = [float(r[5]) for r in rows if r[5] is not None and float(r[5]) > 0]
    if not und_values:
        return "", "No UndrlygPric values in STO1 to use as underlying."

    underlying = und_values[0]
    low = underlying * 0.9
    high = underlying * 1.1

    ce_rows_band = []
    pe_rows_band = []

    for opttype, strike, close, chg_oi, oi_val, und_val, vol_rank, total_vol in rows:
        if strike is None:
            continue
        strike_f = float(strike)
        close_val = float(close) if close is not None else 0.0
        if close_val <= 0:
            continue
        chg_oi_val = int_clean(chg_oi)
        oi_clean = int_clean(oi_val)
        moneycoi = chg_oi_val * close_val
        moneyoi = oi_clean * close_val
        vr = int_clean(vol_rank)
        tot_vol = int_clean(total_vol)

        if not (low <= strike_f <= high):
            continue

        data = (strike_f, close_val, chg_oi_val, oi_clean, moneycoi, moneyoi, vr, tot_vol)
        if opttype == "CE":
            ce_rows_band.append(data)
        elif opttype == "PE":
            pe_rows_band.append(data)

    if ce_rows_band or pe_rows_band:
        ce_rows = sorted(ce_rows_band, key=lambda x: x[6])[:3] if ce_rows_band else []
        pe_rows = sorted(pe_rows_band, key=lambda x: x[6])[:3] if pe_rows_band else []
        reason = ""
    else:
        all_ce = []
        all_pe = []
        for opttype, strike, close, chg_oi, oi_val, und_val, vol_rank, total_vol in rows:
            if strike is None:
                continue
            strike_f = float(strike)
            close_val = float(close) if close is not None else 0.0
            if close_val <= 0:
                continue
            chg_oi_val = int_clean(chg_oi)
            oi_clean = int_clean(oi_val)
            moneycoi = chg_oi_val * close_val
            moneyoi = oi_clean * close_val
            vr = int_clean(vol_rank)
            tot_vol = int_clean(total_vol)
            data = (strike_f, close_val, chg_oi_val, oi_clean, moneycoi, moneyoi, vr, tot_vol)
            if opttype == "CE":
                all_ce.append(data)
            elif opttype == "PE":
                all_pe.append(data)

        def closest_above_below(data_list):
            if not data_list:
                return []
            below = [d for d in data_list if d[0] <= underlying]
            above = [d for d in data_list if d[0] > underlying]
            chosen = []
            if below:
                chosen.append(max(below, key=lambda x: x[0]))
            if above:
                chosen.append(min(above, key=lambda x: x[0]))
            return chosen

        ce_rows = closest_above_below(all_ce)
        pe_rows = closest_above_below(all_pe)
        ce_rows = sorted(ce_rows, key=lambda x: x[6])[:3]
        pe_rows = sorted(pe_rows, key=lambda x: x[6])[:3]
        reason = "Used closest strikes above/below underlying (no rows in ±10% band)."

    if not ce_rows and not pe_rows:
        return "", "No CE/PE rows found even for closest strikes."

    expiry_str = str(nearest_exp)

    def format_table(title, data_rows):
        if not data_rows:
            return title + "\n(No data)\n"
        headers = ["Strike", "Close", "ChgOI", "OpenInt", "MONEYCOI", "MONEYOI", "VOL_RANK", "TotVol"]
        str_rows = [
            [
                str(int(round(r[0]))),
                f"{r[1]:.1f}",
                str(r[2]),
                str(r[3]),
                int_comma(r[4]),
                int_comma(r[5]),
                str(r[6]),
                int_comma(r[7])
            ]
            for r in data_rows
        ]
        col_widths = [
            max(len(headers[i]), max(len(row[i]) for row in str_rows))
            for i in range(len(headers))
        ]
        header_line = " | ".join(headers[i].ljust(col_widths[i]) for i in range(len(headers)))
        lines = [header_line]
        for row in str_rows:
            line = " | ".join(row[i].ljust(col_widths[i]) for i in range(len(headers)))
            lines.append(line)
        return title + "\n" + "\n".join(lines) + "\n"

    txt_ce = format_table(f"Top CALLS (nearest expiry: {expiry_str})", ce_rows)
    txt_pe = format_table(f"Top PUTS  (nearest expiry: {expiry_str})", pe_rows)

    if reason:
        txt_ce = "(Fallback) " + reason + "\n\n" + txt_ce

    return txt_ce + "\n" + txt_pe, ""

def fetch_layer1_rows(ticker):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT S1, S12, S2, S22, S3, S32,
               R1, R12, R2, R22, R3, R32,
               OpnPric, HghPric, LwPric, ClsPric, TradDt
        FROM PCR2
        WHERE UPPER(TckrSymb) = ?
        ORDER BY TradDt DESC
        LIMIT 5
    """, (ticker.upper(),))
    rows = cursor.fetchall()
    conn.close()
    return rows

def format_layer1_ohlc_from_above(rows):
    headers = [
        "Date", "S1", "S12", "S2", "S22", "S3", "S32",
        "R1", "R12", "R2", "R22", "R3", "R32",
        "Open", "HIGH", "LOW", "Close"
    ]
    table_rows = []
    for i, row in enumerate(rows):
        date = datetime.strptime(row[16], '%Y-%m-%d').strftime('%d-%m')
        rvals = [f"{row[j]:.1f}" if row[j] is not None else "NA" for j in range(12)]
        if i > 0:
            prev = rows[i - 1]
            ohlc = [f"{prev[j]:.1f}" if prev[j] is not None else "NA" for j in range(12, 16)]
        else:
            ohlc = ["", "", "", ""]
        table_rows.append([date] + rvals + ohlc)

    col_widths = [
        max(len(headers[i]), max(len(r[i]) for r in table_rows))
        for i in range(len(headers))
    ]
    output = " | ".join(headers[i].ljust(col_widths[i]) for i in range(len(headers))) + " |\n"
    for r in table_rows:
        output += " | ".join(r[i].ljust(col_widths[i]) for i in range(len(headers))) + " |\n"
    return output

def fetch_layer2_and_prices(ticker):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT TradDt
        FROM CMS_Analysis
        WHERE TckrSymb = ?
        ORDER BY TradDt DESC
        LIMIT 25
    """, (ticker,))
    dates = [r[0] for r in cursor.fetchall()]
    if len(dates) < 20:
        conn.close()
        return None, None, None, None, None

    last5_dates = dates[:5]
    latest_date = last5_dates[0]

    table_rows = []
    high, low, close = None, None, None

    for idx, d in enumerate(last5_dates):
        short_date = d[5:]

        cursor.execute("""
            SELECT OpnPric, HghPric, LwPric, ClsPric, TtlTradgVol, PERCENT_CHANGE
            FROM CMS_Analysis
            WHERE TckrSymb = ? AND TradDt = ?
            ORDER BY TradDt DESC
            LIMIT 1
        """, (ticker, d))
        row = cursor.fetchone()
        opn, hgh, lw, cls, vol, chg = row if row else (None,) * 6
        if idx == 0 and row:
            high, low, close = hgh, lw, cls

        cursor.execute("""
            SELECT PCR
            FROM PCR2
            WHERE UPPER(TckrSymb) = ? AND TradDt = ?
            ORDER BY TradDt DESC
            LIMIT 1
        """, (ticker.upper(), d))
        pcr_row = cursor.fetchone()
        pcr = f"{pcr_row[0]:.2f}" if pcr_row and pcr_row[0] is not None else "NA"

        cursor.execute("""
            SELECT Open_Interest, Change_in_OI
            FROM STF1
            WHERE UPPER(Symbol) = ? AND Trade_Date = ?
            ORDER BY Trade_Date DESC
            LIMIT 1
        """, (ticker.upper(), d))
        stf_row = cursor.fetchone()
        foi = "{:,}".format(stf_row[0]) if stf_row and stf_row[0] is not None else "NA"
        fcoi = str(stf_row[1]) if stf_row and stf_row[1] is not None else "NA"

        previous_10 = dates[idx + 1:idx + 11]
        previous_20 = dates[idx + 1:idx + 21]

        if len(previous_10) < 10 or len(previous_20) < 20 or vol is None:
            vol10 = vol20 = "NA"
        else:
            cursor.execute(f"""
                SELECT TtlTradgVol
                FROM CMS_Analysis
                WHERE TckrSymb = ? AND TradDt IN ({','.join(['?'] * len(previous_10))})
            """, (ticker, *previous_10))
            vols10 = [r[0] for r in cursor.fetchall()]
            avg10 = sum(vols10) / len(vols10) if vols10 else None
            vol10 = f"{(vol / avg10):.2f}" if (vol and avg10) else "NA"

            cursor.execute(f"""
                SELECT TtlTradgVol
                FROM CMS_Analysis
                WHERE TckrSymb = ? AND TradDt IN ({','.join(['?'] * len(previous_20))})
            """, (ticker, *previous_20))
            vols20 = [r[0] for r in cursor.fetchall()]
            avg20 = sum(vols20) / len(vols20) if vols20 else None
            vol20 = f"{(vol / avg20):.2f}" if (vol and avg20) else "NA"

        opn_s = f"{opn:.1f}" if opn is not None else "NA"
        hgh_s = f"{hgh:.1f}" if hgh is not None else "NA"
        lw_s = f"{lw:.1f}" if lw is not None else "NA"
        cls_s = f"{cls:.1f}" if cls is not None else "NA"
        chg_s = f"{chg:.2f}%" if chg is not None else "NA"

        table_rows.append([
            short_date, opn_s, hgh_s, lw_s, cls_s,
            pcr, foi, fcoi, vol10, vol20, chg_s
        ])

    conn.close()

    headers = [
        "Dates", "OpnPric", "HghPric", "LwPric", "ClsPric",
        "PCR", "FOI", "FCOI", "Volume(10d)", "Volume(20d)", "Price %Chg"
    ]
    col_widths = [
        max(len(headers[i]), max(len(str(row[i])) for row in table_rows))
        for i in range(len(headers))
    ]
    header_line = "| " + " | ".join(headers[i].ljust(col_widths[i]) for i in range(len(headers))) + " |"
    layer2_rows = [header_line]
    for row in table_rows:
        row_line = "| " + " | ".join(str(row[i]).ljust(col_widths[i]) for i in range(len(headers))) + " |"
        layer2_rows.append(row_line)

    layer2_text = (
        f"——————— LAYER-2 (Latest: {latest_date}) ———————\n" +
        "\n".join(layer2_rows)
    )
    return layer2_text, high, low, close, latest_date

def calc_pivot_points(high, low, close):
    if high is None or low is None or close is None:
        return None

    P = (high + low + close) / 3
    R1 = 2 * P - low
    S1 = 2 * P - high
    R2 = P + (high - low)
    S2 = P - (high - low)
    R3 = high + 2 * (P - low)
    S3 = low - 2 * (high - P)
    TC = (P + high) / 2
    BC = (P + low) / 2

    pivots = {
        "S3": round(S3, 2),
        "S2": round(S2, 2),
        "S1": round(S1, 2),
        "BC": round(BC, 2),
        "P": round(P, 2),
        "TC": round(TC, 2),
        "R1": round(R1, 2),
        "R2": round(R2, 2),
        "R3": round(R3, 2)
    }
    return pivots

def format_layer3(pivots, date_str=""):
    if not pivots:
        return "Pivot points not available for Layer-3.\n"

    headers = ["S3", "S2", "S1", "BC", "P", "TC", "R1", "R2", "R3"]
    values = [
        str(pivots["S3"]), str(pivots["S2"]), str(pivots["S1"]),
        str(pivots["BC"]), str(pivots["P"]), str(pivots["TC"]),
        str(pivots["R1"]), str(pivots["R2"]), str(pivots["R3"])
    ]
    col_widths = [max(len(headers[i]), len(values[i])) for i in range(len(headers))]
    header_line = "| " + " | ".join(headers[i].ljust(col_widths[i]) for i in range(len(headers))) + " |"
    value_line = "| " + " | ".join(values[i].ljust(col_widths[i]) for i in range(len(values))) + " |"

    return (
        f"——————— LAYER-3 (Pivot/CPR from {date_str}) ———————\n" +
        header_line + "\n" + value_line + "\n"
    )

def build_layer1_plotly_chart(rows):
    dates = [datetime.strptime(row[16], '%Y-%m-%d').strftime("%dth %b'%y") for row in rows]
    current_price = rows[0][15]

    def val(idx):
        return [row[idx] for row in rows]

    S1, S12, S2, S22, S3, S32 = val(0), val(1), val(2), val(3), val(4), val(5)
    R1, R12, R2, R22, R3, R32 = val(6), val(7), val(8), val(9), val(10), val(11)
    Open, High, Low, Close = val(12), val(13), val(14), val(15)

    gaps = lambda arr: [x - current_price for x in arr]

    fig = go.Figure()
    fig.add_trace(go.Bar(x=dates, y=gaps(S1), name='S1', marker_color='#2E8B57',
                         offsetgroup=0, base=current_price, showlegend=True))
    fig.add_trace(go.Bar(x=dates, y=gaps(R1), name='R1', marker_color='#DB4545',
                         offsetgroup=0, base=current_price, showlegend=True))

    fig.add_trace(go.Bar(x=dates, y=gaps(S12), name='S12', marker_color='#2E8B57',
                         offsetgroup=1, base=current_price, showlegend=False))
    fig.add_trace(go.Bar(x=dates, y=gaps(R12), name='R12', marker_color='#DB4545',
                         offsetgroup=1, base=current_price, showlegend=False))

    fig.add_trace(go.Bar(x=dates, y=gaps(S2), name='S2', marker_color='#2E8B57',
                         offsetgroup=2, base=current_price, showlegend=False))
    fig.add_trace(go.Bar(x=dates, y=gaps(R2), name='R2', marker_color='#DB4545',
                         offsetgroup=2, base=current_price, showlegend=False))

    fig.add_trace(go.Bar(x=dates, y=gaps(S22), name='S22', marker_color='#2E8B57',
                         offsetgroup=3, base=current_price, showlegend=False))
    fig.add_trace(go.Bar(x=dates, y=gaps(R22), name='R22', marker_color='#DB4545',
                         offsetgroup=3, base=current_price, showlegend=False))

    fig.add_trace(go.Bar(x=dates, y=gaps(S3), name='S3', marker_color='#2E8B57',
                         offsetgroup=4, base=current_price, showlegend=False))
    fig.add_trace(go.Bar(x=dates, y=gaps(R3), name='R3', marker_color='#DB4545',
                         offsetgroup=4, base=current_price, showlegend=False))

    fig.add_trace(go.Bar(x=dates, y=gaps(S32), name='S32', marker_color='#2E8B57',
                         offsetgroup=5, base=current_price, showlegend=False))
    fig.add_trace(go.Bar(x=dates, y=gaps(R32), name='R32', marker_color='#DB4545',
                         offsetgroup=5, base=current_price, showlegend=False))

    fig.add_trace(go.Scatter(x=dates, y=Open, mode='lines', name='Open',
                             line=dict(color='#1FB8CD', width=2)))
    fig.add_trace(go.Scatter(x=dates, y=High, mode='lines', name='High',
                             line=dict(color='#D2BA4C', width=2)))
    fig.add_trace(go.Scatter(x=dates, y=Low, mode='lines', name='Low',
                             line=dict(color='#5D878F', width=2)))
    fig.add_trace(go.Scatter(x=dates, y=Close, mode='lines', name='Close',
                             line=dict(color='#B4413C', width=2)))

    fig.update_layout(
        title='Support/Resist & OHLC',
        xaxis_title='Date',
        yaxis_title='Price',
        barmode='group',
        bargap=0.15,
        bargroupgap=0.05
    )
    fig.update_traces(cliponaxis=False)
    fig.write_image('chart.png', width=900, height=550)
    return 'chart.png'

# =====================================================
# /sr COMMAND (INCLUDES /sr count AND 5-DAY SLICE DATE LOGIC)
# =====================================================

async def sr_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id if user else "unknown"
    raw_command = update.message.text if update.message else ""

    # 0) /sr count [DD-MM] -> COUNT + STO1 joined
    if context.args and context.args[0].lower() == "count":
        args = context.args[1:]
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()

        if len(args) == 0:
            cur.execute("""
                SELECT DISTINCT Trade_Date
                FROM count
                ORDER BY Trade_Date DESC
                LIMIT 4
            """)
        else:
            ddmm = args[0]
            cur.execute("SELECT MAX(Trade_Date) FROM count")
            latest = cur.fetchone()[0]
            if not latest:
                await update.message.reply_text("No data in count table.")
                conn.close()
                return
            year = int(latest[:4])
            try:
                d, m = map(int, ddmm.split("-"))
                anchor = f"{year:04d}-{m:02d}-{d:02d}"
            except Exception:
                await update.message.reply_text("Invalid date. Use /sr count DD-MM")
                conn.close()
                return

            cur.execute("""
                SELECT DISTINCT Trade_Date
                FROM count
                WHERE Trade_Date <= ?
                ORDER BY Trade_Date DESC
                LIMIT 4
            """, (anchor,))

        date_rows = cur.fetchall()
        if not date_rows:
            await update.message.reply_text("No trade dates found in count table.")
            conn.close()
            return

        dates = [r[0] for r in date_rows]
        placeholders = ",".join("?" * len(dates))

        sql = f"""
            SELECT
                c.Symbol,
                c.Option_Type,
                c.Trade_Date,
                c.Count,
                c.Highest_Strike,
                c.Open_Price,
                c.High_Price,
                c.Low_Price,
                c.Close_Price,
                c.PrevClose_Price,
                s.Open_Interest  AS OI,
                s.Change_in_OI   AS COI
            FROM count AS c
            JOIN STO1 AS s
              ON s.Symbol      = c.Symbol
             AND s.Option_Type = c.Option_Type
             AND s.Trade_Date  = c.Trade_Date
             AND s.StrkPric    = c.Highest_Strike
            WHERE c.Trade_Date IN ({placeholders})
            ORDER BY c.Trade_Date DESC, c.Symbol, c.Option_Type
        """
        cur.execute(sql, dates)
        rows = cur.fetchall()
        conn.close()

        if not rows:
            await update.message.reply_text("No matching rows in count/STO1 for those dates.")
            return

        headers = [
            "Symbol","Type","Date","Count","High_Strike",
            "Open","High","Low","Close","PrevClose","OI","COI"
        ]

        table_rows = []
        raw_dates = []
        for r in rows:
            sym, opt, dt, cnt, hst, op, hi, lo, cl, pc, oi, coi = r
            short_dt = dt[8:10] + "-" + dt[5:7]
            raw_dates.append(dt)
            table_rows.append([
                str(sym),
                str(opt),
                short_dt,
                str(cnt),
                str(int(round(hst))) if hst is not None else "NA",
                f"{op:.1f}" if op is not None else "NA",
                f"{hi:.1f}" if hi is not None else "NA",
                f"{lo:.1f}" if lo is not None else "NA",
                f"{cl:.1f}" if cl is not None else "NA",
                f"{pc:.1f}" if pc is not None else "NA",
                str(int(oi)) if oi is not None else "NA",
                str(int(coi)) if coi is not None else "NA"
            ])

        col_widths = [
            max(len(headers[i]), max(len(row[i]) for row in table_rows))
            for i in range(len(headers))
        ]

        header_line = " | ".join(headers[i].ljust(col_widths[i]) for i in range(len(headers)))
        lines = [header_line]

        last_dt = None
        for idx, row in enumerate(table_rows):
            curr_dt = raw_dates[idx]
            if last_dt is not None and curr_dt != last_dt:
                lines.append("")
            lines.append(" | ".join(row[i].ljust(col_widths[i]) for i in range(len(headers))))
            last_dt = curr_dt

        text = "\n".join(lines)
        await send_in_blocks(text, update)
        log_line(f"USER {user_id} CMD {raw_command} -> SR-COUNT mode args={args}")
        return

    # 1) SR SCANNER SHORTCUTS: /sr S1, /sr ALLS, /sr R2 08-12
    if context.args:
        first = context.args[0].upper()
        level_map = {
            "S1": (("S1",), "SUPPORT"),
            "S2": (("S2",), "SUPPORT"),
            "S3": (("S3",), "SUPPORT"),
            "R1": (("R1",), "RESISTANCE"),
            "R2": (("R2",), "RESISTANCE"),
            "R3": (("R3",), "RESISTANCE"),
            "ALLS": (("S1", "S2", "S3"), "SUPPORT"),
            "ALLR": (("R1", "R2", "R3"), "RESISTANCE"),
        }
        if first in level_map:
            date_arg = context.args[1] if len(context.args) == 2 else None
            levels, bias = level_map[first]
            text = fetch_level_data(levels, bias, date_arg)
            await send_in_blocks(text, update)
            log_line(f"USER {user_id} CMD {raw_command} -> SR-SCANNER levels={levels}, date={date_arg}")
            return

    # 2) 5-DAY SLICE: /sr STOCK STRIKE CE|PE [DD-MM]
    if len(context.args) >= 3:
        sym = context.args[0].upper()
        try:
            strike_val = float(context.args[1])
        except Exception:
            pass
        else:
            opt_type = context.args[2].upper()
            if opt_type in ("CE", "PE"):
                ddmm_input = context.args[3] if len(context.args) >= 4 else None

                anchor_trade_date = None
                conn = sqlite3.connect(DB_PATH)
                cur = conn.cursor()

                if sym in ("NIFTY", "BANKNIFTY", "SENSEX", "MIDCPNIFTY"):
                    index_map = {
                        "NIFTY": "NIFTYFO",
                        "BANKNIFTY": "BANKFO",
                        "SENSEX": "SENSEXFO",
                        "MIDCPNIFTY": "MIDFO",
                    }
                    tname = index_map[sym]
                    date_col = "TradDt"
                    sym_col = "TckrSymb"
                else:
                    tname = "STO1"
                    date_col = "Trade_Date"
                    sym_col = "Symbol"

                cur.execute(
                    f"SELECT MAX({date_col}) FROM {tname} WHERE {sym_col} = ?",
                    (sym,)
                )
                latest_row = cur.fetchone()
                latest_td = latest_row[0] if latest_row and latest_row[0] else None

                if latest_td:
                    if ddmm_input:
                        try:
                            year = int(latest_td[:4])
                            d, m = map(int, ddmm_input.split("-"))
                            anchor = f"{year:04d}-{m:02d}-{d:02d}"
                            cur.execute(
                                f"""
                                SELECT MAX({date_col})
                                FROM {tname}
                                WHERE {sym_col} = ?
                                  AND {date_col} <= ?
                                """,
                                (sym, anchor)
                            )
                            row2 = cur.fetchone()
                            anchor_trade_date = row2[0] if row2 and row2[0] else None
                        except Exception:
                            anchor_trade_date = None
                    else:
                        anchor_trade_date = latest_td

                conn.close()

                text, debug = get_5day_option_slice(sym, strike_val, opt_type, anchor_trade_date)

                if text:
                    heading = f"{sym} {int(strike_val)} {opt_type} 5-day slice"
                    if anchor_trade_date:
                        heading += f" up to {anchor_trade_date[8:10]}-{anchor_trade_date[5:7]}"
                    await send_in_blocks(heading + "\n\n" + text, update)
                else:
                    await update.message.reply_text(f"No data: {debug}")

                log_line(
                    f"USER {user_id} CMD {raw_command} -> 5DAY MODE "
                    f"symbol={sym}, strike={strike_val}, type={opt_type}, "
                    f"anchor={anchor_trade_date}"
                )
                return

    # 3) INDEX MODE: /SR NIFTY 26500
    if len(context.args) == 2:
        symbol = context.args[0].upper()
        try:
            strike_val = float(context.args[1])
        except Exception:
            await update.message.reply_text("Usage: /SR <INDEX> <STRIKE>")
            return

        lot_size = INDEX_LOTS_FOR_SR.get(symbol, 1)
        supheading = f"{symbol} Nearest Strikes (lot {lot_size}, strike {int(strike_val)})"

        (
            headers,
            ce_rows, ce_nearest, ce_top3idx_oi, ce_top3_impacts_oi, ce_top3_strikes_oi,
            ce_top3_impact_openint, ce_avg_impact_oi,
            ce_top3idx_cg, ce_top3_impacts_cg, ce_top3_strikes_cg,
            ce_top3_impact_chgoi, ce_avg_impact_cg,
            pe_rows, pe_nearest, pe_top3idx_oi, pe_top3_impacts_oi,
            pe_top3_strikes_oi, pe_top3_impact_openint, pe_avg_impact_oi,
            pe_top3idx_cg, pe_top3_impacts_cg, pe_top3_strikes_cg, pe_top3_impact_chgoi, pe_avg_impact_cg
        ) = prepare_table_data_for_plot(symbol, lot_size, strike_val)

        render_both_tables_stacked(
            headers,
            ce_rows, ce_nearest,
            ce_top3idx_oi, ce_top3_impacts_oi, ce_top3_strikes_oi,
            ce_top3_impact_openint, ce_avg_impact_oi,
            ce_top3idx_cg, ce_top3_impacts_cg, ce_top3_strikes_cg,
            ce_top3_impact_chgoi, ce_avg_impact_cg,
            pe_rows, pe_nearest,
            pe_top3idx_oi, pe_top3_impacts_oi, pe_top3_strikes_oi,
            pe_top3_impact_openint, pe_avg_impact_oi,
            pe_top3idx_cg, pe_top3_impacts_cg, pe_top3_strikes_cg,
            pe_top3_impact_chgoi, pe_avg_impact_cg,
            supheading, "both_tables.png"
        )

        await update.message.reply_photo(open("both_tables.png", "rb"))

        ce_text = table_with_summary(
            headers, ce_rows, ce_nearest,
            ce_top3idx_oi, ce_top3_impacts_oi, ce_top3_strikes_oi,
            ce_top3_impact_openint, ce_avg_impact_oi,
            ce_top3idx_cg, ce_top3_impacts_cg, ce_top3_strikes_cg,
            ce_top3_impact_chgoi, ce_avg_impact_cg
        )
        pe_text = table_with_summary(
            headers, pe_rows, pe_nearest,
            pe_top3idx_oi, pe_top3_impacts_oi, pe_top3_strikes_oi,
            pe_top3_impact_openint, pe_avg_impact_oi,
            pe_top3idx_cg, pe_top3_impacts_cg, pe_top3_strikes_cg,
            pe_top3_impact_chgoi, pe_avg_impact_cg
        )

        await send_in_blocks(f"{supheading}\n\nCALLS (CE):\n" + ce_text, update)
        await send_in_blocks(f"{supheading}\n\nPUTS (PE):\n" + pe_text, update)

        log_line(f"USER {user_id} CMD {raw_command} -> INDEX MODE symbol={symbol}, strike={strike_val}")
        log_line(f"INDEX TEXT CE:\n{ce_text}")
        log_line(f"INDEX TEXT PE:\n{pe_text}")
        return

    # 4) STOCK MODE: /SR INFY
    if len(context.args) == 1:
        ticker = context.args[0].upper()
        layer1_rows = fetch_layer1_rows(ticker)
        if not layer1_rows or len(layer1_rows) < 2:
            await update.message.reply_text("Not enough Layer-1 data found.")
            return

        layer1_ohlc_from_above = format_layer1_ohlc_from_above(layer1_rows)
        layer2, high, low, close, date_str = fetch_layer2_and_prices(ticker)
        pivots = calc_pivot_points(high, low, close) if (high and low and close) else None

        now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        message_parts = [f"***STOCK - {ticker} Analytics ({now_str})***"]
        message_parts.append("——————— LAYER-1 ———————\n" + layer1_ohlc_from_above)
        if layer2:
            message_parts.append(layer2)
        message_parts.append(format_layer3(pivots, date_str))

        top3_text, l4_debug = get_top3_from_sto1(ticker)
        if top3_text:
            message_parts.append("——————— LAYER-4 (Options Snapshot) ———————\n" + top3_text)
        else:
            if l4_debug:
                message_parts.append(
                    "——————— LAYER-4 (Options Snapshot) ———————\n"
                    f"(No data for Layer-4: {l4_debug})"
                )

        reply = "\n\n".join(message_parts)
        await send_in_blocks(reply, update)

        chart_file = build_layer1_plotly_chart(layer1_rows)
        await update.message.reply_photo(open(chart_file, "rb"),
                                         caption=f"{ticker} Support/Resist & OHLC")

        log_line(f"USER {user_id} CMD {raw_command} -> STOCK MODE ticker={ticker}")
        log_line("STOCK TEXT:\n" + reply)
        return

    await update.message.reply_text(
        "Usage:\n"
        "/sr S1 | /sr ALLS | /sr R2 08-12\n"
        "/sr <INDEX> <STRIKE>\n"
        "/sr <STOCK>\n"
        "/sr <STOCK> <STRIKE> CE|PE [DD-MM]\n"
        "/sr count [DD-MM]"
    )

# =====================================================
# BASIC COMMANDS & MAIN
# =====================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Combined SR + OI bot is running and responding!")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "Available commands:\n\n"
        "/start\n"
        "  - Check that the bot is running.\n\n"
        "/help\n"
        "  - Show this help message.\n\n"
        "/srsr <LEVEL> [DD-MM]\n"
        "  - Old SR scanner from PCR2 (text report).\n"
        "  - Examples:\n"
        "    /srsr S1\n"
        "    /srsr ALLS\n"
        "    /srsr R2 08-12\n\n"
        "/sr S1 | /sr ALLS | /sr R2 08-12\n"
        "  - Same SR scanner shortcuts on /sr.\n\n"
        "/sr <INDEX> <STRIKE>\n"
        "  - Index options view around a strike (single day).\n"
        "  - INDEX must be NIFTY, BANKNIFTY, SENSEX or MIDCPNIFTY.\n\n"
        "/sr <STOCK>\n"
        "  - Multi-layer stock analytics (Layers 1–4) + chart.\n\n"
        "/sr <SYMBOL> <STRIKE> CE|PE [DD-MM]\n"
        "  - 5 trading days slice for one symbol/strike and option type.\n"
        "  - If DD-MM is given: uses up to 5 trading days on/before that date (current year).\n"
        "  - If DD-MM is omitted: uses last 5 trading days.\n\n"
        "/sr count [DD-MM]\n"
        "  - Show last 4 trade dates from 'count' joined with STO1 (OI/COI).\n"
        "  - Without date: last 4 dates.\n"
        "  - With DD-MM: that date plus previous 3 trade dates.\n\n"
        "/niftyfo, /niftym, /sensexfo, /bankfo, /midfo\n"
        "  - Index OI/COI structure + unwind images for latest date.\n"
    )
    await update.message.reply_text(text)

async def main():
    print("Combined SR + OI bot running...")
    print(f"Using DB_PATH: {DB_PATH}")
    print(f"Logging to: {LOG_PATH}")

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("srsr", srscan_command))
    app.add_handler(CommandHandler("sr", sr_command))

    app.add_handler(CommandHandler("niftyfo", niftyfo))
    app.add_handler(CommandHandler("niftym", niftym))
    app.add_handler(CommandHandler("sensexfo", sensexfo))
    app.add_handler(CommandHandler("bankfo", bankfo))
    app.add_handler(CommandHandler("midfo", midfo))

    await app.run_polling()

if __name__ == "__main__":
    asyncio.run(main())
