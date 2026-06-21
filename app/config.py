"""Backward-compatible re-exports for configuration loading."""

from app.core.config import (
    AgentConfig,
    AppConfig,
    ConfigError,
    JobSearchTaskConfig,
    NotifiersConfig,
    ScoringConfig,
    TasksConfig,
    TelegramNotifierConfig,
    TwStockMoversTaskConfig,
    load_config,
)
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

__all__ = [
    "AgentConfig",
    "AppConfig",
    "ConfigError",
    "EnvSettings",
    "JobSearchTaskConfig",
    "NotifiersConfig",
    "ScoringConfig",
    "SecretError",
    "TasksConfig",
    "TelegramNotifierConfig",
    "TwStockMoversTaskConfig",
    "has_linkedin_email_source_config",
    "has_openai_api_key",
    "has_telegram_credentials",
    "load_config",
    "load_env_settings",
    "validate_linkedin_email_source_secrets",
    "validate_llm_scoring_secrets",
    "validate_telegram_notifier_secrets",
]
