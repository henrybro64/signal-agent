"""Tests for first-run setup and doctor (#81)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from app.cli import main
from app.setup import (
    SetupOptions,
    detect_browser,
    run_setup,
    show_config_redacted,
    validate_local_setup,
)


def test_setup_fresh_non_interactive(tmp_path: Path) -> None:
    result = run_setup(
        root=tmp_path,
        options=SetupOptions(
            provider="fake",
            model="gpt-4o-mini",
            llm_enabled=False,
            non_interactive=True,
            force=True,
        ),
    )
    assert result.config_path.is_file()
    assert result.env_path.is_file()
    assert result.db_path.is_file()
    assert result.validation.ok
    payload = yaml.safe_load(result.config_path.read_text(encoding="utf-8"))
    assert payload["llm"]["provider"] == "fake"


def test_setup_rerun_preserves_secrets(tmp_path: Path) -> None:
    first = run_setup(
        root=tmp_path,
        options=SetupOptions(
            provider="fake",
            model="gpt-4o-mini",
            openai_api_key="sk-secret-keep",
            non_interactive=True,
            force=True,
        ),
    )
    assert first.env_path.read_text(encoding="utf-8").count("sk-secret-keep") == 1
    # Add a comment that must survive non-force reruns.
    first.env_path.write_text(
        "# keep me\n" + first.env_path.read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    second = run_setup(
        root=tmp_path,
        options=SetupOptions(
            provider="fake",
            model="gpt-4o-mini",
            openai_api_key="",
            non_interactive=True,
            force=False,
        ),
    )
    env_text = second.env_path.read_text(encoding="utf-8")
    assert "sk-secret-keep" in env_text
    assert "# keep me" in env_text


def test_setup_rejects_empty_model(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="model must be"):
        run_setup(
            root=tmp_path,
            options=SetupOptions(
                provider="fake",
                model="   ",
                non_interactive=True,
                force=True,
            ),
        )


def test_setup_force_backs_up_config(tmp_path: Path) -> None:
    first = run_setup(
        root=tmp_path,
        options=SetupOptions(provider="fake", non_interactive=True, force=True),
    )
    first.config_path.write_text(
        first.config_path.read_text(encoding="utf-8") + "\n# custom note\n",
        encoding="utf-8",
    )
    run_setup(
        root=tmp_path,
        options=SetupOptions(provider="fake", non_interactive=True, force=True),
    )
    backup = tmp_path / "data" / "runtime" / "backups" / "config.yaml.bak"
    assert backup.is_file()
    assert "# custom note" in backup.read_text(encoding="utf-8")


def test_setup_force_replaces_malformed_yaml(tmp_path: Path) -> None:
    run_setup(
        root=tmp_path,
        options=SetupOptions(provider="fake", non_interactive=True, force=True),
    )
    (tmp_path / "config.yaml").write_text("llm: [\n: broken\n", encoding="utf-8")
    result = run_setup(
        root=tmp_path,
        options=SetupOptions(provider="fake", non_interactive=True, force=True),
    )
    assert result.validation.ok
    payload = yaml.safe_load(result.config_path.read_text(encoding="utf-8"))
    assert payload["llm"]["provider"] == "fake"


def test_doctor_fails_when_openai_key_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # load_dotenv() leaks keys into os.environ, so clear before asserting.
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    run_setup(
        root=tmp_path,
        options=SetupOptions(
            provider="openai",
            llm_enabled=False,
            openai_api_key="",
            non_interactive=True,
            force=True,
        ),
    )
    report = validate_local_setup(root=tmp_path)
    assert not report.ok
    assert any(issue.code == "openai_key_missing" for issue in report.issues)


def test_setup_rejects_invalid_provider(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Unsupported provider"):
        run_setup(
            root=tmp_path,
            options=SetupOptions(
                provider="not-a-provider",
                non_interactive=True,
                force=True,
            ),
        )


def test_config_show_redacts_secrets(tmp_path: Path) -> None:
    run_setup(
        root=tmp_path,
        options=SetupOptions(
            provider="fake",
            openai_api_key="sk-should-redact",
            telegram_bot_token="bot-secret",
            telegram_chat_id="chat-secret",
            non_interactive=True,
            force=True,
        ),
    )
    shown = show_config_redacted(root=tmp_path)
    assert "sk-should-redact" not in shown
    assert "bot-secret" not in shown
    assert "chat-secret" not in shown
    assert "OPENAI_API_KEY=***" in shown


def test_doctor_and_setup_cli(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    code = main(
        [
            "setup",
            "--root",
            str(tmp_path),
            "--non-interactive",
            "--provider",
            "fake",
            "--force",
        ]
    )
    assert code == 0
    doctor = main(
        [
            "doctor",
            "--root",
            str(tmp_path),
            "--config",
            str(tmp_path / "config.yaml"),
        ]
    )
    assert doctor == 0


def test_validate_warns_when_optional_deps_missing(tmp_path: Path) -> None:
    run_setup(
        root=tmp_path,
        options=SetupOptions(provider="fake", non_interactive=True, force=True),
    )
    report = validate_local_setup(root=tmp_path)
    assert report.ok
    codes = {issue.code for issue in report.issues}
    assert "browser" in codes


def test_detect_browser_override(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fake = tmp_path / "chrome"
    fake.write_text("#!/bin/sh\n", encoding="utf-8")
    fake.chmod(0o755)
    monkeypatch.setenv("SIGNAL_AGENT_BROWSER_PATH", str(fake))
    detection = detect_browser()
    assert detection.ready
    assert detection.executable == str(fake)


def test_setup_collects_anthropic_credentials(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Selecting anthropic must persist its key and a Claude default model."""

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    result = run_setup(
        root=tmp_path,
        options=SetupOptions(
            provider="anthropic",
            anthropic_api_key="sk-ant-secret",
            llm_enabled=True,
            non_interactive=True,
            force=True,
        ),
    )

    payload = yaml.safe_load(result.config_path.read_text(encoding="utf-8"))
    assert payload["llm"]["provider"] == "anthropic"
    assert payload["llm"]["model"] == "claude-sonnet-4-5"
    assert "ANTHROPIC_API_KEY=sk-ant-secret" in result.env_path.read_text(
        encoding="utf-8"
    )
    assert result.validation.ok


def test_doctor_fails_when_fallback_provider_key_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    run_setup(
        root=tmp_path,
        options=SetupOptions(
            provider="openai",
            openai_api_key="sk-primary",
            non_interactive=True,
            force=True,
        ),
    )
    cfg_path = tmp_path / "config.yaml"
    payload = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    payload["llm"]["fallbacks"] = ["anthropic/claude-sonnet-4-5"]
    cfg_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    report = validate_local_setup(root=tmp_path)

    assert not report.ok
    assert any(
        issue.code == "anthropic_key_missing" and "fallback" in issue.message
        for issue in report.issues
    )
