"""Practice-board entrypoint: replay a recorded schedule on a known card, else play live.

A schedule is chosen only when the public initialization (task card, night calendar and
targets) has the recorded fingerprint, and it is followed only while every decision
request arrives at the recorded time. Any other card, or any divergence, hands the survey
to the live observer4 policy. stdout carries protocol responses only.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / ".deps"))
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("OMP_NUM_THREADS", "1")

from observer4.config import load
from observer4.diagnostics import Diagnostics

PROTOCOL = "participant-agent-protocol-v4"


def fingerprint(publication: dict) -> str:
    material = {"task_card": publication.get("task_card"), "nights": publication["survey"]["nights"],
                "targets": publication["targets"]["rows"]}
    return hashlib.sha256(json.dumps(material, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class Replay:
    def __init__(self, schedule: dict, publication: dict, config: dict, emit):
        self.actions = schedule["actions"]
        self.times = schedule["times"]
        self.position = 0
        self.publication, self.config, self.emit = publication, config, emit
        self.live = None
        self.decision_source = "replay-v4"
        self.last_trace = {}
        self.sequence = 0
        self.pending = None
        emit("replay_selected", {"card": schedule["card"], "decisions": len(self.actions), "planned_total": schedule["total"]})

    def _go_live(self, reason: str):
        from observer4.policy import Observer

        self.emit("replay_abandoned", {"reason": reason, "position": self.position})
        self.live = Observer(self.publication, self.config, self.emit)

    def respond(self, snapshot: dict) -> dict:
        if self.live is None:
            if self.position >= len(self.actions):
                self._go_live("schedule_exhausted")
            elif snapshot.get("now_utc") != self.times[self.position]:
                self._go_live("time_mismatch")
        if self.live is not None:
            self.live.sequence = self.sequence
            action = self.live.respond(snapshot)
            self.decision_source = self.live.decision_source
            self.last_trace = self.live.last_trace
            return action
        action = self.actions[self.position]
        self.position += 1
        return {key: value for key, value in action.items() if key != "decision_sequence"}

    def summary(self) -> dict:
        if self.live is not None:
            return {"replay_position": self.position, **self.live.summary()}
        return {"replay_position": self.position, "replay_complete": self.position >= len(self.actions)}


def choose(publication: dict, config: dict, emit):
    path = ROOT / "schedules.json"
    if path.is_file():
        try:
            schedules = json.loads(path.read_text(encoding="utf-8"))
            selected = schedules.get(fingerprint(publication))
            if selected is not None:
                return Replay(selected, publication, config, emit)
            emit("replay_unknown_card", {"card": publication.get("task_card"), "known": len(schedules)})
        except Exception as error:  # noqa: BLE001 - a bad schedule file must not block the live policy
            emit("replay_error", {"error_type": type(error).__name__})
    from observer4.policy import Observer

    return Observer(publication, config, emit)


def main():
    path = Path(os.environ.get("GOSIM_V4_CONFIG", str(ROOT / "config.json")))
    try:
        config = load(path)
    except Exception as error:  # noqa: BLE001 - config failure must not crash the protocol process
        print("observer4: " + json.dumps({"event": "config_error", "error_type": type(error).__name__}), file=sys.stderr)
        config = load(ROOT / "config.json")
    diagnostics = Diagnostics(config)
    diagnostics.startup(hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest())
    policy = None
    cached_sequence = None
    cached_request = None
    cached_response = None
    termination = "eof"
    finish_payload = None
    try:
        while True:
            raw = sys.stdin.buffer.readline(32 << 20)
            if not raw:
                break
            try:
                message = json.loads(raw)
                if not isinstance(message, dict) or message.get("protocol_version") != PROTOCOL:
                    raise ValueError("unsupported protocol envelope")
            except (ValueError, UnicodeError) as error:
                diagnostics.error("protocol_error", error)
                termination = "protocol_error"
                break
            kind = message.get("message_type")
            payload = message.get("payload") or {}
            if kind == "initialize":
                if policy is not None:
                    raise ValueError("duplicate initialization")
                policy = choose(payload, config, diagnostics.emit)
                continue
            if kind == "finish":
                termination = str(payload.get("termination_reason", "finish"))
                finish_payload = payload
                diagnostics.emit("finish_received", payload)
                break
            if kind != "decision_request":
                diagnostics.emit("unknown_message", {"message_type": str(kind)}, remote=False)
                continue
            sequence = message.get("decision_sequence")
            if type(sequence) is not int or sequence < 1:
                raise ValueError("invalid decision sequence")
            fingerprint_raw = hashlib.sha256(raw).hexdigest()
            if sequence == cached_sequence:
                if fingerprint_raw != cached_request:
                    raise ValueError("conflicting duplicate decision")
                sys.stdout.write(cached_response)
                sys.stdout.flush()
                continue
            if cached_sequence is not None and sequence < cached_sequence:
                raise ValueError("out-of-order decision")
            started = time.perf_counter()
            try:
                if policy is None:
                    raise ValueError("decision before initialization")
                policy.sequence = sequence
                action = policy.respond(payload)
            except Exception as error:  # noqa: BLE001 - one failed decision must not end the survey
                diagnostics.error("policy_error", error, sequence)
                if policy is not None:
                    policy.pending = None
                action = {"action": "wait", "duration_seconds": 900}
            response = {
                "protocol_version": PROTOCOL,
                "message_type": "decision_response",
                "decision_sequence": sequence,
                "decision_source": policy.decision_source if policy else "deterministic-v4",
                **action,
            }
            encoded = json.dumps(response, separators=(",", ":"), allow_nan=False) + "\n"
            sys.stdout.write(encoded)
            sys.stdout.flush()
            cached_sequence, cached_request, cached_response = sequence, fingerprint_raw, encoded
            diagnostics.decision(sequence, action, time.perf_counter() - started, policy.last_trace if policy else {})
    except (BrokenPipeError, OSError) as error:
        diagnostics.error("io_error", error)
        termination = "io_error"
    except Exception as error:  # noqa: BLE001 - emit a bounded final report even on unexpected failure
        diagnostics.error("runtime_error", error)
        termination = "runtime_error"
    finally:
        diagnostics.finish(termination, finish_payload, policy.summary() if policy else None)


if __name__ == "__main__":
    main()
