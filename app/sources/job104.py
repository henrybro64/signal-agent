"""104 job-search source."""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, ClassVar

import requests

from app.core import SignalItem
from app.core.config import AppConfig

from .base import BaseSource
from .http_settings import validate_http_fetch_settings

_DEFAULT_SEARCH_URL = "https://www.104.com.tw/jobs/search/list"
_DEFAULT_REQUEST_DELAY_SECONDS = 1.0
_DEFAULT_TIMEOUT_SECONDS = 10.0
_TRANSIENT_STATUS_CODES = {429, 500, 502, 503, 504}
_DEFAULT_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://www.104.com.tw/jobs/search/",
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/137.0.0.0 Safari/537.36"
    ),
    "X-Requested-With": "XMLHttpRequest",
}


class Job104SourceError(RuntimeError):
    """Raised when the 104 source cannot fetch or normalize results."""


@dataclass(slots=True)
class _SearchSpec:
    keyword: str
    location_filter: str | None
    search_term: str


@dataclass(slots=True)
class Job104Source(BaseSource):
    """Fetch job postings from 104's public search surface."""

    keywords: list[str]
    locations: list[str] | None = None
    session: requests.Session | None = None
    request_delay_seconds: float = _DEFAULT_REQUEST_DELAY_SECONDS
    timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS
    max_retries: int = 2
    page_limit: int = 1
    search_url: str = _DEFAULT_SEARCH_URL
    sleeper: Callable[[float], None] = time.sleep

    name: ClassVar[str] = "job104"

    def __post_init__(self) -> None:
        self.keywords = [
            keyword.strip()
            for keyword in self.keywords
            if keyword.strip()
        ]
        self.locations = [
            location.strip()
            for location in (self.locations or [])
            if location.strip()
        ]
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
    ) -> Job104Source:
        """Build the source from application config."""

        settings = config.sources.job104.with_overrides(
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

                    payload = self._request_search_page(
                        session=session,
                        search_spec=search_spec,
                        page=page,
                    )
                    jobs = self._extract_jobs(
                        payload,
                        search_term=search_spec.search_term,
                    )
                    if not jobs:
                        break
                    items.extend(self._normalize_jobs(jobs, search_spec))
        finally:
            if owns_session and hasattr(session, "close"):
                session.close()

        return items

    def _iter_search_specs(self) -> list[_SearchSpec]:
        search_specs: list[_SearchSpec] = []
        if not self.locations:
            for keyword in self.keywords:
                search_specs.append(
                    _SearchSpec(
                        keyword=keyword,
                        location_filter=None,
                        search_term=keyword,
                    )
                )
            return search_specs

        for keyword in self.keywords:
            for location in self.locations:
                search_specs.append(
                    _SearchSpec(
                        keyword=keyword,
                        location_filter=location,
                        # 104 location filters typically use numeric area codes;
                        # until those are configured explicitly, fold locations
                        # into the public keyword query string.
                        search_term=f"{keyword} {location}".strip(),
                    )
                )
        return search_specs

    def _request_search_page(
        self,
        *,
        session: requests.Session,
        search_spec: _SearchSpec,
        page: int,
    ) -> dict[str, Any]:
        params = {
            "ro": "0",
            "keyword": search_spec.search_term,
            "page": str(page),
            "order": "15",
            "asc": "0",
            "mode": "s",
        }

        for attempt in range(self.max_retries + 1):
            try:
                response = session.get(
                    self.search_url,
                    params=params,
                    timeout=self.timeout_seconds,
                )
            except requests.RequestException as exc:
                if attempt >= self.max_retries:
                    raise Job104SourceError(
                        "104 search request failed for "
                        f"'{search_spec.search_term}': {exc}"
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
                raise Job104SourceError(
                    "104 search failed for "
                    f"'{search_spec.search_term}' with status {response.status_code}"
                )

            try:
                payload = response.json()
            except ValueError as exc:
                detail = self._describe_non_json_response(response)
                raise Job104SourceError(
                    "104 search returned non-JSON content for "
                    f"'{search_spec.search_term}': {detail}"
                ) from exc

            if not isinstance(payload, dict):
                raise Job104SourceError(
                    "104 search returned an unexpected payload for "
                    f"'{search_spec.search_term}'"
                )
            return payload

        raise Job104SourceError(
            f"104 search exhausted retries for '{search_spec.search_term}'"
        )

    def _sleep_for_retry(self, attempt: int) -> None:
        self.sleeper(self.request_delay_seconds * (attempt + 1))

    def _extract_jobs(
        self,
        payload: dict[str, Any],
        *,
        search_term: str,
    ) -> list[dict[str, Any]]:
        data = payload.get("data")
        if not isinstance(data, dict):
            raise Job104SourceError(
                f"104 search payload for '{search_term}' did not include a data object"
            )

        jobs = data.get("list")
        if jobs is None:
            return []
        if not isinstance(jobs, list):
            raise Job104SourceError(
                f"104 search payload for '{search_term}' did not include a job list"
            )

        return [job for job in jobs if isinstance(job, dict)]

    def _normalize_jobs(
        self,
        jobs: list[dict[str, Any]],
        search_spec: _SearchSpec,
    ) -> list[SignalItem]:
        observed_at = datetime.now(UTC)
        items: list[SignalItem] = []

        for job in jobs:
            title = _first_str(job, "jobName", "title", "jobTitle")
            job_no = _first_str(job, "jobNo", "id")
            if not title or not job_no:
                continue

            company = _first_str(job, "custName", "company")
            location = _first_str(job, "jobAddrNoDesc", "addrNoDesc", "location")
            salary = _first_str(job, "salaryDesc", "salary")
            description = _normalize_text(
                _first_str(job, "jobDescription", "description", "desc", "snippet")
            )
            posted_date = _first_str(job, "appearDateDesc", "date")
            industry = _first_str(job, "coIndustryDesc", "industryDesc")
            experience = _first_str(job, "periodDesc", "experience")
            education = _first_str(job, "optionEdu", "education")

            raw_url = _extract_job_url(job)
            url = _normalize_job_url(raw_url, job_no=job_no)
            metadata = _compact_metadata(
                {
                    "company": company,
                    "location": location,
                    "salary": salary,
                    "keyword": search_spec.keyword,
                    "posted_date": posted_date,
                    "industry": industry,
                    "experience": experience,
                    "education": education,
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
                    external_id=f"job104-{job_no}",
                    content=description,
                    metadata=metadata,
                )
            )

        return items

    def _describe_non_json_response(self, response: requests.Response) -> str:
        content_type = ""
        if hasattr(response, "headers"):
            content_type = str(response.headers.get("Content-Type", "")).strip()
        body_preview = _normalize_text(response.text)[:120]
        if body_preview:
            return f"{content_type or 'unknown content type'}: {body_preview}"
        return content_type or "unknown response body"


def _extract_job_url(job: dict[str, Any]) -> str | None:
    direct_url = _first_str(job, "jobUrl", "url")
    if direct_url:
        return direct_url

    link = job.get("link")
    if isinstance(link, dict):
        return _first_str(link, "job", "url")
    return None


def _normalize_job_url(raw_url: str | None, *, job_no: str) -> str:
    if raw_url:
        if raw_url.startswith("//"):
            return f"https:{raw_url}"
        if raw_url.startswith("/"):
            return f"https://www.104.com.tw{raw_url}"
        if raw_url.startswith("http://") or raw_url.startswith("https://"):
            return raw_url
    return f"https://www.104.com.tw/job/{job_no}"


def _first_str(data: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


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
