"""Adapt the project LLM gateway into a LangChain chat model."""

from __future__ import annotations

from typing import Any

from app.llm import LLMProvider, LLMRequest, create_llm_provider

from .deps import require_langchain


def build_langchain_chat_model(
    *,
    provider: LLMProvider | None = None,
    api_key: str | None = None,
    model: str = "gpt-4o-mini",
    temperature: float = 0.2,
    timeout_seconds: float = 20.0,
    provider_name: str = "openai",
) -> Any:
    """Create a LangChain chat model backed by the project LLM gateway."""

    require_langchain()
    llm = provider or create_llm_provider(
        provider=provider_name,
        api_key=api_key,
        model=model,
        temperature=temperature,
        timeout_seconds=timeout_seconds,
    )
    return GatewayChatModel(provider=llm, model_name=model, temperature=temperature)


class GatewayChatModel:
    """Minimal LangChain-compatible chat model wrapping ``LLMProvider``.

    Implements the small surface area needed for smoke tests and graph pilots
    without pulling OpenAI SDK usage into business logic.
    """

    def __init__(
        self,
        *,
        provider: LLMProvider,
        model_name: str,
        temperature: float = 0.2,
    ) -> None:
        require_langchain()
        self.provider = provider
        self.model_name = model_name
        self.temperature = temperature

    @property
    def _llm_type(self) -> str:
        return f"signal-agent-{self.provider.name}"

    def invoke(self, input: Any, config: Any | None = None) -> Any:
        del config
        from langchain_core.messages import (
            AIMessage,
            BaseMessage,
            HumanMessage,
            SystemMessage,
        )

        instructions = None
        user_parts: list[str] = []
        if isinstance(input, str):
            user_parts.append(input)
        elif isinstance(input, list):
            for message in input:
                if isinstance(message, SystemMessage):
                    instructions = str(message.content)
                elif isinstance(message, (HumanMessage, BaseMessage)):
                    user_parts.append(str(message.content))
                else:
                    user_parts.append(str(message))
        else:
            user_parts.append(str(input))

        response = self.provider.generate(
            LLMRequest(
                instructions=instructions,
                input="\n".join(user_parts).strip() or " ",
                model=self.model_name,
                temperature=self.temperature,
            )
        )
        return AIMessage(content=response.text)

    def with_config(self, **kwargs: Any) -> GatewayChatModel:
        del kwargs
        return self
