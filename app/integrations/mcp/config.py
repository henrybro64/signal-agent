"""Configuration for the optional local MCP server (#67)."""

from __future__ import annotations

from dataclasses import dataclass, field

from app.tools import DEFAULT_TOOL_ALLOWLIST

# Read-oriented tools only. Telegram / side-effect tools stay out by default.
DEFAULT_MCP_ALLOWLIST: tuple[str, ...] = tuple(DEFAULT_TOOL_ALLOWLIST)
_LOCALHOST_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
_SUPPORTED_TRANSPORTS = frozenset({"stdio", "sse"})


def _normalize_allowlist(allowlist: tuple[str, ...]) -> tuple[str, ...]:
    """Return a cleaned allowlist; an explicit empty tuple stays empty."""

    cleaned = tuple(name.strip() for name in allowlist if str(name).strip())
    forbidden = [name for name in cleaned if name == "send_telegram_message"]
    if forbidden:
        raise ValueError(
            "send_telegram_message cannot be exposed over MCP by default"
        )
    unknown = [name for name in cleaned if name not in DEFAULT_MCP_ALLOWLIST]
    if unknown:
        raise ValueError(
            "MCP allowlist may only include read-oriented tools: "
            + ", ".join(sorted(unknown))
        )
    return cleaned


def _validate_bind(*, host: str, port: int, transport: str) -> None:
    if host not in _LOCALHOST_HOSTS:
        raise ValueError(
            "MCP server host must be localhost/127.0.0.1/::1 by default; "
            "public binds are out of scope for #67"
        )
    if port < 1 or port > 65535:
        raise ValueError("MCP server port must be between 1 and 65535")
    if transport not in _SUPPORTED_TRANSPORTS:
        raise ValueError("MCP transport must be 'stdio' or 'sse'")


def _validate_limits(*, max_requests: int, request_timeout_seconds: float) -> None:
    if max_requests < 1:
        raise ValueError("max_requests must be >= 1")
    if request_timeout_seconds <= 0:
        raise ValueError("request_timeout_seconds must be positive")


@dataclass(slots=True)
class McpServerConfig:
    """Local-only MCP server settings."""

    host: str = "127.0.0.1"
    port: int = 8765
    transport: str = "stdio"  # stdio | sse
    # Default is the read-only set; pass allowlist=() to expose nothing.
    allowlist: tuple[str, ...] = field(default_factory=lambda: DEFAULT_MCP_ALLOWLIST)
    max_requests: int = 100
    request_timeout_seconds: float = 30.0
    include_telegram: bool = False

    def __post_init__(self) -> None:
        _validate_bind(host=self.host, port=self.port, transport=self.transport)
        _validate_limits(
            max_requests=self.max_requests,
            request_timeout_seconds=self.request_timeout_seconds,
        )
        if self.include_telegram:
            raise ValueError(
                "Telegram MCP exposure requires explicit safety review; "
                "include_telegram is not supported in #67"
            )
        self.allowlist = _normalize_allowlist(self.allowlist)
