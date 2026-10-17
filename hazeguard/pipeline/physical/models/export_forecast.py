"""
Write the forecasts the dashboard reads.

data/out/forecast.json           one entry per state for REPLAY_DATE (the agreed format)
data/out/forecast_timeline.json  the same for every day of the test window, so the
                                 frontend can replay the haze day by day (optional)

Values are the Malaysian API index, so the key is "api" (the original
template said "pm25"; tell the frontend person about the rename).

arrival_hours = hours until the forecast first reaches UNHEALTHY_API:
    0 if it is already unhealthy on the forecast day,
    24 / 48 / 72 for the first horizon at or above the line,
    null if no horizon reaches it.

Run from the repo root (after xgb_model.py):
    python -m pipeline.physical.models.export_forecast
"""

import json

import pandas as pd

from pipeline.physical import config
from pipeline.physical.models.xgb_model import BASE_FEATURES, FIRE_FEATURES, build_panel, fit

H = config.HORIZONS_DAYS
FEATURES = BASE_FEATURES + FIRE_FEATURES


def arrival_hours(today: float, forecasts: dict[int, float]) -> int | None:
    if today >= config.UNHEALTHY_API:
        return 0
    for h in H:
        if forecasts[h] >= config.UNHEALTHY_API:
            return h * 24
    return None


def forecast_for(day_rows: pd.DataFrame, models: dict) -> list[dict]:
    out = []
    for _, row in day_rows.iterrows():
        x = row[FEATURES].to_frame().T.astype(float)
        fc = {h: float(row["api"] + models[h].predict(x)[0]) for h in H}
        lat, lon = config.STATE_POINTS[row["state"]]
        out.append({
            "zone": row["state"], "lat": lat, "lon": lon,
            "api_today": round(float(row["api"])),
            "api": {str(h * 24): round(fc[h]) for h in H},
            "arrival_hours": arrival_hours(row["api"], fc),
        })
    return out


def main() -> None:
    panel = build_panel()
    models = {h: fit(panel, FEATURES, h) for h in H}     # same training rule as xgb_model.py
    ready = panel[panel[FEATURES].notna().all(axis=1)]

    config.OUT_DIR.mkdir(parents=True, exist_ok=True)

    replay = forecast_for(ready[ready["date"] == config.REPLAY_DATE], models)
    with open(config.OUT_DIR / "forecast.json", "w") as f:
        json.dump(replay, f, indent=2)

    days = pd.date_range(config.TEST_START, config.TEST_END - pd.Timedelta(days=max(H)))
    timeline = {str(d.date()): forecast_for(ready[ready["date"] == d], models) for d in days}
    with open(config.OUT_DIR / "forecast_timeline.json", "w") as f:
        json.dump(timeline, f)

    missing = set(config.STATES) - {r["zone"] for r in replay}
    print(f"forecast.json: {len(replay)} states for {config.REPLAY_DATE.date()}"
          + (f" (missing: {sorted(missing)})" if missing else ""))
    print(f"forecast_timeline.json: {len(timeline)} days")
    for r in sorted(replay, key=lambda r: -r["api"]["24"])[:5]:
        print(f"  {r['zone']:<16} today {r['api_today']:>3}  24/48/72h {list(r['api'].values())}"
              f"  arrival {r['arrival_hours']}")


if __name__ == "__main__":
    main()
