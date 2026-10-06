"""Local schema validation without fetching remote schema references."""

from __future__ import annotations

import json
from typing import Any

from jsonschema import validators
from jsonschema.exceptions import SchemaError, ValidationError

from .exceptions import LLMProviderError, LLMResponseError
from .models import StructuredOutputSpec


def validate_schema(spec: StructuredOutputSpec) -> None:
    def check_refs(value: Any) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if key in {"$ref", "$dynamicRef", "$recursiveRef"} and (
                    not isinstance(item, str) or not item.startswith("#")
                ):
                    raise LLMProviderError(
                        "Structured schemas must use local references"
                    )
                check_refs(item)
        elif isinstance(value, list):
            for item in value:
                check_refs(item)

    check_refs(spec.schema)
    try:
        validators.validator_for(spec.schema).check_schema(spec.schema)
    except SchemaError:
        raise LLMProviderError("Invalid structured output schema") from None


def validate_output(text: str, spec: StructuredOutputSpec) -> None:
    validate_schema(spec)
    try:
        value = json.loads(text)
        validators.validator_for(spec.schema)(spec.schema).validate(value)
    except (ValueError, ValidationError):
        # ValidationError messages include response data; never propagate them.
        raise LLMResponseError(
            "Structured output did not match the requested schema"
        ) from None
    except Exception:
        raise LLMResponseError(
            "Structured output schema could not be resolved"
        ) from None
