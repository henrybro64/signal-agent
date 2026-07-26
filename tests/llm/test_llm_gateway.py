"""Unit tests for the framework-neutral LLM gateway (#61)."""

from __future__ import annotations

import json

import pytest
import requests

from app.llm import (
    FakeLLMProvider,
    LLMMessage,
    LLMProviderError,
    LLMRequest,
    LLMResponseError,
    LLMTimeoutError,
    OpenAIProvider,
    StructuredOutputSpec,
    create_llm_provider,
    supported_llm_providers,
)

_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["ok"],
    "properties": {"ok": {"type": "boolean"}},
}
_STRUCTURED = StructuredOutputSpec(name="probe", schema=_SCHEMA, strict=True)


class _FakeHttpResponse:
    def __init__(
        self,
        *,
        status_code: int = 200,
        json_payload: dict[str, object] | None = None,
    ) -> None:
        self.status_code = status_code
        self._json_payload = json_payload

    def json(self) -> dict[str, object]:
        if self._json_payload is None:
            raise ValueError("invalid json")
        return self._json_payload


class _FakeSession:
    def __init__(self, responses: list[_FakeHttpResponse | Exception]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, object]] = []

    def post(
        self,
        url: str,
        *,
        headers: dict[str, str],
        json: dict[str, object],
        timeout: float,
    ) -> _FakeHttpResponse:
        self.calls.append(
            {
                "url": url,
                "headers": headers,
                "json": json,
                "timeout": timeout,
            }
        )
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def test_fake_provider_success_and_records_calls() -> None:
    provider = FakeLLMProvider(
        responses=['{"ok": true, "score": 1}'],
        default_model="fake-model",
    )
    request = LLMRequest(
        instructions="sys",
        input="user",
        model="override-model",
        structured_output=_STRUCTURED,
    )

    response = provider.generate(request)

    assert json.loads(response.text)["ok"] is True
    assert response.model == "override-model"
    assert response.provider == "fake"
    assert len(provider.calls) == 1
    assert provider.calls[0].input == "user"


def test_fake_provider_rejects_malformed_structured_output() -> None:
    provider = FakeLLMProvider(responses=["not-json"])
    request = LLMRequest(
        instructions="sys",
        input="user",
        structured_output=_STRUCTURED,
    )

    with pytest.raises(LLMResponseError, match="not valid JSON"):
        provider.generate(request)


def test_fake_provider_timeout_and_provider_error() -> None:
    provider = FakeLLMProvider(
        responses=[
            LLMTimeoutError("timed out"),
            LLMProviderError("upstream down"),
        ]
    )
    request = LLMRequest(instructions="sys", input="user")

    with pytest.raises(LLMTimeoutError, match="timed out"):
        provider.generate(request)
    with pytest.raises(LLMProviderError, match="upstream down"):
        provider.generate(request)


def test_openai_provider_success_maps_responses_payload() -> None:
    session = _FakeSession(
        [
            _FakeHttpResponse(
                json_payload={
                    "output_text": '{"ok": true}',
                }
            )
        ]
    )
    provider = OpenAIProvider(
        api_key="sk-test",
        default_model="gpt-4o-mini",
        default_timeout_seconds=15,
        default_temperature=0.1,
        session=session,
    )
    request = LLMRequest(
        instructions="Be brief.",
        input="Say ok",
        structured_output=_STRUCTURED,
        temperature=0.3,
        timeout_seconds=12,
    )

    response = provider.generate(request)

    assert response.text == '{"ok": true}'
    assert response.provider == "openai"
    assert session.calls[0]["headers"]["Authorization"] == "Bearer sk-test"
    assert session.calls[0]["timeout"] == 12
    body = session.calls[0]["json"]
    assert body["model"] == "gpt-4o-mini"
    assert body["temperature"] == 0.3
    assert body["text"]["format"]["name"] == "probe"
    assert "sk-test" not in str(response)


def test_openai_provider_accepts_chat_messages() -> None:
    session = _FakeSession(
        [_FakeHttpResponse(json_payload={"output_text": "hello"})]
    )
    provider = OpenAIProvider(api_key="sk-test", session=session)
    request = LLMRequest(
        messages=(
            LLMMessage(role="system", content="You are helpful."),
            LLMMessage(role="user", content="Hi"),
        )
    )

    response = provider.generate(request)

    assert response.text == "hello"
    body = session.calls[0]["json"]
    assert body["instructions"] == "You are helpful."
    assert body["input"] == "Hi"


def test_openai_provider_timeout_and_http_error() -> None:
    timeout_session = _FakeSession([requests.Timeout("slow")])
    provider = OpenAIProvider(api_key="sk-test", session=timeout_session)
    with pytest.raises(LLMTimeoutError, match="timed out"):
        provider.generate(LLMRequest(input="hi", instructions="sys"))

    error_session = _FakeSession([_FakeHttpResponse(status_code=503)])
    provider = OpenAIProvider(api_key="sk-test", session=error_session)
    with pytest.raises(LLMProviderError, match="status 503"):
        provider.generate(LLMRequest(input="hi", instructions="sys"))


def test_openai_provider_rejects_empty_api_key() -> None:
    with pytest.raises(LLMProviderError, match="non-empty"):
        OpenAIProvider(api_key="  ")


def test_create_llm_provider_openai_and_fake() -> None:
    fake = create_llm_provider(provider="fake", model="m1")
    assert isinstance(fake, FakeLLMProvider)
    assert fake.name == "fake"

    openai = create_llm_provider(
        provider="openai",
        api_key="sk-test",
        model="gpt-test",
        session=_FakeSession([]),
    )
    assert isinstance(openai, OpenAIProvider)
    assert openai.default_model == "gpt-test"


def test_create_llm_provider_rejects_unknown_and_ollama() -> None:
    with pytest.raises(LLMProviderError, match="Unknown LLM provider"):
        create_llm_provider(provider="anthropic")
    with pytest.raises(LLMProviderError, match="not implemented"):
        create_llm_provider(provider="ollama")
    assert supported_llm_providers() == frozenset({"openai", "fake"})


def test_openai_logs_do_not_include_api_key_or_prompt() -> None:
    import logging

    session = _FakeSession(
        [_FakeHttpResponse(json_payload={"output_text": "ok"})]
    )
    provider = OpenAIProvider(api_key="sk-secret-key", session=session)
    logger = logging.getLogger("signal_agent.llm.openai")
    records: list[logging.LogRecord] = []

    class _Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    handler = _Capture()
    logger.addHandler(handler)
    try:
        provider.generate(
            LLMRequest(
                instructions="SECRET_SYSTEM_PROMPT",
                input="SECRET_USER_PROMPT",
            )
        )
    finally:
        logger.removeHandler(handler)

    joined = " ".join(
        f"{record.getMessage()} {record.__dict__}" for record in records
    )
    assert records, "expected structured llm log events"
    assert "sk-secret-key" not in joined
    assert "SECRET_SYSTEM_PROMPT" not in joined
    assert "SECRET_USER_PROMPT" not in joined
