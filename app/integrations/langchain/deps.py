"""Optional LangChain dependency helpers."""

from __future__ import annotations

_MISSING_MSG = (
    "LangChain optional dependencies are not installed. "
    "Install with: pip install -e '.[langchain]'"
)


def langchain_available() -> bool:
    """Return True when langchain_core can be imported."""

    try:
        import langchain_core  # noqa: F401
    except ImportError:
        return False
    return True


def langgraph_available() -> bool:
    """Return True when langgraph can be imported."""

    try:
        import langgraph  # noqa: F401
    except ImportError:
        return False
    return True


def require_langchain() -> None:
    """Raise a clear error when the optional LangChain extra is missing."""

    if not langchain_available():
        raise ImportError(_MISSING_MSG)


def require_langgraph() -> None:
    """Raise a clear error when LangGraph (part of .[langchain]) is missing."""

    require_langchain()
    if not langgraph_available():
        raise ImportError(
            "LangGraph is not installed. "
            "Install with: pip install -e '.[langchain]'"
        )
