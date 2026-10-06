"""Deterministic fake search provider for offline tests (#93)."""

from __future__ import annotations

from .models import SearchRequest, SearchResponse, SearchResult


class FakeSearchProvider:
    """In-memory search adapter that never touches the network."""

    name = "fake"

    def __init__(
        self,
        *,
        results: list[SearchResult] | None = None,
        answer: str | None = "fake research answer",
    ) -> None:
        self._results = list(results or [])
        self.answer = answer
        self.calls: list[SearchRequest] = []

    def search(self, request: SearchRequest) -> SearchResponse:
        self.calls.append(request)
        limit = max(1, request.max_results)
        if self._results:
            chosen = tuple(self._results[:limit])
        else:
            chosen = (
                SearchResult(
                    title=f"Result for {request.query}",
                    url="https://fixture.local/search",
                    snippet=f"Offline fixture snippet for {request.query}",
                ),
            )
        return SearchResponse(
            query=request.query,
            results=chosen,
            answer=self.answer,
            provider=self.name,
        )
