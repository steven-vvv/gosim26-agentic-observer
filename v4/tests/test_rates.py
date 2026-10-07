"""The lazily evaluated rate profile equals the eager evaluation of every row, bit for bit."""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

from v4.agent.observer4.geometry import HorizonFactors, airmass, altaz, equatorial_vectors, lunar_factors, sidereal
from v4.agent.observer4.rates import OFFSETS, RateProfile

LATITUDE, LONGITUDE = -33.9249, 18.4241
MODEL = {"angular_decay_scale_deg": 35.0, "altitude_exponent": 1.0, "maximum_penalty": 0.75}


def make_task(count, model):
    random = np.random.default_rng(21)
    ra, dec = random.uniform(0, 360, count), np.degrees(np.arcsin(random.uniform(-1, 1, count)))
    scoring = {"q0": 0.68, "airmass_exponent": 0.6, "lunar_model": model}
    return SimpleNamespace(
        ids=list(range(count)), ra=ra, dec=dec, vectors=equatorial_vectors(ra, dec), flux=random.uniform(0.05, 2, count),
        horizon=HorizonFactors(ra, dec, LATITUDE), latitude=LATITUDE, longitude=LONGITUDE, scoring=scoring)


def eager(task, now, altitude, subset, horizon, scale, safety, directional, zero_point):
    """The full-catalogue evaluation the planner used before the rows were lazy."""
    rows = np.ones((len(OFFSETS), len(task.ids)))
    vectors = task.vectors[subset]
    exponent = task.scoring["airmass_exponent"]
    for j, offset in enumerate(OFFSETS):
        lunar = lunar_factors(None, None, now + max(offset, 60.0), task.latitude, task.longitude,
                              task.scoring.get("lunar_model"), vectors=vectors)
        alt = altitude[subset] if j == 0 else horizon.altitude(sidereal(now + offset, task.longitude))
        rows[j, subset] = lunar / (task.scoring["q0"] * airmass(np.maximum(alt, 1.0)) ** exponent)
    return rows, task.flux * rows * scale * directional * safety / zero_point


@pytest.mark.parametrize("model", [MODEL, None])
def test_lazy_rows_match_the_eager_evaluation(model):
    task = make_task(6000, model)
    now = 1.8e9 + 4321.0
    lst = sidereal(now, LONGITUDE)
    ha = (lst - task.ra + 180.0) % 360.0 - 180.0
    altitude, _ = altaz(task.horizon.vectors(lst, ha))
    subset = np.flatnonzero(altitude > 20.0)
    horizon = task.horizon.take(subset)
    random = np.random.default_rng(5)
    directional = random.uniform(0.2, 1.0, len(task.ids))
    scale, safety, zero_point = 0.71, 0.88, 800.0
    rows, rates = eager(task, now, altitude, subset, horizon, scale, safety, directional, zero_point)
    profile = RateProfile(task, now, altitude, subset, horizon, scale, safety, directional, zero_point)
    np.testing.assert_array_equal(profile.k, rows[1])
    np.testing.assert_array_equal(profile.offsets, np.array(OFFSETS))
    for _ in range(6):
        indices = random.choice(subset, size=int(random.integers(1, 400)), replace=False).astype(np.int32)
        np.testing.assert_array_equal(profile[:, indices], rates[:, indices])
    everything = subset.astype(np.int32)
    np.testing.assert_array_equal(profile[:, everything], rates[:, everything])
    np.testing.assert_array_equal(profile[:, everything[:0]], rates[:, everything[:0]])


def test_prepared_batch_with_repeats_equals_the_eager_evaluation():
    task = make_task(4000, MODEL)
    now = 1.8e9 + 99.0
    lst = sidereal(now, LONGITUDE)
    ha = (lst - task.ra + 180.0) % 360.0 - 180.0
    altitude, _ = altaz(task.horizon.vectors(lst, ha))
    subset = np.flatnonzero(altitude > 10.0)
    horizon = task.horizon.take(subset)
    directional = np.random.default_rng(1).uniform(0.2, 1.0, len(task.ids))
    _, rates = eager(task, now, altitude, subset, horizon, 0.6, 0.9, directional, 700.0)
    profile = RateProfile(task, now, altitude, subset, horizon, 0.6, 0.9, directional, 700.0)
    random = np.random.default_rng(8)
    union = np.concatenate([random.choice(subset, size=300, replace=False) for _ in range(5)]).astype(np.int32)
    profile.prepare(union)
    assert profile.have[union].all() and profile.have.sum() == len(np.unique(union))
    np.testing.assert_array_equal(profile[:, union], rates[:, union])
    later = np.setdiff1d(subset, union)[:50].astype(np.int32)
    np.testing.assert_array_equal(profile[:, np.concatenate((union[:20], later))], rates[:, np.concatenate((union[:20], later))])
