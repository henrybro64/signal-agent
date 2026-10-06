"""Bounded reasoning through signal-agent's existing LLM gateway."""

from __future__ import annotations

from app.core.logging import get_logger, log_event
from app.llm import LLMProvider, LLMRequest
from app.llm.exceptions import LLMError
from app.tools.permissions import PermissionPolicy

from .exceptions import RuntimeProviderError
from .guard import authorize
from .models import RuntimeRequest, RuntimeResult


class BuiltinAgentRuntime:
    name = "builtin"

    def __init__(
        self,
        *,
        provider: LLMProvider | None = None,
        policy: PermissionPolicy | None = None,
    ) -> None:
        self.provider = provider
        self.policy = policy

    def execute(self, request: RuntimeRequest) -> RuntimeResult:
        authorize(request, self.policy)
        if self.provider is None:
            raise RuntimeProviderError(
                "Builtin runtime requires the configured LLM gateway"
            )
        try:
            response = self.provider.generate(
                LLMRequest(
                    instructions=(
                        "Analyze only the supplied context. Produce text for these "
                        f"actions: {', '.join(request.allowed_actions)}. "
                        "You cannot read repositories or execute tools. "
                        "Any proposed patch is text for human review only."
                    ),
                    input=request.goal,
                    timeout_seconds=request.timeout_seconds,
                )
            )
        except LLMError:
            raise RuntimeProviderError("Builtin runtime LLM request failed") from None
        if not response.text.strip():
            raise RuntimeProviderError("Builtin runtime returned no analysis")
        log_event(
            get_logger("runtimes.builtin"),
            event="runtime_execute_completed",
            message="Builtin analysis completed",
            runtime=self.name,
            provider=response.provider,
            model=response.model,
        )
        return RuntimeResult(
            ok=True,
            summary=response.text,
            runtime=self.name,
            actions_taken=request.allowed_actions,
        )
