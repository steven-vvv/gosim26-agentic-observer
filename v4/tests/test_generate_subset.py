"""A "cards" subset must resolve each name to its generating parent dataset's full spec
(seed included), not the published-card stub, unless the name is genuinely a published
organizer card with no generatable parent.
"""

from __future__ import annotations

from v4.evaluation.generate import specifications


def _by_name(specs):
    return {spec["name"]: spec for spec in specs}


def test_big_b24v_card_spec_matches_kind_big_exactly():
    subset = _by_name(specifications("big-b24v"))
    parent = _by_name(specifications("kind-big"))
    assert subset["kind-big-r3-065"] == parent["kind-big-r3-065"]
    assert "seed" in subset["kind-big-r3-065"]


def test_big_abc48_d16_ac48v_d24v_also_resolve_via_of_field():
    parent = _by_name(specifications("kind-big"))
    for dataset in ("big-abc48", "big-d16", "big-ac48v", "big-d24v"):
        subset = _by_name(specifications(dataset))
        for name, spec in subset.items():
            assert spec == parent[name], f"{dataset}: {name} spec diverged from kind-big"


def test_big_b_mixed_parents_resolve_by_name_without_of_field():
    subset = _by_name(specifications("big-b"))
    kind_big = _by_name(specifications("kind-big"))
    kind_sweep = _by_name(specifications("kind-sweep"))
    big_names = [n for n in subset if n.startswith("kind-big-")]
    sweep_names = [n for n in subset if n.startswith("kind-sweep-")]
    assert big_names and sweep_names
    for name in big_names:
        assert subset[name] == kind_big[name]
    for name in sweep_names:
        assert subset[name] == kind_sweep[name]


def test_kv_b5_published_card_unaffected_by_subset_resolution():
    subset = _by_name(specifications("kv-b5"))
    kind_validation = _by_name(specifications("kind-validation"))
    assert subset["trial-b"] == {"name": "trial-b", "card_id": "trial-b", "published": True}
    for name in subset:
        if name != "trial-b":
            assert subset[name] == kind_validation[name]
