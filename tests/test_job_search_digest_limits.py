from __future__ import annotations

from datetime import UTC, datetime

from app.core import SignalItem
from app.core.config import ScoringConfig
from app.scorers import JobRuleScore
from app.tasks.job_search import (
    DigestContent,
    JobSearchSummary,
    ScoredJob,
    _build_telegram_digest,
)

OBSERVED_AT = datetime(2026, 6, 16, 9, 0, tzinfo=UTC)


def test_build_telegram_digest_caps_strong_and_maybe_sections() -> None:
    summary = JobSearchSummary(
        fetched_count=5,
        new_count=5,
        strong_matches=[
            _scored_job(f"Strong {index}", 90 - index) for index in range(3)
        ],
        maybe_matches=[_scored_job(f"Maybe {index}", 40 - index) for index in range(3)],
    )
    scoring = ScoringConfig(
        freshness_weight=0.6,
        priority_weight=0.4,
        digest_max_strong_matches=1,
        digest_max_maybe_matches=1,
    )

    digest = _build_telegram_digest(summary, scoring=scoring, run_status="success")

    assert isinstance(digest, DigestContent)
    assert "Strong 0" in digest.text
    assert "Strong 1" not in digest.text
    assert "...and 2 more job(s)" in digest.text
    assert "Maybe 0" in digest.text
    assert "Maybe 1" not in digest.text
    assert len(digest.signal_ids) == 2
    assert "notified=2" in digest.text


def test_build_telegram_digest_round_robins_caps_across_profiles() -> None:
    summary = JobSearchSummary(
        fetched_count=4,
        new_count=4,
        profile_names=["alpha", "beta"],
        strong_matches=[
            _scored_job("Alpha Strong 1", 95, profile="alpha", signal_id=1),
            _scored_job("Alpha Strong 2", 90, profile="alpha", signal_id=2),
            _scored_job("Beta Strong 1", 85, profile="beta", signal_id=3),
            _scored_job("Beta Strong 2", 80, profile="beta", signal_id=4),
        ],
    )
    scoring = ScoringConfig(
        freshness_weight=0.6,
        priority_weight=0.4,
        digest_max_strong_matches=2,
        digest_max_maybe_matches=1,
        include_rejected_summary=False,
    )

    digest = _build_telegram_digest(summary, scoring=scoring, run_status="success")

    assert "Alpha Strong 1" in digest.text
    assert "Beta Strong 1" in digest.text
    assert "Alpha Strong 2" not in digest.text
    assert "Beta Strong 2" not in digest.text
    assert digest.signal_ids == [1, 3]


def test_build_telegram_digest_omits_rejected_summary_when_disabled() -> None:
    summary = JobSearchSummary(
        fetched_count=1,
        new_count=1,
        rejected_jobs=[_scored_job("Helpdesk Support Engineer", 0)],
    )
    scoring = ScoringConfig(
        freshness_weight=0.6,
        priority_weight=0.4,
        include_rejected_summary=False,
    )

    digest = _build_telegram_digest(summary, scoring=scoring, run_status="success")

    assert digest.text == ""
    assert digest.signal_ids == []


def _scored_job(
    title: str,
    score: int,
    *,
    profile: str | None = None,
    signal_id: int | None = None,
) -> ScoredJob:
    recommendation = (
        "strong_match" if score >= 60 else "maybe" if score >= 25 else "reject"
    )
    metadata: dict[str, object] = {}
    if profile is not None:
        metadata["search_profile"] = profile
    return ScoredJob(
        signal_id=signal_id if signal_id is not None else hash(title) % 1000,
        item=SignalItem(
            source="static_jobs",
            item_type="job",
            title=title,
            observed_at=OBSERVED_AT,
            url=f"https://example.com/jobs/{title.lower().replace(' ', '-')}",
            metadata=metadata,
        ),
        score=JobRuleScore(
            score=score,
            recommendation=recommendation,
            reason=f"[rule] {recommendation} ({score}/100): positives: test.",
        ),
    )
