from pathlib import Path

import pytest

from app.core.config import AppConfig, ConfigError, load_config, load_env_settings

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config.yaml"


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
    assert config.notifiers.telegram.enabled is True
    assert config.scoring.freshness_weight == 0.6
    assert config.scoring.priority_weight == 0.4
    assert config.scoring.llm_enabled is False


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
            },
        }
    )

    assert config.agent.timezone == "UTC"
    assert config.tasks.job_search.keywords == ["data"]
    assert config.tasks.job_search.locations == []
    assert config.tasks.tw_stock_movers.top_n == 5
    assert config.scoring.llm_enabled is True


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
