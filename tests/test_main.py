import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def run_main(*args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["OPENAI_API_KEY"] = ""
    env["TELEGRAM_BOT_TOKEN"] = ""
    env["TELEGRAM_CHAT_ID"] = ""
    env["SIGNAL_AGENT_DB_PATH"] = str(
        Path(tempfile.mkdtemp()) / "signals.sqlite3"
    )

    return subprocess.run(
        [sys.executable, "main.py", *args],
        cwd=ROOT,
        capture_output=True,
        env=env,
        text=True,
        check=False,
    )


def test_main_help_command() -> None:
    result = run_main("--help")

    assert result.returncode == 0
    assert "signal-agent" in result.stdout
    assert "--task" in result.stdout
    assert "--list-tasks" in result.stdout
    assert "--health" in result.stdout
    assert "--recent-runs" in result.stdout
    assert "--telegram-test" in result.stdout


def test_main_list_tasks_command() -> None:
    result = run_main("--list-tasks")

    assert result.returncode == 0
    assert result.stdout.splitlines() == [
        "dummy",
        "fetch",
        "job_search",
        "stock_watchlist",
        "tw_stock_movers",
    ]


def test_main_dummy_task_command() -> None:
    result = run_main("--task", "dummy")

    assert result.returncode == 0
    assert "dummy task executed using config.yaml" in result.stdout
    assert "timezone=Asia/Taipei" in result.stdout
    assert "job_search_enabled=True" in result.stdout
    assert "stock_watchlist_enabled=False" in result.stdout
    assert "telegram_ready=False" in result.stdout


def test_main_fetch_task_command(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "fetch.yaml"
    db_path = tmp_path / "signals.sqlite3"
    config_path.write_text(
        "\n".join(
            [
                "agent:",
                "  timezone: Asia/Taipei",
                '  digest_time: "20:00"',
                "tasks:",
                "  job_search:",
                "    enabled: true",
                "    sources:",
                "      - dummy",
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
                "  llm_enabled: false",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("SIGNAL_AGENT_DB_PATH", str(db_path))

    result = run_main("--config", str(config_path), "--task", "fetch")

    assert result.returncode == 0
    assert f"fetch task executed using {config_path}" in result.stdout
    assert "fetched 2 signal(s) from 1 successful source(s)" in result.stdout
    assert "persisted 2 inserted / 0 updated" in result.stdout
    assert "Python Backend Engineer" in result.stdout
    assert "TSMC 上漲 3.1%" in result.stdout


def test_main_job_search_task_command(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "job_search.yaml"
    db_path = tmp_path / "signals.sqlite3"
    fixture_path = ROOT / "tests" / "fixtures" / "linkedin_alert_sample.eml"
    config_path.write_text(
        "\n".join(
            [
                "agent:",
                "  timezone: Asia/Taipei",
                '  digest_time: "20:00"',
                "tasks:",
                "  job_search:",
                "    enabled: true",
                "    sources:",
                "      - linkedin_email",
                "    keywords:",
                "      - engineer",
                "      - python",
                "  tw_stock_movers:",
                "    enabled: true",
                "    top_n: 20",
                "    timezone: Asia/Taipei",
                "notifiers:",
                "  telegram:",
                "    enabled: false",
                "scoring:",
                "  freshness_weight: 0.6",
                "  priority_weight: 0.4",
                "  llm_enabled: false",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("SIGNAL_AGENT_DB_PATH", str(db_path))
    monkeypatch.setenv("LINKEDIN_EMAIL_SOURCE_PATH", str(fixture_path))

    result = run_main("--config", str(config_path), "--task", "job_search")

    assert result.returncode == 0
    assert f"job_search task executed using {config_path}" in result.stdout
    assert "persisted 2 new / 0 duplicate" in result.stdout
    assert "telegram digest skipped: notifier disabled in config" in result.stdout


def test_main_invalid_config_returns_error(tmp_path: Path) -> None:
    config_path = tmp_path / "invalid.yaml"
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

    result = run_main("--config", str(config_path), "--task", "dummy")

    assert result.returncode == 1
    assert "Missing required config value 'agent.timezone'" in result.stderr


def test_main_requires_openai_key_when_llm_scoring_enabled(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "llm.yaml"
    config_path.write_text(
        "\n".join(
            [
                "agent:",
                "  timezone: Asia/Taipei",
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
                "  llm_enabled: true",
            ]
        ),
        encoding="utf-8",
    )

    result = run_main("--config", str(config_path), "--task", "dummy")

    assert result.returncode == 1
    assert "OPENAI_API_KEY is required" in result.stderr


def test_main_missing_config_returns_error() -> None:
    result = run_main("--config", "missing.yaml", "--task", "dummy")

    assert result.returncode == 1
    assert "Config file not found: missing.yaml" in result.stderr


def test_main_invalid_task_returns_error() -> None:
    result = run_main("--task", "missing-task")

    assert result.returncode == 1
    assert "Unknown task: missing-task" in result.stderr
    assert (
        "Available tasks: dummy, fetch, job_search, "
        "stock_watchlist, tw_stock_movers"
    ) in result.stderr


def test_main_tw_stock_movers_disabled_task_returns_error(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "tw_stock_movers.yaml"
    config_path.write_text(
        "\n".join(
            [
                "agent:",
                "  timezone: Asia/Taipei",
                '  digest_time: "20:00"',
                "tasks:",
                "  job_search:",
                "    enabled: true",
                "    sources:",
                "      - dummy",
                "    keywords:",
                "      - python",
                "  tw_stock_movers:",
                "    enabled: false",
                "    top_n: 20",
                "    timezone: Asia/Taipei",
                "notifiers:",
                "  telegram:",
                "    enabled: false",
                "scoring:",
                "  freshness_weight: 0.6",
                "  priority_weight: 0.4",
                "  llm_enabled: false",
            ]
        ),
        encoding="utf-8",
    )

    result = run_main("--config", str(config_path), "--task", "tw_stock_movers")

    assert result.returncode == 1
    assert "Task 'tw_stock_movers' is disabled in config" in result.stderr


def test_main_telegram_test_missing_credentials_returns_error() -> None:
    result = run_main("--telegram-test")

    assert result.returncode == 1
    assert "TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are required" in result.stderr
