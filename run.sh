#!/usr/bin/env bash
set -euo pipefail

echo "[1/3] Fetching data..."
python fetch_data.py

echo "[2/3] Fitting SARIMA forecasts..."
python fit_forecasts.py

echo "[3/3] Starting app..."
python app.py
