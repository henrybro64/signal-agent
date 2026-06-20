from __future__ import annotations

from dataclasses import dataclass

import pytest
import requests

from app.core.secrets import EnvSettings, SecretError
from app.notifiers import (
    TELEGRAM_MAX_MESSAGE_LENGTH,
    TelegramNotifier,
    TelegramNotifierError,
    build_default_test_message,
    build_telegram_notifier,
    split_telegram_message,
)


@dataclass
class FakeResponse:
    status_code: int = 200
    json_payload: dict[str, object] | None = None
    text: str = ""
    reason: str = "OK"

    def json(self) -> dict[str, object]:
        if self.json_payload is None:
            raise ValueError("invalid json")
        return self.json_payload


class FakeSession:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, object]] = []

    def post(
        self,
        url: str,
        *,
        json: dict[str, object],
        timeout: int,
    ) -> FakeResponse:
        self.calls.append(
            {
                "url": url,
                "json": json,
                "timeout": timeout,
            }
        )
        return self.responses.pop(0)


def test_split_telegram_message_returns_one_chunk_for_short_text() -> None:
    chunks = split_telegram_message("hello, telegram")

    assert chunks == ["hello, telegram"]


def test_split_telegram_message_preserves_content_for_long_text() -> None:
    text = (
        ("job report line\n" * 500)
        + "\n"
        + ("tw stock report line\n" * 500)
    )

    chunks = split_telegram_message(text)

    assert "".join(chunks) == text
    assert all(len(chunk) <= TELEGRAM_MAX_MESSAGE_LENGTH for chunk in chunks)
    assert len(chunks) > 1


def test_build_telegram_notifier_requires_credentials() -> None:
    env = EnvSettings(
        openai_api_key=None,
        telegram_bot_token=None,
        telegram_chat_id=None,
        signal_agent_env="test",
    )

    with pytest.raises(SecretError):
        build_telegram_notifier(env=env)


def test_send_message_posts_each_chunk() -> None:
    long_text = (
        ("job report line\n" * 260)
        + "\n"
        + ("tw stock report line\n" * 260)
    )
    chunks = split_telegram_message(long_text)
    session = FakeSession(
        [FakeResponse(json_payload={"ok": True}) for _ in chunks]
    )
    notifier = TelegramNotifier(
        bot_token="token-123",
        chat_id="chat-456",
        session=session,
        api_base_url="https://api.telegram.test",
    )

    notifier.send_message(long_text)

    assert len(session.calls) == len(chunks)
    assert session.calls[0]["url"] == (
        "https://api.telegram.test/bottoken-123/sendMessage"
    )
    assert "".join(call["json"]["text"] for call in session.calls) == long_text


def test_send_message_surfaces_telegram_error_without_leaking_secrets() -> None:
    session = FakeSession(
        [
            FakeResponse(
                status_code=400,
                json_payload={
                    "ok": False,
                    "description": "Bad Request: chat not found for chat-456",
                },
            )
        ]
    )
    notifier = TelegramNotifier(
        bot_token="token-123",
        chat_id="chat-456",
        session=session,
    )

    with pytest.raises(TelegramNotifierError) as exc_info:
        notifier.send_message("hello")

    message = str(exc_info.value)
    assert "status 400" in message
    assert "chat not found" in message
    assert "chat-456" not in message
    assert "token-123" not in message


def test_send_message_sanitizes_request_exception() -> None:
    class RaisingSession:
        def post(
            self,
            url: str,
            *,
            json: dict[str, object],
            timeout: int,
        ) -> FakeResponse:
            raise requests.RequestException(
                f"POST {url} failed for chat chat-456"
            )

    notifier = TelegramNotifier(
        bot_token="token-123",
        chat_id="chat-456",
        session=RaisingSession(),
        api_base_url="https://api.telegram.test",
    )

    with pytest.raises(TelegramNotifierError) as exc_info:
        notifier.send_message("hello")

    message = str(exc_info.value)
    assert "Telegram send failed" in message
    assert "token-123" not in message
    assert "chat-456" not in message
    assert "***" in message


def test_send_message_rejects_blank_text() -> None:
    notifier = TelegramNotifier(
        bot_token="token-123",
        chat_id="chat-456",
        session=FakeSession([]),
    )

    with pytest.raises(ValueError, match="must not be empty"):
        notifier.send_message("   ")


def test_send_message_closes_owned_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class TrackingSession:
        def __init__(self) -> None:
            self.closed = False
            self.calls: list[dict[str, object]] = []

        def post(
            self,
            url: str,
            *,
            json: dict[str, object],
            timeout: int,
        ) -> FakeResponse:
            self.calls.append({"url": url, "json": json, "timeout": timeout})
            return FakeResponse(json_payload={"ok": True})

        def close(self) -> None:
            self.closed = True

    session = TrackingSession()
    monkeypatch.setattr(
        "app.notifiers.telegram.requests.Session",
        lambda: session,
    )
    notifier = TelegramNotifier(
        bot_token="token-123",
        chat_id="chat-456",
        api_base_url="https://api.telegram.test",
    )

    notifier.send_message("hello")

    assert session.closed is True
    assert len(session.calls) == 1


def test_build_default_test_message_includes_environment() -> None:
    message = build_default_test_message(signal_agent_env="staging")

    assert "signal-agent Telegram notifier test" in message
    assert "environment=staging" in message
    assert "sent_at=" in message
