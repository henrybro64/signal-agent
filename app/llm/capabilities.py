"""Explicit LLM provider capability metadata (#93)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True, frozen=True)
class ProviderCapabilities:
    """Features a provider/model adapter supports."""

    text_generation: bool = True
    structured_output: bool = False
    tool_calling: bool = False
    streaming: bool = False
    vision: bool = False


OPENAI_CAPABILITIES = ProviderCapabilities(
    text_generation=True,
    structured_output=True,
    tool_calling=False,
    streaming=False,
    vision=False,
)

ANTHROPIC_CAPABILITIES = ProviderCapabilities(
    text_generation=True,
    structured_output=True,
    tool_calling=False,
    streaming=False,
    vision=False,
)

FAKE_CAPABILITIES = ProviderCapabilities(
    text_generation=True,
    structured_output=True,
    tool_calling=False,
    streaming=False,
    vision=False,
)
