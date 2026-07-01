"""Base interfaces for executable tasks."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, ClassVar

from app.core.db import finish_task_run, init_db, start_task_run
from app.core.logging import configure_logging, get_logger, log_event


class BaseTask(ABC):
    """Common interface for any CLI-executable workflow."""

    name: ClassVar[str]

    def __init__(self, config_path: str) -> None:
        self.config_path = config_path
        self.task_run_id: int | None = None
        configure_logging()
        self.logger = get_logger(f"tasks.{self.name}")

    @abstractmethod
    def run(self) -> None:
        """Execute the task."""

    def emit_log(
        self,
        event: str,
        *,
        message: str,
        level: int = logging.INFO,
        exc_info: BaseException | tuple[Any, Any, Any] | bool | None = None,
        **fields: Any,
    ) -> None:
        """Emit one structured log line with standard task context."""

        if self.task_run_id is not None and "task_run_id" not in fields:
            fields["task_run_id"] = self.task_run_id

        log_event(
            self.logger,
            event=event,
            message=message,
            level=level,
            exc_info=exc_info,
            task_name=self.name,
            config_path=self.config_path,
            **fields,
        )

    def begin_task_run(self, *, db_path: Path) -> int:
        """Initialize storage and create a task-level run record."""

        init_db(db_path)
        self.task_run_id = start_task_run(self.name, db_path=db_path)
        return self.task_run_id

    def end_task_run(
        self,
        *,
        db_path: Path,
        status: str,
        fetched_count: int = 0,
        new_count: int = 0,
        skipped_count: int = 0,
        notified_count: int = 0,
        error_message: str | None = None,
    ) -> None:
        """Finalize the active task-level run record."""

        if self.task_run_id is None:
            raise RuntimeError("Task run has not been started")

        finish_task_run(
            self.task_run_id,
            status=status,
            fetched_count=fetched_count,
            new_count=new_count,
            skipped_count=skipped_count,
            notified_count=notified_count,
            error_message=error_message,
            db_path=db_path,
        )
