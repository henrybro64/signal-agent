import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run_main(*args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["TELEGRAM_BOT_TOKEN"] = ""
    env["TELEGRAM_CHAT_ID"] = ""

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


def test_main_dummy_task_command() -> None:
    result = run_main("--task", "dummy")

    assert result.returncode == 0
    assert "dummy task executed using config.yaml" in result.stdout
    assert "timezone=Asia/Taipei" in result.stdout
    assert "telegram_ready=False" in result.stdout


def test_main_fetch_task_command() -> None:
    result = run_main("--task", "fetch")

    assert result.returncode == 0
    assert "fetch task executed using config.yaml" in result.stdout
    assert "fetched 2 signal(s) from dummy source" in result.stdout
    assert "Python Backend Engineer" in result.stdout
    assert "TSMC gains 3.1%" in result.stdout


def test_main_missing_config_returns_error() -> None:
    result = run_main("--config", "missing.yaml", "--task", "dummy")

    assert result.returncode == 1
    assert "Config file not found: missing.yaml" in result.stderr
