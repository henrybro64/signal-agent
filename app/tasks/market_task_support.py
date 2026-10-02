"""Shared helpers for Taiwan market scheduled tasks."""

from __future__ import annotations

import logging
import os
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from app.core import (
    SignalItem,
    finish_signal_run,
    start_signal_run,
    tw_market_skip_label,
    tw_market_skip_reason,
    upsert_signal,
)
from app.core import tw_stock_utils as tw_stock_utils
from app.core.tw_stock_utils import TwStockMoversSummary
from app.notifiers import build_telegram_notifier

from .base import BaseTask
from .fetch import DB_PATH_ENV_VAR, DEFAULT_DB_PATH

# Re-export metadata helpers so market tasks keep a stable import path.
change_percent = tw_stock_utils.change_percent
company_name = tw_stock_utils.company_name_for_item
source_warning_label = tw_stock_utils.source_warning_label
stock_code = tw_stock_utils.stock_code_for_item


class _NamedFetchSource(Protocol):
    @property
    def name(self) -> str: ...

    def fetch(self) -> list[SignalItem]: ...


@dataclass(slots=True, frozen=True)
class NotificationOutcome:
    """Summarize the Telegram notification phase for structured logs."""

    status: str
    notified_count: int = 0


@dataclass(slots=True, frozen=True)
class TwStockSourceFetch:
    """Successful TWSE/TPEx quote fetch for one source."""

    run_id: int
    fetched_items: list[SignalItem]
    stock_items: list[SignalItem]


def resolve_task_db_path() -> Path:
    """Resolve the SQLite path for a scheduled task run."""

    raw_path = os.getenv(DB_PATH_ENV_VAR)
    if raw_path:
        return Path(raw_path).expanduser()
    return DEFAULT_DB_PATH


def skip_outside_tw_market_hours(
    task: BaseTask,
    *,
    db_path: Path,
    task_name: str,
    skip_log_message: str,
) -> bool:
    """Skip the task outside Taiwan market hours / holidays."""

    skip_reason = tw_market_skip_reason()
    if skip_reason is None:
        return False

    skip_label = tw_market_skip_label(skip_reason)
    task.emit_log(
        "task_skipped",
        message=skip_log_message,
        db_path=str(db_path),
        skip_reason=skip_reason,
    )
    print(f"{task_name} task skipped using {task.config_path}: {skip_label}")
    task.end_task_run(
        db_path=db_path,
        status="skipped",
        notified_count=0,
        error_message=skip_reason,
    )
    return True


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


def fetch_tw_stock_mover_source(
    task: BaseTask,
    *,
    source: _NamedFetchSource,
    db_path: Path,
    failed_sources: list[tuple[str, str]],
) -> TwStockSourceFetch | None:
    """Fetch mover quotes from one source, or record failure and return None."""

    run_id = begin_source_run(
        task,
        source_name=source.name,
        db_path=db_path,
    )
    try:
        fetched_items = source.fetch()
        stock_items = [
            item
            for item in fetched_items
            if item.item_type == "tw_stock_mover"
        ]
    except Exception as exc:
        fail_source_run(
            task,
            source_name=source.name,
            run_id=run_id,
            error=exc,
            db_path=db_path,
            failed_sources=failed_sources,
        )
        return None
    return TwStockSourceFetch(
        run_id=run_id,
        fetched_items=fetched_items,
        stock_items=stock_items,
    )


def complete_tw_stock_source_run(
    task: BaseTask,
    *,
    source_name: str,
    fetch: TwStockSourceFetch,
    db_path: Path,
    inserted_count: int = 0,
    updated_count: int = 0,
    **extra_log_fields: Any,
) -> None:
    """Finish a successful quote-source run with the standard completed log."""

    finish_signal_run(
        fetch.run_id,
        status="completed",
        fetched_count=len(fetch.stock_items),
        inserted_count=inserted_count,
        updated_count=updated_count,
        db_path=db_path,
    )
    task.emit_log(
        "source_run_completed",
        message="Source run completed",
        db_path=str(db_path),
        source_name=source_name,
        run_id=fetch.run_id,
        fetched_count=len(fetch.fetched_items),
        accepted_count=len(fetch.stock_items),
        **extra_log_fields,
    )


def collect_tw_stock_mover_summary(
    task: BaseTask,
    *,
    sources: Sequence[_NamedFetchSource],
    db_path: Path,
) -> TwStockMoversSummary:
    """Fetch, persist, and summarize TWSE/TPEx mover quotes for one task run."""

    summary = TwStockMoversSummary()
    for source in sources:
        fetch = fetch_tw_stock_mover_source(
            task,
            source=source,
            db_path=db_path,
            failed_sources=summary.failed_sources,
        )
        if fetch is None:
            continue

        ignored_count = len(fetch.fetched_items) - len(fetch.stock_items)
        summary.ignored_non_stock_count += ignored_count
        summary.fetched_count += len(fetch.stock_items)

        source_inserted = 0
        source_updated = 0
        for item in fetch.stock_items:
            result = upsert_signal(item, db_path=db_path)
            if result.action == "inserted":
                source_inserted += 1
                summary.new_count += 1
            else:
                source_updated += 1
                summary.duplicate_count += 1
            summary.stock_items.append(item)

        complete_tw_stock_source_run(
            task,
            source_name=source.name,
            fetch=fetch,
            db_path=db_path,
            inserted_count=source_inserted,
            updated_count=source_updated,
            ignored_count=ignored_count,
            new_count=source_inserted,
            duplicate_count=source_updated,
            skipped_count=source_updated + ignored_count,
        )
        summary.successful_sources += 1
    return summary


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
