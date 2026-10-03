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
    state.record_tool("search_jobs")
    if not result.ok:
        state.errors.append(result.error or "search_jobs_failed")
        state.warnings.append("fetch_failed")
        return state

    items = result.data.get("items") or []
    if not isinstance(items, list):
        state.errors.append("search_jobs_bad_payload")
        return state
    state.candidates = [item for item in items if isinstance(item, dict)]
    _record_source_failures(state, result.metadata)
    log_event(
        _LOGGER,
        event="job_research_node",
        message="fetched candidates",
        node="fetch_candidates",
        step=state.step_count,
        candidate_count=len(state.candidates),
    )
    return state


def _record_source_failures(state: JobResearchState, metadata: dict[str, Any]) -> None:
    failed = metadata.get("failed_sources") or []
    source_count = int(metadata.get("source_count") or 0)
    if not failed:
        return
    joined = ",".join(map(str, failed))
    if source_count > 0 and not state.candidates:
        state.errors.append(f"all_job_sources_failed:{joined}")
        state.warnings.append(f"all_source_failure:{joined}")
        return
    state.warnings.append(f"partial_source_failure:{joined}")


def rule_score_node(state: JobResearchState) -> JobResearchState:
    state.bump_step()
    if not state.candidates:
        state.filtered = []
        return state

    scorers: dict[str | None, JobRuleScorer] = {}
    filtered: list[dict[str, Any]] = []
    summaries: list[dict[str, Any]] = []
    for raw in state.candidates:
        item = _dict_to_signal_item(raw)
        profile_key = _scorer_profile_for_item(raw)
        scorer = scorers.get(profile_key)
        if scorer is None:
            scorer = JobRuleScorer(load_job_search_profile(profile_key))
            scorers[profile_key] = scorer
        score = scorer.score(item)
        summary = {
            "title": item.title,
            "score": score.score,
            "recommendation": score.recommendation,
        }
        summaries.append(summary)
        if score.score >= 25 and score.recommendation != "reject":
            filtered.append({**raw, "rule_score": score.score})

    filtered.sort(
        key=lambda row: (
            float(row.get("rule_score") or 0.0),
            str(row.get("title") or ""),
        ),
        reverse=True,
    )
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


def _resolve_memory_retriever(
    state: JobResearchState,
    *,
    db_path: Any | None,
) -> Any | None:
    """Build a keyword retriever, ingesting configured docs when present."""

    if db_path is None:
        state.warnings.append("memory_retriever_absent")
        return None

    from pathlib import Path

    from app.memory import SqliteMemoryStore, build_retriever, ingest_paths

    store = SqliteMemoryStore(Path(db_path))
    if state.memory_document_paths:
        results = ingest_paths(
            store,
            namespace=state.memory_namespace,
            paths=list(state.memory_document_paths),
        )
        errors: list[str] = []
        for row in results:
            if row.status == "error" and row.detail is not None:
                errors.append(row.detail)
        if errors:
            state.warnings.append(
                "memory_ingest_errors:" + ";".join(errors[:3])
            )
    return build_retriever(store, use_embeddings=False)


def _evidence_query(state: JobResearchState) -> str:
    titles = [
        str(item.get("title") or "")
        for item in (state.filtered or state.candidates)[:8]
    ]
    return " ".join(part for part in titles if part).strip() or state.profile_name


def retrieve_evidence_node(
    state: JobResearchState,
    *,
    db_path: Any | None = None,
    retriever: Any | None = None,
) -> JobResearchState:
    """Ingest configured docs (if any) and retrieve namespaced evidence (#65)."""

    state.bump_step()
    active_retriever = retriever
    if active_retriever is None:
        active_retriever = _resolve_memory_retriever(state, db_path=db_path)
        if active_retriever is None:
            return state

    query = _evidence_query(state)
    try:
        evidence = active_retriever.retrieve(
            namespace=state.memory_namespace,
            query=query,
            limit=5,
            max_chars=1500,
        )
    except Exception as exc:  # noqa: BLE001 - memory must not abort research
        state.warnings.append(f"memory_retrieve_failed:{type(exc).__name__}")
        return state

    state.evidence = [item.to_dict() for item in evidence]
    log_event(
        _LOGGER,
        event="job_research_node",
        message="retrieved memory evidence",
        node="retrieve_evidence",
        step=state.step_count,
        evidence_count=len(state.evidence),
        namespace=state.memory_namespace,
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
    evidence_lines = []
    for item in state.evidence[:5]:
        citation = item.get("citation") or item.get("source_path") or "?"
        snippet = str(item.get("content") or "").strip().replace("\n", " ")
        if len(snippet) > 120:
            snippet = snippet[:120] + "…"
        evidence_lines.append(f"- [{citation}] {snippet}")
    evidence_block = (
        "Evidence citations:\n" + "\n".join(evidence_lines)
        if evidence_lines
        else "Evidence citations: none"
    )
    try:
        response = provider.generate(
            LLMRequest(
                instructions=(
                    "Summarize job-research candidates briefly for a digest. "
                    "Use provided evidence citations when relevant. "
                    "Do not invent employers. Keep under 500 characters."
                ),
                input=f"Candidates: {titles}\n{evidence_block}",
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
    lines.extend(_digest_match_lines(state))
    lines.extend(_digest_evidence_lines(state))
    lines.extend(_digest_llm_lines(state))
    if state.warnings:
        lines.append("Warnings: " + "; ".join(state.warnings))
    if state.fallback_used:
        lines.append("Fallback: deterministic/rule path used after LLM failure.")

    state.digest_text = "\n".join(lines)
    state.status = "digest_ready"
    return state


def _digest_evidence_lines(state: JobResearchState) -> list[str]:
    if not state.evidence:
        return []
    lines = ["Evidence:"]
    for item in state.evidence[:5]:
        citation = item.get("citation") or item.get("source_path") or "?"
        snippet = str(item.get("content") or "").strip().replace("\n", " ")
        if len(snippet) > 160:
            snippet = snippet[:160] + "…"
        lines.append(f"- [{citation}] {snippet}")
    return lines


def _digest_match_lines(state: JobResearchState) -> list[str]:
    if not state.candidates:
        return ["No candidates fetched from job sources."]
    if not state.filtered:
        return ["No plausible matches after rule scoring."]
    lines = ["Top matches:"]
    for item in state.filtered[:10]:
        lines.append(_format_match_line(item))
    return lines


def _format_match_line(item: dict[str, Any]) -> str:
    title = item.get("title") or "Untitled"
    score = item.get("rule_score")
    company = ""
    metadata = item.get("metadata")
    if isinstance(metadata, dict):
        company = str(metadata.get("company") or "")
    suffix = f" ({company})" if company else ""
    score_bit = f" [rule={score}]" if score is not None else ""
    return f"- {title}{suffix}{score_bit}"


def _digest_llm_lines(state: JobResearchState) -> list[str]:
    llm_notes = [
        str(row["llm_note"])
        for row in state.scoring_summaries
        if isinstance(row, dict) and row.get("llm_note")
    ]
    if not llm_notes:
        return []
    return ["LLM notes:", *[f"- {note}" for note in llm_notes]]


def persist_results_node(
    state: JobResearchState,
    *,
    db_path: Any | None = None,
    upsert: Callable[..., Any] | None = None,
) -> JobResearchState:
    """Persist filtered matches and digest snapshot via ``upsert_signal``.

    Research candidates use a distinct source/external_id (and omit URL) so they
    never overwrite original ``job_search`` signal rows.
    """

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
        new_items: list[dict[str, Any]] = []
        for raw in state.filtered:
            item = _dict_to_signal_item(raw)
            origin_source = item.source
            origin_external_id = item.external_id
            origin_url = item.url
            research_external_id = (
                f"job-research:{origin_source}:"
                f"{origin_external_id or _fallback_research_id(item)}"
            )
            tagged = replace(
                item,
                source="job_research_agent",
                item_type="job_research_candidate",
                url=None,
                external_id=research_external_id,
                metadata={
                    **item.metadata,
                    "origin_source": origin_source,
                    "origin_external_id": origin_external_id,
                    "origin_url": origin_url,
                    "rule_score": raw.get("rule_score"),
                    "profile_name": state.profile_name,
                    "task_run_id": state.task_run_id,
                },
            )
            result = writer(tagged, db_path=Path(resolved))
            action = _upsert_action(result)
            saved += 1
            if action == "inserted":
                new_items.append(raw)

        state.new_filtered = new_items
        state.new_candidate_count = len(new_items)

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
                    "new_candidate_count": state.new_candidate_count,
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
        new_candidate_count=state.new_candidate_count,
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
    if state.new_candidate_count <= 0 and state.filtered:
        # Dedup: do not resend unchanged research candidates.
        state.notification_status = "skipped_no_new"
        state.warnings.append("telegram_skipped_no_new_candidates")
        return state

    result = registry.invoke(
        "send_telegram_message",
        {"text": state.digest_text or "Job research digest (empty)"},
        context=context,
    )
    state.record_tool("send_telegram_message")
    if result.ok:
        state.notification_status = "sent"
    else:
        state.notification_status = "failed"
        error = result.error or "telegram_failed"
        state.warnings.append(error)
        state.errors.append(error)
    return state


def finalize_node(
    state: JobResearchState,
    *,
    db_path: Any | None = None,
) -> JobResearchState:
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
    _store_operational_summary(state, db_path=db_path)
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


def store_operational_summary(
    state: JobResearchState,
    *,
    db_path: Any | None,
) -> None:
    """Persist a redacted run summary into operational memory (#65)."""

    if db_path is None:
        return
    try:
        from pathlib import Path

        from app.memory import SqliteMemoryStore

        store = SqliteMemoryStore(Path(db_path))
        if state.task_run_id is not None:
            key = f"task-{state.task_run_id}"
        else:
            from datetime import UTC, datetime
            from uuid import uuid4

            stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f")
            key = f"run-{stamp}-{uuid4().hex[:8]}"
        store.put_operational(
            namespace=state.memory_namespace,
            kind="run_summary",
            key=key,
            payload={
                "status": state.status,
                "tool_calls": state.tool_calls,
                "tools_used": list(state.tools_used),
                "llm_calls": state.llm_calls,
                "filtered_count": len(state.filtered),
                "evidence_count": len(state.evidence),
                "fallback_used": state.fallback_used,
                "task_run_id": state.task_run_id,
            },
        )
    except Exception as exc:  # noqa: BLE001 - never abort on memory write
        state.warnings.append(
            f"operational_memory_write_failed:{type(exc).__name__}"
        )


def _store_operational_summary(
    state: JobResearchState,
    *,
    db_path: Any | None,
) -> None:
    store_operational_summary(state, db_path=db_path)


def _dict_to_signal_item(raw: dict[str, Any]):
    from app.core.models import SignalItem

    metadata = raw.get("metadata")
    return SignalItem(
        source=str(raw.get("source") or "tool"),
        item_type=str(raw.get("item_type") or "job"),
        title=str(raw.get("title") or "Untitled"),
        observed_at=_coerce_observed_at(raw.get("observed_at")),
        url=_optional_str(raw.get("url")),
        external_id=_optional_str(raw.get("external_id")),
        content=_optional_str(raw.get("content")),
        metadata=dict(metadata) if isinstance(metadata, dict) else {},
    )


def _optional_str(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def _coerce_observed_at(observed: Any):
    from datetime import UTC, datetime

    if isinstance(observed, str):
        try:
            return datetime.fromisoformat(observed.replace("Z", "+00:00"))
        except ValueError:
            return datetime.now(UTC)
    if isinstance(observed, datetime):
        return observed
    return datetime.now(UTC)


def _scorer_profile_for_item(raw: dict[str, Any]) -> str | None:
    metadata = raw.get("metadata")
    if isinstance(metadata, dict):
        profile = metadata.get("scorer_profile")
        if isinstance(profile, str) and profile.strip():
            return profile.strip()
    return None


def _fallback_research_id(item: Any) -> str:
    title = str(getattr(item, "title", "") or "untitled").strip().lower()
    safe = "".join(ch if ch.isalnum() else "-" for ch in title)[:80]
    return safe or "untitled"


def _upsert_action(result: Any) -> str:
    if hasattr(result, "action"):
        return str(result.action)
    if isinstance(result, dict) and "action" in result:
        return str(result["action"])
    if isinstance(result, str):
        return result
    return "inserted"


NodeFn = Callable[[JobResearchState], JobResearchState]
