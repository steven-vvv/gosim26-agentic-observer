"""The per-plan candidate durations equal the per-field evaluation of every neighbour."""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

from v4.agent.observer4.durations import Durations


def reference(task, settings, rate, required, left, request_thresholds):
    """The evaluation the planner made for each field before the steps were shared."""
    low, high = task.minimum_exposure, min(task.maximum_exposure, int(left))
    values = {int(x) for x in [*settings["durations"], high] if low <= x <= high}
    if settings["adaptive_durations"]:
        thresholds = np.where(required, settings["required_safe_factor"], 1.0)
        candidates = [thresholds / np.maximum(rate, 1e-9)]
        requested = request_thresholds > 0
        candidates.append(request_thresholds[requested] / np.maximum(rate[requested], 1e-9))
        steps = np.ceil(np.concatenate(candidates) / 30).astype(np.int64) * 30
        values.update(np.unique(steps[(steps >= low) & (steps <= high)]).tolist())
    return np.array(sorted(values), dtype=np.int32)


@pytest.mark.parametrize("adaptive", [True, False])
@pytest.mark.parametrize("left", [100.0, 700.4, 3600.0, 9000.0])
def test_candidates_equal_the_per_field_evaluation(adaptive, left):
    random = np.random.default_rng(31)
    count = 4000
    task = SimpleNamespace(minimum_exposure=60, maximum_exposure=3600)
    settings = {"durations": [120, 300, 600, 1800, 3600], "adaptive_durations": adaptive, "required_safe_factor": 0.52}
    rate = random.uniform(0.0, 0.02, count) * (random.random(count) < 0.9)
    required = random.random(count) < 0.3
    request_thresholds = np.where(random.random(count) < 0.05, random.uniform(0.3, 1.0, count), 0.0)
    subset = np.flatnonzero(random.random(count) < 0.6)
    candidates = Durations(task, settings, left, rate, required, request_thresholds, subset)
    for _ in range(25):
        near = np.sort(random.choice(subset, size=int(random.integers(1, 300)), replace=False)).astype(np.int32)
        np.testing.assert_array_equal(candidates(near), reference(task, settings, rate[near], required[near], left,
                                                                  request_thresholds[near]))
    empty = subset[:0].astype(np.int32)
    np.testing.assert_array_equal(candidates(empty), reference(task, settings, rate[empty], required[empty], left,
                                                               request_thresholds[empty]))
