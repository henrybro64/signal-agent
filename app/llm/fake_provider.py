"""Deterministic fake LLM provider for offline tests."""

from __future__ import annotations

import json
from collections.abc import Sequence

from .exceptions import LLMError, LLMResponseError
from .models import LLMRequest, LLMResponse

_QueuedItem = LLMResponse | Exception | str


class FakeLLMProvider:
    """Queue-backed provider that never touches the network."""

    name = "fake"

    def __init__(
        self,
        responses: Sequence[_QueuedItem] | None = None,
        *,
        default_text: str = '{"ok": true}',
        default_model: str = "fake-model",
        validate_structured_json: bool = True,
    ) -> None:
        self._queue: list[_QueuedItem] = list(responses or [])
        self.default_text = default_text
        self.default_model = default_model
        self.validate_structured_json = validate_structured_json
        self.calls: list[LLMRequest] = []

    def enqueue(self, item: _QueuedItem) -> None:
        """Append one scripted response or exception."""

        self._queue.append(item)

    def generate(self, request: LLMRequest) -> LLMResponse:
        self.calls.append(request)
        if self._queue:
            item = self._queue.pop(0)
            if isinstance(item, Exception):
                if isinstance(item, LLMError):
                    raise item
                raise LLMError(str(item)) from item
            if isinstance(item, str):
                response = LLMResponse(
                    text=item,
                    model=request.model or self.default_model,
                    provider=self.name,
                )
            else:
                response = item
        else:
            response = LLMResponse(
                text=self.default_text,
                model=request.model or self.default_model,
                provider=self.name,
            )

        if (
            self.validate_structured_json
            and request.structured_output is not None
        ):
            _require_json_object_text(response.text)

        return response


def _require_json_object_text(text: str) -> None:
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise LLMResponseError(
            "Fake LLM structured output was not valid JSON"
        ) from exc
    if not isinstance(parsed, dict):
        raise LLMResponseError(
            "Fake LLM structured output must be a JSON object"
        )
