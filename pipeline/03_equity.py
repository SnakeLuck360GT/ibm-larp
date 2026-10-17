"""Rank Malaysian states by haze priority with an XGBoost ranker, allocate stock, export states.json.

Run from the repo root:
    .venv/bin/python pipeline/03_equity.py            # retrain on past years, rank the current year
    .venv/bin/python pipeline/03_equity.py --refresh  # reuse ranker.json, re-rank on the latest forecast

Reads data/resource_allocation/features.csv (one row per state x year, built by
pipeline/build_features.py) and config/allocation.json. Ranking groups are each
year plus every 3-day window of observed PM2.5 (see episode_groups). The ranker trains on every year
before the current one; the current year is only ranked.
There is no ground-truth priority list, so labels are a proxy harm grade (see config "label").
Validation is leave-one-year-out against a weighted-index baseline.

The current year is ranked once per PM2.5 forecast window (next 24/48/72 h) and once on the whole
0-72 h window; that last ranking drives the allocation. pipeline/cron_equity.sh runs --refresh hourly.

Writes data/resource_allocation/ranker.json (model), ranker_metrics.json (NDCG)
and data/out/states.json (priority, allocation and assumptions for the Routing tab).
"""

import argparse
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import requests
from sklearn.metrics import ndcg_score
from xgboost import XGBRanker

ROOT = Path(__file__).resolve().parents[1]
RA = ROOT / "data" / "resource_allocation"
RAW = RA / "raw"
OUT = ROOT / "data" / "out" / "states.json"
CONFIG = ROOT / "config" / "allocation.json"

BASE_FEATURES = ["income_median", "income_mean", "income_gap", "poverty_rate", "gini", "u_rate", "gdp_per_capita",
                 "beds_per_1000", "pct_65plus", "pct_under5", "density", "pm25_mean", "unhealthy_days_per_yr"]
EXPOSURE = ["pm25_mean", "unhealthy_days_per_yr"]  # the only features that change hour to hour
UNHEALTHY = 35.0  # ug/m3, MAAQS 2020 24 h PM2.5
# Rates alone can't tell a state of 0.1M from one of 7M; harm is a headcount, so give the ranker size too.
FEATURES = [f"z_{f}" for f in BASE_FEATURES] + ["z_log_population"]

DOSM = "DOSM / data.gov.my (OpenDOSM), CC BY 4.0"
CAMS = "Open-Meteo Air Quality API / Copernicus CAMS, CC BY 4.0"


def load_config() -> dict:
    return json.loads(CONFIG.read_text())


def zscore_by_group(df: pd.DataFrame, col: pd.Series) -> pd.Series:
    g = col.groupby(df["group"])
    std = g.transform("std").replace(0, 1)  # e.g. no state has an unhealthy forecast day
    return (col - g.transform("mean")) / std


def add_z(df: pd.DataFrame) -> pd.DataFrame:
    for f in BASE_FEATURES:
        df[f"z_{f}"] = zscore_by_group(df, df[f])
    df["z_log_population"] = zscore_by_group(df, np.log(df["population"]))
    return df


def load_features() -> pd.DataFrame:
    """One ranking group per year (group = year)."""
    df = pd.read_csv(RA / "features.csv").sort_values(["year", "state"]).reset_index(drop=True)
    df["group"] = df["year"].astype(str)
    return add_z(df)


def split_years(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(training years, current year). The current year is partial (YTD exposure, carried-forward
    surveys), so it is ranked but never trained on."""
    ref = df["year"].max()
    return df[df["year"] < ref].reset_index(drop=True), df[df["year"] == ref].reset_index(drop=True)


# A yearly mean barely moves state to state, so yearly groups alone teach the ranker little about
# PM2.5 and it can't react to a forecast spike. Every non-overlapping 3-day window of observed
# PM2.5 (same length as the 0-72 h allocation window) becomes its own group, paired with that
# year's socioeconomic row.
EPISODE_DAYS = 3


def episode_groups(past: pd.DataFrame) -> pd.DataFrame:
    pm = pd.read_csv(RAW / "pm25_daily_openmeteo.csv", parse_dates=["date"])
    daily = pm.pivot(index="date", columns="state", values="pm25")
    daily = daily.reindex(pd.date_range(daily.index.min(), daily.index.max(), freq="D"))
    rows = []
    for start in daily.index[::EPISODE_DAYS]:
        win = daily.loc[start:start + pd.Timedelta(days=EPISODE_DAYS - 1)]
        if len(win) < EPISODE_DAYS or win.isna().any().any() or start.year not in set(past["year"]):
            continue
        rows.append(pd.DataFrame({"state": win.columns, "year": start.year, "group": start.date().isoformat(),
                                  "pm25_mean": win.mean().to_numpy(),
                                  "unhealthy_days_per_yr": (win > UNHEALTHY).mean().to_numpy() * 365}))
    ep = pd.concat(rows, ignore_index=True)
    socio = past.drop(columns=EXPOSURE + ["group"] + [c for c in past if c.startswith("z_")])
    return ep.merge(socio, on=["state", "year"], how="left", validate="many_to_one")


def training_set(past: pd.DataFrame) -> pd.DataFrame:
    """Yearly groups plus 3-day episode groups, sorted so each group's rows are contiguous."""
    df = pd.concat([past, episode_groups(past)], ignore_index=True)
    df = df.sort_values(["year", "group", "state"]).reset_index(drop=True)
    assert df.groupby("group").size().eq(16).all()
    return add_z(df)


def vulnerable_people(df: pd.DataFrame, cfg: dict) -> pd.Series:
    """Elderly + under-5 + poor households, poor weighted up (config label.vulnerable_weights).
    Groups overlap, so this is a relative weight, not a headcount."""
    w = cfg["label"]["vulnerable_weights"]
    return df["population"] * sum(df[col] * wt for col, wt in w.items()) / 100


def harm_proxy(df: pd.DataFrame, cfg: dict) -> pd.Series:
    return df[cfg["label"]["exposure"]] * vulnerable_people(df, cfg)


def proxy_labels(df: pd.DataFrame, cfg: dict) -> pd.Series:
    """Grade 0..grades-1 by within-group quantile of the harm proxy (most harm = highest grade)."""
    harm = harm_proxy(df, cfg)
    return harm.groupby(df["group"]).transform(
        lambda s: pd.qcut(s.rank(method="first"), cfg["label"]["grades"], labels=False)).astype(int)


def baseline_scores(df: pd.DataFrame, cfg: dict) -> pd.Series:
    """Weighted index on within-group z-scores; higher = more vulnerable."""
    weights = {k: v for k, v in cfg["baseline_weights"].items() if k != "source"}
    return sum(df[f"z_{col}"] * wt for col, wt in weights.items())


def ndcg_by_year(df: pd.DataFrame, y: pd.Series, scores, k: int) -> dict[int, float]:
    """NDCG@k per group, averaged over the groups in each year."""
    scores = pd.Series(np.asarray(scores), index=df.index)
    per_group = pd.Series({g: ndcg_score([y[idx]], [scores[idx]], k=k) for g, idx in df.groupby("group").groups.items()})
    year = df.groupby("group")["year"].first()
    return {int(yr): float(v) for yr, v in per_group.groupby(year).mean().items()}


def make_ranker(cfg: dict) -> XGBRanker:
    return XGBRanker(**cfg["ranker"])


def fit(ranker: XGBRanker, df: pd.DataFrame, y: pd.Series) -> XGBRanker:
    # XGBRanker needs rows grouped by qid; training_set sorts by group.
    return ranker.fit(df[FEATURES], y, qid=pd.factorize(df["group"])[0])


def leave_one_year_out(df: pd.DataFrame, y: pd.Series, cfg: dict) -> dict[int, float]:
    k = cfg["eval"]["k"]
    out = {}
    for yr in sorted(df["year"].unique()):
        train, test = df["year"] != yr, df["year"] == yr
        model = fit(make_ranker(cfg), df[train], y[train])
        out.update(ndcg_by_year(df[test], y[test], model.predict(df.loc[test, FEATURES]), k))
    return out


def train(past: pd.DataFrame, cfg: dict) -> tuple[XGBRanker, dict]:
    df = training_set(past)
    y = proxy_labels(df, cfg)
    k = cfg["eval"]["k"]
    cv = leave_one_year_out(df, y, cfg)
    base = ndcg_by_year(df, y, baseline_scores(df, cfg), k)

    model = fit(make_ranker(cfg), df, y)
    model.save_model(RA / "ranker.json")

    metrics = {
        f"ndcg@{k}": round(float(np.mean(list(cv.values()))), 4),
        f"baseline_ndcg@{k}": round(float(np.mean(list(base.values()))), 4),
        "per_year": {yr: {"ranker": round(cv[yr], 4), "baseline": round(base[yr], 4)} for yr in cv},
        "validation": f"leave-one-year-out over yearly + {EPISODE_DAYS}-day PM2.5 episode groups; "
                      f"gain = proxy grade (0-{cfg['label']['grades'] - 1}), sklearn ndcg_score",
        "groups": {"yearly": int(past["group"].nunique()),
                   f"{EPISODE_DAYS}_day_episodes": int(df["group"].nunique() - past["group"].nunique())},
        "trained_years": sorted(int(y) for y in df["year"].unique()),
        "label_method": cfg["label"]["method"],
        "features": FEATURES,
    }
    (RA / "ranker_metrics.json").write_text(json.dumps(metrics, indent=2) + "\n")
    return model, metrics


def load_trained() -> tuple[XGBRanker, dict]:
    model = XGBRanker()
    model.load_model(RA / "ranker.json")
    return model, json.loads((RA / "ranker_metrics.json").read_text())


# --- PM2.5 by horizon -------------------------------------------------------------------------
# forecast.json from the physical model isn't on main yet. Until it is, use the CAMS forecast at
# each state capital (same source and points as the historical exposure), cached to raw/.
FORECAST_URL = "https://air-quality-api.open-meteo.com/v1/air-quality"
FORECAST_CSV = RAW / "pm25_forecast_openmeteo.csv"
HORIZONS = (24, 48, 72)
MAX_CACHE_AGE_H = 6  # an older cached forecast no longer describes the next 72 h
ALLOC = "0-72"  # the window the allocation is ranked on: lead time to move stock
KL_TZ = ZoneInfo("Asia/Kuala_Lumpur")


def fetch_pm25_forecast(ref: pd.DataFrame) -> pd.DataFrame:
    """Hourly PM2.5 for the next 72 h at each state capital: columns state, hour (0-71), pm25, issued_at."""
    resp = requests.get(FORECAST_URL, params={
        "latitude": ",".join(ref["lat"].astype(str)),
        "longitude": ",".join(ref["lon"].astype(str)),
        "hourly": "pm2_5", "timezone": "Asia/Kuala_Lumpur", "forecast_days": 5,
    }, timeout=60)
    resp.raise_for_status()
    now = pd.Timestamp(datetime.now(KL_TZ).replace(tzinfo=None)).floor("h")
    frames = []
    for state, loc in zip(ref["state"], resp.json()):
        h = pd.Series(loc["hourly"]["pm2_5"], index=pd.to_datetime(loc["hourly"]["time"]), dtype=float)
        h = h[h.index >= now].iloc[:72]
        assert len(h) == 72 and h.notna().all(), state
        frames.append(pd.DataFrame({"state": state, "hour": range(72), "pm25": h.round(1).to_numpy(),
                                    "issued_at": now.isoformat()}))
    return pd.concat(frames, ignore_index=True)


def exposure_windows(fc: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """Per window, the two exposure features on the same footing as the yearly ones:
    pm25_mean = mean PM2.5; unhealthy_days_per_yr = share of 24 h windows above 35 ug/m3, x 365.
    "24" = next 0-24 h, "48" = 24-48 h, "72" = 48-72 h, ALLOC = all 72 h."""
    day = fc.assign(window=fc["hour"] // 24 * 24 + 24).groupby(["state", "window"])["pm25"].mean().unstack()
    out = {str(hz): pd.DataFrame({"pm25_mean": day[hz], "unhealthy_days_per_yr": (day[hz] > UNHEALTHY) * 365.0})
           for hz in HORIZONS}
    out[ALLOC] = pd.DataFrame({"pm25_mean": day.mean(axis=1),
                               "unhealthy_days_per_yr": (day > UNHEALTHY).mean(axis=1) * 365})
    return out


def pm25_windows(ref: pd.DataFrame, ytd: pd.DataFrame) -> tuple[dict[str, pd.DataFrame], dict]:
    """Returns ({window: exposure by state}, assumption describing where it came from).
    ytd (indexed by state, EXPOSURE columns) is the fallback when no forecast is available."""
    try:
        fc = fetch_pm25_forecast(ref)
        fc.to_csv(FORECAST_CSV, index=False)
        how = "fetched"
    except Exception as e:  # network down or API change: reuse a recent fetch, else year-to-date
        cached = pd.read_csv(FORECAST_CSV) if FORECAST_CSV.exists() else None
        now = pd.Timestamp(datetime.now(KL_TZ).replace(tzinfo=None))
        if (cached is None or "hour" not in cached
                or now - pd.Timestamp(cached["issued_at"].iat[0]) > pd.Timedelta(hours=MAX_CACHE_AGE_H)):
            print(f"PM2.5 forecast unavailable ({e}); ranking on year-to-date exposure for every window")
            return ({w: ytd[EXPOSURE] for w in [*map(str, HORIZONS), ALLOC]},
                    {"name": "PM2.5 by horizon (24/48/72 h)",
                     "value": "Year-to-date PM2.5 repeated for every horizon (forecast unavailable)",
                     "unit": "ug/m3", "source": CAMS})
        fc, how = cached, "cached"
    issued = fc["issued_at"].iat[0]
    return exposure_windows(fc), {
        "name": "PM2.5 by horizon (24/48/72 h)",
        "value": f"CAMS forecast at state capital, mean of each 24 h window ahead, issued {issued} ({how}); "
                 f"priority_by_horizon ranks each window, the allocation ranks the whole 0-72 h; "
                 "stand-in until forecast.json from the physical model lands",
        "unit": "ug/m3", "source": CAMS}


# --- Allocation ---------------------------------------------------------------------------------
def allocate_n95(need: np.ndarray, eligible: np.ndarray, stock: int, per_person: float) -> np.ndarray:
    """need is ordered most urgent first. Proportional split over eligible states, capped at
    need * per_person, floored to whole masks; leftovers go to the most urgent state with room."""
    cap = np.where(eligible, np.floor(need * per_person), 0).astype(int)
    if cap.sum() <= stock:
        return cap
    share = np.minimum(np.floor(stock * cap / cap.sum()), cap).astype(int)
    left = stock - share.sum()
    for i in range(len(share)):
        add = min(cap[i] - share[i], left)
        share[i] += add
        left -= add
    return share


def action_for(priority: int, cfg: dict) -> str:
    return next(a["action"] for a in cfg["allocation"]["actions"] if priority <= a["max_priority"])


def rank_scenario(rows: pd.DataFrame, exposure: pd.DataFrame, model: XGBRanker, cfg: dict) -> pd.DataFrame:
    """Rank one year's 16 rows with their exposure swapped for `exposure` (indexed by state).
    Re-z-scores across the 16 states, so a forecast in any units sits on the training scale."""
    df = rows.copy()
    for col in EXPOSURE:
        df[col] = df["state"].map(exposure[col]).astype(float)
    df = add_z(df)
    df["score"] = model.predict(df[FEATURES])
    # Trees give tied scores within a label grade; break ties by the underlying harm proxy.
    df["harm"] = harm_proxy(df, cfg)
    df = df.sort_values(["score", "harm"], ascending=False).reset_index(drop=True)
    df["priority"] = np.arange(1, len(df) + 1)
    s = df["score"].astype(float)  # float32 from xgboost would leak 0.99299997 into the JSON
    df["risk_score"] = ((s - s.min()) / (s.max() - s.min())).round(3)
    return df


def rank_current(rows: pd.DataFrame, windows: dict[str, pd.DataFrame], model: XGBRanker, cfg: dict) -> pd.DataFrame:
    """Allocation ranking on the 0-72 h window, plus priority_<h> for each 24 h window."""
    ranked = rank_scenario(rows, windows[ALLOC], model, cfg)
    for hz in HORIZONS:
        by_state = rank_scenario(rows, windows[str(hz)], model, cfg).set_index("state")["priority"]
        ranked[f"priority_{hz}"] = ranked["state"].map(by_state)
    ranked["vulnerable"] = vulnerable_people(ranked, cfg)

    stock, alloc = cfg["stock"], cfg["allocation"]
    ranked["mobile_clinic"] = ranked["priority"] <= stock["mobile_clinics"]
    ranked["n95"] = allocate_n95(ranked["vulnerable"].to_numpy(), (ranked["priority"] <= alloc["n95_max_priority"]).to_numpy(),
                                 stock["n95"], alloc["n95_per_vulnerable_person"])
    ranked["action"] = [action_for(p, cfg) for p in ranked["priority"]]
    return ranked


# --- Export ---------------------------------------------------------------------------------------
SOURCE_COLS = {"hies_source": "Income, poverty, gini (HIES)", "u_rate_source": "Unemployment (LFS)",
               "beds_source": "Hospital beds", "gdp_source": "GDP per capita", "pm25_source": "PM2.5 (yearly)"}


def build_assumptions(cfg: dict, metrics: dict, rows: pd.DataFrame, pm25_note: dict) -> list[dict]:
    manifest = json.loads((RAW / "manifest.json").read_text())
    a = [{"name": f"Dataset: {m['id']}", "value": f"{m['description']} (years {m['years'][0]}-{m['years'][-1]})",
          "unit": "", "source": f"{m['source']}; {m['url']}"} for m in manifest]
    lab, alloc, k = cfg["label"], cfg["allocation"], cfg["eval"]["k"]
    baseline = {key: v for key, v in cfg["baseline_weights"].items() if key != "source"}
    ranker = ", ".join(f"{key}={v}" for key, v in cfg["ranker"].items())
    year, trained = int(rows["year"].iat[0]), metrics["trained_years"]
    vintage = "; ".join(f"{label}: {'/'.join(sorted(set(rows[col])))}" for col, label in SOURCE_COLS.items())
    a += [
        {"name": "Reference year", "value": f"{year} (ranked, not trained on; ranker trained on {trained[0]}-{trained[-1]})",
         "unit": "year", "source": "Current year in Malaysia time"},
        {"name": f"{year} data vintage", "value": f"{vintage}. Surveys are filled by build_features.py: linear between "
         "survey years, carried forward after the last one (income scaled by state CPI); 'ytd' = year to date",
         "unit": "", "source": DOSM},
        {"name": "PM2.5 exposure (training)", "value": "Yearly mean daily PM2.5 at state capital; CAMS history starts "
         "Aug 2022, so 2023+ are observed and earlier years use the 2023+ mean (climatology); Selangor and KL share "
         "one CAMS grid cell", "unit": "ug/m3", "source": CAMS},
        {"name": "Unhealthy-air day", "value": str(UNHEALTHY), "unit": "ug/m3 (24 h mean PM2.5)",
         "source": "Malaysia Ambient Air Quality Standard 2020"},
        pm25_note,
        {"name": "Priority label (PROXY)", "value": f"{lab['description']} Exposure = {lab['exposure']}. "
         f"{lab['exposure_note']} {lab['grades_note']}", "unit": f"grade 0-{lab['grades'] - 1}",
         "source": lab["source"]},
        {"name": "Vulnerable people weights", "value": json.dumps(lab["vulnerable_weights"]),
         "unit": "multiplier on % of population", "source": lab["source"]},
        {"name": "Baseline weights", "value": json.dumps(baseline), "unit": "weight on within-year z-score",
         "source": cfg["baseline_weights"]["source"]},
        {"name": "Ranker", "value": f"XGBRanker ({ranker}); {metrics['validation']}; "
         f"NDCG@{k} {metrics[f'ndcg@{k}']} vs baseline {metrics[f'baseline_ndcg@{k}']}",
         "unit": "", "source": "This pipeline (pipeline/03_equity.py)"},
        {"name": "Mobile clinic stock", "value": str(cfg["stock"]["mobile_clinics"]), "unit": "clinics",
         "source": cfg["stock"]["source"]},
        {"name": "N95 stock", "value": str(cfg["stock"]["n95"]), "unit": "masks", "source": cfg["stock"]["source"]},
        {"name": "Clinic rule", "value": alloc["clinic_rule"], "unit": "", "source": alloc["source"]},
        {"name": "N95 rule", "value": alloc["n95_rule"].replace("n95_max_priority", str(alloc["n95_max_priority"]))
         .replace("n95_per_vulnerable_person", str(alloc["n95_per_vulnerable_person"])),
         "unit": "", "source": alloc["source"]},
        {"name": "Actions", "value": "; ".join(f"priority <= {x['max_priority']}: {x['action']}" for x in alloc["actions"]),
         "unit": "", "source": alloc["source"]},
        {"name": "Poverty line", "value": "DOSM revised the poverty line in 2019, so 2016 rates (and 2017-2018, "
         "interpolated from them) use the old, lower line; features are z-scored within year so only the ordering "
         "of states is compared", "unit": "% of households", "source": DOSM},
        {"name": "Putrajaya GDP", "value": "Published separately only from 2023; earlier years back-filled at its "
         "2023 GDP per head (KL's earlier figures include Putrajaya)", "unit": "RM per person", "source": DOSM},
        {"name": "State capital coordinates and area", "value": "Hard-coded in data/resource_allocation/state_ref.csv",
         "unit": "deg, km2", "source": "Team reference table"},
    ]
    return a


def export(ranked: pd.DataFrame, cfg: dict, metrics: dict, windows: dict[str, pd.DataFrame], pm25_note: dict) -> dict:
    ref = pd.read_csv(RA / "state_ref.csv").set_index("state")
    k = cfg["eval"]["k"]
    states = [{
        "state": r.state, "lat": float(ref.at[r.state, "lat"]), "lon": float(ref.at[r.state, "lon"]),
        "population": int(r.population),
        "income_median": round(float(r.income_median), 2), "income_mean": round(float(r.income_mean), 2),
        "poverty_rate": float(r.poverty_rate), "beds_per_1000": round(float(r.beds_per_1000), 3),
        "u_rate": round(float(r.u_rate), 2), "gini": round(float(r.gini), 3),
        "pm25": {str(hz): round(float(windows[str(hz)].at[r.state, "pm25_mean"]), 1) for hz in HORIZONS},
        "pm25_ytd": round(float(r.pm25_ytd), 1),
        "risk_score": float(r.risk_score), "priority": int(r.priority),
        "priority_by_horizon": {str(hz): int(getattr(r, f"priority_{hz}")) for hz in HORIZONS},
        "allocation": {"mobile_clinic": bool(r.mobile_clinic), "n95": int(r.n95)},
        "action": r.action,
    } for r in ranked.itertuples()]
    out = {
        "generated_at": datetime.now(KL_TZ).isoformat(timespec="seconds"),
        "reference_year": int(ranked["year"].iat[0]),
        "states": states,
        "stock": {"mobile_clinics": cfg["stock"]["mobile_clinics"], "n95": cfg["stock"]["n95"]},
        "model": {"type": "XGBRanker", f"ndcg@{k}": metrics[f"ndcg@{k}"],
                  f"baseline_ndcg@{k}": metrics[f"baseline_ndcg@{k}"], "trained_years": metrics["trained_years"]},
        "assumptions": build_assumptions(cfg, metrics, ranked, pm25_note),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    # Write then rename, so the API never reads a half-written file while the hourly cron runs.
    tmp = OUT.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n")
    tmp.replace(OUT)
    export_web(ranked, cfg, windows, out["generated_at"])
    export_forecast_live(pm25_note)
    return out


LIVE_OUT = OUT.parent / "forecast_live.json"


def export_forecast_live(pm25_note: dict) -> dict | None:
    """The 72 hourly CAMS PM2.5 values per state behind the 24/48/72 h windows, for the Command map's
    live chart. Skipped when the ranking fell back to year-to-date exposure (no usable hourly forecast)."""
    if not FORECAST_CSV.exists() or pm25_note["value"].startswith("Year-to-date"):
        return None
    fc = pd.read_csv(FORECAST_CSV)
    if "hour" not in fc:
        return None
    issued = pd.Timestamp(fc["issued_at"].iat[0])
    if issued.tzinfo is None:  # fetch_pm25_forecast stores naive Kuala Lumpur time
        issued = issued.tz_localize(KL_TZ)
    out = {
        "issued_at": issued.isoformat(), "unit": "µg/m³",
        "source": f"{CAMS}; hourly PM2.5 at each state capital, hour 0 = issue hour",
        "states": {st: [round(float(v), 1) for v in g.sort_values("hour")["pm25"]] for st, g in fc.groupby("state")},
    }
    tmp = LIVE_OUT.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(out, separators=(",", ":"), ensure_ascii=False) + "\n")
    tmp.replace(LIVE_OUT)
    return out


WEB_OUT = OUT.parent / "ranker_web.json"


def export_web(ranked: pd.DataFrame, cfg: dict, windows: dict[str, pd.DataFrame], generated_at: str) -> dict:
    """Everything the Routing tab needs to re-run rank_current in the browser: the trees, the current
    year's raw rows (plan order) and each state's exposure per window (unrounded, so the browser
    reproduces states.json exactly). Window "all" = ALLOC; its pm25_mean is the mean of the three 24 h
    means, and unhealthy_days_per_yr = share of them above UNHEALTHY x 365."""
    booster = json.loads((RA / "ranker.json").read_text())["learner"]
    gb = booster["gradient_booster"]["model"]
    assert booster["feature_names"] == FEATURES
    trees = [[t["left_children"], t["right_children"], t["split_indices"], t["split_conditions"],
              [int(d) for d in t["default_left"]]] for t in gb["trees"]]
    raw = list(dict.fromkeys(["population", *BASE_FEATURES, *cfg["label"]["vulnerable_weights"]]))
    raw = [c for c in raw if c not in EXPOSURE]
    wins = {**{str(hz): str(hz) for hz in HORIZONS}, "all": ALLOC}
    rows = [{"state": r["state"], **{c: float(r[c]) for c in raw},
             "exposure": {w: [float(windows[src].at[r["state"], col]) for col in EXPOSURE] for w, src in wins.items()}}
            for _, r in ranked.iterrows()]
    alloc = cfg["allocation"]
    out = {
        "generated_at": generated_at, "reference_year": int(ranked["year"].iat[0]),
        "features": FEATURES, "base_features": BASE_FEATURES, "exposure": EXPOSURE, "raw_columns": raw,
        "unhealthy": UNHEALTHY,
        "base_score": float(booster["learner_model_param"]["base_score"].strip("[]")),
        "tree_format": ["left_children", "right_children", "split_indices", "split_conditions (leaf value at leaves)",
                        "default_left"],
        "trees": trees,
        "stock": {"mobile_clinics": cfg["stock"]["mobile_clinics"], "n95": cfg["stock"]["n95"]},
        "n95_max_priority": alloc["n95_max_priority"], "n95_per_vulnerable_person": alloc["n95_per_vulnerable_person"],
        "vulnerable_weights": cfg["label"]["vulnerable_weights"], "label_exposure": cfg["label"]["exposure"],
        "rows": rows,
    }
    tmp = WEB_OUT.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(out, separators=(",", ":"), ensure_ascii=False) + "\n")
    tmp.replace(WEB_OUT)
    return out


def main(refresh: bool = False) -> None:
    cfg = load_config()
    past, current = split_years(load_features())
    k = cfg["eval"]["k"]
    if refresh:
        model, metrics = load_trained()
    else:
        model, metrics = train(past, cfg)
        print(f"{'year':>6} {'ranker':>8} {'baseline':>9}")
        for yr, m in metrics["per_year"].items():
            print(f"{yr:>6} {m['ranker']:>8.3f} {m['baseline']:>9.3f}")
        print(f"{'mean':>6} {metrics[f'ndcg@{k}']:>8.3f} {metrics[f'baseline_ndcg@{k}']:>9.3f}")

    ref = pd.read_csv(RA / "state_ref.csv")
    current["pm25_ytd"] = current["pm25_mean"]
    windows, pm25_note = pm25_windows(ref, current.set_index("state"))
    ranked = rank_current(current, windows, model, cfg)
    export(ranked, cfg, metrics, windows, pm25_note)

    ytd = rank_scenario(current, current.set_index("state")[EXPOSURE], model, cfg).set_index("state")["priority"]
    ranked["ytd_rank"] = ranked["state"].map(ytd)
    ranked["baseline_rank"] = baseline_scores(ranked, cfg).rank(ascending=False, method="first").astype(int)
    ranked["pm25_0_72"] = ranked["state"].map(windows[ALLOC]["pm25_mean"]).round(1)
    print(f"\n{ranked['year'].iat[0]} priority ({pm25_note['value'][:60]}...) -> {OUT.relative_to(ROOT)}")
    print(ranked[["priority", "state", "pm25_0_72", "risk_score", "priority_24", "priority_48", "priority_72",
                  "ytd_rank", "baseline_rank", "mobile_clinic", "n95", "action"]].to_string(index=False))


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--refresh", action="store_true", help="skip training; re-rank with saved ranker.json")
    main(ap.parse_args().refresh)
