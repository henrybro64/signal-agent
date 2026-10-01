"""Tests for optional LangChain adapters (#63)."""

from __future__ import annotations

import json

import pytest

from app.integrations.langchain.deps import langchain_available
from app.llm import FakeLLMProvider
from app.tools import ToolContext, build_tool_registry


def test_require_langchain_message_when_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.integrations.langchain import deps as deps_mod

    monkeypatch.setattr(deps_mod, "langchain_available", lambda: False)
    with pytest.raises(ImportError, match=r"pip install -e '\.\[langchain\]'"):
        deps_mod.require_langchain()


@pytest.mark.skipif(not langchain_available(), reason="langchain extra not installed")
def test_langchain_tools_adapt_registry_and_redact_errors() -> None:
    from app.integrations.langchain import to_langchain_tools

    registry = build_tool_registry(include_telegram=True)
    context = ToolContext(
        telegram_enabled=False,
        fetch_jobs=lambda **_: [
            {"title": "SDET", "source": "dummy", "item_type": "job"}
        ],
        secrets_to_redact=("sk-secret",),
    )
    tools = to_langchain_tools(registry, context=context)
    names = {tool.name for tool in tools}
    assert "search_jobs" in names
    assert "send_telegram_message" in names

    search = next(tool for tool in tools if tool.name == "search_jobs")
    payload = json.loads(search.invoke({"limit": 1}))
    assert payload["ok"] is True
    assert payload["data"]["count"] == 1

    telegram = next(
        tool for tool in tools if tool.name == "send_telegram_message"
    )
    disabled = json.loads(telegram.invoke({"text": "hi sk-secret"}))
    assert disabled["ok"] is False
    assert "sk-secret" not in json.dumps(disabled)


@pytest.mark.skipif(not langchain_available(), reason="langchain extra not installed")
def test_langchain_smoke_uses_fake_model() -> None:
    from app.integrations.langchain import run_langchain_smoke

    result = run_langchain_smoke()
    assert "search_jobs" in result["tool_names"]
    assert "ok" in result["model_reply"] or result["model_reply"]


@pytest.mark.skipif(not langchain_available(), reason="langchain extra not installed")
def test_gateway_chat_model_invoke() -> None:
    from app.integrations.langchain import build_langchain_chat_model

    model = build_langchain_chat_model(
        provider=FakeLLMProvider(
            responses=["hello from gateway"],
            validate_structured_json=False,
        ),
        provider_name="fake",
        model="fake-model",
    )
    message = model.invoke("ping")
    assert message.content == "hello from gateway"
