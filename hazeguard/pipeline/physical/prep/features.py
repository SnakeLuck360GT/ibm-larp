"""
Builds the daily feature table for the haze models.

Input:  data/raw/zones_hourly.csv, data/raw/firms_hotspots.csv
Output: data/processed/features.csv  (one row per zone per day)

Run from the hazeguard/ folder:
    python -m pipeline.physical.prep.features
"""
import numpy as np
import pandas as pd

from pipeline.physical.config import RAW_DIR, PROCESSED_DIR

EARTH_R_KM = 6371.0
MIN_DIST_KM = 10.0      # stops a fire right next to a city producing a near-infinite value
FIRE_DAYS = 2           # count fires from today and yesterday (smoke takes time to travel)


# ---------- 1. Hourly weather + PM2.5 -> daily ----------
def to_daily(hourly):
    """One row per zone per day.

    Wind direction can't be averaged directly (the average of 350° and 10° is
    180°, which is wrong), so we average it as a vector instead.
    """
    h = hourly.copy()
    h["date"] = h["time"].dt.floor("D")
    rad = np.deg2rad(h["wind_dir"])
    h["wind_s"] = h["wind_speed"] * np.sin(rad)
    h["wind_c"] = h["wind_speed"] * np.cos(rad)

    daily = (h.groupby(["zone", "date"])
               .agg(lat=("lat", "first"), lon=("lon", "first"),
                    pm25=("pm25", "mean"), wind_speed=("wind_speed", "mean"),
                    wind_s=("wind_s", "mean"), wind_c=("wind_c", "mean"),
                    rain_mm=("rain_mm", "sum"))
               .reset_index())
    # wind_dir = direction the wind blows FROM (meteorological convention)
    daily["wind_dir"] = (np.rad2deg(np.arctan2(daily.wind_s, daily.wind_c)) + 360) % 360
    return daily.drop(columns=["wind_s", "wind_c"])


# ---------- 2. Geometry helpers (vectorised over many fires) ----------
def haversine_km(lat1, lon1, lat2, lon2):
    """Great-circle distance in km."""
    p1, p2 = np.deg2rad(lat1), np.deg2rad(lat2)
    dlat, dlon = p2 - p1, np.deg2rad(lon2 - lon1)
    a = np.sin(dlat / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dlon / 2) ** 2
    return 2 * EARTH_R_KM * np.arcsin(np.sqrt(a))


def bearing_deg(lat1, lon1, lat2, lon2):
    """Compass bearing from point 1 to point 2 (0 = north, 90 = east)."""
    p1, p2 = np.deg2rad(lat1), np.deg2rad(lat2)
    dlon = np.deg2rad(lon2 - lon1)
    x = np.sin(dlon) * np.cos(p2)
    y = np.cos(p1) * np.sin(p2) - np.sin(p1) * np.cos(p2) * np.cos(dlon)
    return (np.rad2deg(np.arctan2(x, y)) + 360) % 360


# ---------- 3. fire_pressure ----------
def load_fires():
    fires = pd.read_csv(RAW_DIR / "firms_hotspots.csv")
    # VIIRS confidence is l/n/h (low/nominal/high): drop low-confidence detections
    if fires["confidence"].dtype == object:
        fires = fires[fires["confidence"] != "l"]
    fires["date"] = pd.to_datetime(fires["acq_date"])   # UTC date; ~8h offset is fine at daily scale
    return {d: g[["latitude", "longitude", "frp"]].to_numpy()
            for d, g in fires.groupby("date")}


def fire_pressure(lat, lon, wind_from, date, fires_by_day):
    """Sum over recent fires of: intensity x wind alignment / distance.

    alignment = 1 if the wind blows straight from the fire towards the zone,
                0 if sideways or away (negative values clipped to 0).
    Uses the zone's own wind as a proxy for the wind along the smoke's path.
    """
    days = [date - pd.Timedelta(days=k) for k in range(FIRE_DAYS)]
    chunks = [fires_by_day[d] for d in days if d in fires_by_day]
    if not chunks:
        return 0.0
    f = np.vstack(chunks)
    f_lat, f_lon, frp = f[:, 0], f[:, 1], f[:, 2]

    dist = np.maximum(haversine_km(f_lat, f_lon, lat, lon), MIN_DIST_KM)
    fire_to_zone = bearing_deg(f_lat, f_lon, lat, lon)
    blows_to = (wind_from + 180) % 360
    align = np.clip(np.cos(np.deg2rad(blows_to - fire_to_zone)), 0, None)

    return float(np.sum(frp * align / dist))


# ---------- 4. Assemble ----------
def build():
    hourly = pd.read_csv(RAW_DIR / "zones_hourly.csv", parse_dates=["time"])
    df = to_daily(hourly)

    print("Computing fire_pressure...")
    fires_by_day = load_fires()
    df["fire_pressure"] = [
        fire_pressure(r.lat, r.lon, r.wind_dir, r.date, fires_by_day)
        for r in df.itertuples()
    ]

    # Lags: only past information, so the model never "sees the future"
    df = df.sort_values(["zone", "date"])
    g = df.groupby("zone")
    df["pm25_lag1"] = g["pm25"].shift(1)
    df["pm25_lag2"] = g["pm25"].shift(2)
    df["pm25_rolling3"] = g["pm25"].transform(lambda s: s.rolling(3).mean())
    df["fire_pressure_lag1"] = g["fire_pressure"].shift(1)
    df["day_of_year"] = df["date"].dt.dayofyear

    df = df.dropna().reset_index(drop=True)
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    df.to_csv(PROCESSED_DIR / "features.csv", index=False)
    print(f"Saved features.csv ({len(df):,} rows, {df.zone.nunique()} zones)")

    # Quick check: does more fire pressure today go with higher PM2.5 tomorrow?
    df["pm25_next"] = df.groupby("zone")["pm25"].shift(-1)
    print("Corr(fire_pressure, next-day PM2.5):",
          round(df["fire_pressure"].corr(df["pm25_next"]), 3))


if __name__ == "__main__":
    build()