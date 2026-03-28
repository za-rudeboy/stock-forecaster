#!/usr/bin/env python3

from __future__ import annotations

import csv
import json
import logging
import math
import shutil
import sys
from pathlib import Path

import matplotlib
import pandas as pd

from market_data_pipeline import (
    DAILY_DIR,
    INDICATOR_CHARTS_DIR,
    INDICATORS_DIR,
    SYMBOLS_FILE,
    configure_logging,
    ensure_directories,
    ensure_indicator_directories,
    read_symbols,
)


matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402


HISTORY_COLUMNS = [
    "date",
    "symbol",
    "close",
    "adj_close",
    "price_used",
    "price_basis",
    "sma_50",
    "sma_200",
    "ema_20",
    "volume",
    "rsi_14",
    "volume_avg_20",
    "volume_spike_ratio",
    "volume_spike",
    "ema_20_reclaim",
    "screen_rule_pass",
]
HISTORY_OUTPUT = INDICATORS_DIR / "indicators_history.csv"
SNAPSHOT_CSV_OUTPUT = INDICATORS_DIR / "latest_snapshot.csv"
SNAPSHOT_JSON_OUTPUT = INDICATORS_DIR / "latest_snapshot.json"


def clean_numeric(value: object) -> float | None:
    if value is None or value == "":
        return None

    number = float(value)
    if math.isnan(number):
        return None
    return number


def format_optional_number(value: object) -> str:
    if value is None or pd.isna(value):
        return ""
    return f"{float(value):.2f}"


def format_optional_bool(value: object) -> str:
    if value is None or pd.isna(value):
        return ""
    return "true" if bool(value) else "false"


def format_optional_text(value: object) -> str:
    if value is None or pd.isna(value):
        return ""
    return str(value)


def read_daily_history(daily_dir: Path, symbols: set[str]) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    daily_files = sorted(daily_dir.glob("*.csv"))
    if not daily_files:
        raise FileNotFoundError(f"No daily CSV files found in {daily_dir}")

    for daily_file in daily_files:
        with daily_file.open(encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            for raw_row in reader:
                symbol = raw_row["symbol"]
                if symbol not in symbols:
                    continue
                rows.append(
                    {
                        "date": raw_row["date"],
                        "symbol": symbol,
                        "close": clean_numeric(raw_row["close"]),
                        "adj_close": clean_numeric(raw_row["adj_close"]),
                        "volume": clean_numeric(raw_row["volume"]),
                    }
                )

    if not rows:
        raise ValueError("No matching symbol rows were found in the daily CSV files")

    history = pd.DataFrame(rows)
    history["date"] = pd.to_datetime(history["date"], format="%Y-%m-%d")
    history = history.sort_values(["symbol", "date"], ascending=[True, True], kind="stable")
    history = history.drop_duplicates(subset=["symbol", "date"], keep="last")
    return history


def compute_indicators(history: pd.DataFrame) -> pd.DataFrame:
    history = history.copy()
    history["price_used"] = history["adj_close"].where(history["adj_close"].notna(), history["close"])
    history["price_basis"] = history["adj_close"].apply(lambda value: "adj_close" if pd.notna(value) else "close")

    by_symbol = history.groupby("symbol", group_keys=False, sort=False)
    history["sma_50"] = by_symbol["price_used"].transform(lambda series: series.rolling(window=50, min_periods=50).mean())
    history["sma_200"] = by_symbol["price_used"].transform(lambda series: series.rolling(window=200, min_periods=200).mean())
    history["ema_20"] = by_symbol["price_used"].transform(lambda series: series.ewm(span=20, adjust=False, min_periods=20).mean())
    history["rsi_14"] = by_symbol["price_used"].transform(compute_rsi_14)
    history["volume_avg_20"] = by_symbol["volume"].transform(lambda series: series.rolling(window=20, min_periods=20).mean())
    history["volume_spike_ratio"] = history["volume"] / history["volume_avg_20"]
    history.loc[history["volume_avg_20"].isna(), "volume_spike_ratio"] = pd.NA
    history["volume_spike"] = history["volume_spike_ratio"].apply(classify_volume_spike)
    history["ema_20_reclaim"] = compute_ema_reclaim(history)

    has_screen_inputs = (
        history["sma_50"].notna()
        & history["sma_200"].notna()
        & history["rsi_14"].notna()
        & history["ema_20_reclaim"].notna()
    )
    screen_condition = (
        (history["price_used"] > history["sma_200"])
        & (history["price_used"] > history["sma_50"])
        & (history["rsi_14"] > 50.0)
        & (history["ema_20_reclaim"] == True)
    )
    history["screen_rule_pass"] = pd.Series(pd.NA, index=history.index, dtype="boolean")
    history.loc[has_screen_inputs, "screen_rule_pass"] = screen_condition.loc[has_screen_inputs].astype("boolean")

    return history


def compute_rsi_14(series: pd.Series) -> pd.Series:
    delta = series.diff()
    gains = delta.clip(lower=0)
    losses = -delta.clip(upper=0)
    average_gain = gains.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    average_loss = losses.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    rs = average_gain / average_loss
    rsi = 100.0 - (100.0 / (1.0 + rs))
    rsi = rsi.where(average_loss != 0, 100.0)
    rsi = rsi.where(average_gain != 0, 0.0)
    no_movement = (average_gain == 0) & (average_loss == 0)
    return rsi.where(~no_movement, 50.0)


def classify_volume_spike(value: object) -> str | None:
    if value is None or pd.isna(value):
        return None
    return "spike" if float(value) >= 1.5 else "normal"


def compute_ema_reclaim(history: pd.DataFrame) -> pd.Series:
    prior_price = history.groupby("symbol", sort=False)["price_used"].shift(1)
    prior_ema = history.groupby("symbol", sort=False)["ema_20"].shift(1)
    above_ema = history["price_used"] > history["ema_20"]
    prior_below_ema = prior_price < prior_ema
    has_context = history["ema_20"].notna() & prior_ema.notna()
    reclaim = prior_below_ema & above_ema
    result = pd.Series(pd.NA, index=history.index, dtype="boolean")
    result.loc[has_context] = reclaim.loc[has_context].astype("boolean")
    return result


def write_history_csv(history: pd.DataFrame, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    formatted = history.copy()
    formatted["date"] = formatted["date"].dt.strftime("%Y-%m-%d")

    with output_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=HISTORY_COLUMNS)
        writer.writeheader()
        for _, row in formatted.iterrows():
            writer.writerow(
                {
                    "date": row["date"],
                    "symbol": row["symbol"],
                    "close": format_optional_number(row["close"]),
                    "adj_close": format_optional_number(row["adj_close"]),
                    "price_used": format_optional_number(row["price_used"]),
                    "price_basis": row["price_basis"],
                    "sma_50": format_optional_number(row["sma_50"]),
                    "sma_200": format_optional_number(row["sma_200"]),
                    "ema_20": format_optional_number(row["ema_20"]),
                    "volume": format_optional_number(row["volume"]),
                    "rsi_14": format_optional_number(row["rsi_14"]),
                    "volume_avg_20": format_optional_number(row["volume_avg_20"]),
                    "volume_spike_ratio": format_optional_number(row["volume_spike_ratio"]),
                    "volume_spike": format_optional_text(row["volume_spike"]),
                    "ema_20_reclaim": format_optional_bool(row["ema_20_reclaim"]),
                    "screen_rule_pass": format_optional_bool(row["screen_rule_pass"]),
                }
            )


def write_latest_snapshot(history: pd.DataFrame) -> None:
    latest = history.groupby("symbol", group_keys=False).tail(1).sort_values("symbol", kind="stable")
    latest_formatted = latest.copy()
    latest_formatted["date"] = latest_formatted["date"].dt.strftime("%Y-%m-%d")

    with SNAPSHOT_CSV_OUTPUT.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=HISTORY_COLUMNS)
        writer.writeheader()
        for _, row in latest_formatted.iterrows():
            writer.writerow(
                {
                    "date": row["date"],
                    "symbol": row["symbol"],
                    "close": format_optional_number(row["close"]),
                    "adj_close": format_optional_number(row["adj_close"]),
                    "price_used": format_optional_number(row["price_used"]),
                    "price_basis": row["price_basis"],
                    "sma_50": format_optional_number(row["sma_50"]),
                    "sma_200": format_optional_number(row["sma_200"]),
                    "ema_20": format_optional_number(row["ema_20"]),
                    "volume": format_optional_number(row["volume"]),
                    "rsi_14": format_optional_number(row["rsi_14"]),
                    "volume_avg_20": format_optional_number(row["volume_avg_20"]),
                    "volume_spike_ratio": format_optional_number(row["volume_spike_ratio"]),
                    "volume_spike": format_optional_text(row["volume_spike"]),
                    "ema_20_reclaim": format_optional_bool(row["ema_20_reclaim"]),
                    "screen_rule_pass": format_optional_bool(row["screen_rule_pass"]),
                }
            )

    json_rows: list[dict[str, object]] = []
    for _, row in latest_formatted.iterrows():
        json_rows.append(
            {
                "date": row["date"],
                "symbol": row["symbol"],
                "close": None if pd.isna(row["close"]) else float(row["close"]),
                "adj_close": None if pd.isna(row["adj_close"]) else float(row["adj_close"]),
                "price_used": None if pd.isna(row["price_used"]) else float(row["price_used"]),
                "price_basis": row["price_basis"],
                "sma_50": None if pd.isna(row["sma_50"]) else float(row["sma_50"]),
                "sma_200": None if pd.isna(row["sma_200"]) else float(row["sma_200"]),
                "ema_20": None if pd.isna(row["ema_20"]) else float(row["ema_20"]),
                "volume": None if pd.isna(row["volume"]) else float(row["volume"]),
                "rsi_14": None if pd.isna(row["rsi_14"]) else float(row["rsi_14"]),
                "volume_avg_20": None if pd.isna(row["volume_avg_20"]) else float(row["volume_avg_20"]),
                "volume_spike_ratio": None if pd.isna(row["volume_spike_ratio"]) else float(row["volume_spike_ratio"]),
                "volume_spike": None if pd.isna(row["volume_spike"]) else str(row["volume_spike"]),
                "ema_20_reclaim": None if pd.isna(row["ema_20_reclaim"]) else bool(row["ema_20_reclaim"]),
                "screen_rule_pass": None if pd.isna(row["screen_rule_pass"]) else bool(row["screen_rule_pass"]),
            }
        )

    with SNAPSHOT_JSON_OUTPUT.open("w", encoding="utf-8") as handle:
        json.dump(json_rows, handle, indent=2)
        handle.write("\n")


def render_charts(history: pd.DataFrame) -> None:
    if INDICATOR_CHARTS_DIR.exists():
        shutil.rmtree(INDICATOR_CHARTS_DIR)
    INDICATOR_CHARTS_DIR.mkdir(parents=True, exist_ok=True)

    for symbol, frame in history.groupby("symbol"):
        symbol_frame = frame.sort_values("date", kind="stable")
        fig, ax = plt.subplots(figsize=(12, 6))
        ax.plot(symbol_frame["date"], symbol_frame["price_used"], label="Price Used", linewidth=2.0)
        ax.plot(symbol_frame["date"], symbol_frame["sma_50"], label="SMA 50", linewidth=1.6)
        ax.plot(symbol_frame["date"], symbol_frame["sma_200"], label="SMA 200", linewidth=1.6)
        ax.plot(symbol_frame["date"], symbol_frame["ema_20"], label="EMA 20", linewidth=1.6)
        ax.set_title(f"{symbol} Price and Moving Averages")
        ax.set_xlabel("Date")
        ax.set_ylabel("Price")
        ax.grid(True, linestyle="--", linewidth=0.5, alpha=0.5)
        ax.legend()
        fig.autofmt_xdate()
        fig.tight_layout()
        fig.savefig(INDICATOR_CHARTS_DIR / f"{symbol}.png", dpi=150)
        plt.close(fig)


def run() -> int:
    ensure_directories()
    ensure_indicator_directories()
    log_path = configure_logging("indicators")
    logging.info("Starting indicator calculation")

    try:
        symbols = set(read_symbols(SYMBOLS_FILE))
        history = read_daily_history(DAILY_DIR, symbols)
        enriched = compute_indicators(history)
        write_history_csv(enriched, HISTORY_OUTPUT)
        write_latest_snapshot(enriched)
        render_charts(enriched)
    except Exception as exc:
        logging.error("Indicator calculation failed: %s", exc)
        logging.error("No complete indicator output produced. Log file: %s", log_path)
        return 1

    logging.info("Wrote indicator history to %s", HISTORY_OUTPUT)
    logging.info("Wrote latest snapshot to %s and %s", SNAPSHOT_CSV_OUTPUT, SNAPSHOT_JSON_OUTPUT)
    logging.info("Wrote charts to %s", INDICATOR_CHARTS_DIR)
    logging.info("Completed successfully. Log file: %s", log_path)
    return 0


if __name__ == "__main__":
    sys.exit(run())
