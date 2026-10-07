"""The merit bound never drops an exposure length that could enter the finalists."""

from __future__ import annotations

import numpy as np
import pytest

from v4.agent.observer4.bound import surviving_durations
from v4.agent.observer4.fibers import fiber_totals
from v4.agent.observer4.planning import science_value

FIBERS = 16


def _field(random, lengths: int, targets: int, centers: int, negative: bool):
    durations = np.sort(random.choice(np.arange(60, 3601, 30), size=lengths, replace=False)).astype(np.int32)
    gains = random.exponential(1.0, size=(lengths, targets)) * (random.random((lengths, targets)) < 0.9)
    gains *= random.choice([0.01, 1.0, 40.0], size=targets)
    if negative:
        gains -= 0.8 * (random.random((lengths, targets)) < 0.1) * random.exponential(1.0, size=(lengths, targets))
    fibers = random.integers(-1, FIBERS, size=(centers, targets))
    weight = np.where(fibers >= 0, 1.0, 0.0) * np.where(random.random((centers, targets)) < 0.3, 0.45, 1.0)
    return durations, gains, fibers, weight


def _merit(durations, gains, fibers, weight, overhead):
    total = fiber_totals(fibers, gains, weight, FIBERS)
    return total / (durations[None, :] + overhead)


@pytest.mark.parametrize("negative", [False, True])
@pytest.mark.parametrize("overhead", [0.0, 25.0])
def test_dropped_lengths_never_exceed_the_floor(negative, overhead):
    random = np.random.default_rng(5)
    dropped_any = False
    for trial in range(60):
        lengths, targets = int(random.integers(1, 40)), int(random.integers(1, 90))
        durations, gains, fibers, weight = _field(random, lengths, targets, int(random.integers(1, 12)), negative)
        merit = _merit(durations, gains, fibers, weight, overhead)
        best = merit.max(axis=0)
        for floor in (0.0, float(np.quantile(best, 0.5)), float(best.max()), float(best.max()) * 0.999,
                      float(best.max()) * 1.5, float(random.uniform(0, 30))):
            alive = surviving_durations(gains, durations, overhead, FIBERS, floor)
            kept = np.arange(lengths) if alive is None else alive
            dropped = np.setdiff1d(np.arange(lengths), kept)
            dropped_any |= bool(len(dropped))
            assert np.all(best[dropped] <= floor), (trial, floor)
            # Every length that beats the floor is kept, so the planner's winner above the floor is unchanged.
            assert np.all(np.isin(np.flatnonzero(best > floor), kept))


def test_some_lengths_are_dropped_when_the_floor_is_high():
    random = np.random.default_rng(8)
    durations, gains, fibers, weight = _field(random, 30, 80, 6, False)
    merit = _merit(durations, gains, fibers, weight, 0.0)
    alive = surviving_durations(gains, durations, 0.0, FIBERS, float(np.quantile(merit.max(axis=0), 0.9)))
    assert alive is not None and len(alive) < len(durations)


def test_winner_above_the_floor_is_the_same_after_the_cut():
    random = np.random.default_rng(13)
    for _ in range(40):
        durations, gains, fibers, weight = _field(random, int(random.integers(2, 30)), int(random.integers(2, 80)),
                                                  int(random.integers(1, 10)), True)
        merit = _merit(durations, gains, fibers, weight, 0.0)
        floor = float(np.quantile(merit, random.uniform(0.2, 0.95)))
        alive = surviving_durations(gains, durations, 0.0, FIBERS, floor)
        if alive is None:
            continue
        if not len(alive):
            assert merit.max() <= floor
            continue
        cut = _merit(durations[alive], gains[alive], fibers, weight, 0.0)
        # Columns are independent, so each kept column holds the same values as before.
        assert cut.tobytes() == merit[:, alive].tobytes()
        full_i = np.unravel_index(int(np.argmax(merit)), merit.shape)
        cut_i = np.unravel_index(int(np.argmax(cut)), cut.shape)
        if merit[full_i] > floor:
            assert (full_i[0], int(durations[full_i[1]])) == (cut_i[0], int(durations[alive][cut_i[1]]))
        else:
            assert cut.max() <= floor


def test_unusable_gains_never_drop_a_length():
    gains = np.array([[1.0, np.nan, 2.0], [1.0, 1.0, 2.0], [np.inf, 0.0, 0.0]])
    durations = np.array([60, 90, 120], dtype=np.int32)
    alive = surviving_durations(gains, durations, 0.0, 2, 1e9)
    assert alive is not None and 0 in alive and 2 in alive and 1 not in alive
    assert surviving_durations(np.zeros((3, 0)), durations, 0.0, 2, 1.0) is None
    assert surviving_durations(np.zeros((0, 4)), durations[:0], 0.0, 2, 1.0) is None


def test_fewer_targets_than_fibres_bounds_by_their_sum():
    # Only two targets, so at most two fibres score: the sum of all gains bounds every length,
    # and the length whose sum falls short of the floor is dropped even though it has more targets
    # than the field has fibres fewer than ``FIBERS``.
    gains = np.array([[1.0, 2.0], [10.0, 20.0]])
    durations = np.array([100, 100], dtype=np.int32)
    alive = surviving_durations(gains, durations, 0.0, FIBERS, 0.25)
    assert alive is not None and list(alive) == [1]
    # A floor above both sums drops every length; the caller then skips the field.
    assert list(surviving_durations(gains, durations, 0.0, FIBERS, 0.4)) == []


def _old_science_value(factor, exponent, cap):
    ratio = np.minimum(factor / cap, 1.0)
    return np.where(factor >= cap, factor, cap * ratio ** exponent)


@pytest.mark.parametrize("shape_exponent", ["array", "scalar"])
def test_capped_science_value_is_bitwise_the_full_evaluation(shape_exponent):
    random = np.random.default_rng(21)
    for _ in range(80):
        targets = int(random.integers(1, 300))
        factor = np.minimum(1.0, random.uniform(0.0, 1.6, size=(int(random.integers(1, 30)), targets)))
        factor[:, : min(3, targets)] = 1.0
        cap = np.clip(random.uniform(0.05, 1.3, size=targets), 0.05, 1.0)
        # factors landing exactly on and just below the cap
        factor[0, :] = np.where(random.random(targets) < 0.4, cap, factor[0, :])
        factor[-1, :] = np.where(random.random(targets) < 0.4, np.nextafter(cap, 0.0), factor[-1, :])
        exponent = 1.0 + 0.9 * random.random(targets) if shape_exponent == "array" else 1.8
        expected = _old_science_value(factor, exponent, cap)
        assert science_value(factor, exponent, cap).tobytes() == expected.tobytes()
