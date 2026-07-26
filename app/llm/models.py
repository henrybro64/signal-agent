"""Framework-neutral LLM request/response models."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True, frozen=True)
class LLMMessage:
    """One chat-style message."""

    role: str
    content: str


@dataclass(slots=True, frozen=True)
class StructuredOutputSpec:
    """Expect structured JSON matching a JSON Schema."""

    name: str
    schema: dict[str, Any]
    strict: bool = True


@dataclass(slots=True, frozen=True)
class LLMRequest:
    """Provider-agnostic generation request.

    Callers may use either chat ``messages`` or Responses-style
    ``instructions`` + ``input``. Providers normalize as needed.
    """

    messages: tuple[LLMMessage, ...] = ()
    instructions: str | None = None
    input: str | None = None
    model: str | None = None
    temperature: float | None = None
    timeout_seconds: float | None = None
    structured_output: StructuredOutputSpec | None = None
    store: bool = False


@dataclass(slots=True, frozen=True)
class LLMResponse:
    """Normalized provider response."""

    text: str
    model: str | None = None
    provider: str | None = None
    raw: dict[str, Any] | None = field(default=None, repr=False)
