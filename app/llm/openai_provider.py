"""OpenAI Responses API provider adapter."""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Final

import requests

from app.core.logging import get_logger, log_event

from .capabilities import OPENAI_CAPABILITIES, ProviderCapabilities
from .capability_gate import assert_request_supported
from .exceptions import (
    LLMAuthError,
    LLMNetworkError,
    LLMProviderError,
    LLMRateLimitError,
    LLMResponseError,
    LLMTimeoutError,
)
from .models import LLMRequest, LLMResponse, LLMUsage, StructuredOutputSpec
from .structured import validate_output

DEFAULT_OPENAI_RESPONSES_URL: Final[str] = "https://api.openai.com/v1/responses"
_DEFAULT_TIMEOUT_SECONDS: Final[float] = 20.0
_DEFAULT_TEMPERATURE: Final[float] = 0.2
_SUPPORTED_INPUT_ROLES: Final[frozenset[str]] = frozenset(
    {"user", "assistant", "developer"}
)
_LOGGER = get_logger("llm.openai")


class OpenAIProvider:
    """Call OpenAI's Responses API without exposing SDK types to callers."""

    name = "openai"

    def __init__(
        self,
        *,
        api_key: str,
        default_model: str = "gpt-4o-mini",
        default_timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
        default_temperature: float = _DEFAULT_TEMPERATURE,
        api_url: str = DEFAULT_OPENAI_RESPONSES_URL,
        session: requests.Session | None = None,
    ) -> None:
        if not api_key.strip():
            raise LLMAuthError("OpenAI API key must be a non-empty string")
        self._api_key = api_key
        self.default_model = default_model
        self.default_timeout_seconds = default_timeout_seconds
        self.default_temperature = default_temperature
        self.api_url = api_url
        self.session = session or requests.Session()

    def capabilities(self) -> ProviderCapabilities:
        return OPENAI_CAPABILITIES

    def generate(self, request: LLMRequest) -> LLMResponse:
        assert_request_supported(
            request,
            self.capabilities(),
            provider_name=self.name,
        )
        model = request.model or self.default_model
        temperature = (
            self.default_temperature
            if request.temperature is None
            else request.temperature
        )
        timeout = (
            self.default_timeout_seconds
            if request.timeout_seconds is None
            else request.timeout_seconds
        )
        instructions, user_input = _resolve_prompt_parts(request)
        payload: dict[str, Any] = {
            "model": model,
            "instructions": instructions,
            "input": user_input,
            "store": request.store,
            "temperature": temperature,
        }
        if request.structured_output is not None:
            payload["text"] = {
                "verbosity": "low",
                "format": _structured_format(request.structured_output),
            }

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

        started = time.perf_counter()
        try:
            response = self.session.post(
                self.api_url,
                headers=headers,
                json=payload,
                timeout=timeout,
            )
        except requests.Timeout:
            _log_failure(model=model, status="timeout")
            raise LLMTimeoutError("OpenAI request timed out") from None
        except requests.RequestException:
            _log_failure(model=model, status="network_error")
            raise LLMNetworkError("OpenAI network request failed") from None

        duration_ms = int((time.perf_counter() - started) * 1000)
        if response.status_code >= 400:
            _log_failure(
                model=model,
                status="http_error",
                duration_ms=duration_ms,
                http_status=response.status_code,
            )
            if response.status_code in {401, 403}:
                raise LLMAuthError(
                    f"OpenAI authentication failed with status {response.status_code}"
                )
            if response.status_code == 429:
                raise LLMRateLimitError("OpenAI rate limit exceeded")
            if response.status_code == 404:
                raise LLMProviderError(
                    f"OpenAI model unsupported or not found "
                    f"(status {response.status_code})"
                )
            raise LLMProviderError(
                f"OpenAI request failed with status {response.status_code}"
            )

        try:
            response_payload = response.json()
        except ValueError:
            _log_failure(
                model=model,
                status="invalid_json",
                duration_ms=duration_ms,
            )
            raise LLMResponseError("OpenAI response was not valid JSON") from None

        if not isinstance(response_payload, dict):
            raise LLMResponseError("OpenAI response payload was not an object")

        try:
            text = _extract_output_text(response_payload)
        except LLMResponseError:
            _log_failure(
                model=model,
                status="missing_output",
                duration_ms=duration_ms,
            )
            raise

        if request.structured_output is not None:
            validate_output(text, request.structured_output)

        log_event(
            _LOGGER,
            event="llm_generate_completed",
            message="OpenAI generation completed",
            provider=self.name,
            model=model,
            duration_ms=duration_ms,
            status="ok",
            output_chars=len(text),
            structured=request.structured_output is not None,
        )
        return LLMResponse(
            text=text,
            model=model,
            provider=self.name,
            usage=_extract_usage(response_payload),
            raw=response_payload,
        )


def _structured_format(spec: StructuredOutputSpec) -> dict[str, object]:
    return {
        "type": "json_schema",
        "name": spec.name,
        "strict": spec.strict,
        "schema": spec.schema,
    }


def _resolve_prompt_parts(
    request: LLMRequest,
) -> tuple[str, str | list[dict[str, str]]]:
    """Map a gateway request onto Responses ``instructions`` + ``input``.

    Chat ``system`` messages become ``instructions``. ``user`` / ``assistant`` /
    ``developer`` keep their roles in a Responses input list (or a plain string
    when there is a single user turn). Unsupported roles are rejected.
    """

    if request.messages:
        instructions_parts: list[str] = []
        input_items: list[dict[str, str]] = []
        for message in request.messages:
            role = message.role.strip().lower()
            content = message.content.strip()
            if not content:
                continue
            if role == "system":
                instructions_parts.append(content)
                continue
            if role not in _SUPPORTED_INPUT_ROLES:
                supported = ", ".join(["system", *sorted(_SUPPORTED_INPUT_ROLES)])
                raise LLMProviderError(
                    f"Unsupported chat message role {message.role!r}; "
                    f"supported: {supported}"
                )
            input_items.append({"role": role, "content": content})
        if not any(item["role"] == "user" for item in input_items):
            raise LLMProviderError("LLM request messages must include user content")
        instructions = "\n\n".join(instructions_parts)
        if len(input_items) == 1 and input_items[0]["role"] == "user":
            return instructions, input_items[0]["content"]
        return instructions, input_items

    instructions = (request.instructions or "").strip()
    user_input = (request.input or "").strip()
    if not user_input:
        raise LLMProviderError("LLM request input must be a non-empty string")
    return instructions, user_input


def _extract_output_text(payload: dict[str, object]) -> str:
    output_text = payload.get("output_text")
    if isinstance(output_text, str) and output_text.strip():
        return output_text

    output = payload.get("output")
    if not isinstance(output, list):
        raise LLMResponseError("OpenAI response did not include output text")

    for item in output:
        if not isinstance(item, dict):
            continue
        content = item.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            text = block.get("text")
            if isinstance(text, str) and text.strip():
                return text
            parsed = block.get("parsed")
            if isinstance(parsed, dict):
                return json.dumps(parsed, ensure_ascii=False)

    raise LLMResponseError("OpenAI response did not include parsable text")


def _extract_usage(payload: dict[str, object]) -> LLMUsage | None:
    usage = payload.get("usage")
    if not isinstance(usage, dict):
        return None
    if any(
        value is not None and (type(value) is not int or value < 0)
        for value in (
            usage.get(key)
            for key in (
                "input_tokens",
                "output_tokens",
                "total_tokens",
                "prompt_tokens",
                "completion_tokens",
            )
        )
    ):
        raise LLMResponseError("Provider returned invalid usage metadata")
    input_tokens = usage.get("input_tokens", usage.get("prompt_tokens"))
    output_tokens = usage.get("output_tokens", usage.get("completion_tokens"))
    total_tokens = usage.get("total_tokens")
    if input_tokens is None and output_tokens is None and total_tokens is None:
        return None
    return LLMUsage(
        input_tokens=int(input_tokens) if input_tokens is not None else None,
        output_tokens=int(output_tokens) if output_tokens is not None else None,
        total_tokens=int(total_tokens) if total_tokens is not None else None,
    )


def _log_failure(
    *,
    model: str,
    status: str,
    duration_ms: int | None = None,
    http_status: int | None = None,
) -> None:
    log_event(
        _LOGGER,
        event="llm_generate_failed",
        message="OpenAI generation failed",
        level=logging.WARNING,
        provider="openai",
        model=model,
        status=status,
        duration_ms=duration_ms,
        http_status=http_status,
    )
