from ib_insync import IB, Stock, Option
from datetime import datetime, timedelta
import pandas as pd
import time

IB_HOST = '127.0.0.1'
IB_PORT = 7497        # TWS paper default
IB_CLIENT_ID = 1

def test_ibkr_options(symbol="AAPL", max_days_ahead=60, strikes_window=10):
    ib = IB()
    ib.connect(IB_HOST, IB_PORT, clientId=IB_CLIENT_ID)

    under = Stock(symbol, 'SMART', 'USD')
    ib.qualifyContracts(under)

    # get underlying price (delayed/real depending on permissions)
    t = ib.reqMktData(under, '', False, True)
    ib.sleep(2)
    spot = t.last or t.close
    ib.cancelMktData(under)
    print(f"Spot for {symbol}: {spot}")

    # option parameters
    params = ib.reqSecDefOptParams(under.symbol, '', under.secType, under.conId)
    if not params:
        print("No option params.")
        ib.disconnect()
        return

    p = params[0]
    expirations = sorted(p.expirations)
    strikes = sorted(p.strikes)

    # filter expiries to next N days
    today = datetime.utcnow().date()
    max_exp = today + timedelta(days=max_days_ahead)
    expiries = []
    for e in expirations:
        try:
            d = datetime.strptime(e, "%Y%m%d").date()
        except Exception:
            continue
        if d <= max_exp:
            expiries.append((e, d))
    print("Using expiries:", [d.strftime("%Y-%m-%d") for _, d in expiries[:3]])

    # filter strikes ±window
    if spot and strikes:
        import numpy as np
        arr = np.array(strikes, dtype=float)
        idx = (abs(arr - spot)).argmin()
        lo = max(idx - strikes_window, 0)
        hi = min(idx + strikes_window, len(arr)-1)
        keep_strikes = set(arr[lo:hi+1])
    else:
        keep_strikes = set(strikes)

    rows = []
    # only first 1–2 expiries for testing
    for exp_str, exp_dt in expiries[:2]:
        for right in ["C", "P"]:
            contracts = []
            for k in keep_strikes:
                opt = Option(under.symbol, exp_str, float(k), right, 'SMART')
                contracts.append(opt)

            ib.qualifyContracts(*contracts)
            ticks = ib.reqMktData(contracts, '', False, True)
            ib.sleep(2)

            for c, tk in zip(contracts, ticks):
                last = tk.last or tk.close
                rows.append({
                    "symbol": symbol,
                    "expiry": exp_dt.strftime("%Y-%m-%d"),
                    "right": right,
                    "strike": c.strike,
                    "last": last,
                    "bid": tk.bid,
                    "ask": tk.ask,
                    "volume": tk.volume,
                    "oi": getattr(tk, "optionOpenInterest", None),
                })

            for tk in ticks:
                ib.cancelMktData(tk.contract)

    ib.disconnect()

    df = pd.DataFrame(rows)
    print(df.head())
    return df

if __name__ == "__main__":
    test_ibkr_options("AAPL")
