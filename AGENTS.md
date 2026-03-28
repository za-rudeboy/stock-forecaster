# AGENTS

## Repository Purpose

This repository stores a simple Python-based market data pipeline for JSE symbols using Yahoo Finance daily candles.

## Working Rules

- Keep the pipeline simple and file-based; do not introduce a database unless explicitly requested.
- Preserve the one-file-per-trading-day layout under `data/daily/`.
- Keep symbols externalized in `data/symbols.txt`; do not hardcode watchlists in the Python logic.
- Treat Yahoo Finance as an unofficial source and code defensively around missing or malformed data.
- Prefer extending the shared logic in `market_data_pipeline.py` instead of duplicating ingestion behavior.
- Do not commit generated log files or Python cache files.
- Keep public documentation in `README.md` aligned with actual CLI behavior.

## Current Scripts

- `ingest_daily_data.py`: latest completed daily candle ingestion
- `backfill_daily_data.py`: historical daily backfill
- `market_data_pipeline.py`: shared parsing, normalization, merge, logging, and CSV write logic

## Data Expectations

- Daily CSV schema is fixed to: `date,symbol,open,high,low,close,adj_close,volume`
- Existing daily files should be skipped if complete, merged if only new symbols are missing, and overwritten only with `--force`

## Next Likely Step

If indicator work starts, build it as a separate stage that reads from stored daily CSV files rather than refetching market data.
