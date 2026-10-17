"""
Turn the station-level APIMS file into one API series per state.

Input : data/raw/APIMS-final.csv   (one column per monitoring station)
Output: data/processed/api_states.csv  (long format, one row per state per time)

Output columns
    time        timestamp (hourly for most years; daily for 10 Jun 2015 to Dec 2016)
    state       state or federal territory name
    api         mean API across the state's stations that reported at that time
    api_max     worst single station in the state at that time
    n_stations  how many stations contributed (0 rows are dropped)

Run from the repo root:
    python -m pipeline.physical.data.aggregate_states
"""

from pathlib import Path

import pandas as pd

RAW = Path("data/raw/APIMS-final.csv")
OUT = Path("data/processed/api_states.csv")

# Every station in the file, mapped to its state.
# Federal territories (KL, Putrajaya, Labuan) are kept as their own "states".
STATION_TO_STATE = {
    # Johor
    "Batu Pahat": "Johor", "Kluang": "Johor", "Kota Tinggi": "Johor",
    "Larkin": "Johor", "Muar": "Johor", "Pasir Gudang": "Johor",
    "Pengerang": "Johor", "Segamat": "Johor", "Tangkak": "Johor",
    # Kedah
    "Alor Setar": "Kedah", "Kulim": "Kedah", "Langkawi": "Kedah",
    "Sungai Petani": "Kedah",
    # Kelantan
    "Kota Bharu": "Kelantan", "Tanah Merah": "Kelantan",
    # Melaka
    "Alor Gajah": "Melaka", "Bandaraya Melaka": "Melaka",
    "Bukit Rambai": "Melaka",
    # Negeri Sembilan
    "Nilai": "Negeri Sembilan", "Port Dickson": "Negeri Sembilan",
    "Seremban": "Negeri Sembilan",
    # Pahang
    "Balok Baru Kuantan": "Pahang", "Indera Mahkota Kuantan": "Pahang",
    "Jerantut": "Pahang", "Rompin": "Pahang", "Temerloh": "Pahang",
    # Perak
    "Pegoh Ipoh": "Perak", "Seri Manjung": "Perak", "Taiping": "Perak",
    "Tanjung Malim": "Perak", "Tasek Ipoh": "Perak",
    # Perlis
    "Kangar": "Perlis",
    # Pulau Pinang
    "Balik Pulau": "Pulau Pinang", "Minden": "Pulau Pinang",
    "Seberang Jaya": "Pulau Pinang", "Seberang Perai": "Pulau Pinang",
    # Sabah
    "Keningau": "Sabah", "Kimanis": "Sabah", "Kota Kinabalu": "Sabah",
    "Sandakan": "Sabah", "Tawau": "Sabah",
    # Sarawak
    "Bintulu": "Sarawak", "Kapit": "Sarawak", "Kuching": "Sarawak",
    "Limbang": "Sarawak", "Miri": "Sarawak", "Mukah": "Sarawak",
    "Samalaju": "Sarawak", "Samarahan": "Sarawak", "Sarikei": "Sarawak",
    "Sibu": "Sarawak", "Sri Aman": "Sarawak",
    # Selangor
    "Banting": "Selangor", "Klang": "Selangor", "Kuala Selangor": "Selangor",
    "Petaling Jaya": "Selangor", "Shah Alam": "Selangor",
    # Terengganu
    "Besut": "Terengganu", "Kemaman": "Terengganu",
    "Kuala Terengganu": "Terengganu", "Paka": "Terengganu",
    # Federal territories
    "Batu Muda": "Kuala Lumpur", "Cheras": "Kuala Lumpur",
    "Putrajaya": "Putrajaya",
    "Labuan": "Labuan",
}


def parse_time(col: pd.Series) -> pd.Series:
    """The file mixes two formats: 'YYYY-MM-DD HH:MM' (hourly) and
    'YYYY-MM-DD' (the daily stretch from Jun 2015 to Dec 2016)."""
    hourly = pd.to_datetime(col, format="%Y-%m-%d %H:%M", errors="coerce")
    daily = pd.to_datetime(col, format="%Y-%m-%d", errors="coerce")
    t = hourly.fillna(daily)
    if t.isna().any():
        raise ValueError(f"Unparsed times: {col[t.isna()].head().tolist()}")
    return t


def main() -> None:
    wide = pd.read_csv(RAW)
    wide["time"] = parse_time(wide.pop("Time"))

    # Fail loudly if the file gains or loses a station, instead of silently dropping it.
    stations = set(wide.columns) - {"time"}
    unmapped = stations - set(STATION_TO_STATE)
    missing = set(STATION_TO_STATE) - stations
    if unmapped or missing:
        raise ValueError(f"Unmapped stations: {unmapped} | mapped but absent: {missing}")

    # Wide (one column per station) -> long (one row per station per time).
    long = wide.melt(id_vars="time", var_name="station", value_name="api")
    long = long.dropna(subset=["api"])
    long["state"] = long["station"].map(STATION_TO_STATE)

    states = (
        long.groupby(["time", "state"])["api"]
        .agg(api="mean", api_max="max", n_stations="count")
        .reset_index()
        .sort_values(["state", "time"])
    )
    states["api"] = states["api"].round(1)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    states.to_csv(OUT, index=False)
    print(f"Wrote {len(states):,} rows for {states['state'].nunique()} states to {OUT}")


if __name__ == "__main__":
    main()
