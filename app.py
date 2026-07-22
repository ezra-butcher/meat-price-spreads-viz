"""
USDA Meat Price Spreads Dashboard
Run: python app.py
"""

import colorsys
import os
import pathlib
import re
import pandas as pd
import plotly.graph_objects as go
import plotly.colors as pc
from dash import Dash, dcc, html, Input, Output, State, callback, ctx

import fetch_data

# ── Data ──────────────────────────────────────────────────────────────────────

df = fetch_data.load_cache()

FORECAST_PATH = pathlib.Path("data/forecasts.parquet")
FITTED_PATH = pathlib.Path("data/fitted.parquet")
forecasts = pd.read_parquet(FORECAST_PATH) if FORECAST_PATH.exists() else pd.DataFrame()
fitted = pd.read_parquet(FITTED_PATH) if FITTED_PATH.exists() else pd.DataFrame()

COMMODITIES = sorted(df["commodity_desc"].unique())
DATE_MIN = df["date"].min()
DATE_MAX = df["date"].max()


def display_name(commodity: str) -> str:
    return commodity.title()


# Commodity + stage/spread label, e.g. "Beef — Retail value" — canonical series
# labels (Retail value, Wholesale value, ...) repeat across commodities, so the
# commodity prefix disambiguates them for dropdowns, legends, and colors
df["series_key"] = df["commodity_desc"].map(display_name) + " — " + df["series_label"]
for _f in (forecasts, fitted):
    if not _f.empty:
        _f["series_key"] = _f["commodity_desc"].map(display_name) + " — " + _f["series_label"]

ALL_SERIES_KEYS = set(df["series_key"].unique())


def series_type_of(label: str) -> str:
    """Bucket a series_label into one of three mutually-exclusive scales so the
    Series dropdown never mixes ¢/lb values with ¢/lb spreads with % shares —
    those aren't meaningfully comparable on one y-axis."""
    l = label.lower()
    if l.endswith("share of retail value"):
        return "Shares of retail value"
    if l.endswith("spread"):
        return "Spreads"
    return "Values"


SERIES_TYPES = ["Shares of retail value", "Values", "Spreads"]
df["series_type"] = df["series_label"].map(series_type_of)

# Last actual observation per series — forecasts are only drawn when the
# selected date range reaches the series' end, so they stay adjacent
SERIES_LAST_DATE = df.groupby("series_key")["date"].max()
# First fitted date per series — used to clip the differencing burn-in from CI bands
FITTED_FIRST_DATE = (
    fitted.groupby("series_key")["date"].min()
    if not fitted.empty else pd.Series(dtype="datetime64[ns]")
)

# Default series shown per commodity + series-type combination. For shares,
# default to the full breakdown (farm/wholesale/retail for beef & pork,
# wholesale/retail only for chicken, which has no published farm value); for
# Values/Spreads, default to a single "headline" series per commodity.
_DEFAULT_SHARE_SERIES = [
    "Farm share of retail value",
    "Wholesale share of retail value",
    "Retail share of retail value",
]
_DEFAULT_VALUE_SERIES = "Retail value"
_DEFAULT_SPREAD_SERIES = "Wholesale-to-retail spread"

def default_series(commodity_list, series_type):
    defaults = []
    for commodity in commodity_list:
        name = display_name(commodity)
        if series_type == "Shares of retail value":
            defaults.extend(
                f"{name} — {label}" for label in _DEFAULT_SHARE_SERIES
                if f"{name} — {label}" in ALL_SERIES_KEYS
            )
        else:
            label = _DEFAULT_SPREAD_SERIES if series_type == "Spreads" else _DEFAULT_VALUE_SERIES
            key = f"{name} — {label}"
            if key in ALL_SERIES_KEYS:
                defaults.append(key)
    return defaults

MONTHS = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]
MONTH_OPTIONS = [{"label": m, "value": i + 1} for i, m in enumerate(MONTHS)]
YEAR_OPTIONS = [{"label": str(y), "value": y} for y in range(DATE_MAX.year, DATE_MIN.year - 1, -1)]

# ── Helpers ───────────────────────────────────────────────────────────────────

_PALETTES = {
    "default": pc.qualitative.Plotly,
    "colorblind": ["#0072B2", "#E69F00", "#009E73", "#CC79A7", "#56B4E9", "#D55E00", "#F0E442", "#000000"],
    "monochrome": ["#111111", "#444444", "#777777", "#aaaaaa", "#cccccc", "#000000"],
}

# Each commodity gets one base hue; different series within it are shades of
# that hue (see _shade), so beef/pork/chicken stay identifiable by color at a
# glance regardless of how many series are on screen. Not applied in
# monochrome, which keeps its own flat grayscale sequence.
_COMMODITY_BASE_COLORS = {
    "default": {"Beef": "#2CA02C", "Pork": "#1F77B4", "Chicken": "#FF7F0E"},
    "colorblind": {"Beef": "#009E73", "Pork": "#0072B2", "Chicken": "#E69F00"},
}

# series_key -> series_type, used to group "siblings" for shading (series
# within the same commodity AND the same type — values / spreads / shares —
# so the lightness range isn't diluted by types that aren't even on screen)
_SERIES_KEY_TYPE = df.drop_duplicates("series_key").set_index("series_key")["series_type"].to_dict()

def _shade(hex_color: str, index: int, count: int) -> str:
    """The index-th of `count` lightness variants of hex_color, dark to light."""
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16) / 255, int(h[2:4], 16) / 255, int(h[4:6], 16) / 255
    hue, lightness, sat = colorsys.rgb_to_hls(r, g, b)
    if count > 1:
        lo, hi = 0.32, 0.72
        lightness = lo + (hi - lo) * index / (count - 1)
    r2, g2, b2 = colorsys.hls_to_rgb(hue, lightness, sat)
    return "#{:02x}{:02x}{:02x}".format(round(r2 * 255), round(g2 * 255), round(b2 * 255))

def series_color(label: str, all_labels: list, palette: str = "default") -> str:
    if palette != "monochrome":
        commodity_name = label.split(" — ", 1)[0] if " — " in label else ""
        base = _COMMODITY_BASE_COLORS.get(palette, _COMMODITY_BASE_COLORS["default"]).get(commodity_name)
        if base is not None:
            label_type = _SERIES_KEY_TYPE.get(label)
            siblings = [
                l for l in all_labels
                if l.startswith(f"{commodity_name} — ") and _SERIES_KEY_TYPE.get(l) == label_type
            ]
            idx = siblings.index(label) if label in siblings else 0
            return _shade(base, idx, len(siblings))

    seq = _PALETTES.get(palette, _PALETTES["default"])
    idx = all_labels.index(label) if label in all_labels else 0
    return seq[idx % len(seq)]

_DERIVED_UNITS = {"delta", "pct", "yoy", "yoy_pct"}

def apply_unit(series: pd.Series, unit: str, dates: pd.Series = None) -> pd.Series:
    if unit not in _DERIVED_UNITS or dates is None:
        return series
    # Reindex to a complete monthly calendar so diff/pct/rolling offsets are
    # calendar-correct across gaps (broiler series start mid-history)
    idx = pd.DatetimeIndex(dates.values)
    s = pd.Series(series.values, index=idx).asfreq("MS")
    if unit == "delta":
        out = s.diff()
    elif unit == "pct":
        out = s.pct_change(fill_method=None) * 100
    elif unit == "yoy":
        out = s.diff(12)
    else:  # yoy_pct
        out = s.pct_change(12, fill_method=None) * 100
    out = out.reindex(idx)
    out.index = series.index
    return out

def remove_outliers(series: pd.Series) -> pd.Series:
    mean, std = series.mean(), series.std()
    return series.where((series - mean).abs() <= 3 * std)

def _short_unit(base_unit: str) -> str:
    b = base_unit.lower()
    if "percent" in b:
        return "%"
    if "cent" in b:
        return "¢/lb"
    return base_unit

def y_axis_label(unit: str, base_unit: str = "¢/lb") -> str:
    base = _short_unit(base_unit)
    if unit == "delta":
        return f"MoM change ({base})"
    if unit == "pct":
        return "MoM % change"
    if unit == "yoy":
        return f"YoY change ({base})"
    if unit == "yoy_pct":
        return "YoY % change"
    return base

def hex_to_rgba(hex_color: str, alpha: float) -> str:
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"rgba({r},{g},{b},{alpha})"

_btn_style = {
    "fontSize": "12px", "padding": "5px 10px", "cursor": "pointer",
    "border": "1px solid #ccc", "borderRadius": "4px", "background": "#fff",
}
_btn_active = {**_btn_style, "background": "#e8f0fe", "borderColor": "#4a7cf7", "color": "#1a3fc7"}

# ── Layout ────────────────────────────────────────────────────────────────────

_label = {"fontSize": "11px", "fontWeight": "600", "color": "#444", "display": "block", "marginBottom": "3px"}
_card = {"background": "#fff", "borderRadius": "6px", "boxShadow": "0 1px 3px rgba(0,0,0,.08)"}
_dd_sm = {"width": "100px", "fontSize": "12px"}
_dd_yr = {"width": "80px", "fontSize": "12px"}

# Set DASH_URL_BASE_PATHNAME (e.g. "/meat-spreads/", both slashes required) when
# this app is served at a sub-path behind a shared reverse proxy/Funnel port
# alongside other dashboards on the same host. Defaults to root for local dev.
app = Dash(
    __name__,
    title="USDA Meat Price Spreads",
    url_base_pathname=os.environ.get("DASH_URL_BASE_PATHNAME", "/"),
)
server = app.server  # WSGI entry point for gunicorn

app.layout = html.Div(
    style={"fontFamily": "system-ui, sans-serif", "padding": "12px 16px", "backgroundColor": "#f5f5f5", "minHeight": "100vh"},
    children=[
        html.H2("USDA Meat Price Spreads", style={"marginBottom": "2px", "fontSize": "20px"}),
        html.P(
            "Source: USDA ERS Meat Price Spreads — monthly farm, wholesale, and retail "
            "values and spreads for beef, pork, and broilers (chicken). "
            "Independent project, not affiliated with or endorsed by USDA.",
            style={"color": "#777", "fontSize": "12px", "marginTop": 0, "marginBottom": "12px"},
        ),

        # ── Controls card ─────────────────────────────────────────────────────
        html.Div(style={**_card, "marginBottom": "14px"}, children=[
            html.Button(
                "▼ Filters",
                id="filters-toggle",
                n_clicks=0,
                style={
                    "width": "100%", "textAlign": "left", "background": "none",
                    "border": "none", "padding": "10px 14px", "fontSize": "13px",
                    "fontWeight": "600", "cursor": "pointer", "color": "#444",
                },
            ),
            html.Div(
            id="filters-panel",
            style={"display": "flex", "flexWrap": "wrap", "gap": "20px",
                   "alignItems": "flex-end", "padding": "0 14px 12px"},
            children=[
                # Commodity
                html.Div([
                    html.Label("Commodity", style=_label),
                    dcc.Dropdown(
                        id="commodity-select",
                        options=[{"label": display_name(c), "value": c} for c in COMMODITIES],
                        value=["BEEF", "PORK", "CHICKEN"],
                        multi=True,
                        clearable=False,
                        style={"width": "280px", "fontSize": "13px"},
                    ),
                ]),
                # Series type
                html.Div([
                    html.Label("Series type", style=_label),
                    dcc.RadioItems(
                        id="series-type-select",
                        options=[{"label": f" {t}", "value": t} for t in SERIES_TYPES],
                        value="Shares of retail value",
                        inputStyle={"marginRight": "3px"},
                        labelStyle={"display": "block", "fontSize": "12px", "marginBottom": "2px"},
                    ),
                ]),
                # Series
                html.Div([
                    html.Label("Series", style=_label),
                    dcc.Dropdown(
                        id="series-select",
                        multi=True,
                        placeholder="All series",
                        style={"width": "360px", "fontSize": "12px"},
                    ),
                ]),
                # Unit
                html.Div([
                    html.Label("Unit", style=_label),
                    dcc.RadioItems(
                        id="unit-toggle",
                        options=[
                            {"label": " Actual", "value": "actual"},
                            {"label": " MoM Δ", "value": "delta"},
                            {"label": " MoM %Δ", "value": "pct"},
                            {"label": " YoY Δ", "value": "yoy"},
                            {"label": " YoY %Δ", "value": "yoy_pct"},
                        ],
                        value="actual",
                        inline=True,
                        inputStyle={"marginRight": "3px"},
                        labelStyle={"marginRight": "14px", "fontSize": "13px"},
                    ),
                ]),
                # Start date
                html.Div([
                    html.Label("Start date", style=_label),
                    html.Div(
                        style={"display": "flex", "gap": "6px"},
                        children=[
                            dcc.Dropdown(id="start-month", options=MONTH_OPTIONS,
                                         value=1, clearable=False, style=_dd_sm),
                            dcc.Dropdown(id="start-year", options=YEAR_OPTIONS,
                                         value=2013, clearable=False, style=_dd_yr),
                        ],
                    ),
                ]),
                # End date
                html.Div([
                    html.Label("End date", style=_label),
                    html.Div(
                        style={"display": "flex", "gap": "6px"},
                        children=[
                            dcc.Dropdown(id="end-month", options=MONTH_OPTIONS,
                                         value=DATE_MAX.month, clearable=False, style=_dd_sm),
                            dcc.Dropdown(id="end-year", options=YEAR_OPTIONS,
                                         value=DATE_MAX.year, clearable=False, style=_dd_yr),
                        ],
                    ),
                ]),
                # Outliers
                html.Div([
                    html.Label("Outliers", style=_label),
                    html.Button("Remove outliers (>3σ)", id="outlier-btn", n_clicks=0, style=_btn_style),
                ]),
                # Y-axis
                html.Div([
                    html.Label("Y-axis", style=_label),
                    html.Button("Zero baseline", id="zero-btn", n_clicks=0, style=_btn_style),
                ]),
                # Color palette
                html.Div([
                    html.Label("Color palette", style=_label),
                    html.Div(style={"display": "flex", "gap": "4px"}, children=[
                        html.Button("Default", id="palette-default-btn", n_clicks=0, style=_btn_active),
                        html.Button("Colorblind", id="palette-colorblind-btn", n_clicks=0, style=_btn_style),
                        html.Button("Monochrome", id="palette-mono-btn", n_clicks=0, style=_btn_style),
                    ]),
                ]),
                # Download
                html.Div([
                    html.Label("Data", style=_label),
                    html.Button("Download CSV", id="download-btn", n_clicks=0, style=_btn_style),
                    dcc.Download(id="download-data"),
                ]),
                # Forecast controls
                html.Div([
                    html.Label("Forecast horizon (months)", style=_label),
                    html.Div(
                        style={"display": "flex", "alignItems": "center", "gap": "10px"},
                        children=[
                            html.Div(
                                dcc.Slider(
                                    id="forecast-slider",
                                    min=0, max=12, step=1, value=0,
                                    marks={i: str(i) for i in range(0, 13, 3)},
                                    tooltip={"placement": "bottom", "always_visible": False},
                                ),
                                style={"width": "200px"},
                            ),
                            html.Button("Show historical fit", id="fitted-btn", n_clicks=0, style=_btn_style),
                        ],
                    ),
                ]),
            ],
            ),  # filters-panel
        ]),  # controls card

        # ── Line chart + order description ────────────────────────────────────
        html.Div(
            style={**_card, "marginBottom": "12px"},
            children=[
                dcc.Graph(id="line-chart", style={"height": "440px"}),
                html.Div(id="forecast-orders", style={
                    "fontSize": "11px", "color": "#888",
                    "padding": "0 16px 4px", "lineHeight": "1.7",
                }),
                html.Div(style={"borderTop": "1px solid #eee", "margin": "0 16px"}, children=[
                    html.Button("▶ Chart description", id="line-desc-toggle", n_clicks=0,
                        style={"background": "none", "border": "none", "padding": "6px 0",
                               "fontSize": "11px", "color": "#888", "cursor": "pointer"}),
                    html.Div(id="line-desc-panel", style={"display": "none",
                        "fontSize": "12px", "color": "#555", "paddingBottom": "10px", "lineHeight": "1.6"}),
                ]),
            ],
        ),
        html.Div(style={**_card, "marginBottom": "12px"}, children=[
            dcc.Graph(id="histogram", style={"height": "300px"}),
            html.Div(style={"borderTop": "1px solid #eee", "margin": "0 16px"}, children=[
                html.Button("▶ Chart description", id="hist-desc-toggle", n_clicks=0,
                    style={"background": "none", "border": "none", "padding": "6px 0",
                           "fontSize": "11px", "color": "#888", "cursor": "pointer"}),
                html.Div(id="hist-desc-panel", style={"display": "none",
                    "fontSize": "12px", "color": "#555", "paddingBottom": "10px", "lineHeight": "1.6"}),
            ]),
        ]),
        html.Div(
            html.A("View on GitHub", href="https://github.com/ezra-butcher/meat-price-spreads-viz",
                   target="_blank", style={"color": "#888", "fontSize": "11px"}),
            style={"textAlign": "center", "padding": "8px"},
        ),
        dcc.Store(id="palette-store", data="default"),
    ],
)

# ── Callbacks ─────────────────────────────────────────────────────────────────

@callback(
    Output("series-select", "options"),
    Output("series-select", "value"),
    Input("commodity-select", "value"),
    Input("series-type-select", "value"),
)
def update_series_options(commodities, series_type):
    if not commodities:
        return [], []
    commodities = commodities if isinstance(commodities, list) else [commodities]
    keys = sorted(
        df[(df["commodity_desc"].isin(commodities)) & (df["series_type"] == series_type)]["series_key"].unique()
    )
    return [{"label": k, "value": k} for k in keys], default_series(commodities, series_type)


@callback(
    Output("filters-panel", "style"),
    Output("filters-toggle", "children"),
    Input("filters-toggle", "n_clicks"),
)
def toggle_filters(n):
    if (n or 0) % 2 == 1:
        return {"display": "none"}, "▶ Filters"
    return {"display": "flex", "flexWrap": "wrap", "gap": "20px",
            "alignItems": "flex-end", "padding": "0 14px 12px"}, "▼ Filters"


@callback(Output("outlier-btn", "style"), Input("outlier-btn", "n_clicks"))
def toggle_outlier_style(n):
    return _btn_active if (n or 0) % 2 == 1 else _btn_style


@callback(Output("fitted-btn", "style"), Input("fitted-btn", "n_clicks"))
def toggle_fitted_style(n):
    return _btn_active if (n or 0) % 2 == 1 else _btn_style


@callback(Output("zero-btn", "style"), Input("zero-btn", "n_clicks"))
def toggle_zero_style(n):
    return _btn_active if (n or 0) % 2 == 1 else _btn_style


@callback(
    Output("palette-store", "data"),
    Output("palette-default-btn", "style"),
    Output("palette-colorblind-btn", "style"),
    Output("palette-mono-btn", "style"),
    Input("palette-default-btn", "n_clicks"),
    Input("palette-colorblind-btn", "n_clicks"),
    Input("palette-mono-btn", "n_clicks"),
)
def select_palette(n_default, n_colorblind, n_mono):
    triggered = ctx.triggered_id
    if triggered == "palette-colorblind-btn":
        palette = "colorblind"
    elif triggered == "palette-mono-btn":
        palette = "monochrome"
    else:
        palette = "default"
    styles = {
        "default": _btn_style,
        "colorblind": _btn_style,
        "monochrome": _btn_style,
    }
    styles[palette] = _btn_active
    return palette, styles["default"], styles["colorblind"], styles["monochrome"]


@callback(
    Output("line-desc-panel", "style"),
    Output("line-desc-toggle", "children"),
    Input("line-desc-toggle", "n_clicks"),
)
def toggle_line_desc(n):
    _panel_style = {"fontSize": "12px", "color": "#555", "paddingBottom": "10px", "lineHeight": "1.6"}
    if (n or 0) % 2 == 1:
        return {**_panel_style, "display": "block"}, "▼ Chart description"
    return {"display": "none"}, "▶ Chart description"


@callback(
    Output("hist-desc-panel", "style"),
    Output("hist-desc-toggle", "children"),
    Input("hist-desc-toggle", "n_clicks"),
)
def toggle_hist_desc(n):
    _panel_style = {"fontSize": "12px", "color": "#555", "paddingBottom": "10px", "lineHeight": "1.6"}
    if (n or 0) % 2 == 1:
        return {**_panel_style, "display": "block"}, "▼ Chart description"
    return {"display": "none"}, "▶ Chart description"


@callback(
    Output("download-data", "data"),
    Input("download-btn", "n_clicks"),
    State("commodity-select", "value"),
    State("series-select", "value"),
    State("unit-toggle", "value"),
    State("start-month", "value"),
    State("start-year", "value"),
    State("end-month", "value"),
    State("end-year", "value"),
    State("outlier-btn", "n_clicks"),
    prevent_initial_call=True,
)
def download_csv(_, commodities, series_vals, unit,
                 start_month, start_year, end_month, end_year, outlier_clicks):
    if not commodities:
        return None
    if not isinstance(commodities, list):
        commodities = [commodities]

    start_date = pd.Timestamp(year=start_year, month=start_month, day=1)
    end_date = pd.Timestamp(year=end_year, month=end_month, day=1)
    filter_outliers = (outlier_clicks or 0) % 2 == 1

    mask = (df["commodity_desc"].isin(commodities)) & (df["date"] >= start_date) & (df["date"] <= end_date)
    if series_vals:
        mask &= df["series_key"].isin(series_vals)
    subset = df[mask].sort_values(["series_key", "date"])

    rows = []
    for grp, grp_df in subset.groupby("series_key", sort=False):
        grp_df = grp_df.sort_values("date").drop_duplicates("date")
        y = apply_unit(grp_df["Value"], unit, grp_df["date"])
        if filter_outliers:
            y = remove_outliers(y)
        tmp = grp_df[["date", "commodity_desc", "series_label", "series_key"]].copy()
        tmp["value"] = y.values
        tmp["unit"] = y_axis_label(unit, grp_df["unit_desc"].mode()[0] if "unit_desc" in grp_df.columns else "¢/lb")
        rows.append(tmp)

    if not rows:
        return None
    out = pd.concat(rows).reset_index(drop=True)
    out["date"] = out["date"].dt.strftime("%Y-%m")
    commodity_slug = "_".join(re.sub(r"[^a-z0-9]+", "_", c.lower()).strip("_") for c in sorted(commodities))
    return dcc.send_data_frame(out.to_csv, f"meat_price_spreads_{commodity_slug}_{unit}.csv", index=False)


@callback(
    Output("line-chart", "figure"),
    Output("histogram", "figure"),
    Output("forecast-orders", "children"),
    Output("line-desc-panel", "children"),
    Output("hist-desc-panel", "children"),
    Input("commodity-select", "value"),
    Input("series-type-select", "value"),
    Input("series-select", "value"),
    Input("unit-toggle", "value"),
    Input("start-month", "value"),
    Input("start-year", "value"),
    Input("end-month", "value"),
    Input("end-year", "value"),
    Input("outlier-btn", "n_clicks"),
    Input("forecast-slider", "value"),
    Input("fitted-btn", "n_clicks"),
    Input("zero-btn", "n_clicks"),
    Input("palette-store", "data"),
)
def update_charts(commodities, series_type, series_vals, unit,
                  start_month, start_year, end_month, end_year,
                  outlier_clicks, forecast_horizon, fitted_clicks, zero_clicks, palette):

    filter_outliers = (outlier_clicks or 0) % 2 == 1
    show_fitted = (fitted_clicks or 0) % 2 == 1
    zero_baseline = (zero_clicks or 0) % 2 == 1
    palette = palette or "default"

    if not commodities:
        commodities = []
    if not isinstance(commodities, list):
        commodities = [commodities]

    start_date = pd.Timestamp(year=start_year, month=start_month, day=1)
    end_date = pd.Timestamp(year=end_year, month=end_month, day=1)

    # Always constrain to the selected series type — the Series dropdown only
    # offers same-type options, but with an empty selection ("All series") the
    # fallback would otherwise mix %, ¢/lb values, and ¢/lb spreads on one axis
    mask = (
        (df["commodity_desc"].isin(commodities))
        & (df["series_type"] == series_type)
        & (df["date"] >= start_date) & (df["date"] <= end_date)
    )
    subset = df[mask].copy()
    if series_vals:
        subset = subset[subset["series_key"].isin(series_vals)]

    base_unit = subset["unit_desc"].mode()[0] if "unit_desc" in subset.columns and not subset.empty else "¢/lb"
    ylabel = y_axis_label(unit, base_unit)
    # Displayed values are percentages (share series in level terms, or any
    # %-change unit) — used for axis tick formatting
    pct_axis = unit in ("pct", "yoy_pct") or (unit == "actual" and _short_unit(base_unit) == "%")
    groups = sorted(subset["series_key"].unique()) if not subset.empty else []
    all_labels = sorted(df[df["commodity_desc"].isin(commodities)]["series_key"].unique().tolist())

    show_forecast = bool(forecast_horizon) and forecast_horizon > 0 and not forecasts.empty and unit not in ("yoy", "yoy_pct")

    # ── Line chart ────────────────────────────────────────────────────────────
    line_fig = go.Figure()
    order_lines = []
    forecast_drawn = False

    for grp in groups:
        color = series_color(grp, all_labels, palette)
        grp_df = subset[subset["series_key"] == grp].sort_values("date").drop_duplicates("date")
        is_pct_series = not grp_df.empty and "percent" in str(grp_df["unit_desc"].iloc[0]).lower()
        hover_fmt = "%{y:,.1f}%" if unit in ("pct", "yoy_pct") or is_pct_series else "%{y:,.1f}"
        y = apply_unit(grp_df["Value"], unit, grp_df["date"])
        if filter_outliers:
            y = remove_outliers(y)

        line_fig.add_trace(go.Scatter(
            x=grp_df["date"], y=y,
            mode="lines", name=grp,
            line=dict(color=color),
            hovertemplate="%{x|%b %Y}: " + hover_fmt + "<extra>%{fullData.name}</extra>",
        ))

        # Historical fitted values (not shown for derived units)
        if show_fitted and not fitted.empty and unit not in ("yoy", "yoy_pct"):
            fit_rows = (
                fitted[(fitted["series_key"] == grp)
                       & (fitted["date"] >= start_date) & (fitted["date"] <= end_date)]
                .sort_values("date")
            )
            if not fit_rows.empty:
                fit_y = apply_unit(fit_rows["fitted"].reset_index(drop=True), unit, fit_rows["date"].reset_index(drop=True))
                line_fig.add_trace(go.Scatter(
                    x=fit_rows["date"], y=fit_y,
                    mode="lines", name=f"{grp} (fitted)",
                    line=dict(color=color, dash="dot", width=1),
                    hovertemplate="%{x|%b %Y}: " + hover_fmt + "<extra>fitted</extra>",
                    showlegend=True,
                ))
                # CI band only meaningful in level (actual) space; the first 12
                # fitted months are differencing burn-in with meaninglessly wide CIs
                if unit == "actual" and "ci_lower" in fit_rows.columns:
                    burn_start = FITTED_FIRST_DATE.get(grp)
                    band_rows = (
                        fit_rows[fit_rows["date"] >= burn_start + pd.DateOffset(months=12)]
                        if burn_start is not None else fit_rows
                    )
                    if not band_rows.empty:
                        line_fig.add_trace(go.Scatter(
                            x=list(band_rows["date"]) + list(band_rows["date"])[::-1],
                            y=list(band_rows["ci_upper"]) + list(band_rows["ci_lower"])[::-1],
                            fill="toself", fillcolor=hex_to_rgba(color, 0.07),
                            mode="lines", line=dict(width=0),
                            hoverinfo="skip", showlegend=False,
                            name=f"{grp} fitted CI",
                        ))

        # Forward forecast — only when the visible range reaches the series' end,
        # so the forecast stays adjacent to the last plotted actual
        if show_forecast and not grp_df.empty and grp_df["date"].iloc[-1] == SERIES_LAST_DATE.get(grp):
            fc_rows = (
                forecasts[forecasts["series_key"] == grp]
                .sort_values("date").head(forecast_horizon)
            )
            if not fc_rows.empty:
                # Bridge from the last plotted (outlier-filtered) actual point
                valid_y = y.dropna()
                bridge = not valid_y.empty
                if bridge:
                    last_actual_y = valid_y.iloc[-1]
                    last_actual_date = grp_df.loc[valid_y.index[-1], "date"]

                if unit == "actual":
                    fc_y = fc_rows["forecast"].reset_index(drop=True)
                    fc_ci_lower = fc_rows["ci_lower"]
                    fc_ci_upper = fc_rows["ci_upper"]
                else:
                    # Compute MoM transform across the actual→forecast boundary
                    actual_vals = grp_df["Value"].copy()
                    if filter_outliers:
                        actual_vals = remove_outliers(actual_vals)
                    combined = pd.concat(
                        [actual_vals.reset_index(drop=True),
                         fc_rows["forecast"].reset_index(drop=True)],
                        ignore_index=True,
                    )
                    if unit == "delta":
                        transformed = combined.diff()
                    else:
                        transformed = combined.pct_change(fill_method=None) * 100
                    fc_y = transformed.iloc[len(actual_vals):].reset_index(drop=True)
                    fc_ci_lower = None
                    fc_ci_upper = None

                # Hover-less connector so the boundary month isn't labeled "forecast"
                if bridge:
                    line_fig.add_trace(go.Scatter(
                        x=[last_actual_date, fc_rows["date"].iloc[0]],
                        y=[last_actual_y, fc_y.iloc[0]],
                        mode="lines", line=dict(color=color, dash="dash"),
                        hoverinfo="skip", showlegend=False,
                        name=f"{grp} (bridge)",
                    ))
                line_fig.add_trace(go.Scatter(
                    x=fc_rows["date"], y=fc_y,
                    mode="lines", name=f"{grp} (forecast)",
                    line=dict(color=color, dash="dash"),
                    hovertemplate="%{x|%b %Y}: " + hover_fmt + "<extra>forecast</extra>",
                    showlegend=True,
                ))
                forecast_drawn = True
                if fc_ci_lower is not None:
                    if bridge:
                        ci_x = [last_actual_date] + list(fc_rows["date"]) + list(fc_rows["date"])[::-1] + [last_actual_date]
                        ci_upper = [last_actual_y] + list(fc_ci_upper)
                        ci_lower = list(fc_ci_lower)[::-1] + [last_actual_y]
                    else:
                        ci_x = list(fc_rows["date"]) + list(fc_rows["date"])[::-1]
                        ci_upper = list(fc_ci_upper)
                        ci_lower = list(fc_ci_lower)[::-1]
                    line_fig.add_trace(go.Scatter(
                        x=ci_x,
                        y=ci_upper + ci_lower,
                        fill="toself", fillcolor=hex_to_rgba(color, 0.1),
                        mode="lines", line=dict(width=0),
                        hoverinfo="skip", showlegend=False,
                        name=f"{grp} 95% CI",
                    ))
                if "arima_order" in fc_rows.columns:
                    ao, so = fc_rows["arima_order"].iloc[0], fc_rows["seasonal_order"].iloc[0]
                    order_lines.append(f"{grp}: SARIMA{ao}{so}")

    line_fig.update_layout(
        title=dict(text=f"{', '.join(display_name(c) for c in commodities)} — Price Spreads", x=0.01, font_size=14),
        xaxis_title="Date", yaxis_title=ylabel,
        legend=dict(orientation="h", y=-0.22, font_size=11),
        margin=dict(l=70, r=20, t=40, b=90),
        plot_bgcolor="#fff", paper_bgcolor="#fff", hovermode="x unified",
        xaxis=dict(showgrid=True, gridcolor="#eee"),
        yaxis=dict(showgrid=True, gridcolor="#eee",
                   rangemode="tozero" if zero_baseline and unit == "actual" else "normal"),
    )

    order_children = []
    if order_lines:
        order_children.append(html.Div([
            html.Span("Forecast models — ", style={"fontWeight": "600"}),
            html.Span("  |  ".join(order_lines)),
        ]))

    # ── Histogram ─────────────────────────────────────────────────────────────
    hist_fig = go.Figure()
    for grp in groups:
        color = series_color(grp, all_labels, palette)
        grp_df = subset[subset["series_key"] == grp].sort_values("date").drop_duplicates("date")
        y = apply_unit(grp_df["Value"], unit, grp_df["date"])
        if filter_outliers:
            y = remove_outliers(y)
        hist_fig.add_trace(go.Histogram(x=y.dropna(), name=grp, marker_color=color, opacity=0.72, nbinsx=40))

    hist_fig.update_layout(
        barmode="overlay",
        title=dict(text=f"{', '.join(display_name(c) for c in commodities)} — Distribution of {ylabel}", x=0.01, font_size=14),
        xaxis_title=ylabel, yaxis_title="Count",
        legend=dict(orientation="h", y=-0.28, font_size=11),
        margin=dict(l=70, r=20, t=40, b=90),
        plot_bgcolor="#fff", paper_bgcolor="#fff",
        xaxis=dict(showgrid=True, gridcolor="#eee", ticksuffix="%" if pct_axis else ""),
        yaxis=dict(showgrid=True, gridcolor="#eee"),
    )

    # ── Chart descriptions ────────────────────────────────────────────────────
    commodity_names = ", ".join(display_name(c) for c in commodities)
    series_names = ", ".join(groups) if groups else "none"
    unit_label = y_axis_label(unit, base_unit)
    date_range = f"{MONTHS[start_month - 1]} {start_year} – {MONTHS[end_month - 1]} {end_year}"
    outlier_note = " Outliers (>3σ) removed." if filter_outliers else ""
    forecast_note = f" Includes {forecast_horizon}-month SARIMA forecast." if forecast_drawn else ""

    line_desc = (
        f"This line chart shows {unit_label} for {commodity_names} from {date_range}. "
        f"Series shown: {series_names}.{outlier_note}{forecast_note}"
    )
    hist_desc = (
        f"This histogram shows the distribution of {unit_label} values for {commodity_names} "
        f"from {date_range}.{outlier_note}"
    )

    return line_fig, hist_fig, order_children, line_desc, hist_desc


if __name__ == "__main__":
    app.run(debug=False, host="0.0.0.0", port=8052)
