"""The offline audit follows the scorer's active, unrepaired fault rule."""

from __future__ import annotations

import csv
import json

import pytest

from v4.evaluation.report_audit import audit, audit_card


def _fixture(tmp_path, settlement):
    card = tmp_path / "cards" / "sample"
    result = tmp_path / "runs" / "candidate" / "sample"
    (card / "config").mkdir(parents=True)
    (card / "truth").mkdir()
    result.mkdir(parents=True)
    (card / "config/v4_score_config.json").write_text(
        json.dumps({"reporting": {"correct_reward": 100, "false_penalty": -150}}), encoding="utf-8")
    with (card / "truth/v4_events.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=["event_id", "event_type", "actual_start_utc", "actual_end_utc"])
        writer.writeheader()
        writer.writerow({"event_id": "F1", "event_type": "instrument_fault",
                         "actual_start_utc": "2026-10-01T00:00:00Z", "actual_end_utc": "2026-10-02T00:00:00Z"})
    with (result / "decisions.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=["decision_id", "action", "start_utc"])
        writer.writeheader()
        writer.writerow({"decision_id": 1, "action": "observe", "start_utc": "2026-10-01T01:00:00Z"})
        writer.writerow({"decision_id": 2, "action": "report", "start_utc": "2026-10-01T02:00:00Z"})
        writer.writerow({"decision_id": 3, "action": "report", "start_utc": "2026-10-01T03:00:00Z"})
        writer.writerow({"decision_id": 4, "action": "report", "start_utc": "2026-10-02T00:00:00Z"})
    (result / "summary.json").write_text(json.dumps({
        "card": "sample", "components": {"report_settlement": settlement},
    }), encoding="utf-8")
    return card, result


def test_audit_classifies_repaired_and_ended_faults(tmp_path):
    card, result = _fixture(tmp_path, -200)
    row = audit_card(card, result)
    assert (row["correct"], row["false"], row["settlement"]) == (1, 2, -200)
    assert row["reports"][0]["hours_after_fault_start"] == 2
    aggregate = audit(tmp_path / "runs", tmp_path / "cards", ["candidate"])["variants"]["candidate"]
    assert (aggregate["cards_with_fault"], aggregate["reports"], aggregate["correct"], aggregate["false"]) == (1, 3, 1, 2)


def test_audit_rejects_score_mismatch(tmp_path):
    card, result = _fixture(tmp_path, 100)
    with pytest.raises(ValueError, match="report settlement mismatch"):
        audit_card(card, result)
