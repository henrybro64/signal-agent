"""Configuration loading for signal-agent."""

from __future__ import annotations

from dataclasses import dataclass, field
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
    locations: list[str] = field(default_factory=list)


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
    failure_alerts_enabled: bool


@dataclass(slots=True)
class NotifiersConfig:
    telegram: TelegramNotifierConfig


@dataclass(slots=True)
class ScoringConfig:
    freshness_weight: float
    priority_weight: float
    llm_enabled: bool = False
    llm_model: str = "gpt-4o-mini"


@dataclass(slots=True)
class JobHttpSourceConfig:
    timeout_seconds: float = 10.0
    max_retries: int = 2
    request_delay_seconds: float = 1.0
    page_limit: int = 1


@dataclass(slots=True)
class LinkedInEmailSourceConfig:
    lookback_days: int = 14
    imap_mailbox: str = "INBOX"


@dataclass(slots=True)
class TwStockHttpSourceConfig:
    timeout_seconds: float = 10.0
    max_retries: int = 2
    request_delay_seconds: float = 0.2
    batch_size: int = 100


@dataclass(slots=True)
class SourcesConfig:
    job104: JobHttpSourceConfig = field(default_factory=JobHttpSourceConfig)
    indeed: JobHttpSourceConfig = field(default_factory=JobHttpSourceConfig)
    glassdoor: JobHttpSourceConfig = field(default_factory=JobHttpSourceConfig)
    linkedin_email: LinkedInEmailSourceConfig = field(
        default_factory=LinkedInEmailSourceConfig
    )
    twse: TwStockHttpSourceConfig = field(default_factory=TwStockHttpSourceConfig)
    tpex: TwStockHttpSourceConfig = field(default_factory=TwStockHttpSourceConfig)


@dataclass(slots=True)
class AppConfig:
    agent: AgentConfig
    tasks: TasksConfig
    notifiers: NotifiersConfig
    scoring: ScoringConfig
    sources: SourcesConfig = field(default_factory=SourcesConfig)

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
        telegram_enabled = _require_bool(
            telegram,
            "enabled",
            parent_path="notifiers.telegram",
            source_name=source_name,
        )
        scoring = _require_mapping(data, "scoring", source_name=source_name)
        sources = _optional_mapping(
            data,
            "sources",
            source_name=source_name,
        )

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
                    locations=_optional_str_list(
                        job_search,
                        "locations",
                        default=[],
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
                    enabled=telegram_enabled,
                    failure_alerts_enabled=_optional_bool(
                        telegram,
                        "failure_alerts_enabled",
                        default=telegram_enabled,
                        parent_path="notifiers.telegram",
                        source_name=source_name,
                    ),
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
                llm_model=_optional_str(
                    scoring,
                    "llm_model",
                    default="gpt-4o-mini",
                    parent_path="scoring",
                    source_name=source_name,
                ),
            ),
            sources=_parse_sources_config(sources, source_name=source_name),
        )


def _parse_sources_config(
    data: dict[str, Any],
    *,
    source_name: str,
) -> SourcesConfig:
    return SourcesConfig(
        job104=_parse_job_http_source_config(
            _optional_mapping(
                data,
                "job104",
                parent_path="sources",
                source_name=source_name,
            ),
            parent_path="sources.job104",
            source_name=source_name,
            default_request_delay_seconds=1.0,
        ),
        indeed=_parse_job_http_source_config(
            _optional_mapping(
                data,
                "indeed",
                parent_path="sources",
                source_name=source_name,
            ),
            parent_path="sources.indeed",
            source_name=source_name,
            default_request_delay_seconds=1.0,
        ),
        glassdoor=_parse_job_http_source_config(
            _optional_mapping(
                data,
                "glassdoor",
                parent_path="sources",
                source_name=source_name,
            ),
            parent_path="sources.glassdoor",
            source_name=source_name,
            default_request_delay_seconds=1.0,
        ),
        linkedin_email=_parse_linkedin_email_source_config(
            _optional_mapping(
                data,
                "linkedin_email",
                parent_path="sources",
                source_name=source_name,
            ),
            source_name=source_name,
        ),
        twse=_parse_tw_stock_http_source_config(
            _optional_mapping(
                data,
                "twse",
                parent_path="sources",
                source_name=source_name,
            ),
            parent_path="sources.twse",
            source_name=source_name,
        ),
        tpex=_parse_tw_stock_http_source_config(
            _optional_mapping(
                data,
                "tpex",
                parent_path="sources",
                source_name=source_name,
            ),
            parent_path="sources.tpex",
            source_name=source_name,
        ),
    )


def _parse_job_http_source_config(
    data: dict[str, Any],
    *,
    parent_path: str,
    source_name: str,
    default_request_delay_seconds: float,
) -> JobHttpSourceConfig:
    timeout_seconds = _optional_float(
        data,
        "timeout_seconds",
        default=10.0,
        parent_path=parent_path,
        source_name=source_name,
    )
    max_retries = _optional_int(
        data,
        "max_retries",
        default=2,
        parent_path=parent_path,
        source_name=source_name,
    )
    request_delay_seconds = _optional_float(
        data,
        "request_delay_seconds",
        default=default_request_delay_seconds,
        parent_path=parent_path,
        source_name=source_name,
    )
    page_limit = _optional_int(
        data,
        "page_limit",
        default=1,
        parent_path=parent_path,
        source_name=source_name,
    )
    _require_positive_number(
        timeout_seconds,
        config_path=f"{parent_path}.timeout_seconds",
        source_name=source_name,
    )
    _require_non_negative_int(
        max_retries,
        config_path=f"{parent_path}.max_retries",
        source_name=source_name,
    )
    _require_non_negative_number(
        request_delay_seconds,
        config_path=f"{parent_path}.request_delay_seconds",
        source_name=source_name,
    )
    _require_positive_int(
        page_limit,
        config_path=f"{parent_path}.page_limit",
        source_name=source_name,
    )
    return JobHttpSourceConfig(
        timeout_seconds=timeout_seconds,
        max_retries=max_retries,
        request_delay_seconds=request_delay_seconds,
        page_limit=page_limit,
    )


def _parse_linkedin_email_source_config(
    data: dict[str, Any],
    *,
    source_name: str,
) -> LinkedInEmailSourceConfig:
    parent_path = "sources.linkedin_email"
    lookback_days = _optional_int(
        data,
        "lookback_days",
        default=14,
        parent_path=parent_path,
        source_name=source_name,
    )
    imap_mailbox = _optional_str(
        data,
        "imap_mailbox",
        default="INBOX",
        parent_path=parent_path,
        source_name=source_name,
    ).strip()
    _require_positive_int(
        lookback_days,
        config_path=f"{parent_path}.lookback_days",
        source_name=source_name,
    )
    if not imap_mailbox:
        raise ConfigError(
            f"Config value '{parent_path}.imap_mailbox' must be a "
            f"non-empty string in {source_name}"
        )
    return LinkedInEmailSourceConfig(
        lookback_days=lookback_days,
        imap_mailbox=imap_mailbox,
    )


def _parse_tw_stock_http_source_config(
    data: dict[str, Any],
    *,
    parent_path: str,
    source_name: str,
) -> TwStockHttpSourceConfig:
    timeout_seconds = _optional_float(
        data,
        "timeout_seconds",
        default=10.0,
        parent_path=parent_path,
        source_name=source_name,
    )
    max_retries = _optional_int(
        data,
        "max_retries",
        default=2,
        parent_path=parent_path,
        source_name=source_name,
    )
    request_delay_seconds = _optional_float(
        data,
        "request_delay_seconds",
        default=0.2,
        parent_path=parent_path,
        source_name=source_name,
    )
    batch_size = _optional_int(
        data,
        "batch_size",
        default=100,
        parent_path=parent_path,
        source_name=source_name,
    )
    _require_positive_number(
        timeout_seconds,
        config_path=f"{parent_path}.timeout_seconds",
        source_name=source_name,
    )
    _require_non_negative_int(
        max_retries,
        config_path=f"{parent_path}.max_retries",
        source_name=source_name,
    )
    _require_non_negative_number(
        request_delay_seconds,
        config_path=f"{parent_path}.request_delay_seconds",
        source_name=source_name,
    )
    _require_positive_int(
        batch_size,
        config_path=f"{parent_path}.batch_size",
        source_name=source_name,
    )
    return TwStockHttpSourceConfig(
        timeout_seconds=timeout_seconds,
        max_retries=max_retries,
        request_delay_seconds=request_delay_seconds,
        batch_size=batch_size,
    )


def _optional_mapping(
    data: dict[str, Any],
    key: str,
    *,
    source_name: str,
    parent_path: str | None = None,
) -> dict[str, Any]:
    if key not in data:
        return {}
    return _require_mapping(
        data,
        key,
        parent_path=parent_path,
        source_name=source_name,
    )


def _optional_int(
    data: dict[str, Any],
    key: str,
    *,
    default: int,
    source_name: str,
    parent_path: str | None = None,
) -> int:
    if key not in data:
        return default
    return _require_int(
        data,
        key,
        parent_path=parent_path,
        source_name=source_name,
    )


def _optional_float(
    data: dict[str, Any],
    key: str,
    *,
    default: float,
    source_name: str,
    parent_path: str | None = None,
) -> float:
    if key not in data:
        return default
    return _require_float(
        data,
        key,
        parent_path=parent_path,
        source_name=source_name,
    )


def _require_positive_number(
    value: float,
    *,
    config_path: str,
    source_name: str,
) -> None:
    if value <= 0:
        raise ConfigError(
            f"Config value '{config_path}' must be greater than zero "
            f"in {source_name}"
        )


def _require_non_negative_number(
    value: float,
    *,
    config_path: str,
    source_name: str,
) -> None:
    if value < 0:
        raise ConfigError(
            f"Config value '{config_path}' must be zero or greater "
            f"in {source_name}"
        )


def _require_positive_int(
    value: int,
    *,
    config_path: str,
    source_name: str,
) -> None:
    if value <= 0:
        raise ConfigError(
            f"Config value '{config_path}' must be greater than zero "
            f"in {source_name}"
        )


def _require_non_negative_int(
    value: int,
    *,
    config_path: str,
    source_name: str,
) -> None:
    if value < 0:
        raise ConfigError(
            f"Config value '{config_path}' must be zero or greater "
            f"in {source_name}"
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


def _optional_str(
    data: dict[str, Any],
    key: str,
    *,
    default: str,
    source_name: str,
    parent_path: str | None = None,
) -> str:
    if key not in data:
        return default
    return _require_str(
        data,
        key,
        parent_path=parent_path,
        source_name=source_name,
    )


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


def _optional_str_list(
    data: dict[str, Any],
    key: str,
    *,
    default: list[str],
    source_name: str,
    parent_path: str | None = None,
) -> list[str]:
    if key not in data:
        return list(default)
    return _require_str_list(
        data,
        key,
        parent_path=parent_path,
        source_name=source_name,
    )


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
