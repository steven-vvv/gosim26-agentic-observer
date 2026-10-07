"""Sky quantities of the visible targets only; the rest are neutral values the plan masks out."""

from __future__ import annotations

import numpy as np

from .geometry import altaz


def _spread(values, subset, count: int, fill):
    full = np.full((*values.shape[:-1], count), fill, dtype=values.dtype)
    full[..., subset] = values
    return full


def visible_sky(task, quality, lst: float, ha, subset, part):
    """Horizon vectors, altitude, azimuth, direction factors and cleanliness of all targets.

    ``part`` is task.horizon.take(subset). Every quantity is elementwise, so the visible
    targets hold the values of the whole-catalogue evaluation; the others sit at the zenith
    (outside every terrain envelope), clean and unblocked. ``quality`` ends up with its
    terrain_free, notice_clean and local_clean arrays at full length too.
    """
    count = len(task.ids)
    visible_vectors = part.vectors(lst, ha[subset])
    vectors = np.zeros((count, 3))
    vectors[subset] = visible_vectors
    visible_altitude, visible_azimuth = altaz(visible_vectors)
    factor, clean = quality.direction_factors(visible_altitude, visible_azimuth)
    quality.terrain_free = _spread(quality.terrain_free, subset, count, 1.0)
    quality.notice_clean = _spread(quality.notice_clean, subset, count, True)
    quality.local_clean = _spread(quality.local_clean, subset, count, True)
    return (vectors, _spread(visible_altitude, subset, count, 90.0), _spread(visible_azimuth, subset, count, 0.0),
            _spread(factor, subset, count, 1.0), _spread(clean, subset, count, True))


def program_odds(quality, base_quality, subset, *args):
    """quality.program_probabilities of the visible targets (zero for the others)."""
    return _spread(quality.program_probabilities(base_quality[subset], *args), subset, len(base_quality), 0.0)
