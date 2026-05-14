"""Replace overnight_risk_report photo block with clean text table (lines 10065-10111)."""
import sys

path = r"c:\Users\srini\Options_chain_data\NYSE_DATA\telegram_bot.py"
with open(path, "r", encoding="utf-8") as f:
    lines = f.readlines()

# Find the live overnight_risk_report's try: buf = _tbl_img block
# We look for the first occurrence of "buf = _tbl_img" between line 10000 and 10120
start = None
end   = None
for i in range(9990, 10120):
    if 'buf = _tbl_img' in lines[i] and start is None:
        # Find the try: line just before
        for j in range(i-1, i-5, -1):
            if lines[j].strip() == "try:":
                start = j
                break
    if start and 'await _loading.delete()' in lines[i] and i > start + 20:
        end = i + 1
        break

if start is None or end is None:
    print(f"ERROR: could not find block start={start} end={end}")
    sys.exit(1)

print(f"Replacing lines {start+1}–{end} ({end-start} lines)")

new_block = '''    vix_em    = "🔴" if vix_val > 25 else ("🟡" if vix_val > 18 else "🟢")
    tdelta_em = "🟢" if total_delta_1pct > 0 else "🔴"
    theta_em  = "🔴" if total_theta_day < -100 else "🟡"

    # ── Portfolio summary header ──────────────────────────────────
    summary = (
        f"{hdr('⚠️ OVERNIGHT RISK REPORT')}\\n\\n"
        f"{vix_em} <b>VIX:</b> {vix_val:.1f}  "
        f"({'High Fear' if vix_val > 25 else 'Elevated' if vix_val > 18 else 'Calm'})\\n\\n"
        f"{theta_em} <b>Theta tonight:</b> ${total_theta_day:+,.0f}\\n"
        f"{tdelta_em} <b>Delta (mkt +1%):</b> ${total_delta_1pct:+,.0f}\\n"
        f"🔴 <b>Gap-down 2%:</b> ${gap_pnl:+,.0f}\\n"
        f"🟢 <b>Gap-up 2%:</b> ${gap_up_pnl:+,.0f}\\n"
        f"<b>Portfolio Value:</b> ${abs(total_value):,.0f}\\n\\n"
        f"<i>📌 AH/PM spot prices used where available</i>"
    )
    await query.message.reply_text(summary, parse_mode=H)

    # ── Per-position risk table as text (grouped by ticker) ───────
    risk_em = {"HIGH": "🔴", "MED": "🟡", "LOW": "🟢"}
    tbl_lines = ["<b>Position Risk Detail</b>", ""]
    seen_tk = {}
    for row in risk_rows:
        seen_tk.setdefault(row[0], []).append(row)
    for tk_key, rows in seen_tk.items():
        tbl_lines.append(f"<b>── {tk_key} ──</b>")
        for row in rows:
            _, typ, strk, dte, spot_tag, theta_d, delta_1, risk_lv = row
            em = risk_em.get(risk_lv, "⚪")
            tbl_lines.append(
                f"{em} <b>{typ}</b> K${strk}  {dte}  [{spot_tag}]\\n"
                f"   Θ {theta_d}/d  Δ1% {delta_1}  Risk: <b>{risk_lv}</b>"
            )
        tbl_lines.append("")
    tbl_lines.append("<i>🔴 HIGH=DTE≤3 or P&amp;L≤-40%  🟡 MED=DTE≤7</i>")

    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("📡 Position Monitor", callback_data="menu_pos_monitor"),
        InlineKeyboardButton("🌙 AH Predictor", callback_data="menu_aftermarket_predict"),
        BACK_BTN
    ]])
    await query.message.reply_text("\\n".join(tbl_lines), parse_mode=H, reply_markup=kb)
    try: await _loading.delete()
    except Exception: pass
'''

lines[start:end] = [new_block]
with open(path, "w", encoding="utf-8") as f:
    f.writelines(lines)
print("Done.")
