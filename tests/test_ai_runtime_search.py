"""Offline search/runtime integration and fail-closed runtime boundaries."""

import json
import subprocess
from unittest.mock import Mock

import pytest
import requests
import yaml

from app.cli import main
from app.core.config import load_config
from app.runtimes import RuntimeRequest, create_agent_runtime
from app.runtimes.codex import CodexRuntime
from app.runtimes.exceptions import (
    RuntimePermissionError,
    RuntimeProviderError,
    RuntimeTimeoutError,
)
from app.search import SearchRequest, create_search_provider
from app.search.exceptions import (
    SearchAuthError,
    SearchProviderError,
    SearchRateLimitError,
    SearchTimeoutError,
)
from app.skills import disable_skill, enable_skill
from app.tools import ToolContext, build_tool_registry
from app.tools.permissions import (
    PermissionPolicyConfig,
    build_permission_policy,
    push_permission_policy,
    reset_permission_policy,
)


def policy(tmp_path, action="allow", **kwargs):
    return build_permission_policy(
        PermissionPolicyConfig(
            tools={"runtime_execute": action},
            audit_log_path=str(tmp_path / "audit.jsonl"),
        ),
        **kwargs,
    )


def codex_events():
    return "\n".join(
        json.dumps(row)
        for row in [
            {
                "type": "item.completed",
                "item": {"type": "agent_message", "text": "Analysis"},
            },
            {"type": "turn.completed"},
        ]
    )


@pytest.mark.parametrize("name", ["builtin", "codex", "fake"])
def test_runtime_selection_and_default_deny(name):
    runtime = create_agent_runtime(runtime=name)
    assert runtime.name == name
    with pytest.raises(RuntimePermissionError):
        runtime.execute(RuntimeRequest(goal="test"))


def test_unknown_adapters():
    with pytest.raises(RuntimeProviderError, match="Unknown"):
        create_agent_runtime(runtime="invalid")
    with pytest.raises(SearchProviderError, match="Unknown"):
        create_search_provider(provider="invalid")


def test_codex_active_policy_denies_before_process(monkeypatch, tmp_path):
    run = Mock()
    monkeypatch.setattr("app.runtimes.codex.subprocess.run", run)
    runtime = CodexRuntime(api_key="test", policy=policy(tmp_path))
    token = push_permission_policy(policy(tmp_path, "disabled"))
    try:
        with pytest.raises(RuntimePermissionError):
            runtime.execute(RuntimeRequest(goal="test"))
    finally:
        reset_permission_policy(token)
    run.assert_not_called()
    assert json.loads((tmp_path / "audit.jsonl").read_text())["allowed"] is False


def test_codex_command_is_fixed_and_context_is_stdin(monkeypatch, tmp_path):
    run = Mock(return_value=subprocess.CompletedProcess([], 0, codex_events(), ""))
    monkeypatch.setattr("app.runtimes.codex.subprocess.run", run)
    monkeypatch.setattr("app.runtimes.codex.shutil.which", lambda _: "/test/codex")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "must-not-inherit")
    result = CodexRuntime(api_key="test", policy=policy(tmp_path)).execute(
        RuntimeRequest(goal="$(touch secret); analyze this code", timeout_seconds=2)
    )
    assert result.ok and result.summary == "Analysis"
    command = run.call_args.args[0]
    args = run.call_args.kwargs
    assert command[command.index("--sandbox") + 1] == "read-only"
    assert (
        "features.shell_tool=false" in command
        and "features.unified_exec=false" in command
    )
    assert "touch secret" not in str(command)
    assert "touch secret" in args["input"]
    assert args["timeout"] == 2
    assert "ANTHROPIC_API_KEY" not in args["env"]
    assert not args.get("shell", False)


@pytest.mark.parametrize(
    "output,code",
    [
        ('{"type":"turn.failed"}', 0),
        ('{"type":"error"}', 0),
        ("{}", 0),
        ("not json", 0),
        (codex_events(), 1),
        (
            json.dumps(
                {"type": "item.completed", "item": {"type": "command_execution"}}
            ),
            0,
        ),
    ],
)
def test_codex_rejects_failure_or_unexpected_action(
    monkeypatch, tmp_path, output, code
):
    monkeypatch.setattr("app.runtimes.codex.shutil.which", lambda _: "/test/codex")
    monkeypatch.setattr(
        "app.runtimes.codex.subprocess.run",
        Mock(return_value=subprocess.CompletedProcess([], code, output, "secret")),
    )
    with pytest.raises(RuntimeProviderError) as error:
        CodexRuntime(api_key="test", policy=policy(tmp_path)).execute(
            RuntimeRequest(goal="test")
        )
    assert "secret" not in str(error.value)


def test_codex_timeout_is_normalized(monkeypatch, tmp_path):
    monkeypatch.setattr("app.runtimes.codex.shutil.which", lambda _: "/test/codex")
    monkeypatch.setattr(
        "app.runtimes.codex.subprocess.run",
        Mock(side_effect=subprocess.TimeoutExpired("codex", 1)),
    )
    with pytest.raises(RuntimeTimeoutError):
        CodexRuntime(api_key="test", policy=policy(tmp_path)).execute(
            RuntimeRequest(goal="test")
        )


@pytest.mark.parametrize(
    "runtime_request",
    [
        RuntimeRequest(goal="test", allowed_actions=("shell",)),
        RuntimeRequest(goal="test", working_directory="/"),
        RuntimeRequest(goal="test", timeout_seconds=0),
    ],
)
def test_runtime_request_restrictions(tmp_path, runtime_request):
    with pytest.raises((RuntimeProviderError, RuntimePermissionError)):
        create_agent_runtime(runtime="fake", policy=policy(tmp_path)).execute(
            runtime_request
        )


def test_runtime_ask_and_budget(tmp_path):
    approvals = Mock(return_value=True)
    runtime = create_agent_runtime(
        runtime="fake",
        policy=policy(tmp_path, "ask", interactive=True, approve=approvals),
    )
    for _ in range(3):
        assert runtime.execute(RuntimeRequest(goal="test")).ok
    with pytest.raises(RuntimePermissionError, match="limit"):
        runtime.execute(RuntimeRequest(goal="test"))
    assert approvals.call_count == 3


@pytest.mark.parametrize(
    "status,error",
    [(401, SearchAuthError), (429, SearchRateLimitError), (500, SearchProviderError)],
)
def test_search_errors(status, error):
    session = Mock()
    session.post.return_value.status_code = status
    with pytest.raises(error):
        create_search_provider(
            provider="perplexity", api_key="test", session=session
        ).search(SearchRequest("test"))


def test_search_timeout_and_citations():
    session = Mock()
    session.post.side_effect = requests.Timeout
    provider = create_search_provider(
        provider="perplexity", api_key="test", session=session
    )
    with pytest.raises(SearchTimeoutError):
        provider.search(SearchRequest("test"))
    session.post.side_effect = None
    session.post.return_value.status_code = 200
    session.post.return_value.json.return_value = {
        "choices": [{"message": {"content": "answer"}}],
        "citations": ["https://fixture.invalid/a", "https://fixture.invalid/b"],
    }
    response = provider.search(SearchRequest("test", max_results=1))
    assert response.answer == "answer" and len(response.results) == 1


def write_config(tmp_path):
    payload = yaml.safe_load(open("config.yaml"))
    payload["llm"] = {"provider": "fake", "model": "offline"}
    payload["scoring"].pop("llm_model", None)
    payload["runtime"] = {"default": "builtin"}
    payload.setdefault("tools", {}).setdefault("permissions", {})["tools"] = {
        "runtime_execute": "allow"
    }
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(payload))
    return path


def test_builtin_runtime_cli_performs_generation(tmp_path, capsys):
    write_config(tmp_path)
    assert main(["--root", str(tmp_path), "agent-run", "Analyze selected code"]) == 0
    assert '{"ok": true}' in capsys.readouterr().out


def test_research_skill_enables_configured_search(tmp_path):
    path = write_config(tmp_path)
    enable_skill("research", root=tmp_path, config_path=path)
    registry = build_tool_registry(root=tmp_path, config_path=path)
    assert "research_web" in registry.allowlist
    result = registry.invoke(
        "research_web", {"query": "test"}, context=ToolContext(config=load_config(path))
    )
    assert result.ok and result.metadata["provider"] == "fake"
    disable_skill("research", root=tmp_path, config_path=path)
    assert (
        "research_web"
        not in build_tool_registry(root=tmp_path, config_path=path).allowlist
    )
