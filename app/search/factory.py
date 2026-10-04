"""Factory for search/research providers (#93)."""

from __future__ import annotations

from typing import Final

import requests

from .base import SearchProvider
from .exceptions import SearchProviderError
from .fake_provider import FakeSearchProvider
from .perplexity_provider import (
    DEFAULT_PERPLEXITY_CHAT_URL,
    PerplexitySearchProvider,
)

_SUPPORTED: Final[frozenset[str]] = frozenset({"perplexity", "fake"})


def create_search_provider(
    *,
    provider: str = "fake",
    api_key: str | None = None,
    model: str = "sonar",
    timeout_seconds: float = 20.0,
    session: requests.Session | None = None,
    api_url: str | None = None,
) -> SearchProvider:
    name = provider.strip().lower()
    if name not in _SUPPORTED:
        supported = ", ".join(sorted(_SUPPORTED))
        raise SearchProviderError(
            f"Unknown search provider {provider!r}; supported: {supported}"
        )
    if name == "fake":
        return FakeSearchProvider()
    return PerplexitySearchProvider(
        api_key=api_key or "",
        model=model,
        default_timeout_seconds=timeout_seconds,
        api_url=api_url or DEFAULT_PERPLEXITY_CHAT_URL,
        session=session,
    )


def supported_search_providers() -> frozenset[str]:
    return _SUPPORTED
