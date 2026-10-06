"""Offline regressions for vendor-neutral credentials and request contracts."""

from dataclasses import replace
from unittest.mock import Mock

import pytest
import requests

from app.core.config import LlmConfig, LlmFallbackConfig, load_config
from app.core.secrets import (
    EnvSettings,
    llm_api_keys_from_env,
    resolve_provider_credentials,
)
from app.llm import (
    FakeLLMProvider,
    LLMRequest,
    StructuredOutputSpec,
    default_model_for_provider,
)
from app.llm.anthropic_provider import AnthropicProvider
from app.llm.exceptions import (
    LLMAuthError,
    LLMNetworkError,
    LLMProviderError,
    LLMRateLimitError,
    LLMResponseError,
    LLMTimeoutError,
    UnsupportedCapabilityError,
)
from app.llm.factory import create_llm_provider
from app.llm.models import LLMMessage, ToolDefinition
from app.llm.models_cli import build_routed_provider, check_model_health
from app.llm.router import ModelRef, RoutedLLMProvider


@pytest.fixture
def session():
    result = Mock()
    result.post.return_value.status_code = 200
    result.post.return_value.json.return_value = {
        "content": [{"type": "text", "text": '{"answer":"ok"}'}],
        "usage": {"input_tokens": 2, "output_tokens": 3},
    }
    return result


def test_anthropic_uses_messages_and_normalizes_usage(session):
    provider = create_llm_provider(
        provider="anthropic",
        api_keys={"anthropic": "a"},
        model="configured",
        session=session,
    )
    result = provider.generate(
        LLMRequest(messages=(LLMMessage("system", "sys"), LLMMessage("user", "hello")))
    )
    call = session.post.call_args.kwargs
    assert call["headers"]["x-api-key"] == "a"
    assert call["json"]["system"] == "sys"
    assert call["json"]["model"] == "configured"
    assert result.usage.total_tokens == 5


@pytest.mark.parametrize(
    "status,error",
    [
        (401, LLMAuthError),
        (403, LLMAuthError),
        (429, LLMRateLimitError),
        (404, LLMProviderError),
        (500, LLMProviderError),
    ],
)
@pytest.mark.parametrize("name", ["anthropic", "openai"])
def test_http_errors_are_normalized(session, status, error, name):
    session.post.return_value.status_code = status
    with pytest.raises(error):
        create_llm_provider(provider=name, api_key="test", session=session).generate(
            LLMRequest(input="hello")
        )


@pytest.mark.parametrize(
    "failure,error",
    [(requests.Timeout, LLMTimeoutError), (requests.ConnectionError, LLMNetworkError)],
)
@pytest.mark.parametrize("name", ["anthropic", "openai"])
def test_transport_errors_are_normalized(session, failure, error, name):
    session.post.side_effect = failure("synthetic secret")
    with pytest.raises(error, match="timed out|network"):
        create_llm_provider(provider=name, api_key="test", session=session).generate(
            LLMRequest(input="hello")
        )


@pytest.mark.parametrize(
    "change", [{"stream": True}, {"tools": (ToolDefinition("lookup"),)}]
)
def test_router_preserves_capability_requests(change):
    fake = FakeLLMProvider()
    router = RoutedLLMProvider(
        primary=ModelRef("fake", "test"), provider_factory=lambda **kwargs: fake
    )
    with pytest.raises(UnsupportedCapabilityError):
        router.generate(LLMRequest(input="hello", **change))
    assert not fake.calls


def test_router_preserves_metadata():
    fake = FakeLLMProvider()
    router = RoutedLLMProvider(
        primary=ModelRef("fake", "test"), provider_factory=lambda **kwargs: fake
    )
    router.generate(LLMRequest(input="hello", metadata={"opaque": "value"}))
    assert fake.calls[0].metadata == {"opaque": "value"}


def test_cross_vendor_fallback_uses_separate_keys(session):
    seen = []

    def factory(**kwargs):
        seen.append(kwargs)
        if kwargs["provider"] == "openai":
            return FakeLLMProvider(responses=[LLMAuthError("denied")])
        return create_llm_provider(**kwargs, session=session)

    router = build_routed_provider(
        LlmConfig(
            provider="openai",
            model="primary",
            fallbacks=[LlmFallbackConfig(provider="anthropic", model="backup")],
        ),
        api_keys={"openai": "o", "anthropic": "a"},
        provider_factory=factory,
    )
    assert router.generate(LLMRequest(input="test")).provider == "anthropic"
    assert session.post.call_args.kwargs["headers"]["x-api-key"] == "a"
    assert str(router.last_served) == "anthropic/backup"


def test_legacy_key_never_crosses_vendor_boundary():
    router = build_routed_provider(
        LlmConfig(
            provider="openai",
            model="primary",
            fallbacks=[LlmFallbackConfig(provider="anthropic", model="backup")],
        ),
        api_key="o",
    )
    with pytest.raises(LLMProviderError, match="API key"):
        router._provider_for(ModelRef("anthropic", "backup"))


def test_all_providers_failed():
    router = RoutedLLMProvider(
        primary=ModelRef("openai", "x"),
        fallbacks=[ModelRef("anthropic", "y")],
        max_retries=0,
        provider_factory=lambda **kwargs: FakeLLMProvider(
            responses=[LLMTimeoutError("timeout")]
        ),
    )
    with pytest.raises(LLMProviderError, match="All providers/fallbacks failed"):
        router.generate(LLMRequest(input="test"))


@pytest.mark.parametrize("text", ['{"wrong":true}', '{"answer":42}', "not json", "[]"])
def test_anthropic_rejects_malformed_structured_output(session, text):
    session.post.return_value.json.return_value = {
        "content": [{"type": "text", "text": text}]
    }
    provider = AnthropicProvider(api_key="test", session=session)
    spec = StructuredOutputSpec(
        name="answer",
        schema={
            "type": "object",
            "properties": {"answer": {"type": "string"}},
            "required": ["answer"],
        },
    )
    with pytest.raises(LLMResponseError):
        provider.generate(LLMRequest(input="test", structured_output=spec))


def test_schema_cannot_fetch_remote_references(session):
    provider = AnthropicProvider(api_key="test", session=session)
    with pytest.raises(LLMProviderError, match="local references"):
        provider.generate(
            LLMRequest(
                input="test",
                structured_output=StructuredOutputSpec(
                    name="bad", schema={"$ref": "https://fixture.invalid/schema"}
                ),
            )
        )
    session.post.assert_not_called()


def test_custom_credentials_resolve_without_cross_vendor_fallback(monkeypatch):
    config = load_config("config.yaml")
    config.providers.anthropic.api_key_env = "TEAM_CLAUDE_KEY"
    monkeypatch.setenv("TEAM_CLAUDE_KEY", "alias-key")
    env = resolve_provider_credentials(
        EnvSettings(openai_api_key="o", anthropic_api_key="old"), config.providers
    )
    assert llm_api_keys_from_env(env) == {
        "openai": "o",
        "anthropic": "alias-key",
        "fake": None,
    }
    monkeypatch.delenv("TEAM_CLAUDE_KEY")
    assert resolve_provider_credentials(env, config.providers).anthropic_api_key is None


def test_model_health_uses_anthropic_key(monkeypatch, tmp_path, session):
    config = load_config("config.yaml")
    config.llm = replace(config.llm, provider="anthropic", model="configured")
    monkeypatch.setattr("app.llm.models_cli.load_config", lambda path: config)
    monkeypatch.setattr(
        "app.llm.models_cli.load_env_settings",
        lambda path: EnvSettings(openai_api_key="wrong", anthropic_api_key="correct"),
    )
    monkeypatch.setattr("app.llm.anthropic_provider.requests.Session", lambda: session)
    assert check_model_health(root=tmp_path)["ok"]
    assert session.post.call_args.kwargs["headers"]["x-api-key"] == "correct"


def test_router_retains_unsupported_error_when_all_adapters_reject():
    router = RoutedLLMProvider(
        primary=ModelRef("fake", "a"), fallbacks=[ModelRef("fake", "b")]
    )
    with pytest.raises(UnsupportedCapabilityError):
        router.generate(LLMRequest(input="test", stream=True))


def test_router_total_timeout_prevents_late_fallback(monkeypatch):
    clock = iter([0.0, 0.0, 11.0])
    monkeypatch.setattr("app.llm.router.monotonic", lambda: next(clock))
    calls = []

    def factory(**kwargs):
        calls.append(kwargs["provider"])
        return FakeLLMProvider(responses=[LLMAuthError("denied")])

    router = RoutedLLMProvider(
        primary=ModelRef("openai", "a"),
        fallbacks=[ModelRef("anthropic", "b")],
        provider_factory=factory,
    )
    with pytest.raises(LLMTimeoutError, match="budget"):
        router.generate(LLMRequest(input="test", timeout_seconds=10))
    assert calls == ["openai"]


def test_anthropic_omits_sampling_parameters(monkeypatch, session):
    """Claude Opus 4.5+ reject non-default sampling, so never send any."""

    monkeypatch.setattr("app.llm.anthropic_provider.requests.Session", lambda: session)
    router = build_routed_provider(
        LlmConfig(provider="anthropic", model="claude-opus-4-5"),
        api_keys={"anthropic": "a"},
    )

    router.generate(LLMRequest(input="hello", temperature=0.7))

    body = session.post.call_args.kwargs["json"]
    assert "temperature" not in body
    assert "top_p" not in body
    assert "top_k" not in body


def test_provider_switch_without_model_uses_vendor_default(session):
    provider = create_llm_provider(
        provider="anthropic",
        api_keys={"anthropic": "a"},
        session=session,
    )

    provider.generate(LLMRequest(input="hello"))

    assert session.post.call_args.kwargs["json"]["model"] == "claude-sonnet-4-5"
    assert default_model_for_provider("openai") == "gpt-4o-mini"
    with pytest.raises(LLMProviderError, match="Unknown LLM provider"):
        default_model_for_provider("ollama")
