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
    setup.add_argument("--model", default="gpt-4o-mini")
    setup.add_argument("--llm-enabled", action="store_true")
    setup.add_argument("--telegram-enabled", action="store_true")
    setup.add_argument("--openai-api-key", default="")
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

    sub.add_parser("tasks", parents=[shared], help="List available tasks")
    sub.add_parser(
        "models",
        parents=[shared],
        help="List configured/supported LLM providers",
    )
    sub.add_parser("tools", parents=[shared], help="List allowlisted agent tools")

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
