"""Shared capability checks before provider calls (#93)."""

from __future__ import annotations

from app.llm.capabilities import ProviderCapabilities
from app.llm.exceptions import UnsupportedCapabilityError
from app.llm.models import LLMRequest
from app.llm.structured import validate_schema


def assert_request_supported(
    request: LLMRequest,
    capabilities: ProviderCapabilities,
    *,
    provider_name: str,
) -> None:
    """Raise ``UnsupportedCapabilityError`` when the request exceeds capabilities."""

    if request.structured_output is not None:
        validate_schema(request.structured_output)
    if request.stream and not capabilities.streaming:
        raise UnsupportedCapabilityError(
            f"Provider {provider_name!r} does not support streaming"
        )
    if request.structured_output is not None and not capabilities.structured_output:
        raise UnsupportedCapabilityError(
            f"Provider {provider_name!r} does not support structured_output"
        )
    if request.tools and not capabilities.tool_calling:
        raise UnsupportedCapabilityError(
            f"Provider {provider_name!r} does not support tool_calling"
        )
    if not capabilities.text_generation:
        raise UnsupportedCapabilityError(
            f"Provider {provider_name!r} does not support text_generation"
        )
