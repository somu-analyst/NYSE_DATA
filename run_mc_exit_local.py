"""
Local runner to execute the Monte Carlo exit analysis logic standalone.
Run:
    python run_mc_exit_local.py TICKER OPT_TYPE STRIKE ENTRY EXPIRY
Example:
    python run_mc_exit_local.py AAPL put 150 2.5 2026-03-20

This prints MC percentiles, expected P&L, P(Profit), VaR and current greeks.
"""
import sys
from datetime import datetime, timedelta
import numpy as np
import yfinance as yf
from telegram_bot import bs_price, bs_greeks


def run_mc(ticker, opt_type, strike, entry, expiry_str, n_sims=10000):
    try:
        expiry = datetime.strptime(expiry_str, "%Y-%m-%d").date()
    except Exception:
        expiry = (datetime.now() + timedelta(days=20)).date()

    tk_obj = yf.Ticker(ticker)
    hist = tk_obj.history(period="3mo")
    if len(hist) < 2:
        raise RuntimeError("Insufficient history for ticker")
    spot = float(hist["Close"].iloc[-1])
    prev = float(hist["Close"].iloc[-2])
    closes = hist["Close"].dropna().values
    hist_returns = np.diff(np.log(closes))
    hv = float(np.std(hist_returns)) * np.sqrt(252) if len(hist_returns) >= 20 else 0.25

    # IV attempt
    iv = 0.30
    try:
        chain = tk_obj.option_chain(expiry.strftime("%Y-%m-%d"))
        oc = chain.puts if opt_type == "put" else chain.calls
        m = oc[oc["strike"] == float(strike)]
        if not m.empty:
            fiv = float(m.iloc[0].get("impliedVolatility", 0))
            if fiv >= 0.05:
                iv = fiv
    except Exception:
        pass

    # VIX
    vix_val = 20.0
    vix_pct = 0.0
    try:
        vix_h = yf.Ticker("^VIX").history(period="5d")
        if len(vix_h) >= 2:
            vix_val = float(vix_h["Close"].iloc[-1])
            vix_pct = (vix_val - float(vix_h["Close"].iloc[-2])) / float(vix_h["Close"].iloc[-2]) * 100
    except Exception:
        pass

    # Futures gap
    es_pct, nq_pct = 0.0, 0.0
    try:
        for sym, label in [("ES=F", "ES"), ("NQ=F", "NQ")]:
            fh = yf.Ticker(sym).history(period="5d")
            if len(fh) >= 2:
                pct = (float(fh["Close"].iloc[-1]) - float(fh["Close"].iloc[-2])) / float(fh["Close"].iloc[-2]) * 100
                if label == "ES": es_pct = pct
                else: nq_pct = pct
    except Exception:
        pass
    predicted_gap = (es_pct + nq_pct) / 2

    # MC params
    dte = max((datetime.combine(expiry, datetime.min.time()) - datetime.now()).days, 1)
    T_tomorrow = max(dte - 1, 1) / 365.0

    mc_vix_vol = vix_val / 100.0 * 1.3 if vix_val > 15 else 0
    if mc_vix_vol > 0:
        mc_vol = 0.4 * iv + 0.3 * hv + 0.3 * mc_vix_vol
    else:
        mc_vol = 0.6 * iv + 0.4 * hv
    if vix_pct > 10 and mc_vix_vol > mc_vol:
        mc_vol = max(mc_vol, mc_vix_vol * 0.85)
    mc_vol = max(mc_vol, 0.15)

    futures_drift = predicted_gap / 100.0
    overnight_drift = futures_drift - 0.001

    dt = 1.0 / 252.0
    np.random.seed(42)
    Z = np.random.standard_normal(n_sims)
    sim_returns = overnight_drift + (-0.5 * mc_vol**2 * dt) + mc_vol * np.sqrt(dt) * Z
    sim_prices = spot * np.exp(sim_returns)

    iv_base = iv
    if vix_val > 20:
        iv_base = max(iv_base, vix_val / 100.0 * 1.2)
    iv_vix_adj = 0.02 + (0.03 if abs(predicted_gap) > 1 else 0)
    if vix_pct > 10:
        iv_vix_adj += 0.05 + max(0, (vix_pct - 10) * 0.002)
    sim_ivs = np.clip(iv_base + iv_vix_adj + np.random.normal(0, 0.03, n_sims), 0.05, 2.0)

    option_vals = np.array([
        bs_price(s, float(strike), T_tomorrow, 0.045, sigma, opt=opt_type)
        for s, sigma in zip(sim_prices, sim_ivs)
    ])

    exp_stock = float(np.mean(sim_prices))
    exp_val = float(np.mean(option_vals))
    p10 = float(np.percentile(option_vals, 10))
    p90 = float(np.percentile(option_vals, 90))

    pnl_array = (option_vals - float(entry)) * 100.0
    exp_pnl = float(np.mean(pnl_array))
    prob_profit = float(np.mean(option_vals > float(entry)) * 100.0)
    var_95 = float(np.percentile(pnl_array, 5))

    cur_val = bs_price(spot, float(strike), max(dte,1)/365.0, 0.045, iv, opt=opt_type)
    greeks = bs_greeks(spot, float(strike), max(dte,1)/365.0, 0.045, iv, opt=opt_type)

    print(f"Ticker: {ticker} | {opt_type.upper()} ${strike} | Entry ${entry} | Expiry {expiry}")
    print("-"*60)
    print(f"Spot: {spot:.2f}  IV(est): {iv:.2%}  HV: {hv:.2%}")
    print(f"Exp. Stock: ${exp_stock:.2f}")
    print(f"Exp. Option: ${exp_val:.2f}")
    print(f"Range (10-90): ${p10:.2f} - ${p90:.2f}")
    print(f"Exp. P&L: ${exp_pnl:+,.0f}  P(Profit): {prob_profit:.0f}%  VaR95: ${var_95:+,.0f}")
    print(f"Current Theo: ${cur_val:.2f}")
    print(f"Greeks: {greeks}")


if __name__ == '__main__':
    if len(sys.argv) < 6:
        print("Usage: python run_mc_exit_local.py TICKER OPT_TYPE STRIKE ENTRY EXPIRY(YYYY-MM-DD) [SIMS]")
        sys.exit(1)
    _, ticker, opt_type, strike, entry, expiry = sys.argv[:6]
    sims = int(sys.argv[6]) if len(sys.argv) > 6 else 10000
    run_mc(ticker, opt_type, float(strike), float(entry), expiry, n_sims=sims)
