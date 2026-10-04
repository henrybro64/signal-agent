"""Single factory for bounded agent runtimes."""

from __future__ import annotations

from app.llm import LLMProvider
from app.tools.permissions import PermissionPolicy

from .base import AgentRuntime
from .builtin import BuiltinAgentRuntime
from .codex import CodexRuntime
from .exceptions import RuntimeProviderError
from .fake import FakeAgentRuntime


def create_agent_runtime(
    *,
    runtime: str = "builtin",
    api_key: str | None = None,
    model: str | None = None,
    provider: LLMProvider | None = None,
    policy: PermissionPolicy | None = None,
) -> AgentRuntime:
    name = runtime.strip().lower()
    if name == "fake":
        return FakeAgentRuntime(policy=policy)
    if name == "codex":
        return CodexRuntime(api_key=api_key, model=model, policy=policy)
    if name == "builtin":
        return BuiltinAgentRuntime(provider=provider, policy=policy)
    raise RuntimeProviderError(
        f"Unknown runtime {name!r}; supported: builtin, codex, fake"
    )


def supported_runtimes() -> frozenset[str]:
    return frozenset({"builtin", "codex", "fake"})
