"""Project-owned LLM exceptions (no provider SDK types leak upward)."""

from __future__ import annotations


class LLMError(Exception):
    """Base class for LLM gateway failures."""


class LLMTimeoutError(LLMError):
    """Raised when a provider call exceeds the configured timeout."""


class LLMProviderError(LLMError):
    """Raised for transport, HTTP, or configuration failures."""


class LLMResponseError(LLMError):
    """Raised when a provider response is missing or malformed."""
