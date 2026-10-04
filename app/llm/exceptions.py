"""Project-owned LLM exceptions (no provider SDK types leak upward)."""

from __future__ import annotations


class LLMError(Exception):
    """Base class for LLM gateway failures."""


class LLMTimeoutError(LLMError):
    """Raised when a provider call exceeds the configured timeout."""


class LLMProviderError(LLMError):
    """Raised for transport, HTTP, or configuration failures."""


class LLMAuthError(LLMProviderError):
    """Raised when credentials are missing/invalid (e.g. HTTP 401/403)."""


class LLMRateLimitError(LLMProviderError):
    """Raised when the provider rate-limits the caller (e.g. HTTP 429)."""


class LLMNetworkError(LLMProviderError):
    """Raised for connectivity / DNS / connection-reset failures."""


class LLMResponseError(LLMError):
    """Raised when a provider response is missing or malformed."""
