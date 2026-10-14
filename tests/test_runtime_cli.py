"""Tests for runtime lifecycle and status (#82)."""

from __future__ import annotations

import subprocess
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.cli import main
from app.runtime import (
    RuntimeLifecycleError,
    collect_status,
    format_status_report,
    get_runtime_info,
    read_log_tail,
    restart_runtime,
    start_runtime,
    stop_runtime,
)
from app.runtime.paths import log_path
from app.setup import SetupOptions, run_setup


def _prepare(tmp_path: Path) -> None:
    run_setup(
        root=tmp_path,
        options=SetupOptions(provider="fake", non_interactive=True, force=True),
    )


def test_start_stop_status_idempotent(tmp_path: Path) -> None:
    _prepare(tmp_path)
    info = start_runtime(root=tmp_path, config_path=tmp_path / "config.yaml")
    assert info.running
    assert info.pid is not None

    with pytest.raises(RuntimeLifecycleError, match="already running"):
        start_runtime(root=tmp_path, config_path=tmp_path / "config.yaml")

    status = collect_status(root=tmp_path, config_path=tmp_path / "config.yaml")
    assert status.runtime_running
    text = format_status_report(status)
    assert "Runtime: running" in text

    stopped = stop_runtime(root=tmp_path)
    assert not stopped.running
    again = stop_runtime(root=tmp_path)
    assert again.detail == "already stopped"


def test_restart_and_graceful_stop(tmp_path: Path) -> None:
    _prepare(tmp_path)
    first = start_runtime(root=tmp_path, config_path=tmp_path / "config.yaml")
    restarted = restart_runtime(root=tmp_path, config_path=tmp_path / "config.yaml")
    assert restarted.running
    assert restarted.pid != first.pid
    stop_runtime(root=tmp_path)
    assert not get_runtime_info(tmp_path).running


def test_logs_redact_secrets(tmp_path: Path) -> None:
    _prepare(tmp_path)
    (tmp_path / ".env").write_text(
        "OPENAI_API_KEY=sk-log-secret\nTELEGRAM_BOT_TOKEN=\nTELEGRAM_CHAT_ID=\n",
        encoding="utf-8",
    )
    path = log_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("token sk-log-secret leaked\n", encoding="utf-8")
    lines = read_log_tail(root=tmp_path, lines=10)
    assert lines
    assert "sk-log-secret" not in "\n".join(lines)
    assert "***" in lines[0]


def test_logs_redact_quoted_dotenv_and_zero_lines(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _prepare(tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "")
    (tmp_path / ".env").write_text(
        'OPENAI_API_KEY="sk-quoted-secret"\n',
        encoding="utf-8",
    )
    path = log_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("leaked sk-quoted-secret value\nsecond\n", encoding="utf-8")
    lines = read_log_tail(root=tmp_path, lines=10)
    assert "sk-quoted-secret" not in "\n".join(lines)
    assert read_log_tail(root=tmp_path, lines=0) == []


def test_status_marks_stale_worker_stopped(tmp_path: Path) -> None:
    from app.runtime.lifecycle import write_runtime_state

    _prepare(tmp_path)
    write_runtime_state(
        {
            "state": "stopped",
            "worker": "active",
            "last_tick_at": "2026-01-01T00:00:00Z",
            "last_tick_ok": True,
        },
        root=tmp_path,
    )
    status = collect_status(root=tmp_path, config_path=tmp_path / "config.yaml")
    assert not status.runtime_running
    assert status.worker_status == "stopped"


def test_cli_tools_respects_empty_allowlist(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import yaml

    _prepare(tmp_path)
    config = tmp_path / "config.yaml"
    payload = yaml.safe_load(config.read_text(encoding="utf-8"))
    payload.setdefault("tools", {})["allowlist"] = []
    config.write_text(
        yaml.safe_dump(payload, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    loaded = yaml.safe_load(config.read_text(encoding="utf-8"))
    assert loaded["tools"]["allowlist"] == []
    monkeypatch.chdir(tmp_path)
    assert main(["tools", "--config", str(config)]) == 0
    captured = capsys.readouterr().out
    assert "(none)" in captured
    assert "search_jobs:" not in captured


def test_cli_tasks_models_tools_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _prepare(tmp_path)
    monkeypatch.chdir(tmp_path)
    assert main(["tasks"]) == 0
    assert main(["models", "--config", str(tmp_path / "config.yaml")]) == 0
    assert main(["tools", "--config", str(tmp_path / "config.yaml")]) == 0
    assert main(["status", "--root", str(tmp_path)]) == 0
    # dummy task should work through new run subcommand
    assert main(["run", "--config", str(tmp_path / "config.yaml"), "dummy"]) == 0


def test_runtime_worker_tick_survives_tool_failure(tmp_path: Path) -> None:
    from app.runtime.worker import run_worker_tick

    _prepare(tmp_path)
    # Missing/broken config path should be recorded, not raised.
    result = run_worker_tick(
        root=tmp_path,
        config_path=tmp_path / "missing-config.yaml",
    )
    assert result.failures
    assert result.detail


def test_cli_help_lists_top_level_commands() -> None:
    from app.cli.parser import build_parser

    help_text = build_parser().format_help()
    for name in (
        "setup",
        "doctor",
        "start",
        "stop",
        "restart",
        "status",
        "chat",
        "run",
        "tasks",
        "models",
        "tools",
        "logs",
    ):
        assert name in help_text


def test_root_before_subcommand_and_config_under_root(tmp_path: Path) -> None:
    from app.cli.parser import build_parser, resolve_runtime_args

    parser = build_parser()
    before = resolve_runtime_args(
        parser.parse_args(["--root", str(tmp_path), "doctor"])
    )
    assert before.root == str(tmp_path)
    assert before.config == str(tmp_path / "config.yaml")

    after = resolve_runtime_args(
        parser.parse_args(["doctor", "--root", str(tmp_path)])
    )
    assert after.root == str(tmp_path)
    assert after.config == str(tmp_path / "config.yaml")


def test_read_cmdline_not_truncated_by_columns(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ownership checks must survive COLUMNS truncation from ``ps`` (#99)."""

    from app.runtime import lifecycle as runtime_lifecycle

    _prepare(tmp_path)
    info = start_runtime(root=tmp_path, config_path=tmp_path / "config.yaml")
    assert info.pid is not None
    monkeypatch.setenv("COLUMNS", "20")
    cmdline = runtime_lifecycle._read_cmdline(info.pid)
    assert cmdline is not None
    assert "app.runtime.service" in cmdline
    status = get_runtime_info(tmp_path)
    assert status.running
    assert status.pid == info.pid
    stop_runtime(root=tmp_path)
    assert not get_runtime_info(tmp_path).running


def test_ps_cmdline_uses_unlimited_width(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The ps fallback must pass -ww and drop COLUMNS so the test can fail (#99)."""

    from app.runtime import lifecycle as runtime_lifecycle

    captured: dict[str, object] = {}

    def fake_run(*args, **kwargs):
        captured["args"] = args[0] if args else kwargs.get("args")
        captured["env"] = kwargs.get("env")
        return subprocess.CompletedProcess(
            captured["args"],  # type: ignore[arg-type]
            0,
            stdout="/very/long/venv/bin/python -m app.runtime.service\n",
            stderr="",
        )

    monkeypatch.setenv("COLUMNS", "20")
    monkeypatch.setattr(runtime_lifecycle.subprocess, "run", fake_run)
    text = runtime_lifecycle._read_ps_cmdline(12345)
    assert text is not None
    assert "app.runtime.service" in text
    argv = captured["args"]
    assert isinstance(argv, list)
    assert "-ww" in argv
    env = captured["env"]
    assert isinstance(env, dict)
    assert "COLUMNS" not in env


def test_runtime_start_reclaims_orphaned_schedule(tmp_path: Path) -> None:
    """Startup must recover a SIGKILL-orphaned schedule without waiting (#98)."""

    from app.core.db import init_db
    from app.runtime import paths as runtime_paths
    from app.scheduler.store import ScheduleStore

    _prepare(tmp_path)
    db = runtime_paths.db_path(tmp_path)
    init_db(db)
    store = ScheduleStore(db)
    schedule = store.add(
        name="intraday-movers",
        task_name="dummy",
        timezone="Asia/Taipei",
        cron="every:5",
    )
    recent = (datetime.now(UTC) - timedelta(seconds=3)).isoformat().replace(
        "+00:00", "Z"
    )
    with store._connect() as connection:
        connection.execute(
            """
            INSERT INTO schedule_runs (
                schedule_id, run_id, status, started_at, attempt, dry_run
            ) VALUES (?, 'killed-run', 'running', ?, 1, 0)
            """,
            (schedule.id, recent),
        )
        connection.execute(
            "UPDATE schedules SET next_run_at = NULL, last_started_at = ?, "
            "last_status = 'running' WHERE id = ?",
            (recent, schedule.id),
        )
        connection.commit()

    info = start_runtime(root=tmp_path, config_path=tmp_path / "config.yaml")
    assert info.pid is not None
    try:
        recovered = store.get(schedule.id)
        assert recovered is not None
        assert recovered.last_status == "failed"
        assert recovered.next_run_at is not None
        log_text = log_path(tmp_path).read_text(encoding="utf-8")
        assert "reclaimed 1 orphaned schedule run(s) at startup" in log_text
    finally:
        stop_runtime(root=tmp_path)


def test_runtime_survives_briefly(tmp_path: Path) -> None:
    from app.runtime.lifecycle import read_runtime_state

    _prepare(tmp_path)
    info = start_runtime(root=tmp_path, config_path=tmp_path / "config.yaml")
    deadline = time.time() + 3.0
    state = {}
    while time.time() < deadline:
        state = read_runtime_state(tmp_path)
        if state.get("last_tick_at"):
            break
        time.sleep(0.05)
    assert get_runtime_info(tmp_path).running
    assert state.get("worker") == "active"
    assert state.get("last_tick_at")
    stop_runtime(root=tmp_path)
    assert info.pid is not None
    final = read_runtime_state(tmp_path)
    assert final.get("state") == "stopped"
    assert final.get("stopped_at")
