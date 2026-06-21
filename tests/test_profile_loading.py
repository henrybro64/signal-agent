from textwrap import dedent

import pytest

from app.core import SignalItem
from app.scorers import (
    DEFAULT_JOB_PROFILE_PATH,
    load_job_search_profile,
    parse_job_search_profile,
    score_job_posting,
)
from app.sources.dummy import SAMPLE_OBSERVED_AT


def test_parse_job_search_profile_reads_markdown_sections() -> None:
    profile = parse_job_search_profile(
        dedent(
            """
            # Example profile

            ## Target roles
            - Cloud SDET
            - Security QA

            ## Strong skills
            - Python
            - Pytest

            ## Preferred signals
            - Developer tooling

            ## Avoid roles
            - Helpdesk

            ## Reject signals
            - Shift work

            ## Location preferences
            - Taipei

            ## Remote / hybrid preferences
            - Remote

            ## Notes for scorer
            Favor security-heavy testing work.
            """
        ).strip()
    )

    assert profile.target_roles == ["Cloud SDET", "Security QA"]
    assert profile.strong_skills == ["Python", "Pytest"]
    assert profile.preferred_companies_industries == ["Developer tooling"]
    assert profile.avoid_roles == ["Helpdesk"]
    assert profile.reject_keywords == ["Shift work"]
    assert profile.location_preferences == ["Taipei"]
    assert profile.remote_hybrid_preferences == ["Remote"]
    assert profile.notes_for_scorer == ["Favor security-heavy testing work."]


def test_parse_job_search_profile_handles_numbered_and_blockquote_lines() -> None:
    profile = parse_job_search_profile(
        dedent(
            """
            Intro text that should be ignored.

            ## Preferred companies/industries
            1. Security product companies
            > Developer tooling

            ## Unknown section
            - This should not be captured anywhere

            ## Remote/hybrid preferences
            + Hybrid
            """
        ).strip()
    )

    assert profile.preferred_companies_industries == [
        "Security product companies",
        "Developer tooling",
    ]
    assert profile.remote_hybrid_preferences == ["Hybrid"]
    assert profile.target_roles == []


def test_load_default_henry_job_profile() -> None:
    profile = load_job_search_profile()
    profile_text = DEFAULT_JOB_PROFILE_PATH.read_text(encoding="utf-8")

    assert profile.source_path == DEFAULT_JOB_PROFILE_PATH
    assert "Cloud SDET" in profile.target_roles
    assert "Python" in profile.strong_skills
    assert "Remote" in profile.remote_hybrid_preferences
    assert "OPENAI_API_KEY" not in profile_text
    assert "TELEGRAM_BOT_TOKEN" not in profile_text


def test_score_job_posting_uses_loaded_profile() -> None:
    profile = load_job_search_profile()
    matching_job = SignalItem(
        source="manual",
        item_type="job",
        title="Cloud SDET",
        observed_at=SAMPLE_OBSERVED_AT,
        content=(
            "Remote Python and Pytest automation role for a cloud platform team "
            "with Kubernetes and API security responsibilities."
        ),
        metadata={
            "company": "Example Cloud Platform",
            "location": "Taipei",
            "work_style": "Hybrid",
        },
    )
    support_job = SignalItem(
        source="manual",
        item_type="job",
        title="Helpdesk Support Engineer",
        observed_at=SAMPLE_OBSERVED_AT,
        content="24/7 support role with shift work, SLA-heavy support, and SCCM.",
        metadata={"location": "Taipei"},
    )

    matching_score = score_job_posting(matching_job, profile)
    support_score = score_job_posting(support_job, profile)

    assert matching_score.score > 0
    assert "Cloud SDET" in matching_score.matched_positive_signals
    assert "Remote" in matching_score.matched_positive_signals
    assert support_score.score < 0
    assert "Helpdesk" in support_score.matched_negative_signals
    assert matching_score.score > support_score.score


def test_score_job_posting_matches_nested_metadata_values() -> None:
    profile = parse_job_search_profile(
        dedent(
            """
            ## Strong skills
            - Kubernetes

            ## Preferred companies / industries
            - Security product companies
            """
        ).strip()
    )
    item = SignalItem(
        source="manual",
        item_type="job",
        title="Platform QA Engineer",
        observed_at=SAMPLE_OBSERVED_AT,
        metadata={
            "tags": ["Kubernetes", "Observability"],
            "company": {"industry": "Security product companies"},
            "priority": 2,
        },
    )

    result = score_job_posting(item, profile)

    assert result.score > 0
    assert "Kubernetes" in result.matched_positive_signals
    assert "Security product companies" in result.matched_positive_signals


def test_score_job_posting_rejects_non_job_items() -> None:
    profile = load_job_search_profile()
    item = SignalItem(
        source="manual",
        item_type="tw_stock_mover",
        title="TSMC gains 3.1%",
        observed_at=SAMPLE_OBSERVED_AT,
    )

    with pytest.raises(ValueError, match="item_type='job'"):
        score_job_posting(item, profile)
