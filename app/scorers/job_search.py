"""Job-profile loading and lightweight rule-based scoring for job signals."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from importlib.resources import files
from pathlib import Path
from typing import Final

from app.core import SignalItem

DEFAULT_JOB_PROFILE_PATH = Path("app/profiles/henry_job_profile.md")
_DEFAULT_PROFILE_PACKAGE = "app.profiles"
_DEFAULT_PROFILE_FILENAME = "henry_job_profile.md"

_SECTION_ALIASES: Final[dict[str, str]] = {
    "target roles": "target_roles",
    "strong skills": "strong_skills",
    "preferred companies / industries": "preferred_companies_industries",
    "preferred companies/industries": "preferred_companies_industries",
    "preferred signals": "preferred_companies_industries",
    "avoid roles": "avoid_roles",
    "reject keywords": "reject_keywords",
    "reject signals": "reject_keywords",
    "location preferences": "location_preferences",
    "remote / hybrid preferences": "remote_hybrid_preferences",
    "remote/hybrid preferences": "remote_hybrid_preferences",
    "notes for scorer": "notes_for_scorer",
}

_POSITIVE_SIGNAL_WEIGHTS: Final[dict[str, float]] = {
    "target_roles": 4.0,
    "strong_skills": 2.0,
    "preferred_companies_industries": 1.5,
    "location_preferences": 1.0,
    "remote_hybrid_preferences": 1.0,
}

_NEGATIVE_SIGNAL_WEIGHTS: Final[dict[str, float]] = {
    "avoid_roles": 4.0,
    "reject_keywords": 3.0,
}


@dataclass(slots=True)
class JobSearchProfile:
    """Human-editable preferences used to rank job postings."""

    source_path: Path | None = None
    target_roles: list[str] = field(default_factory=list)
    strong_skills: list[str] = field(default_factory=list)
    preferred_companies_industries: list[str] = field(default_factory=list)
    avoid_roles: list[str] = field(default_factory=list)
    reject_keywords: list[str] = field(default_factory=list)
    location_preferences: list[str] = field(default_factory=list)
    remote_hybrid_preferences: list[str] = field(default_factory=list)
    # Parsed for human/LLM guidance; not used by rule-based scoring yet.
    notes_for_scorer: list[str] = field(default_factory=list)


@dataclass(slots=True)
class JobScore:
    """Simple score and match explanation for one job posting."""

    score: float
    matched_positive_signals: list[str] = field(default_factory=list)
    matched_negative_signals: list[str] = field(default_factory=list)


def load_job_search_profile(
    path: str | Path | None = None,
) -> JobSearchProfile:
    """Load a Markdown job-search profile from disk or the bundled default."""

    if path is None:
        resource = files(_DEFAULT_PROFILE_PACKAGE).joinpath(_DEFAULT_PROFILE_FILENAME)
        return parse_job_search_profile(
            resource.read_text(encoding="utf-8"),
            source_path=DEFAULT_JOB_PROFILE_PATH,
        )

    profile_path = Path(path).expanduser()
    return parse_job_search_profile(
        profile_path.read_text(encoding="utf-8"),
        source_path=profile_path,
    )


def parse_job_search_profile(
    markdown: str,
    *,
    source_path: Path | None = None,
) -> JobSearchProfile:
    """Parse the Markdown profile into a typed structure."""

    sections = {
        "target_roles": [],
        "strong_skills": [],
        "preferred_companies_industries": [],
        "avoid_roles": [],
        "reject_keywords": [],
        "location_preferences": [],
        "remote_hybrid_preferences": [],
        "notes_for_scorer": [],
    }
    current_section: str | None = None

    for raw_line in markdown.splitlines():
        stripped = raw_line.strip()
        if not stripped:
            continue

        heading_match = re.match(r"^#{1,6}\s+(?P<heading>.+?)\s*$", stripped)
        if heading_match:
            normalized_heading = _normalize_heading(heading_match.group("heading"))
            current_section = _SECTION_ALIASES.get(normalized_heading)
            continue

        if current_section is None:
            continue

        value = _normalize_section_value(stripped)
        if value:
            sections[current_section].append(value)

    return JobSearchProfile(source_path=source_path, **sections)


def score_job_posting(item: SignalItem, profile: JobSearchProfile) -> JobScore:
    """Score one job posting against the profile using phrase and token matches."""

    if item.item_type != "job":
        msg = "Job scoring only supports item_type='job'"
        raise ValueError(msg)

    search_text = _normalize_search_text(_build_search_text(item))
    positive_matches: list[str] = []
    negative_matches: list[str] = []
    score = 0.0

    for field_name, weight in _POSITIVE_SIGNAL_WEIGHTS.items():
        for signal in _find_matches(search_text, getattr(profile, field_name)):
            positive_matches.append(signal)
            score += weight

    for field_name, weight in _NEGATIVE_SIGNAL_WEIGHTS.items():
        for signal in _find_matches(search_text, getattr(profile, field_name)):
            negative_matches.append(signal)
            score -= weight

    return JobScore(
        score=score,
        matched_positive_signals=positive_matches,
        matched_negative_signals=negative_matches,
    )


def _build_search_text(item: SignalItem) -> str:
    parts = [item.title]
    if item.content:
        parts.append(item.content)
    if item.url:
        parts.append(item.url)
    parts.extend(_flatten_metadata_values(item.metadata))
    return " ".join(parts)


def _flatten_metadata_values(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        results: list[str] = []
        for nested_value in value.values():
            results.extend(_flatten_metadata_values(nested_value))
        return results
    if isinstance(value, (list, tuple, set)):
        results = []
        for nested_value in value:
            results.extend(_flatten_metadata_values(nested_value))
        return results
    return [str(value)]


def _find_matches(search_text: str, signals: list[str]) -> list[str]:
    matches: list[str] = []
    for signal in signals:
        normalized_signal = _normalize_search_text(signal)
        if _signal_matches(search_text, normalized_signal):
            matches.append(signal)
    return matches


def _signal_matches(search_text: str, normalized_signal: str) -> bool:
    if not normalized_signal:
        return False
    if re.search(r"\s", normalized_signal):
        return normalized_signal in search_text

    pattern = re.compile(
        rf"(?<![\w]){re.escape(normalized_signal)}(?![\w])",
        re.ASCII,
    )
    return pattern.search(search_text) is not None


def _normalize_heading(heading: str) -> str:
    return re.sub(r"\s+", " ", heading.strip().casefold())


def _normalize_search_text(text: str) -> str:
    return re.sub(r"\s+", " ", text.casefold()).strip()


def _normalize_section_value(line: str) -> str:
    return re.sub(r"^(?:[-*+]\s+|\d+\.\s+|>\s+)", "", line).strip()
