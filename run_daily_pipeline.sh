#!/usr/bin/env bash
# run_daily_pipeline.sh
# Smart daily pipeline: checks latest data date, backfills if needed, then runs indicators + report.
# Designed to be called by the OpenClaw cron job.

set -euo pipefail

REPO="$HOME/development/stock-forecaster"
DAILY_DIR="$REPO/data/daily"
LOG_DIR="$REPO/data/logs"
PYTHON="$REPO/venv/bin/python3"

mkdir -p "$LOG_DIR"
RUNLOG="$LOG_DIR/pipeline_$(date +%Y%m%d_%H%M%S).log"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$RUNLOG"; }

log "=== Daily pipeline started ==="

# --- Step 1: Determine latest data date ---
LATEST=$(ls "$DAILY_DIR"/*.csv 2>/dev/null | xargs -I{} basename {} .csv | sort | tail -1)
if [[ -z "$LATEST" ]]; then
  log "ERROR: No daily CSV files found in $DAILY_DIR. Cannot determine latest date."
  exit 1
fi

TODAY=$(date +%Y-%m-%d)
log "Latest data date: $LATEST"
log "Today: $TODAY"

# Calculate gap in calendar days
LATEST_EPOCH=$(date -d "$LATEST" +%s)
TODAY_EPOCH=$(date -d "$TODAY" +%s)
GAP_DAYS=$(( (TODAY_EPOCH - LATEST_EPOCH) / 86400 ))
log "Calendar day gap: $GAP_DAYS days"

# Count missing trading days (Mon-Fri only)
MISSING_TRADING_DAYS=$(python3 -c "
import datetime
latest = datetime.date.fromisoformat('$LATEST')
today = datetime.date.fromisoformat('$TODAY')
d = latest + datetime.timedelta(days=1)
count = 0
while d <= today:
    if d.weekday() < 5:
        count += 1
    d += datetime.timedelta(days=1)
print(count)
")
log "Missing trading days: $MISSING_TRADING_DAYS"

# --- Step 2: Fetch data ---
if [[ "$MISSING_TRADING_DAYS" -eq 0 ]]; then
  log "Data is already up to date. Skipping fetch."

elif [[ "$MISSING_TRADING_DAYS" -eq 1 ]]; then
  log "One trading day missing — running ingest_daily_data.py"
  cd "$REPO"
  "$PYTHON" ingest_daily_data.py 2>&1 | tee -a "$RUNLOG"
  log "Ingest complete."

else
  # More than 1 trading day missing — use backfill
  # Add a small buffer: fetch 2x the gap to ensure we cover weekends/holidays
  PERIOD_DAYS=$(( GAP_DAYS + 5 ))
  PERIOD="${PERIOD_DAYS}d"
  log "Multiple trading days missing — running backfill_daily_data.py --period $PERIOD"
  cd "$REPO"
  "$PYTHON" backfill_daily_data.py --period "$PERIOD" 2>&1 | tee -a "$RUNLOG"
  log "Backfill complete."
fi

# --- Step 3: Calculate indicators ---
log "Running calculate_indicators.py"
cd "$REPO"
"$PYTHON" calculate_indicators.py 2>&1 | tee -a "$RUNLOG"
log "Indicators complete."

# --- Step 4: Generate report ---
log "Running generate_indicator_report.py"
"$PYTHON" generate_indicator_report.py 2>&1 | tee -a "$RUNLOG"
log "Report complete."

log "=== Pipeline finished. Log: $RUNLOG ==="
echo "PIPELINE_OK:$RUNLOG"
