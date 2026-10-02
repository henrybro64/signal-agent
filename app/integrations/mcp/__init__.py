"""Optional MCP adapters around the project-owned tool registry (#67).

MCP must not be imported by ``app.core``, ``app.sources``, ``app.scorers``,
``app.notifiers``, or deterministic tasks. Import this package only from
optional CLI paths or tests.
"""

from .config import DEFAULT_MCP_ALLOWLIST, McpServerConfig
from .deps import mcp_available, require_mcp
from .server import build_mcp_server, run_mcp_server, serve_mcp
from .tools import McpToolGateway, list_mcp_tool_descriptors

__all__ = [
    "DEFAULT_MCP_ALLOWLIST",
    "McpServerConfig",
    "McpToolGateway",
    "build_mcp_server",
    "list_mcp_tool_descriptors",
    "mcp_available",
    "require_mcp",
    "run_mcp_server",
    "serve_mcp",
]
