"""Read-only health and recent-run summaries for scheduled operators."""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from app.core.db import (
    SignalRunRecord,
    TaskRunRecord,
    count_failed_signal_runs,
    get_latest_task_run,
    get_recent_signal_runs,
)
from app.core.secrets import EnvSettings, has_telegram_credentials, load_env_settings

DEFAULT_DB_PATH = Path("data/signals.sqlite3")
DB_PATH_ENV_VAR = "SIGNAL_AGENT_DB_PATH"
DEFAULT_HEALTH_TASKS: tuple[str, ...] = (
    "job_search",
    "tw_stock_movers",
    "stock_watchlist",
)
DEFAULT_TIMEZONE = "Asia/Taipei"
# Align roughly with Ubuntu VM schedules: job_search twice daily; stock movers
# and watchlist alerts weekdays only (allow a weekend gap before flagging stale).
DEFAULT_STALE_AFTER_HOURS: dict[str, int] = {
    "job_search": 36,
    "tw_stock_movers": 72,
    "stock_watchlist": 72,
}
DEFAULT_STALE_AFTER_HOURS_FALLBACK = 48
TERMINAL_TASK_STATUSES = frozenset({"success", "failed", "skipped"})


class HealthError(ValueError):
    """Raised when the health/status command cannot read the database."""


@dataclass(slots=True, frozen=True)
class HealthSummary:
    """Operator-facing health snapshot for scheduled runs."""

    db_path: Path
    timezone_name: str
    telegram_configured: bool
    failed_sources_last_window: int
    window_hours: int
    latest_task_runs: dict[str, TaskRunRecord | None] = field(default_factory=dict)
    stale_after_hours_by_task: dict[str, int] = field(default_factory=dict)
    now: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def overall_status(self) -> str:
        if self.failed_sources_last_window > 0:
            return "DEGRADED"
        for task_name, run in self.latest_task_runs.items():
            if run is None:
                continue
            if _is_interrupted_task_run(run):
                return "DEGRADED"
            if run.status == "failed":
                return "DEGRADED"
            if _is_stale_task_run(
                run,
                now=self.now,
                stale_after_hours=_stale_after_hours_for(
                    task_name,
                    self.stale_after_hours_by_task,
                ),
            ):
                return "DEGRADED"
        return "OK"


def resolve_db_path(db_path: str | Path | None = None) -> Path:
    """Resolve the SQLite path used by scheduled tasks and health checks."""

    if db_path is not None:
        return Path(db_path).expanduser()
    raw_path = os.getenv(DB_PATH_ENV_VAR)
    if raw_path:
        return Path(raw_path).expanduser()
    return DEFAULT_DB_PATH


def require_readable_db(db_path: str | Path | None = None) -> Path:
    """Return a readable SQLite path or raise HealthError."""

    resolved = resolve_db_path(db_path)
    if not resolved.exists():
        raise HealthError(f"Database file not found: {resolved}")
    if not resolved.is_file():
        raise HealthError(f"Database path is not a file: {resolved}")
    if not os.access(resolved, os.R_OK):
        raise HealthError(f"Database file is not readable: {resolved}")
    return resolved


def summarize_run_health(
    *,
    db_path: str | Path | None = None,
    window_hours: int = 24,
    task_names: tuple[str, ...] = DEFAULT_HEALTH_TASKS,
    timezone_name: str = DEFAULT_TIMEZONE,
    env: EnvSettings | None = None,
    stale_after_hours_by_task: dict[str, int] | None = None,
    now: datetime | None = None,
) -> HealthSummary:
    """Build a concise health summary from local SQLite history."""

    resolved = require_readable_db(db_path)
    env_settings = env if env is not None else load_env_settings()
    try:
        latest_task_runs = {
            task_name: get_latest_task_run(task_name, db_path=resolved)
            for task_name in task_names
        }
        failed_sources = count_failed_signal_runs(
            window_hours=window_hours,
            db_path=resolved,
            now=now,
        )
    except (OSError, sqlite3.Error) as exc:
        raise HealthError(
            f"Unable to read health data from {resolved}: {exc}"
        ) from exc

    return HealthSummary(
        db_path=resolved,
        timezone_name=timezone_name,
        telegram_configured=has_telegram_credentials(env_settings),
        failed_sources_last_window=failed_sources,
        window_hours=window_hours,
        latest_task_runs=latest_task_runs,
        stale_after_hours_by_task=(
            dict(DEFAULT_STALE_AFTER_HOURS)
            if stale_after_hours_by_task is None
            else dict(stale_after_hours_by_task)
        ),
        now=_coerce_utc(now or datetime.now(UTC)),
    )


def format_health_report(summary: HealthSummary) -> str:
    """Render the human-readable ``--health`` report."""

    lines = [
        "signal-agent health",
        f"DB: {summary.db_path}",
    ]
    for task_name, run in summary.latest_task_runs.items():
        detail = _format_task_run_line(
            run,
            summary.timezone_name,
            stale_after_hours=_stale_after_hours_for(
                task_name,
                summary.stale_after_hours_by_task,
            ),
            now=summary.now,
        )
        lines.append(f"Latest {task_name} run: {detail}")
    lines.append(
        "Recent failed sources: "
        f"{summary.failed_sources_last_window} in last {summary.window_hours}h"
    )
    lines.append(
        "Telegram: "
        f"{'configured' if summary.telegram_configured else 'not configured'}"
    )
    lines.append(f"Overall: {summary.overall_status}")
    return "\n".join(lines)


def format_recent_signal_runs(
    runs: list[SignalRunRecord],
    *,
    timezone_name: str = DEFAULT_TIMEZONE,
) -> str:
    """Render the human-readable ``--recent-runs`` report."""

    if not runs:
        return "No signal runs recorded yet."

    return "\n".join(
        _format_signal_run_line(run, timezone_name) for run in runs
    )


def load_recent_signal_runs(
    *,
    limit: int = 20,
    db_path: str | Path | None = None,
) -> list[SignalRunRecord]:
    """Load recent signal runs after verifying the DB is readable."""

    resolved = require_readable_db(db_path)
    try:
        return get_recent_signal_runs(limit=limit, db_path=resolved)
    except (OSError, sqlite3.Error) as exc:
        raise HealthError(
            f"Unable to read signal runs from {resolved}: {exc}"
        ) from exc


def _format_task_run_line(
    run: TaskRunRecord | None,
    timezone_name: str,
    *,
    stale_after_hours: int,
    now: datetime,
) -> str:
    if run is None:
        return "none"
    stamp = _format_local_timestamp(run.started_at, timezone_name)
    label = _display_task_status(run.status)
    if _is_interrupted_task_run(run):
        return f"{label} (interrupted or still in progress), {stamp}"
    if run.status == "skipped":
        reason = (run.error_message or "unspecified").strip()
        line = f"{label} ({reason}), {stamp}"
    else:
        line = f"{label}, {stamp}"
    if _is_stale_task_run(run, now=now, stale_after_hours=stale_after_hours):
        line = f"{line} (stale >{stale_after_hours}h)"
    return line


def _display_task_status(status: str) -> str:
    """Map stored task_runs statuses to operator-facing labels."""

    if status == "success":
        return "completed"
    return status


def _is_interrupted_task_run(run: TaskRunRecord) -> bool:
    return run.status not in TERMINAL_TASK_STATUSES


def _is_stale_task_run(
    run: TaskRunRecord,
    *,
    now: datetime,
    stale_after_hours: int,
) -> bool:
    if stale_after_hours <= 0:
        return False
    if run.status not in {"success", "skipped"}:
        return False
    age = _coerce_utc(now) - _coerce_utc(run.started_at)
    return age > timedelta(hours=stale_after_hours)


def _stale_after_hours_for(
    task_name: str,
    mapping: dict[str, int],
) -> int:
    return mapping.get(task_name, DEFAULT_STALE_AFTER_HOURS_FALLBACK)


def _format_signal_run_line(
    run: SignalRunRecord,
    timezone_name: str,
) -> str:
    stamp = _format_local_timestamp(run.started_at, timezone_name)
    if run.status == "failed":
        error = (run.error or "unknown error").replace("\n", " ").strip()
        return f"{stamp} {run.source} failed error={error!r}"
    return (
        f"{stamp} {run.source} {run.status} "
        f"fetched={run.fetched_count} "
        f"inserted={run.inserted_count} "
        f"updated={run.updated_count}"
    )


def _format_local_timestamp(value: datetime, timezone_name: str) -> str:
    local = value.astimezone(ZoneInfo(timezone_name))
    return f"{local.strftime('%Y-%m-%d %H:%M')} {timezone_name}"


def _coerce_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
