"""Typed state carried through one minimal-agent decision graph invocation."""

from __future__ import annotations

from typing import TypedDict

try:  # Python 3.11+
    from typing import NotRequired
except ImportError:  # Python 3.9 / 3.10
    try:
        from typing_extensions import NotRequired
    except ImportError:  # no typing_extensions: optional keys are simply not enforced
        class _NotRequired:
            def __class_getitem__(cls, item):
                return item
        NotRequired = _NotRequired  # type: ignore[misc,assignment]


class DecisionState(TypedDict):
    """Per-request graph state; no field persists into a future decision."""

    initial_publication: dict
    snapshot: dict
    top_k: int
    previews: NotRequired[list]
    compact_candidates: NotRequired[list[dict[str, object]]]
    model_selection: NotRequired[dict[str, object] | None]
    model_error: NotRequired[str]
    decision: NotRequired[dict[str, object]]
    memory: NotRequired[dict]
    tile_best_scores: NotRequired[dict]

