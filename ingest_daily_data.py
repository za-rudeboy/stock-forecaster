#!/usr/bin/env python3

from __future__ import annotations

import logging
import sys

from market_data_pipeline import (
    build_common_parser,
    configure_logging,
    ensure_directories,
    fetch_latest_completed_candle,
    read_symbols,
    reconcile_daily_file,
)


def parse_args() -> object:
    return build_common_parser("Fetch the latest completed daily candle for all configured symbols.").parse_args()


def run() -> int:
    args = parse_args()
    ensure_directories()
    log_path = configure_logging("ingest")
    logging.info("Starting daily ingestion")

    try:
        symbols = read_symbols(args.symbols_file)
    except Exception as exc:
        logging.error("Unable to load symbols: %s", exc)
        logging.error("No daily CSV written. Log file: %s", log_path)
        return 1

    successes: list[NormalizedRow] = []
    failures: list[tuple[str, str]] = []
    target_date: str | None = None

    for symbol in symbols:
        try:
            row = fetch_latest_completed_candle(symbol)
            if target_date is None:
                target_date = row.date
            elif row.date != target_date:
                raise ValueError(
                    f"{symbol}: returned date {row.date}, expected {target_date} to keep one file per trading day"
                )
            successes.append(row)
            logging.info("Fetched %s for %s", symbol, row.date)
        except Exception as exc:
            failures.append((symbol, str(exc)))
            logging.error("Failed to fetch %s: %s", symbol, exc)

    if not successes:
        logging.error("No valid rows were fetched. No daily CSV written. Log file: %s", log_path)
        return 1

    successes.sort(key=lambda row: row.symbol)
    reconcile_daily_file(target_date, successes, set(symbols), args.force)

    if failures:
        logging.error("Completed with %s failed symbols. Log file: %s", len(failures), log_path)
        return 1

    logging.info("Completed successfully. Log file: %s", log_path)
    return 0


if __name__ == "__main__":
    sys.exit(run())
