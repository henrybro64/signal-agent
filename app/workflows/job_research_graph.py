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
            return nodes.finalize_node(state)

        state = nodes.rule_score_node(state)
        state = nodes.optional_llm_node(state, provider=provider)
        state = nodes.prepare_digest_node(state)
        state = nodes.persist_results_node(state, db_path=db_path)
        state = nodes.notify_node(state, registry=registry, context=context)
        return nodes.finalize_node(state)
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
        return _state_to_dict(nodes.finalize_node(_dict_to_state(data)))

    workflow.add_node("load_profile", load_profile)
    workflow.add_node("fetch_candidates", fetch_candidates)
    workflow.add_node("rule_score", rule_score)
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
    workflow.add_edge("rule_score", "optional_llm")
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
    }


def _dict_to_state(
    data: dict[str, Any],
    *,
    template: JobResearchState | None = None,
) -> JobResearchState:
    base = template or JobResearchState()
    return JobResearchState(
        profile_name=str(data.get("profile_name") or base.profile_name),
        task_run_id=data.get("task_run_id", base.task_run_id),
        config_path=str(data.get("config_path") or base.config_path),
        candidates=list(data.get("candidates") or []),
        filtered=list(data.get("filtered") or []),
        scoring_summaries=list(data.get("scoring_summaries") or []),
        tool_calls=int(data.get("tool_calls") or 0),
        llm_calls=int(data.get("llm_calls") or 0),
        step_count=int(data.get("step_count") or 0),
        warnings=list(data.get("warnings") or []),
        errors=list(data.get("errors") or []),
        digest_text=str(data.get("digest_text") or ""),
        notification_status=str(
            data.get("notification_status") or base.notification_status
        ),
        fallback_used=bool(data.get("fallback_used", False)),
        status=str(data.get("status") or base.status),
        max_steps=int(data.get("max_steps") or base.max_steps),
        max_tool_calls=int(data.get("max_tool_calls") or base.max_tool_calls),
        max_llm_calls=int(data.get("max_llm_calls") or base.max_llm_calls),
        telegram_enabled=bool(
            data.get("telegram_enabled", base.telegram_enabled)
        ),
        persisted_count=int(data.get("persisted_count") or 0),
        persist_error=(
            None
            if data.get("persist_error") in (None, "")
            else str(data.get("persist_error"))
        ),
        new_candidate_count=int(data.get("new_candidate_count") or 0),
        new_filtered=list(data.get("new_filtered") or []),
    )
