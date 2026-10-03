"""Tests for tool permission policy (#84)."""

from __future__ import annotations

from pathlib import Path

from app.tools import (
    ToolContext,
    ToolRegistry,
    ToolResult,
    build_permission_policy,
    build_tool_registry,
    builtin_tools,
)
from app.tools.permissions import (
    PermissionClass,
    PermissionPolicyConfig,
    PolicyAction,
    format_approval_prompt,
)


def test_builtin_tools_have_explicit_permission_classes() -> None:
    for name, tool in builtin_tools().items():
        assert tool.permission in {
            PermissionClass.READ,
            PermissionClass.NOTIFY,
            PermissionClass.WRITE,
            PermissionClass.EXECUTE,
        }, name


def test_default_deny_for_unclassified_tool() -> None:
    class Mystery:
        name = "search_jobs"
        description = "mystery"
        input_schema = {"type": "object", "properties": {}}
        # Explicit None classification must default-deny (#84).
        permission = None

        @staticmethod
        def invoke(arguments, *, context):
            return ToolResult(ok=True, data={})

    registry = ToolRegistry(
        {
            "search_jobs": Mystery(),
            "get_tw_stock_movers": builtin_tools()["get_tw_stock_movers"],
            "query_recent_runs": builtin_tools()["query_recent_runs"],
        },
        allowlist=["search_jobs", "get_tw_stock_movers", "query_recent_runs"],
    )
    result = registry.invoke("search_jobs", {}, context=ToolContext())
    assert result.ok is False
    assert result.metadata["code"] == "permission_denied"
    assert "default deny" in (result.error or "")


def test_disabled_side_effect_cannot_run() -> None:
    registry = build_tool_registry(include_telegram=True)
    policy = build_permission_policy(
        PermissionPolicyConfig(classes={"notify": "disabled", "read": "allow"})
    )
    result = registry.invoke(
        "send_telegram_message",
        {"text": "hi"},
        context=ToolContext(
            telegram_enabled=True,
            permission_policy=policy,
            send_telegram=lambda _: None,
        ),
    )
    assert result.ok is False
    assert result.metadata["code"] == "permission_denied"


def test_interactive_ask_requires_approval(tmp_path: Path) -> None:
    registry = build_tool_registry(include_telegram=True)
    approved: list[str] = []

    def approve(name: str, arguments: dict, summary: str) -> bool:
        approved.append(name)
        assert "sk-secret" not in summary
        assert "Send Telegram" in summary
        return True

    policy = build_permission_policy(
        PermissionPolicyConfig(
            classes={"notify": "ask", "read": "allow"},
            audit_log_path=str(tmp_path / "audit.jsonl"),
        ),
        interactive=True,
        approve=approve,
        secrets=("sk-secret",),
    )
    sent: list[str] = []
    result = registry.invoke(
        "send_telegram_message",
        {"text": "hello sk-secret"},
        context=ToolContext(
            telegram_enabled=True,
            permission_policy=policy,
            send_telegram=sent.append,
            secrets_to_redact=("sk-secret",),
        ),
    )
    assert result.ok is True
    assert approved == ["send_telegram_message"]
    assert sent == ["hello sk-secret"]
    audit = (tmp_path / "audit.jsonl").read_text(encoding="utf-8")
    assert "send_telegram_message" in audit
    assert "sk-secret" not in audit


def test_ask_denied_non_interactive_and_after_user_deny() -> None:
    registry = build_tool_registry(include_telegram=True)
    non_interactive = build_permission_policy(
        PermissionPolicyConfig(classes={"notify": "ask"}),
        interactive=False,
    )
    denied = registry.invoke(
        "send_telegram_message",
        {"text": "x"},
        context=ToolContext(
            telegram_enabled=True,
            permission_policy=non_interactive,
            send_telegram=lambda _: None,
        ),
    )
    assert denied.ok is False
    assert denied.metadata["code"] == "permission_ask_denied"

    policy = build_permission_policy(
        PermissionPolicyConfig(classes={"notify": "ask"}),
        interactive=True,
        approve=lambda *_: False,
    )
    user_denied = registry.invoke(
        "send_telegram_message",
        {"text": "x"},
        context=ToolContext(
            telegram_enabled=True,
            permission_policy=policy,
            send_telegram=lambda _: None,
        ),
    )
    assert user_denied.ok is False
    assert user_denied.metadata["code"] == "permission_ask_denied"

    # Retry after denial still requires a fresh approval (does not auto-allow).
    again = registry.invoke(
        "send_telegram_message",
        {"text": "retry"},
        context=ToolContext(
            telegram_enabled=True,
            permission_policy=policy,
            send_telegram=lambda _: None,
        ),
    )
    assert again.ok is False


def test_side_effect_limit_and_dangerous_capabilities() -> None:
    registry = build_tool_registry(include_telegram=True)
    policy = build_permission_policy(
        PermissionPolicyConfig(
            classes={"notify": "allow"},
            max_side_effect_actions=1,
        )
    )
    context = ToolContext(
        telegram_enabled=True,
        permission_policy=policy,
        send_telegram=lambda _: None,
    )
    first = registry.invoke(
        "send_telegram_message",
        {"text": "one"},
        context=context,
    )
    second = registry.invoke(
        "send_telegram_message",
        {"text": "two"},
        context=context,
    )
    assert first.ok is True
    assert second.ok is False
    assert "side-effect action limit" in (second.error or "")

    decision = policy.decide("shell", {})
    assert decision.allowed is False
    assert decision.action == PolicyAction.DISABLED


def test_approval_prompt_names_tool_and_side_effect() -> None:
    prompt = format_approval_prompt(
        "send_telegram_message",
        side_effect_summary="Send Telegram message (2 chars): 'hi'",
        permission=PermissionClass.NOTIFY,
    )
    assert "send_telegram_message" in prompt
    assert "NOTIFY" in prompt or "notify" in prompt
    assert "Send Telegram message" in prompt


def test_name_alone_does_not_classify_without_tool_object() -> None:
    policy = build_permission_policy(
        PermissionPolicyConfig(classes={"notify": "allow"})
    )
    # Builtin catalog must not grant notify solely from the tool name.
    assert policy.classify_tool("send_telegram_message") is None
    decision = policy.decide("send_telegram_message", {"text": "x"})
    assert decision.allowed is False


def test_config_policy_used_when_context_has_app_config(tmp_path: Path) -> None:
    from dataclasses import replace

    from app.core.config import load_config
    from app.setup import SetupOptions, run_setup

    run_setup(
        root=tmp_path,
        options=SetupOptions(provider="fake", non_interactive=True, force=True),
    )
    config = load_config(tmp_path / "config.yaml")
    config = replace(
        config,
        tools=replace(
            config.tools,
            permissions=replace(
                config.tools.permissions,
                classes={
                    "read": "allow",
                    "notify": "disabled",
                    "write": "disabled",
                    "execute": "disabled",
                },
            ),
        ),
    )
    registry = build_tool_registry(include_telegram=True)
    result = registry.invoke(
        "send_telegram_message",
        {"text": "hi"},
        context=ToolContext(
            config=config,
            telegram_enabled=True,
            send_telegram=lambda _: None,
        ),
    )
    assert result.ok is False
    assert result.metadata["code"] == "permission_denied"
