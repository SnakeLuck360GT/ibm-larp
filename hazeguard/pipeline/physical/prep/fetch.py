"""
Downloads hourly wind and rain per Malaysian zone (Open-Meteo archive, no key)
and FIRMS fire hotspots over Sumatra/Kalimantan (needs a free MAP_KEY).

The air-quality target (PM2.5 / API) comes from Razik's dataset, not from here.

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
    DATA_START, DATA_END, TIMEZONE, STATES, RAW_DIR, FIRMS_SOURCE, FIRMS_BBOX,
)

RAW_DIR.mkdir(parents=True, exist_ok=True)


def get_json(url, params, tries=3):
    """GET with simple retries, since free APIs occasionally drop requests."""
    for i in range(tries):
        r = requests.get(url, params=params, timeout=120)
        if r.ok:
            return r.json()
        time.sleep(2 * (i + 1))
    r.raise_for_status()


def fetch_weather(state, lat, lon):
    """One state -> hourly DataFrame of wind speed/direction and rain."""
    wx = get_json("https://archive-api.open-meteo.com/v1/archive", {
        "latitude": lat, "longitude": lon,
        "start_date": DATA_START, "end_date": DATA_END, "timezone": TIMEZONE,
        "hourly": "wind_speed_10m,wind_direction_10m,precipitation",
    })["hourly"]
    df = pd.DataFrame(wx).rename(columns={
        "wind_speed_10m": "wind_speed", "wind_direction_10m": "wind_dir",
        "precipitation": "rain_mm"})
    df["time"] = pd.to_datetime(df["time"])
    df["state"], df["lat"], df["lon"] = state, lat, lon
    return df


CHUNK_DAYS = 2          # smaller requests time out less often on the FIRMS server
MAX_TRIES = 6


def fetch_firms_chunk(map_key, d):
    """One chunk, retried with increasing waits. Raises if it never succeeds,
    so a year file is never saved with silent gaps."""
    url = (f"https://firms.modaps.eosdis.nasa.gov/api/area/csv/"
           f"{map_key}/{FIRMS_SOURCE}/{FIRMS_BBOX}/{CHUNK_DAYS}/{d.isoformat()}")
    for i in range(MAX_TRIES):
        try:
            r = requests.get(url, timeout=180)
            if r.ok and not r.text.startswith("Invalid"):
                text = r.text.strip()
                return pd.read_csv(StringIO(text)) if text else None
            reason = f"{r.status_code}"
        except requests.RequestException as e:
            reason = type(e).__name__
        wait = 5 * (i + 1)
        print(f"    chunk {d} failed ({reason}), retry {i + 1}/{MAX_TRIES} in {wait}s")
        time.sleep(wait)
    raise RuntimeError(f"FIRMS chunk {d} failed {MAX_TRIES} times; rerun later to resume")


def fetch_firms_year(map_key, year):
    """FIRMS hotspots for one year, in small chunks."""
    d = max(date(year, 1, 1), date.fromisoformat(DATA_START))
    end = min(date(year, 12, 31), date.fromisoformat(DATA_END))
    frames = []
    while d <= end:
        chunk = fetch_firms_chunk(map_key, d)
        if chunk is not None:
            frames.append(chunk)
        d += timedelta(days=CHUNK_DAYS)
        time.sleep(0.5)
    fires = pd.concat(frames, ignore_index=True)
    # acq_date + acq_time are UTC
    fires = fires[["latitude", "longitude", "frp", "acq_date", "acq_time", "confidence"]]
    # Chunks can overlap at the year edge; drop any duplicate detections
    return fires.drop_duplicates()


if __name__ == "__main__":
    weather_path = RAW_DIR / "weather_hourly.csv"
    if weather_path.exists():
        print("weather_hourly.csv already saved, skipping (delete it to refetch)")
    else:
        print("Fetching weather per state...")
        weather = pd.concat([fetch_weather(s, lat, lon) for s, (lat, lon) in STATES.items()],
                            ignore_index=True)
        weather.to_csv(weather_path, index=False)
        print(f"Saved weather_hourly.csv ({len(weather):,} rows).")

    key = os.environ.get("FIRMS_MAP_KEY")
    if not key:
        print("No FIRMS_MAP_KEY set; skipping hotspots.")
    else:
        # One file per year, so a crash halfway doesn't lose everything.
        # Rerunning skips years that are already saved.
        start_y, end_y = int(DATA_START[:4]), int(DATA_END[:4])
        for year in range(start_y, end_y + 1):
            path = RAW_DIR / f"firms_{year}.csv"
            if path.exists():
                print(f"  {year}: already saved, skipping")
                continue
            print(f"  {year}: fetching...")
            fetch_firms_year(key, year).to_csv(path, index=False)

        fires = pd.concat([pd.read_csv(RAW_DIR / f"firms_{y}.csv")
                           for y in range(start_y, end_y + 1)], ignore_index=True)
        fires.to_csv(RAW_DIR / "firms_hotspots.csv", index=False)
        print(f"Saved firms_hotspots.csv ({len(fires):,} hotspots).")