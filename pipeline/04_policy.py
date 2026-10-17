"""Attribute haze sources and estimate associated costs.

HazeGuard-MY — Damage Costs (economic impact layer)
===================================================

Estimates the RM damage cost of the 2026 haze episode for every Malaysian
state, to date and for the next 24/48/72h, broken down by category, with a
Monte Carlo uncertainty range. Writes ``data/out/damage.json``.

Method: a transparent bottom-up damage function (one formula per category,
every coefficient in ``COEFFS`` below), not a black box.

    health            extra hospital admissions + clinic visits (dose-response on PM2.5) x unit cost
    mortality         OFF by default (haze deaths are rare and hard to attribute) — kept as a field, = 0
    productivity_loss sick days / absence by API band + outdoor-worker output drop
    school_closure    share of a state's schools closed x students x cost per student-day
    tourism_loss      days API > 100 x tourism GDP/day x drop share
    agriculture_loss  days API > 100 x agri GDP/day x yield drop
    transport_loss    disrupted flights x cost per flight
    response          cloud-seeding operations x cost per operation

Calibration targets (sanity checks, not fitted):
    * 2026 haze: RM 20–50 m per day (Yeah Kim Leng, The Star, 14 Oct 2026)
    * 2013 haze: ~RM 1.7 bn total; illness ~RM 410 m (MJPHM literature review)
    * real state PM2.5 Aug–Sep 2026 (Open-Meteo, Resource Allocation branch): Sarawak
      peaks ~240 µg/m3, Klang Valley ~120, most of the Peninsula 50–90.

Data
----
* Haze history + state reference: ``data/mock/damage/*.csv`` if present,
  otherwise a SYNTHETIC dataset is generated in memory (``build_mock``).
* Real PM2.5: if ``pm25_daily_openmeteo.csv`` is found (see REAL_PM25_PATHS) it
  replaces the synthetic PM2.5 for the dates it covers.
* Forecast: ``data/out/forecast.json`` (Forecast team) if present, else
  ``data/mock/forecast.json``, else the last 3 days are persisted.

Run (from the repo root)::

    python pipeline/04_policy.py                 # writes data/out/damage.json
    python pipeline/04_policy.py --sims 200 --dry-run
    python pipeline/04_policy.py --save-mock     # also writes the mock CSVs

Tests and graphs: ``tests/04_damage_tests.ipynb``. Requires numpy, pandas.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
MOCK_DIR = ROOT / "data" / "mock" / "damage"
OUT_PATH = ROOT / "data" / "out" / "damage.json"
FORECAST_PATHS = [ROOT / "data" / "out" / "forecast.json", ROOT / "data" / "mock" / "forecast.json"]
STATES_JSON = ROOT / "data" / "out" / "states.json"
REAL_PM25_PATHS = [ROOT / "data" / "raw" / "pm25_daily_openmeteo.csv",
                   ROOT / "data" / "resource_allocation" / "raw" / "pm25_daily_openmeteo.csv"]

SEED = 42
DUMMY = "DUMMY — replace"
CATEGORIES = ["health", "mortality", "productivity_loss", "school_closure",
              "tourism_loss", "agriculture_loss", "transport_loss", "response"]
INCLUDE_MORTALITY = False   # team decision: haze is treated as non-fatal; set True for a sensitivity run

# Published reference points used to sanity-check the totals (RM)
BENCHMARKS = {
    "2026_daily_low_rm": 20e6, "2026_daily_high_rm": 50e6,      # The Star, 14 Oct 2026 (Yeah Kim Leng)
    "2013_total_rm": 1.7e9, "2013_illness_rm": 410.6e6,         # MJPHM literature review
    "1997_total_rm": 0.8e9,                                     # The Star, 14 Oct 2026 (Yeah Kim Leng)
}

# ---------------------------------------------------------------------------
# 1. Coefficients. value = central estimate; (low, high) = Monte Carlo range
#    (log-normal fitted so low/high are ~ the 5th/95th percentiles).
#    Replace values + sources as real figures are found. Never drop the source.
# ---------------------------------------------------------------------------
COEFFS: dict[str, dict] = {
    # dose-response (log relative risk per +10 µg/m3 PM2.5 above threshold)
    "crf_resp_admission":   dict(value=0.015, low=0.008, high=0.025, unit="log-RR per 10 µg/m3",
                                 source=f"{DUMMY} (in line with short-term PM2.5 meta-analyses, ~1–2%)"),
    "crf_cardio_admission": dict(value=0.008, low=0.004, high=0.013, unit="log-RR per 10 µg/m3",
                                 source=f"{DUMMY} (in line with short-term PM2.5 meta-analyses, ~0.5–1%)"),
    "crf_outpatient":       dict(value=0.025, low=0.012, high=0.040, unit="log-RR per 10 µg/m3",
                                 source=f"{DUMMY} (clinic visits respond more than admissions)"),
    "crf_mortality":        dict(value=0.0, low=0.0, high=0.0, unit="log-RR per 10 µg/m3",
                                 source="Set to 0 — team decision: haze treated as non-fatal (INCLUDE_MORTALITY)"),
    # baselines
    "base_resp_adm":   dict(value=2.1,  low=1.6, high=2.7,  unit="admissions /100k /day", source=DUMMY),
    "base_cardio_adm": dict(value=1.6,  low=1.2, high=2.1,  unit="admissions /100k /day", source=DUMMY),
    "base_outpatient": dict(value=38.0, low=28., high=50.,  unit="resp. clinic visits /100k /day", source=DUMMY),
    "base_deaths":     dict(value=1.55, low=1.55, high=1.55, unit="deaths /100k /day",
                            source=f"{DUMMY} (unused while mortality is off)"),
    "pm25_threshold":  dict(value=15.0, low=15.0, high=15.0, unit="µg/m3", source="WHO 2021 24-hour AQG"),
    # unit costs
    "cost_resp_admission":   dict(value=4200, low=2800, high=6000, unit="RM / admission", source=DUMMY),
    "cost_cardio_admission": dict(value=9800, low=6500, high=14000, unit="RM / admission", source=DUMMY),
    "cost_outpatient":       dict(value=120, low=80, high=180, unit="RM / visit", source=DUMMY),
    "vsl":                   dict(value=2.4e6, low=2.4e6, high=2.4e6, unit="RM / statistical life",
                                  source=f"{DUMMY} (unused while mortality is off)"),
    # economy
    "absence_unhealthy":      dict(value=0.002, low=0.001, high=0.004, unit="share absent/day, API 101–200",
                                   source=f"{DUMMY} (2013: ~RM 203 m medical leave nationally)"),
    "absence_very_unhealthy": dict(value=0.010, low=0.005, high=0.020, unit="share absent/day, API > 200", source=DUMMY),
    "outdoor_output_drop":    dict(value=0.05, low=0.02, high=0.10, unit="outdoor output lost on API > 100 days", source=DUMMY),
    "school_cost_per_student_day": dict(value=15, low=8, high=25, unit="RM / student / closed day",
                                        source=f"{DUMMY} (childcare + learning loss)"),
    "tourism_drop":   dict(value=0.08, low=0.03, high=0.12, unit="share of tourism GDP/day, API > 100",
                           source=f"{DUMMY} (arrivals down up to 8% — The Star, 14 Oct 2026)"),
    "agri_yield_drop": dict(value=0.02, low=0.01, high=0.04, unit="share of agri GDP/day, API > 100", source=DUMMY),
    "flight_cost":    dict(value=85_000, low=50_000, high=130_000, unit="RM / disrupted flight", source=DUMMY),
    "cloud_seeding_cost": dict(value=150_000, low=100_000, high=250_000, unit="RM / operation", source=DUMMY),
}

STATE_NAMES = ["Johor", "Kedah", "Kelantan", "Melaka", "Negeri Sembilan", "Pahang", "Perak", "Perlis",
               "Pulau Pinang", "Sabah", "Sarawak", "Selangor", "Terengganu", "W.P. Kuala Lumpur",
               "W.P. Labuan", "W.P. Putrajaya"]

# PM2.5 (µg/m3, state average) -> API. Approximate: calibrated so state-average PM2.5 ~50 gives API ~95
# and ~120 gives API ~175, matching Aug–Oct 2026 APIMS readings reported in the news against the
# Open-Meteo state averages. Replace with DOE's official PM2.5 sub-index breakpoints if you find them.
_PM_BP = [0, 15, 35, 55, 100, 150, 250, 500]
_API_BP = [0, 30, 70, 100, 160, 200, 300, 500]


def pm25_to_api(pm25):
    """Convert PM2.5 concentration to an API value (piecewise linear)."""
    return np.interp(np.asarray(pm25, dtype=float), _PM_BP, _API_BP)


def api_to_pm25(api):
    """Inverse of pm25_to_api (for drawing API thresholds on PM2.5 axes)."""
    return np.interp(np.asarray(api, dtype=float), _API_BP, _PM_BP)


def api_band(api) -> np.ndarray:
    return pd.cut(np.asarray(api, dtype=float), [-1, 50, 100, 200, 300, 1e9],
                  labels=["good", "moderate", "unhealthy", "very_unhealthy", "hazardous"]).astype(str)


# ---------------------------------------------------------------------------
# 2. Data
# ---------------------------------------------------------------------------
# Haze shape per state: background PM2.5 + peak extra PM2.5 in each wave (state average, µg/m3).
#   wave A ~31 Aug (Borneo-led), wave C ~22 Sep (Sarawak), wave B ~15 Oct (west-coast Peninsula).
#   Calibrated by eye to the real Open-Meteo Aug–Sep 2026 maxima and the October news reports.
_HAZE_SHAPE = {  # state: (background, wave_A, wave_C, wave_B)
    "Sarawak": (25, 165, 95, 25),        "Selangor": (22, 70, 30, 75),
    "W.P. Kuala Lumpur": (22, 68, 30, 72), "W.P. Putrajaya": (20, 62, 26, 70),
    "Negeri Sembilan": (18, 48, 20, 62), "Melaka": (16, 40, 16, 58),
    "Johor": (17, 40, 16, 55),           "Perak": (15, 52, 20, 42),
    "Pulau Pinang": (15, 36, 12, 34),    "Kedah": (13, 32, 12, 30),
    "Perlis": (11, 28, 8, 22),           "Pahang": (12, 32, 12, 24),
    "Terengganu": (11, 32, 12, 16),      "Kelantan": (10, 28, 8, 12),
    "Sabah": (14, 38, 16, 12),           "W.P. Labuan": (14, 50, 18, 12),
}


def build_mock(seed: int = SEED) -> dict[str, pd.DataFrame]:
    """SYNTHETIC state reference + daily haze (15 Aug – 16 Oct 2026) + mock 'ground-truth' costs.

    Shapes follow the reported episode; every number is generated.
    """
    rng = np.random.default_rng(seed)
    cols = ["state", "region", "lat", "lon", "pop_k", "area_km2", "gdp_rm_bn",
            "pct_65plus", "pct_under5", "agri_share", "tourism_share", "airport_flights_day"]
    ref = pd.DataFrame([
        ("Johor", "Peninsular", 1.49, 103.74, 4180, 19166, 171.0, .080, .070, .12, .05, 90),
        ("Kedah", "Peninsular", 6.12, 100.37, 2200, 9492, 56.3, .085, .075, .18, .04, 40),
        ("Kelantan", "Peninsular", 6.13, 102.24, 1920, 15040, 28.0, .075, .095, .20, .03, 30),
        ("Melaka", "Peninsular", 2.19, 102.25, 1050, 1664, 47.0, .095, .065, .06, .09, 4),
        ("Negeri Sembilan", "Peninsular", 2.73, 101.94, 1230, 6686, 55.0, .090, .068, .10, .04, 0),
        ("Pahang", "Peninsular", 3.81, 103.33, 1690, 36137, 61.0, .080, .075, .25, .05, 12),
        ("Perak", "Peninsular", 4.60, 101.09, 2550, 21035, 89.0, .110, .065, .15, .04, 6),
        ("Perlis", "Peninsular", 6.44, 100.20, 300, 821, 6.7, .095, .075, .20, .02, 0),
        ("Pulau Pinang", "Peninsular", 5.41, 100.33, 1800, 1049, 122.0, .105, .060, .02, .07, 110),
        ("Sabah", "Borneo", 5.98, 116.07, 3550, 73904, 89.0, .050, .080, .22, .06, 180),
        ("Sarawak", "Borneo", 1.55, 110.36, 2550, 124450, 155.0, .075, .072, .13, .04, 160),
        ("Selangor", "Peninsular", 3.07, 101.52, 7400, 8104, 400.0, .065, .065, .02, .04, 700),
        ("Terengganu", "Peninsular", 5.33, 103.14, 1290, 13035, 41.0, .070, .090, .10, .04, 20),
        ("W.P. Kuala Lumpur", "Peninsular", 3.14, 101.69, 2020, 243, 285.0, .080, .055, .00, .08, 60),
        ("W.P. Labuan", "Borneo", 5.28, 115.23, 100, 91, 8.5, .055, .070, .03, .06, 20),
        ("W.P. Putrajaya", "Peninsular", 2.93, 101.70, 120, 49, 14.4, .030, .085, .00, .02, 0),
    ], columns=cols)
    n = len(ref)
    ref["population"] = (ref.pop("pop_k") * 1000).astype(int)
    ref["density_per_km2"] = (ref.population / ref.area_km2).round(1)
    ref["gdp_per_day_rm"] = (ref.gdp_rm_bn * 1e9 / 365).round(0)
    ref["labour_force"] = (ref.population * rng.uniform(.48, .53, n)).astype(int)
    ref["employed"] = (ref.labour_force * rng.uniform(.955, .975, n)).astype(int)
    ref["outdoor_worker_share"] = (.12 + ref.agri_share * .8 + rng.normal(0, .015, n)).clip(.06, .35).round(3)
    ref["students"] = (ref.population * rng.uniform(.14, .16, n)).astype(int)
    ref["beds_per_1000"] = rng.uniform(1.2, 2.6, n).round(2)

    dates = pd.date_range("2026-08-15", "2026-10-16", freq="D")
    t = np.arange(len(dates))
    wave_a = np.exp(-.5 * ((t - 16) / 5.0) ** 2)   # ~31 Aug
    wave_c = np.exp(-.5 * ((t - 38) / 3.5) ** 2)   # ~22 Sep
    wave_b = np.exp(-.5 * ((t - 61) / 4.0) ** 2)   # ~15 Oct
    rows = []
    for s in ref.itertuples():
        bg, a, c, b = _HAZE_SHAPE[s.state]
        signal = bg + a * wave_a + c * wave_c + b * wave_b
        # smooth multiplicative day-to-day weather noise (wind shifts, rain washout)
        wiggle = np.exp(np.convolve(rng.normal(0, .22, len(t) + 2), np.ones(3) / 3, "valid"))
        pm25 = np.clip(signal * wiggle, 4, 400)
        n_st = max(1, int(round(np.sqrt(s.population) / 450)) + 1)
        # worst station in the state is dirtier than the state average
        pm25_worst = pm25 * rng.uniform(1.05, 1.35, len(t))
        api_mean, api_max = pm25_to_api(pm25), pm25_to_api(pm25_worst)
        for i, d in enumerate(dates):
            st_api = pm25_to_api(pm25[i] * rng.uniform(.75, 1.35, n_st))
            rows.append(dict(date=d.date().isoformat(), state=s.state, pm25_ugm3=round(pm25[i], 1),
                             api_mean=round(float(api_mean[i]), 1), api_max=round(float(api_max[i]), 1),
                             stations_total=n_st, stations_unhealthy=int((st_api > 100).sum()),
                             stations_over_200=int((st_api > 200).sum()),
                             flights_scheduled=int(s.airport_flights_day)))
    haze = pd.DataFrame(rows).sort_values(["state", "date"], ignore_index=True)
    haze = derive_haze_columns(haze, rng)

    # mock "ground truth" = damage function at central values + Poisson / reporting noise
    truth = damage_by_day(haze, ref, central(), rng=rng)
    return {"state_reference": ref, "haze_daily": haze, "damage_costs_daily": truth}


def derive_haze_columns(haze: pd.DataFrame, rng: np.random.Generator | None = None) -> pd.DataFrame:
    """Columns that follow from PM2.5/API: band, school closures, flights, cloud seeding."""
    haze = haze.sort_values(["state", "date"], ignore_index=True).copy()
    haze["api_band"] = api_band(haze.api_max)
    # Schools close district by district: share of stations > 200, or half the state after 2 days > 150.
    over150_2d = haze.groupby("state").api_max.transform(lambda x: (x > 150) & (x.shift(1) > 150))
    share = haze.stations_over_200 / haze.stations_total.clip(lower=1)
    haze["school_closed_share"] = np.maximum(share, np.where(over150_2d, .5, 0)).round(2)
    haze["school_closed"] = (haze.school_closed_share > 0).astype(int)
    # flights: visibility problems only from API ~150, capped at 15% of the day's flights
    frac = np.clip((haze.api_max - 150) / 1000, 0, .15)
    lam = haze.flights_scheduled * frac
    haze["flights_disrupted"] = rng.poisson(lam) if rng is not None else np.round(lam).astype(int)
    p = np.clip((haze.api_max - 180) / 300, 0, .6)
    haze["cloud_seeding_ops"] = rng.binomial(1, p) if rng is not None else (p > .3).astype(int)
    return haze


def load_real_pm25() -> tuple[pd.DataFrame | None, str | None]:
    for p in REAL_PM25_PATHS:
        if p.exists():
            df = pd.read_csv(p)[["state", "date", "pm25"]]
            return df, str(p.relative_to(ROOT))
    return None, None


def apply_real_pm25(haze: pd.DataFrame, real: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """Overwrite synthetic PM2.5 with real values where dates/states match; re-derive API etc."""
    m = haze.merge(real.rename(columns={"pm25": "pm25_real"}), on=["state", "date"], how="left")
    hit = m.pm25_real.notna()
    ratio = (m.api_max / m.api_mean.clip(lower=1)).fillna(1.3)
    m.loc[hit, "pm25_ugm3"] = m.loc[hit, "pm25_real"]
    m.loc[hit, "api_mean"] = pm25_to_api(m.loc[hit, "pm25_ugm3"]).round(1)
    m.loc[hit, "api_max"] = (pm25_to_api(m.loc[hit, "pm25_ugm3"] * 1.2)).round(1)
    m.loc[hit, "stations_over_200"] = np.round(m.loc[hit, "stations_total"] * (m.loc[hit, "api_max"] > 200) * .5)
    del ratio
    out = derive_haze_columns(m.drop(columns="pm25_real"), np.random.default_rng(SEED + 7))
    return out, int(hit.sum())


REQUIRED_HAZE_COLS = {"date", "state", "pm25_ugm3", "api_mean", "api_max", "stations_total", "stations_over_200",
                      "flights_scheduled", "school_closed_share", "flights_disrupted", "cloud_seeding_ops"}
REQUIRED_REF_COLS = {"state", "population", "gdp_rm_bn", "gdp_per_day_rm", "employed", "students",
                     "outdoor_worker_share", "agri_share", "tourism_share", "pct_65plus", "lat", "lon"}


def load_inputs(save_mock: bool = False, use_real: bool = True):
    """Return (state_reference, haze_daily, mock_truth_or_None, source_label)."""
    files = {k: MOCK_DIR / f"{k}.csv" for k in ("state_reference", "haze_daily", "damage_costs_daily")}
    ref = haze = None
    if files["state_reference"].exists() and files["haze_daily"].exists():
        ref = pd.read_csv(files["state_reference"])
        haze = pd.read_csv(files["haze_daily"])
        stale = (REQUIRED_HAZE_COLS - set(haze.columns)) | (REQUIRED_REF_COLS - set(ref.columns))
        if stale:
            print(f"[warn] {MOCK_DIR.relative_to(ROOT)} is from an older version (missing {sorted(stale)}); "
                  "using freshly generated mock data instead. Re-save with --save-mock to update the CSVs.",
                  file=sys.stderr)
            ref = haze = None
    if ref is not None:
        truth = pd.read_csv(files["damage_costs_daily"]) if files["damage_costs_daily"].exists() else None
        src = f"mock CSVs in {MOCK_DIR.relative_to(ROOT)}"
    else:
        mock = build_mock()
        if save_mock:
            MOCK_DIR.mkdir(parents=True, exist_ok=True)
            for k, df in mock.items():
                df.to_csv(MOCK_DIR / f"{k}.csv", index=False)
        ref, haze, truth, src = mock["state_reference"], mock["haze_daily"], mock["damage_costs_daily"], "synthetic"
    if use_real:
        real, path = load_real_pm25()
        if real is not None:
            haze, n = apply_real_pm25(haze, real)
            src += f" + real PM2.5 for {n} state-days ({path})"
            truth = None   # mock truth no longer matches the inputs
    return ref, haze, truth, src


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
                out[it["state"]] = {h: float(pm[h]) for h in ("24", "48", "72") if h in pm}
            if out:
                return out, str(p.relative_to(ROOT))
        except Exception as e:  # keep the pipeline running; say why
            print(f"[warn] could not parse {p}: {e}", file=sys.stderr)
    last3 = haze.sort_values("date").groupby("state").tail(3).groupby("state").pm25_ugm3.mean()
    return {s: {h: float(v) for h in ("24", "48", "72")} for s, v in last3.items()}, \
        "persistence (mean of last 3 days) — forecast.json not found"


# ---------------------------------------------------------------------------
# 3. Damage function
# ---------------------------------------------------------------------------
def central() -> dict[str, float]:
    return {k: v["value"] for k, v in COEFFS.items()}


def damage_by_day(haze: pd.DataFrame, ref: pd.DataFrame, c: dict[str, float],
                  rng: np.random.Generator | None = None) -> pd.DataFrame:
    """RM damage per state per day per category. rng=None -> expected values (the model);
    rng given -> Poisson counts + reporting noise (used only to make the mock truth)."""
    h = haze.merge(ref, on="state", how="left", validate="many_to_one")
    pop100k = h.population.to_numpy() / 1e5
    x = np.clip(h.pm25_ugm3.to_numpy() - c["pm25_threshold"], 0, None) / 10
    age_adj = 1 + .3 * (h.pct_65plus.to_numpy() / .08 - 1)   # older states respond a bit more

    def cases(base, crf, adj=1.0):
        lam = np.clip(base * pop100k * (np.exp(crf * x) - 1) * adj, 0, None)
        return rng.poisson(lam) if rng is not None else lam

    resp = cases(c["base_resp_adm"], c["crf_resp_admission"], age_adj)
    cardio = cases(c["base_cardio_adm"], c["crf_cardio_admission"], age_adj)
    outp = cases(c["base_outpatient"], c["crf_outpatient"], age_adj)
    deaths = cases(c["base_deaths"], c["crf_mortality"]) if INCLUDE_MORTALITY else np.zeros(len(h))

    api = h.api_max.to_numpy()
    unhealthy, very, over100 = (api > 100) & (api <= 200), api > 200, api > 100
    gdp_day = h.gdp_per_day_rm.to_numpy()
    emp = h.employed.to_numpy()
    gdp_worker_day = gdp_day / emp
    absence = np.where(very, c["absence_very_unhealthy"], np.where(unhealthy, c["absence_unhealthy"], 0))
    closed = (h.school_closed_share if "school_closed_share" in h else h.school_closed).to_numpy()

    out = pd.DataFrame({"date": h.date, "state": h.state})
    out["health"] = resp * c["cost_resp_admission"] + cardio * c["cost_cardio_admission"] + outp * c["cost_outpatient"]
    out["mortality"] = deaths * c["vsl"]
    out["productivity_loss"] = (emp * absence * gdp_worker_day
                                + emp * h.outdoor_worker_share.to_numpy() * over100
                                * c["outdoor_output_drop"] * gdp_worker_day)
    out["school_closure"] = closed * h.students.to_numpy() * c["school_cost_per_student_day"]
    out["tourism_loss"] = over100 * gdp_day * h.tourism_share.to_numpy() * c["tourism_drop"]
    out["agriculture_loss"] = over100 * gdp_day * h.agri_share.to_numpy() * c["agri_yield_drop"]
    out["transport_loss"] = h.flights_disrupted.to_numpy() * c["flight_cost"]
    out["response"] = h.cloud_seeding_ops.to_numpy() * c["cloud_seeding_cost"]
    if rng is not None:
        for col in ("productivity_loss", "tourism_loss", "agriculture_loss"):
            out[col] *= rng.lognormal(0, .15, len(out))
    out["total_damage_rm"] = out[CATEGORIES].sum(axis=1)
    return out


def forecast_day_inputs(haze: pd.DataFrame, pm25: dict[str, dict[str, float]], horizon: str) -> pd.DataFrame:
    """Synthetic 'next day' rows: forecast PM2.5, API derived, other drivers from the last observed day."""
    last = haze.sort_values("date").groupby("state").tail(1).set_index("state").copy()
    worst_ratio = (last.api_max / last.api_mean.clip(lower=1)).clip(1.0, 2.0)
    for s in last.index:
        if s in pm25 and horizon in pm25[s]:
            last.loc[s, "pm25_ugm3"] = pm25[s][horizon]
    last["api_mean"] = pm25_to_api(last.pm25_ugm3)
    last["api_max"] = np.clip(last.api_mean * worst_ratio, 0, 500)
    last["school_closed_share"] = np.where(last.api_max > 200, .5, np.where(last.api_max > 150, .25, 0))
    last["flights_disrupted"] = last.flights_scheduled * np.clip((last.api_max - 150) / 1000, 0, .15)
    last["cloud_seeding_ops"] = np.clip((last.api_max - 180) / 300, 0, .6)
    return last.reset_index()


# ---------------------------------------------------------------------------
# 4. Uncertainty, validation, sensitivity
# ---------------------------------------------------------------------------
def draw(rng: np.random.Generator) -> dict[str, float]:
    """One Monte Carlo draw: log-normal around the central value, low/high ≈ 5th/95th pct."""
    c = {}
    for k, v in COEFFS.items():
        if v["low"] == v["high"]:
            c[k] = v["value"]
            continue
        sigma = (np.log(v["high"]) - np.log(v["low"])) / (2 * 1.645)
        c[k] = float(v["value"] * np.exp(rng.normal(0, sigma)))
    return c


def monte_carlo(haze, ref, n_sims: int, seed: int = SEED) -> pd.DataFrame:
    """n_sims x state table of total damage (RM)."""
    rng = np.random.default_rng(seed + 1)
    return pd.DataFrame([damage_by_day(haze, ref, draw(rng)).groupby("state").total_damage_rm.sum()
                         for _ in range(n_sims)])


def backtest() -> dict:
    """On the pure mock: MAPE of state totals, model vs mock truth.
    Baseline = OLS of daily total on PM2.5 (pooled). NB: circular — the mock was made with this model."""
    mock = build_mock()
    haze, ref, truth = mock["haze_daily"], mock["state_reference"], mock["damage_costs_daily"]
    model = damage_by_day(haze, ref, central())
    t = truth.groupby("state").total_damage_rm.sum()
    m = model.groupby("state").total_damage_rm.sum().reindex(t.index)
    j = truth.merge(haze[["date", "state", "pm25_ugm3"]], on=["date", "state"])
    beta = np.polyfit(j.pm25_ugm3, j.total_damage_rm, 1)
    b = j.assign(pred=np.clip(np.polyval(beta, j.pm25_ugm3), 0, None)).groupby("state").pred.sum().reindex(t.index)
    mape = lambda p: float(np.mean(np.abs(p - t) / t.clip(lower=1)))
    return {"backtest_mape": round(mape(m), 4), "baseline_mape": round(mape(b), 4)}


def sensitivity(haze, ref, top: int = 5) -> pd.DataFrame:
    """Tornado: national total at each coefficient's low vs high, others central."""
    base = central()
    rows = []
    for k, v in COEFFS.items():
        if v["low"] == v["high"]:
            continue
        tot = {end: damage_by_day(haze, ref, dict(base, **{k: v[end]})).total_damage_rm.sum()
               for end in ("low", "high")}
        rows.append(dict(coefficient=k, low_rm=tot["low"], high_rm=tot["high"], swing_rm=abs(tot["high"] - tot["low"])))
    return pd.DataFrame(rows).sort_values("swing_rm", ascending=False).head(top).reset_index(drop=True)


# ---------------------------------------------------------------------------
# 5. Export
# ---------------------------------------------------------------------------
def run(n_sims: int = 1000, write: bool = True, save_mock: bool = False, use_real: bool = True) -> dict:
    ref, haze, _truth, data_src = load_inputs(save_mock, use_real)
    missing = set(STATE_NAMES) - set(ref.state)
    assert not missing, f"missing states: {missing}"

    daily = damage_by_day(haze, ref, central())
    by_state = daily.groupby("state")[CATEGORIES + ["total_damage_rm"]].sum()
    national = float(by_state.total_damage_rm.sum())
    if n_sims > 0:
        nat_draws = monte_carlo(haze, ref, n_sims).sum(axis=1)
        ci = [float(min(np.percentile(nat_draws, 5), national)), float(max(np.percentile(nat_draws, 95), national))]
    else:
        ci = [national, national]

    pm25_fc, fc_src = load_forecast(haze)
    fc = {h: damage_by_day(forecast_day_inputs(haze, pm25_fc, h), ref, central()).set_index("state").total_damage_rm
          for h in ("24", "48", "72")}
    latest = haze.sort_values("date").groupby("state").tail(1).set_index("state")
    ref_i = ref.set_index("state")
    days = int(haze.date.nunique())

    result = {
        "episode": {"start": str(haze.date.min()), "end": str(haze.date.max()), "days": days},
        "national": {
            "total_damage_rm": round(national, 0),
            "by_category_rm": {k: round(float(by_state[k].sum()), 0) for k in CATEGORIES},
            "ci90_rm": [round(ci[0], 0), round(ci[1], 0)],
            "damage_per_day_rm": round(national / days, 0),
        },
        "states": [{
            "state": s,
            "lat": float(ref_i.loc[s, "lat"]), "lon": float(ref_i.loc[s, "lon"]),
            "population": int(ref_i.loc[s, "population"]),
            "damage_to_date_rm": round(float(r.total_damage_rm), 0),
            "by_category_rm": {k: round(float(r[k]), 0) for k in CATEGORIES},
            "damage_pct_gdp": round(float(r.total_damage_rm / (ref_i.loc[s, "gdp_rm_bn"] * 1e9) * 100), 4),
            "forecast_damage_rm": {h: round(float(fc[h].get(s, 0.0)), 0) for h in ("24", "48", "72")},
            "api_latest": float(latest.loc[s, "api_max"]),
        } for s, r in by_state.sort_values("total_damage_rm", ascending=False).iterrows()],
        "model": {"type": "damage_function", "n_sims": n_sims, **backtest(), "baseline": "pm25_only_linear",
                  "mortality_included": INCLUDE_MORTALITY,
                  "benchmark_rm_per_day": [BENCHMARKS["2026_daily_low_rm"], BENCHMARKS["2026_daily_high_rm"]],
                  "sensitivity_top5": sensitivity(haze, ref).round(0).to_dict(orient="records")},
        "assumptions": [{"name": k, "value": v["value"], "unit": v["unit"], "source": v["source"],
                         "range": [v["low"], v["high"]]} for k, v in COEFFS.items()] + [
            {"name": "haze_history", "value": data_src, "unit": "",
             "source": DUMMY if "real" not in data_src else "Open-Meteo (real) + DUMMY for uncovered days"},
            {"name": "forecast_input", "value": fc_src, "unit": "",
             "source": "Forecast team" if "not found" not in fc_src else "persistence"},
            {"name": "state_reference", "value": "population, GDP, labour, students", "unit": "",
             "source": f"{DUMMY} (swap in OpenDOSM population_state, gdp_state_real_supply, lfs_qtr_state)"},
            {"name": "pm25_to_api", "value": "piecewise: PM2.5 55→API 100, 100→160, 150→200", "unit": "",
             "source": "approximation calibrated to 2026 APIMS news readings — replace with DOE breakpoints"},
            {"name": "uncertainty", "value": "log-normal per coefficient, low/high = 5th/95th pct", "unit": "", "source": "method choice"},
            {"name": "benchmark_2026", "value": "RM 20–50 m per day", "unit": "RM/day",
             "source": "The Star, 14 Oct 2026 — Dr Yeah Kim Leng (Sunway University)"},
            {"name": "benchmark_2013", "value": "~RM 1.7 bn total; RM 410.6 m illness", "unit": "RM",
             "source": "MJPHM, 'The Malaysia Haze and Its Health Economic Impact: A Literature Review'"},
        ],
        "_note": "Contains DUMMY inputs — see assumptions. Mortality excluded (haze treated as non-fatal).",
    }

    # sanity checks (the full set lives in tests/04_damage_tests.ipynb)
    for st in result["states"]:
        assert abs(sum(st["by_category_rm"].values()) - st["damage_to_date_rm"]) <= len(CATEGORIES), st["state"]
        assert all(v >= 0 for v in st["by_category_rm"].values()), st["state"]
    assert result["national"]["ci90_rm"][0] <= result["national"]["total_damage_rm"] <= result["national"]["ci90_rm"][1]

    if write:
        OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
        OUT_PATH.write_text(json.dumps(result, indent=2, ensure_ascii=False))
    return result


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--sims", type=int, default=1000, help="Monte Carlo draws (0 to skip)")
    ap.add_argument("--dry-run", action="store_true", help="don't write data/out/damage.json")
    ap.add_argument("--save-mock", action="store_true", help="write generated mock CSVs to data/mock/damage/")
    ap.add_argument("--no-real", action="store_true", help="ignore the real PM2.5 file even if present")
    a = ap.parse_args(argv)
    r = run(a.sims, write=not a.dry_run, save_mock=a.save_mock, use_real=not a.no_real)
    n = r["national"]
    print(f"Episode {r['episode']['start']} → {r['episode']['end']} ({r['episode']['days']} days)")
    print(f"National damage: RM {n['total_damage_rm']/1e9:.2f} bn  (90% range RM {n['ci90_rm'][0]/1e9:.2f}–"
          f"{n['ci90_rm'][1]/1e9:.2f} bn)  ≈ RM {n['damage_per_day_rm']/1e6:.0f} m/day  [benchmark RM 20–50 m/day]")
    for k, v in sorted(n["by_category_rm"].items(), key=lambda kv: -kv[1]):
        print(f"  {k:<18} RM {v/1e6:>9,.1f} m")
    print("Top 5 states:", ", ".join(f"{s['state']} (RM {s['damage_to_date_rm']/1e6:,.0f} m)" for s in r["states"][:5]))
    print(f"Backtest MAPE {r['model']['backtest_mape']} vs baseline {r['model']['baseline_mape']}  [mock truth — not a real validation]")
    print("Most sensitive:", ", ".join(x["coefficient"] for x in r["model"]["sensitivity_top5"]))
    if not a.dry_run:
        print(f"Wrote {OUT_PATH.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
