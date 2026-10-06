"""Secret-loading helpers for local and deployment environments."""

from __future__ import annotations

import os
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .config import ProvidersConfig

from dotenv import load_dotenv


class SecretError(ValueError):
    """Raised when required secret configuration is missing."""


@dataclass(slots=True)
class EnvSettings:
    openai_api_key: str | None = field(default=None, repr=False)
    anthropic_api_key: str | None = field(default=None, repr=False)
    perplexity_api_key: str | None = field(default=None, repr=False)
    codex_api_key: str | None = field(default=None, repr=False)
    telegram_bot_token: str | None = field(default=None, repr=False)
    telegram_chat_id: str | None = field(default=None, repr=False)
    signal_agent_env: str = "development"
    linkedin_email_source_path: str | None = None
    linkedin_email_imap_host: str | None = None
    linkedin_email_imap_username: str | None = field(default=None, repr=False)
    linkedin_email_imap_password: str | None = field(default=None, repr=False)
    linkedin_email_imap_mailbox: str = "INBOX"
    extra_secrets: tuple[str, ...] = field(default=(), repr=False)


def load_env_settings(env_path: str | Path | None = ".env") -> EnvSettings:
    """Load secrets from the process environment and optional dotenv file."""

    if env_path is not None:
        load_dotenv(env_path)

    return EnvSettings(
        openai_api_key=os.getenv("OPENAI_API_KEY") or None,
        anthropic_api_key=os.getenv("ANTHROPIC_API_KEY") or None,
        perplexity_api_key=os.getenv("PERPLEXITY_API_KEY") or None,
        codex_api_key=os.getenv("CODEX_API_KEY") or None,
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


DEFAULT_SECRET_ENV_KEYS = frozenset(
    {
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "PERPLEXITY_API_KEY",
        "CODEX_API_KEY",
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_CHAT_ID",
        "LINKEDIN_EMAIL_IMAP_USERNAME",
        "LINKEDIN_EMAIL_IMAP_PASSWORD",
    }
)


def secret_env_keys(providers: ProvidersConfig | None = None) -> frozenset[str]:
    names = set(DEFAULT_SECRET_ENV_KEYS)
    if providers is not None:
        for name in ("openai", "anthropic", "perplexity", "codex"):
            names.add(getattr(providers, name).api_key_env)
    return frozenset(names)


def secret_values(env: EnvSettings) -> tuple[str, ...]:
    """One redaction inventory for alerts, tools, chat, logs, and diagnostics."""
    values = [
        getattr(env, name)
        for name in (
            "openai_api_key",
            "anthropic_api_key",
            "perplexity_api_key",
            "codex_api_key",
            "telegram_bot_token",
            "telegram_chat_id",
            "linkedin_email_imap_username",
            "linkedin_email_imap_password",
        )
    ]
    return tuple(
        sorted({v for v in (*values, *env.extra_secrets) if v}, key=len, reverse=True)
    )


def resolve_provider_credentials(
    env: EnvSettings,
    providers: ProvidersConfig,
) -> EnvSettings:
    """Resolve explicit aliases without falling back to another credential."""

    def resolve(name: str, env_name: str, current: str | None) -> str | None:
        if env_name == f"{name.upper()}_API_KEY":
            return current
        return os.getenv(env_name) or None

    return replace(
        env,
        openai_api_key=resolve(
            "openai", providers.openai.api_key_env, env.openai_api_key
        ),
        anthropic_api_key=resolve(
            "anthropic", providers.anthropic.api_key_env, env.anthropic_api_key
        ),
        perplexity_api_key=resolve(
            "perplexity", providers.perplexity.api_key_env, env.perplexity_api_key
        ),
        codex_api_key=resolve("codex", providers.codex.api_key_env, env.codex_api_key),
        extra_secrets=secret_values(env),
    )


def has_openai_api_key(env: EnvSettings) -> bool:
    """Return whether an OpenAI API key is configured."""

    return env.openai_api_key is not None


def has_anthropic_api_key(env: EnvSettings) -> bool:
    """Return whether an Anthropic API key is configured."""

    return env.anthropic_api_key is not None


def has_perplexity_api_key(env: EnvSettings) -> bool:
    """Return whether a Perplexity API key is configured."""

    return env.perplexity_api_key is not None


def llm_api_keys_from_env(env: EnvSettings) -> dict[str, str | None]:
    """Map LLM provider names to credentials for the shared gateway/router."""

    return {
        "openai": env.openai_api_key,
        "anthropic": env.anthropic_api_key,
        "fake": None,
    }


# Canonical credential env var per LLM provider, for operator-facing errors.
LLM_PROVIDER_ENV_KEYS: dict[str, str] = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
}


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
    fallback_providers: Sequence[str] = (),
) -> None:
    """Ensure the configured provider chain has at least one usable credential.

    The offline ``fake`` gateway provider does not need cloud API keys. A
    credentialed fallback also satisfies this check: the router walks the whole
    chain, so demanding the primary key would reject work a fallback can serve.
    """

    if not llm_enabled:
        return
    chain = [
        name.strip().lower()
        for name in ((provider or "openai"), *fallback_providers)
        if name and name.strip()
    ]
    keys = llm_api_keys_from_env(env)
    unknown = [name for name in chain if name not in keys]
    if unknown:
        raise SecretError(
            f"Unsupported llm.provider {unknown[0]!r} for LLM scoring credentials"
        )
    if any(name == "fake" or keys.get(name) for name in chain):
        return
    ordered = list(dict.fromkeys(chain))
    if len(ordered) == 1:
        raise SecretError(
            f"{LLM_PROVIDER_ENV_KEYS[ordered[0]]} is required when "
            f"scoring.llm_enabled is true and llm.provider is {ordered[0]!r}"
        )
    required = ", ".join(LLM_PROVIDER_ENV_KEYS[name] for name in ordered)
    raise SecretError(
        "No LLM credentials configured for the provider chain "
        f"{' -> '.join(ordered)} when scoring.llm_enabled is true; "
        f"set one of: {required}"
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
