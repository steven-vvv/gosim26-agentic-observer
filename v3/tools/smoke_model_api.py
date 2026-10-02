"""Exercise the optional advisor with synthetic inputs and print no provider payloads."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agents" / "observer"))

from model_advisor import ModelAdvisor


def main() -> int:
    advisor = ModelAdvisor.from_env(ROOT / "agents" / "observer" / "llm.config.json")
    if not advisor.enabled:
        print(json.dumps({"configured": False, "reason": "missing_model_configuration"}))
        return 2

    cases = [
        ("weekly_planning", "full_horizon", [
            {"id": "full_horizon", "horizon_nights": 14, "replan_interval_seconds": 1800,
             "prior_probability": 0.99},
            {"id": "week_focus", "horizon_nights": 7, "replan_interval_seconds": 3600,
             "prior_probability": 0.01},
        ]),
        ("action_decision", "C0", [
            {"id": "C0", "tile_id": "T00001", "program": "DARK", "expected_gain": 203.1,
             "priority_per_hour": 812.4, "prior_probability": 0.99, "seconds": 900},
            {"id": "C1", "tile_id": "T00002", "program": "DARK", "expected_gain": 201.4,
             "priority_per_hour": 805.6, "prior_probability": 0.01, "seconds": 900},
        ]),
    ]
    ok = True
    for stage, deterministic, options in cases:
        context = {
            "schema": "gosim26.observer.live_state.v1",
            "test_fixture": True,
            "fixture_source": "synthetic_local_smoke_test",
            "night_id": "N20261005",
            "decision_sequence": 1,
            "deterministic_choice": deterministic,
            "cursor": {"timestamp_utc": "2026-10-06T02:00:00Z", "slot_offset_seconds": 0},
            "current_site_weather": {"is_observable": True, "seeing_arcsec": 1.0, "transparency": 0.9},
            "active_requests": [],
            "completed_tiles": 0,
            "wallclock_budget_seconds": 3600,
            "elapsed_wall_seconds": 10,
        }
        started = time.monotonic()
        advisor.choose(stage, options, context)
        outcome = advisor.last_outcome
        accepted = not outcome.get("fallback")
        ok &= accepted
        print(json.dumps({
            "stage": stage,
            "request_succeeded": accepted,
            "fallback": outcome.get("fallback"),
            "reason": outcome.get("reason"),
            "chosen": outcome.get("choice"),
            "accepted_choice": outcome.get("accepted_choice"),
            "probabilities": outcome.get("probabilities"),
            "ttft_ms": outcome.get("ttft_ms"),
            "generation_ttft_ms": outcome.get("generation_ttft_ms"),
            "output_tokens_per_second": outcome.get("output_tokens_per_second"),
            "usage": outcome.get("usage"),
            "elapsed_ms": round((time.monotonic() - started) * 1000),
        }, sort_keys=True))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
