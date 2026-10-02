import math
from pathlib import Path

import pytest

from app.core.config import AppConfig, ConfigError, load_config, load_env_settings

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config.yaml"


def assert_close(actual: float, expected: float) -> None:
    assert math.isclose(actual, expected, rel_tol=0.0, abs_tol=1e-9)



def test_load_config_reads_sample_file() -> None:
    config = load_config(CONFIG_PATH)

    assert config.agent.timezone == "Asia/Taipei"
    assert config.agent.digest_time == "20:00"
    assert config.tasks.job_search.enabled is True
    assert config.tasks.job_search.sources == ["job104", "linkedin_email"]
    assert config.tasks.job_search.keywords == ["python", "backend", "agent"]
    assert config.tasks.job_search.locations == ["Taiwan"]
    assert config.tasks.tw_stock_movers.enabled is True
    assert config.tasks.tw_stock_movers.top_n == 20
    assert config.tasks.tw_stock_movers.timezone == "Asia/Taipei"
    assert config.tasks.stock_watchlist.enabled is False
    assert config.tasks.stock_watchlist.timezone == "Asia/Taipei"
    assert config.tasks.stock_watchlist.sources == ["twse", "tpex"]
    assert [symbol.code for symbol in config.tasks.stock_watchlist.symbols] == [
        "2330",
        "3081",
    ]
    assert config.tasks.stock_watchlist.symbols[0].alert_change_percent_abs == 2.0
    assert set(config.tasks.stock_watchlist.themes) == {
        "AI_infrastructure",
        "CPO_optical",
    }
    assert config.tasks.tw_market_assistant.enabled is False
    assert config.tasks.tw_market_assistant.top_n == 20
    assert config.tasks.job_research_agent.enabled is True
    assert config.tasks.job_research_agent.memory_namespace == "job_research"
    assert config.tasks.job_research_agent.max_steps == 12
    assert "job_research" in config.memory.namespaces
    assert any(
        path.endswith("company-notes.md")
        for path in config.memory.namespaces["job_research"].documents
    )
    assert config.investment.watchlist == ["2330", "2454"]
    assert config.investment.theme_mappings["cpo_optical"].label == "CPO / 光通訊"
    assert "CPO" in config.investment.theme_mappings["cpo_optical"].keywords
    assert config.notifiers.telegram.enabled is True
    assert config.notifiers.telegram.failure_alerts_enabled is True
    assert config.scoring.freshness_weight == 0.6
    assert config.scoring.priority_weight == 0.4
    assert config.scoring.llm_enabled is False
    assert config.scoring.llm_model == "gpt-4o-mini"
    assert config.scoring.llm_min_rule_score == 25
    assert config.scoring.llm_max_jobs_per_run == 20
    assert config.scoring.digest_max_strong_matches == 10
    assert config.scoring.digest_max_maybe_matches == 10
    assert config.scoring.include_rejected_summary is True
    assert config.llm.provider == "openai"
    assert config.llm.model == "gpt-4o-mini"
    assert_close(config.llm.timeout_seconds, 20.0)
    assert_close(config.llm.temperature, 0.2)
    assert_close(config.sources.job104.timeout_seconds, 10.0)
    assert config.sources.job104.max_retries == 2
    assert_close(config.sources.job104.request_delay_seconds, 1.0)
    assert config.sources.job104.page_limit == 1
    assert config.sources.linkedin_email.lookback_days == 14
    assert config.sources.linkedin_email.imap_mailbox is None
    assert config.sources.twse.batch_size == 100
    assert_close(config.sources.tpex.request_delay_seconds, 0.2)


def test_load_config_missing_file_raises() -> None:
    with pytest.raises(FileNotFoundError, match="missing.yaml"):
        load_config(ROOT / "missing.yaml")


def test_load_config_uses_default_path_from_repo_root() -> None:
    config = load_config()

    assert config.agent.timezone == "Asia/Taipei"


def test_load_config_missing_required_value_raises_clear_error(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "\n".join(
            [
                "agent:",
                '  digest_time: "20:00"',
                "tasks:",
                "  job_search:",
                "    enabled: true",
                "    sources:",
                "      - job104",
                "    keywords:",
                "      - python",
                "  tw_stock_movers:",
                "    enabled: true",
                "    top_n: 20",
                "    timezone: Asia/Taipei",
                "notifiers:",
                "  telegram:",
                "    enabled: true",
                "scoring:",
                "  freshness_weight: 0.6",
                "  priority_weight: 0.4",
            ]
        ),
        encoding="utf-8",
    )

    with pytest.raises(
        ConfigError,
        match="Missing required config value 'agent.timezone'",
    ):
        load_config(config_path)


def test_load_config_invalid_nested_mapping_raises_clear_error(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "\n".join(
            [
                "agent:",
                "  timezone: Asia/Taipei",
                '  digest_time: "20:00"',
                "tasks:",
                "  job_search: true",
                "  tw_stock_movers:",
                "    enabled: true",
                "    top_n: 20",
                "    timezone: Asia/Taipei",
                "notifiers:",
                "  telegram:",
                "    enabled: true",
                "scoring:",
                "  freshness_weight: 0.6",
                "  priority_weight: 0.4",
            ]
        ),
        encoding="utf-8",
    )

    with pytest.raises(
        ConfigError,
        match="Config value 'tasks.job_search' must be a mapping",
    ):
        load_config(config_path)


def test_load_config_invalid_value_type_raises_clear_error(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "\n".join(
            [
                "agent:",
                "  timezone: Asia/Taipei",
                '  digest_time: "20:00"',
                "tasks:",
                "  job_search:",
                '    enabled: "yes"',
                "    sources:",
                "      - job104",
                "    keywords:",
                "      - python",
                "  tw_stock_movers:",
                "    enabled: true",
                "    top_n: 20",
                "    timezone: Asia/Taipei",
                "notifiers:",
                "  telegram:",
                "    enabled: true",
                "scoring:",
                "  freshness_weight: 0.6",
                "  priority_weight: 0.4",
            ]
        ),
        encoding="utf-8",
    )

    with pytest.raises(
        ConfigError,
        match="Config value 'tasks.job_search.enabled' must be a boolean",
    ):
        load_config(config_path)


def test_load_config_invalid_yaml_raises_clear_error(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text("agent: [broken\n", encoding="utf-8")

    with pytest.raises(ConfigError, match="Invalid YAML in config file"):
        load_config(config_path)


def test_app_config_from_dict() -> None:
    config = AppConfig.from_dict(
        {
            "agent": {"timezone": "UTC", "digest_time": "09:00"},
            "tasks": {
                "job_search": {
                    "enabled": False,
                    "sources": ["job104"],
                    "keywords": ["data"],
                },
                "tw_stock_movers": {
                    "enabled": True,
                    "top_n": 5,
                    "timezone": "UTC",
                },
            },
            "notifiers": {"telegram": {"enabled": False}},
            "scoring": {
                "freshness_weight": 0.5,
                "priority_weight": 0.5,
                "llm_enabled": True,
                "llm_model": "gpt-5.2",
            },
        }
    )

    assert config.agent.timezone == "UTC"
    assert config.tasks.job_search.keywords == ["data"]
    assert config.tasks.job_search.locations == []
    assert config.tasks.tw_stock_movers.top_n == 5
    assert config.notifiers.telegram.failure_alerts_enabled is False
    assert config.scoring.llm_enabled is True
    assert config.scoring.llm_model == "gpt-5.2"
    assert config.sources.job104.page_limit == 1
    assert_close(config.sources.indeed.request_delay_seconds, 1.0)
    assert config.sources.linkedin_email.lookback_days == 14
    assert config.sources.linkedin_email.imap_mailbox is None
    assert config.sources.twse.batch_size == 100
    assert_close(config.sources.tpex.timeout_seconds, 10.0)
    assert len(config.tasks.job_search.profiles) == 1
    assert config.tasks.job_search.profiles[0].name == "default"
    assert config.tasks.job_search.profiles[0].keywords == ["data"]


def test_app_config_from_dict_reads_custom_scoring_limits() -> None:
    config = AppConfig.from_dict(
        {
            **_base_config_dict(),
            "scoring": {
                "freshness_weight": 0.5,
                "priority_weight": 0.5,
                "llm_enabled": True,
                "llm_model": "gpt-5.2",
                "llm_min_rule_score": 40,
                "llm_max_jobs_per_run": 5,
                "digest_max_strong_matches": 3,
                "digest_max_maybe_matches": 2,
                "include_rejected_summary": False,
            },
        }
    )

    assert config.scoring.llm_min_rule_score == 40
    assert config.scoring.llm_max_jobs_per_run == 5
    assert config.scoring.digest_max_strong_matches == 3
    assert config.scoring.digest_max_maybe_matches == 2
    assert config.scoring.include_rejected_summary is False


def test_app_config_from_dict_rejects_invalid_scoring_limits() -> None:
    with pytest.raises(ConfigError, match="scoring.llm_min_rule_score"):
        AppConfig.from_dict(
            {
                **_base_config_dict(),
                "scoring": {
                    "freshness_weight": 0.5,
                    "priority_weight": 0.5,
                    "llm_min_rule_score": 101,
                },
            }
        )

    with pytest.raises(ConfigError, match="scoring.llm_max_jobs_per_run"):
        AppConfig.from_dict(
            {
                **_base_config_dict(),
                "scoring": {
                    "freshness_weight": 0.5,
                    "priority_weight": 0.5,
                    "llm_max_jobs_per_run": 0,
                },
            }
        )


def test_app_config_from_dict_reads_job_search_profiles() -> None:
    config = AppConfig.from_dict(
        {
            **_base_config_dict(),
            "tasks": {
                "job_search": {
                    "enabled": True,
                    "profiles": [
                        {
                            "name": "cloud_sdet",
                            "sources": ["job104", "linkedin_email"],
                            "keywords": ["Cloud SDET", "Python"],
                            "locations": ["Taiwan", "Remote"],
                            "scorer_profile": "app/profiles/henry_job_profile.md",
                        },
                        {
                            "name": "devsecops_security",
                            "sources": ["job104"],
                            "keywords": ["DevSecOps"],
                            "locations": ["Remote"],
                        },
                    ],
                },
                "tw_stock_movers": {
                    "enabled": True,
                    "top_n": 5,
                    "timezone": "UTC",
                },
            },
        }
    )

    assert [profile.name for profile in config.tasks.job_search.profiles] == [
        "cloud_sdet",
        "devsecops_security",
    ]
    assert config.tasks.job_search.sources == ["job104", "linkedin_email"]
    assert config.tasks.job_search.keywords == ["Cloud SDET", "Python"]
    assert config.tasks.job_search.locations == ["Taiwan", "Remote"]
    first = config.tasks.job_search.profiles[0]
    assert first.scorer_profile == "app/profiles/henry_job_profile.md"
    assert first.sources == ["job104", "linkedin_email"]
    second = config.tasks.job_search.profiles[1]
    assert second.keywords == ["DevSecOps"]
    assert second.scorer_profile is None


def test_app_config_from_dict_rejects_duplicate_job_search_profile_names() -> None:
    with pytest.raises(ConfigError, match="Duplicate job-search profile name"):
        AppConfig.from_dict(
            {
                **_base_config_dict(),
                "tasks": {
                    "job_search": {
                        "enabled": True,
                        "profiles": [
                            {
                                "name": "cloud_sdet",
                                "sources": ["job104"],
                                "keywords": ["python"],
                            },
                            {
                                "name": "cloud_sdet",
                                "sources": ["linkedin_email"],
                                "keywords": ["agent"],
                            },
                        ],
                    },
                    "tw_stock_movers": {
                        "enabled": True,
                        "top_n": 5,
                        "timezone": "UTC",
                    },
                },
            }
        )


def test_app_config_from_dict_rejects_empty_job_search_profiles() -> None:
    with pytest.raises(ConfigError, match="must contain at least one profile"):
        AppConfig.from_dict(
            {
                **_base_config_dict(),
                "tasks": {
                    "job_search": {
                        "enabled": True,
                        "profiles": [],
                    },
                    "tw_stock_movers": {
                        "enabled": True,
                        "top_n": 5,
                        "timezone": "UTC",
                    },
                },
            }
        )


def test_app_config_from_dict_reads_custom_sources_settings() -> None:
    config = AppConfig.from_dict(
        {
            **_base_config_dict(),
            "sources": {
                "job104": {
                    "timeout_seconds": 15,
                    "max_retries": 4,
                    "request_delay_seconds": 2.5,
                    "page_limit": 3,
                },
                "linkedin_email": {
                    "lookback_days": 7,
                    "imap_mailbox": "LinkedIn",
                },
                "twse": {
                    "timeout_seconds": 8,
                    "max_retries": 1,
                    "request_delay_seconds": 0.5,
                    "batch_size": 50,
                },
                "tpex": {
                    "batch_size": 25,
                },
            },
        }
    )

    assert_close(config.sources.job104.timeout_seconds, 15.0)
    assert config.sources.job104.max_retries == 4
    assert_close(config.sources.job104.request_delay_seconds, 2.5)
    assert config.sources.job104.page_limit == 3
    assert config.sources.linkedin_email.lookback_days == 7
    assert config.sources.linkedin_email.imap_mailbox == "LinkedIn"
    assert_close(config.sources.twse.timeout_seconds, 8.0)
    assert config.sources.twse.max_retries == 1
    assert_close(config.sources.twse.request_delay_seconds, 0.5)
    assert config.sources.twse.batch_size == 50
    assert config.sources.tpex.batch_size == 25
    assert_close(config.sources.tpex.timeout_seconds, 10.0)


@pytest.mark.parametrize(
    ("sources", "match"),
    [
        (
            {"job104": {"timeout_seconds": 0}},
            "sources.job104.timeout_seconds",
        ),
        (
            {"job104": {"page_limit": 0}},
            "sources.job104.page_limit",
        ),
        (
            {"job104": {"max_retries": -1}},
            "sources.job104.max_retries",
        ),
        (
            {"job104": {"request_delay_seconds": -0.1}},
            "sources.job104.request_delay_seconds",
        ),
        (
            {"linkedin_email": {"lookback_days": 0}},
            "sources.linkedin_email.lookback_days",
        ),
        (
            {"linkedin_email": {"imap_mailbox": "   "}},
            "sources.linkedin_email.imap_mailbox",
        ),
        (
            {"twse": {"batch_size": 0}},
            "sources.twse.batch_size",
        ),
        (
            {"tpex": {"timeout_seconds": -5}},
            "sources.tpex.timeout_seconds",
        ),
    ],
)
def test_app_config_from_dict_rejects_invalid_sources_settings(
    sources: dict[str, object],
    match: str,
) -> None:
    payload = {**_base_config_dict(), "sources": sources}
    with pytest.raises(ConfigError, match=match):
        AppConfig.from_dict(payload)


def test_app_config_from_dict_defaults_stock_watchlist_when_omitted() -> None:
    config = AppConfig.from_dict(_base_config_dict())

    assert config.tasks.stock_watchlist.enabled is False
    assert config.tasks.stock_watchlist.timezone == "Asia/Taipei"
    assert config.tasks.stock_watchlist.sources == ["twse", "tpex"]
    assert config.tasks.stock_watchlist.symbols == []
    assert config.tasks.stock_watchlist.themes == {}


def test_app_config_from_dict_accepts_disabled_stock_watchlist_without_fields() -> (
    None
):
    payload = _base_config_dict()
    tasks = payload["tasks"]
    assert isinstance(tasks, dict)
    tasks["stock_watchlist"] = {"enabled": False}

    config = AppConfig.from_dict(payload)

    assert config.tasks.stock_watchlist.enabled is False
    assert config.tasks.stock_watchlist.timezone == "Asia/Taipei"
    assert config.tasks.stock_watchlist.sources == ["twse", "tpex"]
    assert config.tasks.stock_watchlist.symbols == []
    assert config.tasks.stock_watchlist.themes == {}


def test_app_config_from_dict_reads_stock_watchlist() -> None:
    payload = _base_config_dict()
    tasks = payload["tasks"]
    assert isinstance(tasks, dict)
    tasks["stock_watchlist"] = {
        "enabled": True,
        "timezone": "Asia/Taipei",
        "sources": ["twse"],
        "symbols": [
            {
                "code": "2330",
                "name": "台積電",
                "alert_change_percent_abs": 2.5,
            }
        ],
        "themes": {"AI_infrastructure": {"symbols": ["2330", "2382"]}},
    }

    config = AppConfig.from_dict(payload)

    watchlist = config.tasks.stock_watchlist
    assert watchlist.enabled is True
    assert watchlist.sources == ["twse"]
    assert len(watchlist.symbols) == 1
    assert watchlist.symbols[0].code == "2330"
    assert watchlist.symbols[0].name == "台積電"
    assert_close(watchlist.symbols[0].alert_change_percent_abs, 2.5)
    assert watchlist.themes["AI_infrastructure"].symbols == ["2330", "2382"]


@pytest.mark.parametrize(
    ("watchlist", "match"),
    [
        (
            {
                "enabled": True,
                "timezone": "Asia/Taipei",
                "symbols": [
                    {
                        "code": "2330",
                        "alert_change_percent_abs": 0,
                    }
                ],
            },
            "alert_change_percent_abs",
        ),
        (
            {
                "enabled": True,
                "timezone": "Asia/Taipei",
                "symbols": [
                    {
                        "code": "2330",
                        "alert_change_percent_abs": 2.0,
                    },
                    {
                        "code": "2330",
                        "alert_change_percent_abs": 3.0,
                    },
                ],
            },
            "Duplicate stock_watchlist symbol code",
        ),
        (
            {
                "enabled": True,
                "timezone": "Asia/Taipei",
                "sources": ["yahoo"],
                "symbols": [],
            },
            "Unsupported stock_watchlist source",
        ),
        (
            {
                "enabled": True,
                "timezone": "Asia/Taipei",
                "themes": {"AI": {"symbols": []}},
            },
            "themes.AI.symbols",
        ),
    ],
)
def test_app_config_from_dict_rejects_invalid_stock_watchlist(
    watchlist: dict[str, object],
    match: str,
) -> None:
    payload = _base_config_dict()
    tasks = payload["tasks"]
    assert isinstance(tasks, dict)
    tasks["stock_watchlist"] = watchlist
    with pytest.raises(ConfigError, match=match):
        AppConfig.from_dict(payload)


def _base_config_dict() -> dict[str, object]:
    return {
        "agent": {"timezone": "UTC", "digest_time": "09:00"},
        "tasks": {
            "job_search": {
                "enabled": True,
                "sources": ["job104"],
                "keywords": ["python"],
            },
            "tw_stock_movers": {
                "enabled": True,
                "top_n": 5,
                "timezone": "UTC",
            },
        },
        "notifiers": {"telegram": {"enabled": False}},
        "scoring": {
            "freshness_weight": 0.5,
            "priority_weight": 0.5,
        },
    }


def test_app_config_from_dict_reads_optional_job_search_locations() -> None:
    config = AppConfig.from_dict(
        {
            "agent": {"timezone": "UTC", "digest_time": "09:00"},
            "tasks": {
                "job_search": {
                    "enabled": True,
                    "sources": ["job104"],
                    "keywords": ["python"],
                    "locations": ["Taipei", "Remote"],
                },
                "tw_stock_movers": {
                    "enabled": True,
                    "top_n": 5,
                    "timezone": "UTC",
                },
            },
            "notifiers": {"telegram": {"enabled": False}},
            "scoring": {
                "freshness_weight": 0.5,
                "priority_weight": 0.5,
            },
        }
    )

    assert config.tasks.job_search.locations == ["Taipei", "Remote"]
    assert config.scoring.llm_model == "gpt-4o-mini"


def test_app_config_from_dict_allows_failure_alert_override() -> None:
    config = AppConfig.from_dict(
        {
            "agent": {"timezone": "UTC", "digest_time": "09:00"},
            "tasks": {
                "job_search": {
                    "enabled": True,
                    "sources": ["job104"],
                    "keywords": ["python"],
                },
                "tw_stock_movers": {
                    "enabled": True,
                    "top_n": 5,
                    "timezone": "UTC",
                },
            },
            "notifiers": {
                "telegram": {
                    "enabled": False,
                    "failure_alerts_enabled": True,
                }
            },
            "scoring": {
                "freshness_weight": 0.5,
                "priority_weight": 0.5,
            },
        }
    )

    assert config.notifiers.telegram.enabled is False
    assert config.notifiers.telegram.failure_alerts_enabled is True


def test_app_config_from_dict_rejects_invalid_llm_model_type() -> None:
    with pytest.raises(
        ConfigError,
        match="Config value 'scoring.llm_model' must be a string",
    ):
        AppConfig.from_dict(
            {
                "agent": {"timezone": "UTC", "digest_time": "09:00"},
                "tasks": {
                    "job_search": {
                        "enabled": True,
                        "sources": ["job104"],
                        "keywords": ["python"],
                    },
                    "tw_stock_movers": {
                        "enabled": True,
                        "top_n": 5,
                        "timezone": "UTC",
                    },
                },
                "notifiers": {"telegram": {"enabled": False}},
                "scoring": {
                    "freshness_weight": 0.5,
                    "priority_weight": 0.5,
                    "llm_model": 123,
                },
            }
        )


def test_app_config_from_dict_parses_llm_gateway_section() -> None:
    config = AppConfig.from_dict(
        {
            "agent": {"timezone": "UTC", "digest_time": "09:00"},
            "tasks": {
                "job_search": {
                    "enabled": True,
                    "sources": ["job104"],
                    "keywords": ["python"],
                },
                "tw_stock_movers": {
                    "enabled": True,
                    "top_n": 5,
                    "timezone": "UTC",
                },
            },
            "notifiers": {"telegram": {"enabled": False}},
            "scoring": {
                "freshness_weight": 0.5,
                "priority_weight": 0.5,
            },
            "llm": {
                "provider": "fake",
                "model": "local-test",
                "timeout_seconds": 7.5,
                "temperature": 0.0,
            },
        }
    )
    assert config.llm.provider == "fake"
    assert config.llm.model == "local-test"
    assert config.scoring.llm_model == "local-test"
    assert config.llm.timeout_seconds == 7.5
    assert config.llm.temperature == 0.0


def test_app_config_syncs_llm_model_from_scoring_alias() -> None:
    config = AppConfig.from_dict(
        {
            "agent": {"timezone": "UTC", "digest_time": "09:00"},
            "tasks": {
                "job_search": {
                    "enabled": True,
                    "sources": ["job104"],
                    "keywords": ["python"],
                },
                "tw_stock_movers": {
                    "enabled": True,
                    "top_n": 5,
                    "timezone": "UTC",
                },
            },
            "notifiers": {"telegram": {"enabled": False}},
            "scoring": {
                "freshness_weight": 0.5,
                "priority_weight": 0.5,
                "llm_model": "gpt-from-scoring",
            },
        }
    )
    assert config.scoring.llm_model == "gpt-from-scoring"
    assert config.llm.model == "gpt-from-scoring"


def test_app_config_rejects_mismatched_llm_model_settings() -> None:
    with pytest.raises(ConfigError, match="scoring.llm_model.*llm.model"):
        AppConfig.from_dict(
            {
                "agent": {"timezone": "UTC", "digest_time": "09:00"},
                "tasks": {
                    "job_search": {
                        "enabled": True,
                        "sources": ["job104"],
                        "keywords": ["python"],
                    },
                    "tw_stock_movers": {
                        "enabled": True,
                        "top_n": 5,
                        "timezone": "UTC",
                    },
                },
                "notifiers": {"telegram": {"enabled": False}},
                "scoring": {
                    "freshness_weight": 0.5,
                    "priority_weight": 0.5,
                    "llm_model": "gpt-scoring",
                },
                "llm": {"model": "gpt-gateway"},
            }
        )


def test_app_config_from_dict_rejects_invalid_llm_provider() -> None:
    with pytest.raises(ConfigError, match="llm.provider"):
        AppConfig.from_dict(
            {
                "agent": {"timezone": "UTC", "digest_time": "09:00"},
                "tasks": {
                    "job_search": {
                        "enabled": True,
                        "sources": ["job104"],
                        "keywords": ["python"],
                    },
                    "tw_stock_movers": {
                        "enabled": True,
                        "top_n": 5,
                        "timezone": "UTC",
                    },
                },
                "notifiers": {"telegram": {"enabled": False}},
                "scoring": {
                    "freshness_weight": 0.5,
                    "priority_weight": 0.5,
                },
                "llm": {"provider": "anthropic"},
            }
        )


def test_load_env_settings_reads_variables(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token-123")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "chat-456")
    monkeypatch.setenv("SIGNAL_AGENT_ENV", "test")

    env = load_env_settings(env_path=None)

    assert env.openai_api_key == "sk-test"
    assert env.telegram_bot_token == "token-123"
    assert env.telegram_chat_id == "chat-456"
    assert env.signal_agent_env == "test"


def test_load_env_settings_reads_dotenv_file(
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
                "SIGNAL_AGENT_ENV=local",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    monkeypatch.delenv("SIGNAL_AGENT_ENV", raising=False)

    env = load_env_settings(env_path=env_path)

    assert env.openai_api_key == "sk-dotenv"
    assert env.telegram_bot_token == "dotenv-token"
    assert env.telegram_chat_id == "dotenv-chat"
    assert env.signal_agent_env == "local"


def test_load_env_settings_treats_blank_values_as_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "")

    env = load_env_settings(env_path=None)

    assert env.openai_api_key is None
    assert env.telegram_bot_token is None
    assert env.telegram_chat_id is None
