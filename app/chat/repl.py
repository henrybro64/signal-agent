"""Interactive chat REPL (#83)."""

from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path
from typing import TextIO

from app.core.config import load_config
from app.core.health import resolve_db_path
from app.core.secrets import load_env_settings
from app.llm import create_llm_provider
from app.runtime import paths as runtime_paths
from app.tools import ToolContext, build_tool_registry

from .loop import ChatBudgets, ChatCancelled, run_chat_turn
from .session import ChatSession, load_session, new_session, save_session

HELP_TEXT = """\
Commands:
  /help     Show this help
  /status   Show session counters
  /tools    List allowlisted tools
  /clear    Clear conversation (keeps session id)
  /exit     Leave chat
"""


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
    base = runtime_paths.project_root(root)
    cfg_path = Path(config_path or runtime_paths.config_path(base))
    config = load_config(cfg_path)
    env = load_env_settings(runtime_paths.env_path(base))
    db_path = resolve_db_path(runtime_paths.db_path(base))

    llm = provider or create_llm_provider(
        provider=config.llm.provider,
        api_key=env.openai_api_key,
        model=config.llm.model,
        timeout_seconds=config.llm.timeout_seconds,
        temperature=config.llm.temperature,
    )
    registry = build_tool_registry(allowlist=list(config.tools.allowlist))
    secrets = tuple(
        value
        for value in (env.openai_api_key, env.telegram_bot_token, env.telegram_chat_id)
        if value
    )
    context = ToolContext(
        config=config,
        env=env,
        db_path=db_path,
        telegram_enabled=False,
        timeout_seconds=config.tools.timeout_seconds,
        secrets_to_redact=secrets,
    )

    if session_id:
        session = load_session(session_id, root=base)
        print(f"Resumed session {session.session_id}", file=out)
    else:
        session = new_session()
        print(f"Chat session {session.session_id}", file=out)
    print("Type /help for commands. Ctrl-C cancels the current turn.", file=out)

    limits = budgets or ChatBudgets()
    messages_seen = 0
    while True:
        if max_messages is not None and messages_seen >= max_messages:
            break
        try:
            line = read("you> ").strip()
        except EOFError:
            print("", file=out)
            break
        except KeyboardInterrupt:
            print("\n(interrupted)", file=out)
            continue
        if not line:
            continue
        messages_seen += 1

        if line.startswith("/"):
            code = _handle_slash(line, session=session, registry=registry, out=out)
            if code == "exit":
                break
            continue

        try:
            result = run_chat_turn(
                session,
                line,
                provider=llm,  # type: ignore[arg-type]
                registry=registry,
                context=context,
                budgets=limits,
                root=base,
            )
        except ChatCancelled:
            print("assistant> (cancelled)", file=out)
            continue
        except KeyboardInterrupt:
            print("\nassistant> (cancelled)", file=out)
            continue
        except Exception as exc:  # noqa: BLE001 - keep REPL alive
            print(f"assistant> error: {type(exc).__name__}: {exc}", file=out)
            continue

        for event in result.events:
            mark = "ok" if event.ok else "fail"
            print(f"  · {event.kind}:{event.name} [{mark}] {event.detail}", file=out)
        if result.trace_id:
            print(f"  · trace:{result.trace_id}", file=out)
        print(f"assistant> {result.reply}", file=out)
        save_session(session, root=base)
    save_session(session, root=base)
    print(f"Session saved: {session.session_id}", file=out)
    return 0


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
