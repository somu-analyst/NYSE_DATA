from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
import sqlite3
import nest_asyncio
import asyncio
import plotly.graph_objects as go
from datetime import datetime
import numpy as np
from PIL import Image, ImageDraw, ImageFont


DB_PATH = r'C:\Users\srini\Options_chain_data\oi_data.db'
BOT_TOKEN = '***REMOVED_TELEGRAM_TOKEN***'  # <-- paste valid token from BotFather

nest_asyncio.apply()

MAX_LEN = 3700
MAX_COL_WIDTH = 18

# HARD LIMITS TO AVOID Photo_invalid_dimensions
MAX_IMG_W = 1500
MAX_IMG_H = 1500
MAX_ROWS_PER_SIDE = 25  # limit visible rows for CE and PE to keep image height reasonable


async def send_in_blocks(text, update):
    blocks = []
    start = 0
    while start < len(text):
        end = start + MAX_LEN
        chunk = text[start:end]
        if "<" in chunk and ">" not in chunk.split("<")[-1]:
            last_open = chunk.rfind("<")
            chunk = chunk[:last_open]
            end = start + len(chunk)
        blocks.append(chunk)
        start = end

    total = len(blocks)
    for i, block in enumerate(blocks, start=1):
        numbered = f"(Part {i}/{total})\n\n{block}"
        await update.message.reply_text(f"<pre>{numbered}</pre>", parse_mode="HTML")


def int_comma(x):
    try:
        return '{:,}'.format(int(round(float(x))))
    except:
        return 'NA'


def int_clean(x):
    try:
        return int(round(float(x)))
    except:
        return 0


def get_font(fontsize=17):
    try:
        return ImageFont.truetype("calibri.ttf", fontsize)
    except:
        return ImageFont.truetype("arial.ttf", fontsize)


# ----------- MACRO BLOCK: INDEX + STOCKS WITH IMAGE -------------------


def format_summary(top3_values, top3_strikes, impacts, avg_impact, label, avg_label):
    vals = [f"{v:,}({s})" for v, s in zip(top3_values, top3_strikes)]
    imp_string = ', '.join(vals) if vals else "NA"
    avg_s = f"{avg_label}: {avg_impact:,}" if avg_impact else f"{avg_label}: NA"
    return f"{label}: {imp_string}\n{avg_s}"


def render_one_table_image(
    headers, rows, nearest_idx,
    top3_openint, imp_openint, strikes_openint, impact_openint, avg_impact_openint,
    top3_chgoi, imp_chgoi, strikes_chgoi, impact_chgoi, avg_impact_chgoi,
    heading, filename
):
    rows = rows[:MAX_ROWS_PER_SIDE]
    ncols = len(headers)
    nrows = len(rows)
    font = get_font(13)

    def cell_text_width(text, font_obj):
        bbox = font_obj.getbbox(str(text))
        return bbox[2] - bbox[0]

    def cell_text_height(font_obj):
        bbox = font_obj.getbbox("X")
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

    img_w = min(MAX_IMG_W, col_sum + 32)
    img_h = min(MAX_IMG_H, pad_top + table_h + summary_h + 20)
    img_w = max(300, img_w)
    img_h = max(300, img_h)

    img = Image.new('RGB', (img_w, img_h), 'white')
    draw = ImageDraw.Draw(img)

    blue = (21, 99, 199)
    hf = get_font(24)
    bbox = draw.textbbox((0, 0), heading, font=hf)
    w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    draw.text(((img_w - w) // 2, 12), heading, font=hf, fill=blue)

    max_table_height = img_h - pad_top - summary_h - 20
    max_rows_fit = max(1, min(nrows, max_table_height // row_h))
    rows_to_draw = rows[:max_rows_fit]

    y = pad_top
    for c, h_ in enumerate(headers):
        x = sum(cell_widths[:c]) + 8
        if x >= img_w:
            break
        x2 = min(x + cell_widths[c], img_w - 1)
        draw.rectangle([x, y, x2, y + row_h], fill='#e6f5ff')
        draw.rectangle([x, y, x2, y + row_h], outline="black", width=2)
        draw.text((x + 8, y + 9), str(h_)[:MAX_COL_WIDTH], font=font, fill='black')
    y += row_h

    for r, vals in enumerate(rows_to_draw):
        is_nearest = r in nearest_idx
        for c, val in enumerate(vals):
            x = sum(cell_widths[:c]) + 8
            if x >= img_w:
                break
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

            x2 = min(x + cell_widths[c], img_w - 1)
            draw.rectangle([x, y, x2, y + row_h], fill=cell_col)
            draw.rectangle([x, y, x2, y + row_h], outline="black", width=1)
            vtxt = str(val) if len(str(val)) < MAX_COL_WIDTH else str(val)[:MAX_COL_WIDTH]
            fontrow = get_font(18) if bold or is_nearest else font
            draw.text((x + 8, y + 9), vtxt, font=fontrow, fill=fill)
        y += row_h

    summary_y = min(pad_top + table_h + 17, img_h - 230)
    sumf = get_font(18)
    bigf = get_font(28)

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

    draw.text((38, summary_y + 120), "Top 3 MONEYCOI:", font=sumf, fill='#2451aa')
    draw.text(
        (56, summary_y + 152),
        ", ".join([f"{v:,}({s})" for v, s in zip(imp_chgoi, strikes_chgoi)]),
        font=sumf, fill='#2451aa'
    )
    draw.text(
        (38, summary_y + 188),
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

    img_w = min(MAX_IMG_W, max(img_ce.width, img_pe.width))
    img_h = min(MAX_IMG_H, img_ce.height + img_pe.height + 70)
    img_w = max(300, img_w)
    img_h = max(300, img_h)

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
        "BANKNIFTY": "bankniftyfo",
        "FINNIFTY": "finniftyfo",
        "SENSEX": "sensexfo",
    }
    stock_table = "STO1"

    symbol_upper = symbol.upper()
    is_index = symbol_upper in index_tables

    if is_index:
        table_name = index_tables[symbol_upper]
        symbol_col = "TckrSymb"
        opttype_col = "OptnTp"
        oi_col = "OpnIntrst"
        chg_oi_col = "ChngInOpnIntrst"
        date_col = "TradDt"
        strike_col = "StrkPric"
        open_col = "OpnPric"
        high_col = "HghPric"
        low_col = "LwPric"
        close_col = "ClsPric"
        prevclose_col = "PrvsClsgPric"

        cursor.execute(f"""
            SELECT DISTINCT {strike_col}
            FROM {table_name}
            WHERE {symbol_col} = ? AND {strike_col} IS NOT NULL
        """, (symbol_upper,))
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
            WHERE {symbol_col} = ? AND {strike_col} IN ({phs})
            ORDER BY {strike_col} ASC, {opttype_col}
        """
        cursor.execute(query, [symbol_upper] + strikes_sel)

    else:
        table_name = stock_table
        symbol_col = "Symbol"
        opttype_col = "Option_Type"
        oi_col = "Open_Interest"
        chg_oi_col = "Change_in_OI"
        date_col = "Trade_Date"
        strike_col = "StrkPric"
        open_col = "OpnPric"
        high_col = "HghPric"
        low_col = "LwPric"
        close_col = "ClsPric"
        prevclose_col = "PrvsClsgPric"

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

    for idx, vals in enumerate(rows[:MAX_ROWS_PER_SIDE]):
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


async def sr_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if len(context.args) != 2:
            await update.message.reply_text(
                "Usage: /SR <SYMBOL> <STRIKE>\nExample: /SR NIFTY 26500 or /SR WIPRO 320"
            )
            return

        symbol = context.args[0].upper()
        try:
            strike_val = float(context.args[1])
        except:
            await update.message.reply_text("Strike must be a number. Usage: /SR <SYMBOL> <STRIKE>")
            return

        lot_sizes = {'NIFTY': 75, 'BANKNIFTY': 35, 'FINNIFTY': 40, 'SENSEX': 20}
        lot_size = lot_sizes.get(symbol, 1)

        supheading = f"{symbol} Nearest Strikes (lot {lot_size}, strike {int(strike_val)})"

        (
            headers,
            ce_rows, ce_nearest, ce_top3idx_oi, ce_top3_impacts_oi, ce_top3_strikes_oi,
            ce_top3_impact_openint, ce_avg_impact_oi,
            ce_top3idx_cg, ce_top3_impacts_cg, ce_top3_strikes_cg,
            ce_top3_impact_chgoi, ce_avg_impact_cg,
            pe_rows, pe_nearest, pe_top3idx_oi, pe_top3_impacts_oi,
            pe_top3_strikes_oi, pe_top3_impact_openint, pe_avg_impact_oi,
            pe_top3idx_cg, pe_top3_impacts_cg, pe_top3_strikes_cg,
            pe_top3_impact_chgoi, pe_avg_impact_cg
        ) = prepare_table_data_for_plot(symbol, lot_size, strike_val)

        if not ce_rows and not pe_rows:
            await update.message.reply_text("No option chain data found for this symbol/strike.")
            return

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

        await send_in_blocks(
            f"{supheading}\n\nCALLS (CE):\n" +
            table_with_summary(
                headers, ce_rows, ce_nearest,
                ce_top3idx_oi, ce_top3_impacts_oi, ce_top3_strikes_oi,
                ce_top3_impact_openint, ce_avg_impact_oi,
                ce_top3idx_cg, ce_top3_impacts_cg, ce_top3_strikes_cg,
                ce_top3_impact_chgoi, ce_avg_impact_cg
            ),
            update
        )

        await send_in_blocks(
            f"{supheading}\n\nPUTS (PE):\n" +
            table_with_summary(
                headers, pe_rows, pe_nearest,
                pe_top3idx_oi, pe_top3_impacts_oi, pe_top3_strikes_oi,
                pe_top3_impact_openint, pe_avg_impact_oi,
                pe_top3idx_cg, pe_top3_impacts_cg, pe_top3_strikes_cg,
                pe_top3_impact_chgoi, pe_avg_impact_cg
            ),
            update
        )

    except Exception as e:
        await update.message.reply_text(f"Error: {e}")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Bot is running and responding!")


async def main():
    print("Bot is running and responding!")
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("sr", sr_command))
    await app.run_polling()


if __name__ == "__main__":
    asyncio.run(main())
