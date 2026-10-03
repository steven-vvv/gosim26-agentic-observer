"""Exercise the submitted JSONL entrypoint and bounded diagnostics failures."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from types import SimpleNamespace

import pytest

from v4.agent.observer4.config import load
from v4.agent.observer4.diagnostics import Diagnostics
from v4.agent.observer4.policy import Observer

ROOT = Path(__file__).resolve().parents[2]
PROTOCOL = "participant-agent-protocol-v4"


@pytest.fixture
def publication():
    return {
        "schema_version": "v4-initialize-v1",
        "task_card": {"card_id": "runtime-test"},
        "site": {"latitude_deg": -24.627, "longitude_deg": -70.404, "minimum_altitude_deg": 30.0},
        "survey": {
            "start_utc": "2026-10-01T12:00:00Z",
            "end_utc": "2026-10-03T12:00:00Z",
            "slot_seconds": 900,
            "nights": [{"night_id": "N0", "night_date": "2026-10-01",
                        "observing_start_utc": "2026-10-02T00:00:00Z", "observing_end_utc": "2026-10-02T09:00:00Z"}],
        },
        "instrument": {"grid_side": 4, "n_fibers": 16, "fiber_area_deg2": 0.75, "gap_deg": 0.1,
                       "exposure": {"min_duration_seconds": 60, "max_duration_seconds": 3600}},
        "scoring": {
            "q0": 0.68, "flux_zero_point": 0.5, "exposure_zero_point_seconds": 900, "airmass_exponent": 0.6,
            "program": {"bands": {"DARK": 0.65, "BRIGHT": 0.40},
                        "multipliers": {"DARK": 1.20, "BRIGHT": 1.12, "BACKUP": 1.06}, "mismatch_multiplier": 1.0},
            "required": {"penalty_per_missing": 50, "observed_factor_threshold": 0.5},
            "uniformity": {"weight": 200, "ra_band_width_deg": 10, "observed_factor_threshold": 0.5},
        },
        "limits": {"global_wallclock_seconds": 900},
        "targets": {
            "columns": ["target_id", "ra_deg", "dec_deg", "target_class", "feature_flux", "science_weight", "required"],
            "rows": [["T0", 300, -25, "galaxy", 0.5, 2.0, True], ["T1", 300.3, -25.1, "galaxy", 0.7, 1.5, False]],
        },
    }


def envelope(kind, payload, sequence=None):
    result = {"protocol_version": PROTOCOL, "message_type": kind, "payload": payload}
    if sequence is not None:
        result["decision_sequence"] = sequence
    return result


def decision(sequence=1, now="2026-10-01T12:00:00Z"):
    return envelope("decision_request", {"now_utc": now, "new_messages": [], "wallclock": {"remaining_seconds": 900}}, sequence)


def run_main(tmp_path, messages, *, config_text=None):
    config_path = tmp_path / "config.json"
    config = load(ROOT / "v4/agent/config.json", {"observability": {"remote": {"enabled": False}}})
    config_path.write_text(config_text if config_text is not None else json.dumps(config), encoding="utf-8")
    env = {**os.environ, "GOSIM_V4_CONFIG": str(config_path), "TELEMETRY_DISABLE": "1", "OBSERVABILITY_LOCAL_ENABLED": "1"}
    result = subprocess.run(
        [sys.executable, str(ROOT / "v4/agent/main.py")],
        input="".join(json.dumps(item) + "\n" if isinstance(item, dict) else item + "\n" for item in messages),
        capture_output=True, text=True, timeout=15, env=env, check=False,
    )
    assert result.returncode == 0, result.stderr
    responses = [json.loads(line) for line in result.stdout.splitlines()]
    events = [json.loads(line.removeprefix("observer4: ")) for line in result.stderr.splitlines()
              if line.startswith("observer4: ")]
    finals = [event for event in events if event["event"] == "final"]
    assert len(finals) == 1
    return responses, events, finals[0]


@pytest.mark.parametrize("reason", ["survey_complete", "global_wallclock_expired"])
def test_finish_emits_one_final_and_no_extra_response(tmp_path, publication, reason):
    responses, events, final = run_main(tmp_path, [
        envelope("initialize", publication), decision(),
        envelope("finish", {"termination_reason": reason, "total": 42.0}), decision(2),
    ])
    assert len(responses) == 1
    assert responses[0]["action"] == "wait"
    assert responses[0]["until_utc"] == "2026-10-02T00:00:00Z"
    assert final["termination_reason"] == reason
    assert final["finish_payload"]["total"] == 42.0
    assert sum(event["event"] == "finish_received" for event in events) == 1


def test_duplicate_request_replays_response_without_advancing_state(tmp_path, publication):
    request = decision()
    responses, _, final = run_main(tmp_path, [envelope("initialize", publication), request, request])
    assert len(responses) == 2 and responses[0] == responses[1]
    assert final["actions"] == {"wait": 1}
    assert final["last_decision_sequence"] == 1
    assert final["termination_reason"] == "eof"


@pytest.mark.parametrize("requests", [
    [decision(), decision(1, "2026-10-01T12:00:01Z")],
    [decision(), decision(2), decision()],
])
def test_conflicting_or_out_of_order_request_ends_cleanly(tmp_path, publication, requests):
    responses, _, final = run_main(tmp_path, [envelope("initialize", publication), *requests])
    assert len(responses) == len(requests) - 1
    assert final["termination_reason"] == "runtime_error"
    assert final["errors"] == {"runtime_error": 1}


def test_bad_protocol_does_not_emit_invalid_stdout(tmp_path):
    responses, _, final = run_main(tmp_path, ["{broken-json"])
    assert responses == []
    assert final["termination_reason"] == "protocol_error"
    assert final["errors"] == {"protocol_error": 1}


def test_bad_configuration_falls_back_to_valid_defaults(tmp_path, publication):
    responses, events, final = run_main(tmp_path, [envelope("initialize", publication), decision()], config_text="{")
    assert len(responses) == 1 and responses[0]["action"] == "wait"
    assert events[0]["event"] == "config_error"
    assert final["termination_reason"] == "eof"


def test_remote_failures_preserve_final_report(monkeypatch, capsys):
    class FailingReporter:
        def __init__(self, *_args, **_kwargs):
            self.closed = False

        def emit(self, *_args, **_kwargs):
            raise OSError("offline")

        def close(self, **_kwargs):
            self.closed = True

        def send_final(self, *_args, **_kwargs):
            raise OSError("offline")

    monkeypatch.delenv("TELEMETRY_DISABLE", raising=False)
    monkeypatch.setenv("OBSERVABILITY_LOCAL_ENABLED", "1")
    monkeypatch.setenv("TELEMETRY_URL", "https://telemetry.example/v1")
    monkeypatch.setenv("TELEMETRY_TOKEN", "telemetry-key")
    monkeypatch.setattr("v4.agent.observer4.diagnostics.load_client",
                        lambda _name: SimpleNamespace(TelemetryReporter=FailingReporter))
    diagnostics = Diagnostics(load(ROOT / "v4/agent/config.json"))
    diagnostics.decision(1, {"action": "wait"}, 0.1, {})
    diagnostics.finish("eof")
    diagnostics.finish("eof")
    assert diagnostics.reporter.closed
    assert diagnostics.errors["telemetry_finish_error"] == 1
    events = [json.loads(line.removeprefix("observer4: ")) for line in capsys.readouterr().err.splitlines()]
    assert sum(event["event"] == "final" for event in events) == 1


def test_model_proxy_credentials_are_not_reused_for_telemetry(monkeypatch):
    class Reporter:
        def __init__(self, *args, **kwargs):
            raise AssertionError("model proxy credentials must not create a telemetry reporter")

    monkeypatch.delenv("TELEMETRY_URL", raising=False)
    monkeypatch.delenv("TELEMETRY_TOKEN", raising=False)
    monkeypatch.setenv("OPENAI_BASE_URL", "https://model-proxy.example/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "model-key")
    monkeypatch.setattr("v4.agent.observer4.diagnostics.load_client",
                        lambda _name: SimpleNamespace(TelemetryReporter=Reporter))
    diagnostics = Diagnostics(load(ROOT / "v4/agent/config.json"), silent=False)
    assert diagnostics.reporter is None


def test_dedicated_telemetry_credentials_are_used(monkeypatch):
    calls = []

    class Reporter:
        def __init__(self, *args, **kwargs):
            calls.append((args, kwargs))

    monkeypatch.setenv("OPENAI_BASE_URL", "https://model-proxy.example/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "model-key")
    monkeypatch.setenv("TELEMETRY_URL", "https://telemetry.example/v1")
    monkeypatch.setenv("TELEMETRY_TOKEN", "telemetry-key")
    monkeypatch.setattr("v4.agent.observer4.diagnostics.load_client",
                        lambda _name: SimpleNamespace(TelemetryReporter=Reporter))
    diagnostics = Diagnostics(load(ROOT / "v4/agent/config.json"), silent=False)
    assert diagnostics.reporter is not None
    assert calls[0][0][:2] == ("https://telemetry.example/v1", "telemetry-key")


def test_model_weekly_strategy_survives_a_new_night(publication):
    events = []
    observer = Observer(publication, load(ROOT / "v4/agent/config.json"),
                        lambda kind, payload: events.append((kind, payload)))
    observer.guidance.weekly_plan = {"strategy": "science_push", "source": "model"}
    observer.respond(decision(now="2026-10-02T00:00:00Z")["payload"])
    assert observer.planner.settings["required_bonus"] == pytest.approx(150)
    assert next(payload for kind, payload in events if kind == "night_plan")["strategy"] == "science_push"


def test_model_weekly_strategy_changes_planner_settings(publication):
    observer = Observer(publication, load(ROOT / "v4/agent/config.json"))
    observer.guidance.weekly_plan = {"strategy": "required_push", "source": "model"}
    observer.respond(decision(now="2026-10-02T00:00:00Z")["payload"])
    assert observer.planner.settings["required_bonus"] == pytest.approx(260)
    assert observer.planner.settings["required_safe_factor"] == 0.55
    assert observer.decision_source == "model-v4"


def test_jsonl_entrypoint_uses_available_model_proxy(tmp_path, publication, monkeypatch):
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append((self.path, json.loads(body["messages"][1]["content"])))
            advice = {"choice": "required_push", "probabilities": {"steady": 0.2, "required_push": 0.7, "science_push": 0.1},
                      "reason": "many required targets are still missing"}
            response = json.dumps({"choices": [{"message": {"content": json.dumps(advice)}}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response)))
            self.end_headers()
            self.wfile.write(response)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        monkeypatch.setenv("OPENAI_BASE_URL", f"http://127.0.0.1:{server.server_port}/v1")
        monkeypatch.setenv("OPENAI_API_KEY", "test-key")
        request = decision(now="2026-10-02T00:00:00Z")
        request["payload"]["latest_forecast"] = {"issued_at_utc": "2026-10-02T00:00:00Z", "notices": []}
        responses, events, final = run_main(tmp_path, [envelope("initialize", publication), request])
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
    assert responses[0]["decision_source"] == "model-v4"
    assert requests[0][0] == "/v1/chat/completions"
    assert requests[0][1]["stage"] == "weekly_planning"
    assert final["model"]["advisor"]["successes"] == 1
    assert any(event["event"] == "model_request" for event in events)


def test_public_feedback_samples_are_json_serializable(publication):
    observer = Observer(publication, load(ROOT / "v4/agent/config.json"))
    samples = observer.state.feedback(
        {"action": "observe", "observe_index": 0, "hits": [{"target_id": "T0", "score": 2.4}]},
        {"program": "DARK", "targets": [{"index": 0, "matched_probability": 0.9}]},
    )
    assert samples[0]["matched"] is True
    assert json.loads(json.dumps(samples))[0]["normalized"] == pytest.approx(1.2)
