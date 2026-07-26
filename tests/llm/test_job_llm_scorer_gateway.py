"""Job LLM scorer integration with the app.llm gateway."""

from __future__ import annotations

import json

import pytest

from app.core import SignalItem
from app.llm import FakeLLMProvider, LLMProviderError, LLMRequest, LLMResponse
from app.scorers import JobLLMScorer, JobRuleScorer, parse_job_search_profile
from app.scorers.job_llm_scorer import JobLLMScorerError
from tests.test_job_search_task import OBSERVED_AT


def test_job_llm_scorer_uses_injected_provider() -> None:
    profile = _profile()
    provider = FakeLLMProvider(
        responses=[
            json.dumps(
                {
                    "score": 85,
                    "recommendation": "strong_match",
                    "fit_reason": "Matches Python automation.",
                    "concern": "Needs deeper backend coding.",
                }
            )
        ]
    )
    scorer = JobLLMScorer(
        provider=provider,
        model="fake-model",
        profile=profile,
        fallback_scorer=JobRuleScorer(profile),
    )

    result = scorer.score_with_llm(_good_fit_job())

    assert result.score == 85
    assert result.recommendation == "strong_match"
    assert provider.calls
    assert isinstance(provider.calls[0], LLMRequest)
    assert provider.calls[0].structured_output is not None
    assert provider.calls[0].structured_output.name == "job_fit_assessment"


def test_job_llm_scorer_maps_provider_errors_and_falls_back() -> None:
    profile = _profile()
    fallback = JobRuleScorer(profile)
    provider = FakeLLMProvider(
        responses=[
            LLMProviderError("boom"),
            LLMProviderError("boom"),
        ]
    )
    scorer = JobLLMScorer(
        provider=provider,
        profile=profile,
        fallback_scorer=fallback,
    )

    result = scorer.score(_good_fit_job())
    expected = fallback.score(_good_fit_job())
    assert result.score == expected.score
    assert result.recommendation == expected.recommendation

    with pytest.raises(JobLLMScorerError, match="boom"):
        scorer.score_with_llm(_good_fit_job())


def test_job_llm_scorer_accepts_llm_response_object() -> None:
    profile = _profile()
    provider = FakeLLMProvider(
        responses=[
            LLMResponse(
                text=json.dumps(
                    {
                        "score": 40,
                        "recommendation": "maybe",
                        "fit_reason": "Partial skill overlap.",
                        "concern": "",
                    }
                ),
                model="fake-model",
                provider="fake",
            )
        ]
    )
    scorer = JobLLMScorer(
        provider=provider,
        profile=profile,
        fallback_scorer=JobRuleScorer(profile),
    )

    result = scorer.score_with_llm(_good_fit_job())
    assert result.score == 40
    assert result.recommendation == "maybe"


def _profile():
    return parse_job_search_profile(
        """
        ## Target roles
        - Cloud SDET

        ## Strong skills
        - Python
        - Kubernetes

        ## Avoid roles
        - Helpdesk

        ## Reject keywords
        - SLA-heavy support
        """
    )


def _good_fit_job() -> SignalItem:
    return SignalItem(
        source="manual",
        item_type="job",
        title="Cloud SDET",
        observed_at=OBSERVED_AT,
        content="Python automation with Kubernetes and cloud security tooling.",
        metadata={
            "company": "Example Security Product Company",
            "location": "Taipei",
        },
    )
