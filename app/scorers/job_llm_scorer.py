"""Optional OpenAI-backed job scorer with rule-based fallback.

Uses the project-owned ``app.llm`` gateway; does not import LangChain.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Final

import requests

from app.core import SignalItem
from app.llm import (
    LLMError,
    LLMProvider,
    LLMRequest,
    OpenAIProvider,
    StructuredOutputSpec,
    create_llm_provider,
)

from .job_rule_scorer import JobRuleScore, JobRuleScorer
from .job_search import JobSearchProfile, load_job_search_profile

_RESPONSES_API_URL: Final[str] = "https://api.openai.com/v1/responses"
_REQUEST_TIMEOUT_SECONDS: Final[float] = 20.0
_REQUEST_TEMPERATURE: Final[float] = 0.2
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
_JOB_FIT_OUTPUT = StructuredOutputSpec(
    name="job_fit_assessment",
    schema=_STRUCTURED_OUTPUT_SCHEMA,
    strict=True,
)


_PROMPT_METADATA_SKIP_KEYS: Final[frozenset[str]] = frozenset(
    {"company", "location"}
)


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
    """Score job postings via the framework-neutral LLM gateway."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str = "gpt-4o-mini",
        profile: JobSearchProfile | None = None,
        fallback_scorer: JobRuleScorer | None = None,
        provider: LLMProvider | None = None,
        session: requests.Session | None = None,
        api_url: str = _RESPONSES_API_URL,
        timeout_seconds: float = _REQUEST_TIMEOUT_SECONDS,
        temperature: float = _REQUEST_TEMPERATURE,
    ) -> None:
        self.model = model
        self.profile = profile or load_job_search_profile()
        self.fallback_scorer = fallback_scorer or JobRuleScorer(self.profile)
        self.timeout_seconds = timeout_seconds
        self.temperature = temperature
        if provider is not None:
            self.provider = provider
        else:
            self.provider = create_llm_provider(
                provider="openai",
                api_key=api_key or "",
                model=model,
                timeout_seconds=timeout_seconds,
                temperature=temperature,
                session=session,
                api_url=api_url,
            )
        # Retain attributes used by older call sites / tests.
        if isinstance(self.provider, OpenAIProvider):
            self.api_key = api_key or ""
            self.session = self.provider.session
            self.api_url = self.provider.api_url
        else:
            self.api_key = api_key or ""
            self.session = session
            self.api_url = api_url

    def score(self, item: SignalItem) -> JobRuleScore:
        """Return an LLM score, falling back to rule-based scoring on failure."""

        if item.item_type != "job":
            return self.fallback_scorer.score(item)

        try:
            result = self._score_with_llm(item)
        except JobLLMScorerError:
            return self.fallback_scorer.score(item)

        return _job_rule_score_from_llm(result)

    def score_with_llm(self, item: SignalItem) -> JobRuleScore:
        """Score with the LLM only; raise on API or parse failures."""

        if item.item_type != "job":
            raise JobLLMScorerError("LLM scoring requires item_type='job'")

        return _job_rule_score_from_llm(self._score_with_llm(item))

    def _score_with_llm(self, item: SignalItem) -> JobLLMScoreResult:
        request = LLMRequest(
            instructions=_SYSTEM_PROMPT,
            input=_build_user_prompt(item, self.profile),
            model=self.model,
            temperature=self.temperature,
            timeout_seconds=self.timeout_seconds,
            structured_output=_JOB_FIT_OUTPUT,
            store=False,
        )
        try:
            response = self.provider.generate(request)
        except LLMError as exc:
            raise JobLLMScorerError(str(exc)) from exc

        try:
            parsed = json.loads(response.text)
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
    job_lines = [
        f"Title: {item.title}",
        f"Company: {company}",
        f"Location: {location}",
        *_metadata_prompt_lines(item),
        f"Description: {description}",
    ]
    return (
        "Candidate profile:\n"
        f"{profile_summary}\n\n"
        "Job posting:\n"
        f"{'\n'.join(job_lines)}\n"
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


def _metadata_prompt_lines(item: SignalItem) -> list[str]:
    lines: list[str] = []
    for key in sorted(item.metadata):
        if key in _PROMPT_METADATA_SKIP_KEYS:
            continue
        value = item.metadata[key]
        if isinstance(value, str) and value.strip():
            lines.append(f"{_format_metadata_key(key)}: {value.strip()}")
    return lines


def _format_metadata_key(key: str) -> str:
    return key.replace("_", " ").strip().title()


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
