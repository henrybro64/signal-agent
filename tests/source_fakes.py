from __future__ import annotations

from dataclasses import dataclass


@dataclass
class FakeResponse:
    status_code: int = 200
    json_payload: dict[str, object] | None = None
    text: str = ""
    headers: dict[str, str] | None = None

    def json(self) -> dict[str, object]:
        if self.json_payload is None:
            raise ValueError("invalid json")
        return self.json_payload


class FakeSession:
    def __init__(self, responses: list[FakeResponse | Exception]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, object]] = []

    def get(
        self,
        url: str,
        *,
        params: dict[str, str],
        timeout: int,
    ) -> FakeResponse:
        self.calls.append({"url": url, "params": params, "timeout": timeout})
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response
