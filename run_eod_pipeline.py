import argparse

from eod_pipeline.pipeline import run_eod_pipeline
from eod_pipeline.symbols import parse_symbol_text


def main() -> None:
    parser = argparse.ArgumentParser(description="Run IBKR + Yahoo EOD pipeline")
    parser.add_argument("--symbols", default="", help="Comma-separated symbols, e.g. AAPL,MSFT,SPY")
    parser.add_argument("--symbols-file", default="", help="Path to .xlsx/.csv/.txt symbol universe")
    parser.add_argument("--no-ibkr", action="store_true", help="Disable IBKR source")
    parser.add_argument("--no-yahoo", action="store_true", help="Disable Yahoo source")
    parser.add_argument("--yahoo-primary", action="store_true", help="Use Yahoo as primary and IBKR fallback")
    parser.add_argument("--lookback-days", type=int, default=None, help="Yahoo lookback window in days")
    args = parser.parse_args()

    result = run_eod_pipeline(
        symbols=parse_symbol_text(args.symbols),
        symbols_file=args.symbols_file or None,
        use_ibkr=not args.no_ibkr,
        use_yahoo=not args.no_yahoo,
        ibkr_as_primary=not args.yahoo_primary,
        lookback_days=args.lookback_days,
    )

    print(f"run_id={result.run_id}")
    print(f"requested_symbols={result.requested_symbols}")
    print(f"loaded_symbols={result.loaded_symbols}")
    print(f"source_breakdown={result.source_breakdown}")
    if result.failed_symbols:
        print(f"failed_symbols={','.join(result.failed_symbols)}")


if __name__ == "__main__":
    main()
