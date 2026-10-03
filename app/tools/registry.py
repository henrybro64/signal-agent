"""Explicit allowlisted tool registry (no dynamic module loading)."""

from __future__ import annotations

import threading
import time
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from app.core.logging import get_logger, log_event

from .base import Tool
from .health_tools import QueryRecentRunsTool
from .job_tools import SearchJobsTool
from .models import ToolContext, ToolError, ToolResult
from .notification_tools import SendTelegramMessageTool
from .permissions import PermissionPolicy, build_permission_policy
from .redaction import redact_mapping, safe_log_fields
from .stock_tools import GetTwStockMoversTool
from .validation import validate_tool_arguments

_LOGGER = get_logger("tools.registry")

DEFAULT_TOOL_ALLOWLIST: tuple[str, ...] = (
    "search_jobs",
    "get_tw_stock_movers",
    "query_recent_runs",
)

# Telegram is intentionally omitted from the default allowlist.
ALL_BUILTIN_TOOLS: tuple[str, ...] = (
    *DEFAULT_TOOL_ALLOWLIST,
    "send_telegram_message",
)


def builtin_tools() -> dict[str, Tool]:
    """Return the canonical built-in tool instances keyed by name."""

    tools: list[Tool] = [
        SearchJobsTool(),
        GetTwStockMoversTool(),
        QueryRecentRunsTool(),
        SendTelegramMessageTool(),
    ]
    return {tool.name: tool for tool in tools}


class ToolRegistry:
    """Allowlisted registry that wraps tools with timeout and safe logging."""

    def __init__(
        self,
        tools: Mapping[str, Tool] | None = None,
        *,
        allowlist: Sequence[str] | None = None,
    ) -> None:
        catalog = dict(tools) if tools is not None else builtin_tools()
        allowed = (
            list(DEFAULT_TOOL_ALLOWLIST)
            if allowlist is None
            else [name.strip() for name in allowlist if str(name).strip()]
        )
        unknown = [name for name in allowed if name not in catalog]
        if unknown:
            raise ToolError(
                "Unknown tools in allowlist: " + ", ".join(sorted(unknown)),
                code="unknown_allowlist_tool",
            )
        self._catalog = catalog
        self._allowlist = frozenset(allowed)

    @property
    def allowlist(self) -> frozenset[str]:
        return self._allowlist

    def list_tools(self) -> list[dict[str, Any]]:
        """Return allowlisted tool descriptors (name, description, schema)."""

        descriptors: list[dict[str, Any]] = []
        for name in sorted(self._allowlist):
            tool = self._catalog[name]
            permission = getattr(tool, "permission", None)
            descriptors.append(
                {
                    "name": tool.name,
                    "description": tool.description,
                    "input_schema": tool.input_schema,
                    "permission": (
                        str(permission) if permission is not None else None
                    ),
                }
            )
        return descriptors

    def get(self, name: str) -> Tool:
        """Return an allowlisted tool or raise."""

        if name not in self._allowlist:
            raise ToolError(
                f"Tool {name!r} is not allowlisted",
                code="tool_not_allowlisted",
            )
        try:
            return self._catalog[name]
        except KeyError as exc:
            raise ToolError(
                f"Unknown tool: {name}",
                code="unknown_tool",
            ) from exc

    def invoke(
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
        *,
        context: ToolContext | None = None,
    ) -> ToolResult:
        """Invoke an allowlisted tool with timeout, logging, and redaction."""

        ctx = context or ToolContext()
        args = {
            key: value
            for key, value in dict(arguments or {}).items()
            if value is not None
        }
        secrets = ctx.secrets_to_redact
        tool = self.get(name)
        started = time.perf_counter()

        denied = _permission_denied_result(
            name,
            tool=tool,
            arguments=args,
            context=ctx,
            started=started,
        )
        if denied is not None:
            return denied

        try:
            validate_tool_arguments(args, tool.input_schema, tool_name=name)
            result = _invoke_with_timeout(tool, args, context=ctx)
        except ToolError as exc:
            duration_ms = int((time.perf_counter() - started) * 1000)
            log_event(
                _LOGGER,
                event="tool_invoke_failed",
                message=f"Tool {name} failed",
                level=30,
                **safe_log_fields(
                    tool_name=name,
                    status=exc.code,
                    duration_ms=duration_ms,
                    arguments=args,
                    error=str(exc),
                    secrets=secrets,
                ),
            )
            return ToolResult(
                ok=False,
                error=str(exc),
                metadata={"code": exc.code, "duration_ms": duration_ms},
            )
        except Exception as exc:  # noqa: BLE001 - boundary: normalize to ToolResult
            duration_ms = int((time.perf_counter() - started) * 1000)
            log_event(
                _LOGGER,
                event="tool_invoke_failed",
                message=f"Tool {name} raised",
                level=40,
                **safe_log_fields(
                    tool_name=name,
                    status="provider_error",
                    duration_ms=duration_ms,
                    arguments=args,
                    error=str(exc),
                    secrets=secrets,
                ),
            )
            return ToolResult(
                ok=False,
                error="Tool invocation failed",
                metadata={"code": "provider_error", "duration_ms": duration_ms},
            )

        duration_ms = int((time.perf_counter() - started) * 1000)
        safe_data = redact_mapping(result.data, secrets=secrets)
        safe_error = (
            None if result.error is None else _redact_text(result.error, secrets)
        )
        policy = _policy_from_context(ctx)
        permission = getattr(tool, "permission", None)
        if policy is not None and result.ok:
            policy.record_side_effect(permission)
        safe_result = ToolResult(
            ok=result.ok,
            data=safe_data,
            error=safe_error,
            metadata={
                **result.metadata,
                "duration_ms": duration_ms,
                "permission": str(permission) if permission is not None else None,
            },
        )
        log_event(
            _LOGGER,
            event="tool_invoke_completed",
            message=f"Tool {name} completed",
            **safe_log_fields(
                tool_name=name,
                status="ok" if safe_result.ok else "error",
                duration_ms=duration_ms,
                arguments=args,
                result=safe_result.to_safe_dict(),
                error=safe_result.error,
                secrets=secrets,
            ),
        )
        return safe_result


def build_tool_registry(
    *,
    allowlist: Iterable[str] | None = None,
    include_telegram: bool = False,
) -> ToolRegistry:
    """Build a registry from builtins with an optional allowlist override."""

    names = list(allowlist) if allowlist is not None else list(DEFAULT_TOOL_ALLOWLIST)
    if include_telegram and "send_telegram_message" not in names:
        names.append("send_telegram_message")
    return ToolRegistry(allowlist=names)


def _invoke_with_timeout(
    tool: Tool,
    arguments: dict[str, Any],
    *,
    context: ToolContext,
) -> ToolResult:
    """Run a tool with a wall-clock timeout.

    Python cannot forcibly kill a running thread. We therefore:
    1. run the worker on a *daemon* thread so a hung collector cannot keep a
       one-shot CLI process alive after the caller has moved on;
    2. set ``context.cancel_event`` on timeout so cooperative tools (and
       HTTP clients that already honor request timeouts) can stop promptly.
    """

    timeout = context.timeout_seconds
    if timeout <= 0:
        return tool.invoke(arguments, context=context)

    cancel_event = context.cancel_event or threading.Event()
    if context.cancel_event is None:
        context.cancel_event = cancel_event

    box: list[ToolResult | BaseException] = []
    done = threading.Event()

    def _worker() -> None:
        try:
            box.append(tool.invoke(arguments, context=context))
        except BaseException as exc:  # noqa: BLE001 - surface to caller
            box.append(exc)
        finally:
            done.set()

    thread = threading.Thread(
        target=_worker,
        name=f"tool-{tool.name}",
        daemon=True,
    )
    thread.start()
    if not done.wait(timeout=timeout):
        cancel_event.set()
        raise ToolError(
            f"Tool {tool.name!r} timed out after {timeout}s",
            code="timeout",
        )

    outcome = box[0]
    if isinstance(outcome, BaseException):
        raise outcome
    return outcome


def _redact_text(value: str, secrets: tuple[str, ...]) -> str:
    from .redaction import redact_value

    return redact_value(value, secrets)


def _policy_from_context(context: ToolContext) -> PermissionPolicy | None:
    policy = context.permission_policy
    if isinstance(policy, PermissionPolicy):
        return policy
    # Opt out only when callers explicitly set permission_policy=False.
    if context.permission_policy is False:
        return None

    from .permissions import (
        PermissionPolicyConfig,
        get_active_permission_policy,
        policy_from_app_config,
    )

    active = get_active_permission_policy()
    if active is not None:
        return active

    # Prefer AppConfig.tools.permissions when the caller attached config.
    if context.config is not None:
        return policy_from_app_config(
            context.config,
            interactive=bool(context.interactive),
            secrets=context.secrets_to_redact,
        )

    # Conservative legacy fallback: never auto-allow notify from telegram_enabled.
    return build_permission_policy(
        PermissionPolicyConfig(
            classes={
                "read": "allow",
                "notify": "disabled",
                "write": "disabled",
                "execute": "disabled",
            }
        ),
        interactive=bool(context.interactive),
        secrets=context.secrets_to_redact,
    )


def _permission_denied_result(
    name: str,
    *,
    tool: Tool,
    arguments: dict[str, Any],
    context: ToolContext,
    started: float,
) -> ToolResult | None:
    policy = _policy_from_context(context)
    if policy is None:
        return None
    decision = policy.decide(name, arguments, tool=tool)
    if decision.allowed:
        return None
    duration_ms = int((time.perf_counter() - started) * 1000)
    code = (
        "permission_denied"
        if not decision.needs_approval
        else "permission_ask_denied"
    )
    log_event(
        _LOGGER,
        event="tool_invoke_failed",
        message=f"Tool {name} blocked by permission policy",
        level=30,
        **safe_log_fields(
            tool_name=name,
            status=code,
            duration_ms=duration_ms,
            arguments=arguments,
            error=decision.reason,
            secrets=context.secrets_to_redact,
        ),
    )
    return ToolResult(
        ok=False,
        error=decision.reason,
        metadata={
            "code": code,
            "duration_ms": duration_ms,
            "permission": (
                decision.permission.value if decision.permission else None
            ),
            "policy_action": decision.action.value,
        },
    )
