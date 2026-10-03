"""Chat session state and optional persistence (#83)."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.runtime import paths as runtime_paths


@dataclass(slots=True)
class ChatMessage:
    role: str  # user | assistant | system | tool
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ChatSession:
    session_id: str
    messages: list[ChatMessage] = field(default_factory=list)
    tool_calls: int = 0
    llm_calls: int = 0
    turns: int = 0
    created_at: str = ""
    updated_at: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "tool_calls": self.tool_calls,
            "llm_calls": self.llm_calls,
            "turns": self.turns,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "metadata": self.metadata,
            "messages": [
                {
                    "role": msg.role,
                    "content": msg.content,
                    "metadata": msg.metadata,
                }
                for msg in self.messages
            ],
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> ChatSession:
        messages = [
            ChatMessage(
                role=str(item.get("role") or "user"),
                content=str(item.get("content") or ""),
                metadata=dict(item.get("metadata") or {}),
            )
            for item in payload.get("messages") or []
            if isinstance(item, dict)
        ]
        return cls(
            session_id=str(payload.get("session_id") or uuid.uuid4()),
            messages=messages,
            tool_calls=int(payload.get("tool_calls") or 0),
            llm_calls=int(payload.get("llm_calls") or 0),
            turns=int(payload.get("turns") or 0),
            created_at=str(payload.get("created_at") or ""),
            updated_at=str(payload.get("updated_at") or ""),
            metadata=dict(payload.get("metadata") or {}),
        )


def new_session() -> ChatSession:
    now = _utc_now()
    return ChatSession(session_id=str(uuid.uuid4()), created_at=now, updated_at=now)


def save_session(session: ChatSession, *, root: str | Path | None = None) -> Path:
    runtime_paths.ensure_local_dirs(root)
    session.updated_at = _utc_now()
    path = runtime_paths.chat_sessions_dir(root) / f"{session.session_id}.json"
    path.write_text(
        json.dumps(session.to_dict(), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return path


def load_session(session_id: str, *, root: str | Path | None = None) -> ChatSession:
    path = runtime_paths.chat_sessions_dir(root) / f"{session_id}.json"
    if not path.is_file():
        raise FileNotFoundError(f"Chat session not found: {session_id}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Invalid chat session file")
    return ChatSession.from_dict(payload)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")
