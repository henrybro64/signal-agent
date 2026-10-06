"""All new credentials and configured aliases share the existing redaction paths."""

from pathlib import Path

import yaml

from app.core.config import load_config
from app.core.error_handling import summarize_error
from app.core.secrets import EnvSettings, resolve_provider_credentials, secret_values
from app.runtime.logs import collect_secrets, read_log_tail
from app.setup.wizard import show_config_redacted
from app.tools import ToolContext, ToolRegistry, ToolResult
from app.tools.permissions import PermissionClass


def configure(tmp_path, monkeypatch):
    payload = yaml.safe_load(Path("config.yaml").read_text())
    payload["providers"] = {"anthropic": {"api_key_env": "TEAM_AI_CREDENTIAL"}}
    cfg = tmp_path / "config.yaml"
    cfg.write_text(yaml.safe_dump(payload))
    keys = {
        "ANTHROPIC_API_KEY": "synthetic-anthropic",
        "PERPLEXITY_API_KEY": "synthetic-perplexity",
        "CODEX_API_KEY": "synthetic-codex",
        "TEAM_AI_CREDENTIAL": "synthetic-alias",
    }
    (tmp_path / ".env").write_text("\n".join(f"{k}={v}" for k, v in keys.items()))
    for key, value in keys.items():
        monkeypatch.setenv(key, value)
    return cfg, keys


def test_config_show_and_log_tail_redact_all_new_keys(tmp_path, monkeypatch):
    cfg, keys = configure(tmp_path, monkeypatch)
    output = show_config_redacted(root=tmp_path, config_path=cfg)
    for key, value in keys.items():
        assert f"{key}=***" in output
        assert value not in output
    logs = tmp_path / "log.txt"
    logs.write_text(" ".join(keys.values()))
    output = "\n".join(read_log_tail(root=tmp_path, path=logs))
    assert all(value not in output for value in keys.values())
    assert set(keys.values()) <= set(collect_secrets(tmp_path))


def test_failure_summary_redacts_alias_and_default_values(tmp_path, monkeypatch):
    cfg, keys = configure(tmp_path, monkeypatch)
    env = resolve_provider_credentials(
        EnvSettings(
            anthropic_api_key=keys["ANTHROPIC_API_KEY"],
            perplexity_api_key=keys["PERPLEXITY_API_KEY"],
            codex_api_key=keys["CODEX_API_KEY"],
        ),
        load_config(cfg).providers,
    )
    assert set(keys.values()) <= set(secret_values(env))
    summary = summarize_error(RuntimeError(" ".join(keys.values())), env=env)
    assert all(value not in summary for value in keys.values())


def test_registry_collects_secrets_even_if_caller_omits_inventory(
    tmp_path, monkeypatch
):
    cfg, keys = configure(tmp_path, monkeypatch)

    class EchoTool:
        name = "echo"
        description = "test"
        permission = PermissionClass.READ
        input_schema = {"type": "object"}

        @staticmethod
        def invoke(arguments, *, context):
            return ToolResult(ok=True, data={"text": " ".join(keys.values())})

    context = ToolContext(
        config=load_config(cfg),
        env=EnvSettings(
            anthropic_api_key=keys["ANTHROPIC_API_KEY"],
            perplexity_api_key=keys["PERPLEXITY_API_KEY"],
            codex_api_key=keys["CODEX_API_KEY"],
        ),
    )
    result = ToolRegistry(tools={"echo": EchoTool()}, allowlist=["echo"]).invoke(
        "echo", context=context
    )
    assert result.ok and all(value not in str(result.data) for value in keys.values())


def test_status_reports_selected_vendor_credential(tmp_path, monkeypatch):
    from app.runtime import collect_status, format_status_report

    cfg, _ = configure(tmp_path, monkeypatch)
    payload = yaml.safe_load(cfg.read_text())
    payload["llm"] = {"provider": "anthropic", "model": "configured"}
    payload["scoring"].pop("llm_model", None)
    cfg.write_text(yaml.safe_dump(payload))
    monkeypatch.setenv("OPENAI_API_KEY", "")
    report = collect_status(root=tmp_path)
    assert report.llm_key_configured and not report.openai_key_configured
    assert "model=configured api_key=set" in format_status_report(report)
