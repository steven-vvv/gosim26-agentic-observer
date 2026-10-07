"""Angular reach of an anchor field: farther targets never fall into a field searched around it."""

from __future__ import annotations

import math

import numpy as np

from .geometry import RAD

REFINE_SHIFTS = (-0.24, -0.12, 0.12, 0.24)


def reach_degrees(grid, settings: dict) -> float:
    """Upper bound (degrees) of the angular distance from an anchor to any target a field can hold.

    The anchor sits in one of the grid cells of every candidate centre (a gnomonic offset,
    so at most the cell's diagonal offset in angle); the centre moves by the height shift
    of ``refine_centers`` and by up to ``refine_iterations`` local shifts, each an altitude
    step and an azimuth step scaled by 1 / cos(alt), together a sphere distance of at
    most sqrt(2) * 1.05 times the step (the haversine bound is 1.04 near the 0.05 floor
    of the cosine); a target inside the field lies within half the field diagonal of the
    centre. A small margin covers rounding.
    """
    shift = 0.12 if settings["refine_centers"] else 0.0
    if int(settings.get("refine_top", 0)) > 0:
        steps = max(abs(step) for step in settings.get("refine_shifts", REFINE_SHIFTS))
        shift += steps * math.sqrt(2) * 1.05 * max(1, int(settings.get("refine_iterations", 1)))
    return float(np.max(np.hypot(grid.north, grid.east))) + grid.fov / 2 * math.sqrt(2) + shift + 0.05


class Reach:
    """Per-anchor masks of the neighbours inside the reach, cached for the run."""

    def __init__(self, task):
        self.task, self.source, self.radius, self.masks = task, None, None, {}

    def crop(self, settings: dict, anchor: int, around, usable, near):
        """``near`` (= ``around[usable]``) without the targets beyond the reach of ``anchor``."""
        if settings is not self.source:
            self.source, radius = settings, reach_degrees(self.task.grid, settings)
            self.radius = radius if radius < settings["neighbor_radius_deg"] else None
            self.masks = {}
        if self.radius is None:
            return near
        inside = self.masks.get(anchor)
        if inside is None:
            vectors = self.task.vectors
            inside = self.masks[anchor] = vectors[around] @ vectors[anchor] >= math.cos(self.radius * RAD)
        return near[inside[usable]]
