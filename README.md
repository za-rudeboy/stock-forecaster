# Stock Forecaster

Small Python scripts for collecting daily JSE market data from Yahoo Finance and storing it as dated CSV files.

## What It Does

- Reads a watchlist from `data/symbols.txt`
- Fetches JSE daily OHLCV candles from Yahoo Finance via `yfinance`
- Writes one CSV per trading day under `data/daily/`
- Supports both incremental daily ingestion and historical backfills
- Skips already-complete daily files by default and merges in newly added symbols when needed

## Project Layout

```text
.
├── generate_indicator_report.py
├── calculate_indicators.py
├── backfill_daily_data.py
├── ingest_daily_data.py
├── market_data_pipeline.py
├── requirements.txt
└── data
    ├── daily/
    ├── indicators/
    └── symbols.txt
```

## Install

```bash
python3 -m pip install --user -r requirements.txt
```

## Watchlist

Edit `data/symbols.txt` and keep one Yahoo Finance symbol per line. Inline comments are supported:

```text
NPN.JO  # Naspers Limited
SBK.JO  # Standard Bank Group Limited
```

## Daily Ingestion

Fetches the latest completed daily candle for each configured symbol and writes or updates one dated CSV.

```bash
python3 ingest_daily_data.py
python3 ingest_daily_data.py --force
```

## Historical Backfill

Backfills daily candles over a period such as 6 or 7 months.

```bash
python3 backfill_daily_data.py --period 6mo
python3 backfill_daily_data.py --period 7mo --chunk-size 10 --sleep-seconds 1
python3 backfill_daily_data.py --period 6mo --force
```

Behavior:

- Existing daily files are skipped when they already contain all requested symbols
- If a daily file exists but a newly added symbol is missing, the missing symbol is merged into that file
- `--force` rewrites matching daily files from the current fetch

## Indicator Calculation

Computes `SMA_50`, `SMA_200`, `EMA_20`, `RSI_14`, and volume-context fields from stored daily files without refetching market data.

```bash
python3 calculate_indicators.py
```

Outputs:

- `data/indicators/indicators_history.csv`
- `data/indicators/latest_snapshot.csv`
- `data/indicators/latest_snapshot.json`
- `data/indicators/charts/<symbol>.png`

Behavior:

- Uses `adj_close` as the primary indicator input price
- Falls back to `close` if `adj_close` is missing
- Leaves full-window indicators blank until enough history exists
- Carries `volume`, `volume_avg_20`, `volume_spike_ratio`, `volume_spike`, `ema_20_reclaim`, and `screen_rule_pass` into the snapshot outputs
- Overwrites Part 2 artifacts on each run so they stay aligned with `data/daily/`

## Indicator Report

Builds a polished human-readable report plus a machine-friendly JSON summary from the latest indicator snapshot.

```bash
python3 generate_indicator_report.py
```

Outputs:

- `data/analysis/latest_indicator_report.md`
- `data/analysis/latest_indicator_report.json`

Behavior:

- Reads `data/indicators/latest_snapshot.csv`
- Produces a basket summary, shortlist tables, and per-symbol reads in plain language
- Uses the first-pass screen rule as a candidate filter:
  `price > SMA_200`, `price > SMA_50`, `RSI_14 > 50`, and fresh `EMA_20` reclaim
- Keeps the JSON shape reusable for future `MACD` fields
- Does not place trades or emit buy/sell automation

## CSV Schema

Each daily file contains one row per symbol:

```csv
date,symbol,open,high,low,close,adj_close,volume
2026-03-25,NPN.JO,86800.00,91606.00,86800.00,90847.00,90847.00,1965310
```

Indicator history rows use this schema:

```csv
date,symbol,close,adj_close,price_used,price_basis,sma_50,sma_200,ema_20,volume,rsi_14,volume_avg_20,volume_spike_ratio,volume_spike,ema_20_reclaim,screen_rule_pass
2026-03-25,NPN.JO,90847.00,90847.00,90847.00,adj_close,83610.42,,86881.67,1965310.00,58.14,1835021.70,1.07,normal,false,false
```

## Notes

- Yahoo Finance is an unofficial source, so this project is intended for personal research and system building
- Non-trading days are skipped implicitly because Yahoo only returns completed trading-day candles
- Runtime logs are written to `data/logs/` and are ignored by git
