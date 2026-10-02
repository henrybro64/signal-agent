"""Helpers for formatting and delivering top-level task failure alerts."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml

from app.notifiers import build_telegram_notifier

from .config import ConfigError, load_config
from .logging import get_logger, log_event
from .secrets import EnvSettings, SecretError, load_env_settings

_MAX_ERROR_SUMMARY_LENGTH = 200
_REDACTION_FALLBACK = "***"
_DEFAULT_FAILURE_ALERT_TIMEZONE = "UTC"
_UNSAFE_ERROR_PATTERNS = (
    re.compile(r"<[^>]+>"),
    re.compile(r"<!doctype", re.IGNORECASE),
    re.compile(r"\b[a-z]+/[a-z0-9.+-]+\b", re.IGNORECASE),
    re.compile(r"https?://", re.IGNORECASE),
    re.compile(r"\bwww\.", re.IGNORECASE),
    re.compile(r"[\{\}\[\]]"),
    re.compile(
        r"\b(?:subject|from|to|cc|bcc|reply-to|return-path):",
        re.IGNORECASE,
    ),
    re.compile(r"\b(?:host|port|path|query|params?)="),
)

FailureAlertStatus = Literal[
    "sent",
    "skipped_config_unavailable",
    "skipped_disabled",
    "skipped_missing_credentials",
    "failed",
]


@dataclass(slots=True, frozen=True)
class FailureAlertOutcome:
    """Describe the result of a best-effort task failure alert attempt."""

    status: FailureAlertStatus
    error_summary: str
    detail: str | None = None


@dataclass(slots=True, frozen=True)
class FailureAlertSettings:
    """Resolved settings for deciding whether and how to send alerts."""

    enabled: bool
    timezone_name: str


def notify_task_failure(
    *,
    task_name: str,
    config_path: str,
    error: BaseException,
    occurred_at: datetime | None = None,
) -> FailureAlertOutcome:
    """Send a best-effort Telegram alert for one top-level task failure."""

    settings = _resolve_failure_alert_settings(config_path)
    error_summary = summarize_error(error)

    if not settings.enabled:
        return FailureAlertOutcome(
            status="skipped_disabled",
            error_summary=error_summary,
        )

    env = load_env_settings()
    error_summary = summarize_error(error, env=env)

    try:
        notifier = build_telegram_notifier(env=env)
    except SecretError as exc:
        outcome = FailureAlertOutcome(
            status="skipped_missing_credentials",
            error_summary=error_summary,
            detail=str(exc),
        )
        _log_task_failure_alert(task_name=task_name, outcome=outcome)
        return outcome

    message = build_task_failure_message(
        task_name=task_name,
        error=error,
        env=env,
        timezone_name=settings.timezone_name,
        occurred_at=occurred_at,
    )

    try:
        notifier.send_message(message)
    except Exception as exc:
        outcome = FailureAlertOutcome(
            status="failed",
            error_summary=error_summary,
            detail=summarize_error(exc, env=env),
        )
        _log_task_failure_alert(task_name=task_name, outcome=outcome)
        return outcome

    outcome = FailureAlertOutcome(
        status="sent",
        error_summary=error_summary,
    )
    _log_task_failure_alert(task_name=task_name, outcome=outcome)
    return outcome


def build_task_failure_message(
    *,
    task_name: str,
    error: BaseException,
    env: EnvSettings,
    timezone_name: str,
    occurred_at: datetime | None = None,
) -> str:
    """Build the Telegram payload for one failed task run."""

    return "\n".join(
        [
            "⚠️ signal-agent task failed",
            f"Task: {task_name}",
            f"Time: {_format_failure_time(timezone_name, occurred_at)}",
            f"Error: {summarize_error(error, env=env)}",
        ]
    )


def summarize_error(
    error: BaseException,
    *,
    env: EnvSettings | None = None,
    max_length: int = _MAX_ERROR_SUMMARY_LENGTH,
) -> str:
    """Collapse an exception into a short single-line summary."""

    raw_message = str(error).strip()
    if raw_message:
        first_line = next(
            (line.strip() for line in raw_message.splitlines() if line.strip()),
            "",
        )
        summary = " ".join(first_line.split())
    else:
        summary = error.__class__.__name__

    if env is not None:
        summary = _redact_secret_values(summary, env)

    summary = _prune_unsafe_error_detail(summary)

    if len(summary) > max_length:
        summary = summary[: max_length - 3].rstrip() + "..."

    if not summary or summary.replace("*", "").strip() == "":
        return error.__class__.__name__

    return summary


def _resolve_failure_alert_settings(config_path: str) -> FailureAlertSettings:
    try:
        config = load_config(config_path)
    except (ConfigError, FileNotFoundError):
        return _load_fallback_failure_alert_settings(config_path)

    return FailureAlertSettings(
        enabled=config.notifiers.telegram.failure_alerts_enabled,
        timezone_name=config.agent.timezone,
    )


def _load_fallback_failure_alert_settings(
    config_path: str,
) -> FailureAlertSettings:
    raw_config = _load_raw_config_mapping(config_path)
    if raw_config is None:
        return FailureAlertSettings(
            enabled=True,
            timezone_name=_DEFAULT_FAILURE_ALERT_TIMEZONE,
        )

    enabled = _extract_bool(
        raw_config,
        "notifiers",
        "telegram",
        "failure_alerts_enabled",
    )
    if enabled is None:
        enabled = _extract_bool(
            raw_config,
            "notifiers",
            "telegram",
            "enabled",
        )
    if enabled is None:
        enabled = True

    timezone_name = _extract_str(raw_config, "agent", "timezone")
    if timezone_name is None:
        timezone_name = _DEFAULT_FAILURE_ALERT_TIMEZONE

    return FailureAlertSettings(
        enabled=enabled,
        timezone_name=timezone_name,
    )


def _load_raw_config_mapping(config_path: str) -> dict[str, object] | None:
    path = Path(config_path)
    if not path.is_file():
        return None

    try:
        with path.open(encoding="utf-8") as config_file:
            data = yaml.safe_load(config_file)
    except (OSError, yaml.YAMLError):
        return None

    if not isinstance(data, dict):
        return None

    return data


def _extract_bool(data: object, *path: str) -> bool | None:
    value = _extract_nested_value(data, *path)
    if isinstance(value, bool):
        return value
    return None


def _extract_str(data: object, *path: str) -> str | None:
    value = _extract_nested_value(data, *path)
    if isinstance(value, str) and value.strip():
        return value
    return None


def _extract_nested_value(data: object, *path: str) -> object | None:
    current = data
    for key in path:
        if not isinstance(current, dict) or key not in current:
            return None
        current = current[key]
    return current


def _prune_unsafe_error_detail(summary: str) -> str:
    candidate = summary
    if "; " in candidate:
        candidate = candidate.split("; ", 1)[0].rstrip()

    while _looks_like_unsafe_error(candidate):
        if ": " not in candidate:
            break
        next_candidate = candidate.rsplit(": ", 1)[0].rstrip()
        if not next_candidate or next_candidate == candidate:
            break
        candidate = next_candidate

    return candidate


def _looks_like_unsafe_error(text: str) -> bool:
    return any(pattern.search(text) for pattern in _UNSAFE_ERROR_PATTERNS)


def _format_failure_time(
    timezone_name: str,
    occurred_at: datetime | None,
) -> str:
    timestamp = occurred_at or datetime.now(UTC)
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=UTC)

    try:
        zone = ZoneInfo(timezone_name)
        label = timezone_name
    except ZoneInfoNotFoundError:
        zone = ZoneInfo("UTC")
        label = "UTC"

    localized = timestamp.astimezone(zone)
    return f"{localized.strftime('%Y-%m-%d %H:%M')} {label}"


def _redact_secret_values(text: str, env: EnvSettings) -> str:
    redacted = text
    for secret in _iter_secret_values(env):
        redacted = redacted.replace(secret, _REDACTION_FALLBACK)
    return redacted


def _iter_secret_values(env: EnvSettings) -> tuple[str, ...]:
    return tuple(
        value
        for value in (
            env.openai_api_key,
            env.telegram_bot_token,
            env.telegram_chat_id,
            env.linkedin_email_imap_username,
            env.linkedin_email_imap_password,
        )
        if value
    )


def _log_task_failure_alert(
    *,
    task_name: str,
    outcome: FailureAlertOutcome,
) -> None:
    if outcome.status == "skipped_disabled":
        return

    logger = get_logger("task_failure_alert")
    level = logging.INFO
    event = "task_failure_alert"
    message = "Task failure alert processed"

    if outcome.status == "skipped_missing_credentials":
        level = logging.WARNING
        message = "Task failure alert skipped"
    elif outcome.status == "failed":
        level = logging.ERROR
        message = "Task failure alert failed"

    log_event(
        logger,
        event=event,
        message=message,
        level=level,
        task_name=task_name,
        notification_status=outcome.status,
        error_summary=outcome.error_summary,
        error=outcome.detail,
    )
