#!/bin/bash
# Cron entry point for the equity ranker.
#   cron_equity.sh refresh   hourly: new PM2.5 forecast -> re-rank with the saved model -> states.json
#   cron_equity.sh full      daily: re-download OpenDOSM + PM2.5 history, rebuild features, retrain, re-rank
# Logs to ~/Library/Logs/hazeguard-equity.log. Installed crontab:
#   5 * * * *  <repo>/pipeline/cron_equity.sh refresh
#   20 3 * * * <repo>/pipeline/cron_equity.sh full
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="$ROOT/.venv/bin/python"
LOG="$HOME/Library/Logs/hazeguard-equity.log"
LOCK="${TMPDIR:-/tmp}/hazeguard-equity.lock"
MODE="${1:-refresh}"

cd "$ROOT"
exec >>"$LOG" 2>&1

# xgboost needs OpenMP; without `brew install libomp`, borrow the copy bundled with scikit-learn.
if [ ! -e /opt/homebrew/opt/libomp/lib/libomp.dylib ]; then
  export DYLD_LIBRARY_PATH="$ROOT/.venv/lib/python3.11/site-packages/sklearn/.dylibs"
fi

# mkdir is atomic; skip this run if the previous one is still going (e.g. the daily full job).
if ! mkdir "$LOCK" 2>/dev/null; then
  echo "$(date '+%F %T') $MODE: previous run still going, skipped"
  exit 0
fi
trap 'rmdir "$LOCK"' EXIT

echo "=== $(date '+%F %T') $MODE"
case "$MODE" in
  refresh) "$PY" pipeline/03_equity.py --refresh | tail -n 18; "$PY" pipeline/waqi_now.py || true ;;
  full)
    "$PY" data/resource_allocation/fetch_raw.py
    "$PY" data/resource_allocation/build_features.py
    "$PY" pipeline/03_equity.py
    ;;
  *) echo "usage: $0 [refresh|full]"; exit 2 ;;
esac
