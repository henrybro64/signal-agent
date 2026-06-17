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
    openai_api_key: str | None
    telegram_bot_token: str | None
    telegram_chat_id: str | None
    signal_agent_env: str


def load_env_settings(env_path: str | Path | None = ".env") -> EnvSettings:
    """Load secrets from the process environment and optional dotenv file."""

    if env_path is not None:
        load_dotenv(env_path)

    return EnvSettings(
        openai_api_key=os.getenv("OPENAI_API_KEY") or None,
        telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN") or None,
        telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID") or None,
        signal_agent_env=os.getenv("SIGNAL_AGENT_ENV", "development"),
    )


def has_openai_api_key(env: EnvSettings) -> bool:
    """Return whether an OpenAI API key is configured."""

    return env.openai_api_key is not None


def has_telegram_credentials(env: EnvSettings) -> bool:
    """Return whether both Telegram secrets are configured."""

    return bool(env.telegram_bot_token and env.telegram_chat_id)


def validate_llm_scoring_secrets(
    *,
    llm_enabled: bool,
    env: EnvSettings,
) -> None:
    """Ensure OpenAI credentials exist before enabling LLM-backed scoring."""

    if llm_enabled and not has_openai_api_key(env):
        raise SecretError(
            "OPENAI_API_KEY is required when scoring.llm_enabled is true"
        )
