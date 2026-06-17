"""Configuration loading for signal-agent."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv


class ConfigError(ValueError):
    """Raised when application configuration is missing or malformed."""


@dataclass(slots=True)
class AgentConfig:
    timezone: str
    digest_time: str


@dataclass(slots=True)
class JobsSourceConfig:
    enabled: bool
    keywords: list[str]


@dataclass(slots=True)
class TwStocksSourceConfig:
    enabled: bool
    movers_limit: int


@dataclass(slots=True)
class SourcesConfig:
    jobs: JobsSourceConfig
    tw_stocks: TwStocksSourceConfig


@dataclass(slots=True)
class TelegramNotifierConfig:
    enabled: bool


@dataclass(slots=True)
class NotifiersConfig:
    telegram: TelegramNotifierConfig


@dataclass(slots=True)
class ScoringConfig:
    freshness_weight: float
    priority_weight: float


@dataclass(slots=True)
class AppConfig:
    agent: AgentConfig
    sources: SourcesConfig
    notifiers: NotifiersConfig
    scoring: ScoringConfig

    @classmethod
    def from_dict(
        cls,
        data: dict[str, Any],
        *,
        source_name: str = "config",
    ) -> AppConfig:
        agent = _require_mapping(data, "agent", source_name=source_name)
        sources = _require_mapping(data, "sources", source_name=source_name)
        jobs = _require_mapping(
            sources,
            "jobs",
            parent_path="sources",
            source_name=source_name,
        )
        tw_stocks = _require_mapping(
            sources,
            "tw_stocks",
            parent_path="sources",
            source_name=source_name,
        )
        notifiers = _require_mapping(data, "notifiers", source_name=source_name)
        telegram = _require_mapping(
            notifiers,
            "telegram",
            parent_path="notifiers",
            source_name=source_name,
        )
        scoring = _require_mapping(data, "scoring", source_name=source_name)

        return cls(
            agent=AgentConfig(
                timezone=_require_value(
                    agent,
                    "timezone",
                    parent_path="agent",
                    source_name=source_name,
                ),
                digest_time=_require_value(
                    agent,
                    "digest_time",
                    parent_path="agent",
                    source_name=source_name,
                ),
            ),
            sources=SourcesConfig(
                jobs=JobsSourceConfig(
                    enabled=_require_value(
                        jobs,
                        "enabled",
                        parent_path="sources.jobs",
                        source_name=source_name,
                    ),
                    keywords=_require_value(
                        jobs,
                        "keywords",
                        parent_path="sources.jobs",
                        source_name=source_name,
                    ),
                ),
                tw_stocks=TwStocksSourceConfig(
                    enabled=_require_value(
                        tw_stocks,
                        "enabled",
                        parent_path="sources.tw_stocks",
                        source_name=source_name,
                    ),
                    movers_limit=_require_value(
                        tw_stocks,
                        "movers_limit",
                        parent_path="sources.tw_stocks",
                        source_name=source_name,
                    ),
                ),
            ),
            notifiers=NotifiersConfig(
                telegram=TelegramNotifierConfig(
                    enabled=_require_value(
                        telegram,
                        "enabled",
                        parent_path="notifiers.telegram",
                        source_name=source_name,
                    )
                ),
            ),
            scoring=ScoringConfig(
                freshness_weight=_require_value(
                    scoring,
                    "freshness_weight",
                    parent_path="scoring",
                    source_name=source_name,
                ),
                priority_weight=_require_value(
                    scoring,
                    "priority_weight",
                    parent_path="scoring",
                    source_name=source_name,
                ),
            ),
        )


@dataclass(slots=True)
class EnvSettings:
    telegram_bot_token: str | None
    telegram_chat_id: str | None
    signal_agent_env: str


def _join_path(parent_path: str | None, key: str) -> str:
    if parent_path is None:
        return key
    return f"{parent_path}.{key}"


def _require_mapping(
    data: dict[str, Any],
    key: str,
    *,
    source_name: str,
    parent_path: str | None = None,
) -> dict[str, Any]:
    value = _require_value(
        data,
        key,
        parent_path=parent_path,
        source_name=source_name,
    )
    config_path = _join_path(parent_path, key)
    if not isinstance(value, dict):
        raise ConfigError(
            f"Config value '{config_path}' must be a mapping in {source_name}"
        )
    return value


def _require_value(
    data: dict[str, Any],
    key: str,
    *,
    source_name: str,
    parent_path: str | None = None,
) -> Any:
    config_path = _join_path(parent_path, key)
    if key not in data:
        raise ConfigError(
            f"Missing required config value '{config_path}' in {source_name}"
        )
    return data[key]


def load_config(path: str | Path = "config.yaml") -> AppConfig:
    """Load and validate the YAML application config."""

    config_path = Path(path)
    if not config_path.is_file():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    try:
        with config_path.open(encoding="utf-8") as config_file:
            data = yaml.safe_load(config_file)
    except yaml.YAMLError as exc:
        raise ConfigError(f"Invalid YAML in config file: {config_path}") from exc

    if not isinstance(data, dict):
        raise ConfigError(f"Config file must contain a mapping: {config_path}")

    return AppConfig.from_dict(data, source_name=str(config_path))


def load_env_settings(env_path: str | Path | None = ".env") -> EnvSettings:
    """Load environment variables used by notifier integrations."""

    if env_path is not None:
        load_dotenv(env_path)

    return EnvSettings(
        telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN") or None,
        telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID") or None,
        signal_agent_env=os.getenv("SIGNAL_AGENT_ENV", "development"),
    )
