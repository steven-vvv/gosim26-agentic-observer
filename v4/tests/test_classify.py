"""FiberGrid.classify keeps the plain formulation's results bit for bit.

The rewrite computes the trigonometry once and works in place, but depth, north and east keep one
matrix product each: a stacked product takes a different BLAS kernel on some CPUs (Sapphire Rapids,
for one) and rounds differently, which moved targets across a fibre edge in full runs.
"""

from __future__ import annotations

import numpy as np
import pytest

from v4.agent.observer4.geometry import RAD, FiberGrid

INSTRUMENTS = {
    "a": {"grid_side": 4, "n_fibers": 16, "fiber_area_deg2": 0.4, "gap_deg": 0.0},
    "b": {"grid_side": 5, "n_fibers": 25, "fiber_area_deg2": 0.064, "gap_deg": 0.0},
    "c": {"grid_side": 3, "n_fibers": 9, "fiber_area_deg2": 0.7, "gap_deg": 0.0},
    "d": {"grid_side": 10, "n_fibers": 100, "fiber_area_deg2": 0.06, "gap_deg": 0.0},
    "gap": {"grid_side": 5, "n_fibers": 25, "fiber_area_deg2": 0.064, "gap_deg": 0.03},
}


def plain_classify(grid, vectors, center_alt, center_az):
    """The straightforward formulation, one expression per quantity."""
    ca, cz = np.asarray(center_alt) * RAD, np.asarray(center_az) * RAD
    center = np.stack((np.cos(ca) * np.cos(cz), np.cos(ca) * np.sin(cz), np.sin(ca)), axis=-1)
    north = np.stack((-np.sin(ca) * np.cos(cz), -np.sin(ca) * np.sin(cz), np.cos(ca)), axis=-1)
    east = np.stack((-np.sin(cz), np.cos(cz), np.zeros_like(cz)), axis=-1)
    depth = center @ np.asarray(vectors).T
    divisor = np.where(depth > 0, depth, 1.0)
    dn, de = (north @ np.asarray(vectors).T) / divisor / RAD, (east @ np.asarray(vectors).T) / divisor / RAD
    row = np.clip(np.floor(dn / grid.pitch + grid.side / 2), 0, grid.side - 1).astype(np.int32)
    col = np.clip(np.floor(de / grid.pitch + grid.side / 2), 0, grid.side - 1).astype(np.int32)
    fiber = row * grid.side + col
    margin = grid.glass / 2 - np.maximum(np.abs(dn - grid.north[fiber]), np.abs(de - grid.east[fiber]))
    valid = (depth > 0) & (np.abs(dn) <= grid.fov / 2) & (np.abs(de) <= grid.fov / 2) & (margin >= 0)
    return np.where(valid, fiber, -1), margin, dn, de


def scene(random, centers, targets, spread):
    """Centres in the sky and targets scattered around the first of them (so many fall in a fibre)."""
    alt = random.uniform(20, 80, size=centers)
    az = random.uniform(0, 360, size=centers)
    t_alt = np.clip(alt[0] + random.uniform(-spread, spread, size=targets), -89, 89)
    t_az = az[0] + random.uniform(-spread, spread, size=targets) / np.cos(t_alt * RAD)
    vectors = np.stack((np.cos(t_alt * RAD) * np.cos(t_az * RAD), np.cos(t_alt * RAD) * np.sin(t_az * RAD),
                        np.sin(t_alt * RAD)), axis=-1)
    # some centres sit on the first one, so their fibre edges are exercised by many targets
    alt[1::3], az[1::3] = alt[0] + random.uniform(-0.3, 0.3, size=len(alt[1::3])), az[0]
    return vectors, alt, az


def assert_same_bits(actual, expected):
    for got, want in zip(actual, expected, strict=True):
        assert got.dtype == want.dtype and got.shape == want.shape
        assert got.tobytes() == want.tobytes()


@pytest.mark.parametrize("kind", INSTRUMENTS)
@pytest.mark.parametrize("centers,targets", [
    (1, 1), (1, 40), (2, 9), (3, 17), (4, 364), (12, 124), (48, 364), (75, 96), (96, 126), (289, 14), (160, 400),
])
def test_classify_matches_plain_formulation_bit_for_bit(kind, centers, targets):
    grid = FiberGrid(INSTRUMENTS[kind])
    random = np.random.default_rng(7000 + 31 * centers + targets)
    for spread in (0.6, 2.0, 6.0):
        vectors, alt, az = scene(random, centers, targets, spread)
        assert_same_bits(grid.classify(vectors, alt, az), plain_classify(grid, vectors, alt, az))


def test_classify_scalar_centre_and_matrix_of_centres():
    grid = FiberGrid(INSTRUMENTS["d"])
    random = np.random.default_rng(5)
    vectors, alt, az = scene(random, 6, 50, 1.5)
    assert_same_bits(grid.classify(vectors, alt[0], az[0]), plain_classify(grid, vectors, alt[0], az[0]))
    assert_same_bits(grid.classify(vectors, alt.reshape(2, 3), az.reshape(2, 3)),
                     plain_classify(grid, vectors, alt.reshape(2, 3), az.reshape(2, 3)))


def test_classify_marks_targets_behind_the_centre_and_outside_the_fibres():
    grid = FiberGrid(INSTRUMENTS["a"])
    vectors = np.array([[0.0, 0.0, 1.0], [0.0, 0.0, -1.0], [1.0, 0.0, 0.0]])
    fiber, margin, _, _ = grid.classify(vectors, np.array([90.0]), np.array([0.0]))
    assert fiber[0, 1] == -1 and fiber[0, 2] == -1
    assert fiber[0, 0] >= 0 and margin[0, 0] >= 0
