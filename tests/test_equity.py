"""Equity layer checks. Run from the repo root after the pipeline:

    .venv/bin/python data/resource_allocation/build_features.py
    .venv/bin/python pipeline/03_equity.py && .venv/bin/python -m pytest tests/test_equity.py
"""

import importlib.util
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
RA = ROOT / "data" / "resource_allocation"

STATES = {
    "Johor", "Kedah", "Kelantan", "Melaka", "Negeri Sembilan", "Pahang", "Perak", "Perlis",
    "Pulau Pinang", "Sabah", "Sarawak", "Selangor", "Terengganu",
    "W.P. Kuala Lumpur", "W.P. Labuan", "W.P. Putrajaya",
}

# pipeline/03_equity.py starts with a digit, so it can't be imported by name.
_spec = importlib.util.spec_from_file_location("equity", ROOT / "pipeline" / "03_equity.py")
equity = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(equity)


@pytest.fixture(scope="module")
def features():
    return equity.load_features()


@pytest.fixture(scope="module")
def out():
    return json.loads((ROOT / "data" / "out" / "states.json").read_text())


# --- data -----------------------------------------------------------------------------------------
def test_features_cover_all_states_every_year(features):
    for yr, g in features.groupby("year"):
        assert set(g["state"]) == STATES, yr
        assert len(g) == 16, yr


def test_features_have_no_nans(features):
    cols = equity.FEATURES + equity.BASE_FEATURES + ["population"]
    assert features[cols].notna().all().all()


def test_current_year_is_flagged_as_estimated(features):
    cur = features[features["year"] == features["year"].max()]
    assert (cur["hies_source"] == "carried+cpi").all()  # no HIES for the current year yet
    assert (cur["pm25_source"] == "ytd").all()
    assert cur["u_rate_source"].isin({"ytd", "observed"}).all()
    past = features[features["year"] < features["year"].max()]
    assert past["pm25_source"].isin({"observed", "climatology"}).all()
    assert (past.loc[past["year"] >= 2023, "pm25_source"] == "observed").all()


def test_household_income_has_median_and_mean_for_every_state():
    inc = pd.read_csv(RA / "raw" / "household_income.csv")
    assert {"state", "year", "income_median", "income_mean"} <= set(inc.columns)
    assert set(inc["state"]) == STATES
    assert inc[["income_median", "income_mean"]].notna().all().all()
    assert (inc["income_mean"] >= inc["income_median"]).mean() > 0.9  # income is right-skewed


# --- allocation -------------------------------------------------------------------------------------
def test_allocate_n95_never_exceeds_stock_and_skips_ineligible():
    need = np.array([500_000, 200_000, 900_000, 50_000])
    eligible = np.array([True, True, True, False])
    got = equity.allocate_n95(need, eligible, 300_000, 1)
    assert got.sum() == 300_000
    assert got[3] == 0
    # proportional: the state with the most vulnerable people gets the most
    assert got[2] > got[0] > got[1]


def test_allocate_n95_caps_at_need_when_stock_is_plentiful():
    need = np.array([1000, 500])
    got = equity.allocate_n95(need, np.array([True, True]), 10_000, 1)
    assert list(got) == [1000, 500]


def test_allocate_n95_leftover_goes_to_most_urgent():
    got = equity.allocate_n95(np.array([100, 100, 100]), np.array([True, True, True]), 10, 1)
    assert got.sum() == 10 and got[0] == 4


# --- forecast windows ----------------------------------------------------------------------------
def test_exposure_windows_split_72h_into_three_days():
    fc = pd.DataFrame({"state": ["A"] * 72 + ["B"] * 72, "hour": list(range(72)) * 2,
                       "pm25": [10.0] * 24 + [40.0] * 24 + [60.0] * 24 + [5.0] * 72, "issued_at": "x"})
    w = equity.exposure_windows(fc)
    assert w["24"].at["A", "pm25_mean"] == 10 and w["48"].at["A", "pm25_mean"] == 40
    assert w["72"].at["A", "pm25_mean"] == 60
    assert w["24"].at["A", "unhealthy_days_per_yr"] == 0 and w["48"].at["A", "unhealthy_days_per_yr"] == 365
    assert w[equity.ALLOC].at["A", "pm25_mean"] == pytest.approx(110 / 3)
    assert w[equity.ALLOC].at["A", "unhealthy_days_per_yr"] == pytest.approx(365 * 2 / 3)
    assert w[equity.ALLOC].at["B", "unhealthy_days_per_yr"] == 0


def test_pm25_spike_raises_priority(features):
    model, _ = equity.load_trained()
    cfg = equity.load_config()
    _, cur = equity.split_years(features)
    ytd = cur.set_index("state")[equity.EXPOSURE]
    before = equity.rank_scenario(cur, ytd, model, cfg).set_index("state")["priority"]
    state = before[before == 8].index[0]
    spiked = ytd.copy()
    spiked.loc[state] = [ytd["pm25_mean"].max() * 3, 365.0]
    after = equity.rank_scenario(cur, spiked, model, cfg).set_index("state")["priority"]
    assert after[state] < before[state], state


# --- states.json ------------------------------------------------------------------------------------
STATE_KEYS = {"state", "lat", "lon", "population", "income_median", "income_mean", "poverty_rate",
              "beds_per_1000", "u_rate", "gini", "pm25", "pm25_ytd", "risk_score", "priority",
              "priority_by_horizon", "allocation", "action"}


def test_output_format(out):
    assert {"generated_at", "reference_year", "states", "stock", "model", "assumptions"} <= set(out)
    assert len(out["states"]) == 16
    for s in out["states"]:
        assert STATE_KEYS <= set(s), s["state"]
        assert set(s["pm25"]) == {"24", "48", "72"}
        assert all(isinstance(v, (int, float)) and math.isfinite(v) for v in s["pm25"].values())
        assert set(s["allocation"]) >= {"mobile_clinic", "n95"}
        assert isinstance(s["allocation"]["mobile_clinic"], bool)
        assert isinstance(s["allocation"]["n95"], int) and s["allocation"]["n95"] >= 0
        assert 0 <= s["risk_score"] <= 1
        assert -90 <= s["lat"] <= 90 and -180 <= s["lon"] <= 180
        assert s["population"] > 0
    assert {"mobile_clinics", "n95"} <= set(out["stock"])
    assert out["model"]["type"] == "XGBRanker"
    for a in out["assumptions"]:
        assert set(a) >= {"name", "value", "unit", "source"}
        assert a["name"] and a["value"] and a["source"]


def test_all_states_present(out):
    assert {s["state"] for s in out["states"]} == STATES


def test_priorities_unique_1_to_16(out):
    assert sorted(s["priority"] for s in out["states"]) == list(range(1, 17))


def test_priority_by_horizon_unique_1_to_16(out):
    for hz in ("24", "48", "72"):
        assert sorted(s["priority_by_horizon"][hz] for s in out["states"]) == list(range(1, 17)), hz


def test_current_year_ranked_but_not_trained(out, features):
    assert out["reference_year"] == features["year"].max()
    trained = out["model"]["trained_years"]
    assert out["reference_year"] not in trained and max(trained) == out["reference_year"] - 1
    metrics = json.loads((RA / "ranker_metrics.json").read_text())
    assert str(out["reference_year"]) not in metrics["per_year"]


def test_risk_score_follows_priority(out):
    by_priority = sorted(out["states"], key=lambda s: s["priority"])
    scores = [s["risk_score"] for s in by_priority]
    assert scores == sorted(scores, reverse=True)
    assert scores[0] == 1 and scores[-1] == 0


def test_allocations_within_stock(out):
    stock = out["stock"]
    assert sum(s["allocation"]["mobile_clinic"] for s in out["states"]) <= stock["mobile_clinics"]
    assert sum(s["allocation"]["n95"] for s in out["states"]) <= stock["n95"]


def test_clinics_go_to_top_priorities(out):
    n = out["stock"]["mobile_clinics"]
    for s in out["states"]:
        assert s["allocation"]["mobile_clinic"] == (s["priority"] <= n)


def test_actions_follow_priority(out):
    for s in out["states"]:
        p = s["priority"]
        # Deploy covers exactly the states that get a clinic, so the action never contradicts the allocation.
        want = ("Deploy mobile clinic + distribute N95s" if p <= out["stock"]["mobile_clinics"] else
                "Pre-position N95s, issue advisory" if p <= 10 else "Monitor")
        assert s["action"] == want, s["state"]
        if want == "Monitor":
            assert s["allocation"]["n95"] == 0


def test_ndcg_filled(out):
    m = out["model"]
    for key in ("ndcg@5", "baseline_ndcg@5"):
        assert isinstance(m[key], float) and 0 < m[key] <= 1, key


def test_assumptions_cover_config_and_credit_opendosm(out):
    text = json.dumps(out["assumptions"])
    assert "OpenDOSM" in text and "CC BY 4.0" in text
    assert "PROXY" in text
    for name in ("Mobile clinic stock", "N95 stock", "Baseline weights", "Vulnerable people weights",
                 "Priority label (PROXY)", "Clinic rule", "N95 rule", "Actions"):
        assert any(a["name"] == name for a in out["assumptions"]), name
