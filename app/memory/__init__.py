"""Durable agent memory and retrieval (#65).

Independent of LangChain/LangGraph. Vector backends remain optional.
"""

from .embeddings import EmbeddingProvider, FakeEmbeddingProvider
from .ingest import (
    IngestResult,
    MemoryIngestError,
    chunk_text,
    ingest_document,
    ingest_paths,
    is_forbidden_path,
)
from .models import (
    MemoryChunk,
    MemoryDocument,
    OperationalMemoryRecord,
    RetrievedEvidence,
)
from .retriever import HybridRetriever, KeywordRetriever, build_retriever
from .sqlite_store import SqliteMemoryStore

__all__ = [
    "EmbeddingProvider",
    "FakeEmbeddingProvider",
    "HybridRetriever",
    "IngestResult",
    "KeywordRetriever",
    "MemoryChunk",
    "MemoryDocument",
    "MemoryIngestError",
    "OperationalMemoryRecord",
    "RetrievedEvidence",
    "SqliteMemoryStore",
    "build_retriever",
    "chunk_text",
    "ingest_document",
    "ingest_paths",
    "is_forbidden_path",
]
