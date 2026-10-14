from __future__ import annotations

import imaplib
import re
from email.parser import BytesParser
from email.policy import default as email_policy
from pathlib import Path
from typing import Self

import pytest

from app.core import AppConfig, SecretError
from app.sources import (
    DummySource,
    LinkedInEmailSource,
    fetch_from_sources,
)
from app.sources import linkedin_email as linkedin_email_module

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PATH = ROOT / "tests" / "fixtures" / "linkedin_alert_sample.eml"


def test_linkedin_email_source_from_config_reads_local_fixture_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LINKEDIN_EMAIL_SOURCE_PATH", str(FIXTURE_PATH))

    source = LinkedInEmailSource.from_config(_build_config())

    assert source.email_paths == [FIXTURE_PATH]
    assert source.keywords == ["python", "agent"]
    assert source.lookback_days == 14
    assert source.imap_mailbox == "INBOX"


def test_linkedin_email_source_from_config_applies_sources_runtime_settings() -> None:
    source = LinkedInEmailSource.from_config(
        _build_config(
            sources={
                "linkedin_email": {
                    "lookback_days": 7,
                    "imap_mailbox": "Alerts",
                }
            }
        )
    )

    assert source.lookback_days == 7
    assert source.imap_mailbox == "Alerts"


def test_linkedin_email_source_from_config_falls_back_to_env_mailbox(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LINKEDIN_EMAIL_IMAP_MAILBOX", "EnvMailbox")

    source = LinkedInEmailSource.from_config(_build_config())

    assert source.imap_mailbox == "EnvMailbox"


def test_linkedin_email_source_from_config_prefers_config_mailbox_over_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LINKEDIN_EMAIL_IMAP_MAILBOX", "EnvMailbox")

    source = LinkedInEmailSource.from_config(
        _build_config(
            sources={
                "linkedin_email": {
                    "imap_mailbox": "ConfigMailbox",
                }
            }
        )
    )

    assert source.imap_mailbox == "ConfigMailbox"


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
        first_item.metadata["email_subject"] == "LinkedIn Job Alerts: 2 jobs in Taiwan"
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


def test_linkedin_email_source_filters_by_keywords_and_locations() -> None:
    source = LinkedInEmailSource(
        keywords=["nonexistent-keyword"],
        locations=["Japan"],
        email_paths=[FIXTURE_PATH],
    )

    assert source.fetch() == []


def test_linkedin_email_source_parses_plain_text_only_email(tmp_path: Path) -> None:
    email_path = tmp_path / "plain_text_only.eml"
    email_path.write_text(
        "\n".join(
            [
                "From: LinkedIn Jobs <jobs-noreply@linkedin.com>",
                "Subject: LinkedIn Job Alerts: 1 job in Taiwan",
                "Date: Sun, 21 Jun 2026 09:15:00 +0800",
                "MIME-Version: 1.0",
                "Content-Type: text/plain; charset=utf-8",
                "",
                "Job Title: Senior Python Backend Engineer",
                "Company: Appier",
                "Location: Taipei, Taiwan",
                "Snippet: Build backend APIs and automation workflows.",
                "Apply: https://www.linkedin.com/jobs/view/1234567890/?trackingId=abc",
            ]
        ),
        encoding="utf-8",
    )
    source = LinkedInEmailSource(
        keywords=["python"],
        email_paths=[email_path],
    )

    items = source.fetch()

    assert len(items) == 1
    assert items[0].title == "Senior Python Backend Engineer"
    assert items[0].external_id == "linkedin-email-1234567890"


def test_linkedin_email_source_parses_html_only_email(tmp_path: Path) -> None:
    email_path = tmp_path / "html_only.eml"
    email_path.write_text(
        "\n".join(
            [
                "From: LinkedIn Jobs <jobs-noreply@linkedin.com>",
                "Subject: LinkedIn Job Alerts: 1 job in Taiwan",
                "Date: Sun, 21 Jun 2026 09:15:00 +0800",
                "MIME-Version: 1.0",
                "Content-Type: text/html; charset=utf-8",
                "",
                "<html><body>",
                '<a href="https://www.linkedin.com/jobs/view/1234567890/">',
                "Senior Python Backend Engineer",
                "</a>",
                "<div>Appier</div>",
                "<div>Taipei, Taiwan</div>",
                "<div>Build backend APIs and automation workflows.</div>",
                "</body></html>",
            ]
        ),
        encoding="utf-8",
    )
    source = LinkedInEmailSource(
        keywords=["python"],
        email_paths=[email_path],
    )

    items = source.fetch()

    assert len(items) == 1
    assert items[0].title == "Senior Python Backend Engineer"
    assert items[0].metadata["company"] == "Appier"


def test_linkedin_email_source_fetch_from_imap_filters_job_alerts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    linkedin_message = FIXTURE_PATH.read_bytes()
    other_message = (
        b"From: Other Sender <other@example.com>\r\n"
        b"Subject: Weekly newsletter\r\n"
        b"Date: Sun, 21 Jun 2026 09:15:00 +0800\r\n"
        b"\r\n"
        b"Not a LinkedIn job alert.\r\n"
    )

    created: dict[str, object] = {}

    class FakeIMAPClient:
        def __init__(self, host: str, *, timeout: float | None = None) -> None:
            created["host"] = host
            created["timeout"] = timeout
            self.selected_mailbox: str | None = None
            self.search_criteria: object | None = None

        def __enter__(self) -> Self:
            return self

        @staticmethod
        def __exit__(*_args: object) -> bool:
            return False

        @staticmethod
        def login(username: str, password: str) -> tuple[str, list[bytes]]:
            assert username == "user@example.com"
            assert password == "app-password"
            return ("OK", [b"Logged in"])

        def select(self, mailbox: str) -> tuple[str, list[bytes]]:
            self.selected_mailbox = mailbox
            return ("OK", [b"2"])

        def search(
            self,
            charset: object,
            *criteria: object,
        ) -> tuple[str, list[bytes | None]]:
            assert charset is None
            self.search_criteria = criteria[0] if len(criteria) == 1 else criteria
            created["search"] = self.search_criteria
            inbox = {b"1": linkedin_message, b"2": other_message}
            blob = " ".join(str(part) for part in criteria)
            senders = re.findall(r'FROM "([^"]+)"', blob)
            matched: list[bytes] = []
            for message_id, raw in inbox.items():
                parsed = BytesParser(policy=email_policy).parsebytes(raw)
                from_header = str(parsed.get("From") or "")
                if senders and not any(
                    sender.casefold() in from_header.casefold() for sender in senders
                ):
                    continue
                matched.append(message_id)
            created["search_ids"] = matched
            return ("OK", [b" ".join(matched)])

        def fetch(
            self,
            message_id: str | bytes,
            query: str,
        ) -> tuple[str, list[tuple[bytes, bytes] | bytes | None]]:
            fetched = created.setdefault("fetched", [])
            assert isinstance(fetched, list)
            fetched.append(message_id)
            if message_id in (b"1", "1"):
                return ("OK", [(b"1 (RFC822)", linkedin_message)])
            if message_id in (b"2", "2"):
                return ("OK", [(b"2 (RFC822)", other_message)])
            raise AssertionError(f"unexpected message id: {message_id!r}")

    monkeypatch.setattr(
        linkedin_email_module.imaplib,
        "IMAP4_SSL",
        FakeIMAPClient,
    )

    source = LinkedInEmailSource(
        keywords=["python", "agent"],
        locations=["Taiwan"],
        imap_host="imap.gmail.com",
        imap_username="user@example.com",
        imap_password="app-password",
        imap_mailbox="INBOX",
        lookback_days=14,
        timeout_seconds=12.5,
    )

    items = source.fetch()

    assert len(items) == 2
    assert items[0].source == "linkedin_email"
    assert items[0].external_id == "linkedin-email-1234567890"
    assert created["timeout"] == 12.5
    search = str(created["search"])
    assert search.startswith("(") and search.endswith(")")
    assert "SINCE" in search
    assert "jobs-noreply@linkedin.com" in search
    assert "jobalerts-noreply@linkedin.com" in search
    assert created["search_ids"] == [b"1"]
    assert created["fetched"] == ["1"]


def test_linkedin_email_imap_mailbox_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeIMAPClient:
        def __init__(self, host: str, *, timeout: float | None = None) -> None:
            self.host = host
            self.timeout = timeout

        def __enter__(self) -> Self:
            return self

        @staticmethod
        def __exit__(*_args: object) -> bool:
            return False

        @staticmethod
        def login(username: str, password: str) -> tuple[str, list[bytes]]:
            return ("OK", [b"Logged in"])

        @staticmethod
        def select(mailbox: str) -> tuple[str, list[bytes]]:
            return ("NO", [b"Mailbox does not exist"])

    monkeypatch.setattr(
        linkedin_email_module.imaplib,
        "IMAP4_SSL",
        FakeIMAPClient,
    )
    source = LinkedInEmailSource(
        keywords=["python"],
        imap_host="imap.gmail.com",
        imap_username="user@example.com",
        imap_password="app-password",
        imap_mailbox="MissingBox",
    )
    with pytest.raises(
        linkedin_email_module.LinkedInEmailSourceError,
        match="mailbox not found",
    ):
        source.fetch()


def test_linkedin_email_imap_login_error_is_labeled_login(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeIMAPClient:
        def __init__(self, host: str, *, timeout: float | None = None) -> None:
            self.host = host
            self.timeout = timeout

        def __enter__(self) -> Self:
            return self

        @staticmethod
        def __exit__(*_args: object) -> bool:
            return False

        @staticmethod
        def login(username: str, password: str) -> tuple[str, list[bytes]]:
            raise imaplib.IMAP4.error("AUTHENTICATIONFAILED")

        @staticmethod
        def select(mailbox: str) -> tuple[str, list[bytes]]:
            raise AssertionError("select must not run after login failure")

    monkeypatch.setattr(
        linkedin_email_module.imaplib,
        "IMAP4_SSL",
        FakeIMAPClient,
    )
    source = LinkedInEmailSource(
        keywords=["python"],
        imap_host="imap.gmail.com",
        imap_username="user@example.com",
        imap_password="wrong-password",
        imap_mailbox="INBOX",
    )
    with pytest.raises(
        linkedin_email_module.LinkedInEmailSourceError,
        match="IMAP login failed",
    ):
        source.fetch()


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


def _build_config(*, sources: dict[str, object] | None = None) -> AppConfig:
    payload: dict[str, object] = {
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
    if sources is not None:
        payload["sources"] = sources
    return AppConfig.from_dict(payload)
