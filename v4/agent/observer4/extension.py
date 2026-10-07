"""Exposure lengths extended at a shadow price of telescope time (``extend_rate``).

The anchor search ranks fields by credited gain per second, ``total(T) / T``. Without
readout overheads that ratio prefers the shortest exposure that completes the bright (or
required) targets of a field; its fainter targets then keep a partial factor that a later,
longer exposure of the same field supersedes (a target scores its best exposure only), so
the time of the first exposure is spent twice. When the season is limited by telescope
time rather than by fibres, an extra second of an exposure is worth taking while it earns
more than ``lam``, the credited gain per second of the last exposures the season can still
afford. A finalist therefore keeps its field and centre (and its rank, which stays the
gain per second of its own length) and is exposed for the candidate length ``T' >= T``
with the largest ``total(T') - lam * T'``. ``lam = 0`` (the default) changes no plan.
"""

from __future__ import annotations

import numpy as np

from .fibers import fiber_totals


def extend(item: dict, lam: float, grid, vectors, glass_margin: float, limit: float, choose) -> bool:
    """Lengthen a finalist's exposure in place; True when its length changed.

    ``item["_search"]`` holds the (targets, gains per candidate length, lengths) of its field,
    ``limit`` the longest exposure its centre allows and ``choose`` the per-fibre target choice.
    """
    search = item.get("_search")
    if lam <= 0 or search is None:
        return False
    near, gains, durations = search
    longer = np.flatnonzero((durations >= item["duration"]) & (durations <= limit))
    if len(longer) < 2:
        return False
    fibers, margin, _, _ = grid.classify(vectors[near], np.array([item["alt"]]), np.array([item["az"]]))
    weight = np.where(fibers >= 0, 1.0, 0.0)
    weight *= np.where(margin >= glass_margin, 1.0, 0.45)
    total = fiber_totals(fibers, gains[longer], weight, grid.n)[0]
    best = longer[int(np.argmax(total - lam * durations[longer]))]
    if int(durations[best]) == item["duration"]:
        return False
    item["duration"] = int(durations[best])
    item["selected"] = choose(fibers[0], gains[best] * weight[0], near, item["duration"])
    item["extended"] = True
    return True
