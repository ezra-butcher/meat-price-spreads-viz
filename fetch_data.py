"""
Fetch USDA ERS Meat Price Spreads data via static CSV downloads.
No API key required — these are public files, refreshed monthly.
Run directly to refresh the local parquet cache:
    python fetch_data.py
"""

import io
import pathlib
import requests
import pandas as pd

CACHE_PATH = pathlib.Path("data/meat_price_spreads.parquet")

BASE_URL = "https://www.ers.usda.gov"

# Long-run history (1970 – most recent full year), refreshed roughly annually
_HISTORICAL_URL = f"{BASE_URL}/media/5028/historical-monthly-price-spread-data-for-beef-pork-broilers.csv"
# Updated monthly — extend beef/pork series past the historical file's cutoff
_CHOICE_BEEF_URL = f"{BASE_URL}/media/5020/choice-beef-values-and-spreads-and-the-all-fresh-retail-value.csv"
_PORK_URL = f"{BASE_URL}/media/5026/pork-values-and-spreads.csv"
# Updated monthly — the only source of current-month broiler (chicken) spread data;
# the ERS site does not publish a standalone "current broiler" file the way it does
# for beef/pork, since broilers have no farm-value breakout (vertically integrated
# production — see README)
_RETAIL_CUTS_URL = f"{BASE_URL}/media/5024/retail-prices-for-beef-pork-poultry-cuts-eggs-and-dairy-products.csv"

# raw Data_Item -> (commodity, canonical series label). Only series present in
# the historical file are mapped — cut-level retail prices, CPI, and %-share
# metrics in the "current" files are intentionally out of scope.
_HISTORICAL_MAP = {
    "Choice beef byproduct value": ("BEEF", "Byproduct value"),
    "Choice beef gross farm value": ("BEEF", "Gross farm value"),
    "Choice beef net farm value": ("BEEF", "Net farm value"),
    "Choice beef wholesale value": ("BEEF", "Wholesale value"),
    "Choice beef retail value": ("BEEF", "Retail value"),
    "Choice beef farm to retail price spread": ("BEEF", "Farm-to-retail spread"),
    "Choice beef farm to wholesale price spread": ("BEEF", "Farm-to-wholesale spread"),
    "Choice beef wholesale to retail price spread": ("BEEF", "Wholesale-to-retail spread"),
    "All fresh beef retail value": ("BEEF", "All-fresh retail value"),
    "Pork byproduct value": ("PORK", "Byproduct value"),
    "Pork gross farm value": ("PORK", "Gross farm value"),
    "Pork net farm value": ("PORK", "Net farm value"),
    "Pork wholesale value": ("PORK", "Wholesale value"),
    "Pork retail value": ("PORK", "Retail value"),
    "Pork farm to retail price spread": ("PORK", "Farm-to-retail spread"),
    "Pork farm to wholesale price spread": ("PORK", "Farm-to-wholesale spread"),
    "Pork Wholesale to retail price spread": ("PORK", "Wholesale-to-retail spread"),
    "Wholesale broiler composite": ("CHICKEN", "Wholesale value"),
    "Retail broiler composite": ("CHICKEN", "Retail value"),
    "Retail-wholesale spread for broiler composite": ("CHICKEN", "Wholesale-to-retail spread"),
}

_CHOICE_BEEF_MAP = {
    "Choice beef byproduct allowance": ("BEEF", "Byproduct value"),
    "Choice beef gross farm value": ("BEEF", "Gross farm value"),
    "Choice beef net farm value": ("BEEF", "Net farm value"),
    "Choice beef wholesale value": ("BEEF", "Wholesale value"),
    "Choice beef retail value": ("BEEF", "Retail value"),
    "Choice beef price spread, farm to retail": ("BEEF", "Farm-to-retail spread"),
    "Choice beef price spread, farm to wholesale": ("BEEF", "Farm-to-wholesale spread"),
    "Choice beef price spread, wholesale to retail": ("BEEF", "Wholesale-to-retail spread"),
    "All-fresh beef retail value": ("BEEF", "All-fresh retail value"),
}

_PORK_MAP = {
    "Pork byproduct allowance": ("PORK", "Byproduct value"),
    "Pork gross farm value": ("PORK", "Gross farm value"),
    "Pork net farm value": ("PORK", "Net farm value"),
    "Pork wholesale value": ("PORK", "Wholesale value"),
    "Pork retail value": ("PORK", "Retail value"),
    "Pork price spread: farm to retail": ("PORK", "Farm-to-retail spread"),
    "Pork price spread: farm to wholesale": ("PORK", "Farm-to-wholesale spread"),
    "Pork price spread: wholesale to retail": ("PORK", "Wholesale-to-retail spread"),
}

_RETAIL_CUTS_MAP = {
    "Wholesale broiler composite": ("CHICKEN", "Wholesale value"),
    "Retail broiler composite": ("CHICKEN", "Retail value"),
    "Wholesale-retail broiler spread": ("CHICKEN", "Wholesale-to-retail spread"),
}


def _get_csv(url: str) -> pd.DataFrame:
    resp = requests.get(url, timeout=60)
    resp.raise_for_status()
    return pd.read_csv(io.BytesIO(resp.content), encoding="utf-8-sig")


def _clean_value(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series.astype(str).str.replace(",", ""), errors="coerce")


def _split_mapped(df: pd.DataFrame, mapping: dict) -> pd.DataFrame:
    mapped = df["Data_Item"].map(mapping)
    keep = mapped.notna()
    df = df[keep].copy()
    df["commodity_desc"], df["series_label"] = zip(*mapped[keep])
    return df


def _parse_historical(df: pd.DataFrame) -> pd.DataFrame:
    df = _split_mapped(df, _HISTORICAL_MAP)
    df["date"] = pd.to_datetime(dict(year=df["Year"], month=df["Month-number"], day=1))
    df["Value"] = _clean_value(df["Value"])
    df["unit_desc"] = df["Units"]
    return df[["date", "commodity_desc", "series_label", "Value", "unit_desc"]]


def _parse_current(df: pd.DataFrame, mapping: dict) -> pd.DataFrame:
    df = df.copy()
    df["Period_Number"] = pd.to_numeric(df["Period_Number"], errors="coerce")
    df = df[df["Period_Number"].between(1, 12)]  # drop Quarter/Annual rollup rows
    df = _split_mapped(df, mapping)
    df["date"] = pd.to_datetime(dict(year=df["Year"], month=df["Period_Number"], day=1))
    df["Value"] = _clean_value(df["Value"])
    df["unit_desc"] = df["Units"]
    return df[["date", "commodity_desc", "series_label", "Value", "unit_desc"]]


def _parse_retail_cuts_broiler(df: pd.DataFrame) -> pd.DataFrame:
    df = _split_mapped(df, _RETAIL_CUTS_MAP)
    df["Month_Number"] = pd.to_numeric(df["Month_Number"], errors="coerce")
    df["date"] = pd.to_datetime(dict(year=df["Year"], month=df["Month_Number"], day=1))
    df["Value"] = _clean_value(df["Value"])
    df["unit_desc"] = df["Units"]
    return df[["date", "commodity_desc", "series_label", "Value", "unit_desc"]]


def _pivot_series(df: pd.DataFrame, commodity: str, labels: list) -> pd.DataFrame:
    sub = df[(df["commodity_desc"] == commodity) & (df["series_label"].isin(labels))]
    return sub.pivot_table(index="date", columns="series_label", values="Value")


def _melt_shares(shares: pd.DataFrame, commodity: str) -> pd.DataFrame:
    long = shares.reset_index().melt(id_vars="date", var_name="series_label", value_name="Value")
    long["commodity_desc"] = commodity
    long["unit_desc"] = "Percent"
    return long


def add_share_series(df: pd.DataFrame) -> pd.DataFrame:
    """Farm/wholesale/retail share of the retail dollar, computed from our own
    net farm / wholesale / retail series rather than pulled from ERS's own
    (differently-scoped) share columns, so they stay internally consistent
    with the value series in this dataset.

    Beef and pork get the full 3-way split. Broilers (chicken) have no
    published farm value — production is vertically integrated — so chicken
    gets a 2-way wholesale/retail split instead of being left out or shown in
    ¢/lb (which wouldn't be comparable on the same percent axis as the others).
    """
    share_rows = []

    for commodity in ("BEEF", "PORK"):
        wide = _pivot_series(df, commodity, ["Net farm value", "Wholesale value", "Retail value"])
        if not {"Net farm value", "Wholesale value", "Retail value"}.issubset(wide.columns):
            continue
        farm, wholesale, retail = wide["Net farm value"], wide["Wholesale value"], wide["Retail value"]
        shares = pd.DataFrame({
            "Farm share of retail value": farm / retail * 100,
            "Wholesale share of retail value": (wholesale - farm) / retail * 100,
            "Retail share of retail value": (retail - wholesale) / retail * 100,
        })
        share_rows.append(_melt_shares(shares, commodity))

    wide = _pivot_series(df, "CHICKEN", ["Wholesale value", "Retail value"])
    if {"Wholesale value", "Retail value"}.issubset(wide.columns):
        wholesale, retail = wide["Wholesale value"], wide["Retail value"]
        shares = pd.DataFrame({
            "Wholesale share of retail value": wholesale / retail * 100,
            "Retail share of retail value": (retail - wholesale) / retail * 100,
        })
        share_rows.append(_melt_shares(shares, "CHICKEN"))

    if not share_rows:
        return df
    shares_df = pd.concat(share_rows, ignore_index=True).dropna(subset=["Value"])
    return pd.concat([df, shares_df[["date", "commodity_desc", "series_label", "Value", "unit_desc"]]], ignore_index=True)


def fetch_all() -> pd.DataFrame:
    print("Fetching historical monthly price spread data (1970-present)...")
    historical = _parse_historical(_get_csv(_HISTORICAL_URL))
    cutoff = historical["date"].max()
    print(f"  -> {len(historical):,} rows, through {cutoff.date()}")

    print("Fetching current beef values and spreads...")
    beef_current = _parse_current(_get_csv(_CHOICE_BEEF_URL), _CHOICE_BEEF_MAP)
    beef_current = beef_current[beef_current["date"] > cutoff]
    print(f"  -> {len(beef_current):,} new rows past {cutoff.date()}")

    print("Fetching current pork values and spreads...")
    pork_current = _parse_current(_get_csv(_PORK_URL), _PORK_MAP)
    pork_current = pork_current[pork_current["date"] > cutoff]
    print(f"  -> {len(pork_current):,} new rows past {cutoff.date()}")

    print("Fetching current broiler (chicken) spread data...")
    chicken_current = _parse_retail_cuts_broiler(_get_csv(_RETAIL_CUTS_URL))
    chicken_current = chicken_current[chicken_current["date"] > cutoff]
    print(f"  -> {len(chicken_current):,} new rows past {cutoff.date()}")

    combined = pd.concat(
        [historical, beef_current, pork_current, chicken_current], ignore_index=True
    )
    combined = add_share_series(combined)
    combined = combined.dropna(subset=["date", "Value"])
    combined = combined.sort_values(["commodity_desc", "series_label", "date"]).reset_index(drop=True)
    return combined


def main():
    CACHE_PATH.parent.mkdir(exist_ok=True)
    cleaned = fetch_all()
    # Write-then-rename so a restart mid-write never sees a partial file
    tmp = CACHE_PATH.with_suffix(".tmp")
    cleaned.to_parquet(tmp, index=False)
    tmp.replace(CACHE_PATH)
    print(f"\nSaved {len(cleaned):,} rows -> {CACHE_PATH}")
    print("Commodities:", sorted(cleaned["commodity_desc"].unique().tolist()))
    print("Series per commodity:", cleaned.groupby("commodity_desc")["series_label"].nunique().to_dict())
    print("Date range:", cleaned["date"].min().date(), "->", cleaned["date"].max().date())


def load_cache() -> pd.DataFrame:
    if not CACHE_PATH.exists():
        raise FileNotFoundError(
            f"{CACHE_PATH} not found. Run `python fetch_data.py` first."
        )
    return pd.read_parquet(CACHE_PATH)


if __name__ == "__main__":
    main()
