"""Tests for runtime lifecycle and status (#82)."""

from __future__ import annotations

import time
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
