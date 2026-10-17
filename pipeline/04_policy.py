"""Attribute haze sources and estimate associated costs.

HazeGuard-MY — Damage Costs (economic impact layer)
===================================================

Sector-by-sector estimate of the RM damage caused by the 2026 haze, for every
Malaysian state: to date, and over the 72-hour forecast window under best /
expected / worst scenarios. Writes ``data/out/damage.json``.

Method (one formula, applied per sector, per state, per day)
------------------------------------------------------------

    Damage = Daily Economic Value x Loss Rate(haze band) x Share of activity affected x Days

    e.g. RM 10 m/day x 5% x 3 days = RM 1.5 m     (see ``damage_cost``)

Sectors summed into the total:

    tourism_loss               Tourism & hospitality   inbound + domestic tourism receipts (Tourism Malaysia / DOSM)
    agriculture_loss           Agriculture             state agriculture GDP (OpenDOSM gdp_state_real_supply)
    transport_loss             Transport & logistics   transport & storage share of state services GDP
    construction_outdoor_loss  Construction & outdoor  state construction GDP + outdoor share of services
    productivity_loss          Other sectors' workers  sick days among workers NOT in the sectors above
    health                     Healthcare              extra clinic visits / admissions x MOH unit costs

Reported separately, NOT added to the total (they overlap with the sectors above):

    school_closure   parents' lost work days are already inside the sector losses
    response         cloud seeding — public spending, not lost output
    mortality        off: haze is treated as non-fatal

Data
----
* Sector baselines: OpenDOSM parquet files are downloaded once and cached in
  ``data/raw/opendosm/`` (needs internet + pyarrow). If that fails, built-in
  values from DOSM's published 2025 figures are used and labelled as such.
* Haze: ``data/raw/pm25_daily_openmeteo.csv`` (real) where available, otherwise
  synthetic haze calibrated to the 2026 episode (``build_mock``).
* Forecast: ``data/out/forecast.json`` (Forecast team) if present, otherwise
  the last 3 days are assumed to persist.

Run (from the repo root)::

    python pipeline/04_policy.py                 # writes data/out/damage.json
    python pipeline/04_policy.py --sims 200 --dry-run
    python pipeline/04_policy.py --offline       # skip the OpenDOSM download
    python pipeline/04_policy.py --save-mock     # also writes the mock CSVs

Tests and graphs: ``tests/04_damage_tests.ipynb``.
"""
from __future__ import annotations

import argparse
import io
import json
import sys
import urllib.request
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
MOCK_DIR = ROOT / "data" / "mock" / "damage"
OUT_PATH = ROOT / "data" / "out" / "damage.json"
OPENDOSM_DIR = ROOT / "data" / "raw" / "opendosm"
FORECAST_PATHS = [ROOT / "data" / "out" / "forecast.json", ROOT / "data" / "mock" / "forecast.json"]
STATES_JSON = ROOT / "data" / "out" / "states.json"
REAL_PM25_PATHS = [ROOT / "data" / "raw" / "pm25_daily_openmeteo.csv",
                   ROOT / "data" / "resource_allocation" / "raw" / "pm25_daily_openmeteo.csv"]
OPENDOSM_URLS = {
    "gdp_state_real_supply": "https://storage.dosm.gov.my/gdp/gdp_state_real_supply.parquet",
    "gdp_lookup": "https://storage.dosm.gov.my/gdp/gdp_lookup.parquet",
}

SEED = 42
DUMMY = "DUMMY — replace"
APPROX = "APPROX — check"

# Summed into the total ------------------------------------------------------
SECTORS = {
    "tourism_loss": "Tourism & hospitality",
    "agriculture_loss": "Agriculture",
    "transport_loss": "Transport & logistics",
    "construction_outdoor_loss": "Construction & outdoor business",
    "productivity_loss": "Other sectors (sick days)",
    "health": "Healthcare (direct medical costs)",
}
CATEGORIES = list(SECTORS)
# Reported, not summed (overlap with the sectors, or not lost output) ------------
MEMO = ["school_closure", "response", "mortality"]
INCLUDE_MORTALITY = False
BANDS = ["good", "moderate", "unhealthy", "very_unhealthy", "hazardous"]
HORIZONS = ("24", "48", "72")

# Published reference points used to sanity-check the totals (RM)
BENCHMARKS = {
    "2026_daily_low_rm": 20e6, "2026_daily_high_rm": 50e6,      # The Star, 14 Oct 2026 (Yeah Kim Leng)
    "2013_total_rm": 1.7e9, "2013_illness_rm": 410.6e6,         # MJPHM literature review
    "1997_total_rm": 0.8e9,                                     # The Star, 14 Oct 2026 (Yeah Kim Leng)
}

# ---------------------------------------------------------------------------
# 1. Loss rates: share of a sector's daily activity lost on a day in each API band,
#    as (low, central, high). low/high feed the best/worst scenarios and the Monte Carlo.
# ---------------------------------------------------------------------------
LOSS_RATES: dict[str, dict] = {
    "tourism_loss": dict(
        moderate=(0.0, 0.005, 0.01), unhealthy=(0.03, 0.06, 0.10),
        very_unhealthy=(0.06, 0.12, 0.20), hazardous=(0.12, 0.25, 0.40),
        source="Arrivals down up to 8% in the 2026 haze (Yeah Kim Leng, The Star, 14 Oct 2026); "
               "cancellations and event postponements at API > 150 (Borneo Post, 31 Aug 2026)"),
    "agriculture_loss": dict(
        moderate=(0.0, 0.0, 0.0), unhealthy=(0.005, 0.01, 0.02),
        very_unhealthy=(0.01, 0.025, 0.05), hazardous=(0.03, 0.05, 0.10),
        source="Reduced sunlight cuts photosynthesis; analysts warned of a 10–20% oil-palm output fall "
               "after the prolonged 2015 haze (The Star, 18 Sep 2015). Daily rates kept well below that"),
    "transport_loss": dict(
        moderate=(0.0, 0.0, 0.0), unhealthy=(0.0, 0.005, 0.01),
        very_unhealthy=(0.01, 0.02, 0.04), hazardous=(0.03, 0.06, 0.10),
        source=f"{APPROX}: flight delays and port/road visibility limits mostly above API 200"),
    "construction_outdoor_loss": dict(
        moderate=(0.0, 0.0, 0.0), unhealthy=(0.01, 0.03, 0.05),
        very_unhealthy=(0.05, 0.12, 0.20), hazardous=(0.20, 0.35, 0.50),
        source="MOH advice: minimise strenuous outdoor work at API 101–200, avoid it at 201–300 "
               "(Borneo Post, 31 Aug 2026)"),
    "productivity_loss": dict(
        moderate=(0.0, 0.0, 0.0), unhealthy=(0.001, 0.002, 0.004),
        very_unhealthy=(0.005, 0.01, 0.02), hazardous=(0.01, 0.025, 0.04),
        source="Medical leave ~RM 203 m in the 2013 haze (MJPHM review); applied only to sectors not listed above"),
}

# ---------------------------------------------------------------------------
# 2. Other coefficients. value = central; (low, high) = Monte Carlo range (~5th/95th pct).
# ---------------------------------------------------------------------------
COEFFS: dict[str, dict] = {
    # healthcare: dose-response (log relative risk per +10 µg/m3 PM2.5 above threshold)
    "crf_resp_admission":   dict(value=0.015, low=0.008, high=0.025, unit="log-RR per 10 µg/m3",
                                 source=f"{APPROX} (short-term PM2.5 meta-analyses, ~1–2%)"),
    "crf_cardio_admission": dict(value=0.008, low=0.004, high=0.013, unit="log-RR per 10 µg/m3",
                                 source=f"{APPROX} (short-term PM2.5 meta-analyses, ~0.5–1%)"),
    "crf_outpatient":       dict(value=0.025, low=0.012, high=0.040, unit="log-RR per 10 µg/m3",
                                 source=f"{APPROX} (clinic visits respond more than admissions)"),
    "base_resp_adm":   dict(value=2.1,  low=1.6, high=2.7, unit="admissions /100k /day",
                            source=f"{DUMMY} (MOH Health Facts: respiratory admissions)"),
    "base_cardio_adm": dict(value=1.6,  low=1.2, high=2.1, unit="admissions /100k /day",
                            source=f"{DUMMY} (MOH Health Facts: circulatory admissions)"),
    "base_outpatient": dict(value=38.0, low=28., high=50., unit="resp. clinic visits /100k /day",
                            source=f"{DUMMY} (MOH Health Facts: outpatient attendances)"),
    "pm25_threshold":  dict(value=15.0, low=15.0, high=15.0, unit="µg/m3", source="WHO 2021 24-hour AQG"),
    "cost_resp_admission":   dict(value=4200, low=2800, high=6000, unit="RM / admission",
                                  source=f"{DUMMY} (MOH cost per admission)"),
    "cost_cardio_admission": dict(value=9800, low=6500, high=14000, unit="RM / admission", source=DUMMY),
    "cost_outpatient":       dict(value=120, low=80, high=180, unit="RM / visit", source=DUMMY),
    # sector sizes that aren't published by state
    "transport_share_of_services": dict(value=0.065, low=0.055, high=0.075, unit="share of services GDP",
                                        source=f"{APPROX} (transport & storage ≈ 3.5–4% of national GDP)"),
    "outdoor_share_of_services":   dict(value=0.03, low=0.015, high=0.05, unit="share of services GDP",
                                        source=f"{DUMMY} (markets, hawkers, recreation, outdoor retail)"),
    # memo items (not summed)
    "school_cost_per_student_day": dict(value=15, low=8, high=25, unit="RM / student / closed day",
                                        source=f"{DUMMY} (childcare + learning loss; memo only)"),
    "cloud_seeding_cost": dict(value=150_000, low=100_000, high=250_000, unit="RM / operation",
                               source=f"{DUMMY} (memo only)"),
}

# Scenarios for the 72-hour forecast window
SCENARIOS = {
    "best":     dict(pm25_factor=0.75, rates="low", coverage=0.75, days=2, extra_days=0,
                     label="Mild haze, clears after 2 days, low loss rates"),
    "expected": dict(pm25_factor=1.00, rates="central", coverage=1.00, days=3, extra_days=0,
                     label="Forecast severity for 72 h, central loss rates"),
    "worst":    dict(pm25_factor=1.35, rates="high", coverage=1.25, days=3, extra_days=4,
                     label="Severe haze for 72 h, high loss rates, persists 4 more days"),
}

STATE_NAMES = ["Johor", "Kedah", "Kelantan", "Melaka", "Negeri Sembilan", "Pahang", "Perak", "Perlis",
               "Pulau Pinang", "Sabah", "Sarawak", "Selangor", "Terengganu", "W.P. Kuala Lumpur",
               "W.P. Labuan", "W.P. Putrajaya"]

# ---------------------------------------------------------------------------
# 3. State economic baseline (fallback when OpenDOSM can't be downloaded)
#   gdp_rm_bn: DOSM "GDP by State 2025" (real, 2015 prices) — published figures.
#   agri / construction / services shares: approximate (DOSM state GDP tables) — replaced
#     automatically by gdp_state_real_supply when the download works.
#   domestic tourism receipts: DOSM 2025 (KL 16.9, Selangor 15.8, Pahang 9.8 bn published; rest split
#     approximately so the total is RM 121.3 bn). Inbound receipts RM 110.6 bn split approximately.
# ---------------------------------------------------------------------------
_STATE_BASE = pd.DataFrame([
    # state,              region,      lat,   lon,    pop_k, gdp_bn, agri,  constr, serv, dom_tour_bn, inbound_share, flights/day
    ("Johor",             "Peninsular", 1.49, 103.74, 4180, 170.9, .105, .055, .48, 11.4, .12, 90),
    ("Kedah",             "Peninsular", 6.12, 100.37, 2200,  56.3, .080, .025, .56,  6.9, .04, 40),
    ("Kelantan",          "Peninsular", 6.13, 102.24, 1920,  29.8, .170, .035, .718, 4.6, .005, 30),
    ("Melaka",            "Peninsular", 2.19, 102.25, 1050,  50.3, .070, .040, .524, 8.0, .06, 4),
    ("Negeri Sembilan",   "Peninsular", 2.73, 101.94, 1230,  55.8, .090, .030, .47,  5.7, .015, 0),
    ("Pahang",            "Peninsular", 3.81, 103.33, 1690,  71.0, .200, .035, .45,  9.8, .03, 12),
    ("Perak",             "Peninsular", 4.60, 101.09, 2550,  91.5, .136, .030, .56,  9.2, .02, 6),
    ("Perlis",            "Peninsular", 6.44, 100.20,  300,   6.7, .170, .040, .70,  1.1, .005, 0),
    ("Pulau Pinang",      "Peninsular", 5.41, 100.33, 1800, 130.3, .020, .030, .48,  9.2, .10, 110),
    ("Sabah",             "Borneo",     5.98, 116.07, 3550,  88.8, .170, .030, .50,  8.0, .08, 180),
    ("Sarawak",           "Borneo",     1.55, 110.36, 2550, 153.5, .120, .030, .38,  8.0, .05, 160),
    ("Selangor",          "Peninsular", 3.07, 101.52, 7400, 460.1, .015, .045, .58, 15.8, .15, 700),
    ("Terengganu",        "Peninsular", 5.33, 103.14, 1290,  40.6, .080, .035, .50,  4.6, .01, 20),
    ("W.P. Kuala Lumpur", "Peninsular", 3.14, 101.69, 2020, 265.1, .000, .050, .917, 16.9, .30, 60),
    ("W.P. Labuan",       "Borneo",     5.28, 115.23,  100,   8.7, .030, .020, .70,  0.8, .005, 20),
    ("W.P. Putrajaya",    "Peninsular", 2.93, 101.70,  120,  14.4, .000, .040, .95,  1.3, .01, 0),
], columns=["state", "region", "lat", "lon", "pop_k", "gdp_rm_bn", "agri_share", "construction_share",
            "services_share", "domestic_tourism_rm_bn", "inbound_share", "airport_flights_day"])
INBOUND_TOURISM_RM_BN = 110.6      # 2025, MOF Economic Outlook 2027 via The Star, 9 Oct 2026
DOMESTIC_TOURISM_RM_BN = 121.3     # 2025, DOSM via Business Today, 15 Sep 2026
EMPLOYMENT_RATE = 0.49             # employed / population (LFS 2026 ≈ 16.9 m / 34.4 m)

BASELINE_SOURCES = {
    "gdp_rm_bn": "DOSM, GDP by State 2025 (real, 2015 prices), released 1 Jul 2026",
    "sector_shares": f"{APPROX} (DOSM state GDP tables); auto-replaced by OpenDOSM gdp_state_real_supply",
    "tourism": "Inbound RM 110.6 bn (MOF Economic Outlook 2027); domestic RM 121.3 bn (DOSM 2025). "
               f"State split {APPROX} except KL/Selangor/Pahang domestic (published)",
    "population": f"{APPROX} — replace with OpenDOSM population_state (Resource Allocation branch has it)",
}


def _download(name: str, timeout: int = 15) -> pd.DataFrame | None:
    """Read an OpenDOSM parquet from the local cache, downloading it once if needed."""
    path = OPENDOSM_DIR / f"{name}.parquet"
    try:
        if not path.exists():
            with urllib.request.urlopen(OPENDOSM_URLS[name], timeout=timeout) as r:
                data = r.read()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        return pd.read_parquet(path)
    except Exception as e:   # no internet, blocked, or no pyarrow
        print(f"[info] OpenDOSM {name} unavailable ({type(e).__name__}); using built-in 2025 values", file=sys.stderr)
        return None


def opendosm_sector_shares(fetch: bool = True) -> tuple[pd.DataFrame | None, str]:
    """State agri / construction / services shares + GDP from OpenDOSM gdp_state_real_supply (latest year)."""
    if not fetch and not (OPENDOSM_DIR / "gdp_state_real_supply.parquet").exists():
        return None, "offline"
    gdp = _download("gdp_state_real_supply") if fetch or (OPENDOSM_DIR / "gdp_state_real_supply.parquet").exists() else None
    if gdp is None:
        return None, "unavailable"
    try:
        lookup = _download("gdp_lookup")
        names = {}
        if lookup is not None and "sector" in lookup:
            text_cols = [c for c in lookup.columns if c != "sector" and not pd.api.types.is_numeric_dtype(lookup[c])]
            for _, r in lookup.iterrows():
                names[r["sector"]] = " ".join(str(r[c]) for c in text_cols).lower()
        g = gdp[gdp.series == "abs"] if "series" in gdp else gdp
        g = g[pd.to_datetime(g.date) == pd.to_datetime(g.date).max()]
        year = pd.to_datetime(g.date).max().year

        def kind(code):
            t = names.get(code, str(code)).lower()
            for k in ("agri", "construct", "service", "mining", "manufact", "import", "overall", "total"):
                if k in t:
                    return k
            return t
        g = g.assign(kind=g.sector.map(kind))
        p = g.pivot_table(index="state", columns="kind", values="value", aggfunc="sum")
        total = p.get("overall", p.get("total", p.drop(columns=[c for c in p.columns if c == "import"], errors="ignore").sum(axis=1)))
        out = pd.DataFrame({"gdp_rm_bn": total / 1000, "agri_share": p["agri"] / total,
                            "construction_share": p["construct"] / total, "services_share": p["service"] / total})
        out.index = out.index.str.replace("^Kuala Lumpur$", "W.P. Kuala Lumpur", regex=True) \
                             .str.replace("^Labuan$", "W.P. Labuan", regex=True) \
                             .str.replace("^Putrajaya$", "W.P. Putrajaya", regex=True) \
                             .str.replace("^Penang$", "Pulau Pinang", regex=True)
        return out.dropna(), f"OpenDOSM gdp_state_real_supply ({year})"
    except Exception as e:
        print(f"[warn] could not parse OpenDOSM GDP ({e}); using built-in values", file=sys.stderr)
        return None, "parse-failed"


def state_economy(fetch: bool = True) -> tuple[pd.DataFrame, dict]:
    """One row per state with the daily economic value (RM/day) of each sector."""
    ref = _STATE_BASE.copy()
    sources = dict(BASELINE_SOURCES)
    shares, src = opendosm_sector_shares(fetch)
    if shares is not None:
        for s in ref.state:
            key = s if s in shares.index else ("W.P. Kuala Lumpur" if s == "W.P. Putrajaya" else None)
            if key in shares.index:
                for col in ("agri_share", "construction_share", "services_share"):
                    ref.loc[ref.state == s, col] = shares.loc[key, col]
        sources["sector_shares"] = src + " (Putrajaya uses KL's shares: OpenDOSM combines them)"
    ref["population"] = (ref.pop("pop_k") * 1000).astype(int)
    ref["employed"] = (ref.population * EMPLOYMENT_RATE).astype(int)
    ref["students"] = (ref.population * 0.15).astype(int)
    ref["pct_65plus"] = 0.075
    ref["gdp_per_day_rm"] = ref.gdp_rm_bn * 1e9 / 365
    ref["services_gdp_day_rm"] = ref.gdp_per_day_rm * ref.services_share
    # Daily economic value per sector (RM/day) -----------------------------------------
    dom = ref.domestic_tourism_rm_bn / ref.domestic_tourism_rm_bn.sum() * DOMESTIC_TOURISM_RM_BN
    inb = ref.inbound_share / ref.inbound_share.sum() * INBOUND_TOURISM_RM_BN
    ref["dev_tourism_loss"] = (dom + inb) * 1e9 / 365
    ref["dev_agriculture_loss"] = ref.gdp_per_day_rm * ref.agri_share
    return ref, sources


def add_sector_values(ref: pd.DataFrame, c: dict) -> pd.DataFrame:
    """Sector daily values that depend on coefficients (so they vary in the Monte Carlo)."""
    ref = ref.copy()
    ref["dev_transport_loss"] = ref.services_gdp_day_rm * c["transport_share_of_services"]
    ref["dev_construction_outdoor_loss"] = (ref.gdp_per_day_rm * ref.construction_share
                                            + ref.services_gdp_day_rm * c["outdoor_share_of_services"])
    listed = (ref.dev_agriculture_loss + ref.dev_transport_loss + ref.dev_construction_outdoor_loss)
    ref["dev_productivity_loss"] = (ref.gdp_per_day_rm - listed).clip(lower=0)   # everything else
    return ref


# PM2.5 (µg/m3, state average) -> API. Approximate: calibrated so state-average PM2.5 ~50 gives API ~95
# and ~120 gives API ~175, matching Aug–Oct 2026 APIMS readings reported in the news.
_PM_BP = [0, 15, 35, 55, 100, 150, 250, 500]
_API_BP = [0, 30, 70, 100, 160, 200, 300, 500]


def pm25_to_api(pm25):
    return np.interp(np.asarray(pm25, dtype=float), _PM_BP, _API_BP)


def api_to_pm25(api):
    return np.interp(np.asarray(api, dtype=float), _API_BP, _PM_BP)


def api_band(api) -> np.ndarray:
    return pd.cut(np.asarray(api, dtype=float), [-1, 50, 100, 200, 300, 1e9], labels=BANDS).astype(str)


def damage_cost(daily_value, loss_rate, affected_days=1, affected_share=1.0):
    """The core formula: Daily Economic Value x Loss Rate x Affected Days (x share of activity affected)."""
    return np.asarray(daily_value) * np.asarray(loss_rate) * np.asarray(affected_days) * np.asarray(affected_share)


# ---------------------------------------------------------------------------
# 4. Haze data (synthetic, calibrated to the real 2026 pattern)
# ---------------------------------------------------------------------------
_HAZE_SHAPE = {  # state: (background, wave_A ~31 Aug, wave_C ~22 Sep, wave_B ~15 Oct), state-average PM2.5
    "Sarawak": (25, 165, 95, 25),        "Selangor": (22, 70, 30, 75),
    "W.P. Kuala Lumpur": (22, 68, 30, 72), "W.P. Putrajaya": (20, 62, 26, 70),
    "Negeri Sembilan": (18, 48, 20, 62), "Melaka": (16, 40, 16, 58),
    "Johor": (17, 40, 16, 55),           "Perak": (15, 52, 20, 42),
    "Pulau Pinang": (15, 36, 12, 34),    "Kedah": (13, 32, 12, 30),
    "Perlis": (11, 28, 8, 22),           "Pahang": (12, 32, 12, 24),
    "Terengganu": (11, 32, 12, 16),      "Kelantan": (10, 28, 8, 12),
    "Sabah": (14, 38, 16, 12),           "W.P. Labuan": (14, 50, 18, 12),
}


def build_mock(seed: int = SEED, fetch: bool = False) -> dict[str, pd.DataFrame]:
    """State reference + SYNTHETIC daily haze (15 Aug – 16 Oct 2026) + mock 'ground-truth' costs."""
    rng = np.random.default_rng(seed)
    ref, _ = state_economy(fetch)
    dates = pd.date_range("2026-08-15", "2026-10-16", freq="D")
    t = np.arange(len(dates))
    wave_a = np.exp(-.5 * ((t - 16) / 5.0) ** 2)
    wave_c = np.exp(-.5 * ((t - 38) / 3.5) ** 2)
    wave_b = np.exp(-.5 * ((t - 61) / 4.0) ** 2)
    rows = []
    for s in ref.itertuples():
        bg, a, c, b = _HAZE_SHAPE[s.state]
        signal = bg + a * wave_a + c * wave_c + b * wave_b
        wiggle = np.exp(np.convolve(rng.normal(0, .22, len(t) + 2), np.ones(3) / 3, "valid"))
        pm25 = np.clip(signal * wiggle, 4, 400)
        n_st = max(1, int(round(np.sqrt(s.population) / 450)) + 1)
        pm25_worst = pm25 * rng.uniform(1.05, 1.35, len(t))
        api_mean, api_max = pm25_to_api(pm25), pm25_to_api(pm25_worst)
        for i, d in enumerate(dates):
            st_api = pm25_to_api(pm25[i] * rng.uniform(.75, 1.35, n_st))
            rows.append(dict(date=d.date().isoformat(), state=s.state, pm25_ugm3=round(pm25[i], 1),
                             api_mean=round(float(api_mean[i]), 1), api_max=round(float(api_max[i]), 1),
                             stations_total=n_st, stations_unhealthy=int((st_api > 100).sum()),
                             stations_over_200=int((st_api > 200).sum()),
                             flights_scheduled=int(s.airport_flights_day)))
    haze = derive_haze_columns(pd.DataFrame(rows), rng)
    truth = damage_by_day(haze, ref, central(), rng=rng)
    return {"state_reference": ref, "haze_daily": haze, "damage_costs_daily": truth}


def derive_haze_columns(haze: pd.DataFrame, rng: np.random.Generator | None = None) -> pd.DataFrame:
    """Columns that follow from PM2.5/API: band, share of the state affected, closures, flights, cloud seeding."""
    haze = haze.sort_values(["state", "date"], ignore_index=True).copy()
    haze["api_band"] = api_band(haze.api_max)
    # share of the state's activity under the day's band (from the share of stations that are unhealthy)
    st = haze.stations_total.clip(lower=1)
    haze["affected_share"] = np.where(haze.api_max > 100,
                                      np.clip(haze.stations_unhealthy / st, 0.25, 1.0), 1.0).round(2)
    over150_2d = haze.groupby("state").api_max.transform(lambda x: (x > 150) & (x.shift(1) > 150))
    share = haze.stations_over_200 / st
    haze["school_closed_share"] = np.maximum(share, np.where(over150_2d, .5, 0)).round(2)
    haze["school_closed"] = (haze.school_closed_share > 0).astype(int)
    frac = np.clip((haze.api_max - 150) / 1000, 0, .15)
    lam = haze.flights_scheduled * frac
    haze["flights_disrupted"] = rng.poisson(lam) if rng is not None else np.round(lam).astype(int)
    p = np.clip((haze.api_max - 180) / 300, 0, .6)
    haze["cloud_seeding_ops"] = rng.binomial(1, p) if rng is not None else (p > .3).astype(int)
    return haze


def load_real_pm25() -> tuple[pd.DataFrame | None, str | None]:
    for p in REAL_PM25_PATHS:
        if p.exists():
            return pd.read_csv(p)[["state", "date", "pm25"]], str(p.relative_to(ROOT))
    return None, None


def apply_real_pm25(haze: pd.DataFrame, real: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """Overwrite synthetic PM2.5 with real values where dates/states match; re-derive API etc."""
    m = haze.merge(real.rename(columns={"pm25": "pm25_real"}), on=["state", "date"], how="left")
    hit = m.pm25_real.notna()
    m.loc[hit, "pm25_ugm3"] = m.loc[hit, "pm25_real"]
    m.loc[hit, "api_mean"] = pm25_to_api(m.loc[hit, "pm25_ugm3"]).round(1)
    m.loc[hit, "api_max"] = pm25_to_api(m.loc[hit, "pm25_ugm3"] * 1.2).round(1)
    frac_unh = np.clip((m.loc[hit, "api_mean"] - 70) / 60, 0, 1)
    m.loc[hit, "stations_unhealthy"] = np.round(m.loc[hit, "stations_total"] * frac_unh)
    m.loc[hit, "stations_over_200"] = np.round(m.loc[hit, "stations_total"] * (m.loc[hit, "api_max"] > 200) * .5)
    return derive_haze_columns(m.drop(columns="pm25_real"), np.random.default_rng(SEED + 7)), int(hit.sum())


REQUIRED_HAZE_COLS = {"date", "state", "pm25_ugm3", "api_mean", "api_max", "stations_total", "stations_unhealthy",
                      "stations_over_200", "flights_scheduled", "affected_share", "school_closed_share",
                      "flights_disrupted", "cloud_seeding_ops"}
REQUIRED_REF_COLS = {"state", "population", "gdp_rm_bn", "gdp_per_day_rm", "services_gdp_day_rm", "employed",
                     "students", "agri_share", "construction_share", "services_share", "dev_tourism_loss",
                     "dev_agriculture_loss", "pct_65plus", "lat", "lon"}


def load_inputs(save_mock: bool = False, use_real: bool = True, fetch: bool = True):
    """Return (state_reference, haze_daily, mock_truth_or_None, source_label, baseline_sources)."""
    ref_live, sources = state_economy(fetch)
    files = {k: MOCK_DIR / f"{k}.csv" for k in ("state_reference", "haze_daily", "damage_costs_daily")}
    haze = truth = None
    if files["haze_daily"].exists():
        haze = pd.read_csv(files["haze_daily"])
        stale = REQUIRED_HAZE_COLS - set(haze.columns)
        if stale:
            print(f"[warn] {MOCK_DIR.relative_to(ROOT)} is from an older version (missing {sorted(stale)}); "
                  "using freshly generated mock data. Re-save with --save-mock to update the CSVs.", file=sys.stderr)
            haze = None
        else:
            truth = pd.read_csv(files["damage_costs_daily"]) if files["damage_costs_daily"].exists() else None
            src = f"mock CSVs in {MOCK_DIR.relative_to(ROOT)}"
    if haze is None:
        mock = build_mock(fetch=False)
        haze, truth, src = mock["haze_daily"], mock["damage_costs_daily"], "synthetic"
        if save_mock:
            MOCK_DIR.mkdir(parents=True, exist_ok=True)
            mock["state_reference"] = ref_live
            for k, df in mock.items():
                df.to_csv(MOCK_DIR / f"{k}.csv", index=False)
    if use_real:
        real, path = load_real_pm25()
        if real is not None:
            haze, n = apply_real_pm25(haze, real)
            src += f" + real PM2.5 for {n} state-days ({path})"
            truth = None
    return ref_live, haze, truth, src, sources


def load_forecast(haze: pd.DataFrame) -> tuple[dict[str, dict[str, float]], str]:
    """PM2.5 per state for horizons 24/48/72h. Tolerates a few likely forecast.json shapes."""
    for p in FORECAST_PATHS:
        if not p.exists():
            continue
        try:
            raw = json.loads(p.read_text())
            items = raw.get("states", raw) if isinstance(raw, dict) else raw
            out = {}
            for it in items:
                pm = it.get("pm25") or it.get("pm25_forecast") or {}
                out[it["state"]] = {h: float(pm[h]) for h in HORIZONS if h in pm}
            if out:
                return out, str(p.relative_to(ROOT))
        except Exception as e:
            print(f"[warn] could not parse {p}: {e}", file=sys.stderr)
    last3 = haze.sort_values("date").groupby("state").tail(3).groupby("state").pm25_ugm3.mean()
    return {s: {h: float(v) for h in HORIZONS} for s, v in last3.items()}, \
        "persistence (mean of last 3 days) — forecast.json not found"


# ---------------------------------------------------------------------------
# 5. Damage function
# ---------------------------------------------------------------------------
def central() -> dict[str, float]:
    return {k: v["value"] for k, v in COEFFS.items()}


def rate_table(level: str = "central") -> dict[str, dict[str, float]]:
    """Loss rate per sector per band at one level: 'low', 'central' or 'high'."""
    i = {"low": 0, "central": 1, "high": 2}[level]
    return {s: {"good": 0.0, **{b: r[b][i] for b in BANDS[1:]}} for s, r in LOSS_RATES.items()}


def damage_by_day(haze: pd.DataFrame, ref: pd.DataFrame, c: dict[str, float],
                  rates: dict | None = None, rng: np.random.Generator | None = None,
                  coverage: float = 1.0) -> pd.DataFrame:
    """RM damage per state per day: sector columns (summed) + memo columns (not summed)."""
    rates = rates or rate_table("central")
    r = add_sector_values(ref, c)
    h = haze.merge(r, on="state", how="left", validate="many_to_one", suffixes=("", "_ref"))
    band = api_band(h.api_max)
    share = np.clip(h.affected_share.to_numpy() * coverage, 0, 1) if "affected_share" in h else np.ones(len(h))

    out = pd.DataFrame({"date": h.date.to_numpy(), "state": h.state.to_numpy()})
    for sector in LOSS_RATES:
        rate = pd.Series(band).map(rates[sector]).to_numpy(dtype=float)
        out[sector] = damage_cost(h[f"dev_{sector}"].to_numpy(), rate, 1, share)

    # healthcare: extra visits/admissions from a dose-response on PM2.5 x unit costs
    pop100k = h.population.to_numpy() / 1e5
    x = np.clip(h.pm25_ugm3.to_numpy() - c["pm25_threshold"], 0, None) / 10
    age_adj = 1 + .3 * (h.pct_65plus.to_numpy() / .08 - 1)

    def cases(base, crf):
        lam = np.clip(base * pop100k * (np.exp(crf * x) - 1) * age_adj, 0, None)
        return rng.poisson(lam) if rng is not None else lam
    out["health"] = (cases(c["base_resp_adm"], c["crf_resp_admission"]) * c["cost_resp_admission"]
                     + cases(c["base_cardio_adm"], c["crf_cardio_admission"]) * c["cost_cardio_admission"]
                     + cases(c["base_outpatient"], c["crf_outpatient"]) * c["cost_outpatient"])
    # memo items (reported, not summed)
    closed = (h.school_closed_share if "school_closed_share" in h else h.get("school_closed", 0)).to_numpy()
    out["school_closure"] = closed * h.students.to_numpy() * c["school_cost_per_student_day"]
    out["response"] = h.cloud_seeding_ops.to_numpy() * c["cloud_seeding_cost"]
    out["mortality"] = 0.0
    if rng is not None:
        for col in LOSS_RATES:
            out[col] *= rng.lognormal(0, .15, len(out))
    out["total_damage_rm"] = out[CATEGORIES].sum(axis=1)
    return out


def forecast_day_inputs(haze: pd.DataFrame, pm25: dict[str, dict[str, float]], horizon: str,
                        pm25_factor: float = 1.0) -> pd.DataFrame:
    """One 'next day' row per state at the forecast PM2.5; other drivers from the last observed day."""
    last = haze.sort_values("date").groupby("state").tail(1).set_index("state").copy()
    worst_ratio = (last.api_max / last.api_mean.clip(lower=1)).clip(1.0, 2.0)
    for s in last.index:
        if s in pm25 and horizon in pm25[s]:
            last.loc[s, "pm25_ugm3"] = pm25[s][horizon]
    last["pm25_ugm3"] = last.pm25_ugm3 * pm25_factor
    last["api_mean"] = pm25_to_api(last.pm25_ugm3)
    last["api_max"] = np.clip(last.api_mean * worst_ratio, 0, 500)
    last["affected_share"] = np.where(last.api_max > 100, np.clip((last.api_mean - 70) / 60, .25, 1), 1.0)
    last["school_closed_share"] = np.where(last.api_max > 200, .5, np.where(last.api_max > 150, .25, 0))
    last["flights_disrupted"] = last.flights_scheduled * np.clip((last.api_max - 150) / 1000, 0, .15)
    last["cloud_seeding_ops"] = np.clip((last.api_max - 180) / 300, 0, .6)
    last["date"] = f"+{horizon}h"
    return last.reset_index()


def scenarios(haze, ref, pm25_fc) -> dict:
    """Best / expected / worst damage over the 72-hour forecast window, by sector and state."""
    out = {}
    for name, sc in SCENARIOS.items():
        rates = rate_table(sc["rates"])
        coef = {k: v[{"low": "low", "central": "value", "high": "high"}[sc["rates"]]] for k, v in COEFFS.items()}
        days = []
        for h in HORIZONS[: sc["days"]]:
            d = damage_by_day(forecast_day_inputs(haze, pm25_fc, h, sc["pm25_factor"]), ref, coef, rates,
                              coverage=sc["coverage"])
            days.append(d.assign(horizon=h))
        d = pd.concat(days)
        last_day = days[-1]
        out[name] = {
            "label": sc["label"],
            "total_72h_rm": round(float(d.total_damage_rm.sum()), 0),
            "by_sector_rm": {k: round(float(d[k].sum()), 0) for k in CATEGORIES},
            "by_state_rm": {s: round(float(v), 0) for s, v in d.groupby("state").total_damage_rm.sum().items()},
            "by_horizon_rm": {h: round(float(g.total_damage_rm.sum()), 0) for h, g in d.groupby("horizon")},
            "if_prolonged_rm": round(float(d.total_damage_rm.sum() + sc["extra_days"] * last_day.total_damage_rm.sum()), 0),
            "affected_days": sc["days"] + sc["extra_days"],
        }
    return out


# ---------------------------------------------------------------------------
# 6. Uncertainty, validation, sensitivity
# ---------------------------------------------------------------------------
def _lognormal_between(central_v, low, high, z):
    if central_v <= 0:
        return 0.0
    hi_ratio, lo_ratio = max(high, 1e-12) / central_v, max(low, 1e-12) / central_v
    return float(central_v * (hi_ratio ** (z / 1.645) if z >= 0 else lo_ratio ** (-z / 1.645)))


def draw(rng: np.random.Generator) -> tuple[dict, dict]:
    """One Monte Carlo draw of coefficients and loss rates."""
    c = {k: (v["value"] if v["low"] == v["high"] else _lognormal_between(v["value"], v["low"], v["high"], rng.normal()))
         for k, v in COEFFS.items()}
    rates = {}
    for s, r in LOSS_RATES.items():
        z = rng.normal()   # one draw per sector, shared across bands
        rates[s] = {"good": 0.0, **{b: _lognormal_between(r[b][1], r[b][0], r[b][2], z) for b in BANDS[1:]}}
    return c, rates


def monte_carlo(haze, ref, n_sims: int, seed: int = SEED) -> pd.DataFrame:
    """n_sims x state table of total damage (RM)."""
    rng = np.random.default_rng(seed + 1)
    rows = []
    for _ in range(n_sims):
        c, rates = draw(rng)
        rows.append(damage_by_day(haze, ref, c, rates).groupby("state").total_damage_rm.sum())
    return pd.DataFrame(rows)


def backtest() -> dict:
    """On the pure mock: MAPE of state totals, model vs mock truth (circular — see notebook).
    Baseline = OLS of daily total on PM2.5 (pooled)."""
    mock = build_mock()
    haze, ref, truth = mock["haze_daily"], mock["state_reference"], mock["damage_costs_daily"]
    t = truth.groupby("state").total_damage_rm.sum()
    m = damage_by_day(haze, ref, central()).groupby("state").total_damage_rm.sum().reindex(t.index)
    j = truth.merge(haze[["date", "state", "pm25_ugm3"]], on=["date", "state"])
    beta = np.polyfit(j.pm25_ugm3, j.total_damage_rm, 1)
    b = j.assign(pred=np.clip(np.polyval(beta, j.pm25_ugm3), 0, None)).groupby("state").pred.sum().reindex(t.index)
    mape = lambda p: float(np.mean(np.abs(p - t) / t.clip(lower=1)))
    return {"backtest_mape": round(mape(m), 4), "baseline_mape": round(mape(b), 4)}


def sensitivity(haze, ref, top: int = 5) -> pd.DataFrame:
    """Tornado: national total with one input at its low vs high end, everything else central."""
    base_c, base_r = central(), rate_table("central")
    rows = []
    for s in LOSS_RATES:
        tot = {}
        for end in ("low", "high"):
            r = dict(base_r, **{s: rate_table(end)[s]})
            tot[end] = damage_by_day(haze, ref, base_c, r).total_damage_rm.sum()
        rows.append(dict(coefficient=f"loss rate: {SECTORS[s]}", low_rm=tot["low"], high_rm=tot["high"]))
    for k, v in COEFFS.items():
        if v["low"] == v["high"]:
            continue
        tot = {end: damage_by_day(haze, ref, dict(base_c, **{k: v[end]})).total_damage_rm.sum() for end in ("low", "high")}
        rows.append(dict(coefficient=k, low_rm=tot["low"], high_rm=tot["high"]))
    df = pd.DataFrame(rows)
    df["swing_rm"] = (df.high_rm - df.low_rm).abs()
    return df.sort_values("swing_rm", ascending=False).head(top).reset_index(drop=True)


# ---------------------------------------------------------------------------
# 7. Export
# ---------------------------------------------------------------------------
def run(n_sims: int = 1000, write: bool = True, save_mock: bool = False, use_real: bool = True,
        fetch: bool = True) -> dict:
    ref, haze, _truth, data_src, base_src = load_inputs(save_mock, use_real, fetch)
    missing = set(STATE_NAMES) - set(ref.state)
    assert not missing, f"missing states: {missing}"

    daily = damage_by_day(haze, ref, central())
    by_state = daily.groupby("state")[CATEGORIES + MEMO + ["total_damage_rm"]].sum()
    national = float(by_state.total_damage_rm.sum())
    if n_sims > 0:
        nat_draws = monte_carlo(haze, ref, n_sims).sum(axis=1)
        ci = [float(min(np.percentile(nat_draws, 5), national)), float(max(np.percentile(nat_draws, 95), national))]
    else:
        ci = [national, national]

    pm25_fc, fc_src = load_forecast(haze)
    scen = scenarios(haze, ref, pm25_fc)
    fc = {h: damage_by_day(forecast_day_inputs(haze, pm25_fc, h), ref, central()).set_index("state").total_damage_rm
          for h in HORIZONS}
    latest = haze.sort_values("date").groupby("state").tail(1).set_index("state")
    r_i = add_sector_values(ref, central()).set_index("state")
    days = int(haze.date.nunique())

    result = {
        "episode": {"start": str(haze.date.min()), "end": str(haze.date.max()), "days": days},
        "method": "Damage = Daily Economic Value x Loss Rate(API band) x Share of activity affected x Days, per sector",
        "national": {
            "total_damage_rm": round(national, 0),
            "by_category_rm": {k: round(float(by_state[k].sum()), 0) for k in CATEGORIES},
            "memo_not_in_total_rm": {k: round(float(by_state[k].sum()), 0) for k in MEMO},
            "ci90_rm": [round(ci[0], 0), round(ci[1], 0)],
            "damage_per_day_rm": round(national / days, 0),
            "daily_economic_value_rm": {k: round(float(r_i[f"dev_{k}"].sum()), 0) for k in LOSS_RATES},
        },
        "forecast_72h": {"source": fc_src, "scenarios": scen},
        "states": [{
            "state": s,
            "lat": float(r_i.loc[s, "lat"]), "lon": float(r_i.loc[s, "lon"]),
            "population": int(r_i.loc[s, "population"]),
            "damage_to_date_rm": round(float(row.total_damage_rm), 0),
            "by_category_rm": {k: round(float(row[k]), 0) for k in CATEGORIES},
            "memo_not_in_total_rm": {k: round(float(row[k]), 0) for k in MEMO},
            "damage_pct_gdp": round(float(row.total_damage_rm / (r_i.loc[s, "gdp_rm_bn"] * 1e9) * 100), 4),
            "forecast_damage_rm": {h: round(float(fc[h].get(s, 0.0)), 0) for h in HORIZONS},
            "forecast_72h_scenarios_rm": {n: scen[n]["by_state_rm"].get(s, 0.0) for n in SCENARIOS},
            "api_latest": float(latest.loc[s, "api_max"]),
        } for s, row in by_state.sort_values("total_damage_rm", ascending=False).iterrows()],
        "model": {"type": "sector_damage_function", "n_sims": n_sims, **backtest(), "baseline": "pm25_only_linear",
                  "mortality_included": INCLUDE_MORTALITY,
                  "benchmark_rm_per_day": [BENCHMARKS["2026_daily_low_rm"], BENCHMARKS["2026_daily_high_rm"]],
                  "sensitivity_top5": sensitivity(haze, ref).round(0).to_dict(orient="records")},
        "loss_rates": {s: {"label": SECTORS[s], **{b: list(r[b]) for b in BANDS[1:]}, "source": r["source"]}
                       for s, r in LOSS_RATES.items()},
        "assumptions": [{"name": k, "value": v["value"], "unit": v["unit"], "source": v["source"],
                         "range": [v["low"], v["high"]]} for k, v in COEFFS.items()] + [
            {"name": f"loss_rate_{s}", "value": {b: r[b][1] for b in BANDS[1:]}, "unit": "share of daily activity",
             "source": r["source"], "range": {b: [r[b][0], r[b][2]] for b in BANDS[1:]}}
            for s, r in LOSS_RATES.items()] + [
            {"name": f"baseline_{k}", "value": "", "unit": "", "source": v} for k, v in base_src.items()] + [
            {"name": "haze_history", "value": data_src, "unit": "",
             "source": DUMMY if "real" not in data_src else "Open-Meteo (real) + DUMMY for uncovered days"},
            {"name": "forecast_input", "value": fc_src, "unit": "",
             "source": "Forecast team" if "not found" not in fc_src else "persistence"},
            {"name": "pm25_to_api", "value": "piecewise: PM2.5 55→API 100, 100→160, 150→200", "unit": "",
             "source": "approximation calibrated to 2026 APIMS news readings — replace with DOE breakpoints"},
            {"name": "affected_share", "value": "share of state's stations above API 100 (min 25%)", "unit": "",
             "source": "method choice"},
            {"name": "double_counting", "value": "school closures, cloud seeding and mortality reported as memo items",
             "unit": "", "source": "method choice (brief step 6)"},
            {"name": "benchmark_2026", "value": "RM 20–50 m per day", "unit": "RM/day",
             "source": "The Star, 14 Oct 2026 — Dr Yeah Kim Leng (Sunway University)"},
            {"name": "benchmark_2013", "value": "~RM 1.7 bn total; RM 410.6 m illness", "unit": "RM",
             "source": "MJPHM, 'The Malaysia Haze and Its Health Economic Impact: A Literature Review'"},
        ],
        "_note": "Contains DUMMY/APPROX inputs — see assumptions. Mortality excluded (haze treated as non-fatal).",
    }

    for st in result["states"]:
        assert abs(sum(st["by_category_rm"].values()) - st["damage_to_date_rm"]) <= len(CATEGORIES), st["state"]
        assert all(v >= 0 for v in st["by_category_rm"].values()), st["state"]
    assert result["national"]["ci90_rm"][0] <= result["national"]["total_damage_rm"] <= result["national"]["ci90_rm"][1]
    s = result["forecast_72h"]["scenarios"]
    assert s["best"]["total_72h_rm"] <= s["expected"]["total_72h_rm"] <= s["worst"]["total_72h_rm"]

    if write:
        OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
        OUT_PATH.write_text(json.dumps(result, indent=2, ensure_ascii=False))
    return result


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--sims", type=int, default=1000, help="Monte Carlo draws (0 to skip)")
    ap.add_argument("--dry-run", action="store_true", help="don't write data/out/damage.json")
    ap.add_argument("--save-mock", action="store_true", help="write the mock CSVs to data/mock/damage/")
    ap.add_argument("--no-real", action="store_true", help="ignore the real PM2.5 file even if present")
    ap.add_argument("--offline", action="store_true", help="don't try to download OpenDOSM data")
    a = ap.parse_args(argv)
    r = run(a.sims, write=not a.dry_run, save_mock=a.save_mock, use_real=not a.no_real, fetch=not a.offline)
    n = r["national"]
    print(f"Episode {r['episode']['start']} → {r['episode']['end']} ({r['episode']['days']} days)")
    print(f"Damage to date: RM {n['total_damage_rm']/1e9:.2f} bn  (90% range RM {n['ci90_rm'][0]/1e9:.2f}–"
          f"{n['ci90_rm'][1]/1e9:.2f} bn)  ≈ RM {n['damage_per_day_rm']/1e6:.0f} m/day  [benchmark RM 20–50 m/day]")
    for k, v in sorted(n["by_category_rm"].items(), key=lambda kv: -kv[1]):
        print(f"  {SECTORS[k]:<34} RM {v/1e6:>8,.1f} m")
    print("  Not in total: " + ", ".join(f"{k} RM {v/1e6:,.1f} m" for k, v in n["memo_not_in_total_rm"].items()))
    print("Next 72 hours:")
    for name, sc in r["forecast_72h"]["scenarios"].items():
        print(f"  {name:<9} RM {sc['total_72h_rm']/1e6:>7,.1f} m   ({sc['label']})"
              + (f"; RM {sc['if_prolonged_rm']/1e6:,.1f} m if it lasts {sc['affected_days']} days" if sc["affected_days"] > 3 else ""))
    print("Top 5 states to date:", ", ".join(f"{s['state']} (RM {s['damage_to_date_rm']/1e6:,.0f} m)" for s in r["states"][:5]))
    if not a.dry_run:
        print(f"Wrote {OUT_PATH.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
