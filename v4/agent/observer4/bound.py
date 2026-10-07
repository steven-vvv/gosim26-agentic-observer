"""Exposure lengths of an anchor field that cannot enter the finalists, found from an upper bound.

A field's merit for one exposure length is the sum, over its fibres, of the best target
of that fibre (gain times a geometry weight in [0, 1]), divided by the length plus the
overhead. The fibres hold disjoint targets, so that sum never exceeds the sum of the
``count`` largest nonnegative gains of the length, for any centre. A length whose bound
does not reach the merit of the sixth finalist can neither enter the finalists nor beat
the finalist that a later refinement starts from, so the planner leaves it out before the
geometry is evaluated; the lengths kept give the same values as before, column by column,
and the best of them is chosen in the same order.
"""

from __future__ import annotations

import numpy as np

# The bound is formed from sums of at most ``count`` terms and compared after one division, so
# floating-point error is below 1e-14 relative; this margin is far above it.
MARGIN = 1.0 + 1e-9


def _unreachable(bound: np.ndarray, floor: float) -> np.ndarray:
    """Lengths whose bound cannot exceed ``floor`` (a NaN bound is never unreachable)."""
    return bound * MARGIN <= floor


def surviving_durations(gains: np.ndarray, durations: np.ndarray, overhead: float, count: int,
                        floor: float) -> np.ndarray | None:
    """Indices of the exposure lengths (rows of ``gains``) whose merit bound exceeds ``floor``.

    ``gains`` is (lengths, targets), ``count`` the number of fibres and ``floor`` the merit of
    the sixth finalist. Returns None when every length survives.
    """
    lengths = len(durations)
    if not lengths or not gains.shape[1]:
        return None
    positive = np.maximum(gains, 0)
    denominator = durations + overhead
    # Cheap bound first: at most ``count`` targets are chosen, none above the largest gain, and
    # none beyond the sum of all gains.
    bound = np.minimum(positive.sum(axis=1), count * positive.max(axis=1)) / denominator
    alive = np.flatnonzero(~_unreachable(bound, floor))
    if len(alive) and gains.shape[1] > count:
        # Exact bound on the lengths left: the sum of their ``count`` largest gains.
        top = np.partition(positive[alive], gains.shape[1] - count, axis=1)[:, gains.shape[1] - count:]
        alive = alive[~_unreachable(top.sum(axis=1) / denominator[alive], floor)]
    return None if len(alive) == lengths else alive
