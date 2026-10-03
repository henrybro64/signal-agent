"""Evaluation case and result models (#66)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class EvalExpectation:
    """Structured expectations for one evaluation case."""

    final_status_prefix: str | None = None
    required_tools: list[str] = field(default_factory=list)
    prohibited_substrings: list[str] = field(default_factory=list)
    required_substrings: list[str] = field(default_factory=list)
    max_tool_calls: int | None = None
    max_llm_calls: int | None = None
    require_citations: bool = False
    min_filtered: int | None = None
    expected_recommendation: str | None = None
    min_score: int | None = None
    max_score: int | None = None


@dataclass(slots=True)
class EvalCase:
    """One versioned offline evaluation fixture."""

    id: str
    suite: str
    description: str
    version: int = 1
    kind: str = "job_research"
    inputs: dict[str, Any] = field(default_factory=dict)
    expectations: EvalExpectation = field(default_factory=EvalExpectation)


@dataclass(slots=True)
class EvalCheckResult:
    name: str
    passed: bool
    detail: str = ""


@dataclass(slots=True)
class EvalCaseResult:
    case_id: str
    suite: str
    passed: bool
    checks: list[EvalCheckResult] = field(default_factory=list)
    trace_id: str | None = None
    summary: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "suite": self.suite,
            "passed": self.passed,
            "trace_id": self.trace_id,
            "summary": self.summary,
            "checks": [
                {"name": check.name, "passed": check.passed, "detail": check.detail}
                for check in self.checks
            ],
        }


@dataclass(slots=True)
class EvalReport:
    suite: str
    passed: bool
    results: list[EvalCaseResult] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "suite": self.suite,
            "passed": self.passed,
            "case_count": len(self.results),
            "pass_count": sum(1 for row in self.results if row.passed),
            "results": [row.to_dict() for row in self.results],
        }
