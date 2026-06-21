"""Command-line entrypoint for signal-agent."""

from __future__ import annotations

import argparse
import sys

from app.config import ConfigError, SecretError
from app.core import load_env_settings
from app.notifiers import (
    TelegramNotifierError,
    build_default_test_message,
    build_telegram_notifier,
)
from app.tasks import list_tasks, run_task

DEFAULT_CONFIG_PATH = "config.yaml"


def build_parser() -> argparse.ArgumentParser:
    """Build the bootstrap CLI parser."""

    parser = argparse.ArgumentParser(
        prog="signal-agent",
        description=(
            "Run the bootstrap signal-agent CLI for local development checks."
        ),
    )
    action_group = parser.add_mutually_exclusive_group()
    action_group.add_argument(
        "--task",
        help="Run one named bootstrap task.",
    )
    action_group.add_argument(
        "--list-tasks",
        action="store_true",
        help="List the available bootstrap tasks and exit.",
    )
    action_group.add_argument(
        "--telegram-test",
        nargs="?",
        const="",
        metavar="TEXT",
        help="Send a Telegram test message, optionally using custom text.",
    )
    parser.add_argument(
        "--config",
        default=DEFAULT_CONFIG_PATH,
        help="Path to the YAML config file. Default: %(default)s",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the CLI and return a process exit code."""

    parser = build_parser()
    args = parser.parse_args(argv)

    if args.list_tasks:
        print("\n".join(list_tasks()))
        return 0

    try:
        if args.telegram_test is not None:
            send_telegram_test_message(args.telegram_test)
            return 0

        if args.task is None:
            parser.print_help()
            return 0

        run_task(args.task, config_path=args.config)
    except (
        FileNotFoundError,
        ConfigError,
        SecretError,
        TelegramNotifierError,
        ValueError,
    ) as exc:
        print(exc, file=sys.stderr)
        return 1

    return 0


def send_telegram_test_message(text: str) -> None:
    """Send a one-off Telegram test message from the CLI."""

    env = load_env_settings()
    notifier = build_telegram_notifier(env=env)
    message = text or build_default_test_message(
        signal_agent_env=env.signal_agent_env
    )
    notifier.send_message(message)
    print("telegram test message sent")
