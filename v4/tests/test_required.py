import numpy as np

from v4.agent.observer4.required import stretched


def test_off_and_without_required_targets_is_identity():
    durations = np.array([300, 600, 1200], dtype=np.int32)
    safe = np.array([[False, True], [True, True], [True, True]])
    gain = safe * 200.0
    assert stretched(gain, safe, durations, 0.0) is gain
    assert stretched(gain, safe, durations, 1.0) is gain
    zero = np.zeros((3, 2))
    assert stretched(zero, safe, durations, 4.0) is zero


def test_bonus_rate_is_flat_up_to_the_stretch():
    durations = np.array([300, 600, 1200, 2400], dtype=np.int32)
    safe = np.array([[False, True], [True, True], [True, True], [True, True]])
    gain = safe * 200.0
    out = stretched(gain, safe, durations, 2.0)
    # target 0: shortest safe 600 s -> rate 200/600 up to 1200 s, then a lump of 400
    assert np.allclose(out[:, 0], [0.0, 200.0, 400.0, 400.0])
    # target 1: shortest safe 300 s -> 200, 400 (capped at 2x) ...
    assert np.allclose(out[:, 1], [200.0, 400.0, 400.0, 400.0])
    rate = out[:, 1] / durations
    assert rate[0] == rate[1]
