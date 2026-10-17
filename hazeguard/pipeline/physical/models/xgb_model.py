"""
XGBoost forecaster: predicts each state's API 1, 2 and 3 days ahead.

Two versions are trained so we can see what the fire data adds:
    xgb_no_fire    past API + weather + season
    xgb_with_fire  the same, plus fire_pressure features

Key ideas (see the comments below for detail):
  * Targets are built with a per-state shift, so one state's future never
    leaks into another state's row.
  * The model predicts the CHANGE in API (future minus today), not the level.
    Trees can't output values bigger than they saw in training; predicting the
    change and adding it to today's value lets forecasts go above past peaks.
  * Training rows must have their TARGET day on or before TRAIN_END, so nothing
    from the test window is used for fitting.
  * Only information known on the forecast day is used (today's weather, not
    tomorrow's).

Run from the repo root:
    python -m pipeline.physical.models.xgb_model
"""

import numpy as np
import pandas as pd
from xgboost import XGBRegressor

from pipeline.physical import config
from pipeline.physical.models.common import load_daily, summarise

H = config.HORIZONS_DAYS

BASE_FEATURES = [
    "api", "api_lag1", "api_lag2", "api_roll3", "api_roll7",
    "wind_speed", "wind_u", "wind_v", "rain_mm", "rain_roll3",
    "doy_sin", "doy_cos", "state_code",
]
FIRE_FEATURES = [
    "fire_pressure", "fire_pressure_lag1", "fire_roll3", "fire_roll7",
]

PARAMS = dict(n_estimators=400, learning_rate=0.05, max_depth=4,
              subsample=0.8, colsample_bytree=0.8, random_state=42)


def build_panel() -> pd.DataFrame:
    """One row per (state, day): features known that day + future API for targets."""
    api_in = load_daily(fill=True)       # inputs: short gaps filled
    api_true = load_daily(fill=False)    # targets: real readings only

    feats = pd.read_csv(config.FEATURES, parse_dates=["date"])

    frames = []
    for code, state in enumerate(config.STATES):
        # Every state has a complete daily index, so shifting by rows = shifting by days.
        df = pd.DataFrame({"api": api_in[state], "api_true": api_true[state]})
        df.index.name = "date"
        df["api_lag1"] = df["api"].shift(1)
        df["api_lag2"] = df["api"].shift(2)
        df["api_roll3"] = df["api"].rolling(3).mean()
        df["api_roll7"] = df["api"].rolling(7).mean()
        for h in H:
            df[f"target_{h}"] = df["api_true"].shift(-h) - df["api"]   # change over h days

        f = feats[feats["state"] == state].set_index("date").drop(columns="state")
        df = df.join(f, how="inner")

        # Wind direction is circular (359 deg is next to 1 deg), so give the
        # model the wind as east-west (u) and north-south (v) components instead.
        # Meteorological convention: direction is where the wind comes FROM.
        rad = np.deg2rad(df["wind_dir"])
        df["wind_u"] = -df["wind_speed"] * np.sin(rad)
        df["wind_v"] = -df["wind_speed"] * np.cos(rad)
        df["rain_roll3"] = df["rain_mm"].rolling(3).mean()

        # Smoke builds up over several days, so add short running averages of fire.
        df["fire_roll3"] = df["fire_pressure"].rolling(3).mean()
        df["fire_roll7"] = df["fire_pressure"].rolling(7).mean()

        doy = df.index.dayofyear
        df["doy_sin"] = np.sin(2 * np.pi * doy / 365.25)
        df["doy_cos"] = np.cos(2 * np.pi * doy / 365.25)
        df["state"] = state
        df["state_code"] = code
        frames.append(df.reset_index())
    return pd.concat(frames, ignore_index=True)


def training_rows(panel: pd.DataFrame, features: list[str], h: int) -> pd.DataFrame:
    """Rows whose inputs and target exist and whose TARGET day is before the test gap."""
    target = f"target_{h}"
    target_date = panel["date"] + pd.Timedelta(days=h)
    usable = panel[features + [target]].notna().all(axis=1)
    return panel[usable & (target_date <= config.TRAIN_END)
                 & (panel["date"] >= config.TRAIN_START)]


def fit(panel: pd.DataFrame, features: list[str], h: int) -> XGBRegressor:
    train = training_rows(panel, features, h)
    return XGBRegressor(**PARAMS).fit(train[features], train[f"target_{h}"])


def train_and_predict(panel: pd.DataFrame, features: list[str], name: str):
    rows, models = [], {}
    for h in H:
        target = f"target_{h}"
        target_date = panel["date"] + pd.Timedelta(days=h)
        train = training_rows(panel, features, h)
        # Test rows only need their inputs; rows with no real reading on the
        # target day are dropped later, when scoring.
        has_inputs = panel[features].notna().all(axis=1)
        test = panel[has_inputs & (target_date >= config.TEST_START)
                     & (target_date <= config.TEST_END)]

        model = XGBRegressor(**PARAMS).fit(train[features], train[target])
        models[h] = model

        pred = test["api"] + model.predict(test[features])     # today's level + predicted change
        rows.append(pd.DataFrame({
            "model": name, "state": test["state"], "origin": test["date"],
            "target_date": test["date"] + pd.Timedelta(days=h), "horizon": h,
            "actual": test["api"] + test[target], "pred": pred,
        }))
        print(f"  {name:<14} +{h}d  trained on {len(train):,} rows, tested on {len(test):,}")
    return pd.concat(rows, ignore_index=True), models


def main() -> None:
    panel = build_panel()
    print("Training XGBoost...")
    no_fire, _ = train_and_predict(panel, BASE_FEATURES, "xgb_no_fire")
    with_fire, fire_models = train_and_predict(panel, BASE_FEATURES + FIRE_FEATURES,
                                               "xgb_with_fire")

    preds = pd.concat([no_fire, with_fire]).dropna(subset=["actual", "pred"])
    config.OUT_DIR.mkdir(parents=True, exist_ok=True)
    preds.to_csv(config.OUT_DIR / "xgb_predictions.csv", index=False)

    # Compare with the baselines on exactly the same (state, target day, horizon) rows.
    base = pd.read_csv(config.OUT_DIR / "baseline_predictions.csv",
                       parse_dates=["origin", "target_date"])
    allp = pd.concat([base, preds], ignore_index=True)
    key = ["state", "target_date", "horizon"]
    common = allp.groupby(key)["model"].transform("nunique") == allp["model"].nunique()
    allp = allp[common]

    table = summarise(allp)
    order = ["persistence", "arima", "xgb_no_fire", "xgb_with_fire"]
    table = table.loc[[m for m in order if m in table.index]]
    table.to_csv(config.OUT_DIR / "model_comparison.csv")

    by_state = (allp.assign(err=(allp["actual"] - allp["pred"]).abs())
                .pivot_table(index="state", columns="model", values="err", aggfunc="mean")
                [order].round(2))
    by_state.to_csv(config.OUT_DIR / "model_comparison_by_state.csv")

    imp = pd.DataFrame({f"+{h}d": pd.Series(m.feature_importances_, index=m.feature_names_in_)
                        for h, m in fire_models.items()}).round(3)
    imp.sort_values("+1d", ascending=False).to_csv(config.OUT_DIR / "feature_importance.csv")

    print(f"\nMAE on the {config.TEST_START.date()} to {config.TEST_END.date()} haze "
          f"(API points, lower is better, {common.sum() // len(order):,} rows per model):")
    print(table.to_string())
    best_base = table.loc[["persistence", "arima"]].min()
    gain = (1 - table.loc["xgb_with_fire"] / best_base) * 100
    print("\nxgb_with_fire vs best baseline (%):", gain.round(1).to_dict())
    print("\nTop features (+1d model):")
    print(imp.sort_values("+1d", ascending=False).head(8).to_string())


if __name__ == "__main__":
    main()
