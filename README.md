# USDA Meat Price Spreads Dashboard

An interactive web dashboard for visualizing USDA ERS Meat Price Spreads data — monthly farm, wholesale, and retail values and the spreads between them, for beef, pork, and broilers (chicken), back to 1970.

Built with Plotly Dash, designed to be self-hosted and embedded in a Google Sites page.

> This is an independent project built on publicly available data. It is not
> affiliated with or endorsed by the U.S. Department of Agriculture.

## Features

- **Line charts and histograms** of price levels and spreads by commodity and value-chain stage
- **Unit toggle**: Actual / MoM Δ / MoM % / YoY Δ / YoY %
- **Date range filter** by month and year
- **Outlier removal** (>3σ from mean)
- **SARIMA forecasting** with 1–12 month horizon, auto-selected order via `pmdarima`
- **Historical fitted values** with 95% confidence intervals
- **CSV download** of currently visible data

## Data Source

[USDA ERS Meat Price Spreads](https://www.ers.usda.gov/data-products/meat-price-spreads) — plain CSV downloads, no API key or signup required, updated monthly.

The dashboard combines two kinds of ERS files:
- The **historical monthly file** (1970–most recent full year), refreshed roughly annually
- The **current beef/pork/broiler files**, updated monthly, used to extend each series past the historical file's cutoff

Series pulled, per commodity:

| | Beef | Pork | Chicken (broiler) |
|---|---|---|---|
| Byproduct value | ✓ | ✓ | — |
| Gross farm value | ✓ | ✓ | — |
| Net farm value | ✓ | ✓ | — |
| Wholesale value | ✓ | ✓ | ✓ |
| Retail value | ✓ | ✓ | ✓ |
| All-fresh retail value | ✓ | — | — |
| Farm-to-wholesale spread | ✓ | ✓ | — |
| Wholesale-to-retail spread | ✓ | ✓ | ✓ |
| Farm-to-retail spread | ✓ | ✓ | — |
| Farm share of retail value | ✓ | ✓ | — |
| Wholesale share of retail value | ✓ | ✓ | ✓ |
| Retail share of retail value | ✓ | ✓ | ✓ |

Chicken has no farm-value breakout because broiler production is vertically integrated — ERS doesn't publish a comparable farm/wholesale split for it, only wholesale and retail composite prices.

The three **share-of-retail-value** rows are computed in `fetch_data.py`, not pulled from ERS's own (differently-scoped) share columns, so they stay internally consistent with the value series above:

```
farm share      = net farm value / retail value
wholesale share = (wholesale value − net farm value) / retail value
retail share    = (retail value − wholesale value) / retail value
```

For beef and pork these three sum to 100% of the retail dollar. Chicken gets a 2-way split instead (`wholesale share` + `retail share` only, no farm share) since it has no farm value to subtract — these are the series shown by default when you select a commodity.

Two properties of the share **forecasts** worth knowing:
- Each share series is fit by its own independent SARIMA model, so forecast
  shares sum to roughly — not exactly — 100% (typically within ±2%).
- Forecast confidence intervals are deliberately **not** clipped at zero. A
  negative wholesale share means wholesale value below net farm value — a
  negative packer margin — which has occurred historically, so a CI that spans
  zero is economically meaningful, not a bug.

Cut-level retail prices (chicken breast, pork chops, specific beef cuts, etc.) and CPI series are also published by ERS on this page but are **not** pulled here — this dashboard is scoped to the farm/wholesale/retail spread structure.

## Setup

### Prerequisites

- Python 3.9–3.12 (Docker image uses 3.12; the pinned `numpy`/`pmdarima`
  versions rule out 3.8 and 3.13)

### Install

```bash
git clone <this-repo-url>
cd meat-price-spreads-viz
pip install -r requirements.txt
```

### Run

```bash
# Fetch data, fit forecasts, and start the app
bash run.sh
```

Or run each step individually:

```bash
python fetch_data.py       # ~seconds — pulls the ERS CSV files
python fit_forecasts.py    # ~13 min — fits SARIMA models for 28 series
python app.py              # starts the Dash app on http://localhost:8052
```

## Deployment (Docker + systemd + Tailscale)

The included `Dockerfile` and `meat-price-spreads-viz.service` are set up for self-hosting on a home server with public access via [Tailscale Funnel](https://tailscale.com/kb/1223/funnel), following the same pattern as the companion [cold storage dashboard](https://github.com/ezra-butcher/usda-cold-storage-visualization).

### Build and run with Docker

```bash
docker build -t meat-price-spreads-viz .

# One-time (and after each monthly release): fetch data and fit forecasts into ./data
docker run --rm -v "$(pwd)/data:/app/data" meat-price-spreads-viz python fetch_data.py
docker run --rm -v "$(pwd)/data:/app/data" meat-price-spreads-viz python fit_forecasts.py

# Run the app
docker run -d \
  --name meat-price-spreads-viz \
  -p 127.0.0.1:8052:8052 \
  -v "$(pwd)/data:/app/data" \
  meat-price-spreads-viz
```

> **Podman users:** local images are fully qualified — reference the image as
> `localhost/meat-price-spreads-viz:latest` and pass `--pull=never`.

### systemd service

The unit file hardcodes the repo path (`/home/user/meat-price-spreads-viz`)
and `User=user` — edit both to match your machine, then:

```bash
sudo cp meat-price-spreads-viz.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now meat-price-spreads-viz
```

### Monthly data refresh

Schedule `refresh_data.sh` via cron to run once a month after the ERS release
(the files are updated mid-month — check the
[dataset page](https://www.ers.usda.gov/data-products/meat-price-spreads) for
the current schedule). The script re-fetches the CSVs, refits all SARIMA
models, and restarts the service; the final `systemctl restart` step needs
passwordless sudo scoped to that one command (a one-line file in
`/etc/sudoers.d/`) to run unattended.

### Running alongside other dashboards on one host

The app listens on local port 8052 (configurable in `app.py`, the `Dockerfile`,
and the service file), so it can coexist with other Dash apps on their own
ports behind one Tailscale node.

One sharp edge worth knowing: Tailscale Funnel only allows public exposure on
three ports per node (443, 8443, 10000), and its path-based routing
(`tailscale serve --set-path`) currently breaks apps that load sub-resources
at absolute paths — which describes any Dash app — due to an open bug,
[tailscale/tailscale#12413](https://github.com/tailscale/tailscale/issues/12413).
Until that's fixed, give each Dash app its own Funnel port; past three apps,
put a dedicated reverse proxy (Caddy/nginx) on one funneled port and let it do
the path routing. `app.py` supports serving at a sub-path via the
`DASH_URL_BASE_PATHNAME` env var (unset/`/` by default) for exactly that
setup.

### Embedding in Google Sites

1. Expose the app publicly via Tailscale Funnel
2. In Google Sites: **Insert → Embed → By URL** → paste the Funnel URL
3. All filter state is managed in Dash callbacks (not URL query params), which is required for Google Sites embedding

## Project Structure

```
meat-price-spreads-viz/
├── app.py                        # Dash application
├── fetch_data.py                 # ERS CSV data pull
├── fit_forecasts.py              # SARIMA model fitting
├── run.sh                        # Sequential runner: fetch → fit → serve
├── refresh_data.sh               # Monthly cron refresh script
├── Dockerfile
├── meat-price-spreads-viz.service  # systemd unit file
├── requirements.txt
└── data/                          # Generated — not committed
    ├── meat_price_spreads.parquet
    ├── forecasts.parquet
    └── fitted.parquet
```

## License

MIT
