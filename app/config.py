"""Configuration loading for signal-agent."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv


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
    def from_dict(cls, data: dict[str, Any]) -> AppConfig:
        return cls(
            agent=AgentConfig(**data["agent"]),
            sources=SourcesConfig(
                jobs=JobsSourceConfig(**data["sources"]["jobs"]),
                tw_stocks=TwStocksSourceConfig(**data["sources"]["tw_stocks"]),
            ),
            notifiers=NotifiersConfig(
                telegram=TelegramNotifierConfig(
                    **data["notifiers"]["telegram"]
                ),
            ),
            scoring=ScoringConfig(**data["scoring"]),
        )


@dataclass(slots=True)
class EnvSettings:
    telegram_bot_token: str | None
    telegram_chat_id: str | None
    signal_agent_env: str


def load_config(path: str | Path) -> AppConfig:
    """Load and validate the YAML application config."""

    config_path = Path(path)
    if not config_path.is_file():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    with config_path.open(encoding="utf-8") as config_file:
        data = yaml.safe_load(config_file)

    if not isinstance(data, dict):
        raise ValueError(f"Config file must contain a mapping: {config_path}")

    return AppConfig.from_dict(data)


def load_env_settings(env_path: str | Path | None = ".env") -> EnvSettings:
    """Load environment variables used by notifier integrations."""

    if env_path is not None:
        load_dotenv(env_path)

    return EnvSettings(
        telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN") or None,
        telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID") or None,
        signal_agent_env=os.getenv("SIGNAL_AGENT_ENV", "development"),
    )
