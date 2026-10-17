"""
Downloads hourly PM2.5, wind and rain per Malaysian zone (Open-Meteo, no key)
and FIRMS fire hotspots over Sumatra/Kalimantan (needs a free MAP_KEY).

Run from the hazeguard/ folder:
    export FIRMS_MAP_KEY=your_key_here
    python -m pipeline.physical.prep.fetch
"""
import os
import time
from datetime import date, timedelta
from io import StringIO

import pandas as pd
import requests

from pipeline.physical.config import (
    DATA_START, DATA_END, TIMEZONE, ZONES, RAW_DIR, FIRMS_SOURCE, FIRMS_BBOX,
)

RAW_DIR.mkdir(parents=True, exist_ok=True)


def get_json(url, params, tries=3):
    """GET with simple retries, since free APIs occasionally drop requests."""
    for i in range(tries):
        r = requests.get(url, params=params, timeout=60)
        if r.ok:
            return r.json()
        time.sleep(2 * (i + 1))
    r.raise_for_status()


def fetch_zone(zone, lat, lon):
    """One zone -> hourly DataFrame of pm25, wind speed/direction, rain."""
    common = {"latitude": lat, "longitude": lon,
              "start_date": DATA_START, "end_date": DATA_END, "timezone": TIMEZONE}
    aq = get_json("https://air-quality-api.open-meteo.com/v1/air-quality",
                  {**common, "hourly": "pm2_5"})["hourly"]
    wx = get_json("https://archive-api.open-meteo.com/v1/archive",
                  {**common, "hourly": "wind_speed_10m,wind_direction_10m,precipitation"})["hourly"]

    df = pd.DataFrame(aq).merge(pd.DataFrame(wx), on="time", how="outer")
    df = df.rename(columns={"pm2_5": "pm25", "wind_speed_10m": "wind_speed",
                            "wind_direction_10m": "wind_dir", "precipitation": "rain_mm"})
    df["time"] = pd.to_datetime(df["time"])
    df["zone"], df["lat"], df["lon"] = zone, lat, lon
    return df


def fetch_firms(map_key):
    """FIRMS hotspots for the bounding box, in 5-day chunks (API limit)."""
    d, end = date.fromisoformat(DATA_START), date.fromisoformat(DATA_END)
    frames = []
    while d <= end:
        url = (f"https://firms.modaps.eosdis.nasa.gov/api/area/csv/"
               f"{map_key}/{FIRMS_SOURCE}/{FIRMS_BBOX}/5/{d.isoformat()}")
        r = requests.get(url, timeout=120)
        if r.ok and r.text.strip() and not r.text.startswith("Invalid"):
            frames.append(pd.read_csv(StringIO(r.text)))
        else:
            print(f"  FIRMS chunk {d} failed: {r.status_code} {r.text[:80]}")
        d += timedelta(days=5)
        time.sleep(0.5)
    fires = pd.concat(frames, ignore_index=True)
    # acq_date + acq_time are UTC
    return fires[["latitude", "longitude", "frp", "acq_date", "acq_time", "confidence"]]


if __name__ == "__main__":
    print("Fetching PM2.5 + weather per zone...")
    zones_df = pd.concat([fetch_zone(z, lat, lon) for z, (lat, lon) in ZONES.items()],
                         ignore_index=True)
    zones_df.to_csv(RAW_DIR / "zones_hourly.csv", index=False)
    print(f"Saved zones_hourly.csv ({len(zones_df):,} rows). Razik can start ARIMA now.")

    key = os.environ.get("FIRMS_MAP_KEY")
    if not key:
        print("No FIRMS_MAP_KEY set; skipping hotspots.")
    else:
        print("Fetching FIRMS hotspots (takes a few minutes)...")
        fires = fetch_firms(key)
        fires.to_csv(RAW_DIR / "firms_hotspots.csv", index=False)
        print(f"Saved firms_hotspots.csv ({len(fires):,} hotspots).")