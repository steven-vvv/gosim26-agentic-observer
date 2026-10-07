"""Science gain of exposing targets that already hold a partial score.

The score of a target is its single best exposure, so an exposure to a target that already
reached factor ``e`` adds only ``f - e``. The planner credits a factor ``f`` with the convex
value ``science_value(f)`` (partial exposures are often superseded); its default gain for a
revisit, ``science_value(f) - science_value(e)``, therefore credits a revisit more than the
increment it can add (``1 - 0.5 ** 1.5 = 0.65`` for ``e = 0.5``), and ranks revisits ahead of
fresh partial exposures that add as much.

``revisit_increment`` (weight ``r`` in [0, 1], default 0) blends that difference with the
increment valued at the same convexity as a fresh exposure of the new factor:

    (1 - r) * (v(f) - v(e)) + r * (f - e) * v(f) / f,

which equals ``v(f)`` for a first exposure (``e = 0``) and ``1 - e`` for a revisit that
completes the target. ``r = 0`` takes the original arithmetic, bit for bit.
"""

from __future__ import annotations

import numpy as np


def science_gains(values: np.ndarray, factors: np.ndarray, estimate: np.ndarray, estimate_value: np.ndarray,
                  increment: float) -> np.ndarray:
    """Nonnegative science gain per unit weight; ``values`` (= v(factors)) is reused in place."""
    revisit = np.flatnonzero(estimate > 0) if increment > 0 else ()
    new = values[..., revisit] if len(revisit) else None
    values -= estimate_value
    if new is not None:
        f = factors[..., revisit]
        linear = (f - estimate[revisit]) * np.divide(new, f, out=np.zeros_like(new), where=f > 0)
        values[..., revisit] = (1 - increment) * values[..., revisit] + increment * linear
    return np.maximum(0, values, out=values)
