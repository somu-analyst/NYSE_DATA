from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path

from .config import load_config
from .db import create_run, finalize_run, init_db, upsert_prices
from .providers import IbkrConnectionConfig, fetch_ibkr_eod, fetch_yahoo_eod, merge_primary_fallback
from .symbols import load_symbols_from_file, parse_symbol_text


@dataclass
class PipelineResult:
    run_id: int
    requested_symbols: int
    loaded_symbols: int
    source_breakdown: dict[str, int]
    failed_symbols: list[str]


def run_eod_pipeline(
    symbols: list[str] | None = None,
    symbols_file: str | None = None,
    use_ibkr: bool = True,
    use_yahoo: bool = True,
    ibkr_as_primary: bool = True,
    lookback_days: int | None = None,
) -> PipelineResult:
    config = load_config()
    init_db(config.db_path)

    merged_symbols = list(symbols or [])
    if symbols_file:
        merged_symbols.extend(load_symbols_from_file(Path(symbols_file)))
    if not merged_symbols:
        merged_symbols = load_symbols_from_file(config.default_universe_file)

    merged_symbols = sorted(list(dict.fromkeys(parse_symbol_text(",".join(merged_symbols)))))
    run_id = create_run(config.db_path, requested_symbols=len(merged_symbols), notes="ibkr+yahoo eod")

    ibkr_cfg = IbkrConnectionConfig(
        host=config.ibkr_host,
        port=config.ibkr_port,
        client_id=config.ibkr_client_id,
        timeout_sec=config.ibkr_timeout_sec,
    )

    source_breakdown = {"ibkr": 0, "yahoo": 0}
    failed_symbols: list[str] = []
    rows_to_write: list[dict] = []
    lookback = lookback_days if lookback_days is not None else config.lookback_days

    for symbol in merged_symbols:
        ibkr_row = fetch_ibkr_eod(symbol, ibkr_cfg) if use_ibkr else None
        yahoo_row = fetch_yahoo_eod(symbol, lookback_days=lookback) if use_yahoo else None

        if ibkr_as_primary:
            final_row = merge_primary_fallback(ibkr_row, yahoo_row)
        else:
            final_row = merge_primary_fallback(yahoo_row, ibkr_row)

        if not final_row:
            failed_symbols.append(symbol)
            continue

        source = final_row.get("source", "unknown")
        if source in source_breakdown:
            source_breakdown[source] += 1
        rows_to_write.append(final_row)

    inserted = upsert_prices(config.db_path, run_id=run_id, rows=rows_to_write)
    status = "success" if inserted > 0 else "failed"
    notes = f"failed_symbols={len(failed_symbols)}"
    finalize_run(config.db_path, run_id=run_id, loaded_symbols=inserted, status=status, notes=notes)

    return PipelineResult(
        run_id=run_id,
        requested_symbols=len(merged_symbols),
        loaded_symbols=inserted,
        source_breakdown=source_breakdown,
        failed_symbols=failed_symbols,
    )


def run_and_print(**kwargs):
    result = run_eod_pipeline(**kwargs)
    payload = asdict(result)
    print(payload)
