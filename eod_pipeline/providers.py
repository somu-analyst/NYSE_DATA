from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

import pandas as pd
import yfinance as yf


@dataclass
class IbkrConnectionConfig:
    host: str
    port: int
    client_id: int
    timeout_sec: int


def fetch_yahoo_eod(symbol: str, lookback_days: int = 45) -> Optional[dict]:
    end = datetime.utcnow().date() + timedelta(days=1)
    start = end - timedelta(days=lookback_days)
    try:
        data = yf.download(
            tickers=symbol,
            start=start.isoformat(),
            end=end.isoformat(),
            interval="1d",
            auto_adjust=False,
            progress=False,
            threads=False,
        )
    except Exception:
        return None

    if data is None or data.empty:
        return None

    data = data.dropna(how="all")
    if data.empty:
        return None

    last_idx = data.index[-1]
    row = data.iloc[-1]

    def _get(field: str):
        value = row.get(field)
        return None if pd.isna(value) else float(value)

    return {
        "source": "yahoo",
        "symbol": symbol,
        "trade_date": pd.Timestamp(last_idx).strftime("%Y-%m-%d"),
        "open": _get("Open"),
        "high": _get("High"),
        "low": _get("Low"),
        "close": _get("Close"),
        "adj_close": _get("Adj Close") if "Adj Close" in data.columns else _get("Close"),
        "volume": _get("Volume"),
        "vwap": None,
        "bar_count": None,
        "currency": "USD",
        "exchange": "SMART",
    }


def fetch_ibkr_eod(symbol: str, config: IbkrConnectionConfig, duration: str = "3 M") -> Optional[dict]:
    try:
        from ib_insync import IB, Stock
    except Exception:
        return None

    ib = IB()
    try:
        ib.connect(
            config.host,
            config.port,
            clientId=config.client_id,
            timeout=config.timeout_sec,
            readonly=True,
        )

        contract = Stock(symbol, "SMART", "USD")
        ib.qualifyContracts(contract)

        bars = ib.reqHistoricalData(
            contract,
            endDateTime="",
            durationStr=duration,
            barSizeSetting="1 day",
            whatToShow="TRADES",
            useRTH=True,
            formatDate=1,
        )
        if not bars:
            return None

        bar = bars[-1]
        trade_date = pd.Timestamp(bar.date).strftime("%Y-%m-%d")
        return {
            "source": "ibkr",
            "symbol": symbol,
            "trade_date": trade_date,
            "open": float(bar.open) if bar.open is not None else None,
            "high": float(bar.high) if bar.high is not None else None,
            "low": float(bar.low) if bar.low is not None else None,
            "close": float(bar.close) if bar.close is not None else None,
            "adj_close": float(bar.close) if bar.close is not None else None,
            "volume": float(bar.volume) if bar.volume is not None else None,
            "vwap": float(bar.wap) if getattr(bar, "wap", None) is not None else None,
            "bar_count": int(bar.barCount) if getattr(bar, "barCount", None) is not None else None,
            "currency": "USD",
            "exchange": "SMART",
        }
    except Exception:
        return None
    finally:
        if ib.isConnected():
            ib.disconnect()


def merge_primary_fallback(primary: Optional[dict], fallback: Optional[dict]) -> Optional[dict]:
    if primary:
        return primary
    return fallback
