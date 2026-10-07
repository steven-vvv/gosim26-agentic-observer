"""Evaluating the sky of the visible targets only gives the whole-catalogue values for those targets."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from v4.agent.observer4.config import load
from v4.agent.observer4.geometry import HorizonFactors, altaz, sidereal, wrap180
from v4.agent.observer4.quality import QualityModel
from v4.agent.observer4.sky import program_odds, visible_sky
from v4.agent.observer4.terrain import TerrainModel

CONFIG = Path(__file__).resolve().parents[1] / "agent/config.json"
LATITUDE, LONGITUDE = -24.6, 30.0


def build(count=5000, terrain=True, notices=()):
    random = np.random.default_rng(9)
    ra, dec = random.uniform(0, 360, count), np.degrees(np.arcsin(random.uniform(-1, 1, count)))
    task = SimpleNamespace(ids=list(range(count)), ra=ra, dec=dec, horizon=HorizonFactors(ra, dec, LATITUDE),
                           nights=[], scoring={"program": {"bands": {"DARK": 0.65, "BRIGHT": 0.4}}})
    quality = QualityModel(task, load(CONFIG))
    quality.notices = set(notices)
    if terrain:
        quality.terrain = {"E", "SW"}
        quality.terrain_model = TerrainModel(quality.terrain, quality.settings)
    return task, quality


@pytest.mark.parametrize("terrain,notices", [(False, ()), (True, ()), (True, (("rain", "S"), ("haze", "NE")))])
def test_visible_sky_equals_the_full_evaluation_on_the_visible_targets(terrain, notices):
    task, quality = build(terrain=terrain, notices=notices)
    lst = sidereal(1.8e9 + 777.0, LONGITUDE)
    ha = wrap180(lst - task.ra)
    vectors = task.horizon.vectors(lst, ha)
    altitude, azimuth = altaz(vectors)
    directional, clean = quality.direction_factors(altitude, azimuth)
    expected = (vectors, altitude, azimuth, directional, clean, quality.terrain_free, quality.local_clean,
                quality.notice_clean)
    subset = np.flatnonzero(altitude > 5.0)
    assert 0 < len(subset) < len(task.ids)
    got = visible_sky(task, quality, lst, ha, subset, task.horizon.take(subset))
    got += (quality.terrain_free, quality.local_clean, quality.notice_clean)
    for want, have in zip(expected, got):
        assert want.shape == have.shape
        np.testing.assert_array_equal(have[subset], want[subset])
    rest = np.setdiff1d(np.arange(len(task.ids)), subset)
    assert (got[1][rest] == 90.0).all() and (got[3][rest] == 1.0).all() and got[4][rest].all()
    # An empty visible set is also fine.
    none = visible_sky(task, quality, lst, ha, subset[:0], task.horizon.take(subset[:0]))
    assert none[0].shape == (len(task.ids), 3) and none[4].all()


@pytest.mark.parametrize("extra", [(), (0.07,), (0.07, 0.2)])
def test_program_odds_equal_the_full_evaluation_on_the_visible_targets(extra):
    _, quality = build(count=3000, terrain=False)
    base = np.random.default_rng(2).uniform(0.05, 1.4, 3000)
    subset = np.flatnonzero(base > 0.5)
    full = quality.program_probabilities(base, *extra)
    got = program_odds(quality, base, subset, *extra)
    assert got.shape == full.shape
    np.testing.assert_array_equal(got[:, subset], full[:, subset])
    assert (np.delete(got, subset, axis=1) == 0).all()
