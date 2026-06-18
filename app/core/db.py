"""SQLite storage helpers for normalized signals."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
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
                    content_hash,
                    metadata_json,
                    first_seen_at,
                    last_seen_at,
                    status,
                    score,
                    reason
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item.source,
                    item.item_type,
                    external_id,
                    item.title,
                    url,
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
    url: str | None,
    external_id: str | None,
    content_hash: str | None,
) -> tuple[sqlite3.Row | None, DedupMatch | None]:
    # Ordered lookup matches the issue requirements exactly.
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
    payload = json.dumps(
        {
            "item_type": item.item_type.strip(),
            "title": item.title.strip(),
            "content": (item.content or "").strip(),
        },
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
