"""Tests for the bounded job-research workflow (#64)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.integrations.langchain.deps import langgraph_available
from app.llm import FakeLLMProvider, LLMProviderError
from app.tools import ToolContext, ToolRegistry, build_tool_registry, builtin_tools
from app.workflows import JobResearchState, run_job_research, run_job_research_python
from app.workflows.job_research_graph import build_job_research_graph
from app.workflows.job_research_nodes import persist_results_node

_DIGEST_GOLDEN_SNIPPETS = (
    "Job research digest",
    "Profile:",
    "Candidates fetched:",
    "Filtered matches:",
)


def _sample_jobs() -> list[dict]:
    return [
        {
            "source": "dummy",
            "item_type": "job",
            "title": "Cloud SDET",
            "observed_at": datetime(2026, 7, 1, tzinfo=UTC).isoformat(),
            "content": "Python automation with Kubernetes.",
            "metadata": {"company": "Example Security"},
            "external_id": "dummy-cloud-sdet",
        },
        {
            "source": "dummy",
            "item_type": "job",
            "title": "Helpdesk Support Engineer",
            "observed_at": datetime(2026, 7, 1, tzinfo=UTC).isoformat(),
            "content": "SLA-heavy support and rotating on-call coverage.",
            "metadata": {"company": "Example Support"},
            "external_id": "dummy-helpdesk",
        },
    ]


def _registry_with_jobs(
    jobs: list[dict] | None = None,
) -> tuple[ToolRegistry, ToolContext]:
    items = _sample_jobs() if jobs is None else jobs
    context = ToolContext(
        fetch_jobs=lambda **_: items,
        telegram_enabled=False,
        timeout_seconds=5,
        db_path=Path(":memory:"),
    )
    return build_tool_registry(), context


def test_job_research_happy_path_digest_shape() -> None:
    registry, context = _registry_with_jobs()
    saved: list[object] = []

    def _upsert(item, *, db_path=None):
        saved.append(item)
        return "inserted"

    # Inject upsert via monkeypatch on node during run by wrapping persist.
    from app.workflows import job_research_nodes as nodes

    original = nodes.persist_results_node

    def _persist(state, *, db_path=None, upsert=None):
        return original(state, db_path=db_path, upsert=_upsert)

    nodes.persist_results_node = _persist  # type: ignore[assignment]
    try:
        provider = FakeLLMProvider(
            responses=["Focus on Cloud SDET for automation fit."],
            validate_structured_json=False,
        )
        final = run_job_research_python(
            JobResearchState(profile_name="default", max_steps=10),
            registry=registry,
            context=context,
            provider=provider,
        )
    finally:
        nodes.persist_results_node = original  # type: ignore[assignment]

    assert final.status.startswith("completed")
    for snippet in _DIGEST_GOLDEN_SNIPPETS:
        assert snippet in final.digest_text
    assert "Cloud SDET" in final.digest_text
    assert final.notification_status == "skipped_disabled"
    assert final.tool_calls >= 1
    assert final.llm_calls == 1
    assert final.persisted_count >= 1
    assert saved


def test_job_research_digest_snapshot_fixture() -> None:
    registry, context = _registry_with_jobs()
    from app.workflows import job_research_nodes as nodes

    original = nodes.persist_results_node
    nodes.persist_results_node = (  # type: ignore[assignment]
        lambda state, *, db_path=None, upsert=None: persist_results_node(
            state, db_path=db_path, upsert=lambda *a, **k: "ok"
        )
    )
    try:
        final = run_job_research_python(
            JobResearchState(profile_name="snapshot_profile", max_steps=10),
            registry=registry,
            context=context,
            provider=None,
        )
    finally:
        nodes.persist_results_node = original  # type: ignore[assignment]

    fixture = Path(__file__).with_name("job_research_digest_golden.txt")
    expected = fixture.read_text(encoding="utf-8").strip()
    # Normalize dynamic score-dependent lines by checking stable skeleton.
    assert final.digest_text.startswith("Job research digest")
    assert "Profile: snapshot_profile" in final.digest_text
    for line in expected.splitlines():
        if line.startswith("#"):
            continue
        assert line in final.digest_text


def test_job_research_no_candidates() -> None:
    registry, context = _registry_with_jobs([])
    from app.workflows import job_research_nodes as nodes

    original = nodes.persist_results_node
    nodes.persist_results_node = (  # type: ignore[assignment]
        lambda state, *, db_path=None, upsert=None: persist_results_node(
            state, db_path=db_path, upsert=lambda *a, **k: "ok"
        )
    )
    try:
        final = run_job_research_python(
            JobResearchState(max_steps=10),
            registry=registry,
            context=context,
            provider=None,
        )
    finally:
        nodes.persist_results_node = original  # type: ignore[assignment]

    assert "no_candidates" in final.warnings
    assert "No candidates fetched" in final.digest_text
    assert final.filtered == []


def test_job_research_llm_failure_falls_back() -> None:
    registry, context = _registry_with_jobs()
    from app.workflows import job_research_nodes as nodes

    original = nodes.persist_results_node
    nodes.persist_results_node = (  # type: ignore[assignment]
        lambda state, *, db_path=None, upsert=None: persist_results_node(
            state, db_path=db_path, upsert=lambda *a, **k: "ok"
        )
    )
    try:
        provider = FakeLLMProvider(responses=[LLMProviderError("boom")])
        final = run_job_research_python(
            JobResearchState(max_steps=10),
            registry=registry,
            context=context,
            provider=provider,
        )
    finally:
        nodes.persist_results_node = original  # type: ignore[assignment]

    assert final.fallback_used is True
    assert any(w.startswith("llm_failed") for w in final.warnings)
    assert "Job research digest" in final.digest_text


def test_job_research_step_limit() -> None:
    registry, context = _registry_with_jobs()
    final = run_job_research_python(
        JobResearchState(max_steps=1),
        registry=registry,
        context=context,
        provider=None,
    )
    assert final.errors
    assert final.fallback_used is True


def test_job_research_telegram_disabled() -> None:
    registry, context = _registry_with_jobs()
    from app.workflows import job_research_nodes as nodes

    original = nodes.persist_results_node
    nodes.persist_results_node = (  # type: ignore[assignment]
        lambda state, *, db_path=None, upsert=None: persist_results_node(
            state, db_path=db_path, upsert=lambda *a, **k: "ok"
        )
    )
    try:
        final = run_job_research_python(
            JobResearchState(telegram_enabled=False, max_steps=10),
            registry=registry,
            context=context,
            provider=None,
        )
    finally:
        nodes.persist_results_node = original  # type: ignore[assignment]
    assert final.notification_status == "skipped_disabled"


def test_job_research_tool_rejection_telegram_not_allowlisted() -> None:
    registry = ToolRegistry(
        builtin_tools(),
        allowlist=["search_jobs", "get_tw_stock_movers", "query_recent_runs"],
    )
    context = ToolContext(
        fetch_jobs=lambda **_: _sample_jobs(),
        telegram_enabled=True,
    )
    from app.workflows import job_research_nodes as nodes

    original = nodes.persist_results_node
    nodes.persist_results_node = (  # type: ignore[assignment]
        lambda state, *, db_path=None, upsert=None: persist_results_node(
            state, db_path=db_path, upsert=lambda *a, **k: "ok"
        )
    )
    try:
        final = run_job_research_python(
            JobResearchState(telegram_enabled=True, max_steps=10),
            registry=registry,
            context=context,
            provider=None,
        )
    finally:
        nodes.persist_results_node = original  # type: ignore[assignment]
    assert final.notification_status == "skipped_not_allowlisted"


def test_job_research_persistence_failure_still_returns_digest() -> None:
    registry, context = _registry_with_jobs()

    def _boom(*_args, **_kwargs):
        raise RuntimeError("disk full")

    from app.workflows import job_research_nodes as nodes

    original = nodes.persist_results_node
    nodes.persist_results_node = (  # type: ignore[assignment]
        lambda state, *, db_path=None, upsert=None: persist_results_node(
            state, db_path=db_path, upsert=_boom
        )
    )
    try:
        final = run_job_research_python(
            JobResearchState(max_steps=10),
            registry=registry,
            context=context,
            provider=None,
        )
    finally:
        nodes.persist_results_node = original  # type: ignore[assignment]

    assert "Job research digest" in final.digest_text
    assert final.status == "completed_with_errors"
    assert any("persist_failed" in err for err in final.errors)


def test_job_research_source_partial_failure_warning() -> None:
    from app.tools.permissions import PermissionClass

    class FlakySearch:
        name = "search_jobs"
        description = "flaky"
        input_schema = {"type": "object", "properties": {}}
        permission = PermissionClass.READ

        @staticmethod
        def invoke(arguments, *, context):
            from app.tools import ToolResult

            return ToolResult(
                ok=True,
                data={"count": 1, "items": _sample_jobs()[:1]},
                metadata={"failed_sources": ["indeed:Timeout"]},
            )

    tools = builtin_tools()
    tools["search_jobs"] = FlakySearch()
    registry = ToolRegistry(
        tools,
        allowlist=["search_jobs", "query_recent_runs", "get_tw_stock_movers"],
    )
    from app.workflows import job_research_nodes as nodes

    original = nodes.persist_results_node
    nodes.persist_results_node = (  # type: ignore[assignment]
        lambda state, *, db_path=None, upsert=None: persist_results_node(
            state, db_path=db_path, upsert=lambda *a, **k: "ok"
        )
    )
    try:
        final = run_job_research_python(
            JobResearchState(max_steps=10),
            registry=registry,
            context=ToolContext(),
            provider=None,
        )
    finally:
        nodes.persist_results_node = original  # type: ignore[assignment]
    assert any("partial_source_failure" in w for w in final.warnings)


@pytest.mark.skipif(not langgraph_available(), reason="langgraph extra not installed")
def test_job_research_langgraph_path_and_no_candidates() -> None:
    registry, context = _registry_with_jobs()
    from app.workflows import job_research_nodes as nodes

    original = nodes.persist_results_node
    nodes.persist_results_node = (  # type: ignore[assignment]
        lambda state, *, db_path=None, upsert=None: persist_results_node(
            state, db_path=db_path, upsert=lambda *a, **k: "ok"
        )
    )
    try:
        provider = FakeLLMProvider(
            responses=["LangGraph note"],
            validate_structured_json=False,
        )
        final = run_job_research(
            JobResearchState(profile_name="default", max_steps=10),
            registry=registry,
            context=context,
            provider=provider,
            use_langgraph=True,
        )
        assert "Cloud SDET" in final.digest_text

        empty_registry, empty_context = _registry_with_jobs([])
        empty = run_job_research(
            JobResearchState(max_steps=10),
            registry=empty_registry,
            context=empty_context,
            provider=None,
            use_langgraph=True,
        )
        assert "no_candidates" in empty.warnings
        assert "No candidates fetched" in empty.digest_text
    finally:
        nodes.persist_results_node = original  # type: ignore[assignment]

    graph = build_job_research_graph(
        registry=registry, context=context, provider=None
    )
    assert graph is not None


def test_rule_score_sorts_by_score_descending() -> None:
    from app.workflows.job_research_nodes import rule_score_node

    state = JobResearchState(
        candidates=[
            {
                "source": "dummy",
                "item_type": "job",
                "title": "Helpdesk Support Engineer",
                "observed_at": datetime(2026, 7, 1, tzinfo=UTC).isoformat(),
                "content": "SLA-heavy support and rotating on-call coverage.",
                "metadata": {"company": "Example Support"},
                "external_id": "low",
            },
            {
                "source": "dummy",
                "item_type": "job",
                "title": "Cloud SDET",
                "observed_at": datetime(2026, 7, 1, tzinfo=UTC).isoformat(),
                "content": "Python automation with Kubernetes.",
                "metadata": {"company": "Example Security"},
                "external_id": "high",
            },
        ],
        max_steps=10,
    )
    scored = rule_score_node(state)
    assert scored.filtered
    scores = [item["rule_score"] for item in scored.filtered]
    assert scores == sorted(scores, reverse=True)
    assert scored.filtered[0]["title"] == "Cloud SDET"


def test_persist_uses_distinct_research_identity() -> None:
    saved: list[object] = []

    def _upsert(item, *, db_path=None):
        saved.append(item)

        class _Result:
            action = "inserted"

        return _Result()

    state = JobResearchState(
        filtered=[
            {
                "source": "job104",
                "item_type": "job",
                "title": "Cloud SDET",
                "observed_at": datetime(2026, 7, 1, tzinfo=UTC).isoformat(),
                "url": "https://example.com/jobs/1",
                "external_id": "job104-1",
                "content": "x" * 600,
                "metadata": {},
                "rule_score": 80,
            }
        ],
        digest_text="Job research digest",
        max_steps=10,
    )
    persist_results_node(state, upsert=_upsert)
    assert state.new_candidate_count == 1
    candidate = saved[0]
    assert candidate.source == "job_research_agent"
    assert candidate.item_type == "job_research_candidate"
    assert candidate.url is None
    assert candidate.external_id.startswith("job-research:job104:")
    assert candidate.metadata["origin_url"] == "https://example.com/jobs/1"


def test_notify_propagates_telegram_failure_as_error() -> None:
    from app.tools.permissions import (
        PermissionClass,
        PermissionPolicyConfig,
        build_permission_policy,
    )

    class FailTelegram:
        name = "send_telegram_message"
        description = "fail"
        input_schema = {
            "type": "object",
            "required": ["text"],
            "properties": {"text": {"type": "string"}},
        }
        permission = PermissionClass.NOTIFY

        @staticmethod
        def invoke(arguments, *, context):
            from app.tools import ToolResult

            return ToolResult(ok=False, error="telegram_http_failed")

    tools = builtin_tools()
    tools["send_telegram_message"] = FailTelegram()
    registry = ToolRegistry(tools, allowlist=["send_telegram_message", "search_jobs"])
    from app.workflows.job_research_nodes import notify_node

    state = JobResearchState(
        telegram_enabled=True,
        digest_text="hi",
        new_candidate_count=1,
        filtered=[{"title": "x"}],
        max_steps=10,
    )
    notify_node(
        state,
        registry=registry,
        context=ToolContext(
            telegram_enabled=True,
            permission_policy=build_permission_policy(
                PermissionPolicyConfig(classes={"notify": "allow"})
            ),
        ),
    )
    assert state.notification_status == "failed"
    assert "telegram_http_failed" in state.errors


def test_fetch_promotes_total_source_outage_to_error() -> None:
    from app.tools.permissions import PermissionClass

    class DeadSearch:
        name = "search_jobs"
        description = "dead"
        input_schema = {"type": "object", "properties": {}}
        permission = PermissionClass.READ

        @staticmethod
        def invoke(arguments, *, context):
            from app.tools import ToolResult

            return ToolResult(
                ok=True,
                data={"count": 0, "items": []},
                metadata={
                    "failed_sources": ["job104:Timeout", "indeed:Timeout"],
                    "source_count": 2,
                },
            )

    tools = builtin_tools()
    tools["search_jobs"] = DeadSearch()
    registry = ToolRegistry(
        tools,
        allowlist=["search_jobs", "query_recent_runs", "get_tw_stock_movers"],
    )
    from app.workflows.job_research_nodes import fetch_candidates_node

    state = fetch_candidates_node(
        JobResearchState(max_steps=10),
        registry=registry,
        context=ToolContext(),
    )
    assert any("all_job_sources_failed" in err for err in state.errors)
