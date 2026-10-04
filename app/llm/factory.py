"""Factory/registry for framework-neutral LLM providers (#61/#93)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final

import requests

from .anthropic_provider import (
    DEFAULT_ANTHROPIC_MESSAGES_URL,
    AnthropicProvider,
)
from .base import LLMProvider
from .exceptions import LLMProviderError
from .fake_provider import FakeLLMProvider
from .openai_provider import DEFAULT_OPENAI_RESPONSES_URL, OpenAIProvider

_SUPPORTED_PROVIDERS: Final[frozenset[str]] = frozenset({"openai", "anthropic", "fake"})
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
    api_keys: Mapping[str, str | None] | None = None,
) -> LLMProvider:
    """Create an LLM provider from project config/env conventions.

    ``api_keys`` may supply per-provider credentials when falling back across
    vendors. ``base_url`` remains reserved for a future Ollama adapter.
    """

    del base_url  # reserved for future local providers
    name = provider.strip().lower()
    if name == "ollama":
        raise LLMProviderError(
            "Ollama provider is reserved but not implemented yet; "
            "use provider 'openai', 'anthropic', or 'fake'"
        )
    if name not in _SUPPORTED_PROVIDERS:
        supported = ", ".join(sorted(_SUPPORTED_PROVIDERS))
        raise LLMProviderError(
            f"Unknown LLM provider {provider!r}; supported: {supported}"
        )

    resolved_key = _resolve_api_key(name, api_key=api_key, api_keys=api_keys)

    if name == "fake":
        return FakeLLMProvider(default_model=model)
    if name == "anthropic":
        return AnthropicProvider(
            api_key=resolved_key or "",
            default_model=model,
            default_timeout_seconds=timeout_seconds,
            default_temperature=temperature,
            api_url=api_url or DEFAULT_ANTHROPIC_MESSAGES_URL,
            session=session,
        )
    return OpenAIProvider(
        api_key=resolved_key or "",
        default_model=model,
        default_timeout_seconds=timeout_seconds,
        default_temperature=temperature,
        api_url=api_url or DEFAULT_OPENAI_RESPONSES_URL,
        session=session,
    )


def supported_llm_providers() -> frozenset[str]:
    """Return implemented provider names (excludes reserved stubs)."""

    return _SUPPORTED_PROVIDERS


def resolve_provider_api_key(
    provider: str,
    *,
    api_key: str | None = None,
    api_keys: Mapping[str, str | None] | None = None,
) -> str | None:
    """Public helper for routers/CLI to pick the credential for a provider."""

    return _resolve_api_key(
        provider.strip().lower(),
        api_key=api_key,
        api_keys=api_keys,
    )


def _resolve_api_key(
    provider: str,
    *,
    api_key: str | None,
    api_keys: Mapping[str, str | None] | None,
) -> str | None:
    if api_keys is not None:
        if provider in api_keys and api_keys[provider]:
            return api_keys[provider]
        # Explicit None in map means "no key for this provider".
        return api_keys.get(provider)
    return api_key
