"""Optional LangChain adapters around project-owned LLM + tools (#63).

LangChain must not be imported by ``app.core``, ``app.sources``, ``app.scorers``,
``app.notifiers``, or deterministic tasks. Import this package only from
optional CLI paths, workflows, or tests.

``GatewayChatModel`` is available via lazy ``__getattr__`` (not listed in
``__all__``) so importing this package does not require the LangChain extra.
"""

from typing import Any

from .deps import (
    langchain_available,
    langgraph_available,
    require_langchain,
    require_langgraph,
)
from .factory import (
    build_langchain_bundle,
    build_langchain_chat_model,
    run_langchain_smoke,
)
from .tools import to_langchain_tools

__all__ = [
    "build_langchain_bundle",
    "build_langchain_chat_model",
    "langchain_available",
    "langgraph_available",
    "require_langchain",
    "require_langgraph",
    "run_langchain_smoke",
    "to_langchain_tools",
]


def __getattr__(name: str) -> Any:
    if name == "GatewayChatModel":
        from .models import GatewayChatModel as gateway_chat_model_cls

        return gateway_chat_model_cls
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
