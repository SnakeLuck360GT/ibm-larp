"""Step 1: download raw OpenDOSM / data.gov.my datasets (CC BY 4.0) for the equity layer.

Run from the repo root:  .venv/bin/python data/resource_allocation/fetch_raw.py

Writes raw copies to data/resource_allocation/raw/, a tidy household_income.csv,
and manifest.json recording dataset IDs, URLs and years (feeds `assumptions`).
"""

import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests

RAW = Path(__file__).resolve().parent / "raw"

# file name -> (data.gov.my catalogue id, url, description)
DATASETS = {
    "population_state.parquet": (
        "population_state",
        "https://storage.dosm.gov.my/population/population_state.parquet",
        "Population by state, sex, age group and ethnicity (thousands)",
    ),
    "hies_state.parquet": (
        "hies_state",
        "https://storage.dosm.gov.my/hies/hies_state.parquet",
        "HIES headline stats by state: mean/median income, gini, poverty",
    ),
    "hh_income_state.parquet": (
        "hh_income_state",
        "https://storage.dosm.gov.my/hies/hh_income_state.parquet",
        "Mean and median household income by state, all HIES years",
    ),
    "hh_poverty_state.parquet": (
        "hh_poverty_state",
        "https://storage.dosm.gov.my/hies/hh_poverty_state.parquet",
        "Absolute, hardcore and relative poverty rate by state, all HIES years",
    ),
    "hospital_beds.parquet": (
        "hospital_beds",
        "https://storage.data.gov.my/healthcare/hospital_beds.parquet",
        "Hospital beds by state, district and facility type",
    ),
}


def years(df: pd.DataFrame) -> list[int]:
    return sorted({int(str(d)[:4]) for d in df["date"].unique()})


def main() -> None:
    RAW.mkdir(parents=True, exist_ok=True)
    fetched_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    manifest = []

    for fname, (ds_id, url, desc) in DATASETS.items():
        df = pd.read_parquet(url)
        df.to_parquet(RAW / fname, index=False)
        manifest.append({
            "file": fname, "id": ds_id, "url": url, "description": desc,
            "rows": len(df), "years": years(df),
            "source": "DOSM / data.gov.my (OpenDOSM), CC BY 4.0",
            "fetched_at": fetched_at,
        })
        print(f"{fname:28s} {df.shape}  years {years(df)[0]}-{years(df)[-1]}")

    # hies_state only covers 2022 and 2024; hh_income_state has every HIES year,
    # so it is the source for the long income series.
    inc = pd.read_parquet(RAW / "hh_income_state.parquet")
    inc["year"] = inc["date"].astype(str).str[:4].astype(int)
    inc = inc[["state", "year", "income_median", "income_mean"]]
    dropped = inc[inc[["income_median", "income_mean"]].isna().any(axis=1)]
    inc = inc.dropna(subset=["income_median", "income_mean"]).sort_values(["state", "year"])
    inc.to_csv(RAW / "household_income.csv", index=False)
    print(f"household_income.csv         {inc.shape}  (dropped {len(dropped)} rows missing median/mean)")
    manifest.append({
        "file": "household_income.csv", "id": "hh_income_state",
        "url": DATASETS["hh_income_state.parquet"][1],
        "description": "state, year, income_median, income_mean (RM/month); rows missing either value dropped",
        "rows": len(inc), "years": sorted(inc["year"].unique().tolist()),
        "source": "DOSM / data.gov.my (OpenDOSM), CC BY 4.0", "fetched_at": fetched_at,
    })

    pm = fetch_pm25()
    pm.to_csv(RAW / "pm25_daily_openmeteo.csv", index=False)
    print(f"pm25_daily_openmeteo.csv     {pm.shape}  {PM25_START} to {PM25_END}")
    manifest.append({
        "file": "pm25_daily_openmeteo.csv", "id": "open-meteo air-quality (CAMS global)",
        "url": PM25_URL,
        "description": "Daily mean PM2.5 (ug/m3) at each state capital (state_ref.csv), from hourly CAMS global "
                       "model output; history only exists from Aug 2022",
        "rows": len(pm), "years": sorted(pd.to_datetime(pm["date"]).dt.year.unique().tolist()),
        "source": "Open-Meteo Air Quality API / Copernicus CAMS, CC BY 4.0", "fetched_at": fetched_at,
    })

    (RAW / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")


# No state-level PM2.5 history exists on data.gov.my (air_pollution is national-only),
# and forecast.json from the physical model isn't on main yet.
PM25_URL = "https://air-quality-api.open-meteo.com/v1/air-quality"
PM25_START, PM25_END = "2022-08-01", "2026-09-30"


def fetch_pm25() -> pd.DataFrame:
    ref = pd.read_csv(Path(__file__).resolve().parent / "state_ref.csv")
    resp = requests.get(PM25_URL, params={
        "latitude": ",".join(ref["lat"].astype(str)),
        "longitude": ",".join(ref["lon"].astype(str)),
        "hourly": "pm2_5", "timezone": "Asia/Kuala_Lumpur",
        "start_date": PM25_START, "end_date": PM25_END,
    }, timeout=120)
    resp.raise_for_status()
    frames = []
    for state, loc in zip(ref["state"], resp.json()):
        h = pd.DataFrame({"time": pd.to_datetime(loc["hourly"]["time"]), "pm25": loc["hourly"]["pm2_5"]})
        d = h.groupby(h["time"].dt.date)["pm25"].agg(["mean", "count"]).reset_index()
        d = d[d["count"] >= 18]  # need most of the day to call it a daily mean
        frames.append(pd.DataFrame({"state": state, "date": d["time"].astype(str), "pm25": d["mean"].round(1)}))
    return pd.concat(frames, ignore_index=True)


if __name__ == "__main__":
    main()
