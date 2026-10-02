"""Telegram Bot API notifier helpers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import requests

from app.core.secrets import (
    EnvSettings,
    SecretError,
    has_telegram_credentials,
)

TELEGRAM_MAX_MESSAGE_LENGTH = 4096
_DEFAULT_API_BASE_URL = "https://api.telegram.org"
_REQUEST_TIMEOUT_SECONDS = 10


class TelegramNotifierError(ValueError):
    """Raised when Telegram delivery fails."""


@dataclass(slots=True)
class TelegramNotifier:
    """Send plain-text notifications through the Telegram Bot API."""

    bot_token: str
    chat_id: str
    session: requests.Session | None = None
    api_base_url: str = _DEFAULT_API_BASE_URL

    def send_message(self, text: str) -> None:
        """Send one logical message, splitting it into safe Telegram chunks."""

        if not text.strip():
            raise ValueError("Telegram message text must not be empty")

        session = self.session
        if session is None:
            session = requests.Session()
            owns_session = True
        else:
            owns_session = False

        url = f"{self.api_base_url}/bot{self.bot_token}/sendMessage"

        try:
            for chunk in split_telegram_message(text):
                try:
                    response = session.post(
                        url,
                        json={
                            "chat_id": self.chat_id,
                            "text": chunk,
                        },
                        timeout=_REQUEST_TIMEOUT_SECONDS,
                    )
                except requests.RequestException as exc:
                    detail = self._redact_secrets(str(exc))
                    raise TelegramNotifierError(
                        f"Telegram send failed: {detail}"
                    ) from exc

                if response.status_code >= 400:
                    raise TelegramNotifierError(
                        "Telegram send failed with "
                        f"status {response.status_code}: "
                        f"{self._extract_error_detail(response)}"
                    )

                try:
                    payload = response.json()
                except ValueError as exc:
                    raise TelegramNotifierError(
                        "Telegram send failed: API returned invalid JSON"
                    ) from exc

                if payload.get("ok") is not True:
                    description = payload.get(
                        "description",
                        "Telegram API rejected the request",
                    )
                    raise TelegramNotifierError(
                        f"Telegram send failed: {description}"
                    )
        finally:
            if owns_session:
                session.close()

    def _extract_error_detail(self, response: requests.Response) -> str:
        try:
            payload = response.json()
        except ValueError:
            body = response.text.strip() or response.reason or "unknown error"
            return self._redact_secrets(body[:200])

        detail = payload.get("description")
        if isinstance(detail, str) and detail.strip():
            return self._redact_secrets(detail)

        return "Telegram API rejected the request"

    def _redact_secrets(self, text: str) -> str:
        redacted = text
        for secret in (self.bot_token, self.chat_id):
            if secret:
                redacted = redacted.replace(secret, "***")
        return redacted


def build_telegram_notifier(
    *,
    env: EnvSettings,
    session: requests.Session | None = None,
) -> TelegramNotifier:
    """Build a Telegram notifier from validated environment settings."""

    if not has_telegram_credentials(env):
        raise SecretError(
            "TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are required "
            "to send Telegram messages"
        )

    return TelegramNotifier(
        bot_token=env.telegram_bot_token or "",
        chat_id=env.telegram_chat_id or "",
        session=session,
    )


def split_telegram_message(
    text: str,
    *,
    max_length: int = TELEGRAM_MAX_MESSAGE_LENGTH,
) -> list[str]:
    """Split a message into Telegram-safe chunks without dropping content."""

    if max_length <= 0:
        raise ValueError("max_length must be greater than zero")
    if text == "":
        return [""]

    chunks: list[str] = []
    remaining = text

    while len(remaining) > max_length:
        split_at = _find_split_index(remaining, max_length)
        chunks.append(remaining[:split_at])
        remaining = remaining[split_at:]

    if remaining:
        chunks.append(remaining)

    return chunks


def build_default_test_message(*, signal_agent_env: str) -> str:
    """Build the default Telegram CLI test payload."""

    sent_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    return (
        "signal-agent Telegram notifier test\n"
        f"environment={signal_agent_env}\n"
        f"sent_at={sent_at}"
    )


def _find_split_index(text: str, max_length: int) -> int:
    for delimiter in ("\n\n", "\n", " "):
        index = text.rfind(delimiter, 0, max_length + 1)
        if index > 0:
            return index + len(delimiter)

    return max_length
