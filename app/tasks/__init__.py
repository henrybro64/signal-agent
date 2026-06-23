"""Task registry for the bootstrap CLI."""

from __future__ import annotations

from .base import BaseTask
from .dummy import DummyTask
from .fetch import FetchTask
from .job_search import JobSearchTask
from .tw_stock_movers import TwStockMoversTask

TASKS: dict[str, type[BaseTask]] = {
    DummyTask.name: DummyTask,
    FetchTask.name: FetchTask,
    JobSearchTask.name: JobSearchTask,
    TwStockMoversTask.name: TwStockMoversTask,
}


def list_tasks() -> list[str]:
    """Return the sorted task names exposed by the CLI."""

    return sorted(TASKS)


def build_task(name: str, *, config_path: str) -> BaseTask:
    """Instantiate a task by name."""

    try:
        task_class = TASKS[name]
    except KeyError as exc:
        available = ", ".join(list_tasks())
        raise ValueError(
            f"Unknown task: {name}. Available tasks: {available}"
        ) from exc

    return task_class(config_path)


def run_task(name: str, *, config_path: str) -> None:
    """Execute a task by name."""

    build_task(name, config_path=config_path).run()


__all__ = [
    "BaseTask",
    "DummyTask",
    "FetchTask",
    "JobSearchTask",
    "TASKS",
    "TwStockMoversTask",
    "build_task",
    "list_tasks",
    "run_task",
]
