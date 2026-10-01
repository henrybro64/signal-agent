"""Secret redaction helpers for tool args, results, and logs."""

from __future__ import annotations

import re
from typing import Any

_REDACTED = "***"
_SECRET_KEY_RE = re.compile(
    r"(api[_-]?key|token|password|secret|authorization|bot_token|chat_id)",
    re.IGNORECASE,
)


def redact_value(value: str, secrets: tuple[str, ...] = ()) -> str:
    """Replace known secret substrings in text."""

    redacted = value
    for secret in secrets:
        if secret and secret in redacted:
            redacted = redacted.replace(secret, _REDACTED)
    return redacted


def redact_mapping(
    payload: dict[str, Any],
    *,
    secrets: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Return a shallow-copied mapping with secret-looking values redacted."""

    result: dict[str, Any] = {}
    for key, value in payload.items():
        if _SECRET_KEY_RE.search(str(key)):
            result[key] = _REDACTED
            continue
        if isinstance(value, dict):
            result[key] = redact_mapping(value, secrets=secrets)
        elif isinstance(value, list):
            result[key] = [
                redact_mapping(item, secrets=secrets)
                if isinstance(item, dict)
                else (
                    redact_value(item, secrets)
                    if isinstance(item, str)
                    else item
                )
                for item in value
            ]
        elif isinstance(value, str):
            result[key] = redact_value(value, secrets)
        else:
            result[key] = value
    return result


def safe_log_fields(
    *,
    tool_name: str,
    status: str,
    duration_ms: int,
    arguments: dict[str, Any],
    result: dict[str, Any] | None = None,
    error: str | None = None,
    secrets: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Build structured log fields without leaking secrets or huge payloads."""

    safe_args = redact_mapping(arguments, secrets=secrets)
    fields: dict[str, Any] = {
        "tool_name": tool_name,
        "status": status,
        "duration_ms": duration_ms,
        "arg_keys": sorted(safe_args),
        "arg_count": len(safe_args),
    }
    if error is not None:
        fields["error"] = redact_value(error, secrets)[:200]
    if result is not None:
        fields["result_ok"] = bool(result.get("ok"))
        data = result.get("data")
        if isinstance(data, dict):
            fields["result_keys"] = sorted(data)
            if "count" in data:
                fields["result_count"] = data.get("count")
            if "items" in data and isinstance(data["items"], list):
                fields["result_item_count"] = len(data["items"])
    return fields
