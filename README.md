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
├── backfill_daily_data.py
├── ingest_daily_data.py
├── market_data_pipeline.py
├── requirements.txt
└── data
    ├── daily/
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

## CSV Schema

Each daily file contains one row per symbol:

```csv
date,symbol,open,high,low,close,adj_close,volume
2026-03-25,NPN.JO,86800.00,91606.00,86800.00,90847.00,90847.00,1965310
```

## Notes

- Yahoo Finance is an unofficial source, so this project is intended for personal research and system building
- Non-trading days are skipped implicitly because Yahoo only returns completed trading-day candles
- Runtime logs are written to `data/logs/` and are ignored by git
