#!/usr/bin/env python3
"""Observer agent 入口：JSON-Lines 协议循环（只用标准库，Python >= 3.9）。

进程退出会以 agent_error 终止整场运行，所以协议层只认消息、回响应，其余意外一律容忍：
不认识的协议或 schema 版本照常处理，只记一次警告；不认识的消息类型、坏掉的 JSON 跳过；
策略抛异常时退回保底动作。所有这些都只写到 stderr（平台保存为 agent.log）。
同一序号的请求重发时，原样重放第一次的响应（平台只接受相同的重试，不接受替换）。
"""
from __future__ import annotations

import json
import re
import sys
import traceback
from pathlib import Path

try:  # Local CPython evaluations may provide orjson; the submitted agent also works without it.
    import orjson as fast_json
except ImportError:
    fast_json = json

AGENT_DIR = Path(__file__).resolve().parent
if str(AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(AGENT_DIR))

from policy import ObserverPolicy, epoch, load_params
from protocol import ACCEPTED_PROTOCOL_VERSIONS, ACCEPTED_SNAPSHOT_VERSIONS, INITIAL_PUBLICATION_VERSION, decision_response

WAIT = {"action": "wait", "tile_id": "", "program": "", "request_id": "", "decision_source": "deterministic"}
SEQUENCE = re.compile(r'"decision_sequence"\s*:\s*(\d+)')
_warned: set = set()


def warn(key: str, text: str) -> None:
    """同一类警告只写一次，免得每轮都刷一行。"""
    if key not in _warned:
        _warned.add(key)
        print(f"observer: warning: {text}", file=sys.stderr, flush=True)


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


def respond(sequence: int, decision: dict, version) -> str:
    """回显请求里的协议版本（示例项目也是这样做的）；拿不到可用的版本时用 v2。"""
    response = decision_response(sequence, decision, decision.get("reports"))
    if isinstance(version, str) and version.startswith("participant-agent-protocol-"):
        response["protocol_version"] = version
    return json.dumps(response, separators=(",", ":"))


def main() -> None:
    params = load_params(AGENT_DIR)
    policy = None
    sent: dict = {}  # decision_sequence -> 已发出的响应行；一场约 1,500 条，全部保留
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            message = fast_json.loads(line)
            if not isinstance(message, dict):
                raise TypeError("message is not a JSON object")
        except Exception as exc:  # noqa: BLE001
            # 坏掉的请求也要回：否则平台一直等到时钟耗尽。能从原文认出序号就回等待
            match = SEQUENCE.search(line) if '"decision_request"' in line else None
            print(f"observer: warning: unreadable message ({exc}); "
                  f"{'answering wait' if match else 'ignored'}", file=sys.stderr, flush=True)
            if match and int(match.group(1)) not in sent:
                sequence = int(match.group(1))
                sent[sequence] = respond(sequence, {**WAIT, "reason": "unreadable request"}, None)
                if not emit(sent[sequence]):
                    return
            continue
        kind, version, payload = message.get("message_type"), message.get("protocol_version"), message.get("payload")
        if version not in ACCEPTED_PROTOCOL_VERSIONS:
            warn(f"protocol:{version}", f"unknown protocol_version {version!r}; handling as the latest known")
        if kind == "initialize":
            if not isinstance(payload, dict):
                warn("init-payload", "initialize without an object payload; ignored")
                continue
            if payload.get("schema_version") != INITIAL_PUBLICATION_VERSION:
                warn(f"init:{payload.get('schema_version')}", f"unknown initialize schema {payload.get('schema_version')!r}")
            payload.pop("target_catalog", None)  # 约 40 万行、占内存约 250 MB，策略用不到
            try:
                policy = ObserverPolicy(payload, params)
            except Exception:  # noqa: BLE001
                traceback.print_exc(file=sys.stderr)
                policy = None
            sent.clear()
            print(f"observer: params={json.dumps(params, sort_keys=True)}", file=sys.stderr, flush=True)
            continue
        if kind != "decision_request":
            warn(f"kind:{kind}", f"ignoring unknown message_type {kind!r}")
            continue
        try:
            sequence = int(message["decision_sequence"] if "decision_sequence" in message else payload["decision_sequence"])
        except Exception:  # noqa: BLE001
            warn("no-sequence", "decision_request without a usable decision_sequence; ignored")
            continue
        if sequence in sent:  # 重发：原样重放，不再调用策略（策略状态已经前进了一步）
            warn("replay", f"repeated decision_sequence {sequence}; replaying the recorded response")
            if not emit(sent[sequence]):
                return
            continue
        if not isinstance(payload, dict):
            decision = {**WAIT, "reason": "decision_request without an object payload"}
        else:
            if payload.get("schema_version") not in ACCEPTED_SNAPSHOT_VERSIONS:
                warn(f"snapshot:{payload.get('schema_version')}", f"unknown snapshot schema {payload.get('schema_version')!r}")
            try:
                decision = policy.decide(payload) if policy else fallback(payload, None)
            except Exception:  # noqa: BLE001
                traceback.print_exc(file=sys.stderr)
                decision = fallback(payload, policy)
        sent[sequence] = respond(sequence, decision, version)
        if not emit(sent[sequence]):
            return


def emit(text: str) -> bool:
    """写一行响应并立即刷新；平台已经关掉管道时安静退出。"""
    try:
        sys.stdout.write(text + "\n")
        sys.stdout.flush()
        return True
    except (BrokenPipeError, OSError):
        return False


if __name__ == "__main__":
    main()
