"""SQLite storage helpers for normalized signals."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from typing import Literal

from .models import SignalItem

SignalStatus = Literal[
    "new",
    "reviewed",
    "ignored",
    "applied",
    "rejected",
    "expired",
]
DedupMatch = Literal["url", "source_external_id", "content_hash"]

VALID_SIGNAL_STATUSES: frozenset[SignalStatus] = frozenset(
    {
        "new",
        "reviewed",
        "ignored",
        "applied",
        "rejected",
        "expired",
    }
)

_DEFAULT_DB_PATH: Path | None = None
_SCHEMA_PATH = Path(__file__).resolve().parents[2] / "sql" / "schema.sql"


@dataclass(slots=True, frozen=True)
class UpsertResult:
    """Summary of an upsert attempt."""

    signal_id: int
    action: Literal["inserted", "updated"]
    matched_on: DedupMatch | None = None


SignalRunStatus = Literal["completed", "failed"]
TaskRunStatus = Literal["success", "failed", "skipped"]
VALID_TASK_RUN_STATUSES: frozenset[str] = frozenset(
    {"success", "failed", "skipped"}
)


@dataclass(slots=True, frozen=True)
class SignalRunRecord:
    """One persisted source collection run."""

    id: int
    source: str
    started_at: datetime
    finished_at: datetime | None
    status: str
    fetched_count: int
    inserted_count: int
    updated_count: int
    error: str | None


@dataclass(slots=True, frozen=True)
class TaskRunRecord:
    """One persisted top-level task execution."""

    id: int
    task_name: str
    started_at: datetime
    finished_at: datetime | None
    status: str
    fetched_count: int
    new_count: int
    skipped_count: int
    notified_count: int
    error_message: str | None


def init_db(db_path: str | Path) -> None:
    """Create the SQLite database and required tables if they do not exist."""

    global _DEFAULT_DB_PATH

    resolved_path = Path(db_path).expanduser()
    resolved_path.parent.mkdir(parents=True, exist_ok=True)

    with _connect(resolved_path) as connection:
        connection.executescript(_SCHEMA_PATH.read_text(encoding="utf-8"))

    _DEFAULT_DB_PATH = resolved_path


def upsert_signal(
    item: SignalItem,
    *,
    db_path: str | Path | None = None,
) -> UpsertResult:
    """Insert a new signal or refresh an existing one using ordered dedup rules."""

    resolved_path = _resolve_db_path(db_path)
    observed_at = _coerce_utc(item.observed_at)
    observed_at_text = _format_timestamp(observed_at)
    url = _clean_optional_text(item.url)
    external_id = _clean_optional_text(item.external_id)
    content = _clean_optional_text(item.content)
    content_hash = _build_content_hash(item)
    metadata_json = json.dumps(
        item.metadata,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )

    with _connect(resolved_path) as connection:
        existing_row, matched_on = _find_existing_signal(
            connection,
            source=item.source,
            item_type=item.item_type,
            url=url,
            external_id=external_id,
            content_hash=content_hash,
        )

        if existing_row is None:
            cursor = connection.execute(
                """
                INSERT INTO signals (
                    source,
                    item_type,
                    external_id,
                    title,
                    url,
                    content,
                    content_hash,
                    metadata_json,
                    first_seen_at,
                    last_seen_at,
                    status,
                    score,
                    reason
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item.source,
                    item.item_type,
                    external_id,
                    item.title,
                    url,
                    content,
                    content_hash,
                    metadata_json,
                    observed_at_text,
                    observed_at_text,
                    "new",
                    None,
                    None,
                ),
            )
            return UpsertResult(signal_id=int(cursor.lastrowid), action="inserted")

        first_seen_at = min(
            _parse_timestamp(existing_row["first_seen_at"]),
            observed_at,
        )
        last_seen_at = max(
            _parse_timestamp(existing_row["last_seen_at"]),
            observed_at,
        )

        # Preserve status and scoring fields, but refresh the latest payload.
        connection.execute(
            """
            UPDATE signals
            SET
                item_type = ?,
                external_id = ?,
                title = ?,
                url = ?,
                content = ?,
                content_hash = ?,
                metadata_json = ?,
                first_seen_at = ?,
                last_seen_at = ?
            WHERE id = ?
            """,
            (
                item.item_type,
                external_id or existing_row["external_id"],
                item.title,
                url or existing_row["url"],
                content or existing_row["content"],
                content_hash or existing_row["content_hash"],
                metadata_json,
                _format_timestamp(first_seen_at),
                _format_timestamp(last_seen_at),
                existing_row["id"],
            ),
        )
        return UpsertResult(
            signal_id=int(existing_row["id"]),
            action="updated",
            matched_on=matched_on,
        )


def update_signal_status(
    signal_id: int,
    status: SignalStatus,
    *,
    db_path: str | Path | None = None,
) -> None:
    """Update the stored status for one signal."""

    if status not in VALID_SIGNAL_STATUSES:
        valid_statuses = ", ".join(sorted(VALID_SIGNAL_STATUSES))
        raise ValueError(
            f"Invalid signal status: {status}. Valid statuses: {valid_statuses}"
        )

    resolved_path = _resolve_db_path(db_path)

    with _connect(resolved_path) as connection:
        cursor = connection.execute(
            "UPDATE signals SET status = ? WHERE id = ?",
            (status, signal_id),
        )
        if cursor.rowcount == 0:
            raise ValueError(f"Unknown signal id: {signal_id}")


def update_signal_evaluation(
    signal_id: int,
    *,
    score: float,
    reason: str,
    status: SignalStatus | None = None,
    db_path: str | Path | None = None,
) -> None:
    """Persist scoring output and optionally update the signal status."""

    if status is not None and status not in VALID_SIGNAL_STATUSES:
        valid_statuses = ", ".join(sorted(VALID_SIGNAL_STATUSES))
        raise ValueError(
            f"Invalid signal status: {status}. Valid statuses: {valid_statuses}"
        )

    resolved_path = _resolve_db_path(db_path)

    with _connect(resolved_path) as connection:
        cursor = connection.execute(
            """
            UPDATE signals
            SET
                score = ?,
                reason = ?,
                status = COALESCE(?, status)
            WHERE id = ?
            """,
            (score, reason, status, signal_id),
        )
        if cursor.rowcount == 0:
            raise ValueError(f"Unknown signal id: {signal_id}")


SignalEvaluationState = Literal["unscored", "pending_digest", "complete"]


def get_signal_evaluation_state(
    signal_id: int,
    *,
    db_path: str | Path | None = None,
) -> SignalEvaluationState:
    """Return whether a signal still needs scoring or digest reporting."""

    resolved_path = _resolve_db_path(db_path)

    with _connect(resolved_path) as connection:
        row = connection.execute(
            "SELECT score, status FROM signals WHERE id = ?",
            (signal_id,),
        ).fetchone()

    if row is None:
        raise ValueError(f"Unknown signal id: {signal_id}")
    if row["score"] is None:
        return "unscored"
    if row["status"] in {"new", "rejected"}:
        return "pending_digest"
    return "complete"


def get_signal_evaluation(
    signal_id: int,
    *,
    db_path: str | Path | None = None,
) -> tuple[float, str]:
    """Return the stored score and reason for one signal."""

    resolved_path = _resolve_db_path(db_path)

    with _connect(resolved_path) as connection:
        row = connection.execute(
            "SELECT score, reason FROM signals WHERE id = ?",
            (signal_id,),
        ).fetchone()

    if row is None:
        raise ValueError(f"Unknown signal id: {signal_id}")
    if row["score"] is None or row["reason"] is None:
        raise ValueError(f"Signal id {signal_id} has not been scored yet")

    return float(row["score"]), str(row["reason"])


def mark_signals_digest_reported(
    signal_ids: list[int],
    *,
    db_path: str | Path | None = None,
) -> None:
    """Mark scored jobs as reported after a digest is delivered."""

    if not signal_ids:
        return

    resolved_path = _resolve_db_path(db_path)
    placeholders = ", ".join("?" for _ in signal_ids)

    with _connect(resolved_path) as connection:
        connection.execute(
            f"""
            UPDATE signals
            SET status = 'reviewed'
            WHERE id IN ({placeholders})
            """,
            signal_ids,
        )


def start_signal_run(
    source: str,
    *,
    started_at: datetime | None = None,
    db_path: str | Path | None = None,
) -> int:
    """Record the start of a source collection run."""

    resolved_path = _resolve_db_path(db_path)
    started_at_text = _format_timestamp(started_at or datetime.now(UTC))

    with _connect(resolved_path) as connection:
        cursor = connection.execute(
            """
            INSERT INTO signal_runs (
                source,
                started_at,
                status
            )
            VALUES (?, ?, ?)
            """,
            (source, started_at_text, "running"),
        )
        return int(cursor.lastrowid)


def finish_signal_run(
    run_id: int,
    *,
    status: SignalRunStatus,
    fetched_count: int = 0,
    inserted_count: int = 0,
    updated_count: int = 0,
    error: str | None = None,
    finished_at: datetime | None = None,
    db_path: str | Path | None = None,
) -> None:
    """Record the completion of a source collection run."""

    if status not in {"completed", "failed"}:
        raise ValueError(f"Invalid signal run status: {status}")

    resolved_path = _resolve_db_path(db_path)
    finished_at_text = _format_timestamp(finished_at or datetime.now(UTC))

    with _connect(resolved_path) as connection:
        cursor = connection.execute(
            """
            UPDATE signal_runs
            SET
                finished_at = ?,
                status = ?,
                fetched_count = ?,
                inserted_count = ?,
                updated_count = ?,
                error = ?
            WHERE id = ?
            """,
            (
                finished_at_text,
                status,
                fetched_count,
                inserted_count,
                updated_count,
                error,
                run_id,
            ),
        )
        if cursor.rowcount == 0:
            raise ValueError(f"Unknown signal run id: {run_id}")


def start_task_run(
    task_name: str,
    *,
    started_at: datetime | None = None,
    db_path: str | Path | None = None,
) -> int:
    """Record the start of one top-level task execution."""

    resolved_path = _resolve_db_path(db_path)
    started_at_text = _format_timestamp(started_at or datetime.now(UTC))

    with _connect(resolved_path) as connection:
        cursor = connection.execute(
            """
            INSERT INTO task_runs (
                task_name,
                started_at,
                status
            )
            VALUES (?, ?, ?)
            """,
            (task_name, started_at_text, "running"),
        )
        return int(cursor.lastrowid)


def finish_task_run(
    run_id: int,
    *,
    status: TaskRunStatus,
    fetched_count: int = 0,
    new_count: int = 0,
    skipped_count: int = 0,
    notified_count: int = 0,
    error_message: str | None = None,
    finished_at: datetime | None = None,
    db_path: str | Path | None = None,
) -> None:
    """Record the completion of one top-level task execution."""

    if status not in VALID_TASK_RUN_STATUSES:
        valid = ", ".join(sorted(VALID_TASK_RUN_STATUSES))
        raise ValueError(f"Invalid task run status: {status}. Valid: {valid}")

    resolved_path = _resolve_db_path(db_path)
    finished_at_text = _format_timestamp(finished_at or datetime.now(UTC))

    with _connect(resolved_path) as connection:
        cursor = connection.execute(
            """
            UPDATE task_runs
            SET
                finished_at = ?,
                status = ?,
                fetched_count = ?,
                new_count = ?,
                skipped_count = ?,
                notified_count = ?,
                error_message = ?
            WHERE id = ?
            """,
            (
                finished_at_text,
                status,
                fetched_count,
                new_count,
                skipped_count,
                notified_count,
                error_message,
                run_id,
            ),
        )
        if cursor.rowcount == 0:
            raise ValueError(f"Unknown task run id: {run_id}")


def get_recent_signal_runs(
    *,
    limit: int = 20,
    db_path: str | Path | None = None,
) -> list[SignalRunRecord]:
    """Return the newest source collection runs, newest first."""

    if limit <= 0:
        return []

    resolved_path = _resolve_db_path(db_path)
    with _connect(resolved_path) as connection:
        rows = connection.execute(
            """
            SELECT
                id,
                source,
                started_at,
                finished_at,
                status,
                fetched_count,
                inserted_count,
                updated_count,
                error
            FROM signal_runs
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

    return [_row_to_signal_run(row) for row in rows]


def get_latest_signal_run_by_source(
    source: str,
    *,
    db_path: str | Path | None = None,
) -> SignalRunRecord | None:
    """Return the newest run for one source, if any."""

    resolved_path = _resolve_db_path(db_path)
    with _connect(resolved_path) as connection:
        row = connection.execute(
            """
            SELECT
                id,
                source,
                started_at,
                finished_at,
                status,
                fetched_count,
                inserted_count,
                updated_count,
                error
            FROM signal_runs
            WHERE source = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (source,),
        ).fetchone()

    if row is None:
        return None
    return _row_to_signal_run(row)


def get_latest_task_run(
    task_name: str,
    *,
    db_path: str | Path | None = None,
) -> TaskRunRecord | None:
    """Return the newest task-level run for one task name, if any."""

    resolved_path = _resolve_db_path(db_path)
    with _connect(resolved_path) as connection:
        row = connection.execute(
            """
            SELECT
                id,
                task_name,
                started_at,
                finished_at,
                status,
                fetched_count,
                new_count,
                skipped_count,
                notified_count,
                error_message
            FROM task_runs
            WHERE task_name = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (task_name,),
        ).fetchone()

    if row is None:
        return None
    return _row_to_task_run(row)


def count_failed_signal_runs(
    *,
    window_hours: int = 24,
    db_path: str | Path | None = None,
    now: datetime | None = None,
) -> int:
    """Count failed source runs started within the lookback window."""

    if window_hours <= 0:
        return 0

    resolved_path = _resolve_db_path(db_path)
    cutoff = _coerce_utc(now or datetime.now(UTC)) - timedelta(hours=window_hours)
    cutoff_text = _format_timestamp(cutoff)

    with _connect(resolved_path) as connection:
        row = connection.execute(
            """
            SELECT COUNT(*) AS failed_count
            FROM signal_runs
            WHERE status = 'failed'
              AND started_at >= ?
            """,
            (cutoff_text,),
        ).fetchone()

    return int(row["failed_count"]) if row is not None else 0


def _row_to_signal_run(row: sqlite3.Row) -> SignalRunRecord:
    finished_at = row["finished_at"]
    return SignalRunRecord(
        id=int(row["id"]),
        source=str(row["source"]),
        started_at=_parse_timestamp(row["started_at"]),
        finished_at=(
            _parse_timestamp(finished_at) if finished_at is not None else None
        ),
        status=str(row["status"]),
        fetched_count=int(row["fetched_count"]),
        inserted_count=int(row["inserted_count"]),
        updated_count=int(row["updated_count"]),
        error=row["error"],
    )


def _row_to_task_run(row: sqlite3.Row) -> TaskRunRecord:
    finished_at = row["finished_at"]
    return TaskRunRecord(
        id=int(row["id"]),
        task_name=str(row["task_name"]),
        started_at=_parse_timestamp(row["started_at"]),
        finished_at=(
            _parse_timestamp(finished_at) if finished_at is not None else None
        ),
        status=str(row["status"]),
        fetched_count=int(row["fetched_count"]),
        new_count=int(row["new_count"]),
        skipped_count=int(row["skipped_count"]),
        notified_count=int(row["notified_count"]),
        error_message=row["error_message"],
    )


def _resolve_db_path(db_path: str | Path | None) -> Path:
    if db_path is not None:
        return Path(db_path).expanduser()
    if _DEFAULT_DB_PATH is not None:
        return _DEFAULT_DB_PATH
    raise RuntimeError("Database is not initialized. Call init_db() first.")


def _connect(db_path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    return connection


def _find_existing_signal(
    connection: sqlite3.Connection,
    *,
    source: str,
    item_type: str,
    url: str | None,
    external_id: str | None,
    content_hash: str | None,
) -> tuple[sqlite3.Row | None, DedupMatch | None]:
    # Stock movers use snapshot external IDs when present; otherwise fall back
    # to the normal URL/content-hash dedup path for custom sources.
    if item_type == "tw_stock_mover" and external_id is not None:
        row = connection.execute(
            """
            SELECT *
            FROM signals
            WHERE source = ? AND external_id = ?
            ORDER BY id
            LIMIT 1
            """,
            (source, external_id),
        ).fetchone()
        if row is not None:
            return row, "source_external_id"
        return None, None

    if url is not None:
        row = connection.execute(
            "SELECT * FROM signals WHERE url = ? ORDER BY id LIMIT 1",
            (url,),
        ).fetchone()
        if row is not None:
            return row, "url"

    if external_id is not None:
        row = connection.execute(
            """
            SELECT *
            FROM signals
            WHERE source = ? AND external_id = ?
            ORDER BY id
            LIMIT 1
            """,
            (source, external_id),
        ).fetchone()
        if row is not None:
            return row, "source_external_id"

    if content_hash is not None:
        row = connection.execute(
            "SELECT * FROM signals WHERE content_hash = ? ORDER BY id LIMIT 1",
            (content_hash,),
        ).fetchone()
        if row is not None:
            return row, "content_hash"

    return None, None


def _build_content_hash(item: SignalItem) -> str:
    payload_data = {
        "item_type": item.item_type.strip(),
        "title": item.title.strip(),
        "content": (item.content or "").strip(),
    }
    if item.item_type == "tw_stock_mover":
        payload_data["external_id"] = (item.external_id or "").strip()

    payload = json.dumps(
        payload_data,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return sha256(payload.encode("utf-8")).hexdigest()


def _coerce_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _format_timestamp(value: datetime) -> str:
    return _coerce_utc(value).isoformat().replace("+00:00", "Z")


def _parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def _clean_optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None
