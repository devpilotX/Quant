#!/usr/bin/env bash
# Refresh NSE history from Angel One, then run a real walk-forward backtest and
# persist it to backtest_runs (-> dashboard "Backtests" page). Incremental &
# idempotent: each run only fetches bars newer than what is already cached, so a
# nightly timer grows the dataset and keeps the backtest current.
#
#   deploy/scripts/backtest_refresh.sh [INTERVAL] [START] [EXTRA_RUNSTUDY_ARGS...]
#     INTERVAL  Angel candle interval (default FIVE_MINUTE; ONE_DAY for long history)
#     START     ISO date for symbols with no cache yet (default 2021-01-01)
#
# Runs in a throwaway container off the engine-paper image (so it inherits the
# Angel creds + DB access from .env) with a persistent named volume for the
# CSVs. Does NOT touch the live engine container. Read-only on the broker.
set -euo pipefail

cd "$(dirname "$0")/.."                      # -> deploy/
INTERVAL="${1:-FIVE_MINUTE}"; shift || true
START="${2:-2021-01-01}"; shift || true
EXTRA=("$@")
DATA_VOL="quant_histdata"
CFG="/app/engine/config/base.yaml"

# use sudo only when not already root (systemd runs as root; humans use sudo)
DC="docker compose"
[ "$(id -u)" -eq 0 ] || DC="sudo docker compose"

run() { $DC --profile paper run --rm -v "${DATA_VOL}:/app/data" \
          --no-deps engine-paper "$@"; }

echo "[$(date -Is)] === fetch ${INTERVAL} (from ${START} for new symbols) ==="
run python -m quantsys.data.fetch_history \
      --config "$CFG" --out /app/data/nse --interval "$INTERVAL" --start "$START"

echo "[$(date -Is)] === walk-forward backtest + persist ==="
run python -m quantsys.backtest.runstudy \
      --config "$CFG" --replay /app/data/nse --persist \
      --label "auto ${INTERVAL} $(date +%Y-%m-%d)" "${EXTRA[@]}"

echo "[$(date -Is)] === done — see the Backtests page ==="
