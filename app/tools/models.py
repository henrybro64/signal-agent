"""Project-owned tool models (framework-neutral)."""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from app.core.config import AppConfig
from app.core.secrets import EnvSettings


class SupportsLLMGenerate(Protocol):
    """Minimal LLM surface tools may use (duck-typed ``LLMProvider``)."""

    def generate(self, request: Any) -> Any: ...


@dataclass(slots=True)
class ToolContext:
    """Dependency bag injected into tool invocations."""

    config: AppConfig | None = None
    env: EnvSettings | None = None
    db_path: Path | None = None
    llm: SupportsLLMGenerate | None = None
    telegram_enabled: bool = False
    timeout_seconds: float = 30.0
    max_items: int = 20
    # Cooperative cancellation signal set when the registry timeout fires.
    cancel_event: threading.Event | None = None
    # Optional callables for unit tests / fakes (skip live network).
    fetch_jobs: Callable[..., list[dict[str, Any]]] | None = None
    fetch_movers: Callable[..., list[dict[str, Any]]] | None = None
    send_telegram: Callable[[str], None] | None = None
    query_runs: Callable[..., list[dict[str, Any]]] | None = None
    secrets_to_redact: tuple[str, ...] = ()
    # Permission policy gate (#84). When unset, registry uses AppConfig when
    # present, else a scheduler/runtime ContextVar, else conservative defaults
    # (notify disabled). Set False to opt out of the gate entirely.
    interactive: bool = False
    permission_policy: Any | None = None


@dataclass(slots=True, frozen=True)
class ToolResult:
    """Normalized tool invocation outcome."""

    ok: bool
    data: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_safe_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "ok": self.ok,
            "data": self.data,
            "metadata": self.metadata,
        }
        if self.error is not None:
            payload["error"] = self.error
        return payload


class ToolError(RuntimeError):
    """Raised for tool validation or invocation failures before result wrap."""

    def __init__(self, message: str, *, code: str = "tool_error") -> None:
        super().__init__(message)
        self.code = code
