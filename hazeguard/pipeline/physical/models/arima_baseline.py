"""
Baselines: forecasts that use ONLY past API values, no fire or weather info.
XGBoost has to beat these for the fire features to mean anything.

1. Persistence: "the API in h days will be the same as today."
2. ARIMA: one model per state, fitted on training data only.

How the test works (a "rolling" forecast, like a real forecaster):
    For each day in the test window, pretend it is that morning.
    Use everything observed up to that day, forecast 1, 2 and 3 days ahead,
    then compare with what actually happened.
    ARIMA's parameters are fitted once on the training years and never
    see the test data; each day it only updates with the newest readings.

Run from the repo root:
    python -m pipeline.physical.models.arima_baseline
"""

import warnings

import pandas as pd
from statsmodels.tsa.arima.model import ARIMA

from pipeline.physical import config
from pipeline.physical.models.common import continuous_tail, load_daily, summarise

H = config.HORIZONS_DAYS
# Forecast origins: the days we "stand on" when forecasting. The first origin
# is max(H) days before the test starts, so every test day gets a forecast at
# every horizon. Origins in the gap are fine: that data is observed history,
# it is just never used to fit parameters.
ORIGINS = pd.date_range(config.TEST_START - pd.Timedelta(days=max(H)),
                        config.TEST_END - pd.Timedelta(days=1), freq="D")

# Small grid of ARIMA orders (p, d, q); the lowest AIC on training data wins.
# d is fixed at 0 (API swings around a stable level), because AIC values
# are not comparable between models with different d.
ORDERS = [(1, 0, 0), (2, 0, 0), (3, 0, 0), (1, 0, 1), (2, 0, 1)]


def keep_test_targets(rows: list[dict]) -> list[dict]:
    """Keep only forecasts whose target day lies inside the test window."""
    return [r for r in rows if config.TEST_START <= r["target_date"] <= config.TEST_END]


def persistence(daily_filled: pd.DataFrame, daily_raw: pd.DataFrame) -> list[dict]:
    rows = []
    for state in config.STATES:
        for origin in ORIGINS:
            for h in H:
                target = origin + pd.Timedelta(days=h)
                rows.append(dict(model="persistence", state=state, origin=origin,
                                 target_date=target, horizon=h,
                                 actual=daily_raw[state].get(target),
                                 pred=daily_filled[state].get(origin)))
    return keep_test_targets(rows)


def fit_best_arima(train: pd.Series):
    best = None
    for order in ORDERS:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")      # convergence chatter
            res = ARIMA(train, order=order, trend="c").fit()
        if best is None or res.aic < best.aic:
            best = res
    return best


def arima(daily_filled: pd.DataFrame, daily_raw: pd.DataFrame) -> tuple[list[dict], dict]:
    rows, chosen = [], {}
    for state in config.STATES:
        s = daily_filled[state]
        train = continuous_tail(s.loc[config.TRAIN_START:config.TRAIN_END])
        res = fit_best_arima(train)
        chosen[state] = (res.model.order, str(train.index.min().date()))

        for origin in ORIGINS:
            # Same fitted parameters, re-run over all data observed up to `origin`.
            history = continuous_tail(s.loc[train.index.min():origin])
            fc = res.apply(history).forecast(steps=max(H))
            for h in H:
                target = origin + pd.Timedelta(days=h)
                rows.append(dict(model="arima", state=state, origin=origin,
                                 target_date=target, horizon=h,
                                 actual=daily_raw[state].get(target),
                                 pred=float(fc.iloc[h - 1])))
        print(f"  {state:<16} order={res.model.order} trained from {chosen[state][1]}")
    return keep_test_targets(rows), chosen


def main() -> None:
    daily_raw = load_daily(fill=False)      # truth: never score against filled values
    daily_filled = load_daily(fill=True)    # input: what the models see

    print("Fitting ARIMA per state...")
    arima_rows, _ = arima(daily_filled, daily_raw)
    preds = pd.DataFrame(persistence(daily_filled, daily_raw) + arima_rows)
    preds = preds.dropna(subset=["actual", "pred"])

    config.OUT_DIR.mkdir(parents=True, exist_ok=True)
    preds.to_csv(config.OUT_DIR / "baseline_predictions.csv", index=False)

    overall = summarise(preds)
    per_state = (preds.assign(err=(preds["actual"] - preds["pred"]).abs())
                 .pivot_table(index="state", columns="model", values="err", aggfunc="mean")
                 .round(2))
    overall.to_csv(config.OUT_DIR / "baseline_mae.csv")
    per_state.to_csv(config.OUT_DIR / "baseline_mae_by_state.csv")

    print(f"\nTest window {config.TEST_START.date()} to {config.TEST_END.date()} "
          f"(trained up to {config.TRAIN_END.date()})")
    print("\nMAE (API points, lower is better):")
    print(overall.to_string())
    print("\nMAE by state, all horizons:")
    print(per_state.to_string())


if __name__ == "__main__":
    main()
