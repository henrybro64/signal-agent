"""Runtime log rotation helpers (#104)."""

from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

# Defaults keep idle VMs from filling disks (~5 MiB × 5 ≈ 25 MiB cap).
DEFAULT_LOG_MAX_BYTES = 5 * 1024 * 1024
DEFAULT_LOG_BACKUP_COUNT = 5


def runtime_log_limits() -> tuple[int, int]:
    """Return ``(max_bytes, backup_count)`` from env overrides or defaults."""

    max_bytes = _env_int("SIGNAL_AGENT_LOG_MAX_BYTES", DEFAULT_LOG_MAX_BYTES)
    backups = _env_int("SIGNAL_AGENT_LOG_BACKUP_COUNT", DEFAULT_LOG_BACKUP_COUNT)
    if max_bytes < 1024:
        raise ValueError("SIGNAL_AGENT_LOG_MAX_BYTES must be at least 1024")
    if backups < 1:
        raise ValueError("SIGNAL_AGENT_LOG_BACKUP_COUNT must be at least 1")
    return max_bytes, backups


def attach_rotating_file_handler(
    logger: logging.Logger,
    path: Path,
    *,
    formatter: logging.Formatter,
    max_bytes: int | None = None,
    backup_count: int | None = None,
) -> RotatingFileHandler:
    """Attach the sole size-based rotator for ``path``; return it for reuse.

    Callers must not also append to ``path`` with a second rotator or a raw
    open() — that desyncs the handler fd from the filename ``signal-agent logs``
    tails.
    """

    resolved_max, resolved_backups = _resolve_limits(max_bytes, backup_count)
    path.parent.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        path,
        maxBytes=resolved_max,
        backupCount=resolved_backups,
        encoding="utf-8",
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    return handler


def _resolve_limits(
    max_bytes: int | None,
    backup_count: int | None,
) -> tuple[int, int]:
    resolved_max, resolved_backups = runtime_log_limits()
    if max_bytes is not None:
        resolved_max = max_bytes
    if backup_count is not None:
        resolved_backups = backup_count
    return resolved_max, resolved_backups


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        return int(str(raw).strip())
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer, got {raw!r}") from exc
