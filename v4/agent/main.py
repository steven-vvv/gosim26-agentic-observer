"""Persistent v4 participant entrypoint; stdout contains protocol responses only."""

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
from observer4.policy import Observer

PROTOCOL = "participant-agent-protocol-v4"


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
                policy = Observer(payload, config, diagnostics.emit)
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
            fingerprint = hashlib.sha256(raw).hexdigest()
            if sequence == cached_sequence:
                if fingerprint != cached_request:
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
                "decision_source": "deterministic-v4",
                **action,
            }
            encoded = json.dumps(response, separators=(",", ":"), allow_nan=False) + "\n"
            sys.stdout.write(encoded)
            sys.stdout.flush()
            cached_sequence, cached_request, cached_response = sequence, fingerprint, encoded
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
