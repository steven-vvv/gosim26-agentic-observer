import numpy as np

from v4.agent.observer4.timing import QualityTiming


def test_warmup_returns_one_and_counts():
    timing = QualityTiming(warmup=3)
    assert timing.boost(np.array([2.0, 0.5]), 0.8, 1.0) == 1.0
    assert timing.total == 1


def test_rare_completion_is_raised_and_common_is_not():
    timing = QualityTiming(warmup=1)
    for scale in np.linspace(0.4, 1.2, 200):
        timing.add(float(scale))
    # now scale 1.2 (best sky): target 0 completes with 5x margin (needs 0.24: always possible),
    # target 1 just completes (needs 1.2: rare), target 2 cannot complete now.
    out = timing.boost(np.array([5.0, 1.0, 0.8]), 1.2, 1.0)
    assert abs(out[0] - 1.0) < 1e-9
    assert out[1] > 1.9
    assert out[2] == 1.0
