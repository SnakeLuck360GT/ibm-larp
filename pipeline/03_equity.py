"""Step 3: rank Malaysian states by haze priority with an XGBoost ranker.

Run from the repo root:  .venv/bin/python pipeline/03_equity.py

Reads data/resource_allocation/features.csv (one row per state x year, built in
notebooks/03_equity_eda.ipynb) and config/allocation.json. Each year is one ranking
group. There is no ground-truth priority list, so labels are a proxy harm grade
(see config "label"). Validation is leave-one-year-out against a weighted-index baseline.

Writes data/resource_allocation/ranker.json (model) and ranker_metrics.json (NDCG).
"""

import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import ndcg_score
from xgboost import XGBRanker

ROOT = Path(__file__).resolve().parents[1]
RA = ROOT / "data" / "resource_allocation"
CONFIG = ROOT / "config" / "allocation.json"

BASE_FEATURES = ["income_median", "income_mean", "income_gap", "poverty_rate", "beds_per_1000",
                 "pct_65plus", "pct_under5", "density", "pm25_mean", "unhealthy_days_per_yr"]
# Rates alone can't tell a state of 0.1M from one of 7M; harm is a headcount, so give the ranker size too.
FEATURES = [f"z_{f}" for f in BASE_FEATURES] + ["z_log_population"]


def load_config() -> dict:
    return json.loads(CONFIG.read_text())


def zscore_by_year(df: pd.DataFrame, col: pd.Series) -> pd.Series:
    g = col.groupby(df["year"])
    return (col - g.transform("mean")) / g.transform("std")


def load_features() -> pd.DataFrame:
    df = pd.read_csv(RA / "features.csv").sort_values(["year", "state"]).reset_index(drop=True)
    df["z_log_population"] = zscore_by_year(df, np.log(df["population"]))
    return df


def harm_proxy(df: pd.DataFrame, cfg: dict) -> pd.Series:
    """Exposure x vulnerable people, with poor households weighted up (config label.vulnerable_weights)."""
    w = cfg["label"]["vulnerable_weights"]
    vulnerable_share = sum(df[col] * wt for col, wt in w.items()) / 100
    return df[cfg["label"]["exposure"]] * df["population"] * vulnerable_share


def proxy_labels(df: pd.DataFrame, cfg: dict) -> pd.Series:
    """Grade 0..grades-1 by within-year quantile of the harm proxy (top quartile = 3)."""
    harm = harm_proxy(df, cfg)
    grades = cfg["label"]["grades"]
    return harm.groupby(df["year"]).transform(
        lambda s: pd.qcut(s.rank(method="first"), grades, labels=False)).astype(int)


def baseline_scores(df: pd.DataFrame, cfg: dict) -> pd.Series:
    """Weighted index on within-year z-scores; higher = more vulnerable."""
    return sum(df[f"z_{col}"] * wt for col, wt in cfg["baseline_weights"].items())


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


def main() -> None:
    cfg = load_config()
    df = load_features()
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

    print(f"{'year':>6} {'ranker':>8} {'baseline':>9}")
    for yr in cv:
        print(f"{yr:>6} {cv[yr]:>8.3f} {base[yr]:>9.3f}")
    print(f"{'mean':>6} {metrics[f'ndcg@{k}']:>8.3f} {metrics[f'baseline_ndcg@{k}']:>9.3f}")

    latest = df[df["year"] == df["year"].max()].copy()
    latest["score"] = model.predict(latest[FEATURES])
    latest["baseline"] = baseline_scores(latest, cfg)
    latest["grade"] = y[latest.index]
    latest = latest.sort_values("score", ascending=False)
    latest["baseline_rank"] = latest["baseline"].rank(ascending=False).astype(int)
    print(f"\n{latest['year'].iat[0]} ranking (ranker order):")
    print(latest[["state", "grade", "score", "baseline_rank"]].round(3).to_string(index=False))


if __name__ == "__main__":
    main()
