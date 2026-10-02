#!/usr/bin/env python3
"""Observer agent 入口：JSON-Lines 协议循环（只用标准库，Python >= 3.9）。

策略出任何异常都只记到 stderr（平台保存为 agent.log），并退回保底动作；进程退出会以 agent_error 终止整场运行。
"""
from __future__ import annotations

import json
import sys
import traceback
from pathlib import Path

AGENT_DIR = Path(__file__).resolve().parent
if str(AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(AGENT_DIR))

from policy import ObserverPolicy, epoch, load_params
from protocol import decision_response, parse_platform_message

WAIT = {"action": "wait", "tile_id": "", "program": "", "request_id": "", "decision_source": "deterministic"}


def fallback(snapshot: dict, policy: ObserverPolicy | None) -> dict:
    """保底动作：第一个能观测、能在窗口内拍完的新天区；没有就等待。"""
    try:
        now = epoch(snapshot["cursor"]["timestamp_utc"])
        for c in snapshot.get("candidate_tiles") or []:
            weather = c["effective_weather"]
            if c.get("already_completed") or not weather.get("is_observable"):
                continue
            if now + float(c["nominal_exptime_seconds"]) > epoch(c["window_end_utc"]):
                continue
            band = policy._band(policy._quality(weather, c["geometry"])) if policy else "BACKUP"
            return {"action": "observe", "tile_id": str(c["tile_id"]), "program": band, "request_id": "",
                    "reason": "fallback after policy error", "decision_source": "deterministic"}
    except Exception:  # noqa: BLE001
        traceback.print_exc(file=sys.stderr)
    return {**WAIT, "reason": "fallback after policy error"}


def main() -> None:
    params = load_params(AGENT_DIR)
    policy = None
    for line in sys.stdin:
        if not line.strip():
            continue
        message = json.loads(line)
        kind, payload = parse_platform_message(message)
        if kind == "initialize":
            try:
                policy = ObserverPolicy(payload, params)
            except Exception:  # noqa: BLE001
                traceback.print_exc(file=sys.stderr)
            print(f"observer: params={json.dumps(params, sort_keys=True)}", file=sys.stderr, flush=True)
            continue
        try:
            decision = policy.decide(payload) if policy else fallback(payload, None)
        except Exception:  # noqa: BLE001
            traceback.print_exc(file=sys.stderr)
            decision = fallback(payload, policy)
        response = decision_response(int(message["decision_sequence"]), decision, decision.get("reports"))
        sys.stdout.write(json.dumps(response, separators=(",", ":")) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
