#!/usr/bin/env bash
# Monthly cron script: refresh meat price spread data, refit SARIMA models, restart the app.
# Runs relative to the repo checkout it lives in. Example crontab (6am on the 15th —
# ERS releases the Meat Price Spreads update mid-month):
#   0 6 15 * * /path/to/repo/refresh_data.sh >> /var/log/meat-price-spreads-viz-refresh.log 2>&1
#
# Defaults assume Podman (aliased as docker), which requires the fully
# qualified localhost/ image name and --pull=never for local-only images —
# override MEAT_SPREADS_IMAGE if running on real Docker instead.

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
IMAGE="${MEAT_SPREADS_IMAGE:-localhost/meat-price-spreads-viz:latest}"

echo "[$(date -Iseconds)] Starting meat price spreads data refresh"

# No API key needed — ERS files are public downloads
docker run --rm --pull=never \
    -v "$REPO_DIR/data:/app/data:rw" \
    "$IMAGE" \
    python fetch_data.py

echo "[$(date -Iseconds)] Data fetched, fitting SARIMA forecasts (~10 min)..."

docker run --rm --pull=never \
    -v "$REPO_DIR/data:/app/data:rw" \
    "$IMAGE" \
    python fit_forecasts.py

echo "[$(date -Iseconds)] Refresh complete, restarting app"

# Requires passwordless sudo for this command, or run the script as root
sudo systemctl restart meat-price-spreads-viz
