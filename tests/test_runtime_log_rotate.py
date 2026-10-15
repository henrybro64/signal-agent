"""Tests for runtime log rotation (#104)."""

from __future__ import annotations

import io
import logging
from collections.abc import Iterator
from pathlib import Path

import pytest

from app.core.log_rotate import runtime_log_limits
from app.core.logging import configure_logging, get_logger, log_event
from app.runtime.logs import read_log_tail


@pytest.fixture(autouse=True)
def _restore_logging() -> Iterator[None]:
    """Keep file handlers from leaking into later tests."""

    yield
    logger = configure_logging(force=True)
    for handler in list(logger.handlers):
        handler.close()
    configure_logging(force=True)


def test_runtime_log_limits_rejects_non_integer_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SIGNAL_AGENT_LOG_MAX_BYTES", "abc")
    with pytest.raises(ValueError, match="SIGNAL_AGENT_LOG_MAX_BYTES"):
        runtime_log_limits()


def test_runtime_log_limits_rejects_non_integer_backup_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SIGNAL_AGENT_LOG_BACKUP_COUNT", "1.5")
    with pytest.raises(ValueError, match="SIGNAL_AGENT_LOG_BACKUP_COUNT"):
        runtime_log_limits()


def test_runtime_log_limits_rejects_zero_backup_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SIGNAL_AGENT_LOG_BACKUP_COUNT", "0")
    with pytest.raises(ValueError, match="at least 1"):
        runtime_log_limits()


def test_rotating_handler_and_logs_tail_current_file(tmp_path: Path) -> None:
    log_file = tmp_path / "signal-agent.log"
    logger = configure_logging(
        level=logging.INFO,
        stream=io.StringIO(),
        force=True,
        log_file=log_file,
        log_max_bytes=800,
        log_backup_count=2,
    )
    for index in range(40):
        log_event(
            logger,
            event="fill",
            message="payload-" + ("x" * 80) + str(index),
        )

    backup = Path(f"{log_file}.1")
    assert log_file.is_file()
    assert backup.is_file()
    current = log_file.read_text(encoding="utf-8")
    assert "payload-" in current
    assert "fill" in current
    tail = read_log_tail(path=log_file, lines=5)
    assert tail
    assert tail[-1] in current.splitlines()
    assert any("payload-" in line for line in tail)


def test_debug_idle_ticks_are_dropped_at_default_info(tmp_path: Path) -> None:
    log_file = tmp_path / "signal-agent.log"
    configure_logging(
        level=logging.INFO,
        stream=io.StringIO(),
        force=True,
        log_file=log_file,
        log_max_bytes=64 * 1024,
        log_backup_count=2,
    )
    worker = get_logger("runtime.worker")
    log_event(
        worker,
        event="runtime_worker_tick",
        message="runtime worker tick completed",
        level=logging.DEBUG,
        ok=True,
        detail="idle",
    )
    content = log_file.read_text(encoding="utf-8") if log_file.is_file() else ""
    assert "runtime_worker_tick" not in content

    log_event(
        worker,
        event="runtime_started",
        message="local runtime started",
        level=logging.INFO,
        pid=1,
    )
    written = log_file.read_text(encoding="utf-8")
    assert "runtime_started" in written
    assert "local runtime started" in written
