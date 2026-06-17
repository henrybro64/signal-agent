"""Base interfaces for executable tasks."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import ClassVar


class BaseTask(ABC):
    """Common interface for any CLI-executable workflow."""

    name: ClassVar[str]

    def __init__(self, config_path: str) -> None:
        self.config_path = config_path

    @abstractmethod
    def run(self) -> None:
        """Execute the task."""
