"""Framework-neutral agent-callable tools (#62).

Dependency direction: workflows/adapters depend on these interfaces; tools wrap
existing sources, DB, and notifiers. Do not import LangChain here.
"""

from .base import Tool
from .health_tools import QueryRecentRunsTool
from .job_tools import SearchJobsTool
from .models import ToolContext, ToolError, ToolResult
from .notification_tools import SendTelegramMessageTool
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
    "QueryRecentRunsTool",
    "SearchJobsTool",
    "SendTelegramMessageTool",
    "Tool",
    "ToolContext",
    "ToolError",
    "ToolRegistry",
    "ToolResult",
    "build_tool_registry",
    "builtin_tools",
]
