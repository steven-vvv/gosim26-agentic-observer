"""Policy overrides selected from the public season structure of a card."""

from __future__ import annotations

import numpy as np


def card_structure(task, switch: dict) -> dict:
    """Public season structure of the card, and the policy overrides it selects.

    ``late_share``: fraction of targets with more than ``late_fraction`` of their public
    visible hours in the last third of the season (a region that only rises late). The
    nightly setting boost then keeps the earlier region busy when the late one finally
    rises. ``fiber_hours``: fibre hours of the whole season per target; a season rich in
    time can afford re-exposures for a better program multiplier.
    """
    visibility = np.asarray(getattr(task, "visibility", np.zeros((0, 0))))
    result = {"late_share": None, "fiber_hours": None, "overrides": {}}
    if not switch.get("enabled", False) or visibility.size == 0:
        return result
    nights = visibility.shape[0]
    total = visibility.sum(axis=0)
    late = visibility[nights - nights // 3:].sum(axis=0)
    share = float(np.mean(late > float(switch.get("late_fraction", 0.6)) * np.maximum(total, 1e-9)))
    hours = sum(night.end - night.start for night in task.nights) / 3600.0
    fiber_hours = task.grid.n * hours / max(1, len(task.ids))
    overrides = {}
    if share >= float(switch.get("late_share_threshold", 1.0)):
        overrides.update(switch.get("late_overrides", {}))
    if fiber_hours >= float(switch.get("rich_fiber_hours", 1e9)):
        overrides.update(switch.get("rich_overrides", {}))
    return {"late_share": round(share, 4), "fiber_hours": round(fiber_hours, 3), "overrides": overrides}
