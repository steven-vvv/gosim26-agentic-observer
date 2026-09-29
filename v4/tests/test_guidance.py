"""Model decisions and fault evidence remain bounded by public state."""

from __future__ import annotations

import json
from collections import deque
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from v4.agent.observer4.advice import ModelGuidance
from v4.agent.observer4.config import load
from v4.agent.observer4.quality import QualityModel

CONFIG = Path(__file__).resolve().parents[1] / "agent" / "config.json"


class FakeAdvisor:
    enabled = True

    def __init__(self, choice):
        self.choice = choice
        self.requests = []

    def choose(self, stage, options, context):
        self.requests.append((stage, options, context))
        return self.choice

    def status(self):
        return {"calls": len(self.requests)}


def guidance(choice=None):
    config = load(CONFIG, {"model": {"enabled": True}})
    task = SimpleNamespace(
        nights=[SimpleNamespace(date=f"2026-10-0{i + 1}") for i in range(4)],
        scoring={"required": {"observed_factor_threshold": 0.5}, "program": {}},
        latitude=30.0,
        longitude=100.0,
        minimum_altitude=20.0,
        start=1.0,
        end=4.0,
        slot_seconds=900,
        grid=SimpleNamespace(n=16),
        minimum_exposure=120,
        maximum_exposure=3600,
        ids=["T0", "T1"],
        required=np.array([True, False]),
        weight=np.array([3.0, 1.0]),
    )
    state = SimpleNamespace(
        factor_lower=np.array([0.0, 0.0]),
        best_score=np.array([0.0, 0.0]),
        required_missing=lambda: 1,
    )
    events = []
    guide = object.__new__(ModelGuidance)
    guide.task, guide.state = task, state
    guide.quality = SimpleNamespace(status=lambda: {"quality_scale": 0.7})
    guide.pointing = SimpleNamespace(status=lambda: {"uncertainty_deg": 0.1})
    guide.settings, guide.policy = config["model"], config["policy"]
    guide.emit = lambda event, payload: events.append((event, payload))
    guide.advisor = FakeAdvisor(choice)
    guide.last_forecast = None
    guide.action_nights = set()
    guide.weekly_plan = {"strategy": "balanced", "source": "deterministic"}
    return guide, events


def snapshot():
    return {
        "now_utc": "2026-10-03T00:00:00Z",
        "running_total": 17.0,
        "latest_bulletin": {"notices": []},
        "latest_forecast": {"issued_at_utc": "2026-10-03T00:00:00Z", "notices": []},
        "last_result": {"action": "wait"},
    }


def candidates(second_merit):
    return [
        {
            "merit": merit,
            "action": {"action": "observe", "pointing": {"alt_deg": 45, "az_deg": 90 + index},
                       "assignments": {"0": f"T{index}"}, "duration_seconds": 600, "program": "DARK"},
            "pending": {"targets": [{"index": index, "predicted_factor": 0.7}]},
        }
        for index, merit in enumerate((10.0, second_merit))
    ]


def test_weekly_plan_uses_deterministic_fallback_and_only_public_context():
    guide, events = guidance(None)
    selected = guide.weekly(snapshot(), 2, 900, 7)
    assert selected == "coverage_focus"
    assert guide.weekly_plan["source"] == "deterministic"
    assert guide.weekly(snapshot(), 2, 900, 8) is None
    stage, options, context = guide.advisor.requests[0]
    assert stage == "weekly_planning"
    assert context["deterministic_choice"] == "coverage_focus"
    assert {option["id"] for option in options} == {"balanced", "coverage_focus"}
    assert context["current_state"]["latest_forecast"]["issued_at_utc"] == snapshot()["latest_forecast"]["issued_at_utc"]
    assert "truth" not in json.dumps(context)
    assert events[-1][0] == "weekly_plan"


def test_action_model_can_select_feasible_second_option_once_per_night():
    guide, _ = guidance("C1")
    assert guide.action(snapshot(), 0, 900, 9, candidates(9.8)) == 1
    assert guide.action(snapshot(), 0, 900, 10, candidates(9.8)) is None
    stage, options, context = guide.advisor.requests[0]
    assert stage == "action_decision"
    assert context["eligible_choices"] == ["C0", "C1"]
    assert options[1]["action"]["assignments"] == {"0": "T1"}
    assert options[1]["predicted_targets"][0]["prior_best_score"] == 0.0


def test_action_rejects_second_option_outside_merit_tolerance():
    guide, _ = guidance("C1")
    assert guide.action(snapshot(), 0, 900, 9, candidates(8.0)) is None
    assert guide.advisor.requests[0][2]["eligible_choices"] == ["C0"]


def test_public_weather_forecast_blocks_fault_report_from_affected_nights():
    guide, _ = guidance()
    guide.task.scoring["program"] = {"bands": {"DARK": 1.0}}
    config = load(CONFIG, {"fault": {"minimum_samples": 4, "confirm_nights": 2}})
    quality = QualityModel(guide.task, config)
    quality.clean = deque([(1.0, 0, 1.0)] * 8 + [(2.0, 2, 0.4)] * 2 + [(3.0, 3, 0.4)] * 2)
    quality.band_checks = deque([(2.0, 2, "DARK", True, 2.0)] * 4)
    assert quality.report_evidence(3.0) is not None
    quality.messages({"latest_forecast": {"notices": [
        {"event_kind": "overcast", "direction": "ALL", "nights": [guide.task.nights[2].date]}
    ]}}, 3.0)
    assert quality.report_evidence(3.0) is None
