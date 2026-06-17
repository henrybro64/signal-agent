from pathlib import Path

import pytest

from app.config import AppConfig, ConfigError, load_config, load_env_settings

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config.yaml"


def test_load_config_reads_sample_file() -> None:
    config = load_config(CONFIG_PATH)

    assert config.agent.timezone == "Asia/Taipei"
    assert config.agent.digest_time == "20:00"
    assert config.sources.jobs.enabled is True
    assert config.sources.jobs.keywords == ["python", "backend", "agent"]
    assert config.sources.tw_stocks.enabled is True
    assert config.sources.tw_stocks.movers_limit == 10
    assert config.notifiers.telegram.enabled is True
    assert config.scoring.freshness_weight == 0.6
    assert config.scoring.priority_weight == 0.4


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
                "sources:",
                "  jobs:",
                "    enabled: true",
                "    keywords:",
                "      - python",
                "  tw_stocks:",
                "    enabled: true",
                "    movers_limit: 10",
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
                "sources:",
                "  jobs: true",
                "  tw_stocks:",
                "    enabled: true",
                "    movers_limit: 10",
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
        match="Config value 'sources.jobs' must be a mapping",
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
            "sources": {
                "jobs": {"enabled": False, "keywords": ["data"]},
                "tw_stocks": {"enabled": True, "movers_limit": 5},
            },
            "notifiers": {"telegram": {"enabled": False}},
            "scoring": {"freshness_weight": 0.5, "priority_weight": 0.5},
        }
    )

    assert config.agent.timezone == "UTC"
    assert config.sources.jobs.keywords == ["data"]


def test_load_env_settings_reads_variables(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token-123")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "chat-456")
    monkeypatch.setenv("SIGNAL_AGENT_ENV", "test")

    env = load_env_settings(env_path=None)

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
                "TELEGRAM_BOT_TOKEN=dotenv-token",
                "TELEGRAM_CHAT_ID=dotenv-chat",
                "SIGNAL_AGENT_ENV=local",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    monkeypatch.delenv("SIGNAL_AGENT_ENV", raising=False)

    env = load_env_settings(env_path=env_path)

    assert env.telegram_bot_token == "dotenv-token"
    assert env.telegram_chat_id == "dotenv-chat"
    assert env.signal_agent_env == "local"


def test_load_env_settings_treats_blank_values_as_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "")

    env = load_env_settings(env_path=None)

    assert env.telegram_bot_token is None
    assert env.telegram_chat_id is None
