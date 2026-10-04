"""Codex CLI adapter for supplied-context analysis, with no repository tools."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from app.core.logging import get_logger, log_event
from app.tools.permissions import PermissionPolicy

from .exceptions import RuntimeAuthError, RuntimeProviderError, RuntimeTimeoutError
from .guard import authorize
from .models import RuntimeRequest, RuntimeResult


class CodexRuntime:
    """Run a fixed Codex command in an empty, isolated, read-only workspace.

    No shell command strings, repository paths, user configs, MCP servers, or
    environment secrets other than the Codex credential are forwarded.
    """

    name = "codex"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        policy: PermissionPolicy | None = None,
    ) -> None:
        self._api_key = api_key
        self.model = model
        self.policy = policy

    def execute(self, request: RuntimeRequest) -> RuntimeResult:
        authorize(request, self.policy)
        if not self._api_key:
            raise RuntimeAuthError("Codex runtime requires its configured API key")
        executable = shutil.which("codex")
        if executable is None:
            raise RuntimeProviderError(
                "Install the Codex CLI to use runtime.default=codex"
            )
        # A fresh home prevents user plugins, hooks, auth, and config inheritance.
        with TemporaryDirectory(prefix="signal-agent-codex-") as directory:
            base = Path(directory)
            work = base / "work"
            work.mkdir()
            home = base / "home"
            home.mkdir()
            command = [
                executable,
                "exec",
                "--json",
                "--ephemeral",
                "--skip-git-repo-check",
                "--sandbox",
                "read-only",
                "-c",
                'approval_policy="never"',
                "-c",
                'web_search="disabled"',
                "-c",
                "features.shell_tool=false",
                "-c",
                "features.unified_exec=false",
                "-c",
                "features.apps=false",
                "-c",
                "features.multi_agent=false",
                "-c",
                "features.shell_snapshot=false",
                "-c",
                "features.apply_patch_freeform=false",
                "-c",
                'history.persistence="none"',
            ]
            if self.model:
                command.extend(["--model", self.model])
            command.append("-")
            child_env = {
                "PATH": os.environ.get("PATH", ""),
                "HOME": str(home),
                "CODEX_HOME": str(home / ".codex"),
                "CODEX_API_KEY": self._api_key,
                "TMPDIR": str(base),
            }
            prompt = (
                "Use only the supplied context; return analysis as text. "
                "No repository access, tools, commands, or file changes are allowed. "
                f"Requested analysis: {', '.join(request.allowed_actions)}.\n"
                + request.goal
            )
            try:
                completed = subprocess.run(
                    command,
                    input=prompt,
                    text=True,
                    capture_output=True,
                    cwd=work,
                    env=child_env,
                    timeout=request.timeout_seconds,
                    check=False,
                )
            except subprocess.TimeoutExpired:
                raise RuntimeTimeoutError("Codex runtime timed out") from None
            except OSError:
                raise RuntimeProviderError("Could not start Codex CLI") from None
            if completed.returncode != 0:
                # CLI output may include prompts or credentials: never expose it.
                raise RuntimeProviderError(
                    "Codex CLI failed; check installation, model, and credentials"
                )
            result = _parse_events(completed.stdout, request)
        log_event(
            get_logger("runtimes.codex"),
            event="runtime_execute_completed",
            message="Codex analysis completed",
            runtime=self.name,
            model=self.model,
        )
        return result


def _parse_events(output: str, request: RuntimeRequest) -> RuntimeResult:
    messages: list[str] = []
    completed = False
    for line in output.splitlines():
        try:
            event: Any = json.loads(line)
        except ValueError:
            raise RuntimeProviderError("Codex returned malformed events") from None
        if not isinstance(event, dict):
            raise RuntimeProviderError("Codex returned malformed events")
        kind = event.get("type")
        if kind not in {
            "thread.started",
            "turn.started",
            "turn.completed",
            "error",
            "turn.failed",
            "item.started",
            "item.updated",
            "item.completed",
        }:
            raise RuntimeProviderError("Codex returned an unknown event")
        if kind in {"error", "turn.failed"}:
            raise RuntimeProviderError("Codex reported execution failure")
        if kind == "turn.completed":
            completed = True
        if kind in {"item.started", "item.updated", "item.completed"}:
            item = event.get("item")
            if not isinstance(item, dict):
                raise RuntimeProviderError("Codex returned a malformed item")
            if item.get("type") not in {"agent_message", "reasoning"}:
                raise RuntimeProviderError("Codex reported an unexpected tool action")
            if kind == "item.completed" and item.get("type") == "agent_message":
                text = item.get("text")
                if not isinstance(text, str) or not text.strip():
                    raise RuntimeProviderError("Codex returned an empty analysis")
                messages.append(text)
    if not completed or not messages:
        raise RuntimeProviderError("Codex did not complete an analysis")
    return RuntimeResult(
        ok=True,
        summary="\n".join(messages),
        runtime="codex",
        actions_taken=request.allowed_actions,
    )
