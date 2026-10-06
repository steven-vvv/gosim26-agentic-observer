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
import re
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

LOG = logging.getLogger(__name__)
MODEL_REQUEST_CONTEXT_BYTES = 60_000


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
    max_context_bytes: int = MODEL_REQUEST_CONTEXT_BYTES
    max_response_bytes: int = 65_536
    max_tokens: int = 4096
    temperature: float = 0.0
    top_p: float = 1.0
    selection_mode: str = "guided"
    min_alternative_probability: float = 0.85
    stream: bool = False


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
        "You are the planning and decision agent of the GOSIM26 survey observer, a telescope "
        "that observes a target catalogue over many nights. The observer hands you two kinds "
        "of decisions. weekly_planning: at each new weekly weather forecast, choose the survey "
        "strategy for the coming week, weighing the forecast notices, the share of required "
        "targets still missing against the nights left, active observation requests and the "
        "remaining wall clock. action_decision: choose which of two near-equal candidate "
        "observations to execute now, comparing pointing, program, duration and predicted "
        "targets (required targets below the completion threshold matter most, then science "
        "weight times the gain over the prior best score). Every candidate is feasible. "
        "deterministic_choice is what the controller would do without you and the merits are "
        "its estimates: evidence, not a verdict. Decide from the live state. Never invent "
        "state or candidates. Return JSON only: one object with keys choice (a candidate id), "
        "probabilities (every candidate id mapped to a number between 0 and 1, summing to 1) "
        "and reason (one short sentence)."
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
        on_event: Any | None = None,
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
        self.usage_totals = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        self.on_event = on_event
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
            max_context_bytes=_as_int(
                pick("OBSERVER_MODEL_MAX_CONTEXT_BYTES", MODEL_REQUEST_CONTEXT_BYTES),
                MODEL_REQUEST_CONTEXT_BYTES,
                4_096,
            ),
            max_response_bytes=_as_int(pick("OBSERVER_MODEL_MAX_RESPONSE_BYTES", 65_536), 65_536, 1_024),
            max_tokens=min(4_096, _as_int(pick("OBSERVER_MODEL_MAX_TOKENS", 4_096), 4_096, 128)),
            temperature=_as_float(pick("OBSERVER_MODEL_TEMPERATURE", 0.0), 0.0, 0.0, 2.0),
            top_p=_as_float(pick("OBSERVER_MODEL_TOP_P", 1.0), 1.0, 0.0, 1.0),
            selection_mode=selection_mode,
            min_alternative_probability=_as_float(
                pick("OBSERVER_MODEL_MIN_ALTERNATIVE_PROBABILITY", 0.85), 0.85, 0.0, 1.0
            ),
            stream=_as_bool(pick("OBSERVER_MODEL_STREAM", False), False),
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
            "usage_totals": dict(self.usage_totals),
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
        start, end = text.find("{"), text.rfind("}")
        candidates = [text]
        if 0 <= start < end:
            body = text[start : end + 1]
            # A reply seen in practice closes a number with a stray quote: {"C0":0.55,"C1":0.45"}.
            candidates += [body, re.sub(r'(:\s*-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)"(\s*[,}\]])', r"\1\2", body)]
        for candidate in candidates:
            try:
                value = json.loads(candidate)
            except ValueError:
                continue
            return value if isinstance(value, dict) else None
        return None

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
            "live_state": dict(context),
            "deterministic_choice": deterministic,
            "candidates": list(options),
        }
        encoded = json.dumps(dynamic, ensure_ascii=True, separators=(",", ":"))
        self.context_bytes = len(encoded.encode("utf-8"))
        if self.context_bytes > self.config.max_context_bytes:
            raise AdviceError("context_too_large")
        body = {
            "model": self.model,
            "top_p": self.config.top_p,
            "max_tokens": self.config.max_tokens,
            "stream": self.config.stream,
            "messages": [
                {"role": "system", "content": self._SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(dynamic, ensure_ascii=True, separators=(",", ":"))},
            ],
        }
        # Kimi Coding Plan's k3 endpoint rejects an explicit temperature even
        # when it is the provider default. Keep the regular OpenAI-compatible
        # request unchanged for every other endpoint.
        host = urlsplit(self.url or "").hostname
        kimi_endpoint = host in {"api.kimi.com", "api.kimi.ai"}
        kimi_model = self.model.strip().lower() in {"k3", "kimi-for-coding"}
        if not (kimi_endpoint or kimi_model):
            body["temperature"] = self.config.temperature
        if self.config.stream:
            body["stream_options"] = {"include_usage": True}
        return body

    @staticmethod
    def _usage(value: Any) -> dict[str, int] | None:
        if not isinstance(value, Mapping):
            return None
        result = {}
        for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
            item = value.get(key)
            if isinstance(item, int) and not isinstance(item, bool) and item >= 0:
                result[key] = item
        return result or None

    def _read_stream(self, response: Any, started: float, metrics: dict[str, Any]) -> dict[str, Any]:
        content: list[str] = []
        finish_reason = None
        completed = False
        data_lines: list[str] = []

        def process_event() -> None:
            nonlocal finish_reason, completed
            if not data_lines:
                return
            data = "\n".join(data_lines)
            data_lines.clear()
            if data == "[DONE]":
                completed = True
                return
            try:
                chunk = json.loads(data)
            except ValueError as exc:
                raise AdviceError("invalid_stream_json") from exc
            if not isinstance(chunk, dict):
                raise AdviceError("invalid_stream_chunk")
            metrics["usage"] = self._usage(chunk.get("usage")) or metrics["usage"]
            choices = chunk.get("choices")
            if not isinstance(choices, list) or not choices:
                return
            first = choices[0]
            if not isinstance(first, dict):
                raise AdviceError("invalid_stream_chunk")
            delta = first.get("delta")
            text = delta.get("content") if isinstance(delta, dict) else None
            reasoning = delta.get("reasoning_content") if isinstance(delta, dict) else None
            if isinstance(delta, dict) and not reasoning:
                reasoning = delta.get("reasoning")
            if (((isinstance(text, str) and text) or (isinstance(reasoning, str) and reasoning)) and
                    metrics["generation_ttft_ms"] is None):
                metrics["generation_ttft_ms"] = round((time.perf_counter() - started) * 1000, 3)
            if isinstance(text, str) and text:
                if metrics["ttft_ms"] is None:
                    metrics["ttft_ms"] = round((time.perf_counter() - started) * 1000, 3)
                content.append(text)
            finish_reason = first.get("finish_reason") or finish_reason

        while True:
            raw_line = response.readline(self.config.max_response_bytes - metrics["response_bytes"] + 1)
            if not raw_line:
                break
            if not isinstance(raw_line, bytes):
                raise AdviceError("invalid_stream_line")
            metrics["response_bytes"] += len(raw_line)
            if metrics["response_bytes"] > self.config.max_response_bytes:
                raise AdviceError("response_too_large")
            if (time.perf_counter() - started) > self.timeout:
                raise AdviceError("request_deadline_exceeded")
            if metrics["first_byte_ms"] is None:
                metrics["first_byte_ms"] = round((time.perf_counter() - started) * 1000, 3)
            line = raw_line.decode("utf-8", errors="replace").strip()
            if not line:
                process_event()
                if completed:
                    break
            elif line.startswith("data:"):
                data_lines.append(line[5:].lstrip())
        if data_lines:
            process_event()
        if not completed:
            raise AdviceError("incomplete_stream")
        return {"choices": [{"finish_reason": finish_reason, "message": {"content": "".join(content)}}],
                "usage": metrics["usage"]}

    def _notify(self, outcome: dict[str, Any], sequence: Any) -> None:
        if self.on_event is None:
            return
        event = {**outcome, "decision_sequence": sequence, "model": self.model}
        try:
            self.on_event(event)
        except Exception as exc:  # noqa: BLE001  # diagnostics may never affect the policy
            LOG.debug("model diagnostics callback failed: %s", type(exc).__name__)

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
        started = time.perf_counter()
        request_bytes = 0
        metrics = {"response_bytes": 0, "first_byte_ms": None, "ttft_ms": None,
                   "generation_ttft_ms": None, "usage": None}
        usage = None
        usage_accounted = False
        try:
            body = self._request_body(stage, options, context)
            encoded = json.dumps(body, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
            request_bytes = len(encoded)
            call_id = str(uuid.uuid4())
            request = urllib.request.Request(
                self.url or "",
                data=encoded,
                headers={
                    "Authorization": f"Bearer {self.key}",
                    "Content-Type": "application/json",
                    "Accept": "text/event-stream" if self.config.stream else "application/json",
                    "User-Agent": "gosim26-observer/1",
                    "Idempotency-Key": call_id,
                    "X-GOSIM26-Request-ID": call_id,
                },
                method="POST",
            )
            transport = self._transport or urllib.request.urlopen
            with transport(request, timeout=self.timeout) as response:
                if self.config.stream and hasattr(response, "readline"):
                    payload = self._read_stream(response, started, metrics)
                else:
                    raw = response.read(self.config.max_response_bytes + 1)
                    metrics["response_bytes"] = len(raw)
                    if metrics["response_bytes"] > self.config.max_response_bytes:
                        raise AdviceError("response_too_large")
                    payload = json.loads(raw.decode("utf-8"))
            usage = self._usage(payload.get("usage") if isinstance(payload, dict) else None)
            if usage:
                for key, value in usage.items():
                    self.usage_totals[key] += value
                usage_accounted = True
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
            duration_ms = round((time.perf_counter() - started) * 1000, 3)
            completion_tokens = usage.get("completion_tokens") if usage else None
            generation_ttft_ms = metrics["generation_ttft_ms"]
            reason = parsed.get("reason")
            self.last_outcome = {
                "stage": stage,
                "available": True,
                "choice": choice,
                "model_reason": reason[:240] if isinstance(reason, str) else None,
                "deterministic_choice": deterministic,
                "probabilities": probabilities,
                "fallback": False,
                "duration_ms": duration_ms,
                "first_byte_ms": metrics["first_byte_ms"],
                "ttft_ms": metrics["ttft_ms"],
                "generation_ttft_ms": generation_ttft_ms,
                "output_tokens_per_second": (round(completion_tokens * 1000 / max(duration_ms - generation_ttft_ms, 1), 3)
                                             if completion_tokens is not None and generation_ttft_ms is not None else None),
                "usage": usage,
                "request_bytes": request_bytes,
                "response_bytes": metrics["response_bytes"],
                "stream": self.config.stream,
                "context_bytes": self.context_bytes,
                "context_truncated": self.context_truncated,
            }
            if self.config.selection_mode == "deterministic":
                self.last_outcome["accepted_choice"] = None
                self._notify(self.last_outcome, context.get("decision_sequence"))
                return None
            eligible = context.get("eligible_choices")
            if isinstance(eligible, list) and choice not in eligible:
                self.last_outcome["safety_override"] = True
                self.last_outcome["accepted_choice"] = None
                self._notify(self.last_outcome, context.get("decision_sequence"))
                return None
            if (
                self.config.selection_mode == "guided"
                and deterministic is not None
                and choice != deterministic
                and probabilities.get(choice, 0.0) < self.config.min_alternative_probability
            ):
                self.last_outcome["guided_override"] = True
                self.last_outcome["accepted_choice"] = None
                self._notify(self.last_outcome, context.get("decision_sequence"))
                return None
            self.last_outcome["accepted_choice"] = choice
            self._notify(self.last_outcome, context.get("decision_sequence"))
            return choice
        except Exception as exc:  # noqa: BLE001  # network, provider, and schema failures all fail closed
            self.failures += 1
            self.consecutive_failures += 1
            usage = usage or metrics["usage"]
            if usage and not usage_accounted:
                for key, value in usage.items():
                    self.usage_totals[key] += value
            reason = (str(exc) if isinstance(exc, AdviceError) else
                      f"http_{exc.code}" if isinstance(exc, urllib.error.HTTPError) else type(exc).__name__)
            self._fallback(stage, deterministic, reason)
            self.last_outcome.update({
                "duration_ms": round((time.perf_counter() - started) * 1000, 3),
                "first_byte_ms": metrics["first_byte_ms"],
                "ttft_ms": metrics["ttft_ms"],
                "generation_ttft_ms": metrics["generation_ttft_ms"],
                "usage": usage,
                "request_bytes": request_bytes,
                "response_bytes": metrics["response_bytes"],
                "stream": self.config.stream,
            })
            self._notify(self.last_outcome, context.get("decision_sequence"))
            if self.on_event is None:
                LOG.warning("model advice failed at %s: %s", stage, reason)
            return None

    def extract(self, stage: str, system_prompt: str, payload: Mapping[str, Any],
                sequence: Any = None, extra_body: Mapping[str, Any] | None = None,
                timeout: float | None = None) -> dict[str, Any] | None:
        """Return the model's JSON object for a reading task, or ``None`` on any failure.

        Same transport, budgets and circuit breaker as ``choose``; the caller validates
        every field of the returned object.
        """
        if not self.enabled:
            return None
        used = self.stage_calls.get(stage, 0)
        if used >= self.config.max_stage_calls:
            return None
        self.stage_calls[stage] = used + 1
        self.calls += 1
        started = time.perf_counter()
        usage = None
        try:
            content = json.dumps(dict(payload), ensure_ascii=False, separators=(",", ":"))
            self.context_bytes = len(content.encode("utf-8"))
            if self.context_bytes > self.config.max_context_bytes:
                raise AdviceError("context_too_large")
            body = {
                "model": self.model,
                "top_p": self.config.top_p,
                "max_tokens": self.config.max_tokens,
                "stream": False,
                "messages": [{"role": "system", "content": system_prompt}, {"role": "user", "content": content}],
            }
            host = urlsplit(self.url or "").hostname
            if not (host in {"api.kimi.com", "api.kimi.ai"} or self.model.strip().lower() in {"k3", "kimi-for-coding"}):
                body["temperature"] = self.config.temperature
            body.update(extra_body or {})
            encoded = json.dumps(body, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
            call_id = str(uuid.uuid4())
            request = urllib.request.Request(
                self.url or "",
                data=encoded,
                headers={
                    "Authorization": f"Bearer {self.key}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                    "User-Agent": "gosim26-observer/1",
                    "Idempotency-Key": call_id,
                    "X-GOSIM26-Request-ID": call_id,
                },
                method="POST",
            )
            transport = self._transport or urllib.request.urlopen
            with transport(request, timeout=timeout or self.timeout) as response:
                raw = response.read(self.config.max_response_bytes + 1)
            if len(raw) > self.config.max_response_bytes:
                raise AdviceError("response_too_large")
            response_payload = json.loads(raw.decode("utf-8"))
            usage = self._usage(response_payload.get("usage") if isinstance(response_payload, dict) else None)
            if usage:
                for key, value in usage.items():
                    self.usage_totals[key] += value
            choices = response_payload.get("choices") if isinstance(response_payload, dict) else None
            if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
                raise AdviceError("missing_choice")
            if choices[0].get("finish_reason") == "length":
                raise AdviceError("completion_truncated")
            message = choices[0].get("message")
            parsed = self._json_content(message.get("content") if isinstance(message, dict) else None)
            if parsed is None:
                raise AdviceError("invalid_reading_json")
            self.successes += 1
            self.consecutive_failures = 0
            self.last_outcome = {"stage": stage, "available": True, "fallback": False,
                                 "duration_ms": round((time.perf_counter() - started) * 1000, 3),
                                 "usage": usage, "request_bytes": len(encoded), "response_bytes": len(raw)}
            self._notify(self.last_outcome, sequence)
            return parsed
        except Exception as exc:  # noqa: BLE001  # network, provider, and schema failures all fail closed
            self.failures += 1
            self.consecutive_failures += 1
            reason = (str(exc) if isinstance(exc, AdviceError) else
                      f"http_{exc.code}" if isinstance(exc, urllib.error.HTTPError) else type(exc).__name__)
            self.last_outcome = {"stage": stage, "available": self.enabled, "fallback": True, "reason": reason,
                                 "duration_ms": round((time.perf_counter() - started) * 1000, 3), "usage": usage}
            self._notify(self.last_outcome, sequence)
            if self.on_event is None:
                LOG.warning("model reading failed at %s: %s", stage, reason)
            return None


__all__ = ["AdvisorConfig", "ModelAdvisor"]
