"""Glassdoor Taiwan job-search source."""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from html import unescape
from typing import ClassVar
from urllib.parse import parse_qs, urlparse

import requests

from app.core import SignalItem
from app.core.config import AppConfig

from .base import BaseSource
from .http_settings import validate_http_fetch_settings

_DEFAULT_SEARCH_URL = "https://www.glassdoor.com/Job/jobs.htm"
_DEFAULT_REQUEST_DELAY_SECONDS = 1.0
_DEFAULT_TIMEOUT_SECONDS = 10.0
_TRANSIENT_STATUS_CODES = {429, 500, 502, 503, 504}
_DEFAULT_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.glassdoor.com/Job/",
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/137.0.0.0 Safari/537.36"
    ),
}
_DEFAULT_LOCATIONS = ("Taiwan",)
_TAIWAN_LOCATION_ID = "238"
_JOB_CARD_RE = re.compile(
    r"<li\b[^>]*data-test=\"jobListing\"[^>]*>(?P<body>.*?)</li>",
    re.IGNORECASE | re.DOTALL,
)


class GlassdoorSourceError(RuntimeError):
    """Raised when the Glassdoor source cannot fetch or normalize results."""


@dataclass(slots=True)
class _SearchSpec:
    keyword: str
    location_filter: str | None
    search_term: str


@dataclass(slots=True)
class GlassdoorSource(BaseSource):
    """Fetch job postings from Glassdoor Taiwan search results."""

    keywords: list[str]
    locations: list[str] | None = None
    session: requests.Session | None = None
    request_delay_seconds: float = _DEFAULT_REQUEST_DELAY_SECONDS
    timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS
    max_retries: int = 2
    page_limit: int = 1
    search_url: str = _DEFAULT_SEARCH_URL
    sleeper: Callable[[float], None] = time.sleep

    name: ClassVar[str] = "glassdoor"

    def __post_init__(self) -> None:
        self.keywords = [
            keyword.strip()
            for keyword in self.keywords
            if keyword.strip()
        ]
        configured_locations = [
            location.strip()
            for location in (self.locations or [])
            if location.strip()
        ]
        self.locations = configured_locations or list(_DEFAULT_LOCATIONS)
        validate_http_fetch_settings(
            timeout_seconds=self.timeout_seconds,
            request_delay_seconds=self.request_delay_seconds,
            max_retries=self.max_retries,
        )
        if self.page_limit <= 0:
            raise ValueError("page_limit must be greater than zero")

    @classmethod
    def from_config(
        cls,
        config: AppConfig,
        *,
        session: requests.Session | None = None,
        request_delay_seconds: float | None = None,
        timeout_seconds: float | None = None,
        max_retries: int | None = None,
        page_limit: int | None = None,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> GlassdoorSource:
        """Build the source from application config."""

        settings = config.sources.glassdoor.with_overrides(
            request_delay_seconds=request_delay_seconds,
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
            page_limit=page_limit,
        )
        return cls(
            keywords=config.tasks.job_search.keywords,
            locations=config.tasks.job_search.locations,
            session=session,
            request_delay_seconds=settings.request_delay_seconds,
            timeout_seconds=settings.timeout_seconds,
            max_retries=settings.max_retries,
            page_limit=settings.page_limit,
            sleeper=sleeper,
        )

    def fetch(self) -> list[SignalItem]:
        if not self.keywords:
            return []

        session = self.session
        owns_session = session is None
        if owns_session:
            session = requests.Session()
            session.headers.update(_DEFAULT_HEADERS)

        items: list[SignalItem] = []
        is_first_request = True

        try:
            for search_spec in self._iter_search_specs():
                for page in range(1, self.page_limit + 1):
                    if not is_first_request:
                        self.sleeper(self.request_delay_seconds)
                    is_first_request = False

                    html = self._request_search_page(
                        session=session,
                        search_spec=search_spec,
                        page=page,
                    )
                    jobs = self._extract_jobs(html)
                    if not jobs:
                        break
                    items.extend(self._normalize_jobs(jobs, search_spec))
        finally:
            if owns_session and hasattr(session, "close"):
                session.close()

        return items

    def _iter_search_specs(self) -> list[_SearchSpec]:
        search_specs: list[_SearchSpec] = []
        for keyword in self.keywords:
            for location in self.locations or list(_DEFAULT_LOCATIONS):
                search_specs.append(
                    _SearchSpec(
                        keyword=keyword,
                        location_filter=location,
                        search_term=keyword,
                    )
                )
        return search_specs

    def _request_search_page(
        self,
        *,
        session: requests.Session,
        search_spec: _SearchSpec,
        page: int,
    ) -> str:
        params = {
            "sc.keyword": search_spec.search_term,
            "locKeyword": search_spec.location_filter or _DEFAULT_LOCATIONS[0],
            "p": str(page),
        }
        if (search_spec.location_filter or "").casefold() == "taiwan":
            params["locId"] = _TAIWAN_LOCATION_ID
            params["locT"] = "N"

        for attempt in range(self.max_retries + 1):
            try:
                response = session.get(
                    self.search_url,
                    params=params,
                    timeout=self.timeout_seconds,
                )
            except requests.RequestException as exc:
                if attempt >= self.max_retries:
                    raise GlassdoorSourceError(
                        "Glassdoor search request failed for "
                        f"'{search_spec.search_term}' in "
                        f"'{params['locKeyword']}': {exc}"
                    ) from exc
                self._sleep_for_retry(attempt)
                continue

            if (
                response.status_code in _TRANSIENT_STATUS_CODES
                and attempt < self.max_retries
            ):
                self._sleep_for_retry(attempt)
                continue

            if response.status_code >= 400:
                detail = _describe_text_response(response.text, response.headers)
                raise GlassdoorSourceError(
                    "Glassdoor search failed for "
                    f"'{search_spec.search_term}' in "
                    f"'{params['locKeyword']}' with status "
                    f"{response.status_code}: {detail}"
                )

            html = response.text
            if _looks_like_bot_challenge(html):
                raise GlassdoorSourceError(
                    "Glassdoor search returned a bot challenge for "
                    f"'{search_spec.search_term}' in '{params['locKeyword']}'"
                )

            return html

        raise GlassdoorSourceError(
            f"Glassdoor search exhausted retries for '{search_spec.search_term}'"
        )

    def _sleep_for_retry(self, attempt: int) -> None:
        self.sleeper(self.request_delay_seconds * (attempt + 1))

    def _extract_jobs(self, html: str) -> list[dict[str, str | None]]:
        jobs: list[dict[str, str | None]] = []
        for match in _JOB_CARD_RE.finditer(html):
            body = match.group("body")
            href = _extract_first_match(
                body,
                [
                    r'data-test="job-link"[^>]*href="([^"]+)"',
                    r'href="([^"]*jobListingId=[^"]+)"',
                ],
            )
            title = _extract_first_match(
                body,
                [
                    r'data-test="job-link"[^>]*>(.*?)</a>',
                    r'aria-label="([^"]+)"',
                ],
            )
            company = _extract_first_match(
                body,
                [
                    r'data-test="employer-name"[^>]*>(.*?)</[^>]+>',
                    r'class="[^"]*employerName[^"]*"[^>]*>(.*?)</[^>]+>',
                ],
            )
            location = _extract_first_match(
                body,
                [r'data-test="location"[^>]*>(.*?)</[^>]+>'],
            )
            salary = _extract_first_match(
                body,
                [
                    r'data-test="detailSalary"[^>]*>(.*?)</[^>]+>',
                    r'class="[^"]*salaryEstimate[^"]*"[^>]*>(.*?)</[^>]+>',
                ],
            )
            description = _extract_first_match(
                body,
                [
                    r'data-test="job-snippet"[^>]*>(.*?)</div>',
                    r'class="[^"]*job-snippet[^"]*"[^>]*>(.*?)</div>',
                ],
            )
            posted_date = _extract_first_match(
                body,
                [
                    r'data-test="job-age"[^>]*>(.*?)</[^>]+>',
                    r'class="[^"]*jobAge[^"]*"[^>]*>(.*?)</[^>]+>',
                ],
            )
            job_listing_id = _extract_job_listing_id(body, href)
            if not title or not job_listing_id:
                continue

            jobs.append(
                {
                    "job_listing_id": job_listing_id,
                    "title": title,
                    "href": href,
                    "company": company,
                    "location": location,
                    "salary": salary,
                    "description": description,
                    "posted_date": posted_date,
                }
            )

        return jobs

    def _normalize_jobs(
        self,
        jobs: list[dict[str, str | None]],
        search_spec: _SearchSpec,
    ) -> list[SignalItem]:
        observed_at = datetime.now(UTC)
        items: list[SignalItem] = []

        for job in jobs:
            job_listing_id = job["job_listing_id"]
            title = _normalize_text(job["title"])
            if not job_listing_id or not title:
                continue

            url = _normalize_job_url(job["href"], job_listing_id=job_listing_id)
            metadata = _compact_metadata(
                {
                    "company": _normalize_text(job["company"]),
                    "location": _normalize_text(job["location"]),
                    "salary": _normalize_text(job["salary"]),
                    "keyword": search_spec.keyword,
                    "posted_date": _normalize_text(job["posted_date"]),
                    "location_filter": search_spec.location_filter,
                    "search_term": search_spec.search_term,
                }
            )

            items.append(
                SignalItem(
                    source=self.name,
                    item_type="job",
                    title=title,
                    observed_at=observed_at,
                    url=url,
                    external_id=f"glassdoor-{job_listing_id}",
                    content=_normalize_text(job["description"]),
                    metadata=metadata,
                )
            )

        return items


def _extract_first_match(text: str, patterns: list[str]) -> str | None:
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
        if match is None:
            continue
        value = _normalize_text(_strip_html(match.group(1)))
        if value:
            return value
    return None


def _extract_job_listing_id(body: str, raw_url: str | None) -> str | None:
    data_id = _extract_first_match(
        body,
        [r'data-id="([^"]+)"', r'data-jobid="([^"]+)"'],
    )
    if data_id:
        return data_id
    if raw_url is None:
        return None
    parsed = urlparse(raw_url)
    query = parse_qs(parsed.query)
    values = query.get("jobListingId")
    if not values:
        return None
    job_listing_id = values[0].strip()
    return job_listing_id or None


def _normalize_job_url(raw_url: str | None, *, job_listing_id: str) -> str:
    if raw_url:
        if raw_url.startswith("/"):
            return f"https://www.glassdoor.com{raw_url}"
        if raw_url.startswith("http://") or raw_url.startswith("https://"):
            return raw_url
    return (
        "https://www.glassdoor.com/job-listing/"
        f"?jobListingId={job_listing_id}"
    )


def _looks_like_bot_challenge(html: str) -> bool:
    lowered = html.lower()
    return (
        "just a moment" in lowered
        or "verify you are human" in lowered
        or "cf-mitigated" in lowered
    )


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


def _describe_text_response(
    text: str,
    headers: dict[str, str] | None,
) -> str:
    content_type = ""
    if headers is not None:
        content_type = str(headers.get("Content-Type", "")).strip()
    preview = _normalize_text(_strip_html(text)) or ""
    preview = preview[:120]
    if preview:
        return f"{content_type or 'unknown content type'}: {preview}"
    return content_type or "unknown response body"
