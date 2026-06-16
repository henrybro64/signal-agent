from pathlib import Path

import pytest

from app.config import AppConfig, load_config, load_env_settings

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


def test_load_env_settings_treats_blank_values_as_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "")

    env = load_env_settings(env_path=None)

    assert env.telegram_bot_token is None
    assert env.telegram_chat_id is None
