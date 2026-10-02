"""Telegram notification tool wrapper (disabled by default)."""

from __future__ import annotations

from typing import Any

from app.notifiers import build_telegram_notifier

from .models import ToolContext, ToolError, ToolResult

_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["text"],
    "properties": {
        "text": {
            "type": "string",
            "minLength": 1,
            "description": "Plain-text message body to send.",
        }
    },
}


class SendTelegramMessageTool:
    """Send a Telegram message via the existing notifier.

    Disabled unless ``ToolContext.telegram_enabled`` is true (tests default off).
    """

    name = "send_telegram_message"
    description = (
        "Send a plain-text Telegram notification using configured bot credentials. "
        "Disabled unless explicitly enabled for the agent workflow."
    )
    input_schema = _INPUT_SCHEMA

    def invoke(
        self,
        arguments: dict[str, Any],
        *,
        context: ToolContext,
    ) -> ToolResult:
        return _send_telegram_message(self.name, arguments, context=context)


def _send_telegram_message(
    tool_name: str,
    arguments: dict[str, Any],
    *,
    context: ToolContext,
) -> ToolResult:
    if not context.telegram_enabled:
        raise ToolError(
            f"{tool_name} is disabled in this context",
            code="tool_disabled",
        )

    text = arguments.get("text")
    if not isinstance(text, str) or not text.strip():
        raise ToolError(
            f"{tool_name}: 'text' must be a non-empty string",
            code="invalid_argument",
        )
    message = text.strip()

    if context.cancel_event is not None and context.cancel_event.is_set():
        raise ToolError(
            f"{tool_name} cancelled after tool timeout",
            code="cancelled",
        )

    if context.send_telegram is not None:
        context.send_telegram(message)
        return ToolResult(
            ok=True,
            data={"sent": True, "chars": len(message)},
            metadata={"mode": "injected"},
        )

    env = context.env
    if env is None:
        raise ToolError(
            f"{tool_name} requires env in ToolContext",
            code="missing_env",
        )
    notifier = build_telegram_notifier(env=env)
    notifier.send_message(message)
    return ToolResult(
        ok=True,
        data={"sent": True, "chars": len(message)},
        metadata={"mode": "live"},
    )
