from pathlib import Path

import pytest

from app.core.secrets import (
    EnvSettings,
    SecretError,
    has_openai_api_key,
    has_telegram_credentials,
    load_env_settings,
    validate_llm_scoring_secrets,
)


def test_load_env_settings_reads_openai_and_telegram_variables(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token-123")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "chat-456")

    env = load_env_settings(env_path=None)

    assert env.openai_api_key == "sk-test"
    assert env.telegram_bot_token == "token-123"
    assert env.telegram_chat_id == "chat-456"


def test_load_env_settings_reads_openai_and_telegram_from_dotenv(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text(
        "\n".join(
            [
                "OPENAI_API_KEY=sk-dotenv",
                "TELEGRAM_BOT_TOKEN=dotenv-token",
                "TELEGRAM_CHAT_ID=dotenv-chat",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)

    env = load_env_settings(env_path=env_path)

    assert env.openai_api_key == "sk-dotenv"
    assert env.telegram_bot_token == "dotenv-token"
    assert env.telegram_chat_id == "dotenv-chat"


def test_load_env_settings_treats_blank_secret_values_as_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "")

    env = load_env_settings(env_path=None)

    assert env.openai_api_key is None
    assert env.telegram_bot_token is None
    assert env.telegram_chat_id is None


def test_secret_helpers_report_available_credentials() -> None:
    env = EnvSettings(
        openai_api_key="sk-test",
        telegram_bot_token="token-123",
        telegram_chat_id="chat-456",
        signal_agent_env="test",
    )

    assert has_openai_api_key(env) is True
    assert has_telegram_credentials(env) is True


def test_validate_llm_scoring_secrets_requires_openai_key() -> None:
    env = EnvSettings(
        openai_api_key=None,
        telegram_bot_token="token-123",
        telegram_chat_id="chat-456",
        signal_agent_env="test",
    )

    with pytest.raises(
        SecretError,
        match="OPENAI_API_KEY is required when scoring.llm_enabled is true",
    ):
        validate_llm_scoring_secrets(llm_enabled=True, env=env)


def test_validate_llm_scoring_secrets_allows_disabled_llm_without_key() -> None:
    env = EnvSettings(
        openai_api_key=None,
        telegram_bot_token=None,
        telegram_chat_id=None,
        signal_agent_env="test",
    )

    validate_llm_scoring_secrets(llm_enabled=False, env=env)
