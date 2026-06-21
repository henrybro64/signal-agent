from __future__ import annotations

from pathlib import Path

import pytest

from app.core import AppConfig, SecretError
from app.sources import (
    DummySource,
    LinkedInEmailSource,
    fetch_from_sources,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PATH = ROOT / "tests" / "fixtures" / "linkedin_alert_sample.eml"


def test_linkedin_email_source_from_config_reads_local_fixture_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LINKEDIN_EMAIL_SOURCE_PATH", str(FIXTURE_PATH))

    source = LinkedInEmailSource.from_config(_build_config())

    assert source.email_paths == [FIXTURE_PATH]
    assert source.keywords == ["python", "agent"]


def test_linkedin_email_source_fetch_normalizes_fixture_email() -> None:
    source = LinkedInEmailSource(
        keywords=["python", "agent"],
        locations=["Taiwan"],
        email_paths=[FIXTURE_PATH],
    )

    items = source.fetch()

    assert len(items) == 2
    first_item = items[0]
    second_item = items[1]
    assert first_item.source == "linkedin_email"
    assert first_item.item_type == "job"
    assert first_item.title == "Senior Python Backend Engineer"
    assert first_item.external_id == "linkedin-email-1234567890"
    assert (
        first_item.url
        == "https://www.linkedin.com/jobs/view/1234567890/?trackingId=abc"
    )
    assert first_item.content == "Build backend APIs and automation workflows."
    assert first_item.metadata["company"] == "Appier"
    assert first_item.metadata["location"] == "Taipei, Taiwan"
    assert (
        first_item.metadata["email_subject"]
        == "LinkedIn Job Alerts: 2 jobs in Taiwan"
    )
    assert "linkedin" in first_item.metadata["email_from"].casefold()
    assert second_item.title == "Staff AI Agent Engineer"
    assert second_item.external_id == "linkedin-email-9876543210"


def test_linkedin_email_source_dedups_jobs_across_multiple_emails(
    tmp_path: Path,
) -> None:
    second_fixture = tmp_path / "linkedin_alert_duplicate.eml"
    second_fixture.write_bytes(FIXTURE_PATH.read_bytes())
    source = LinkedInEmailSource(
        keywords=["python", "agent"],
        locations=["Taiwan"],
        email_paths=[FIXTURE_PATH, second_fixture],
    )

    items = source.fetch()

    assert len(items) == 2
    assert [item.external_id for item in items] == [
        "linkedin-email-1234567890",
        "linkedin-email-9876543210",
    ]


def test_linkedin_email_source_requires_local_path_or_imap_credentials() -> None:
    source = LinkedInEmailSource(
        keywords=["python"],
    )

    with pytest.raises(
        SecretError,
        match="LinkedIn email source requires LINKEDIN_EMAIL_SOURCE_PATH",
    ):
        source.fetch()


def test_linkedin_email_source_failure_can_be_skipped_in_source_pipeline() -> None:
    source = LinkedInEmailSource(
        keywords=["python"],
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
            "linkedin_email: LinkedIn email source requires "
            "LINKEDIN_EMAIL_SOURCE_PATH or IMAP credentials: "
            "LINKEDIN_EMAIL_IMAP_HOST, LINKEDIN_EMAIL_IMAP_USERNAME, "
            "and LINKEDIN_EMAIL_IMAP_PASSWORD"
        )
    ]


def _build_config() -> AppConfig:
    return AppConfig.from_dict(
        {
            "agent": {
                "timezone": "Asia/Taipei",
                "digest_time": "20:00",
            },
            "tasks": {
                "job_search": {
                    "enabled": True,
                    "sources": ["linkedin_email"],
                    "keywords": ["python", "agent"],
                    "locations": ["Taiwan"],
                },
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
    )
