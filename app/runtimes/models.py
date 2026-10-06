"""Runtime request/result models (#93)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True, frozen=True)
class RuntimeRequest:
    """Bounded coding/repository task for an agent runtime."""

    goal: str
    timeout_seconds: float = 30.0
    # Explicit allowlist of high-level actions; never implies unrestricted shell.
    allowed_actions: tuple[str, ...] = ("analyze", "summarize")
    working_directory: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True, frozen=True)
class RuntimeResult:
    ok: bool
    summary: str
    runtime: str | None = None
    actions_taken: tuple[str, ...] = ()
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
