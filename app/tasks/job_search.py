"""Run the job-search pipeline end to end and optionally send a digest."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field, replace
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
    get_signal_evaluation,
    get_signal_evaluation_state,
    mark_signals_digest_reported,
    start_signal_run,
    update_signal_evaluation,
    upsert_signal,
)
from app.notifiers import build_telegram_notifier
from app.scorers import (
    JobRuleScore,
    JobScorePipeline,
    build_job_score_pipeline,
)
from app.sources import build_job_search_sources_for_profile

from .base import BaseTask
from .fetch import DB_PATH_ENV_VAR, DEFAULT_DB_PATH

_MAX_REJECT_LINES = 5
_SEARCH_PROFILE_METADATA_KEY = "search_profile"
_DEFAULT_PROFILE_NAME = "default"


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
    profile_names: list[str] = field(default_factory=list)
    recorded_signal_ids: set[int] = field(default_factory=set)


@dataclass(slots=True, frozen=True)
class DigestContent:
    """Telegram digest body and the signal ids it includes."""

    text: str
    signal_ids: list[int]


@dataclass(slots=True, frozen=True)
class NotificationOutcome:
    """Summarize the Telegram notification phase for structured logs."""

    status: str
    notified_count: int = 0


class JobSearchTask(BaseTask):
    """Fetch, dedup, score, persist, and notify for job-search signals."""

    name: ClassVar[str] = "job_search"

    def run(self) -> None:
        db_path = _resolve_db_path()
        summary = JobSearchSummary()
        notification = NotificationOutcome(status="not_applicable")

        self.begin_task_run(db_path=db_path)
        self.emit_log(
            "task_started",
            message="Job search task started",
            db_path=str(db_path),
        )

        try:
            config = load_config(self.config_path)
            if not config.tasks.job_search.enabled:
                raise ValueError("Task 'job_search' is disabled in config")

            env = load_env_settings()
            validate_llm_scoring_secrets(
                llm_enabled=config.scoring.llm_enabled,
                env=env,
            )

            profiles = list(config.tasks.job_search.profiles)
            if not profiles:
                raise ValueError("No job-search profiles are configured.")
            summary.profile_names = [profile.name for profile in profiles]

            scorers_by_key: dict[str | None, JobScorePipeline] = {}

            for profile in profiles:
                sources = build_job_search_sources_for_profile(
                    config,
                    profile,
                    on_unknown=lambda name, profile_name=profile.name: (
                        summary.unsupported_sources.append(
                            f"{profile_name}/{name}"
                        )
                    ),
                )
                if not sources:
                    continue

                pipeline = scorers_by_key.get(profile.scorer_profile)
                if pipeline is None:
                    pipeline = build_job_score_pipeline(
                        config=config.scoring,
                        env=env,
                        scorer_profile=profile.scorer_profile,
                    )
                    scorers_by_key[profile.scorer_profile] = pipeline

                for source in sources:
                    run_id = start_signal_run(source.name, db_path=db_path)
                    source_label = f"{profile.name}/{source.name}"
                    self.emit_log(
                        "source_run_started",
                        message="Source run started",
                        db_path=str(db_path),
                        source_name=source.name,
                        search_profile=profile.name,
                        run_id=run_id,
                    )
                    try:
                        fetched_items = source.fetch()
                        job_items = [
                            _tag_search_profile(item, profile.name)
                            for item in fetched_items
                            if item.item_type == "job"
                        ]
                        ignored_count = len(fetched_items) - len(job_items)
                        summary.ignored_non_job_count += ignored_count
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
                                    pipeline=pipeline,
                                    db_path=db_path,
                                )
                            else:
                                updated_count += 1
                                summary.duplicate_count += 1
                                if result.signal_id in summary.recorded_signal_ids:
                                    continue
                                evaluation_state = get_signal_evaluation_state(
                                    result.signal_id,
                                    db_path=db_path,
                                )
                                if evaluation_state == "unscored":
                                    self._score_and_record(
                                        item=item,
                                        signal_id=result.signal_id,
                                        summary=summary,
                                        pipeline=pipeline,
                                        db_path=db_path,
                                    )
                                elif evaluation_state == "pending_digest":
                                    self._record_pending_digest_job(
                                        item=item,
                                        signal_id=result.signal_id,
                                        summary=summary,
                                        db_path=db_path,
                                    )

                        finish_signal_run(
                            run_id,
                            status="completed",
                            fetched_count=len(job_items),
                            inserted_count=inserted_count,
                            updated_count=updated_count,
                            db_path=db_path,
                        )
                        self.emit_log(
                            "source_run_completed",
                            message="Source run completed",
                            db_path=str(db_path),
                            source_name=source.name,
                            search_profile=profile.name,
                            run_id=run_id,
                            fetched_count=len(fetched_items),
                            accepted_count=len(job_items),
                            ignored_count=ignored_count,
                            new_count=inserted_count,
                            duplicate_count=updated_count,
                            skipped_count=updated_count + ignored_count,
                        )
                        summary.successful_sources += 1
                    except Exception as exc:
                        finish_signal_run(
                            run_id,
                            status="failed",
                            error=str(exc),
                            db_path=db_path,
                        )
                        summary.failed_sources.append(
                            (source_label, str(exc))
                        )
                        self.emit_log(
                            "source_run_failed",
                            message="Source run failed",
                            level=logging.ERROR,
                            exc_info=exc,
                            db_path=str(db_path),
                            source_name=source.name,
                            search_profile=profile.name,
                            run_id=run_id,
                            error=str(exc),
                        )

            if (
                summary.successful_sources == 0
                and not summary.failed_sources
                and summary.unsupported_sources
            ):
                unsupported = ", ".join(summary.unsupported_sources)
                raise ValueError(
                    "No supported job-search sources are configured. "
                    f"Unsupported sources: {unsupported}"
                )
            if (
                summary.successful_sources == 0
                and not summary.failed_sources
                and not summary.unsupported_sources
            ):
                raise ValueError("No job-search sources are configured.")

            if summary.successful_sources == 0:
                failure_summary = "; ".join(
                    f"{source_name}: {message}"
                    for source_name, message in summary.failed_sources
                )
                print(
                    "\n".join(
                        _build_console_summary(self.config_path, db_path, summary)
                    )
                )
                notification = self._maybe_send_telegram_digest(
                    config=config,
                    env=env,
                    summary=summary,
                    db_path=db_path,
                )
                self.emit_log(
                    "task_summary",
                    message="Job search task summary",
                    db_path=str(db_path),
                    task_status="failed",
                    fetched_count=summary.fetched_count,
                    new_count=summary.new_count,
                    duplicate_count=summary.duplicate_count,
                    skipped_count=(
                        summary.duplicate_count + summary.ignored_non_job_count
                    ),
                    notified_count=notification.notified_count,
                    notification_status=notification.status,
                    successful_source_count=summary.successful_sources,
                    failed_source_count=len(summary.failed_sources),
                    unsupported_source_count=len(summary.unsupported_sources),
                    ignored_count=summary.ignored_non_job_count,
                    strong_match_count=len(summary.strong_matches),
                    maybe_count=len(summary.maybe_matches),
                    rejected_count=len(summary.rejected_jobs),
                    unsupported_sources=summary.unsupported_sources,
                )
                raise ValueError(
                    "All configured job-search sources failed: "
                    f"{failure_summary}"
                )

            print(
                "\n".join(_build_console_summary(self.config_path, db_path, summary))
            )

            notification = self._maybe_send_telegram_digest(
                config=config,
                env=env,
                summary=summary,
                db_path=db_path,
            )
            self.emit_log(
                "task_completed",
                message="Job search task completed",
                db_path=str(db_path),
                fetched_count=summary.fetched_count,
                new_count=summary.new_count,
                duplicate_count=summary.duplicate_count,
                skipped_count=(
                    summary.duplicate_count + summary.ignored_non_job_count
                ),
                notified_count=notification.notified_count,
                notification_status=notification.status,
                successful_source_count=summary.successful_sources,
                failed_source_count=len(summary.failed_sources),
                unsupported_source_count=len(summary.unsupported_sources),
                ignored_count=summary.ignored_non_job_count,
                strong_match_count=len(summary.strong_matches),
                maybe_count=len(summary.maybe_matches),
                rejected_count=len(summary.rejected_jobs),
                unsupported_sources=summary.unsupported_sources,
            )
            self.end_task_run(
                db_path=db_path,
                status="success",
                fetched_count=summary.fetched_count,
                new_count=summary.new_count,
                skipped_count=(
                    summary.duplicate_count + summary.ignored_non_job_count
                ),
                notified_count=notification.notified_count,
            )
        except Exception as exc:
            self.end_task_run(
                db_path=db_path,
                status="failed",
                fetched_count=summary.fetched_count,
                new_count=summary.new_count,
                skipped_count=(
                    summary.duplicate_count + summary.ignored_non_job_count
                ),
                notified_count=notification.notified_count,
                error_message=str(exc),
            )
            self.emit_log(
                "task_failed",
                message="Job search task failed",
                level=logging.ERROR,
                exc_info=exc,
                db_path=str(db_path),
                error=str(exc),
            )
            raise

    def _maybe_send_telegram_digest(
        self,
        *,
        config,
        env,
        summary: JobSearchSummary,
        db_path: Path,
    ) -> NotificationOutcome:
        reported_signal_ids: list[int] = []
        digest_content = _build_telegram_digest(
            summary,
            scoring=config.scoring,
            run_status=(
                "failed" if summary.successful_sources == 0 else "success"
            ),
        )
        if not digest_content.text:
            self.emit_log(
                "notification_skipped",
                message="Telegram digest skipped",
                db_path=str(db_path),
                notifier="telegram",
                notification_status="skipped_no_items",
                notified_count=0,
            )
            print("telegram digest skipped: no new jobs to report")
            return NotificationOutcome(status="skipped_no_items")

        reported_signal_ids = digest_content.signal_ids
        digest = digest_content.text

        if not config.notifiers.telegram.enabled:
            self.emit_log(
                "notification_skipped",
                message="Telegram digest skipped",
                db_path=str(db_path),
                notifier="telegram",
                notification_status="skipped_notifier_disabled",
                notified_count=0,
            )
            print("telegram digest skipped: notifier disabled in config")
            return NotificationOutcome(status="skipped_notifier_disabled")

        validate_telegram_notifier_secrets(
            telegram_enabled=True,
            env=env,
        )
        notifier = build_telegram_notifier(env=env)
        try:
            notifier.send_message(digest)
        except Exception as exc:
            self.emit_log(
                "notification_failed",
                message="Telegram digest failed",
                level=logging.ERROR,
                exc_info=exc,
                db_path=str(db_path),
                notifier="telegram",
                notification_status="failed",
                attempted_notified_count=len(reported_signal_ids),
                error=str(exc),
            )
            raise
        mark_signals_digest_reported(reported_signal_ids, db_path=db_path)
        self.emit_log(
            "notification_sent",
            message="Telegram digest sent",
            db_path=str(db_path),
            notifier="telegram",
            notification_status="sent",
            notified_count=len(reported_signal_ids),
        )
        print("telegram digest sent")
        return NotificationOutcome(
            status="sent",
            notified_count=len(reported_signal_ids),
        )

    def _score_and_record(
        self,
        *,
        item: SignalItem,
        signal_id: int,
        summary: JobSearchSummary,
        pipeline: JobScorePipeline,
        db_path: Path,
    ) -> None:
        result = pipeline.score(item)
        score = result.score
        status = "rejected" if score.recommendation == "reject" else "new"
        update_signal_evaluation(
            signal_id,
            score=score.score,
            reason=score.reason,
            status=status,
            db_path=db_path,
        )
        self.emit_log(
            "job_scored",
            message="Job scored",
            db_path=str(db_path),
            signal_id=signal_id,
            job_title=item.title,
            scoring_method=result.scoring_method,
            llm_attempted=result.llm_attempted,
            llm_skipped_reason=result.llm_skipped_reason,
            llm_fallback_reason=result.llm_fallback_reason,
            rule_score=result.rule_score,
            final_score=score.score,
            recommendation=score.recommendation,
        )

        scored_job = ScoredJob(signal_id=signal_id, item=item, score=score)
        if score.recommendation == "strong_match":
            summary.strong_matches.append(scored_job)
        elif score.recommendation == "maybe":
            summary.maybe_matches.append(scored_job)
        else:
            summary.rejected_jobs.append(scored_job)
        summary.recorded_signal_ids.add(signal_id)

    def _record_pending_digest_job(
        self,
        *,
        item: SignalItem,
        signal_id: int,
        summary: JobSearchSummary,
        db_path: Path,
    ) -> None:
        stored_score, reason = get_signal_evaluation(signal_id, db_path=db_path)
        score = _job_rule_score_from_stored(stored_score, reason)
        scored_job = ScoredJob(signal_id=signal_id, item=item, score=score)
        if score.recommendation == "strong_match":
            summary.strong_matches.append(scored_job)
        elif score.recommendation == "maybe":
            summary.maybe_matches.append(scored_job)
        else:
            summary.rejected_jobs.append(scored_job)
        summary.recorded_signal_ids.add(signal_id)


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


def _build_telegram_digest(
    summary: JobSearchSummary,
    *,
    scoring,
    run_status: str,
) -> DigestContent:
    has_useful_output = (
        bool(summary.strong_matches)
        or bool(summary.maybe_matches)
        or bool(summary.rejected_jobs)
        or bool(summary.failed_sources)
        or bool(summary.unsupported_sources)
    )
    if not has_useful_output:
        return DigestContent(text="", signal_ids=[])

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
    included_signal_ids: list[int] = []

    profile_names = summary.profile_names or _digest_profile_names(summary)
    group_by_profile = len(profile_names) > 1
    strong_remaining = scoring.digest_max_strong_matches
    maybe_remaining = scoring.digest_max_maybe_matches

    if group_by_profile:
        for profile_name in profile_names:
            lines.append("")
            lines.append(f"## {profile_name}")
            strong = _jobs_for_profile(summary.strong_matches, profile_name)
            maybe = _jobs_for_profile(summary.maybe_matches, profile_name)
            rejected = _jobs_for_profile(summary.rejected_jobs, profile_name)
            if strong and strong_remaining > 0:
                section_lines, signal_ids = _render_job_section(
                    "Top recommended jobs",
                    strong,
                    max_items=strong_remaining,
                )
                lines.extend(section_lines)
                included_signal_ids.extend(signal_ids)
                strong_remaining -= len(signal_ids)
            if maybe and maybe_remaining > 0:
                section_lines, signal_ids = _render_job_section(
                    "Maybe jobs",
                    maybe,
                    max_items=maybe_remaining,
                )
                lines.extend(section_lines)
                included_signal_ids.extend(signal_ids)
                maybe_remaining -= len(signal_ids)
            if rejected and scoring.include_rejected_summary:
                section_lines, signal_ids = _render_rejected_section(rejected)
                lines.extend(section_lines)
                included_signal_ids.extend(signal_ids)
            if (
                not strong
                and not maybe
                and not (rejected and scoring.include_rejected_summary)
            ):
                lines.append("")
                lines.append("(no new scored jobs for this profile)")
    else:
        if summary.strong_matches and strong_remaining > 0:
            section_lines, signal_ids = _render_job_section(
                "Top recommended jobs",
                summary.strong_matches,
                max_items=strong_remaining,
            )
            lines.extend(section_lines)
            included_signal_ids.extend(signal_ids)
        if summary.maybe_matches and maybe_remaining > 0:
            section_lines, signal_ids = _render_job_section(
                "Maybe jobs",
                summary.maybe_matches,
                max_items=maybe_remaining,
            )
            lines.extend(section_lines)
            included_signal_ids.extend(signal_ids)
        if summary.rejected_jobs and scoring.include_rejected_summary:
            section_lines, signal_ids = _render_rejected_section(
                summary.rejected_jobs
            )
            lines.extend(section_lines)
            included_signal_ids.extend(signal_ids)

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
    lines.append("")
    lines.append(
        "run summary: "
        f"status={run_status}, "
        f"fetched={summary.fetched_count}, "
        f"new={summary.new_count}, "
        f"skipped={summary.duplicate_count + summary.ignored_non_job_count}, "
        f"notified={len(included_signal_ids)}"
    )

    return DigestContent(
        text="\n".join(lines),
        signal_ids=included_signal_ids,
    )


def _digest_profile_names(summary: JobSearchSummary) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()
    for job in (
        summary.strong_matches + summary.maybe_matches + summary.rejected_jobs
    ):
        name = _search_profile_name(job.item)
        if name in seen:
            continue
        seen.add(name)
        names.append(name)
    return names


def _jobs_for_profile(jobs: list[ScoredJob], profile_name: str) -> list[ScoredJob]:
    return [job for job in jobs if _search_profile_name(job.item) == profile_name]


def _search_profile_name(item: SignalItem) -> str:
    value = item.metadata.get(_SEARCH_PROFILE_METADATA_KEY)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return _DEFAULT_PROFILE_NAME


def _tag_search_profile(item: SignalItem, profile_name: str) -> SignalItem:
    return replace(
        item,
        metadata={**item.metadata, _SEARCH_PROFILE_METADATA_KEY: profile_name},
    )


def _render_job_section(
    title: str,
    jobs: list[ScoredJob],
    *,
    max_items: int | None = None,
) -> tuple[list[str], list[int]]:
    ordered = _sorted_jobs(jobs)
    if max_items is not None:
        displayed = ordered[:max_items]
        remaining = len(ordered) - len(displayed)
    else:
        displayed = ordered
        remaining = 0

    lines = ["", title]
    for job in displayed:
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
    if remaining > 0:
        lines.append(f"- ...and {remaining} more job(s)")
    return lines, [job.signal_id for job in displayed]


def _render_rejected_section(
    jobs: list[ScoredJob],
) -> tuple[list[str], list[int]]:
    lines = ["", "Rejected jobs summary"]
    ordered_jobs = _sorted_jobs(jobs)
    displayed = ordered_jobs[:_MAX_REJECT_LINES]
    for job in displayed:
        company = _company_name(job.item)
        prefix = f"- {job.item.title} [{job.score.score}/100]"
        if company:
            prefix += f" at {company}"
        lines.append(prefix)
        lines.append(f"  {job.score.reason}")
    remaining = len(ordered_jobs) - len(displayed)
    if remaining > 0:
        lines.append(f"- ...and {remaining} more rejected job(s)")
    return lines, [job.signal_id for job in displayed]


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


def _job_rule_score_from_stored(score: float, reason: str) -> JobRuleScore:
    normalized = int(score)
    if normalized >= 60:
        recommendation = "strong_match"
    elif normalized >= 25:
        recommendation = "maybe"
    else:
        recommendation = "reject"
    return JobRuleScore(
        score=normalized,
        recommendation=recommendation,
        reason=reason,
    )


def _resolve_db_path() -> Path:
    raw_path = os.getenv(DB_PATH_ENV_VAR)
    if raw_path:
        return Path(raw_path).expanduser()
    return DEFAULT_DB_PATH
