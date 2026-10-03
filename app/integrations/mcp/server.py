"""Optional local MCP server adapter (#67)."""

from __future__ import annotations

import inspect
import json
from typing import Annotated, Any, Literal

from app.tools import ToolContext, ToolRegistry

from .config import McpServerConfig
from .deps import require_mcp
from .tools import McpToolGateway

_JSON_TYPE_MAP: dict[str, type] = {
    "string": str,
    "integer": int,
    "number": float,
    "boolean": bool,
    "array": list,
    "object": dict,
}


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
    schema = dict(descriptor.get("inputSchema") or {"type": "object", "properties": {}})
    handler = _handler_from_schema(tool_name, description, schema, gateway)
    server.add_tool(handler, name=tool_name, description=description)


def _python_type_for_property(prop: dict[str, Any]) -> Any:
    enum_values = prop.get("enum")
    if isinstance(enum_values, list) and enum_values:
        # Build Literal[…] dynamically so FastMCP exposes the enum in discovery.
        return Literal.__getitem__(tuple(enum_values))

    json_type = str(prop.get("type") or "string")
    return _JSON_TYPE_MAP.get(json_type, Any)


def _annotation_for_property(prop: dict[str, Any]) -> Any:
    """Build a typing annotation that preserves schema constraints for FastMCP."""

    from pydantic import Field

    py_type = _python_type_for_property(prop)
    field_kwargs: dict[str, Any] = {}
    if prop.get("description"):
        field_kwargs["description"] = str(prop["description"])
    if "minimum" in prop:
        field_kwargs["ge"] = prop["minimum"]
    if "maximum" in prop:
        field_kwargs["le"] = prop["maximum"]
    return Annotated[py_type | None, Field(default=None, **field_kwargs)]


def _handler_from_schema(
    tool_name: str,
    description: str,
    schema: dict[str, Any],
    gateway: McpToolGateway,
) -> Any:
    """Build a FastMCP-callable whose signature mirrors ``inputSchema``."""

    properties = dict(schema.get("properties") or {})
    parameters: list[inspect.Parameter] = []
    annotations: dict[str, Any] = {}

    for name, prop in properties.items():
        prop_dict = prop if isinstance(prop, dict) else {}
        annotation = _annotation_for_property(prop_dict)
        annotations[name] = annotation
        parameters.append(
            inspect.Parameter(
                name,
                inspect.Parameter.KEYWORD_ONLY,
                default=None,
                annotation=annotation,
            )
        )

    signature = inspect.Signature(parameters)

    def handler(**kwargs: Any) -> str:
        cleaned = {key: value for key, value in kwargs.items() if value is not None}
        result = gateway.call_tool(tool_name, cleaned)
        return json.dumps(result.to_safe_dict(), ensure_ascii=False)

    handler.__name__ = tool_name
    handler.__doc__ = description
    handler.__signature__ = signature  # type: ignore[attr-defined]
    handler.__annotations__ = annotations
    return handler


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
