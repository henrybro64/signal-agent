"""Configuration loading for signal-agent."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import yaml

from .secrets import EnvSettings as EnvSettings
from .secrets import load_env_settings as load_env_settings


class ConfigError(ValueError):
    """Raised when application configuration is missing or malformed."""


def apply_optional_overrides[TSourceSettings](
    settings: TSourceSettings,
    **overrides: object,
) -> TSourceSettings:
    """Return dataclass settings with any non-None overrides applied."""

    return replace(
        settings,  # type: ignore[arg-type]
        **{key: value for key, value in overrides.items() if value is not None},
    )


@dataclass(slots=True)
class AgentConfig:
    timezone: str
    digest_time: str


@dataclass(slots=True)
class JobSearchProfileConfig:
    """One named job-search profile with its own sources and keywords."""

    name: str
    sources: list[str]
    keywords: list[str]
    locations: list[str] = field(default_factory=list)
    scorer_profile: str | None = None


@dataclass(slots=True)
class JobSearchTaskConfig:
    enabled: bool
    sources: list[str]
    keywords: list[str]
    locations: list[str] = field(default_factory=list)
    profiles: list[JobSearchProfileConfig] = field(default_factory=list)


@dataclass(slots=True)
class TwStockMoversTaskConfig:
    enabled: bool
    top_n: int
    timezone: str


@dataclass(slots=True)
class StockWatchlistSymbolConfig:
    code: str
    name: str = ""
    alert_change_percent_abs: float = 3.0


@dataclass(slots=True)
class StockWatchlistThemeConfig:
    symbols: list[str] = field(default_factory=list)


@dataclass(slots=True)
class StockWatchlistTaskConfig:
    enabled: bool
    timezone: str
    sources: list[str] = field(default_factory=list)
    symbols: list[StockWatchlistSymbolConfig] = field(default_factory=list)
    themes: dict[str, StockWatchlistThemeConfig] = field(default_factory=dict)


@dataclass(slots=True)
class TasksConfig:
    job_search: JobSearchTaskConfig
    tw_stock_movers: TwStockMoversTaskConfig
    stock_watchlist: StockWatchlistTaskConfig


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
    llm_min_rule_score: int = 25
    llm_max_jobs_per_run: int = 20
    digest_max_strong_matches: int = 10
    digest_max_maybe_matches: int = 10
    include_rejected_summary: bool = True


@dataclass(slots=True)
class JobHttpSourceConfig:
    timeout_seconds: float = 10.0
    max_retries: int = 2
    request_delay_seconds: float = 1.0
    page_limit: int = 1


@dataclass(slots=True)
class LinkedInEmailSourceConfig:
    lookback_days: int = 14
    # None means "omit from config" so from_config can fall back to
    # LINKEDIN_EMAIL_IMAP_MAILBOX (default INBOX).
    imap_mailbox: str | None = None


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
        stock_watchlist = _optional_mapping(
            tasks,
            "stock_watchlist",
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
                job_search=_parse_job_search_task_config(
                    job_search,
                    source_name=source_name,
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
                stock_watchlist=_parse_stock_watchlist_task_config(
                    stock_watchlist,
                    source_name=source_name,
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
                llm_min_rule_score=_parse_scoring_llm_min_rule_score(
                    scoring,
                    source_name=source_name,
                ),
                llm_max_jobs_per_run=_parse_scoring_positive_int(
                    scoring,
                    key="llm_max_jobs_per_run",
                    default=20,
                    source_name=source_name,
                ),
                digest_max_strong_matches=_parse_scoring_positive_int(
                    scoring,
                    key="digest_max_strong_matches",
                    default=10,
                    source_name=source_name,
                ),
                digest_max_maybe_matches=_parse_scoring_positive_int(
                    scoring,
                    key="digest_max_maybe_matches",
                    default=10,
                    source_name=source_name,
                ),
                include_rejected_summary=_optional_bool(
                    scoring,
                    "include_rejected_summary",
                    default=True,
                    parent_path="scoring",
                    source_name=source_name,
                ),
            ),
            sources=_parse_sources_config(sources, source_name=source_name),
        )


_DEFAULT_STOCK_WATCHLIST_SOURCES: tuple[str, ...] = ("twse", "tpex")
_SUPPORTED_STOCK_WATCHLIST_SOURCES = frozenset(_DEFAULT_STOCK_WATCHLIST_SOURCES)


def _parse_stock_watchlist_task_config(
    data: dict[str, Any],
    *,
    source_name: str,
) -> StockWatchlistTaskConfig:
    """Parse ``tasks.stock_watchlist`` (optional; defaults keep fixtures working)."""

    parent_path = "tasks.stock_watchlist"
    if not data:
        return StockWatchlistTaskConfig(
            enabled=False,
            timezone="Asia/Taipei",
            sources=list(_DEFAULT_STOCK_WATCHLIST_SOURCES),
            symbols=[],
            themes={},
        )

    enabled = _require_bool(
        data,
        "enabled",
        parent_path=parent_path,
        source_name=source_name,
    )
    timezone = _require_str(
        data,
        "timezone",
        parent_path=parent_path,
        source_name=source_name,
    )
    if "sources" in data:
        sources = _require_str_list(
            data,
            "sources",
            parent_path=parent_path,
            source_name=source_name,
        )
        if not sources:
            raise ConfigError(
                f"Config value '{parent_path}.sources' must not be empty "
                f"in {source_name}"
            )
    else:
        sources = list(_DEFAULT_STOCK_WATCHLIST_SOURCES)

    unsupported = sorted(
        {
            name
            for name in sources
            if name not in _SUPPORTED_STOCK_WATCHLIST_SOURCES
        }
    )
    if unsupported:
        supported = ", ".join(sorted(_SUPPORTED_STOCK_WATCHLIST_SOURCES))
        raise ConfigError(
            f"Unsupported stock_watchlist source(s) {unsupported} in "
            f"{source_name}. Supported sources: {supported}"
        )

    symbols = _parse_stock_watchlist_symbols(
        data.get("symbols"),
        parent_path=f"{parent_path}.symbols",
        source_name=source_name,
    )
    themes = _parse_stock_watchlist_themes(
        data.get("themes"),
        parent_path=f"{parent_path}.themes",
        source_name=source_name,
    )
    return StockWatchlistTaskConfig(
        enabled=enabled,
        timezone=timezone,
        sources=sources,
        symbols=symbols,
        themes=themes,
    )


def _parse_stock_watchlist_symbols(
    raw_symbols: Any,
    *,
    parent_path: str,
    source_name: str,
) -> list[StockWatchlistSymbolConfig]:
    if raw_symbols is None:
        return []
    if not isinstance(raw_symbols, list):
        raise ConfigError(
            f"Config value '{parent_path}' must be a list in {source_name}"
        )

    symbols: list[StockWatchlistSymbolConfig] = []
    seen_codes: set[str] = set()
    for index, entry in enumerate(raw_symbols):
        entry_path = f"{parent_path}[{index}]"
        if not isinstance(entry, dict):
            raise ConfigError(
                f"Config value '{entry_path}' must be a mapping in {source_name}"
            )
        code = _require_str(
            entry,
            "code",
            parent_path=entry_path,
            source_name=source_name,
        ).strip()
        if not code:
            raise ConfigError(
                f"Config value '{entry_path}.code' must not be blank "
                f"in {source_name}"
            )
        if code in seen_codes:
            raise ConfigError(
                f"Duplicate stock_watchlist symbol code '{code}' in "
                f"{source_name}"
            )
        seen_codes.add(code)

        name = ""
        if "name" in entry:
            name = _require_str(
                entry,
                "name",
                parent_path=entry_path,
                source_name=source_name,
            ).strip()

        threshold = _require_float(
            entry,
            "alert_change_percent_abs",
            parent_path=entry_path,
            source_name=source_name,
        )
        _require_positive_number(
            threshold,
            config_path=f"{entry_path}.alert_change_percent_abs",
            source_name=source_name,
        )
        symbols.append(
            StockWatchlistSymbolConfig(
                code=code,
                name=name,
                alert_change_percent_abs=threshold,
            )
        )
    return symbols


def _parse_stock_watchlist_themes(
    raw_themes: Any,
    *,
    parent_path: str,
    source_name: str,
) -> dict[str, StockWatchlistThemeConfig]:
    if raw_themes is None:
        return {}
    if not isinstance(raw_themes, dict):
        raise ConfigError(
            f"Config value '{parent_path}' must be a mapping in {source_name}"
        )

    themes: dict[str, StockWatchlistThemeConfig] = {}
    for theme_name, theme_value in raw_themes.items():
        if not isinstance(theme_name, str) or not theme_name.strip():
            raise ConfigError(
                f"Theme names under '{parent_path}' must be non-empty "
                f"strings in {source_name}"
            )
        theme_path = f"{parent_path}.{theme_name.strip()}"
        if not isinstance(theme_value, dict):
            raise ConfigError(
                f"Config value '{theme_path}' must be a mapping in {source_name}"
            )
        theme_symbols = _require_str_list(
            theme_value,
            "symbols",
            parent_path=theme_path,
            source_name=source_name,
        )
        if not theme_symbols:
            raise ConfigError(
                f"Config value '{theme_path}.symbols' must not be empty "
                f"in {source_name}"
            )
        themes[theme_name.strip()] = StockWatchlistThemeConfig(
            symbols=[code.strip() for code in theme_symbols if code.strip()]
        )
        if not themes[theme_name.strip()].symbols:
            raise ConfigError(
                f"Config value '{theme_path}.symbols' must not be empty "
                f"in {source_name}"
            )
    return themes


def _parse_scoring_llm_min_rule_score(
    data: dict[str, Any],
    *,
    source_name: str,
) -> int:
    parent_path = "scoring"
    value = _optional_int(
        data,
        "llm_min_rule_score",
        default=25,
        parent_path=parent_path,
        source_name=source_name,
    )
    _require_non_negative_int(
        value,
        config_path=f"{parent_path}.llm_min_rule_score",
        source_name=source_name,
    )
    if value > 100:
        raise ConfigError(
            f"Config value '{parent_path}.llm_min_rule_score' must be "
            f"100 or less in {source_name}"
        )
    return value


def _parse_scoring_positive_int(
    data: dict[str, Any],
    *,
    key: str,
    default: int,
    source_name: str,
) -> int:
    parent_path = "scoring"
    value = _optional_int(
        data,
        key,
        default=default,
        parent_path=parent_path,
        source_name=source_name,
    )
    _require_positive_int(
        value,
        config_path=f"{parent_path}.{key}",
        source_name=source_name,
    )
    return value


def _parse_job_search_task_config(
    data: dict[str, Any],
    *,
    source_name: str,
) -> JobSearchTaskConfig:
    parent_path = "tasks.job_search"
    enabled = _require_bool(
        data,
        "enabled",
        parent_path=parent_path,
        source_name=source_name,
    )
    raw_profiles = data.get("profiles")
    if raw_profiles is not None:
        profiles = _parse_job_search_profiles(
            raw_profiles,
            parent_path=f"{parent_path}.profiles",
            source_name=source_name,
        )
        if not profiles:
            raise ConfigError(
                f"Config value '{parent_path}.profiles' must contain at least "
                f"one profile in {source_name}"
            )
        sources = _unique_preserve_order(
            source
            for profile in profiles
            for source in profile.sources
        )
        # Keep legacy top-level fields populated for fetch/helpers that still
        # read tasks.job_search.sources/keywords/locations.
        keywords = list(profiles[0].keywords)
        locations = list(profiles[0].locations)
        return JobSearchTaskConfig(
            enabled=enabled,
            sources=sources,
            keywords=keywords,
            locations=locations,
            profiles=profiles,
        )

    sources = _require_str_list(
        data,
        "sources",
        parent_path=parent_path,
        source_name=source_name,
    )
    keywords = _require_str_list(
        data,
        "keywords",
        parent_path=parent_path,
        source_name=source_name,
    )
    locations = _optional_str_list(
        data,
        "locations",
        default=[],
        parent_path=parent_path,
        source_name=source_name,
    )
    return JobSearchTaskConfig(
        enabled=enabled,
        sources=sources,
        keywords=keywords,
        locations=locations,
        profiles=[
            JobSearchProfileConfig(
                name="default",
                sources=list(sources),
                keywords=list(keywords),
                locations=list(locations),
            )
        ],
    )


def _parse_job_search_profiles(
    raw_profiles: object,
    *,
    parent_path: str,
    source_name: str,
) -> list[JobSearchProfileConfig]:
    if not isinstance(raw_profiles, list):
        raise ConfigError(
            f"Config value '{parent_path}' must be a list in {source_name}"
        )

    profiles: list[JobSearchProfileConfig] = []
    seen_names: set[str] = set()
    for index, entry in enumerate(raw_profiles):
        entry_path = f"{parent_path}[{index}]"
        if not isinstance(entry, dict):
            raise ConfigError(
                f"Config value '{entry_path}' must be a mapping in {source_name}"
            )
        name = _require_str(
            entry,
            "name",
            parent_path=entry_path,
            source_name=source_name,
        ).strip()
        if not name:
            raise ConfigError(
                f"Config value '{entry_path}.name' must be a "
                f"non-empty string in {source_name}"
            )
        if name in seen_names:
            raise ConfigError(
                f"Duplicate job-search profile name '{name}' "
                f"in {parent_path} of {source_name}"
            )
        seen_names.add(name)
        scorer_profile = None
        if "scorer_profile" in entry:
            scorer_profile = _require_str(
                entry,
                "scorer_profile",
                parent_path=entry_path,
                source_name=source_name,
            ).strip() or None
        profiles.append(
            JobSearchProfileConfig(
                name=name,
                sources=_require_str_list(
                    entry,
                    "sources",
                    parent_path=entry_path,
                    source_name=source_name,
                ),
                keywords=_require_str_list(
                    entry,
                    "keywords",
                    parent_path=entry_path,
                    source_name=source_name,
                ),
                locations=_optional_str_list(
                    entry,
                    "locations",
                    default=[],
                    parent_path=entry_path,
                    source_name=source_name,
                ),
                scorer_profile=scorer_profile,
            )
        )
    return profiles


def _unique_preserve_order(values: Any) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(str(value))
        ordered.append(str(value))
    return ordered


def _parse_sources_config(
    data: dict[str, Any],
    *,
    source_name: str,
) -> SourcesConfig:
    def job_http(key: str) -> JobHttpSourceConfig:
        return _parse_job_http_source_config(
            _optional_mapping(
                data,
                key,
                parent_path="sources",
                source_name=source_name,
            ),
            parent_path=f"sources.{key}",
            source_name=source_name,
        )

    def tw_stock_http(key: str) -> TwStockHttpSourceConfig:
        return _parse_tw_stock_http_source_config(
            _optional_mapping(
                data,
                key,
                parent_path="sources",
                source_name=source_name,
            ),
            parent_path=f"sources.{key}",
            source_name=source_name,
        )

    return SourcesConfig(
        job104=job_http("job104"),
        indeed=job_http("indeed"),
        glassdoor=job_http("glassdoor"),
        linkedin_email=_parse_linkedin_email_source_config(
            _optional_mapping(
                data,
                "linkedin_email",
                parent_path="sources",
                source_name=source_name,
            ),
            source_name=source_name,
        ),
        twse=tw_stock_http("twse"),
        tpex=tw_stock_http("tpex"),
    )


def _parse_http_runtime_settings(
    data: dict[str, Any],
    *,
    parent_path: str,
    source_name: str,
    timeout_seconds: float,
    max_retries: int,
    request_delay_seconds: float,
) -> tuple[float, int, float]:
    parsed_timeout = _optional_float(
        data,
        "timeout_seconds",
        default=timeout_seconds,
        parent_path=parent_path,
        source_name=source_name,
    )
    parsed_retries = _optional_int(
        data,
        "max_retries",
        default=max_retries,
        parent_path=parent_path,
        source_name=source_name,
    )
    parsed_delay = _optional_float(
        data,
        "request_delay_seconds",
        default=request_delay_seconds,
        parent_path=parent_path,
        source_name=source_name,
    )
    _require_positive_number(
        parsed_timeout,
        config_path=f"{parent_path}.timeout_seconds",
        source_name=source_name,
    )
    _require_non_negative_int(
        parsed_retries,
        config_path=f"{parent_path}.max_retries",
        source_name=source_name,
    )
    _require_non_negative_number(
        parsed_delay,
        config_path=f"{parent_path}.request_delay_seconds",
        source_name=source_name,
    )
    return parsed_timeout, parsed_retries, parsed_delay


def _parse_job_http_source_config(
    data: dict[str, Any],
    *,
    parent_path: str,
    source_name: str,
) -> JobHttpSourceConfig:
    defaults = JobHttpSourceConfig()
    timeout_seconds, max_retries, request_delay_seconds = (
        _parse_http_runtime_settings(
            data,
            parent_path=parent_path,
            source_name=source_name,
            timeout_seconds=defaults.timeout_seconds,
            max_retries=defaults.max_retries,
            request_delay_seconds=defaults.request_delay_seconds,
        )
    )
    page_limit = _optional_int(
        data,
        "page_limit",
        default=defaults.page_limit,
        parent_path=parent_path,
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
    _require_positive_int(
        lookback_days,
        config_path=f"{parent_path}.lookback_days",
        source_name=source_name,
    )
    imap_mailbox: str | None = None
    if "imap_mailbox" in data:
        imap_mailbox = _require_str(
            data,
            "imap_mailbox",
            parent_path=parent_path,
            source_name=source_name,
        ).strip()
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
    defaults = TwStockHttpSourceConfig()
    timeout_seconds, max_retries, request_delay_seconds = (
        _parse_http_runtime_settings(
            data,
            parent_path=parent_path,
            source_name=source_name,
            timeout_seconds=defaults.timeout_seconds,
            max_retries=defaults.max_retries,
            request_delay_seconds=defaults.request_delay_seconds,
        )
    )
    batch_size = _optional_int(
        data,
        "batch_size",
        default=defaults.batch_size,
        parent_path=parent_path,
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
