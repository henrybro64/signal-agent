"""Side-effect tool approval helpers (#83/#84)."""

from __future__ import annotations

from collections.abc import Callable

from app.tools.permissions import (
    BUILTIN_TOOL_PERMISSIONS,
    PermissionClass,
    PermissionPolicy,
    PolicyAction,
    format_approval_prompt,
)

# Read-oriented tools may auto-run when allowlisted.
READ_ONLY_TOOLS = frozenset(
    name
    for name, permission in BUILTIN_TOOL_PERMISSIONS.items()
    if permission == PermissionClass.READ
)

SIDE_EFFECT_TOOLS = frozenset(
    name
    for name, permission in BUILTIN_TOOL_PERMISSIONS.items()
    if permission != PermissionClass.READ
)

ApproveFn = Callable[[str, dict], bool]


def is_read_only_tool(name: str) -> bool:
    return name in READ_ONLY_TOOLS


def requires_approval(name: str) -> bool:
    return name in SIDE_EFFECT_TOOLS


def default_approver(name: str, _arguments: dict) -> bool:
    """Default policy: deny side-effecting tools."""

    return not requires_approval(name)


def policy_approver(
    policy: PermissionPolicy,
    *,
    prompt_fn: Callable[[str], bool] | None = None,
) -> ApproveFn:
    """Build a chat approver that asks via ``prompt_fn`` for ask-policy tools."""

    def _approve(name: str, arguments: dict) -> bool:
        from app.tools.registry import builtin_tools

        tool = builtin_tools().get(name)
        permission = policy.classify_tool(name, tool=tool)
        action = policy.resolve_action(name, permission=permission)
        if action == PolicyAction.ALLOW:
            return True
        if action == PolicyAction.DISABLED:
            return False
        # ASK — interactive prompt; never block on missing prompt_fn.
        if prompt_fn is None:
            return False
        summary = (
            f"Invoke {name} with args {sorted(arguments)}"
            if not arguments
            else f"Invoke {name}"
        )
        if name == "send_telegram_message":
            text = str(arguments.get("text") or "")
            preview = text[:80] + ("…" if len(text) > 80 else "")
            summary = f"Send Telegram message ({len(text)} chars): {preview!r}"
        prompt = format_approval_prompt(
            name,
            side_effect_summary=summary,
            permission=permission,
        )
        return bool(prompt_fn(prompt))

    return _approve
