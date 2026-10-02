"""Shared argument helpers for tool wrappers."""

from __future__ import annotations

from typing import Any

from .models import ToolError


def optional_int(
    arguments: dict[str, Any],
    key: str,
    *,
    default: int,
    minimum: int = 1,
    maximum: int = 100,
) -> int:
    """Return an optional integer argument with inclusive bounds."""

    if key not in arguments or arguments[key] is None:
        return default
    value = arguments[key]
    if isinstance(value, bool) or not isinstance(value, int):
        raise ToolError(f"'{key}' must be an integer", code="invalid_argument")
    if value < minimum or value > maximum:
        raise ToolError(
            f"'{key}' must be between {minimum} and {maximum}",
            code="invalid_argument",
        )
    return value
