import json
import sqlite3
from pathlib import Path

import pytest

from app.core.config import AppConfig
from app.sources import (
    SOURCE_FACTORIES,
    GlassdoorSource,
    IndeedSource,
    Job104Source,
)
from app.tasks import BaseTask, DummyTask, FetchTask, build_task, list_tasks
from app.tasks import fetch as fetch_task_module
from tests.source_fakes import FakeResponse, FakeSession

FIXTURE_PATH = (
    Path(__file__).resolve().parent / "fixtures" / "job104_search_response.json"
)
INDEED_FIXTURE_PATH = (
    Path(__file__).resolve().parent / "fixtures" / "indeed_search_taiwan.html"
)
GLASSDOOR_FIXTURE_PATH = (
    Path(__file__).resolve().parent / "fixtures" / "glassdoor_search_taiwan.html"
)


def test_list_tasks_returns_registered_task_names() -> None:
    assert list_tasks() == ["dummy", "fetch"]


def test_build_task_returns_task_instance() -> None:
    task = build_task("dummy", config_path="config.yaml")

    assert isinstance(task, DummyTask)
    assert isinstance(task, BaseTask)
    assert task.config_path == "config.yaml"


def test_build_task_supports_fetch_task() -> None:
    task = build_task("fetch", config_path="config.yaml")

    assert isinstance(task, FetchTask)
    assert task.name == "fetch"


def test_build_task_unknown_name_raises_clear_error() -> None:
    with pytest.raises(ValueError, match="Unknown task: missing-task"):
        build_task("missing-task", config_path="config.yaml")

    with pytest.raises(ValueError, match="Available tasks: dummy, fetch"):
        build_task("missing-task", config_path="config.yaml")


def test_fetch_task_persists_items_from_supported_sources(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config_path = _write_config(
        tmp_path,
        source_names=["dummy"],
    )
    db_path = tmp_path / "signals.sqlite3"
    monkeypatch.setattr(fetch_task_module, "DEFAULT_DB_PATH", db_path)

    FetchTask(str(config_path)).run()

    captured = capsys.readouterr()
    assert "fetch task executed using" in captured.out
    assert "fetched 2 signal(s) from 1 successful source(s)" in captured.out
    assert "persisted 2 inserted / 0 updated" in captured.out
    assert _count_rows(db_path, "signals") == 2
    assert _signal_run_statuses(db_path) == ["completed"]


def test_fetch_task_skips_failed_and_unsupported_sources(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config_path = _write_config(
        tmp_path,
        source_names=["dummy", "job104", "linkedin_email"],
    )
    db_path = tmp_path / "signals.sqlite3"
    monkeypatch.setattr(fetch_task_module, "DEFAULT_DB_PATH", db_path)

    def fail_job104_fetch(self: Job104Source) -> list[object]:
        raise RuntimeError("cloudflare challenge")

    monkeypatch.setattr(Job104Source, "fetch", fail_job104_fetch)

    FetchTask(str(config_path)).run()

    captured = capsys.readouterr()
    assert "skipped unsupported sources: linkedin_email" in captured.out
    assert "source failed: job104: cloudflare challenge" in captured.out
    assert _count_rows(db_path, "signals") == 2
    assert _signal_run_statuses(db_path) == ["completed", "failed"]


def test_fetch_task_persists_and_dedups_job104_results(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config_path = _write_config(
        tmp_path,
        source_names=["job104"],
    )
    db_path = tmp_path / "signals.sqlite3"
    monkeypatch.setattr(fetch_task_module, "DEFAULT_DB_PATH", db_path)

    fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    session = FakeSession(
        [
            FakeResponse(json_payload=fixture),
            FakeResponse(json_payload=fixture),
        ]
    )

    def build_job104_source(config: AppConfig) -> Job104Source:
        return Job104Source(
            keywords=config.tasks.job_search.keywords,
            session=session,
            request_delay_seconds=0.0,
        )

    monkeypatch.setitem(SOURCE_FACTORIES, "job104", build_job104_source)

    FetchTask(str(config_path)).run()
    first_run = capsys.readouterr()
    assert "persisted 2 inserted / 0 updated" in first_run.out
    assert _count_rows(db_path, "signals") == 2

    FetchTask(str(config_path)).run()
    second_run = capsys.readouterr()
    assert "persisted 0 inserted / 2 updated" in second_run.out
    assert _count_rows(db_path, "signals") == 2
    assert _signal_run_statuses(db_path) == ["completed", "completed"]


def test_fetch_task_persists_and_dedups_indeed_results(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config_path = _write_config(
        tmp_path,
        source_names=["indeed"],
    )
    db_path = tmp_path / "signals.sqlite3"
    monkeypatch.setattr(fetch_task_module, "DEFAULT_DB_PATH", db_path)

    fixture_html = INDEED_FIXTURE_PATH.read_text(encoding="utf-8")
    session = FakeSession(
        [
            FakeResponse(text=fixture_html),
            FakeResponse(text=fixture_html),
        ]
    )

    def build_indeed_source(config: AppConfig) -> IndeedSource:
        return IndeedSource(
            keywords=config.tasks.job_search.keywords,
            session=session,
            request_delay_seconds=0.0,
        )

    monkeypatch.setitem(SOURCE_FACTORIES, "indeed", build_indeed_source)

    FetchTask(str(config_path)).run()
    first_run = capsys.readouterr()
    assert "persisted 2 inserted / 0 updated" in first_run.out
    assert _count_rows(db_path, "signals") == 2

    FetchTask(str(config_path)).run()
    second_run = capsys.readouterr()
    assert "persisted 0 inserted / 2 updated" in second_run.out
    assert _count_rows(db_path, "signals") == 2
    assert _signal_run_statuses(db_path) == ["completed", "completed"]


def test_fetch_task_persists_and_dedups_glassdoor_results(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config_path = _write_config(
        tmp_path,
        source_names=["glassdoor"],
    )
    db_path = tmp_path / "signals.sqlite3"
    monkeypatch.setattr(fetch_task_module, "DEFAULT_DB_PATH", db_path)

    fixture_html = GLASSDOOR_FIXTURE_PATH.read_text(encoding="utf-8")
    session = FakeSession(
        [
            FakeResponse(text=fixture_html),
            FakeResponse(text=fixture_html),
        ]
    )

    def build_glassdoor_source(config: AppConfig) -> GlassdoorSource:
        return GlassdoorSource(
            keywords=config.tasks.job_search.keywords,
            session=session,
            request_delay_seconds=0.0,
        )

    monkeypatch.setitem(SOURCE_FACTORIES, "glassdoor", build_glassdoor_source)

    FetchTask(str(config_path)).run()
    first_run = capsys.readouterr()
    assert "persisted 2 inserted / 0 updated" in first_run.out
    assert _count_rows(db_path, "signals") == 2

    FetchTask(str(config_path)).run()
    second_run = capsys.readouterr()
    assert "persisted 0 inserted / 2 updated" in second_run.out
    assert _count_rows(db_path, "signals") == 2
    assert _signal_run_statuses(db_path) == ["completed", "completed"]


def test_fetch_task_persists_from_multiple_successful_sources(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config_path = _write_config(
        tmp_path,
        source_names=["dummy", "job104"],
    )
    db_path = tmp_path / "signals.sqlite3"
    monkeypatch.setattr(fetch_task_module, "DEFAULT_DB_PATH", db_path)

    fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    session = FakeSession([FakeResponse(json_payload=fixture)])

    def build_job104_source(config: AppConfig) -> Job104Source:
        return Job104Source(
            keywords=config.tasks.job_search.keywords,
            session=session,
            request_delay_seconds=0.0,
        )

    monkeypatch.setitem(SOURCE_FACTORIES, "job104", build_job104_source)

    FetchTask(str(config_path)).run()

    captured = capsys.readouterr()
    assert "fetched 4 signal(s) from 2 successful source(s)" in captured.out
    assert "persisted 4 inserted / 0 updated" in captured.out
    assert _count_rows(db_path, "signals") == 4
    assert _signal_run_statuses(db_path) == ["completed", "completed"]


def test_fetch_task_fails_when_all_configured_sources_fail(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = _write_config(
        tmp_path,
        source_names=["job104"],
    )
    db_path = tmp_path / "signals.sqlite3"
    monkeypatch.setattr(fetch_task_module, "DEFAULT_DB_PATH", db_path)

    def fail_job104_fetch(self: Job104Source) -> list[object]:
        raise RuntimeError("cloudflare challenge")

    monkeypatch.setattr(Job104Source, "fetch", fail_job104_fetch)

    with pytest.raises(ValueError, match="All configured job-search sources failed"):
        FetchTask(str(config_path)).run()

    assert _count_rows(db_path, "signals") == 0
    assert _signal_run_statuses(db_path) == ["failed"]


def test_fetch_task_fails_when_only_unsupported_sources_are_configured(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = _write_config(
        tmp_path,
        source_names=["linkedin_email"],
    )
    db_path = tmp_path / "signals.sqlite3"
    monkeypatch.setattr(fetch_task_module, "DEFAULT_DB_PATH", db_path)

    with pytest.raises(
        ValueError,
        match="No supported job-search sources are configured",
    ):
        FetchTask(str(config_path)).run()


def _write_config(
    tmp_path: Path,
    *,
    source_names: list[str],
) -> Path:
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
                *[f"      - {source_name}" for source_name in source_names],
                "    keywords:",
                "      - python",
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
            ]
        ),
        encoding="utf-8",
    )
    return config_path


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
