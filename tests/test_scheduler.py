"""Tests for persistent schedules (#85)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from app.cli import main
from app.scheduler import (
    ScheduleStore,
    compute_next_run,
    format_schedules,
    run_schedule_now,
    run_schedule_tick,
)
from app.setup import SetupOptions, run_setup


def _prepare(tmp_path: Path) -> Path:
    run_setup(
        root=tmp_path,
        options=SetupOptions(provider="fake", non_interactive=True, force=True),
    )
    return tmp_path / "data" / "signal_agent.db"


def test_cron_every_and_timezone_explicit(tmp_path: Path) -> None:
    db = _prepare(tmp_path)
    store = ScheduleStore(db)
    with pytest.raises(ValueError, match="timezone"):
        store.add(
            name="bad",
            task_name="dummy",
            timezone="Asia/Taipei",
            run_at="2026-10-03T12:00:00",  # naive
        )
    schedule = store.add(
        name="movers",
        task_name="dummy",
        timezone="Asia/Taipei",
        cron="every:15",
    )
    assert schedule.timezone == "Asia/Taipei"
    assert schedule.next_run_at is not None

    after = datetime(2026, 3, 8, 6, 30, tzinfo=ZoneInfo("America/New_York"))
    nxt = compute_next_run(
        cron="0 2 * * *",
        after=after,
        timezone_name="America/New_York",
    )
    assert nxt is not None
    # DST spring-forward: 02:00 local on 2026-03-08 does not exist; next 02:00
    # is the following day.
    assert nxt.tzinfo is not None
    assert nxt.astimezone(ZoneInfo("America/New_York")).hour == 2


def test_overlap_prevention_restart_and_disabled(tmp_path: Path) -> None:
    db = _prepare(tmp_path)
    store = ScheduleStore(db)
    past = (datetime.now(UTC) - timedelta(minutes=1)).isoformat().replace(
        "+00:00", "Z"
    )
    schedule = store.add(
        name="once",
        task_name="dummy",
        timezone="Asia/Taipei",
        cron="every:60",
        max_retries=0,
        backoff_seconds=0,
    )
    # Force due.
    with store._connect() as connection:
        connection.execute(
            "UPDATE schedules SET next_run_at = ? WHERE id = ?",
            (past, schedule.id),
        )
        connection.commit()

    calls: list[str] = []

    def runner(task_name: str, *, config_path: str) -> None:
        calls.append(task_name)
        # Simulate overlap: leave a running row if allow_overlap is false by
        # inserting a stuck run before the second tick would start.
        del config_path

    first = run_schedule_tick(
        root=tmp_path,
        config_path=tmp_path / "config.yaml",
        db_path=db,
        task_runner=runner,
    )
    assert first.started == 1
    assert calls == ["dummy"]

    # Restart safety: next_run advanced; immediate second tick should not
    # re-execute the same due slot.
    second = run_schedule_tick(
        root=tmp_path,
        config_path=tmp_path / "config.yaml",
        db_path=db,
        task_runner=runner,
    )
    assert second.due == 0
    assert calls == ["dummy"]

    # Overlap: mark a running row and force due again.
    schedule = store.get(schedule.id)
    assert schedule is not None
    with store._connect() as connection:
        connection.execute(
            "UPDATE schedules SET next_run_at = ? WHERE id = ?",
            (past, schedule.id),
        )
        connection.execute(
            """
            INSERT INTO schedule_runs (
                schedule_id, run_id, status, started_at, attempt, dry_run
            ) VALUES (?, 'stuck', 'running', ?, 1, 0)
            """,
            (schedule.id, past),
        )
        connection.commit()
    overlap = run_schedule_tick(
        root=tmp_path,
        config_path=tmp_path / "config.yaml",
        db_path=db,
        task_runner=runner,
    )
    assert overlap.skipped >= 1
    assert "skipped_overlap" in " ".join(overlap.details or [])

    disabled = store.set_enabled(schedule.id, enabled=False)
    assert disabled.enabled is False
    # Clear the stuck running row so a forced manual run can proceed.
    with store._connect() as connection:
        connection.execute(
            "UPDATE schedule_runs SET status = 'failed' WHERE run_id = 'stuck'"
        )
        connection.commit()
    status = run_schedule_now(
        schedule.id,
        root=tmp_path,
        config_path=tmp_path / "config.yaml",
        db_path=db,
        dry_run=True,
        task_runner=runner,
    )
    # force=True allows disabled schedules for manual run.
    assert status == "started_ok"


def test_transient_retry_and_permission_rejection(tmp_path: Path) -> None:
    db = _prepare(tmp_path)
    store = ScheduleStore(db)
    past = (datetime.now(UTC) - timedelta(minutes=1)).isoformat().replace(
        "+00:00", "Z"
    )
    flaky = store.add(
        name="flaky",
        task_name="dummy",
        timezone="Asia/Taipei",
        cron="every:30",
        max_retries=2,
        backoff_seconds=0,
    )
    with store._connect() as connection:
        connection.execute(
            "UPDATE schedules SET next_run_at = ? WHERE id = ?",
            (past, flaky.id),
        )
        connection.commit()

    attempts = {"n": 0}

    def flaky_runner(task_name: str, *, config_path: str) -> None:
        del task_name, config_path
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise TimeoutError("temporarily unavailable")

    result = run_schedule_tick(
        root=tmp_path,
        config_path=tmp_path / "config.yaml",
        db_path=db,
        task_runner=flaky_runner,
    )
    assert result.succeeded == 1
    assert attempts["n"] == 3

    blocked = store.add(
        name="notify",
        task_name="dummy",
        timezone="Asia/Taipei",
        cron="every:10",
        metadata={"require_tools": ["send_telegram_message"]},
    )
    with store._connect() as connection:
        connection.execute(
            "UPDATE schedules SET next_run_at = ? WHERE id = ?",
            (past, blocked.id),
        )
        connection.commit()
    denied = run_schedule_tick(
        root=tmp_path,
        config_path=tmp_path / "config.yaml",
        db_path=db,
        task_runner=lambda *_args, **_kwargs: None,
    )
    assert denied.skipped >= 1
    assert any("skipped_permission" in item for item in (denied.details or []))
    history = store.history(blocked.id)
    assert history
    assert history[0].status == "permission_denied"


def test_mid_retry_preserves_next_run_and_stale_reclaim(tmp_path: Path) -> None:
    db = _prepare(tmp_path)
    store = ScheduleStore(db)
    schedule = store.add(
        name="preserve",
        task_name="dummy",
        timezone="Asia/Taipei",
        cron="every:30",
        max_retries=2,
        backoff_seconds=0,
    )
    original_next = schedule.next_run_at
    assert original_next is not None

    # Mid-retry must not null out an existing next_run_at (#85).
    store.mark_finished(
        schedule,
        run_id="attempt-1",
        status="failed",
        error="timeout",
        schedule_next=False,
    )
    mid = store.get(schedule.id)
    assert mid is not None
    assert mid.next_run_at == original_next
    assert mid.last_status == "retrying"

    # Claim clears next_run; crash recovery via reclaim restores cadence.
    past = (datetime.now(UTC) - timedelta(hours=2)).isoformat().replace(
        "+00:00", "Z"
    )
    store.mark_started(
        schedule.id, run_id="stuck-run", attempt=1, dry_run=False
    )
    with store._connect() as connection:
        connection.execute(
            "UPDATE schedules SET last_started_at = ? WHERE id = ?",
            (past, schedule.id),
        )
        connection.execute(
            "UPDATE schedule_runs SET started_at = ? WHERE run_id = ?",
            (past, "stuck-run"),
        )
        connection.commit()
    claimed = store.get(schedule.id)
    assert claimed is not None
    assert claimed.next_run_at is None
    assert store.has_running(schedule.id) is True

    reclaimed = store.reclaim_stale_runs(older_than_seconds=60)
    assert reclaimed == 1
    recovered = store.get(schedule.id)
    assert recovered is not None
    assert recovered.next_run_at is not None
    assert recovered.last_status == "failed"
    assert store.has_running(schedule.id) is False


def test_schedule_cli_list_add_history(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _prepare(tmp_path)
    code = main(
        [
            "--root",
            str(tmp_path),
            "schedule",
            "add",
            "--name",
            "cli",
            "--task",
            "dummy",
            "--timezone",
            "Asia/Taipei",
            "--cron",
            "every:5",
            "--dry-run",
        ]
    )
    assert code == 0
    out = capsys.readouterr().out
    assert "cli" in out
    assert "Asia/Taipei" in out

    code = main(["--root", str(tmp_path), "schedules"])
    assert code == 0
    listed = capsys.readouterr().out
    assert "cli" in listed
    assert format_schedules([]) == "(no schedules)"
