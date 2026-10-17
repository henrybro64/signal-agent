"""Shared browser session / snapshot models (#106)."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True, frozen=True)
class PageSnapshot:
    """One-shot page capture used by fake/Chromium/Playwright backends."""

    url: str
    title: str
    text: str
    status: str = "ok"
    screenshot_path: str | None = None


@dataclass(slots=True, frozen=True)
class InteractiveElement:
    """One actionable element exposed to the model."""

    ref: int
    role: str
    label: str
    url: str | None = None
    input_type: str | None = None

    def format_line(self) -> str:
        base = f"[{self.ref}] {self.role} {self.label!r}"
        if self.url:
            return f"{base} → {self.url}"
        if self.input_type:
            return f"{base} (type={self.input_type})"
        return base


@dataclass(slots=True, frozen=True)
class SessionSnapshot:
    """Page state returned after open / navigate / act."""

    session_id: str
    url: str
    title: str
    text: str
    elements: list[InteractiveElement] = field(default_factory=list)
    status: str = "ok"
    screenshot_path: str | None = None
    blocked_reason: str | None = None
    steps_used: int = 0

    def format_for_model(self, *, text_limit: int = 400) -> str:
        # Elements first: chat truncates the serialized tool result to 1,200
        # characters, so refs must appear before page text.
        lines = [
            f"session_id: {self.session_id}",
            f"url: {self.url}",
            f"title: {self.title}",
            f"status: {self.status}",
            f"steps_used: {self.steps_used}",
        ]
        if self.blocked_reason:
            lines.append(f"blocked_reason: {self.blocked_reason}")
        if self.elements:
            lines.append("elements:")
            lines.extend(element.format_line() for element in self.elements)
        lines.append("text:")
        lines.append(self.text[:text_limit])
        return "\n".join(lines)

    def to_tool_data(self, *, text_limit: int = 400) -> dict:
        formatted = self.format_for_model(text_limit=text_limit)
        return {
            "formatted": formatted,
            "session_id": self.session_id,
            "url": self.url,
            "title": self.title,
            "status": self.status,
            "blocked_reason": self.blocked_reason,
            "steps_used": self.steps_used,
            "screenshot_path": self.screenshot_path,
            "elements": [
                {
                    "ref": element.ref,
                    "role": element.role,
                    "label": element.label,
                    "url": element.url,
                    "input_type": element.input_type,
                }
                for element in self.elements
            ],
            "text": self.text[:text_limit],
            "text_length": len(self.text),
        }
