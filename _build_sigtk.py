
# Helper script to build and insert signal_ticker_detail function
import sys
import ast

BS_N = b'\x5c\x6e'  # literal \n (backslash + n) in file = \n escape

# Emoji as UTF-8 bytes
E_HOURGLASS = '⏳'.encode('utf-8')   # ⏳
E_GREEN     = '\U0001f7e2'.encode('utf-8')  # 🟢
E_RED       = '\U0001f534'.encode('utf-8')  # 🔴
E_BLUE      = '\U0001f535'.encode('utf-8')  # 🔵
E_YELLOW    = '\U0001f7e1'.encode('utf-8')  # 🟡
E_CHART_D   = '\U0001f4ca'.encode('utf-8')  # 📊
E_MIDOT     = '\xb7'.encode('utf-8')        # ·
E_WALL      = '\U0001f9f1'.encode('utf-8')  # 🧱
E_FOLDER    = '\U0001f5d3'.encode('utf-8')  # 🗓
E_CALENDAR  = '\U0001f4c6'.encode('utf-8')  # 📆
E_ARROW     = '→'.encode('utf-8')      # →
E_MAGNIFY   = '\U0001f50d'.encode('utf-8')  # 🔍
E_DATES     = '\U0001f4c5'.encode('utf-8')  # 📅
E_CHART_U   = '\U0001f4c8'.encode('utf-8')  # 📈
E_OI_ROLLS  = '\U0001f4ca'.encode('utf-8')  # 📊
E_ANTENNA   = '\U0001f4e1'.encode('utf-8')  # 📡
E_MEAN_REV  = '\U0001f4c8'.encode('utf-8')  # 📈
E_REFRESH   = '\U0001f504'.encode('utf-8')  # 🔄
E_FIRE      = '\U0001f525'.encode('utf-8')  # 🔥
E_SPEC      = '⚡'.encode('utf-8')      # ⚡
E_NEUTRAL   = '⚪'.encode('utf-8')      # ⚪
E_GAMMA     = 'Γ'.encode('utf-8')      # Γ
E_EMDASH    = '—'.encode('utf-8')      # —

func_bytes = []
func_bytes.append(b'\n')
func_bytes.append(b'async def signal_ticker_detail(query, ticker):\n')
func_bytes.append(b'    """Per-ticker OI signal detail with this-week/next-week expiry split, OI walls, volume analysis."""\n')
func_bytes.append(b'    tk = str(ticker).upper()\n')
func_bytes.append(b'    _loading = await query.message.reply_text(f"' + E_HOURGLASS + b' Loading {tk} signals...", parse_mode=H)\n')
func_bytes.append(b'    conn = get_conn()\n')
func_bytes.append(b'    try:\n')
func_bytes.append(b'        lr = pd.read_sql("""SELECT DISTINCT trade_date_now FROM options_change WHERE ticker=?\n')
func_bytes.append(b'            ORDER BY substr(trade_date_now,7,4)||substr(trade_date_now,1,2)||substr(trade_date_now,4,2) DESC\n')
func_bytes.append(b'            LIMIT 1""", conn, params=(tk,))\n')
func_bytes.append(b'        if lr.empty:\n')
func_bytes.append(b'            await query.message.reply_text(f"No OI data for {tk}.", reply_markup=InlineKeyboardMarkup([[BACK_BTN]]))\n')
func_bytes.append(b'            conn.close(); return\n')
func_bytes.append(b'        latest_date = lr["trade_date_now"].iloc[0]\n')
func_bytes.append(b'        agg = pd.read_sql("""SELECT SUM(change_OI_Call) as cc, SUM(change_OI_Put) as pp,\n')
func_bytes.append(b'               SUM(openInt_Call_now) as co, SUM(openInt_Put_now) as po\n')
func_bytes.append(b'            FROM options_change WHERE ticker=? AND trade_date_now=?""", conn, params=(tk, latest_date))\n')
func_bytes.append(b'        call_chg = float(agg["cc"].iloc[0] or 0) if not agg.empty else 0\n')
func_bytes.append(b'        put_chg  = float(agg["pp"].iloc[0] or 0) if not agg.empty else 0\n')
func_bytes.append(b'        call_oi  = float(agg["co"].iloc[0] or 0) if not agg.empty else 0\n')
func_bytes.append(b'        put_oi   = float(agg["po"].iloc[0] or 0) if not agg.empty else 0\n')
func_bytes.append(b'        pcr = put_oi / call_oi if call_oi > 0 else 1.0\n')
func_bytes.append(b'    except Exception as _e:\n')
func_bytes.append(b'        log.warning(f"signal_ticker_detail {tk}: {_e}")\n')
func_bytes.append(b'        conn.close(); return\n')
func_bytes.append(b'\n')
func_bytes.append(b'    sig_lbl, sig_txt = _oi_signal_light(call_chg, put_chg, pcr)\n')
func_bytes.append(b'    sig_em = "' + E_GREEN + b'" if "BULL" in sig_lbl else ("' + E_RED + b'" if "BEAR" in sig_lbl else ("' + E_BLUE + b'" if "HEDGE" in sig_lbl else "' + E_YELLOW + b'"))\n')
func_bytes.append(b'\n')
func_bytes.append(b'    def _fk(n):\n')
func_bytes.append(b'        n = float(n or 0); s = "+" if n >= 0 else ""\n')
func_bytes.append(b'        a = abs(n)\n')
func_bytes.append(b'        if a >= 1_000_000: return f"{s}{a/1_000_000:.1f}M"\n')
func_bytes.append(b'        if a >= 1_000:     return f"{s}{a/1_000:.0f}K"\n')
func_bytes.append(b'        return f"{s}{n:.0f}"\n')
func_bytes.append(b'\n')
func_bytes.append(b'    parts = [\n')
func_bytes.append(b'        hdr(f"' + E_CHART_D + b' {tk} ' + E_MIDOT + b' {latest_date}"),\n')
func_bytes.append(b'        f"{sig_em} <b>{sig_lbl}</b>' + BS_N + b'<i>{sig_txt}</i>",\n')
func_bytes.append(b"        mono(\n")
func_bytes.append(b"            f\"{'Call dOI':<10} {_fk(call_chg):>8}" + BS_N + b'"\n')
func_bytes.append(b"            f\"{'Put  dOI':<10} {_fk(put_chg):>8}" + BS_N + b'"\n')
func_bytes.append(b"            f\"{'PCR':<10} {pcr:>8.2f}\"\n")
func_bytes.append(b'        )\n')
func_bytes.append(b'    ]\n')
func_bytes.append(b'\n')
func_bytes.append(b'    # OI walls\n')
func_bytes.append(b'    try:\n')
func_bytes.append(b'        _kl = _oi_key_levels(tk, conn, latest_date)\n')
func_bytes.append(b'        if _kl:\n')
func_bytes.append(b'            _cws = _kl.get("call_wall",0); _pws = _kl.get("put_wall",0); _mps = _kl.get("max_pain",0)\n')
func_bytes.append(b'            _gws = " / ".join(f"${g:.0f}" for g in _kl.get("gamma_walls",[])[:3]) or "' + E_EMDASH + b'"\n')
func_bytes.append(b'            parts.append(\n')
func_bytes.append(b"                f\"" + BS_N + b"<b>" + E_WALL + b" OI Walls ({_kl.get('expiry','')[:8]})</b>" + BS_N + b'"\n')
func_bytes.append(b"                + mono(f\"{'CWall':<8}${_cws:.0f}  {'PWall':<7}${_pws:.0f}" + BS_N + b'"\n')
func_bytes.append(b"                      f\"{'MaxPain':<8}${_mps:.0f}  " + E_GAMMA + b':{_gws}")\n')
func_bytes.append(b'            )\n')
func_bytes.append(b'    except Exception: pass\n')
func_bytes.append(b'\n')
func_bytes.append(b'    # Per-expiry: This Week / Next Week / Later\n')
func_bytes.append(b'    try:\n')
func_bytes.append(b'        exp_df = pd.read_sql("""\n')
func_bytes.append(b'            SELECT expiry_date,\n')
func_bytes.append(b'                   SUM(change_OI_Call) as cc, SUM(change_OI_Put) as pp,\n')
func_bytes.append(b'                   SUM(openInt_Call_now) as co, SUM(openInt_Put_now) as po\n')
func_bytes.append(b'            FROM options_change WHERE ticker=? AND trade_date_now=?\n')
func_bytes.append(b'            GROUP BY expiry_date\n')
func_bytes.append(b'            ORDER BY substr(expiry_date,7,4)||substr(expiry_date,1,2)||substr(expiry_date,4,2)\n')
func_bytes.append(b'        """, conn, params=(tk, latest_date))\n')
func_bytes.append(b'        if not exp_df.empty:\n')
func_bytes.append(b'            _today = datetime.now().date()\n')
func_bytes.append(b'            _dtf = (4 - _today.weekday()) % 7\n')
func_bytes.append(b'            _this_fri = _today + timedelta(days=_dtf)\n')
func_bytes.append(b'            _next_fri = _this_fri + timedelta(days=7)\n')
func_bytes.append(b'            _bk_tw = chr(0x26a1) + " THIS WEEK"\n')
func_bytes.append(b'            _bk_nw = chr(0x1f4c5) + " NEXT WEEK"\n')
func_bytes.append(b'            _bk_lt = chr(0x1f52d) + " LATER"\n')
func_bytes.append(b'            _bk = {_bk_tw: [], _bk_nw: [], _bk_lt: []}\n')
func_bytes.append(b'            for _, er in exp_df.iterrows():\n')
func_bytes.append(b'                try:\n')
func_bytes.append(b'                    _edt = datetime.strptime(str(er["expiry_date"]), "%m-%d-%Y").date()\n')
func_bytes.append(b'                    if _edt <= _this_fri:   _bk[_bk_tw].append(er)\n')
func_bytes.append(b'                    elif _edt <= _next_fri: _bk[_bk_nw].append(er)\n')
func_bytes.append(b'                    else:                   _bk[_bk_lt].append(er)\n')
func_bytes.append(b'                except Exception:\n')
func_bytes.append(b'                    _bk[_bk_lt].append(er)\n')
func_bytes.append(b'            exp_parts = ["' + BS_N + b'<b>Expiry Breakdown:</b>"]\n')
func_bytes.append(b'            for _lbl, _rows in _bk.items():\n')
func_bytes.append(b'                if not _rows: continue\n')
func_bytes.append(b'                tbl_lines = ["{:<8} {:>6} {:>6} {:>4}".format("Expiry","CdOI","PdOI","PCR")]\n')
func_bytes.append(b'                tbl_lines.append("-" * 27)\n')
func_bytes.append(b'                for er in _rows:\n')
func_bytes.append(b'                    cc2 = float(er["cc"] or 0); pp2 = float(er["pp"] or 0)\n')
func_bytes.append(b'                    ep2 = float(er["po"] or 0) / max(float(er["co"] or 0), 1)\n')
func_bytes.append(b'                    tbl_lines.append("{:<8} {:>6} {:>6} {:>4.1f}".format(\n')
func_bytes.append(b'                        str(er["expiry_date"])[:8], _fk(cc2), _fk(pp2), min(ep2,9.9)))\n')
func_bytes.append(b'                exp_parts.append(f"' + BS_N + b'<b>{_lbl}</b>' + BS_N + b'{mono(chr(10).join(tbl_lines))}")\n')
func_bytes.append(b'            parts.append("' + BS_N + b'".join(exp_parts))\n')
func_bytes.append(b'    except Exception as _ex_e:\n')
func_bytes.append(b'        log.warning(f"signal_ticker_detail expiry {tk}: {_ex_e}")\n')
func_bytes.append(b'\n')
func_bytes.append(b'    # Calendar spreads\n')
func_bytes.append(b'    try:\n')
func_bytes.append(b'        _cal = [r for r in analyze_oi_rolls(tk, conn) if r["type"] == "CALENDAR_ROLL"]\n')
func_bytes.append(b'        if _cal:\n')
func_bytes.append(b'            cal_lines = ["' + BS_N + b'<b>' + E_FOLDER + b' Calendar Spreads:</b>"]\n')
func_bytes.append(b'            for cr in _cal[:3]:\n')
func_bytes.append(b'                cal_lines.append(\n')
func_bytes.append(b"                    f\"" + E_CALENDAR + b" {cr['option']} <b>${cr['strike']:.0f}</b>  \"\n")
func_bytes.append(b"                    f\"{str(cr['near_expiry'])[:8]}" + E_ARROW + b"{str(cr['far_expiry'])[:8]}  ~{cr['qty']:,}c" + BS_N + b'"\n')
func_bytes.append(b'                    f"   <i>Duration extended ' + E_EMDASH + b' same direction, more time.</i>"\n')
func_bytes.append(b'                )\n')
func_bytes.append(b'            parts.append("' + BS_N + b'".join(cal_lines))\n')
func_bytes.append(b'    except Exception: pass\n')
func_bytes.append(b'\n')
func_bytes.append(b'    # Spot + strike breakdown\n')
func_bytes.append(b'    spot = 0.0\n')
func_bytes.append(b'    try:\n')
func_bytes.append(b'        _sd = pd.read_sql("""SELECT close FROM stock_daily WHERE ticker=?\n')
func_bytes.append(b'            ORDER BY substr(trade_date,7,4)||substr(trade_date,1,2)||substr(trade_date,4,2) DESC LIMIT 1""",\n')
func_bytes.append(b'            conn, params=(tk,))\n')
func_bytes.append(b'        if not _sd.empty: spot = float(_sd["close"].iloc[0])\n')
func_bytes.append(b'    except Exception: pass\n')
func_bytes.append(b'\n')
func_bytes.append(b'    bd = _oi_strike_breakdown(tk, conn, spot, latest_date)\n')
func_bytes.append(b'    if bd: parts.append(f"' + BS_N + b'<b>' + E_MAGNIFY + b' Strike Activity:</b>' + BS_N + b'{bd}")\n')
func_bytes.append(b'\n')
func_bytes.append(b'    trend = _oi_trend_summary(tk, conn, latest_date)\n')
func_bytes.append(b'    if trend: parts.append(f"' + BS_N + b'<b>' + E_DATES + b' OI Trend (1W/1M):</b>' + BS_N + b'{trend}")\n')
func_bytes.append(b'\n')
func_bytes.append(b'    # Volume analysis\n')
func_bytes.append(b'    try:\n')
func_bytes.append(b'        _vm = _classify_stock_move(tk, call_chg, put_chg)\n')
func_bytes.append(b'        if _vm and _vm.get("signal") not in ("NEUTRAL", "MIXED"):\n')
func_bytes.append(b'            _vi = {"REAL_BUY":"' + E_GREEN + b'","DELTA_HEDGE":"' + E_BLUE + b'","SHORT_COVER":"' + E_YELLOW + b'",\n')
func_bytes.append(b'                    "SPEC_CALL":"' + E_SPEC + b'","REAL_SELL":"' + E_RED + b'","EVENT_STRADDLE":"' + E_SPEC + b'"}.get(_vm["signal"],"' + E_NEUTRAL + b'")\n')
func_bytes.append(b'            parts.append(\n')
func_bytes.append(b'                f"' + BS_N + b'<b>' + E_CHART_D + b' Move Classification</b>' + BS_N + b'"\n')
func_bytes.append(b"                f\"{_vi} <b>{_vm['signal'].replace('_',' ')}</b>  [{_vm['confidence']}]" + BS_N + b'"\n')
func_bytes.append(b"                + mono(f\"Vol {_vm['vol_ratio']:.2f}x  Price {_vm['price_chg']:+.2f}%\")\n")
func_bytes.append(b"                + f\"" + BS_N + b"<i>{_vm['explanation'][:100]}</i>\"\n")
func_bytes.append(b'            )\n')
func_bytes.append(b'    except Exception: pass\n')
func_bytes.append(b'\n')
func_bytes.append(b'    conn.close()\n')
func_bytes.append(b'    kb = InlineKeyboardMarkup([\n')
func_bytes.append(b'        [InlineKeyboardButton("' + E_OI_ROLLS + b' OI Rolls",     callback_data=f"oi_roll_{tk}"),\n')
func_bytes.append(b'         InlineKeyboardButton("' + E_ANTENNA + b' Inst Signals", callback_data=f"inst_sig_{tk}")],\n')
func_bytes.append(b'        [InlineKeyboardButton("' + E_MEAN_REV + b' Mean Rev",     callback_data=f"mean_rev_{tk}"),\n')
func_bytes.append(b'         InlineKeyboardButton("' + E_REFRESH + b' Refresh",      callback_data=f"sigtk_{tk}")],\n')
func_bytes.append(b'        [InlineKeyboardButton("' + E_FIRE + b' Back to Signals", callback_data="menu_signals"), BACK_BTN],\n')
func_bytes.append(b'    ])\n')
func_bytes.append(b'    try: await _loading.delete()\n')
func_bytes.append(b'    except Exception: pass\n')
func_bytes.append(b'    await query.message.reply_text("' + BS_N + b'".join(parts), parse_mode=H, reply_markup=kb)\n')
func_bytes.append(b'\n')

func_text = b''.join(func_bytes).decode('utf-8')

# Verify syntax
try:
    ast.parse(func_text)
    print(f'Syntax OK. Lines: {len(func_text.splitlines())}')
except SyntaxError as e:
    print(f'SyntaxError: {e}')
    lines_list = func_text.splitlines()
    if e.lineno:
        for i in range(max(0, e.lineno-3), min(len(lines_list), e.lineno+2)):
            print(f'{i+1}: {repr(lines_list[i][:80])}')

# Save for insertion
with open('_sigtk_func.txt', 'w', encoding='utf-8') as f:
    f.write(func_text)
print('Saved to _sigtk_func.txt')
