"""Optional, bounded OpenAI-compatible model advice for the observer.

The observer remains the source of truth. This client only supplies an
advisory choice and an optional probability distribution; every transport,
schema, or validation failure is treated as a deterministic fallback.
"""

from __future__ import annotations

import json
import logging
import math
import os
import urllib.error
import urllib.request
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

LOG = logging.getLogger(__name__)


class AdviceError(ValueError):
    """A fixed validation reason safe to expose in local diagnostics."""


@dataclass(frozen=True)
class AdvisorConfig:
    """Runtime limits for the optional advisory call."""

    enabled: bool = True
    model: str = "team-model"
    timeout: float = 18.0
    max_stage_calls: int = 4
    max_total_calls: int = 8
    max_consecutive_failures: int = 3
    max_context_bytes: int = 120_000
    max_response_bytes: int = 65_536
    max_tokens: int = 4096
    temperature: float = 0.0
    top_p: float = 1.0
    selection_mode: str = "guided"
    min_alternative_probability: float = 0.85


def _as_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() not in {"0", "false", "no", "off", "disable", "disabled"}


def _as_int(value: Any, default: int, minimum: int = 1) -> int:
    try:
        return max(minimum, int(value))
    except (TypeError, ValueError):
        return default


def _as_float(value: Any, default: float, minimum: float = 0.0, maximum: float | None = None) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(parsed):
        return default
    parsed = max(minimum, parsed)
    if maximum is not None:
        parsed = min(maximum, parsed)
    return parsed


def _load_defaults(path: str | os.PathLike[str] | None) -> dict[str, Any]:
    if not path:
        return {}
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    return value if isinstance(value, dict) else {}


class ModelAdvisor:
    """Small fail-closed client for an OpenAI-compatible chat endpoint."""

    # Keep these messages byte-for-byte stable so providers can reuse the
    # prefix in their prompt cache. Current state and candidates are appended
    # in the user message and therefore vary only at the end of the request.
    _SYSTEM_PROMPT = (
        "You are an advisory component inside the GOSIM26 observer. "
        "Return JSON only. The deterministic controller has already selected "
        "the preferred operation. Evaluate the supplied live state, assign a "
        "probability to every candidate id, and normally preserve that choice. "
        "Never invent state or candidates. "
        "Output one object with keys choice (string) and probabilities (object "
        "mapping every candidate id to a number between 0 and 1). "
        "The probabilities should sum to 1. If the request is incomplete, "
        "repeat deterministic_choice and use a conservative distribution."
    )

    def __init__(
        self,
        url: str | None,
        key: str | None,
        model: str = "team-model",
        timeout: float = 18.0,
        *,
        config: AdvisorConfig | None = None,
        transport: Any | None = None,
    ) -> None:
        supplied = config or AdvisorConfig(model=model, timeout=timeout)
        self.config = supplied
        self.url = self._endpoint(url) if url else None
        self.key = key
        self.model = supplied.model or model
        # A very small timeout makes local proxies flaky and was the source of
        # avoidable retries in the original implementation.
        self.timeout = _as_float(supplied.timeout, 18.0, minimum=0.2, maximum=30.0)
        self.context_bytes = 0
        self.context_truncated = False
        self.calls = 0
        self.successes = 0
        self.failures = 0
        self.consecutive_failures = 0
        self.stage_calls: dict[str, int] = {}
        self.last_outcome: dict[str, Any] = {
            "available": bool(self.url and self.key and supplied.enabled),
            "choice": None,
            "deterministic_choice": None,
            "probabilities": {},
            "fallback": True,
        }
        # Keep the default lookup late so tests and embedding applications can
        # replace ``urllib.request.urlopen`` after constructing the advisor.
        self._transport = transport

    @staticmethod
    def _endpoint(value: str) -> str:
        value = value.rstrip("/")
        if value.endswith("/chat/completions"):
            return value
        if value.endswith("/v1"):
            return f"{value}/chat/completions"
        return f"{value}/v1/chat/completions"

    @classmethod
    def from_env(cls, defaults_path: str | os.PathLike[str] | None = None) -> ModelAdvisor:
        defaults = _load_defaults(defaults_path)
        env = os.environ

        def pick(name: str, default: Any = None) -> Any:
            short = name.removeprefix("OBSERVER_MODEL_").lower()
            return env.get(name, defaults.get(short, defaults.get(name.lower(), defaults.get(name, default))))

        enabled = _as_bool(pick("OBSERVER_MODEL_ENABLED", True), True)
        if _as_bool(env.get("OBSERVER_MODEL_DISABLE"), False):
            enabled = False
        url = pick("OBSERVER_MODEL_BASE_URL") or pick("OPENAI_BASE_URL")
        key = pick("OBSERVER_MODEL_API_KEY") or pick("OPENAI_API_KEY")
        selection_mode = str(pick("OBSERVER_MODEL_SELECTION_MODE", "guided") or "guided").strip().lower()
        if selection_mode not in {"guided", "deterministic"}:
            selection_mode = "guided"
        config = AdvisorConfig(
            enabled=enabled,
            model=str(pick("OBSERVER_MODEL_NAME") or pick("MODEL_NAME") or pick("OPENAI_MODEL") or "team-model"),
            timeout=_as_float(pick("OBSERVER_MODEL_TIMEOUT", 18.0), 18.0, minimum=0.2, maximum=30.0),
            max_stage_calls=_as_int(pick("OBSERVER_MODEL_MAX_STAGE_CALLS", 4), 4),
            max_total_calls=_as_int(pick("OBSERVER_MODEL_MAX_TOTAL_CALLS", 8), 8),
            max_consecutive_failures=_as_int(pick("OBSERVER_MODEL_MAX_FAILURES", 3), 3),
            max_context_bytes=_as_int(pick("OBSERVER_MODEL_MAX_CONTEXT_BYTES", 120_000), 120_000, 4_096),
            max_response_bytes=_as_int(pick("OBSERVER_MODEL_MAX_RESPONSE_BYTES", 65_536), 65_536, 1_024),
            max_tokens=min(4_096, _as_int(pick("OBSERVER_MODEL_MAX_TOKENS", 4_096), 4_096, 128)),
            temperature=_as_float(pick("OBSERVER_MODEL_TEMPERATURE", 0.0), 0.0, 0.0, 2.0),
            top_p=_as_float(pick("OBSERVER_MODEL_TOP_P", 1.0), 1.0, 0.0, 1.0),
            selection_mode=selection_mode,
            min_alternative_probability=_as_float(
                pick("OBSERVER_MODEL_MIN_ALTERNATIVE_PROBABILITY", 0.85), 0.85, 0.0, 1.0
            ),
        )
        return cls(url, key, config.model, config.timeout, config=config)

    @property
    def enabled(self) -> bool:
        return bool(
            self.config.enabled
            and self.url
            and self.key
            and self.calls < self.config.max_total_calls
            and self.consecutive_failures < self.config.max_consecutive_failures
        )

    def status(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "configured": bool(self.config.enabled and self.url and self.key),
            "model": self.model,
            "calls": self.calls,
            "successes": self.successes,
            "failures": self.failures,
            "consecutive_failures": self.consecutive_failures,
            "selection_mode": self.config.selection_mode,
            "min_alternative_probability": self.config.min_alternative_probability,
            "last_outcome": dict(self.last_outcome),
        }

    @staticmethod
    def _json_content(content: Any) -> dict[str, Any] | None:
        if isinstance(content, dict):
            return content
        if not isinstance(content, str):
            return None
        text = content.strip()
        if text.startswith("```"):
            text = text.strip("`").removeprefix("json").strip()
        try:
            value = json.loads(text)
        except ValueError:
            start, end = text.find("{"), text.rfind("}")
            if start < 0 or end <= start:
                return None
            try:
                value = json.loads(text[start : end + 1])
            except ValueError:
                return None
        return value if isinstance(value, dict) else None

    @staticmethod
    def _normalise_probabilities(value: Any, ids: Sequence[str]) -> dict[str, float] | None:
        if not isinstance(value, Mapping) or set(value) != set(ids):
            return None
        parsed: dict[str, float] = {}
        for choice_id in ids:
            try:
                probability = float(value[choice_id])
            except (TypeError, ValueError):
                return None
            if not math.isfinite(probability) or probability < 0:
                return None
            parsed[choice_id] = probability
        total = sum(parsed.values())
        if total <= 0 or not math.isfinite(total):
            return None
        return {choice_id: round(probability / total, 8) for choice_id, probability in parsed.items()}

    def _request_body(self, stage: str, options: Sequence[Mapping[str, Any]], context: Mapping[str, Any]) -> dict[str, Any]:
        ids = [str(option.get("id", "")) for option in options]
        deterministic = context.get("deterministic_choice") or (ids[0] if ids else None)
        dynamic = {
            "schema": "gosim26.observer.advice.v2",
            "stage": stage,
            "deterministic_choice": deterministic,
            "candidates": list(options),
            "live_state": dict(context),
        }
        encoded = json.dumps(dynamic, ensure_ascii=True, separators=(",", ":"))
        self.context_bytes = len(encoded.encode("utf-8"))
        self.context_truncated = self.context_bytes > self.config.max_context_bytes
        if self.context_truncated:
            # Preserve the decision-critical fields when a provider or proxy
            # imposes a smaller body limit. The omission is explicit.
            dynamic["live_state"] = {
                "schema": context.get("schema"),
                "night_id": context.get("night_id"),
                "decision_sequence": context.get("decision_sequence"),
                "deterministic_choice": deterministic,
                "context_truncated": True,
            }
        return {
            "model": self.model,
            "temperature": self.config.temperature,
            "top_p": self.config.top_p,
            "max_tokens": self.config.max_tokens,
            "stream": False,
            "messages": [
                {"role": "system", "content": self._SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(dynamic, ensure_ascii=True, separators=(",", ":"))},
            ],
        }

    def _fallback(self, stage: str, deterministic: str | None, reason: str) -> None:
        self.last_outcome = {
            "stage": stage,
            "available": self.enabled,
            "choice": None,
            "deterministic_choice": deterministic,
            "probabilities": {},
            "fallback": True,
            "reason": reason,
            "context_bytes": self.context_bytes,
            "context_truncated": self.context_truncated,
        }

    def choose(self, stage: str, options: Sequence[Mapping[str, Any]], context: Mapping[str, Any]) -> str | None:
        """Return a validated model choice, or ``None`` for deterministic fallback."""

        self.context_bytes = 0
        self.context_truncated = False
        ids = [str(option.get("id", "")) for option in options]
        deterministic = str(context.get("deterministic_choice") or (ids[0] if ids else "")) or None
        if len(ids) < 2:
            self._fallback(stage, deterministic, "not_enough_candidates")
            return None
        if not self.enabled:
            self._fallback(stage, deterministic, "disabled_or_circuit_open")
            return None
        used = self.stage_calls.get(stage, 0)
        if used >= self.config.max_stage_calls:
            self._fallback(stage, deterministic, "stage_budget_exhausted")
            return None

        self.stage_calls[stage] = used + 1
        self.calls += 1
        try:
            body = self._request_body(stage, options, context)
            request = urllib.request.Request(
                self.url or "",
                data=json.dumps(body, ensure_ascii=True, separators=(",", ":")).encode("utf-8"),
                headers={
                    "Authorization": f"Bearer {self.key}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                    "User-Agent": "gosim26-observer/1",
                    "X-GOSIM26-Request-ID": uuid.uuid4().hex,
                },
                method="POST",
            )
            transport = self._transport or urllib.request.urlopen
            with transport(request, timeout=self.timeout) as response:
                raw = response.read(self.config.max_response_bytes + 1)
            if len(raw) > self.config.max_response_bytes:
                raise AdviceError("response_too_large")
            payload = json.loads(raw.decode("utf-8"))
            choices = payload.get("choices") if isinstance(payload, dict) else None
            if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
                raise AdviceError("missing_choice")
            if choices[0].get("finish_reason") == "length":
                raise AdviceError("completion_truncated")
            message = choices[0].get("message")
            parsed = self._json_content(message.get("content") if isinstance(message, dict) else None)
            if parsed is None:
                raise AdviceError("invalid_advice_json")
            choice = str(parsed.get("choice")) if parsed and parsed.get("choice") is not None else ""
            if choice not in ids:
                raise AdviceError("invalid_choice")
            probabilities = self._normalise_probabilities(parsed.get("probabilities") if parsed else None, ids)
            if probabilities is None:
                raise AdviceError("invalid_probabilities")
            self.successes += 1
            self.consecutive_failures = 0
            self.last_outcome = {
                "stage": stage,
                "available": True,
                "choice": choice,
                "deterministic_choice": deterministic,
                "probabilities": probabilities,
                "fallback": False,
                "latency_ms": None,
                "context_bytes": self.context_bytes,
                "context_truncated": self.context_truncated,
            }
            if self.config.selection_mode == "deterministic":
                return None
            if (
                self.config.selection_mode == "guided"
                and deterministic is not None
                and choice != deterministic
                and probabilities.get(choice, 0.0) < self.config.min_alternative_probability
            ):
                self.last_outcome["guided_override"] = True
                self.last_outcome["accepted_choice"] = None
                return None
            self.last_outcome["accepted_choice"] = choice
            return choice
        except Exception as exc:  # noqa: BLE001  # network, provider, and schema failures all fail closed
            self.failures += 1
            self.consecutive_failures += 1
            reason = (str(exc) if isinstance(exc, AdviceError) else
                      f"http_{exc.code}" if isinstance(exc, urllib.error.HTTPError) else type(exc).__name__)
            self._fallback(stage, deterministic, reason)
            LOG.warning("model advice failed at %s: %s", stage, reason)
            return None


__all__ = ["AdvisorConfig", "ModelAdvisor"]
