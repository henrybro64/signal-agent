"""Shared helpers for Taiwan market scheduled tasks."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.core import SignalItem, finish_signal_run, start_signal_run
from app.notifiers import build_telegram_notifier

from .base import BaseTask
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


def begin_source_run(
    task: BaseTask,
    *,
    source_name: str,
    db_path: Path,
) -> int:
    """Start a source run and emit the standard started log."""

    run_id = start_signal_run(source_name, db_path=db_path)
    task.emit_log(
        "source_run_started",
        message="Source run started",
        db_path=str(db_path),
        source_name=source_name,
        run_id=run_id,
    )
    return run_id


def fail_source_run(
    task: BaseTask,
    *,
    source_name: str,
    run_id: int,
    error: Exception,
    db_path: Path,
    failed_sources: list[tuple[str, str]],
) -> None:
    """Record a failed source run with the standard structured log."""

    finish_signal_run(
        run_id,
        status="failed",
        error=str(error),
        db_path=db_path,
    )
    failed_sources.append((source_name, str(error)))
    task.emit_log(
        "source_run_failed",
        message="Source run failed",
        level=logging.ERROR,
        exc_info=error,
        db_path=str(db_path),
        source_name=source_name,
        run_id=run_id,
        error=str(error),
    )


def stock_code(item: SignalItem) -> str:
    value = item.metadata.get("stock_code")
    if isinstance(value, str):
        return value.strip()
    return ""


def company_name(item: SignalItem) -> str:
    value = item.metadata.get("company")
    if isinstance(value, str):
        return value.strip()
    return ""


def change_percent(item: SignalItem) -> float:
    value = item.metadata.get("change_percent")
    if isinstance(value, int | float):
        return float(value)
    return 0.0


def send_optional_telegram_digest(
    task: BaseTask,
    *,
    digest: str,
    config: Any,
    env: Any,
    db_path: Path,
    notified_count: int,
    empty_status: str,
    empty_message: str,
    validate_secrets,
    empty_log_fields: dict[str, Any] | None = None,
) -> NotificationOutcome:
    """Send a Telegram digest or skip with structured logs."""

    if not digest:
        task.emit_log(
            "notification_skipped",
            message="Telegram digest skipped",
            db_path=str(db_path),
            notifier="telegram",
            notification_status=empty_status,
            notified_count=0,
            **(empty_log_fields or {}),
        )
        print(empty_message)
        return NotificationOutcome(status=empty_status)

    if not config.notifiers.telegram.enabled:
        task.emit_log(
            "notification_skipped",
            message="Telegram digest skipped",
            db_path=str(db_path),
            notifier="telegram",
            notification_status="skipped_notifier_disabled",
            notified_count=0,
        )
        print("telegram digest skipped: notifier disabled in config")
        return NotificationOutcome(status="skipped_notifier_disabled")

    validate_secrets(telegram_enabled=True, env=env)
    notifier = build_telegram_notifier(env=env)
    try:
        notifier.send_message(digest)
    except Exception as exc:
        task.emit_log(
            "notification_failed",
            message="Telegram digest failed",
            level=logging.ERROR,
            exc_info=exc,
            db_path=str(db_path),
            notifier="telegram",
            notification_status="failed",
            attempted_notified_count=notified_count,
            error=str(exc),
        )
        raise
    task.emit_log(
        "notification_sent",
        message="Telegram digest sent",
        db_path=str(db_path),
        notifier="telegram",
        notification_status="sent",
        notified_count=notified_count,
    )
    print("telegram digest sent")
    return NotificationOutcome(status="sent", notified_count=notified_count)
