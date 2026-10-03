"""Side-effect tool approval helpers (#83)."""

from __future__ import annotations

from collections.abc import Callable

# Read-oriented tools may auto-run when allowlisted.
READ_ONLY_TOOLS = frozenset(
    {
        "search_jobs",
        "get_tw_stock_movers",
        "query_recent_runs",
    }
)

SIDE_EFFECT_TOOLS = frozenset({"send_telegram_message"})

ApproveFn = Callable[[str, dict], bool]


def is_read_only_tool(name: str) -> bool:
    return name in READ_ONLY_TOOLS


def requires_approval(name: str) -> bool:
    return name in SIDE_EFFECT_TOOLS


def default_approver(name: str, arguments: dict) -> bool:
    """Default policy: deny side-effecting tools."""

    del arguments
    return not requires_approval(name)
