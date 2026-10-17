"""Tests for the damage-cost model in pipeline/04_policy.py.

Run from the repo root:
    pytest tests/test_damage.py -v
"""
import importlib.util
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]

# 04_policy.py starts with a digit, so it can't be imported normally — load it by path.
_spec = importlib.util.spec_from_file_location("policy", ROOT / "pipeline" / "04_policy.py")
policy = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(policy)

HORIZONS = ("24", "48", "72")


@pytest.fixture(scope="module")
def inputs():
    ref, haze, truth, _ = policy.load_inputs()
    return ref, haze, truth


@pytest.fixture(scope="module")
def result():
    # 50 draws keeps the test fast; the real run uses 1000.
    return policy.run(n_sims=50, write=False)


# ---------------------------------------------------------------- inputs
def test_all_16_states_present(inputs):
    ref, haze, _ = inputs
    assert set(ref.state) == set(policy.STATE_NAMES)
    assert set(haze.state) == set(policy.STATE_NAMES)
    assert len(policy.STATE_NAMES) == 16


def test_no_missing_values(inputs):
    ref, haze, _ = inputs
    assert not ref.isna().any().any(), ref.columns[ref.isna().any()].tolist()
    assert not haze.isna().any().any(), haze.columns[haze.isna().any()].tolist()


def test_state_names_match_states_json():
    if not policy.STATES_JSON.exists():
        pytest.skip("data/out/states.json not produced yet (Resource Allocation team)")
    theirs = {s["state"] for s in json.loads(policy.STATES_JSON.read_text())["states"]}
    assert theirs == set(policy.STATE_NAMES)


# ---------------------------------------------------------------- output
def test_output_format(result):
    assert set(result) >= {"episode", "national", "states", "model", "assumptions"}
    assert set(result["national"]) >= {"total_damage_rm", "by_category_rm", "ci90_rm"}
    keys = {"state", "lat", "lon", "population", "damage_to_date_rm", "by_category_rm",
            "damage_pct_gdp", "forecast_damage_rm", "api_latest"}
    for s in result["states"]:
        assert keys <= set(s), keys - set(s)
        assert set(s["by_category_rm"]) == set(policy.CATEGORIES)


def test_costs_non_negative_and_categories_add_up(result):
    for s in result["states"]:
        assert all(v >= 0 for v in s["by_category_rm"].values()), s["state"]
        assert abs(sum(s["by_category_rm"].values()) - s["damage_to_date_rm"]) <= len(policy.CATEGORIES), s["state"]


def test_states_sum_to_national(result):
    total = sum(s["damage_to_date_rm"] for s in result["states"])
    assert total == pytest.approx(result["national"]["total_damage_rm"], abs=len(result["states"]))
    for cat, v in result["national"]["by_category_rm"].items():
        assert sum(s["by_category_rm"][cat] for s in result["states"]) == pytest.approx(v, abs=len(result["states"]))


def test_total_inside_ci90(result):
    lo, hi = result["national"]["ci90_rm"]
    assert lo <= result["national"]["total_damage_rm"] <= hi
    assert lo < hi


def test_forecast_horizons(result):
    for s in result["states"]:
        assert set(s["forecast_damage_rm"]) == set(HORIZONS)
        assert all(v >= 0 for v in s["forecast_damage_rm"].values())


def test_every_assumption_has_a_source(result):
    for a in result["assumptions"]:
        assert a["name"] and str(a["source"]).strip(), a


def test_dummy_values_are_flagged(result):
    """Nothing dummy may pass as real: every coefficient still marked DUMMY must say so in the output."""
    dummy = {k for k, v in policy.COEFFS.items() if "DUMMY" in v["source"]}
    out = {a["name"] for a in result["assumptions"] if "DUMMY" in str(a["source"])}
    assert dummy <= out


def test_model_scores_filled(result):
    m = result["model"]
    assert m["backtest_mape"] is not None and m["baseline_mape"] is not None
    assert len(m["sensitivity_top5"]) == 5


# ---------------------------------------------------------------- behaviour
def _one_day(ref, pm25, api):
    """One synthetic day for every state at a fixed PM2.5 / API."""
    return pd.DataFrame({
        "date": "2026-01-01", "state": ref.state, "pm25_ugm3": pm25, "api_max": api,
        "school_closed": int(api > 200), "school_closed_share": 0.5 if api > 200 else 0.0,
        "flights_disrupted": 0, "cloud_seeding_ops": 0,
    })


def test_clean_air_costs_nothing(inputs):
    ref, _, _ = inputs
    d = policy.damage_by_day(_one_day(ref, pm25=10.0, api=40.0), ref, policy.central())
    assert d.total_damage_rm.sum() == pytest.approx(0, abs=1)


def test_damage_rises_with_pm25(inputs):
    ref, _, _ = inputs
    totals = [policy.damage_by_day(_one_day(ref, pm25=p, api=p * 1.6), ref, policy.central()).total_damage_rm.sum()
              for p in (20, 40, 60, 90, 120, 180)]
    assert all(np.diff(totals) > 0), totals


def test_mortality_excluded(result):
    assert result["national"]["by_category_rm"]["mortality"] == 0
    assert result["model"]["mortality_included"] is False


def test_daily_damage_near_benchmark(result):
    """Within 0.5x–2x of the published RM 20–50 m/day estimate for the 2026 haze."""
    lo, hi = result["model"]["benchmark_rm_per_day"]
    assert lo / 2 <= result["national"]["damage_per_day_rm"] <= hi * 2


def test_mock_is_reproducible():
    a, b = policy.build_mock(seed=42), policy.build_mock(seed=42)
    pd.testing.assert_frame_equal(a["haze_daily"], b["haze_daily"])


def test_writes_json(tmp_path, monkeypatch):
    out = tmp_path / "damage.json"
    monkeypatch.setattr(policy, "OUT_PATH", out)
    policy.run(n_sims=5, write=True)
    assert json.loads(out.read_text())["states"]
