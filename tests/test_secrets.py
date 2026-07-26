from pathlib import Path

import pytest

from app.core.secrets import (
    EnvSettings,
    SecretError,
    has_linkedin_email_source_config,
    has_openai_api_key,
    has_telegram_credentials,
    load_env_settings,
    validate_linkedin_email_source_secrets,
    validate_llm_scoring_secrets,
    validate_telegram_notifier_secrets,
)


def test_load_env_settings_reads_openai_and_telegram_variables(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token-123")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "chat-456")
    monkeypatch.setenv("LINKEDIN_EMAIL_SOURCE_PATH", "/tmp/linkedin.eml")

    env = load_env_settings(env_path=None)

    assert env.openai_api_key == "sk-test"
    assert env.telegram_bot_token == "token-123"
    assert env.telegram_chat_id == "chat-456"
    assert env.linkedin_email_source_path == "/tmp/linkedin.eml"


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
                "LINKEDIN_EMAIL_IMAP_HOST=imap.gmail.com",
                "LINKEDIN_EMAIL_IMAP_USERNAME=user@example.com",
                "LINKEDIN_EMAIL_IMAP_PASSWORD=app-password",
                "LINKEDIN_EMAIL_IMAP_MAILBOX=LinkedIn",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    monkeypatch.delenv("LINKEDIN_EMAIL_IMAP_HOST", raising=False)
    monkeypatch.delenv("LINKEDIN_EMAIL_IMAP_USERNAME", raising=False)
    monkeypatch.delenv("LINKEDIN_EMAIL_IMAP_PASSWORD", raising=False)
    monkeypatch.delenv("LINKEDIN_EMAIL_IMAP_MAILBOX", raising=False)

    env = load_env_settings(env_path=env_path)

    assert env.openai_api_key == "sk-dotenv"
    assert env.telegram_bot_token == "dotenv-token"
    assert env.telegram_chat_id == "dotenv-chat"
    assert env.linkedin_email_imap_host == "imap.gmail.com"
    assert env.linkedin_email_imap_username == "user@example.com"
    assert env.linkedin_email_imap_password == "app-password"
    assert env.linkedin_email_imap_mailbox == "LinkedIn"


def test_load_env_settings_treats_blank_secret_values_as_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "")
    monkeypatch.setenv("LINKEDIN_EMAIL_SOURCE_PATH", "")
    monkeypatch.setenv("LINKEDIN_EMAIL_IMAP_HOST", "")
    monkeypatch.setenv("LINKEDIN_EMAIL_IMAP_USERNAME", "")
    monkeypatch.setenv("LINKEDIN_EMAIL_IMAP_PASSWORD", "")

    env = load_env_settings(env_path=None)

    assert env.openai_api_key is None
    assert env.telegram_bot_token is None
    assert env.telegram_chat_id is None
    assert env.linkedin_email_source_path is None
    assert env.linkedin_email_imap_host is None
    assert env.linkedin_email_imap_username is None
    assert env.linkedin_email_imap_password is None


def test_secret_helpers_report_available_credentials() -> None:
    env = EnvSettings(
        openai_api_key="sk-test",
        telegram_bot_token="token-123",
        telegram_chat_id="chat-456",
        signal_agent_env="test",
        linkedin_email_source_path="/tmp/linkedin.eml",
    )

    assert has_openai_api_key(env) is True
    assert has_telegram_credentials(env) is True
    assert has_linkedin_email_source_config(env) is True


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


def test_validate_llm_scoring_secrets_allows_fake_provider_without_key() -> None:
    env = EnvSettings(
        openai_api_key=None,
        telegram_bot_token=None,
        telegram_chat_id=None,
        signal_agent_env="test",
    )

    validate_llm_scoring_secrets(
        llm_enabled=True,
        env=env,
        provider="fake",
    )


def test_validate_llm_scoring_secrets_allows_disabled_llm_without_key() -> None:
    env = EnvSettings(
        openai_api_key=None,
        telegram_bot_token=None,
        telegram_chat_id=None,
        signal_agent_env="test",
    )

    validate_llm_scoring_secrets(llm_enabled=False, env=env)



def test_validate_telegram_notifier_secrets_requires_telegram_credentials() -> None:
    env = EnvSettings(
        openai_api_key="sk-test",
        telegram_bot_token=None,
        telegram_chat_id=None,
        signal_agent_env="test",
    )

    with pytest.raises(
        SecretError,
        match=(
            "TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are required "
            "when notifiers.telegram.enabled is true"
        ),
    ):
        validate_telegram_notifier_secrets(telegram_enabled=True, env=env)


def test_validate_telegram_notifier_secrets_allows_disabled_notifier() -> None:
    env = EnvSettings(
        openai_api_key="sk-test",
        telegram_bot_token=None,
        telegram_chat_id=None,
        signal_agent_env="test",
    )

    validate_telegram_notifier_secrets(telegram_enabled=False, env=env)


def test_validate_linkedin_email_source_secrets_accepts_local_path() -> None:
    env = EnvSettings(
        linkedin_email_source_path="/tmp/linkedin.eml",
    )

    validate_linkedin_email_source_secrets(env)


def test_validate_linkedin_email_source_secrets_accepts_imap_credentials() -> None:
    env = EnvSettings(
        linkedin_email_imap_host="imap.gmail.com",
        linkedin_email_imap_username="user@example.com",
        linkedin_email_imap_password="app-password",
    )

    validate_linkedin_email_source_secrets(env)


def test_validate_linkedin_email_source_secrets_requires_configuration() -> None:
    env = EnvSettings()

    with pytest.raises(
        SecretError,
        match="LinkedIn email source requires LINKEDIN_EMAIL_SOURCE_PATH",
    ):
        validate_linkedin_email_source_secrets(env)
