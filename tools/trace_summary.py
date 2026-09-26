#!/usr/bin/env python3
"""Summarize observer telemetry lines from an agent log.

The platform may truncate ``agent.log`` and may prefix lines while archiving
it.  This parser therefore searches for the telemetry marker inside each line
and ignores incomplete JSON records instead of failing the whole report.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter
from collections.abc import Iterable
from pathlib import Path
from typing import TextIO

MARKER = "observer: telemetry "
TIMING_FIELDS = ("input_wait_ms", "policy_ms", "response_ms", "stdout_ms")


def iter_events(lines: Iterable[str]) -> tuple[list[dict], int]:
    """Return valid telemetry objects and the number of malformed candidates."""
    events: list[dict] = []
    malformed = 0
    for line in lines:
        start = line.find(MARKER)
        if start < 0:
            continue
        raw = line[start + len(MARKER):].strip()
        try:
            event = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            malformed += 1
            continue
        if not isinstance(event, dict) or not isinstance(event.get("event"), str):
            malformed += 1
            continue
        events.append(event)
    return events, malformed


def _number(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        result = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _stats(values: list[float]) -> dict[str, float | int]:
    if not values:
        return {"count": 0}
    ordered = sorted(values)

    def percentile(fraction: float) -> float:
        position = (len(ordered) - 1) * fraction
        lower = math.floor(position)
        upper = math.ceil(position)
        if lower == upper:
            return ordered[lower]
        weight = position - lower
        return ordered[lower] + (ordered[upper] - ordered[lower]) * weight

    return {
        "count": len(values),
        "mean": sum(values) / len(values),
        "p50": percentile(0.50),
        "p95": percentile(0.95),
        "max": max(values),
    }


def summarize(events: list[dict], malformed: int = 0) -> dict:
    """Build a JSON-serializable aggregate from parsed telemetry events."""
    counts = Counter(str(event["event"]) for event in events)
    decisions = [event for event in events if event.get("event") == "decision_trace" and event.get("kind") == "decision"]
    sequences = [int(value) for event in decisions if isinstance((value := event.get("sequence")), int)]
    timings = {
        field: _stats([value for event in decisions if (value := _number(event.get(field))) is not None])
        for field in TIMING_FIELDS
    }
    actions = Counter(str(event["action"]) for event in decisions if event.get("action") is not None)
    plans = [value for event in events if (value := _number(event.get("plans"))) is not None]
    runtime = next((event for event in events if event.get("event") == "runtime"), None)
    config = next((event for event in events if event.get("event") == "telemetry_config"), None)
    final = next((event for event in reversed(events) if event.get("event") in {"final", "telemetry_final"}), None)
    result: dict[str, object] = {
        "event_counts": dict(sorted(counts.items())),
        "malformed_records": malformed,
        "decision_count": len(decisions),
        "actions": dict(sorted(actions.items())),
        "timing_ms": timings,
        "plans_max": max(plans) if plans else 0,
        "first_sequence": min(sequences) if sequences else None,
        "last_sequence": max(sequences) if sequences else None,
        "runtime": runtime,
        "telemetry_config": config,
        "final": final,
    }
    if sequences:
        result["sequence_gaps"] = sorted(set(range(min(sequences), max(sequences) + 1)) - set(sequences))
    else:
        result["sequence_gaps"] = []
    for field in ("wall_s", "cpu_s", "rss_kib"):
        value = next((_number(event.get(field)) for event in reversed(events) if field in event), None)
        if value is not None:
            result[f"last_{field}"] = value
    return result


def _open_log(path: str) -> tuple[TextIO, bool]:
    if path == "-":
        return sys.stdin, False
    return Path(path).open(encoding="utf-8", errors="replace"), True


def _print_text(summary: dict) -> None:
    timing = summary["timing_ms"]
    print(f"events: {sum(summary['event_counts'].values())}  malformed: {summary['malformed_records']}")
    print(f"decisions: {summary['decision_count']}  sequences: {summary['first_sequence']}..{summary['last_sequence']}  gaps: {len(summary['sequence_gaps'])}")
    print(f"actions: {summary['actions']}")
    for field in TIMING_FIELDS:
        stats = timing[field]
        if stats["count"]:
            print(f"{field}: mean={stats['mean']:.3f}ms p50={stats['p50']:.3f}ms p95={stats['p95']:.3f}ms max={stats['max']:.3f}ms")
    print(f"plans_max: {summary['plans_max']}")
    for field in ("last_wall_s", "last_cpu_s", "last_rss_kib"):
        if field in summary:
            print(f"{field}: {summary[field]}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("log", nargs="?", default="-", help="agent.log path, or - for stdin")
    parser.add_argument("--json", action="store_true", dest="as_json", help="emit machine-readable JSON")
    args = parser.parse_args(argv)
    handle, should_close = _open_log(args.log)
    try:
        events, malformed = iter_events(handle)
    finally:
        if should_close:
            handle.close()
    summary = summarize(events, malformed)
    if args.as_json:
        print(json.dumps(summary, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    else:
        _print_text(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
