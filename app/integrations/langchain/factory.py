"""Factories for optional LangChain adapters."""

from __future__ import annotations

from typing import Any

from app.llm import LLMProvider
from app.tools import ToolContext, ToolRegistry, build_tool_registry

from .deps import langchain_available, langgraph_available, require_langchain
from .models import GatewayChatModel, build_langchain_chat_model
from .tools import to_langchain_tools


def build_langchain_bundle(
    *,
    registry: ToolRegistry | None = None,
    context: ToolContext | None = None,
    provider: LLMProvider | None = None,
    model: str = "fake-model",
    provider_name: str = "fake",
) -> dict[str, Any]:
    """Build tools + chat model adapters for smoke tests and pilots."""

    require_langchain()
    tool_registry = registry or build_tool_registry()
    tool_context = context or ToolContext()
    chat_model = build_langchain_chat_model(
        provider=provider,
        model=model,
        provider_name=provider_name,
    )
    tools = to_langchain_tools(tool_registry, context=tool_context)
    return {
        "registry": tool_registry,
        "tools": tools,
        "chat_model": chat_model,
        "tool_names": [tool.name for tool in tools],
    }


def run_langchain_smoke(
    *,
    registry: ToolRegistry | None = None,
    context: ToolContext | None = None,
) -> dict[str, Any]:
    """Deterministic fake-model smoke used by the CLI."""

    from app.llm import FakeLLMProvider

    fake = FakeLLMProvider(
        responses=['{"status":"ok","note":"langchain smoke"}'],
        default_model="fake-model",
        validate_structured_json=False,
    )
    bundle = build_langchain_bundle(
        registry=registry,
        context=context,
        provider=fake,
        model="fake-model",
        provider_name="fake",
    )
    message = bundle["chat_model"].invoke("ping")
    return {
        "tool_names": bundle["tool_names"],
        "model_reply": getattr(message, "content", str(message)),
        "langchain_available": True,
        "langgraph_available": langgraph_available(),
    }


__all__ = [
    "GatewayChatModel",
    "build_langchain_bundle",
    "build_langchain_chat_model",
    "langchain_available",
    "langgraph_available",
    "require_langchain",
    "run_langchain_smoke",
    "to_langchain_tools",
]
