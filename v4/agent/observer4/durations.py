"""Candidate exposure lengths of one plan."""

from __future__ import annotations

import numpy as np

STEP = 30


class Durations:
    """The exposure lengths a field may use: the fixed ones, plus the 30-second step at which
    each neighbour reaches its completion threshold (and its request threshold).

    The steps are elementwise in the rate and the thresholds of a target, so they are formed
    once for the visible targets; a field only gathers those of its neighbours.
    """

    def __init__(self, task, settings: dict, left: float, rate, incomplete, request_thresholds, subset,
                 open_factor=None):
        self.low, self.high = task.minimum_exposure, min(task.maximum_exposure, int(left))
        self.fixed = {int(x) for x in [*settings["durations"], self.high] if self.low <= x <= self.high}
        self.steps = None
        self.completing = None
        if open_factor is not None:
            # With the revisit increment the exposure that just completes a partly scored
            # target is a candidate of its own (``open_factor`` holds the missing factor).
            completing = np.zeros(len(rate), dtype=np.int64)
            missing = open_factor[subset]
            completing[subset] = np.ceil(missing / np.maximum(rate[subset], 1e-9) / STEP).astype(np.int64) * STEP
            self.completing = completing
        if settings["adaptive_durations"]:
            count = len(rate)
            own, asked = np.full(count, -1, dtype=np.int64), np.full(count, -1, dtype=np.int64)
            thresholds = np.where(incomplete[subset], settings["required_safe_factor"], 1.0)
            own[subset] = np.ceil(thresholds / np.maximum(rate[subset], 1e-9) / STEP).astype(np.int64) * STEP
            requested = np.flatnonzero(request_thresholds > 0)
            asked[requested] = np.ceil(request_thresholds[requested] / np.maximum(rate[requested], 1e-9)
                                       / STEP).astype(np.int64) * STEP
            self.steps = own, asked

    def __call__(self, near) -> np.ndarray:
        values = self.fixed
        if self.steps is not None:
            steps = np.concatenate((self.steps[0][near], self.steps[1][near]))
            values = values | set(np.unique(steps[(steps >= self.low) & (steps <= self.high)]).tolist())
        if self.completing is not None:
            steps = self.completing[near]
            values = values | set(np.unique(steps[(steps >= self.low) & (steps <= self.high)]).tolist())
        return np.array(sorted(values), dtype=np.int32)
