"""
Builds the daily weather + fire feature table for the haze models.

Input:  data/raw/weather_hourly.csv, data/raw/firms_hotspots.csv
Output: data/processed/features.csv, one row per state per day:
        date, state, wind_speed, wind_dir, rain_mm,
        fire_pressure, fire_pressure_lag1, fire_pressure_3d, fire_pressure_7d,
        fires_100km, fires_300km, frp_300km

API lags and targets are added on the models side (Razik).

Run from the hazeguard/ folder:
    python -m pipeline.physical.prep.features
"""
import numpy as np
import pandas as pd

from pipeline.physical.config import RAW_DIR, PROCESSED_DIR, STATES

EARTH_R_KM = 6371.0
MIN_DIST_KM = 10.0      # stops a fire right next to a city producing a near-infinite value
FIRE_DAYS = 2           # count fires from today and yesterday (smoke takes time to travel)
UTC_OFFSET_H = 8        # Malaysia time = UTC+8; FIRMS times are UTC
NEAR_KM, FAR_KM = 100, 300

FIRE_COLS = ["fire_pressure", "fire_pressure_lag1", "fire_pressure_3d", "fire_pressure_7d",
             "fires_100km", "fires_300km", "frp_300km"]


# ---------- 1. Hourly weather -> daily ----------
def to_daily(hourly):
    """One row per state per (Malaysian local) day.

    Wind direction can't be averaged directly (the average of 350° and 10° is
    180°, which is wrong), so it's averaged as a speed-weighted vector.
    """
    h = hourly.copy()
    h["date"] = h["time"].dt.floor("D")
    rad = np.deg2rad(h["wind_dir"])
    h["wind_s"] = h["wind_speed"] * np.sin(rad)
    h["wind_c"] = h["wind_speed"] * np.cos(rad)

    daily = (h.groupby(["state", "date"])
               .agg(lat=("lat", "first"), lon=("lon", "first"),
                    wind_speed=("wind_speed", "mean"),
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


# ---------- 3. Fire features ----------
def load_fires():
    fires = pd.read_csv(RAW_DIR / "firms_hotspots.csv")
    # VIIRS confidence is l/n/h (low/nominal/high): drop low-confidence detections
    if fires["confidence"].dtype == object:
        fires = fires[fires["confidence"] != "l"]
    # Convert UTC detection time to the Malaysian local date, to match the weather days
    hhmm = fires["acq_time"].astype(int)
    utc = (pd.to_datetime(fires["acq_date"])
           + pd.to_timedelta(hhmm // 100, unit="h") + pd.to_timedelta(hhmm % 100, unit="m"))
    fires["date"] = (utc + pd.Timedelta(hours=UTC_OFFSET_H)).dt.floor("D")
    return {d: g[["latitude", "longitude", "frp"]].to_numpy()
            for d, g in fires.groupby("date")}


def fire_features(lat, lon, wind_from, date, fires_by_day):
    """All same-day fire features for one state, using fires from the last FIRE_DAYS days.

    fire_pressure: sum of intensity x wind alignment / distance
                   (alignment = 1 if the wind blows straight from fire to state, 0 if
                   sideways or away; uses the state's own wind as a proxy)
    fires_100km / fires_300km: number of fires within that distance, ignoring wind
    frp_300km: total fire intensity within 300 km, ignoring wind
    """
    days = [date - pd.Timedelta(days=k) for k in range(FIRE_DAYS)]
    chunks = [fires_by_day[d] for d in days if d in fires_by_day]
    if not chunks:
        return 0.0, 0, 0, 0.0
    f = np.vstack(chunks)
    f_lat, f_lon, frp = f[:, 0], f[:, 1], f[:, 2]

    dist_raw = haversine_km(f_lat, f_lon, lat, lon)
    dist = np.maximum(dist_raw, MIN_DIST_KM)
    fire_to_state = bearing_deg(f_lat, f_lon, lat, lon)
    blows_to = (wind_from + 180) % 360
    align = np.clip(np.cos(np.deg2rad(blows_to - fire_to_state)), 0, None)

    near, far = dist_raw <= NEAR_KM, dist_raw <= FAR_KM
    return (float(np.sum(frp * align / dist)),
            int(near.sum()), int(far.sum()), float(frp[far].sum()))


# ---------- 4. Sanity check against Razik's API data ----------
def check_against_api(df):
    """How well does each fire feature track next-day API?
    Uses api_daily.csv (state, date, api) or, failing that, hourly api_states.csv."""
    daily_path = PROCESSED_DIR / "api_daily.csv"
    hourly_path = PROCESSED_DIR / "api_states.csv"
    if daily_path.exists():
        api_daily = pd.read_csv(daily_path, parse_dates=["date"])
    elif hourly_path.exists():
        api = pd.read_csv(hourly_path, parse_dates=["time"])
        api["date"] = api["time"].dt.floor("D")
        api_daily = api.groupby(["state", "date"])["api"].mean().reset_index()
    else:
        print("(no api_daily.csv or api_states.csv found; skipping correlation check)")
        return

    m = df.merge(api_daily, on=["state", "date"], how="inner").sort_values(["state", "date"])
    m["api_next"] = m.groupby("state")["api"].shift(-1)
    sep19 = m[(m.date >= "2019-09-01") & (m.date <= "2019-10-06")]

    # Pearson = straight-line link; Spearman = any consistent up/down link
    # (better for skewed fire data, where most days are near zero)
    summary = pd.DataFrame({
        "pearson_all": [m[c].corr(m["api_next"]) for c in FIRE_COLS],
        "spearman_all": [m[c].corr(m["api_next"], method="spearman") for c in FIRE_COLS],
        "spearman_sep19": [sep19[c].corr(sep19["api_next"], method="spearman") for c in FIRE_COLS],
    }, index=FIRE_COLS).round(3)
    print("\nCorrelation with next-day API:")
    print(summary.to_string())

    per_state = (m.groupby("state")
                  .apply(lambda g: pd.Series({c: g[c].corr(g["api_next"], method="spearman")
                                              for c in ["fire_pressure_7d", "fires_300km"]}),
                         include_groups=False)
                  .round(3).sort_values("fires_300km", ascending=False))
    print("\nPer-state Spearman with next-day API:")
    print(per_state.to_string())


# ---------- 5. Assemble ----------
def build():
    hourly = pd.read_csv(RAW_DIR / "weather_hourly.csv", parse_dates=["time"])
    df = to_daily(hourly)

    print("Computing fire features...")
    fires_by_day = load_fires()
    feats = [fire_features(r.lat, r.lon, r.wind_dir, r.date, fires_by_day)
             for r in df.itertuples()]
    df[["fire_pressure", "fires_100km", "fires_300km", "frp_300km"]] = pd.DataFrame(
        feats, index=df.index)

    df = df.sort_values(["state", "date"])
    g = df.groupby("state")["fire_pressure"]
    df["fire_pressure_lag1"] = g.shift(1)
    # Rolling sums include today: smoke builds up over several days
    df["fire_pressure_3d"] = g.transform(lambda s: s.rolling(3, min_periods=1).sum())
    df["fire_pressure_7d"] = g.transform(lambda s: s.rolling(7, min_periods=1).sum())

    missing = set(STATES) - set(df["state"])
    if missing:
        print("WARNING: states with no weather data:", missing)

    out = df[["date", "state", "wind_speed", "wind_dir", "rain_mm"] + FIRE_COLS]
    out = out.reset_index(drop=True)
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    out.to_csv(PROCESSED_DIR / "features.csv", index=False)
    print(f"Saved features.csv ({len(out):,} rows, {out.state.nunique()} states, "
          f"{out.date.min().date()} to {out.date.max().date()})")

    check_against_api(out)


if __name__ == "__main__":
    build()