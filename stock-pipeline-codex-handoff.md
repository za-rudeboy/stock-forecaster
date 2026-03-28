# Codex Handoff: JSE Yahoo Finance Daily Data Pipeline + Moving Averages

## Project Goal
Build a Python script that fetches daily stock market data for a given watchlist of **JSE-listed symbols from Yahoo Finance**, then stores that data in a **daily CSV file structure**.

This is a first-version ingestion pipeline, so keep it simple, reliable, and easy to inspect manually. Do **not** introduce a database yet unless absolutely necessary.

---

## Part 1: Daily data ingestion requirements

### Objective
Create a Python script that:
1. Accepts or reads a list of symbols to track.
2. Fetches market data from Yahoo Finance.
3. Saves one CSV per trading day.
4. Each daily CSV should contain **one row per symbol**.

### Data source
Use **Yahoo Finance**, likely via `yfinance`, because the goal is to support more symbols than very limited free official APIs like Alpha Vantage.

### Important notes about source
- This is for personal/research/system-building use, not a production trading platform.
- Yahoo is unofficial, so code should be written defensively.
- It should be easy to swap out the source later.

### Symbols
The watchlist will be made up of **JSE symbols as represented on Yahoo Finance**.
The script should be written so the symbol list is externalized, for example in:
- a text file like `symbols.txt`, or
- a simple config file.

Do not hardcode symbols deeply into the logic.

### Required fields to save
Each row should include at least:
- `date`
- `symbol`
- `open`
- `high`
- `low`
- `close`
- `adj_close`
- `volume`

### Directory structure
```
data/
  daily/
    2026-03-25.csv
    2026-03-26.csv
  symbols.txt
  logs/
```

### CSV shape
```
date,symbol,open,high,low,close,adj_close,volume
2026-03-25,AGL.JO,510.00,518.50,507.25,516.10,516.10,1234567
2026-03-25,SBK.JO,199.20,201.40,198.60,200.85,200.85,2345678
```

### Operational requirements
- Script should run manually or via cron.
- Fetch only completed daily candles.
- Log failures clearly.
- Do not silently ignore missing data.

---

## Part 2: Moving average calculation requirements

### Objective
Using stored data, calculate:
- **200 SMA**
- **50 SMA**
- **20 EMA**

### Data input
- Use **adjusted close** primarily
- Fall back to close if needed

### Calculations
- SMA_50
- SMA_200
- EMA_20

### Interpretation intent
- 200 SMA → long-term trend
- 50 SMA → medium trend
- 20 EMA → timing

### Technical expectations
- Sort by date ascending
- Handle insufficient history
- Keep implementation simple

---

## Deliverables
1. Data ingestion script
2. Moving average calculation module
3. Usage instructions

---

## Non-goals
- No database
- No UI
- No trading automation

---

## Engineering intent
Stage 1: Data ingestion  
Stage 2: Indicators  
Stage 3 (later): Signals
