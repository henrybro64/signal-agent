"""LLM provider protocol."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from .models import LLMRequest, LLMResponse


@runtime_checkable
class LLMProvider(Protocol):
    """Framework-neutral LLM gateway interface."""

    @property
    def name(self) -> str:
        """Stable provider identifier (e.g. ``openai``, ``fake``)."""

    def generate(self, request: LLMRequest) -> LLMResponse:
        """Generate text (optionally structured) for ``request``."""
