"""Run the job-search pipeline end to end and optionally send a digest."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar

from app.config import (
    load_config,
    load_env_settings,
    validate_llm_scoring_secrets,
    validate_telegram_notifier_secrets,
)
from app.core import (
    SignalItem,
    finish_signal_run,
    init_db,
    start_signal_run,
    update_signal_evaluation,
    upsert_signal,
)
from app.notifiers import build_telegram_notifier
from app.scorers import JobRuleScore, JobRuleScorer
from app.sources import build_job_search_sources

from .base import BaseTask
from .fetch import DB_PATH_ENV_VAR, DEFAULT_DB_PATH

_MAX_REJECT_LINES = 5


@dataclass(slots=True)
class ScoredJob:
    """Scored job signal that can be persisted and reported."""

    signal_id: int
    item: SignalItem
    score: JobRuleScore


@dataclass(slots=True)
class JobSearchSummary:
    """Collected counts and categorized jobs for one task run."""

    successful_sources: int = 0
    fetched_count: int = 0
    new_count: int = 0
    duplicate_count: int = 0
    strong_matches: list[ScoredJob] = field(default_factory=list)
    maybe_matches: list[ScoredJob] = field(default_factory=list)
    rejected_jobs: list[ScoredJob] = field(default_factory=list)
    failed_sources: list[tuple[str, str]] = field(default_factory=list)
    unsupported_sources: list[str] = field(default_factory=list)
    ignored_non_job_count: int = 0


class JobSearchTask(BaseTask):
    """Fetch, dedup, score, persist, and notify for job-search signals."""

    name: ClassVar[str] = "job_search"

    def run(self) -> None:
        config = load_config(self.config_path)
        if not config.tasks.job_search.enabled:
            raise ValueError("Task 'job_search' is disabled in config")

        env = load_env_settings()
        validate_llm_scoring_secrets(
            llm_enabled=config.scoring.llm_enabled,
            env=env,
        )
        validate_telegram_notifier_secrets(
            telegram_enabled=config.notifiers.telegram.enabled,
            env=env,
        )

        db_path = _resolve_db_path()
        init_db(db_path)

        summary = JobSearchSummary()
        sources = build_job_search_sources(
            config,
            on_unknown=summary.unsupported_sources.append,
        )
        if not sources:
            if summary.unsupported_sources:
                unsupported = ", ".join(summary.unsupported_sources)
                raise ValueError(
                    "No supported job-search sources are configured. "
                    f"Unsupported sources: {unsupported}"
                )
            raise ValueError("No job-search sources are configured.")

        scorer = JobRuleScorer()

        for source in sources:
            run_id = start_signal_run(source.name, db_path=db_path)
            try:
                fetched_items = source.fetch()
                job_items = [
                    item for item in fetched_items if item.item_type == "job"
                ]
                summary.ignored_non_job_count += len(fetched_items) - len(job_items)
                summary.fetched_count += len(job_items)

                inserted_count = 0
                updated_count = 0

                for item in job_items:
                    result = upsert_signal(item, db_path=db_path)
                    if result.action == "inserted":
                        inserted_count += 1
                        summary.new_count += 1
                        self._score_and_record(
                            item=item,
                            signal_id=result.signal_id,
                            summary=summary,
                            scorer=scorer,
                            db_path=db_path,
                        )
                    else:
                        updated_count += 1
                        summary.duplicate_count += 1

                finish_signal_run(
                    run_id,
                    status="completed",
                    fetched_count=len(job_items),
                    inserted_count=inserted_count,
                    updated_count=updated_count,
                    db_path=db_path,
                )
                summary.successful_sources += 1
            except Exception as exc:
                finish_signal_run(
                    run_id,
                    status="failed",
                    error=str(exc),
                    db_path=db_path,
                )
                summary.failed_sources.append((source.name, str(exc)))

        if summary.successful_sources == 0:
            failure_summary = "; ".join(
                f"{source_name}: {message}"
                for source_name, message in summary.failed_sources
            )
            raise ValueError(
                "All configured job-search sources failed: "
                f"{failure_summary}"
            )

        print("\n".join(_build_console_summary(self.config_path, db_path, summary)))

        digest = _build_telegram_digest(summary)
        if not digest:
            print("telegram digest skipped: no new jobs to report")
            return

        if not config.notifiers.telegram.enabled:
            print("telegram digest skipped: notifier disabled in config")
            return

        notifier = build_telegram_notifier(env=env)
        notifier.send_message(digest)
        print("telegram digest sent")

    def _score_and_record(
        self,
        *,
        item: SignalItem,
        signal_id: int,
        summary: JobSearchSummary,
        scorer: JobRuleScorer,
        db_path: Path,
    ) -> None:
        score = scorer.score(item)
        status = "rejected" if score.recommendation == "reject" else "new"
        update_signal_evaluation(
            signal_id,
            score=score.score,
            reason=score.reason,
            status=status,
            db_path=db_path,
        )

        scored_job = ScoredJob(signal_id=signal_id, item=item, score=score)
        if score.recommendation == "strong_match":
            summary.strong_matches.append(scored_job)
        elif score.recommendation == "maybe":
            summary.maybe_matches.append(scored_job)
        else:
            summary.rejected_jobs.append(scored_job)


def _build_console_summary(
    config_path: str,
    db_path: Path,
    summary: JobSearchSummary,
) -> list[str]:
    lines = [
        f"job_search task executed using {config_path}:",
        (
            f"fetched {summary.fetched_count} job(s) from "
            f"{summary.successful_sources} successful source(s):"
        ),
        (
            f"persisted {summary.new_count} new / {summary.duplicate_count} "
            f"duplicate to {db_path}"
        ),
        (
            "classified "
            f"{len(summary.strong_matches)} strong_match / "
            f"{len(summary.maybe_matches)} maybe / "
            f"{len(summary.rejected_jobs)} reject"
        ),
    ]
    if summary.ignored_non_job_count:
        lines.append(
            f"ignored {summary.ignored_non_job_count} non-job signal(s)"
        )
    if summary.unsupported_sources:
        lines.append(
            "skipped unsupported sources: "
            + ", ".join(summary.unsupported_sources)
        )
    for source_name, message in summary.failed_sources:
        lines.append(f"source failed: {source_name}: {message}")
    return lines


def _build_telegram_digest(summary: JobSearchSummary) -> str:
    has_useful_output = (
        summary.new_count > 0
        or bool(summary.failed_sources)
        or bool(summary.unsupported_sources)
    )
    if not has_useful_output:
        return ""

    lines = [
        "signal-agent job search digest",
        (
            "counts: "
            f"fetched={summary.fetched_count}, "
            f"new={summary.new_count}, "
            f"duplicates={summary.duplicate_count}, "
            f"strong={len(summary.strong_matches)}, "
            f"maybe={len(summary.maybe_matches)}, "
            f"rejected={len(summary.rejected_jobs)}"
        ),
    ]

    if summary.strong_matches:
        lines.extend(_render_job_section("Top recommended jobs", summary.strong_matches))
    if summary.maybe_matches:
        lines.extend(_render_job_section("Maybe jobs", summary.maybe_matches))
    if summary.rejected_jobs:
        lines.extend(_render_rejected_section(summary.rejected_jobs))
    if summary.failed_sources:
        lines.append("")
        lines.append("Source failures")
        for source_name, message in summary.failed_sources:
            lines.append(f"- {source_name}: {message}")
    if summary.unsupported_sources:
        lines.append("")
        lines.append(
            "Skipped unsupported sources: "
            + ", ".join(summary.unsupported_sources)
        )

    return "\n".join(lines)


def _render_job_section(title: str, jobs: list[ScoredJob]) -> list[str]:
    lines = ["", title]
    for job in _sorted_jobs(jobs):
        company = _company_name(job.item)
        location = _location_name(job.item)
        descriptor = f"{company} | {location}" if location else company
        lines.append(
            f"- {job.item.title} [{job.score.score}/100]"
            + (f" at {descriptor}" if descriptor else "")
        )
        if job.item.url:
            lines.append(f"  {job.item.url}")
        lines.append(f"  {job.score.reason}")
    return lines


def _render_rejected_section(jobs: list[ScoredJob]) -> list[str]:
    lines = ["", "Rejected jobs summary"]
    ordered_jobs = _sorted_jobs(jobs)
    for job in ordered_jobs[:_MAX_REJECT_LINES]:
        company = _company_name(job.item)
        prefix = f"- {job.item.title} [{job.score.score}/100]"
        if company:
            prefix += f" at {company}"
        lines.append(prefix)
        lines.append(f"  {job.score.reason}")
    remaining = len(ordered_jobs) - _MAX_REJECT_LINES
    if remaining > 0:
        lines.append(f"- ...and {remaining} more rejected job(s)")
    return lines


def _sorted_jobs(jobs: list[ScoredJob]) -> list[ScoredJob]:
    return sorted(
        jobs,
        key=lambda job: (job.score.score, job.item.observed_at),
        reverse=True,
    )


def _company_name(item: SignalItem) -> str:
    company = item.metadata.get("company")
    if isinstance(company, str):
        return company.strip()
    return ""


def _location_name(item: SignalItem) -> str:
    location = item.metadata.get("location")
    if isinstance(location, str):
        return location.strip()
    return ""


def _resolve_db_path() -> Path:
    raw_path = os.getenv(DB_PATH_ENV_VAR)
    if raw_path:
        return Path(raw_path).expanduser()
    return DEFAULT_DB_PATH
