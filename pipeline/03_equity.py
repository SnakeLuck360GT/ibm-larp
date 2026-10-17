"""Rank Malaysian states by haze priority with an XGBoost ranker, allocate stock, export states.json.

Run from the repo root:  .venv/bin/python pipeline/03_equity.py

Reads data/resource_allocation/features.csv (one row per state x year, built in
notebooks/03_equity_eda.ipynb) and config/allocation.json. Each year is one ranking
group. There is no ground-truth priority list, so labels are a proxy harm grade
(see config "label"). Validation is leave-one-year-out against a weighted-index baseline.

Writes data/resource_allocation/ranker.json (model), ranker_metrics.json (NDCG)
and data/out/states.json (priority, allocation and assumptions for the Routing tab).
"""

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

BASE_FEATURES = ["income_median", "income_mean", "income_gap", "poverty_rate", "beds_per_1000",
                 "pct_65plus", "pct_under5", "density", "pm25_mean", "unhealthy_days_per_yr"]
# Rates alone can't tell a state of 0.1M from one of 7M; harm is a headcount, so give the ranker size too.
FEATURES = [f"z_{f}" for f in BASE_FEATURES] + ["z_log_population"]

DOSM = "DOSM / data.gov.my (OpenDOSM), CC BY 4.0"
CAMS = "Open-Meteo Air Quality API / Copernicus CAMS, CC BY 4.0"


def load_config() -> dict:
    return json.loads(CONFIG.read_text())


def zscore_by_year(df: pd.DataFrame, col: pd.Series) -> pd.Series:
    g = col.groupby(df["year"])
    return (col - g.transform("mean")) / g.transform("std")


def load_features() -> pd.DataFrame:
    df = pd.read_csv(RA / "features.csv").sort_values(["year", "state"]).reset_index(drop=True)
    df["z_log_population"] = zscore_by_year(df, np.log(df["population"]))
    return df


def vulnerable_people(df: pd.DataFrame, cfg: dict) -> pd.Series:
    """Elderly + under-5 + poor households, poor weighted up (config label.vulnerable_weights).
    Groups overlap, so this is a relative weight, not a headcount."""
    w = cfg["label"]["vulnerable_weights"]
    return df["population"] * sum(df[col] * wt for col, wt in w.items()) / 100


def harm_proxy(df: pd.DataFrame, cfg: dict) -> pd.Series:
    return df[cfg["label"]["exposure"]] * vulnerable_people(df, cfg)


def proxy_labels(df: pd.DataFrame, cfg: dict) -> pd.Series:
    """Grade 0-3 by within-year quartile of the harm proxy (top quartile = 3)."""
    harm = harm_proxy(df, cfg)
    return harm.groupby(df["year"]).transform(
        lambda s: pd.qcut(s.rank(method="first"), 4, labels=False)).astype(int)


def baseline_scores(df: pd.DataFrame, cfg: dict) -> pd.Series:
    """Weighted index on within-year z-scores; higher = more vulnerable."""
    weights = {k: v for k, v in cfg["baseline_weights"].items() if k != "source"}
    return sum(df[f"z_{col}"] * wt for col, wt in weights.items())


def ndcg_by_year(df: pd.DataFrame, y: pd.Series, scores, k: int) -> dict[int, float]:
    scores = pd.Series(np.asarray(scores), index=df.index)
    return {int(yr): float(ndcg_score([y[idx]], [scores[idx]], k=k))
            for yr, idx in df.groupby("year").groups.items()}


def make_ranker(cfg: dict) -> XGBRanker:
    return XGBRanker(**cfg["ranker"])


def fit(ranker: XGBRanker, df: pd.DataFrame, y: pd.Series) -> XGBRanker:
    # XGBRanker needs rows grouped by qid; load_features sorts by year.
    return ranker.fit(df[FEATURES], y, qid=df["year"])


def leave_one_year_out(df: pd.DataFrame, y: pd.Series, cfg: dict) -> dict[int, float]:
    k = cfg["eval"]["k"]
    out = {}
    for yr in sorted(df["year"].unique()):
        train, test = df["year"] != yr, df["year"] == yr
        model = fit(make_ranker(cfg), df[train], y[train])
        out.update(ndcg_by_year(df[test], y[test], model.predict(df.loc[test, FEATURES]), k))
    return out


def train(df: pd.DataFrame, cfg: dict) -> tuple[XGBRanker, dict]:
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
        "validation": "leave-one-year-out; gain = proxy grade (0-3), sklearn ndcg_score",
        "label_method": cfg["label"]["method"],
        "features": FEATURES,
    }
    (RA / "ranker_metrics.json").write_text(json.dumps(metrics, indent=2) + "\n")
    return model, metrics


# --- PM2.5 by horizon -------------------------------------------------------------------------
# forecast.json from the physical model isn't on main yet. Until it is, use the CAMS forecast at
# each state capital (same source and points as the historical exposure), cached to raw/.
FORECAST_URL = "https://air-quality-api.open-meteo.com/v1/air-quality"
FORECAST_CSV = RAW / "pm25_forecast_openmeteo.csv"
HORIZONS = (24, 48, 72)
KL_TZ = ZoneInfo("Asia/Kuala_Lumpur")


def fetch_pm25_forecast(ref: pd.DataFrame) -> pd.DataFrame:
    """Mean PM2.5 over each 24 h window ahead: "24" = next 0-24 h, "48" = 24-48 h, "72" = 48-72 h."""
    resp = requests.get(FORECAST_URL, params={
        "latitude": ",".join(ref["lat"].astype(str)),
        "longitude": ",".join(ref["lon"].astype(str)),
        "hourly": "pm2_5", "timezone": "Asia/Kuala_Lumpur", "forecast_days": 4,
    }, timeout=60)
    resp.raise_for_status()
    now = pd.Timestamp(datetime.now(KL_TZ).replace(tzinfo=None)).floor("h")
    rows = []
    for state, loc in zip(ref["state"], resp.json()):
        h = pd.Series(loc["hourly"]["pm2_5"], index=pd.to_datetime(loc["hourly"]["time"]), dtype=float)
        h = h[h.index >= now]
        for hz in HORIZONS:
            window = h.iloc[hz - 24:hz]
            rows.append({"state": state, "horizon_h": hz, "pm25": round(float(window.mean()), 1),
                         "issued_at": now.isoformat()})
    return pd.DataFrame(rows)


def pm25_by_horizon(ref: pd.DataFrame, fallback: pd.Series) -> tuple[dict, dict]:
    """Returns ({state: {"24": x, "48": y, "72": z}}, assumption describing where it came from)."""
    try:
        fc = fetch_pm25_forecast(ref)
        assert fc["pm25"].notna().all()
        fc.to_csv(FORECAST_CSV, index=False)
        how = "fetched"
    except Exception as e:  # network down or API change: reuse last good fetch, else historical mean
        if not FORECAST_CSV.exists():
            print(f"PM2.5 forecast unavailable ({e}); using historical mean for every horizon")
            return ({s: {str(hz): float(v) for hz in HORIZONS} for s, v in fallback.items()},
                    {"name": "PM2.5 by horizon (24/48/72 h)",
                     "value": "Historical mean PM2.5 repeated for every horizon (forecast unavailable)",
                     "unit": "ug/m3", "source": CAMS})
        fc = pd.read_csv(FORECAST_CSV)
        how = "cached"
    issued = fc["issued_at"].iat[0]
    out = {s: {str(int(r.horizon_h)): float(r.pm25) for r in g.itertuples()} for s, g in fc.groupby("state")}
    return out, {"name": "PM2.5 by horizon (24/48/72 h)",
                 "value": f"CAMS forecast at state capital, mean of each 24 h window ahead, issued {issued} "
                          f"({how}); stand-in until forecast.json from the physical model lands",
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


def rank_latest(df: pd.DataFrame, model: XGBRanker, cfg: dict) -> pd.DataFrame:
    latest = df[df["year"] == df["year"].max()].copy()
    latest["score"] = model.predict(latest[FEATURES])
    # Trees give tied scores within a label grade; break ties by the underlying harm proxy.
    latest["harm"] = harm_proxy(latest, cfg)
    latest = latest.sort_values(["score", "harm"], ascending=False).reset_index(drop=True)
    latest["priority"] = np.arange(1, len(latest) + 1)
    s = latest["score"].astype(float)  # float32 from xgboost would leak 0.99299997 into the JSON
    latest["risk_score"] = ((s - s.min()) / (s.max() - s.min())).round(3)
    latest["vulnerable"] = vulnerable_people(latest, cfg)

    stock, alloc = cfg["stock"], cfg["allocation"]
    latest["mobile_clinic"] = latest["priority"] <= stock["mobile_clinics"]
    latest["n95"] = allocate_n95(latest["vulnerable"].to_numpy(), (latest["priority"] <= alloc["n95_max_priority"]).to_numpy(),
                                 stock["n95"], alloc["n95_per_vulnerable_person"])
    latest["action"] = [action_for(p, cfg) for p in latest["priority"]]
    return latest


# --- Export ---------------------------------------------------------------------------------------
def build_assumptions(cfg: dict, metrics: dict, year: int, pm25_note: dict) -> list[dict]:
    manifest = json.loads((RAW / "manifest.json").read_text())
    a = [{"name": f"Dataset: {m['id']}", "value": f"{m['description']} (years {m['years'][0]}-{m['years'][-1]})",
          "unit": "", "source": f"{m['source']}; {m['url']}"} for m in manifest]
    lab, alloc, k = cfg["label"], cfg["allocation"], cfg["eval"]["k"]
    baseline = {key: v for key, v in cfg["baseline_weights"].items() if key != "source"}
    ranker = ", ".join(f"{key}={v}" for key, v in cfg["ranker"].items())
    a += [
        {"name": "Reference year", "value": str(year), "unit": "year",
         "source": "Latest year present in population, income, poverty and beds tables"},
        {"name": "PM2.5 exposure (ranking)", "value": "Mean daily PM2.5 at state capital, Aug 2022 - Sep 2026, "
         "same value for every year; Selangor and KL share one CAMS grid cell", "unit": "ug/m3", "source": CAMS},
        {"name": "Unhealthy-air day", "value": "35", "unit": "ug/m3 (24 h mean PM2.5)",
         "source": "Malaysia Ambient Air Quality Standard 2020"},
        pm25_note,
        {"name": "Priority label (PROXY)", "value": f"{lab['description']} Exposure = {lab['exposure']}. "
         f"{lab['exposure_note']}", "unit": "grade 0-3", "source": lab["source"]},
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
        {"name": "Poverty line", "value": "DOSM revised the poverty line in 2019, so 2016 rates use the old, lower "
         "line; features are z-scored within year so only the ordering of states is compared",
         "unit": "% of households", "source": DOSM},
        {"name": "State capital coordinates and area", "value": "Hard-coded in data/resource_allocation/state_ref.csv",
         "unit": "deg, km2", "source": "Team reference table"},
    ]
    return a


def export(ranked: pd.DataFrame, cfg: dict, metrics: dict, pm25: dict, pm25_note: dict) -> dict:
    ref = pd.read_csv(RA / "state_ref.csv").set_index("state")
    k = cfg["eval"]["k"]
    states = [{
        "state": r.state, "lat": float(ref.at[r.state, "lat"]), "lon": float(ref.at[r.state, "lon"]),
        "population": int(r.population),
        "income_median": float(r.income_median), "income_mean": float(r.income_mean),
        "poverty_rate": float(r.poverty_rate), "beds_per_1000": round(float(r.beds_per_1000), 3),
        "pm25": pm25[r.state],
        "risk_score": float(r.risk_score), "priority": int(r.priority),
        "allocation": {"mobile_clinic": bool(r.mobile_clinic), "n95": int(r.n95)},
        "action": r.action,
    } for r in ranked.itertuples()]
    out = {
        "states": states,
        "stock": {"mobile_clinics": cfg["stock"]["mobile_clinics"], "n95": cfg["stock"]["n95"]},
        "model": {"type": "XGBRanker", f"ndcg@{k}": metrics[f"ndcg@{k}"],
                  f"baseline_ndcg@{k}": metrics[f"baseline_ndcg@{k}"]},
        "assumptions": build_assumptions(cfg, metrics, int(ranked["year"].iat[0]), pm25_note),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n")
    return out


def main() -> None:
    cfg = load_config()
    df = load_features()
    model, metrics = train(df, cfg)
    k = cfg["eval"]["k"]

    print(f"{'year':>6} {'ranker':>8} {'baseline':>9}")
    for yr, m in metrics["per_year"].items():
        print(f"{yr:>6} {m['ranker']:>8.3f} {m['baseline']:>9.3f}")
    print(f"{'mean':>6} {metrics[f'ndcg@{k}']:>8.3f} {metrics[f'baseline_ndcg@{k}']:>9.3f}")

    ranked = rank_latest(df, model, cfg)
    ref = pd.read_csv(RA / "state_ref.csv")
    pm25, pm25_note = pm25_by_horizon(ref, ranked.set_index("state")["pm25_mean"])
    export(ranked, cfg, metrics, pm25, pm25_note)

    ranked["baseline_rank"] = baseline_scores(ranked, cfg).rank(ascending=False).astype(int)
    print(f"\n{ranked['year'].iat[0]} priority -> {OUT.relative_to(ROOT)}")
    print(ranked[["priority", "state", "risk_score", "baseline_rank", "mobile_clinic", "n95", "action"]]
          .to_string(index=False))


if __name__ == "__main__":
    main()
