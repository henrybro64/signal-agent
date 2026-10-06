"""Fake agent runtime for offline tests (#93)."""

from __future__ import annotations

from app.tools.permissions import PermissionPolicy

from .guard import authorize
from .models import RuntimeRequest, RuntimeResult


class FakeAgentRuntime:
    name = "fake"

    def __init__(
        self,
        *,
        summary: str = "fake runtime ok",
        policy: PermissionPolicy | None = None,
    ) -> None:
        self.summary = summary
        self.policy = policy
        self.calls: list[RuntimeRequest] = []

    def execute(self, request: RuntimeRequest) -> RuntimeResult:
        authorize(request, self.policy)
        self.calls.append(request)
        return RuntimeResult(
            ok=True,
            summary=self.summary,
            runtime=self.name,
            actions_taken=request.allowed_actions or ("analyze",),
        )
