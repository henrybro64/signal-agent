"""Optional OpenAI-backed job scorer with rule-based fallback."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Final

import requests

from app.core import SignalItem

from .job_rule_scorer import JobRuleScore, JobRuleScorer
from .job_search import JobSearchProfile, load_job_search_profile

_RESPONSES_API_URL: Final[str] = "https://api.openai.com/v1/responses"
_REQUEST_TIMEOUT_SECONDS: Final[int] = 20
_ALLOWED_RECOMMENDATIONS: Final[frozenset[str]] = frozenset(
    {"strong_match", "maybe", "reject"}
)
_SYSTEM_PROMPT: Final[str] = (
    "You score how well a job posting fits a candidate profile. "
    "Use the supplied profile and job details only. "
    "Return structured JSON that follows the schema exactly. "
    "Use recommendation strong_match for scores 60-100, maybe for 25-59, "
    "and reject for 0-24."
)
_STRUCTURED_OUTPUT_SCHEMA: Final[dict[str, object]] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["score", "recommendation", "fit_reason", "concern"],
    "properties": {
        "score": {
            "type": "integer",
            "minimum": 0,
            "maximum": 100,
        },
        "recommendation": {
            "type": "string",
            "enum": ["strong_match", "maybe", "reject"],
        },
        "fit_reason": {
            "type": "string",
        },
        "concern": {
            "type": "string",
        },
    },
}


class JobLLMScorerError(RuntimeError):
    """Raised when LLM scoring fails before fallback is applied."""


@dataclass(slots=True, frozen=True)
class JobLLMScoreResult:
    """Validated structured result returned by the model."""

    score: int
    recommendation: str
    fit_reason: str
    concern: str


class JobLLMScorer:
    """Score job postings with the OpenAI Responses API."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        profile: JobSearchProfile | None = None,
        fallback_scorer: JobRuleScorer | None = None,
        session: requests.Session | None = None,
        api_url: str = _RESPONSES_API_URL,
        timeout_seconds: int = _REQUEST_TIMEOUT_SECONDS,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.profile = profile or load_job_search_profile()
        self.fallback_scorer = fallback_scorer or JobRuleScorer(self.profile)
        self.session = session or requests.Session()
        self.api_url = api_url
        self.timeout_seconds = timeout_seconds

    def score(self, item: SignalItem) -> JobRuleScore:
        """Return an LLM score, falling back to rule-based scoring on failure."""

        if item.item_type != "job":
            return self.fallback_scorer.score(item)

        try:
            result = self._score_with_llm(item)
        except JobLLMScorerError:
            return self.fallback_scorer.score(item)

        return _job_rule_score_from_llm(result)

    def _score_with_llm(self, item: SignalItem) -> JobLLMScoreResult:
        payload = {
            "model": self.model,
            "instructions": _SYSTEM_PROMPT,
            "input": _build_user_prompt(item, self.profile),
            "store": False,
            "temperature": 0.2,
            "text": {
                "verbosity": "low",
                "format": {
                    "type": "json_schema",
                    "name": "job_fit_assessment",
                    "strict": True,
                    "schema": _STRUCTURED_OUTPUT_SCHEMA,
                },
            },
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        try:
            response = self.session.post(
                self.api_url,
                headers=headers,
                json=payload,
                timeout=self.timeout_seconds,
            )
        except requests.RequestException as exc:
            raise JobLLMScorerError("LLM request failed") from exc

        if response.status_code >= 400:
            raise JobLLMScorerError(
                f"LLM request failed with status {response.status_code}"
            )

        try:
            response_payload = response.json()
        except ValueError as exc:
            raise JobLLMScorerError("LLM response was not valid JSON") from exc

        output_text = _extract_output_text(response_payload)
        try:
            parsed = json.loads(output_text)
        except json.JSONDecodeError as exc:
            raise JobLLMScorerError(
                "LLM structured output was not valid JSON"
            ) from exc

        return _parse_llm_score_result(parsed)


def _build_user_prompt(item: SignalItem, profile: JobSearchProfile) -> str:
    description = (item.content or "").strip() or "No description provided."
    company = _metadata_value(item, "company", fallback="Unknown company")
    location = _metadata_value(item, "location", fallback="Unknown location")
    profile_summary = "\n".join(_profile_lines(profile))
    return (
        "Candidate profile:\n"
        f"{profile_summary}\n\n"
        "Job posting:\n"
        f"Title: {item.title}\n"
        f"Company: {company}\n"
        f"Location: {location}\n"
        f"Description: {description}\n"
    )


def _profile_lines(profile: JobSearchProfile) -> list[str]:
    sections = [
        ("Target roles", profile.target_roles),
        ("Strong skills", profile.strong_skills),
        (
            "Preferred companies / industries",
            profile.preferred_companies_industries,
        ),
        ("Avoid roles", profile.avoid_roles),
        ("Reject keywords", profile.reject_keywords),
        ("Location preferences", profile.location_preferences),
        ("Remote / hybrid preferences", profile.remote_hybrid_preferences),
        ("Notes for scorer", profile.notes_for_scorer),
    ]
    return [
        f"- {label}: {_join_profile_values(values)}"
        for label, values in sections
        if values
    ]


def _join_profile_values(values: list[str]) -> str:
    return "; ".join(value.strip() for value in values if value.strip())


def _metadata_value(
    item: SignalItem,
    key: str,
    *,
    fallback: str,
) -> str:
    value = item.metadata.get(key)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return fallback


def _extract_output_text(payload: object) -> str:
    if not isinstance(payload, dict):
        raise JobLLMScorerError("LLM response payload was not an object")

    output_text = payload.get("output_text")
    if isinstance(output_text, str) and output_text.strip():
        return output_text

    output = payload.get("output")
    if not isinstance(output, list):
        raise JobLLMScorerError("LLM response did not include output text")

    for item in output:
        if not isinstance(item, dict):
            continue
        content = item.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            text = block.get("text")
            if isinstance(text, str) and text.strip():
                return text
            parsed = block.get("parsed")
            if isinstance(parsed, dict):
                return json.dumps(parsed, ensure_ascii=False)

    raise JobLLMScorerError("LLM response did not include parsable text")


def _parse_llm_score_result(payload: object) -> JobLLMScoreResult:
    if not isinstance(payload, dict):
        raise JobLLMScorerError("LLM structured result was not an object")

    raw_score = payload.get("score")
    if isinstance(raw_score, bool):
        raise JobLLMScorerError("LLM score must be an integer")
    if isinstance(raw_score, float) and raw_score.is_integer():
        raw_score = int(raw_score)
    if not isinstance(raw_score, int):
        raise JobLLMScorerError("LLM score must be an integer")
    if not 0 <= raw_score <= 100:
        raise JobLLMScorerError("LLM score must be between 0 and 100")

    recommendation = payload.get("recommendation")
    if recommendation not in _ALLOWED_RECOMMENDATIONS:
        raise JobLLMScorerError("LLM recommendation was invalid")
    if recommendation != _recommendation_for_score(raw_score):
        raise JobLLMScorerError(
            "LLM recommendation did not match the returned score"
        )

    fit_reason = _require_text_field(payload, "fit_reason")
    concern = _require_optional_text_field(payload, "concern")

    return JobLLMScoreResult(
        score=raw_score,
        recommendation=recommendation,
        fit_reason=fit_reason,
        concern=concern,
    )


def _require_text_field(payload: dict[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise JobLLMScorerError(f"LLM field '{key}' must be a non-empty string")
    return value.strip()


def _require_optional_text_field(payload: dict[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str):
        raise JobLLMScorerError(f"LLM field '{key}' must be a string")
    return value.strip()


def _recommendation_for_score(score: int) -> str:
    if score >= 60:
        return "strong_match"
    if score >= 25:
        return "maybe"
    return "reject"


def _job_rule_score_from_llm(result: JobLLMScoreResult) -> JobRuleScore:
    concern = result.concern or "no major concerns"
    recommendation_text = result.recommendation.replace("_", " ")
    return JobRuleScore(
        score=result.score,
        recommendation=result.recommendation,
        reason=(
            f"{recommendation_text} ({result.score}/100): "
            f"positives: {result.fit_reason}; concerns: {concern}."
        ),
        reasons=[result.fit_reason],
        concerns=[result.concern] if result.concern else [],
    )
