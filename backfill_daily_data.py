#!/usr/bin/env python3

from __future__ import annotations

import argparse
import logging
import sys
import time
from collections import defaultdict

import yfinance as yf

from market_data_pipeline import (
    build_common_parser,
    configure_logging,
    ensure_directories,
    group_rows_by_date,
    normalize_history_row,
    read_symbols,
    reconcile_daily_file,
)


def parse_args() -> argparse.Namespace:
    parser = build_common_parser("Backfill daily candles for all configured symbols.")
    parser.add_argument(
        "--period",
        default="6mo",
        help="Yahoo Finance period string for the backfill window (default: 6mo).",
    )
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=10,
        help="Number of symbols per Yahoo download request (default: 10).",
    )
    parser.add_argument(
        "--sleep-seconds",
        type=float,
        default=1.0,
        help="Delay between chunk requests to reduce rate-limit pressure (default: 1.0).",
    )
    return parser.parse_args()


def chunked(items: list[str], size: int) -> list[list[str]]:
    return [items[index : index + size] for index in range(0, len(items), size)]


def extract_rows_from_download(data, symbols: list[str]) -> tuple[list, list[tuple[str, str]]]:
    rows = []
    failures: list[tuple[str, str]] = []

    if data.empty:
        for symbol in symbols:
            failures.append((symbol, f"{symbol}: no price history returned for requested period"))
        return rows, failures

    for symbol in symbols:
        try:
            if len(symbols) == 1:
                symbol_frame = data
            else:
                symbol_frame = data[symbol]

            symbol_frame = symbol_frame.dropna(how="all")
            if symbol_frame.empty:
                raise ValueError(f"{symbol}: no price history returned for requested period")

            for date_value, price_row in symbol_frame.iterrows():
                rows.append(normalize_history_row(symbol, date_value, price_row))
        except Exception as exc:
            failures.append((symbol, str(exc)))

    return rows, failures


def fetch_backfill_rows(symbols: list[str], period: str, chunk_size: int, sleep_seconds: float) -> tuple[list, list[tuple[str, str]]]:
    all_rows = []
    failures: list[tuple[str, str]] = []
    chunks = chunked(symbols, chunk_size)

    for index, chunk in enumerate(chunks, start=1):
        logging.info("Fetching chunk %s/%s: %s", index, len(chunks), ", ".join(chunk))
        try:
            data = yf.download(
                tickers=chunk,
                period=period,
                interval="1d",
                auto_adjust=False,
                actions=False,
                group_by="ticker",
                progress=False,
                threads=False,
            )
            chunk_rows, chunk_failures = extract_rows_from_download(data, chunk)
            all_rows.extend(chunk_rows)
            failures.extend(chunk_failures)
        except Exception as exc:
            for symbol in chunk:
                failures.append((symbol, f"{symbol}: download request failed: {exc}"))

        if index < len(chunks):
            time.sleep(sleep_seconds)

    return all_rows, failures


def run() -> int:
    args = parse_args()
    if args.chunk_size < 1:
        raise ValueError("--chunk-size must be at least 1")
    if args.sleep_seconds < 0:
        raise ValueError("--sleep-seconds cannot be negative")

    ensure_directories()
    log_path = configure_logging("backfill")
    logging.info("Starting backfill for period=%s", args.period)

    try:
        symbols = read_symbols(args.symbols_file)
    except Exception as exc:
        logging.error("Unable to load symbols: %s", exc)
        logging.error("No daily CSV files written. Log file: %s", log_path)
        return 1

    rows, failures = fetch_backfill_rows(symbols, args.period, args.chunk_size, args.sleep_seconds)
    if not rows:
        logging.error("No valid rows were fetched. No backfill files written. Log file: %s", log_path)
        return 1

    grouped_rows = group_rows_by_date(rows)
    action_counts: dict[str, int] = defaultdict(int)
    for date_str in sorted(grouped_rows):
        action, _ = reconcile_daily_file(date_str, sorted(grouped_rows[date_str], key=lambda row: row.symbol), set(symbols), args.force)
        action_counts[action] += 1

    logging.info(
        "Backfill summary: %s wrote, %s merged, %s skipped across %s trading days",
        action_counts["wrote"],
        action_counts["merged"],
        action_counts["skipped"],
        len(grouped_rows),
    )

    if failures:
        for symbol, message in failures:
            logging.error("Failed to fetch %s: %s", symbol, message)
        logging.error("Completed with %s failed symbols. Log file: %s", len(failures), log_path)
        return 1

    logging.info("Completed successfully. Log file: %s", log_path)
    return 0


if __name__ == "__main__":
    sys.exit(run())
