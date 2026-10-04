"""Search request/response models (#93)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True, frozen=True)
class SearchResult:
    title: str
    url: str
    snippet: str = ""
    score: float | None = None


@dataclass(slots=True, frozen=True)
class SearchRequest:
    query: str
    max_results: int = 5
    timeout_seconds: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True, frozen=True)
class SearchResponse:
    query: str
    results: tuple[SearchResult, ...] = ()
    answer: str | None = None
    provider: str | None = None
    raw: dict[str, Any] | None = field(default=None, repr=False)
