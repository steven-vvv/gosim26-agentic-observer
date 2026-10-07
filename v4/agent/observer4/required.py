"""Exposure length of fields that complete required targets.

A required target earns its bonus once an exposure brings it to the safe factor. Credited as a
lump sum, the bonus divided by the exposure length dominates the merit of every field that holds
one, so such fields always take the shortest exposure that just reaches the safe factor; the
science targets sharing the field then get partial exposures that later ones supersede (on the
lean kinds a sixth of the observing time goes to these fields, with more supersession and
overexposure than the others).

``required_stretch`` (``s``; default 0, off) credits the bonus of a required target per second
of its shortest safe exposure ``d_safe``: an exposure of length ``d`` earns
``bonus * min(d / d_safe, s)``. The bonus rate then stays the same up to ``s * d_safe`` and
the science of the field chooses the length; at ``d = d_safe`` the credit, and so the ranking
of fields by their shortest safe exposure, is unchanged.
"""

from __future__ import annotations

import numpy as np


def stretched(req_gain: np.ndarray, safe: np.ndarray, durations: np.ndarray, stretch: float) -> np.ndarray:
    """Required gain per (duration, target), scaled by the length over the shortest safe one.

    ``durations`` ascend; ``safe`` is monotone along them. Targets never safe have no gain.
    """
    if stretch <= 1.0 or not req_gain.any():
        return req_gain
    first = np.argmax(safe, axis=0)
    scale = durations[:, None] / durations[first][None, :].astype(float)
    np.minimum(scale, stretch, out=scale)
    return req_gain * scale
