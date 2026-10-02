"""Tests for optional MCP adapter (#67)."""

from __future__ import annotations

import pytest

from app.integrations.mcp import (
    DEFAULT_MCP_ALLOWLIST,
    McpServerConfig,
    McpToolGateway,
    list_mcp_tool_descriptors,
    mcp_available,
    require_mcp,
)
from app.tools import ToolContext, build_tool_registry


def test_default_mcp_allowlist_is_read_only() -> None:
    assert "search_jobs" in DEFAULT_MCP_ALLOWLIST
    assert "get_tw_stock_movers" in DEFAULT_MCP_ALLOWLIST
    assert "query_recent_runs" in DEFAULT_MCP_ALLOWLIST
    assert "send_telegram_message" not in DEFAULT_MCP_ALLOWLIST


def test_mcp_config_rejects_public_host_and_telegram() -> None:
    with pytest.raises(ValueError, match="localhost"):
        McpServerConfig(host="0.0.0.0")
    with pytest.raises(ValueError, match="Telegram"):
        McpServerConfig(include_telegram=True)
    with pytest.raises(ValueError, match="send_telegram_message"):
        McpServerConfig(allowlist=("send_telegram_message",))


def test_list_and_invoke_tools_offline() -> None:
    context = ToolContext(
        fetch_jobs=lambda **_: [
            {
                "source": "dummy",
                "item_type": "job",
                "title": "Cloud SDET",
                "observed_at": "2026-07-01T00:00:00+00:00",
            }
        ],
        query_runs=lambda **_: [{"id": 1, "status": "success"}],
        telegram_enabled=False,
        secrets_to_redact=("sk-live-secret",),
    )
    gateway = McpToolGateway(
        config=McpServerConfig(max_requests=5),
        registry=build_tool_registry(),
        context=context,
    )
    names = {item["name"] for item in gateway.list_tools()}
    assert names == set(DEFAULT_MCP_ALLOWLIST)

    ok = gateway.call_tool("query_recent_runs", {"limit": 1})
    assert ok.ok
    assert ok.metadata.get("via") == "mcp"

    denied = gateway.call_tool("send_telegram_message", {"text": "hi"})
    assert not denied.ok
    assert denied.metadata.get("code") == "tool_not_allowlisted"


def test_mcp_request_limit_and_schema_validation() -> None:
    gateway = McpToolGateway(
        config=McpServerConfig(max_requests=1),
        context=ToolContext(
            query_runs=lambda **_: [],
            telegram_enabled=False,
        ),
    )
    first = gateway.call_tool("query_recent_runs", {"limit": 1})
    assert first.ok
    second = gateway.call_tool("query_recent_runs", {"limit": 1})
    assert not second.ok
    assert second.metadata.get("code") == "request_limit"

    gateway2 = McpToolGateway(
        context=ToolContext(query_runs=lambda **_: [], telegram_enabled=False)
    )
    bad = gateway2.call_tool("query_recent_runs", {"limit": "nope"})
    assert not bad.ok


def test_mcp_tool_timeout_maps_to_error() -> None:
    import time

    from app.tools import ToolRegistry, builtin_tools
    from app.tools.models import ToolResult

    class SlowTool:
        name = "query_recent_runs"
        description = "slow"
        input_schema = {"type": "object", "properties": {}}

        @staticmethod
        def invoke(arguments, *, context):
            time.sleep(0.2)
            return ToolResult(ok=True, data={"count": 0, "runs": []})

    registry = ToolRegistry(
        {
            "query_recent_runs": SlowTool(),
            "search_jobs": builtin_tools()["search_jobs"],
            "get_tw_stock_movers": builtin_tools()["get_tw_stock_movers"],
        },
        allowlist=["query_recent_runs", "search_jobs", "get_tw_stock_movers"],
    )
    gateway = McpToolGateway(
        config=McpServerConfig(request_timeout_seconds=0.05),
        registry=registry,
        context=ToolContext(telegram_enabled=False),
    )
    result = gateway.call_tool("query_recent_runs", {})
    assert not result.ok
    assert result.metadata.get("code") == "timeout"


def test_list_mcp_tool_descriptors_shape() -> None:
    descriptors = list_mcp_tool_descriptors()
    assert descriptors
    for item in descriptors:
        assert "name" in item
        assert "description" in item
        assert "inputSchema" in item


def test_require_mcp_message_when_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.integrations.mcp.deps.mcp_available",
        lambda: False,
    )
    with pytest.raises(ImportError, match=r"\.\[mcp\]"):
        require_mcp()


@pytest.mark.skipif(not mcp_available(), reason="mcp optional extra not installed")
def test_build_mcp_server_when_installed() -> None:
    from app.integrations.mcp import build_mcp_server

    server = build_mcp_server(config=McpServerConfig(transport="stdio"))
    assert server is not None
