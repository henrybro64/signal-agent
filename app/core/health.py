"""Read-only health and recent-run summaries for scheduled operators."""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
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
DEFAULT_HEALTH_TASKS: tuple[str, ...] = ("job_search", "tw_stock_movers")
DEFAULT_TIMEZONE = "Asia/Taipei"


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

    @property
    def overall_status(self) -> str:
        if self.failed_sources_last_window > 0:
            return "DEGRADED"
        for run in self.latest_task_runs.values():
            if run is not None and run.status == "failed":
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
    )


def format_health_report(summary: HealthSummary) -> str:
    """Render the human-readable ``--health`` report."""

    lines = [
        "signal-agent health",
        f"DB: {summary.db_path}",
    ]
    for task_name, run in summary.latest_task_runs.items():
        detail = _format_task_run_line(run, summary.timezone_name)
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
) -> str:
    if run is None:
        return "none"
    stamp = _format_local_timestamp(run.started_at, timezone_name)
    return f"{run.status}, {stamp}"


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
