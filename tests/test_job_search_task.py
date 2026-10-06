from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.core import SecretError, SignalItem, init_db, upsert_signal
from app.tasks import JobSearchTask
from app.tasks import job_search as job_search_task_module

OBSERVED_AT = datetime(2026, 6, 16, 9, 0, tzinfo=UTC)


class StaticJobSource:
    name = "static_jobs"

    def __init__(self, items: list[SignalItem]) -> None:
        self._items = items

    def fetch(self) -> list[SignalItem]:
        return list(self._items)


class FakeTelegramNotifier:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def send_message(self, text: str) -> None:
        self.messages.append(text)


def test_job_search_task_runs_end_to_end_and_sends_digest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config_path = _write_config(tmp_path, source_names=["static_jobs"])
    db_path = tmp_path / "signals.sqlite3"
    notifier = FakeTelegramNotifier()
    monkeypatch.setattr(job_search_task_module, "DEFAULT_DB_PATH", db_path)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token-123")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "chat-456")
    monkeypatch.setitem(
        job_search_task_module.build_job_search_sources_for_profile.__globals__["SOURCE_FACTORIES"],
        "static_jobs",
        lambda _config: StaticJobSource(_sample_jobs()),
    )
    monkeypatch.setattr(
        job_search_task_module,
        "build_telegram_notifier",
        lambda *, env: notifier,
    )

    JobSearchTask(str(config_path)).run()

    captured = capsys.readouterr()
    assert "job_search task executed using" in captured.out
    assert "persisted 3 new / 0 duplicate" in captured.out
    assert "classified 1 strong_match / 1 maybe / 1 reject" in captured.out
    assert "telegram digest sent" in captured.out
    structured_logs = _parse_structured_logs(captured.out)
    notification_log = next(
        log for log in structured_logs if log["event"] == "notification_sent"
    )
    task_log = next(
        log for log in structured_logs if log["event"] == "task_completed"
    )
    assert notification_log["task_name"] == "job_search"
    assert notification_log["notification_status"] == "sent"
    assert notification_log["notified_count"] == 3
    assert task_log["notification_status"] == "sent"
    assert task_log["new_count"] == 3
    assert task_log["duplicate_count"] == 0
    assert task_log["notified_count"] == 3
    assert len(notifier.messages) == 1

    message = notifier.messages[0]
    assert "signal-agent job search digest" in message
    counts = (
        "counts: fetched=3, new=3, duplicates=0, "
        "strong=1, maybe=1, rejected=1"
    )
    assert counts in message
    assert "Top recommended jobs" in message
    assert "Cloud SDET" in message
    assert "Maybe jobs" in message
    assert "Test Automation Engineer" in message
    assert "Rejected jobs summary" in message
    assert "Helpdesk Support Engineer" in message
    assert (
        "run summary: status=success, fetched=3, new=3, "
        "skipped=0, notified=3"
    ) in message

    rows = _fetch_signal_rows(db_path)
    assert len(rows) == 3
    assert rows["Cloud SDET"]["score"] == 100.0
    assert rows["Cloud SDET"]["status"] == "reviewed"
    assert rows["Test Automation Engineer"]["score"] == 30.0
    assert rows["Test Automation Engineer"]["status"] == "reviewed"
    assert rows["Helpdesk Support Engineer"]["score"] == 0.0
    assert rows["Helpdesk Support Engineer"]["status"] == "reviewed"
    assert _task_run_statuses(db_path) == ["success"]


def test_job_search_task_skips_duplicates_for_telegram_digest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config_path = _write_config(tmp_path, source_names=["static_jobs"])
    db_path = tmp_path / "signals.sqlite3"
    notifier = FakeTelegramNotifier()
    monkeypatch.setattr(job_search_task_module, "DEFAULT_DB_PATH", db_path)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token-123")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "chat-456")
    monkeypatch.setitem(
        job_search_task_module.build_job_search_sources_for_profile.__globals__["SOURCE_FACTORIES"],
        "static_jobs",
        lambda _config: StaticJobSource(_sample_jobs()),
    )
    monkeypatch.setattr(
        job_search_task_module,
        "build_telegram_notifier",
        lambda *, env: notifier,
    )

    JobSearchTask(str(config_path)).run()
    capsys.readouterr()
    assert len(notifier.messages) == 1

    JobSearchTask(str(config_path)).run()

    captured = capsys.readouterr()
    assert "persisted 0 new / 3 duplicate" in captured.out
    assert "telegram digest skipped: no new jobs to report" in captured.out
    assert len(notifier.messages) == 1
    assert _count_rows(db_path, "signals") == 3
    assert _signal_run_statuses(db_path) == ["completed", "completed"]
    assert _task_run_statuses(db_path) == ["success", "success"]


def test_job_search_task_drains_capped_pending_digest_without_refetch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    strong_content = (
        "Remote role building Python and Pytest automation for Azure, AWS, "
        "and Kubernetes workloads with SIEM, SOAR, DLP, and API security."
    )
    jobs = [
        SignalItem(
            source="static_jobs",
            item_type="job",
            title="Cloud SDET",
            observed_at=OBSERVED_AT,
            url="https://example.com/jobs/cloud-sdet",
            content=strong_content,
            metadata={
                "company": "Example Security Product Company",
                "location": "Taipei",
                "work_style": "Hybrid",
            },
        ),
        SignalItem(
            source="static_jobs",
            item_type="job",
            title="Platform SDET",
            observed_at=OBSERVED_AT,
            url="https://example.com/jobs/platform-sdet",
            content=strong_content,
            metadata={
                "company": "Example Platform Company",
                "location": "Taipei",
                "work_style": "Hybrid",
            },
        ),
    ]
    config_path = _write_config(
        tmp_path,
        source_names=["static_jobs"],
        digest_max_strong_matches=1,
        include_rejected_summary=False,
    )
    db_path = tmp_path / "signals.sqlite3"
    notifier = FakeTelegramNotifier()
    monkeypatch.setattr(job_search_task_module, "DEFAULT_DB_PATH", db_path)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token-123")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "chat-456")
    monkeypatch.setitem(
        job_search_task_module.build_job_search_sources_for_profile.__globals__[
            "SOURCE_FACTORIES"
        ],
        "static_jobs",
        lambda _config: StaticJobSource(jobs),
    )
    monkeypatch.setattr(
        job_search_task_module,
        "build_telegram_notifier",
        lambda *, env: notifier,
    )

    JobSearchTask(str(config_path)).run()
    capsys.readouterr()
    assert len(notifier.messages) == 1
    first_message = notifier.messages[0]
    rows = _fetch_signal_rows(db_path)
    assert rows["Cloud SDET"]["score"] >= 60
    assert rows["Platform SDET"]["score"] >= 60
    reviewed = [
        title for title, row in rows.items() if row["status"] == "reviewed"
    ]
    pending = [title for title, row in rows.items() if row["status"] == "new"]
    assert len(reviewed) == 1
    assert len(pending) == 1
    assert reviewed[0] in first_message
    assert pending[0] not in first_message

    monkeypatch.setitem(
        job_search_task_module.build_job_search_sources_for_profile.__globals__[
            "SOURCE_FACTORIES"
        ],
        "static_jobs",
        lambda _config: StaticJobSource([]),
    )
    JobSearchTask(str(config_path)).run()

    captured = capsys.readouterr()
    assert "telegram digest sent" in captured.out
    assert len(notifier.messages) == 2
    assert pending[0] in notifier.messages[1]
    rows = _fetch_signal_rows(db_path)
    assert rows[pending[0]]["status"] == "reviewed"


def test_job_search_task_reports_useful_failures_even_without_new_jobs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = _write_config(
        tmp_path,
        source_names=["failing_source", "static_jobs"],
    )
    db_path = tmp_path / "signals.sqlite3"
    notifier = FakeTelegramNotifier()
    monkeypatch.setattr(job_search_task_module, "DEFAULT_DB_PATH", db_path)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token-123")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "chat-456")

    class FailingSource:
        name = "failing_source"

        def fetch(self) -> list[SignalItem]:
            raise RuntimeError("cloudflare challenge")

    monkeypatch.setitem(
        job_search_task_module.build_job_search_sources_for_profile.__globals__["SOURCE_FACTORIES"],
        "failing_source",
        lambda _config: FailingSource(),
    )
    monkeypatch.setitem(
        job_search_task_module.build_job_search_sources_for_profile.__globals__["SOURCE_FACTORIES"],
        "static_jobs",
        lambda _config: StaticJobSource(_sample_jobs()),
    )
    monkeypatch.setattr(
        job_search_task_module,
        "build_telegram_notifier",
        lambda *, env: notifier,
    )

    JobSearchTask(str(config_path)).run()
    notifier.messages.clear()

    monkeypatch.setitem(
        job_search_task_module.build_job_search_sources_for_profile.__globals__["SOURCE_FACTORIES"],
        "static_jobs",
        lambda _config: StaticJobSource(_sample_jobs()),
    )

    JobSearchTask(str(config_path)).run()

    assert len(notifier.messages) == 1
    assert "Source failures" in notifier.messages[0]
    assert "cloudflare challenge" in notifier.messages[0]


def test_job_search_task_rejects_disabled_config(tmp_path: Path) -> None:
    config_path = _write_config(
        tmp_path,
        source_names=["static_jobs"],
        job_search_enabled=False,
    )

    with pytest.raises(ValueError, match="job_search' is disabled"):
        JobSearchTask(str(config_path)).run()


def test_job_search_task_falls_open_to_rules_without_primary_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A missing LLM key must degrade the digest to rules, not abort the task."""

    config_path = _write_config(
        tmp_path,
        source_names=["static_jobs"],
        llm_enabled=True,
        llm_min_rule_score=0,
    )
    db_path = tmp_path / "signals.sqlite3"
    notifier = FakeTelegramNotifier()
    monkeypatch.setattr(job_search_task_module, "DEFAULT_DB_PATH", db_path)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token-123")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "chat-456")
    monkeypatch.setitem(
        job_search_task_module.build_job_search_sources_for_profile.__globals__[
            "SOURCE_FACTORIES"
        ],
        "static_jobs",
        lambda _config: StaticJobSource(_sample_jobs()[:1]),
    )
    monkeypatch.setattr(
        job_search_task_module,
        "build_telegram_notifier",
        lambda *, env: notifier,
    )

    JobSearchTask(str(config_path)).run()

    captured = capsys.readouterr()
    assert '"scoring_method": "rule_fallback"' in captured.out
    assert len(notifier.messages) == 1


def test_job_search_task_allows_fake_llm_provider_without_openai_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = _write_config(
        tmp_path,
        source_names=["static_jobs"],
        llm_enabled=True,
        llm_provider="fake",
    )
    db_path = tmp_path / "signals.sqlite3"
    notifier = FakeTelegramNotifier()
    monkeypatch.setattr(job_search_task_module, "DEFAULT_DB_PATH", db_path)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token-123")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "chat-456")
    monkeypatch.setitem(
        job_search_task_module.build_job_search_sources_for_profile.__globals__[
            "SOURCE_FACTORIES"
        ],
        "static_jobs",
        lambda _config: StaticJobSource(_sample_jobs()[:1]),
    )
    monkeypatch.setattr(
        job_search_task_module,
        "build_telegram_notifier",
        lambda *, env: notifier,
    )

    JobSearchTask(str(config_path)).run()

    assert len(notifier.messages) == 1


def test_job_search_task_skips_telegram_validation_without_digest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config_path = _write_config(tmp_path, source_names=["static_jobs"])
    db_path = tmp_path / "signals.sqlite3"
    notifier = FakeTelegramNotifier()
    monkeypatch.setattr(job_search_task_module, "DEFAULT_DB_PATH", db_path)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token-123")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "chat-456")
    monkeypatch.setitem(
        job_search_task_module.build_job_search_sources_for_profile.__globals__["SOURCE_FACTORIES"],
        "static_jobs",
        lambda _config: StaticJobSource(_sample_jobs()),
    )
    monkeypatch.setattr(
        job_search_task_module,
        "build_telegram_notifier",
        lambda *, env: notifier,
    )

    JobSearchTask(str(config_path)).run()
    capsys.readouterr()

    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)

    JobSearchTask(str(config_path)).run()

    captured = capsys.readouterr()
    assert "telegram digest skipped: no new jobs to report" in captured.out


def test_job_search_task_requires_telegram_credentials_when_sending_digest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = _write_config(tmp_path, source_names=["static_jobs"])
    db_path = tmp_path / "signals.sqlite3"
    monkeypatch.setattr(job_search_task_module, "DEFAULT_DB_PATH", db_path)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    monkeypatch.setitem(
        job_search_task_module.build_job_search_sources_for_profile.__globals__["SOURCE_FACTORIES"],
        "static_jobs",
        lambda _config: StaticJobSource(_sample_jobs()),
    )

    with pytest.raises(
        SecretError,
        match="TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are required",
    ):
        JobSearchTask(str(config_path)).run()


def test_job_search_task_scores_unscored_duplicates_from_fetch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = _write_config(tmp_path, source_names=["static_jobs"])
    db_path = tmp_path / "signals.sqlite3"
    notifier = FakeTelegramNotifier()
    monkeypatch.setattr(job_search_task_module, "DEFAULT_DB_PATH", db_path)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token-123")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "chat-456")
    monkeypatch.setitem(
        job_search_task_module.build_job_search_sources_for_profile.__globals__["SOURCE_FACTORIES"],
        "static_jobs",
        lambda _config: StaticJobSource(_sample_jobs()),
    )
    monkeypatch.setattr(
        job_search_task_module,
        "build_telegram_notifier",
        lambda *, env: notifier,
    )

    init_db(db_path)
    for item in _sample_jobs():
        upsert_signal(item, db_path=db_path)

    JobSearchTask(str(config_path)).run()

    assert len(notifier.messages) == 1
    assert "Top recommended jobs" in notifier.messages[0]
    rows = _fetch_signal_rows(db_path)
    assert rows["Cloud SDET"]["score"] == 100.0
    assert rows["Cloud SDET"]["status"] == "reviewed"


def test_job_search_task_sends_failure_digest_when_all_sources_fail(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = _write_config(tmp_path, source_names=["failing_source"])
    db_path = tmp_path / "signals.sqlite3"
    notifier = FakeTelegramNotifier()
    monkeypatch.setattr(job_search_task_module, "DEFAULT_DB_PATH", db_path)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token-123")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "chat-456")

    class FailingSource:
        name = "failing_source"

        def fetch(self) -> list[SignalItem]:
            raise RuntimeError("imap auth failed")

    monkeypatch.setitem(
        job_search_task_module.build_job_search_sources_for_profile.__globals__["SOURCE_FACTORIES"],
        "failing_source",
        lambda _config: FailingSource(),
    )
    monkeypatch.setattr(
        job_search_task_module,
        "build_telegram_notifier",
        lambda *, env: notifier,
    )

    with pytest.raises(ValueError, match="All configured job-search sources failed"):
        JobSearchTask(str(config_path)).run()

    assert len(notifier.messages) == 1
    assert "Source failures" in notifier.messages[0]
    assert "imap auth failed" in notifier.messages[0]
    assert (
        "run summary: status=failed, fetched=0, new=0, "
        "skipped=0, notified=0"
    ) in notifier.messages[0]
    assert _task_run_statuses(db_path) == ["failed"]


def test_job_search_task_retries_telegram_digest_after_credential_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = _write_config(tmp_path, source_names=["static_jobs"])
    db_path = tmp_path / "signals.sqlite3"
    notifier = FakeTelegramNotifier()
    monkeypatch.setattr(job_search_task_module, "DEFAULT_DB_PATH", db_path)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    monkeypatch.setitem(
        job_search_task_module.build_job_search_sources_for_profile.__globals__["SOURCE_FACTORIES"],
        "static_jobs",
        lambda _config: StaticJobSource(_sample_jobs()),
    )
    monkeypatch.setattr(
        job_search_task_module,
        "build_telegram_notifier",
        lambda *, env: notifier,
    )

    with pytest.raises(SecretError):
        JobSearchTask(str(config_path)).run()

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token-123")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "chat-456")

    JobSearchTask(str(config_path)).run()

    assert len(notifier.messages) == 1
    assert "Cloud SDET" in notifier.messages[0]


def test_job_search_task_uses_llm_scorer_when_enabled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = _write_config(
        tmp_path,
        source_names=["static_jobs"],
        llm_enabled=True,
        llm_model="gpt-5.2",
    )
    db_path = tmp_path / "signals.sqlite3"
    notifier = FakeTelegramNotifier()
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(job_search_task_module, "DEFAULT_DB_PATH", db_path)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token-123")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "chat-456")
    monkeypatch.setitem(
        job_search_task_module.build_job_search_sources_for_profile.__globals__["SOURCE_FACTORIES"],
        "static_jobs",
        lambda _config: StaticJobSource(_sample_jobs()[:1]),
    )
    monkeypatch.setattr(
        job_search_task_module,
        "build_telegram_notifier",
        lambda *, env: notifier,
    )

    class FakeJobLLMScorer:
        def __init__(
            self,
            *,
            provider,
            model: str,
            profile,
            fallback_scorer,
            **_kwargs,
        ) -> None:
            calls.append((provider.name, model))

        def score_with_llm(self, item: SignalItem):
            return job_search_task_module.JobRuleScore(
                score=88,
                recommendation="strong_match",
                reason=(
                    "strong match (88/100): positives: LLM fit; "
                    "concerns: needs minor ramp."
                ),
                reasons=["LLM fit"],
                concerns=["needs minor ramp"],
            )

    monkeypatch.setattr(
        "app.scorers.job_score_pipeline.JobLLMScorer",
        FakeJobLLMScorer,
    )

    JobSearchTask(str(config_path)).run()

    assert calls == [("openai", "gpt-5.2")]
    assert len(notifier.messages) == 1
    assert "LLM fit" in notifier.messages[0]
    rows = _fetch_signal_rows(db_path)
    assert rows["Cloud SDET"]["score"] == 88.0
    assert rows["Cloud SDET"]["reason"].startswith("[llm]")


def test_job_search_task_skips_llm_for_low_rule_score_jobs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = _write_config(
        tmp_path,
        source_names=["static_jobs"],
        llm_enabled=True,
        llm_model="gpt-5.2",
        llm_min_rule_score=25,
    )
    db_path = tmp_path / "signals.sqlite3"
    notifier = FakeTelegramNotifier()
    llm_calls: list[str] = []
    monkeypatch.setattr(job_search_task_module, "DEFAULT_DB_PATH", db_path)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token-123")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "chat-456")
    monkeypatch.setitem(
        job_search_task_module.build_job_search_sources_for_profile.__globals__["SOURCE_FACTORIES"],
        "static_jobs",
        lambda _config: StaticJobSource(_sample_jobs()),
    )
    monkeypatch.setattr(
        job_search_task_module,
        "build_telegram_notifier",
        lambda *, env: notifier,
    )

    class FakeJobLLMScorer:
        def __init__(self, **kwargs) -> None:
            pass

        def score_with_llm(self, item: SignalItem):
            llm_calls.append(item.title)
            return job_search_task_module.JobRuleScore(
                score=88,
                recommendation="strong_match",
                reason="strong match (88/100): positives: LLM fit.",
            )

    monkeypatch.setattr(
        "app.scorers.job_score_pipeline.JobLLMScorer",
        FakeJobLLMScorer,
    )

    JobSearchTask(str(config_path)).run()

    assert llm_calls == ["Cloud SDET", "Test Automation Engineer"]
    rows = _fetch_signal_rows(db_path)
    assert rows["Helpdesk Support Engineer"]["reason"].startswith("[rule]")
    assert rows["Cloud SDET"]["reason"].startswith("[llm]")
    assert rows["Test Automation Engineer"]["reason"].startswith("[llm]")


def test_job_search_task_runs_multiple_profiles_and_groups_digest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = _write_multi_profile_config(tmp_path)
    db_path = tmp_path / "signals.sqlite3"
    notifier = FakeTelegramNotifier()
    monkeypatch.setattr(job_search_task_module, "DEFAULT_DB_PATH", db_path)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token-123")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "chat-456")

    cloud_jobs = [
        SignalItem(
            source="cloud_jobs",
            item_type="job",
            title="Cloud SDET",
            observed_at=OBSERVED_AT,
            url="https://example.com/jobs/cloud-sdet",
            content=(
                "Remote role building Python and Pytest automation for Azure, AWS, "
                "and Kubernetes workloads with SIEM, SOAR, DLP, and API security."
            ),
            metadata={"company": "Cloud Co", "location": "Taipei"},
        )
    ]
    security_jobs = [
        SignalItem(
            source="security_jobs",
            item_type="job",
            title="DevSecOps Engineer",
            observed_at=OBSERVED_AT,
            url="https://example.com/jobs/devsecops",
            content=(
                "Cloud security and DevSecOps role with Kubernetes, Python, and "
                "security automation for product security teams."
            ),
            metadata={"company": "Sec Co", "location": "Remote"},
        ),
        # Same URL as cloud profile — must dedup to one DB row.
        SignalItem(
            source="security_jobs",
            item_type="job",
            title="Cloud SDET",
            observed_at=OBSERVED_AT,
            url="https://example.com/jobs/cloud-sdet",
            content="Duplicate listing across profiles.",
            metadata={"company": "Cloud Co", "location": "Taipei"},
        ),
    ]

    factories = (
        job_search_task_module.build_job_search_sources_for_profile.__globals__[
            "SOURCE_FACTORIES"
        ]
    )
    monkeypatch.setitem(
        factories,
        "cloud_jobs",
        lambda _config: StaticJobSource(cloud_jobs),
    )
    monkeypatch.setitem(
        factories,
        "security_jobs",
        lambda _config: StaticJobSource(security_jobs),
    )
    monkeypatch.setattr(
        job_search_task_module,
        "build_telegram_notifier",
        lambda *, env: notifier,
    )

    JobSearchTask(str(config_path)).run()

    assert len(notifier.messages) == 1
    message = notifier.messages[0]
    assert "## cloud_sdet" in message
    assert "## devsecops_security" in message
    assert "Cloud SDET" in message
    assert "DevSecOps Engineer" in message
    assert "default/cloud_jobs" not in message

    assert _count_rows(db_path, "signals") == 2
    rows = _fetch_signal_rows(db_path)
    assert "Cloud SDET" in rows
    assert "DevSecOps Engineer" in rows


def test_job_search_task_reports_source_failures_per_profile(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = _write_multi_profile_config(tmp_path)
    db_path = tmp_path / "signals.sqlite3"
    notifier = FakeTelegramNotifier()
    monkeypatch.setattr(job_search_task_module, "DEFAULT_DB_PATH", db_path)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token-123")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "chat-456")

    class FailingSource:
        name = "cloud_jobs"

        def fetch(self) -> list[SignalItem]:
            raise RuntimeError("rate limited")

    factories = (
        job_search_task_module.build_job_search_sources_for_profile.__globals__[
            "SOURCE_FACTORIES"
        ]
    )
    monkeypatch.setitem(
        factories,
        "cloud_jobs",
        lambda _config: FailingSource(),
    )
    monkeypatch.setitem(
        factories,
        "security_jobs",
        lambda _config: StaticJobSource(
            [
                SignalItem(
                    source="security_jobs",
                    item_type="job",
                    title="Cloud SDET",
                    observed_at=OBSERVED_AT,
                    url="https://example.com/jobs/cloud-sdet",
                    content=(
                        "Remote role building Python and Pytest automation for "
                        "Azure, AWS, and Kubernetes workloads with SIEM, SOAR, "
                        "DLP, and API security."
                    ),
                    metadata={"company": "Cloud Co", "location": "Taipei"},
                )
            ]
        ),
    )
    monkeypatch.setattr(
        job_search_task_module,
        "build_telegram_notifier",
        lambda *, env: notifier,
    )

    JobSearchTask(str(config_path)).run()

    assert len(notifier.messages) == 1
    assert "cloud_sdet/cloud_jobs: rate limited" in notifier.messages[0]
    assert "## cloud_sdet" in notifier.messages[0]
    assert "## devsecops_security" in notifier.messages[0]


def _sample_jobs() -> list[SignalItem]:
    return [
        SignalItem(
            source="static_jobs",
            item_type="job",
            title="Cloud SDET",
            observed_at=OBSERVED_AT,
            url="https://example.com/jobs/cloud-sdet",
            content=(
                "Remote role building Python and Pytest automation for Azure, AWS, "
                "and Kubernetes workloads with SIEM, SOAR, DLP, and API security."
            ),
            metadata={
                "company": "Example Security Product Company",
                "location": "Taipei",
                "work_style": "Hybrid",
            },
        ),
        SignalItem(
            source="static_jobs",
            item_type="job",
            title="Test Automation Engineer",
            observed_at=OBSERVED_AT,
            url="https://example.com/jobs/test-automation-engineer",
            content=(
                "Hybrid Python, Pytest, and Cypress automation role, but the team "
                "also runs an on-call-heavy support rotation."
            ),
            metadata={
                "company": "Example QA Company",
                "location": "Taipei",
            },
        ),
        SignalItem(
            source="static_jobs",
            item_type="job",
            title="Helpdesk Support Engineer",
            observed_at=OBSERVED_AT,
            url="https://example.com/jobs/helpdesk-support-engineer",
            content=(
                "Customer support role with SLA-heavy support, rotating shift work, "
                "on-call-heavy support, SCCM, and Intune administration."
            ),
            metadata={
                "company": "Example Support Company",
                "location": "Taipei",
            },
        ),
    ]


def _write_config(
    tmp_path: Path,
    *,
    source_names: list[str],
    job_search_enabled: bool = True,
    telegram_enabled: bool = True,
    llm_enabled: bool = False,
    llm_model: str = "gpt-4o-mini",
    llm_provider: str | None = None,
    llm_min_rule_score: int | None = None,
    digest_max_strong_matches: int | None = None,
    digest_max_maybe_matches: int | None = None,
    include_rejected_summary: bool | None = None,
) -> Path:
    config_path = tmp_path / "config.yaml"
    scoring_lines = [
        "scoring:",
        "  freshness_weight: 0.6",
        "  priority_weight: 0.4",
        f"  llm_enabled: {'true' if llm_enabled else 'false'}",
        f"  llm_model: {llm_model}",
    ]
    if llm_min_rule_score is not None:
        scoring_lines.append(f"  llm_min_rule_score: {llm_min_rule_score}")
    if digest_max_strong_matches is not None:
        scoring_lines.append(
            f"  digest_max_strong_matches: {digest_max_strong_matches}"
        )
    if digest_max_maybe_matches is not None:
        scoring_lines.append(
            f"  digest_max_maybe_matches: {digest_max_maybe_matches}"
        )
    if include_rejected_summary is not None:
        scoring_lines.append(
            f"  include_rejected_summary: "
            f"{'true' if include_rejected_summary else 'false'}"
        )
    llm_lines: list[str] = []
    if llm_provider is not None:
        llm_lines = [
            "llm:",
            f"  provider: {llm_provider}",
            f"  model: {llm_model}",
        ]

    config_path.write_text(
        "\n".join(
            [
                "agent:",
                "  timezone: Asia/Taipei",
                '  digest_time: "20:00"',
                "tasks:",
                "  job_search:",
                f"    enabled: {'true' if job_search_enabled else 'false'}",
                "    sources:",
                *[f"      - {source_name}" for source_name in source_names],
                "    keywords:",
                "      - python",
                "      - pytest",
                "  tw_stock_movers:",
                "    enabled: true",
                "    top_n: 20",
                "    timezone: Asia/Taipei",
                "notifiers:",
                "  telegram:",
                f"    enabled: {'true' if telegram_enabled else 'false'}",
                *scoring_lines,
                *llm_lines,
            ]
        ),
        encoding="utf-8",
    )
    return config_path


def _write_multi_profile_config(tmp_path: Path) -> Path:
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
                "    profiles:",
                "      - name: cloud_sdet",
                "        sources:",
                "          - cloud_jobs",
                "        keywords:",
                "          - Cloud SDET",
                "        locations:",
                "          - Taiwan",
                "      - name: devsecops_security",
                "        sources:",
                "          - security_jobs",
                "        keywords:",
                "          - DevSecOps",
                "        locations:",
                "          - Remote",
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
                "  llm_enabled: false",
                "  llm_model: gpt-4o-mini",
            ]
        ),
        encoding="utf-8",
    )
    return config_path


def _fetch_signal_rows(db_path: Path) -> dict[str, sqlite3.Row]:
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            "SELECT title, score, status, reason FROM signals ORDER BY id"
        ).fetchall()
    return {str(row["title"]): row for row in rows}


def _count_rows(db_path: Path, table_name: str) -> int:
    with sqlite3.connect(db_path) as connection:
        row = connection.execute(
            f"SELECT COUNT(*) FROM {table_name}"
        ).fetchone()
    assert row is not None
    return int(row[0])


def _signal_run_statuses(db_path: Path) -> list[str]:
    with sqlite3.connect(db_path) as connection:
        rows = connection.execute(
            "SELECT status FROM signal_runs ORDER BY id"
        ).fetchall()
    return [str(row[0]) for row in rows]


def _task_run_statuses(db_path: Path) -> list[str]:
    with sqlite3.connect(db_path) as connection:
        rows = connection.execute(
            "SELECT status FROM task_runs ORDER BY id"
        ).fetchall()
    return [str(row[0]) for row in rows]


def _parse_structured_logs(output: str) -> list[dict[str, object]]:
    return [
        json.loads(line)
        for line in output.splitlines()
        if line.startswith("{")
    ]
