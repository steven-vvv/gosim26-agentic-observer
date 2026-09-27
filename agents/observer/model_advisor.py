"""Bounded OpenAI-compatible model choices for two online decision stages."""
from __future__ import annotations

import json
import os
import sys
import urllib.parse
import urllib.request


class ModelAdvisor:
    def __init__(self, url: str | None, key: str | None, model: str = "team-model", timeout: float = 5.0) -> None:
        self.url = self._endpoint(url) if url and key else None
        self.key = key if self.url else None
        self.model = model or "team-model"
        self.timeout = min(5.0, max(0.2, timeout))
        self.calls = 0
        self.successes = 0
        self.failures = 0
        self.consecutive_failures = 0
        self.stage_calls: dict[str, int] = {}

    @staticmethod
    def _endpoint(value: str) -> str:
        url = value.strip().rstrip("/")
        if url.endswith("/chat/completions"):
            return url
        return f"{url}/chat/completions" if url.endswith("/v1") else f"{url}/v1/chat/completions"

    @classmethod
    def from_env(cls) -> ModelAdvisor:
        if os.environ.get("OBSERVER_MODEL_DISABLE", "").lower() in {"1", "true", "yes", "on"}:
            return cls(None, None)
        url = os.environ.get("OPENAI_BASE_URL", "")
        key = os.environ.get("OPENAI_API_KEY", "")
        # Direct Worker deployments use the same OpenAI-shaped endpoint for telemetry.
        # Its acknowledgement is not a planning model.
        host = urllib.parse.urlsplit(url).hostname or ""
        if host == "gosim26-telemetry.wxlsteven.workers.dev":
            return cls(None, None)
        raw_timeout = os.environ.get("OBSERVER_MODEL_TIMEOUT_SECONDS", "5")
        try:
            timeout = float(raw_timeout)
        except ValueError:
            timeout = 5.0
        return cls(url, key, os.environ.get("MODEL_NAME") or os.environ.get("OPENAI_MODEL") or "team-model", timeout)

    @property
    def enabled(self) -> bool:
        return bool(self.url and self.key and self.consecutive_failures < 3)

    def choose(self, stage: str, options: list[dict], context: dict) -> str | None:
        """Return a validated option ID; callers execute it rather than recomputing the choice."""
        if not self.enabled or len(options) < 2 or self.stage_calls.get(stage, 0) >= 4:
            return None
        ids = {option["id"] for option in options}
        prompt = {"stage": stage, "context": context, "options": options,
                  "instruction": "Choose one safe option. Return only JSON: {\"choice\":\"<option id>\"}."}
        body = json.dumps({
            "model": self.model,
            "messages": [
                {"role": "system", "content": "You schedule an astronomical survey. Select one of the provided valid options to maximize expected final score while respecting the remaining compute budget."},
                {"role": "user", "content": json.dumps(prompt, separators=(",", ":"))},
            ],
            "stream": False,
        }, separators=(",", ":")).encode("utf-8")
        request = urllib.request.Request(self.url or "", data=body, method="POST", headers={
            "authorization": f"Bearer {self.key}", "content-type": "application/json",
            "user-agent": "gosim26-observer-model/1",
        })
        self.calls += 1
        self.stage_calls[stage] = self.stage_calls.get(stage, 0) + 1
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                data = response.read(65537)
            if len(data) > 65536:
                raise ValueError("oversized model response")
            message = json.loads(data)["choices"][0]["message"]["content"]
            choice = json.loads(message)["choice"]
            if choice not in ids:
                raise ValueError("choice outside candidate set")
            self.successes += 1
            self.consecutive_failures = 0
            print(f"observer: model_choice stage={stage} choice={choice}", file=sys.stderr, flush=True)
            return choice
        except Exception as exc:  # noqa: BLE001  A model outage must not stop the survey.
            self.failures += 1
            self.consecutive_failures += 1
            print(f"observer: model_fallback stage={stage} error={type(exc).__name__}", file=sys.stderr, flush=True)
            return None


__all__ = ["ModelAdvisor"]
