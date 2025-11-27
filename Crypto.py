import requests
import pandas as pd
from datetime import datetime

# Deribit public HTTP API base
BASE_URL = "https://www.deribit.com/api/v2"

# Edit this list any time to add/remove underlyings Deribit supports
CRYPTO_UNDERLYINGS = ["BTC", "ETH"]


def deribit_get(path, params=None):
    """
    Simple helper to call a Deribit public HTTP endpoint and
    return the 'result' field.
    """
    url = f"{BASE_URL}{path}"
    resp = requests.get(url, params=params or {}, timeout=10)
    resp.raise_for_status()
    data = resp.json()
    if "result" not in data:
        raise RuntimeError(f"Unexpected response: {data}")
    return data["result"]


def fetch_deribit_instruments(underlying):
    """
    Get all active option instruments for one underlying (e.g. BTC or ETH).
    Uses base_currency to filter by underlying.
    """
    params = {
        "kind": "option",
        "base_currency": underlying,
        "expired": False
    }
    return deribit_get("/public/get_instruments", params=params)


def fetch_deribit_summary(instrument_name):
    """
    Get summary for a single option instrument: open interest, 24h volume, etc.
    """
    params = {"instrument_name": instrument_name}
    result_list = deribit_get("/public/get_book_summary_by_instrument", params=params)
    if not result_list:
        raise RuntimeError(f"No summary returned for {instrument_name}")
    return result_list[0]


def parse_instrument_name(name):
    """
    Parse Deribit option instrument name, e.g. BTC-27DEC24-50000-C
    -> underlying, expiry_date(YYYY-MM-DD), strike(float), kind('C'/'P')
    """
    parts = name.split("-")
    if len(parts) < 4:
        return None, None, None, None

    underlying = parts[0]
    expiry_raw = parts[1]
    strike_raw = parts[2]
    opt_type = parts[3]

    # Convert expiry DMMMYY -> YYYY-MM-DD if possible
    try:
        expiry_dt = datetime.strptime(expiry_raw, "%d%b%y")
        expiry_str = expiry_dt.strftime("%Y-%m-%d")
    except Exception:
        expiry_str = expiry_raw

    try:
        strike_val = float(strike_raw)
    except Exception:
        strike_val = None

    kind = "C" if opt_type.upper().startswith("C") else "P"
    return underlying, expiry_str, strike_val, kind


def collect_deribit_options(underlyings=None):
    """
    Collect option chain for all specified underlyings from Deribit
    and return a pandas DataFrame.
    """
    if underlyings is None:
        underlyings = CRYPTO_UNDERLYINGS

    rows = []
    trade_date = datetime.utcnow().strftime("%Y-%m-%d")

    for cur in underlyings:
        print(f"Fetching instruments for {cur} on Deribit...")
        instruments = fetch_deribit_instruments(cur)
        print(f"  Found {len(instruments)} instruments")

        for inst in instruments:
            inst_name = inst["instrument_name"]
            underlying, expiry, strike, kind = parse_instrument_name(inst_name)
            if underlying is None:
                continue

            try:
                summary = fetch_deribit_summary(inst_name)
            except Exception as e:
                print(f"  Summary error for {inst_name}: {e}")
                continue

            row = {
                "underlying": underlying,
                "instrument_name": inst_name,
                "expiry_date": expiry,
                "strike": strike,
                "option_type": "CALL" if kind == "C" else "PUT",
                "open_interest": summary.get("open_interest"),
                "volume_24h": summary.get("volume"),
                "mark_price": summary.get("mark_price"),
                "bid_price": summary.get("bid_price"),
                "ask_price": summary.get("ask_price"),
                "exchange": "Deribit",
                "trade_date": trade_date,
            }
            rows.append(row)

    if not rows:
        print("No Deribit options data collected.")
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    return df


if __name__ == "__main__":
    df = collect_deribit_options()
    if not df.empty:
        out_name = f"Crypto_Options_Deribit_{datetime.utcnow().strftime('%d%b%Y')}.csv"
        df.to_csv(out_name, index=False)
        print(f"\n✅ Saved Deribit crypto options to {out_name}")
        print(df.head().to_string(index=False))
    else:
        print("\n❌ No data to save")

