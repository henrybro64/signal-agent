"""Tests for interactive chat loop (#83)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.chat import (
    ChatBudgets,
    ChatCancelled,
    default_approver,
    load_session,
    new_session,
    requires_approval,
    run_chat_repl,
    run_chat_turn,
    save_session,
)
from app.llm import FakeLLMProvider, LLMProviderError
from app.setup import SetupOptions, run_setup
from app.tools import ToolContext, ToolRegistry, build_tool_registry, builtin_tools


def _context(tmp_path: Path, jobs: list[dict] | None = None) -> ToolContext:
    return ToolContext(
        fetch_jobs=lambda **_: jobs
        or [
            {
                "source": "dummy",
                "item_type": "job",
                "title": "Cloud SDET",
                "observed_at": "2026-07-01T00:00:00+00:00",
                "content": "Python automation",
                "metadata": {"company": "Example"},
            }
        ],
        query_runs=lambda **_: [{"id": 1, "status": "success"}],
        telegram_enabled=False,
        db_path=tmp_path / "signals.sqlite3",
        secrets_to_redact=("sk-chat-secret",),
    )


def test_chat_normal_and_tool_flow(tmp_path: Path) -> None:
    provider = FakeLLMProvider(
        responses=[
            '{"type":"tool","name":"query_recent_runs","arguments":{"limit":1}}',
            '{"type":"respond","message":"Found one recent run."}',
        ],
        validate_structured_json=False,
    )
    session = new_session()
    result = run_chat_turn(
        session,
        "show recent runs",
        provider=provider,
        registry=build_tool_registry(),
        context=_context(tmp_path),
        root=tmp_path,
    )
    assert "recent run" in result.reply.lower() or "Found" in result.reply
    assert any(
        event.kind == "tool" and event.name == "query_recent_runs"
        for event in result.events
    )
    assert session.tool_calls >= 1
    assert result.trace_id
    assert result.trace_path
    payload = json.loads(Path(result.trace_path).read_text(encoding="utf-8"))
    assert payload["suite"] == "chat"
    assert payload["events"]


def test_chat_multi_tool_flow(tmp_path: Path) -> None:
    provider = FakeLLMProvider(
        responses=[
            '{"type":"tool","name":"query_recent_runs","arguments":{"limit":1}}',
            '{"type":"tool","name":"search_jobs","arguments":{"limit":1}}',
            '{"type":"respond","message":"Used two tools."}',
        ],
        validate_structured_json=False,
    )
    result = run_chat_turn(
        new_session(),
        "query then search",
        provider=provider,
        registry=build_tool_registry(),
        context=_context(tmp_path),
        root=tmp_path,
        budgets=ChatBudgets(
            max_session_turns=8,
            max_steps_per_turn=6,
            max_tool_calls=4,
            max_llm_calls=6,
        ),
    )
    tool_names = [event.name for event in result.events if event.kind == "tool"]
    assert "query_recent_runs" in tool_names
    assert "search_jobs" in tool_names


def test_chat_workflow_job_research(tmp_path: Path) -> None:
    provider = FakeLLMProvider(
        responses=[
            '{"type":"workflow","name":"job_research"}',
            '{"type":"respond","message":"Research complete."}',
        ],
        validate_structured_json=False,
    )
    session = new_session()
    result = run_chat_turn(
        session,
        "run job research",
        provider=provider,
        registry=build_tool_registry(),
        context=_context(tmp_path),
        budgets=ChatBudgets(
            max_session_turns=6,
            max_steps_per_turn=6,
            max_tool_calls=4,
            max_llm_calls=6,
        ),
        root=tmp_path,
    )
    assert result.reply
    assert any(event.kind == "workflow" for event in result.events)


def test_chat_rejects_non_allowlisted_tool(tmp_path: Path) -> None:
    provider = FakeLLMProvider(
        responses=[
            '{"type":"tool","name":"search_jobs","arguments":{"limit":1}}',
            '{"type":"respond","message":"search_jobs not allowed."}',
        ],
        validate_structured_json=False,
    )
    registry = ToolRegistry(builtin_tools(), allowlist=["query_recent_runs"])
    result = run_chat_turn(
        new_session(),
        "search jobs",
        provider=provider,
        registry=registry,
        context=_context(tmp_path),
        root=tmp_path,
    )
    assert any(
        event.name == "search_jobs" and not event.ok and "allowlisted" in event.detail
        for event in result.events
    )


def test_chat_approval_denied_for_side_effect_tool(tmp_path: Path) -> None:
    provider = FakeLLMProvider(
        responses=[
            '{"type":"tool","name":"send_telegram_message","arguments":{"text":"hi"}}',
            '{"type":"respond","message":"Telegram blocked by policy."}',
        ],
        validate_structured_json=False,
    )
    registry = ToolRegistry(
        builtin_tools(),
        allowlist=["query_recent_runs", "send_telegram_message"],
    )
    result = run_chat_turn(
        new_session(),
        "message me on telegram",
        provider=provider,
        registry=registry,
        context=_context(tmp_path),
        approve=default_approver,
        root=tmp_path,
    )
    assert any(
        event.name == "send_telegram_message"
        and not event.ok
        and event.detail == "approval denied"
        for event in result.events
    )
    assert requires_approval("send_telegram_message")


def test_chat_step_limit(tmp_path: Path) -> None:
    provider = FakeLLMProvider(
        responses=[
            '{"type":"tool","name":"query_recent_runs","arguments":{"limit":1}}',
            '{"type":"tool","name":"query_recent_runs","arguments":{"limit":1}}',
            '{"type":"tool","name":"query_recent_runs","arguments":{"limit":1}}',
        ],
        validate_structured_json=False,
    )
    session = new_session()
    result = run_chat_turn(
        session,
        "keep querying",
        provider=provider,
        registry=build_tool_registry(),
        context=_context(tmp_path),
        budgets=ChatBudgets(
            max_session_turns=10,
            max_steps_per_turn=5,
            max_tool_calls=1,
            max_llm_calls=5,
        ),
        root=tmp_path,
    )
    assert result.stopped_reason == "max_tool_calls"
    assert session.tool_calls <= 1
    assert "budget" in result.reply.lower() or "limit" in result.reply.lower()


def test_chat_cancellation(tmp_path: Path) -> None:
    provider = FakeLLMProvider(
        responses=['{"type":"respond","message":"hi"}'],
        validate_structured_json=False,
    )
    session = new_session()
    with pytest.raises(ChatCancelled):
        run_chat_turn(
            session,
            "hello",
            provider=provider,
            registry=build_tool_registry(),
            context=_context(tmp_path),
            cancel_check=lambda: True,
            root=tmp_path,
        )


def test_chat_llm_failure_keeps_control(tmp_path: Path) -> None:
    provider = FakeLLMProvider(
        responses=[LLMProviderError("boom")],
        validate_structured_json=False,
    )
    session = new_session()
    result = run_chat_turn(
        session,
        "hello",
        provider=provider,
        registry=build_tool_registry(),
        context=_context(tmp_path),
        root=tmp_path,
    )
    assert "failed" in result.reply.lower() or "doctor" in result.reply.lower()


def test_chat_session_resume(tmp_path: Path) -> None:
    from app.chat import ChatMessage

    session = new_session()
    session.messages.append(ChatMessage(role="user", content="hi"))
    session.turns = 1
    path = save_session(session, root=tmp_path)
    assert path.is_file()
    loaded = load_session(session.session_id, root=tmp_path)
    assert loaded.turns == 1
    assert loaded.messages[0].content == "hi"


def test_chat_repl_slash_and_exit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_setup(
        root=tmp_path,
        options=SetupOptions(provider="fake", non_interactive=True, force=True),
    )
    monkeypatch.chdir(tmp_path)
    answers = iter(["/help", "/tools", "/status", "/clear", "/exit"])

    class FakeProvider(FakeLLMProvider):
        pass

    code = run_chat_repl(
        root=tmp_path,
        config_path=tmp_path / "config.yaml",
        input_fn=lambda _prompt: next(answers, "/exit"),
        provider=FakeProvider(validate_structured_json=False),
        max_messages=10,
    )
    assert code == 0


def test_chat_repl_session_resume_end_to_end(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_setup(
        root=tmp_path,
        options=SetupOptions(provider="fake", non_interactive=True, force=True),
    )
    monkeypatch.chdir(tmp_path)
    first = new_session()
    first.turns = 1
    save_session(first, root=tmp_path)
    answers = iter(["/status", "/exit"])
    code = run_chat_repl(
        root=tmp_path,
        config_path=tmp_path / "config.yaml",
        session_id=first.session_id,
        input_fn=lambda _prompt: next(answers, "/exit"),
        provider=FakeLLMProvider(validate_structured_json=False),
        max_messages=5,
    )
    assert code == 0
