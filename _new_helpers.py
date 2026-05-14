def _oi_signal_verdict(ticker, today_date, prev_date):
    """OI SIGNAL / Verdict text for EOD vs EOD (same format as live vs EOD)."""
    try:
        conn = get_conn()
        df_t = pd.read_sql("""
            SELECT strike, SUM(openInt_Call_now) AS c_oi, SUM(openInt_Put_now) AS p_oi
            FROM options_change WHERE ticker=? AND trade_date_now=?
            GROUP BY strike""", conn, params=(ticker.upper(), today_date))
        df_p = pd.read_sql("""
            SELECT strike, SUM(openInt_Call_now) AS c_oi, SUM(openInt_Put_now) AS p_oi
            FROM options_change WHERE ticker=? AND trade_date_now=?
            GROUP BY strike""", conn, params=(ticker.upper(), prev_date))
        sd = pd.read_sql("""SELECT close FROM stock_daily WHERE ticker=?
            ORDER BY substr(trade_date,7,4)||substr(trade_date,1,2)||substr(trade_date,4,2) DESC LIMIT 1""",
            conn, params=(ticker.upper(),))
        kl = _oi_key_levels(ticker.upper(), conn)
        conn.close()
    except Exception:
        return ""

    if df_t.empty or df_p.empty:
        return ""

    c_now = float(df_t["c_oi"].sum()); p_now = float(df_t["p_oi"].sum())
    c_prv = float(df_p["c_oi"].sum()); p_prv = float(df_p["p_oi"].sum())
    call_chg = c_now - c_prv; put_chg = p_now - p_prv
    call_pct = call_chg / max(c_prv, 1) * 100
    put_pct  = put_chg  / max(p_prv, 1) * 100
    pcr_eod  = p_prv / max(c_prv, 1)
    pcr_now  = p_now / max(c_now, 1)
    spot     = float(sd["close"].iloc[0]) if not sd.empty else 0.0

    if put_chg > 0 and abs(put_chg) > abs(call_chg) * 1.2:
        sig = "BEARISH";  sig_em = "\U0001f4c9"
    elif call_chg > 0 and call_chg > abs(put_chg) * 1.2:
        sig = "BULLISH";  sig_em = "\U0001f4c8"
    elif call_chg > 0 and put_chg > 0:
        sig = "STRADDLE"; sig_em = "⚡"
    elif call_chg < 0 and put_chg < 0:
        sig = "UNWIND";   sig_em = "\U0001f504"
    elif pcr_now > 1.3:
        sig = "BEARISH";  sig_em = "\U0001f4c9"
    elif pcr_now < 0.7:
        sig = "BULLISH";  sig_em = "\U0001f4c8"
    else:
        sig = "NEUTRAL";  sig_em = "⚪"

    reasons = []
    if put_chg > 0 and abs(put_chg) > abs(call_chg):
        reasons.append(f"• Put OI grew {put_chg:+,.0f} ({put_pct:+.1f}%) — traders adding downside bets")
    if call_chg > 0 and call_chg > abs(put_chg):
        reasons.append(f"• Call OI grew {call_chg:+,.0f} ({call_pct:+.1f}%) — bullish positioning increasing")
    if call_chg < 0:
        reasons.append(f"• Call OI fell {call_chg:+,.0f} ({call_pct:+.1f}%) — bulls reducing exposure")
    if pcr_now > pcr_eod * 1.05:
        reasons.append(f"• PCR rose {pcr_eod:.2f} → {pcr_now:.2f} (more puts vs calls = bearish lean)")
    elif pcr_now < pcr_eod * 0.95:
        reasons.append(f"• PCR fell {pcr_eod:.2f} → {pcr_now:.2f} (fewer puts vs calls = bullish lean)")
    if not reasons:
        reasons.append(f"• Net change: calls {call_chg:+,.0f}  puts {put_chg:+,.0f}")

    strike_lines = []
    if kl:
        cw = kl.get("call_wall", 0); pw = kl.get("put_wall", 0)
        mp = kl.get("max_pain", 0)
        cw_oi = kl.get("call_wall_oi", 0); pw_oi = kl.get("put_wall_oi", 0)
        if cw and spot:
            strike_lines.append(f"  Call Wall: ${cw:.0f} ({(cw-spot)/spot*100:+.1f}% from spot) OI:{cw_oi/1000:.0f}K — CEILING")
        if pw and spot:
            strike_lines.append(f"  Put Wall:  ${pw:.0f} ({(pw-spot)/spot*100:+.1f}% from spot) OI:{pw_oi/1000:.0f}K — FLOOR")
        if mp and spot:
            _dir = "above" if mp > spot else "below"
            strike_lines.append(f"  Max Pain:  ${mp:.0f} ({(mp-spot)/spot*100:+.1f}%) {_dir} spot — expiry magnet")

    simple_ans = ""
    if spot and kl:
        cw = kl.get("call_wall", 0); pw = kl.get("put_wall", 0); mp = kl.get("max_pain", 0)
        bull_tgt = cw if cw and cw > spot else spot * 1.03
        bear_tgt = pw if pw and pw < spot else spot * 0.97
        simple_ans = (
            chr(10) + "\U0001f3af <b>Simple Answer</b>" + chr(10)
            + ("⚠️" if sig in ("BEARISH", "STRADDLE") else "✅")
            + f" Bullish scenario → ${bull_tgt:.0f} target"
            + (" (call wall)" if cw and cw > spot else " (+3% est)") + chr(10)
            + ("❌" if sig in ("BEARISH", "STRADDLE") else "⚠️")
            + f" Bearish scenario → ${bear_tgt:.0f} target"
            + (" (put wall)" if pw and pw < spot else " (-3% est)")
            + (chr(10) + f"\U0001f9f2 Max Pain ${mp:.0f} — price may drift here by expiry" if mp else "")
        )

    return (
        f"{sig_em} <b>{ticker} OI SIGNAL — {today_date}</b>" + chr(10)
        + f"<b>Verdict: {sig}</b>" + chr(10) + chr(10)
        + f"<b>Why {sig.title()}?</b>" + chr(10)
        + chr(10).join(reasons)
        + (chr(10) + chr(10) + "<b>Key Strike Levels</b>" + chr(10) + chr(10).join(strike_lines) if strike_lines else "")
        + simple_ans
        + chr(10) + chr(10) + f"<i>Net call chg: {call_chg:+,.0f}  Net put chg: {put_chg:+,.0f}</i>"
    )


def _oi_volume_chart(ticker: str, conn, spot: float, latest_date: str):
    """Option Volume Profile — calls top, puts bottom, OI as dashed outline.
    Returns BytesIO PNG or None.
    """
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import numpy as np

        BG = "#0D1117"; PANEL = "#161B22"; TXT = "#E6EDF3"; GRID = "#30363D"

        df = pd.read_sql("""
            SELECT strike,
                   SUM(vol_Call_now)    AS c_vol,
                   SUM(vol_Put_now)     AS p_vol,
                   SUM(openInt_Call_now) AS c_oi,
                   SUM(openInt_Put_now)  AS p_oi
            FROM options_change
            WHERE ticker=? AND trade_date_now=?
              AND strike BETWEEN ? AND ?
            GROUP BY strike ORDER BY strike
        """, conn, params=(ticker, latest_date, spot * 0.80, spot * 1.20))

        if df.empty:
            return None

        for col in ["c_vol", "p_vol", "c_oi", "p_oi"]:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)

        df["total_vol"] = df["c_vol"] + df["p_vol"]
        df = df[df["total_vol"] > 0].nlargest(20, "total_vol").sort_values("strike")
        if df.empty:
            return None

        strikes = df["strike"].tolist()
        c_vol = df["c_vol"].tolist()
        p_vol = [-v for v in df["p_vol"].tolist()]
        c_oi  = df["c_oi"].tolist()
        p_oi  = [-v for v in df["p_oi"].tolist()]
        x = np.arange(len(strikes))
        w = 0.38

        fig, ax = plt.subplots(figsize=(10, 5), facecolor=BG)
        ax.set_facecolor(PANEL)

        ax.bar(x - w/2, c_vol, width=w, color="#2D8B2D", label="Call Vol", alpha=0.85)
        ax.bar(x + w/2, p_vol, width=w, color="#8B0000", label="Put Vol",  alpha=0.85)
        ax.bar(x - w/2, c_oi,  width=w, fill=False, edgecolor="#00CC66", linewidth=0.8, linestyle="--", label="Call OI")
        ax.bar(x + w/2, p_oi,  width=w, fill=False, edgecolor="#FF6666", linewidth=0.8, linestyle="--", label="Put OI")

        atm_idx = min(range(len(strikes)), key=lambda i: abs(strikes[i] - spot))
        ax.axvline(atm_idx, color="#FFD700", linewidth=1.5, linestyle="--", alpha=0.9, label="ATM")

        try:
            kl = _oi_key_levels(ticker, conn)
            if kl:
                for wall_k, wall_lbl, wall_col in [
                    ("call_wall", "CWall", "#00CC66"),
                    ("put_wall",  "PWall", "#FF4444"),
                ]:
                    wv = kl.get(wall_k, 0)
                    if wv and wv in strikes:
                        wi = strikes.index(wv)
                        y_top = max(max(c_vol), 1)
                        ax.axvline(wi, color=wall_col, linewidth=1.2, linestyle=":", alpha=0.8)
                        ax.text(wi + 0.15, y_top * 0.85, wall_lbl,
                                fontsize=7, color=wall_col, rotation=90, va="top")
        except Exception:
            pass

        ax.set_xticks(x)
        ax.set_xticklabels([f"${s:.0f}" for s in strikes], fontsize=7, color=TXT, rotation=45, ha="right")
        ax.tick_params(colors=TXT, length=0)
        ax.axhline(0, color=TXT, linewidth=0.6)
        ax.legend(fontsize=7, facecolor=PANEL, edgecolor=GRID, labelcolor=TXT, loc="upper right")
        ax.set_title(f"{ticker}  Option Volume Profile  ·  Spot ${spot:.0f}  ·  {latest_date}",
                     color=TXT, fontsize=10, fontweight="bold")
        ax.set_ylabel("Volume (calls +, puts −)", fontsize=8, color=TXT)
        for spine in ax.spines.values():
            spine.set_edgecolor(GRID)
        ax.yaxis.label.set_color(TXT)
        ax.tick_params(axis="y", colors=TXT)
        fig.text(0.5, 0.01,
                 "Solid bars=today volume  Dashed outline=open interest  Gold dashed=ATM",
                 ha="center", fontsize=7, color="#8B949E")
        plt.tight_layout(rect=[0, 0.04, 1, 1])

        buf = BytesIO()
        plt.savefig(buf, format="png", dpi=105, bbox_inches="tight", facecolor=BG)
        plt.close(fig)
        buf.seek(0)
        return buf
    except Exception as _e:
        log.warning(f"_oi_volume_chart failed: {_e}")
        return None


