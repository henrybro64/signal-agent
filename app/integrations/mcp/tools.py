"""Map project-owned tools to MCP descriptors and invocations (#67)."""

from __future__ import annotations

import threading
import time
from typing import Any

from app.core.logging import get_logger, log_event
from app.tools import ToolContext, ToolRegistry, ToolResult, build_tool_registry
from app.tools.redaction import redact_mapping, safe_log_fields

from .config import DEFAULT_MCP_ALLOWLIST, McpServerConfig

_LOGGER = get_logger("integrations.mcp")


def list_mcp_tool_descriptors(
    registry: ToolRegistry | None = None,
    *,
    allowlist: tuple[str, ...] | None = None,
) -> list[dict[str, Any]]:
    """Return MCP-oriented descriptors for allowlisted registry tools."""

    names = DEFAULT_MCP_ALLOWLIST if allowlist is None else allowlist
    tool_registry = registry or build_tool_registry(allowlist=names)
    descriptors: list[dict[str, Any]] = []
    for item in tool_registry.list_tools():
        if item["name"] not in names:
            continue
        descriptors.append(
            {
                "name": item["name"],
                "description": item["description"],
                "inputSchema": item["input_schema"],
            }
        )
    return descriptors


class McpToolGateway:
    """Invoke allowlisted tools with request limits and redaction."""

    def __init__(
        self,
        *,
        config: McpServerConfig | None = None,
        registry: ToolRegistry | None = None,
        context: ToolContext | None = None,
    ) -> None:
        self.config = config or McpServerConfig()
        self.registry = registry or build_tool_registry(allowlist=self.config.allowlist)
        if context is None:
            from app.tools.permissions import policy_from_app_config

            self.context = ToolContext(
                timeout_seconds=self.config.request_timeout_seconds,
                telegram_enabled=False,
                interactive=False,
                permission_policy=policy_from_app_config(
                    None,
                    interactive=False,
                ),
            )
        else:
            self.context = context
        self._lock = threading.Lock()
        self._request_count = 0

    @property
    def request_count(self) -> int:
        return self._request_count

    def list_tools(self) -> list[dict[str, Any]]:
        return list_mcp_tool_descriptors(
            self.registry,
            allowlist=self.config.allowlist,
        )

    def call_tool(
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
    ) -> ToolResult:
        with self._lock:
            if self._request_count >= self.config.max_requests:
                return ToolResult(
                    ok=False,
                    error="MCP request limit exceeded",
                    metadata={"code": "request_limit"},
                )
            self._request_count += 1

        if name not in self.config.allowlist:
            return ToolResult(
                ok=False,
                error=f"Tool {name!r} is not MCP-allowlisted",
                metadata={"code": "tool_not_allowlisted"},
            )

        started = time.perf_counter()
        ctx = ToolContext(
            config=self.context.config,
            env=self.context.env,
            db_path=self.context.db_path,
            llm=self.context.llm,
            telegram_enabled=False,
            timeout_seconds=self.config.request_timeout_seconds,
            max_items=self.context.max_items,
            fetch_jobs=self.context.fetch_jobs,
            fetch_movers=self.context.fetch_movers,
            query_runs=self.context.query_runs,
            secrets_to_redact=self.context.secrets_to_redact,
            interactive=False,
            permission_policy=self.context.permission_policy,
        )
        result = self.registry.invoke(name, arguments or {}, context=ctx)
        duration_ms = int((time.perf_counter() - started) * 1000)
        safe_data = redact_mapping(
            result.data,
            secrets=ctx.secrets_to_redact,
        )
        safe_result = ToolResult(
            ok=result.ok,
            data=safe_data,
            error=result.error,
            metadata={**result.metadata, "duration_ms": duration_ms, "via": "mcp"},
        )
        log_event(
            _LOGGER,
            event="mcp_tool_call",
            message=f"MCP tool {name} completed",
            **safe_log_fields(
                tool_name=name,
                status="ok" if safe_result.ok else "error",
                duration_ms=duration_ms,
                arguments=arguments or {},
                result=safe_result.to_safe_dict(),
                error=safe_result.error,
                secrets=ctx.secrets_to_redact,
            ),
        )
        return safe_result
