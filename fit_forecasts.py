"""
Pre-fit SARIMA for every series in the meat price spreads cache and save:
  data/forecasts.parquet  — 12-month forward forecasts + 95% CI + model order
  data/fitted.parquet     — in-sample fitted values + 95% CI

Run after fetch_data.py:
    python fit_forecasts.py

Uses bounded stepwise auto_arima:
  ARIMA:    p in [0,3], d in [0,1] (unit-root tested), q in [0,3]
  Seasonal: P in [0,1], D in [0,1], Q in [0,1], m=12
  ~28s/series, ~10 min total for 20 series

Each series is fit on its contiguous monthly tail (observations after the last
calendar gap) — series whose tail is shorter than 24 months are skipped.
"""

import warnings
import pathlib
import pandas as pd
import pmdarima as pm

CACHE_PATH = pathlib.Path("data/meat_price_spreads.parquet")
FORECAST_PATH = pathlib.Path("data/forecasts.parquet")
FITTED_PATH = pathlib.Path("data/fitted.parquet")
HORIZON = 12


def fit_series(series: pd.Series, dates: pd.Series):
    # Regularize to a complete monthly calendar and fit on the contiguous run at
    # the end of the series — SARIMA assumes evenly spaced observations, and
    # broiler series in particular start mid-history
    s = pd.Series(series.values, index=pd.DatetimeIndex(dates.values)).asfreq("MS")
    isna = s.isna()
    if isna.any():
        s = s.loc[s.index > isna[isna].index.max()]
    if len(s) < 24:
        return None, None

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            model = pm.auto_arima(
                s.to_numpy(),
                start_p=0, max_p=3,
                max_d=1,
                start_q=0, max_q=3,
                start_P=0, max_P=1,
                max_D=1,
                start_Q=0, max_Q=1,
                m=12,
                stepwise=True,
                information_criterion="aic",
                error_action="ignore",
                suppress_warnings=True,
            )

        p, d, q = model.order
        P, D, Q, m = model.seasonal_order
        arima_order = f"({p},{d},{q})"
        seasonal_order = f"({P},{D},{Q})[{m}]"

        # Forward forecast
        fc, ci = model.predict(n_periods=HORIZON, return_conf_int=True, alpha=0.05)

        fc_dates = pd.date_range(s.index.max(), periods=HORIZON + 1, freq="MS")[1:]
        forecast_df = pd.DataFrame({
            "date": fc_dates,
            "forecast": fc,
            "ci_lower": ci[:, 0],
            "ci_upper": ci[:, 1],
            "arima_order": arima_order,
            "seasonal_order": seasonal_order,
        })

        # In-sample fitted values with 95% prediction intervals via the
        # underlying statsmodels result object
        sarimax_result = model.arima_res_
        insample_pred = sarimax_result.get_prediction(start=0)
        insample_summary = insample_pred.summary_frame(alpha=0.05)
        fitted_df = pd.DataFrame({
            "date": s.index,
            "fitted": insample_summary["mean"].values,
            "ci_lower": insample_summary["mean_ci_lower"].values,
            "ci_upper": insample_summary["mean_ci_upper"].values,
            "arima_order": arima_order,
            "seasonal_order": seasonal_order,
        })

        return forecast_df, fitted_df

    except Exception as e:
        print(f"    FAILED: {e}")
        return None, None


def main():
    df = pd.read_parquet(CACHE_PATH)
    forecast_records = []
    fitted_records = []

    groups = list(df.groupby(["commodity_desc", "series_label"]))
    total = len(groups)
    for i, ((commodity, series_label), grp) in enumerate(groups, 1):
        grp = grp.sort_values("date").drop_duplicates("date")
        print(f"[{i}/{total}] {commodity} — {series_label}", flush=True)
        forecast_df, fitted_df = fit_series(grp["Value"], grp["date"])
        if forecast_df is not None:
            forecast_df["commodity_desc"] = commodity
            forecast_df["series_label"] = series_label
            forecast_records.append(forecast_df)
            fitted_df["commodity_desc"] = commodity
            fitted_df["series_label"] = series_label
            fitted_records.append(fitted_df)
        else:
            print("    skipped (insufficient contiguous data)")

    if not forecast_records:
        print("No forecasts generated.")
        return

    FORECAST_PATH.parent.mkdir(exist_ok=True)

    # Write-then-rename so a restart mid-write never sees a partial file
    tmp = FORECAST_PATH.with_suffix(".tmp")
    pd.concat(forecast_records, ignore_index=True).to_parquet(tmp, index=False)
    tmp.replace(FORECAST_PATH)
    print(f"\nSaved {len(forecast_records)} series -> {FORECAST_PATH}")

    tmp = FITTED_PATH.with_suffix(".tmp")
    pd.concat(fitted_records, ignore_index=True).to_parquet(tmp, index=False)
    tmp.replace(FITTED_PATH)
    print(f"Saved {len(fitted_records)} series -> {FITTED_PATH}")


if __name__ == "__main__":
    main()
