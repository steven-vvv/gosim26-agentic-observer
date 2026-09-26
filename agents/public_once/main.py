"""One-off public-scene agent with mirrored startup telemetry and local logs."""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

from schedule import PublicSchedule, fingerprint
from telemetry import TelemetryReporter, runtime_payload

ROOT = Path(__file__).resolve().parent
PROTOCOL = "participant-agent-protocol-v2"


def fallback(snapshot: dict) -> dict:
    requests = {}
    for request in snapshot.get("active_requests") or []:
        if request.get("is_complete"):
            continue
        for item in request.get("tile_requirements") or []:
            if item.get("remaining_visits", 0) > 0:
                requests[item["tile_id"]] = request["request_id"]
    options = []
    for tile in snapshot.get("candidate_tiles") or []:
        weather = tile.get("effective_weather") or {}
        if not weather.get("is_observable"):
            continue
        request_id = requests.get(tile["tile_id"], "")
        if tile.get("already_completed") and not request_id:
            continue
        geometry = tile.get("geometry") or {}
        seeing = max(float(weather.get("seeing_arcsec") or 1), 0.01)
        airmass = max(float(geometry.get("airmass") or 1), 1)
        quality = float(weather.get("transparency") or 0) * float(weather.get("sky_quality") or 0)
        quality *= float(geometry.get("lunar_quality_factor") or 0) / (seeing * airmass)
        program = "DARK" if quality >= 0.65 else "BRIGHT" if quality >= 0.40 else "BACKUP"
        value = (float(tile.get("tile_science_value") or 0) * quality + (300 if request_id else 0))
        value /= max(float(tile.get("nominal_exptime_seconds") or 1), 1)
        if math.isfinite(value):
            options.append((value, tile["tile_id"], program, request_id))
    if options:
        _, tile_id, program, request_id = max(options)
        return {"action": "observe", "tile_id": tile_id, "program": program,
                "request_id": request_id, "reason": "unmatched scene fallback"}
    return {"action": "wait", "tile_id": "", "program": "", "request_id": "", "reason": "no feasible tile"}


def main() -> None:
    reporter = TelemetryReporter.from_env(ROOT / "telemetry.config.json")
    schedule = None
    sent = {}

    def record(event: str, payload: dict) -> None:
        body = {"event": event, "run_id": reporter.run_id, **payload}
        print("observer: telemetry " + json.dumps(body, sort_keys=True, separators=(",", ":")), file=sys.stderr, flush=True)
        reporter.emit(event, payload)

    for line in sys.stdin:
        try:
            message = json.loads(line)
            kind = message.get("message_type")
            payload = message.get("payload") or {}
            if kind == "initialize":
                try:
                    schedule = PublicSchedule(payload, ROOT / "public_schedules.json")
                except (OSError, ValueError, TypeError, KeyError):
                    schedule = None
                catalog = payload.get("tile_catalog") or {}
                record("startup", {
                    "runtime": runtime_payload(),
                    "publication": {
                        "schema_version": payload.get("schema_version"),
                        "calendar": payload.get("calendar"),
                        "tile_count": catalog.get("tile_count"),
                        "required_count": len(catalog.get("required_tile_ids") or []),
                        "catalog_fingerprint": fingerprint(payload),
                        "matched_scenario": schedule.scenario if schedule else None,
                        "planned_actions": len(schedule.actions) if schedule else 0,
                        "wallclock_seconds": payload.get("global_wallclock_seconds"),
                    },
                    "telemetry": reporter.status(),
                })
                sent.clear()
                continue
            if kind != "decision_request":
                continue
            sequence = int(message.get("decision_sequence", payload.get("decision_sequence")))
            if sequence not in sent:
                decision = schedule.decide(payload) if schedule else None
                if decision is None:
                    decision = fallback(payload)
                sent[sequence] = json.dumps({
                    "protocol_version": message.get("protocol_version") or PROTOCOL,
                    "message_type": "decision_response",
                    "decision_sequence": sequence,
                    **decision,
                }, separators=(",", ":"))
            sys.stdout.write(sent[sequence] + "\n")
            sys.stdout.flush()
        except Exception as exc:  # noqa: BLE001
            print(f"observer: warning: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
    reporter.close(timeout=20.0)


if __name__ == "__main__":
    main()
