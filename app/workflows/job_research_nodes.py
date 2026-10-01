"""Node helpers for the bounded job-research pilot."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.core.logging import get_logger, log_event
from app.llm import LLMProvider, LLMRequest
from app.scorers import JobRuleScorer, load_job_search_profile
from app.tools import ToolContext, ToolRegistry

from .state import JobResearchState

_LOGGER = get_logger("workflows.job_research")


def load_profile_node(state: JobResearchState) -> JobResearchState:
    state.bump_step()
    profile = load_job_search_profile()
    state.profile_name = state.profile_name or "default"
    if not profile.target_roles:
        state.warnings.append("profile_missing_target_roles")
    log_event(
        _LOGGER,
        event="job_research_node",
        message="loaded profile",
        node="load_profile",
        step=state.step_count,
        profile_name=state.profile_name,
    )
    return state


def fetch_candidates_node(
    state: JobResearchState,
    *,
    registry: ToolRegistry,
    context: ToolContext,
) -> JobResearchState:
    state.bump_step()
    if state.tool_calls >= state.max_tool_calls:
        state.warnings.append("tool_budget_exhausted_before_fetch")
        return state

    result = registry.invoke("search_jobs", {"limit": 20}, context=context)
    state.tool_calls += 1
    if not result.ok:
        state.errors.append(result.error or "search_jobs_failed")
        state.warnings.append("fetch_failed")
        return state

    items = result.data.get("items") or []
    if not isinstance(items, list):
        state.errors.append("search_jobs_bad_payload")
        return state
    state.candidates = [item for item in items if isinstance(item, dict)]
    failed = result.metadata.get("failed_sources") or []
    if failed:
        state.warnings.append(f"partial_source_failure:{','.join(map(str, failed))}")
    log_event(
        _LOGGER,
        event="job_research_node",
        message="fetched candidates",
        node="fetch_candidates",
        step=state.step_count,
        candidate_count=len(state.candidates),
    )
    return state


def rule_score_node(state: JobResearchState) -> JobResearchState:
    state.bump_step()
    if not state.candidates:
        state.filtered = []
        return state

    scorer = JobRuleScorer(load_job_search_profile())
    filtered: list[dict[str, Any]] = []
    summaries: list[dict[str, Any]] = []
    for raw in state.candidates:
        item = _dict_to_signal_item(raw)
        score = scorer.score(item)
        summary = {
            "title": item.title,
            "score": score.score,
            "recommendation": score.recommendation,
        }
        summaries.append(summary)
        if score.score >= 25 and score.recommendation != "reject":
            filtered.append({**raw, "rule_score": score.score})
    state.scoring_summaries = summaries
    state.filtered = filtered
    log_event(
        _LOGGER,
        event="job_research_node",
        message="rule scored candidates",
        node="rule_score",
        step=state.step_count,
        filtered_count=len(state.filtered),
    )
    return state


def optional_llm_node(
    state: JobResearchState,
    *,
    provider: LLMProvider | None,
) -> JobResearchState:
    state.bump_step()
    if provider is None:
        state.warnings.append("llm_provider_absent")
        return state
    if not state.filtered:
        return state
    if state.llm_calls >= state.max_llm_calls:
        state.warnings.append("llm_budget_exhausted")
        return state

    titles = ", ".join(str(item.get("title") or "?") for item in state.filtered[:5])
    try:
        response = provider.generate(
            LLMRequest(
                instructions=(
                    "Summarize job-research candidates briefly for a digest. "
                    "Do not invent employers. Keep under 500 characters."
                ),
                input=f"Candidates: {titles}",
                temperature=0.2,
            )
        )
        state.llm_calls += 1
        note = response.text.strip()
        if note:
            state.scoring_summaries.append({"llm_note": note[:500]})
    except Exception as exc:  # noqa: BLE001 - fail open to rules
        state.warnings.append(f"llm_failed:{type(exc).__name__}")
        state.fallback_used = True
    return state


def prepare_digest_node(state: JobResearchState) -> JobResearchState:
    state.bump_step()
    if not state.candidates and "no_candidates" not in state.warnings:
        state.warnings.append("no_candidates")
    lines = [
        "Job research digest",
        f"Profile: {state.profile_name}",
        f"Candidates fetched: {len(state.candidates)}",
        f"Filtered matches: {len(state.filtered)}",
    ]
    if not state.candidates:
        lines.append("No candidates fetched from job sources.")
    elif not state.filtered:
        lines.append("No plausible matches after rule scoring.")
    else:
        lines.append("Top matches:")
        for item in state.filtered[:10]:
            title = item.get("title") or "Untitled"
            score = item.get("rule_score")
            company = ""
            metadata = item.get("metadata")
            if isinstance(metadata, dict):
                company = str(metadata.get("company") or "")
            suffix = f" ({company})" if company else ""
            score_bit = f" [rule={score}]" if score is not None else ""
            lines.append(f"- {title}{suffix}{score_bit}")

    llm_notes = [
        str(row["llm_note"])
        for row in state.scoring_summaries
        if isinstance(row, dict) and row.get("llm_note")
    ]
    if llm_notes:
        lines.append("LLM notes:")
        lines.extend(f"- {note}" for note in llm_notes)

    if state.warnings:
        lines.append("Warnings: " + "; ".join(state.warnings))
    if state.fallback_used:
        lines.append("Fallback: deterministic/rule path used after LLM failure.")

    state.digest_text = "\n".join(lines)
    state.status = "digest_ready"
    return state


def persist_results_node(
    state: JobResearchState,
    *,
    db_path: Any | None = None,
    upsert: Callable[..., Any] | None = None,
) -> JobResearchState:
    """Persist filtered matches and digest snapshot via ``upsert_signal``."""

    from dataclasses import replace
    from datetime import UTC, datetime
    from pathlib import Path

    from app.core.db import upsert_signal
    from app.core.health import resolve_db_path
    from app.core.models import SignalItem

    state.bump_step()
    writer = upsert or upsert_signal
    try:
        resolved = (
            resolve_db_path(db_path) if db_path is not None else resolve_db_path()
        )
        saved = 0
        for raw in state.filtered:
            item = _dict_to_signal_item(raw)
            tagged = replace(
                item,
                item_type="job_research_candidate",
                external_id=item.external_id
                or f"job-research:{item.source}:{item.title}",
                metadata={
                    **item.metadata,
                    "rule_score": raw.get("rule_score"),
                    "profile_name": state.profile_name,
                    "task_run_id": state.task_run_id,
                },
            )
            writer(tagged, db_path=Path(resolved))
            saved += 1

        if state.digest_text:
            snapshot = SignalItem(
                source="job_research_agent",
                item_type="job_research_digest",
                title=f"Job research digest ({state.profile_name})",
                observed_at=datetime.now(UTC),
                external_id=(
                    f"job-research-digest:{state.task_run_id or 'adhoc'}:"
                    f"{state.profile_name}"
                ),
                content=state.digest_text[:4000],
                metadata={
                    "profile_name": state.profile_name,
                    "candidate_count": len(state.candidates),
                    "filtered_count": len(state.filtered),
                    "fallback_used": state.fallback_used,
                },
            )
            writer(snapshot, db_path=Path(resolved))
            saved += 1
        state.persisted_count = saved
    except Exception as exc:  # noqa: BLE001 - persist failures must not abort digest
        state.persist_error = str(exc)[:300]
        state.warnings.append(f"persist_failed:{type(exc).__name__}")
        state.errors.append(f"persist_failed:{type(exc).__name__}")
    log_event(
        _LOGGER,
        event="job_research_node",
        message="persisted research results",
        node="persist_results",
        step=state.step_count,
        persisted_count=state.persisted_count,
        persist_error=state.persist_error,
    )
    return state


def notify_node(
    state: JobResearchState,
    *,
    registry: ToolRegistry,
    context: ToolContext,
) -> JobResearchState:
    state.bump_step()
    if not state.telegram_enabled:
        state.notification_status = "skipped_disabled"
        return state
    if "send_telegram_message" not in registry.allowlist:
        state.notification_status = "skipped_not_allowlisted"
        state.warnings.append("telegram_tool_not_allowlisted")
        return state
    if state.tool_calls >= state.max_tool_calls:
        state.notification_status = "skipped_tool_budget"
        return state

    result = registry.invoke(
        "send_telegram_message",
        {"text": state.digest_text or "Job research digest (empty)"},
        context=context,
    )
    state.tool_calls += 1
    if result.ok:
        state.notification_status = "sent"
    else:
        state.notification_status = "failed"
        state.warnings.append(result.error or "telegram_failed")
    return state


def finalize_node(state: JobResearchState) -> JobResearchState:
    state.bump_step()
    if state.errors and not state.digest_text:
        state.digest_text = (
            "Job research digest\n"
            f"Profile: {state.profile_name}\n"
            "Research failed; deterministic fallback digest.\n"
            "Errors: " + "; ".join(state.errors)
        )
        state.fallback_used = True
    state.status = "completed" if not state.errors else "completed_with_errors"
    log_event(
        _LOGGER,
        event="job_research_completed",
        message="job research workflow finished",
        status=state.status,
        step_count=state.step_count,
        tool_calls=state.tool_calls,
        llm_calls=state.llm_calls,
        fallback_used=state.fallback_used,
    )
    return state


def _dict_to_signal_item(raw: dict[str, Any]):
    from datetime import UTC, datetime

    from app.core.models import SignalItem

    observed = raw.get("observed_at")
    if isinstance(observed, str):
        try:
            observed_at = datetime.fromisoformat(observed.replace("Z", "+00:00"))
        except ValueError:
            observed_at = datetime.now(UTC)
    elif isinstance(observed, datetime):
        observed_at = observed
    else:
        observed_at = datetime.now(UTC)

    metadata = raw.get("metadata")
    return SignalItem(
        source=str(raw.get("source") or "tool"),
        item_type=str(raw.get("item_type") or "job"),
        title=str(raw.get("title") or "Untitled"),
        observed_at=observed_at,
        url=raw.get("url") if isinstance(raw.get("url"), str) else None,
        external_id=(
            raw.get("external_id")
            if isinstance(raw.get("external_id"), str)
            else None
        ),
        content=raw.get("content") if isinstance(raw.get("content"), str) else None,
        metadata=dict(metadata) if isinstance(metadata, dict) else {},
    )


NodeFn = Callable[[JobResearchState], JobResearchState]
