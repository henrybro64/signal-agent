"""Configuration loading for signal-agent."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .secrets import EnvSettings as EnvSettings
from .secrets import load_env_settings as load_env_settings


class ConfigError(ValueError):
    """Raised when application configuration is missing or malformed."""


@dataclass(slots=True)
class AgentConfig:
    timezone: str
    digest_time: str


@dataclass(slots=True)
class JobSearchTaskConfig:
    enabled: bool
    sources: list[str]
    keywords: list[str]


@dataclass(slots=True)
class TwStockMoversTaskConfig:
    enabled: bool
    top_n: int
    timezone: str


@dataclass(slots=True)
class TasksConfig:
    job_search: JobSearchTaskConfig
    tw_stock_movers: TwStockMoversTaskConfig


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
    llm_enabled: bool = False


@dataclass(slots=True)
class AppConfig:
    agent: AgentConfig
    tasks: TasksConfig
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
        tasks = _require_mapping(data, "tasks", source_name=source_name)
        job_search = _require_mapping(
            tasks,
            "job_search",
            parent_path="tasks",
            source_name=source_name,
        )
        tw_stock_movers = _require_mapping(
            tasks,
            "tw_stock_movers",
            parent_path="tasks",
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
                timezone=_require_str(
                    agent,
                    "timezone",
                    parent_path="agent",
                    source_name=source_name,
                ),
                digest_time=_require_str(
                    agent,
                    "digest_time",
                    parent_path="agent",
                    source_name=source_name,
                ),
            ),
            tasks=TasksConfig(
                job_search=JobSearchTaskConfig(
                    enabled=_require_bool(
                        job_search,
                        "enabled",
                        parent_path="tasks.job_search",
                        source_name=source_name,
                    ),
                    sources=_require_str_list(
                        job_search,
                        "sources",
                        parent_path="tasks.job_search",
                        source_name=source_name,
                    ),
                    keywords=_require_str_list(
                        job_search,
                        "keywords",
                        parent_path="tasks.job_search",
                        source_name=source_name,
                    ),
                ),
                tw_stock_movers=TwStockMoversTaskConfig(
                    enabled=_require_bool(
                        tw_stock_movers,
                        "enabled",
                        parent_path="tasks.tw_stock_movers",
                        source_name=source_name,
                    ),
                    top_n=_require_int(
                        tw_stock_movers,
                        "top_n",
                        parent_path="tasks.tw_stock_movers",
                        source_name=source_name,
                    ),
                    timezone=_require_str(
                        tw_stock_movers,
                        "timezone",
                        parent_path="tasks.tw_stock_movers",
                        source_name=source_name,
                    ),
                ),
            ),
            notifiers=NotifiersConfig(
                telegram=TelegramNotifierConfig(
                    enabled=_require_bool(
                        telegram,
                        "enabled",
                        parent_path="notifiers.telegram",
                        source_name=source_name,
                    )
                ),
            ),
            scoring=ScoringConfig(
                freshness_weight=_require_float(
                    scoring,
                    "freshness_weight",
                    parent_path="scoring",
                    source_name=source_name,
                ),
                priority_weight=_require_float(
                    scoring,
                    "priority_weight",
                    parent_path="scoring",
                    source_name=source_name,
                ),
                llm_enabled=_optional_bool(
                    scoring,
                    "llm_enabled",
                    default=False,
                    parent_path="scoring",
                    source_name=source_name,
                ),
            ),
        )


def _join_path(parent_path: str | None, key: str) -> str:
    if parent_path is None:
        return key
    return f"{parent_path}.{key}"


def _config_path(parent_path: str | None, key: str) -> str:
    return _join_path(parent_path, key)


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
    config_path = _config_path(parent_path, key)
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
    config_path = _config_path(parent_path, key)
    if key not in data:
        raise ConfigError(
            f"Missing required config value '{config_path}' in {source_name}"
        )
    return data[key]


def _require_bool(
    data: dict[str, Any],
    key: str,
    *,
    source_name: str,
    parent_path: str | None = None,
) -> bool:
    value = _require_value(
        data,
        key,
        parent_path=parent_path,
        source_name=source_name,
    )
    config_path = _config_path(parent_path, key)
    if not isinstance(value, bool):
        raise ConfigError(
            f"Config value '{config_path}' must be a boolean in {source_name}"
        )
    return value


def _optional_bool(
    data: dict[str, Any],
    key: str,
    *,
    default: bool,
    source_name: str,
    parent_path: str | None = None,
) -> bool:
    if key not in data:
        return default
    return _require_bool(
        data,
        key,
        parent_path=parent_path,
        source_name=source_name,
    )


def _require_str(
    data: dict[str, Any],
    key: str,
    *,
    source_name: str,
    parent_path: str | None = None,
) -> str:
    value = _require_value(
        data,
        key,
        parent_path=parent_path,
        source_name=source_name,
    )
    config_path = _config_path(parent_path, key)
    if not isinstance(value, str):
        raise ConfigError(
            f"Config value '{config_path}' must be a string in {source_name}"
        )
    return value


def _require_int(
    data: dict[str, Any],
    key: str,
    *,
    source_name: str,
    parent_path: str | None = None,
) -> int:
    value = _require_value(
        data,
        key,
        parent_path=parent_path,
        source_name=source_name,
    )
    config_path = _config_path(parent_path, key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ConfigError(
            f"Config value '{config_path}' must be an integer in {source_name}"
        )
    return value


def _require_float(
    data: dict[str, Any],
    key: str,
    *,
    source_name: str,
    parent_path: str | None = None,
) -> float:
    value = _require_value(
        data,
        key,
        parent_path=parent_path,
        source_name=source_name,
    )
    config_path = _config_path(parent_path, key)
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ConfigError(
            f"Config value '{config_path}' must be a number in {source_name}"
        )
    return float(value)


def _require_str_list(
    data: dict[str, Any],
    key: str,
    *,
    source_name: str,
    parent_path: str | None = None,
) -> list[str]:
    value = _require_value(
        data,
        key,
        parent_path=parent_path,
        source_name=source_name,
    )
    config_path = _config_path(parent_path, key)
    if not isinstance(value, list):
        raise ConfigError(
            f"Config value '{config_path}' must be a list in {source_name}"
        )
    for index, item in enumerate(value):
        if not isinstance(item, str):
            raise ConfigError(
                "Config value "
                f"'{config_path}[{index}]' must be a string in {source_name}"
            )
    return value


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
