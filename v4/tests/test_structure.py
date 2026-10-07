"""Card-structure overrides: bands, optional groups, validation and precedence over the scale profile."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from v4.agent.observer4.config import OVERRIDE_BOUNDS, OVERRIDE_INTEGERS, load, resolve
from v4.agent.observer4.planning import Planner
from v4.agent.observer4.structure import card_structure

CONFIG = Path(__file__).resolve().parents[1] / "agent" / "config.json"

# Kinds c, a, b, d: fibres and season hours chosen for 0.356, 0.504, 0.843 and 9.6 fibre hours per target.
KINDS = {"c": (9, 39.6), "a": (16, 31.5), "b": (25, 33.7), "d": (100, 96.1)}


def task(fibers, hours, targets=1000, late_only=False):
    night = SimpleNamespace(start=0.0, end=hours * 3600.0)
    visibility = np.ones((3, targets))
    if late_only:
        visibility[:2] = 0.0
    return SimpleNamespace(visibility=visibility, nights=[night], grid=SimpleNamespace(n=fibers),
                           ids=list(range(targets)))


def kind(name, **kwargs):
    return task(*KINDS[name], **kwargs)


def switch(**extra):
    base = {"enabled": True, "late_fraction": 0.6, "late_share_threshold": 0.34,
            "late_overrides": {"setting_boost": 0.0}, "rich_fiber_hours": 4.0,
            "rich_overrides": {"upgrade_weight": 0.5}, "lean_fiber_hours": 0.7,
            "lean_overrides": {"window_weight": 0.5}}
    base.update(extra)
    return base


def patched(structure):
    return load(CONFIG, {"structure": structure})["structure"]


def test_default_config_overrides_and_bands_are_unchanged():
    default = load(CONFIG)["structure"]
    lean = {"window_weight": 0.5, "upgrade_weight": 0.5, "science_horizon_hours": 120.0, "science_cap_quality": 0.6,
            "refine_iterations": 2, "science_exponent": 1.8, "revisit_increment": 1.0}
    wide = {"anchors": 72, "anchor_pool": 768, "cluster_anchors": 24, "refine_top": 8}
    narrow = {"anchors": 48, "anchor_pool": 512, "cluster_anchors": 16, "refine_top": 6}
    mid = {"setting_boost": 0.0, "anchor_pool": 512, "anchors": 48, "cluster_anchors": 24, "refine_top": 6, "pace_shrink": 0.5,
           "upgrade_weight": 0.5, "science_horizon_hours": 240.0, "science_exponent": 1.65}
    expected = {"c": {**lean, **narrow}, "a": {**lean, **wide}, "b": mid, "d": {"upgrade_weight": 0.5}}
    bands = {"c": "very_lean", "a": "lean", "b": "mid", "d": "rich"}
    for name in KINDS:
        found = card_structure(kind(name), default)
        assert found["overrides"] == expected[name]
        assert found["band"] == bands[name]
    late = card_structure(kind("a", late_only=True), default)
    assert late["late_share"] == 1.0
    assert late["overrides"] == {"setting_boost": 0.0, **lean, **wide}


def test_new_keys_select_overrides_by_band():
    config = switch(very_lean_overrides={"anchors": 32, "window_weight": 0.2}, lean_split_fiber_hours=0.43,
                    mid_overrides={"anchors": 24, "refine_top": 2})
    assert card_structure(kind("c"), config) | {"x": 0} == {
        "late_share": 0.0, "fiber_hours": 0.356, "overrides": {"window_weight": 0.2, "anchors": 32},
        "band": "very_lean", "x": 0}
    a = card_structure(kind("a"), config)
    assert (a["band"], a["overrides"]) == ("lean", {"window_weight": 0.5})
    b = card_structure(kind("b"), config)
    assert (b["band"], b["overrides"]) == ("mid", {"anchors": 24, "refine_top": 2})
    d = card_structure(kind("d"), config)
    assert (d["band"], d["overrides"]) == ("rich", {"upgrade_weight": 0.5})


def test_group_order_late_rich_lean_very_lean_mid():
    late_all = {"setting_boost": 0.1, "anchors": 1}
    config = switch(late_overrides=late_all, lean_overrides={"anchors": 2}, very_lean_overrides={"anchors": 3},
                    lean_split_fiber_hours=0.43, mid_overrides={"anchors": 4}, rich_overrides={"anchors": 5})
    expected = {"c": 3, "a": 2, "b": 4, "d": 5}
    for name, anchors in expected.items():
        found = card_structure(kind(name, late_only=True), config)
        assert found["overrides"]["anchors"] == anchors
        assert found["overrides"]["setting_boost"] == 0.1


def test_split_and_mid_are_inert_without_their_groups():
    config = switch(lean_split_fiber_hours=0.43)
    assert card_structure(kind("c"), config)["overrides"] == {"window_weight": 0.5}
    assert card_structure(kind("b"), config)["overrides"] == {}
    # The split only applies inside the lean band, and without lean_fiber_hours the lean band is empty.
    config = switch(very_lean_overrides={"anchors": 3}, lean_split_fiber_hours=0.6)
    assert card_structure(kind("b"), config)["overrides"] == {}
    no_lean = {key: value for key, value in switch(mid_overrides={"anchors": 4}).items()
               if key not in ("lean_fiber_hours", "lean_overrides")}
    assert card_structure(kind("c"), no_lean)["overrides"] == {"anchors": 4}
    assert card_structure(kind("c"), no_lean)["band"] == "mid"


def test_disabled_switch_has_no_band():
    found = card_structure(kind("a"), switch(enabled=False, mid_overrides={"anchors": 4}))
    assert found == {"late_share": None, "fiber_hours": None, "overrides": {}, "band": None}


@pytest.mark.parametrize("structure", [
    {"lean_overrides": {"cheat": 1.0}},
    {"mid_overrides": {"policy": 1}},
    {"very_lean_overrides": {"durations": 1}},
    {"late_overrides": {"anchors": 0}},
    {"rich_overrides": {"anchor_pool": 4097}},
    {"mid_overrides": {"anchors": 129}},
    {"mid_overrides": {"upgrade_factor": 0.4}},
    {"mid_overrides": {"quality_safety": 1.6}},
    {"mid_overrides": {"neighbor_radius_deg": 0.9}},
    {"lean_overrides": {"refine_top": 33}},
    {"lean_overrides": {"refine_iterations": 0}},
    {"mid_overrides": {"glass_margin_deg": float("nan")}},
    {"mid_overrides": {"glass_margin_deg": True}},
    {"mid_overrides": {"anchors": 24.0}},
    {"very_lean_overrides": {"refine_iterations": 2.0}},
    {"mid_overrides": {"cluster_anchors": 4.5}},
    {"mid_overrides": [("anchors", 4)]},
    {"lean_split_fiber_hours": -0.1},
    {"lean_split_fiber_hours": float("inf")},
    {"lean_split_fiber_hours": "0.43"},
    {"unknown_group_overrides": {"anchors": 4}},
])
def test_invalid_structure_configuration_is_rejected(structure):
    with pytest.raises(ValueError):
        patched(structure)


def test_every_whitelisted_override_accepts_its_bounds_and_nothing_beyond():
    for field, (low, high) in OVERRIDE_BOUNDS.items():
        cast = int if field in OVERRIDE_INTEGERS else float
        for group in ("late_overrides", "rich_overrides", "lean_overrides", "very_lean_overrides", "mid_overrides"):
            for value in (low, high):
                assert patched({group: {field: cast(value)}})[group][field] == cast(value)
        step = 1 if cast is int else 1e-6
        for value in (low - step, high + step):
            with pytest.raises(ValueError):
                patched({"mid_overrides": {field: cast(value)}})
    assert len(OVERRIDE_BOUNDS) == 27


def test_structure_override_bounds_match_policy_bounds():
    # A policy field the validator bounds is bounded identically as a structure override.
    base = json.loads(CONFIG.read_text(encoding="utf-8"))
    for field, (low, high) in OVERRIDE_BOUNDS.items():
        step = 1 if field in OVERRIDE_INTEGERS else 1e-6
        outside = []
        for value in (low - step, high + step):
            config = copy.deepcopy(base)
            config["policy"][field] = value
            try:
                resolve(config)
            except ValueError:
                outside.append(value)
        assert len(outside) in (0, 2), field


def test_neighbor_radius_policy_floor_is_one_degree():
    assert load(CONFIG, {"policy": {"neighbor_radius_deg": 1.0}})["policy"]["neighbor_radius_deg"] == 1.0
    with pytest.raises(ValueError):
        load(CONFIG, {"policy": {"neighbor_radius_deg": 0.99}})


def test_patch_merges_group_fields_and_leaves_the_default_untouched():
    default = load(CONFIG)["structure"]
    merged = patched({"lean_overrides": {"anchors": 20}, "mid_overrides": {"anchors": 8}})
    assert merged["lean_overrides"] == {**default["lean_overrides"], "anchors": 20}
    assert merged["mid_overrides"] == {**default["mid_overrides"], "anchors": 8}
    assert default["lean_split_fiber_hours"] == 0.43
    with pytest.raises(ValueError):
        load(CONFIG, {"structure": {"enabled": True, "surprise": 1}})


def test_default_config_file_has_no_new_structure_keys():
    structure = json.loads(CONFIG.read_text(encoding="utf-8"))["structure"]
    assert set(structure) == {"enabled", "late_fraction", "late_share_threshold", "late_overrides",
                              "rich_fiber_hours", "rich_overrides", "lean_fiber_hours", "lean_overrides",
                              "mid_overrides", "lean_split_fiber_hours", "very_lean_overrides"}


def scaled_planner(overrides):
    planner = object.__new__(Planner)
    planner.task = SimpleNamespace(ids=range(50_000), grid=SimpleNamespace(n=25))
    planner.config = {"scale": {"enabled": True, "cost_threshold": 800_000, "anchor_pool": 32, "anchors": 6,
                                "cluster_anchors": 2, "refine_top": 1, "durations": [450, 600]}}
    planner.structure = {"overrides": overrides}
    return planner


def test_mid_override_beats_the_scale_profile_search_knobs():
    base = {"anchor_pool": 64, "anchors": 12, "cluster_anchors": 4, "refine_top": 3, "durations": [120, 900],
            "neighbor_radius_deg": 4.2}
    config = switch(mid_overrides={"anchors": 24, "anchor_pool": 96, "neighbor_radius_deg": 2.5})
    chosen = card_structure(kind("b"), config)["overrides"]
    scaled = scaled_planner(chosen)._scaled_settings(base)
    assert (scaled["anchors"], scaled["anchor_pool"]) == (24, 96)
    assert scaled["neighbor_radius_deg"] == 2.5
    # Knobs the override leaves alone still take the scale profile.
    assert (scaled["cluster_anchors"], scaled["refine_top"], scaled["durations"]) == (2, 1, [450, 600])
    assert base["anchors"] == 12
    # Without overrides the scale profile alone applies, as before.
    assert scaled_planner({})._scaled_settings(base)["anchors"] == 6
    # On a card below the cost threshold the overrides are merged into the plain policy.
    small = scaled_planner(chosen)
    small.task = SimpleNamespace(ids=range(30_000), grid=SimpleNamespace(n=16))
    assert small._scaled_settings(base) == {**base, **chosen}


def test_planner_prepares_neighbors_after_choosing_the_structure():
    radius = []
    card = kind("b")
    fake = SimpleNamespace(prepare_neighbors=radius.append, visibility=card.visibility, nights=card.nights,
                           grid=card.grid, ids=card.ids, required=np.zeros(1000, dtype=bool), minimum_exposure=60.0)
    config = load(CONFIG, {"structure": {"mid_overrides": {"neighbor_radius_deg": 2.5}}})
    config["scale"]["enabled"] = False
    Planner(fake, None, None, None, config)
    assert radius == [2.5]
