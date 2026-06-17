"""Task registry for the bootstrap CLI."""

from __future__ import annotations

from collections.abc import Callable

from app.config import load_config, load_env_settings
from app.sources import DummySource, fetch_from_sources

TaskHandler = Callable[[str], str]


def run_dummy_task(config_path: str) -> str:
    """Run a no-op task to validate the CLI and config wiring."""

    config = load_config(config_path)
    env = load_env_settings()
    telegram_ready = bool(env.telegram_bot_token and env.telegram_chat_id)

    return (
        f"dummy task executed using {config_path}: "
        f"timezone={config.agent.timezone}, "
        f"digest_time={config.agent.digest_time}, "
        f"job_search_enabled={config.tasks.job_search.enabled}, "
        f"tw_stock_movers_enabled={config.tasks.tw_stock_movers.enabled}, "
        f"telegram_ready={telegram_ready}"
    )


def run_fetch_task(config_path: str) -> str:
    """Fetch sample signals through the source plugin pipeline."""

    load_config(config_path)
    items = fetch_from_sources([DummySource()])

    lines = [
        f"fetch task executed using {config_path}:",
        f"fetched {len(items)} signal(s) from dummy source:",
    ]
    lines.extend(f"- [{item.item_type}] {item.title}" for item in items)
    return "\n".join(lines)


TASKS: dict[str, TaskHandler] = {
    "dummy": run_dummy_task,
    "fetch": run_fetch_task,
}


def list_tasks() -> list[str]:
    """Return the sorted task names exposed by the CLI."""

    return sorted(TASKS)


def run_task(name: str, *, config_path: str) -> str:
    """Execute a task by name."""

    try:
        task = TASKS[name]
    except KeyError as exc:
        raise ValueError(f"Unknown task: {name}") from exc

    return task(config_path)
