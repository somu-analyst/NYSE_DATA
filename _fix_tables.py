"""
Fix all major tables to use consistent flat <pre> table format.
Target: telegram_bot.py (live copy only, before main()).
"""
import sys

path = r"c:\Users\srini\Options_chain_data\NYSE_DATA\telegram_bot.py"
with open(path, "r", encoding="utf-8") as f:
    lines = f.readlines()

def find_range(needle_start, needle_end, search_start=0, search_end=None):
    """Return (start_lineno, end_lineno) 0-based, inclusive."""
    limit = search_end or len(lines)
    start = None
    for i in range(search_start, limit):
        if needle_start in lines[i]:
            start = i
            break
    if start is None:
        return None, None
    for i in range(start + 1, limit):
        if needle_end in lines[i]:
            return start, i
    return start, None


# ─────────────────────────────────────────────────────────────────────
# 1. OVERNIGHT RISK — replace the giant per-ticker loop output section
#    with a single flat table.
#    Find: "r_rate = 0.045" ... "await _loading.delete()" in overnight fn
# ─────────────────────────────────────────────────────────────────────
OR_START, OR_END = find_range(
    "r_rate = 0.045\n",
    "except Exception: pass\n",
    search_start=9990, search_end=10300
)
if OR_START is None or OR_END is None:
    print(f"ERROR: overnight risk block not found  start={OR_START} end={OR_END}")
    sys.exit(1)
print(f"Overnight risk block: lines {OR_START+1}–{OR_END+1}")

OR_NEW = '''\
    r_rate = 0.045
    total_theta_day  = 0.0
    total_delta_1pct = 0.0
    total_value      = 0.0
    risk_rows = []   # (tk, type, strk, dte, spot_tag, theta_d, delta_1, risk_lvl, pnl_pct)

    for idx, tr in trades.iterrows():
        tk    = str(tr.get("ticker", "?"))
        ot    = str(tr.get("option_type", "?")).upper()
        strk  = _safe_float(tr.get("strike", 0), 0)
        entry = _safe_float(tr.get("entry_price", 0), 0)
        qty   = _safe_int(tr.get("quantity", 1), 1)
        exp_s = str(tr.get("expiry", ""))[:10]
        try:
            dte = max((datetime.strptime(exp_s, "%Y-%m-%d").date() - datetime.now().date()).days, 1)
        except Exception:
            dte = 30

        px = _get_spot_with_ah(tk)
        spot_reg = px["spot_reg"] if px["spot_reg"] > 0 else strk
        spot_ext = px["spot_ext"] if px["spot_ext"] > 0 else spot_reg
        spot     = spot_ext
        ah_tag   = f"AH:{spot_ext:.0f}" if px["is_extended"] else f"${spot_reg:.0f}"

        T      = max(dte, 1) / 365.0
        opt_lc = ot.lower() if ot.lower() in ("call", "put") else "put"
        greeks = bs_greeks(spot, strk, T, r_rate, iv_base, opt=opt_lc)
        theo   = bs_price(spot, strk, T, r_rate, iv_base, opt=opt_lc)

        delta = greeks.get("delta", 0)
        theta = greeks.get("theta", 0)
        contracts = abs(qty)
        pos_sign  = 1 if qty > 0 else -1
        side_s    = "S" if qty < 0 else "B"

        theta_day   = theta * 100 * contracts * pos_sign
        delta_1pct  = delta * spot * 0.01 * 100 * contracts * pos_sign
        pos_value   = theo * 100 * contracts

        total_theta_day   += theta_day
        total_delta_1pct  += delta_1pct
        total_value       += pos_value * pos_sign

        pnl_pct  = (theo - entry) / entry * 100 * pos_sign if entry > 0 else 0
        risk_lvl = "HIGH" if dte <= 3 or pnl_pct < -40 else ("MED" if dte <= 7 else "LOW")
        risk_rows.append((tk, f"{ot[:4]}{side_s}", strk, dte, ah_tag,
                          theta_day, delta_1pct, risk_lvl))

    gap_dn = total_delta_1pct * -2
    gap_up = total_delta_1pct *  2

    # ── Portfolio summary (HTML bold, no table) ───────────────────
    vix_em   = "🔴" if vix_val > 25 else ("🟡" if vix_val > 18 else "🟢")
    theta_em = "🔴" if total_theta_day < -50 else "🟡"
    delta_em = "🟢" if total_delta_1pct > 0 else "🔴"

    summary = (
        f"<b>⚠️ OVERNIGHT RISK REPORT</b>  <i>{datetime.now().strftime('%H:%M ET')}</i>\\n\\n"
        f"{vix_em} VIX <b>{vix_val:.1f}</b>  "
        f"({'High Fear' if vix_val > 25 else 'Elevated' if vix_val > 18 else 'Calm'})\\n"
        f"{theta_em} Theta tonight  <b>${total_theta_day:+,.0f}</b>\\n"
        f"{delta_em} Delta (mkt+1%) <b>${total_delta_1pct:+,.0f}</b>\\n"
        f"🔴 Gap-down 2%  <b>${gap_dn:+,.0f}</b>\\n"
        f"🟢 Gap-up 2%    <b>${gap_up:+,.0f}</b>\\n"
        f"💼 Portfolio    <b>${abs(total_value):,.0f}</b>\\n\\n"
        f"<i>AH/PM prices used where available</i>"
    )
    await query.message.reply_text(summary, parse_mode=H)

    # ── Flat <pre> table ──────────────────────────────────────────
    C = [5, 6, 6, 4, 8, 6, 6, 4]   # col widths
    def _cell(v, w, right=False):
        s = str(v)[:w]
        return s.rjust(w) if right else s.ljust(w)

    HDR = ("Tkr", "Type", "Strk", "DTE", "Spot", "Th/d", "D1%", "Risk")
    sep = "─" * (sum(C) + len(C) - 1)
    rows_pre = [
        "  ".join(_cell(h, w) for h, w in zip(HDR, C)),
        sep,
    ]
    for (tk2, typ, strk2, dte2, spot_tag, theta_d, delta_1, risk_lv) in risk_rows:
        rs = {"HIGH": "HI!", "MED": "MED", "LOW": "ok"}.get(risk_lv, "   ")
        rows_pre.append("  ".join([
            _cell(tk2,            C[0]),
            _cell(typ,            C[1]),
            _cell(f"${strk2:.0f}", C[2], right=True),
            _cell(f"{dte2}d",     C[3], right=True),
            _cell(spot_tag,       C[4], right=True),
            _cell(f"${theta_d:+.0f}", C[5], right=True),
            _cell(f"${delta_1:+.0f}", C[6], right=True),
            _cell(rs,             C[7]),
        ]))
    rows_pre += [
        sep,
        f"Theta: ${total_theta_day:+,.0f}   Delta+1%: ${total_delta_1pct:+,.0f}",
        "HI!=DTE<=3/PnL<=-40%  MED=DTE<=7",
    ]
    detail_msg = f"<b>📋 Position Detail</b>\\n<pre>{chr(10).join(rows_pre)}</pre>"

    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("📡 Position Monitor", callback_data="menu_pos_monitor"),
        InlineKeyboardButton("🌙 AH Predictor", callback_data="menu_aftermarket_predict"),
        BACK_BTN
    ]])
    await query.message.reply_text(detail_msg, parse_mode=H, reply_markup=kb)
    try: await _loading.delete()
    except Exception: pass
'''
lines[OR_START:OR_END+1] = [OR_NEW]
print("  overnight risk patched")


# ─────────────────────────────────────────────────────────────────────
# 2. AH PREDICTOR — per-ticker leg block → flat <pre> table per ticker
#    + pre-market order table
# ─────────────────────────────────────────────────────────────────────
# Re-index after overnight change
with open(path, "w", encoding="utf-8") as f:
    f.writelines(lines)
with open(path, "r", encoding="utf-8") as f:
    lines = f.readlines()

# Find the leg_lines.append block inside aftermarket_predict
AH_LEG_START, AH_LEG_END = find_range(
    "leg_lines.append(\n",
    "all_orders.append(",
    search_start=10100, search_end=10400
)
if AH_LEG_START is None or AH_LEG_END is None:
    print(f"WARN: AH leg block not found, skipping  {AH_LEG_START} {AH_LEG_END}")
else:
    # extend end to include the all_orders.append line
    AH_LEG_END += 1
    print(f"AH leg block: lines {AH_LEG_START+1}–{AH_LEG_END+1}")
    AH_LEG_NEW = '''\
            act_em = {"CLOSE":"🔴","TAKE PROFIT":"🟢","STOP LOSS":"🟠","HOLD":"🟢","WATCH":"⚪"}.get(action,"⚪")
            pnl_em = "🟢" if pnl_vs_entry >= 0 else "🔴"
            crush_sfx = ""
            if val_post is not None:
                pnl_post = (val_post - entry) * 100 * contracts * pos_sign
                crush_sfx = f" | crush→${val_post:.2f} P&L${pnl_post:+,.0f}"

            leg_lines.append((
                side_s, ot_s, strk, dte, entry,
                val_now, pnl_vs_entry, pnl_pct,
                val_tmrw, pnl_chg_pct, action, limit, reason, crush_sfx
            ))
            all_orders.append({"tk": tk, "ot_s": ot_s, "strk": strk, "action": action,
                                "limit": limit, "pnl_pct": pnl_pct, "reason": reason})
'''
    lines[AH_LEG_START:AH_LEG_END+1] = [AH_LEG_NEW]
    print("  AH leg tuple patched")

# Patch tk_card output to use <pre> table
with open(path, "w", encoding="utf-8") as f:
    f.writelines(lines)
with open(path, "r", encoding="utf-8") as f:
    lines = f.readlines()

TK_CARD_START, TK_CARD_END = find_range(
    "# Event risk line\n",
    "await query.message.reply_text(tk_card",
    search_start=10100, search_end=10450
)
if TK_CARD_START is None or TK_CARD_END is None:
    print(f"WARN: tk_card block not found, skipping  {TK_CARD_START} {TK_CARD_END}")
else:
    print(f"tk_card block: lines {TK_CARD_START+1}–{TK_CARD_END+1}")
    TK_CARD_NEW = '''\
        # Event risk line
        ev_line = f"\\n{ev['iv_crush_warning']}" if ev.get("iv_crush_warning") else ""

        tk_pnl_em  = "🟢" if tk_pnl_now >= 0 else "🔴"
        tk_tmrw_em = "🟢" if tk_pnl_tmrw >= 0 else "🔴"
        stock_line = (
            f"{stock_em} <b>{tk}</b>  EOD <b>${spot_reg:.2f}</b> → {ah_src} <b>${spot_ext:.2f}</b> {ext_tag}\\n"
            f"{tk_pnl_em} P&amp;L now <b>${tk_pnl_now:+,.0f}</b>  "
            f"{tk_tmrw_em} Tmrw est <b>${tk_pnl_tmrw:+,.0f}</b>"
            + ev_line
        )

        # Flat <pre> leg table
        _C = [5, 5, 6, 4, 6, 6, 6, 5, 5]
        _H = ("Side","Type","Strk","DTE","Entry","Now","P&L%","Tmrw","Act")
        _sep = "─" * (sum(_C) + len(_C) - 1)
        _rows = [
            "  ".join(str(h).ljust(w) for h, w in zip(_H, _C)),
            _sep,
        ]
        for (sd, ot2, st2, dt2, en2, vn, pnl$, pnl%, vt, pct_t, act, lim, rsn, csfx) in leg_lines:
            act_s = act[:5] if act else "WATCH"
            _rows.append("  ".join([
                str(sd)[:_C[0]].ljust(_C[0]),
                str(ot2)[:_C[1]].ljust(_C[1]),
                f"${st2:.0f}".rjust(_C[2]),
                f"{dt2}d".rjust(_C[3]),
                f"${en2:.2f}".rjust(_C[4]),
                f"${vn:.2f}".rjust(_C[5]),
                f"{pnl%:+.0f}%".rjust(_C[6]),
                f"${vt:.2f}".rjust(_C[7]),
                str(act_s).ljust(_C[8]),
            ]))
            # crush line below row if present
            if csfx:
                _rows.append(f"  🔥 {csfx}")
        _rows.append(_sep)
        # order lines
        for (sd, ot2, st2, dt2, en2, vn, pnl$, pnl%, vt, pct_t, act, lim, rsn, csfx) in leg_lines:
            if lim:
                _rows.append(f"  → {act}: limit ${lim:.2f}  ({rsn})")

        tk_card = (
            stock_line + "\\n"
            + f"<pre>{chr(10).join(_rows)}</pre>"
        )
        await query.message.reply_text(tk_card, parse_mode="HTML")
'''
    lines[TK_CARD_START:TK_CARD_END+1] = [TK_CARD_NEW]
    print("  tk_card patched")

# ─────────────────────────────────────────────────────────────────────
# 3. AH Predictor summary pre-market order table — already uses mono(),
#    improve column layout
# ─────────────────────────────────────────────────────────────────────
with open(path, "w", encoding="utf-8") as f:
    f.writelines(lines)
with open(path, "r", encoding="utf-8") as f:
    lines = f.readlines()

ORD_START, ORD_END = find_range(
    "if close_orders:\n",
    "Place these as GTC limit orders",
    search_start=10300, search_end=10500
)
if ORD_START is None or ORD_END is None:
    print(f"WARN: close_orders block not found {ORD_START} {ORD_END}")
else:
    print(f"close_orders block: lines {ORD_START+1}–{ORD_END+1}")
    ORD_NEW = '''\
    if close_orders:
        summary_lines.append("\\n<b>📋 Pre-Market GTC Orders</b>")
        _C2 = [5, 5, 6, 11, 7, 6]
        _H2 = ("Tkr","Type","Strk","Action","Limit","Why")
        _sep2 = "─" * (sum(_C2) + len(_C2) - 1)
        ord_rows = [
            "  ".join(str(h).ljust(w) for h, w in zip(_H2, _C2)),
            _sep2,
        ]
        for o in close_orders:
            lim_s = f"${o['limit']:.2f}" if o["limit"] else "MKT"
            why_s = (o["reason"] or "")[:_C2[5]]
            ord_rows.append("  ".join([
                str(o["tk"])[:_C2[0]].ljust(_C2[0]),
                str(o["ot_s"])[:_C2[1]].ljust(_C2[1]),
                f"${o['strk']:.0f}".rjust(_C2[2]),
                str(o["action"])[:_C2[3]].ljust(_C2[3]),
                str(lim_s).rjust(_C2[4]),
                str(why_s).ljust(_C2[6] if len(_C2) > 6 else _C2[5]),
            ]))
        ord_rows.append(_sep2)
        summary_lines.append(mono("\\n".join(ord_rows)))
        summary_lines.append("<i>Place as GTC limit orders before market open</i>")
'''
    lines[ORD_START:ORD_END+1] = [ORD_NEW]
    print("  close_orders table patched")

# ─────────────────────────────────────────────────────────────────────
# 4. POSITIONS VIEW — leg_lines → flat <pre> table
# ─────────────────────────────────────────────────────────────────────
with open(path, "w", encoding="utf-8") as f:
    f.writelines(lines)
with open(path, "r", encoding="utf-8") as f:
    lines = f.readlines()

POS_START, POS_END = find_range(
    "leg_lines = []\n",
    "+ mono('\\n'.join(leg_lines))\n",
    search_start=2300, search_end=2420
)
if POS_START is None or POS_END is None:
    print(f"WARN: positions leg_lines block not found {POS_START} {POS_END}")
else:
    print(f"positions leg_lines block: lines {POS_START+1}–{POS_END+1}")
    POS_NEW = '''\
        _pc = [4, 5, 6, 7, 8, 10]  # col widths: #id side type strk entry exp
        _ph = ("#", "Side", "Type", "Strk", "Entry", "Expiry")
        _psep = "─" * (sum(_pc) + len(_pc) - 1)
        leg_rows = [
            "  ".join(str(h).ljust(w) for h, w in zip(_ph, _pc)),
            _psep,
        ]
        for _, tr in grp.iterrows():
            tid  = _safe_int(tr.get('trade_id', 0), 0)
            ot   = str(tr.get('option_type', '?'))[:4].upper()
            st   = _safe_float(tr.get('strike', 0), 0)
            ep   = _safe_float(tr.get('entry_price', 0), 0)
            qty  = _safe_int(tr.get('quantity', 0), 0)
            exp  = str(tr.get('expiry', ''))[:10]
            side = 'L' if qty >= 0 else 'S'
            leg_rows.append("  ".join([
                str(f"#{tid}")[:_pc[0]].ljust(_pc[0]),
                str(side).ljust(_pc[1]),
                str(ot).ljust(_pc[2]),
                f"${st:.0f}".rjust(_pc[3]),
                f"${ep:.2f}".rjust(_pc[4]),
                str(exp).ljust(_pc[5]),
            ]))
        leg_rows.append(_psep)
        s_mark = 's' if n_legs > 1 else ''
        parts.append(
            f'\\n<b>{tk}</b>  {n_legs} leg{s_mark}  '
            f'({n_calls}C / {n_puts}P  \\u2022  {n_long}L / {n_short}S)\\n'
            f'Next exp: <code>{next_exp}</code>\\n'
            + mono("\\n".join(leg_rows))
        )
'''
    lines[POS_START:POS_END+1] = [POS_NEW]
    print("  positions leg table patched")

with open(path, "w", encoding="utf-8") as f:
    f.writelines(lines)
print("\\nAll patches done.")
