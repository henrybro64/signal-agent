"""Optional LangChain adapters around project-owned LLM + tools (#63).

LangChain must not be imported by ``app.core``, ``app.sources``, ``app.scorers``,
``app.notifiers``, or deterministic tasks. Import this package only from
optional CLI paths, workflows, or tests.
"""

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
from .models import GatewayChatModel
from .tools import to_langchain_tools

__all__ = [
    "GatewayChatModel",
    "build_langchain_bundle",
    "build_langchain_chat_model",
    "langchain_available",
    "langgraph_available",
    "require_langchain",
    "require_langgraph",
    "run_langchain_smoke",
    "to_langchain_tools",
]
