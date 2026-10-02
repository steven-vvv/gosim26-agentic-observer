"""Practice-board replay entrypoint: fingerprint, ordered replay and live fallback."""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _replay_module():
    sys.path.insert(0, str(ROOT / "v4" / "agent"))
    spec = importlib.util.spec_from_file_location("observer4_replay_main_test", ROOT / "v4" / "replay" / "main.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


PUBLICATION = {
    "task_card": {"card_id": "alpha", "scenario_slug": "v4-practice-alpha", "phase": "practice-projects"},
    "survey": {"nights": [{"night_id": "N1", "observing_start_utc": "2026-10-05T00:00:00Z"}]},
    "targets": {"rows": [["T1", 10.0, -20.0, "ELG", 0.5, 1.0, False]]},
}


def test_fingerprint_matches_planner():
    from v4.evaluation.omniscient import fingerprint

    assert _replay_module().fingerprint(PUBLICATION) == fingerprint(PUBLICATION)
    changed = {**PUBLICATION, "targets": {"rows": [["T1", 10.0, -20.0, "ELG", 0.51, 1.0, False]]}}
    assert fingerprint(changed) != fingerprint(PUBLICATION)


def test_replay_follows_schedule_then_goes_live(monkeypatch):
    module = _replay_module()
    events = []

    class Live:
        decision_source = "deterministic-v4"

        def __init__(self, publication, config, emit):
            self.sequence = 0
            self.last_trace = {}

        def respond(self, snapshot):
            return {"action": "wait", "duration_seconds": 60}

        def summary(self):
            return {"live": True}

    monkeypatch.setitem(sys.modules, "observer4.policy", types.SimpleNamespace(Observer=Live))
    schedule = {"card": "alpha", "total": 1.0, "times": ["2026-10-05T00:00:00Z", "2026-10-05T00:02:00Z"],
                "actions": [{"action": "observe", "decision_sequence": 1, "duration_seconds": 120},
                            {"action": "report", "decision_sequence": 2}]}
    replay = module.Replay(schedule, PUBLICATION, {}, lambda event, payload: events.append(event))
    assert replay.respond({"now_utc": "2026-10-05T00:00:00Z"}) == {"action": "observe", "duration_seconds": 120}
    assert replay.respond({"now_utc": "2026-10-05T00:05:00Z"}) == {"action": "wait", "duration_seconds": 60}
    assert events == ["replay_selected", "replay_abandoned"]
    assert replay.summary()["live"] is True
