#!/usr/bin/env python3

from __future__ import annotations

import csv
import json
import logging
import math
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from market_data_pipeline import (
    ANALYSIS_DIR,
    INDICATORS_DIR,
    SYMBOLS_FILE,
    configure_logging,
    ensure_analysis_directories,
    ensure_directories,
    ensure_indicator_directories,
    read_symbols,
)


SNAPSHOT_INPUT = INDICATORS_DIR / "latest_snapshot.csv"
REPORT_MD_OUTPUT = ANALYSIS_DIR / "latest_indicator_report.md"
REPORT_JSON_OUTPUT = ANALYSIS_DIR / "latest_indicator_report.json"
REQUIRED_COLUMNS = {
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
}
OPTIONAL_COLUMNS = ["macd", "macd_signal", "macd_histogram"]
DETAILED_SYMBOL_LIMIT = 5
SHORTLIST_LIMIT = 5


@dataclass(frozen=True)
class SymbolReport:
    symbol: str
    as_of_date: str
    price_used: float
    sma_50: float | None
    sma_200: float | None
    ema_20: float | None
    volume: float | None
    rsi_14: float | None
    volume_avg_20: float | None
    volume_spike_ratio: float | None
    volume_spike: str | None
    ema_20_reclaim: bool | None
    screen_rule_pass: bool | None
    price_basis: str
    long_term: str
    medium_term: str
    timing: str
    momentum: str
    confirmation: str
    overall_state: str
    story: str
    caution: str
    strengthen_next: str
    weaken_next: str
    review_priority: str
    review_score: float
    observed_metrics: dict[str, float | None]
    optional_indicators: dict[str, float | str | None]


def parse_optional_float(value: str | None) -> float | None:
    if value in (None, ""):
        return None
    number = float(value)
    if math.isnan(number):
        return None
    return number


def parse_optional_bool(value: str | None) -> bool | None:
    if value in (None, ""):
        return None
    lowered = value.strip().lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    raise ValueError(f"Invalid boolean value: {value}")


def percent_gap(price: float, moving_average: float | None) -> float | None:
    if moving_average in (None, 0):
        return None
    return ((price / moving_average) - 1.0) * 100.0


def classify_long_term(price: float, sma_200: float | None) -> str:
    if sma_200 is None:
        return "insufficient_history"
    return "constructive" if price >= sma_200 else "weak"


def classify_medium_term(price: float, sma_50: float | None, sma_200: float | None) -> str:
    if sma_50 is None:
        return "insufficient_history"
    if sma_200 is not None and sma_50 >= sma_200 and price >= sma_50:
        return "positive"
    if sma_200 is not None and sma_50 < sma_200 and price < sma_50:
        return "negative"
    if price >= sma_50:
        return "improving"
    return "pullback"


def classify_timing(price: float, ema_20: float | None) -> str:
    if ema_20 is None:
        return "insufficient_history"
    gap = percent_gap(price, ema_20)
    if gap is None:
        return "insufficient_history"
    if gap >= 1.0:
        return "strong"
    if gap >= 0:
        return "firm"
    if gap > -1.5:
        return "neutral_to_soft"
    return "weak"


def classify_momentum(rsi_14: float | None) -> str:
    if rsi_14 is None:
        return "insufficient_history"
    if rsi_14 >= 65:
        return "strong"
    if rsi_14 > 50:
        return "constructive"
    if rsi_14 >= 40:
        return "neutral"
    return "weak"


def classify_confirmation(volume_spike: str | None, volume_spike_ratio: float | None) -> str:
    if volume_spike is None or volume_spike_ratio is None:
        return "insufficient_history"
    if volume_spike == "spike":
        return "high_participation"
    if volume_spike_ratio >= 0.9:
        return "adequate"
    return "light"


def build_story(
    symbol: str,
    long_term: str,
    medium_term: str,
    timing: str,
    momentum: str,
    confirmation: str,
    screen_rule_pass: bool | None,
) -> tuple[str, str, str, str, str]:
    if screen_rule_pass:
        overall_state = "screen_pass_candidate"
        story = (
            f"{symbol} passes the current first-pass screen: it is above both long- and medium-term trend lines, "
            "RSI is on the constructive side of 50, and price has just reclaimed the 20 EMA."
        )
        caution = "A fresh reclaim is an early timing event, so it needs follow-through rather than a one-day burst."
        strengthen_next = "Stay above the 20 EMA, keep RSI above 50, and expand above the 50 SMA."
        weaken_next = "Lose the 20 EMA quickly or let RSI slip back below 50."
        return overall_state, story, caution, strengthen_next, weaken_next

    if long_term == "constructive" and medium_term in {"positive", "improving"} and timing in {"strong", "firm"} and momentum in {"strong", "constructive"}:
        overall_state = "trend_strength"
        story = (
            f"{symbol} is aligned across the bigger trend and the nearer-term trend, with momentum on its side. "
            f"Volume confirmation is {confirmation.replace('_', ' ')}."
        )
        caution = "Short-term strength can still fail if momentum rolls over quickly."
        strengthen_next = "Hold above the 20 EMA and continue separating above the 50 SMA."
        weaken_next = "Slip back under the 20 EMA and then lose the 50 SMA."
        return overall_state, story, caution, strengthen_next, weaken_next

    if long_term == "constructive" and medium_term in {"pullback", "negative"}:
        overall_state = "constructive_pullback"
        story = (
            f"{symbol} still has a supportive longer-term structure, but the stock is in a pullback or repair phase rather than a clean upswing. "
            f"Momentum is {momentum.replace('_', ' ')} and volume confirmation is {confirmation.replace('_', ' ')}."
        )
        caution = "If the pullback continues and the 200 SMA breaks, the bigger-picture read worsens materially."
        strengthen_next = "Reclaim the 20 EMA first, then the 50 SMA."
        weaken_next = "Stay pinned under the 20 EMA and drift down toward the 200 SMA."
        return overall_state, story, caution, strengthen_next, weaken_next

    if long_term == "weak":
        overall_state = "downtrend_or_damage"
        story = (
            f"{symbol} is operating below its long-term trend line, so the chart is in repair mode rather than in a healthy trend. "
            f"Momentum is {momentum.replace('_', ' ')}."
        )
        caution = "A cheap-looking price is not the same thing as a healthy setup."
        strengthen_next = "Get back above the 20 EMA, then the 50 SMA, and eventually reclaim the 200 SMA."
        weaken_next = "Fail every bounce under the 20 EMA or continue printing lower prices under the 50 SMA."
        return overall_state, story, caution, strengthen_next, weaken_next

    overall_state = "mixed"
    story = (
        f"{symbol} has mixed evidence across the trend layers, so the setup is not decisive yet. "
        f"Momentum is {momentum.replace('_', ' ')} and volume confirmation is {confirmation.replace('_', ' ')}."
    )
    caution = "Mixed charts can create false starts in both directions."
    strengthen_next = "Let short-term price action start agreeing with the broader trend."
    weaken_next = "Let the weaker trend layer take control of the chart."
    return overall_state, story, caution, strengthen_next, weaken_next


def score_symbol(
    long_term: str,
    medium_term: str,
    timing: str,
    momentum: str,
    confirmation: str,
    screen_rule_pass: bool | None,
    observed_metrics: dict[str, float | None],
) -> tuple[float, str]:
    score = 0.0

    if long_term == "constructive":
        score += 3.0
    elif long_term == "weak":
        score -= 3.0

    medium_weights = {
        "positive": 2.0,
        "improving": 1.5,
        "pullback": 0.5,
        "negative": -1.5,
    }
    score += medium_weights.get(medium_term, 0.0)

    timing_weights = {
        "strong": 1.5,
        "firm": 1.0,
        "neutral_to_soft": 0.25,
        "weak": -1.0,
    }
    score += timing_weights.get(timing, 0.0)

    momentum_weights = {
        "strong": 1.5,
        "constructive": 1.0,
        "neutral": 0.0,
        "weak": -1.0,
    }
    score += momentum_weights.get(momentum, 0.0)

    confirmation_weights = {
        "high_participation": 0.75,
        "adequate": 0.25,
        "light": -0.25,
    }
    score += confirmation_weights.get(confirmation, 0.0)

    for metric_name in ("vs_sma_200_pct", "vs_sma_50_pct", "vs_ema_20_pct"):
        metric_value = observed_metrics.get(metric_name)
        if metric_value is not None:
            score += max(min(metric_value / 10.0, 1.5), -1.5)

    if screen_rule_pass:
        score += 3.0
        priority = "review_now"
    elif long_term == "constructive" and medium_term in {"positive", "improving"}:
        priority = "review_now"
    elif long_term == "constructive" and medium_term == "pullback":
        priority = "watch_pullback"
    elif long_term == "weak":
        priority = "avoid_for_now"
    else:
        priority = "mixed_watch"

    return score, priority


def load_snapshot(path: Path, symbols: set[str]) -> list[dict[str, str]]:
    if not path.exists():
        raise FileNotFoundError(f"Snapshot input not found: {path}")

    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise ValueError(f"Snapshot input has no header: {path}")
        missing = REQUIRED_COLUMNS.difference(reader.fieldnames)
        if missing:
            raise ValueError(f"Snapshot input missing required columns: {sorted(missing)}")
        rows = [row for row in reader if row["symbol"] in symbols]

    if not rows:
        raise ValueError("No matching symbol rows found in latest snapshot")
    return rows


def build_symbol_report(row: dict[str, str]) -> SymbolReport:
    price_used = parse_optional_float(row["price_used"])
    if price_used is None:
        raise ValueError(f"{row['symbol']}: missing price_used in latest snapshot")

    sma_50 = parse_optional_float(row["sma_50"])
    sma_200 = parse_optional_float(row["sma_200"])
    ema_20 = parse_optional_float(row["ema_20"])
    volume = parse_optional_float(row["volume"])
    rsi_14 = parse_optional_float(row["rsi_14"])
    volume_avg_20 = parse_optional_float(row["volume_avg_20"])
    volume_spike_ratio = parse_optional_float(row["volume_spike_ratio"])
    volume_spike = row["volume_spike"] or None
    ema_20_reclaim = parse_optional_bool(row["ema_20_reclaim"])
    screen_rule_pass = parse_optional_bool(row["screen_rule_pass"])

    long_term = classify_long_term(price_used, sma_200)
    medium_term = classify_medium_term(price_used, sma_50, sma_200)
    timing = classify_timing(price_used, ema_20)
    momentum = classify_momentum(rsi_14)
    confirmation = classify_confirmation(volume_spike, volume_spike_ratio)
    overall_state, story, caution, strengthen_next, weaken_next = build_story(
        row["symbol"], long_term, medium_term, timing, momentum, confirmation, screen_rule_pass
    )
    observed_metrics = {
        "vs_sma_50_pct": percent_gap(price_used, sma_50),
        "vs_sma_200_pct": percent_gap(price_used, sma_200),
        "vs_ema_20_pct": percent_gap(price_used, ema_20),
    }
    review_score, review_priority = score_symbol(
        long_term, medium_term, timing, momentum, confirmation, screen_rule_pass, observed_metrics
    )

    optional_indicators: dict[str, float | str | None] = {}
    for column in OPTIONAL_COLUMNS:
        if column not in row:
            continue
        optional_indicators[column] = parse_optional_float(row[column])

    return SymbolReport(
        symbol=row["symbol"],
        as_of_date=row["date"],
        price_used=price_used,
        sma_50=sma_50,
        sma_200=sma_200,
        ema_20=ema_20,
        volume=volume,
        rsi_14=rsi_14,
        volume_avg_20=volume_avg_20,
        volume_spike_ratio=volume_spike_ratio,
        volume_spike=volume_spike,
        ema_20_reclaim=ema_20_reclaim,
        screen_rule_pass=screen_rule_pass,
        price_basis=row["price_basis"],
        long_term=long_term,
        medium_term=medium_term,
        timing=timing,
        momentum=momentum,
        confirmation=confirmation,
        overall_state=overall_state,
        story=story,
        caution=caution,
        strengthen_next=strengthen_next,
        weaken_next=weaken_next,
        review_priority=review_priority,
        review_score=review_score,
        observed_metrics=observed_metrics,
        optional_indicators=optional_indicators,
    )


def basket_summary(reports: list[SymbolReport]) -> tuple[dict[str, int], str]:
    counts = {
        "total_symbols": len(reports),
        "above_sma_200": sum(1 for report in reports if report.sma_200 is not None and report.price_used >= report.sma_200),
        "above_sma_50": sum(1 for report in reports if report.sma_50 is not None and report.price_used >= report.sma_50),
        "above_ema_20": sum(1 for report in reports if report.ema_20 is not None and report.price_used >= report.ema_20),
        "rsi_above_50": sum(1 for report in reports if report.rsi_14 is not None and report.rsi_14 > 50.0),
        "fresh_ema_20_reclaims": sum(1 for report in reports if report.ema_20_reclaim is True),
        "screen_rule_pass": sum(1 for report in reports if report.screen_rule_pass is True),
        "volume_spikes": sum(1 for report in reports if report.volume_spike == "spike"),
        "constructive_pullbacks": sum(1 for report in reports if report.overall_state == "constructive_pullback"),
        "downtrend_or_damage": sum(1 for report in reports if report.overall_state == "downtrend_or_damage"),
    }

    narrative = (
        f"{counts['above_sma_200']} of {counts['total_symbols']} symbols remain above the 200 SMA, "
        f"{counts['above_sma_50']} are above the 50 SMA, and {counts['rsi_above_50']} have RSI above 50. "
        f"{counts['fresh_ema_20_reclaims']} have just reclaimed the 20 EMA, while {counts['screen_rule_pass']} pass the full first-pass screen. "
        "That gives you a tighter shortlist than using moving averages alone."
    )
    return counts, narrative


def format_pct(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:.1f}%"


def format_bool(value: bool | None) -> str:
    if value is None:
        return "n/a"
    return "yes" if value else "no"


def shortlist_reports(reports: list[SymbolReport]) -> list[SymbolReport]:
    return sorted(reports, key=lambda report: (-report.review_score, report.symbol))[:SHORTLIST_LIMIT]


def grouped_reports(reports: list[SymbolReport]) -> dict[str, list[SymbolReport]]:
    groups = {
        "review_now": [],
        "watch_pullback": [],
        "mixed_watch": [],
        "avoid_for_now": [],
    }
    for report in sorted(reports, key=lambda item: (-item.review_score, item.symbol)):
        groups.setdefault(report.review_priority, []).append(report)
    return groups


def shortlist_table_lines(title: str, reports: list[SymbolReport]) -> list[str]:
    lines = [f"## {title}", ""]
    if not reports:
        lines.extend(["No symbols in this bucket right now.", ""])
        return lines

    lines.extend(
        [
            "| Symbol | Priority | Score | RSI 14 | EMA reclaim | Volume | Screen pass |",
            "| --- | --- | ---: | ---: | --- | --- | --- |",
        ]
    )
    for report in reports:
        lines.append(
            "| "
            f"{report.symbol} | "
            f"{report.review_priority} | "
            f"{report.review_score:.2f} | "
            f"{'n/a' if report.rsi_14 is None else f'{report.rsi_14:.1f}'} | "
            f"{format_bool(report.ema_20_reclaim)} | "
            f"{report.volume_spike or 'n/a'} | "
            f"{format_bool(report.screen_rule_pass)} |"
        )
    lines.append("")
    return lines


def write_markdown_report(reports: list[SymbolReport], counts: dict[str, int], summary_text: str) -> None:
    as_of_date = reports[0].as_of_date
    ranked_reports = sorted(reports, key=lambda report: (-report.review_score, report.symbol))
    weakest_reports = sorted(reports, key=lambda report: (report.review_score, report.symbol))
    top_shortlist = shortlist_reports(reports)
    buckets = grouped_reports(reports)
    detailed_reports = ranked_reports[: min(DETAILED_SYMBOL_LIMIT, len(ranked_reports))]

    lines = [
        "# Indicator Report",
        "",
        f"- As of: `{as_of_date}`",
        f"- Symbols covered: `{', '.join(report.symbol for report in reports)}`",
        "",
        "## Basket Summary",
        "",
        summary_text,
        "",
        f"- Above `SMA_200`: `{counts['above_sma_200']}/{counts['total_symbols']}`",
        f"- Above `SMA_50`: `{counts['above_sma_50']}/{counts['total_symbols']}`",
        f"- Above `EMA_20`: `{counts['above_ema_20']}/{counts['total_symbols']}`",
        f"- `RSI_14 > 50`: `{counts['rsi_above_50']}/{counts['total_symbols']}`",
        f"- Fresh `EMA_20` reclaims: `{counts['fresh_ema_20_reclaims']}`",
        f"- Screen-rule passes: `{counts['screen_rule_pass']}`",
        f"- Volume spikes: `{counts['volume_spikes']}`",
        f"- Review-now candidates: `{len(buckets['review_now'])}`",
        f"- Pullbacks worth watching: `{len(buckets['watch_pullback'])}`",
        f"- Avoid-for-now names: `{len(buckets['avoid_for_now'])}`",
    ]

    if top_shortlist:
        lines.append(f"- Highest-priority name right now: `{top_shortlist[0].symbol}`")
    if weakest_reports:
        lines.append(f"- Weakest setup right now: `{weakest_reports[0].symbol}`")

    lines.extend(["", "## How To Use This Report", ""])
    lines.extend(
        [
            "- Start with the shortlist tables below; they are the screening layer for a larger watchlist.",
            "- Treat the first-pass screen as a candidate filter, not as a buy signal.",
            "- Use the detailed reads only for the highest-priority names, not every symbol in the universe.",
            "- Use the JSON report when another agent needs the same view in structured form.",
            "",
        ]
    )

    lines.extend(shortlist_table_lines("Top Review Candidates", top_shortlist))
    lines.extend(shortlist_table_lines("Constructive Pullbacks To Watch", buckets["watch_pullback"][:SHORTLIST_LIMIT]))
    lines.extend(shortlist_table_lines("Avoid For Now", buckets["avoid_for_now"][:SHORTLIST_LIMIT]))

    lines.extend(["## Detailed Reads", ""])

    for report in detailed_reports:
        lines.extend(
            [
                f"### {report.symbol}",
                "",
                f"- Review priority: `{report.review_priority}`",
                f"- Review score: `{report.review_score:.2f}`",
                f"- Long-term trend: `{report.long_term}`",
                f"- Medium trend: `{report.medium_term}`",
                f"- Timing: `{report.timing}`",
                f"- Momentum: `{report.momentum}`",
                f"- Confirmation: `{report.confirmation}`",
                f"- `RSI_14`: `{'n/a' if report.rsi_14 is None else f'{report.rsi_14:.2f}'}`",
                f"- Volume vs `20d avg`: `{'n/a' if report.volume_spike_ratio is None else f'{report.volume_spike_ratio:.2f}x'}`",
                f"- Volume flag: `{report.volume_spike or 'n/a'}`",
                f"- Fresh `EMA_20` reclaim: `{format_bool(report.ema_20_reclaim)}`",
                f"- Screen-rule pass: `{format_bool(report.screen_rule_pass)}`",
                f"- Price vs `SMA_200`: `{format_pct(report.observed_metrics['vs_sma_200_pct'])}`",
                f"- Price vs `SMA_50`: `{format_pct(report.observed_metrics['vs_sma_50_pct'])}`",
                f"- Price vs `EMA_20`: `{format_pct(report.observed_metrics['vs_ema_20_pct'])}`",
                f"- Story: {report.story}",
                f"- Caution: {report.caution}",
                f"- Strengthens if: {report.strengthen_next}",
                f"- Weakens if: {report.weaken_next}",
                "",
            ]
        )

    lines.extend(
        [
            "## Notes",
            "",
            "- Direct observations come from the latest indicator snapshot and derived percentage gaps to the moving averages.",
            "- Narrative language is an interpretation layer, not a prediction or personalized financial advice.",
            "- The markdown report is intentionally shortlist-first so it stays readable when the watchlist grows.",
            "- Future indicators such as MACD can be appended to the same report structure without changing the top-level sections.",
            "",
        ]
    )

    REPORT_MD_OUTPUT.write_text("\n".join(lines), encoding="utf-8")


def write_json_report(reports: list[SymbolReport], counts: dict[str, int], summary_text: str) -> None:
    ranked_reports = sorted(reports, key=lambda report: (-report.review_score, report.symbol))
    buckets = grouped_reports(reports)
    payload = {
        "generated_at_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "as_of_date": reports[0].as_of_date,
        "input_files": {
            "latest_snapshot_csv": str(SNAPSHOT_INPUT),
            "report_markdown": str(REPORT_MD_OUTPUT),
        },
        "basket_summary": {
            "counts": counts,
            "narrative": summary_text,
        },
        "screening": {
            "top_review_candidates": [report.symbol for report in ranked_reports[:SHORTLIST_LIMIT]],
            "watch_pullback": [report.symbol for report in buckets["watch_pullback"][:SHORTLIST_LIMIT]],
            "avoid_for_now": [report.symbol for report in buckets["avoid_for_now"][:SHORTLIST_LIMIT]],
            "screen_rule_pass": [report.symbol for report in ranked_reports if report.screen_rule_pass is True],
        },
        "symbols": [
            {
                "symbol": report.symbol,
                "price_basis": report.price_basis,
                "review_priority": report.review_priority,
                "review_score": report.review_score,
                "long_term": report.long_term,
                "medium_term": report.medium_term,
                "timing": report.timing,
                "momentum": report.momentum,
                "confirmation": report.confirmation,
                "overall_state": report.overall_state,
                "rsi_14": report.rsi_14,
                "volume": report.volume,
                "volume_avg_20": report.volume_avg_20,
                "volume_spike_ratio": report.volume_spike_ratio,
                "volume_spike": report.volume_spike,
                "ema_20_reclaim": report.ema_20_reclaim,
                "screen_rule_pass": report.screen_rule_pass,
                "story": report.story,
                "caution": report.caution,
                "strengthen_next": report.strengthen_next,
                "weaken_next": report.weaken_next,
                "observed_metrics": report.observed_metrics,
                "optional_indicators": report.optional_indicators,
            }
            for report in reports
        ],
    }

    with REPORT_JSON_OUTPUT.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)
        handle.write("\n")


def run() -> int:
    ensure_directories()
    ensure_indicator_directories()
    ensure_analysis_directories()
    log_path = configure_logging("analysis")
    logging.info("Starting indicator report generation")

    try:
        symbols = set(read_symbols(SYMBOLS_FILE))
        rows = load_snapshot(SNAPSHOT_INPUT, symbols)
        reports = sorted((build_symbol_report(row) for row in rows), key=lambda report: report.symbol)
        counts, summary_text = basket_summary(reports)
        write_markdown_report(reports, counts, summary_text)
        write_json_report(reports, counts, summary_text)
    except Exception as exc:
        logging.error("Indicator report generation failed: %s", exc)
        logging.error("No complete analysis output produced. Log file: %s", log_path)
        return 1

    logging.info("Wrote analysis markdown report to %s", REPORT_MD_OUTPUT)
    logging.info("Wrote analysis json report to %s", REPORT_JSON_OUTPUT)
    logging.info("Completed successfully. Log file: %s", log_path)
    return 0


if __name__ == "__main__":
    sys.exit(run())
