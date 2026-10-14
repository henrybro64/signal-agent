"""Tests for persistent schedules (#85)."""

from __future__ import annotations

import time
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
    past = (datetime.now(UTC) - timedelta(minutes=1)).isoformat().replace("+00:00", "Z")
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

    def runner(task_name: str, **_kwargs) -> None:
        calls.append(task_name)
        # Simulate overlap: leave a running row if allow_overlap is false by
        # inserting a stuck run before the second tick would start.

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
    past = (datetime.now(UTC) - timedelta(minutes=1)).isoformat().replace("+00:00", "Z")
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

    def flaky_runner(*_args, **_kwargs) -> None:
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


def test_reclaim_orphaned_runs_ignores_age(tmp_path: Path) -> None:
    """Startup reclaim must recover freshly-killed runs without waiting (#98)."""

    db = _prepare(tmp_path)
    store = ScheduleStore(db)
    schedule = store.add(
        name="fresh-orphan",
        task_name="dummy",
        timezone="Asia/Taipei",
        cron="every:5",
    )
    recent = (
        (datetime.now(UTC) - timedelta(seconds=3)).isoformat().replace("+00:00", "Z")
    )
    with store._connect() as connection:
        connection.execute(
            """
            INSERT INTO schedule_runs (
                schedule_id, run_id, status, started_at, attempt, dry_run
            ) VALUES (?, 'fresh-orphan-run', 'running', ?, 1, 0)
            """,
            (schedule.id, recent),
        )
        connection.execute(
            "UPDATE schedules SET next_run_at = NULL, last_started_at = ?, "
            "last_status = 'running' WHERE id = ?",
            (recent, schedule.id),
        )
        connection.commit()

    # Age-based reclaim still waits the default hour window.
    assert store.reclaim_stale_runs(older_than_seconds=3600) == 0
    stuck = store.get(schedule.id)
    assert stuck is not None
    assert stuck.last_status == "running"
    assert stuck.next_run_at is None

    assert store.reclaim_orphaned_runs() == 1
    recovered = store.get(schedule.id)
    assert recovered is not None
    assert recovered.last_status == "failed"
    assert recovered.next_run_at is not None
    assert store.has_running(schedule.id) is False


def test_reclaim_orphaned_runs_skips_live_manual_owner(tmp_path: Path) -> None:
    """A live `schedule run` process must not be reclaimed at daemon start (#98)."""

    import subprocess

    db = _prepare(tmp_path)
    store = ScheduleStore(db)
    schedule = store.add(
        name="manual-live",
        task_name="dummy",
        timezone="Asia/Taipei",
        cron="every:5",
    )
    recent = (
        (datetime.now(UTC) - timedelta(seconds=3)).isoformat().replace("+00:00", "Z")
    )
    child = subprocess.Popen(["sleep", "60"])
    try:
        with store._connect() as connection:
            connection.execute(
                """
                INSERT INTO schedule_runs (
                    schedule_id, run_id, status, started_at, attempt, dry_run,
                    owner_pid
                ) VALUES (?, 'live-manual-run', 'running', ?, 1, 0, ?)
                """,
                (schedule.id, recent, child.pid),
            )
            connection.execute(
                "UPDATE schedules SET next_run_at = NULL, last_started_at = ?, "
                "last_status = 'running', last_owner_pid = ? WHERE id = ?",
                (recent, child.pid, schedule.id),
            )
            connection.commit()

        assert store.reclaim_orphaned_runs() == 0
        still = store.get(schedule.id)
        assert still is not None
        assert still.last_status == "running"
        assert still.next_run_at is None
    finally:
        child.terminate()
        child.wait(timeout=5)

    assert store.reclaim_orphaned_runs() == 1
    recovered = store.get(schedule.id)
    assert recovered is not None
    assert recovered.last_status == "failed"
    assert recovered.next_run_at is not None


def test_retry_backoff_respects_runtime_stop(tmp_path: Path) -> None:
    """Backoff must not block shutdown for the full retry sleep (#98)."""

    from app.scheduler import service as scheduler_service

    db = _prepare(tmp_path)
    store = ScheduleStore(db)
    schedule = store.add(
        name="backoff-stop",
        task_name="dummy",
        timezone="Asia/Taipei",
        cron="every:5",
        max_retries=2,
        backoff_seconds=30.0,
    )
    past = (datetime.now(UTC) - timedelta(minutes=1)).isoformat().replace("+00:00", "Z")
    with store._connect() as connection:
        connection.execute(
            "UPDATE schedules SET next_run_at = ? WHERE id = ?",
            (past, schedule.id),
        )
        connection.commit()

    stop_state = {"requested": False}
    attempts = {"count": 0}

    def fake_stop() -> bool:
        return stop_state["requested"]

    def flaky_runner(*_args, **_kwargs):
        attempts["count"] += 1
        if attempts["count"] == 1:
            stop_state["requested"] = True
            raise TimeoutError("transient")
        raise AssertionError("retry should not run after stop")

    outcome = scheduler_service._execute_with_retries(
        store,
        store.get(schedule.id) or schedule,
        root=tmp_path,
        db_path=db,
        config_path=str(tmp_path / "config.yaml"),
        runner=flaky_runner,
        dry_run=False,
        force=False,
        stop_requested=fake_stop,
    )
    assert outcome == "started_failed"
    assert attempts["count"] == 1
    finished = store.get(schedule.id)
    assert finished is not None
    assert finished.last_status == "failed"
    assert finished.next_run_at is not None
    history = store.history(schedule.id)
    assert history
    assert "TimeoutError" in (history[0].error or "")
    assert "interrupted by runtime shutdown" not in (history[0].error or "")


def test_tick_stop_skips_remaining_due_schedules(tmp_path: Path) -> None:
    """A stop during one schedule must not count the rest as failures (#98)."""

    db = _prepare(tmp_path)
    store = ScheduleStore(db)
    past = (datetime.now(UTC) - timedelta(minutes=1)).isoformat().replace("+00:00", "Z")
    for name in ("sched-a", "sched-b", "sched-c"):
        schedule = store.add(
            name=name,
            task_name="dummy",
            timezone="Asia/Taipei",
            cron="every:5",
        )
        with store._connect() as connection:
            connection.execute(
                "UPDATE schedules SET next_run_at = ? WHERE id = ?",
                (past, schedule.id),
            )
            connection.commit()

    stop_state = {"requested": False}

    def runner(*_args, **_kwargs) -> None:
        stop_state["requested"] = True

    result = run_schedule_tick(
        root=tmp_path,
        db_path=db,
        task_runner=runner,
        stop_requested=lambda: stop_state["requested"],
    )
    assert result.due == 3
    assert result.started == 1
    assert result.succeeded == 1
    assert result.failed == 0
    assert result.skipped == 2
    assert any("skipped_stopping" in item for item in (result.details or []))
    due_again = store.due()
    assert len(due_again) == 2


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

    # Mid-retry without a prior claim must not null an existing next_run_at.
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

    # Realistic claim → mid-retry path leaves next_run NULL until final finish;
    # crash recovery via reclaim restores cadence.
    past = (datetime.now(UTC) - timedelta(hours=2)).isoformat().replace("+00:00", "Z")
    with store._connect() as connection:
        connection.execute(
            "UPDATE schedules SET next_run_at = ?, last_status = 'retrying' "
            "WHERE id = ?",
            (None, schedule.id),
        )
        connection.execute(
            """
            INSERT INTO schedule_runs (
                schedule_id, run_id, status, started_at, attempt, dry_run
            ) VALUES (?, 'stuck-run', 'running', ?, 1, 0)
            """,
            (schedule.id, past),
        )
        connection.execute(
            "UPDATE schedules SET last_started_at = ?, last_status = 'running' "
            "WHERE id = ?",
            (past, schedule.id),
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


def test_atomic_claim_prevents_double_start(tmp_path: Path) -> None:
    db = _prepare(tmp_path)
    store = ScheduleStore(db)
    past = (datetime.now(UTC) - timedelta(minutes=1)).isoformat().replace("+00:00", "Z")
    schedule = store.add(
        name="race",
        task_name="dummy",
        timezone="Asia/Taipei",
        cron="every:15",
    )
    with store._connect() as connection:
        connection.execute(
            "UPDATE schedules SET next_run_at = ? WHERE id = ?",
            (past, schedule.id),
        )
        connection.commit()
    first = store.mark_started(
        schedule.id,
        run_id="a",
        attempt=1,
        dry_run=False,
        expected_next_run_at=past,
    )
    second = store.mark_started(
        schedule.id,
        run_id="b",
        attempt=1,
        dry_run=False,
        expected_next_run_at=past,
    )
    assert first is not None
    assert second is None


def test_cron_sunday_seven_dom_dow_or_and_validation(tmp_path: Path) -> None:
    """Sunday=7, DOM+DOW OR semantics, and reject never-run crons (#101)."""

    after = datetime(2026, 10, 14, 12, 0, tzinfo=ZoneInfo("Asia/Taipei"))
    sunday = compute_next_run(
        cron="0 9 * * 7",
        after=after,
        timezone_name="Asia/Taipei",
    )
    assert sunday is not None
    local_sunday = sunday.astimezone(ZoneInfo("Asia/Taipei"))
    assert local_sunday.weekday() == 6  # Sunday
    assert (local_sunday.hour, local_sunday.minute) == (9, 0)

    # Both DOM and DOW restricted → OR (next Monday, not next Mon-the-1st).
    either = compute_next_run(
        cron="0 9 1 * 1",
        after=after,
        timezone_name="Asia/Taipei",
    )
    assert either is not None
    local_either = either.astimezone(ZoneInfo("Asia/Taipei"))
    assert (local_either.year, local_either.month, local_either.day) == (
        2026,
        10,
        19,
    )
    assert local_either.weekday() == 0  # Monday

    with pytest.raises(ValueError, match="out of range"):
        compute_next_run(
            cron="0 25 * * *",
            after=after,
            timezone_name="Asia/Taipei",
        )
    with pytest.raises(ValueError, match="out of range"):
        compute_next_run(
            cron="60 9 * * *",
            after=after,
            timezone_name="Asia/Taipei",
        )

    started = time.perf_counter()
    missing = compute_next_run(
        cron="0 9 31 2 *",
        after=after,
        timezone_name="Asia/Taipei",
    )
    elapsed = time.perf_counter() - started
    assert missing is None
    assert elapsed < 0.25

    with pytest.raises(ValueError, match="every minutes"):
        compute_next_run(
            cron="every:abc",
            after=after,
            timezone_name="Asia/Taipei",
        )

    monday_in_feb = compute_next_run(
        cron="0 9 31 2 1",
        after=after,
        timezone_name="Asia/Taipei",
    )
    assert monday_in_feb is not None
    local_mon = monday_in_feb.astimezone(ZoneInfo("Asia/Taipei"))
    assert local_mon.month == 2
    assert local_mon.weekday() == 0

    db = _prepare(tmp_path)
    store = ScheduleStore(db)
    with pytest.raises(ValueError, match="never matches"):
        store.add(
            name="feb-31",
            task_name="dummy",
            timezone="Asia/Taipei",
            cron="0 9 31 2 *",
        )


def test_cron_dst_gap_and_leap_day_horizon() -> None:
    tz = ZoneInfo("America/New_York")
    after_gap = datetime(2026, 3, 8, 1, 30, tzinfo=tz)
    nxt = compute_next_run(
        cron="0 2 * * *",
        after=after_gap,
        timezone_name="America/New_York",
    )
    assert nxt is not None
    local = nxt.astimezone(tz)
    assert local.hour == 2
    assert local.day == 9  # skip nonexistent 2026-03-08 02:00

    after_leap = datetime(2025, 3, 1, 12, 0, tzinfo=UTC)
    leap = compute_next_run(
        cron="0 9 29 2 *",
        after=after_leap,
        timezone_name="UTC",
    )
    assert leap is not None
    assert (leap.year, leap.month, leap.day, leap.hour) == (2028, 2, 29, 9)


def test_schedule_cli_rejects_unknown_task_and_id(
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
            "bad",
            "--task",
            "not_a_real_task",
            "--timezone",
            "Asia/Taipei",
            "--cron",
            "every:5",
        ]
    )
    assert code == 1
    assert "Unknown task" in capsys.readouterr().err

    code = main(
        [
            "--root",
            str(tmp_path),
            "schedule",
            "enable",
            "missing-id",
        ]
    )
    assert code == 1
    assert "Unknown schedule id" in capsys.readouterr().err


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
