"""Unified argparse for OpenClaw-like subcommands + legacy flags (#81/#82)."""

from __future__ import annotations

import argparse
from typing import Any

from app.cli.legacy import DEFAULT_CONFIG_PATH, DEFAULT_RECENT_RUNS_LIMIT
from app.runtime import paths as runtime_paths


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="signal-agent",
        description=(
            "Local personal signal agent: setup, runtime lifecycle, "
            "tasks, chat, and diagnostics."
        ),
    )

    # Shared options use SUPPRESS on subparsers so a value set before the
    # subcommand (e.g. `signal-agent --root /tmp setup`) is not overwritten
    # by the subparser default.
    shared = argparse.ArgumentParser(add_help=False)
    _add_path_options(shared, with_defaults=False)
    _add_path_options(parser, with_defaults=True)

    sub = parser.add_subparsers(dest="command")

    setup = sub.add_parser(
        "setup",
        parents=[shared],
        help="Interactive first-run setup wizard",
    )
    setup.add_argument("--non-interactive", action="store_true")
    setup.add_argument("--provider", default="fake")
    setup.add_argument("--model", default="")
    setup.add_argument("--llm-enabled", action="store_true")
    setup.add_argument("--telegram-enabled", action="store_true")
    setup.add_argument("--openai-api-key", default="")
    setup.add_argument("--anthropic-api-key", default="")
    setup.add_argument("--telegram-bot-token", default="")
    setup.add_argument("--telegram-chat-id", default="")
    setup.add_argument("--force", action="store_true")

    config_p = sub.add_parser("config", parents=[shared], help="Configuration helpers")
    config_sub = config_p.add_subparsers(dest="config_command")
    config_sub.add_parser(
        "show",
        parents=[shared],
        help="Show config with secrets redacted",
    )

    sub.add_parser(
        "doctor",
        parents=[shared],
        help="Diagnose local dependencies and configuration",
    )

    sub.add_parser(
        "start",
        parents=[shared],
        help="Start the long-running local runtime",
    )
    sub.add_parser("stop", parents=[shared], help="Stop the local runtime")
    sub.add_parser(
        "restart",
        parents=[shared],
        help="Restart the local runtime",
    )
    sub.add_parser(
        "status",
        parents=[shared],
        help="Show runtime and readiness status",
    )

    chat = sub.add_parser(
        "chat",
        parents=[shared],
        help="Interactive bounded agent chat",
    )
    chat.add_argument("--session", default="", help="Resume a saved session id")
    chat.add_argument(
        "--max-turns",
        type=int,
        default=8,
        help="Max user turns in this session before /clear is required",
    )
    chat.add_argument(
        "--max-steps",
        type=int,
        default=8,
        help="Max plan-act steps inside one user turn",
    )
    chat.add_argument("--max-tool-calls", type=int, default=6)
    chat.add_argument(
        "--max-estimated-tokens",
        type=int,
        default=0,
        help="Optional estimated-token budget for one turn (0 disables)",
    )

    run_p = sub.add_parser("run", parents=[shared], help="Run one named task")
    run_p.add_argument("task_name", help="Task name (see `signal-agent tasks`)")

    agent_run = sub.add_parser(
        "agent-run",
        parents=[shared],
        help="Bounded coding analysis through the configured runtime",
    )
    agent_run.add_argument(
        "goal", help="Goal and selected code context; no automatic file access"
    )
    agent_run.add_argument(
        "--action",
        choices=["analyze", "summarize", "plan", "propose_patch"],
        default="analyze",
    )
    agent_run.add_argument("--timeout", type=float, default=30.0)

    sub.add_parser("tasks", parents=[shared], help="List available tasks")

    models_p = sub.add_parser(
        "models",
        parents=[shared],
        help="Inspect/set LLM models and run health checks",
    )
    models_sub = models_p.add_subparsers(dest="models_command")
    models_sub.add_parser(
        "status",
        parents=[shared],
        help="Show default model and fallbacks",
    )
    models_set = models_sub.add_parser(
        "set",
        parents=[shared],
        help="Set default provider/model (provider/model)",
    )
    models_set.add_argument("model_id", help="e.g. fake/gpt-4o-mini")
    models_fb = models_sub.add_parser(
        "fallback",
        parents=[shared],
        help="Manage fallback models",
    )
    models_fb_sub = models_fb.add_subparsers(dest="models_fallback_command")
    models_fb_add = models_fb_sub.add_parser(
        "add",
        parents=[shared],
        help="Add a fallback provider/model",
    )
    models_fb_add.add_argument("model_id", help="e.g. fake/backup-model")
    models_sub.add_parser(
        "test",
        parents=[shared],
        help="Run a tiny provider health request",
    )

    sub.add_parser("tools", parents=[shared], help="List allowlisted agent tools")

    sub.add_parser(
        "schedules",
        parents=[shared],
        help="List persistent schedules",
    )
    schedule_p = sub.add_parser(
        "schedule",
        parents=[shared],
        help="Manage persistent schedules",
    )
    schedule_sub = schedule_p.add_subparsers(dest="schedule_command")
    schedule_add = schedule_sub.add_parser(
        "add",
        parents=[shared],
        help="Add a recurring or one-shot schedule",
    )
    schedule_add.add_argument("--name", required=True)
    schedule_add.add_argument("--task", required=True, dest="schedule_task")
    schedule_add.add_argument(
        "--timezone",
        default="Asia/Taipei",
        help="IANA timezone (required explicitly; default Asia/Taipei)",
    )
    schedule_add.add_argument(
        "--cron",
        default="",
        help="Cron (5-field) or every:<minutes>",
    )
    schedule_add.add_argument(
        "--run-at",
        default="",
        help="One-shot ISO-8601 timestamp with timezone",
    )
    schedule_add.add_argument("--allow-overlap", action="store_true")
    schedule_add.add_argument("--dry-run", action="store_true")
    schedule_add.add_argument("--max-retries", type=int, default=2)
    schedule_add.add_argument("--backoff-seconds", type=float, default=30.0)
    schedule_enable = schedule_sub.add_parser(
        "enable",
        parents=[shared],
        help="Enable a schedule",
    )
    schedule_enable.add_argument("schedule_id")
    schedule_disable = schedule_sub.add_parser(
        "disable",
        parents=[shared],
        help="Disable a schedule",
    )
    schedule_disable.add_argument("schedule_id")
    schedule_run = schedule_sub.add_parser(
        "run",
        parents=[shared],
        help="Run a schedule immediately",
    )
    schedule_run.add_argument("schedule_id")
    schedule_run.add_argument("--dry-run", action="store_true")
    schedule_history = schedule_sub.add_parser(
        "history",
        parents=[shared],
        help="Show recent runs for a schedule",
    )
    schedule_history.add_argument("schedule_id")
    schedule_history.add_argument("--limit", type=int, default=20)

    logs = sub.add_parser(
        "logs",
        parents=[shared],
        help="Show recent runtime logs (secrets redacted)",
    )
    logs.add_argument("--lines", type=int, default=50)
    logs.add_argument(
        "--follow",
        action="store_true",
        help="Follow log output (Ctrl-C to stop)",
    )

    browser_p = sub.add_parser(
        "browser",
        parents=[shared],
        help="Optional isolated browser service",
    )
    browser_sub = browser_p.add_subparsers(dest="browser_command")
    browser_sub.add_parser(
        "status",
        parents=[shared],
        help="Show browser readiness and profile",
    )
    browser_sub.add_parser(
        "start",
        parents=[shared],
        help="Start/prepare the isolated browser profile",
    )
    browser_sub.add_parser(
        "stop",
        parents=[shared],
        help="Stop the browser service",
    )
    browser_test = browser_sub.add_parser(
        "test",
        parents=[shared],
        help="Open a local fixture page through the browser backend",
    )
    browser_test.add_argument(
        "--url",
        default="",
        help="Optional URL (defaults to offline fixture)",
    )

    service_p = sub.add_parser(
        "service",
        parents=[shared],
        help="Installable systemd --user background service",
    )
    service_sub = service_p.add_subparsers(dest="service_command")
    service_install = service_sub.add_parser(
        "install",
        parents=[shared],
        help="Install (and optionally enable) the user service",
    )
    service_install.add_argument(
        "--xdg",
        action="store_true",
        help="Use XDG config/data/state paths instead of the repo checkout",
    )
    service_install.add_argument(
        "--no-enable",
        action="store_true",
        help="Write the unit file only (do not systemctl enable)",
    )
    service_sub.add_parser(
        "status",
        parents=[shared],
        help="Show systemd user-service and runtime status",
    )
    service_sub.add_parser(
        "uninstall",
        parents=[shared],
        help="Disable and remove the user service unit",
    )

    sub.add_parser(
        "skills",
        parents=[shared],
        help="List built-in skills and enablement",
    )
    skill_p = sub.add_parser(
        "skill",
        parents=[shared],
        help="Inspect or enable/disable a built-in skill",
    )
    skill_sub = skill_p.add_subparsers(dest="skill_command")
    skill_info = skill_sub.add_parser(
        "info",
        parents=[shared],
        help="Show one skill manifest",
    )
    skill_info.add_argument("skill_name")
    skill_enable = skill_sub.add_parser(
        "enable",
        parents=[shared],
        help="Enable a built-in skill",
    )
    skill_enable.add_argument("skill_name")
    skill_disable = skill_sub.add_parser(
        "disable",
        parents=[shared],
        help="Disable a built-in skill",
    )
    skill_disable.add_argument("skill_name")

    # Legacy flags kept for backward compatibility; prefer subcommands.
    parser.add_argument(
        "--task",
        help="(legacy) Run one named task. Prefer: signal-agent run <task>",
    )
    parser.add_argument(
        "--list-tasks",
        action="store_true",
        help="(legacy) List tasks. Prefer: signal-agent tasks",
    )
    parser.add_argument(
        "--health",
        action="store_true",
        help="(legacy) Print scheduled-run health summary.",
    )
    parser.add_argument(
        "--recent-runs",
        action="store_true",
        help="(legacy) Print recent source collection runs.",
    )
    parser.add_argument(
        "--telegram-test",
        nargs="?",
        const="",
        metavar="TEXT",
        help="(legacy) Send a Telegram test message.",
    )
    parser.add_argument(
        "--langchain-smoke",
        action="store_true",
        help="(legacy) LangChain adapter smoke (requires .[langchain]).",
    )
    parser.add_argument(
        "--eval",
        nargs="?",
        const="core",
        metavar="SUITE",
        help="(legacy) Run offline evaluation suite.",
    )
    parser.add_argument(
        "--mcp-serve",
        action="store_true",
        help="(legacy) Serve allowlisted tools over localhost MCP.",
    )
    parser.add_argument("--mcp-host", default="127.0.0.1", help="MCP bind host")
    parser.add_argument("--mcp-port", type=int, default=8765, help="MCP SSE port")
    parser.add_argument(
        "--mcp-transport",
        choices=("stdio", "sse"),
        default="stdio",
        help="MCP transport",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=DEFAULT_RECENT_RUNS_LIMIT,
        help=f"Max rows for --recent-runs. Default: {DEFAULT_RECENT_RUNS_LIMIT}.",
    )
    return parser


def resolve_runtime_args(args: Any) -> Any:
    """Normalize ``--root`` / ``--config`` after argparse.

    When ``--config`` is omitted, resolve it under the selected root so chat,
    doctor, and status operate on one installation. For the default root (``.``)
    keep the historical relative ``config.yaml`` path for legacy CLI messages.
    """

    root = getattr(args, "root", None) or "."
    config = getattr(args, "config", None)
    if config is None:
        if root == ".":
            config = DEFAULT_CONFIG_PATH
        else:
            config = str(runtime_paths.config_path(root))
    args.root = root
    args.config = config
    return args


def _add_path_options(
    parser: argparse.ArgumentParser,
    *,
    with_defaults: bool,
) -> None:
    # Root parser: default root=".", config=None (resolved under root later).
    # Subparsers: SUPPRESS so pre-subcommand values are not overwritten.
    parser.add_argument(
        "--config",
        default=None if with_defaults else argparse.SUPPRESS,
        help="Path to the YAML config file. Default: <root>/config.yaml",
    )
    parser.add_argument(
        "--root",
        default="." if with_defaults else argparse.SUPPRESS,
        help="Project root for local data/config/runtime files. Default: .",
    )
