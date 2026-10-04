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
class ToolDefinition:
    """Optional tool/function definition for providers that support tools."""

    name: str
    description: str = ""
    parameters: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True, frozen=True)
class LLMUsage:
    """Token/usage metadata when a provider returns it."""

    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None


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
    tools: tuple[ToolDefinition, ...] = ()
    stream: bool = False
    store: bool = False
    # Opaque provider-specific knobs; adapters may ignore unknown keys.
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True, frozen=True)
class LLMResponse:
    """Normalized provider response."""

    text: str
    model: str | None = None
    provider: str | None = None
    usage: LLMUsage | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] | None = field(default=None, repr=False)
