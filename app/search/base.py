"""Search provider protocol (#93)."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from .models import SearchRequest, SearchResponse


@runtime_checkable
class SearchProvider(Protocol):
    """Framework-neutral web/research search interface."""

    @property
    def name(self) -> str:
        """Stable provider identifier (e.g. ``perplexity``, ``fake``)."""

    def search(self, request: SearchRequest) -> SearchResponse:
        """Execute a bounded research/search request."""
