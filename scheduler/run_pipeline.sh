#!/bin/bash
# run_pipeline.sh — wrapper for cron / launchd / systemd
# Edit PIPELINE_DIR to match your actual install path.

PIPELINE_DIR="$HOME/.openclaw/workspace/job-applier"
LOG_DIR="$PIPELINE_DIR/logs"
DATE=$(date +%Y-%m-%d)

mkdir -p "$LOG_DIR"

cd "$PIPELINE_DIR" || exit 1

# Activate virtualenv if present
if [ -f "$PIPELINE_DIR/venv/bin/activate" ]; then
    source "$PIPELINE_DIR/venv/bin/activate"
fi

python main_pipeline.py >> "$LOG_DIR/pipeline_$DATE.log" 2>&1

# Crontab line (runs daily at 9 AM — edit time to your preference):
# 0 9 * * * /bin/bash /path/to/job-applier/scheduler/run_pipeline.sh
