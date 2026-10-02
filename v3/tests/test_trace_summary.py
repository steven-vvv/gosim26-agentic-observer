"""Tests for resilient platform telemetry-log summaries."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools.trace_summary import iter_events, summarize


def _line(event: dict) -> str:
    return f"prefix observer: telemetry {json.dumps(event, separators=(',', ':'))}\n"


def test_parser_skips_prefixes_and_truncated_records() -> None:
    lines = [
        "prefix observer: telemetry {\"event\":\"decision_trace\"",
        _line({"event": "runtime", "numpy": None}),
        _line({"event": "decision_trace", "kind": "decision", "sequence": 3, "action": "wait",
               "input_wait_ms": 1000, "policy_ms": 2}),
        _line({"event": "decision_trace", "kind": "decision", "sequence": 5, "action": "observe",
               "input_wait_ms": 3000, "policy_ms": 4}),
    ]
    events, malformed = iter_events(lines)
    result = summarize(events, malformed)

    assert result["malformed_records"] == 1
    assert result["decision_count"] == 2
    assert result["first_sequence"] == 3 and result["last_sequence"] == 5
    assert result["sequence_gaps"] == [4]
    assert result["actions"] == {"observe": 1, "wait": 1}
    assert result["timing_ms"]["input_wait_ms"]["p50"] == 2000


def test_summary_uses_last_runtime_counters() -> None:
    events = [
        {"event": "decision_trace", "kind": "decision", "sequence": 1, "action": "wait",
         "input_wait_ms": 10, "policy_ms": 1, "response_ms": 0.1, "stdout_ms": 0.2,
         "plans": 1, "wall_s": 2.0, "cpu_s": 0.4},
        {"event": "final", "decisions": 1, "plans": 1, "wall_s": 2.0, "cpu_s": 0.4, "rss_kib": 1234},
        {"event": "telemetry_final", "queued": 0, "sent": 1},
    ]
    result = summarize(events)

    assert result["plans_max"] == 1
    assert result["last_wall_s"] == 2.0
    assert result["last_cpu_s"] == 0.4
    assert result["last_rss_kib"] == 1234
    assert result["final"]["event"] == "telemetry_final"


def test_summary_aggregates_model_service_events() -> None:
    events = [
        {"event": "model_request", "stage": "weekly_planning", "fallback": False,
         "duration_ms": 200, "ttft_ms": 80, "generation_ttft_ms": 50, "output_tokens_per_second": 60,
         "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120}},
        {"event": "model_request", "stage": "action_decision", "fallback": True,
         "reason": "http_503", "duration_ms": 300, "ttft_ms": None, "usage": None},
    ]
    model = summarize(events)["model"]
    assert model["requests"] == 2
    assert model["by_stage"] == {"weekly_planning": 1, "action_decision": 1}
    assert model["fallbacks"] == {"http_503": 1}
    assert model["usage"] == {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120}
    assert model["ttft_ms"]["count"] == 1
    assert model["generation_ttft_ms"]["mean"] == 50
