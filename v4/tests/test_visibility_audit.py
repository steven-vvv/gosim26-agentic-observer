"""Geometric visibility uses only public calendar and target coordinates."""

from __future__ import annotations

import csv
import json
from datetime import datetime, timedelta, timezone

import pytest

from v4.agent.observer4.geometry import sidereal
from v4.evaluation.visibility_audit import _group_hours, _summarize, geometric_hours


def test_visibility_separates_geometric_gap_from_unassigned_visible_target(tmp_path):
    card = tmp_path / "card"
    result = tmp_path / "result" / "card"
    (card / "config").mkdir(parents=True)
    (card / "public").mkdir()
    result.mkdir(parents=True)
    start = datetime(2026, 10, 1, tzinfo=timezone.utc)
    end = start + timedelta(hours=8)
    transit_ra = sidereal((start + timedelta(hours=4)).timestamp(), 0)
    with (card / "public/targets.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=["target_id", "ra_deg", "dec_deg", "required"])
        writer.writeheader()
        writer.writerows([
            {"target_id": "visible", "ra_deg": transit_ra, "dec_deg": 0, "required": "true"},
            {"target_id": "hidden", "ra_deg": (transit_ra + 180) % 360, "dec_deg": 0, "required": "true"},
        ])
    with (card / "public/v4_night_calendar.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=["observing_start_utc", "observing_end_utc"])
        writer.writeheader()
        writer.writerow({"observing_start_utc": start.isoformat(), "observing_end_utc": end.isoformat()})
    (card / "config/v4_scenario.json").write_text(json.dumps({
        "site": {"latitude_deg": 0, "longitude_deg": 0}, "minimum_altitude_deg": 30,
    }), encoding="utf-8")
    (card / "config/v4_score_config.json").write_text(json.dumps({
        "required": {"observed_factor_threshold": 0.5},
    }), encoding="utf-8")
    (result / "summary.json").write_text(json.dumps({
        "card": "card", "counts": {"required_missing": 1},
    }), encoding="utf-8")
    (result / "actions.jsonl").write_text(json.dumps({
        "action": "observe", "assignments": {"0": "visible"},
    }) + "\n", encoding="utf-8")
    with (result / "observations.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=["target_id", "factor", "valid"])
        writer.writeheader()
        writer.writerow({"target_id": "visible", "factor": 0.6, "valid": "true"})

    hours = geometric_hours(card)
    assert hours["visible"] == pytest.approx(8, abs=0.1)
    assert hours["hidden"] == pytest.approx(0)
    groups = _group_hours(card, result)
    assert groups["never_assigned"] == [0]
    assert groups["reached_threshold"] == [hours["visible"]]
    assert _summarize(groups["never_assigned"])["bins"]["under_1h"] == 1
