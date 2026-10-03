"""Framework-neutral agent-callable tools (#62).

Dependency direction: workflows/adapters depend on these interfaces; tools wrap
existing sources, DB, and notifiers. Do not import LangChain here.
"""

from .base import Tool
from .health_tools import QueryRecentRunsTool
from .job_tools import SearchJobsTool
from .models import ToolContext, ToolError, ToolResult
from .notification_tools import SendTelegramMessageTool
from .permissions import (
    PermissionClass,
    PermissionPolicy,
    PermissionPolicyConfig,
    PolicyAction,
    build_permission_policy,
    format_approval_prompt,
    permission_policy_config_from_tools,
    policy_from_app_config,
)
from .registry import (
    ALL_BUILTIN_TOOLS,
    DEFAULT_TOOL_ALLOWLIST,
    ToolRegistry,
    build_tool_registry,
    builtin_tools,
)
from .stock_tools import GetTwStockMoversTool

__all__ = [
    "ALL_BUILTIN_TOOLS",
    "DEFAULT_TOOL_ALLOWLIST",
    "GetTwStockMoversTool",
    "PermissionClass",
    "PermissionPolicy",
    "PermissionPolicyConfig",
    "PolicyAction",
    "QueryRecentRunsTool",
    "SearchJobsTool",
    "SendTelegramMessageTool",
    "Tool",
    "ToolContext",
    "ToolError",
    "ToolRegistry",
    "ToolResult",
    "build_permission_policy",
    "build_tool_registry",
    "builtin_tools",
    "format_approval_prompt",
    "permission_policy_config_from_tools",
    "policy_from_app_config",
]
