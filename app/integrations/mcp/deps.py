"""Optional MCP dependency helpers."""

from __future__ import annotations

_MISSING_MSG = (
    "MCP optional dependencies are not installed. "
    "Install with: pip install -e '.[mcp]'"
)


def mcp_available() -> bool:
    """Return True when the ``mcp`` package can be imported."""

    try:
        import mcp  # noqa: F401
    except ImportError:
        return False
    return True


def require_mcp() -> None:
    """Raise a clear error when the optional MCP extra is missing."""

    if not mcp_available():
        raise ImportError(_MISSING_MSG)
