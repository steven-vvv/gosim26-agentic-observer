"""Run a participant or official baseline against the pinned, unmodified v4 engine."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
import time
from pathlib import Path

from v4.agent.observer4.config import load
from v4.agent.observer4.policy import Observer

from .upstream import ROOT, resolve


def source_sha256() -> str:
    digest = hashlib.sha256()
    for path in sorted((ROOT / "v4" / "agent").rglob("*.py")):
        digest.update(path.relative_to(ROOT).as_posix().encode("utf-8"))
        digest.update(path.read_bytes())
    for name in ("telemetry", "model_advisor"):
        path = ROOT / "agents" / "observer" / f"{name}.py"
        digest.update(path.relative_to(ROOT).as_posix().encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()


def baseline_factory(clone: Path):
    # The organizer archived the starter kit on 2026-10-02 (PR #180) without changing it.
    path = next(candidate for candidate in (clone / "archive" / "starter_kit_v4" / "agent", clone / "starter_kit_v4" / "agent")
                if candidate.is_dir())
    sys.path.insert(0, str(path))
    spec = importlib.util.spec_from_file_location("gosim4_official_baseline", path / "baseline_agent.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return lambda publication: module.BaselineAgent(publication)


def evaluate(card: Path, output: Path, config: dict, *, baseline=False, fast=False, wallclock=None, profile=None,
             factory=None) -> dict:
    clone = resolve()
    from challenge.v4_workflow import V4Workflow

    workflow = V4Workflow(card.resolve())
    holder = {}
    policy_seconds = 0.0
    actions = []
    events = []

    def record(event, payload):
        events.append({"event": event, "payload": payload})

    if factory is not None:
        custom = factory
        factory = lambda publication: custom(publication, record)
    else:
        factory = baseline_factory(clone) if baseline else lambda publication: Observer(publication, config, record)

    def initialize(publication):
        holder["agent"] = factory(publication)

    def decide(message, _deadline):
        nonlocal policy_seconds
        started = time.perf_counter()
        if not baseline:
            holder["agent"].sequence = message["decision_sequence"]
        action = holder["agent"].respond(message["payload"])
        policy_seconds += time.perf_counter() - started
        response = {
            "protocol_version": "participant-agent-protocol-v4",
            "message_type": "decision_response",
            "decision_sequence": message["decision_sequence"],
            "decision_source": holder["agent"].decision_source if not baseline else "official-baseline",
            **action,
        }
        actions.append(response)
        return response

    started = time.perf_counter()
    if fast:
        from .acceleration import accelerated_engine

        with accelerated_engine():
            result = workflow.run(decide, output, wallclock_seconds=wallclock, initialize=initialize)
    else:
        result = workflow.run(decide, output, wallclock_seconds=wallclock, initialize=initialize)
    elapsed = time.perf_counter() - started
    report = result["score_report"]
    trace = holder["agent"].summary() if not baseline and "agent" in holder else {}
    summary = {
        "card": card.name,
        "variant": "official-baseline" if baseline else "observer4",
        "fast_engine": fast,
        "total": report["total"],
        "components": report["components"],
        "counts": report["counts"],
        "termination_reason": result["termination_reason"],
        "engine_detail": result["termination_detail"],
        "elapsed_seconds": round(elapsed, 6),
        "policy_seconds": round(policy_seconds, 6),
        "engine_seconds": round(elapsed - policy_seconds, 6),
        "decision_requests": result["decision_requests"],
        "config_sha256": hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest(),
        "source_sha256": source_sha256(),
        "action_sha256": hashlib.sha256(json.dumps(actions, sort_keys=True).encode()).hexdigest(),
        "agent_state": trace,
    }
    (output / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if not baseline:
        (output / "agent_events.jsonl").write_text(
            "".join(json.dumps(event, sort_keys=True) + "\n" for event in events), encoding="utf-8"
        )
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--card", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=ROOT / "v4" / "agent" / "config.json")
    parser.add_argument("--patch", type=Path)
    parser.add_argument("--baseline", action="store_true")
    parser.add_argument("--fast", action="store_true")
    parser.add_argument("--wallclock", type=float)
    args = parser.parse_args()
    patch = json.loads(args.patch.read_text(encoding="utf-8")) if args.patch else None
    config = load(args.config, patch)
    if args.out.exists():
        parser.error("output already exists; choose a new run path")
    print(
        json.dumps(
            evaluate(args.card, args.out, config, baseline=args.baseline, fast=args.fast, wallclock=args.wallclock),
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
