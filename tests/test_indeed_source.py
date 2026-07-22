from __future__ import annotations

from pathlib import Path

import pytest
import requests

from app.core import AppConfig, SignalItem
from app.sources import DummySource, IndeedSource, IndeedSourceError, fetch_from_sources
from tests.source_fakes import FakeResponse, FakeSession

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PATH = ROOT / "tests" / "fixtures" / "indeed_search_taiwan.html"


def _load_fixture() -> str:
    return FIXTURE_PATH.read_text(encoding="utf-8")


def test_indeed_source_from_config_defaults_to_taiwan_region() -> None:
    source = IndeedSource.from_config(_build_config())

    assert source.keywords == ["python"]
    assert source.locations == ["Taiwan"]
    assert source.max_retries == 2
    assert source.page_limit == 1


def test_indeed_source_fetch_normalizes_search_results() -> None:
    session = FakeSession([FakeResponse(text=_load_fixture())])
    source = IndeedSource(
        keywords=["python"],
        session=session,
        request_delay_seconds=0.0,
    )

    items = source.fetch()

    assert len(items) == 2
    first_item = items[0]
    second_item = items[1]
    assert all(isinstance(item, SignalItem) for item in items)
    assert first_item.source == "indeed"
    assert first_item.item_type == "job"
    assert first_item.title == "Senior Python Platform Engineer"
    assert first_item.external_id == "indeed-indeed123"
    assert first_item.url == "https://tw.indeed.com/viewjob?jk=indeed123&from=serp"
    assert first_item.content == (
        "Build internal Python platforms and automation workflows."
    )
    assert first_item.metadata["company"] == "Trend Micro"
    assert first_item.metadata["location"] == "Taipei, Taiwan"
    assert first_item.metadata["salary"] == "NT$1,400,000 - NT$1,900,000 a year"
    assert first_item.metadata["keyword"] == "python"
    assert first_item.metadata["posted_date"] == "Posted 2 days ago"
    assert second_item.url == "https://tw.indeed.com/viewjob?jk=indeed456"


def test_indeed_source_rate_limits_between_locations() -> None:
    session = FakeSession(
        [
            FakeResponse(text=_load_fixture()),
            FakeResponse(text="<html><body></body></html>"),
        ]
    )
    sleeps: list[float] = []
    source = IndeedSource(
        keywords=["python"],
        locations=["Taiwan", "Remote"],
        session=session,
        request_delay_seconds=0.25,
        sleeper=sleeps.append,
    )

    items = source.fetch()

    assert len(items) == 2
    assert len(session.calls) == 2
    assert session.calls[0]["params"]["l"] == "Taiwan"
    assert session.calls[1]["params"]["l"] == "Remote"
    assert sleeps == [0.25]


def test_indeed_source_retries_transient_errors() -> None:
    session = FakeSession(
        [
            FakeResponse(status_code=429, text="rate limited"),
            FakeResponse(text=_load_fixture()),
        ]
    )
    sleeps: list[float] = []
    source = IndeedSource(
        keywords=["python"],
        session=session,
        request_delay_seconds=1.0,
        sleeper=sleeps.append,
    )

    items = source.fetch()

    assert len(items) == 2
    assert len(session.calls) == 2
    assert sleeps == [1.0]


def test_indeed_source_failure_can_be_skipped_in_source_pipeline() -> None:
    session = FakeSession([requests.RequestException("network down")])
    source = IndeedSource(
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
        "indeed: Indeed search request failed for 'python' in 'Taiwan': network down"
    ]


def test_indeed_source_surfaces_bot_challenges_cleanly() -> None:
    session = FakeSession(
        [
            FakeResponse(
                text="<!DOCTYPE html><title>Just a moment...</title>",
                headers={"Content-Type": "text/html"},
            )
        ]
    )
    source = IndeedSource(
        keywords=["python"],
        session=session,
        max_retries=0,
    )

    with pytest.raises(IndeedSourceError, match="bot challenge"):
        source.fetch()


def _build_config(
    *,
    locations: list[str] | None = None,
    sources: dict[str, object] | None = None,
) -> AppConfig:
    job_search: dict[str, object] = {
        "enabled": True,
        "sources": ["indeed"],
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
