from __future__ import annotations

import requests

from app.core import SignalItem
from app.scorers import JobLLMScorer, JobRuleScorer, parse_job_search_profile
from tests.test_job_search_task import OBSERVED_AT


class FakeResponse:
    def __init__(
        self,
        *,
        status_code: int = 200,
        json_payload: dict[str, object] | None = None,
    ) -> None:
        self.status_code = status_code
        self._json_payload = json_payload

    def json(self) -> dict[str, object]:
        if self._json_payload is None:
            raise ValueError("invalid json")
        return self._json_payload


class FakeSession:
    def __init__(self, responses: list[FakeResponse | Exception]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, object]] = []

    def post(
        self,
        url: str,
        *,
        headers: dict[str, str],
        json: dict[str, object],
        timeout: int,
    ) -> FakeResponse:
        self.calls.append(
            {
                "url": url,
                "headers": headers,
                "json": json,
                "timeout": timeout,
            }
        )
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def test_job_llm_scorer_returns_structured_score() -> None:
    profile = _profile()
    fallback = JobRuleScorer(profile)
    session = FakeSession(
        [
            FakeResponse(
                json_payload={
                    "output": [
                        {
                            "content": [
                                {
                                    "type": "output_text",
                                    "text": (
                                        '{"score": 85, "recommendation": '
                                        '"strong_match", "fit_reason": '
                                        '"Matches Python automation and cloud '
                                        'security focus.", "concern": '
                                        '"May need deeper backend coding."}'
                                    ),
                                }
                            ]
                        }
                    ]
                }
            )
        ]
    )
    scorer = JobLLMScorer(
        api_key="sk-test",
        model="gpt-5.2",
        profile=profile,
        fallback_scorer=fallback,
        session=session,
    )

    result = scorer.score(_good_fit_job())

    assert result.score == 85
    assert result.recommendation == "strong_match"
    assert "Matches Python automation and cloud security focus." in result.reason
    assert "May need deeper backend coding." in result.reason
    assert session.calls[0]["url"] == "https://api.openai.com/v1/responses"
    assert session.calls[0]["headers"]["Authorization"] == "Bearer sk-test"
    request_json = session.calls[0]["json"]
    assert request_json["model"] == "gpt-5.2"
    assert request_json["text"]["format"]["type"] == "json_schema"


def test_job_llm_scorer_falls_back_on_invalid_json() -> None:
    profile = _profile()
    fallback = JobRuleScorer(profile)
    session = FakeSession(
        [
            FakeResponse(
                json_payload={
                    "output": [
                        {
                            "content": [
                                {
                                    "type": "output_text",
                                    "text": "not valid json",
                                }
                            ]
                        }
                    ]
                }
            )
        ]
    )
    scorer = JobLLMScorer(
        api_key="sk-test",
        model="gpt-5.2",
        profile=profile,
        fallback_scorer=fallback,
        session=session,
    )

    result = scorer.score(_bad_fit_job())

    fallback_result = fallback.score(_bad_fit_job())
    assert result.score == fallback_result.score
    assert result.recommendation == fallback_result.recommendation
    assert result.reason == fallback_result.reason


def test_job_llm_scorer_falls_back_on_api_failure() -> None:
    profile = _profile()
    fallback = JobRuleScorer(profile)
    session = FakeSession(
        [requests.RequestException("network timeout")]
    )
    scorer = JobLLMScorer(
        api_key="sk-test",
        model="gpt-5.2",
        profile=profile,
        fallback_scorer=fallback,
        session=session,
    )

    result = scorer.score(_good_fit_job())

    fallback_result = fallback.score(_good_fit_job())
    assert result.score == fallback_result.score
    assert result.recommendation == fallback_result.recommendation


def test_job_llm_scorer_falls_back_on_mismatched_recommendation() -> None:
    profile = _profile()
    fallback = JobRuleScorer(profile)
    session = FakeSession(
        [
            FakeResponse(
                json_payload={
                    "output_text": (
                        '{"score": 10, "recommendation": "strong_match", '
                        '"fit_reason": "Looks good.", "concern": ""}'
                    )
                }
            )
        ]
    )
    scorer = JobLLMScorer(
        api_key="sk-test",
        model="gpt-5.2",
        profile=profile,
        fallback_scorer=fallback,
        session=session,
    )

    result = scorer.score(_good_fit_job())

    fallback_result = fallback.score(_good_fit_job())
    assert result.score == fallback_result.score
    assert result.recommendation == fallback_result.recommendation


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


def _bad_fit_job() -> SignalItem:
    return SignalItem(
        source="manual",
        item_type="job",
        title="Helpdesk Support Engineer",
        observed_at=OBSERVED_AT,
        content="SLA-heavy support and rotating on-call coverage.",
        metadata={
            "company": "Example Support Company",
            "location": "Taipei",
        },
    )
