"""Vendor-neutral search/research provider gateway (#93)."""

from .base import SearchProvider
from .exceptions import (
    SearchAuthError,
    SearchError,
    SearchProviderError,
    SearchRateLimitError,
    SearchTimeoutError,
)
from .factory import create_search_provider, supported_search_providers
from .fake_provider import FakeSearchProvider
from .models import SearchRequest, SearchResponse, SearchResult
from .perplexity_provider import PerplexitySearchProvider

__all__ = [
    "FakeSearchProvider",
    "PerplexitySearchProvider",
    "SearchAuthError",
    "SearchError",
    "SearchProvider",
    "SearchProviderError",
    "SearchRateLimitError",
    "SearchRequest",
    "SearchResponse",
    "SearchResult",
    "SearchTimeoutError",
    "create_search_provider",
    "supported_search_providers",
]
