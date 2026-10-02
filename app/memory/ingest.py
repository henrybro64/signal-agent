"""Document ingestion for explicitly configured text/Markdown paths."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from .sqlite_store import SqliteMemoryStore

_FORBIDDEN_NAME_PARTS = (
    ".env",
    "credentials",
    "secret",
    "passwd",
    "password",
    ".pem",
    "id_rsa",
)
_DEFAULT_CHUNK_CHARS = 800
_TOKEN_DIVISOR = 4


@dataclass(slots=True, frozen=True)
class IngestResult:
    source_path: str
    namespace: str
    status: str  # ingested | unchanged | skipped | error
    chunk_count: int = 0
    detail: str | None = None


class MemoryIngestError(ValueError):
    """Raised when a path is refused or cannot be ingested."""


def is_forbidden_path(path: Path) -> bool:
    """Return True for secrets-like paths that must never be ingested."""

    lowered = str(path).lower()
    name = path.name.lower()
    if name.startswith(".env") or name == "env":
        return True
    return any(part in lowered for part in _FORBIDDEN_NAME_PARTS)


def chunk_text(text: str, *, chunk_chars: int = _DEFAULT_CHUNK_CHARS) -> list[str]:
    """Split text into roughly sized chunks on paragraph boundaries."""

    normalized = text.replace("\r\n", "\n").strip()
    if not normalized:
        return []
    paragraphs = [
        part.strip()
        for part in re.split(r"\n\s*\n", normalized)
        if part.strip()
    ]
    chunks: list[str] = []
    current = ""
    for paragraph in paragraphs:
        candidate = paragraph if not current else f"{current}\n\n{paragraph}"
        if len(candidate) <= chunk_chars:
            current = candidate
            continue
        if current:
            chunks.append(current)
        if len(paragraph) <= chunk_chars:
            current = paragraph
        else:
            for start in range(0, len(paragraph), chunk_chars):
                pieces = paragraph[start : start + chunk_chars].strip()
                if pieces:
                    chunks.append(pieces)
            current = ""
    if current:
        chunks.append(current)
    return chunks


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // _TOKEN_DIVISOR)


def ingest_document(
    store: SqliteMemoryStore,
    *,
    namespace: str,
    path: str | Path,
    title: str | None = None,
    chunk_chars: int = _DEFAULT_CHUNK_CHARS,
) -> IngestResult:
    """Ingest one explicitly configured document path."""

    source = Path(path).expanduser().resolve()
    if is_forbidden_path(source):
        raise MemoryIngestError(f"Refusing to ingest forbidden path: {source}")
    if not source.is_file():
        raise MemoryIngestError(f"Memory document not found: {source}")
    if source.suffix.lower() not in {".md", ".txt", ".markdown"}:
        raise MemoryIngestError(
            f"Unsupported memory document type: {source.suffix or '(none)'}"
        )

    content = source.read_text(encoding="utf-8")
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    existing = store.get_document_hash(
        namespace=namespace,
        source_path=str(source),
    )
    if existing == digest:
        return IngestResult(
            source_path=str(source),
            namespace=namespace,
            status="unchanged",
            chunk_count=0,
        )

    pieces = chunk_text(content, chunk_chars=chunk_chars)
    chunks = [
        (f"c{index:04d}", piece, estimate_tokens(piece))
        for index, piece in enumerate(pieces)
    ]
    store.upsert_document(
        namespace=namespace,
        source_path=str(source),
        content_hash=digest,
        title=title or source.stem,
        metadata={"suffix": source.suffix.lower()},
        chunks=chunks,
    )
    return IngestResult(
        source_path=str(source),
        namespace=namespace,
        status="ingested",
        chunk_count=len(chunks),
    )


def ingest_paths(
    store: SqliteMemoryStore,
    *,
    namespace: str,
    paths: list[str | Path],
) -> list[IngestResult]:
    """Ingest many explicit paths; skip/forbid individually without aborting."""

    results: list[IngestResult] = []
    for path in paths:
        try:
            results.append(
                ingest_document(store, namespace=namespace, path=path)
            )
        except MemoryIngestError as exc:
            results.append(
                IngestResult(
                    source_path=str(path),
                    namespace=namespace,
                    status="error",
                    detail=str(exc),
                )
            )
    return results
