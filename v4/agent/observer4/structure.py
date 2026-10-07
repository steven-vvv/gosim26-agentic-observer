"""Policy overrides selected from the public season structure of a card."""

from __future__ import annotations

import numpy as np


def card_structure(task, switch: dict) -> dict:
    """Public season structure of the card, and the policy overrides it selects.

    ``late_share``: fraction of targets with more than ``late_fraction`` of their public
    visible hours in the last third of the season (a region that only rises late). The
    nightly setting boost then keeps the earlier region busy when the late one finally
    rises. ``fiber_hours``: fibre hours of the whole season per target; a season rich in
    time can afford re-exposures for a better program multiplier. A lean season (kinds a
    and c) gains from ranking targets by the nights they have left; on the 25-fibre kind b,
    whose late region rises in the last third, the same urgency costs 7 % of the targets.

    ``band`` names where the fibre hours fall: ``rich`` (at least ``rich_fiber_hours``),
    ``lean`` (at most ``lean_fiber_hours``, zero when absent), ``very_lean`` (a lean card at
    most ``lean_split_fiber_hours``, which tells kind c from kind a) or ``mid`` (between the
    two). The overrides are merged in the order late, rich, lean, very lean, mid, so a later
    group wins a shared key. ``mid_overrides`` and ``very_lean_overrides`` are optional;
    without the new keys the overrides are exactly those of the late, rich and lean groups.
    """
    visibility = np.asarray(getattr(task, "visibility", np.zeros((0, 0))))
    result = {"late_share": None, "fiber_hours": None, "overrides": {}, "band": None}
    if not switch.get("enabled", False) or visibility.size == 0:
        return result
    nights = visibility.shape[0]
    total = visibility.sum(axis=0)
    late = visibility[nights - nights // 3:].sum(axis=0)
    share = float(np.mean(late > float(switch.get("late_fraction", 0.6)) * np.maximum(total, 1e-9)))
    hours = sum(night.end - night.start for night in task.nights) / 3600.0
    fiber_hours = task.grid.n * hours / max(1, len(task.ids))
    lean = float(switch.get("lean_fiber_hours", 0.0))
    rich = float(switch.get("rich_fiber_hours", 1e9))
    split = switch.get("lean_split_fiber_hours")
    overrides = {}
    band = None
    if share >= float(switch.get("late_share_threshold", 1.0)):
        overrides.update(switch.get("late_overrides", {}))
    if fiber_hours >= rich:
        overrides.update(switch.get("rich_overrides", {}))
        band = "rich"
    if fiber_hours <= lean:
        overrides.update(switch.get("lean_overrides", {}))
        band = "lean"
        if split is not None and fiber_hours <= float(split):
            overrides.update(switch.get("very_lean_overrides", {}))
            band = "very_lean"
    if lean < fiber_hours < rich:
        overrides.update(switch.get("mid_overrides", {}))
        band = "mid"
    return {"late_share": round(share, 4), "fiber_hours": round(fiber_hours, 3), "overrides": overrides, "band": band}
