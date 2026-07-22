from __future__ import annotations

from pathlib import Path

import pytest
import requests

from app.core import AppConfig, SignalItem
from app.sources import (
    DummySource,
    GlassdoorSource,
    GlassdoorSourceError,
    fetch_from_sources,
)
from tests.source_fakes import FakeResponse, FakeSession

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PATH = ROOT / "tests" / "fixtures" / "glassdoor_search_taiwan.html"


def _load_fixture() -> str:
    return FIXTURE_PATH.read_text(encoding="utf-8")


def test_glassdoor_source_from_config_defaults_to_taiwan_region() -> None:
    source = GlassdoorSource.from_config(_build_config())

    assert source.keywords == ["python"]
    assert source.locations == ["Taiwan"]
    assert source.max_retries == 2
    assert source.page_limit == 1


def test_glassdoor_source_fetch_normalizes_search_results() -> None:
    session = FakeSession([FakeResponse(text=_load_fixture())])
    source = GlassdoorSource(
        keywords=["python"],
        session=session,
        request_delay_seconds=0.0,
    )

    items = source.fetch()

    assert len(items) == 2
    first_item = items[0]
    second_item = items[1]
    assert all(isinstance(item, SignalItem) for item in items)
    assert first_item.source == "glassdoor"
    assert first_item.item_type == "job"
    assert first_item.title == "Senior Python Data Engineer"
    assert first_item.external_id == "glassdoor-1009123456"
    assert (
        first_item.url
        == "https://www.glassdoor.com/partner/jobListing.htm?jobListingId=1009123456&pos=101"
    )
    assert first_item.content == (
        "Build data products and Python services for internal platforms."
    )
    assert first_item.metadata["company"] == "ASUS"
    assert first_item.metadata["location"] == "Taipei, Taiwan"
    assert first_item.metadata["salary"] == "NT$ 1.3M - NT$ 1.8M"
    assert first_item.metadata["keyword"] == "python"
    assert first_item.metadata["posted_date"] == "3d"
    assert (
        second_item.url
        == "https://www.glassdoor.com/partner/jobListing.htm?jobListingId=1009765432"
    )


def test_glassdoor_source_rate_limits_between_keywords() -> None:
    session = FakeSession(
        [
            FakeResponse(text=_load_fixture()),
            FakeResponse(text="<html><body></body></html>"),
        ]
    )
    sleeps: list[float] = []
    source = GlassdoorSource(
        keywords=["python", "backend"],
        session=session,
        request_delay_seconds=0.25,
        sleeper=sleeps.append,
    )

    items = source.fetch()

    assert len(items) == 2
    assert len(session.calls) == 2
    assert session.calls[0]["params"]["sc.keyword"] == "python"
    assert session.calls[1]["params"]["sc.keyword"] == "backend"
    assert session.calls[0]["params"]["locKeyword"] == "Taiwan"
    assert session.calls[0]["params"]["locId"] == "238"
    assert sleeps == [0.25]


def test_glassdoor_source_retries_transient_errors() -> None:
    session = FakeSession(
        [
            FakeResponse(status_code=502, text="bad gateway"),
            FakeResponse(text=_load_fixture()),
        ]
    )
    sleeps: list[float] = []
    source = GlassdoorSource(
        keywords=["python"],
        session=session,
        request_delay_seconds=1.0,
        sleeper=sleeps.append,
    )

    items = source.fetch()

    assert len(items) == 2
    assert len(session.calls) == 2
    assert sleeps == [1.0]


def test_glassdoor_source_failure_can_be_skipped_in_source_pipeline() -> None:
    session = FakeSession([requests.RequestException("network down")])
    source = GlassdoorSource(
        keywords=["python"],
        session=session,
        max_retries=0,
        request_delay_seconds=0.0,
    )
    errors: list[str] = []

    def on_error(source_name: object, exc: Exception) -> None:
        errors.append(f"{getattr(source_name, 'name', 'unknown')}: {exc}")

    items = fetch_from_sources(
        [DummySource(), source],
        on_error=on_error,
    )

    assert len(items) == 2
    assert errors == [
        (
            "glassdoor: Glassdoor search request failed for "
            "'python' in 'Taiwan': network down"
        )
    ]


def test_glassdoor_source_surfaces_bot_challenges_cleanly() -> None:
    session = FakeSession(
        [
            FakeResponse(
                text="<!DOCTYPE html><title>Just a moment...</title>",
                headers={"Content-Type": "text/html"},
            )
        ]
    )
    source = GlassdoorSource(
        keywords=["python"],
        session=session,
        max_retries=0,
    )

    with pytest.raises(GlassdoorSourceError, match="bot challenge"):
        source.fetch()


def _build_config(
    *,
    locations: list[str] | None = None,
    sources: dict[str, object] | None = None,
) -> AppConfig:
    job_search: dict[str, object] = {
        "enabled": True,
        "sources": ["glassdoor"],
        "keywords": ["python"],
    }
    if locations is not None:
        job_search["locations"] = locations

    payload: dict[str, object] = {
        "agent": {
            "timezone": "Asia/Taipei",
            "digest_time": "20:00",
        },
        "tasks": {
            "job_search": job_search,
            "tw_stock_movers": {
                "enabled": True,
                "top_n": 20,
                "timezone": "Asia/Taipei",
            },
        },
        "notifiers": {
            "telegram": {
                "enabled": True,
            }
        },
        "scoring": {
            "freshness_weight": 0.6,
            "priority_weight": 0.4,
        },
    }
    if sources is not None:
        payload["sources"] = sources
    return AppConfig.from_dict(payload)
