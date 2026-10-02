"""Optional local MCP server adapter (#67)."""

from __future__ import annotations

import json
from typing import Any

from app.tools import ToolContext, ToolRegistry

from .config import McpServerConfig
from .deps import require_mcp
from .tools import McpToolGateway


def build_mcp_server(
    *,
    config: McpServerConfig | None = None,
    registry: ToolRegistry | None = None,
    context: ToolContext | None = None,
) -> Any:
    """Build a FastMCP server exposing allowlisted read-oriented tools."""

    require_mcp()
    from mcp.server.fastmcp import FastMCP

    cfg = config or McpServerConfig()
    gateway = McpToolGateway(config=cfg, registry=registry, context=context)
    server = FastMCP(
        "signal-agent",
        host=cfg.host,
        port=cfg.port,
    )

    for descriptor in gateway.list_tools():
        _register_tool(server, gateway, descriptor)

    return server


def _register_tool(
    server: Any,
    gateway: McpToolGateway,
    descriptor: dict[str, Any],
) -> None:
    tool_name = str(descriptor["name"])
    description = str(descriptor.get("description") or tool_name)

    def _handler(**kwargs: Any) -> str:
        result = gateway.call_tool(tool_name, dict(kwargs))
        return json.dumps(result.to_safe_dict(), ensure_ascii=False)

    _handler.__name__ = tool_name
    _handler.__doc__ = description
    server.tool(name=tool_name, description=description)(_handler)


def run_mcp_server(
    server: Any,
    *,
    config: McpServerConfig | None = None,
) -> None:
    """Run a previously built MCP server (blocking)."""

    cfg = config or McpServerConfig()
    if cfg.transport == "stdio":
        server.run(transport="stdio")
        return
    server.run(transport="sse")


def serve_mcp(
    *,
    config: McpServerConfig | None = None,
    registry: ToolRegistry | None = None,
    context: ToolContext | None = None,
) -> None:
    """Build and run the local MCP server."""

    cfg = config or McpServerConfig()
    server = build_mcp_server(config=cfg, registry=registry, context=context)
    run_mcp_server(server, config=cfg)
