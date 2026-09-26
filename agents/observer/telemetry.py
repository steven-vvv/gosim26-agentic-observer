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
import sys
import sysconfig
import threading
import time
import urllib.error
import urllib.request
import uuid
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
        return value if value == value and abs(value) != float("inf") else None
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
    except Exception:
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


def runtime_payload() -> dict[str, Any]:
    """Collect non-secret interpreter, host and process metrics."""
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
    except Exception:
        jit_enabled = None
    build, build_date = platform.python_build()
    payload: dict[str, Any] = {
        "python": platform.python_version(),
        "implementation": platform.python_implementation(),
        "python_build": build,
        "python_build_date": build_date,
        "system": platform.system(),
        "release": platform.release(),
        "version": platform.version(),
        "machine": platform.machine(),
        "processor": platform.processor(),
        "cpu_count": os.cpu_count(),
        "load_average": list(os.getloadavg()) if hasattr(os, "getloadavg") else None,
        "numpy": _package_version("numpy"),
        "orjson": _package_version("orjson"),
        "gil_disabled": gil_disabled,
        "jit_enabled": jit_enabled,
        "rss_kib": _rss_kib(),
        "disk_root": _disk_snapshot(),
    }
    return _sanitize(payload)


class TelemetryReporter:
    """A bounded, retrying, daemon-thread uploader."""

    def __init__(
        self,
        url: str | None = None,
        token: str | None = None,
        *,
        run_id: str | None = None,
        source: str = "observer",
        queue_size: int = 256,
        timeout: float = 0.8,
        retries: int = 2,
        backoff: float = 0.1,
    ) -> None:
        self.url = self._normalize_url(url) if url and token else None
        self.token = token if self.url else None
        self.run_id = run_id or str(uuid.uuid4())
        self.source = source[:48] or "observer"
        self.timeout = _safe_float(timeout, 0.8, 0.1, 3.0)
        self.retries = max(0, min(int(retries), 3))
        self.backoff = max(0.0, min(float(backoff), 1.0))
        self._queue: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=max(1, min(int(queue_size), 4096)))
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._stats = {"queued": 0, "sent": 0, "failed": 0, "dropped": 0}
        if self.url:
            self._thread = threading.Thread(target=self._run, name="observer-telemetry", daemon=True)
            self._thread.start()

    @classmethod
    def from_env(cls) -> "TelemetryReporter":
        return cls(
            os.environ.get("TELEMETRY_URL"),
            os.environ.get("TELEMETRY_TOKEN"),
            run_id=os.environ.get("TELEMETRY_RUN_ID"),
            source=os.environ.get("TELEMETRY_SOURCE", "observer"),
            queue_size=_safe_int(os.environ.get("TELEMETRY_QUEUE_SIZE"), 256, 1, 4096),
            timeout=_safe_float(os.environ.get("TELEMETRY_TIMEOUT_SECONDS"), 0.8, 0.1, 3.0),
            retries=_safe_int(os.environ.get("TELEMETRY_RETRIES"), 2, 0, 3),
        )

    @staticmethod
    def _normalize_url(value: str) -> str:
        url = value.strip().rstrip("/")
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
        event = {
            "id": str(uuid.uuid4()),
            "run_id": self.run_id,
            "event_type": str(event_type)[:96],
            "source": self.source,
            "client_time_ms": int(time.time() * 1000),
            "sequence": sequence,
            "payload": _sanitize(payload or {}),
        }
        try:
            self._queue.put_nowait(event)
        except queue.Full:
            self._inc("dropped")
            return False
        self._inc("queued")
        return True

    def _inc(self, key: str) -> None:
        with self._lock:
            self._stats[key] += 1

    def stats(self) -> dict[str, int]:
        with self._lock:
            return dict(self._stats)

    def _run(self) -> None:
        while not self._stop.is_set() or not self._queue.empty():
            try:
                event = self._queue.get(timeout=0.05)
            except queue.Empty:
                continue
            delivered = False
            for attempt in range(self.retries + 1):
                try:
                    self._send(event)
                    self._inc("sent")
                    delivered = True
                    break
                except Exception:
                    self._inc("failed")
                    if attempt < self.retries:
                        self._stop.wait(self.backoff * (2**attempt))
            if not delivered:
                self._inc("dropped")

    def _send(self, event: dict[str, Any]) -> None:
        body = json.dumps({"events": [event]}, separators=(",", ":")).encode("utf-8")
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
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            if response.status < 200 or response.status >= 300:
                raise urllib.error.HTTPError(self.url or "", response.status, "telemetry rejected", response.headers, None)

    def close(self, timeout: float = 0.05) -> None:
        if not self.enabled:
            return
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=max(0.0, timeout))


__all__ = ["TelemetryReporter", "runtime_payload"]
