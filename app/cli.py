"""Command-line entrypoint for signal-agent."""

from __future__ import annotations

import argparse
import sys

from app.config import ConfigError
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

    if args.task is None:
        parser.print_help()
        return 0

    try:
        run_task(args.task, config_path=args.config)
    except (FileNotFoundError, ConfigError, ValueError) as exc:
        print(exc, file=sys.stderr)
        return 1

    return 0
