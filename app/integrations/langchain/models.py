"""Adapt the project LLM gateway into a LangChain chat model."""

from __future__ import annotations

from typing import Any

from app.llm import LLMProvider, LLMRequest, create_llm_provider

from .deps import require_langchain

# Mutable cache avoids a module-level ``global`` statement.
_GATEWAY_CHAT_MODEL_CACHE: dict[str, type[Any]] = {}


def build_langchain_chat_model(
    *,
    provider: LLMProvider | None = None,
    api_key: str | None = None,
    model: str = "gpt-4o-mini",
    temperature: float = 0.2,
    timeout_seconds: float = 20.0,
    provider_name: str = "openai",
) -> Any:
    """Create a LangChain ``BaseChatModel`` backed by the project LLM gateway."""

    require_langchain()
    llm = provider or create_llm_provider(
        provider=provider_name,
        api_key=api_key,
        model=model,
        temperature=temperature,
        timeout_seconds=timeout_seconds,
    )
    model_cls = _gateway_chat_model_class()
    return model_cls(
        provider=llm,
        model_name=model,
        temperature=temperature,
    )


def _gateway_chat_model_class() -> type[Any]:
    """Return a cached ``BaseChatModel`` subclass (requires langchain)."""

    cached = _GATEWAY_CHAT_MODEL_CACHE.get("cls")
    if cached is not None:
        return cached

    require_langchain()
    from langchain_core.language_models.chat_models import BaseChatModel
    from langchain_core.messages import (
        AIMessage,
        BaseMessage,
        HumanMessage,
        SystemMessage,
    )
    from langchain_core.outputs import ChatGeneration, ChatResult

    class GatewayChatModel(BaseChatModel):
        """LangChain chat model wrapping the project-owned ``LLMProvider``."""

        model_name: str = "gpt-4o-mini"
        temperature: float = 0.2

        def __init__(
            self,
            *,
            provider: LLMProvider,
            model_name: str = "gpt-4o-mini",
            temperature: float = 0.2,
            **kwargs: Any,
        ) -> None:
            super().__init__(
                model_name=model_name,
                temperature=temperature,
                **kwargs,
            )
            object.__setattr__(self, "_provider", provider)

        @property
        def _llm_type(self) -> str:
            return f"signal-agent-{self._provider.name}"

        def _generate(
            self,
            messages: list[Any],
            stop: list[str] | None = None,
            run_manager: Any | None = None,
            **kwargs: Any,
        ) -> ChatResult:
            _ = (stop, run_manager, kwargs)
            instructions = None
            user_parts: list[str] = []
            for message in messages:
                if isinstance(message, SystemMessage):
                    instructions = str(message.content)
                elif isinstance(message, (HumanMessage, BaseMessage)):
                    user_parts.append(str(message.content))
                else:
                    user_parts.append(str(message))

            response = self._provider.generate(
                LLMRequest(
                    instructions=instructions,
                    input="\n".join(user_parts).strip() or " ",
                    model=self.model_name,
                    temperature=self.temperature,
                )
            )
            ai_message = AIMessage(content=response.text)
            return ChatResult(generations=[ChatGeneration(message=ai_message)])

        @property
        def _identifying_params(self) -> dict[str, Any]:
            return {
                "model_name": self.model_name,
                "provider": self._provider.name,
            }

    GatewayChatModel.__name__ = "GatewayChatModel"
    GatewayChatModel.__qualname__ = "GatewayChatModel"
    _GATEWAY_CHAT_MODEL_CACHE["cls"] = GatewayChatModel
    return GatewayChatModel


def __getattr__(name: str) -> Any:
    if name == "GatewayChatModel":
        return _gateway_chat_model_class()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
