"""
Shared settings for the haze prediction pipeline.
Edit together, rarely. Both prep/ and models/ import from here.
"""
from pathlib import Path

# ---- Paths (work no matter which folder you run from) ----
ROOT = Path(__file__).resolve().parents[2]          # .../hazeguard
RAW_DIR = ROOT / "data" / "raw"
PROCESSED_DIR = ROOT / "data" / "processed"
OUT_DIR = ROOT / "data" / "out"

# ---- Dates ----
# CAMS global PM2.5 on Open-Meteo starts Aug 2022.
DATA_START = "2022-08-01"
DATA_END = "2023-10-31"
TEST_START = "2023-09-25"     # 2023 haze event week(s): held out for scoring + demo replay
TEST_END = "2023-10-08"
TIMEZONE = "Asia/Kuala_Lumpur"

# ---- Forecast settings ----
HORIZONS_H = [24, 48, 72]     # hours ahead
UNHEALTHY_PM25 = 55           # µg/m³: arrival time = first forecast hour above this

# ---- Fire data ----
FIRMS_SOURCE = "VIIRS_SNPP_SP"
FIRMS_BBOX = "95,-6,120,8"    # west,south,east,north: Sumatra, Kalimantan, Malaysia

# ---- Malaysian zones: name -> (lat, lon) ----
ZONES = {
    "Kuala Lumpur": (3.139, 101.687), "Shah Alam": (3.073, 101.518),
    "Klang": (3.044, 101.445),        "Putrajaya": (2.926, 101.696),
    "Seremban": (2.725, 101.938),     "Melaka": (2.189, 102.250),
    "Johor Bahru": (1.492, 103.741),  "Ipoh": (4.597, 101.090),
    "Penang": (5.414, 100.329),       "Alor Setar": (6.121, 100.367),
    "Kota Bharu": (6.125, 102.238),   "Kuantan": (3.817, 103.326),
    "Kuching": (1.553, 110.359),      "Sibu": (2.288, 111.831),
    "Miri": (4.399, 113.991),
}