"""The in-place azimuth wrap is bit for bit the plain ``(az - c + 180) % 360 - 180``."""

import numpy as np
import pytest

from v4.agent.observer4.terrain import _inside, mod360, wrapped_offset


def _edge_values():
    rng = np.random.default_rng(7)
    special = np.array([0.0, -0.0, 360.0, 359.9999999999999, -1e-20, 1e-20, 180.0, -180.0, 540.0, 719.999999,
                        -359.999999, -360.0, 720.0, 1e-300, -1e-300, np.nextafter(360.0, 0.0), np.nextafter(0.0, -1.0)])
    return np.concatenate([rng.uniform(-50.0, 410.0, 200_000), rng.uniform(-400.0, 800.0, 50_000), special])


def test_mod360_matches_remainder_bitwise():
    values = _edge_values()
    for shift in (0.0, 45.0, 22.5, 180.0, 337.5, 123.456789, -22.5):
        original = (values + shift) % 360.0
        wrapped = mod360(values + shift)
        assert original.tobytes() == wrapped.tobytes(), shift


@pytest.mark.filterwarnings("ignore::RuntimeWarning")
def test_mod360_falls_back_outside_the_exact_range():
    for values in (np.array([1e6, -1e6, 3.0]), np.array([np.nan, 5.0]), np.array([np.inf, 5.0]), np.array([]),
                   np.float32([361.0, -1.0])):
        expected = values % 360.0
        assert mod360(values.copy()).tobytes() == expected.tobytes()
    assert mod360(np.float64(725.0)) == np.float64(725.0) % 360.0


def test_wrapped_offset_and_inside_match_the_plain_expressions():
    values = _edge_values()
    for center in (0.0, 45.0, 315.0, 22.5, 337.5, 123.456789):
        plain = (values - center + 180.0) % 360.0 - 180.0
        assert wrapped_offset(values, center).tobytes() == plain.tobytes()
        for half in (22.5, 57.5, 83.0):
            assert np.array_equal(_inside(values, center, half), np.abs(plain) <= half)
    centers = np.linspace(-30.0, 30.0, 7)[:, None]
    plain = (values[None, :1000] - centers + 180.0) % 360.0 - 180.0
    assert wrapped_offset(values[None, :1000], centers).tobytes() == plain.tobytes()
    assert wrapped_offset(5.0, 10.0) == (5.0 - 10.0 + 180.0) % 360.0 - 180.0
