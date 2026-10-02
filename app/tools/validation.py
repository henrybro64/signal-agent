"""Lightweight JSON Schema subset validation for tool arguments."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .models import ToolError

_Validator = Callable[..., None]


def validate_tool_arguments(
    arguments: dict[str, Any],
    schema: dict[str, Any],
    *,
    tool_name: str,
) -> None:
    """Validate ``arguments`` against a tool ``input_schema``.

    Supports the subset used by built-in tools: object type, properties,
    additionalProperties, required, and simple string/integer/boolean/array
    property constraints (minimum/maximum/minItems/maxItems/enum).
    """

    if schema.get("type", "object") != "object":
        raise ToolError(
            f"Tool {tool_name!r} schema must describe an object",
            code="invalid_schema",
        )
    if not isinstance(arguments, dict):
        raise ToolError(
            f"Tool {tool_name!r} arguments must be an object",
            code="invalid_argument",
        )

    properties = schema.get("properties") or {}
    if not isinstance(properties, dict):
        properties = {}

    additional = schema.get("additionalProperties", True)
    if additional is False:
        unknown = sorted(set(arguments) - set(properties))
        if unknown:
            raise ToolError(
                f"Tool {tool_name!r} rejected unknown arguments: "
                + ", ".join(unknown),
                code="invalid_argument",
            )

    required = schema.get("required") or []
    if isinstance(required, list):
        missing = [key for key in required if key not in arguments]
        if missing:
            raise ToolError(
                f"Tool {tool_name!r} missing required arguments: "
                + ", ".join(str(key) for key in missing),
                code="invalid_argument",
            )

    for key, value in arguments.items():
        prop_schema = properties.get(key)
        if not isinstance(prop_schema, dict):
            continue
        _validate_typed_value(value, prop_schema, path=f"{tool_name}.{key}")


def _validate_typed_value(value: Any, schema: dict[str, Any], *, path: str) -> None:
    expected = schema.get("type")
    validator = _TYPE_VALIDATORS.get(expected) if isinstance(expected, str) else None
    if validator is not None:
        validator(value, schema, path=path)


def _validate_integer(value: Any, schema: dict[str, Any], *, path: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ToolError(
            f"'{path}' must be an integer",
            code="invalid_argument",
        )
    minimum = schema.get("minimum")
    maximum = schema.get("maximum")
    if isinstance(minimum, int) and value < minimum:
        raise ToolError(
            f"'{path}' must be >= {minimum}",
            code="invalid_argument",
        )
    if isinstance(maximum, int) and value > maximum:
        raise ToolError(
            f"'{path}' must be <= {maximum}",
            code="invalid_argument",
        )


def _validate_string(value: Any, schema: dict[str, Any], *, path: str) -> None:
    if not isinstance(value, str):
        raise ToolError(
            f"'{path}' must be a string",
            code="invalid_argument",
        )
    min_length = schema.get("minLength")
    max_length = schema.get("maxLength")
    if isinstance(min_length, int) and len(value) < min_length:
        raise ToolError(
            f"'{path}' must be at least {min_length} characters",
            code="invalid_argument",
        )
    if isinstance(max_length, int) and len(value) > max_length:
        raise ToolError(
            f"'{path}' must be at most {max_length} characters",
            code="invalid_argument",
        )
    enum = schema.get("enum")
    if isinstance(enum, list) and value not in enum:
        raise ToolError(
            f"'{path}' must be one of {enum}",
            code="invalid_argument",
        )


def _validate_boolean(value: Any, schema: dict[str, Any], *, path: str) -> None:
    _ = schema
    if not isinstance(value, bool):
        raise ToolError(
            f"'{path}' must be a boolean",
            code="invalid_argument",
        )


def _validate_array(value: Any, schema: dict[str, Any], *, path: str) -> None:
    if not isinstance(value, list):
        raise ToolError(
            f"'{path}' must be an array",
            code="invalid_argument",
        )
    min_items = schema.get("minItems")
    max_items = schema.get("maxItems")
    if isinstance(min_items, int) and len(value) < min_items:
        raise ToolError(
            f"'{path}' must have at least {min_items} items",
            code="invalid_argument",
        )
    if isinstance(max_items, int) and len(value) > max_items:
        raise ToolError(
            f"'{path}' must have at most {max_items} items",
            code="invalid_argument",
        )
    item_schema = schema.get("items")
    if isinstance(item_schema, dict):
        for index, item in enumerate(value):
            _validate_typed_value(item, item_schema, path=f"{path}[{index}]")


_TYPE_VALIDATORS: dict[str, _Validator] = {
    "integer": _validate_integer,
    "string": _validate_string,
    "boolean": _validate_boolean,
    "array": _validate_array,
}
