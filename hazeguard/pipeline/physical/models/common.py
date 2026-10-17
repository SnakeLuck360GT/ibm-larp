"""
Helpers shared by every model file: load the daily state series and score forecasts.
"""

import pandas as pd

from pipeline.physical import config


def load_daily(fill: bool = True) -> pd.DataFrame:
    """Daily mean API per state.

    Returns a table with one row per day and one column per state.
    With fill=True, gaps of up to MAX_FILL_GAP_DAYS are interpolated;
    longer gaps stay empty (NaN).
    """
    hourly = pd.read_csv(config.STATE_API, parse_dates=["time"])
    daily = (
        hourly.assign(date=hourly["time"].dt.floor("D"))
        .groupby(["date", "state"])["api"].mean()
        .unstack("state")
        .asfreq("D")                     # make missing days explicit rows
    )[config.STATES]
    if fill:
        daily = daily.interpolate(limit=config.MAX_FILL_GAP_DAYS, limit_area="inside")
    return daily


def continuous_tail(series: pd.Series) -> pd.Series:
    """The most recent stretch of the series with no missing days.

    ARIMA needs an unbroken series, so for a state like Selangor
    (no data in 2017) we train only on the part after the gap.
    """
    missing = series.isna()
    if not missing.any():
        return series
    last_gap = missing[missing].index.max()
    return series.loc[last_gap + pd.Timedelta(days=1):]


def mae(actual: pd.Series, pred: pd.Series) -> float:
    return float((actual - pred).abs().mean())


def summarise(preds: pd.DataFrame) -> pd.DataFrame:
    """MAE per model and horizon, from a predictions table with columns
    model, horizon, actual, pred."""
    table = (
        preds.groupby(["model", "horizon"])
        .apply(lambda g: mae(g["actual"], g["pred"]), include_groups=False)
        .unstack("horizon")
    )
    table.columns = [f"mae_{h * 24}h" for h in table.columns]
    return table.round(2)
