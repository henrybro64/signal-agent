"""Typed state for the bounded job-research pilot workflow."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class JobResearchState:
    """Mutable workflow state (no secrets / raw email bodies)."""

    profile_name: str = "default"
    task_run_id: int | None = None
    config_path: str = "config.yaml"
    candidates: list[dict[str, Any]] = field(default_factory=list)
    filtered: list[dict[str, Any]] = field(default_factory=list)
    scoring_summaries: list[dict[str, Any]] = field(default_factory=list)
    tool_calls: int = 0
    llm_calls: int = 0
    step_count: int = 0
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    digest_text: str = ""
    notification_status: str = "not_attempted"
    fallback_used: bool = False
    status: str = "running"
    max_steps: int = 10
    max_tool_calls: int = 6
    max_llm_calls: int = 3
    telegram_enabled: bool = False
    persisted_count: int = 0
    persist_error: str | None = None

    def bump_step(self) -> None:
        self.step_count += 1
        if self.step_count > self.max_steps:
            raise RuntimeError(
                f"job_research step limit exceeded ({self.max_steps})"
            )

    def to_summary(self) -> dict[str, Any]:
        return {
            "profile_name": self.profile_name,
            "status": self.status,
            "candidate_count": len(self.candidates),
            "filtered_count": len(self.filtered),
            "tool_calls": self.tool_calls,
            "llm_calls": self.llm_calls,
            "step_count": self.step_count,
            "warnings": list(self.warnings),
            "errors": list(self.errors),
            "notification_status": self.notification_status,
            "fallback_used": self.fallback_used,
            "digest_text": self.digest_text,
        }
