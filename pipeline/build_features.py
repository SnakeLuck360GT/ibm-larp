"""Step 2: build features.csv (one row per state x year, 2017 to the current year) from raw/.

Run from the repo root after fetch_raw.py:  .venv/bin/python pipeline/build_features.py

Replaces the feature cell in notebooks/03_equity_eda.ipynb (the notebook stays as EDA). Surveys
don't run every year, so each gap is filled and the *_source columns say how:
  HIES (income, poverty, gini)  every 2-3 years to 2024: linear between surveys, then carried
                                forward; carried income is scaled by the state's CPI.
  beds                          yearly to 2024, then carried forward.
  GDP                           yearly to 2025, then carried forward.
  unemployment, CPI             quarterly / monthly; the current year is year-to-date.
  PM2.5                         CAMS history starts Aug 2022, so full years from 2023 are observed,
                                earlier years get the 2023+ climatology and the current year is YTD.
Raw values only; 03_equity.py z-scores within year.
"""

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

RA = Path(__file__).resolve().parents[1] / "data" / "resource_allocation"
RAW = RA / "raw"

FIRST_YEAR = 2017  # first year of the quarterly labour force survey
REF_YEAR = datetime.now(ZoneInfo("Asia/Kuala_Lumpur")).year
YEARS = list(range(FIRST_YEAR, REF_YEAR + 1))
UNHEALTHY = 35.0  # ug/m3, MAAQS 2020 24 h PM2.5
FULL_YEAR_DAYS = 300  # days of PM2.5 needed to call a past year observed


def year_of(s: pd.Series) -> pd.Series:
    return s.astype(str).str[:4].astype(int)


def grid(states: list[str]) -> pd.MultiIndex:
    return pd.MultiIndex.from_product([states, YEARS], names=["state", "year"])


def population() -> pd.DataFrame:
    raw = pd.read_parquet(RAW / "population_state.parquet")
    raw["year"] = year_of(raw["date"])
    p = raw[(raw.sex == "both") & (raw.ethnicity == "overall")].copy()
    p["population"] *= 1000  # DOSM publishes thousands
    total = p[p.age == "overall"].set_index(["state", "year"])["population"]
    under5 = p[p.age == "0-4"].set_index(["state", "year"])["population"]
    # Bands above 65 change over time (70+, 80+, 85+...), but 0-4 ... 60-64 are stable.
    under65 = p[p.age.isin([f"{a}-{a + 4}" for a in range(0, 65, 5)])].groupby(["state", "year"])["population"].sum()
    return pd.DataFrame({"population": total, "pct_under5": under5 / total * 100,
                         "pct_65plus": (total - under65) / total * 100})


def fill(s: pd.Series, idx: pd.MultiIndex) -> tuple[pd.Series, pd.Series]:
    """Linear between observed years, carried forward after the last one. Returns (values, source)."""
    wide = s.dropna().unstack("state")  # year x state
    years = sorted(set(wide.index) | set(idx.get_level_values("year")))
    wide = wide.reindex(years)
    observed = wide.notna()
    inside = wide.interpolate(method="index", limit_area="inside")
    filled = inside.ffill()
    source = pd.DataFrame(np.where(observed, "observed", np.where(inside.notna(), "interpolated", "carried")),
                          index=wide.index, columns=wide.columns)
    long = lambda w: w.stack().reorder_levels(["state", "year"]).reindex(idx)  # noqa: E731
    out, src = long(filled), long(source)
    assert out.notna().all(), f"{s.name}: no data before {FIRST_YEAR} for some state"
    return out, src


def hies(idx: pd.MultiIndex) -> pd.DataFrame:
    """Income, poverty and gini, observed in survey years only. Pull in survey years before
    FIRST_YEAR so 2017-2018 interpolate from 2016."""
    inc = pd.read_csv(RAW / "household_income.csv").set_index(["state", "year"])
    pov = pd.read_parquet(RAW / "hh_poverty_state.parquet")
    pov["year"] = year_of(pov["date"])
    gini = pd.read_parquet(RAW / "hh_inequality_state.parquet")
    gini["year"] = year_of(gini["date"])
    obs = inc.join(pov.set_index(["state", "year"])["poverty_absolute"].rename("poverty_rate"), how="outer") \
             .join(gini.set_index(["state", "year"])["gini"], how="outer")
    obs = obs[obs.index.get_level_values("year") >= 2016]
    wide_idx = obs.index.union(idx)
    out = {}
    for col in ["income_median", "income_mean", "poverty_rate", "gini"]:
        out[col], src = fill(obs[col], wide_idx)
        out[col], src = out[col].reindex(idx), src.reindex(idx)
        if col == "income_median":  # gini skips 2020, so its 2020 is interpolated; flagged by income only
            out["hies_source"] = src
    df = pd.DataFrame(out)

    # Carried-forward income is nominal 2024 money; scale by the state's CPI so later years are in their own prices.
    cpi = cpi_by_year().reindex(idx)
    last_obs = obs["income_median"].dropna().reset_index().groupby("state")["year"].max()
    base = cpi.reindex(pd.MultiIndex.from_arrays([idx.get_level_values("state"),
                                                  last_obs.reindex(idx.get_level_values("state")).to_numpy()]))
    ratio = np.where(df["hies_source"] == "carried", cpi.to_numpy() / base.to_numpy(), 1.0)
    df["income_median"] *= ratio
    df["income_mean"] *= ratio
    df.loc[df["hies_source"] == "carried", "hies_source"] = "carried+cpi"
    return df


def cpi_by_year() -> pd.Series:
    c = pd.read_parquet(RAW / "cpi_2d_state.parquet")
    c = c[c.division == "overall"]
    c["year"] = year_of(c["date"])
    return c.groupby(["state", "year"])["index"].mean().rename("cpi")  # current year = YTD mean


def unemployment(idx: pd.MultiIndex) -> pd.DataFrame:
    lfs = pd.read_parquet(RAW / "lfs_qtr_state.parquet")
    lfs["year"] = year_of(lfs["date"])
    g = lfs.groupby(["state", "year"])
    df = pd.DataFrame({"u_rate": g["u_rate"].mean(), "quarters": g.size()}).reindex(idx)
    assert df["u_rate"].notna().all(), "labour force survey missing a state-year"
    df["u_rate_source"] = np.where(df["quarters"] < 4, "ytd", "observed")
    return df.drop(columns="quarters")


def beds(idx: pd.MultiIndex) -> pd.DataFrame:
    raw = pd.read_parquet(RAW / "hospital_beds.parquet")
    raw["year"] = year_of(raw["date"])
    # Each state has an 'All Districts' row; summing the districts as well would double-count.
    b = raw[(raw.district == "All Districts") & (raw.type == "all") & (raw.state != "Malaysia")]
    b = b.set_index(["state", "year"])["beds"]
    assert not b.index.duplicated().any()
    v, src = fill(b, idx)
    return pd.DataFrame({"beds": v, "beds_source": src})


def gdp(idx: pd.MultiIndex) -> pd.DataFrame:
    g = pd.read_parquet(RAW / "gdp_state_real_supply.parquet")
    g = g[(g.series == "abs") & (g.sector == "p0") & (g.state != "Supra")]  # Supra = offshore oil & gas
    g["year"] = year_of(g["date"])
    g = g.dropna(subset=["value"]).set_index(["state", "year"])["value"]
    # Putrajaya is only published from its own first year; before that DOSM counts it inside KL.
    # Back-fill its per-capita GDP from that first year (KL's earlier figures include Putrajaya).
    pop = population()["population"]
    pj = (g / pop.reindex(g.index)).loc["W.P. Putrajaya"]
    early = [y for y in YEARS if y < pj.index.min()]
    for y in early:
        g.loc[("W.P. Putrajaya", y)] = pj.iloc[0] * pop.loc[("W.P. Putrajaya", y)]
    g = g.sort_index()
    v, src = fill(g, idx)
    src.loc[[("W.P. Putrajaya", y) for y in early]] = "backfilled"
    return pd.DataFrame({"gdp_rm_mil": v, "gdp_source": src})


def pm25(states: list[str]) -> pd.DataFrame:
    pm = pd.read_csv(RAW / "pm25_daily_openmeteo.csv", parse_dates=["date"])
    pm["year"] = pm["date"].dt.year
    yearly = pm.groupby(["state", "year"])["pm25"].agg(
        pm25_mean="mean", unhealthy_days_per_yr=lambda s: (s > UNHEALTHY).mean() * 365, days="size")
    full = yearly[(yearly["days"] >= FULL_YEAR_DAYS) & (yearly.index.get_level_values("year") < REF_YEAR)]
    clim = full.groupby("state")[["pm25_mean", "unhealthy_days_per_yr"]].mean()

    rows = []
    for st in states:
        for yr in YEARS:
            if (st, yr) in full.index:
                r, src = full.loc[(st, yr)], "observed"
            elif yr == REF_YEAR:
                r, src = yearly.loc[(st, yr)], "ytd"
            else:
                r, src = clim.loc[st], "climatology"
            rows.append({"state": st, "year": yr, "pm25_mean": r["pm25_mean"],
                         "unhealthy_days_per_yr": r["unhealthy_days_per_yr"], "pm25_source": src})
    return pd.DataFrame(rows).set_index(["state", "year"])


def main() -> None:
    ref = pd.read_csv(RA / "state_ref.csv").set_index("state")
    states = sorted(ref.index)
    idx = grid(states)
    pop = population().reindex(idx)
    assert pop.notna().all().all(), "population missing a state-year"

    df = pd.concat([pop, hies(idx), unemployment(idx), beds(idx), gdp(idx), pm25(states)], axis=1)
    df["area_km2"] = ref["area_km2"].reindex(df.index.get_level_values("state")).to_numpy()
    df["income_gap"] = df.income_mean - df.income_median
    df["beds_per_1000"] = df.beds / df.population * 1000
    df["density"] = df.population / df.area_km2
    df["gdp_per_capita"] = df.gdp_rm_mil * 1e6 / df.population

    cols = ["population", "beds", "area_km2", "income_median", "income_mean", "income_gap", "poverty_rate", "gini",
            "u_rate", "gdp_per_capita", "beds_per_1000", "pct_65plus", "pct_under5", "density",
            "pm25_mean", "unhealthy_days_per_yr",
            "hies_source", "u_rate_source", "beds_source", "gdp_source", "pm25_source"]
    out = df[cols].reset_index().sort_values(["year", "state"])
    num = out.select_dtypes("number").columns
    out[num] = out[num].round(4)
    assert len(out) == 16 * len(YEARS) and out.notna().all().all()
    out.to_csv(RA / "features.csv", index=False)
    print(f"wrote features.csv {out.shape}, years {YEARS[0]}-{YEARS[-1]}")
    print(out.groupby("year")[["hies_source", "beds_source", "gdp_source", "u_rate_source", "pm25_source"]]
          .agg(lambda s: "/".join(sorted(set(s)))).to_string())


if __name__ == "__main__":
    main()
