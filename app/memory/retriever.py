"""Keyword and optional vector retrieval over memory chunks."""

from __future__ import annotations

import math
import re
from collections import Counter

from .embeddings import EmbeddingProvider, FakeEmbeddingProvider
from .models import MemoryChunk, RetrievedEvidence
from .sqlite_store import SqliteMemoryStore

_LATIN_TOKEN_RE = re.compile(r"[a-z0-9_]+", re.IGNORECASE)
_CJK_RUN_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]+")


def _tokenize(text: str) -> list[str]:
    """Tokenize Latin words and CJK character runs (bigrams + short phrases)."""

    tokens: list[str] = [
        token.lower() for token in _LATIN_TOKEN_RE.findall(text) if len(token) > 1
    ]
    for run in _CJK_RUN_RE.findall(text):
        if len(run) == 1:
            tokens.append(run)
            continue
        tokens.extend(run[index : index + 2] for index in range(len(run) - 1))
        if len(run) <= 4:
            tokens.append(run)
    return tokens


class KeywordRetriever:
    """Deterministic keyword retrieval ranked by overlap score."""

    def __init__(self, store: SqliteMemoryStore) -> None:
        self.store = store

    def retrieve(
        self,
        *,
        namespace: str,
        query: str,
        limit: int = 5,
        max_chars: int = 2000,
    ) -> list[RetrievedEvidence]:
        query_tokens = _tokenize(query)
        if not query_tokens or limit <= 0:
            return []

        query_counts = Counter(query_tokens)
        scored: list[tuple[float, MemoryChunk]] = []
        for chunk in self.store.list_chunks(namespace):
            chunk_tokens = _tokenize(chunk.content)
            if not chunk_tokens:
                continue
            chunk_counts = Counter(chunk_tokens)
            overlap = sum(
                min(query_counts[token], chunk_counts[token])
                for token in query_counts
            )
            if overlap <= 0:
                continue
            # Prefer denser matches and shorter chunks slightly.
            density = overlap / max(1, len(chunk_tokens))
            score = float(overlap) + density
            scored.append((score, chunk))

        scored.sort(key=lambda item: (-item[0], item[1].source_path, item[1].ordinal))
        evidence: list[RetrievedEvidence] = []
        used_chars = 0
        for score, chunk in scored:
            if len(evidence) >= limit:
                break
            remaining = max_chars - used_chars
            if remaining <= 0:
                break
            content = chunk.content
            if len(content) > remaining:
                # Honor the budget for the first chunk as well as later ones.
                if remaining < 80 and evidence:
                    break
                content = content[:remaining] + "…"
            evidence.append(
                RetrievedEvidence(
                    chunk_id=chunk.chunk_id,
                    content=content,
                    source_path=chunk.source_path,
                    namespace=chunk.namespace,
                    score=score,
                    title=chunk.title,
                    citation=f"{chunk.source_path}#{chunk.chunk_id}",
                )
            )
            used_chars += len(content)
        return evidence


class HybridRetriever:
    """Keyword-first retriever with optional embedding re-rank."""

    def __init__(
        self,
        store: SqliteMemoryStore,
        *,
        embeddings: EmbeddingProvider | None = None,
        use_embeddings: bool = False,
    ) -> None:
        self.store = store
        self.keyword = KeywordRetriever(store)
        self.embeddings = embeddings
        self.use_embeddings = use_embeddings

    def retrieve(
        self,
        *,
        namespace: str,
        query: str,
        limit: int = 5,
        max_chars: int = 2000,
    ) -> list[RetrievedEvidence]:
        base = self.keyword.retrieve(
            namespace=namespace,
            query=query,
            limit=max(limit * 3, limit),
            max_chars=max_chars * 2,
        )
        if not self.use_embeddings or self.embeddings is None or not base:
            return base[:limit]

        vectors = self.embeddings.embed([query, *[item.content for item in base]])
        if len(vectors) != len(base) + 1:
            return base[:limit]
        query_vec = vectors[0]
        rescored: list[RetrievedEvidence] = []
        for item, vector in zip(base, vectors[1:], strict=True):
            score = _cosine(query_vec, vector) + (item.score * 0.01)
            rescored.append(
                RetrievedEvidence(
                    chunk_id=item.chunk_id,
                    content=item.content,
                    source_path=item.source_path,
                    namespace=item.namespace,
                    score=score,
                    title=item.title,
                    citation=item.citation,
                )
            )
        rescored.sort(key=lambda row: (-row.score, row.source_path, row.chunk_id))
        return rescored[:limit]


def _cosine(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(a * a for a in left))
    right_norm = math.sqrt(sum(b * b for b in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return dot / (left_norm * right_norm)


def build_retriever(
    store: SqliteMemoryStore,
    *,
    use_embeddings: bool = False,
) -> KeywordRetriever | HybridRetriever:
    """Factory that keeps embeddings optional and CI-safe."""

    if not use_embeddings:
        return KeywordRetriever(store)
    return HybridRetriever(
        store,
        embeddings=FakeEmbeddingProvider(),
        use_embeddings=True,
    )
