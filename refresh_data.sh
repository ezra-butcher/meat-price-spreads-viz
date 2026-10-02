#!/usr/bin/env bash
# Daily cron script: poll for new meat price spread data; when it has changed, refit
# the SARIMA models and restart the app. Safe to run every day — the fetch (seconds)
# always runs, but the ~10 min refit and the restart only happen when the data is new.
#
# Polling daily (rather than on a fixed monthly date) means a late or rescheduled ERS
# release, or a missed run during an outage, is picked up the next morning.
#
# Runs relative to the repo checkout it lives in and logs to ~/logs (no crontab
# redirect needed). Example crontab (6am daily):
#   0 6 * * * /path/to/repo/refresh_data.sh
#
# Defaults assume Podman (aliased as docker), which requires the fully
# qualified localhost/ image name and --pull=never for local-only images —
# override MEAT_SPREADS_IMAGE if running on real Docker instead.

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
IMAGE="${MEAT_SPREADS_IMAGE:-localhost/meat-price-spreads-viz:latest}"
DATA_DIR="$REPO_DIR/data"
PARQUET="$DATA_DIR/meat_price_spreads.parquet"
SIG_FILE="$DATA_DIR/.fitted_sig"   # hash of the data the current forecasts were fit on

LOG_DIR="${REFRESH_LOG_DIR:-$HOME/logs}"
mkdir -p "$LOG_DIR" "$DATA_DIR"
exec > >(tee -a "$LOG_DIR/meat-price-spreads-viz-refresh.log") 2>&1

# One run at a time — a manual run during the cron window must not collide
exec 9>"$DATA_DIR/.refresh.lock"
if ! flock -n 9; then
    echo "[$(date -Iseconds)] Another refresh is already running, exiting"
    exit 0
fi

sha256() {
    if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | cut -d' ' -f1
    else shasum -a 256 "$1" | cut -d' ' -f1; fi
}

echo "[$(date -Iseconds)] Checking for new meat price spreads data"

# No API key needed — ERS files are public downloads
docker run --rm --pull=never \
    -v "$DATA_DIR:/app/data:rw" \
    "$IMAGE" \
    python fetch_data.py

NOW="$(sha256 "$PARQUET")"
if [[ -f "$SIG_FILE" && "$(cat "$SIG_FILE")" == "$NOW" && -f "$DATA_DIR/forecasts.parquet" ]]; then
    echo "[$(date -Iseconds)] No new data since the last successful fit, nothing to do"
    exit 0
fi

echo "[$(date -Iseconds)] New data detected, fitting SARIMA forecasts (~10 min)..."

docker run --rm --pull=never \
    -v "$DATA_DIR:/app/data:rw" \
    "$IMAGE" \
    python fit_forecasts.py

# Record only after a successful fit, so a failed fit is retried on the next run
echo "$NOW" > "$SIG_FILE"

echo "[$(date -Iseconds)] Refresh complete, restarting app"

# Requires passwordless sudo for this command, or run the script as root
sudo systemctl restart meat-price-spreads-viz
