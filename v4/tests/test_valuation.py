import numpy as np

from v4.agent.observer4.planning import science_value
from v4.agent.observer4.valuation import science_gains


def _case():
    rng = np.random.default_rng(7)
    factors = rng.uniform(0, 1, size=(5, 40))
    factors[:, :3] = 1.0
    estimate = np.where(rng.uniform(size=40) < 0.5, rng.uniform(0, 1, size=40), 0.0)
    return factors, estimate


def test_zero_weight_keeps_original_arithmetic_bit_for_bit():
    factors, estimate = _case()
    estimate_value = science_value(estimate, 1.5, None)
    expected = science_value(factors, 1.5, None)
    expected -= estimate_value
    np.maximum(0, expected, out=expected)
    got = science_gains(science_value(factors, 1.5, None), factors, estimate, estimate_value, 0.0)
    assert np.array_equal(got, expected)


def test_full_weight_values_revisits_by_increment():
    factors, estimate = _case()
    estimate_value = science_value(estimate, 1.5, None)
    got = science_gains(science_value(factors, 1.5, None), factors, estimate, estimate_value, 1.0)
    fresh = estimate == 0
    assert np.allclose(got[:, fresh], factors[:, fresh] ** 1.5)
    revisit = ~fresh
    expected = np.maximum(0, (factors - estimate) * factors ** 0.5)[:, revisit]
    assert np.allclose(got[:, revisit], expected)
    one = science_gains(science_value(np.array([1.0]), 1.5, None), np.array([1.0]), np.array([0.5]),
                        science_value(np.array([0.5]), 1.5, None), 1.0)
    assert np.allclose(one, 0.5)
