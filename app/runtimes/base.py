"""Agent runtime protocol (#93)."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from .models import RuntimeRequest, RuntimeResult


@runtime_checkable
class AgentRuntime(Protocol):
    """Specialized execution runtime (not a generic LLM vendor)."""

    @property
    def name(self) -> str:
        """Stable runtime identifier (e.g. ``builtin``, ``codex``, ``fake``)."""

    def execute(self, request: RuntimeRequest) -> RuntimeResult:
        """Run a bounded coding/repository task."""
