"""Nonblocking events and bounded shutdown, governed by one public configuration."""

from __future__ import annotations

import json
import math
import os
import platform
import sys
import time
from collections import Counter
from urllib.parse import urlsplit

from .services import load_client


def _enabled(value, default: bool) -> bool:
    if value is None:
        return default
    return str(value).lower() not in {"false", "0", "off", "no", "disabled"}


def _clean(value, depth=0):
    if depth > 8:
        return None
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, str):
        return value[:2048]
    if isinstance(value, (tuple, list)):
        return [_clean(item, depth + 1) for item in value[:128]]
    if isinstance(value, dict):
        return {
            str(key)[:128]: _clean(item, depth + 1)
            for key, item in list(value.items())[:128]
            if not any(word in str(key).lower() for word in ("secret", "password", "authorization", "api_key", "token_value"))
        }
    return str(type(value).__name__)


class Diagnostics:
    def __init__(self, config: dict, *, silent=False):
        self.config = config["observability"]
        self.local = not silent and _enabled(os.environ.get("OBSERVABILITY_LOCAL_ENABLED"), self.config["local_enabled"])
        self.events = self.config["events"]
        self.started = time.perf_counter()
        self.actions = Counter()
        self.latencies = []
        self.errors = Counter()
        self.last_sequence = 0
        self.closed = False
        self.reporter = None
        self.service = load_client("telemetry") if not silent else None
        remote = self.config["remote"]
        enabled = remote["enabled"] and not silent and not _enabled(os.environ.get("TELEMETRY_DISABLE"), False)
        if self.service is not None and enabled:
            explicit_url = os.environ.get("TELEMETRY_URL")
            explicit_key = os.environ.get("TELEMETRY_TOKEN")
            # The platform's model proxy and the optional telemetry Worker may both
            # expose OpenAI-compatible URLs.  Only reuse model credentials when the
            # injected endpoint is the configured telemetry host; otherwise a model
            # key must never be sent to diagnostics by accident.
            url = explicit_url
            key = explicit_key
            if not explicit_url and not explicit_key:
                model_url = os.environ.get("OPENAI_BASE_URL")
                model_key = os.environ.get("OPENAI_API_KEY")
                try:
                    telemetry_host = urlsplit(str(remote["url"])).hostname
                    model_host = urlsplit(model_url).hostname if model_url else None
                except ValueError:
                    telemetry_host = model_host = None
                if model_url and model_key and telemetry_host and model_host == telemetry_host:
                    url, key = model_url, model_key
            if explicit_key and not explicit_url:
                url = remote["url"]
            transport = os.environ.get("TELEMETRY_TRANSPORT") or (
                "events" if explicit_url and not explicit_url.rstrip("/").endswith("/v1") else remote["transport"]
            )
            try:
                parsed = urlsplit(url)
                if (
                    parsed.scheme not in {"http", "https"}
                    or parsed.username
                    or parsed.password
                    or parsed.query
                    or parsed.fragment
                ):
                    raise ValueError("unsafe telemetry endpoint")
                self.reporter = self.service.TelemetryReporter(
                    url,
                    key,
                    run_id=os.environ.get("TELEMETRY_RUN_ID"),
                    source="observer-v4",
                    transport=transport,
                    model=remote["model"],
                    queue_size=remote["queue_size"],
                    timeout=remote["timeout_seconds"],
                    retries=remote["retries"],
                    batch_size=remote["batch_size"],
                    drain_timeout=remote["drain_timeout_seconds"],
                    final_timeout=min(5, remote["drain_timeout_seconds"]),
                )
            except Exception:  # noqa: BLE001 - remote diagnostics cannot affect decisions
                self.errors["telemetry_config"] += 1

    def emit(self, event: str, payload: dict | None = None, *, sequence=None, remote=True):
        if not self.events.get(event, True):
            return
        cleaned = _clean(payload or {})
        if self.local:
            try:
                print(
                    "observer4: "
                    + json.dumps(
                        {"event": event, "sequence": sequence, **cleaned}, separators=(",", ":"), sort_keys=True, allow_nan=False
                    ),
                    file=sys.stderr,
                    flush=True,
                )
            except (OSError, ValueError):
                self.errors["local_log"] += 1
        if remote and self.reporter is not None:
            try:
                self.reporter.emit(event, cleaned, sequence=sequence)
            except Exception:  # noqa: BLE001 - remote diagnostics cannot affect decisions
                self.errors["telemetry_emit"] += 1

    def startup(self, config_hash: str):
        runtime = {
            "python": platform.python_version(),
            "implementation": platform.python_implementation(),
            "machine": platform.machine(),
            "config_sha256": config_hash,
        }
        if self.service is not None:
            try:
                runtime.update(self.service.runtime_payload())
            except Exception:  # noqa: BLE001 - runtime metadata is optional
                self.errors["runtime_probe"] += 1
        self.emit("runtime", runtime)

    def decision(self, sequence: int, action: dict, elapsed: float, trace: dict):
        self.actions[action["action"]] += 1
        self.latencies.append(elapsed)
        self.last_sequence = sequence
        if sequence % self.config["decision_every"] == 0:
            self.emit(
                "decision",
                {"action": action["action"], "elapsed_seconds": round(elapsed, 6), **trace},
                sequence=sequence,
                remote=sequence % self.config["remote"]["decision_every"] == 0,
            )
        if sequence % self.config["checkpoint_every"] == 0:
            self.emit("checkpoint", self.summary(), sequence=sequence)

    def error(self, event: str, error: Exception, sequence=None):
        self.errors[event] += 1
        self.emit(event, {"error_type": type(error).__name__}, sequence=sequence)

    def summary(self):
        ordered = sorted(self.latencies)
        latency = {}
        if ordered:
            for name, fraction in (("p50", 0.5), ("p95", 0.95), ("p99", 0.99)):
                latency[name] = round(ordered[min(len(ordered) - 1, int(fraction * len(ordered)))], 6)
            latency.update(total=round(sum(ordered), 6), maximum=round(ordered[-1], 6))
        return {
            "wallclock_seconds": round(time.perf_counter() - self.started, 6),
            "actions": dict(self.actions),
            "decision_latency_seconds": latency,
            "errors": dict(self.errors),
            "last_decision_sequence": self.last_sequence,
        }

    def finish(self, reason: str, payload: dict | None = None, policy_summary: dict | None = None):
        if self.closed:
            return
        self.closed = True
        report = {**self.summary(), "termination_reason": reason, "finish_payload": payload or {}, **(policy_summary or {})}
        self.emit("final", report, remote=False)
        if self.reporter is not None:
            timeout = min(20.0, float(self.config["remote"]["drain_timeout_seconds"]))
            try:
                self.reporter.close(timeout=max(0, timeout - 3))
                delivered = self.reporter.send_final(report, sequence=self.last_sequence, wait_seconds=min(3, timeout))
                self.emit("final_delivery", {"delivered": delivered, "stats": self.reporter.stats()}, remote=False)
            except Exception as error:  # noqa: BLE001 - shutdown remains bounded if telemetry fails
                self.error("telemetry_finish_error", error)
