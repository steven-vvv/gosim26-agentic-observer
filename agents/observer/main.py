#!/usr/bin/env python3
"""Observer agent 入口：JSON-Lines 协议循环（只用标准库，Python >= 3.9）。

进程退出会以 agent_error 终止整场运行，所以协议层只认消息、回响应，其余意外一律容忍：
不认识的协议或 schema 版本照常处理，只记一次警告；不认识的消息类型、坏掉的 JSON 跳过；
策略抛异常时退回保底动作。所有这些都只写到 stderr（平台保存为 agent.log）。
同一序号的请求重发时，原样重放第一次的响应（平台只接受相同的重试，不接受替换）。
"""
from __future__ import annotations

import atexit
import json
import re
import sys
import time
import traceback
from pathlib import Path

try:
    import resource
except ImportError:  # pragma: no cover - resource is Unix-only
    resource = None

AGENT_DIR = Path(__file__).resolve().parent
DEPS_DIR = AGENT_DIR / ".deps"
if DEPS_DIR.is_dir():
    # Complete-project builds install optional wheels here because the
    # platform's user site may be read-only.  The source checkout is unchanged.
    sys.path.insert(0, str(DEPS_DIR))

try:  # Local CPython evaluations may provide orjson; the submitted agent also works without it.
    import orjson as fast_json
except ImportError:
    fast_json = json

if str(AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(AGENT_DIR))

from policy import ObserverPolicy, epoch, load_params
from protocol import ACCEPTED_PROTOCOL_VERSIONS, ACCEPTED_SNAPSHOT_VERSIONS, INITIAL_PUBLICATION_VERSION, decision_response
from telemetry import RunSummary, TelemetryReporter, TerminalStep, runtime_payload

WAIT = {"action": "wait", "tile_id": "", "program": "", "request_id": "", "decision_source": "deterministic"}
SEQUENCE = re.compile(r'"decision_sequence"\s*:\s*(\d+)')
_warned: set = set()


def _rss_kib() -> int | None:
    """Return the process peak resident set size in KiB when the platform exposes it."""
    if resource is None:
        return None
    try:
        value = float(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
        if sys.platform == "darwin":  # macOS reports bytes; Linux reports KiB.
            value /= 1024.0
        return round(value)
    except (OSError, ValueError):
        return None


def _cpu_seconds() -> float | None:
    if resource is None:
        return None
    try:
        usage = resource.getrusage(resource.RUSAGE_SELF)
        return round(float(usage.ru_utime + usage.ru_stime), 3)
    except (OSError, ValueError):
        return None


def _runtime_fields() -> dict:
    """Use one runtime snapshot for local and remote telemetry."""
    return runtime_payload()


def telemetry(event: str, **fields) -> None:
    """Emit one compact, machine-readable diagnostic line to stderr only."""
    payload = {"event": event, **fields}
    print(f"observer: telemetry {json.dumps(payload, sort_keys=True, separators=(',', ':'))}",
          file=sys.stderr, flush=True)


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
    reporter = TelemetryReporter.from_env(AGENT_DIR / "telemetry.config.json")
    atexit.register(reporter.close)
    policy = None
    sent: dict = {}  # decision_sequence -> 已发出的响应行；一场约 1,500 条，全部保留
    started = time.monotonic()
    last_io_at = time.perf_counter()
    counters = {"messages": 0, "decisions": 0, "responses": 0, "replays": 0,
                "parse_errors": 0, "unknown_messages": 0}
    summary = RunSummary()
    terminal = TerminalStep()
    finished = False
    runtime = _runtime_fields()
    telemetry("runtime", **runtime)
    telemetry("telemetry_config", **reporter.status())
    if reporter.enabled:
        reporter.emit("telemetry_config", reporter.status())
        reporter.emit("runtime", runtime)

    def finish(response_pending: bool = False) -> None:
        nonlocal finished
        if finished:
            return
        finished = True
        reporter.close(timeout=2.0)
        planner = getattr(policy, "planner", None)
        report = {
            **counters,
            "wall_s": round(time.monotonic() - started, 3),
            "cpu_s": _cpu_seconds(),
            "rss_kib": _rss_kib(),
            "plans": getattr(planner, "plans", 0),
            "plan_s": round(getattr(planner, "plan_seconds", 0.0), 3),
            "summary": summary.report(),
            "runtime": runtime,
            "telemetry_status": reporter.status(),
            "final_response_pending": response_pending,
        }
        telemetry("final", **report)
        delivered = reporter.send_final(report, sequence=counters["decisions"], wait_seconds=5.0)
        telemetry("final_delivery", remote_delivered=delivered, **reporter.status())

    for line in sys.stdin:
        if not line.strip():
            continue
        input_received = time.perf_counter()
        input_wait_ms = round(max(0.0, (input_received - last_io_at) * 1000.0), 3)
        counters["messages"] += 1
        try:
            message = fast_json.loads(line)
            if not isinstance(message, dict):
                raise TypeError("message is not a JSON object")
        except Exception as exc:  # noqa: BLE001
            counters["parse_errors"] += 1
            # 坏掉的请求也要回：否则平台一直等到时钟耗尽。能从原文认出序号就回等待
            match = SEQUENCE.search(line) if '"decision_request"' in line else None
            print(f"observer: warning: unreadable message ({exc}); "
                  f"{'answering wait' if match else 'ignored'}", file=sys.stderr, flush=True)
            if match and int(match.group(1)) not in sent:
                sequence = int(match.group(1))
                sent[sequence] = respond(sequence, {**WAIT, "reason": "unreadable request"}, None)
                if not emit(sent[sequence]):
                    return
                counters["responses"] += 1
                last_io_at = time.perf_counter()
            continue
        kind, version, payload = message.get("message_type"), message.get("protocol_version"), message.get("payload")
        if version not in ACCEPTED_PROTOCOL_VERSIONS:
            warn(f"protocol:{version}", f"unknown protocol_version {version!r}; handling as the latest known")
        if kind == "initialize":
            if not isinstance(payload, dict):
                warn("init-payload", "initialize without an object payload; ignored")
                continue
            catalog = payload.get("tile_catalog") or {}
            telemetry("initialize", protocol=version, schema=payload.get("schema_version"),
                      tiles=len(catalog.get("tiles") or []), regions=len(catalog.get("region_ids") or []),
                      required=len(catalog.get("required_tile_ids") or []),
                      calendar_nights=(payload.get("calendar") or {}).get("night_count"))
            reporter.emit("initialize", {
                "protocol": version,
                "schema_version": payload.get("schema_version"),
                "tiles": len(catalog.get("tiles") or []),
                "regions": len(catalog.get("region_ids") or []),
                "required": len(catalog.get("required_tile_ids") or []),
                "calendar_nights": (payload.get("calendar") or {}).get("night_count"),
            })
            if payload.get("schema_version") != INITIAL_PUBLICATION_VERSION:
                warn(f"init:{payload.get('schema_version')}", f"unknown initialize schema {payload.get('schema_version')!r}")
            terminal.initialize(payload)
            payload.pop("target_catalog", None)  # 约 40 万行、占内存约 250 MB，策略用不到
            try:
                policy = ObserverPolicy(payload, params)
            except Exception:  # noqa: BLE001
                traceback.print_exc(file=sys.stderr)
                policy = None
            sent.clear()
            print(f"observer: params={json.dumps(params, sort_keys=True)}", file=sys.stderr, flush=True)
            last_io_at = time.perf_counter()
            continue
        if kind != "decision_request":
            counters["unknown_messages"] += 1
            warn(f"kind:{kind}", f"ignoring unknown message_type {kind!r}")
            continue
        try:
            sequence = int(message["decision_sequence"] if "decision_sequence" in message else payload["decision_sequence"])
        except Exception:  # noqa: BLE001
            warn("no-sequence", "decision_request without a usable decision_sequence; ignored")
            continue
        if sequence in sent:  # 重发：原样重放，不再调用策略（策略状态已经前进了一步）
            counters["replays"] += 1
            warn("replay", f"repeated decision_sequence {sequence}; replaying the recorded response")
            replay_started = time.perf_counter()
            emitted = emit(sent[sequence])
            replay_trace = {
                "kind": "replay",
                "sequence": sequence,
                "input_wait_ms": input_wait_ms,
                "stdout_ms": round((time.perf_counter() - replay_started) * 1000.0, 3),
                "response_bytes": len(sent[sequence].encode("utf-8")),
            }
            telemetry("decision_trace", **replay_trace)
            reporter.emit("decision_trace", replay_trace, sequence=sequence)
            last_io_at = time.perf_counter()
            if not emitted:
                return
            counters["responses"] += 1
            continue
        if finished:
            finished = False
        counters["decisions"] += 1
        decision_started = time.perf_counter()
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
        policy_ms = (time.perf_counter() - decision_started) * 1000.0
        response_started = time.perf_counter()
        sent[sequence] = respond(sequence, decision, version)
        response_ms = (time.perf_counter() - response_started) * 1000.0
        planner = getattr(policy, "planner", None)
        advisor = getattr(policy, "advisor", None)
        advisor_status = advisor.status() if advisor is not None and hasattr(advisor, "status") else {}
        trace = {
            "kind": "decision",
            "sequence": sequence,
            "input_wait_ms": input_wait_ms,
            "decision_ms": round((time.perf_counter() - decision_started) * 1000.0, 3),
            "policy_ms": round(policy_ms, 3),
            "response_ms": round(response_ms, 3),
            "stdout_ms": None,
            "request_bytes": len(line.encode("utf-8")),
            "response_bytes": len(sent[sequence].encode("utf-8")),
            "candidate_count": len(payload.get("candidate_tiles") or []) if isinstance(payload, dict) else 0,
            "active_request_count": len(payload.get("active_requests") or []) if isinstance(payload, dict) else 0,
            "action": decision.get("action"),
            "decision_source": decision.get("decision_source"),
            "model_calls": getattr(getattr(policy, "advisor", None), "calls", 0),
            "model_failures": getattr(getattr(policy, "advisor", None), "failures", 0),
            "model_advice": advisor_status.get("last_outcome", {}),
            "planning": getattr(policy, "planning", None),
            "plans": getattr(planner, "plans", 0),
            "plan_s": round(getattr(planner, "plan_seconds", 0.0), 3),
            "wall_s": round(time.monotonic() - started, 3),
            "cpu_s": _cpu_seconds(),
        }
        is_terminal = isinstance(payload, dict) and terminal.is_final(payload, decision)
        if is_terminal:
            summary.add(trace)
            telemetry("decision_trace", **trace)
            reporter.emit("decision_trace", trace, sequence=sequence)
            finish(response_pending=True)
        stdout_started = time.perf_counter()
        emitted = emit(sent[sequence])
        trace["stdout_ms"] = round((time.perf_counter() - stdout_started) * 1000.0, 3)
        counters["responses"] += 1
        if not is_terminal:
            summary.add(trace)
            telemetry("decision_trace", **trace)
            reporter.emit("decision_trace", trace, sequence=sequence)
        last_io_at = time.perf_counter()
        if not emitted:
            return
        if not is_terminal and counters["decisions"] % 100 == 0:
            telemetry("checkpoint", **counters, wall_s=round(time.monotonic() - started, 3),
                      cpu_s=_cpu_seconds(), rss_kib=_rss_kib(),
                      plans=getattr(planner, "plans", 0), plan_s=round(getattr(planner, "plan_seconds", 0.0), 3))
            reporter.emit("checkpoint", {
                **counters,
                "wall_s": round(time.monotonic() - started, 3),
                "cpu_s": _cpu_seconds(),
                "rss_kib": _rss_kib(),
                "plans": getattr(planner, "plans", 0),
                "plan_s": round(getattr(planner, "plan_seconds", 0.0), 3),
            }, sequence=counters["decisions"])
    finish()
    reporter.close(timeout=0.2)
    telemetry("telemetry_final", **reporter.status())


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
