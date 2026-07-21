"""Tests for read-only health/status helpers and CLI commands."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.cli import main
from app.core.db import (
    finish_signal_run,
    finish_task_run,
    get_latest_signal_run_by_source,
    get_recent_signal_runs,
    init_db,
    start_signal_run,
    start_task_run,
)
from app.core.health import (
    HealthError,
    format_health_report,
    format_recent_signal_runs,
    load_recent_signal_runs,
    require_readable_db,
    summarize_run_health,
)
from app.core.secrets import EnvSettings


def test_require_readable_db_missing_path_raises(tmp_path: Path) -> None:
    missing = tmp_path / "missing.sqlite3"

    with pytest.raises(HealthError, match="Database file not found"):
        require_readable_db(missing)


def test_get_recent_signal_runs_orders_newest_first(tmp_path: Path) -> None:
    db_path = tmp_path / "signals.sqlite3"
    init_db(db_path)

    first = start_signal_run("job104", db_path=db_path)
    finish_signal_run(
        first,
        status="completed",
        fetched_count=3,
        inserted_count=2,
        updated_count=1,
        db_path=db_path,
    )
    second = start_signal_run("twse", db_path=db_path)
    finish_signal_run(
        second,
        status="failed",
        error="timeout",
        db_path=db_path,
    )

    runs = get_recent_signal_runs(limit=2, db_path=db_path)

    assert [run.source for run in runs] == ["twse", "job104"]
    assert runs[0].status == "failed"
    assert runs[0].error == "timeout"
    assert get_latest_signal_run_by_source("job104", db_path=db_path) is not None
    assert get_latest_signal_run_by_source("missing", db_path=db_path) is None


def test_summarize_run_health_empty_db_is_ok(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "signals.sqlite3"
    init_db(db_path)
    monkeypatch.setenv("SIGNAL_AGENT_DB_PATH", str(db_path))

    summary = summarize_run_health(
        env=EnvSettings(telegram_bot_token="token", telegram_chat_id="123"),
    )
    report = format_health_report(summary)

    assert summary.overall_status == "OK"
    assert "signal-agent health" in report
    assert f"DB: {db_path}" in report
    assert "Latest job_search run: none" in report
    assert "Latest tw_stock_movers run: none" in report
    assert "Recent failed sources: 0 in last 24h" in report
    assert "Telegram: configured" in report
    assert "Overall: OK" in report


def test_summarize_run_health_marks_failed_sources_degraded(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "signals.sqlite3"
    init_db(db_path)

    task_id = start_task_run("job_search", db_path=db_path)
    finish_task_run(task_id, status="success", db_path=db_path)
    run_id = start_signal_run("job104", db_path=db_path)
    finish_signal_run(
        run_id,
        status="failed",
        error='104 returned non-JSON content',
        db_path=db_path,
    )

    summary = summarize_run_health(
        db_path=db_path,
        env=EnvSettings(),
    )
    report = format_health_report(summary)

    assert summary.failed_sources_last_window == 1
    assert summary.overall_status == "DEGRADED"
    assert "Latest job_search run: completed," in report
    assert "Telegram: not configured" in report
    assert "Overall: DEGRADED" in report


def test_summarize_run_health_shows_market_closed_skip(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "signals.sqlite3"
    init_db(db_path)

    task_id = start_task_run("tw_stock_movers", db_path=db_path)
    finish_task_run(
        task_id,
        status="skipped",
        error_message="taiwan_market_closed",
        db_path=db_path,
    )

    summary = summarize_run_health(
        db_path=db_path,
        env=EnvSettings(),
    )
    report = format_health_report(summary)

    assert summary.overall_status == "OK"
    assert (
        "Latest tw_stock_movers run: skipped (taiwan_market_closed)," in report
    )


def test_format_recent_signal_runs_includes_failed_error(tmp_path: Path) -> None:
    db_path = tmp_path / "signals.sqlite3"
    init_db(db_path)

    ok_id = start_signal_run("twse", db_path=db_path)
    finish_signal_run(
        ok_id,
        status="completed",
        fetched_count=900,
        inserted_count=900,
        updated_count=0,
        db_path=db_path,
    )
    fail_id = start_signal_run("job104", db_path=db_path)
    finish_signal_run(
        fail_id,
        status="failed",
        error="104 returned non-JSON content",
        db_path=db_path,
    )

    runs = load_recent_signal_runs(limit=5, db_path=db_path)
    text = format_recent_signal_runs(runs)

    assert "job104 failed error='104 returned non-JSON content'" in text
    assert "twse completed fetched=900 inserted=900 updated=0" in text


def test_cli_health_missing_db_returns_nonzero(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    missing = tmp_path / "missing.sqlite3"
    monkeypatch.setenv("SIGNAL_AGENT_DB_PATH", str(missing))

    exit_code = main(["--health"])
    captured = capsys.readouterr()

    assert exit_code == 1
    assert "Database file not found" in captured.err


def test_cli_health_reads_temp_db(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    db_path = tmp_path / "signals.sqlite3"
    init_db(db_path)
    started = datetime(2026, 6, 25, 0, 5, tzinfo=UTC)
    task_id = start_task_run(
        "job_search",
        started_at=started,
        db_path=db_path,
    )
    finish_task_run(
        task_id,
        status="success",
        finished_at=started + timedelta(minutes=1),
        db_path=db_path,
    )
    monkeypatch.setenv("SIGNAL_AGENT_DB_PATH", str(db_path))
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)

    exit_code = main(["--health"])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "Latest job_search run: completed," in captured.out
    assert "Asia/Taipei" in captured.out
    assert "Overall: OK" in captured.out


def test_cli_recent_runs_respects_limit_and_order(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    db_path = tmp_path / "signals.sqlite3"
    init_db(db_path)

    for source in ("a", "b", "c"):
        run_id = start_signal_run(source, db_path=db_path)
        finish_signal_run(
            run_id,
            status="completed",
            fetched_count=1,
            inserted_count=1,
            db_path=db_path,
        )

    monkeypatch.setenv("SIGNAL_AGENT_DB_PATH", str(db_path))
    exit_code = main(["--recent-runs", "--limit", "2"])
    captured = capsys.readouterr()
    lines = [line for line in captured.out.splitlines() if line.strip()]

    assert exit_code == 0
    assert len(lines) == 2
    assert " c " in f" {lines[0]} "
    assert " b " in f" {lines[1]} "


def test_cli_recent_runs_empty_db(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    db_path = tmp_path / "signals.sqlite3"
    init_db(db_path)
    monkeypatch.setenv("SIGNAL_AGENT_DB_PATH", str(db_path))

    exit_code = main(["--recent-runs"])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert captured.out.strip() == "No signal runs recorded yet."
