"""Command-line entrypoint for signal-agent."""

from __future__ import annotations

import argparse
import sys

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
    parser.add_argument(
        "--config",
        default=DEFAULT_CONFIG_PATH,
        help="Path to the YAML config file. Default: %(default)s",
    )
    parser.add_argument(
        "--task",
        choices=list_tasks(),
        help="Run one named bootstrap task.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the CLI and return a process exit code."""

    parser = build_parser()
    args = parser.parse_args(argv)

    if args.task is None:
        parser.print_help()
        return 0

    try:
        print(run_task(args.task, config_path=args.config))
    except (FileNotFoundError, ValueError) as exc:
        print(exc, file=sys.stderr)
        return 1

    return 0
