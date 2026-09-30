"""Coverage diagnosis must match the official required-target count."""

from __future__ import annotations

import csv
import json

import pytest

from v4.evaluation.coverage_audit import audit, audit_card


def _fixture(tmp_path, missing=4):
    card = tmp_path / "cards" / "sample"
    result = tmp_path / "runs" / "candidate" / "sample"
    (card / "public").mkdir(parents=True)
    (card / "config").mkdir()
    result.mkdir(parents=True)
    with (card / "public/targets.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=["target_id", "required"])
        writer.writeheader()
        writer.writerows({"target_id": target, "required": "true"} for target in "ABCDE")
    (card / "config/v4_score_config.json").write_text(
        json.dumps({"required": {"observed_factor_threshold": 0.5}}), encoding="utf-8")
    (result / "actions.jsonl").write_text(json.dumps({
        "action": "observe", "assignments": {str(i): target for i, target in enumerate("BCDE")}
    }) + "\n", encoding="utf-8")
    with (result / "observations.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=["target_id", "factor", "valid"])
        writer.writeheader()
        writer.writerows([
            {"target_id": "C", "factor": 0.4, "valid": "true"},
            {"target_id": "D", "factor": 0.6, "valid": "true"},
            {"target_id": "E", "factor": 0.9, "valid": "false"},
        ])
    (result / "summary.json").write_text(json.dumps({
        "card": "sample", "counts": {"required_missing": missing},
    }), encoding="utf-8")
    return card, result


def test_audit_separates_planning_hits_and_threshold(tmp_path):
    card, result = _fixture(tmp_path)
    row = audit_card(card, result)
    assert row == {"card": "sample", "required": 5, "required_missing": 4,
                   "never_assigned": 1, "assigned_no_valid_hit": 2,
                   "hit_below_threshold": 1, "reached_threshold": 1}
    totals = audit(tmp_path / "runs", tmp_path / "cards", ["candidate"])["variants"]["candidate"]["totals"]
    assert totals["required_missing"] == 4


def test_audit_rejects_official_count_mismatch(tmp_path):
    card, result = _fixture(tmp_path, missing=3)
    with pytest.raises(ValueError, match="required missing mismatch"):
        audit_card(card, result)
