"""Exposure-length matching of the targets in one field."""

from __future__ import annotations

import numpy as np


def overexposure_weights(raw: np.ndarray, gamma: float) -> np.ndarray:
    """Discount of each target's science gain for the part of the exposure it does not need.

    ``raw`` is the unclipped completion factor of every (duration, target); a target that
    saturates after a fraction ``1 / raw`` of the exposure keeps that fraction ** gamma of
    its gain. A long exposure then gives its fibres to the targets that need the time, and
    the bright targets go to exposures that match them. Targets that do not saturate keep
    their gain (weight one).
    """
    weights = np.ones(np.shape(raw))
    over = raw > 1.0
    if np.any(over):
        weights[over] = raw[over] ** -gamma
    return weights
