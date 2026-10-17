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
# Target = Malaysian API index (Kaggle APIMS, state-averaged by aggregate_states.py).
# Weather (Open-Meteo archive) and fires (FIRMS VIIRS) are fetched for the same range.
DATA_START = "2013-01-01"
DATA_END = "2019-10-06"
TRAIN_END = "2019-08-28"      # gap before the test starts, so 3-day-ahead targets never leak into it
TEST_START = "2019-09-01"     # Sep 2019 haze: held out for scoring + demo replay
TEST_END = "2019-10-06"
TIMEZONE = "Asia/Kuala_Lumpur"

# ---- Forecast settings ----
HORIZONS_DAYS = [1, 2, 3]     # daily mean API, 1/2/3 days ahead
UNHEALTHY_API = 101           # API index: "unhealthy" starts at 101

# ---- Fire data ----
FIRMS_SOURCE = "VIIRS_SNPP_SP"
FIRMS_BBOX = "95,-6,120,8"    # west,south,east,north: Sumatra, Kalimantan, Malaysia

# ---- States: name -> (lat, lon) of one representative point ----
# Names must match the 'state' column in data/processed/api_states.csv exactly.
STATES = {
    "Johor": (1.492, 103.741),            # Johor Bahru
    "Kedah": (6.121, 100.367),            # Alor Setar
    "Kelantan": (6.125, 102.238),         # Kota Bharu
    "Kuala Lumpur": (3.139, 101.687),
    "Labuan": (5.277, 115.242),           # Victoria
    "Melaka": (2.189, 102.250),
    "Negeri Sembilan": (2.725, 101.938),  # Seremban
    "Pahang": (3.817, 103.326),           # Kuantan
    "Perak": (4.597, 101.090),            # Ipoh
    "Perlis": (6.441, 100.199),           # Kangar
    "Pulau Pinang": (5.414, 100.329),     # George Town
    "Putrajaya": (2.926, 101.696),
    "Sabah": (5.980, 116.074),            # Kota Kinabalu
    "Sarawak": (1.553, 110.359),          # Kuching
    "Selangor": (3.073, 101.518),         # Shah Alam
    "Terengganu": (5.330, 103.137),       # Kuala Terengganu
}   