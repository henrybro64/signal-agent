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
    assert result.stdout.splitlines() == ["dummy", "fetch", "job_search"]


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
