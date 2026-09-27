"""Best-effort remote telemetry for the packaged observer.

The module deliberately uses only the Python standard library.  ``emit`` never
performs I/O: it only attempts a non-blocking enqueue into a bounded queue.  A
daemon worker sends batches in the background and drops events after bounded
retries, so an unavailable telemetry service cannot hold up a decision.
"""
from __future__ import annotations

import importlib.metadata
import json
import os
import platform
import queue
import shutil
import struct
import sys
import sysconfig
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections import Counter
from datetime import datetime
from typing import Any

try:
    import resource
except ImportError:  # pragma: no cover - resource is Unix-only
    resource = None


_BLOCKED_KEYS = {
    "authorization", "api_key", "apikey", "access_token", "refresh_token",
    "secret", "password", "cookie", "prompt", "messages",
}


def _safe_int(value: str | None, default: int, low: int, high: int) -> int:
    try:
        parsed = int(value or "")
    except (TypeError, ValueError):
        return default
    return max(low, min(high, parsed))


def _safe_float(value: str | float | None, default: float, low: float, high: float) -> float:
    try:
        parsed = float(value or "")
    except (TypeError, ValueError):
        return default
    return max(low, min(high, parsed))


def _blocked_key(key: str) -> bool:
    normalized = key.lower().replace("-", "_")
    return normalized in _BLOCKED_KEYS or any(
        marker in normalized for marker in ("private", "bearer", "credential", "passphrase")
    )


def _sanitize(value: Any, depth: int = 0) -> Any:
    """Return bounded JSON data while removing common secret-bearing fields."""
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        return value if value == value and abs(value) != float("inf") else None  # noqa: PLR0124
    if isinstance(value, str):
        return value[:4096]
    if depth >= 6:
        return "[depth-limit]"
    if isinstance(value, (list, tuple)):
        return [_sanitize(item, depth + 1) for item in value[:128]]
    if isinstance(value, dict):
        output: dict[str, Any] = {}
        for key, child in list(value.items())[:96]:
            text = str(key)
            if not _blocked_key(text):
                output[text[:128]] = _sanitize(child, depth + 1)
        return output
    return str(value)[:4096]


def _package_version(name: str) -> str | None:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None
    except Exception:  # noqa: BLE001
        return "unknown"


def _rss_kib() -> int | None:
    if resource is None:
        return None
    try:
        value = float(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
        if sys.platform == "darwin":
            value /= 1024.0
        return round(value)
    except (AttributeError, OSError, ValueError):
        return None


def _disk_snapshot(path: str = "/") -> dict[str, int] | None:
    try:
        usage = shutil.disk_usage(path)
        return {"total_bytes": usage.total, "used_bytes": usage.used, "free_bytes": usage.free}
    except OSError:
        return None


def _cpu_snapshot() -> dict[str, Any]:
    """Read a bounded CPU model/feature snapshot when procfs is available."""
    model: str | None = None
    flags: list[str] = []
    try:
        with open("/proc/cpuinfo", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                key, separator, value = line.partition(":")
                if not separator:
                    continue
                normalized = key.strip().lower()
                value = value.strip()
                if normalized in {"model name", "hardware", "cpu model"} and value and model is None:
                    model = value[:256]
                elif normalized in {"flags", "features"} and value and not flags:
                    flags = list(dict.fromkeys(value.split()))[:256]
    except (OSError, UnicodeError):
        pass
    if model is None:
        model = platform.processor() or None
    return {"model": model, "flags": flags or None}


def _memory_snapshot() -> dict[str, int] | None:
    """Return host memory counters when Linux exposes procfs."""
    wanted = {
        "MemTotal": "mem_total_bytes",
        "MemAvailable": "mem_available_bytes",
        "SwapTotal": "swap_total_bytes",
        "SwapFree": "swap_free_bytes",
    }
    values: dict[str, int] = {}
    try:
        with open("/proc/meminfo", encoding="ascii", errors="replace") as handle:
            for line in handle:
                key, separator, rest = line.partition(":")
                if not separator or key not in wanted:
                    continue
                parts = rest.strip().split()
                if not parts:
                    continue
                try:
                    value = int(parts[0])
                except ValueError:
                    continue
                # /proc/meminfo reports these counters in KiB.  Normalize to
                # bytes so all memory fields share one unambiguous unit.
                values[wanted[key]] = value * (1024 if len(parts) > 1 and parts[1].lower() == "kb" else 1)
    except (OSError, UnicodeError):
        return None
    return values or None


def _resource_limits() -> dict[str, dict[str, int | None]] | None:
    """Capture limits that can explain a slow or truncated evaluation."""
    if resource is None:
        return None
    names = {
        "cpu_seconds": getattr(resource, "RLIMIT_CPU", None),
        "address_space_bytes": getattr(resource, "RLIMIT_AS", None),
        "open_files": getattr(resource, "RLIMIT_NOFILE", None),
    }
    limits: dict[str, dict[str, int | None]] = {}
    infinity = getattr(resource, "RLIM_INFINITY", -1)
    for label, kind in names.items():
        if kind is None:
            continue
        try:
            soft, hard = resource.getrlimit(kind)
        except (OSError, ValueError):
            continue
        limits[label] = {
            "soft": None if soft in {infinity, -1} else int(soft),
            "hard": None if hard in {infinity, -1} else int(hard),
        }
    return limits or None


def _cpu_affinity_count() -> int | None:
    """Report the CPUs available to the process, including container limits."""
    getter = getattr(os, "sched_getaffinity", None)
    if getter is None:
        return None
    try:
        return len(getter(0))
    except (OSError, TypeError):
        return None


def _load_average() -> list[float] | None:
    try:
        return list(os.getloadavg())
    except (AttributeError, OSError):
        return None


def runtime_payload() -> dict[str, Any]:
    """Collect bounded, non-secret interpreter, ABI, host and process metrics."""
    gil_flag = sysconfig.get_config_var("Py_GIL_DISABLED")
    if gil_flag is None:
        gil_flag = getattr(sys.flags, "gil", None)
    if isinstance(gil_flag, str):
        gil_disabled: bool | None = gil_flag.lower() not in {"", "0", "false", "no", "off"}
    elif gil_flag is None:
        gil_disabled = None
    else:
        gil_disabled = bool(gil_flag)
    jit = getattr(sys, "_jit", None)
    try:
        jit_enabled = bool(jit.is_enabled()) if jit is not None and hasattr(jit, "is_enabled") else None
    except Exception:  # noqa: BLE001
        jit_enabled = None
    build, build_date = platform.python_build()
    cpu = _cpu_snapshot()
    libc_name, libc_version = platform.libc_ver()
    try:
        pointer_bits = struct.calcsize("P") * 8
    except (struct.error, TypeError):
        pointer_bits = None
    payload: dict[str, Any] = {
        "python": platform.python_version(),
        "implementation": platform.python_implementation(),
        "python_build": build,
        "python_build_date": build_date,
        "python_compiler": platform.python_compiler(),
        "python_soabi": sysconfig.get_config_var("SOABI"),
        "python_multiarch": sysconfig.get_config_var("MULTIARCH"),
        "system": platform.system(),
        "release": platform.release(),
        "version": platform.version(),
        "architecture": platform.machine() or None,
        "machine": platform.machine(),
        "processor": platform.processor(),
        "cpu_model": cpu["model"],
        "cpu_flags": cpu["flags"],
        "cpu_count": os.cpu_count(),
        "cpu_affinity_count": _cpu_affinity_count(),
        "load_average": _load_average(),
        "pointer_bits": pointer_bits,
        "byteorder": sys.byteorder,
        "libc": {"name": libc_name or None, "version": libc_version or None},
        "numpy": _package_version("numpy"),
        "orjson": _package_version("orjson"),
        "gil_disabled": gil_disabled,
        "jit_enabled": jit_enabled,
        "rss_kib": _rss_kib(),
        "memory": _memory_snapshot(),
        "resource_limits": _resource_limits(),
        "disk_root": _disk_snapshot(),
    }
    try:
        payload["disk_cwd"] = _disk_snapshot(os.getcwd())
    except OSError:
        payload["disk_cwd"] = None
    return _sanitize(payload)


class RunSummary:
    """Keep a compact, complete-run performance report independent of the log file."""

    FIELDS = ("input_wait_ms", "policy_ms", "response_ms", "stdout_ms",
              "request_bytes", "response_bytes", "candidate_count", "active_request_count")

    def __init__(self) -> None:
        self.actions: Counter[str] = Counter()
        self.timings: dict[str, list[float]] = {field: [] for field in self.FIELDS}
        self.first_sequence: int | None = None
        self.last_sequence: int | None = None
        self.planning_disabled_at: int | None = None
        self.last_planning: bool | None = None
        self.plans = 0
        self.plan_s = 0.0
        self.last_wall_s = 0.0
        self.last_cpu_s: float | None = None

    def add(self, trace: dict[str, Any]) -> None:
        if trace.get("kind") != "decision":
            return
        sequence = trace.get("sequence")
        if isinstance(sequence, int):
            if self.first_sequence is None:
                self.first_sequence = sequence
            self.last_sequence = sequence
        action = trace.get("action")
        if isinstance(action, str):
            self.actions[action] += 1
        for field in self.FIELDS:
            value = trace.get(field)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                self.timings[field].append(float(value))
        planning = trace.get("planning")
        if isinstance(planning, bool):
            if self.last_planning is True and not planning and self.planning_disabled_at is None:
                self.planning_disabled_at = sequence if isinstance(sequence, int) else None
            self.last_planning = planning
        self.plans = max(self.plans, int(trace.get("plans") or 0))
        self.plan_s = max(self.plan_s, float(trace.get("plan_s") or 0.0))
        self.last_wall_s = float(trace.get("wall_s") or self.last_wall_s)
        self.last_cpu_s = trace.get("cpu_s", self.last_cpu_s)

    @staticmethod
    def _stats(values: list[float]) -> dict[str, float | int]:
        if not values:
            return {"count": 0}
        ordered = sorted(values)

        def percentile(fraction: float) -> float:
            position = (len(ordered) - 1) * fraction
            lower = int(position)
            upper = min(lower + 1, len(ordered) - 1)
            return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)

        return {"count": len(values), "mean": round(sum(values) / len(values), 3),
                "p50": round(percentile(0.5), 3), "p95": round(percentile(0.95), 3),
                "max": round(ordered[-1], 3)}

    def report(self) -> dict[str, Any]:
        return {
            "actions": dict(self.actions),
            "first_sequence": self.first_sequence,
            "last_sequence": self.last_sequence,
            "planning_disabled_at": self.planning_disabled_at,
            "planning_at_end": self.last_planning,
            "plans": self.plans,
            "plan_s": round(self.plan_s, 3),
            "last_wall_s": round(self.last_wall_s, 3),
            "last_cpu_s": self.last_cpu_s,
            "metrics": {field: self._stats(values) for field, values in self.timings.items()},
        }


class TerminalStep:
    """Recognize a decision that consumes the final published observing slot."""

    def __init__(self) -> None:
        self.last_night_id: str | None = None
        self.last_night_end: datetime | None = None
        self.slot_seconds = 0

    def initialize(self, publication: dict[str, Any]) -> None:
        calendar = publication.get("calendar") or {}
        last_night = calendar.get("last_night")
        self.last_night_id = "N" + last_night.replace("-", "") if isinstance(last_night, str) else None
        self.slot_seconds = int(calendar.get("slot_duration_seconds") or 0)
        self.last_night_end = None

    def is_final(self, snapshot: dict[str, Any], decision: dict[str, Any]) -> bool:
        cursor = snapshot.get("cursor") or {}
        if cursor.get("night_id") != self.last_night_id or not self.slot_seconds:
            return False
        night = (snapshot.get("night_start") or {}).get("night") or {}
        try:
            if night.get("observing_end_utc"):
                self.last_night_end = datetime.fromisoformat(night["observing_end_utc"].replace("Z", "+00:00"))
            if self.last_night_end is None:
                return False
            now = datetime.fromisoformat(cursor["timestamp_utc"].replace("Z", "+00:00"))
            if decision.get("action") == "wait":
                duration = self.slot_seconds - int(cursor.get("slot_offset_seconds") or 0)
            elif decision.get("action") == "observe":
                duration = next(int(tile["nominal_exptime_seconds"])
                                for tile in snapshot.get("candidate_tiles") or []
                                if tile.get("tile_id") == decision.get("tile_id"))
            else:
                return False
            return duration > 0 and (self.last_night_end - now).total_seconds() <= duration
        except (KeyError, ValueError, TypeError, StopIteration, OverflowError):
            return False


class TelemetryReporter:
    """A bounded, retrying, daemon-thread uploader."""

    def __init__(
        self,
        url: str | None = None,
        token: str | None = None,
        *,
        run_id: str | None = None,
        source: str = "observer",
        transport: str = "events",
        model: str = "telemetry-ack",
        queue_size: int = 256,
        timeout: float = 0.8,
        retries: int = 2,
        backoff: float = 0.1,
        batch_size: int = 16,
    ) -> None:
        normalized_transport = str(transport or "events").strip().lower()
        self.transport = normalized_transport if normalized_transport in {"events", "openai"} else "events"
        self.model = str(model or "telemetry-ack").strip()[:128] or "telemetry-ack"
        self.url = self._normalize_url(url, self.transport) if url and token else None
        self.token = token if self.url else None
        self.run_id = run_id or str(uuid.uuid4())
        self.source = source[:48] or "observer"
        self.timeout = _safe_float(timeout, 0.8, 0.1, 10.0)
        self.retries = max(0, min(int(retries), 3))
        self.backoff = max(0.0, min(float(backoff), 1.0))
        self.batch_size = max(1, min(int(batch_size), 32))
        self._queue: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=max(1, min(int(queue_size), 4096)))
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._stats = {"queued": 0, "sent": 0, "failed": 0, "dropped": 0}
        if self.url:
            self._thread = threading.Thread(target=self._run, name="observer-telemetry", daemon=True)
            self._thread.start()

    @classmethod
    def from_env(cls, defaults_path: str | os.PathLike[str] | None = None) -> TelemetryReporter:
        # An explicit event endpoint remains available for deployments that
        # want separate ingestion. Otherwise the OpenAI-compatible variables
        # select the shared Worker, whose marker-aware router consumes events
        # before ordinary model traffic reaches the upstream provider.
        defaults: dict[str, Any] = {}
        if defaults_path:
            try:
                with open(defaults_path, encoding="utf-8") as handle:
                    loaded = json.load(handle)
                if isinstance(loaded, dict):
                    defaults = loaded
            except (OSError, TypeError, ValueError):
                # A malformed optional package config must never stop the
                # protocol process.  Environment variables remain usable.
                defaults = {}
        if str(defaults.get("enabled", True)).lower() in {"0", "false", "no", "off"}:
            return cls(None, None)
        if os.environ.get("TELEMETRY_DISABLE", "").lower() in {"1", "true", "yes", "on"}:
            return cls(None, None)
        url = os.environ.get("TELEMETRY_URL")
        token = os.environ.get("TELEMETRY_TOKEN")
        transport_setting = os.environ.get("TELEMETRY_TRANSPORT")
        if transport_setting:
            transport = transport_setting.strip().lower()
        elif url:
            # An explicit event URL keeps the historical events transport;
            # the packaged default uses OpenAI only when it supplies the URL.
            transport = "events"
        else:
            transport = str(defaults.get("transport", "events")).strip().lower()
        if not token and os.environ.get("TELEMETRY_DISABLE_OPENAI", "").lower() not in {"1", "true", "yes", "on"}:
            openai_url = os.environ.get("OPENAI_BASE_URL")
            openai_key = os.environ.get("OPENAI_API_KEY")
            if openai_url and openai_key:
                default_host = urllib.parse.urlsplit(str(defaults.get("url", ""))).hostname
                injected_host = urllib.parse.urlsplit(openai_url).hostname
                if not default_host or injected_host == default_host:
                    url, token, transport = openai_url, openai_key, "openai"
        if token and not url and defaults.get("url"):
            # A dedicated telemetry token may use the packaged default URL;
            # never substitute the model proxy key here.
            url = str(defaults["url"])
            transport = str(defaults.get("transport", "openai")).strip().lower()
        model = (
            os.environ.get("TELEMETRY_MODEL")
            or os.environ.get("OPENAI_MODEL")
            or os.environ.get("MODEL_NAME")
            or os.environ.get("MODEL")
            or str(defaults.get("model", ""))
            or "telemetry-ack"
        )
        return cls(
            url,
            token,
            run_id=os.environ.get("TELEMETRY_RUN_ID"),
            source=os.environ.get("TELEMETRY_SOURCE", "observer"),
            transport=transport,
            model=model,
            queue_size=_safe_int(os.environ.get("TELEMETRY_QUEUE_SIZE") or defaults.get("queue_size"), 1024, 1, 4096),
            timeout=_safe_float(os.environ.get("TELEMETRY_TIMEOUT_SECONDS") or defaults.get("timeout_seconds"), 3.0, 0.1, 10.0),
            retries=_safe_int(os.environ.get("TELEMETRY_RETRIES"), 2, 0, 3),
            batch_size=_safe_int(os.environ.get("TELEMETRY_BATCH_SIZE") or defaults.get("batch_size"), 16, 1, 32),
        )

    @staticmethod
    def _normalize_url(value: str, transport: str = "events") -> str:
        url = value.strip().rstrip("/")
        try:
            parsed = urllib.parse.urlsplit(url)
            if parsed.scheme and parsed.netloc:
                # Credentials belong in the Authorization header, never in a
                # query string that could leak through diagnostics or proxies.
                netloc = parsed.netloc.rsplit("@", 1)[-1]
                url = urllib.parse.urlunsplit((parsed.scheme, netloc, parsed.path.rstrip("/"), "", ""))
        except ValueError:
            pass
        if transport == "openai":
            if url.endswith("/v1/chat/completions"):
                return url
            if url.endswith("/v1"):
                return f"{url}/chat/completions"
            return f"{url}/v1/chat/completions"
        if url.endswith("/v1/telemetry"):
            return url
        if url.endswith("/v1"):
            return f"{url}/telemetry"
        return f"{url}/v1/telemetry"

    @property
    def enabled(self) -> bool:
        return self.url is not None and self.token is not None

    def emit(self, event_type: str, payload: dict[str, Any] | None = None, *, sequence: int | None = None) -> bool:
        if not self.enabled:
            return False
        event = self._event(event_type, payload, sequence)
        try:
            self._queue.put_nowait(event)
        except queue.Full:
            self._inc("dropped")
            return False
        self._inc("queued")
        return True

    def _event(self, event_type: str, payload: dict[str, Any] | None, sequence: int | None) -> dict[str, Any]:
        event = {
            "id": str(uuid.uuid4()),
            "run_id": self.run_id,
            "event_type": str(event_type)[:96],
            "source": self.source,
            "client_time_ms": int(time.time() * 1000),
            "sequence": sequence,
            "payload": _sanitize(payload or {}),
        }
        return event

    def send_final(self, payload: dict[str, Any], *, sequence: int | None = None, wait_seconds: float = 5.0) -> bool:
        """Send the final report ahead of queued traces, with a hard wait limit."""
        if not self.enabled:
            return False
        event = self._event("final", payload, sequence)
        deadline = time.monotonic() + max(0.0, min(float(wait_seconds), 10.0))
        completed = threading.Event()
        outcome = {"delivered": False}

        def upload() -> None:
            try:
                for attempt in range(2):
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        break
                    try:
                        self._send([event], timeout=min(self.timeout, remaining))
                        self._inc("sent")
                        outcome["delivered"] = True
                        return
                    except Exception:  # noqa: BLE001
                        self._inc("failed")
                        if attempt == 0 and deadline - time.monotonic() > 0.2:
                            time.sleep(0.1)
                self._inc("dropped")
            finally:
                completed.set()

        threading.Thread(target=upload, name="observer-final-telemetry", daemon=True).start()
        completed.wait(max(0.0, deadline - time.monotonic()))
        return outcome["delivered"]

    def _inc(self, key: str, count: int = 1) -> None:
        with self._lock:
            self._stats[key] += count

    def stats(self) -> dict[str, int]:
        with self._lock:
            return dict(self._stats)

    def status(self) -> dict[str, Any]:
        """Return a redacted configuration and delivery snapshot for logs."""
        endpoint = None
        if self.url:
            try:
                parsed = urllib.parse.urlsplit(self.url)
                endpoint = urllib.parse.urlunsplit((parsed.scheme, parsed.netloc.rsplit("@", 1)[-1], parsed.path, "", ""))
            except ValueError:
                endpoint = "<invalid>"
        return {
            "configured": self.url is not None and self.token is not None,
            "enabled": self.enabled,
            "transport": self.transport,
            "endpoint": endpoint,
            "token_present": self.token is not None,
            "run_id": self.run_id,
            "queue_capacity": self._queue.maxsize,
            "queue_depth": self._queue.qsize(),
            "batch_size": self.batch_size,
            "timeout_s": self.timeout,
            "retries": self.retries,
            "thread_alive": bool(self._thread and self._thread.is_alive()),
            "stats": self.stats(),
        }

    def _run(self) -> None:
        while not self._stop.is_set() or not self._queue.empty():
            try:
                first = self._queue.get(timeout=0.05)
            except queue.Empty:
                continue
            events = [first]
            while len(events) < self.batch_size:
                try:
                    events.append(self._queue.get_nowait())
                except queue.Empty:
                    break
            delivered = False
            for attempt in range(self.retries + 1):
                try:
                    self._send(events)
                    self._inc("sent", len(events))
                    delivered = True
                    break
                except Exception:  # noqa: BLE001
                    self._inc("failed")
                    if attempt < self.retries:
                        self._stop.wait(self.backoff * (2**attempt))
            if not delivered:
                self._inc("dropped", len(events))

    def _send(self, events: list[dict[str, Any]], *, timeout: float | None = None) -> None:
        if self.transport == "openai":
            # The shared Worker consumes this marked tool message before any
            # provider call.  It is deliberately distinguishable from normal
            # model traffic, so telemetry does not spend inference tokens.
            envelope = {"schema": "gosim26.telemetry.v1"}
            envelope["event" if len(events) == 1 else "events"] = events[0] if len(events) == 1 else events
            body_object = {
                "model": self.model,
                "messages": [{
                    "role": "tool",
                    "tool_call_id": "gosim26.telemetry",
                    "name": "gosim26.telemetry",
                    "content": json.dumps(envelope, separators=(",", ":")),
                }],
                "stream": False,
            }
        else:
            body_object = {"events": events}
        body = json.dumps(body_object, separators=(",", ":")).encode("utf-8")
        request = urllib.request.Request(
            self.url or "",
            data=body,
            method="POST",
            headers={
                "authorization": f"Bearer {self.token}",
                "content-type": "application/json",
                "user-agent": "gosim26-observer-telemetry/1",
            },
        )
        with urllib.request.urlopen(request, timeout=self.timeout if timeout is None else timeout) as response:
            if response.status < 200 or response.status >= 300:
                raise urllib.error.HTTPError(self.url or "", response.status, "telemetry rejected", response.headers, None)

    def close(self, timeout: float = 0.05) -> None:
        if not self.enabled:
            return
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=max(0.0, timeout))


__all__ = ["RunSummary", "TelemetryReporter", "TerminalStep", "runtime_payload"]
