from __future__ import annotations

import argparse
import csv
import logging
import math
import os
import tempfile
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterable

import pandas as pd
import yfinance as yf


ROOT_DIR = Path(__file__).resolve().parent
DATA_DIR = ROOT_DIR / "data"
DAILY_DIR = DATA_DIR / "daily"
LOG_DIR = DATA_DIR / "logs"
INDICATORS_DIR = DATA_DIR / "indicators"
INDICATOR_CHARTS_DIR = INDICATORS_DIR / "charts"
ANALYSIS_DIR = DATA_DIR / "analysis"
SYMBOLS_FILE = DATA_DIR / "symbols.txt"
POSITIONS_FILE = DATA_DIR / "positions.csv"
FOCUS_SYMBOLS_FILE = DATA_DIR / "focus_symbols.txt"
CSV_COLUMNS = ["date", "symbol", "open", "high", "low", "close", "adj_close", "volume"]
PRICE_FIELDS = ("open", "high", "low", "close", "adj_close")


@dataclass(frozen=True)
class NormalizedRow:
    date: str
    symbol: str
    open: float
    high: float
    low: float
    close: float
    adj_close: float
    volume: int

    def as_csv_row(self) -> dict[str, str]:
        return {
            "date": self.date,
            "symbol": self.symbol,
            "open": f"{self.open:.2f}",
            "high": f"{self.high:.2f}",
            "low": f"{self.low:.2f}",
            "close": f"{self.close:.2f}",
            "adj_close": f"{self.adj_close:.2f}",
            "volume": str(self.volume),
        }


@dataclass(frozen=True)
class PositionRow:
    symbol: str
    status: str
    opened_on: str | None = None
    notes: str | None = None


def ensure_directories() -> None:
    DAILY_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)


def ensure_indicator_directories() -> None:
    INDICATORS_DIR.mkdir(parents=True, exist_ok=True)
    INDICATOR_CHARTS_DIR.mkdir(parents=True, exist_ok=True)


def ensure_analysis_directories() -> None:
    ANALYSIS_DIR.mkdir(parents=True, exist_ok=True)


def configure_logging(prefix: str) -> Path:
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    log_path = LOG_DIR / f"{prefix}_{timestamp}.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[
            logging.FileHandler(log_path, encoding="utf-8"),
            logging.StreamHandler(),
        ],
        force=True,
    )
    return log_path


def build_common_parser(description: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument(
        "--symbols-file",
        type=Path,
        default=SYMBOLS_FILE,
        help=f"Path to the symbol watchlist file (default: {SYMBOLS_FILE})",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite dated CSV files even when they already contain all requested symbols.",
    )
    return parser


def read_symbols(path: Path) -> list[str]:
    if not path.exists():
        raise FileNotFoundError(f"Symbols file not found: {path}")

    symbols: list[str] = []
    with path.open(encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.split("#", 1)[0].strip()
            if not line:
                continue
            symbols.append(line)

    if not symbols:
        raise ValueError(f"No symbols found in {path}")

    return symbols


def read_positions(path: Path) -> list[PositionRow]:
    if not path.exists():
        return []

    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise ValueError(f"Positions file has no header: {path}")

        required = {"symbol", "status"}
        missing = required.difference(reader.fieldnames)
        if missing:
            raise ValueError(f"Positions file missing required columns: {sorted(missing)}")

        positions: list[PositionRow] = []
        for raw in reader:
            symbol = (raw.get("symbol") or "").split("#", 1)[0].strip()
            status = (raw.get("status") or "").split("#", 1)[0].strip().lower()
            if not symbol or not status:
                continue
            positions.append(
                PositionRow(
                    symbol=symbol,
                    status=status,
                    opened_on=(raw.get("opened_on") or "").strip() or None,
                    notes=(raw.get("notes") or "").strip() or None,
                )
            )

    return positions


def normalize_float(value: object, field_name: str, symbol: str) -> float:
    if value is None:
        raise ValueError(f"{symbol}: missing {field_name}")

    number = float(value)
    if math.isnan(number):
        raise ValueError(f"{symbol}: invalid {field_name}")
    return number


def normalize_int(value: object, field_name: str, symbol: str) -> int:
    if value is None:
        raise ValueError(f"{symbol}: missing {field_name}")

    number = int(value)
    if number < 0:
        raise ValueError(f"{symbol}: negative {field_name}")
    return number


def normalize_history_row(symbol: str, date_value: object, price_row: pd.Series) -> NormalizedRow:
    date_str = pd.Timestamp(date_value).strftime("%Y-%m-%d")
    values = {
        "open": price_row.get("Open"),
        "high": price_row.get("High"),
        "low": price_row.get("Low"),
        "close": price_row.get("Close"),
        "adj_close": price_row.get("Adj Close"),
        "volume": price_row.get("Volume"),
    }

    normalized_prices = {
        field: normalize_float(value, field, symbol)
        for field, value in values.items()
        if field in PRICE_FIELDS
    }

    return NormalizedRow(
        date=date_str,
        symbol=symbol,
        open=normalized_prices["open"],
        high=normalized_prices["high"],
        low=normalized_prices["low"],
        close=normalized_prices["close"],
        adj_close=normalized_prices["adj_close"],
        volume=normalize_int(values["volume"], "volume", symbol),
    )


def fetch_history_rows(symbol: str, **history_kwargs: object) -> list[NormalizedRow]:
    history = yf.Ticker(symbol).history(auto_adjust=False, actions=False, **history_kwargs)
    if history.empty:
        raise ValueError(f"{symbol}: no price history returned")

    rows: list[NormalizedRow] = []
    for date_value, price_row in history.iterrows():
        rows.append(normalize_history_row(symbol, date_value, price_row))
    return rows


def fetch_latest_completed_candle(symbol: str) -> NormalizedRow:
    rows = fetch_history_rows(symbol, period="10d", interval="1d")
    return rows[-1]


def group_rows_by_date(rows: Iterable[NormalizedRow]) -> dict[str, list[NormalizedRow]]:
    grouped: dict[str, list[NormalizedRow]] = defaultdict(list)
    for row in rows:
        grouped[row.date].append(row)
    return dict(grouped)


def read_existing_rows(path: Path) -> list[NormalizedRow]:
    if not path.exists():
        return []

    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        rows: list[NormalizedRow] = []
        for raw in reader:
            rows.append(
                NormalizedRow(
                    date=raw["date"],
                    symbol=raw["symbol"],
                    open=float(raw["open"]),
                    high=float(raw["high"]),
                    low=float(raw["low"]),
                    close=float(raw["close"]),
                    adj_close=float(raw["adj_close"]),
                    volume=int(raw["volume"]),
                )
            )
    return rows


def write_daily_csv(output_path: Path, rows: Iterable[NormalizedRow]) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        newline="",
        delete=False,
        dir=output_path.parent,
        prefix=f".{output_path.stem}_",
        suffix=".tmp",
    ) as tmp_file:
        writer = csv.DictWriter(tmp_file, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow(row.as_csv_row())
        temp_name = tmp_file.name

    os.replace(temp_name, output_path)


def merge_rows(existing_rows: Iterable[NormalizedRow], new_rows: Iterable[NormalizedRow]) -> list[NormalizedRow]:
    merged: dict[str, NormalizedRow] = {row.symbol: row for row in existing_rows}
    for row in new_rows:
        merged[row.symbol] = row
    return sorted(merged.values(), key=lambda row: row.symbol)


def reconcile_daily_file(
    date_str: str,
    fetched_rows: list[NormalizedRow],
    expected_symbols: set[str],
    force: bool,
) -> tuple[str, int]:
    output_path = DAILY_DIR / f"{date_str}.csv"
    existing_rows = read_existing_rows(output_path)
    existing_symbols = {row.symbol for row in existing_rows}

    if not force and output_path.exists() and expected_symbols.issubset(existing_symbols):
        logging.info("Skipping %s; existing file already contains all %s requested symbols", output_path, len(expected_symbols))
        return "skipped", len(existing_rows)

    rows_to_write = fetched_rows
    action = "wrote"

    if output_path.exists() and not force:
        missing_rows = [row for row in fetched_rows if row.symbol not in existing_symbols]
        if not missing_rows:
            logging.info("Skipping %s; no missing symbols were fetched for this date", output_path)
            return "skipped", len(existing_rows)
        rows_to_write = merge_rows(existing_rows, missing_rows)
        action = "merged"

    if force:
        rows_to_write = sorted(fetched_rows, key=lambda row: row.symbol)

    write_daily_csv(output_path, rows_to_write)
    logging.info("%s %s with %s rows", action.capitalize(), output_path, len(rows_to_write))
    return action, len(rows_to_write)
