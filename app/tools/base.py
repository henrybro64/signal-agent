"""Tool protocol and shared helpers."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from .models import ToolContext, ToolResult


@runtime_checkable
class Tool(Protocol):
    """Bounded callable capability with a typed input contract."""

    @property
    def name(self) -> str:
        """Stable tool identifier used by registries and allowlists."""

    @property
    def description(self) -> str:
        """Human/LLM-facing description (must not contain secrets)."""

    @property
    def input_schema(self) -> dict[str, Any]:
        """JSON Schema for tool arguments."""

    def invoke(
        self,
        arguments: dict[str, Any],
        *,
        context: ToolContext,
    ) -> ToolResult:
        """Execute the tool and return a normalized result."""
