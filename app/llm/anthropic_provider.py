"""Anthropic Messages API provider adapter (#93).

Uses HTTP only — Anthropic SDK types never leak into business logic.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Final

import requests

from app.core.logging import get_logger, log_event

from .capabilities import ANTHROPIC_CAPABILITIES, ProviderCapabilities
from .capability_gate import assert_request_supported
from .exceptions import (
    LLMAuthError,
    LLMNetworkError,
    LLMProviderError,
    LLMRateLimitError,
    LLMResponseError,
    LLMTimeoutError,
)
from .models import LLMRequest, LLMResponse, LLMUsage
from .structured import validate_output

DEFAULT_ANTHROPIC_MESSAGES_URL: Final[str] = "https://api.anthropic.com/v1/messages"
_DEFAULT_TIMEOUT_SECONDS: Final[float] = 20.0
_DEFAULT_TEMPERATURE: Final[float] = 0.2
_ANTHROPIC_VERSION: Final[str] = "2023-06-01"
_LOGGER = get_logger("llm.anthropic")


class AnthropicProvider:
    """Call Anthropic's Messages API without exposing SDK types."""

    name = "anthropic"

    def __init__(
        self,
        *,
        api_key: str,
        default_model: str = "claude-sonnet-4-5",
        default_timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
        default_temperature: float = _DEFAULT_TEMPERATURE,
        api_url: str = DEFAULT_ANTHROPIC_MESSAGES_URL,
        session: requests.Session | None = None,
    ) -> None:
        if not api_key.strip():
            raise LLMAuthError("Anthropic API key must be a non-empty string")
        self._api_key = api_key
        self.default_model = default_model
        self.default_timeout_seconds = default_timeout_seconds
        self.default_temperature = default_temperature
        self.api_url = api_url
        self.session = session or requests.Session()

    def capabilities(self) -> ProviderCapabilities:
        return ANTHROPIC_CAPABILITIES

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
        system, messages = _resolve_messages(request)
        if request.structured_output is not None:
            schema = json.dumps(
                request.structured_output.schema,
                ensure_ascii=False,
            )
            system = (
                (system + "\n\n" if system else "")
                + "Respond with a single JSON object matching this schema "
                f"named {request.structured_output.name!r}: {schema}"
            )
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "max_tokens": 1024,
            "temperature": temperature,
        }
        if system:
            payload["system"] = system

        headers = {
            "x-api-key": self._api_key,
            "anthropic-version": _ANTHROPIC_VERSION,
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
            raise LLMTimeoutError("Anthropic request timed out") from None
        except requests.RequestException:
            _log_failure(model=model, status="network_error")
            raise LLMNetworkError("Anthropic network request failed") from None

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
                    f"Anthropic authentication failed with status "
                    f"{response.status_code}"
                )
            if response.status_code == 429:
                raise LLMRateLimitError("Anthropic rate limit exceeded")
            if response.status_code == 404:
                raise LLMProviderError(
                    f"Anthropic model unsupported or not found "
                    f"(status {response.status_code})"
                )
            raise LLMProviderError(
                f"Anthropic request failed with status {response.status_code}"
            )

        try:
            response_payload = response.json()
        except ValueError:
            raise LLMResponseError("Anthropic response was not valid JSON") from None
        if not isinstance(response_payload, dict):
            raise LLMResponseError("Anthropic response payload was not an object")

        text = _extract_text(response_payload)
        if response_payload.get("stop_reason") == "max_tokens":
            raise LLMResponseError("Anthropic response was truncated")
        if request.structured_output is not None:
            validate_output(text, request.structured_output)

        log_event(
            _LOGGER,
            event="llm_generate_completed",
            message="Anthropic generation completed",
            provider=self.name,
            model=model,
            duration_ms=duration_ms,
            status="ok",
            output_chars=len(text),
            structured=request.structured_output is not None,
        )
        return LLMResponse(
            text=text,
            model=str(response_payload.get("model") or model),
            provider=self.name,
            usage=_extract_usage(response_payload),
            raw=response_payload,
        )


def _resolve_messages(request: LLMRequest) -> tuple[str, list[dict[str, str]]]:
    if request.messages:
        system_parts: list[str] = []
        messages: list[dict[str, str]] = []
        for message in request.messages:
            role = message.role.strip().lower()
            content = message.content.strip()
            if not content:
                continue
            if role == "system":
                system_parts.append(content)
                continue
            if role == "developer":
                system_parts.append(content)
                continue
            if role not in {"user", "assistant"}:
                raise LLMProviderError(
                    f"Unsupported chat message role {message.role!r} for Anthropic"
                )
            messages.append({"role": role, "content": content})
        if not any(item["role"] == "user" for item in messages):
            raise LLMProviderError("LLM request messages must include user content")
        return "\n\n".join(system_parts), messages

    instructions = (request.instructions or "").strip()
    user_input = (request.input or "").strip()
    if not user_input:
        raise LLMProviderError("LLM request input must be a non-empty string")
    return instructions, [{"role": "user", "content": user_input}]


def _extract_text(payload: dict[str, object]) -> str:
    content = payload.get("content")
    if not isinstance(content, list):
        raise LLMResponseError("Anthropic response did not include content")
    texts: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "text" and isinstance(block.get("text"), str):
            texts.append(block["text"])
    text = "".join(texts).strip()
    if not text:
        raise LLMResponseError("Anthropic response did not include text")
    return text


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
    input_tokens = usage.get("input_tokens")
    output_tokens = usage.get("output_tokens")
    if input_tokens is None and output_tokens is None:
        return None
    total = None
    if input_tokens is not None and output_tokens is not None:
        total = int(input_tokens) + int(output_tokens)
    return LLMUsage(
        input_tokens=int(input_tokens) if input_tokens is not None else None,
        output_tokens=int(output_tokens) if output_tokens is not None else None,
        total_tokens=total,
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
        message="Anthropic generation failed",
        level=logging.WARNING,
        provider="anthropic",
        model=model,
        status=status,
        duration_ms=duration_ms,
        http_status=http_status,
    )
