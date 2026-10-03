"""Bounded plan-act-observe chat loop (#83)."""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.evaluation.trace import TraceCollector
from app.llm import LLMProvider, LLMRequest
from app.runtime import paths as runtime_paths
from app.tools import ToolContext, ToolRegistry
from app.workflows import JobResearchState, run_job_research_python

from .permissions import ApproveFn, default_approver, requires_approval
from .session import ChatMessage, ChatSession

_ACTION_RE = re.compile(r"\{.*\}", re.DOTALL)
CancelCheck = Callable[[], bool]


@dataclass(slots=True)
class ChatBudgets:
    """Budgets for one chat session turn and the overall session.

    ``max_session_turns`` limits user messages before ``/clear``.
    ``max_steps_per_turn`` limits plan-act iterations inside one user turn.
    ``max_estimated_tokens`` is a soft budget using provider usage when present
    else a chars/4 estimate (0 or None disables).
    """

    max_session_turns: int = 8
    max_steps_per_turn: int = 8
    max_tool_calls: int = 6
    max_llm_calls: int = 8
    max_elapsed_seconds: float = 60.0
    max_estimated_tokens: int | None = None

    # Backward-compatible aliases used by older call sites/tests.
    @property
    def max_turns(self) -> int:
        return self.max_session_turns


@dataclass(slots=True)
class TurnEvent:
    kind: str
    name: str
    detail: str = ""
    ok: bool = True


@dataclass(slots=True)
class TurnResult:
    reply: str
    events: list[TurnEvent] = field(default_factory=list)
    stopped_reason: str | None = None
    trace_id: str | None = None
    trace_path: str | None = None
    estimated_tokens: int = 0


class ChatCancelled(Exception):
    """Raised when the user cancels mid-turn."""


def run_chat_turn(
    session: ChatSession,
    user_text: str,
    *,
    provider: LLMProvider,
    registry: ToolRegistry,
    context: ToolContext,
    budgets: ChatBudgets | None = None,
    approve: ApproveFn | None = None,
    cancel_check: CancelCheck | None = None,
    root: str | Path | None = None,
    persist_trace: bool = True,
) -> TurnResult:
    """Execute one user turn with a bounded plan-act-observe loop."""

    limits = budgets or ChatBudgets()
    # Accept legacy ChatBudgets(max_turns=...) constructed via kwargs in tests
    # that still pass max_turns into the dataclass initializer through a shim.
    approver = approve or default_approver
    started = time.monotonic()
    events: list[TurnEvent] = []
    estimated_tokens = 0
    session.messages.append(ChatMessage(role="user", content=user_text))
    session.turns += 1

    collector = TraceCollector(
        suite="chat",
        case_id=session.session_id,
        secrets=tuple(context.secrets_to_redact or ()),
    )
    collector.trace.add(
        kind="turn",
        name="user",
        status="ok",
        metadata={"text": user_text[:500]},
    )

    if session.turns > limits.max_session_turns:
        reply = "Turn budget exhausted for this session. Use /clear to continue."
        session.messages.append(ChatMessage(role="assistant", content=reply))
        collector.trace.add(
            kind="budget",
            name="max_session_turns",
            status="error",
            metadata={"turns": session.turns},
        )
        trace_path = _maybe_persist_trace(collector, root=root, enabled=persist_trace)
        return TurnResult(
            reply=reply,
            stopped_reason="max_session_turns",
            trace_id=collector.trace.trace_id,
            trace_path=str(trace_path) if trace_path else None,
        )

    observations: list[str] = []
    final_reply: str | None = None
    stopped: str | None = None

    for _step in range(max(1, limits.max_steps_per_turn)):
        if cancel_check is not None and cancel_check():
            raise ChatCancelled("chat cancelled")
        if time.monotonic() - started > limits.max_elapsed_seconds:
            stopped = "max_elapsed"
            break
        if session.llm_calls >= limits.max_llm_calls:
            stopped = "max_llm_calls"
            break
        if session.tool_calls >= limits.max_tool_calls:
            stopped = "max_tool_calls"
            break
        if (
            limits.max_estimated_tokens
            and estimated_tokens >= limits.max_estimated_tokens
        ):
            stopped = "max_estimated_tokens"
            break

        with collector.span("plan", "next_action") as span:
            action, used_tokens = _plan_next_action(
                provider=provider,
                session=session,
                observations=observations,
                user_text=user_text,
            )
            estimated_tokens += used_tokens
            span.metadata["action_type"] = str(action.get("type") or "unknown")
            span.metadata["estimated_tokens"] = used_tokens
        session.llm_calls += 1
        events.append(
            TurnEvent(kind="plan", name=str(action.get("type") or "unknown"))
        )

        action_type = str(action.get("type") or "respond").lower()
        if action_type in {"respond", "final", "answer"}:
            final_reply = str(
                action.get("message") or action.get("text") or ""
            ).strip()
            if not final_reply:
                final_reply = "Done."
            break

        if action_type == "workflow":
            name = str(action.get("name") or "job_research")
            if name not in {"job_research", "job_research_agent"}:
                observations.append(f"workflow_rejected:{name}")
                events.append(
                    TurnEvent(
                        kind="workflow",
                        name=name,
                        ok=False,
                        detail="not allowlisted",
                    )
                )
                collector.trace.add(
                    kind="workflow",
                    name=name,
                    status="error",
                    metadata={"reason": "not allowlisted"},
                )
                continue
            with collector.span("workflow", name) as span:
                result_text = _run_job_research(
                    registry=registry,
                    context=context,
                    provider=provider,
                )
                span.metadata["digest_chars"] = len(result_text)
            session.tool_calls += 1
            observations.append(result_text)
            events.append(TurnEvent(kind="workflow", name=name, detail="ok"))
            continue

        if action_type == "tool":
            tool_name = str(action.get("name") or "").strip()
            arguments = action.get("arguments") or {}
            if not isinstance(arguments, dict):
                arguments = {}
            if not tool_name:
                observations.append("tool_missing_name")
                continue
            if tool_name not in registry.allowlist:
                observations.append(f"tool_not_allowlisted:{tool_name}")
                events.append(
                    TurnEvent(
                        kind="tool",
                        name=tool_name,
                        ok=False,
                        detail="not allowlisted",
                    )
                )
                collector.trace.add(
                    kind="tool",
                    name=tool_name,
                    status="error",
                    metadata={"reason": "not allowlisted"},
                )
                continue
            if requires_approval(tool_name) and not approver(tool_name, arguments):
                observations.append(f"tool_rejected_by_policy:{tool_name}")
                events.append(
                    TurnEvent(
                        kind="tool",
                        name=tool_name,
                        ok=False,
                        detail="approval denied",
                    )
                )
                collector.trace.add(
                    kind="tool",
                    name=tool_name,
                    status="error",
                    metadata={"reason": "approval denied"},
                )
                continue
            with collector.span("tool", tool_name) as span:
                try:
                    result = registry.invoke(tool_name, arguments, context=context)
                except Exception as exc:  # noqa: BLE001
                    observations.append(f"tool_error:{type(exc).__name__}")
                    events.append(
                        TurnEvent(
                            kind="tool",
                            name=tool_name,
                            ok=False,
                            detail=type(exc).__name__,
                        )
                    )
                    span.status = "error"
                    span.metadata["error"] = type(exc).__name__
                    continue
                span.metadata["ok"] = result.ok
                if not result.ok:
                    span.status = "error"
            session.tool_calls += 1
            payload = json.dumps(result.to_safe_dict(), ensure_ascii=False)[:1200]
            observations.append(f"tool:{tool_name}:{payload}")
            events.append(
                TurnEvent(
                    kind="tool",
                    name=tool_name,
                    ok=result.ok,
                    detail="ok" if result.ok else (result.error or "error"),
                )
            )
            continue

        observations.append(f"unknown_action:{action_type}")

    if final_reply is None:
        if observations:
            final_reply = (
                "I hit a safety/budget limit before finishing. "
                f"Last observation: {observations[-1][:300]}"
            )
        else:
            final_reply = "I could not complete that request within budgets."
        if stopped:
            final_reply += f" ({stopped})"
        if stopped is None and session.tool_calls >= limits.max_tool_calls:
            stopped = "max_tool_calls"
        if stopped is None:
            stopped = "max_steps_per_turn"

    session.messages.append(ChatMessage(role="assistant", content=final_reply))
    collector.record_text(kind="digest", name="assistant_reply", text=final_reply)
    if stopped:
        collector.trace.add(
            kind="budget",
            name=stopped,
            status="error",
            metadata={"estimated_tokens": estimated_tokens},
        )
    trace_path = _maybe_persist_trace(collector, root=root, enabled=persist_trace)
    return TurnResult(
        reply=final_reply,
        events=events,
        stopped_reason=stopped,
        trace_id=collector.trace.trace_id,
        trace_path=str(trace_path) if trace_path else None,
        estimated_tokens=estimated_tokens,
    )


def _plan_next_action(
    *,
    provider: LLMProvider,
    session: ChatSession,
    observations: list[str],
    user_text: str,
) -> tuple[dict[str, Any], int]:
    history = "\n".join(
        f"{msg.role}: {msg.content}" for msg in session.messages[-8:]
    )
    obs = "\n".join(observations[-6:]) or "(none)"
    instructions = (
        "You are signal-agent. Reply with a single JSON object only. "
        "Allowed types: "
        '{"type":"tool","name":"search_jobs|get_tw_stock_movers|query_recent_runs",'
        '"arguments":{...}} '
        'or {"type":"workflow","name":"job_research"} '
        'or {"type":"respond","message":"..."}. '
        "Never invent secrets. Do not submit job applications or trade stocks."
    )
    prompt = (
        f"User request: {user_text}\n"
        f"Recent dialogue:\n{history}\n"
        f"Observations:\n{obs}\n"
        "Choose the next action JSON."
    )
    try:
        response = provider.generate(
            LLMRequest(instructions=instructions, input=prompt, temperature=0.1)
        )
        tokens = _estimate_tokens(instructions, prompt, response.text, response.raw)
        return _parse_action(response.text), tokens
    except Exception as exc:  # noqa: BLE001 - fail open to a useful reply
        message = (
            "The language model failed "
            f"({type(exc).__name__}). "
            "You can retry, run `signal-agent doctor`, or use "
            "`signal-agent run job_research_agent` directly."
        )
        return {"type": "respond", "message": message}, _estimate_tokens(
            instructions, prompt, message, None
        )


def _estimate_tokens(
    instructions: str,
    prompt: str,
    response_text: str,
    raw: dict[str, Any] | None,
) -> int:
    if isinstance(raw, dict):
        usage = raw.get("usage")
        if isinstance(usage, dict):
            for key in ("total_tokens", "totalTokens", "output_tokens"):
                value = usage.get(key)
                if isinstance(value, int | float) and value > 0:
                    return int(value)
    return max(1, (len(instructions) + len(prompt) + len(response_text)) // 4)


def _parse_action(text: str) -> dict[str, Any]:
    raw = text.strip()
    try:
        payload = json.loads(raw)
        if isinstance(payload, dict):
            return payload
    except json.JSONDecodeError:
        pass
    match = _ACTION_RE.search(raw)
    if match:
        try:
            payload = json.loads(match.group(0))
            if isinstance(payload, dict):
                return payload
        except json.JSONDecodeError:
            pass
    return {"type": "respond", "message": raw[:1000] or "OK"}


def _run_job_research(
    *,
    registry: ToolRegistry,
    context: ToolContext,
    provider: LLMProvider | None,
) -> str:
    final = run_job_research_python(
        JobResearchState(max_steps=12),
        registry=registry,
        context=context,
        provider=provider,
    )
    return final.digest_text[:1500]


def _maybe_persist_trace(
    collector: TraceCollector,
    *,
    root: str | Path | None,
    enabled: bool,
) -> Path | None:
    if not enabled:
        return None
    runtime_paths.ensure_local_dirs(root)
    path = (
        runtime_paths.chat_traces_dir(root)
        / f"{collector.trace.case_id}-{collector.trace.trace_id}.json"
    )
    path.write_text(
        json.dumps(collector.trace.to_dict(), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return path
