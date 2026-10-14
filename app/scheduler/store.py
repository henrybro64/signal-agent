"""SQLite-backed schedule persistence (#85)."""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from app.core.db import init_db

from .cron import compute_next_run

# Stuck "running" rows older than this are reclaimed so overlap checks cannot
# permanently disable a schedule after a crash (#85).
DEFAULT_STALE_RUNNING_SECONDS = 3600.0


@dataclass(slots=True)
class Schedule:
    id: str
    name: str
    task_name: str
    enabled: bool
    timezone: str
    cron: str | None
    run_at: str | None
    allow_overlap: bool
    max_retries: int
    backoff_seconds: float
    dry_run: bool
    last_started_at: str | None
    last_finished_at: str | None
    last_status: str | None
    next_run_at: str | None
    created_at: str
    updated_at: str
    metadata: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "task_name": self.task_name,
            "enabled": self.enabled,
            "timezone": self.timezone,
            "cron": self.cron,
            "run_at": self.run_at,
            "allow_overlap": self.allow_overlap,
            "max_retries": self.max_retries,
            "backoff_seconds": self.backoff_seconds,
            "dry_run": self.dry_run,
            "last_started_at": self.last_started_at,
            "last_finished_at": self.last_finished_at,
            "last_status": self.last_status,
            "next_run_at": self.next_run_at,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "metadata": self.metadata,
        }


@dataclass(slots=True)
class ScheduleRun:
    id: int
    schedule_id: str
    run_id: str
    status: str
    started_at: str
    finished_at: str | None
    attempt: int
    error: str | None
    dry_run: bool


class ScheduleStore:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        init_db(self.db_path)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def add(
        self,
        *,
        name: str,
        task_name: str,
        timezone: str,
        cron: str | None = None,
        run_at: str | None = None,
        allow_overlap: bool = False,
        max_retries: int = 2,
        backoff_seconds: float = 30.0,
        dry_run: bool = False,
        enabled: bool = True,
        schedule_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Schedule:
        if not cron and not run_at:
            raise ValueError("schedule requires cron or run_at")
        if cron and run_at:
            raise ValueError("schedule cannot set both cron and run_at")
        # Validate timezone early.
        ZoneInfo(timezone)
        now = _utc_now()
        next_run = None
        if cron:
            nxt = compute_next_run(
                cron=cron,
                after=datetime.now(UTC),
                timezone_name=timezone,
            )
            next_run = None if nxt is None else _format(nxt.astimezone(UTC))
        elif run_at:
            next_run = _normalize_iso(run_at)
        sid = schedule_id or str(uuid.uuid4())
        meta_json = json.dumps(metadata or {}, ensure_ascii=False)
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO schedules (
                    id, name, task_name, enabled, timezone, cron, run_at,
                    allow_overlap, max_retries, backoff_seconds, dry_run,
                    last_started_at, last_finished_at, last_status, next_run_at,
                    created_at, updated_at, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL, ?, ?, ?, ?)
                """,
                (
                    sid,
                    name,
                    task_name,
                    1 if enabled else 0,
                    timezone,
                    cron,
                    run_at,
                    1 if allow_overlap else 0,
                    max_retries,
                    backoff_seconds,
                    1 if dry_run else 0,
                    next_run,
                    now,
                    now,
                    meta_json,
                ),
            )
            connection.commit()
        schedule = self.get(sid)
        assert schedule is not None
        return schedule

    def get(self, schedule_id: str) -> Schedule | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM schedules WHERE id = ?",
                (schedule_id,),
            ).fetchone()
        return None if row is None else _row_to_schedule(row)

    def list(self) -> list[Schedule]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM schedules ORDER BY name, id"
            ).fetchall()
        return [_row_to_schedule(row) for row in rows]

    def set_enabled(self, schedule_id: str, *, enabled: bool) -> Schedule:
        schedule = self.get(schedule_id)
        if schedule is None:
            raise KeyError(schedule_id)
        now = _utc_now()
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE schedules
                SET enabled = ?, updated_at = ?
                WHERE id = ?
                """,
                (1 if enabled else 0, now, schedule_id),
            )
            connection.commit()
        updated = self.get(schedule_id)
        assert updated is not None
        return updated

    def due(self, *, now: datetime | None = None) -> list[Schedule]:
        stamp = _format(now or datetime.now(UTC))
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM schedules
                WHERE enabled = 1
                  AND next_run_at IS NOT NULL
                  AND next_run_at <= ?
                ORDER BY next_run_at
                """,
                (stamp,),
            ).fetchall()
        return [_row_to_schedule(row) for row in rows]

    def mark_started(
        self,
        schedule_id: str,
        *,
        run_id: str,
        attempt: int,
        dry_run: bool,
        expected_next_run_at: str | None = None,
        resume_retry: bool = False,
        force: bool = False,
        exclusive: bool = True,
    ) -> ScheduleRun | None:
        """Atomically claim a schedule slot and insert a running row.

        Returns ``None`` when another worker already claimed the same due slot
        (or the schedule is already running/retrying and this is not a resume).
        When ``exclusive`` is false (``allow_overlap``), concurrent starts are
        permitted.
        """

        now = _utc_now()
        with self._connect() as connection:
            if resume_retry:
                claim = connection.execute(
                    """
                    UPDATE schedules
                    SET last_started_at = ?, last_status = ?, next_run_at = NULL,
                        updated_at = ?
                    WHERE id = ?
                      AND last_status = 'retrying'
                      AND next_run_at IS NULL
                    """,
                    (now, "running", now, schedule_id),
                )
            elif not exclusive:
                # allow_overlap: concurrent starts are intentional.
                claim = connection.execute(
                    """
                    UPDATE schedules
                    SET last_started_at = ?, last_status = ?, next_run_at = NULL,
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (now, "running", now, schedule_id),
                )
            elif force:
                claim = connection.execute(
                    """
                    UPDATE schedules
                    SET last_started_at = ?, last_status = ?, next_run_at = NULL,
                        updated_at = ?
                    WHERE id = ?
                      AND (
                        last_status IS NULL
                        OR last_status NOT IN ('running', 'retrying')
                      )
                    """,
                    (now, "running", now, schedule_id),
                )
            else:
                if expected_next_run_at is None:
                    return None
                claim = connection.execute(
                    """
                    UPDATE schedules
                    SET last_started_at = ?, last_status = ?, next_run_at = NULL,
                        updated_at = ?
                    WHERE id = ?
                      AND next_run_at IS NOT NULL
                      AND next_run_at = ?
                      AND (
                        last_status IS NULL
                        OR last_status NOT IN ('running', 'retrying')
                      )
                    """,
                    (now, "running", now, schedule_id, expected_next_run_at),
                )
            if claim.rowcount != 1:
                connection.rollback()
                return None
            cursor = connection.execute(
                """
                INSERT INTO schedule_runs (
                    schedule_id, run_id, status, started_at, finished_at,
                    attempt, error, dry_run
                ) VALUES (?, ?, 'running', ?, NULL, ?, NULL, ?)
                """,
                (schedule_id, run_id, now, attempt, 1 if dry_run else 0),
            )
            raw_row_id = cursor.lastrowid
            if raw_row_id is None:
                raise RuntimeError("schedule_runs insert did not return lastrowid")
            row_id = int(raw_row_id)
            connection.commit()
        return ScheduleRun(
            id=row_id,
            schedule_id=schedule_id,
            run_id=run_id,
            status="running",
            started_at=now,
            finished_at=None,
            attempt=attempt,
            error=None,
            dry_run=dry_run,
        )

    def mark_finished(
        self,
        schedule: Schedule,
        *,
        run_id: str | None = None,
        status: str,
        error: str | None = None,
        schedule_next: bool = True,
    ) -> Schedule:
        now_dt = datetime.now(UTC)
        now = _format(now_dt)
        with self._connect() as connection:
            if run_id is not None:
                connection.execute(
                    """
                    UPDATE schedule_runs
                    SET status = ?, finished_at = ?, error = ?
                    WHERE run_id = ?
                    """,
                    (status, now, error, run_id),
                )
            if schedule_next:
                next_run = None
                if schedule.cron and status != "running":
                    nxt = compute_next_run(
                        cron=schedule.cron,
                        after=now_dt,
                        timezone_name=schedule.timezone,
                    )
                    next_run = None if nxt is None else _format(nxt.astimezone(UTC))
                elif schedule.run_at:
                    # One-shot schedules do not recur.
                    next_run = None
                connection.execute(
                    """
                    UPDATE schedules
                    SET last_finished_at = ?, last_status = ?, next_run_at = ?,
                        updated_at = ?,
                        enabled = CASE
                            WHEN ? IS NULL AND cron IS NULL THEN 0
                            ELSE enabled
                        END
                    WHERE id = ?
                    """,
                    (
                        now,
                        status,
                        next_run,
                        now,
                        next_run,
                        schedule.id,
                    ),
                )
            else:
                # Mid-retry: keep next_run_at as claimed (NULL) / unchanged so a
                # crash cannot permanently clear a previously valid next run by
                # writing NULL over an existing timestamp. last_status stays
                # "retrying" so reclaim can recover stuck schedules.
                connection.execute(
                    """
                    UPDATE schedules
                    SET last_finished_at = ?, last_status = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (now, "retrying", now, schedule.id),
                )
            connection.commit()
        updated = self.get(schedule.id)
        assert updated is not None
        return updated

    def reclaim_orphaned_runs(self, *, now: datetime | None = None) -> int:
        """Reclaim every running/retrying schedule regardless of age (#98).

        Safe only when the caller knows no live worker is executing schedules
        (for example, immediately after acquiring the exclusive runtime lock).
        """

        return self.reclaim_stale_runs(older_than_seconds=0.0, now=now)

    def reclaim_stale_runs(
        self,
        *,
        older_than_seconds: float = DEFAULT_STALE_RUNNING_SECONDS,
        now: datetime | None = None,
    ) -> int:
        """Mark stuck running/retrying rows failed and restore next_run_at."""

        moment = now or datetime.now(UTC)
        # older_than_seconds=0 means "any started_at <= now" (orphaned reclaim).
        cutoff = _format(moment - timedelta(seconds=older_than_seconds))
        reclaimed = 0
        with self._connect() as connection:
            stuck_runs = connection.execute(
                """
                SELECT schedule_id, run_id FROM schedule_runs
                WHERE status = 'running' AND started_at <= ?
                """,
                (cutoff,),
            ).fetchall()
            stuck_schedules = connection.execute(
                """
                SELECT id FROM schedules
                WHERE last_status IN ('running', 'retrying')
                  AND last_started_at IS NOT NULL
                  AND last_started_at <= ?
                """,
                (cutoff,),
            ).fetchall()
        schedule_ids = {str(row["schedule_id"]) for row in stuck_runs}
        schedule_ids.update(str(row["id"]) for row in stuck_schedules)
        for schedule_id in sorted(schedule_ids):
            schedule = self.get(schedule_id)
            if schedule is None:
                continue
            with self._connect() as connection:
                connection.execute(
                    """
                    UPDATE schedule_runs
                    SET status = 'failed',
                        finished_at = ?,
                        error = COALESCE(error, 'reclaimed stale running run')
                    WHERE schedule_id = ? AND status = 'running'
                    """,
                    (_format(moment), schedule_id),
                )
                connection.commit()
            # Restore cadence so the schedule becomes due again.
            next_run = None
            if schedule.cron:
                nxt = compute_next_run(
                    cron=schedule.cron,
                    after=moment,
                    timezone_name=schedule.timezone,
                )
                next_run = None if nxt is None else _format(nxt.astimezone(UTC))
            with self._connect() as connection:
                connection.execute(
                    """
                    UPDATE schedules
                    SET last_status = ?, next_run_at = ?, updated_at = ?,
                        last_finished_at = ?
                    WHERE id = ?
                    """,
                    (
                        "failed",
                        next_run,
                        _format(moment),
                        _format(moment),
                        schedule_id,
                    ),
                )
                connection.commit()
            reclaimed += 1
        return reclaimed

    def history(self, schedule_id: str, *, limit: int = 20) -> list[ScheduleRun]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM schedule_runs
                WHERE schedule_id = ?
                ORDER BY started_at DESC
                LIMIT ?
                """,
                (schedule_id, limit),
            ).fetchall()
        return [
            ScheduleRun(
                id=int(row["id"]),
                schedule_id=str(row["schedule_id"]),
                run_id=str(row["run_id"]),
                status=str(row["status"]),
                started_at=str(row["started_at"]),
                finished_at=row["finished_at"],
                attempt=int(row["attempt"]),
                error=row["error"],
                dry_run=bool(row["dry_run"]),
            )
            for row in rows
        ]

    def has_running(self, schedule_id: str) -> bool:
        schedule = self.get(schedule_id)
        if schedule is not None and schedule.last_status in {"running", "retrying"}:
            return True
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT 1 FROM schedule_runs
                WHERE schedule_id = ? AND status = 'running'
                LIMIT 1
                """,
                (schedule_id,),
            ).fetchone()
        return row is not None


def _row_to_schedule(row: sqlite3.Row) -> Schedule:
    metadata = json.loads(row["metadata_json"] or "{}")
    if not isinstance(metadata, dict):
        metadata = {}
    return Schedule(
        id=str(row["id"]),
        name=str(row["name"]),
        task_name=str(row["task_name"]),
        enabled=bool(row["enabled"]),
        timezone=str(row["timezone"]),
        cron=row["cron"],
        run_at=row["run_at"],
        allow_overlap=bool(row["allow_overlap"]),
        max_retries=int(row["max_retries"]),
        backoff_seconds=float(row["backoff_seconds"]),
        dry_run=bool(row["dry_run"]),
        last_started_at=row["last_started_at"],
        last_finished_at=row["last_finished_at"],
        last_status=row["last_status"],
        next_run_at=row["next_run_at"],
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
        metadata=metadata,
    )


def _utc_now() -> str:
    return _format(datetime.now(UTC))


def _format(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _normalize_iso(value: str) -> str:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("run_at must include a timezone")
    return _format(parsed)
