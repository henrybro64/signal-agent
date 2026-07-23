"""Shared helpers for Taiwan market scheduled tasks."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .fetch import DB_PATH_ENV_VAR, DEFAULT_DB_PATH


@dataclass(slots=True, frozen=True)
class NotificationOutcome:
    """Summarize the Telegram notification phase for structured logs."""

    status: str
    notified_count: int = 0


def resolve_task_db_path() -> Path:
    """Resolve the SQLite path for a scheduled task run."""

    raw_path = os.getenv(DB_PATH_ENV_VAR)
    if raw_path:
        return Path(raw_path).expanduser()
    return DEFAULT_DB_PATH
