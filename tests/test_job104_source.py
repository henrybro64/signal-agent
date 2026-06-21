from __future__ import annotations

import json
from pathlib import Path

import pytest
import requests

from app.core import SignalItem, load_config
from app.sources import DummySource, Job104Source, Job104SourceError, fetch_from_sources
from tests.source_fakes import FakeResponse, FakeSession

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PATH = ROOT / "tests" / "fixtures" / "job104_search_response.json"


def _load_fixture() -> dict[str, object]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))

def test_job104_source_from_config_reads_keywords_and_locations(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "\n".join(
            [
                "agent:",
                "  timezone: Asia/Taipei",
                '  digest_time: "20:00"',
                "tasks:",
                "  job_search:",
                "    enabled: true",
                "    sources:",
                "      - job104",
                "    keywords:",
                "      - python",
                "    locations:",
                "      - Taipei",
                "      - Remote",
                "  tw_stock_movers:",
                "    enabled: true",
                "    top_n: 20",
                "    timezone: Asia/Taipei",
                "notifiers:",
                "  telegram:",
                "    enabled: true",
                "scoring:",
                "  freshness_weight: 0.6",
                "  priority_weight: 0.4",
            ]
        ),
        encoding="utf-8",
    )

    config = load_config(config_path)
    source = Job104Source.from_config(config)

    assert source.keywords == ["python"]
    assert source.locations == ["Taipei", "Remote"]


def test_job104_source_fetch_normalizes_search_results() -> None:
    session = FakeSession([FakeResponse(json_payload=_load_fixture())])
    source = Job104Source(
        keywords=["python"],
        session=session,
        request_delay_seconds=0.0,
    )

    items = source.fetch()

    assert len(items) == 2
    first_item = items[0]
    second_item = items[1]
    assert all(isinstance(item, SignalItem) for item in items)
    assert first_item.source == "job104"
    assert first_item.item_type == "job"
    assert first_item.title == "AD10601 Senior Python Backend Engineer"
    assert first_item.external_id == "job104-84ph6"
    assert first_item.url == "https://www.104.com.tw/job/84ph6?jobsource=cs_2018indexpoc"
    assert first_item.content == (
        "Design and implement scalable backend services and high-concurrency "
        "APIs using Python."
    )
    assert first_item.metadata["company"] == "華碩電腦股份有限公司"
    assert first_item.metadata["location"] == "台北市北投區"
    assert first_item.metadata["salary"] == "待遇面議"
    assert first_item.metadata["keyword"] == "python"
    assert first_item.metadata["posted_date"] == "12/16"
    assert first_item.metadata["industry"] == "電腦及其週邊設備製造業"
    assert second_item.url == "https://www.104.com.tw/job/8abcd?jobsource=index_s"


def test_job104_source_rate_limits_between_keywords() -> None:
    session = FakeSession(
        [
            FakeResponse(json_payload=_load_fixture()),
            FakeResponse(json_payload={"data": {"list": []}}),
        ]
    )
    sleeps: list[float] = []
    source = Job104Source(
        keywords=["python", "backend"],
        session=session,
        request_delay_seconds=0.25,
        sleeper=sleeps.append,
    )

    items = source.fetch()

    assert len(items) == 2
    assert len(session.calls) == 2
    assert session.calls[0]["params"]["keyword"] == "python"
    assert session.calls[1]["params"]["keyword"] == "backend"
    assert sleeps == [0.25]


def test_job104_source_retries_transient_errors() -> None:
    session = FakeSession(
        [
            FakeResponse(status_code=502, text="bad gateway"),
            FakeResponse(json_payload=_load_fixture()),
        ]
    )
    sleeps: list[float] = []
    source = Job104Source(
        keywords=["python"],
        session=session,
        request_delay_seconds=1.0,
        sleeper=sleeps.append,
    )

    items = source.fetch()

    assert len(items) == 2
    assert len(session.calls) == 2
    assert sleeps == [1.0]


def test_job104_source_failure_can_be_skipped_in_source_pipeline() -> None:
    session = FakeSession([requests.RequestException("network down")])
    source = Job104Source(
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
    assert errors == ["job104: 104 search request failed for 'python': network down"]


def test_job104_source_surfaces_non_json_responses_cleanly() -> None:
    session = FakeSession(
        [
            FakeResponse(
                json_payload=None,
                text="<!DOCTYPE html><title>Just a moment...</title>",
                headers={"Content-Type": "text/html"},
            )
        ]
    )
    source = Job104Source(
        keywords=["python"],
        session=session,
        max_retries=0,
    )

    with pytest.raises(Job104SourceError, match="non-JSON content"):
        source.fetch()


def test_job104_items_dedup_by_url_and_external_id(tmp_path: Path) -> None:
    from app.core import init_db, upsert_signal

    session = FakeSession([FakeResponse(json_payload=_load_fixture())])
    source = Job104Source(
        keywords=["python"],
        session=session,
        request_delay_seconds=0.0,
    )
    db_path = tmp_path / "signals.sqlite3"
    init_db(db_path)
    items = source.fetch()

    first = upsert_signal(items[0], db_path=db_path)
    second = upsert_signal(items[0], db_path=db_path)

    assert first.action == "inserted"
    assert second.action == "updated"
    assert first.signal_id == second.signal_id
