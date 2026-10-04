"""Optional, bounded startup configuration delivery from the telemetry Worker."""

from __future__ import annotations

import hashlib
import json
import os
import time
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from .config import resolve

SCHEMA = "gosim26.v4.config-request.v1"
RESPONSE_SCHEMA = "gosim26.v4.config-response.v1"
MAX_RESPONSE_BYTES = 256 * 1024


def _digest(config: dict) -> str:
    encoded = json.dumps(config, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _local_status(remote: dict, reason: str, *, attempted: bool = False, profile: str = "full") -> dict:
    return {
        "source": "local",
        "remote_attempted": attempted,
        "config_id": remote.get("config_id", "observer4"),
        "profile": profile,
        "required": bool(remote.get("required", False)),
        "reason": reason,
    }


def _read_bounded(response) -> bytes:
    length = response.headers.get("content-length")
    if length and length.isdigit() and int(length) > MAX_RESPONSE_BYTES:
        raise ValueError("remote config response is too large")
    data = response.read(MAX_RESPONSE_BYTES + 1)
    if len(data) > MAX_RESPONSE_BYTES:
        raise ValueError("remote config response is too large")
    return data


def _shared_worker_token(url: str) -> str | None:
    """Reuse the model key only when the configured OpenAI endpoint is this Worker."""
    token = os.environ.get("OPENAI_API_KEY")
    model_url = os.environ.get("OPENAI_BASE_URL")
    if not token or not model_url:
        return None
    try:
        config_endpoint = urlsplit(url)
        model_endpoint = urlsplit(model_url)
        if (
            config_endpoint.scheme not in {"http", "https"}
            or model_endpoint.scheme not in {"http", "https"}
            or config_endpoint.hostname != model_endpoint.hostname
            or config_endpoint.port != model_endpoint.port
        ):
            return None
    except ValueError:
        return None
    return token


def resolve_startup(local: dict) -> tuple[dict, dict]:
    """Return a validated startup config and an audit-safe source description.

    Network failures are deliberately treated as a local-config fallback.  A bad or
    stale remote response cannot prevent the protocol process from starting.
    """
    local_resolved = resolve(local)
    release = local.get("release", {})
    remote = release.get("remote_config", {}) if isinstance(release, dict) else {}
    if not isinstance(remote, dict) or not remote.get("enabled", False):
        return local_resolved, _local_status(
            remote if isinstance(remote, dict) else {},
            "disabled",
            profile=local_resolved.get("release", {}).get("profile", "full"),
        )
    url = remote.get("url")
    token = os.environ.get("OBSERVER_CONFIG_TOKEN") or os.environ.get("TELEMETRY_TOKEN")
    if not token and isinstance(url, str):
        token = _shared_worker_token(url)
    local_profile = local_resolved.get("release", {}).get("profile", "full")
    if not isinstance(url, str) or not url.strip():
        return local_resolved, _local_status(remote, "missing_url", profile=local_profile)
    if not token:
        return local_resolved, _local_status(remote, "missing_token", profile=local_profile)
    try:
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("unsafe remote config endpoint")
        config_id = str(remote.get("config_id", "observer4"))
        request_payload = {
            "schema": SCHEMA,
            "config_id": config_id,
            "profile": local.get("release", {}).get("profile", "full"),
            "config_sha256": _digest(local_resolved),
            "agent_version": "observer4-v4",
        }
        request = Request(
            url,
            data=json.dumps(request_payload, separators=(",", ":"), allow_nan=False).encode("utf-8"),
            headers={
                "accept": "application/json",
                "authorization": f"Bearer {token}",
                "content-type": "application/json",
                "user-agent": "gosim26-observer4-config/1",
            },
            method="POST",
        )
        timeout = float(remote.get("timeout_seconds", 1.2))
        with urlopen(request, timeout=timeout) as response:
            raw = _read_bounded(response)
        body = json.loads(raw.decode("utf-8"))
        if not isinstance(body, dict) or body.get("schema") != RESPONSE_SCHEMA:
            raise ValueError("unsupported remote config response schema")
        if body.get("config_id") != config_id:
            raise ValueError("remote config id mismatch")
        revision = body.get("revision")
        if type(revision) is not int or revision < 0:
            raise ValueError("invalid remote config revision")
        expires_at = body.get("expires_at_ms")
        if expires_at is not None and (type(expires_at) is not int or expires_at < 0 or expires_at <= int(time.time() * 1000)):
            raise ValueError("remote config has expired")
        patch = body.get("patch", {})
        if not isinstance(patch, dict):
            raise TypeError("remote config patch must be an object")
        resolved = resolve(local, patch)
        status = {
            "source": "remote",
            "remote_attempted": True,
            "config_id": config_id,
            "revision": revision,
            "profile": resolved.get("release", {}).get("profile", "full"),
            "reason": "accepted",
        }
        return resolved, status
    except Exception as error:  # noqa: BLE001 - remote config is optional and must not stop a survey
        return local_resolved, _local_status(remote, type(error).__name__, attempted=True, profile=local_profile)
