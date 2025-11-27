from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
import sqlite3
import nest_asyncio
import asyncio
import plotly.graph_objects as go
from datetime import datetime
import numpy as np
from PIL import Image, ImageDraw, ImageFont

DB_PATH = r'C:\Users\User\Desktop\ZERODHA\WORKING\\oi_data.db'
BOT_TOKEN = '8018716820:AAEMAtRy6D0B0xt7SJgJB-bj7VF07ld4aVA'   # ----> use your real token

nest_asyncio.apply()
MAX_LEN = 3700
MAX_COL_WIDTH = 18

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
    try: return '{:,}'.format(int(round(float(x))))
    except: return 'NA'
def int_clean(x):
    try: return int(round(float(x)))
    except: return 0
def get_font(fontsize=17):
    try: return ImageFont.truetype("calibri.ttf", fontsize)
    except: return ImageFont.truetype("arial.ttf", fontsize)

# ----------- MACRO BLOCK: NIFTY/BANKNIFTY WITH IMAGE -------------------

def format_summary(top3_values, top3_strikes, impacts, avg_impact, label, avg_label):
    vals = [f"{v:,}({s})" for v, s in zip(top3_values, top3_strikes)]
    imp_string = ', '.join(vals) if vals else "NA"
    avg_s = f"{avg_label}: {avg_impact:,}" if avg_impact else f"{avg_label}: NA"
    return f"{label}: {imp_string}\n{avg_s}"

def render_one_table_image(headers, rows, nearest_idx,
                          top3_openint, imp_openint, strikes_openint, impact_openint, avg_impact_openint,
                          top3_chgoi, imp_chgoi, strikes_chgoi, impact_chgoi, avg_impact_chgoi,
                          heading, filename):
    ncols = len(headers)
    nrows = len(rows)
    font = get_font(13)
    def cell_text_width(text, font):
        bbox = font.getbbox(str(text))
        return bbox[2] - bbox[0]
    def cell_text_height(font):
        bbox = font.getbbox("X")
        return bbox[3] - bbox[1]
    cell_widths = [max([cell_text_width(headers[c], font)] +
                       [cell_text_width(r[c], font) for r in rows]) + 18 for c in range(ncols)]
    col_sum = sum(cell_widths)
    row_h = cell_text_height(font) + 20
    pad_top = 50
    summary_h = 280
    table_h = row_h * (nrows + 1)
    img_w = min(2100, col_sum+32)
    img_h = pad_top + table_h + summary_h + 20
    img = Image.new('RGB', (img_w, img_h), 'white')
    draw = ImageDraw.Draw(img)
    blue = (21, 99, 199)
    hf = get_font(24)
    bbox = draw.textbbox((0,0), heading, font=hf)
    w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    draw.text(((img_w-w)//2, 12), heading, font=hf, fill=blue)
    y = pad_top
    for c, h in enumerate(headers):
        x = sum(cell_widths[:c]) + 8
        draw.rectangle([x, y, x+cell_widths[c], y+row_h], fill='#e6f5ff')
        draw.rectangle([x, y, x+cell_widths[c], y+row_h], outline="black", width=2)
        draw.text((x+8, y+9), str(h)[:MAX_COL_WIDTH], font=font, fill='black')
    y += row_h
    for r, vals in enumerate(rows):
        is_nearest = r in nearest_idx
        for c, val in enumerate(vals):
            x = sum(cell_widths[:c]) + 8
            cell_col = '#fff'
            fill = 'black'
            bold = False
            if r in top3_openint and headers[c] == "OpenInt*Close":
                cell_col = '#ffe6e8'
                fill = 'red'
                bold = True
            if r in top3_chgoi and headers[c] == "ChgOI*Close":
                cell_col = '#e6edff'
                fill = 'red'
                bold = True
            draw.rectangle([x, y, x+cell_widths[c], y+row_h], fill=cell_col)
            draw.rectangle([x, y, x+cell_widths[c], y+row_h], outline="black", width=1)
            vtxt = str(val) if len(str(val))<MAX_COL_WIDTH else str(val)[:MAX_COL_WIDTH]
            fontrow = get_font(18) if bold or is_nearest else font
            draw.text((x+8, y+9), vtxt, font=fontrow, fill=fill)
        y += row_h
    summary_y = pad_top + table_h + 17
    sumf = get_font(18)
    bigf = get_font(36)
    # Correct average: use corresponding impact value of the top-3, not the column itself
    draw.text((38, summary_y), "Top 3 OpenInt*Close:", font=sumf, fill='#1543b0')
    draw.text((56, summary_y + 32), ", ".join([f"{v:,}({s})" for v, s in zip(imp_openint, strikes_openint)]), font=sumf, fill='#1543b0')
    draw.text((38, summary_y + 68), f"AVG IMPACT (OpenInt): {avg_impact_openint:,}", font=bigf, fill='#1749e3')
    draw.text((38, summary_y + 135), "Top 3 ChgOI*Close:", font=sumf, fill='#2451aa')
    draw.text((56, summary_y + 167), ", ".join([f"{v:,}({s})" for v, s in zip(imp_chgoi, strikes_chgoi)]), font=sumf, fill='#2451aa')
    draw.text((38, summary_y + 203), f"AVG IMPACT (ChgOI): {avg_impact_chgoi:,}", font=bigf, fill='#a10ae3')
    img.save(filename)

def render_both_tables_stacked(headers,
        ce_rows, ce_nearest, ce_top3idx_oi, ce_top3_impacts_oi, ce_top3_strikes_oi, ce_top3_impact_openint, ce_avg_impact_oi,
        ce_top3idx_cg, ce_top3_impacts_cg, ce_top3_strikes_cg, ce_top3_impact_chgoi, ce_avg_impact_cg,
        pe_rows, pe_nearest, pe_top3idx_oi, pe_top3_impacts_oi, pe_top3_strikes_oi, pe_top3_impact_openint, pe_avg_impact_oi,
        pe_top3idx_cg, pe_top3_impacts_cg, pe_top3_strikes_cg, pe_top3_impact_chgoi, pe_avg_impact_cg,
        supheading, filename):
    render_one_table_image(headers, ce_rows, ce_nearest, ce_top3idx_oi, ce_top3_impacts_oi, ce_top3_strikes_oi, ce_top3_impact_openint, ce_avg_impact_oi,
                          ce_top3idx_cg, ce_top3_impacts_cg, ce_top3_strikes_cg, ce_top3_impact_chgoi, ce_avg_impact_cg, "CALLS", "ce_table.png")
    render_one_table_image(headers, pe_rows, pe_nearest, pe_top3idx_oi, pe_top3_impacts_oi, pe_top3_strikes_oi, pe_top3_impact_openint, pe_avg_impact_oi,
                          pe_top3idx_cg, pe_top3_impacts_cg, pe_top3_strikes_cg, pe_top3_impact_chgoi, pe_avg_impact_cg, "PUTS", "pe_table.png")
    img_ce = Image.open("ce_table.png")
    img_pe = Image.open("pe_table.png")
    img_w = max(img_ce.width, img_pe.width)
    img_h = img_ce.height + img_pe.height + 70
    combo = Image.new('RGB', (img_w, img_h), 'white')
    draw = ImageDraw.Draw(combo)
    font = get_font(28)
    bbox = draw.textbbox((0,0), supheading, font=font)
    w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    draw.text(((img_w-w)//2, 12), supheading, fill='#153099', font=font)
    combo.paste(img_ce, (0, 47))
    combo.paste(img_pe, (0, 47+img_ce.height))
    combo.save(filename)

def prepare_table_data_for_plot(symbol, lot_size, target_strike):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(f"""
        SELECT DISTINCT StrkPric FROM {symbol.lower()}fo
        WHERE TckrSymb = ?
        AND StrkPric IS NOT NULL
    """, (symbol.upper(),))
    strikes = sorted(float(row[0]) for row in cursor.fetchall() if row[0] is not None)
    closest = min(strikes, key=lambda x: abs(x - target_strike))
    closest_int = int(round(closest))
    idx = strikes.index(closest)
    start = max(0, idx-6)
    end = min(len(strikes), idx+7)
    strikes_sel = strikes[start:end]
    phs = ",".join("?"*len(strikes_sel))
    query = f"""
        SELECT TradDt, StrkPric, OptnTp, OpnPric, HghPric, LwPric, ClsPric, PrvsClsgPric, OpnIntrst, ChngInOpnIntrst
        FROM {symbol.lower()}fo
        WHERE TckrSymb=? AND StrkPric IN ({phs})
        ORDER BY StrkPric ASC, OptnTp
    """
    cursor.execute(query, [symbol.upper()]+strikes_sel)
    rows = cursor.fetchall()
    conn.close()
    headers = ["Date","Strike","Type","Open","High","Low","Close","PrevClose","OpenInt","ChgOI","Impact","ChgOI*Close","OpenInt*Close"]
    def prep_rows(option_type, sort_desc):
        filtered = [r for r in rows if r[2] == option_type]
        filtered_sorted = sorted(filtered, key=lambda r: float(r[1]), reverse=sort_desc)
        data_rows, nearest_idx = [], []
        openintclose_abs, chgoiclose_abs = [], []
        for i, row in enumerate(filtered_sorted):
            trad_dt_fmt = datetime.strptime(row[0], '%Y-%m-%d').strftime('%d-%m') if row[0] else row[0]
            strike_val = row[1]; strike_int = int(round(strike_val))
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
            impact_val = (int(round(strike_val + close_val)) if typ == 'CE'
                          else int(round(strike_val - close_val)) if typ == 'PE' else 0)
            impact = int_comma(impact_val)
            chgoi_close = chg_oi_val * close_val
            openint_close = oi_val * close_val
            vals = [trad_dt_fmt, int_comma(strike_val), typ, open_, high, low, close,
                    prev, oi, chg_oi, impact, int_comma(chgoi_close), int_comma(openint_close)]
            data_rows.append(vals)
            if strike_int == closest_int:
                nearest_idx.append(i)
            openintclose_abs.append((abs(openint_close), strike_int, int(impact_val)))
            chgoiclose_abs.append((abs(chgoi_close), strike_int, int(impact_val)))
        # get top3 and matching impacts for summary (and correct average)
        top3_openint = sorted(openintclose_abs, reverse=True)[:3]
        imp_openint = [x[0] for x in top3_openint]
        strikes_openint_v = [x[1] for x in top3_openint]
        impact_openint = [x[2] for x in top3_openint]
        avg_impact_openint = int(round(np.mean(impact_openint))) if impact_openint else 0

        top3_chgoi = sorted(chgoiclose_abs, reverse=True)[:3]
        imp_chgoi = [x[0] for x in top3_chgoi]
        strikes_chgoi_v = [x[1] for x in top3_chgoi]
        impact_chgoi = [x[2] for x in top3_chgoi]
        avg_impact_chgoi = int(round(np.mean(impact_chgoi))) if impact_chgoi else 0

        idxs_openint = [i for i, row in enumerate(filtered_sorted)
                        if int(round(float(row[1]))) in strikes_openint_v]
        idxs_chgoi = [i for i, row in enumerate(filtered_sorted)
                        if int(round(float(row[1]))) in strikes_chgoi_v]
        return data_rows, nearest_idx, idxs_openint, imp_openint, strikes_openint_v, impact_openint, avg_impact_openint, \
               idxs_chgoi, imp_chgoi, strikes_chgoi_v, impact_chgoi, avg_impact_chgoi
    ce = prep_rows('CE', sort_desc=False)
    pe = prep_rows('PE', sort_desc=True)
    return headers, *ce, *pe

def table_with_summary(headers, rows, nearest_idx,
                      top3idx_oi, imp_openint, strikes_openint, impact_openint, avg_impact_openint,
                      top3idx_cg, imp_chgoi, strikes_chgoi, impact_chgoi, avg_impact_chgoi):
    show_width = [min(MAX_COL_WIDTH, max(len(str(row[i])) for row in [headers]+rows)) for i in range(len(headers))]
    header_line = " | ".join(headers[i].ljust(show_width[i]) for i in range(len(headers)))
    lines = [header_line]
    for idx, vals in enumerate(rows):
        pre = "→" if idx in nearest_idx else " "
        line = " | ".join(vals[i].ljust(show_width[i]) for i in range(len(headers)))
        lines.append(pre+line)
    s_oi = "Top 3 OpenInt*Close: " + ", ".join([f"{v:,}({s})" for v, s in zip(imp_openint, strikes_openint)])
    s_oi_avg = f"AVG Impact (OpenInt): {avg_impact_openint:,}"
    s_cg = "Top 3 ChgOI*Close: " + ", ".join([f"{v:,}({s})" for v, s in zip(imp_chgoi, strikes_chgoi)])
    s_cg_avg = f"AVG Impact (ChgOI): {avg_impact_chgoi:,}"
    lines += ["", s_oi, s_oi_avg, "", s_cg, s_cg_avg]
    return "\n".join(lines)

# ----------- STOCK LAYER ANALYTICS + CHART -----------

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
        "Date","S1","S12","S2","S22","S3","S32",
        "R1","R12","R2","R22","R3","R32",
        "Open","HIGH","LOW","Close"
    ]
    table_rows = []
    for i, row in enumerate(rows):
        date = datetime.strptime(row[16], '%Y-%m-%d').strftime('%d-%m')
        rvals = [f"{row[j]:.1f}" if row[j] is not None else "NA" for j in range(12)]
        if i > 0:
            prev = rows[i-1]
            ohlc = [f"{prev[j]:.1f}" if prev[j] is not None else "NA" for j in range(12,16)]
        else:
            ohlc = ["", "", "", ""]
        table_rows.append([date]+rvals+ohlc)
    col_widths = [max(len(headers[i]), max(len(r[i]) for r in table_rows)) for i in range(len(headers))]
    output = " | ".join(headers[i].ljust(col_widths[i]) for i in range(len(headers)))+" |\n"
    for r in table_rows:
        output += " | ".join(r[i].ljust(col_widths[i]) for i in range(len(headers)))+" |\n"
    return output

def fetch_layer2_and_prices(ticker):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT TradDt FROM CMS_Analysis
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
            FROM CMS_Analysis WHERE TckrSymb = ? AND TradDt = ?
            ORDER BY TradDt DESC LIMIT 1
        """, (ticker, d))
        row = cursor.fetchone()
        opn, hgh, lw, cls, vol, chg = row if row else (None,)*6
        if idx == 0 and row:
            high, low, close = hgh, lw, cls
        cursor.execute("""
            SELECT PCR FROM PCR2 WHERE UPPER(TckrSymb) = ? AND TradDt = ?
            ORDER BY TradDt DESC LIMIT 1
        """, (ticker.upper(), d))
        pcr_row = cursor.fetchone()
        pcr = f"{pcr_row[0]:.2f}" if pcr_row and pcr_row[0] is not None else "NA"
        cursor.execute("""
            SELECT Open_Interest, Change_in_OI FROM STF1
            WHERE UPPER(Symbol) = ? AND Trade_Date = ?
            ORDER BY Trade_Date DESC LIMIT 1
        """, (ticker.upper(), d))
        stf_row = cursor.fetchone()
        foi = "{:,}".format(stf_row[0]) if stf_row and stf_row[0] is not None else "NA"
        fcoi = str(stf_row[1]) if stf_row and stf_row[1] is not None else "NA"
        previous_10 = dates[idx+1:idx+11]
        previous_20 = dates[idx+1:idx+21]
        if len(previous_10) < 10 or len(previous_20) < 20 or vol is None:
            vol10 = vol20 = "NA"
        else:
            cursor.execute(f"""
                SELECT TtlTradgVol FROM CMS_Analysis
                WHERE TckrSymb = ? AND TradDt IN ({','.join(['?']*len(previous_10))})
            """, (ticker, *previous_10))
            vols10 = [r[0] for r in cursor.fetchall()]
            avg10 = sum(vols10)/len(vols10) if vols10 else None
            vol10 = f"{(vol/avg10):.2f}" if (vol and avg10) else "NA"
            cursor.execute(f"""
                SELECT TtlTradgVol FROM CMS_Analysis
                WHERE TckrSymb = ? AND TradDt IN ({','.join(['?']*len(previous_20))})
            """, (ticker, *previous_20))
            vols20 = [r[0] for r in cursor.fetchall()]
            avg20 = sum(vols20)/len(vols20) if vols20 else None
            vol20 = f"{(vol/avg20):.2f}" if (vol and avg20) else "NA"
        opn_s = f"{opn:.1f}" if opn is not None else "NA"
        hgh_s = f"{hgh:.1f}" if hgh is not None else "NA"
        lw_s  = f"{lw:.1f}"  if lw  is not None else "NA"
        cls_s = f"{cls:.1f}" if cls is not None else "NA"
        chg_s = f"{chg:.2f}%" if chg is not None else "NA"
        table_rows.append([short_date, opn_s, hgh_s, lw_s, cls_s,
            pcr, foi, fcoi, vol10, vol20, chg_s])
    conn.close()
    headers = ["Dates", "OpnPric", "HghPric", "LwPric", "ClsPric",
               "PCR", "FOI", "FCOI", "Volume(10d)", "Volume(20d)", "Price %Chg"]
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
        f"——————— LAYER-2 (Latest: {latest_date}) ———————\n"
        + "\n".join(layer2_rows)
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
        "S3": round(S3, 2), "S2": round(S2, 2), "S1": round(S1, 2),
        "BC": round(BC, 2), "P": round(P, 2), "TC": round(TC, 2),
        "R1": round(R1, 2), "R2": round(R2, 2), "R3": round(R3, 2)
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
    value_line  = "| " + " | ".join(values[i].ljust(col_widths[i]) for i in range(len(values))) + " |"
    return (
        f"——————— LAYER-3 (Pivot/CPR from {date_str}) ———————\n"
        + header_line + "\n"
        + value_line + "\n"
    )

def build_layer1_plotly_chart(rows):
    dates = [datetime.strptime(row[16], '%Y-%m-%d').strftime("%dth %b'%y") for row in rows]
    current_price = rows[0][15]
    def val(idx): return [row[idx] for row in rows]
    S1,S12,S2,S22,S3,S32 = val(0),val(1),val(2),val(3),val(4),val(5)
    R1,R12,R2,R22,R3,R32 = val(6),val(7),val(8),val(9),val(10),val(11)
    Open,High,Low,Close = val(12),val(13),val(14),val(15)
    gaps = lambda arr: [x - current_price for x in arr]
    fig = go.Figure()
    fig.add_trace(go.Bar(x=dates, y=gaps(S1), name='S1', marker_color='#2E8B57', offsetgroup=0, base=current_price, showlegend=True))
    fig.add_trace(go.Bar(x=dates, y=gaps(R1), name='R1', marker_color='#DB4545', offsetgroup=0, base=current_price, showlegend=True))
    fig.add_trace(go.Bar(x=dates, y=gaps(S12), name='S12', marker_color='#2E8B57', offsetgroup=1, base=current_price, showlegend=False))
    fig.add_trace(go.Bar(x=dates, y=gaps(R12), name='R12', marker_color='#DB4545', offsetgroup=1, base=current_price, showlegend=False))
    fig.add_trace(go.Bar(x=dates, y=gaps(S2), name='S2', marker_color='#2E8B57', offsetgroup=2, base=current_price, showlegend=False))
    fig.add_trace(go.Bar(x=dates, y=gaps(R2), name='R2', marker_color='#DB4545', offsetgroup=2, base=current_price, showlegend=False))
    fig.add_trace(go.Bar(x=dates, y=gaps(S22), name='S22', marker_color='#2E8B57', offsetgroup=3, base=current_price, showlegend=False))
    fig.add_trace(go.Bar(x=dates, y=gaps(R22), name='R22', marker_color='#DB4545', offsetgroup=3, base=current_price, showlegend=False))
    fig.add_trace(go.Bar(x=dates, y=gaps(S3), name='S3', marker_color='#2E8B57', offsetgroup=4, base=current_price, showlegend=False))
    fig.add_trace(go.Bar(x=dates, y=gaps(R3), name='R3', marker_color='#DB4545', offsetgroup=4, base=current_price, showlegend=False))
    fig.add_trace(go.Bar(x=dates, y=gaps(S32), name='S32', marker_color='#2E8B57', offsetgroup=5, base=current_price, showlegend=False))
    fig.add_trace(go.Bar(x=dates, y=gaps(R32), name='R32', marker_color='#DB4545', offsetgroup=5, base=current_price, showlegend=False))
    fig.add_trace(go.Scatter(x=dates, y=Open, mode='lines', name='Open', line=dict(color='#1FB8CD', width=2)))
    fig.add_trace(go.Scatter(x=dates, y=High, mode='lines', name='High', line=dict(color='#D2BA4C', width=2)))
    fig.add_trace(go.Scatter(x=dates, y=Low, mode='lines', name='Low', line=dict(color='#5D878F', width=2)))
    fig.add_trace(go.Scatter(x=dates, y=Close, mode='lines', name='Close', line=dict(color='#B4413C', width=2)))
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

# ----------- HANDLER ---

async def sr_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Macro index functionality
    if len(context.args) == 2:
        symbol = context.args[0].upper()
        try: strike_val = float(context.args[1])
        except:
            await update.message.reply_text(f"Usage: /SR <INDEX> <STRIKE>")
            return
        lot_sizes = {'NIFTY': 75, 'BANKNIFTY': 35, 'SENSEX': 20}
        lot_size = lot_sizes.get(symbol, 1)
        supheading = f"{symbol} [finance:Nifty 50] Nearest Strikes (lot {lot_size}, strike {int(strike_val)})"
        (
            headers,
            ce_rows, ce_nearest, ce_top3idx_oi, ce_top3_impacts_oi, ce_top3_strikes_oi, ce_top3_impact_openint, ce_avg_impact_oi,
            ce_top3idx_cg, ce_top3_impacts_cg, ce_top3_strikes_cg, ce_top3_impact_chgoi, ce_avg_impact_cg,
            pe_rows, pe_nearest, pe_top3idx_oi, pe_top3_impacts_oi, pe_top3_strikes_oi, pe_top3_impact_openint, pe_avg_impact_oi,
            pe_top3idx_cg, pe_top3_impacts_cg, pe_top3_strikes_cg, pe_top3_impact_chgoi, pe_avg_impact_cg
        ) = prepare_table_data_for_plot(symbol, lot_size, strike_val)
        render_both_tables_stacked(
            headers,
            ce_rows, ce_nearest, ce_top3idx_oi, ce_top3_impacts_oi, ce_top3_strikes_oi, ce_top3_impact_openint, ce_avg_impact_oi,
            ce_top3idx_cg, ce_top3_impacts_cg, ce_top3_strikes_cg, ce_top3_impact_chgoi, ce_avg_impact_cg,
            pe_rows, pe_nearest, pe_top3idx_oi, pe_top3_impacts_oi, pe_top3_strikes_oi, pe_top3_impact_openint, pe_avg_impact_oi,
            pe_top3idx_cg, pe_top3_impacts_cg, pe_top3_strikes_cg, pe_top3_impact_chgoi, pe_avg_impact_cg,
            supheading, "both_tables.png"
        )
        await update.message.reply_photo(open("both_tables.png", "rb"))
        await send_in_blocks(f"{supheading}\n\nCALLS (CE):\n{table_with_summary(headers, ce_rows, ce_nearest, ce_top3idx_oi, ce_top3_impacts_oi, ce_top3_strikes_oi, ce_top3_impact_openint, ce_avg_impact_oi, ce_top3idx_cg, ce_top3_impacts_cg, ce_top3_strikes_cg, ce_top3_impact_chgoi, ce_avg_impact_cg)}", update)
        await send_in_blocks(f"{supheading}\n\nPUTS (PE):\n{table_with_summary(headers, pe_rows, pe_nearest, pe_top3idx_oi, pe_top3_impacts_oi, pe_top3_strikes_oi, pe_top3_impact_openint, pe_avg_impact_oi, pe_top3idx_cg, pe_top3_impacts_cg, pe_top3_strikes_cg, pe_top3_impact_chgoi, pe_avg_impact_cg)}", update)
        return
    # Individual stock logic
    elif len(context.args) == 1:
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
        reply = "\n\n".join(message_parts)
        await send_in_blocks(reply, update)
        chart_file = build_layer1_plotly_chart(layer1_rows)
        await update.message.reply_photo(open(chart_file, "rb"), caption=f"{ticker} Support/Resist & OHLC")
    else:
        await update.message.reply_text("Use /SR <TICKER> or /SR NIFTY <STRIKE>")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Bot is running and responding!")

async def main():
    print("Bot is running and responding!")  # Console message
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("sr", sr_command))
    await app.run_polling()

if __name__ == "__main__":
    asyncio.run(main())
