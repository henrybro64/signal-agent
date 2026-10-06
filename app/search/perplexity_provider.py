"""Perplexity search/research adapter behind SearchProvider (#93)."""

from __future__ import annotations

import logging
import time
from typing import Any, Final

import requests

from app.core.logging import get_logger, log_event

from .exceptions import (
    SearchAuthError,
    SearchProviderError,
    SearchRateLimitError,
    SearchTimeoutError,
)
from .models import SearchRequest, SearchResponse, SearchResult

DEFAULT_PERPLEXITY_CHAT_URL: Final[str] = "https://api.perplexity.ai/chat/completions"
_DEFAULT_MODEL: Final[str] = "sonar"
_DEFAULT_TIMEOUT_SECONDS: Final[float] = 20.0
_LOGGER = get_logger("search.perplexity")


class PerplexitySearchProvider:
    """Use Perplexity chat/completions as a research/search backend."""

    name = "perplexity"

    def __init__(
        self,
        *,
        api_key: str,
        model: str = _DEFAULT_MODEL,
        default_timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
        api_url: str = DEFAULT_PERPLEXITY_CHAT_URL,
        session: requests.Session | None = None,
    ) -> None:
        if not api_key.strip():
            raise SearchAuthError("Perplexity API key must be a non-empty string")
        self._api_key = api_key
        self.model = model
        self.default_timeout_seconds = default_timeout_seconds
        self.api_url = api_url
        self.session = session or requests.Session()

    def search(self, request: SearchRequest) -> SearchResponse:
        query = request.query.strip()
        if not query:
            raise SearchProviderError("Search query must be non-empty")
        timeout = (
            self.default_timeout_seconds
            if request.timeout_seconds is None
            else request.timeout_seconds
        )
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are a research assistant. Answer briefly and "
                        "include source URLs when possible."
                    ),
                },
                {"role": "user", "content": query},
            ],
            "max_tokens": 512,
        }
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        started = time.perf_counter()
        try:
            response = self.session.post(
                self.api_url,
                headers=headers,
                json=payload,
                timeout=timeout,
            )
        except requests.Timeout:
            raise SearchTimeoutError("Perplexity search timed out") from None
        except requests.RequestException:
            raise SearchProviderError("Perplexity search request failed") from None

        duration_ms = int((time.perf_counter() - started) * 1000)
        if response.status_code >= 400:
            log_event(
                _LOGGER,
                event="search_failed",
                message="Perplexity search failed",
                level=logging.WARNING,
                provider=self.name,
                status="http_error",
                http_status=response.status_code,
                duration_ms=duration_ms,
            )
            if response.status_code in {401, 403}:
                raise SearchAuthError(
                    f"Perplexity authentication failed with status "
                    f"{response.status_code}"
                )
            if response.status_code == 429:
                raise SearchRateLimitError("Perplexity rate limit exceeded")
            raise SearchProviderError(
                f"Perplexity search failed with status {response.status_code}"
            )

        try:
            body = response.json()
        except ValueError:
            raise SearchProviderError(
                "Perplexity response was not valid JSON"
            ) from None
        if not isinstance(body, dict):
            raise SearchProviderError("Perplexity response was not an object")

        answer = _extract_answer(body)
        citations = _extract_citations(body, limit=request.max_results)
        log_event(
            _LOGGER,
            event="search_completed",
            message="Perplexity search completed",
            provider=self.name,
            model=self.model,
            duration_ms=duration_ms,
            result_count=len(citations),
        )
        return SearchResponse(
            query=query,
            results=citations,
            answer=answer,
            provider=self.name,
            raw=body,
        )


def _extract_answer(payload: dict[str, Any]) -> str | None:
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        return None
    message = choices[0].get("message") if isinstance(choices[0], dict) else None
    if not isinstance(message, dict):
        return None
    content = message.get("content")
    return content.strip() if isinstance(content, str) else None


def _extract_citations(
    payload: dict[str, Any],
    *,
    limit: int,
) -> tuple[SearchResult, ...]:
    citations = payload.get("citations")
    results: list[SearchResult] = []
    if isinstance(citations, list):
        for index, item in enumerate(citations[: max(1, limit)]):
            if isinstance(item, str) and item.strip():
                results.append(
                    SearchResult(
                        title=f"Source {index + 1}",
                        url=item.strip(),
                        snippet="",
                    )
                )
            elif isinstance(item, dict):
                url = str(item.get("url") or item.get("link") or "").strip()
                if not url:
                    continue
                results.append(
                    SearchResult(
                        title=str(item.get("title") or f"Source {index + 1}"),
                        url=url,
                        snippet=str(item.get("snippet") or ""),
                    )
                )
    return tuple(results)
