from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.core.config import AppConfig
from app.core.error_handling import (
    build_task_failure_message,
    notify_task_failure,
    summarize_error,
)
from app.core.secrets import EnvSettings


def test_build_task_failure_message_formats_time_and_redacts_secrets() -> None:
    env = EnvSettings(
        openai_api_key="sk-secret",
        telegram_bot_token="token-123",
        telegram_chat_id="chat-456",
        signal_agent_env="test",
        linkedin_email_imap_password="imap-secret",
    )

    message = build_task_failure_message(
        task_name="tw_stock_movers",
        error=RuntimeError(
            "Failed to fetch token-123 with sk-secret and imap-secret"
        ),
        env=env,
        timezone_name="Asia/Taipei",
        occurred_at=datetime(2026, 6, 12, 2, 10, tzinfo=UTC),
    )

    assert message.splitlines() == [
        "⚠️ signal-agent task failed",
        "Task: tw_stock_movers",
        "Time: 2026-06-12 10:10 Asia/Taipei",
        "Error: Failed to fetch *** with *** and ***",
    ]


def test_summarize_error_uses_first_line_and_truncates() -> None:
    summary = summarize_error(
        ValueError(("x" * 220) + "\nsecond line")
    )

    assert summary.startswith("x")
    assert "second line" not in summary
    assert summary.endswith("...")
    assert len(summary) == 200


def test_summarize_error_strips_response_preview_details() -> None:
    summary = summarize_error(
        ValueError(
            "All configured job-search sources failed: "
            "indeed: Indeed search failed for 'python' in 'Taiwan' "
            "with status 403: text/html: Access denied by edge policy; "
            "job104: 104 search failed for 'python' with status 403"
        )
    )

    assert summary == (
        "All configured job-search sources failed: indeed: "
        "Indeed search failed for 'python' in 'Taiwan' with status 403"
    )


def test_notify_task_failure_sends_message_when_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent_messages: list[str] = []

    class FakeNotifier:
        def send_message(self, text: str) -> None:
            sent_messages.append(text)

    monkeypatch.setattr(
        "app.core.error_handling.load_config",
        lambda path: AppConfig.from_dict(
            {
                "agent": {"timezone": "Asia/Taipei", "digest_time": "20:00"},
                "tasks": {
                    "job_search": {
                        "enabled": True,
                        "sources": ["job104"],
                        "keywords": ["python"],
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
                        "failure_alerts_enabled": True,
                    }
                },
                "scoring": {
                    "freshness_weight": 0.6,
                    "priority_weight": 0.4,
                },
            }
        ),
    )
    monkeypatch.setattr(
        "app.core.error_handling.load_env_settings",
        lambda: EnvSettings(
            telegram_bot_token="token-123",
            telegram_chat_id="chat-456",
            signal_agent_env="test",
        ),
    )
    monkeypatch.setattr(
        "app.core.error_handling.build_telegram_notifier",
        lambda *, env: FakeNotifier(),
    )

    outcome = notify_task_failure(
        task_name="job_search",
        config_path="config.yaml",
        error=ValueError("TWSE timeout for token-123"),
        occurred_at=datetime(2026, 6, 12, 2, 10, tzinfo=UTC),
    )

    assert outcome.status == "sent"
    assert outcome.error_summary == "TWSE timeout for ***"
    assert sent_messages == [
        "\n".join(
            [
                "⚠️ signal-agent task failed",
                "Task: job_search",
                "Time: 2026-06-12 10:10 Asia/Taipei",
                "Error: TWSE timeout for ***",
            ]
        )
    ]


def test_notify_task_failure_skips_when_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.core.error_handling.load_config",
        lambda path: AppConfig.from_dict(
            {
                "agent": {"timezone": "Asia/Taipei", "digest_time": "20:00"},
                "tasks": {
                    "job_search": {
                        "enabled": True,
                        "sources": ["job104"],
                        "keywords": ["python"],
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
                        "failure_alerts_enabled": False,
                    }
                },
                "scoring": {
                    "freshness_weight": 0.6,
                    "priority_weight": 0.4,
                },
            }
        ),
    )
    monkeypatch.setattr(
        "app.core.error_handling.build_telegram_notifier",
        lambda *, env: pytest.fail("notifier should not be built"),
    )

    outcome = notify_task_failure(
        task_name="job_search",
        config_path="config.yaml",
        error=ValueError("TWSE timeout"),
    )

    assert outcome.status == "skipped_disabled"


def test_notify_task_failure_uses_raw_config_when_validation_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "\n".join(
            [
                "tasks:",
                "  job_search:",
                "    enabled: true",
                "    sources:",
                "      - job104",
                "    keywords:",
                "      - python",
                "  tw_stock_movers:",
                "    enabled: true",
                "    top_n: 20",
                "    timezone: Asia/Taipei",
                "notifiers:",
                "  telegram:",
                "    enabled: true",
                "    failure_alerts_enabled: false",
                "scoring:",
                "  freshness_weight: 0.6",
                "  priority_weight: 0.4",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "app.core.error_handling.build_telegram_notifier",
        lambda *, env: pytest.fail("notifier should not be built"),
    )

    outcome = notify_task_failure(
        task_name="job_search",
        config_path=str(config_path),
        error=ValueError("Missing required config value 'agent.timezone'"),
    )

    assert outcome.status == "skipped_disabled"


def test_notify_task_failure_sends_when_config_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent_messages: list[str] = []

    class FakeNotifier:
        def send_message(self, text: str) -> None:
            sent_messages.append(text)

    monkeypatch.setattr(
        "app.core.error_handling.load_env_settings",
        lambda: EnvSettings(
            telegram_bot_token="token-123",
            telegram_chat_id="chat-456",
            signal_agent_env="test",
        ),
    )
    monkeypatch.setattr(
        "app.core.error_handling.build_telegram_notifier",
        lambda *, env: FakeNotifier(),
    )

    outcome = notify_task_failure(
        task_name="job_search",
        config_path="missing.yaml",
        error=FileNotFoundError("Config file not found: missing.yaml"),
        occurred_at=datetime(2026, 6, 12, 2, 10, tzinfo=UTC),
    )

    assert outcome.status == "sent"
    assert sent_messages == [
        "\n".join(
            [
                "⚠️ signal-agent task failed",
                "Task: job_search",
                "Time: 2026-06-12 02:10 UTC",
                "Error: Config file not found: missing.yaml",
            ]
        )
    ]
