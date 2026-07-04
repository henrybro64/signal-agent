import shutil
import subprocess
from pathlib import Path

import pytest

from app.cli import main
from app.core.secrets import EnvSettings

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(
    shutil.which("signal-agent") is None,
    reason="signal-agent console script is not installed in this environment",
)
def test_signal_agent_console_script_lists_tasks() -> None:
    result = subprocess.run(
        ["signal-agent", "--list-tasks"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert result.stdout.splitlines() == [
        "dummy",
        "fetch",
        "job_search",
        "tw_stock_movers",
    ]


def test_cli_telegram_test_uses_default_message(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    sent_messages: list[str] = []

    class FakeNotifier:
        def send_message(self, text: str) -> None:
            sent_messages.append(text)

    monkeypatch.setattr(
        "app.cli.load_env_settings",
        lambda: EnvSettings(
            openai_api_key=None,
            telegram_bot_token="token-123",
            telegram_chat_id="chat-456",
            signal_agent_env="test",
        ),
    )
    monkeypatch.setattr(
        "app.cli.build_telegram_notifier",
        lambda *, env: FakeNotifier(),
    )

    exit_code = main(["--telegram-test"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert captured.out.strip() == "telegram test message sent"
    assert len(sent_messages) == 1
    assert "signal-agent Telegram notifier test" in sent_messages[0]
    assert "environment=test" in sent_messages[0]


def test_cli_telegram_test_uses_custom_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent_messages: list[str] = []

    class FakeNotifier:
        def send_message(self, text: str) -> None:
            sent_messages.append(text)

    monkeypatch.setattr(
        "app.cli.load_env_settings",
        lambda: EnvSettings(
            openai_api_key=None,
            telegram_bot_token="token-123",
            telegram_chat_id="chat-456",
            signal_agent_env="test",
        ),
    )
    monkeypatch.setattr(
        "app.cli.build_telegram_notifier",
        lambda *, env: FakeNotifier(),
    )

    exit_code = main(["--telegram-test", "custom smoke test"])

    assert exit_code == 0
    assert sent_messages == ["custom smoke test"]


def test_cli_telegram_test_missing_credentials_returns_error(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        "app.cli.load_env_settings",
        lambda: EnvSettings(
            openai_api_key=None,
            telegram_bot_token=None,
            telegram_chat_id=None,
            signal_agent_env="test",
        ),
    )

    exit_code = main(["--telegram-test"])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are required" in captured.err


def test_cli_failed_task_triggers_failure_alert(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    recorded_calls: list[dict[str, object]] = []

    class FailingTask:
        name = "job_search"

        def run(self) -> None:
            raise ValueError("TWSE timeout")

    monkeypatch.setattr(
        "app.cli.build_task",
        lambda name, *, config_path: FailingTask(),
    )
    monkeypatch.setattr(
        "app.cli.notify_task_failure",
        lambda **kwargs: recorded_calls.append(kwargs),
    )

    exit_code = main(["--task", "job_search", "--config", "custom.yaml"])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert captured.err.strip() == "TWSE timeout"
    assert len(recorded_calls) == 1
    assert recorded_calls[0]["task_name"] == "job_search"
    assert recorded_calls[0]["config_path"] == "custom.yaml"
    assert isinstance(recorded_calls[0]["error"], ValueError)
    assert str(recorded_calls[0]["error"]) == "TWSE timeout"
