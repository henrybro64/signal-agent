"""Interactive chat REPL (#83)."""

from __future__ import annotations

import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TextIO

from app.core.config import load_config
from app.core.health import resolve_db_path
from app.core.secrets import load_env_settings
from app.llm.models_cli import build_routed_provider
from app.runtime import paths as runtime_paths
from app.tools import ToolContext, ToolRegistry, build_tool_registry
from app.tools.permissions import (
    build_permission_policy,
    format_approval_prompt,
    permission_policy_config_from_tools,
)
from app.tools.registry import builtin_tools

from .loop import ChatBudgets, ChatCancelled, TurnResult, run_chat_turn
from .permissions import policy_approver
from .session import ChatSession, load_session, new_session, save_session

HELP_TEXT = """\
Commands:
  /help     Show this help
  /status   Show session counters
  /tools    List allowlisted tools
  /clear    Clear conversation (keeps session id)
  /exit     Leave chat
"""


@dataclass(slots=True)
class _ChatRuntime:
    base: Path
    llm: object
    registry: ToolRegistry
    context: ToolContext
    approve: Callable[[str, dict], bool] | None = None


def run_chat_repl(
    *,
    root: str | Path | None = None,
    config_path: str | Path | None = None,
    session_id: str | None = None,
    budgets: ChatBudgets | None = None,
    input_fn: Callable[[str], str] | None = None,
    output: TextIO | None = None,
    provider: object | None = None,
    max_messages: int | None = None,
) -> int:
    """Run the interactive chat loop until /exit or EOF."""

    out = output or sys.stdout
    read = input_fn or input
    runtime = _bootstrap_chat_runtime(
        root=root,
        config_path=config_path,
        provider=provider,
        input_fn=read,
        output=out,
    )
    session = _open_session(session_id, root=runtime.base, out=out)
    print("Type /help for commands. Ctrl-C cancels the current turn.", file=out)

    limits = budgets or ChatBudgets()
    messages_seen = 0
    while True:
        if max_messages is not None and messages_seen >= max_messages:
            break
        line = _read_user_line(read, out=out)
        if line is None:
            break
        if not line:
            continue
        messages_seen += 1

        if line.startswith("/"):
            action = _handle_slash(
                line,
                session=session,
                registry=runtime.registry,
                out=out,
            )
            if action == "exit":
                break
            continue

        result = _safe_chat_turn(
            session,
            line,
            runtime=runtime,
            budgets=limits,
            out=out,
            approve=runtime.approve,
        )
        if result is None:
            continue
        _print_turn_result(result, out=out)
        save_session(session, root=runtime.base)
    save_session(session, root=runtime.base)
    print(f"Session saved: {session.session_id}", file=out)
    return 0


def _bootstrap_chat_runtime(
    *,
    root: str | Path | None,
    config_path: str | Path | None,
    provider: object | None,
    input_fn: Callable[[str], str],
    output: TextIO,
) -> _ChatRuntime:
    base = runtime_paths.project_root(root)
    cfg_path = Path(config_path or runtime_paths.config_path(base))
    config = load_config(cfg_path)
    env = load_env_settings(runtime_paths.env_path(base))
    db_path = resolve_db_path(runtime_paths.db_path(base))
    llm = provider or build_routed_provider(
        config.llm,
        api_key=env.openai_api_key,
    )
    registry = build_tool_registry(
        allowlist=list(config.tools.allowlist),
        root=base,
        config_path=cfg_path,
    )
    secrets = tuple(
        value
        for value in (env.openai_api_key, env.telegram_bot_token, env.telegram_chat_id)
        if value
    )

    def _prompt_yes(prompt: str) -> bool:
        print(prompt, file=output)
        try:
            answer = input_fn("approve> ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            return False
        return answer in {"y", "yes"}

    policy_box: dict[str, Any] = {}

    def _policy_approve(name: str, _args: dict, summary: str) -> bool:
        permission = None
        active = policy_box.get("policy")
        if active is not None:
            permission = active.classify_tool(name, tool=builtin_tools().get(name))
        return _prompt_yes(
            format_approval_prompt(
                name,
                side_effect_summary=summary,
                permission=permission,
            )
        )

    policy = build_permission_policy(
        permission_policy_config_from_tools(
            config.tools.permissions,
            audit_log_path=str(base / "logs" / "permissions-audit.jsonl"),
        ),
        interactive=True,
        approve=_policy_approve,
        secrets=secrets,
        root=base,
    )
    policy_box["policy"] = policy
    context = ToolContext(
        config=config,
        env=env,
        db_path=db_path,
        telegram_enabled=bool(env.telegram_bot_token and env.telegram_chat_id),
        timeout_seconds=config.tools.timeout_seconds,
        secrets_to_redact=secrets,
        interactive=True,
        permission_policy=policy,
        root=base,
        config_path=cfg_path,
    )
    return _ChatRuntime(
        base=base,
        llm=llm,
        registry=registry,
        context=context,
        approve=policy_approver(policy, prompt_fn=_prompt_yes),
    )


def _open_session(
    session_id: str | None,
    *,
    root: Path,
    out: TextIO,
) -> ChatSession:
    if session_id:
        session = load_session(session_id, root=root)
        print(f"Resumed session {session.session_id}", file=out)
        return session
    session = new_session()
    print(f"Chat session {session.session_id}", file=out)
    return session


def _read_user_line(
    read: Callable[[str], str],
    *,
    out: TextIO,
) -> str | None:
    try:
        return read("you> ").strip()
    except EOFError:
        print("", file=out)
        return None
    except KeyboardInterrupt:
        print("\n(interrupted)", file=out)
        return ""


def _safe_chat_turn(
    session: ChatSession,
    line: str,
    *,
    runtime: _ChatRuntime,
    budgets: ChatBudgets,
    out: TextIO,
    approve: Callable[[str, dict], bool] | None = None,
) -> TurnResult | None:
    try:
        return run_chat_turn(
            session,
            line,
            provider=runtime.llm,  # type: ignore[arg-type]
            registry=runtime.registry,
            context=runtime.context,
            budgets=budgets,
            approve=approve,
            root=runtime.base,
        )
    except ChatCancelled:
        print("assistant> (cancelled)", file=out)
        return None
    except KeyboardInterrupt:
        print("\nassistant> (cancelled)", file=out)
        return None
    except Exception as exc:  # noqa: BLE001 - keep REPL alive
        print(f"assistant> error: {type(exc).__name__}: {exc}", file=out)
        return None


def _print_turn_result(result: TurnResult, *, out: TextIO) -> None:
    for event in result.events:
        mark = "ok" if event.ok else "fail"
        print(f"  · {event.kind}:{event.name} [{mark}] {event.detail}", file=out)
    if result.trace_id:
        print(f"  · trace:{result.trace_id}", file=out)
    print(f"assistant> {result.reply}", file=out)


def _handle_slash(
    line: str,
    *,
    session: ChatSession,
    registry: object,
    out: TextIO,
) -> str | None:
    command = line.split()[0].lower()
    if command in {"/exit", "/quit"}:
        return "exit"
    if command == "/help":
        print(HELP_TEXT, file=out)
        return None
    if command == "/status":
        print(
            "session="
            f"{session.session_id} turns={session.turns} "
            f"tool_calls={session.tool_calls} llm_calls={session.llm_calls}",
            file=out,
        )
        return None
    if command == "/tools":
        names = sorted(getattr(registry, "allowlist", []))
        print("tools: " + (", ".join(names) if names else "(none)"), file=out)
        return None
    if command == "/clear":
        session.messages.clear()
        session.turns = 0
        session.tool_calls = 0
        session.llm_calls = 0
        print("Conversation cleared.", file=out)
        return None
    print(f"Unknown command: {command}. Try /help.", file=out)
    return None
