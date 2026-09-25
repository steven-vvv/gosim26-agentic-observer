"""Environment-driven LangChain model construction for the minimal agent."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


PROVIDER_ALIASES = {
    "chatgpt": "openai",
    "claude": "anthropic",
    "grok": "xai",
    "glm": "zai",
    "kimi": "moonshot",
    "qwen": "dashscope",
}

PROVIDER_KEY_ENV = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "xai": "XAI_API_KEY",
    "zai": "ZAI_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "moonshot": "MOONSHOT_API_KEY",
    "dashscope": "DASHSCOPE_API_KEY",
    "minimax": "MINIMAX_API_KEY",
}


class ModelConfigurationError(ValueError):
    """Raised when an optional model provider is only partially configured."""


@dataclass(frozen=True)
class ModelSettings:
    """Secret-safe provider settings loaded from process environment variables."""

    provider: str
    model: str
    base_url: str
    api_key: str = field(repr=False)
    api_mode: str = "chat"
    timeout_seconds: float = 30.0
    max_retries: int = 1
    top_k_candidates: int = 12

    @classmethod
    def from_environment(cls, dotenv_path: Path | None = None) -> "ModelSettings":
        try:
            from dotenv import load_dotenv
        except ModuleNotFoundError:
            load_dotenv = None
        if load_dotenv is not None:
            load_dotenv(dotenv_path=dotenv_path, override=False)
        provider = os.environ.get("MODEL_PROVIDER", "deterministic").strip().lower()
        provider = PROVIDER_ALIASES.get(provider, provider)
        key_name = os.environ.get("MODEL_API_KEY_ENV", "").strip() or PROVIDER_KEY_ENV.get(
            provider, "MODEL_API_KEY"
        )
        api_mode = os.environ.get(
            "MODEL_API_MODE", "responses" if provider == "openai" else "chat"
        ).strip().lower()
        return cls(
            provider=provider,
            model=os.environ.get("MODEL_NAME", "").strip(),
            base_url=os.environ.get("MODEL_BASE_URL", "").strip(),
            api_key=os.environ.get(key_name, "").strip(),
            api_mode=api_mode,
            timeout_seconds=float(os.environ.get("LLM_TIMEOUT_SECONDS", "30")),
            max_retries=int(os.environ.get("LLM_MAX_RETRIES", "1")),
            top_k_candidates=int(os.environ.get("LLM_TOP_K_CANDIDATES", "12")),
        )

    @property
    def deterministic(self) -> bool:
        return self.provider in {"", "none", "deterministic"}


def build_chat_model(settings: ModelSettings):
    """Build one optional LangChain chat model without leaking its credential."""
    if settings.deterministic:
        return None
    if not settings.model or not settings.api_key:
        raise ModelConfigurationError(
            f"{settings.provider} requires MODEL_NAME and its configured API key"
        )
    if settings.timeout_seconds <= 0 or settings.max_retries < 0:
        raise ModelConfigurationError("timeout must be positive and retries non-negative")
    if settings.top_k_candidates < 1:
        raise ModelConfigurationError("LLM_TOP_K_CANDIDATES must be positive")
    if settings.provider == "anthropic":
        try:
            from langchain_anthropic import ChatAnthropic
        except ModuleNotFoundError as exc:
            raise ModelConfigurationError(
                "install langchain-anthropic to use Anthropic"
            ) from exc
        return ChatAnthropic(
            model=settings.model,
            api_key=settings.api_key,
            timeout=settings.timeout_seconds,
            max_retries=settings.max_retries,
        )
    try:
        from langchain_openai import ChatOpenAI
    except ModuleNotFoundError as exc:
        raise ModelConfigurationError(
            "install langchain-openai to use OpenAI or an OpenAI-compatible endpoint"
        ) from exc
    if settings.provider != "openai" and not settings.base_url:
        raise ModelConfigurationError(
            f"{settings.provider} requires MODEL_BASE_URL for its compatible endpoint"
        )
    kwargs = {
        "model": settings.model,
        "api_key": settings.api_key,
        "timeout": settings.timeout_seconds,
        "max_retries": settings.max_retries,
    }
    if settings.base_url:
        kwargs["base_url"] = settings.base_url
    if settings.provider == "openai":
        if settings.api_mode not in {"chat", "responses"}:
            raise ModelConfigurationError("MODEL_API_MODE must be chat or responses")
        kwargs["use_responses_api"] = settings.api_mode == "responses"
    else:
        kwargs["use_responses_api"] = False
    return ChatOpenAI(**kwargs)

