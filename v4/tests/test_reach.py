"""The reach bound: targets farther from an anchor than reach_degrees never fall into a searched field."""

from __future__ import annotations

import math
from types import SimpleNamespace

import numpy as np
import pytest

from v4.agent.observer4.geometry import RAD, FiberGrid
from v4.agent.observer4.reach import Reach, reach_degrees

INSTRUMENTS = {
    "a": {"grid_side": 4, "n_fibers": 16, "fiber_area_deg2": 0.4, "gap_deg": 0.0},
    "b": {"grid_side": 5, "n_fibers": 25, "fiber_area_deg2": 0.064, "gap_deg": 0.0},
    "c": {"grid_side": 3, "n_fibers": 9, "fiber_area_deg2": 0.7, "gap_deg": 0.0},
    "d": {"grid_side": 10, "n_fibers": 100, "fiber_area_deg2": 0.06, "gap_deg": 0.0},
    "gap": {"grid_side": 5, "n_fibers": 25, "fiber_area_deg2": 0.064, "gap_deg": 0.03},
}
SHIFTS = (-0.24, -0.12, 0.12, 0.24)


def unit(alt, az):
    alt, az = np.asarray(alt) * RAD, np.asarray(az) * RAD
    return np.stack((np.cos(alt) * np.cos(az), np.cos(alt) * np.sin(az), np.sin(alt)), axis=-1)


def separation(a, b):
    return np.degrees(np.arccos(np.clip(a @ b.T, -1, 1)))


def refine_moves(random, alt, az, iterations):
    """Centres a chain of Planner._refine steps can reach (random shifts, as in _refine)."""
    steps = np.asarray(SHIFTS)
    for _ in range(iterations):
        alt = alt + random.choice(np.r_[0.0, steps])
        az = (az + random.choice(np.r_[0.0, steps]) / max(0.05, math.cos(alt * RAD))) % 360.0
        yield alt, az


@pytest.mark.parametrize("kind", sorted(INSTRUMENTS))
@pytest.mark.parametrize("iterations", [1, 2])
def test_targets_beyond_the_reach_are_never_classified_into_a_field(kind, iterations):
    grid = FiberGrid(INSTRUMENTS[kind])
    settings = {"refine_centers": True, "refine_top": 6, "refine_iterations": iterations}
    radius = reach_degrees(grid, settings)
    random = np.random.default_rng(7 + iterations)
    for _ in range(10):
        alt, az = random.uniform(5, 88), random.uniform(0, 360)
        centers_alt, centers_az = grid.centers_for_target(alt, az)
        anchor = unit(alt, az)[None, :]
        # Centres of the anchor search: the grid cells, then +-0.12 in height.
        searched = [(centers_alt, centers_az), (centers_alt + 0.12, centers_az), (centers_alt - 0.12, centers_az)]
        ca = np.concatenate([c[0] for c in searched])
        cz = np.concatenate([c[1] for c in searched])
        # Plus the chains of refinement shifts from a random searched centre.
        chains = []
        for _ in range(25):
            k = random.integers(len(ca))
            chains.extend(refine_moves(random, float(ca[k]), float(cz[k]), iterations))
        ca = np.concatenate((ca, [c[0] for c in chains]))
        cz = np.concatenate((cz, [c[1] for c in chains]))
        keep = (ca >= 0) & (ca <= 89.85)
        ca, cz = ca[keep], cz[keep]
        # Targets over a disc beyond the reach: uniform in area, plus a ring around the bound.
        offset = np.where(random.random(1500) < 0.5, 1.4 * radius * np.sqrt(random.random(1500)),
                          radius * (0.8 + 0.4 * random.random(1500)))
        bearing = random.uniform(0, 2 * np.pi, 1500)
        a0, z0 = alt * RAD, az * RAD
        d = offset * RAD
        t_alt = np.arcsin(np.clip(np.sin(a0) * np.cos(d) + np.cos(a0) * np.sin(d) * np.cos(bearing), -1, 1))
        t_az = z0 + np.arctan2(np.sin(bearing) * np.sin(d) * np.cos(a0), np.cos(d) - np.sin(a0) * np.sin(t_alt))
        vectors = unit(np.degrees(t_alt), np.degrees(t_az))
        far = separation(vectors, anchor)[:, 0] > radius
        fibers, _, _, _ = grid.classify(vectors, ca, cz)
        assert far.any()
        assert (fibers[:, far] == -1).all()
        # The bound is not vacuous: nearer targets do fall into fields, within a fraction of it.
        inside = (fibers >= 0).any(axis=0)
        assert inside.any()
        assert separation(vectors[inside], anchor).max() > 0.5 * radius


def test_anchor_centres_hold_the_anchor_within_the_cell_diagonal():
    grid = FiberGrid(INSTRUMENTS["b"])
    random = np.random.default_rng(3)
    cell = float(np.max(np.hypot(grid.north, grid.east)))
    for _ in range(100):
        alt, az = random.uniform(1, 89), random.uniform(0, 360)
        ca, cz = grid.centers_for_target(alt, az)
        assert separation(unit(ca, cz), unit(alt, az)[None, :]).max() <= cell + 1e-9


def test_reach_grows_with_refinement_and_ignores_it_when_off():
    grid = FiberGrid(INSTRUMENTS["b"])
    base = {"refine_centers": False, "refine_top": 0}
    assert reach_degrees(grid, {"refine_centers": True, "refine_top": 0}) == pytest.approx(reach_degrees(grid, base) + 0.12)
    one = reach_degrees(grid, {**base, "refine_top": 1, "refine_iterations": 1})
    two = reach_degrees(grid, {**base, "refine_top": 1, "refine_iterations": 2})
    assert one == pytest.approx(reach_degrees(grid, base) + 0.24 * math.sqrt(2) * 1.05)
    assert two == pytest.approx(reach_degrees(grid, base) + 0.48 * math.sqrt(2) * 1.05)


def test_crop_keeps_the_order_and_only_the_targets_in_reach():
    grid = FiberGrid(INSTRUMENTS["b"])
    random = np.random.default_rng(5)
    vectors = unit(random.uniform(20, 30, 500), random.uniform(0, 12, 500))
    task = SimpleNamespace(grid=grid, vectors=vectors)
    settings = {"refine_centers": True, "refine_top": 6, "refine_iterations": 1, "neighbor_radius_deg": 4.2}
    radius = reach_degrees(grid, settings)
    anchor = 7
    around = np.flatnonzero(separation(vectors, vectors[anchor][None, :])[:, 0] <= 4.2).astype(np.int32)
    usable = random.random(len(around)) < 0.7
    reach = Reach(task)
    cropped = reach.crop(settings, anchor, around, usable, around[usable])
    wanted = around[usable][separation(vectors[around[usable]], vectors[anchor][None, :])[:, 0] <= radius]
    np.testing.assert_array_equal(cropped, wanted)
    assert 0 < len(cropped) < usable.sum()
    # A second call hits the cache, and a changed setting object recomputes the radius.
    np.testing.assert_array_equal(reach.crop(settings, anchor, around, usable, around[usable]), wanted)
    wide = {**settings, "refine_top": 0, "refine_centers": False}
    assert len(reach.crop(wide, anchor, around, usable, around[usable])) < len(cropped)
    huge = {**settings, "neighbor_radius_deg": 1.0}
    np.testing.assert_array_equal(reach.crop(huge, anchor, around, usable, around[usable]), around[usable])


def test_one_refinement_shift_moves_the_centre_less_than_the_bound_at_any_altitude():
    random = np.random.default_rng(11)
    steps = np.asarray(SHIFTS)
    alt = np.concatenate((random.uniform(0, 90, 2000), random.uniform(85, 89.85, 2000)))
    az = random.uniform(0, 360, alt.size)
    da, dz = random.choice(steps, alt.size), random.choice(steps, alt.size)
    moved_alt = alt + da
    moved_az = az + dz / np.maximum(0.05, np.cos(alt * RAD))
    moved = np.degrees(np.arccos(np.clip(np.einsum("ij,ij->i", unit(alt, az), unit(moved_alt, moved_az)), -1, 1)))
    assert moved.max() <= 0.24 * math.sqrt(2) * 1.05
