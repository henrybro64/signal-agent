"""Project-owned search provider exceptions (#93)."""

from __future__ import annotations


class SearchError(Exception):
    """Base class for search gateway failures."""


class SearchTimeoutError(SearchError):
    """Raised when a search call exceeds the configured timeout."""


class SearchProviderError(SearchError):
    """Raised for transport, HTTP, or configuration failures."""


class SearchAuthError(SearchProviderError):
    """Raised when credentials are missing/invalid."""


class SearchRateLimitError(SearchProviderError):
    """Raised when the provider rate-limits the caller."""
