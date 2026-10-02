"""Memory and retriever protocols."""

from __future__ import annotations

from typing import Protocol

from .embeddings import EmbeddingProvider
from .models import OperationalMemoryRecord, RetrievedEvidence

__all__ = [
    "EmbeddingProvider",
    "MemoryStore",
    "Retriever",
]


class MemoryStore(Protocol):
    """Durable store for documents, chunks, and operational memory."""

    def delete_namespace(self, namespace: str) -> int:
        """Delete all documents/chunks/ops for a namespace; return rows touched."""

    def delete_document(self, *, namespace: str, source_path: str) -> bool:
        """Delete one document and its chunks."""

    def list_documents(self, namespace: str) -> list[object]:
        """List documents in a namespace."""

    def put_operational(
        self,
        *,
        namespace: str,
        kind: str,
        key: str,
        payload: dict,
        expires_at: object | None = None,
    ) -> OperationalMemoryRecord:
        """Upsert operational memory."""

    def get_operational(
        self,
        *,
        namespace: str,
        kind: str,
        key: str,
    ) -> OperationalMemoryRecord | None:
        """Fetch one operational record."""


class Retriever(Protocol):
    """Retrieve bounded evidence for a query within a namespace."""

    def retrieve(
        self,
        *,
        namespace: str,
        query: str,
        limit: int = 5,
        max_chars: int = 2000,
    ) -> list[RetrievedEvidence]:
        """Return ranked evidence with citations."""
