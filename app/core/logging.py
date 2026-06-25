"""Structured stdout logging helpers for task execution."""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from types import TracebackType
from typing import Any

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


class CurrentStdoutHandler(logging.Handler):
    """Write records to the current stdout stream at emit time."""

    terminator = "\n"

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = self.format(record)
            stream = sys.stdout
            stream.write(message + self.terminator)
            stream.flush()
        except Exception:
            self.handleError(record)


def configure_logging(*, level: int | str = logging.INFO) -> logging.Logger:
    """Configure the shared stdout logger once and return it."""

    global _LOGGING_CONFIGURED

    logger = logging.getLogger(_ROOT_LOGGER_NAME)
    logger.setLevel(level)

    if _LOGGING_CONFIGURED:
        return logger

    handler = CurrentStdoutHandler()
    handler.setFormatter(JsonLogFormatter())

    logger.handlers.clear()
    logger.addHandler(handler)
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
