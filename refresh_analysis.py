#!/usr/bin/env python3

from __future__ import annotations

import json
import sys
from pathlib import Path

import calculate_indicators
import generate_indicator_report
from market_data_pipeline import ANALYSIS_DIR


REPORT_JSON_PATH = ANALYSIS_DIR / "latest_indicator_report.json"


def print_section(title: str, symbols: list[str], reports_by_symbol: dict[str, dict[str, object]]) -> None:
    print(title)
    if not symbols:
        print("- none")
        print()
        return

    for symbol in symbols:
        report = reports_by_symbol.get(symbol, {})
        priority = report.get("review_priority", "n/a")
        position_status = report.get("position_status", "n/a")
        is_focus_symbol = report.get("is_focus_symbol", False)
        print(
            f"- {symbol}: priority={priority}, position_status={position_status}, "
            f"is_focus_symbol={str(is_focus_symbol).lower()}"
        )
    print()


def load_report_payload(path: Path) -> dict[str, object]:
    if not path.exists():
        raise FileNotFoundError(f"Expected report output not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def run() -> int:
    indicators_exit = calculate_indicators.run()
    if indicators_exit != 0:
        return indicators_exit

    report_exit = generate_indicator_report.run()
    if report_exit != 0:
        return report_exit

    payload = load_report_payload(REPORT_JSON_PATH)
    screening = payload["screening"]
    reports_by_symbol = {item["symbol"]: item for item in payload["symbols"]}

    print()
    print(f"Refreshed analysis for {payload['as_of_date']}")
    print(f"JSON report: {REPORT_JSON_PATH}")
    print()
    print_section("Open Positions", screening["open_positions"], reports_by_symbol)
    print_section("Focus Symbols", screening["focus_symbols"], reports_by_symbol)
    return 0


if __name__ == "__main__":
    sys.exit(run())
