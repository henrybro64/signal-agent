"""Configuration for the optional local MCP server (#67)."""

from __future__ import annotations

from dataclasses import dataclass, field

from app.tools import DEFAULT_TOOL_ALLOWLIST

# Read-oriented tools only. Telegram / side-effect tools stay out by default.
DEFAULT_MCP_ALLOWLIST: tuple[str, ...] = tuple(DEFAULT_TOOL_ALLOWLIST)


@dataclass(slots=True)
class McpServerConfig:
    """Local-only MCP server settings."""

    host: str = "127.0.0.1"
    port: int = 8765
    transport: str = "stdio"  # stdio | sse
    allowlist: tuple[str, ...] = field(default_factory=lambda: DEFAULT_MCP_ALLOWLIST)
    max_requests: int = 100
    request_timeout_seconds: float = 30.0
    include_telegram: bool = False

    def __post_init__(self) -> None:
        if self.host not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError(
                "MCP server host must be localhost/127.0.0.1/::1 by default; "
                "public binds are out of scope for #67"
            )
        if self.port < 1 or self.port > 65535:
            raise ValueError("MCP server port must be between 1 and 65535")
        if self.transport not in {"stdio", "sse"}:
            raise ValueError("MCP transport must be 'stdio' or 'sse'")
        if self.max_requests < 1:
            raise ValueError("max_requests must be >= 1")
        if self.request_timeout_seconds <= 0:
            raise ValueError("request_timeout_seconds must be positive")
        if self.include_telegram:
            raise ValueError(
                "Telegram MCP exposure requires explicit safety review; "
                "include_telegram is not supported in #67"
            )
        cleaned = tuple(
            name.strip() for name in self.allowlist if str(name).strip()
        )
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
        self.allowlist = cleaned or DEFAULT_MCP_ALLOWLIST
