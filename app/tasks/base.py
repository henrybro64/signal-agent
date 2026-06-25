"""Base interfaces for executable tasks."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any, ClassVar

from app.core.logging import configure_logging, get_logger, log_event


class BaseTask(ABC):
    """Common interface for any CLI-executable workflow."""

    name: ClassVar[str]

    def __init__(self, config_path: str) -> None:
        self.config_path = config_path
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
