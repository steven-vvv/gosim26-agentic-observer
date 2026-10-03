"""Model decisions and fault evidence remain bounded by public state."""

from __future__ import annotations

import io
import json
from collections import deque
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import numpy as np
import pytest

from v4.agent.observer4.advice import ModelGuidance
from v4.agent.observer4.config import load
from v4.agent.observer4.quality import QualityModel
from v4.agent.observer4.services import load_client

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
    guide.weekly_plan = {"strategy": "steady", "source": "deterministic"}
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
    assert selected == "steady"
    assert guide.weekly_plan["source"] == "deterministic"
    assert guide.weekly(snapshot(), 2, 900, 8) is None
    stage, options, context = guide.advisor.requests[0]
    assert stage == "weekly_planning"
    assert context["deterministic_choice"] == "steady"
    assert context["eligible_choices"] == ["steady", "required_push", "science_push"]
    assert [option["id"] for option in options] == ["steady", "required_push", "science_push"]
    assert [(row["required_bonus"], row["required_safe_factor"]) for row in options] == [
        (200, 0.52), (260, 0.55), (150, 0.52)]
    assert all(option["effect"] for option in options)
    assert context["current_state"]["latest_forecast"]["issued_at_utc"] == snapshot()["latest_forecast"]["issued_at_utc"]
    assert "truth" not in json.dumps(context)
    assert "prior_probability" not in json.dumps(options)
    assert events[-1][0] == "weekly_plan"


def test_model_context_includes_only_public_active_request_progress():
    guide, _ = guidance(None)
    current = snapshot()
    request = {"request_id": "R1", "target_ids": ["T0"], "remaining_count": 1,
               "deadline_utc": "2026-10-04T00:00:00Z", "completion_reward": 100.0}
    current["active_requests"] = [request]
    assert guide.context(current, 0, 900, 1)["current_state"]["active_requests"] == [request]


def test_weekly_plan_runs_when_model_is_disabled():
    config = load(CONFIG)
    task = SimpleNamespace(
        nights=[SimpleNamespace(date=f"2026-10-0{i + 1}") for i in range(4)],
        required=np.array([True, False]),
    )
    state = SimpleNamespace(required_missing=lambda: 1)
    guide = object.__new__(ModelGuidance)
    guide.task, guide.state = task, state
    guide.settings, guide.policy = config["model"], config["policy"]
    guide.emit = lambda *_args, **_kwargs: None
    guide.advisor = None
    guide.last_forecast = None
    guide.action_nights = set()
    guide.weekly_plan = {"strategy": "steady", "source": "deterministic"}
    assert guide.weekly(snapshot(), 2, 900, 7) == "steady"
    assert guide.weekly_plan["source"] == "deterministic"


def test_weekly_deficit_alarm_is_evidence_for_the_model():
    guide, _ = guidance(None)
    guide.weekly(snapshot(), 3, 900, 7)
    assert guide.advisor.requests[0][2]["required_deficit_alarm"] is True
    assert guide.weekly_plan["strategy"] == "steady"


@pytest.mark.parametrize("choice", ["required_push", "science_push", "steady"])
def test_weekly_model_choice_changes_effective_strategy(choice):
    guide, _ = guidance(choice)
    assert guide.weekly(snapshot(), 0, 900, 7) == choice
    assert guide.weekly_plan["source"] == "model"
    assert guide.advisor.requests[0][2]["deterministic_choice"] == "steady"


def test_weekly_ignores_unknown_model_strategy():
    guide, _ = guidance("balanced")
    assert guide.weekly(snapshot(), 0, 900, 7) == "steady"
    assert guide.weekly_plan["source"] == "deterministic"


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
    assert not guide.advisor.requests
    assert guide.action(snapshot(), 0, 900, 10, candidates(9.8)) == 1
    assert guide.advisor.requests[0][2]["eligible_choices"] == ["C0", "C1"]


def test_real_model_client_drives_both_planning_and_action(monkeypatch):
    client = load_client("model_advisor")
    assert client is not None
    requests = []
    call_ids = []

    def transport(request, *, timeout):
        body = json.loads(request.data)
        payload = json.loads(body["messages"][1]["content"])
        requests.append((request.full_url, payload["stage"], timeout))
        call_ids.append({name.lower(): value for name, value in request.header_items()}["idempotency-key"])
        if payload["stage"] == "weekly_planning":
            advice = {"choice": "science_push", "probabilities": {"steady": 0.3, "required_push": 0.1, "science_push": 0.6},
                      "reason": "required targets are on track"}
        else:
            advice = {"choice": "C1", "probabilities": {"C0": 0.45, "C1": 0.55}}
        return io.BytesIO(json.dumps({"choices": [{"message": {"content": json.dumps(advice)}}]}).encode())

    monkeypatch.setenv("OPENAI_BASE_URL", "http://model.test/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(client.urllib.request, "urlopen", transport)
    stub, events = guidance(None)
    guide = ModelGuidance(stub.task, stub.state, stub.quality, stub.pointing,
                          load(CONFIG), lambda event, payload: events.append((event, payload)))

    assert guide.weekly(snapshot(), 0, 900, 7) == "science_push"
    assert guide.weekly_plan["source"] == "model"
    assert guide.advisor.last_outcome["model_reason"] == "required targets are on track"
    assert guide.action(snapshot(), 0, 900, 8, candidates(9.8)) == 1
    assert guide.advisor.status()["successes"] == 2
    assert [stage for _, stage, _ in requests] == ["weekly_planning", "action_decision"]
    assert all(url == "http://model.test/v1/chat/completions" for url, _, _ in requests)
    assert all(timeout == 10 for _, _, timeout in requests)
    assert len(set(call_ids)) == 2
    assert all(UUID(value).version == 4 for value in call_ids)


def test_failure_streak_pauses_model_until_next_weekly_forecast(monkeypatch):
    client = load_client("model_advisor")
    outcomes = ["fail", "fail", "ok"]
    stages = []

    def transport(request, *, timeout):
        payload = json.loads(json.loads(request.data)["messages"][1]["content"])
        stages.append(payload["stage"])
        if outcomes.pop(0) == "fail":
            raise client.urllib.error.URLError("provider down")
        advice = {"choice": "science_push", "probabilities": {"steady": 0.3, "required_push": 0.1, "science_push": 0.6}}
        return io.BytesIO(json.dumps({"choices": [{"message": {"content": json.dumps(advice)}}]}).encode())

    monkeypatch.setenv("OPENAI_BASE_URL", "http://model.test/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(client.urllib.request, "urlopen", transport)
    stub, events = guidance(None)
    guide = ModelGuidance(stub.task, stub.state, stub.quality, stub.pointing,
                          load(CONFIG), lambda event, payload: events.append((event, payload)))

    assert guide.weekly(snapshot(), 0, 900, 1) == "steady"
    assert guide.weekly_plan["source"] != "model"
    assert guide.action(snapshot(), 0, 900, 2, candidates(9.8)) is None
    assert not guide.available(900)  # two failures in a row pause the model
    assert guide.action(snapshot(), 1, 900, 3, candidates(9.8)) is None
    later = dict(snapshot(), latest_forecast={"issued_at_utc": "2026-10-10T00:00:00Z", "notices": []})
    assert guide.weekly(later, 1, 900, 4) == "science_push"
    assert guide.available(900)
    assert stages == ["weekly_planning", "action_decision", "weekly_planning"]


def test_advice_parser_repairs_a_stray_quote_after_a_number():
    client = load_client("model_advisor")
    parse = client.ModelAdvisor._json_content
    reply = '{"choice":"C0","probabilities":{"C0":0.55,"C1":0.45"},"reason":"C1\'s field is close"}'
    assert parse(reply) == {"choice": "C0", "probabilities": {"C0": 0.55, "C1": 0.45}, "reason": "C1's field is close"}
    assert parse('```json\n{"choice": "C1", "probabilities": {"C0": 0.4, "C1": 0.6}}\n```')["choice"] == "C1"
    assert parse('no advice') is None and parse('[1, 2]') is None


def test_public_weather_forecast_blocks_fault_report_from_affected_nights():
    guide, _ = guidance()
    guide.task.scoring["program"] = {"bands": {"DARK": 1.0}}
    config = load(CONFIG, {"fault": {"minimum_samples": 4, "confirm_nights": 2, "forecast_guard": True}})
    quality = QualityModel(guide.task, config)
    quality.clean = deque([(1.0, 0, 1.0)] * 8 + [(2.0, 2, 0.4)] * 2 + [(3.0, 3, 0.4)] * 2)
    quality.band_checks = deque([(2.0, 2, "DARK", True, 2.0)] * 4)
    assert quality.report_evidence(3.0) is not None
    quality.messages({"latest_forecast": {"notices": [
        {"event_kind": "overcast", "direction": "ALL", "nights": [guide.task.nights[2].date]}
    ]}}, 3.0)
    assert quality.report_evidence(3.0) is None
