"""Legacy flag helpers preserved for backward-compatible CLI paths."""

from __future__ import annotations

import logging
import sys

from app.config import ConfigError, SecretError
from app.core import load_env_settings
from app.core.error_handling import notify_task_failure
from app.core.health import (
    HealthError,
    format_health_report,
    format_recent_signal_runs,
    load_recent_signal_runs,
    summarize_run_health,
)
from app.core.logging import get_logger, log_event
from app.notifiers import (
    TelegramNotifierError,
    build_default_test_message,
    build_telegram_notifier,
)
from app.tasks import build_task, list_tasks

DEFAULT_CONFIG_PATH = "config.yaml"
DEFAULT_RECENT_RUNS_LIMIT = 20
_CLI_LOGGER = get_logger("cli")

__all__ = [
    "DEFAULT_CONFIG_PATH",
    "DEFAULT_RECENT_RUNS_LIMIT",
    "ConfigError",
    "HealthError",
    "SecretError",
    "TelegramNotifierError",
    "build_task",
    "build_telegram_notifier",
    "list_tasks",
    "load_env_settings",
    "notify_task_failure",
    "print_health_report",
    "print_recent_runs",
    "run_eval_command",
    "run_langchain_smoke_command",
    "run_mcp_serve_command",
    "run_task_with_failure_alerts",
    "send_telegram_test_message",
    "summarize_run_health",
]


def print_health_report() -> None:
    """Print the operator health summary without contacting external services."""

    summary = summarize_run_health()
    print(format_health_report(summary))


def print_recent_runs(*, limit: int) -> None:
    """Print recent source runs without contacting external services."""

    if limit < 0:
        raise ValueError("--limit must be a non-negative integer")
    runs = load_recent_signal_runs(limit=limit)
    print(format_recent_signal_runs(runs))


def send_telegram_test_message(text: str) -> None:
    """Send a one-off Telegram test message from the CLI."""

    env = load_env_settings()
    notifier = build_telegram_notifier(env=env)
    message = text or build_default_test_message(
        signal_agent_env=env.signal_agent_env
    )
    notifier.send_message(message)
    print("telegram test message sent")


def run_langchain_smoke_command() -> int:
    """Run the optional LangChain adapter smoke command."""

    try:
        from app.integrations.langchain import run_langchain_smoke
    except ImportError as exc:
        print(exc, file=sys.stderr)
        return 1

    try:
        result = run_langchain_smoke()
    except ImportError as exc:
        print(exc, file=sys.stderr)
        return 1

    print("langchain tools:")
    for name in result["tool_names"]:
        print(f"- {name}")
    print(f"model_reply: {result['model_reply']}")
    print(f"langgraph_available: {result['langgraph_available']}")
    return 0


def run_eval_command(*, suite: str) -> int:
    """Run the offline evaluation suite (#66)."""

    from app.evaluation.runner import main as eval_main

    return eval_main(["--suite", suite])


def run_mcp_serve_command(
    *,
    host: str,
    port: int,
    transport: str,
    config_path: str = DEFAULT_CONFIG_PATH,
) -> int:
    """Serve allowlisted tools over a localhost MCP adapter (#67)."""

    try:
        from app.integrations.mcp import McpServerConfig, serve_mcp
        from app.tools import ToolContext, build_tool_registry
    except ImportError as exc:
        print(exc, file=sys.stderr)
        return 1

    try:
        from app.core import load_config
        from app.core.health import resolve_db_path
        from app.core.logging import configure_logging

        if transport == "stdio":
            configure_logging(stream=sys.stderr, force=True)

        app_config = load_config(config_path)
        env = load_env_settings()
        db_path = resolve_db_path()
        secrets = tuple(
            value
            for value in (
                env.openai_api_key,
                env.telegram_bot_token,
            )
            if value
        )
        context = ToolContext(
            config=app_config,
            env=env,
            db_path=db_path,
            telegram_enabled=False,
            secrets_to_redact=secrets,
        )
        mcp_config = McpServerConfig(host=host, port=port, transport=transport)
        registry = build_tool_registry(allowlist=list(mcp_config.allowlist))
        print(
            f"Starting MCP server transport={transport} host={host} port={port}",
            file=sys.stderr,
        )
        serve_mcp(config=mcp_config, registry=registry, context=context)
    except ImportError as exc:
        print(exc, file=sys.stderr)
        return 1
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 1
    return 0


def run_task_with_failure_alerts(name: str, *, config_path: str) -> None:
    """Run one task and send a best-effort alert if it fails."""

    task = build_task(name, config_path=config_path)

    try:
        task.run()
    except Exception as exc:
        try:
            notify_task_failure(
                task_name=task.name,
                config_path=config_path,
                error=exc,
            )
        except Exception as alert_exc:  # pragma: no cover - defensive guard
            log_event(
                _CLI_LOGGER,
                event="task_failure_alert_failed",
                message="Task failure alert raised unexpectedly",
                level=logging.ERROR,
                exc_info=alert_exc,
                task_name=task.name,
                config_path=config_path,
                original_error=str(exc),
                alert_error=str(alert_exc),
            )
        raise
