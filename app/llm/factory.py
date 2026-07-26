"""Factory for framework-neutral LLM providers."""

from __future__ import annotations

from typing import Final

import requests

from .base import LLMProvider
from .exceptions import LLMProviderError
from .fake_provider import FakeLLMProvider
from .openai_provider import DEFAULT_OPENAI_RESPONSES_URL, OpenAIProvider

_SUPPORTED_PROVIDERS: Final[frozenset[str]] = frozenset({"openai", "fake"})
_DEFAULT_TIMEOUT_SECONDS: Final[float] = 20.0
_DEFAULT_TEMPERATURE: Final[float] = 0.2


def create_llm_provider(
    *,
    provider: str = "openai",
    api_key: str | None = None,
    model: str = "gpt-4o-mini",
    timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
    temperature: float = _DEFAULT_TEMPERATURE,
    session: requests.Session | None = None,
    api_url: str | None = None,
    base_url: str | None = None,
) -> LLMProvider:
    """Create an LLM provider from project config/env conventions.

    ``base_url`` is reserved for a future Ollama/local adapter and is ignored
    for ``openai`` / ``fake``. CI must not require a running Ollama server.
    """

    del base_url  # reserved for future local providers
    name = provider.strip().lower()
    if name == "ollama":
        raise LLMProviderError(
            "Ollama provider is reserved but not implemented yet; "
            "use provider 'openai' or 'fake'"
        )
    if name not in _SUPPORTED_PROVIDERS:
        raise LLMProviderError(
            f"Unknown LLM provider {provider!r}; "
            f"supported: {', '.join(sorted(_SUPPORTED_PROVIDERS))}"
        )
    if name == "fake":
        return FakeLLMProvider(default_model=model)
    return OpenAIProvider(
        api_key=api_key or "",
        default_model=model,
        default_timeout_seconds=timeout_seconds,
        default_temperature=temperature,
        api_url=api_url or DEFAULT_OPENAI_RESPONSES_URL,
        session=session,
    )


def supported_llm_providers() -> frozenset[str]:
    """Return implemented provider names (excludes reserved stubs)."""

    return _SUPPORTED_PROVIDERS
