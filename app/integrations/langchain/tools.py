"""Adapt project-owned tools into LangChain StructuredTool instances."""

from __future__ import annotations

import json
from typing import Any

from app.tools import ToolContext, ToolRegistry

from .deps import require_langchain


def to_langchain_tools(
    registry: ToolRegistry,
    *,
    context: ToolContext | None = None,
) -> list[Any]:
    """Wrap allowlisted registry tools as LangChain structured tools."""

    require_langchain()
    from langchain_core.tools import StructuredTool

    ctx = context or ToolContext()
    adapted: list[Any] = []
    for descriptor in registry.list_tools():
        name = descriptor["name"]
        description = descriptor["description"]
        schema = descriptor["input_schema"]

        def _make_runner(tool_name: str, tool_description: str):
            def _run(**kwargs: Any) -> str:
                result = registry.invoke(tool_name, dict(kwargs), context=ctx)
                return json.dumps(result.to_safe_dict(), ensure_ascii=False)

            _run.__name__ = tool_name
            _run.__doc__ = tool_description
            return _run

        adapted.append(
            StructuredTool.from_function(
                func=_make_runner(name, description),
                name=name,
                description=description,
                infer_schema=False,
                args_schema=_json_schema_to_args_model(name, schema),
            )
        )
    return adapted


def _json_schema_to_args_model(tool_name: str, schema: dict[str, Any]) -> Any:
    """Build a lightweight Pydantic model from a JSON Schema object."""

    from pydantic import Field, create_model

    properties = schema.get("properties") or {}
    required = set(schema.get("required") or [])
    fields: dict[str, Any] = {}
    for key, prop in properties.items():
        if not isinstance(prop, dict):
            continue
        py_type = _json_type_to_python(prop.get("type"))
        description = prop.get("description") or ""
        if key in required:
            fields[key] = (py_type, Field(description=description))
        else:
            fields[key] = (py_type | None, Field(default=None, description=description))

    model_name = "".join(part.capitalize() for part in tool_name.split("_")) + "Args"
    if not fields:
        return create_model(model_name)
    return create_model(model_name, **fields)


def _json_type_to_python(prop_type: Any) -> Any:
    mapping = {
        "string": str,
        "integer": int,
        "number": float,
        "boolean": bool,
        "array": list,
    }
    return mapping.get(prop_type, Any)
