"""
Combines FIRMS country yearly files (downloaded from firms.modaps.eosdis.nasa.gov/country/)
into data/raw/firms_hotspots.csv, the same file fetch.py would produce.

Put the VIIRS S-NPP files for Indonesia + Malaysia, 2013-2019 (.csv or .zip),
in data/raw/firms_country/, then run from the hazeguard/ folder:
    python -m pipeline.physical.prep.combine_firms
"""
import zipfile

import pandas as pd

from pipeline.physical.config import RAW_DIR, DATA_START, DATA_END, FIRMS_BBOX

COLS = ["latitude", "longitude", "frp", "acq_date", "acq_time", "confidence"]
SRC = RAW_DIR / "firms_country"


def read_any(path):
    """Read a .csv, or every .csv inside a .zip."""
    if path.suffix == ".zip":
        with zipfile.ZipFile(path) as z:
            return [pd.read_csv(z.open(n), usecols=COLS)
                    for n in z.namelist() if n.endswith(".csv")]
    return [pd.read_csv(path, usecols=COLS)]


if __name__ == "__main__":
    files = sorted(list(SRC.glob("*.csv")) + list(SRC.glob("*.zip")))
    if not files:
        raise SystemExit(f"No files found in {SRC}")

    frames = []
    for f in files:
        parts = read_any(f)
        print(f"  {f.name}: {sum(len(p) for p in parts):,} rows")
        frames += parts
    fires = pd.concat(frames, ignore_index=True)

    # Keep only our date range and the same box fetch.py uses
    west, south, east, north = map(float, FIRMS_BBOX.split(","))
    fires = fires[(fires.acq_date >= DATA_START) & (fires.acq_date <= DATA_END)
                  & fires.longitude.between(west, east)
                  & fires.latitude.between(south, north)]
    fires = fires.drop_duplicates()

    fires.to_csv(RAW_DIR / "firms_hotspots.csv", index=False)
    print(f"Saved firms_hotspots.csv ({len(fires):,} hotspots, "
          f"{fires.acq_date.min()} to {fires.acq_date.max()})")
    print("Hotspots per year:")
    print(fires.acq_date.str[:4].value_counts().sort_index().to_string())