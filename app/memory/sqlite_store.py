"""SQLite-backed memory store."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.core.db import init_db

from .models import MemoryChunk, MemoryDocument, OperationalMemoryRecord


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _format_ts(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


class SqliteMemoryStore:
    """Persist memory documents/chunks/ops in the project SQLite database."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        init_db(self.db_path)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def upsert_document(
        self,
        *,
        namespace: str,
        source_path: str,
        content_hash: str,
        title: str | None,
        metadata: dict[str, Any] | None = None,
        chunks: list[tuple[str, str, int]],
    ) -> MemoryDocument:
        """Replace document chunks for ``(namespace, source_path)``.

        ``chunks`` entries are ``(chunk_id, content, token_estimate)``.
        """

        now = _format_ts(_utc_now())
        meta_json = json.dumps(metadata or {}, ensure_ascii=False)
        with self._connect() as connection:
            existing = connection.execute(
                """
                SELECT id, created_at FROM memory_documents
                WHERE namespace = ? AND source_path = ?
                """,
                (namespace, source_path),
            ).fetchone()
            if existing is None:
                cursor = connection.execute(
                    """
                    INSERT INTO memory_documents (
                        namespace, source_path, content_hash, title,
                        created_at, updated_at, metadata_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        namespace,
                        source_path,
                        content_hash,
                        title,
                        now,
                        now,
                        meta_json,
                    ),
                )
                document_id = int(cursor.lastrowid)
                created_at = now
            else:
                document_id = int(existing["id"])
                created_at = str(existing["created_at"])
                connection.execute(
                    """
                    UPDATE memory_documents
                    SET content_hash = ?, title = ?, updated_at = ?,
                        metadata_json = ?
                    WHERE id = ?
                    """,
                    (content_hash, title, now, meta_json, document_id),
                )
                connection.execute(
                    "DELETE FROM memory_chunks WHERE document_id = ?",
                    (document_id,),
                )

            for ordinal, (chunk_id, content, token_estimate) in enumerate(chunks):
                connection.execute(
                    """
                    INSERT INTO memory_chunks (
                        document_id, chunk_id, content, token_estimate, ordinal
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (document_id, chunk_id, content, token_estimate, ordinal),
                )
            connection.commit()

        return MemoryDocument(
            id=document_id,
            namespace=namespace,
            source_path=source_path,
            content_hash=content_hash,
            title=title,
            created_at=_parse_ts(created_at),
            updated_at=_parse_ts(now),
            metadata=dict(metadata or {}),
        )

    def get_document_hash(self, *, namespace: str, source_path: str) -> str | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT content_hash FROM memory_documents
                WHERE namespace = ? AND source_path = ?
                """,
                (namespace, source_path),
            ).fetchone()
        return None if row is None else str(row["content_hash"])

    def list_documents(self, namespace: str) -> list[MemoryDocument]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM memory_documents
                WHERE namespace = ?
                ORDER BY source_path
                """,
                (namespace,),
            ).fetchall()
        return [_row_to_document(row) for row in rows]

    def list_chunks(self, namespace: str) -> list[MemoryChunk]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT c.*, d.namespace, d.source_path, d.title
                FROM memory_chunks c
                JOIN memory_documents d ON d.id = c.document_id
                WHERE d.namespace = ?
                ORDER BY d.source_path, c.ordinal
                """,
                (namespace,),
            ).fetchall()
        return [_row_to_chunk(row) for row in rows]

    def delete_document(self, *, namespace: str, source_path: str) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT id FROM memory_documents
                WHERE namespace = ? AND source_path = ?
                """,
                (namespace, source_path),
            ).fetchone()
            if row is None:
                return False
            document_id = int(row["id"])
            connection.execute(
                "DELETE FROM memory_chunks WHERE document_id = ?",
                (document_id,),
            )
            connection.execute(
                "DELETE FROM memory_documents WHERE id = ?",
                (document_id,),
            )
            connection.commit()
        return True

    def delete_namespace(self, namespace: str) -> int:
        with self._connect() as connection:
            docs = connection.execute(
                "SELECT id FROM memory_documents WHERE namespace = ?",
                (namespace,),
            ).fetchall()
            deleted = 0
            for row in docs:
                document_id = int(row["id"])
                cur = connection.execute(
                    "DELETE FROM memory_chunks WHERE document_id = ?",
                    (document_id,),
                )
                deleted += cur.rowcount
                cur = connection.execute(
                    "DELETE FROM memory_documents WHERE id = ?",
                    (document_id,),
                )
                deleted += cur.rowcount
            cur = connection.execute(
                "DELETE FROM memory_operational WHERE namespace = ?",
                (namespace,),
            )
            deleted += cur.rowcount
            connection.commit()
        return deleted

    def put_operational(
        self,
        *,
        namespace: str,
        kind: str,
        key: str,
        payload: dict[str, Any],
        expires_at: datetime | None = None,
    ) -> OperationalMemoryRecord:
        now = _format_ts(_utc_now())
        expires = None if expires_at is None else _format_ts(expires_at)
        payload_json = json.dumps(payload, ensure_ascii=False)
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO memory_operational (
                    namespace, kind, key, payload_json, created_at, expires_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(namespace, kind, key) DO UPDATE SET
                    payload_json = excluded.payload_json,
                    created_at = excluded.created_at,
                    expires_at = excluded.expires_at
                """,
                (namespace, kind, key, payload_json, now, expires),
            )
            row = connection.execute(
                """
                SELECT * FROM memory_operational
                WHERE namespace = ? AND kind = ? AND key = ?
                """,
                (namespace, kind, key),
            ).fetchone()
            connection.commit()
        assert row is not None
        return _row_to_operational(row)

    def get_operational(
        self,
        *,
        namespace: str,
        kind: str,
        key: str,
    ) -> OperationalMemoryRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM memory_operational
                WHERE namespace = ? AND kind = ? AND key = ?
                """,
                (namespace, kind, key),
            ).fetchone()
        if row is None:
            return None
        record = _row_to_operational(row)
        if record.expires_at is not None and record.expires_at <= _utc_now():
            self.delete_operational(namespace=namespace, kind=kind, key=key)
            return None
        return record

    def delete_operational(self, *, namespace: str, kind: str, key: str) -> bool:
        with self._connect() as connection:
            cur = connection.execute(
                """
                DELETE FROM memory_operational
                WHERE namespace = ? AND kind = ? AND key = ?
                """,
                (namespace, kind, key),
            )
            connection.commit()
            return cur.rowcount > 0

    def purge_expired_operational(self, *, now: datetime | None = None) -> int:
        stamp = _format_ts(now or _utc_now())
        with self._connect() as connection:
            cur = connection.execute(
                """
                DELETE FROM memory_operational
                WHERE expires_at IS NOT NULL AND expires_at <= ?
                """,
                (stamp,),
            )
            connection.commit()
            return cur.rowcount


def _row_to_document(row: sqlite3.Row) -> MemoryDocument:
    metadata = json.loads(row["metadata_json"] or "{}")
    if not isinstance(metadata, dict):
        metadata = {}
    return MemoryDocument(
        id=int(row["id"]),
        namespace=str(row["namespace"]),
        source_path=str(row["source_path"]),
        content_hash=str(row["content_hash"]),
        title=row["title"],
        created_at=_parse_ts(str(row["created_at"])),
        updated_at=_parse_ts(str(row["updated_at"])),
        metadata=metadata,
    )


def _row_to_chunk(row: sqlite3.Row) -> MemoryChunk:
    return MemoryChunk(
        id=int(row["id"]),
        document_id=int(row["document_id"]),
        chunk_id=str(row["chunk_id"]),
        content=str(row["content"]),
        token_estimate=int(row["token_estimate"]),
        ordinal=int(row["ordinal"]),
        namespace=str(row["namespace"]),
        source_path=str(row["source_path"]),
        title=row["title"],
    )


def _row_to_operational(row: sqlite3.Row) -> OperationalMemoryRecord:
    payload = json.loads(row["payload_json"] or "{}")
    if not isinstance(payload, dict):
        payload = {}
    expires_raw = row["expires_at"]
    return OperationalMemoryRecord(
        id=int(row["id"]),
        namespace=str(row["namespace"]),
        kind=str(row["kind"]),
        key=str(row["key"]),
        payload=payload,
        created_at=_parse_ts(str(row["created_at"])),
        expires_at=None if expires_raw is None else _parse_ts(str(expires_raw)),
    )
