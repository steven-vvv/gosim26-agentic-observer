"""The grouped field search must preserve the legacy fiber merit exactly."""

from __future__ import annotations

import numpy as np
import pytest

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


def test_weekly_model_strategies_change_frozen_policy_but_fallback_preserves_it():
    policy = {"required_bonus": 200.0, "required_safe_factor": 0.52}
    assert strategy_settings(policy, "balanced") == policy
    assert strategy_settings(policy, "coverage_focus") == policy
    assert strategy_settings(policy, "balanced", model_selected=True) == {
        "required_bonus": 120.0, "required_safe_factor": 0.55,
    }
    assert strategy_settings(policy, "coverage_focus", model_selected=True) == policy


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
