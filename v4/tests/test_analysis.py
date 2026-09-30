"""Analysis must expose missing pairs and mixed experiment provenance."""

from __future__ import annotations

import copy
import json

import pytest

from v4.evaluation.analyze import analyze, paired


def result(total, missing=2):
    return {"total": total, "source_sha256": "frozen-source", "fast_engine": True,
            "components": {"sum_best_scores": total + 50 * missing, "required_penalty": -50 * missing},
            "counts": {"required_missing": missing}, "policy_seconds": 10,
            "termination_reason": "survey_complete"}


def test_paired_tails_and_components_do_not_hide_regressions():
    baseline = {name: result(100) for name in ("a", "b", "c")}
    candidate = {"a": result(80), "b": result(100), "c": result(180, 1)}
    report = paired(baseline, candidate)
    assert report["mean"] == 20
    assert report["minimum"] == -20
    assert (report["wins"], report["ties"], report["losses"]) == (1, 1, 1)
    assert [row["card"] for row in report["per_card"]] == ["a", "b", "c"]
    assert sum(report["component_delta_means"].values()) == pytest.approx(20, abs=1e-6)
    assert report["count_delta_means"] == {"required_missing": pytest.approx(-1 / 3, abs=1e-6)}
    assert report["mean_bootstrap_95_ci"][0] < 0 < report["mean_bootstrap_95_ci"][1]
    assert report == paired(baseline, candidate)


def test_missing_pairs_are_rejected_or_explicitly_provisional():
    baseline = {"a": result(100), "b": result(100)}
    candidate = {"a": result(110)}
    with pytest.raises(ValueError, match="unpaired"):
        paired(baseline, candidate)
    report = paired(baseline, candidate, allow_incomplete=True)
    assert report["provisional"] and report["cards"] == 1
    assert report["missing_candidate_cards"] == ["b"]


@pytest.mark.parametrize("key,value", [("source_sha256", "changed-source"), ("fast_engine", False)])
def test_different_experiment_provenance_is_rejected(key, value):
    baseline = {"a": result(100)}
    candidate = copy.deepcopy(baseline)
    candidate["a"][key] = value
    with pytest.raises(ValueError, match=key):
        paired(baseline, candidate)


def test_mismatched_count_structure_is_rejected():
    baseline = {"a": result(100)}
    candidate = copy.deepcopy(baseline)
    candidate["a"]["counts"]["observe_actions"] = 10
    with pytest.raises(ValueError, match="count mismatch"):
        paired(baseline, candidate)


def test_profiles_require_every_predeclared_card_and_keep_group_tails(tmp_path):
    specs = [
        {"name": "short-0", "start_date": "2026-10-01", "end_date": "2026-10-15",
         "targets": 100, "area_deg2": 20, "stress": True},
        {"name": "long-0", "start_date": "2026-11-01", "end_date": "2026-12-01",
         "targets": 200, "area_deg2": 50, "stress": False},
    ]
    for variant, totals in (("baseline", (10, 20)), ("default", (15, 5))):
        for spec, total in zip(specs, totals):
            directory = tmp_path / variant / spec["name"]
            directory.mkdir(parents=True)
            row = {**result(total), "card": spec["name"], "variant": variant,
                   "config_sha256": variant, "elapsed_seconds": 20}
            (directory / "summary.json").write_text(json.dumps(row), encoding="utf-8")
    report = analyze(tmp_path, "baseline", ["default"], specs=specs)
    assert report["profiles"]["profile_0"]["paired"]["default"]["mean"] == 5
    assert report["profiles"]["profile_1"]["paired"]["default"]["mean"] == -15
    (tmp_path / "default/long-0/summary.json").unlink()
    with pytest.raises(ValueError, match="unpaired|dataset specifications"):
        analyze(tmp_path, "baseline", ["default"], specs=specs)
