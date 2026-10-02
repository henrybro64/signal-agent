"""Project-owned memory models (framework-neutral)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(slots=True, frozen=True)
class MemoryDocument:
    """One ingested source document under a namespace."""

    id: int
    namespace: str
    source_path: str
    content_hash: str
    title: str | None
    created_at: datetime
    updated_at: datetime
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True, frozen=True)
class MemoryChunk:
    """One chunk of an ingested document."""

    id: int
    document_id: int
    chunk_id: str
    content: str
    token_estimate: int
    ordinal: int
    namespace: str = ""
    source_path: str = ""
    title: str | None = None


@dataclass(slots=True, frozen=True)
class RetrievedEvidence:
    """Bounded evidence returned to a workflow/LLM call."""

    chunk_id: str
    content: str
    source_path: str
    namespace: str
    score: float
    title: str | None = None
    citation: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "chunk_id": self.chunk_id,
            "content": self.content,
            "source_path": self.source_path,
            "namespace": self.namespace,
            "score": self.score,
            "title": self.title,
            "citation": self.citation or f"{self.source_path}#{self.chunk_id}",
        }


@dataclass(slots=True, frozen=True)
class OperationalMemoryRecord:
    """Operational state such as run summaries or tool outcomes."""

    id: int
    namespace: str
    kind: str
    key: str
    payload: dict[str, Any]
    created_at: datetime
    expires_at: datetime | None = None
