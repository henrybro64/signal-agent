"""Secret-loading helpers for local and deployment environments."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


class SecretError(ValueError):
    """Raised when required secret configuration is missing."""


@dataclass(slots=True)
class EnvSettings:
    openai_api_key: str | None = None
    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None
    signal_agent_env: str = "development"
    linkedin_email_source_path: str | None = None
    linkedin_email_imap_host: str | None = None
    linkedin_email_imap_username: str | None = None
    linkedin_email_imap_password: str | None = None
    linkedin_email_imap_mailbox: str = "INBOX"


def load_env_settings(env_path: str | Path | None = ".env") -> EnvSettings:
    """Load secrets from the process environment and optional dotenv file."""

    if env_path is not None:
        load_dotenv(env_path)

    return EnvSettings(
        openai_api_key=os.getenv("OPENAI_API_KEY") or None,
        telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN") or None,
        telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID") or None,
        signal_agent_env=os.getenv("SIGNAL_AGENT_ENV", "development"),
        linkedin_email_source_path=os.getenv("LINKEDIN_EMAIL_SOURCE_PATH") or None,
        linkedin_email_imap_host=os.getenv("LINKEDIN_EMAIL_IMAP_HOST") or None,
        linkedin_email_imap_username=(
            os.getenv("LINKEDIN_EMAIL_IMAP_USERNAME") or None
        ),
        linkedin_email_imap_password=(
            os.getenv("LINKEDIN_EMAIL_IMAP_PASSWORD") or None
        ),
        linkedin_email_imap_mailbox=(
            os.getenv("LINKEDIN_EMAIL_IMAP_MAILBOX") or "INBOX"
        ).strip()
        or "INBOX",
    )


def has_openai_api_key(env: EnvSettings) -> bool:
    """Return whether an OpenAI API key is configured."""

    return env.openai_api_key is not None


def has_telegram_credentials(env: EnvSettings) -> bool:
    """Return whether both Telegram secrets are configured."""

    return bool(env.telegram_bot_token and env.telegram_chat_id)


def has_linkedin_email_source_config(env: EnvSettings) -> bool:
    """Return whether LinkedIn email source settings are usable."""

    if env.linkedin_email_source_path is not None:
        return True

    return bool(
        env.linkedin_email_imap_host
        and env.linkedin_email_imap_username
        and env.linkedin_email_imap_password
    )


def validate_llm_scoring_secrets(
    *,
    llm_enabled: bool,
    env: EnvSettings,
    provider: str | None = None,
) -> None:
    """Ensure OpenAI credentials exist before enabling LLM-backed scoring.

    The offline ``fake`` gateway provider does not need ``OPENAI_API_KEY``.
    """

    if not llm_enabled:
        return
    if (provider or "openai").strip().lower() == "fake":
        return
    if not has_openai_api_key(env):
        raise SecretError(
            "OPENAI_API_KEY is required when scoring.llm_enabled is true "
            "and llm.provider is not 'fake'"
        )



def validate_telegram_notifier_secrets(
    *,
    telegram_enabled: bool,
    env: EnvSettings,
) -> None:
    """Ensure Telegram credentials exist before enabling Telegram notifier."""

    if telegram_enabled and not has_telegram_credentials(env):
        raise SecretError(
            "TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are required "
            "when notifiers.telegram.enabled is true"
        )


def validate_linkedin_email_source_secrets(env: EnvSettings) -> None:
    """Ensure LinkedIn email source config is present before using it."""

    if has_linkedin_email_source_config(env):
        return

    raise SecretError(
        "LinkedIn email source requires LINKEDIN_EMAIL_SOURCE_PATH "
        "or IMAP credentials: LINKEDIN_EMAIL_IMAP_HOST, "
        "LINKEDIN_EMAIL_IMAP_USERNAME, and LINKEDIN_EMAIL_IMAP_PASSWORD"
    )
