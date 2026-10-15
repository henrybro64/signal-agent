"""Structured stdout logging helpers for task execution."""

from __future__ import annotations

import json
import logging
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import Any, Literal, TextIO

_ROOT_LOGGER_NAME = "signal_agent"
_LOGGING_CONFIGURED = False
_RESERVED_RECORD_FIELDS = frozenset(logging.makeLogRecord({}).__dict__) | {
    "asctime",
    "message",
}

ExcInfoArg = (
    BaseException
    | tuple[type[BaseException], BaseException, TracebackType | None]
    | bool
    | None
)


class JsonLogFormatter(logging.Formatter):
    """Render log records as JSON for Docker and k3s stdout consumption."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(
                record.created,
                tz=UTC,
            ).isoformat().replace("+00:00", "Z"),
            "level": record.levelname.lower(),
            "logger": record.name,
            "message": record.getMessage(),
        }

        for key, value in record.__dict__.items():
            if key in _RESERVED_RECORD_FIELDS or key.startswith("_"):
                continue
            payload[key] = value

        if record.exc_info:
            payload["stack_trace"] = self.formatException(record.exc_info)

        return json.dumps(payload, ensure_ascii=False, sort_keys=True)


class CurrentStreamHandler(logging.Handler):
    """Write records to a live stream getter at emit time."""

    terminator = "\n"

    def __init__(
        self,
        stream_getter: Callable[[], TextIO] | None = None,
    ) -> None:
        super().__init__()
        self._stream_getter = stream_getter or _stdout_stream

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = self.format(record)
            stream = self._stream_getter()
            stream.write(message + self.terminator)
            stream.flush()
        except Exception:
            self.handleError(record)


# Backward-compatible alias used by older imports/tests.
CurrentStdoutHandler = CurrentStreamHandler


def configure_logging(
    *,
    level: int | str = logging.INFO,
    stream: TextIO | Literal[False] | None = None,
    force: bool = False,
    log_file: str | Path | None = None,
    log_max_bytes: int | None = None,
    log_backup_count: int | None = None,
) -> logging.Logger:
    """Configure the shared structured logger once and return it.

    Defaults to stdout. Pass ``stream=sys.stderr`` (and ``force=True`` when
    reconfiguring) so MCP stdio JSON-RPC is not interleaved with log lines.
    Pass ``stream=False`` to attach only the rotating file handler (detached
    runtime). When ``log_file`` is set, also attach a size-based rotating
    file handler (#104).
    """

    global _LOGGING_CONFIGURED

    logger = logging.getLogger(_ROOT_LOGGER_NAME)
    logger.setLevel(level)

    if _LOGGING_CONFIGURED and not force:
        return logger

    logger.handlers.clear()
    if stream is not False:
        handler = CurrentStreamHandler(_stream_getter(stream))
        handler.setFormatter(JsonLogFormatter())
        logger.addHandler(handler)
    if log_file is not None:
        from app.core.log_rotate import attach_rotating_file_handler

        attach_rotating_file_handler(
            logger,
            Path(log_file),
            formatter=JsonLogFormatter(),
            max_bytes=log_max_bytes,
            backup_count=log_backup_count,
        )
    logger.propagate = False

    _LOGGING_CONFIGURED = True
    return logger


def _stdout_stream() -> TextIO:
    return sys.stdout


def _stderr_stream() -> TextIO:
    return sys.stderr


def _stream_getter(stream: TextIO | None) -> Callable[[], TextIO]:
    if stream is sys.stderr:
        return _stderr_stream
    if stream is None or stream is sys.stdout:
        return _stdout_stream

    def _captured() -> TextIO:
        return stream

    return _captured


def get_logger(name: str | None = None) -> logging.Logger:
    """Return a child logger within the shared structured logger tree."""

    configure_logging()
    if not name:
        return logging.getLogger(_ROOT_LOGGER_NAME)

    normalized = name.removeprefix("app.")
    return logging.getLogger(f"{_ROOT_LOGGER_NAME}.{normalized}")


def log_event(
    logger: logging.Logger,
    *,
    event: str,
    message: str,
    level: int = logging.INFO,
    exc_info: ExcInfoArg = None,
    **fields: Any,
) -> None:
    """Emit one structured log event with optional exception details."""

    extra = {"event": event}
    extra.update({key: value for key, value in fields.items() if value is not None})

    logger.log(
        level,
        message,
        extra=extra,
        exc_info=_normalize_exc_info(exc_info),
    )


def _normalize_exc_info(exc_info: ExcInfoArg):
    if exc_info is None or isinstance(exc_info, bool | tuple):
        return exc_info
    return (type(exc_info), exc_info, exc_info.__traceback__)
