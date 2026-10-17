"""Extra data for the Cross-border ledger tab -> data/out/ledger.json

Everything here is computed from the teams' own code and data; nothing is typed in by hand.

1. episode_daily: the damage model's day-by-day output for the episode so far (15 Aug - 16 Oct 2026).
   damage.json only carries the totals; this re-runs pipeline/04_policy.py's own functions
   (load_inputs + damage_by_day at central values) to get the national figure per day and sector,
   the population-weighted PM2.5 per day and each state's daily total. The national sum is checked
   against damage.json.
2. fire_reference: real NASA FIRMS fire features from the haze team's processed table
   (hazeguard/data/processed/features.csv), for the latest haze season it covers (1 Aug - 6 Oct 2019).
   Per Malaysian state: upwind-weighted fire pressure and fires within 300 km. This is NOT a
   province-level source attribution (no back-trajectories exist in this build).

Run from the repo root, after pipeline/04_policy.py:
    .venv/bin/python web/src/tabs/Ledger/build_ledger_data.py
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[4]
OUT = ROOT / "data" / "out" / "ledger.json"
DAMAGE = ROOT / "data" / "out" / "damage.json"
FEATURES = ROOT / "hazeguard" / "data" / "processed" / "features.csv"
IMPORTANCE = ROOT / "hazeguard" / "data" / "out" / "feature_importance.csv"
FIRE_WINDOW = ("2019-08-01", "2019-10-06")
# features.csv uses short names for the federal territories
FEAT_NAMES = {"Kuala Lumpur": "W.P. Kuala Lumpur", "Labuan": "W.P. Labuan", "Putrajaya": "W.P. Putrajaya"}


def load_policy():
    spec = importlib.util.spec_from_file_location("policy04", ROOT / "pipeline" / "04_policy.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def episode_daily(p) -> dict:
    ref, haze, _truth, src, _ = p.load_inputs(save_mock=False, use_real=True, fetch=False)
    d = p.damage_by_day(haze, ref, p.central())
    nat = d.groupby("date")[p.CATEGORIES + ["total_damage_rm"]].sum().sort_index()
    pop = ref.set_index("state").population
    h = haze.assign(w=haze.state.map(pop))
    pm = h.groupby("date").apply(lambda g: (g.pm25_ugm3 * g.w).sum() / g.w.sum(), include_groups=False)
    unhealthy = haze.assign(u=haze.api_max > 100).groupby("date").u.sum()
    by_state = d.pivot_table(index="date", columns="state", values="total_damage_rm", aggfunc="sum").sort_index()

    total = float(nat.total_damage_rm.sum())
    if DAMAGE.exists():
        ref_total = json.loads(DAMAGE.read_text())["national"]["total_damage_rm"]
        assert abs(total - ref_total) <= max(1.0, 1e-6 * ref_total), (total, ref_total)
    return {
        "source": src,
        "dates": [str(x) for x in nat.index],
        "by_sector_rm": {k: [round(float(v), 0) for v in nat[k]] for k in p.CATEGORIES},
        "total_rm": [round(float(v), 0) for v in nat.total_damage_rm],
        "pm25_popweighted": [round(float(pm[x]), 1) for x in nat.index],
        "states_over_api100": [int(unhealthy[x]) for x in nat.index],
        "by_state_rm": {s: [round(float(v), 0) for v in by_state[s].fillna(0)] for s in by_state.columns},
    }


def fire_reference() -> dict | None:
    if not FEATURES.exists():
        return None
    f = pd.read_csv(FEATURES, usecols=["date", "state", "fire_pressure", "fires_300km", "frp_300km"])
    f = f[(f.date >= FIRE_WINDOW[0]) & (f.date <= FIRE_WINDOW[1])].copy()
    f["state"] = f.state.replace(FEAT_NAMES)
    g = f.groupby("state")
    per_state = [{
        "state": s,
        "fire_pressure_sum": round(float(x.fire_pressure.sum()), 2),
        "fire_pressure_peak": round(float(x.fire_pressure.max()), 2),
        "peak_date": str(x.loc[x.fire_pressure.idxmax(), "date"]),
        "fires_300km_peak": int(x.fires_300km.max()),
        "fires_300km_mean": round(float(x.fires_300km.mean()), 1),
    } for s, x in g]
    per_state.sort(key=lambda r: -r["fire_pressure_sum"])
    daily = f.groupby("date").agg(fire_pressure=("fire_pressure", "sum"), fires_300km=("fires_300km", "mean"))
    share = None
    if IMPORTANCE.exists():   # haze team's XGBoost feature importance, one column per forecast horizon
        imp = pd.read_csv(IMPORTANCE, index_col=0)
        fire_rows = imp.index.str.startswith("fire")
        share = {c: round(float(imp.loc[fire_rows, c].sum() / imp[c].sum()), 3) for c in imp.columns}
    return {
        "window": list(FIRE_WINDOW),
        "forecast_importance_share": share,
        "source": "NASA FIRMS hotspots (VIIRS/MODIS, low-confidence detections dropped) + Open-Meteo wind, "
                  "processed by hazeguard/pipeline/physical/prep/features.py",
        "definition": {
            "fire_pressure": "sum over fires of the last 2 days of FRP x wind alignment / distance (km); "
                             "alignment = 1 when the state's wind blows straight from the fire to the state, 0 when sideways or away",
            "fires_300km": "fire detections within 300 km of the state's main city (today and yesterday), ignoring wind",
        },
        "caveat": "Counts every fire within reach, in Malaysia as well as Indonesia. Not a source attribution by province.",
        "states": per_state,
        "daily": {"dates": list(daily.index), "fire_pressure_sum": [round(float(v), 2) for v in daily.fire_pressure],
                  "fires_300km_mean": [round(float(v), 1) for v in daily.fires_300km]},
    }


def main():
    p = load_policy()
    out = {
        "_about": "Built by web/src/tabs/Ledger/build_ledger_data.py from pipeline/04_policy.py and "
                  "hazeguard/data/processed/features.csv",
        "episode_daily": episode_daily(p),
        "fire_reference": fire_reference(),
        "source_attribution": None,   # no back-trajectory / province-share output exists in this repo
    }
    OUT.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")))
    print(f"Wrote {OUT.relative_to(ROOT)} ({OUT.stat().st_size / 1024:.1f} KB)")


if __name__ == "__main__":
    main()
