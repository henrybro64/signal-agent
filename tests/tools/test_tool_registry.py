"""Unit tests for the framework-neutral tool registry (#62)."""

from __future__ import annotations

import time

import pytest

from app.tools import (
    DEFAULT_TOOL_ALLOWLIST,
    ToolContext,
    ToolError,
    ToolRegistry,
    ToolResult,
    build_tool_registry,
    builtin_tools,
)
from app.tools.redaction import redact_mapping, safe_log_fields


def test_builtin_tools_have_stable_names_and_schemas() -> None:
    tools = builtin_tools()
    assert set(DEFAULT_TOOL_ALLOWLIST) <= set(tools)
    assert "send_telegram_message" in tools
    for name, tool in tools.items():
        assert tool.name == name
        assert tool.description
        assert tool.input_schema["type"] == "object"
        assert "api_key" not in tool.description.lower()
        assert "token" not in tool.description.lower()


def test_registry_rejects_unknown_and_disabled_tools() -> None:
    registry = build_tool_registry()
    with pytest.raises(ToolError, match="not allowlisted"):
        registry.get("send_telegram_message")
    with pytest.raises(ToolError, match="Unknown tools"):
        ToolRegistry(allowlist=["not_a_real_tool"])


def test_search_jobs_success_and_validation_via_injected_fetch() -> None:
    registry = build_tool_registry()
    context = ToolContext(
        fetch_jobs=lambda **_: [
            {"title": "Cloud SDET", "source": "dummy", "item_type": "job"}
        ],
        timeout_seconds=5,
    )
    result = registry.invoke("search_jobs", {"limit": 5}, context=context)
    assert result.ok is True
    assert result.data["count"] == 1
    assert result.data["items"][0]["title"] == "Cloud SDET"

    bad = registry.invoke("search_jobs", {"limit": 0}, context=context)
    assert bad.ok is False
    assert bad.metadata["code"] == "invalid_argument"

    unknown = registry.invoke(
        "search_jobs",
        {"limit": 1, "unexpected": True},
        context=context,
    )
    assert unknown.ok is False
    assert unknown.metadata["code"] == "invalid_argument"


def test_query_recent_runs_injected_defaults_to_task_kind() -> None:
    registry = build_tool_registry()
    context = ToolContext(
        query_runs=lambda **kwargs: [
            {
                "id": 1,
                "task_name": "job_search",
                "status": "success",
                "run_kind": kwargs.get("run_kind"),
            }
        ]
    )
    result = registry.invoke("query_recent_runs", {"limit": 1}, context=context)
    assert result.ok is True
    assert result.data["run_kind"] == "task"
    assert result.data["runs"][0]["task_name"] == "job_search"


def test_get_tw_stock_movers_injected() -> None:
    registry = build_tool_registry()
    context = ToolContext(
        fetch_movers=lambda **_: [
            {"title": "2330 台積電", "source": "twse", "item_type": "stock"}
        ]
    )
    result = registry.invoke("get_tw_stock_movers", {}, context=context)
    assert result.ok is True
    assert result.data["count"] == 1


def test_telegram_tool_disabled_by_default_and_redacts_secrets() -> None:
    registry = build_tool_registry(include_telegram=True)
    disabled = registry.invoke(
        "send_telegram_message",
        {"text": "hello"},
        context=ToolContext(telegram_enabled=False),
    )
    assert disabled.ok is False
    assert disabled.metadata["code"] == "tool_disabled"

    sent: list[str] = []
    enabled = registry.invoke(
        "send_telegram_message",
        {"text": "hello sk-secret-value"},
        context=ToolContext(
            telegram_enabled=True,
            send_telegram=sent.append,
            secrets_to_redact=("sk-secret-value",),
        ),
    )
    assert enabled.ok is True
    assert sent == ["hello sk-secret-value"]

    redacted = redact_mapping(
        {"bot_token": "123:abc", "text": "x sk-secret-value"},
        secrets=("sk-secret-value",),
    )
    assert redacted["bot_token"] == "***"
    assert "sk-secret-value" not in redacted["text"]


def test_registry_timeout_returns_promptly() -> None:
    class SlowTool:
        name = "search_jobs"
        description = "slow"
        input_schema = {"type": "object", "properties": {}}

        def invoke(self, arguments, *, context):
            time.sleep(0.3)
            return ToolResult(ok=True, data={"count": 0, "items": []})

    class BoomTool:
        name = "query_recent_runs"
        description = "boom"
        input_schema = {"type": "object", "properties": {}}

        def invoke(self, arguments, *, context):
            raise RuntimeError("network down token=abc")

    registry = ToolRegistry(
        {
            "search_jobs": SlowTool(),
            "query_recent_runs": BoomTool(),
            "get_tw_stock_movers": builtin_tools()["get_tw_stock_movers"],
        },
        allowlist=["search_jobs", "query_recent_runs", "get_tw_stock_movers"],
    )
    started = time.perf_counter()
    timed_out = registry.invoke(
        "search_jobs",
        {},
        context=ToolContext(timeout_seconds=0.05),
    )
    elapsed = time.perf_counter() - started
    assert timed_out.ok is False
    assert timed_out.metadata["code"] == "timeout"
    assert elapsed < 0.2
    assert timed_out.metadata["duration_ms"] < 200

    failed = registry.invoke(
        "query_recent_runs",
        {},
        context=ToolContext(secrets_to_redact=("token=abc",)),
    )
    assert failed.ok is False
    assert failed.metadata["code"] == "provider_error"
    assert "token=abc" not in (failed.error or "")


def test_safe_log_fields_omit_secret_values() -> None:
    fields = safe_log_fields(
        tool_name="search_jobs",
        status="ok",
        duration_ms=12,
        arguments={"api_key": "sk-live", "limit": 2},
        result={"ok": True, "data": {"count": 2, "items": [{}, {}]}},
        secrets=("sk-live",),
    )
    assert fields["tool_name"] == "search_jobs"
    assert fields["result_count"] == 2
    assert "sk-live" not in str(fields)


def test_timeout_sets_cancel_event() -> None:
    import threading

    seen: list[bool] = []

    class SlowTool:
        name = "search_jobs"
        description = "slow"
        input_schema = {"type": "object", "properties": {}}

        def invoke(self, arguments, *, context):
            time.sleep(0.25)
            seen.append(bool(context.cancel_event and context.cancel_event.is_set()))
            return ToolResult(ok=True, data={"count": 0, "items": []})

    registry = ToolRegistry(
        {
            "search_jobs": SlowTool(),
            "query_recent_runs": builtin_tools()["query_recent_runs"],
            "get_tw_stock_movers": builtin_tools()["get_tw_stock_movers"],
        },
        allowlist=["search_jobs", "query_recent_runs", "get_tw_stock_movers"],
    )
    cancel = threading.Event()
    result = registry.invoke(
        "search_jobs",
        {},
        context=ToolContext(timeout_seconds=0.05, cancel_event=cancel),
    )
    assert result.ok is False
    assert result.metadata["code"] == "timeout"
    assert cancel.is_set()
    # Give the daemon worker a moment to observe cancellation.
    time.sleep(0.3)
    assert seen == [True]


def test_stock_movers_ranks_by_change_percent(monkeypatch: pytest.MonkeyPatch) -> None:
    from datetime import UTC, datetime

    from app.core.models import SignalItem
    from app.tools.stock_tools import GetTwStockMoversTool

    items = [
        SignalItem(
            source="twse",
            item_type="stock",
            title="low",
            observed_at=datetime(2026, 7, 1, tzinfo=UTC),
            metadata={"change_percent": 0.5, "market": "twse", "stock_code": "1"},
        ),
        SignalItem(
            source="tpex",
            item_type="stock",
            title="high",
            observed_at=datetime(2026, 7, 1, tzinfo=UTC),
            metadata={"change_percent": 9.5, "market": "tpex", "stock_code": "2"},
        ),
        SignalItem(
            source="twse",
            item_type="stock",
            title="down",
            observed_at=datetime(2026, 7, 1, tzinfo=UTC),
            metadata={"change_percent": -8.0, "market": "twse", "stock_code": "3"},
        ),
    ]

    monkeypatch.setattr(
        "app.tools.stock_tools.build_tw_stock_mover_sources",
        lambda config: ["fake"],
    )
    monkeypatch.setattr(
        "app.tools.stock_tools.fetch_from_sources",
        lambda sources, on_error=None: items,
    )

    from app.core.config import load_config

    result = GetTwStockMoversTool().invoke(
        {"limit": 2},
        context=ToolContext(config=load_config("config.yaml")),
    )
    assert result.ok is True
    titles = [row["title"] for row in result.data["items"]]
    assert titles[0] == "high"
    assert "low" not in titles
