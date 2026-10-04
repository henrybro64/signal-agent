"""Tool permission classes and policy gates (#84)."""

from __future__ import annotations

import contextvars
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from app.core.logging import get_logger, log_event
from app.tools.redaction import redact_mapping, redact_value

_LOGGER = get_logger("tools.permissions")

ApproveCallback = Callable[[str, dict[str, Any], str], bool]


class PermissionClass(StrEnum):
    READ = "read"
    NOTIFY = "notify"
    WRITE = "write"
    EXECUTE = "execute"


class PolicyAction(StrEnum):
    DISABLED = "disabled"
    ASK = "ask"
    ALLOW = "allow"


DEFAULT_CLASS_POLICY: dict[PermissionClass, PolicyAction] = {
    PermissionClass.READ: PolicyAction.ALLOW,
    PermissionClass.NOTIFY: PolicyAction.ASK,
    PermissionClass.WRITE: PolicyAction.DISABLED,
    PermissionClass.EXECUTE: PolicyAction.DISABLED,
}

BUILTIN_TOOL_PERMISSIONS: dict[str, PermissionClass] = {
    "search_jobs": PermissionClass.READ,
    "get_tw_stock_movers": PermissionClass.READ,
    "query_recent_runs": PermissionClass.READ,
    "send_telegram_message": PermissionClass.NOTIFY,
    "browser_navigate": PermissionClass.EXECUTE,
    "browser_read_page": PermissionClass.EXECUTE,
    "browser_screenshot": PermissionClass.EXECUTE,
}

DANGEROUS_CAPABILITIES = frozenset(
    {
        "shell",
        "exec",
        "filesystem",
        "unrestricted_http",
        "browser_automation",
    }
)


@dataclass(slots=True)
class PermissionDecision:
    allowed: bool
    action: PolicyAction
    permission: PermissionClass | None
    reason: str
    needs_approval: bool = False


@dataclass(slots=True)
class PermissionPolicyConfig:
    """Config-facing permission policy (#84)."""

    classes: dict[str, str] = field(
        default_factory=lambda: {
            "read": "allow",
            "notify": "ask",
            "write": "disabled",
            "execute": "disabled",
        }
    )
    tools: dict[str, str] = field(default_factory=dict)
    max_side_effect_actions: int = 3
    audit_log_path: str | None = None


@dataclass(slots=True)
class PermissionPolicy:
    """Evaluate and enforce tool permission decisions."""

    config: PermissionPolicyConfig = field(default_factory=PermissionPolicyConfig)
    interactive: bool = False
    approve: ApproveCallback | None = None
    secrets: tuple[str, ...] = ()
    _side_effect_count: int = 0

    @property
    def side_effect_count(self) -> int:
        return self._side_effect_count

    @staticmethod
    def classify_tool(
        _tool_name: str,
        tool: Any | None = None,
    ) -> PermissionClass | None:
        """Return the tool's permission class, or None for default-deny.

        Classification requires an explicit ``permission`` attribute on the
        tool object/class. Builtin name catalog is only a documentation aid
        for known names — unregistered doubles must set ``permission``.
        """

        if tool is None:
            return None
        has_explicit = hasattr(type(tool), "permission") or (
            hasattr(tool, "__dict__") and "permission" in tool.__dict__
        )
        if not has_explicit:
            return None
        raw = getattr(tool, "permission", None)
        if raw is None:
            return None
        if isinstance(raw, PermissionClass):
            return raw
        try:
            return PermissionClass(str(raw))
        except ValueError:
            return None

    def resolve_action(
        self,
        tool_name: str,
        *,
        permission: PermissionClass | None,
    ) -> PolicyAction:
        if tool_name in DANGEROUS_CAPABILITIES:
            return PolicyAction.DISABLED
        tool_override = self.config.tools.get(tool_name)
        if tool_override:
            return _parse_action(tool_override, default=PolicyAction.DISABLED)
        if permission is None:
            return PolicyAction.DISABLED
        class_action = self.config.classes.get(permission.value)
        if class_action:
            return _parse_action(class_action, default=DEFAULT_CLASS_POLICY[permission])
        return DEFAULT_CLASS_POLICY.get(permission, PolicyAction.DISABLED)

    def decide(
        self,
        tool_name: str,
        arguments: Mapping[str, Any] | None = None,
        *,
        tool: Any | None = None,
        side_effect_summary: str | None = None,
    ) -> PermissionDecision:
        permission = self.classify_tool(tool_name, tool=tool)
        action = self.resolve_action(tool_name, permission=permission)
        args = dict(arguments or {})
        summary = side_effect_summary or _default_side_effect_summary(
            tool_name,
            permission,
            args,
        )
        safe_summary = redact_value(summary, self.secrets)

        if permission is None:
            decision = PermissionDecision(
                allowed=False,
                action=PolicyAction.DISABLED,
                permission=None,
                reason="tool has no explicit permission classification (default deny)",
            )
            self._audit(tool_name, args, decision, safe_summary)
            return decision

        if action == PolicyAction.DISABLED:
            decision = PermissionDecision(
                allowed=False,
                action=action,
                permission=permission,
                reason=f"policy disabled for {permission.value}/{tool_name}",
            )
            self._audit(tool_name, args, decision, safe_summary)
            return decision

        if (
            permission != PermissionClass.READ
            and self._side_effect_count >= self.config.max_side_effect_actions
        ):
            decision = PermissionDecision(
                allowed=False,
                action=action,
                permission=permission,
                reason=(
                    "side-effect action limit reached "
                    f"({self.config.max_side_effect_actions})"
                ),
            )
            self._audit(tool_name, args, decision, safe_summary)
            return decision

        if action == PolicyAction.ALLOW:
            decision = PermissionDecision(
                allowed=True,
                action=action,
                permission=permission,
                reason="policy allow",
            )
            self._audit(tool_name, args, decision, safe_summary)
            return decision

        # ASK
        if not self.interactive:
            decision = PermissionDecision(
                allowed=False,
                action=action,
                permission=permission,
                reason="ask policy denied in non-interactive mode",
                needs_approval=True,
            )
            self._audit(tool_name, args, decision, safe_summary)
            return decision

        approved = False
        if self.approve is not None:
            approved = bool(self.approve(tool_name, args, safe_summary))
        decision = PermissionDecision(
            allowed=approved,
            action=action,
            permission=permission,
            reason="user approved" if approved else "user denied",
            needs_approval=True,
        )
        self._audit(tool_name, args, decision, safe_summary)
        return decision

    def record_side_effect(self, permission: PermissionClass | None) -> None:
        if permission is not None and permission != PermissionClass.READ:
            self._side_effect_count += 1

    def _audit(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        decision: PermissionDecision,
        summary: str,
    ) -> None:
        safe_args = redact_mapping(arguments, secrets=self.secrets)
        permission_value = decision.permission.value if decision.permission else None
        arg_keys = sorted(safe_args)
        log_event(
            _LOGGER,
            event="tool_permission_decision",
            message=f"permission {decision.action.value} for {tool_name}",
            level=20 if decision.allowed else 30,
            tool_name=tool_name,
            allowed=decision.allowed,
            action=decision.action.value,
            permission=permission_value,
            reason=decision.reason,
            summary=summary,
            arg_keys=arg_keys,
        )
        audit_path = self.config.audit_log_path
        if not audit_path:
            return
        path = Path(audit_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        row = {
            "ts": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "tool_name": tool_name,
            "allowed": decision.allowed,
            "action": decision.action.value,
            "permission": permission_value,
            "reason": decision.reason,
            "summary": summary,
            "arg_keys": arg_keys,
        }
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def build_permission_policy(
    config: PermissionPolicyConfig | None = None,
    *,
    interactive: bool = False,
    approve: ApproveCallback | None = None,
    secrets: tuple[str, ...] = (),
    root: str | Path | None = None,
) -> PermissionPolicy:
    cfg = config or PermissionPolicyConfig()
    if cfg.audit_log_path is None and root is not None:
        cfg = PermissionPolicyConfig(
            classes=dict(cfg.classes),
            tools=dict(cfg.tools),
            max_side_effect_actions=cfg.max_side_effect_actions,
            audit_log_path=str(Path(root) / "logs" / "permissions-audit.jsonl"),
        )
    return PermissionPolicy(
        config=cfg,
        interactive=interactive,
        approve=approve,
        secrets=secrets,
    )


def permission_policy_config_from_tools(
    tools_permissions: Any,
    *,
    audit_log_path: str | None = None,
) -> PermissionPolicyConfig:
    """Map ``ToolsPermissionsConfig`` (or compatible) into a policy config."""

    classes = getattr(tools_permissions, "classes", None) or dict(
        PermissionPolicyConfig().classes
    )
    tools = getattr(tools_permissions, "tools", None) or {}
    max_side = getattr(tools_permissions, "max_side_effect_actions", 3)
    return PermissionPolicyConfig(
        classes=dict(classes),
        tools=dict(tools),
        max_side_effect_actions=int(max_side),
        audit_log_path=audit_log_path,
    )


def policy_from_app_config(
    config: Any | None,
    *,
    interactive: bool = False,
    approve: ApproveCallback | None = None,
    secrets: tuple[str, ...] = (),
    root: str | Path | None = None,
    audit_log_path: str | None = None,
) -> PermissionPolicy:
    """Build a policy from ``AppConfig.tools.permissions`` (or safe defaults)."""

    tools_permissions = None
    if config is not None:
        tools_cfg = getattr(config, "tools", None)
        tools_permissions = getattr(tools_cfg, "permissions", None)
    policy_cfg = permission_policy_config_from_tools(
        tools_permissions,
        audit_log_path=audit_log_path,
    )
    return build_permission_policy(
        policy_cfg,
        interactive=interactive,
        approve=approve,
        secrets=secrets,
        root=root,
    )


def format_approval_prompt(
    tool_name: str,
    *,
    side_effect_summary: str,
    permission: PermissionClass | None,
) -> str:
    perm = permission.value if permission else "unknown"
    return (
        f"Allow tool {tool_name!r} [{perm}]?\n"
        f"Side effect: {side_effect_summary}\n"
        "Approve? [y/N]"
    )


# Scheduler/runtime can install an unattended policy for the duration of a tick
# so nested tool invokes honor config without every task rebuilding context.
_ACTIVE_PERMISSION_POLICY: contextvars.ContextVar[PermissionPolicy | None] = (
    contextvars.ContextVar("signal_agent_permission_policy", default=None)
)


def get_active_permission_policy() -> PermissionPolicy | None:
    return _ACTIVE_PERMISSION_POLICY.get()


def push_permission_policy(
    policy: PermissionPolicy | None,
) -> contextvars.Token[PermissionPolicy | None]:
    return _ACTIVE_PERMISSION_POLICY.set(policy)


def reset_permission_policy(
    token: contextvars.Token[PermissionPolicy | None],
) -> None:
    _ACTIVE_PERMISSION_POLICY.reset(token)


def _parse_action(raw: str, *, default: PolicyAction) -> PolicyAction:
    try:
        return PolicyAction(str(raw).strip().lower())
    except ValueError:
        return default


def _default_side_effect_summary(
    tool_name: str,
    permission: PermissionClass | None,
    arguments: dict[str, Any],
) -> str:
    if tool_name == "send_telegram_message":
        text = str(arguments.get("text") or "")
        preview = text[:80] + ("…" if len(text) > 80 else "")
        return f"Send Telegram message ({len(text)} chars): {preview!r}"
    if permission == PermissionClass.READ:
        return f"Read-only tool {tool_name} with args {sorted(arguments)}"
    if permission == PermissionClass.NOTIFY:
        return f"Send an external notification via {tool_name}"
    if permission == PermissionClass.WRITE:
        return f"Mutate an external/local resource via {tool_name}"
    if permission == PermissionClass.EXECUTE:
        return f"Execute a high-risk capability via {tool_name}"
    return f"Invoke unclassified tool {tool_name}"
