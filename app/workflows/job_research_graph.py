"""Bounded job-research runners (pure Python + optional LangGraph)."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from app.llm import LLMProvider
from app.tools import ToolContext, ToolRegistry, build_tool_registry

from . import job_research_nodes as nodes
from .state import JobResearchState


def run_job_research(
    state: JobResearchState | None = None,
    *,
    registry: ToolRegistry | None = None,
    context: ToolContext | None = None,
    provider: LLMProvider | None = None,
    use_langgraph: bool | None = None,
) -> JobResearchState:
    """Execute the bounded job-research pilot.

    Prefer LangGraph when available and requested; otherwise run the same
    node sequence in pure Python (CI-safe fallback).

    Note: the optional LangGraph path uses ``langgraph`` directly for the
    fixed node graph. ``app.integrations.langchain`` adapters (#63) remain
    available for ChatModel/tool bridging elsewhere; this pilot depends on
    #63 mainly for the optional LangGraph install/deps gate.
    """

    current = state or JobResearchState()
    tool_registry = registry or build_tool_registry(
        include_telegram=current.telegram_enabled,
    )
    tool_context = context or ToolContext(telegram_enabled=current.telegram_enabled)

    if use_langgraph is None:
        from app.integrations.langchain.deps import langgraph_available

        use_langgraph = langgraph_available()

    if use_langgraph:
        try:
            return run_job_research_langgraph(
                current,
                registry=tool_registry,
                context=tool_context,
                provider=provider,
            )
        except Exception as exc:  # noqa: BLE001 - fall back without double Telegram
            current.warnings.append(f"langgraph_failed:{type(exc).__name__}")
            current.fallback_used = True
            safe_context = replace(tool_context, telegram_enabled=False)
            reset = JobResearchState(
                profile_name=current.profile_name,
                task_run_id=current.task_run_id,
                config_path=current.config_path,
                max_steps=current.max_steps,
                max_tool_calls=current.max_tool_calls,
                max_llm_calls=current.max_llm_calls,
                telegram_enabled=False,
                warnings=list(current.warnings),
                fallback_used=True,
                memory_namespace=current.memory_namespace,
                memory_document_paths=list(current.memory_document_paths),
                tools_used=list(current.tools_used),
            )
            return run_job_research_python(
                reset,
                registry=tool_registry,
                context=safe_context,
                provider=provider,
            )

    return run_job_research_python(
        current,
        registry=tool_registry,
        context=tool_context,
        provider=provider,
    )


def run_job_research_python(
    state: JobResearchState,
    *,
    registry: ToolRegistry,
    context: ToolContext,
    provider: LLMProvider | None,
) -> JobResearchState:
    """Deterministic sequential runner with the same node order as the graph."""

    db_path = context.db_path

    try:
        state = nodes.load_profile_node(state)
        state = nodes.fetch_candidates_node(
            state, registry=registry, context=context
        )
        if not state.candidates:
            state = nodes.prepare_digest_node(state)
            state = nodes.persist_results_node(state, db_path=db_path)
            state = nodes.notify_node(state, registry=registry, context=context)
            return nodes.finalize_node(state, db_path=db_path)

        state = nodes.rule_score_node(state)
        state = nodes.retrieve_evidence_node(state, db_path=db_path)
        state = nodes.optional_llm_node(state, provider=provider)
        state = nodes.prepare_digest_node(state)
        state = nodes.persist_results_node(state, db_path=db_path)
        state = nodes.notify_node(state, registry=registry, context=context)
        return nodes.finalize_node(state, db_path=db_path)
    except RuntimeError as exc:
        state.errors.append(str(exc))
        state.fallback_used = True
        if not state.digest_text:
            state.digest_text = (
                "Job research digest\n"
                f"Profile: {state.profile_name}\n"
                "Research stopped after hitting safety limits.\n"
                "Errors: " + "; ".join(state.errors)
            )
        state.status = "completed_with_errors"
        nodes.store_operational_summary(state, db_path=db_path)
        return state


def run_job_research_langgraph(
    state: JobResearchState,
    *,
    registry: ToolRegistry,
    context: ToolContext,
    provider: LLMProvider | None,
) -> JobResearchState:
    """Compile and invoke the LangGraph pilot (requires .[langchain])."""

    from app.integrations.langchain.deps import require_langgraph

    require_langgraph()
    graph = build_job_research_graph(
        registry=registry,
        context=context,
        provider=provider,
    )
    payload = _state_to_dict(state)
    final_payload = graph.invoke(payload)
    return _dict_to_state(final_payload, template=state)


def build_job_research_graph(
    *,
    registry: ToolRegistry,
    context: ToolContext,
    provider: LLMProvider | None,
) -> Any:
    """Build a compiled LangGraph StateGraph for job research."""

    from app.integrations.langchain.deps import require_langgraph

    require_langgraph()
    from langgraph.graph import END, StateGraph

    workflow: Any = StateGraph(dict)
    db_path = context.db_path

    def load_profile(data: dict[str, Any]) -> dict[str, Any]:
        state = _dict_to_state(data)
        return _state_to_dict(nodes.load_profile_node(state))

    def fetch_candidates(data: dict[str, Any]) -> dict[str, Any]:
        state = _dict_to_state(data)
        return _state_to_dict(
            nodes.fetch_candidates_node(
                state, registry=registry, context=context
            )
        )

    def rule_score(data: dict[str, Any]) -> dict[str, Any]:
        return _state_to_dict(nodes.rule_score_node(_dict_to_state(data)))

    def retrieve_evidence(data: dict[str, Any]) -> dict[str, Any]:
        return _state_to_dict(
            nodes.retrieve_evidence_node(_dict_to_state(data), db_path=db_path)
        )

    def optional_llm(data: dict[str, Any]) -> dict[str, Any]:
        return _state_to_dict(
            nodes.optional_llm_node(_dict_to_state(data), provider=provider)
        )

    def prepare_digest(data: dict[str, Any]) -> dict[str, Any]:
        return _state_to_dict(nodes.prepare_digest_node(_dict_to_state(data)))

    def persist_results(data: dict[str, Any]) -> dict[str, Any]:
        return _state_to_dict(
            nodes.persist_results_node(_dict_to_state(data), db_path=db_path)
        )

    def notify(data: dict[str, Any]) -> dict[str, Any]:
        state = _dict_to_state(data)
        return _state_to_dict(
            nodes.notify_node(state, registry=registry, context=context)
        )

    def finalize(data: dict[str, Any]) -> dict[str, Any]:
        return _state_to_dict(
            nodes.finalize_node(_dict_to_state(data), db_path=db_path)
        )

    workflow.add_node("load_profile", load_profile)
    workflow.add_node("fetch_candidates", fetch_candidates)
    workflow.add_node("rule_score", rule_score)
    workflow.add_node("retrieve_evidence", retrieve_evidence)
    workflow.add_node("optional_llm", optional_llm)
    workflow.add_node("prepare_digest", prepare_digest)
    workflow.add_node("persist_results", persist_results)
    workflow.add_node("notify", notify)
    workflow.add_node("finalize", finalize)

    workflow.set_entry_point("load_profile")
    workflow.add_edge("load_profile", "fetch_candidates")

    def after_fetch(data: dict[str, Any]) -> str:
        candidates = data.get("candidates") or []
        if not candidates:
            return "prepare_digest"
        return "rule_score"

    workflow.add_conditional_edges(
        "fetch_candidates",
        after_fetch,
        {
            "rule_score": "rule_score",
            "prepare_digest": "prepare_digest",
        },
    )
    workflow.add_edge("rule_score", "retrieve_evidence")
    workflow.add_edge("retrieve_evidence", "optional_llm")
    workflow.add_edge("optional_llm", "prepare_digest")
    workflow.add_edge("prepare_digest", "persist_results")
    workflow.add_edge("persist_results", "notify")
    workflow.add_edge("notify", "finalize")
    workflow.add_edge("finalize", END)
    return workflow.compile()


def _state_to_dict(state: JobResearchState) -> dict[str, Any]:
    return {
        "profile_name": state.profile_name,
        "task_run_id": state.task_run_id,
        "config_path": state.config_path,
        "candidates": list(state.candidates),
        "filtered": list(state.filtered),
        "scoring_summaries": list(state.scoring_summaries),
        "tool_calls": state.tool_calls,
        "llm_calls": state.llm_calls,
        "step_count": state.step_count,
        "warnings": list(state.warnings),
        "errors": list(state.errors),
        "digest_text": state.digest_text,
        "notification_status": state.notification_status,
        "fallback_used": state.fallback_used,
        "status": state.status,
        "max_steps": state.max_steps,
        "max_tool_calls": state.max_tool_calls,
        "max_llm_calls": state.max_llm_calls,
        "telegram_enabled": state.telegram_enabled,
        "persisted_count": state.persisted_count,
        "persist_error": state.persist_error,
        "new_candidate_count": state.new_candidate_count,
        "new_filtered": list(state.new_filtered),
        "evidence": list(state.evidence),
        "memory_namespace": state.memory_namespace,
        "memory_document_paths": list(state.memory_document_paths),
        "tools_used": list(state.tools_used),
    }


def _dict_to_state(
    data: dict[str, Any],
    *,
    template: JobResearchState | None = None,
) -> JobResearchState:
    base = template or JobResearchState()
    persist_error = data.get("persist_error")
    return JobResearchState(
        profile_name=_str_or(data, "profile_name", base.profile_name),
        task_run_id=data.get("task_run_id", base.task_run_id),
        config_path=_str_or(data, "config_path", base.config_path),
        candidates=_list_or(data, "candidates"),
        filtered=_list_or(data, "filtered"),
        scoring_summaries=_list_or(data, "scoring_summaries"),
        tool_calls=_int_or(data, "tool_calls"),
        llm_calls=_int_or(data, "llm_calls"),
        step_count=_int_or(data, "step_count"),
        warnings=_list_or(data, "warnings"),
        errors=_list_or(data, "errors"),
        digest_text=_str_or(data, "digest_text", ""),
        notification_status=_str_or(
            data, "notification_status", base.notification_status
        ),
        fallback_used=bool(data.get("fallback_used", False)),
        status=_str_or(data, "status", base.status),
        max_steps=_int_or(data, "max_steps", base.max_steps),
        max_tool_calls=_int_or(data, "max_tool_calls", base.max_tool_calls),
        max_llm_calls=_int_or(data, "max_llm_calls", base.max_llm_calls),
        telegram_enabled=bool(data.get("telegram_enabled", base.telegram_enabled)),
        persisted_count=_int_or(data, "persisted_count"),
        persist_error=None if persist_error in (None, "") else str(persist_error),
        new_candidate_count=_int_or(data, "new_candidate_count"),
        new_filtered=_list_or(data, "new_filtered"),
        evidence=_list_or(data, "evidence"),
        memory_namespace=_str_or(
            data, "memory_namespace", base.memory_namespace
        ),
        memory_document_paths=_list_or(
            data, "memory_document_paths", base.memory_document_paths
        ),
        tools_used=_list_or(data, "tools_used", base.tools_used),
    )


def _str_or(data: dict[str, Any], key: str, default: str) -> str:
    return str(data.get(key) or default)


def _int_or(data: dict[str, Any], key: str, default: int = 0) -> int:
    return int(data.get(key) or default)


def _list_or(
    data: dict[str, Any],
    key: str,
    default: list[Any] | None = None,
) -> list[Any]:
    if key in data:
        return list(data.get(key) or [])
    return list(default or [])
