"""Structured stdout logging helpers for task execution."""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import Any

from app.core.log_rotate import attach_rotating_file_handler

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

    def __init__(self, stream_getter=None) -> None:
        super().__init__()
        self._stream_getter = stream_getter or (lambda: sys.stdout)  # noqa: E731

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
    stream=None,
    force: bool = False,
    log_file: str | Path | None = None,
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
        if stream is sys.stderr:
            stream_getter = lambda: sys.stderr  # noqa: E731  # skipcq: PYL-E731
        elif stream is None or stream is sys.stdout:
            stream_getter = lambda: sys.stdout  # noqa: E731  # skipcq: PYL-E731
        else:
            captured = stream
            stream_getter = lambda: captured  # noqa: E731  # skipcq: PYL-E731
        handler = CurrentStreamHandler(stream_getter)
        handler.setFormatter(JsonLogFormatter())
        logger.addHandler(handler)
    if log_file is not None:
        attach_rotating_file_handler(
            logger,
            Path(log_file),
            formatter=JsonLogFormatter(),
        )
    logger.propagate = False

    _LOGGING_CONFIGURED = True
    return logger


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
