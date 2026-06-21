"""Offline rule-based scorer for job postings."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Final

from app.core import SignalItem

from .job_search import (
    JobProfileMatches,
    JobSearchProfile,
    load_job_search_profile,
    match_job_posting,
)

_SECTION_DEFAULT_WEIGHTS: Final[dict[str, int]] = {
    "target_roles": 20,
    "strong_skills": 10,
    "preferred_companies_industries": 5,
    "location_preferences": 5,
    "remote_hybrid_preferences": 5,
    "avoid_roles": -20,
    "reject_keywords": -20,
}

_SIGNAL_WEIGHT_OVERRIDES: Final[dict[str, int]] = {
    "devsecops": 15,
    "devsecops engineer": 15,
    "pure customer support": -30,
    "customer support": -30,
    "sla-heavy support": -30,
    "sla": -30,
}

_SECTION_REASON_LABELS: Final[dict[str, str]] = {
    "target_roles": "target role",
    "strong_skills": "strong skill",
    "preferred_companies_industries": "preferred industry/company",
    "location_preferences": "location preference",
    "remote_hybrid_preferences": "work-style preference",
    "avoid_roles": "avoid role",
    "reject_keywords": "reject keyword",
}

_STRONG_MATCH_MIN_SCORE: Final[int] = 60
_MAYBE_MIN_SCORE: Final[int] = 25


@dataclass(slots=True)
class JobRuleScore:
    """Normalized rule-based job score."""

    score: int
    recommendation: str
    reason: str
    reasons: list[str] = field(default_factory=list)
    concerns: list[str] = field(default_factory=list)


@dataclass(slots=True)
class _ScoredReason:
    text: str
    points: int
    signal: str


class JobRuleScorer:
    """Offline job scorer that uses the Markdown job profile."""

    def __init__(self, profile: JobSearchProfile | None = None) -> None:
        self.profile = profile or load_job_search_profile()

    def score(self, item: SignalItem) -> JobRuleScore:
        matches = match_job_posting(item, self.profile)
        if _has_critical_negative_matches(matches):
            matches = _strip_secondary_positive_matches(matches)
        reasons = _build_positive_reasons(matches)
        concerns = _build_negative_concerns(matches)

        raw_score = 0
        for entry in reasons:
            raw_score += entry.points
        for entry in concerns:
            raw_score += entry.points

        score = max(0, min(100, raw_score))
        recommendation = _recommendation_for_score(score)
        reason = _build_reason_summary(score, recommendation, reasons, concerns)
        return JobRuleScore(
            score=score,
            recommendation=recommendation,
            reason=reason,
            reasons=[entry.text for entry in reasons],
            concerns=[entry.text for entry in concerns],
        )


def _has_critical_negative_matches(matches: JobProfileMatches) -> bool:
    return bool(
        matches.negative_matches.get("avoid_roles")
        or matches.negative_matches.get("reject_keywords")
    )


def _strip_secondary_positive_matches(
    matches: JobProfileMatches,
) -> JobProfileMatches:
    positive_matches = {
        section: signals
        for section, signals in matches.positive_matches.items()
        if section not in {"location_preferences", "remote_hybrid_preferences"}
    }
    return JobProfileMatches(
        positive_matches=positive_matches,
        negative_matches=matches.negative_matches,
    )


def _build_positive_reasons(matches: JobProfileMatches) -> list[_ScoredReason]:
    reasons: list[_ScoredReason] = []
    for section_name, signals in matches.positive_matches.items():
        label = _SECTION_REASON_LABELS[section_name]
        for signal in signals:
            points = _points_for_signal(section_name, signal)
            reasons.append(
                _ScoredReason(
                    text=f"Matched {label}: {signal} (+{points})",
                    points=points,
                    signal=signal,
                )
            )
    return reasons


def _build_negative_concerns(matches: JobProfileMatches) -> list[_ScoredReason]:
    concerns: list[_ScoredReason] = []
    for section_name, signals in matches.negative_matches.items():
        label = _SECTION_REASON_LABELS[section_name]
        for signal in signals:
            points = _points_for_signal(section_name, signal)
            concerns.append(
                _ScoredReason(
                    text=f"Matched {label}: {signal} ({points})",
                    points=points,
                    signal=signal,
                )
            )
    return concerns


def _points_for_signal(section_name: str, signal: str) -> int:
    normalized_signal = _normalize_signal(signal)
    if normalized_signal in _SIGNAL_WEIGHT_OVERRIDES:
        return _SIGNAL_WEIGHT_OVERRIDES[normalized_signal]
    return _SECTION_DEFAULT_WEIGHTS[section_name]


def _recommendation_for_score(score: int) -> str:
    if score >= _STRONG_MATCH_MIN_SCORE:
        return "strong_match"
    if score >= _MAYBE_MIN_SCORE:
        return "maybe"
    return "reject"


def _build_reason_summary(
    score: int,
    recommendation: str,
    reasons: list[_ScoredReason],
    concerns: list[_ScoredReason],
) -> str:
    recommendation_text = recommendation.replace("_", " ")
    positive_summary = _summarize_signals(
        reasons,
        fallback="no strong positive signals",
    )
    concern_summary = _summarize_signals(concerns, fallback="no major concerns")
    return (
        f"{recommendation_text} ({score}/100): "
        f"positives: {positive_summary}; concerns: {concern_summary}."
    )


def _summarize_signals(
    entries: list[_ScoredReason],
    *,
    fallback: str,
) -> str:
    if not entries:
        return fallback

    ordered = sorted(entries, key=lambda entry: abs(entry.points), reverse=True)
    top_signals: list[str] = []
    for entry in ordered:
        if entry.signal not in top_signals:
            top_signals.append(entry.signal)
        if len(top_signals) == 3:
            break
    return ", ".join(top_signals)


def _normalize_signal(signal: str) -> str:
    return re.sub(r"\s+", " ", signal.casefold()).strip()
