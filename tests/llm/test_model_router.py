"""Tests for model routing, health classification, and CLI (#86)."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.cli import main
from app.core.config import LlmConfig, LlmFallbackConfig, load_config
from app.llm import (
    LLMAuthError,
    LLMNetworkError,
    LLMProviderError,
    LLMRateLimitError,
    LLMRequest,
    LLMResponse,
    LLMTimeoutError,
    ModelRef,
    RoutedLLMProvider,
    classify_provider_failure,
)
from app.llm.models_cli import (
    add_fallback_model,
    build_routed_provider,
    check_model_health,
    format_models_status,
    set_default_model,
)
from app.setup import SetupOptions, run_setup


class _FailThenOk:
    name = "fake"

    def __init__(self, *, fail_with: Exception | None = None) -> None:
        self.fail_with = fail_with
        self.calls = 0

    def generate(self, request: LLMRequest) -> LLMResponse:
        del request
        self.calls += 1
        if self.fail_with is not None:
            raise self.fail_with
        return LLMResponse(text="ok", model="ok-model", raw={})


def test_routed_provider_fallback_success_and_all_failed() -> None:
    primary = _FailThenOk(fail_with=LLMTimeoutError("timeout"))
    backup = _FailThenOk()
    providers = {
        ("fake", "primary"): primary,
        ("fake", "backup"): backup,
    }

    def factory(**kwargs):
        return providers[(kwargs["provider"], kwargs["model"])]

    router = RoutedLLMProvider(
        primary=ModelRef("fake", "primary"),
        fallbacks=[ModelRef("fake", "backup")],
        max_retries=0,
        provider_factory=factory,
    )
    response = router.generate(LLMRequest(instructions="x", input="y"))
    assert response.text == "ok"
    assert str(router.last_served) == "fake/backup"
    assert primary.calls == 1
    assert backup.calls == 1

    broken = RoutedLLMProvider(
        primary=ModelRef("fake", "primary"),
        fallbacks=[ModelRef("fake", "backup")],
        max_retries=0,
        provider_factory=lambda **_: _FailThenOk(fail_with=LLMRateLimitError("429")),
    )
    with pytest.raises(LLMProviderError, match="All providers"):
        broken.generate(LLMRequest(instructions="x", input="y"))


def test_classify_provider_failure_classes() -> None:
    assert classify_provider_failure(LLMAuthError("401")) == "auth"
    assert classify_provider_failure(LLMRateLimitError("429")) == "rate_limit"
    assert classify_provider_failure(LLMTimeoutError("t")) == "timeout"
    assert classify_provider_failure(LLMNetworkError("down")) == "network"
    assert (
        classify_provider_failure(LLMProviderError("model unsupported"))
        == "unsupported_model"
    )
    assert classify_provider_failure(LLMProviderError("connection reset")) == "network"
    wrapped = LLMProviderError("All providers failed")
    wrapped.__cause__ = LLMAuthError("401")
    assert classify_provider_failure(wrapped) == "auth"


def test_models_cli_status_set_fallback_test(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    run_setup(
        root=tmp_path,
        options=SetupOptions(provider="fake", non_interactive=True, force=True),
    )
    cfg = tmp_path / "config.yaml"
    assert "default:" in format_models_status(root=tmp_path, config_path=cfg)

    selected = set_default_model("fake/custom-model", root=tmp_path, config_path=cfg)
    assert selected == "fake/custom-model"
    config = load_config(cfg)
    assert config.llm.provider == "fake"
    assert config.llm.model == "custom-model"

    fallbacks = add_fallback_model("fake/backup", root=tmp_path, config_path=cfg)
    assert "fake/backup" in fallbacks

    health = check_model_health(root=tmp_path, config_path=cfg)
    assert health["ok"] is True
    assert "sk-" not in str(health)

    code = main(["--root", str(tmp_path), "models", "status"])
    assert code == 0
    out = capsys.readouterr().out
    assert "fake/custom-model" in out
    assert "fake/backup" in out

    code = main(["--root", str(tmp_path), "models", "test"])
    assert code == 0
    assert "models test: ok" in capsys.readouterr().out


def test_build_routed_provider_from_config() -> None:
    llm = LlmConfig(
        provider="fake",
        model="a",
        fallbacks=[LlmFallbackConfig(provider="fake", model="b")],
        max_retries=0,
    )
    router = build_routed_provider(llm, api_key=None)
    response = router.generate(
        LLMRequest(instructions="Reply with exactly: ok", input="ping")
    )
    assert response.text
    assert router.last_served is not None


def test_auth_failure_health_does_not_leak_credentials(tmp_path: Path) -> None:
    run_setup(
        root=tmp_path,
        options=SetupOptions(provider="fake", non_interactive=True, force=True),
    )

    class AuthBoom:
        name = "boom"

        def generate(self, request: LLMRequest) -> LLMResponse:
            del request
            raise LLMAuthError("invalid key sk-should-not-appear")

    router = RoutedLLMProvider(
        primary=ModelRef("fake", "primary"),
        max_retries=0,
        provider_factory=lambda **_: AuthBoom(),
    )
    result = check_model_health(
        root=tmp_path,
        config_path=tmp_path / "config.yaml",
        provider=router,
    )
    assert result["ok"] is False
    assert result["failure_class"] == "auth"
    assert "sk-should-not-appear" not in result["error"]


def test_network_failures_are_retryable_and_providers_are_cached() -> None:
    attempts = {"n": 0}

    class FlakyNetwork:
        name = "fake"

        def generate(self, request: LLMRequest) -> LLMResponse:
            del request
            attempts["n"] += 1
            if attempts["n"] < 2:
                raise LLMNetworkError("connection reset")
            return LLMResponse(text="ok", model="m", raw={})

    factory_calls = {"n": 0}

    def factory(**kwargs):
        del kwargs
        factory_calls["n"] += 1
        return FlakyNetwork()

    router = RoutedLLMProvider(
        primary=ModelRef("fake", "primary"),
        max_retries=1,
        provider_factory=factory,
    )
    response = router.generate(LLMRequest(instructions="x", input="y"))
    assert response.text == "ok"
    assert attempts["n"] == 2
    assert factory_calls["n"] == 1

    auth_router = RoutedLLMProvider(
        primary=ModelRef("fake", "primary"),
        max_retries=2,
        provider_factory=lambda **_: _FailThenOk(fail_with=LLMAuthError("401")),
    )
    with pytest.raises(LLMAuthError):
        auth_router.generate(LLMRequest(instructions="x", input="y"))
    # Auth is terminal: one attempt only despite max_retries=2.
    assert auth_router._providers  # cached provider exists


def test_models_set_rejects_unknown_provider(tmp_path: Path) -> None:
    run_setup(
        root=tmp_path,
        options=SetupOptions(provider="fake", non_interactive=True, force=True),
    )
    with pytest.raises(ValueError, match="Unknown LLM provider"):
        set_default_model(
            "nope/model",
            root=tmp_path,
            config_path=tmp_path / "config.yaml",
        )
