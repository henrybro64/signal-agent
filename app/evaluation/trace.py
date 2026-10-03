"""Safe execution traces for offline evaluation (#66)."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from app.tools.redaction import redact_mapping, redact_value


@dataclass(slots=True)
class TraceEvent:
    kind: str
    name: str
    status: str
    duration_ms: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ExecutionTrace:
    """Redacted trace correlated with an optional task/run id."""

    trace_id: str
    suite: str
    case_id: str
    task_run_id: int | None = None
    events: list[TraceEvent] = field(default_factory=list)
    secrets: tuple[str, ...] = ()

    def add(
        self,
        *,
        kind: str,
        name: str,
        status: str,
        duration_ms: int = 0,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        safe_meta = redact_mapping(metadata or {}, secrets=self.secrets)
        self.events.append(
            TraceEvent(
                kind=kind,
                name=name,
                status=status,
                duration_ms=duration_ms,
                metadata=safe_meta,
            )
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "suite": self.suite,
            "case_id": self.case_id,
            "task_run_id": self.task_run_id,
            "events": [
                {
                    "kind": event.kind,
                    "name": event.name,
                    "status": event.status,
                    "duration_ms": event.duration_ms,
                    "metadata": event.metadata,
                }
                for event in self.events
            ],
        }


class TraceCollector:
    """Helper that records timed, redacted spans."""

    def __init__(
        self,
        *,
        suite: str,
        case_id: str,
        task_run_id: int | None = None,
        secrets: tuple[str, ...] = (),
    ) -> None:
        self.trace = ExecutionTrace(
            trace_id=str(uuid.uuid4()),
            suite=suite,
            case_id=case_id,
            task_run_id=task_run_id,
            secrets=secrets,
        )

    def span(self, kind: str, name: str) -> _Span:
        return _Span(self, kind=kind, name=name)

    def record_text(
        self,
        *,
        kind: str,
        name: str,
        text: str,
        status: str = "ok",
    ) -> None:
        self.trace.add(
            kind=kind,
            name=name,
            status=status,
            metadata={"preview": redact_value(text[:120], self.trace.secrets)},
        )


class _Span:
    def __init__(self, collector: TraceCollector, *, kind: str, name: str) -> None:
        self.collector = collector
        self.kind = kind
        self.name = name
        self.started = 0.0
        self.metadata: dict[str, Any] = {}
        self.status = "ok"

    def __enter__(self) -> _Span:
        self.started = time.perf_counter()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        duration_ms = int((time.perf_counter() - self.started) * 1000)
        status = self.status if exc is None else "error"
        if exc is not None:
            self.metadata["error_type"] = type(exc).__name__
        self.collector.trace.add(
            kind=self.kind,
            name=self.name,
            status=status,
            duration_ms=duration_ms,
            metadata=self.metadata,
        )
