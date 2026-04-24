from pathlib import Path
from typing import Iterable

import pandas as pd


def normalize_symbol(symbol: str) -> str:
    symbol = str(symbol).strip().upper()
    if not symbol:
        return ""
    return symbol.replace(".", "-")


def _clean_symbols(symbols: Iterable[str]) -> list[str]:
    cleaned = []
    seen = set()
    for raw in symbols:
        symbol = normalize_symbol(raw)
        if symbol and symbol not in seen:
            seen.add(symbol)
            cleaned.append(symbol)
    return cleaned


def load_symbols_from_file(path: Path, sheet_name: str = "ticker_universe") -> list[str]:
    if not path.exists():
        return []

    suffix = path.suffix.lower()
    if suffix in {".xlsx", ".xls"}:
        df = pd.read_excel(path, sheet_name=sheet_name, dtype=str, engine="openpyxl")
        source = df.get("ticker", pd.Series(dtype=str)).tolist()
        return _clean_symbols(source)

    if suffix == ".csv":
        df = pd.read_csv(path, dtype=str)
        source = df.get("ticker", pd.Series(dtype=str)).tolist()
        return _clean_symbols(source)

    if suffix == ".txt":
        rows = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        return _clean_symbols(rows)

    raise ValueError(f"Unsupported symbol file type: {path}")


def parse_symbol_text(symbol_text: str) -> list[str]:
    if not symbol_text.strip():
        return []
    symbols = [part.strip() for part in symbol_text.replace("\n", ",").split(",")]
    return _clean_symbols(symbols)
