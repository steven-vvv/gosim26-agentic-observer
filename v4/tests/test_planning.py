"""The grouped field search must preserve the legacy fiber merit exactly."""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

from v4.agent.observer4.geometry import parse_utc
from v4.agent.observer4.planning import Planner, expected_science_gains, fiber_totals, scarcity_multipliers, strategy_settings


def legacy_totals(fibers, gains, weight, count):
    total = np.zeros((len(fibers), len(gains)))
    for fiber in range(count):
        values = np.where(fibers[:, None, :] == fiber, gains[None, :, :] * weight[:, None, :], 0)
        total += np.max(values, axis=2)
    return total


@pytest.mark.parametrize("centers,durations,targets,count", [
    (1, 1, 1, 1), (4, 3, 1, 4), (8, 13, 35, 16), (16, 37, 180, 16),
])
def test_grouped_fiber_totals_match_legacy_search(centers, durations, targets, count):
    random = np.random.default_rng(4242 + targets)
    fibers = random.integers(-1, count, size=(centers, targets), dtype=np.int32)
    gains = random.normal(2, 3, size=(durations, targets))
    weight = random.choice([0.0, 0.45, 1.0], size=fibers.shape)
    weight[fibers < 0] = 0
    np.testing.assert_array_equal(fiber_totals(fibers, gains, weight, count),
                                  legacy_totals(fibers, gains, weight, count))


def test_grouped_fiber_totals_keep_negative_single_fiber_maximum():
    fibers = np.zeros((2, 3), dtype=np.int32)
    fibers[1, 1] = -1
    gains = np.array([[-3.0, -1.0, -2.0]])
    weight = np.ones_like(fibers, dtype=float)
    np.testing.assert_array_equal(fiber_totals(fibers, gains, weight, 1),
                                  legacy_totals(fibers, gains, weight, 1))


def test_expected_marginal_gain_uses_best_score_and_program_match_probability():
    factors = np.array([[0.5, 0.5], [1.0, 1.0]])
    weights = np.array([10.0, 10.0])
    best = np.array([5.5, 11.5])
    probability = np.array([[0.8, 0.8], [0.1, 0.1], [0.1, 0.1]])
    multipliers = np.array([1.2, 1.12, 1.06])
    gains = expected_science_gains(factors, weights, best, probability, multipliers, 1.0)
    assert gains.shape == (3, 2, 2)
    assert gains[0, 0, 0] == pytest.approx(0.8 * 0.5)
    assert gains[0, 1, 0] == pytest.approx(0.8 * 6.5 + 0.2 * 4.5)
    assert gains[0, 1, 1] == pytest.approx(0.8 * 0.5)
    assert gains[1, 1, 1] == 0


def test_scarcity_uses_only_remaining_public_visibility():
    visibility = np.array([[100.0, 20.0, 10.0, 2.0], [100.0, 20.0, 0.0, 0.0]])
    required = np.array([True, True, True, True])
    weights = scarcity_multipliers(visibility, required, 1.0, 0.5)
    np.testing.assert_allclose(weights[0], [1.0, 1.0, 1.75, 2.0])
    np.testing.assert_allclose(weights[1, :2], [1.0, 2.0])
    np.testing.assert_array_equal(scarcity_multipliers(visibility, required, 1.0, 0), np.ones_like(visibility))


def test_weekly_strategies_change_required_weights_and_steady_keeps_the_policy():
    policy = {"required_bonus": 200.0, "required_safe_factor": 0.52}
    assert strategy_settings(policy, "steady") == policy
    assert strategy_settings(policy, "required_push") == {"required_bonus": 260.0, "required_safe_factor": 0.55}
    assert strategy_settings(policy, "science_push") == {"required_bonus": 150.0, "required_safe_factor": 0.52}
    with pytest.raises(ValueError):
        strategy_settings(policy, "balanced")


@pytest.mark.parametrize("latitude", [-70.0, -24.627, 30.0, 70.0])
def test_vectorized_center_setting_time_matches_scalar(latitude):
    random = np.random.default_rng(108)
    planner = object.__new__(Planner)
    planner.task = type("Task", (), {"latitude": latitude, "minimum_altitude": 30.0})()
    alt = random.uniform(30.5, 89.8, 256)
    az = random.uniform(0, 360, 256)
    lst = 217.35
    expected = np.array([planner._center_up(float(a), float(z), lst) for a, z in zip(alt, az)])
    actual = planner._centers_up(alt, az, lst)
    np.testing.assert_allclose(actual, expected, rtol=0, atol=1e-8)


def test_request_gain_uses_public_progress_factor_and_whole_exposure_deadline():
    planner = object.__new__(Planner)
    planner.task = SimpleNamespace(index={"T0": 0, "T1": 1, "T2": 2}, minimum_exposure=120)
    planner.settings = {"request_weight": 1.0}
    now = parse_utc("2026-10-05T00:00:00Z").timestamp()
    planner.set_requests([{
        "target_ids": ["T0", "T1", "T2"], "completed_target_ids": ["T0"],
        "remaining_count": 1, "deadline_utc": "2026-10-05T00:05:00Z",
        "completion_factor_threshold": 0.5, "completion_reward": 100.0,
    }])
    terms = planner._request_terms(now)
    np.testing.assert_array_equal(terms[0]["indices"], [1, 2])
    assert terms[0]["bonus"] == 100.0
    durations = np.array([120, 300, 330])
    factors = np.array([[0.9, 0.51, 0.53], [0.9, 0.55, 0.6], [0.9, 0.7, 0.7]])
    gains = planner._request_gains(terms, np.array([0, 1, 2]), durations, factors, now)
    np.testing.assert_array_equal(gains, [[0, 0, 100], [0, 100, 100], [0, 0, 0]])
    assert planner._request_terms(now + 181) == []
    planner.settings["request_weight"] = 0.0
    assert planner._request_terms(now) == []


def test_scale_profile_only_changes_high_cost_cards():
    planner = object.__new__(Planner)
    planner.task = SimpleNamespace(ids=range(50_000), grid=SimpleNamespace(n=25))
    planner.config = {
        "scale": {
            "enabled": True,
            "cost_threshold": 800_000,
            "anchor_pool": 32,
            "anchors": 6,
            "cluster_anchors": 2,
            "refine_top": 1,
            "durations": [450, 600],
        }
    }
    base = {"anchor_pool": 64, "anchors": 12, "cluster_anchors": 4, "refine_top": 3,
            "durations": [120, 180, 900]}
    scaled = planner._scaled_settings(base)
    assert scaled is not base
    assert scaled["anchor_pool"] == 32
    assert scaled["anchors"] == 6
    assert scaled["cluster_anchors"] == 2
    assert scaled["refine_top"] == 1
    assert scaled["durations"] == [450, 600]
    assert base["durations"] == [120, 180, 900]

    planner.task = SimpleNamespace(ids=range(30_000), grid=SimpleNamespace(n=16))
    assert planner._scaled_settings(base) is base


def test_pacing_cuts_only_below_the_fair_share_floor():
    planner = object.__new__(Planner)
    nights = [SimpleNamespace(start=100.0 * k, end=100.0 * k + 50.0) for k in range(10)]
    planner.task = SimpleNamespace(nights=nights)
    planner.settings = {"anchors": 20, "budget_reserve_seconds": 5.0, "pace_budget": True, "pace_floor": 0.8}
    planner._pace_origin = None
    planner.paced_plans, planner.pace_headroom, planner.pace_headroom_at, planner.paced_nights = 0, None, None, []
    # Front-loaded spending is fine while the budget stays above 0.8 of its fair share.
    assert planner._paced_anchors(0.0, 0, 900.0, extra=4) == 24
    assert planner._paced_anchors(250.0, 2, 520.0, extra=4) == 24
    # Half the season left: the fair share is 437.5 usable seconds; 0.71 of it keeps 0.775.
    assert planner._paced_anchors(500.0, 5, 0.71 * 437.5 + 25.0, extra=4) == 18
    assert planner._paced_anchors(500.0, 5, 0.4 * 437.5 + 25.0, extra=4) == 2
    assert planner._paced_anchors(500.0, 5, 10.0, extra=4) == 2
    assert planner.paced_plans == 3
    planner.settings["pace_budget"] = False
    assert planner._paced_anchors(500.0, 5, 100.0, extra=4) == 24
