"""Tool protocol and shared helpers."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from .models import ToolContext, ToolResult
from .permissions import PermissionClass


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

    @property
    def permission(self) -> PermissionClass:
        """Explicit permission classification (#84). Default-deny if missing."""

    def invoke(
        self,
        arguments: dict[str, Any],
        *,
        context: ToolContext,
    ) -> ToolResult:
        """Execute the tool and return a normalized result."""