"""Tests for durable agent memory (#65)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.core import init_db, read_schema_version
from app.core.migrations import latest_migration_version
from app.memory import (
    FakeEmbeddingProvider,
    HybridRetriever,
    KeywordRetriever,
    MemoryIngestError,
    SqliteMemoryStore,
    build_retriever,
    chunk_text,
    ingest_document,
    ingest_paths,
    is_forbidden_path,
)
from app.tools import ToolContext, build_tool_registry
from app.workflows import JobResearchState, run_job_research_python


def test_migration_creates_memory_tables(tmp_path: Path) -> None:
    db_path = tmp_path / "memory.sqlite3"
    init_db(db_path)
    assert read_schema_version(db_path) == latest_migration_version()
    assert latest_migration_version() >= 2
    store = SqliteMemoryStore(db_path)
    assert store.list_documents("job_research") == []


def test_forbidden_paths_refused(tmp_path: Path) -> None:
    assert is_forbidden_path(tmp_path / ".env")
    assert is_forbidden_path(tmp_path / "secrets" / "token.txt")
    store = SqliteMemoryStore(tmp_path / "db.sqlite3")
    env_path = tmp_path / ".env"
    env_path.write_text("OPENAI_API_KEY=sk-test\n", encoding="utf-8")
    with pytest.raises(MemoryIngestError, match="forbidden"):
        ingest_document(store, namespace="job_research", path=env_path)


def test_ingest_unchanged_and_reindex(tmp_path: Path) -> None:
    db_path = tmp_path / "db.sqlite3"
    store = SqliteMemoryStore(db_path)
    doc = tmp_path / "resume.md"
    doc.write_text(
        "Cloud SDET with Python.\n\nKubernetes automation.",
        encoding="utf-8",
    )

    first = ingest_document(store, namespace="job_research", path=doc)
    assert first.status == "ingested"
    assert first.chunk_count >= 1

    second = ingest_document(store, namespace="job_research", path=doc)
    assert second.status == "unchanged"

    doc.write_text("Cloud SDET with Go.\n\nKubernetes automation.", encoding="utf-8")
    third = ingest_document(store, namespace="job_research", path=doc)
    assert third.status == "ingested"
    docs = store.list_documents("job_research")
    assert len(docs) == 1


def test_namespace_isolation_and_keyword_ranking(tmp_path: Path) -> None:
    db_path = tmp_path / "db.sqlite3"
    store = SqliteMemoryStore(db_path)
    job_doc = tmp_path / "jobs.md"
    stock_doc = tmp_path / "stocks.md"
    job_doc.write_text("Prefer Cloud SDET roles with Python.", encoding="utf-8")
    stock_doc.write_text("Watch semiconductor movers on TWSE.", encoding="utf-8")
    ingest_document(store, namespace="job_research", path=job_doc)
    ingest_document(store, namespace="tw_market", path=stock_doc)

    retriever = KeywordRetriever(store)
    job_hits = retriever.retrieve(namespace="job_research", query="Cloud SDET Python")
    stock_hits = retriever.retrieve(namespace="tw_market", query="Cloud SDET Python")
    assert job_hits
    assert all(item.namespace == "job_research" for item in job_hits)
    assert not stock_hits
    assert job_hits[0].citation.endswith("#c0000") or "#" in job_hits[0].citation


def test_hybrid_embeddings_optional(tmp_path: Path) -> None:
    db_path = tmp_path / "db.sqlite3"
    store = SqliteMemoryStore(db_path)
    doc = tmp_path / "notes.md"
    doc.write_text("Python automation Kubernetes Cloud SDET", encoding="utf-8")
    ingest_document(store, namespace="job_research", path=doc)

    keyword_only = build_retriever(store, use_embeddings=False)
    assert isinstance(keyword_only, KeywordRetriever)

    hybrid = HybridRetriever(
        store,
        embeddings=FakeEmbeddingProvider(),
        use_embeddings=True,
    )
    hits = hybrid.retrieve(namespace="job_research", query="Python Kubernetes")
    assert hits
    assert hits[0].citation


def test_prompt_limits_and_deletion(tmp_path: Path) -> None:
    db_path = tmp_path / "db.sqlite3"
    store = SqliteMemoryStore(db_path)
    doc = tmp_path / "long.md"
    doc.write_text("alpha beta\n\n" + ("gamma delta " * 200), encoding="utf-8")
    ingest_document(store, namespace="job_research", path=doc, chunk_chars=120)
    retriever = KeywordRetriever(store)
    hits = retriever.retrieve(
        namespace="job_research",
        query="gamma delta alpha",
        limit=2,
        max_chars=180,
    )
    assert len(hits) <= 2
    assert sum(len(item.content) for item in hits) <= 200

    assert store.delete_document(
        namespace="job_research",
        source_path=str(doc.resolve()),
    )
    assert store.list_chunks("job_research") == []
    deleted = store.delete_namespace("job_research")
    assert deleted >= 0


def test_operational_memory_expiry(tmp_path: Path) -> None:
    store = SqliteMemoryStore(tmp_path / "db.sqlite3")
    past = datetime.now(UTC) - timedelta(hours=1)
    store.put_operational(
        namespace="job_research",
        kind="run_summary",
        key="run-1",
        payload={"status": "ok"},
        expires_at=past,
    )
    assert store.get_operational(
        namespace="job_research",
        kind="run_summary",
        key="run-1",
    ) is None
    store.put_operational(
        namespace="job_research",
        kind="run_summary",
        key="run-2",
        payload={"status": "ok"},
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    assert store.get_operational(
        namespace="job_research",
        kind="run_summary",
        key="run-2",
    ) is not None


def test_ingest_paths_skips_errors(tmp_path: Path) -> None:
    store = SqliteMemoryStore(tmp_path / "db.sqlite3")
    good = tmp_path / "ok.md"
    good.write_text("hello", encoding="utf-8")
    results = ingest_paths(
        store,
        namespace="job_research",
        paths=[good, tmp_path / ".env", tmp_path / "missing.md"],
    )
    statuses = {row.status for row in results}
    assert "ingested" in statuses
    assert "error" in statuses


def test_ingest_rejects_secret_content_and_read_errors(tmp_path: Path) -> None:
    store = SqliteMemoryStore(tmp_path / "db.sqlite3")
    secret_doc = tmp_path / "notes.md"
    secret_doc.write_text("api_key: sk-abcdefghijklmnop\n", encoding="utf-8")
    with pytest.raises(MemoryIngestError, match="secret-bearing"):
        ingest_document(store, namespace="job_research", path=secret_doc)

    results = ingest_paths(
        store,
        namespace="job_research",
        paths=[secret_doc],
    )
    assert results[0].status == "error"

    unreadable = tmp_path / "broken.md"
    unreadable.write_bytes(b"\xff\xfe not utf-8")
    bad = ingest_paths(store, namespace="job_research", paths=[unreadable])
    assert bad[0].status == "error"


def test_cjk_keyword_retrieval_and_first_chunk_budget(tmp_path: Path) -> None:
    store = SqliteMemoryStore(tmp_path / "db.sqlite3")
    doc = tmp_path / "tw-jobs.md"
    doc.write_text("雲端測試工程師，熟悉自動化與Kubernetes。", encoding="utf-8")
    ingest_document(store, namespace="job_research", path=doc)
    hits = KeywordRetriever(store).retrieve(
        namespace="job_research",
        query="雲端測試工程師",
        limit=1,
        max_chars=40,
    )
    assert hits
    assert "雲端" in hits[0].content or "測試" in hits[0].content
    assert len(hits[0].content) <= 41


def test_chunk_text_splits_paragraphs() -> None:
    chunks = chunk_text("one\n\ntwo\n\nthree", chunk_chars=8)
    assert len(chunks) >= 2


def test_job_research_includes_memory_evidence(tmp_path: Path) -> None:
    db_path = tmp_path / "db.sqlite3"
    store = SqliteMemoryStore(db_path)
    notes = tmp_path / "company-notes.md"
    notes.write_text(
        "Cloud SDET notes for Example Security with Python.",
        encoding="utf-8",
    )
    ingest_document(store, namespace="job_research", path=notes)

    jobs = [
        {
            "source": "dummy",
            "item_type": "job",
            "title": "Cloud SDET",
            "observed_at": "2026-07-01T00:00:00+00:00",
            "content": "Python automation with Kubernetes.",
            "metadata": {"company": "Example Security"},
        }
    ]
    context = ToolContext(
        fetch_jobs=lambda **_: jobs,
        telegram_enabled=False,
        db_path=db_path,
    )
    final = run_job_research_python(
        JobResearchState(
            profile_name="default",
            max_steps=12,
            task_run_id=42,
            memory_document_paths=[str(notes)],
        ),
        registry=build_tool_registry(),
        context=context,
        provider=None,
    )
    assert final.status.startswith("completed")
    assert final.evidence
    assert "Evidence:" in final.digest_text
    assert "company-notes.md" in final.digest_text
    assert "search_jobs" in final.tools_used
    record = store.get_operational(
        namespace="job_research",
        kind="run_summary",
        key="task-42",
    )
    assert record is not None
    assert record.payload.get("task_run_id") == 42


def test_optional_llm_receives_evidence_citations(tmp_path: Path) -> None:
    from app.llm import FakeLLMProvider
    from app.workflows.job_research_nodes import optional_llm_node

    captured: list[str] = []

    class CapturingProvider(FakeLLMProvider):
        def generate(self, request):  # type: ignore[no-untyped-def]
            captured.append(request.input)
            return super().generate(request)

    state = JobResearchState(
        filtered=[{"title": "Cloud SDET"}],
        evidence=[
            {
                "citation": "notes.md#c0000",
                "content": "Prefer Python automation",
                "source_path": "notes.md",
            }
        ],
        max_llm_calls=2,
    )
    optional_llm_node(
        state,
        provider=CapturingProvider(
            responses=["ok"],
            validate_structured_json=False,
        ),
    )
    assert captured
    assert "Evidence citations:" in captured[0]
    assert "notes.md#c0000" in captured[0]
