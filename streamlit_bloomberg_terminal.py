from __future__ import annotations

import json
import os
import sqlite3
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
import re
import time

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st
import yfinance as yf

try:
    from ib_insync import IB, Stock, Forex, ContFuture, Crypto, Option, util  # type: ignore
    IBKR_AVAILABLE = True
except Exception:
    IBKR_AVAILABLE = False


st.set_page_config(
    page_title="Global Macro Terminal",
    page_icon="🖥️",
    layout="wide",
)

app_refresh_start = time.perf_counter()


GLOBAL_ASSETS = {
    "US Equity Indices": ["^GSPC", "^DJI", "^IXIC", "^RUT"],
    "Global Equity Indices": ["^FTSE", "^GDAXI", "^FCHI", "^N225", "^HSI", "000001.SS"],
    "Sectors (US ETFs)": ["XLF", "XLK", "XLE", "XLI", "XLY", "XLV", "XLP", "XLB", "XLRE"],
    "Rates & FX": ["^TNX", "DX-Y.NYB", "EURUSD=X", "JPY=X", "GBPUSD=X"],
    "Commodities": ["CL=F", "BZ=F", "NG=F", "GC=F", "SI=F", "HG=F", "ZW=F", "ZS=F", "ZC=F"],
    "Crypto": ["BTC-USD", "ETH-USD"],
}

RELATIONSHIP_MAP = {
    "Oil Complex": ["CL=F", "BZ=F", "XLE", "OXY", "XOM", "CVX"],
    "Gold Complex": ["GC=F", "SI=F", "GDX", "NEM", "AEM", "GOLD"],
    "Industrial Metals": ["HG=F", "XLB", "RIO", "BHP", "FCX", "SCCO"],
    "Agri Basket": ["ZW=F", "ZS=F", "ZC=F", "WEAT", "CORN", "SOYB"],
}

DEFAULT_WATCHLIST = [
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "JPM", "BAC", "WMT",
    "ASML", "TSM", "TM", "SONY", "SHEL", "BP", "RIO", "BHP", "NVO", "SAP",
]

FUTURES_CONT_MAP = {
    "CL=F": ("CL", "NYMEX"),
    "BZ=F": ("BZ", "ICEEU"),
    "NG=F": ("NG", "NYMEX"),
    "GC=F": ("GC", "COMEX"),
    "SI=F": ("SI", "COMEX"),
    "HG=F": ("HG", "COMEX"),
    "ZW=F": ("ZW", "ECBOT"),
    "ZS=F": ("ZS", "ECBOT"),
    "ZC=F": ("ZC", "ECBOT"),
}


def _to_naive_utc_timestamp(ts: pd.Timestamp) -> pd.Timestamp:
    if ts.tzinfo is None:
        return ts
    return ts.tz_convert("UTC").tz_localize(None)


def _period_to_ibkr_duration(period: str) -> str:
    mapping = {
        "1mo": "1 M",
        "3mo": "3 M",
        "6mo": "6 M",
        "1y": "1 Y",
        "2y": "2 Y",
    }
    return mapping.get(period, "6 M")


def _interval_to_ibkr_barsize(interval: str) -> str:
    mapping = {
        "1d": "1 day",
        "1wk": "1 week",
    }
    return mapping.get(interval, "1 day")


def _symbol_to_ibkr_contract(symbol: str):
    if symbol in FUTURES_CONT_MAP:
        sym, ex = FUTURES_CONT_MAP[symbol]
        return ContFuture(sym, exchange=ex)

    if symbol.endswith("=X") and len(symbol) >= 7:
        pair = symbol.split("=")[0]
        if len(pair) == 6 and pair.isalpha():
            return Forex(pair.upper())

    if "-" in symbol and symbol.endswith("-USD"):
        base = symbol.split("-")[0].upper()
        return Crypto(base, "PAXOS", "USD")

    if symbol.startswith("^"):
        return None

    if re.fullmatch(r"[A-Za-z0-9.]+", symbol):
        return Stock(symbol.upper(), "SMART", "USD")

    return None


@st.cache_data(ttl=300, show_spinner=False)
def download_close_prices(symbols: list[str], period: str = "6mo", interval: str = "1d") -> pd.DataFrame:
    if not symbols:
        return pd.DataFrame()

    try:
        data = yf.download(
            tickers=" ".join(sorted(set(symbols))),
            period=period,
            interval=interval,
            auto_adjust=True,
            progress=False,
            threads=True,
        )
    except Exception:
        return pd.DataFrame()

    if data.empty:
        return pd.DataFrame()

    if isinstance(data.columns, pd.MultiIndex):
        if "Close" in data.columns.get_level_values(0):
            closes = data["Close"].copy()
        elif "Adj Close" in data.columns.get_level_values(0):
            closes = data["Adj Close"].copy()
        else:
            return pd.DataFrame()
    else:
        if "Close" in data.columns:
            closes = data[["Close"]].copy()
            closes.columns = [symbols[0]]
        elif "Adj Close" in data.columns:
            closes = data[["Adj Close"]].copy()
            closes.columns = [symbols[0]]
        else:
            return pd.DataFrame()

    closes = closes.dropna(how="all").sort_index()
    if not closes.empty and isinstance(closes.index, pd.DatetimeIndex):
        closes.index = pd.to_datetime(closes.index).map(_to_naive_utc_timestamp)
    return closes


def download_close_prices_ibkr(
    symbols: list[str],
    period: str,
    interval: str,
    host: str,
    port: int,
    client_id: int,
    market_data_type: int,
    timeout: int,
) -> tuple[pd.DataFrame, dict]:
    if not IBKR_AVAILABLE:
        return pd.DataFrame(), {"connected": False, "reason": "ib_insync not installed", "loaded": 0, "requested": 0}

    if not symbols:
        return pd.DataFrame(), {"connected": False, "reason": "no symbols", "loaded": 0, "requested": 0}

    ib = IB()
    loaded = 0
    series_map: dict[str, pd.Series] = {}

    try:
        ib.connect(host, port, clientId=client_id, timeout=timeout, readonly=True)
        try:
            ib.reqMarketDataType(market_data_type)
        except Exception:
            pass

        for symbol in symbols:
            try:
                contract = _symbol_to_ibkr_contract(symbol)
                if contract is None:
                    continue
                ib.qualifyContracts(contract)
                bars = ib.reqHistoricalData(
                    contract,
                    endDateTime="",
                    durationStr=_period_to_ibkr_duration(period),
                    barSizeSetting=_interval_to_ibkr_barsize(interval),
                    whatToShow="TRADES",
                    useRTH=False,
                    formatDate=1,
                    keepUpToDate=False,
                )
                if not bars:
                    continue
                df = util.df(bars)
                if df.empty or "date" not in df.columns or "close" not in df.columns:
                    continue
                s = pd.Series(df["close"].values, index=pd.to_datetime(df["date"]), name=symbol).dropna()
                if s.empty:
                    continue
                s.index = pd.to_datetime(s.index).map(_to_naive_utc_timestamp)
                series_map[symbol] = s
                loaded += 1
            except Exception:
                continue
    except Exception as exc:
        try:
            if ib.isConnected():
                ib.disconnect()
        except Exception:
            pass
        return pd.DataFrame(), {"connected": False, "reason": str(exc), "loaded": 0, "requested": len(symbols)}
    finally:
        try:
            if ib.isConnected():
                ib.disconnect()
        except Exception:
            pass

    if not series_map:
        return pd.DataFrame(), {"connected": True, "reason": "no IBKR bars returned", "loaded": loaded, "requested": len(symbols)}

    combined = pd.concat(series_map.values(), axis=1)
    combined = combined.sort_index().dropna(how="all")
    return combined, {"connected": True, "reason": "ok", "loaded": loaded, "requested": len(symbols)}


def get_price_matrix(
    symbols: list[str],
    period: str,
    interval: str,
    source_mode: str,
    ibkr_host: str,
    ibkr_port: int,
    ibkr_client_id: int,
    ibkr_market_data_type: int,
    ibkr_timeout: int,
) -> tuple[pd.DataFrame, dict]:
    symbols = sorted(set([s for s in symbols if s]))
    if not symbols:
        return pd.DataFrame(), {"source": "none", "loaded": 0, "requested": 0}

    if source_mode == "Yahoo":
        yf_df = download_close_prices(symbols, period=period, interval=interval)
        return yf_df, {"source": "Yahoo", "loaded": int(yf_df.shape[1]) if not yf_df.empty else 0, "requested": len(symbols)}

    if source_mode == "IBKR":
        ib_df, status = download_close_prices_ibkr(
            symbols=symbols,
            period=period,
            interval=interval,
            host=ibkr_host,
            port=ibkr_port,
            client_id=ibkr_client_id,
            market_data_type=ibkr_market_data_type,
            timeout=ibkr_timeout,
        )
        status["source"] = "IBKR"
        return ib_df, status

    ib_df, status = download_close_prices_ibkr(
        symbols=symbols,
        period=period,
        interval=interval,
        host=ibkr_host,
        port=ibkr_port,
        client_id=ibkr_client_id,
        market_data_type=ibkr_market_data_type,
        timeout=ibkr_timeout,
    )
    missing = [s for s in symbols if ib_df.empty or s not in ib_df.columns]
    if missing:
        yf_df = download_close_prices(missing, period=period, interval=interval)
        if ib_df.empty:
            merged = yf_df
        elif yf_df.empty:
            merged = ib_df
        else:
            merged = pd.concat([ib_df, yf_df], axis=1)
            merged = merged.loc[:, ~merged.columns.duplicated()]
            merged = merged.sort_index().dropna(how="all")
    else:
        merged = ib_df

    return merged, {
        "source": "Auto",
        "ibkr_loaded": int(ib_df.shape[1]) if not ib_df.empty else 0,
        "requested": len(symbols),
        "loaded": int(merged.shape[1]) if not merged.empty else 0,
        "ibkr_status": status,
    }


def _extract_ibkr_open_interest(ticker_obj) -> float | None:
    candidate_attrs = [
        "futuresOpenInterest",
        "openInterest",
        "callOpenInterest",
        "putOpenInterest",
    ]
    for attr in candidate_attrs:
        try:
            value = getattr(ticker_obj, attr, None)
            if value is not None and pd.notna(value):
                value = float(value)
                if value >= 0:
                    return value
        except Exception:
            continue
    return None


@st.cache_data(ttl=180, show_spinner=False)
def fetch_open_interest_ibkr(
    symbols: list[str],
    host: str,
    port: int,
    client_id: int,
    market_data_type: int,
    timeout: int,
) -> pd.DataFrame:
    if not IBKR_AVAILABLE or not symbols:
        return pd.DataFrame(columns=["Symbol", "OI"])

    ib = IB()
    rows: list[dict] = []
    try:
        ib.connect(host, port, clientId=client_id, timeout=timeout, readonly=True)
        try:
            ib.reqMarketDataType(market_data_type)
        except Exception:
            pass

        for symbol in sorted(set(symbols)):
            try:
                contract = _symbol_to_ibkr_contract(symbol)
                if contract is None:
                    rows.append({"Symbol": symbol, "OI": np.nan})
                    continue

                ib.qualifyContracts(contract)
                ticker_obj = ib.reqMktData(contract, "101,588", False, False)
                ib.sleep(1.0)
                oi = _extract_ibkr_open_interest(ticker_obj)
                rows.append({"Symbol": symbol, "OI": oi if oi is not None else np.nan})

                try:
                    ib.cancelMktData(contract)
                except Exception:
                    pass
            except Exception:
                rows.append({"Symbol": symbol, "OI": np.nan})
    except Exception:
        return pd.DataFrame(columns=["Symbol", "OI"])
    finally:
        try:
            if ib.isConnected():
                ib.disconnect()
        except Exception:
            pass

    if not rows:
        return pd.DataFrame(columns=["Symbol", "OI"])

    oi_df = pd.DataFrame(rows)
    oi_df = oi_df.drop_duplicates(subset=["Symbol"], keep="last")
    return oi_df


def fetch_live_snapshot_ibkr(
    symbol: str,
    host: str,
    port: int,
    client_id: int,
    market_data_type: int,
    timeout: int,
) -> dict:
    if not IBKR_AVAILABLE:
        return {}

    contract = _symbol_to_ibkr_contract(symbol)
    if contract is None:
        return {}

    ib = IB()
    try:
        ib.connect(host, port, clientId=client_id, timeout=timeout, readonly=True)
        try:
            ib.reqMarketDataType(market_data_type)
        except Exception:
            pass

        ib.qualifyContracts(contract)
        tk = ib.reqMktData(contract, "101,233,236,258,588", False, False)
        ib.sleep(1.2)

        snapshot = {
            "last": tk.last if pd.notna(tk.last) else tk.marketPrice(),
            "bid": tk.bid,
            "ask": tk.ask,
            "open": tk.open,
            "high": tk.high,
            "low": tk.low,
            "close": tk.close,
            "volume": tk.volume,
        }

        try:
            ib.cancelMktData(contract)
        except Exception:
            pass

        if any(pd.isna(snapshot.get(k)) for k in ["open", "high", "low", "close", "volume"]):
            try:
                bars = ib.reqHistoricalData(
                    contract,
                    endDateTime="",
                    durationStr="2 D",
                    barSizeSetting="1 day",
                    whatToShow="TRADES",
                    useRTH=False,
                    formatDate=1,
                    keepUpToDate=False,
                )
                if bars:
                    h = util.df(bars)
                    if not h.empty:
                        row = h.iloc[-1]
                        snapshot["open"] = snapshot["open"] if pd.notna(snapshot["open"]) else row.get("open")
                        snapshot["high"] = snapshot["high"] if pd.notna(snapshot["high"]) else row.get("high")
                        snapshot["low"] = snapshot["low"] if pd.notna(snapshot["low"]) else row.get("low")
                        snapshot["close"] = snapshot["close"] if pd.notna(snapshot["close"]) else row.get("close")
                        snapshot["volume"] = snapshot["volume"] if pd.notna(snapshot["volume"]) else row.get("volume")
            except Exception:
                pass

        return snapshot
    except Exception:
        return {}
    finally:
        try:
            if ib.isConnected():
                ib.disconnect()
        except Exception:
            pass


def fetch_live_snapshot_yahoo(symbol: str) -> dict:
    try:
        hist = yf.download(symbol, period="5d", interval="1d", auto_adjust=False, progress=False)
        if hist.empty:
            return {}
        row = hist.iloc[-1]
        return {
            "last": row.get("Close", np.nan),
            "bid": np.nan,
            "ask": np.nan,
            "open": row.get("Open", np.nan),
            "high": row.get("High", np.nan),
            "low": row.get("Low", np.nan),
            "close": row.get("Close", np.nan),
            "volume": row.get("Volume", np.nan),
        }
    except Exception:
        return {}


def init_ibkr_backfill_tables(db_path: str) -> None:
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS ibkr_stock_daily (
            symbol TEXT NOT NULL,
            trade_date TEXT NOT NULL,
            open REAL,
            high REAL,
            low REAL,
            close REAL,
            volume REAL,
            source TEXT,
            load_ts TEXT,
            PRIMARY KEY (symbol, trade_date)
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS ibkr_stock_monthly (
            symbol TEXT NOT NULL,
            month_key TEXT NOT NULL,
            open REAL,
            high REAL,
            low REAL,
            close REAL,
            volume REAL,
            source TEXT,
            load_ts TEXT,
            PRIMARY KEY (symbol, month_key)
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS ibkr_oi_snapshot (
            snapshot_ts TEXT NOT NULL,
            symbol TEXT NOT NULL,
            open_interest REAL,
            source TEXT,
            PRIMARY KEY (snapshot_ts, symbol)
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS ibkr_live_oi_snapshot (
            snapshot_ts TEXT NOT NULL,
            symbol TEXT NOT NULL,
            open_interest REAL,
            source TEXT,
            PRIMARY KEY (snapshot_ts, symbol)
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS ibkr_options_daily (
            snapshot_ts TEXT NOT NULL,
            symbol TEXT NOT NULL,
            expiry TEXT NOT NULL,
            strike REAL NOT NULL,
            right TEXT NOT NULL,
            bid REAL,
            ask REAL,
            last REAL,
            close REAL,
            volume REAL,
            open_interest REAL,
            iv REAL,
            delta REAL,
            gamma REAL,
            theta REAL,
            vega REAL,
            source TEXT,
            PRIMARY KEY (snapshot_ts, symbol, expiry, strike, right)
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS ibkr_options_change (
            snapshot_ts TEXT NOT NULL,
            symbol TEXT NOT NULL,
            expiry TEXT NOT NULL,
            strike REAL NOT NULL,
            right TEXT NOT NULL,
            prev_open_interest REAL,
            curr_open_interest REAL,
            change_open_interest REAL,
            pct_change_open_interest REAL,
            prev_close REAL,
            curr_close REAL,
            change_close REAL,
            source TEXT,
            PRIMARY KEY (snapshot_ts, symbol, expiry, strike, right)
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS options_daily (
            ticker TEXT NOT NULL,
            company_name TEXT,
            asset_type TEXT,
            strike REAL NOT NULL,
            expiry_date TEXT NOT NULL,
            trade_date TEXT NOT NULL,
            openInt_Call REAL,
            openInt_Put REAL,
            vol_Call REAL,
            vol_Put REAL,
            lastPrice_Call REAL,
            lastPrice_Put REAL,
            call_open REAL,
            call_high REAL,
            call_low REAL,
            call_close REAL,
            put_open REAL,
            put_high REAL,
            put_low REAL,
            put_close REAL,
            contractSymbol_Call TEXT,
            contractSymbol_Put TEXT,
            load_date TEXT,
            source TEXT,
            PRIMARY KEY (ticker, strike, expiry_date, trade_date)
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS options_change (
            ticker TEXT NOT NULL,
            company_name_now TEXT,
            asset_type_now TEXT,
            strike REAL NOT NULL,
            expiry_date TEXT NOT NULL,
            trade_date_now TEXT NOT NULL,
            openInt_Call_now REAL,
            openInt_Call_prev REAL,
            change_OI_Call REAL,
            pct_change_OI_Call REAL,
            openInt_Put_now REAL,
            openInt_Put_prev REAL,
            change_OI_Put REAL,
            pct_change_OI_Put REAL,
            vol_Call_now REAL,
            vol_Call_prev REAL,
            change_vol_Call REAL,
            pct_change_vol_Call REAL,
            vol_Put_now REAL,
            vol_Put_prev REAL,
            change_vol_Put REAL,
            pct_change_vol_Put REAL,
            lastPrice_Call_now REAL,
            lastPrice_Put_now REAL,
            call_open_now REAL,
            call_high_now REAL,
            call_low_now REAL,
            call_close_now REAL,
            put_open_now REAL,
            put_high_now REAL,
            put_low_now REAL,
            put_close_now REAL,
            R1 REAL,
            S1 REAL,
            R12 REAL,
            S12 REAL,
            load_date TEXT,
            source TEXT,
            PRIMARY KEY (ticker, strike, expiry_date, trade_date_now)
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS stock_daily (
            ticker TEXT NOT NULL,
            trade_date TEXT NOT NULL,
            open REAL,
            high REAL,
            low REAL,
            close REAL,
            volume REAL,
            pcr_oi REAL,
            load_date TEXT,
            source TEXT,
            PRIMARY KEY (ticker, trade_date)
        )
        """
    )
    conn.commit()
    conn.close()


def _format_expiry_mmddyyyy(expiry_yyyymmdd: str) -> str:
    try:
        return datetime.strptime(str(expiry_yyyymmdd), "%Y%m%d").strftime("%m-%d-%Y")
    except Exception:
        return str(expiry_yyyymmdd)


def _safe_pct(now_val: float | int | None, prev_val: float | int | None) -> float | None:
    if now_val is None or prev_val is None:
        return None
    try:
        now_f = float(now_val)
        prev_f = float(prev_val)
    except Exception:
        return None
    if prev_f == 0:
        return None
    return (now_f - prev_f) / prev_f * 100.0


def compute_options_change_compat(db_path: str, trade_date_now: str, symbol_filter: str | None = None) -> int:
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    if symbol_filter:
        cur.execute(
            "SELECT DISTINCT ticker FROM options_daily WHERE trade_date = ? AND ticker = ?",
            (trade_date_now, symbol_filter),
        )
    else:
        cur.execute("SELECT DISTINCT ticker FROM options_daily WHERE trade_date = ?", (trade_date_now,))
    tickers = [r[0] for r in cur.fetchall()]
    if not tickers:
        conn.close()
        return 0

    inserted = 0
    for ticker in tickers:
        cur.execute(
            """
            SELECT DISTINCT trade_date
            FROM options_daily
            WHERE ticker = ? AND trade_date < ?
            ORDER BY trade_date DESC
            LIMIT 1
            """,
            (ticker, trade_date_now),
        )
        prev_row = cur.fetchone()
        if not prev_row:
            continue
        prev_trade_date = prev_row[0]

        df_now = pd.read_sql(
            "SELECT * FROM options_daily WHERE ticker = ? AND trade_date = ?",
            conn,
            params=(ticker, trade_date_now),
        )
        df_prev = pd.read_sql(
            "SELECT * FROM options_daily WHERE ticker = ? AND trade_date = ?",
            conn,
            params=(ticker, prev_trade_date),
        )
        if df_now.empty or df_prev.empty:
            continue

        merged = df_now.merge(
            df_prev,
            on=["ticker", "strike", "expiry_date"],
            how="inner",
            suffixes=("_now", "_prev"),
        )
        if merged.empty:
            continue

        for _, r in merged.iterrows():
            strike = float(r["strike"]) if pd.notna(r.get("strike")) else None
            last_call_now = float(r["lastPrice_Call_now"]) if pd.notna(r.get("lastPrice_Call_now")) else None
            last_put_now = float(r["lastPrice_Put_now"]) if pd.notna(r.get("lastPrice_Put_now")) else None
            call_high_now = float(r["call_high_now"]) if pd.notna(r.get("call_high_now")) else None
            put_high_now = float(r["put_high_now"]) if pd.notna(r.get("put_high_now")) else None

            oi_call_now = float(r["openInt_Call_now"]) if pd.notna(r.get("openInt_Call_now")) else None
            oi_call_prev = float(r["openInt_Call_prev"]) if pd.notna(r.get("openInt_Call_prev")) else None
            oi_put_now = float(r["openInt_Put_now"]) if pd.notna(r.get("openInt_Put_now")) else None
            oi_put_prev = float(r["openInt_Put_prev"]) if pd.notna(r.get("openInt_Put_prev")) else None
            vol_call_now = float(r["vol_Call_now"]) if pd.notna(r.get("vol_Call_now")) else None
            vol_call_prev = float(r["vol_Call_prev"]) if pd.notna(r.get("vol_Call_prev")) else None
            vol_put_now = float(r["vol_Put_now"]) if pd.notna(r.get("vol_Put_now")) else None
            vol_put_prev = float(r["vol_Put_prev"]) if pd.notna(r.get("vol_Put_prev")) else None

            cur.execute(
                """
                INSERT OR REPLACE INTO options_change
                (ticker, company_name_now, asset_type_now, strike, expiry_date, trade_date_now,
                 openInt_Call_now, openInt_Call_prev, change_OI_Call, pct_change_OI_Call,
                 openInt_Put_now, openInt_Put_prev, change_OI_Put, pct_change_OI_Put,
                 vol_Call_now, vol_Call_prev, change_vol_Call, pct_change_vol_Call,
                 vol_Put_now, vol_Put_prev, change_vol_Put, pct_change_vol_Put,
                 lastPrice_Call_now, lastPrice_Put_now,
                 call_open_now, call_high_now, call_low_now, call_close_now,
                 put_open_now, put_high_now, put_low_now, put_close_now,
                 R1, S1, R12, S12, load_date, source)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    ticker,
                    str(r.get("company_name_now") or ticker),
                    str(r.get("asset_type_now") or "stock"),
                    strike,
                    str(r.get("expiry_date")),
                    trade_date_now,
                    oi_call_now,
                    oi_call_prev,
                    (oi_call_now - oi_call_prev) if (oi_call_now is not None and oi_call_prev is not None) else None,
                    _safe_pct(oi_call_now, oi_call_prev),
                    oi_put_now,
                    oi_put_prev,
                    (oi_put_now - oi_put_prev) if (oi_put_now is not None and oi_put_prev is not None) else None,
                    _safe_pct(oi_put_now, oi_put_prev),
                    vol_call_now,
                    vol_call_prev,
                    (vol_call_now - vol_call_prev) if (vol_call_now is not None and vol_call_prev is not None) else None,
                    _safe_pct(vol_call_now, vol_call_prev),
                    vol_put_now,
                    vol_put_prev,
                    (vol_put_now - vol_put_prev) if (vol_put_now is not None and vol_put_prev is not None) else None,
                    _safe_pct(vol_put_now, vol_put_prev),
                    last_call_now,
                    last_put_now,
                    float(r.get("call_open_now")) if pd.notna(r.get("call_open_now")) else None,
                    call_high_now,
                    float(r.get("call_low_now")) if pd.notna(r.get("call_low_now")) else None,
                    float(r.get("call_close_now")) if pd.notna(r.get("call_close_now")) else None,
                    float(r.get("put_open_now")) if pd.notna(r.get("put_open_now")) else None,
                    put_high_now,
                    float(r.get("put_low_now")) if pd.notna(r.get("put_low_now")) else None,
                    float(r.get("put_close_now")) if pd.notna(r.get("put_close_now")) else None,
                    (strike + last_call_now) if (strike is not None and last_call_now is not None) else None,
                    (strike - last_put_now) if (strike is not None and last_put_now is not None) else None,
                    (strike + call_high_now) if (strike is not None and call_high_now is not None) else None,
                    (strike - put_high_now) if (strike is not None and put_high_now is not None) else None,
                    datetime.now().strftime("%m-%d-%Y"),
                    "IBKR",
                ),
            )
            inserted += 1

    conn.commit()
    conn.close()
    return inserted


def compute_ibkr_options_change(db_path: str, snapshot_ts: str, symbol_filter: str | None = None) -> int:
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    if symbol_filter:
        cur.execute(
            """
            SELECT DISTINCT symbol FROM ibkr_options_daily
            WHERE snapshot_ts = ? AND symbol = ?
            """,
            (snapshot_ts, symbol_filter),
        )
    else:
        cur.execute(
            """
            SELECT DISTINCT symbol FROM ibkr_options_daily
            WHERE snapshot_ts = ?
            """,
            (snapshot_ts,),
        )
    symbols = [r[0] for r in cur.fetchall()]
    if not symbols:
        conn.close()
        return 0

    inserted = 0
    for symbol in symbols:
        cur.execute(
            """
            SELECT MAX(snapshot_ts)
            FROM ibkr_options_daily
            WHERE symbol = ? AND snapshot_ts < ?
            """,
            (symbol, snapshot_ts),
        )
        prev_ts = cur.fetchone()[0]
        if not prev_ts:
            continue

        curr = pd.read_sql(
            """
            SELECT symbol, expiry, strike, right, open_interest, close
            FROM ibkr_options_daily
            WHERE snapshot_ts = ? AND symbol = ?
            """,
            conn,
            params=(snapshot_ts, symbol),
        )
        prev = pd.read_sql(
            """
            SELECT symbol, expiry, strike, right, open_interest, close
            FROM ibkr_options_daily
            WHERE snapshot_ts = ? AND symbol = ?
            """,
            conn,
            params=(prev_ts, symbol),
        )
        if curr.empty:
            continue

        merged = curr.merge(
            prev,
            on=["symbol", "expiry", "strike", "right"],
            how="left",
            suffixes=("_curr", "_prev"),
        )

        merged["change_open_interest"] = merged["open_interest_curr"] - merged["open_interest_prev"]
        merged["pct_change_open_interest"] = (
            merged["change_open_interest"] / merged["open_interest_prev"].replace(0, np.nan)
        ) * 100
        merged["change_close"] = merged["close_curr"] - merged["close_prev"]

        for _, r in merged.iterrows():
            cur.execute(
                """
                INSERT OR REPLACE INTO ibkr_options_change
                (snapshot_ts, symbol, expiry, strike, right,
                 prev_open_interest, curr_open_interest, change_open_interest, pct_change_open_interest,
                 prev_close, curr_close, change_close, source)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    snapshot_ts,
                    str(r.get("symbol")),
                    str(r.get("expiry")),
                    float(r.get("strike")) if pd.notna(r.get("strike")) else None,
                    str(r.get("right")),
                    float(r.get("open_interest_prev")) if pd.notna(r.get("open_interest_prev")) else None,
                    float(r.get("open_interest_curr")) if pd.notna(r.get("open_interest_curr")) else None,
                    float(r.get("change_open_interest")) if pd.notna(r.get("change_open_interest")) else None,
                    float(r.get("pct_change_open_interest")) if pd.notna(r.get("pct_change_open_interest")) else None,
                    float(r.get("close_prev")) if pd.notna(r.get("close_prev")) else None,
                    float(r.get("close_curr")) if pd.notna(r.get("close_curr")) else None,
                    float(r.get("change_close")) if pd.notna(r.get("change_close")) else None,
                    "IBKR",
                ),
            )
            inserted += 1

    conn.commit()
    conn.close()
    return inserted


def backfill_ibkr_options_chain(
    symbols: list[str],
    db_path: str,
    host: str,
    port: int,
    client_id: int,
    market_data_type: int,
    timeout: int,
    max_expiries: int,
    strikes_per_side: int,
) -> dict:
    init_ibkr_backfill_tables(db_path)
    if not IBKR_AVAILABLE:
        return {"ok": False, "reason": "ib_insync not installed", "rows": 0}

    symbols = sorted(set([s for s in symbols if re.fullmatch(r"[A-Za-z0-9.]+", s or "")]))
    if not symbols:
        return {"ok": False, "reason": "no stock symbols", "rows": 0}

    ib = IB()
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    snapshot_ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = 0
    rows_skipped_empty = 0
    compat_rows = 0
    loaded_symbols = 0
    skipped = 0
    errors: list[str] = []
    diagnostics: list[dict] = []
    trade_date_mmddyyyy = datetime.now(timezone.utc).strftime("%m-%d-%Y")

    def _fetch_option_daily_ohlc(contract: Option) -> dict:
        try:
            bars = ib.reqHistoricalData(
                contract,
                endDateTime="",
                durationStr="5 D",
                barSizeSetting="1 day",
                whatToShow="TRADES",
                useRTH=False,
                formatDate=1,
                keepUpToDate=False,
            )
            if not bars:
                return {}
            hdf = util.df(bars)
            if hdf.empty:
                return {}
            hdf["date"] = pd.to_datetime(hdf["date"], errors="coerce")
            hdf = hdf.dropna(subset=["date"]).sort_values("date")
            if hdf.empty:
                return {}
            row = hdf.iloc[-1]
            return {
                "open": float(row["open"]) if pd.notna(row.get("open")) else None,
                "high": float(row["high"]) if pd.notna(row.get("high")) else None,
                "low": float(row["low"]) if pd.notna(row.get("low")) else None,
                "close": float(row["close"]) if pd.notna(row.get("close")) else None,
            }
        except Exception:
            return {}

    try:
        ib.connect(host, port, clientId=client_id, timeout=timeout, readonly=True)
        try:
            ib.reqMarketDataType(market_data_type)
        except Exception:
            pass

        for symbol in symbols:
            symbol_diag = {
                "symbol": symbol,
                "status": "unknown",
                "requested_contracts": 0,
                "qualified_contracts": 0,
                "rows_inserted": 0,
                "rows_with_oi": 0,
                "rows_with_price": 0,
                "used_spot_fallback": False,
                "spot": None,
                "error": "",
            }
            rows_before_symbol = rows
            try:
                stock = Stock(symbol, "SMART", "USD")
                ib.qualifyContracts(stock)

                tk = ib.reqMktData(stock, "", False, False)
                ib.sleep(0.6)
                spot = tk.last or tk.close or tk.marketPrice()
                try:
                    ib.cancelMktData(stock)
                except Exception:
                    pass
                if spot is None or (isinstance(spot, float) and np.isnan(spot)):
                    try:
                        hb = ib.reqHistoricalData(
                            stock,
                            endDateTime="",
                            durationStr="5 D",
                            barSizeSetting="1 day",
                            whatToShow="TRADES",
                            useRTH=False,
                            formatDate=1,
                            keepUpToDate=False,
                        )
                        if hb:
                            hdf = util.df(hb)
                            if not hdf.empty and "close" in hdf.columns:
                                spot = float(hdf["close"].dropna().iloc[-1])
                                symbol_diag["used_spot_fallback"] = True
                    except Exception:
                        pass
                symbol_diag["spot"] = float(spot) if (spot is not None and pd.notna(spot)) else None

                chains = ib.reqSecDefOptParams(stock.symbol, "", stock.secType, stock.conId)
                if not chains:
                    skipped += 1
                    continue

                chain = chains[0]
                expiries = sorted(list(chain.expirations))[:max_expiries]
                strikes = sorted([float(s) for s in chain.strikes if s is not None])
                if not strikes:
                    skipped += 1
                    continue

                if spot is None or (isinstance(spot, float) and np.isnan(spot)):
                    atm_idx = len(strikes) // 2
                else:
                    atm_idx = min(range(len(strikes)), key=lambda i: abs(strikes[i] - float(spot)))
                left = max(0, atm_idx - strikes_per_side)
                right = min(len(strikes), atm_idx + strikes_per_side + 1)
                selected_strikes = strikes[left:right]

                option_contracts = []
                for exp in expiries:
                    for strike in selected_strikes:
                        option_contracts.append(Option(symbol, exp, strike, "C", "SMART"))
                        option_contracts.append(Option(symbol, exp, strike, "P", "SMART"))
                symbol_diag["requested_contracts"] = len(option_contracts)

                if not option_contracts:
                    skipped += 1
                    continue

                qualified = ib.qualifyContracts(*option_contracts)
                if not qualified:
                    symbol_diag["status"] = "no_qualified_contracts"
                    skipped += 1
                    diagnostics.append(symbol_diag)
                    continue
                symbol_diag["qualified_contracts"] = len(qualified)

                tickers = []
                for oc in qualified:
                    t = ib.reqMktData(oc, "100,101,104,106", False, False)
                    tickers.append((oc, t))
                ib.sleep(2.0)

                compat_map: dict[tuple[str, float], dict] = {}
                cur.execute(
                    "DELETE FROM options_daily WHERE ticker = ? AND trade_date = ?",
                    (symbol, trade_date_mmddyyyy),
                )

                for oc, t in tickers:
                    iv = t.modelGreeks.impliedVol if t.modelGreeks is not None else np.nan
                    delta = t.modelGreeks.delta if t.modelGreeks is not None else np.nan
                    gamma = t.modelGreeks.gamma if t.modelGreeks is not None else np.nan
                    theta = t.modelGreeks.theta if t.modelGreeks is not None else np.nan
                    vega = t.modelGreeks.vega if t.modelGreeks is not None else np.nan
                    oi = _extract_ibkr_open_interest(t)

                    bid_val = float(t.bid) if pd.notna(t.bid) else None
                    ask_val = float(t.ask) if pd.notna(t.ask) else None
                    last_val = float(t.last) if pd.notna(t.last) else None
                    close_val = float(t.close) if pd.notna(t.close) else None
                    vol_val = float(t.volume) if pd.notna(t.volume) else None

                    # If live tick is empty, try historical daily bar as fallback for OHLC/volume.
                    if all(v is None for v in [bid_val, ask_val, last_val, close_val, vol_val]):
                        try:
                            hb_opt = ib.reqHistoricalData(
                                oc,
                                endDateTime="",
                                durationStr="5 D",
                                barSizeSetting="1 day",
                                whatToShow="TRADES",
                                useRTH=False,
                                formatDate=1,
                                keepUpToDate=False,
                            )
                            if hb_opt:
                                hdf_opt = util.df(hb_opt)
                                if not hdf_opt.empty:
                                    hdf_opt["date"] = pd.to_datetime(hdf_opt["date"], errors="coerce")
                                    hdf_opt = hdf_opt.dropna(subset=["date"]).sort_values("date")
                                    if not hdf_opt.empty:
                                        bar = hdf_opt.iloc[-1]
                                        close_val = float(bar.get("close")) if pd.notna(bar.get("close")) else close_val
                                        last_val = last_val if last_val is not None else close_val
                                        vol_val = float(bar.get("volume")) if pd.notna(bar.get("volume")) else vol_val
                        except Exception:
                            pass

                    # Skip contracts where IBKR returned absolutely nothing useful.
                    if all(v is None for v in [bid_val, ask_val, last_val, close_val, vol_val]) and (
                        oi is None or not pd.notna(oi)
                    ):
                        rows_skipped_empty += 1
                        continue

                    if oi is not None and pd.notna(oi):
                        symbol_diag["rows_with_oi"] += 1
                    if any(v is not None for v in [bid_val, ask_val, last_val, close_val]):
                        symbol_diag["rows_with_price"] += 1

                    ohlc = _fetch_option_daily_ohlc(oc)
                    expiry_fmt = _format_expiry_mmddyyyy(str(oc.lastTradeDateOrContractMonth))
                    pair_key = (expiry_fmt, float(oc.strike))
                    if pair_key not in compat_map:
                        compat_map[pair_key] = {
                            "ticker": symbol,
                            "company_name": symbol,
                            "asset_type": "stock",
                            "strike": float(oc.strike),
                            "expiry_date": expiry_fmt,
                            "trade_date": trade_date_mmddyyyy,
                            "openInt_Call": None,
                            "openInt_Put": None,
                            "vol_Call": None,
                            "vol_Put": None,
                            "lastPrice_Call": None,
                            "lastPrice_Put": None,
                            "call_open": None,
                            "call_high": None,
                            "call_low": None,
                            "call_close": None,
                            "put_open": None,
                            "put_high": None,
                            "put_low": None,
                            "put_close": None,
                            "contractSymbol_Call": None,
                            "contractSymbol_Put": None,
                            "load_date": datetime.now().strftime("%m-%d-%Y"),
                            "source": "IBKR",
                        }
                    row_ref = compat_map[pair_key]
                    if str(oc.right) == "C":
                        row_ref["openInt_Call"] = float(oi) if oi is not None and pd.notna(oi) else None
                        row_ref["vol_Call"] = vol_val
                        row_ref["lastPrice_Call"] = last_val
                        row_ref["call_open"] = ohlc.get("open")
                        row_ref["call_high"] = ohlc.get("high")
                        row_ref["call_low"] = ohlc.get("low")
                        row_ref["call_close"] = ohlc.get("close") if ohlc.get("close") is not None else close_val
                        row_ref["contractSymbol_Call"] = getattr(oc, "localSymbol", None) or str(oc)
                    else:
                        row_ref["openInt_Put"] = float(oi) if oi is not None and pd.notna(oi) else None
                        row_ref["vol_Put"] = vol_val
                        row_ref["lastPrice_Put"] = last_val
                        row_ref["put_open"] = ohlc.get("open")
                        row_ref["put_high"] = ohlc.get("high")
                        row_ref["put_low"] = ohlc.get("low")
                        row_ref["put_close"] = ohlc.get("close") if ohlc.get("close") is not None else close_val
                        row_ref["contractSymbol_Put"] = getattr(oc, "localSymbol", None) or str(oc)

                    cur.execute(
                        """
                        INSERT OR REPLACE INTO ibkr_options_daily
                        (snapshot_ts, symbol, expiry, strike, right, bid, ask, last, close, volume,
                         open_interest, iv, delta, gamma, theta, vega, source)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            snapshot_ts,
                            symbol,
                            str(oc.lastTradeDateOrContractMonth),
                            float(oc.strike),
                            str(oc.right),
                            bid_val,
                            ask_val,
                            last_val,
                            close_val,
                            vol_val,
                            float(oi) if oi is not None else None,
                            float(iv) if pd.notna(iv) else None,
                            float(delta) if pd.notna(delta) else None,
                            float(gamma) if pd.notna(gamma) else None,
                            float(theta) if pd.notna(theta) else None,
                            float(vega) if pd.notna(vega) else None,
                            "IBKR",
                        ),
                    )
                    rows += 1

                for row_ref in compat_map.values():
                    has_any_market_data = any(
                        row_ref.get(k) is not None
                        for k in [
                            "openInt_Call",
                            "openInt_Put",
                            "vol_Call",
                            "vol_Put",
                            "lastPrice_Call",
                            "lastPrice_Put",
                            "call_open",
                            "call_high",
                            "call_low",
                            "call_close",
                            "put_open",
                            "put_high",
                            "put_low",
                            "put_close",
                        ]
                    )
                    if not has_any_market_data:
                        continue
                    cur.execute(
                        """
                        INSERT OR REPLACE INTO options_daily
                        (ticker, company_name, asset_type, strike, expiry_date, trade_date,
                         openInt_Call, openInt_Put, vol_Call, vol_Put, lastPrice_Call, lastPrice_Put,
                         call_open, call_high, call_low, call_close,
                         put_open, put_high, put_low, put_close,
                         contractSymbol_Call, contractSymbol_Put, load_date, source)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            row_ref["ticker"],
                            row_ref["company_name"],
                            row_ref["asset_type"],
                            row_ref["strike"],
                            row_ref["expiry_date"],
                            row_ref["trade_date"],
                            row_ref["openInt_Call"],
                            row_ref["openInt_Put"],
                            row_ref["vol_Call"],
                            row_ref["vol_Put"],
                            row_ref["lastPrice_Call"],
                            row_ref["lastPrice_Put"],
                            row_ref["call_open"],
                            row_ref["call_high"],
                            row_ref["call_low"],
                            row_ref["call_close"],
                            row_ref["put_open"],
                            row_ref["put_high"],
                            row_ref["put_low"],
                            row_ref["put_close"],
                            row_ref["contractSymbol_Call"],
                            row_ref["contractSymbol_Put"],
                            row_ref["load_date"],
                            row_ref["source"],
                        ),
                    )
                    compat_rows += 1

                for oc, _ in tickers:
                    try:
                        ib.cancelMktData(oc)
                    except Exception:
                        pass

                loaded_symbols += 1
                symbol_diag["rows_inserted"] = rows - rows_before_symbol
                symbol_diag["status"] = "ok"
                diagnostics.append(symbol_diag)
                conn.commit()
            except Exception as exc:
                errors.append(f"{symbol}: {type(exc).__name__}: {exc}")
                symbol_diag["status"] = "error"
                symbol_diag["error"] = f"{type(exc).__name__}: {exc}"
                diagnostics.append(symbol_diag)
                skipped += 1

        change_rows = compute_ibkr_options_change(db_path, snapshot_ts)
        compat_change_rows = compute_options_change_compat(db_path, trade_date_mmddyyyy)
        conn.commit()
    except Exception as e:
        conn.rollback()
        return {
            "ok": False,
            "reason": str(e),
            "rows": rows,
            "symbols_loaded": loaded_symbols,
            "skipped": skipped,
            "errors": errors[:20],
            "diagnostics": diagnostics,
            "compat_rows": compat_rows,
            "rows_skipped_empty": rows_skipped_empty,
        }
    finally:
        conn.close()
        try:
            if ib.isConnected():
                ib.disconnect()
        except Exception:
            pass

    return {
        "ok": True,
        "snapshot_ts": snapshot_ts,
        "rows": rows,
        "symbols_loaded": loaded_symbols,
        "skipped": skipped,
        "change_rows": change_rows,
        "compat_rows": compat_rows,
        "compat_change_rows": compat_change_rows,
        "rows_skipped_empty": rows_skipped_empty,
        "errors": errors[:20],
        "diagnostics": diagnostics,
    }


def load_latest_options_snapshot(db_path: str, symbol: str) -> pd.DataFrame:
    if not _table_exists(db_path, "ibkr_options_daily"):
        return pd.DataFrame()
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute(
        """
        SELECT MAX(snapshot_ts)
        FROM ibkr_options_daily
        WHERE symbol = ?
        """,
        (symbol,),
    )
    latest = cur.fetchone()[0]
    if not latest:
        conn.close()
        return pd.DataFrame()
    df = pd.read_sql(
        """
        SELECT * FROM ibkr_options_daily
        WHERE symbol = ? AND snapshot_ts = ?
        ORDER BY expiry, strike, right
        """,
        conn,
        params=(symbol, latest),
    )
    conn.close()
    return df


def load_latest_options_change(db_path: str, symbol: str, expiry: str) -> pd.DataFrame:
    if not _table_exists(db_path, "ibkr_options_change"):
        return pd.DataFrame()
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute(
        """
        SELECT MAX(snapshot_ts)
        FROM ibkr_options_change
        WHERE symbol = ? AND expiry = ?
        """,
        (symbol, expiry),
    )
    latest = cur.fetchone()[0]
    if not latest:
        conn.close()
        return pd.DataFrame()
    df = pd.read_sql(
        """
        SELECT * FROM ibkr_options_change
        WHERE symbol = ? AND expiry = ? AND snapshot_ts = ?
        ORDER BY strike, right
        """,
        conn,
        params=(symbol, expiry, latest),
    )
    conn.close()
    return df


def build_options_telegram_style_chart(symbol: str, expiry: str, change_df: pd.DataFrame) -> go.Figure | None:
    if change_df.empty:
        return None

    df = change_df.copy()
    for col in [
        "strike",
        "prev_open_interest",
        "curr_open_interest",
        "prev_close",
        "curr_close",
    ]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    df = df.dropna(subset=["strike", "right"])
    if df.empty:
        return None

    call_df = df[df["right"] == "C"].copy()
    put_df = df[df["right"] == "P"].copy()

    merged = pd.merge(
        call_df[["strike", "prev_open_interest", "curr_open_interest", "prev_close", "curr_close"]].rename(
            columns={
                "prev_open_interest": "call_oi_prev",
                "curr_open_interest": "call_oi_now",
                "prev_close": "call_px_prev",
                "curr_close": "call_px_now",
            }
        ),
        put_df[["strike", "prev_open_interest", "curr_open_interest", "prev_close", "curr_close"]].rename(
            columns={
                "prev_open_interest": "put_oi_prev",
                "curr_open_interest": "put_oi_now",
                "prev_close": "put_px_prev",
                "curr_close": "put_px_now",
            }
        ),
        on="strike",
        how="outer",
    ).sort_values("strike")

    if merged.empty:
        return None

    for c in ["call_oi_prev", "call_oi_now", "put_oi_prev", "put_oi_now", "call_px_prev", "call_px_now", "put_px_prev", "put_px_now"]:
        if c in merged.columns:
            merged[c] = merged[c].fillna(0.0)

    merged["put_oi_prev_plot"] = -merged["put_oi_prev"]
    merged["put_oi_now_plot"] = -merged["put_oi_now"]
    merged["put_px_prev_plot"] = -merged["put_px_prev"]
    merged["put_px_now_plot"] = -merged["put_px_now"]

    fig = make_subplots(
        rows=1,
        cols=2,
        horizontal_spacing=0.08,
        subplot_titles=(
            f"{symbol} {expiry} - OI (Prev vs Now)",
            f"{symbol} {expiry} - Option Price (Prev vs Now)",
        ),
    )

    fig.add_trace(go.Bar(x=merged["strike"], y=merged["call_oi_prev"], name="Call OI Prev", marker_color="#2ca02c", opacity=0.65), row=1, col=1)
    fig.add_trace(go.Bar(x=merged["strike"], y=merged["put_oi_prev_plot"], name="Put OI Prev", marker_color="#d62728", opacity=0.65), row=1, col=1)
    fig.add_trace(go.Bar(x=merged["strike"], y=merged["call_oi_now"], name="Call OI Now", marker_color="white", marker_line_color="#1f7a1f", marker_line_width=1.1, width=0.35), row=1, col=1)
    fig.add_trace(go.Bar(x=merged["strike"], y=merged["put_oi_now_plot"], name="Put OI Now", marker_color="white", marker_line_color="#a81b1b", marker_line_width=1.1, width=0.35), row=1, col=1)

    fig.add_trace(go.Scatter(x=merged["strike"], y=merged["call_px_prev"], mode="lines+markers", name="Call Px Prev", line=dict(color="#2ca02c", width=2, dash="dot")), row=1, col=2)
    fig.add_trace(go.Scatter(x=merged["strike"], y=merged["put_px_prev_plot"], mode="lines+markers", name="Put Px Prev", line=dict(color="#d62728", width=2, dash="dot")), row=1, col=2)
    fig.add_trace(go.Scatter(x=merged["strike"], y=merged["call_px_now"], mode="lines+markers", name="Call Px Now", line=dict(color="#1f7a1f", width=2.3)), row=1, col=2)
    fig.add_trace(go.Scatter(x=merged["strike"], y=merged["put_px_now_plot"], mode="lines+markers", name="Put Px Now", line=dict(color="#a81b1b", width=2.3)), row=1, col=2)

    fig.add_hline(y=0, line_dash="dash", line_color="gray", row=1, col=1)
    fig.add_hline(y=0, line_dash="dash", line_color="gray", row=1, col=2)

    fig.update_layout(height=560, barmode="overlay", legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1))
    fig.update_xaxes(title_text="Strike", row=1, col=1)
    fig.update_xaxes(title_text="Strike", row=1, col=2)
    fig.update_yaxes(title_text="OI (+Call / -Put)", row=1, col=1)
    fig.update_yaxes(title_text="Price (+Call / -Put)", row=1, col=2)

    return fig


def persist_live_oi_snapshot(db_path: str, oi_snapshot: pd.DataFrame, source: str = "IBKR") -> int:
    init_ibkr_backfill_tables(db_path)
    if oi_snapshot.empty:
        return 0

    now_ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = oi_snapshot.copy()
    rows = rows.rename(columns={"Symbol": "symbol", "OI": "open_interest"})
    if "symbol" not in rows.columns or "open_interest" not in rows.columns:
        return 0

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    inserted = 0
    try:
        for _, r in rows.iterrows():
            cur.execute(
                """
                INSERT OR REPLACE INTO ibkr_live_oi_snapshot
                (snapshot_ts, symbol, open_interest, source)
                VALUES (?, ?, ?, ?)
                """,
                (
                    now_ts,
                    str(r.get("symbol")),
                    float(r.get("open_interest")) if pd.notna(r.get("open_interest")) else None,
                    source,
                ),
            )
            inserted += 1
        conn.commit()
    finally:
        conn.close()
    return inserted


def load_live_oi_compare_points(db_path: str, symbol: str) -> dict:
    if not _table_exists(db_path, "ibkr_live_oi_snapshot"):
        return {}

    conn = sqlite3.connect(db_path)
    df = pd.read_sql(
        """
        SELECT snapshot_ts, symbol, open_interest
        FROM ibkr_live_oi_snapshot
        WHERE symbol = ?
        ORDER BY snapshot_ts ASC
        """,
        conn,
        params=(symbol,),
    )
    conn.close()

    if df.empty:
        return {}

    df["snapshot_ts"] = pd.to_datetime(df["snapshot_ts"], errors="coerce", utc=True)
    df = df.dropna(subset=["snapshot_ts"]).sort_values("snapshot_ts")
    if df.empty:
        return {}

    now_utc = pd.Timestamp.now(tz="UTC")
    today = now_utc.date()
    df_today = df[df["snapshot_ts"].dt.date == today].copy()
    if df_today.empty:
        df_today = df.copy()

    start_row = df_today.iloc[0]
    current_row = df_today.iloc[-1]

    t15 = now_utc - pd.Timedelta(minutes=15)
    df_pre15 = df_today[df_today["snapshot_ts"] <= t15]
    pre15_row = df_pre15.iloc[-1] if not df_pre15.empty else None

    start_oi = float(start_row["open_interest"]) if pd.notna(start_row["open_interest"]) else np.nan
    current_oi = float(current_row["open_interest"]) if pd.notna(current_row["open_interest"]) else np.nan
    pre15_oi = float(pre15_row["open_interest"]) if pre15_row is not None and pd.notna(pre15_row["open_interest"]) else np.nan

    def _pct(curr, base):
        if pd.isna(curr) or pd.isna(base) or base == 0:
            return np.nan
        return (curr - base) / base * 100

    return {
        "start_ts": start_row["snapshot_ts"],
        "pre15_ts": pre15_row["snapshot_ts"] if pre15_row is not None else pd.NaT,
        "current_ts": current_row["snapshot_ts"],
        "start_oi": start_oi,
        "pre15_oi": pre15_oi,
        "current_oi": current_oi,
        "chg_from_start": current_oi - start_oi if pd.notna(start_oi) and pd.notna(current_oi) else np.nan,
        "chg_from_start_pct": _pct(current_oi, start_oi),
        "chg_vs_pre15": current_oi - pre15_oi if pd.notna(pre15_oi) and pd.notna(current_oi) else np.nan,
        "chg_vs_pre15_pct": _pct(current_oi, pre15_oi),
        "rows_today": int(df_today.shape[0]),
    }


def _lookback_days_to_duration_str(lookback_days: int) -> str:
    if lookback_days <= 365:
        return f"{lookback_days} D"
    years = int(np.ceil(lookback_days / 365))
    return f"{years} Y"


def _period_to_days(period: str) -> int:
    return {
        "1mo": 31,
        "3mo": 93,
        "6mo": 186,
        "1y": 366,
        "2y": 732,
    }.get(period, 186)


def load_price_matrix_from_ibkr_db(
    db_path: str,
    symbols: list[str],
    period: str,
    interval: str,
) -> pd.DataFrame:
    if not _table_exists(db_path, "ibkr_stock_daily"):
        return pd.DataFrame()

    symbols = sorted(set([s for s in symbols if s]))
    if not symbols:
        return pd.DataFrame()

    since_dt = (datetime.now(timezone.utc) - pd.Timedelta(days=_period_to_days(period))).strftime("%Y-%m-%d")
    placeholders = ",".join(["?"] * len(symbols))

    conn = sqlite3.connect(db_path)
    df = pd.read_sql(
        f"""
        SELECT symbol, trade_date, close
        FROM ibkr_stock_daily
        WHERE symbol IN ({placeholders})
          AND trade_date >= ?
        ORDER BY trade_date ASC
        """,
        conn,
        params=(*symbols, since_dt),
    )
    conn.close()

    if df.empty:
        return pd.DataFrame()

    df["trade_date"] = pd.to_datetime(df["trade_date"], errors="coerce")
    df = df.dropna(subset=["trade_date", "close"])
    if df.empty:
        return pd.DataFrame()

    piv = df.pivot_table(index="trade_date", columns="symbol", values="close", aggfunc="last").sort_index()
    if interval == "1wk":
        piv = piv.resample("W-FRI").last()
    piv = piv.dropna(how="all")
    return piv


def load_latest_oi_from_db(db_path: str, symbols: list[str]) -> pd.DataFrame:
    if not _table_exists(db_path, "ibkr_live_oi_snapshot"):
        return pd.DataFrame(columns=["Symbol", "OI"])
    symbols = sorted(set([s for s in symbols if s]))
    if not symbols:
        return pd.DataFrame(columns=["Symbol", "OI"])

    placeholders = ",".join(["?"] * len(symbols))
    conn = sqlite3.connect(db_path)
    df = pd.read_sql(
        f"""
        WITH latest_ts AS (
            SELECT symbol, MAX(snapshot_ts) AS max_ts
            FROM ibkr_live_oi_snapshot
            WHERE symbol IN ({placeholders})
            GROUP BY symbol
        )
        SELECT l.symbol AS Symbol, s.open_interest AS OI
        FROM latest_ts l
        JOIN ibkr_live_oi_snapshot s
          ON s.symbol = l.symbol AND s.snapshot_ts = l.max_ts
        ORDER BY l.symbol
        """,
        conn,
        params=tuple(symbols),
    )
    conn.close()
    return df if not df.empty else pd.DataFrame(columns=["Symbol", "OI"])


def backfill_ibkr_db(
    symbols: list[str],
    db_path: str,
    lookback_days: int,
    host: str,
    port: int,
    client_id: int,
    market_data_type: int,
    timeout: int,
    pull_mode: str = "incremental",
) -> dict:
    init_ibkr_backfill_tables(db_path)

    if not IBKR_AVAILABLE:
        return {"ok": False, "reason": "ib_insync not installed", "symbols_loaded": 0, "rows_written": 0}

    valid_symbols = sorted(set([s for s in symbols if s]))
    if not valid_symbols:
        return {"ok": False, "reason": "no symbols provided", "symbols_loaded": 0, "rows_written": 0}

    ib = IB()
    rows_written = 0
    monthly_rows_written = 0
    oi_written = 0
    symbols_loaded = 0
    skipped = 0
    pull_mode = (pull_mode or "incremental").lower()
    if pull_mode not in {"incremental", "historical"}:
        pull_mode = "incremental"

    duration_str = (
        "10 D"
        if pull_mode == "incremental"
        else _lookback_days_to_duration_str(int(max(5, lookback_days)))
    )
    snapshot_ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    load_ts = snapshot_ts

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    try:
        ib.connect(host, port, clientId=client_id, timeout=timeout, readonly=True)
        try:
            ib.reqMarketDataType(market_data_type)
        except Exception:
            pass

        for symbol in valid_symbols:
            try:
                contract = _symbol_to_ibkr_contract(symbol)
                if contract is None:
                    skipped += 1
                    continue

                ib.qualifyContracts(contract)

                bars = ib.reqHistoricalData(
                    contract,
                    endDateTime="",
                    durationStr=duration_str,
                    barSizeSetting="1 day",
                    whatToShow="TRADES",
                    useRTH=False,
                    formatDate=1,
                    keepUpToDate=False,
                )

                if not bars:
                    skipped += 1
                    continue

                hist = util.df(bars)
                if hist.empty:
                    skipped += 1
                    continue

                hist["trade_date"] = pd.to_datetime(hist.get("date"), errors="coerce")
                hist = hist.dropna(subset=["trade_date"]).sort_values("trade_date")
                if hist.empty:
                    skipped += 1
                    continue

                if pull_mode == "incremental":
                    yesterday_utc = (datetime.now(timezone.utc) - pd.Timedelta(days=1)).date()
                    hist = hist[hist["trade_date"].dt.date <= yesterday_utc]
                    if hist.empty:
                        skipped += 1
                        continue
                    hist = hist.tail(1)

                symbols_loaded += 1

                for _, row in hist.iterrows():
                    dt = pd.to_datetime(row.get("trade_date"), errors="coerce")
                    if pd.isna(dt):
                        continue
                    cur.execute(
                        """
                        INSERT OR REPLACE INTO ibkr_stock_daily
                        (symbol, trade_date, open, high, low, close, volume, source, load_ts)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            symbol,
                            dt.strftime("%Y-%m-%d"),
                            float(row.get("open")) if pd.notna(row.get("open")) else None,
                            float(row.get("high")) if pd.notna(row.get("high")) else None,
                            float(row.get("low")) if pd.notna(row.get("low")) else None,
                            float(row.get("close")) if pd.notna(row.get("close")) else None,
                            float(row.get("volume")) if pd.notna(row.get("volume")) else None,
                            "IBKR",
                            load_ts,
                        ),
                    )
                    rows_written += 1

                latest_daily = hist.sort_values("trade_date").iloc[-1]
                latest_trade_date = pd.to_datetime(latest_daily.get("trade_date"), errors="coerce")
                if pd.notna(latest_trade_date):
                    trade_date_mmddyyyy = latest_trade_date.strftime("%m-%d-%Y")
                    cur.execute(
                        """
                        SELECT
                            COALESCE(SUM(openInt_Put), 0.0),
                            COALESCE(SUM(openInt_Call), 0.0)
                        FROM options_daily
                        WHERE ticker = ? AND trade_date = ?
                        """,
                        (symbol, trade_date_mmddyyyy),
                    )
                    pcr_row = cur.fetchone() or (0.0, 0.0)
                    put_oi_sum = float(pcr_row[0] or 0.0)
                    call_oi_sum = float(pcr_row[1] or 0.0)
                    pcr_oi = (put_oi_sum / call_oi_sum) if call_oi_sum > 0 else None

                    cur.execute(
                        """
                        INSERT OR REPLACE INTO stock_daily
                        (ticker, trade_date, open, high, low, close, volume, pcr_oi, load_date, source)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            symbol,
                            trade_date_mmddyyyy,
                            float(latest_daily.get("open")) if pd.notna(latest_daily.get("open")) else None,
                            float(latest_daily.get("high")) if pd.notna(latest_daily.get("high")) else None,
                            float(latest_daily.get("low")) if pd.notna(latest_daily.get("low")) else None,
                            float(latest_daily.get("close")) if pd.notna(latest_daily.get("close")) else None,
                            float(latest_daily.get("volume")) if pd.notna(latest_daily.get("volume")) else None,
                            pcr_oi,
                            datetime.now().strftime("%m-%d-%Y"),
                            "IBKR",
                        ),
                    )

                touched_months = sorted(set(hist["trade_date"].dt.to_period("M").astype(str).tolist()))
                for month_key in touched_months:
                    month_daily = pd.read_sql(
                        """
                        SELECT trade_date, open, high, low, close, volume
                        FROM ibkr_stock_daily
                        WHERE symbol = ?
                          AND substr(trade_date, 1, 7) = ?
                        ORDER BY trade_date ASC
                        """,
                        conn,
                        params=(symbol, month_key),
                    )
                    if month_daily.empty:
                        continue

                    m_open = pd.to_numeric(month_daily["open"], errors="coerce")
                    m_high = pd.to_numeric(month_daily["high"], errors="coerce")
                    m_low = pd.to_numeric(month_daily["low"], errors="coerce")
                    m_close = pd.to_numeric(month_daily["close"], errors="coerce")
                    m_volume = pd.to_numeric(month_daily["volume"], errors="coerce")

                    open_val = m_open.dropna().iloc[0] if not m_open.dropna().empty else np.nan
                    high_val = m_high.max(skipna=True)
                    low_val = m_low.min(skipna=True)
                    close_val = m_close.dropna().iloc[-1] if not m_close.dropna().empty else np.nan
                    volume_val = m_volume.sum(skipna=True)

                    cur.execute(
                        """
                        INSERT OR REPLACE INTO ibkr_stock_monthly
                        (symbol, month_key, open, high, low, close, volume, source, load_ts)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            symbol,
                            month_key,
                            float(open_val) if pd.notna(open_val) else None,
                            float(high_val) if pd.notna(high_val) else None,
                            float(low_val) if pd.notna(low_val) else None,
                            float(close_val) if pd.notna(close_val) else None,
                            float(volume_val) if pd.notna(volume_val) else None,
                            "IBKR",
                            load_ts,
                        ),
                    )
                    monthly_rows_written += 1

                ticker_obj = ib.reqMktData(contract, "101,588", False, False)
                ib.sleep(0.9)
                oi_val = _extract_ibkr_open_interest(ticker_obj)
                cur.execute(
                    """
                    INSERT OR REPLACE INTO ibkr_oi_snapshot
                    (snapshot_ts, symbol, open_interest, source)
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        snapshot_ts,
                        symbol,
                        float(oi_val) if oi_val is not None else None,
                        "IBKR",
                    ),
                )
                oi_written += 1

                try:
                    ib.cancelMktData(contract)
                except Exception:
                    pass
            except Exception:
                skipped += 1
                continue

        conn.commit()
    except Exception as exc:
        conn.rollback()
        return {
            "ok": False,
            "reason": str(exc),
            "symbols_loaded": symbols_loaded,
            "rows_written": rows_written,
            "monthly_rows_written": monthly_rows_written,
            "oi_written": oi_written,
            "db_path": db_path,
        }
    finally:
        conn.close()
        try:
            if ib.isConnected():
                ib.disconnect()
        except Exception:
            pass

    return {
        "ok": True,
        "symbols_loaded": symbols_loaded,
        "rows_written": rows_written,
        "monthly_rows_written": monthly_rows_written,
        "oi_written": oi_written,
        "skipped": skipped,
        "db_path": db_path,
        "duration": duration_str,
        "mode": pull_mode,
    }


def _table_exists(db_path: str, table_name: str) -> bool:
    if not os.path.exists(db_path):
        return False
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table_name,))
    row = cur.fetchone()
    conn.close()
    return row is not None


def run_ibkr_data_validations(db_path: str) -> dict:
    required_tables = [
        "ibkr_stock_daily",
        "ibkr_stock_monthly",
        "ibkr_oi_snapshot",
        "ibkr_live_oi_snapshot",
        "ibkr_options_daily",
        "ibkr_options_change",
        "options_daily",
        "options_change",
        "stock_daily",
    ]

    if not os.path.exists(db_path):
        return {
            "tables": pd.DataFrame(columns=["table", "exists", "rows"]),
            "issues": pd.DataFrame([{"severity": "critical", "check": "db_exists", "detail": f"DB not found: {db_path}"}]),
            "field_coverage": pd.DataFrame(columns=["table", "field", "rows", "non_null", "non_null_pct"]),
        }

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    table_rows = []
    issues = []
    field_rows = []

    def _table_count(table_name: str) -> int:
        cur.execute(f"SELECT COUNT(*) FROM {table_name}")
        return int(cur.fetchone()[0])

    def _nonnull(table_name: str, column_name: str) -> int:
        cur.execute(f"SELECT COUNT(*) FROM {table_name} WHERE {column_name} IS NOT NULL")
        return int(cur.fetchone()[0])

    for table_name in required_tables:
        exists = _table_exists(db_path, table_name)
        row_count = _table_count(table_name) if exists else 0
        table_rows.append({"table": table_name, "exists": bool(exists), "rows": int(row_count)})
        if not exists:
            issues.append(
                {
                    "severity": "critical",
                    "check": "table_exists",
                    "detail": f"Missing table: {table_name}",
                }
            )

    # Field-level coverage checks for critical data columns.
    coverage_targets = {
        "ibkr_options_daily": ["bid", "ask", "last", "close", "volume", "open_interest", "iv", "delta", "gamma", "theta", "vega"],
        "options_daily": ["openInt_Call", "openInt_Put", "vol_Call", "vol_Put", "lastPrice_Call", "lastPrice_Put", "call_close", "put_close"],
        "ibkr_oi_snapshot": ["open_interest"],
        "ibkr_live_oi_snapshot": ["open_interest"],
        "stock_daily": ["pcr_oi"],
    }

    for table_name, columns in coverage_targets.items():
        if not _table_exists(db_path, table_name):
            continue
        rows = _table_count(table_name)
        for column_name in columns:
            non_null = _nonnull(table_name, column_name) if rows > 0 else 0
            pct = (non_null / rows * 100.0) if rows > 0 else 0.0
            field_rows.append(
                {
                    "table": table_name,
                    "field": column_name,
                    "rows": rows,
                    "non_null": non_null,
                    "non_null_pct": round(pct, 2),
                }
            )

    # Rule checks.
    if _table_exists(db_path, "ibkr_options_daily"):
        opt_rows = _table_count("ibkr_options_daily")
        if opt_rows > 0:
            price_non_null = 0
            for c in ["bid", "ask", "last", "close", "volume", "open_interest"]:
                price_non_null += _nonnull("ibkr_options_daily", c)
            if price_non_null == 0:
                issues.append(
                    {
                        "severity": "critical",
                        "check": "options_market_fields",
                        "detail": "ibkr_options_daily has rows but all option market fields are null (likely entitlement/subscription issue).",
                    }
                )

    if _table_exists(db_path, "options_daily"):
        compat_rows = _table_count("options_daily")
        if compat_rows == 0:
            issues.append(
                {
                    "severity": "warning",
                    "check": "compat_options_rows",
                    "detail": "options_daily is empty.",
                }
            )

    if _table_exists(db_path, "stock_daily"):
        stock_rows = _table_count("stock_daily")
        if stock_rows > 0 and _nonnull("stock_daily", "pcr_oi") == 0:
            issues.append(
                {
                    "severity": "warning",
                    "check": "stock_pcr_oi",
                    "detail": "stock_daily has no pcr_oi values.",
                }
            )

    if _table_exists(db_path, "ibkr_stock_daily"):
        cur.execute("SELECT MAX(trade_date) FROM ibkr_stock_daily")
        max_trade_date = cur.fetchone()[0]
        if max_trade_date:
            max_dt = pd.to_datetime(max_trade_date, errors="coerce")
            if pd.notna(max_dt):
                age_days = (pd.Timestamp.now('UTC').normalize() - max_dt.normalize()).days
                if age_days > 2:
                    issues.append(
                        {
                            "severity": "warning",
                            "check": "stale_stock_data",
                            "detail": f"ibkr_stock_daily appears stale by {age_days} days.",
                        }
                    )

    conn.close()
    return {
        "tables": pd.DataFrame(table_rows),
        "issues": pd.DataFrame(issues),
        "field_coverage": pd.DataFrame(field_rows),
    }


def load_ibkr_oi_history(db_path: str) -> pd.DataFrame:
    if not _table_exists(db_path, "ibkr_oi_snapshot"):
        return pd.DataFrame(columns=["snapshot_ts", "symbol", "open_interest", "source"])
    conn = sqlite3.connect(db_path)
    df = pd.read_sql(
        """
        SELECT snapshot_ts, symbol, open_interest, source
        FROM ibkr_oi_snapshot
        ORDER BY snapshot_ts ASC, symbol ASC
        """,
        conn,
    )
    conn.close()
    if df.empty:
        return df
    df["snapshot_ts"] = pd.to_datetime(df["snapshot_ts"], errors="coerce")
    df = df.dropna(subset=["snapshot_ts"]).reset_index(drop=True)
    return df


def load_ibkr_close_history(db_path: str, symbol: str) -> pd.DataFrame:
    if not _table_exists(db_path, "ibkr_stock_daily"):
        return pd.DataFrame(columns=["trade_date", "close"])
    conn = sqlite3.connect(db_path)
    df = pd.read_sql(
        """
        SELECT trade_date, close
        FROM ibkr_stock_daily
        WHERE symbol = ?
        ORDER BY trade_date ASC
        """,
        conn,
        params=(symbol,),
    )
    conn.close()
    if df.empty:
        return df
    df["trade_date"] = pd.to_datetime(df["trade_date"], errors="coerce")
    df = df.dropna(subset=["trade_date"])
    return df


def load_ibkr_monthly_history(db_path: str, symbol: str) -> pd.DataFrame:
    if not _table_exists(db_path, "ibkr_stock_monthly"):
        return pd.DataFrame(columns=["month_key", "open", "high", "low", "close", "volume"])
    conn = sqlite3.connect(db_path)
    df = pd.read_sql(
        """
        SELECT month_key, open, high, low, close, volume
        FROM ibkr_stock_monthly
        WHERE symbol = ?
        ORDER BY month_key ASC
        """,
        conn,
        params=(symbol,),
    )
    conn.close()
    return df


def summarize_significant_oi_changes(
    oi_hist: pd.DataFrame,
    min_pct_change: float,
    min_abs_change: float,
) -> pd.DataFrame:
    if oi_hist.empty:
        return pd.DataFrame()

    df = oi_hist.copy()
    df = df.dropna(subset=["snapshot_ts", "symbol", "open_interest"])
    if df.empty:
        return pd.DataFrame()

    df["week_start"] = df["snapshot_ts"].dt.to_period("W-MON").dt.start_time

    latest_row = df.sort_values("snapshot_ts").iloc[-1]
    current_week = latest_row["week_start"]
    previous_week = current_week - pd.Timedelta(days=7)

    def _week_first_last(sub: pd.DataFrame, week_value: pd.Timestamp) -> pd.DataFrame:
        wk = sub[sub["week_start"] == week_value].sort_values("snapshot_ts")
        if wk.empty:
            return pd.DataFrame(columns=["symbol", "first_oi", "last_oi"])
        return wk.groupby("symbol", as_index=False).agg(
            first_oi=("open_interest", "first"),
            last_oi=("open_interest", "last"),
        )

    cur = _week_first_last(df, current_week)
    prev = _week_first_last(df, previous_week)

    cur = cur.rename(columns={"first_oi": "cur_week_first_oi", "last_oi": "cur_week_last_oi"})
    prev = prev.rename(columns={"last_oi": "prev_week_last_oi"})[["symbol", "prev_week_last_oi"]]

    merged = cur.merge(prev, on="symbol", how="left")
    if merged.empty:
        return pd.DataFrame()

    merged["intraweek_abs_change"] = merged["cur_week_last_oi"] - merged["cur_week_first_oi"]
    merged["intraweek_pct_change"] = (
        merged["intraweek_abs_change"] / merged["cur_week_first_oi"].replace(0, np.nan)
    ) * 100

    merged["wow_abs_change"] = merged["cur_week_last_oi"] - merged["prev_week_last_oi"]
    merged["wow_pct_change"] = (
        merged["wow_abs_change"] / merged["prev_week_last_oi"].replace(0, np.nan)
    ) * 100

    merged["sig_intraweek"] = (
        merged["intraweek_pct_change"].abs() >= float(min_pct_change)
    ) & (merged["intraweek_abs_change"].abs() >= float(min_abs_change))

    merged["sig_wow"] = (
        merged["wow_pct_change"].abs() >= float(min_pct_change)
    ) & (merged["wow_abs_change"].abs() >= float(min_abs_change))

    merged["signal_strength"] = (
        merged[["intraweek_pct_change", "wow_pct_change"]].abs().max(axis=1)
    )

    merged["signal_label"] = np.where(
        merged["sig_intraweek"] & merged["sig_wow"],
        "Both",
        np.where(merged["sig_intraweek"], "Intraweek", np.where(merged["sig_wow"], "WoW", "None")),
    )

    out = merged[
        (merged["sig_intraweek"]) | (merged["sig_wow"])
    ].copy()

    if out.empty:
        return out

    out = out.sort_values("signal_strength", ascending=False)
    return out[
        [
            "symbol",
            "cur_week_first_oi",
            "cur_week_last_oi",
            "intraweek_abs_change",
            "intraweek_pct_change",
            "prev_week_last_oi",
            "wow_abs_change",
            "wow_pct_change",
            "signal_label",
        ]
    ]


def compute_perf_table(closes: pd.DataFrame) -> pd.DataFrame:
    if closes.empty:
        return pd.DataFrame(columns=["Symbol", "Last", "1D%", "1W%", "1M%", "YTD%"])

    out = []
    for symbol in closes.columns:
        s = closes[symbol].dropna()
        if len(s) < 3:
            continue

        last = float(s.iloc[-1])
        d1 = (s.iloc[-1] / s.iloc[-2] - 1) * 100 if len(s) >= 2 else np.nan
        w1 = (s.iloc[-1] / s.iloc[-6] - 1) * 100 if len(s) >= 6 else np.nan
        m1 = (s.iloc[-1] / s.iloc[-22] - 1) * 100 if len(s) >= 22 else np.nan

        this_year = s[s.index.year == datetime.now().year]
        if len(this_year) >= 2:
            ytd = (this_year.iloc[-1] / this_year.iloc[0] - 1) * 100
        else:
            ytd = np.nan

        out.append(
            {
                "Symbol": symbol,
                "Last": round(last, 3),
                "1D%": round(d1, 3) if pd.notna(d1) else np.nan,
                "1W%": round(w1, 3) if pd.notna(w1) else np.nan,
                "1M%": round(m1, 3) if pd.notna(m1) else np.nan,
                "YTD%": round(ytd, 3) if pd.notna(ytd) else np.nan,
            }
        )
    return pd.DataFrame(out)


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    up = delta.clip(lower=0)
    down = -1 * delta.clip(upper=0)
    avg_up = up.ewm(alpha=1 / period, adjust=False).mean()
    avg_down = down.ewm(alpha=1 / period, adjust=False).mean()
    rs = avg_up / avg_down.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


@st.cache_data(ttl=300, show_spinner=False)
def fetch_rss(url: str, limit: int = 10) -> pd.DataFrame:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0 (compatible; MacroTerminal/1.0)"},
    )
    try:
        with urllib.request.urlopen(req, timeout=12) as response:
            raw = response.read()
    except Exception:
        return pd.DataFrame(columns=["title", "link", "published"])

    try:
        root = ET.fromstring(raw)
    except Exception:
        return pd.DataFrame(columns=["title", "link", "published"])

    items = []
    for node in root.findall(".//item")[:limit]:
        title = node.findtext("title", default="")
        link = node.findtext("link", default="")
        published = node.findtext("pubDate", default="")
        items.append({"title": title, "link": link, "published": published})

    if not items:
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        for entry in root.findall(".//atom:entry", ns)[:limit]:
            title = entry.findtext("atom:title", default="", namespaces=ns)
            link_node = entry.find("atom:link", ns)
            link = link_node.attrib.get("href", "") if link_node is not None else ""
            published = entry.findtext("atom:updated", default="", namespaces=ns)
            items.append({"title": title, "link": link, "published": published})

    return pd.DataFrame(items)


@st.cache_data(ttl=900, show_spinner=False)
def fetch_open_source_repos() -> pd.DataFrame:
    url = "https://api.github.com/search/repositories?q=trading+OR+quant+OR+market+dashboard+language:python&sort=stars&order=desc&per_page=12"
    req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json", "User-Agent": "MacroTerminal/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=12) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception:
        return pd.DataFrame(columns=["name", "stars", "updated", "url", "description"])

    rows = []
    for item in payload.get("items", []):
        rows.append(
            {
                "name": item.get("full_name", ""),
                "stars": item.get("stargazers_count", 0),
                "updated": item.get("updated_at", ""),
                "url": item.get("html_url", ""),
                "description": item.get("description", "") or "",
            }
        )
    return pd.DataFrame(rows)


def init_sqlite(db_path: str) -> None:
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS market_snapshots (
            snapshot_ts TEXT NOT NULL,
            symbol TEXT NOT NULL,
            last REAL,
            d1_pct REAL,
            w1_pct REAL,
            m1_pct REAL,
            ytd_pct REAL,
            PRIMARY KEY (snapshot_ts, symbol)
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS news_snapshots (
            snapshot_ts TEXT NOT NULL,
            source TEXT,
            title TEXT,
            published TEXT,
            link TEXT
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS trade_checks (
            snapshot_ts TEXT NOT NULL,
            symbol TEXT NOT NULL,
            benchmark TEXT,
            stance TEXT,
            score INTEGER,
            ret20 REAL,
            rsi14 REAL,
            beta REAL,
            rel20_vs_bench REAL,
            vol20 REAL,
            vix REAL,
            dxy REAL,
            PRIMARY KEY (snapshot_ts, symbol, benchmark)
        )
        """
    )
    conn.commit()
    conn.close()


def save_market_snapshot(db_path: str, snapshot_ts: str, perf_table: pd.DataFrame) -> int:
    if perf_table.empty:
        return 0
    rows = perf_table[["Symbol", "Last", "1D%", "1W%", "1M%", "YTD%"]].copy()
    rows.columns = ["symbol", "last", "d1_pct", "w1_pct", "m1_pct", "ytd_pct"]

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.executemany(
        """
        INSERT OR REPLACE INTO market_snapshots
        (snapshot_ts, symbol, last, d1_pct, w1_pct, m1_pct, ytd_pct)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                snapshot_ts,
                str(r["symbol"]),
                float(r["last"]) if pd.notna(r["last"]) else None,
                float(r["d1_pct"]) if pd.notna(r["d1_pct"]) else None,
                float(r["w1_pct"]) if pd.notna(r["w1_pct"]) else None,
                float(r["m1_pct"]) if pd.notna(r["m1_pct"]) else None,
                float(r["ytd_pct"]) if pd.notna(r["ytd_pct"]) else None,
            )
            for _, r in rows.iterrows()
        ],
    )
    conn.commit()
    affected = cur.rowcount
    conn.close()
    return int(affected)


def save_news_snapshot(db_path: str, snapshot_ts: str, news_df: pd.DataFrame) -> int:
    if news_df.empty:
        return 0
    rows = news_df[["Source", "title", "published", "link"]].fillna("")

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.executemany(
        """
        INSERT INTO news_snapshots
        (snapshot_ts, source, title, published, link)
        VALUES (?, ?, ?, ?, ?)
        """,
        [
            (snapshot_ts, str(r["Source"]), str(r["title"]), str(r["published"]), str(r["link"]))
            for _, r in rows.iterrows()
        ],
    )
    conn.commit()
    affected = cur.rowcount
    conn.close()
    return int(affected)


def save_trade_check_snapshot(db_path: str, snapshot_ts: str, symbol: str, benchmark: str, result: dict) -> int:
    if not result.get("ok", False):
        return 0
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute(
        """
        INSERT OR REPLACE INTO trade_checks
        (snapshot_ts, symbol, benchmark, stance, score, ret20, rsi14, beta, rel20_vs_bench, vol20, vix, dxy)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            snapshot_ts,
            symbol,
            benchmark,
            result.get("stance"),
            int(result.get("score")) if result.get("score") is not None else None,
            float(result.get("ret20")) if pd.notna(result.get("ret20")) else None,
            float(result.get("rsi14")) if pd.notna(result.get("rsi14")) else None,
            float(result.get("beta")) if pd.notna(result.get("beta")) else None,
            float(result.get("rel20_vs_bench")) if pd.notna(result.get("rel20_vs_bench")) else None,
            float(result.get("vol20")) if pd.notna(result.get("vol20")) else None,
            float(result.get("vix")) if pd.notna(result.get("vix")) else None,
            float(result.get("dxy")) if pd.notna(result.get("dxy")) else None,
        ),
    )
    conn.commit()
    affected = cur.rowcount
    conn.close()
    return int(affected)


def build_trade_check(
    symbol: str,
    benchmark: str,
    lookback: str = "1y",
    source_mode: str = "Yahoo",
    ibkr_host: str = "127.0.0.1",
    ibkr_port: int = 7497,
    ibkr_client_id: int = 91,
    ibkr_market_data_type: int = 3,
    ibkr_timeout: int = 8,
) -> dict:
    data, _ = get_price_matrix(
        [symbol, benchmark, "^VIX", "DX-Y.NYB"],
        period=lookback,
        interval="1d",
        source_mode=source_mode,
        ibkr_host=ibkr_host,
        ibkr_port=ibkr_port,
        ibkr_client_id=ibkr_client_id,
        ibkr_market_data_type=ibkr_market_data_type,
        ibkr_timeout=ibkr_timeout,
    )
    if data.empty or symbol not in data.columns:
        return {"ok": False, "reason": "No price history for symbol."}

    px = data[symbol].dropna()
    if len(px) < 60:
        return {"ok": False, "reason": "Not enough history for robust trade-check."}

    sma20 = px.rolling(20).mean().iloc[-1]
    sma50 = px.rolling(50).mean().iloc[-1]
    ret20 = (px.iloc[-1] / px.iloc[-21] - 1) * 100 if len(px) > 21 else np.nan
    vol20 = px.pct_change().rolling(20).std().iloc[-1] * np.sqrt(252) * 100
    rsi14 = rsi(px, 14).iloc[-1]

    if benchmark in data.columns:
        joined = pd.concat([px.pct_change(), data[benchmark].pct_change()], axis=1).dropna()
        joined.columns = ["asset", "bench"]
        beta = (joined["asset"].cov(joined["bench"]) / joined["bench"].var()) if len(joined) > 30 else np.nan
        rel_20 = (
            (px.iloc[-1] / px.iloc[-21] - 1)
            - (data[benchmark].dropna().iloc[-1] / data[benchmark].dropna().iloc[-21] - 1)
        ) * 100 if len(data[benchmark].dropna()) > 21 else np.nan
    else:
        beta = np.nan
        rel_20 = np.nan

    vix_level = float(data["^VIX"].dropna().iloc[-1]) if "^VIX" in data.columns and not data["^VIX"].dropna().empty else np.nan
    dxy_level = float(data["DX-Y.NYB"].dropna().iloc[-1]) if "DX-Y.NYB" in data.columns and not data["DX-Y.NYB"].dropna().empty else np.nan

    score = 50
    score += 15 if px.iloc[-1] > sma20 else -10
    score += 15 if sma20 > sma50 else -10
    score += 10 if pd.notna(ret20) and ret20 > 0 else -10
    score += 8 if pd.notna(rel_20) and rel_20 > 0 else -5
    score += 6 if pd.notna(rsi14) and 45 <= rsi14 <= 70 else -4
    score += 6 if pd.notna(vol20) and vol20 < 45 else -5
    score = int(max(0, min(100, score)))

    if score >= 70:
        stance = "BULLISH"
    elif score >= 45:
        stance = "NEUTRAL"
    else:
        stance = "RISK-OFF"

    return {
        "ok": True,
        "price": float(px.iloc[-1]),
        "sma20": float(sma20),
        "sma50": float(sma50),
        "ret20": float(ret20) if pd.notna(ret20) else np.nan,
        "vol20": float(vol20) if pd.notna(vol20) else np.nan,
        "rsi14": float(rsi14) if pd.notna(rsi14) else np.nan,
        "beta": float(beta) if pd.notna(beta) else np.nan,
        "rel20_vs_bench": float(rel_20) if pd.notna(rel_20) else np.nan,
        "vix": vix_level,
        "dxy": dxy_level,
        "score": score,
        "stance": stance,
    }


def summarize_trade_check(result: dict) -> str:
    if not result.get("ok"):
        return "Trade-check is unavailable because there is not enough valid market data."

    stance = result.get("stance", "UNKNOWN")
    score = result.get("score", 0)
    ret20 = result.get("ret20")
    rel20 = result.get("rel20_vs_bench")
    rsi14 = result.get("rsi14")
    vol20 = result.get("vol20")

    if stance == "RISK-OFF":
        stance_msg = "Risk appetite is low for this symbol right now."
    elif stance == "NEUTRAL":
        stance_msg = "Setup is mixed, without strong directional conviction."
    else:
        stance_msg = "Momentum and structure currently support a risk-on posture."

    trend_msg = ""
    if pd.notna(ret20):
        trend_msg = (
            f"20-day return is {ret20:.2f}%, indicating {'downside' if ret20 < 0 else 'upside'} momentum."
        )

    rel_msg = ""
    if pd.notna(rel20):
        rel_msg = (
            f"Relative strength vs benchmark is {rel20:.2f}% over 20 days, "
            f"showing {'underperformance' if rel20 < 0 else 'outperformance'}."
        )

    rsi_msg = ""
    if pd.notna(rsi14):
        if rsi14 < 40:
            rsi_msg = f"RSI14 is {rsi14:.2f}, which is weak/oversold territory."
        elif rsi14 > 70:
            rsi_msg = f"RSI14 is {rsi14:.2f}, which is strong/overbought territory."
        else:
            rsi_msg = f"RSI14 is {rsi14:.2f}, which is in a neutral momentum zone."

    vol_msg = ""
    if pd.notna(vol20):
        vol_msg = f"Annualized 20-day volatility is {vol20:.2f}%, capturing current risk intensity."

    return " ".join([m for m in [f"Score is {score}/100.", stance_msg, trend_msg, rel_msg, rsi_msg, vol_msg] if m])


def build_ai_prompt(symbol: str, benchmark: str, result: dict) -> str:
    regime = []
    if pd.notna(result.get("vix")):
        regime.append(f"VIX={result['vix']:.2f}")
    if pd.notna(result.get("dxy")):
        regime.append(f"DXY={result['dxy']:.2f}")
    regime_text = ", ".join(regime) if regime else "N/A"

    metrics = {
        "Stance": result.get("stance"),
        "Score": result.get("score"),
        "20D Return %": None if pd.isna(result.get("ret20")) else round(float(result.get("ret20")), 2),
        "RSI14": None if pd.isna(result.get("rsi14")) else round(float(result.get("rsi14")), 2),
        "Last Price": None if pd.isna(result.get("price")) else round(float(result.get("price")), 2),
        "Beta vs Bench": None if pd.isna(result.get("beta")) else round(float(result.get("beta")), 2),
        "RelStr 20D %": None if pd.isna(result.get("rel20_vs_bench")) else round(float(result.get("rel20_vs_bench")), 2),
        "20D Vol Ann %": None if pd.isna(result.get("vol20")) else round(float(result.get("vol20")), 2),
        "Macro Regime": regime_text,
    }

    return (
        f"You are a market strategist. Explain the trade-check output for {symbol} versus {benchmark} in plain English for an active trader.\n\n"
        f"Metrics: {json.dumps(metrics)}\n\n"
        "Please provide:\n"
        "1) One-paragraph summary of what the setup means now\n"
        "2) Why the stance/score reached this level\n"
        "3) Bull case and bear case for the next 5-20 trading days\n"
        "4) 3 confirmation signals to wait for before adding risk\n"
        "5) 3 invalidation/risk signals to reduce exposure\n"
        "6) A watchlist of related assets (index, rates, dollar, commodities) that could change the view\n"
        "Use concise bullets and avoid financial advice language."
    )


def ask_ai_openai_compatible(
    user_query: str,
    api_key: str,
    model: str,
    system_prompt: str,
    base_url: str = "https://api.openai.com/v1",
) -> str:
    url = f"{base_url.rstrip('/')}/chat/completions"
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_query},
        ],
        "temperature": 0.2,
    }

    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )

    with urllib.request.urlopen(req, timeout=45) as response:
        body = json.loads(response.read().decode("utf-8"))

    choices = body.get("choices", [])
    if not choices:
        return "AI API returned no choices."

    message = choices[0].get("message", {})
    content = message.get("content", "")
    if isinstance(content, list):
        return "\n".join([c.get("text", "") for c in content if isinstance(c, dict)]).strip()
    return str(content).strip() if content else "AI API returned an empty response."


def ask_local_copilot_style(user_query: str, symbol: str, benchmark: str, result: dict) -> str:
    if not result.get("ok"):
        return "I cannot evaluate this setup because trade-check metrics are incomplete."

    stance = result.get("stance", "UNKNOWN")
    score = result.get("score")
    ret20 = result.get("ret20")
    rel20 = result.get("rel20_vs_bench")
    rsi14 = result.get("rsi14")
    vol20 = result.get("vol20")
    beta = result.get("beta")
    vix = result.get("vix")
    dxy = result.get("dxy")

    summary = []
    summary.append(f"Setup for {symbol} vs {benchmark}: {stance} (score {score}/100).")

    if pd.notna(ret20):
        direction = "negative" if ret20 < 0 else "positive"
        summary.append(f"20-day momentum is {direction} at {ret20:.2f}%.")
    if pd.notna(rel20):
        rs = "underperforming" if rel20 < 0 else "outperforming"
        summary.append(f"Relative strength is {rel20:.2f}% ({rs} benchmark).")
    if pd.notna(rsi14):
        if rsi14 < 40:
            summary.append(f"RSI14 is {rsi14:.2f}, indicating weak momentum.")
        elif rsi14 > 70:
            summary.append(f"RSI14 is {rsi14:.2f}, indicating stretched upside momentum.")
        else:
            summary.append(f"RSI14 is {rsi14:.2f}, indicating neutral momentum.")
    if pd.notna(vol20):
        summary.append(f"20-day annualized volatility is {vol20:.2f}%, defining current risk level.")

    macro = []
    if pd.notna(vix):
        macro.append(f"VIX {vix:.2f}")
    if pd.notna(dxy):
        macro.append(f"DXY {dxy:.2f}")
    macro_text = ", ".join(macro) if macro else "macro proxies unavailable"

    bull_triggers = [
        "Price closes above 20-day moving average for 2+ sessions",
        "RSI14 reclaims and holds above 45",
        "Relative strength vs benchmark turns positive",
    ]
    bear_triggers = [
        "New 20-day low with rising volume",
        "Relative strength keeps deteriorating",
        "VIX rising while price fails to reclaim 20-day average",
    ]

    risk_plan = []
    if pd.notna(beta):
        risk_plan.append(f"Beta {beta:.2f} suggests position sizing near market-risk levels.")
    if pd.notna(vol20):
        risk_plan.append("Use smaller size when volatility is elevated and prefer staged entries.")
    risk_plan.append("Define invalidation before entry and cut risk if invalidation is hit.")

    return (
        f"Question: {user_query}\n\n"
        f"Summary: {' '.join(summary)}\n"
        f"Macro Context: {macro_text}.\n\n"
        "Bullish confirmation triggers:\n- " + "\n- ".join(bull_triggers) + "\n\n"
        "Bearish risk triggers:\n- " + "\n- ".join(bear_triggers) + "\n\n"
        "Risk plan:\n- " + "\n- ".join(risk_plan)
    )


st.title("Global Macro Terminal")
st.caption(
    "Bloomberg-style open-source dashboard: global markets, commodities, cross-asset links, event radar, and trade-check."
)

with st.sidebar:
    st.header("Controls")
    data_source_mode = st.selectbox("Data Source", ["Auto", "Yahoo", "IBKR"], index=2)
    period = st.selectbox("Lookback", ["1mo", "3mo", "6mo", "1y", "2y"], index=2)
    interval = st.selectbox("Interval", ["1d", "1wk"], index=0)
    st.markdown("**IBKR Connection**")
    ibkr_host = st.text_input("IBKR Host", value="127.0.0.1")
    ibkr_port = st.number_input("IBKR Port", min_value=1, max_value=65535, value=7497)
    ibkr_client_id = st.number_input("IBKR Client ID", min_value=1, max_value=9999, value=91)
    ibkr_market_data_type = st.selectbox(
        "IBKR Market Data Type",
        options=[1, 2, 3, 4],
        index=2,
        format_func=lambda x: {1: "1 Live", 2: "2 Frozen", 3: "3 Delayed", 4: "4 Delayed Frozen"}[x],
    )
    ibkr_timeout = st.number_input("IBKR Timeout (sec)", min_value=3, max_value=30, value=8)
    use_scheduled_db_mode = st.checkbox("Use Scheduled DB Pull (UI reads DB only)", value=False)
    auto_refresh_enabled = st.checkbox("Auto Refresh", value=True)
    auto_refresh_minutes = st.number_input("Refresh every (minutes)", min_value=1, max_value=120, value=15)
    fetch_oi_enabled = st.checkbox("Fetch Open Interest (IBKR)", value=True)
    persist_live_oi_enabled = st.checkbox("Persist Live OI Snapshots", value=True)
    st.markdown("---")
    enable_sqlite = st.checkbox("Enable SQLite persistence", value=False)
    sqlite_path_yahoo = st.text_input("Yahoo DB path", value="macro_terminal_yahoo.db")
    sqlite_path_ibkr = st.text_input("IBKR DB path", value="macro_terminal_ibkr.db")
    sqlite_path_auto = st.text_input("Auto DB path", value="macro_terminal_auto.db")
    st.markdown("**IBKR DB Backfill**")
    ibkr_backfill_mode = st.selectbox(
        "IBKR Backfill Mode",
        ["Incremental (Prev Day)", "Historical (Lookback Days)"],
        index=0,
    )
    ibkr_backfill_days = st.number_input("IBKR Backfill Days", min_value=5, max_value=2000, value=365)
    ibkr_backfill_run = st.button("Fill IBKR DB Now", use_container_width=True)
    st.markdown("**IBKR Options Pull**")
    ibkr_opt_max_expiries = st.number_input("Options Max Expiries", min_value=1, max_value=8, value=2)
    ibkr_opt_strikes_side = st.number_input("Options Strikes per Side", min_value=2, max_value=30, value=8)
    ibkr_options_run = st.button("Pull IBKR Options Chain", use_container_width=True)
    custom_watchlist = st.text_area(
        "Custom symbols (comma-separated)",
        value=", ".join(DEFAULT_WATCHLIST),
        height=120,
    )
    trade_symbol = st.text_input("Trade-check symbol", value="AAPL").strip().upper()
    benchmark_symbol = st.text_input("Benchmark", value="SPY").strip().upper()
    st.markdown("---")
    st.subheader("AI Copilot")
    ai_enabled = st.checkbox("Enable in-dashboard AI", value=True)
    ai_mode = st.selectbox("AI Mode", ["Local (No Key)", "OpenAI-Compatible API"], index=0)
    ai_base_url = st.text_input("AI Base URL", value=os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1"))
    ai_model = st.text_input("AI Model", value=os.getenv("OPENAI_MODEL", "gpt-4o-mini"))
    ai_api_key = st.text_input("API Key", value=os.getenv("OPENAI_API_KEY", ""), type="password")


all_symbols: list[str] = []
for group in GLOBAL_ASSETS.values():
    all_symbols.extend(group)

watch_symbols = [s.strip().upper() for s in custom_watchlist.split(",") if s.strip()]
all_symbols.extend(watch_symbols)
all_symbols.extend([trade_symbol, benchmark_symbol])
all_symbols = sorted(set(all_symbols))

if auto_refresh_enabled:
    st.markdown(
        f"<meta http-equiv='refresh' content='{int(auto_refresh_minutes) * 60}'>",
        unsafe_allow_html=True,
    )

def fetch_prices(symbols: list[str], selected_period: str, selected_interval: str) -> tuple[pd.DataFrame, dict]:
    if use_scheduled_db_mode:
        db_df = load_price_matrix_from_ibkr_db(sqlite_path_ibkr, symbols, selected_period, selected_interval)
        return db_df, {
            "source": "IBKR_DB_ONLY",
            "loaded": int(db_df.shape[1]) if not db_df.empty else 0,
            "requested": len(set(symbols)),
            "db_path": sqlite_path_ibkr,
        }
    return get_price_matrix(
        symbols,
        period=selected_period,
        interval=selected_interval,
        source_mode=data_source_mode,
        ibkr_host=ibkr_host,
        ibkr_port=int(ibkr_port),
        ibkr_client_id=int(ibkr_client_id),
        ibkr_market_data_type=int(ibkr_market_data_type),
        ibkr_timeout=int(ibkr_timeout),
    )


prices_fetch_start = time.perf_counter()
prices, source_status = fetch_prices(all_symbols, period, interval)
prices_fetch_sec = time.perf_counter() - prices_fetch_start
latest_global_perf = pd.DataFrame()
merged_news = pd.DataFrame()
oi_snapshot = pd.DataFrame(columns=["Symbol", "OI"])
oi_fetch_sec = 0.0
live_oi_persist_rows = 0

if fetch_oi_enabled and use_scheduled_db_mode:
    oi_snapshot = load_latest_oi_from_db(sqlite_path_ibkr, all_symbols)
elif fetch_oi_enabled and data_source_mode in ["IBKR", "Auto"]:
    oi_fetch_start = time.perf_counter()
    oi_universe = sorted(set(all_symbols))
    oi_snapshot = fetch_open_interest_ibkr(
        symbols=oi_universe,
        host=ibkr_host,
        port=int(ibkr_port),
        client_id=int(ibkr_client_id) + 100,
        market_data_type=int(ibkr_market_data_type),
        timeout=int(ibkr_timeout),
    )
    oi_fetch_sec = time.perf_counter() - oi_fetch_start
    if persist_live_oi_enabled and not oi_snapshot.empty:
        live_oi_persist_rows = persist_live_oi_snapshot(sqlite_path_ibkr, oi_snapshot, source="IBKR")
        st.caption(f"Live OI snapshot rows persisted: {live_oi_persist_rows} -> {sqlite_path_ibkr}")

st.caption(f"Data status: {source_status}")
if use_scheduled_db_mode:
    st.info("Scheduled DB mode is ON: Streamlit reads from IBKR DB and does not perform live IBKR/Yahoo pulls for price matrix.")

ibkr_dq = run_ibkr_data_validations(sqlite_path_ibkr)
ibkr_dq_issues = ibkr_dq.get("issues", pd.DataFrame())
if not ibkr_dq_issues.empty:
    critical_count = int((ibkr_dq_issues["severity"] == "critical").sum())
    warning_count = int((ibkr_dq_issues["severity"] == "warning").sum())
    st.caption(f"IBKR data validation: critical={critical_count}, warning={warning_count}")
    if critical_count > 0:
        top_critical = ibkr_dq_issues[ibkr_dq_issues["severity"] == "critical"]["detail"].head(2).tolist()
        st.error(" | ".join(top_critical))

if data_source_mode == "Yahoo":
    active_db_path = sqlite_path_yahoo
elif data_source_mode == "IBKR":
    active_db_path = sqlite_path_ibkr
else:
    active_db_path = sqlite_path_auto

st.caption(f"Persistence target DB: {active_db_path}")

if ibkr_backfill_run:
    with st.spinner("Filling IBKR DB with historical OHLCV + OI snapshot..."):
        ibkr_result = backfill_ibkr_db(
            symbols=all_symbols,
            db_path=sqlite_path_ibkr,
            lookback_days=int(ibkr_backfill_days),
            host=ibkr_host,
            port=int(ibkr_port),
            client_id=int(ibkr_client_id) + 300,
            market_data_type=int(ibkr_market_data_type),
            timeout=int(ibkr_timeout),
            pull_mode=("incremental" if ibkr_backfill_mode.startswith("Incremental") else "historical"),
        )
    if ibkr_result.get("ok"):
        st.success(
            "IBKR DB fill complete: "
            f"mode={ibkr_result.get('mode', 'historical')}, "
            f"symbols={ibkr_result.get('symbols_loaded', 0)}, "
            f"ohlcv_rows={ibkr_result.get('rows_written', 0)}, "
            f"monthly_rows={ibkr_result.get('monthly_rows_written', 0)}, "
            f"oi_rows={ibkr_result.get('oi_written', 0)}, "
            f"skipped={ibkr_result.get('skipped', 0)}, "
            f"db={ibkr_result.get('db_path')}"
        )
        st.caption("Note: skipped/missing symbols are usually due to contract mapping or market-data subscriptions in IBKR.")
    else:
        st.error(f"IBKR DB fill failed: {ibkr_result.get('reason', 'unknown error')}")

if ibkr_options_run:
    option_symbols = sorted(set([s for s in watch_symbols if re.fullmatch(r"[A-Za-z0-9.]+", s or "")]))
    if not option_symbols:
        option_symbols = [trade_symbol] if re.fullmatch(r"[A-Za-z0-9.]+", trade_symbol or "") else []
    with st.spinner("Pulling IBKR options chain into DB..."):
        opt_result = backfill_ibkr_options_chain(
            symbols=option_symbols,
            db_path=sqlite_path_ibkr,
            host=ibkr_host,
            port=int(ibkr_port),
            client_id=int(ibkr_client_id) + 600,
            market_data_type=int(ibkr_market_data_type),
            timeout=int(ibkr_timeout),
            max_expiries=int(ibkr_opt_max_expiries),
            strikes_per_side=int(ibkr_opt_strikes_side),
        )
    if opt_result.get("ok"):
        st.success(
            "IBKR options pull complete: "
            f"symbols={opt_result.get('symbols_loaded', 0)}, "
            f"rows={opt_result.get('rows', 0)}, "
            f"rows_skipped_empty={opt_result.get('rows_skipped_empty', 0)}, "
            f"change_rows={opt_result.get('change_rows', 0)}, "
            f"compat_options_daily_rows={opt_result.get('compat_rows', 0)}, "
            f"compat_options_change_rows={opt_result.get('compat_change_rows', 0)}, "
            f"skipped={opt_result.get('skipped', 0)}, "
            f"snapshot={opt_result.get('snapshot_ts', '')}"
        )
        if opt_result.get("errors"):
            st.caption(f"Options pull warnings: {len(opt_result.get('errors'))}")
        diagnostics_df = pd.DataFrame(opt_result.get("diagnostics") or [])
        if not diagnostics_df.empty:
            st.dataframe(diagnostics_df, use_container_width=True, hide_index=True)
    else:
        st.error(f"IBKR options pull failed: {opt_result.get('reason', 'unknown error')}")
        diagnostics_df = pd.DataFrame(opt_result.get("diagnostics") or [])
        if not diagnostics_df.empty:
            st.dataframe(diagnostics_df, use_container_width=True, hide_index=True)

tab1, tab2, tab3, tab4, tab5, tab6, tab7 = st.tabs(
    [
        "Market Grid",
        "Cross-Asset Relations",
        "Events & News",
        "Watchlist Scanner",
        "OI Comparison",
        "Trade Check",
        "Data Quality",
    ]
)


with tab1:
    st.subheader("Global Market Grid")
    if prices.empty:
        st.warning("No data returned from data source.")
    else:
        frames = []
        for group_name, symbols in GLOBAL_ASSETS.items():
            perf = compute_perf_table(prices[[s for s in symbols if s in prices.columns]])
            if not perf.empty:
                perf.insert(0, "Group", group_name)
                frames.append(perf)
        grid = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()

        c1, c2, c3, c4 = st.columns(4)
        risk_on = grid[grid["1D%"] > 0].shape[0] if not grid.empty else 0
        risk_off = grid[grid["1D%"] < 0].shape[0] if not grid.empty else 0
        c1.metric("Universe Symbols", int(grid.shape[0]))
        c2.metric("Advancers", int(risk_on))
        c3.metric("Decliners", int(risk_off))
        c4.metric("As of (UTC)", datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M"))

        if not grid.empty:
            if not oi_snapshot.empty:
                grid = grid.merge(oi_snapshot, on="Symbol", how="left")
            else:
                grid["OI"] = np.nan
            latest_global_perf = grid[["Symbol", "Last", "1D%", "1W%", "1M%", "YTD%"]].copy()
            heat_df = grid.pivot_table(index="Group", columns="Symbol", values="1D%")
            fig = px.imshow(
                heat_df,
                color_continuous_scale="RdYlGn",
                aspect="auto",
                title="1-Day Performance Heatmap (%)",
            )
            fig.update_layout(height=480)
            st.plotly_chart(fig, use_container_width=True)

            st.dataframe(
                grid.sort_values(["Group", "1D%"], ascending=[True, False]),
                use_container_width=True,
                hide_index=True,
            )
            st.caption("OI shown where IBKR publishes it for the mapped contract; unavailable symbols show NaN.")


with tab2:
    st.subheader("Cross-Asset Correlations")
    corr_universe = sorted(
        set(
            GLOBAL_ASSETS["US Equity Indices"]
            + GLOBAL_ASSETS["Global Equity Indices"]
            + GLOBAL_ASSETS["Rates & FX"]
            + GLOBAL_ASSETS["Commodities"]
            + ["BTC-USD", "ETH-USD", "SPY", "QQQ", "TLT"]
        )
    )
    corr_data, _ = get_price_matrix(
        corr_universe,
        period="1y",
        interval="1d",
        source_mode=data_source_mode,
        ibkr_host=ibkr_host,
        ibkr_port=int(ibkr_port),
        ibkr_client_id=int(ibkr_client_id),
        ibkr_market_data_type=int(ibkr_market_data_type),
        ibkr_timeout=int(ibkr_timeout),
    )

    if corr_data.empty:
        st.warning("Unable to compute correlations.")
    else:
        returns = corr_data.pct_change().dropna(how="all")
        corr = returns.corr(min_periods=40)

        fig_corr = px.imshow(
            corr,
            color_continuous_scale="RdBu",
            zmin=-1,
            zmax=1,
            title="1Y Daily Return Correlation Matrix",
            aspect="auto",
        )
        fig_corr.update_layout(height=680)
        st.plotly_chart(fig_corr, use_container_width=True)

    st.markdown("### Commodity / Metals / Oil Relationship Monitor")
    chain_choice = st.selectbox("Select relation basket", list(RELATIONSHIP_MAP.keys()))
    chain_symbols = RELATIONSHIP_MAP[chain_choice]
    chain_data, _ = get_price_matrix(
        chain_symbols,
        period="6mo",
        interval="1d",
        source_mode=data_source_mode,
        ibkr_host=ibkr_host,
        ibkr_port=int(ibkr_port),
        ibkr_client_id=int(ibkr_client_id),
        ibkr_market_data_type=int(ibkr_market_data_type),
        ibkr_timeout=int(ibkr_timeout),
    )

    if chain_data.empty:
        st.info("No relation data available.")
    else:
        rebased = chain_data.div(chain_data.iloc[0]).mul(100)
        fig_chain = go.Figure()
        for col in rebased.columns:
            fig_chain.add_trace(go.Scatter(x=rebased.index, y=rebased[col], mode="lines", name=col))
        fig_chain.update_layout(title=f"{chain_choice} (Rebased to 100)", height=460)
        st.plotly_chart(fig_chain, use_container_width=True)


with tab3:
    st.subheader("Global Events & Market News Radar")
    rss_sources = {
        "Yahoo Finance": "https://finance.yahoo.com/news/rssindex",
        "CNBC World": "https://www.cnbc.com/id/100727362/device/rss/rss.html",
        "CNBC Markets": "https://www.cnbc.com/id/10001147/device/rss/rss.html",
        "Reddit r/investing": "https://www.reddit.com/r/investing/.rss",
        "Reddit r/stocks": "https://www.reddit.com/r/stocks/.rss",
    }

    cols = st.columns(len(rss_sources))
    news_tables = []
    for i, (name, url) in enumerate(rss_sources.items()):
        feed = fetch_rss(url, limit=8)
        with cols[i]:
            st.markdown(f"**{name}**")
            st.metric("Items", int(feed.shape[0]))
        if not feed.empty:
            feed.insert(0, "Source", name)
            news_tables.append(feed)

    if news_tables:
        merged_news = pd.concat(news_tables, ignore_index=True)
        merged_news = merged_news[["Source", "title", "published", "link"]]
        st.dataframe(merged_news, use_container_width=True, hide_index=True)
    else:
        st.info("No RSS items loaded right now.")

    st.markdown("### Open-Source Trading Tools (GitHub Pulse)")
    repos = fetch_open_source_repos()
    if repos.empty:
        st.info("GitHub API not reachable at the moment.")
    else:
        st.dataframe(repos, use_container_width=True, hide_index=True)


with tab4:
    st.subheader("Watchlist Scanner")
    if not watch_symbols:
        st.warning("Add watchlist symbols in sidebar.")
    else:
        watch_prices, _ = get_price_matrix(
            watch_symbols,
            period="1y",
            interval="1d",
            source_mode=data_source_mode,
            ibkr_host=ibkr_host,
            ibkr_port=int(ibkr_port),
            ibkr_client_id=int(ibkr_client_id),
            ibkr_market_data_type=int(ibkr_market_data_type),
            ibkr_timeout=int(ibkr_timeout),
        )
        if watch_prices.empty:
            st.warning("No watchlist data available.")
        else:
            table = compute_perf_table(watch_prices)
            rows = []
            for symbol in watch_prices.columns:
                s = watch_prices[symbol].dropna()
                if len(s) < 60:
                    continue
                sma20 = s.rolling(20).mean().iloc[-1]
                sma50 = s.rolling(50).mean().iloc[-1]
                r = rsi(s).iloc[-1]
                trend = "Up" if s.iloc[-1] > sma20 > sma50 else "Down"
                rows.append(
                    {
                        "Symbol": symbol,
                        "Trend": trend,
                        "RSI14": round(float(r), 2) if pd.notna(r) else np.nan,
                        "Price_vs_20D": round((s.iloc[-1] / sma20 - 1) * 100, 2) if pd.notna(sma20) else np.nan,
                    }
                )
            extra = pd.DataFrame(rows)
            scanner = table.merge(extra, on="Symbol", how="left")
            if not oi_snapshot.empty:
                scanner = scanner.merge(oi_snapshot, on="Symbol", how="left")
            else:
                scanner["OI"] = np.nan
            st.dataframe(scanner.sort_values("1D%", ascending=False), use_container_width=True, hide_index=True)


with tab5:
    st.subheader("OI Comparison (IBKR DB)")
    st.caption("Uses only IBKR pulled data from `ibkr_oi_snapshot` and `ibkr_stock_daily` in your IBKR DB path.")

    oi_hist = load_ibkr_oi_history(sqlite_path_ibkr)
    if oi_hist.empty:
        st.warning(
            "No IBKR OI history found yet. Run 'Fill IBKR DB Now' from the sidebar to populate OI snapshots first."
        )
    else:
        st.markdown("### Significant OI Change Scanner")
        s1, s2 = st.columns(2)
        with s1:
            sig_pct_threshold = st.number_input("Min % change (weekly/intraweek)", min_value=1.0, max_value=500.0, value=10.0, step=1.0)
        with s2:
            sig_abs_threshold = st.number_input("Min absolute OI change", min_value=1.0, max_value=10_000_000.0, value=1000.0, step=100.0)

        sig_df = summarize_significant_oi_changes(
            oi_hist=oi_hist,
            min_pct_change=float(sig_pct_threshold),
            min_abs_change=float(sig_abs_threshold),
        )

        if sig_df.empty:
            st.info("No symbols crossed current significance thresholds for weekly or within-week OI change.")
        else:
            st.dataframe(sig_df, use_container_width=True, hide_index=True)

        latest_ts = oi_hist["snapshot_ts"].max()
        latest = oi_hist[oi_hist["snapshot_ts"] == latest_ts].copy()
        latest = latest.sort_values("open_interest", ascending=False)

        c1, c2, c3 = st.columns(3)
        c1.metric("IBKR OI Rows", int(oi_hist.shape[0]))
        c2.metric("Symbols with OI", int(oi_hist["symbol"].nunique()))
        c3.metric("Latest Snapshot", latest_ts.strftime("%Y-%m-%d %H:%M") if pd.notna(latest_ts) else "NA")

        st.markdown("### Latest OI Ranking")
        st.dataframe(latest[["symbol", "open_interest", "source"]], use_container_width=True, hide_index=True)

        symbols = sorted(oi_hist["symbol"].dropna().unique().tolist())
        default_symbol = symbols[0] if symbols else trade_symbol
        oi_symbol = st.selectbox("Select symbol", options=symbols, index=symbols.index(default_symbol) if default_symbol in symbols else 0)

        sym_oi = oi_hist[oi_hist["symbol"] == oi_symbol].copy()
        if sym_oi.empty:
            st.info("No OI history for selected symbol.")
        else:
            sym_oi = sym_oi.sort_values("snapshot_ts")
            sym_oi["oi_change"] = sym_oi["open_interest"].diff()
            sym_oi["week_start"] = sym_oi["snapshot_ts"].dt.to_period("W-MON").dt.start_time
            latest_oi = sym_oi["open_interest"].iloc[-1] if not sym_oi.empty else np.nan
            prev_oi = sym_oi["open_interest"].iloc[-2] if len(sym_oi) >= 2 else np.nan
            pcr_like = np.nan

            d1_chg = latest_oi - prev_oi if pd.notna(latest_oi) and pd.notna(prev_oi) else np.nan
            pct_chg = (d1_chg / prev_oi * 100) if pd.notna(d1_chg) and pd.notna(prev_oi) and prev_oi != 0 else np.nan

            cc1, cc2, cc3 = st.columns(3)
            cc1.metric("Latest OI", f"{int(latest_oi):,}" if pd.notna(latest_oi) else "NA")
            cc2.metric("OI Change", f"{int(d1_chg):,}" if pd.notna(d1_chg) else "NA")
            cc3.metric("OI Change %", f"{pct_chg:.2f}%" if pd.notna(pct_chg) else "NA")

            cur_w = sym_oi["week_start"].max()
            prev_w = cur_w - pd.Timedelta(days=7) if pd.notna(cur_w) else pd.NaT
            cur_week_rows = sym_oi[sym_oi["week_start"] == cur_w].sort_values("snapshot_ts") if pd.notna(cur_w) else pd.DataFrame()
            prev_week_rows = sym_oi[sym_oi["week_start"] == prev_w].sort_values("snapshot_ts") if pd.notna(prev_w) else pd.DataFrame()

            intra_week_abs = np.nan
            intra_week_pct = np.nan
            wow_abs = np.nan
            wow_pct = np.nan
            if not cur_week_rows.empty:
                first_cur = cur_week_rows["open_interest"].iloc[0]
                last_cur = cur_week_rows["open_interest"].iloc[-1]
                intra_week_abs = last_cur - first_cur
                intra_week_pct = (intra_week_abs / first_cur * 100) if first_cur not in [0, np.nan] else np.nan
                if not prev_week_rows.empty:
                    last_prev = prev_week_rows["open_interest"].iloc[-1]
                    wow_abs = last_cur - last_prev
                    wow_pct = (wow_abs / last_prev * 100) if last_prev not in [0, np.nan] else np.nan

            cw1, cw2 = st.columns(2)
            cw1.metric("Within Week OI %", f"{intra_week_pct:.2f}%" if pd.notna(intra_week_pct) else "NA")
            cw2.metric("Week-over-Week OI %", f"{wow_pct:.2f}%" if pd.notna(wow_pct) else "NA")

            st.markdown("### Intraday OI Comparison")
            compare = load_live_oi_compare_points(sqlite_path_ibkr, oi_symbol)
            if not compare:
                st.info("No live intraday OI snapshot history yet for this symbol. Enable 'Persist Live OI Snapshots' and refresh periodically.")
            else:
                ic1, ic2, ic3 = st.columns(3)
                ic1.metric(
                    "Start of Day OI",
                    f"{int(compare['start_oi']):,}" if pd.notna(compare.get("start_oi")) else "NA",
                    delta=f"rows today: {compare.get('rows_today', 0)}",
                )
                ic2.metric(
                    "15 Min Before OI",
                    f"{int(compare['pre15_oi']):,}" if pd.notna(compare.get("pre15_oi")) else "NA",
                    delta=f"{compare.get('pre15_ts').strftime('%H:%M') if pd.notna(compare.get('pre15_ts')) else 'NA'}",
                )
                ic3.metric(
                    "Current OI",
                    f"{int(compare['current_oi']):,}" if pd.notna(compare.get("current_oi")) else "NA",
                    delta=f"{compare.get('current_ts').strftime('%H:%M') if pd.notna(compare.get('current_ts')) else 'NA'}",
                )

                ic4, ic5 = st.columns(2)
                ic4.metric(
                    "Current vs Start",
                    f"{int(compare['chg_from_start']):,}" if pd.notna(compare.get("chg_from_start")) else "NA",
                    delta=f"{compare.get('chg_from_start_pct'):.2f}%" if pd.notna(compare.get("chg_from_start_pct")) else "NA",
                )
                ic5.metric(
                    "Current vs 15 Min Before",
                    f"{int(compare['chg_vs_pre15']):,}" if pd.notna(compare.get("chg_vs_pre15")) else "NA",
                    delta=f"{compare.get('chg_vs_pre15_pct'):.2f}%" if pd.notna(compare.get("chg_vs_pre15_pct")) else "NA",
                )

            close_hist = load_ibkr_close_history(sqlite_path_ibkr, oi_symbol)
            monthly_hist = load_ibkr_monthly_history(sqlite_path_ibkr, oi_symbol)

            fig_oi = go.Figure()
            fig_oi.add_trace(
                go.Scatter(
                    x=sym_oi["snapshot_ts"],
                    y=sym_oi["open_interest"],
                    mode="lines+markers",
                    name="Open Interest",
                )
            )
            if not close_hist.empty:
                fig_oi.add_trace(
                    go.Scatter(
                        x=close_hist["trade_date"],
                        y=close_hist["close"],
                        mode="lines",
                        name="Close",
                        yaxis="y2",
                        line=dict(dash="dot"),
                    )
                )

            fig_oi.update_layout(
                title=f"{oi_symbol}: OI vs Close (IBKR)",
                height=500,
                yaxis=dict(title="Open Interest"),
                yaxis2=dict(title="Close", overlaying="y", side="right", showgrid=False),
                legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
            )
            st.plotly_chart(fig_oi, use_container_width=True)

            st.markdown("### OI Time Series")
            st.dataframe(
                sym_oi[["snapshot_ts", "symbol", "open_interest", "oi_change"]].sort_values("snapshot_ts", ascending=False),
                use_container_width=True,
                hide_index=True,
            )

            st.markdown("### Options OI Comparison (IBKR)")
            options_df = load_latest_options_snapshot(sqlite_path_ibkr, oi_symbol)
            if options_df.empty:
                st.info("No options snapshot found for this symbol yet. Use 'Pull IBKR Options Chain' in sidebar.")
            else:
                expiries = sorted(options_df["expiry"].dropna().astype(str).unique().tolist())
                selected_expiry = st.selectbox("Options expiry", options=expiries, key=f"expiry_{oi_symbol}")
                exp_df = options_df[options_df["expiry"].astype(str) == selected_expiry].copy()
                if exp_df.empty:
                    st.info("No options rows for selected expiry.")
                else:
                    agg = exp_df.pivot_table(
                        index="strike",
                        columns="right",
                        values="open_interest",
                        aggfunc="sum",
                    ).reset_index()
                    if "C" not in agg.columns:
                        agg["C"] = 0.0
                    if "P" not in agg.columns:
                        agg["P"] = 0.0
                    agg["C"] = agg["C"].fillna(0.0)
                    agg["P"] = agg["P"].fillna(0.0)
                    agg = agg.sort_values("strike")

                    total_call = float(agg["C"].sum())
                    total_put = float(agg["P"].sum())
                    pcr = (total_put / total_call) if total_call > 0 else np.nan

                    oc1, oc2, oc3 = st.columns(3)
                    oc1.metric("Total Call OI", f"{int(total_call):,}")
                    oc2.metric("Total Put OI", f"{int(total_put):,}")
                    oc3.metric("PCR OI", f"{pcr:.2f}" if pd.notna(pcr) else "NA")

                    bar_df = pd.DataFrame(
                        {
                            "strike": agg["strike"],
                            "Call OI": agg["C"],
                            "Put OI": -agg["P"],
                        }
                    )
                    fig_opt = go.Figure()
                    fig_opt.add_trace(go.Bar(x=bar_df["strike"], y=bar_df["Call OI"], name="Call OI"))
                    fig_opt.add_trace(go.Bar(x=bar_df["strike"], y=bar_df["Put OI"], name="Put OI (neg)"))
                    fig_opt.update_layout(
                        title=f"{oi_symbol} {selected_expiry} - Call vs Put OI by Strike",
                        barmode="overlay",
                        height=420,
                    )
                    st.plotly_chart(fig_opt, use_container_width=True)

                    change_df = load_latest_options_change(sqlite_path_ibkr, oi_symbol, selected_expiry)
                    if not change_df.empty:
                        fig_tg = build_options_telegram_style_chart(oi_symbol, selected_expiry, change_df)
                        if fig_tg is not None:
                            st.plotly_chart(fig_tg, use_container_width=True)

                        st.markdown("#### Latest OI Changes")
                        st.dataframe(
                            change_df[
                                [
                                    "strike",
                                    "right",
                                    "prev_open_interest",
                                    "curr_open_interest",
                                    "change_open_interest",
                                    "pct_change_open_interest",
                                    "change_close",
                                ]
                            ].sort_values("change_open_interest", ascending=False),
                            use_container_width=True,
                            hide_index=True,
                        )

            st.markdown("### EOD Tables Check (IBKR)")
            ec1, ec2 = st.columns(2)
            ec1.metric("Daily rows", int(close_hist.shape[0]) if not close_hist.empty else 0)
            ec2.metric("Monthly rows", int(monthly_hist.shape[0]) if not monthly_hist.empty else 0)

            if not monthly_hist.empty:
                st.dataframe(monthly_hist.sort_values("month_key", ascending=False), use_container_width=True, hide_index=True)


with tab6:
    st.subheader("Trade Check Engine")
    result = build_trade_check(
        trade_symbol,
        benchmark_symbol,
        lookback="1y",
        source_mode=data_source_mode,
        ibkr_host=ibkr_host,
        ibkr_port=int(ibkr_port),
        ibkr_client_id=int(ibkr_client_id),
        ibkr_market_data_type=int(ibkr_market_data_type),
        ibkr_timeout=int(ibkr_timeout),
    )
    if not result["ok"]:
        st.warning(result["reason"])
    else:
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Stance", result["stance"])
        c2.metric("Score", result["score"])
        c3.metric("20D Return %", f"{result['ret20']:.2f}" if pd.notna(result["ret20"]) else "NA")
        c4.metric("RSI14", f"{result['rsi14']:.2f}" if pd.notna(result["rsi14"]) else "NA")

        c5, c6, c7, c8 = st.columns(4)
        c5.metric("Last Price", f"{result['price']:.2f}")
        c6.metric("Beta vs Bench", f"{result['beta']:.2f}" if pd.notna(result["beta"]) else "NA")
        c7.metric("RelStr 20D %", f"{result['rel20_vs_bench']:.2f}" if pd.notna(result["rel20_vs_bench"]) else "NA")
        c8.metric("20D Vol(Ann)%", f"{result['vol20']:.2f}" if pd.notna(result["vol20"]) else "NA")

        oi_value = np.nan
        if not oi_snapshot.empty:
            this_row = oi_snapshot[oi_snapshot["Symbol"] == trade_symbol]
            if not this_row.empty:
                oi_value = this_row.iloc[-1]["OI"]

        c9, c10, c11, c12 = st.columns(4)
        c9.metric("Open Interest", f"{int(oi_value):,}" if pd.notna(oi_value) else "NA")
        c10.metric("OI Source", "IBKR" if pd.notna(oi_value) else "NA")
        c11.metric("Data Source", data_source_mode)
        c12.metric("OI Enabled", "Yes" if fetch_oi_enabled else "No")

        if data_source_mode in ["IBKR", "Auto"]:
            live_quote = fetch_live_snapshot_ibkr(
                symbol=trade_symbol,
                host=ibkr_host,
                port=int(ibkr_port),
                client_id=int(ibkr_client_id) + 200,
                market_data_type=int(ibkr_market_data_type),
                timeout=int(ibkr_timeout),
            )
        else:
            live_quote = fetch_live_snapshot_yahoo(trade_symbol)

        st.markdown("### OHLC + Volume Snapshot")
        q1, q2, q3, q4 = st.columns(4)
        q1.metric("Open", f"{float(live_quote.get('open')):.2f}" if pd.notna(live_quote.get("open")) else "NA")
        q2.metric("High", f"{float(live_quote.get('high')):.2f}" if pd.notna(live_quote.get("high")) else "NA")
        q3.metric("Low", f"{float(live_quote.get('low')):.2f}" if pd.notna(live_quote.get("low")) else "NA")
        q4.metric("Close", f"{float(live_quote.get('close')):.2f}" if pd.notna(live_quote.get("close")) else "NA")

        q5, q6, q7, q8 = st.columns(4)
        q5.metric("Volume", f"{int(live_quote.get('volume')):,}" if pd.notna(live_quote.get("volume")) else "NA")
        q6.metric("Last", f"{float(live_quote.get('last')):.2f}" if pd.notna(live_quote.get("last")) else "NA")
        q7.metric("Bid", f"{float(live_quote.get('bid')):.2f}" if pd.notna(live_quote.get("bid")) else "NA")
        q8.metric("Ask", f"{float(live_quote.get('ask')):.2f}" if pd.notna(live_quote.get("ask")) else "NA")

        regime_text = []
        if pd.notna(result["vix"]):
            regime_text.append(f"VIX: {result['vix']:.2f}")
        if pd.notna(result["dxy"]):
            regime_text.append(f"DXY: {result['dxy']:.2f}")
        if regime_text:
            st.info(" | ".join(regime_text))

        st.markdown("### What This Means")
        st.write(summarize_trade_check(result))

        st.markdown("### AI Explanation Prompt")
        st.caption("Copy this prompt and run it in your preferred LLM for a deeper explanation of the current trade-check state.")
        ai_prompt = build_ai_prompt(trade_symbol, benchmark_symbol, result)
        st.text_area("Prompt", value=ai_prompt, height=230, key="trade_check_ai_prompt")

        st.markdown("### Ask AI (Inside Dashboard)")
        if "dashboard_ai_answer" not in st.session_state:
            st.session_state.dashboard_ai_answer = ""

        default_query = (
            f"Explain whether {trade_symbol} is tradable now vs {benchmark_symbol}, based on this trade-check output. "
            f"Give entry conditions, risk flags, and what to monitor tomorrow."
        )
        user_query = st.text_area("Your query", value=default_query, height=100, key="dashboard_ai_query")

        col_a, col_b = st.columns([1, 3])
        with col_a:
            ask_now = st.button("Ask AI", type="primary", use_container_width=True)
        with col_b:
            st.caption("Runs in this dashboard and shows answer below. Local mode needs no key.")

        if ask_now:
            if not ai_enabled:
                st.warning("Enable 'in-dashboard AI' in sidebar first.")
            elif not user_query.strip():
                st.warning("Please enter a query.")
            else:
                live_metrics = {
                    "symbol": trade_symbol,
                    "benchmark": benchmark_symbol,
                    "stance": result.get("stance"),
                    "score": result.get("score"),
                    "ret20": result.get("ret20"),
                    "rsi14": result.get("rsi14"),
                    "last": result.get("price"),
                    "beta": result.get("beta"),
                    "rel20": result.get("rel20_vs_bench"),
                    "vol20": result.get("vol20"),
                    "vix": result.get("vix"),
                    "dxy": result.get("dxy"),
                }
                sys_prompt = (
                    "You are GitHub Copilot using GPT-5.3-Codex. "
                    "Explain market metrics clearly, with concise bullet points, and avoid absolute guarantees."
                )
                composed_query = (
                    f"User question: {user_query}\n\n"
                    f"Current trade-check metrics JSON: {json.dumps(live_metrics, default=str)}\n\n"
                    "Respond with: summary, what drives current stance, bullish triggers, bearish triggers, and risk plan."
                )
                try:
                    with st.spinner("Asking AI..."):
                        if ai_mode == "Local (No Key)":
                            ai_answer = ask_local_copilot_style(
                                user_query=user_query,
                                symbol=trade_symbol,
                                benchmark=benchmark_symbol,
                                result=result,
                            )
                        else:
                            if not ai_api_key.strip():
                                raise ValueError("Add API key in sidebar (or set OPENAI_API_KEY env var).")
                            ai_answer = ask_ai_openai_compatible(
                                user_query=composed_query,
                                api_key=ai_api_key.strip(),
                                model=ai_model.strip(),
                                system_prompt=sys_prompt,
                                base_url=ai_base_url.strip(),
                            )
                    st.session_state.dashboard_ai_answer = ai_answer
                except Exception as ai_exc:
                    st.session_state.dashboard_ai_answer = f"AI request failed: {ai_exc}"

        if st.session_state.dashboard_ai_answer:
            st.markdown("#### AI Response")
            st.write(st.session_state.dashboard_ai_answer)

        check_symbols = [trade_symbol, benchmark_symbol, "GC=F", "CL=F", "^VIX"]
        chart_data, _ = get_price_matrix(
            check_symbols,
            period="6mo",
            interval="1d",
            source_mode=data_source_mode,
            ibkr_host=ibkr_host,
            ibkr_port=int(ibkr_port),
            ibkr_client_id=int(ibkr_client_id),
            ibkr_market_data_type=int(ibkr_market_data_type),
            ibkr_timeout=int(ibkr_timeout),
        )
        if not chart_data.empty:
            rebased = chart_data.div(chart_data.iloc[0]).mul(100)
            fig = go.Figure()
            for col in rebased.columns:
                fig.add_trace(go.Scatter(x=rebased.index, y=rebased[col], mode="lines", name=col))
            fig.update_layout(title="Trade Context Panel (Rebased)", height=460)
            st.plotly_chart(fig, use_container_width=True)


with tab7:
    st.subheader("IBKR Data Quality")
    st.caption(f"Validation target DB: {sqlite_path_ibkr}")

    table_df = ibkr_dq.get("tables", pd.DataFrame())
    issues_df = ibkr_dq.get("issues", pd.DataFrame())
    coverage_df = ibkr_dq.get("field_coverage", pd.DataFrame())

    if table_df.empty:
        st.warning("No table metadata available.")
    else:
        st.dataframe(table_df.sort_values(["exists", "table"], ascending=[False, True]), use_container_width=True, hide_index=True)

    if issues_df.empty:
        st.success("No validation issues detected.")
    else:
        st.dataframe(issues_df, use_container_width=True, hide_index=True)

    if not coverage_df.empty:
        st.markdown("### Critical Field Coverage")
        st.dataframe(
            coverage_df.sort_values(["non_null_pct", "table", "field"], ascending=[True, True, True]),
            use_container_width=True,
            hide_index=True,
        )


st.markdown("---")
st.caption("Data: Yahoo Finance, RSS feeds, Reddit RSS, GitHub public API. Built for research, not investment advice.")

refresh_total_sec = time.perf_counter() - app_refresh_start
st.caption(
    "Refresh timing: "
    f"prices={prices_fetch_sec:.2f}s | "
    f"oi={oi_fetch_sec:.2f}s | "
    f"total={refresh_total_sec:.2f}s | "
    f"auto_refresh={'on' if auto_refresh_enabled else 'off'} ({int(auto_refresh_minutes)}m)"
)


if enable_sqlite:
    try:
        init_sqlite(active_db_path)
        snapshot_ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        saved_market = save_market_snapshot(active_db_path, snapshot_ts, latest_global_perf)
        saved_news = save_news_snapshot(active_db_path, snapshot_ts, merged_news)
        saved_trade = save_trade_check_snapshot(active_db_path, snapshot_ts, trade_symbol, benchmark_symbol, result)
        st.success(
            f"SQLite snapshot saved: market={saved_market}, news={saved_news}, trade={saved_trade} | db={active_db_path}"
        )
    except Exception as db_exc:
        st.error(f"SQLite persistence error: {db_exc}")
