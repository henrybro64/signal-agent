"""LinkedIn job-alert email source."""

from __future__ import annotations

import imaplib
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from email import message_from_bytes, policy
from email.message import EmailMessage
from email.utils import parsedate_to_datetime
from html import unescape
from pathlib import Path
from typing import ClassVar

from app.core import (
    SignalItem,
    load_env_settings,
    validate_linkedin_email_source_secrets,
)
from app.core.config import AppConfig
from app.core.secrets import EnvSettings, SecretError

from .base import BaseSource

_DEFAULT_IMAP_MAILBOX = "INBOX"
_DEFAULT_LOOKBACK_DAYS = 14
_LINKEDIN_JOB_URL_RE = re.compile(
    r"https?://www\.linkedin\.com/jobs/view/(?P<job_id>\d+)[^\"'\s<)]*",
    re.IGNORECASE,
)
_HTML_JOB_LINK_RE = re.compile(
    r"<a[^>]+href=\"(?P<url>https?://www\.linkedin\.com/jobs/view/[^\"]+)\""
    r"[^>]*>(?P<title>.*?)</a>",
    re.IGNORECASE | re.DOTALL,
)
_GENERIC_LINK_TEXTS = {
    "apply",
    "apply now",
    "view job",
    "see job",
    "learn more",
}


class LinkedInEmailSourceError(RuntimeError):
    """Raised when LinkedIn email messages cannot be loaded or parsed."""


@dataclass(slots=True)
class _ParsedJob:
    external_id: str
    title: str
    company: str | None
    location: str | None
    url: str
    snippet: str | None


@dataclass(slots=True)
class LinkedInEmailSource(BaseSource):
    """Parse LinkedIn job-alert emails from local `.eml` files or IMAP."""

    keywords: list[str]
    locations: list[str] = field(default_factory=list)
    email_paths: list[Path] = field(default_factory=list)
    imap_host: str | None = None
    imap_username: str | None = None
    imap_password: str | None = None
    imap_mailbox: str = _DEFAULT_IMAP_MAILBOX
    lookback_days: int = _DEFAULT_LOOKBACK_DAYS

    name: ClassVar[str] = "linkedin_email"

    def __post_init__(self) -> None:
        self.keywords = [
            keyword.strip()
            for keyword in self.keywords
            if keyword.strip()
        ]
        self.locations = [
            location.strip()
            for location in self.locations
            if location.strip()
        ]
        self.email_paths = [Path(path).expanduser() for path in self.email_paths]
        self.imap_mailbox = self.imap_mailbox.strip() or _DEFAULT_IMAP_MAILBOX
        if self.lookback_days <= 0:
            raise ValueError("lookback_days must be greater than zero")

    @classmethod
    def from_config(
        cls,
        config: AppConfig,
        *,
        env: EnvSettings | None = None,
        lookback_days: int | None = None,
        imap_mailbox: str | None = None,
    ) -> LinkedInEmailSource:
        """Build the source from config and environment settings."""

        resolved_env = env or load_env_settings()
        settings = config.sources.linkedin_email
        email_paths: list[Path] = []
        if resolved_env.linkedin_email_source_path:
            source_path = Path(
                resolved_env.linkedin_email_source_path
            ).expanduser()
            if source_path.is_dir():
                email_paths = sorted(source_path.glob("*.eml"))
            else:
                email_paths = [source_path]

        # Precedence: explicit kwarg > config.yaml > LINKEDIN_EMAIL_IMAP_MAILBOX.
        if imap_mailbox is not None:
            resolved_mailbox = imap_mailbox
        elif settings.imap_mailbox is not None:
            resolved_mailbox = settings.imap_mailbox
        else:
            resolved_mailbox = resolved_env.linkedin_email_imap_mailbox
        return cls(
            keywords=config.tasks.job_search.keywords,
            locations=config.tasks.job_search.locations,
            email_paths=email_paths,
            imap_host=resolved_env.linkedin_email_imap_host,
            imap_username=resolved_env.linkedin_email_imap_username,
            imap_password=resolved_env.linkedin_email_imap_password,
            imap_mailbox=resolved_mailbox,
            lookback_days=(
                settings.lookback_days
                if lookback_days is None
                else lookback_days
            ),
        )

    def fetch(self) -> list[SignalItem]:
        messages = self._load_messages()
        seen_ids: set[str] = set()
        items: list[SignalItem] = []

        for raw_message in messages:
            email_message = message_from_bytes(
                raw_message,
                policy=policy.default,
            )
            observed_at = _message_datetime(email_message)
            metadata = _message_metadata(email_message)

            for job in self._extract_jobs(email_message):
                if job.external_id in seen_ids:
                    continue
                if not self._matches_filters(job):
                    continue
                seen_ids.add(job.external_id)
                items.append(
                    SignalItem(
                        source=self.name,
                        item_type="job",
                        title=job.title,
                        observed_at=observed_at,
                        url=job.url,
                        external_id=job.external_id,
                        content=job.snippet,
                        metadata={
                            **metadata,
                            **_compact_metadata(
                                {
                                    "company": job.company,
                                    "location": job.location,
                                }
                            ),
                        },
                    )
                )

        return items

    def _load_messages(self) -> list[bytes]:
        if self.email_paths:
            return self._load_messages_from_paths()
        return self._load_messages_from_imap()

    def _load_messages_from_paths(self) -> list[bytes]:
        messages: list[bytes] = []
        for email_path in self.email_paths:
            if not email_path.exists():
                raise FileNotFoundError(
                    f"LinkedIn email source path not found: {email_path}"
                )
            if email_path.is_dir():
                messages.extend(
                    child.read_bytes()
                    for child in sorted(email_path.glob("*.eml"))
                )
                continue
            messages.append(email_path.read_bytes())
        return messages

    def _load_messages_from_imap(self) -> list[bytes]:
        env = EnvSettings(
            linkedin_email_imap_host=self.imap_host,
            linkedin_email_imap_username=self.imap_username,
            linkedin_email_imap_password=self.imap_password,
        )
        validate_linkedin_email_source_secrets(env)

        since_text = (datetime.now(UTC) - timedelta(days=self.lookback_days)).strftime(
            "%d-%b-%Y"
        )

        try:
            with imaplib.IMAP4_SSL(self.imap_host) as client:
                client.login(self.imap_username, self.imap_password)
                client.select(self.imap_mailbox)
                status, data = client.search(None, "SINCE", since_text)
                if status != "OK":
                    raise LinkedInEmailSourceError(
                        "LinkedIn email IMAP search failed"
                    )

                messages: list[bytes] = []
                for message_id in data[0].split():
                    fetch_status, fetch_data = client.fetch(message_id, "(RFC822)")
                    if fetch_status != "OK":
                        continue
                    for part in fetch_data:
                        if isinstance(part, tuple) and len(part) == 2:
                            raw_message = part[1]
                            if isinstance(raw_message, bytes):
                                email_message = message_from_bytes(
                                    raw_message,
                                    policy=policy.default,
                                )
                                if _looks_like_linkedin_job_alert(email_message):
                                    messages.append(raw_message)
                return messages
        except SecretError:
            raise
        except imaplib.IMAP4.error as exc:
            raise LinkedInEmailSourceError(
                f"LinkedIn email IMAP login failed: {exc}"
            ) from exc
        except OSError as exc:
            raise LinkedInEmailSourceError(
                f"LinkedIn email IMAP connection failed: {exc}"
            ) from exc

    def _extract_jobs(self, email_message: EmailMessage) -> list[_ParsedJob]:
        html_body = _extract_message_body(email_message, preferred_type="text/html")
        if html_body is not None:
            html_jobs = _extract_jobs_from_html(html_body)
            if html_jobs:
                return html_jobs

        text_body = _extract_message_body(email_message, preferred_type="text/plain")
        if text_body is None:
            return []

        return _extract_jobs_from_text(text_body)

    def _matches_filters(self, job: _ParsedJob) -> bool:
        if self.keywords:
            haystack = " ".join(
                value
                for value in [job.title, job.company, job.location, job.snippet]
                if value is not None
            ).casefold()
            if not any(keyword.casefold() in haystack for keyword in self.keywords):
                return False

        if self.locations:
            location_text = " ".join(
                value
                for value in [job.location, job.snippet]
                if value is not None
            ).casefold()
            if location_text and not any(
                location.casefold() in location_text
                for location in self.locations
            ):
                return False

        return True


def _extract_message_body(
    email_message: EmailMessage,
    *,
    preferred_type: str,
) -> str | None:
    if email_message.is_multipart():
        for part in email_message.walk():
            if part.get_content_type() != preferred_type:
                continue
            try:
                content = part.get_content()
            except LookupError:
                continue
            if isinstance(content, str) and content.strip():
                return content
        return None

    if email_message.get_content_type() != preferred_type:
        return None

    content = email_message.get_content()
    if isinstance(content, str) and content.strip():
        return content
    return None


def _extract_jobs_from_html(html: str) -> list[_ParsedJob]:
    matches = list(_HTML_JOB_LINK_RE.finditer(html))
    if not matches:
        return []

    jobs: list[_ParsedJob] = []
    for index, match in enumerate(matches):
        url = _normalize_linkedin_job_url(match.group("url"))
        title = _normalize_text(_strip_html(match.group("title")))
        if title is None or title.casefold() in _GENERIC_LINK_TEXTS:
            continue

        job_id = _extract_job_id(url)
        if job_id is None:
            continue

        segment_start = match.end()
        segment_end = len(html)
        if index + 1 < len(matches):
            segment_end = matches[index + 1].start()
        segment = html[segment_start:segment_end]
        chunks = _extract_text_chunks(segment)
        company = chunks[0] if len(chunks) >= 1 else None
        location = chunks[1] if len(chunks) >= 2 else None
        snippet = " ".join(chunks[2:]) if len(chunks) >= 3 else None

        jobs.append(
            _ParsedJob(
                external_id=f"linkedin-email-{job_id}",
                title=title,
                company=company,
                location=location,
                url=url,
                snippet=_normalize_text(snippet),
            )
        )

    return jobs


def _extract_jobs_from_text(text: str) -> list[_ParsedJob]:
    labeled_matcher = re.compile(
        r"Job Title:\s*(?P<title>.+?)\n"
        r"Company:\s*(?P<company>.+?)\n"
        r"Location:\s*(?P<location>.+?)\n"
        r"Snippet:\s*(?P<snippet>.+?)\n"
        r"Apply:\s*(?P<url>https?://www\.linkedin\.com/jobs/view/\d+[^\s]*)",
        re.IGNORECASE | re.DOTALL,
    )

    labeled_jobs: list[_ParsedJob] = []
    for match in labeled_matcher.finditer(text):
        url = _normalize_linkedin_job_url(match.group("url"))
        job_id = _extract_job_id(url)
        title = _normalize_text(match.group("title"))
        if job_id is None or title is None:
            continue
        labeled_jobs.append(
            _ParsedJob(
                external_id=f"linkedin-email-{job_id}",
                title=title,
                company=_normalize_text(match.group("company")),
                location=_normalize_text(match.group("location")),
                url=url,
                snippet=_normalize_text(match.group("snippet")),
            )
        )

    if labeled_jobs:
        return labeled_jobs

    lines = [
        _normalize_text(line)
        for line in text.splitlines()
    ]
    lines = [line for line in lines if line is not None]
    jobs: list[_ParsedJob] = []

    for index, line in enumerate(lines):
        url_match = _LINKEDIN_JOB_URL_RE.search(line)
        if url_match is None:
            continue
        url = _normalize_linkedin_job_url(url_match.group(0))
        job_id = url_match.group("job_id")
        context = lines[max(0, index - 4):index]
        if not context:
            continue
        title = context[-1]
        company = context[-2] if len(context) >= 2 else None
        location = context[-3] if len(context) >= 3 else None
        snippet = context[-4] if len(context) >= 4 else None
        if title.casefold() in _GENERIC_LINK_TEXTS:
            continue
        jobs.append(
            _ParsedJob(
                external_id=f"linkedin-email-{job_id}",
                title=title,
                company=company,
                location=location,
                url=url,
                snippet=snippet,
            )
        )

    return jobs


def _message_datetime(email_message: EmailMessage) -> datetime:
    header = email_message.get("Date")
    if header is None:
        return datetime.now(UTC)
    try:
        parsed = parsedate_to_datetime(header)
    except (TypeError, ValueError, IndexError):
        return datetime.now(UTC)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _message_metadata(email_message: EmailMessage) -> dict[str, str]:
    return _compact_metadata(
        {
            "email_subject": _normalize_text(email_message.get("Subject")),
            "email_from": _normalize_text(email_message.get("From")),
        }
    )


def _looks_like_linkedin_job_alert(email_message: EmailMessage) -> bool:
    sender = _normalize_text(email_message.get("From")) or ""
    subject = _normalize_text(email_message.get("Subject")) or ""
    sender_matches = "linkedin" in sender.casefold()
    subject_lower = subject.casefold()
    subject_matches = "job" in subject_lower or "alert" in subject_lower
    return sender_matches and subject_matches


def _extract_job_id(url: str) -> str | None:
    match = _LINKEDIN_JOB_URL_RE.search(url)
    if match is None:
        return None
    job_id = match.group("job_id").strip()
    return job_id or None


def _normalize_linkedin_job_url(url: str) -> str:
    return unescape(url).strip()


def _extract_text_chunks(html_segment: str) -> list[str]:
    normalized = re.sub(r"(?i)<br\s*/?>", "\n", html_segment)
    normalized = re.sub(r"(?i)</(p|div|li|td|tr|section|table|span)>", "\n", normalized)
    text = _strip_html(normalized)
    chunks = [
        chunk
        for chunk in (
            _normalize_text(line)
            for line in text.splitlines()
        )
        if chunk is not None and chunk.casefold() not in _GENERIC_LINK_TEXTS
    ]
    return chunks


def _strip_html(value: str) -> str:
    without_tags = re.sub(r"<[^>]+>", " ", value)
    return unescape(without_tags)


def _normalize_text(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = re.sub(r"\s+", " ", value).strip()
    return normalized or None


def _compact_metadata(metadata: dict[str, str | None]) -> dict[str, str]:
    return {
        key: value
        for key, value in metadata.items()
        if value is not None and value != ""
    }
