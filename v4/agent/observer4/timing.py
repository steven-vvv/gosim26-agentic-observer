"""Quality timing: complete a target when the sky allows it, while that is rare for the target.

A target whose longest exposure completes it in a typical sky can wait for any night; a faint
target completes only in the better skies. The greedy planner prefers the targets with the highest
rate, so the good nights go to the bright targets as well, and the faint ones are left with
partial exposures in poorer skies (later superseded, or never completed).

``timing_weight`` (``k``; default 0, off) raises the urgency of every target the current sky can
complete within its longest exposure by ``k * (1 - p)``, where ``p`` is the share of past plans
whose sky (the planner's global quality scale) was at least the scale that completion needs at
the target's current geometry. A bright target (``p`` near one) keeps its urgency; a faint target
that only the best skies complete is ranked ahead while they last. The history starts with the
run; before ``warmup`` plans no target is raised.
"""

from __future__ import annotations

import math

import numpy as np


class QualityTiming:
    def __init__(self, bins: int = 240, low: float = -4.0, high: float = 2.0, warmup: int = 150):
        self.edges = np.linspace(low, high, bins + 1)
        self.counts = np.zeros(bins)
        self.total = 0
        self.warmup = warmup

    def add(self, scale: float) -> None:
        if scale > 0 and math.isfinite(scale):
            k = int(np.clip(np.searchsorted(self.edges, math.log(scale)) - 1, 0, len(self.counts) - 1))
            self.counts[k] += 1
            self.total += 1

    def survival(self, needed: np.ndarray) -> np.ndarray:
        """Share of past plans whose scale reached ``needed`` (bin resolution)."""
        tail = np.append(np.cumsum(self.counts[::-1])[::-1], 0.0) / max(1, self.total)
        k = np.clip(np.searchsorted(self.edges, np.log(np.maximum(needed, 1e-12))), 0, len(self.counts))
        return tail[k]

    def boost(self, full: np.ndarray, scale: float, weight: float) -> np.ndarray | float:
        """Urgency factor per target; ``full`` is the unclipped factor of the longest exposure now."""
        self.add(scale)
        if self.total < self.warmup or scale <= 0:
            return 1.0
        result = np.ones(len(full))
        able = np.flatnonzero(full >= 1.0)
        if len(able):
            result[able] += weight * (1.0 - self.survival(scale / full[able]))
        return result
