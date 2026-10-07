"""Discount of targets exposed far below their transit altitude (``transit_weight``).

The rate of a target falls with airmass as ``airmass ** -a``. A greedy plan values a fresh
target the same whether it has just risen or stands at transit, so every night it exposes
the band of targets rising past the altitude limit, at the worst airmass they will have,
while they would be up for hours more (and on many later nights) at a better one. The
gain of a target is multiplied by ``(airmass at transit / airmass now) ** (a * k * share)``:
a target at its transit altitude keeps its gain, one far below it loses part of it.
``share`` (in [0, 1], from the public visible hours left) lifts the discount for targets
with few chances left. ``k = 0`` (the default) changes no plan.
"""

from __future__ import annotations

import numpy as np

from .geometry import airmass


def transit_weights(task, altitude: np.ndarray, weight: float, share) -> np.ndarray:
    """Per-target gain multiplier in (0, 1]; ``altitude`` is the current altitude of every target."""
    peak = 90.0 - np.abs(task.dec - task.latitude)
    ratio = airmass(np.maximum(peak, 1.0)) / airmass(np.maximum(altitude, 1.0))
    exponent = float(task.scoring["airmass_exponent"]) * weight * share
    return np.minimum(1.0, ratio) ** exponent
