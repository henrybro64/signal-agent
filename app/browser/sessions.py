"""In-process browser session lifecycle (#106)."""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from app.browser.models import InteractiveElement, SessionSnapshot
from app.browser.policy import BrowserPolicy, BrowserPolicyError
from app.browser.safety import (
    apply_challenge_status,
    assert_element_allowed,
    assert_select_allowed,
    assert_type_allowed,
)


class SessionBackend(Protocol):
    """Backend that can drive a multi-step browsing session."""

    def session_open(
        self,
        url: str,
        *,
        policy: BrowserPolicy,
        profile_dir: Path,
    ) -> tuple[Any, SessionSnapshot]: ...

    def session_snapshot(
        self,
        handle: Any,
        *,
        policy: BrowserPolicy,
        session_id: str,
        steps_used: int,
    ) -> SessionSnapshot: ...

    def session_click(
        self,
        handle: Any,
        element: InteractiveElement,
        *,
        policy: BrowserPolicy,
        session_id: str,
        steps_used: int,
    ) -> SessionSnapshot: ...

    def session_type(
        self,
        handle: Any,
        element: InteractiveElement,
        text: str,
        *,
        submit: bool,
        policy: BrowserPolicy,
        session_id: str,
        steps_used: int,
    ) -> SessionSnapshot: ...

    def session_select(
        self,
        handle: Any,
        element: InteractiveElement,
        value: str,
        *,
        policy: BrowserPolicy,
        session_id: str,
        steps_used: int,
    ) -> SessionSnapshot: ...

    def session_scroll(
        self,
        handle: Any,
        direction: str,
        *,
        policy: BrowserPolicy,
        session_id: str,
        steps_used: int,
    ) -> SessionSnapshot: ...

    def session_back(
        self,
        handle: Any,
        *,
        policy: BrowserPolicy,
        session_id: str,
        steps_used: int,
    ) -> SessionSnapshot: ...

    def session_screenshot(
        self,
        handle: Any,
        *,
        policy: BrowserPolicy,
        session_id: str,
        steps_used: int,
        output_path: Path,
    ) -> SessionSnapshot: ...

    def session_close(self, handle: Any) -> None: ...


@dataclass(slots=True)
class _SessionRecord:
    session_id: str
    handle: Any
    backend: SessionBackend
    policy: BrowserPolicy
    created_at: float
    last_used_at: float
    steps_used: int = 0
    history: list[str] = field(default_factory=list)
    closed: bool = False


@dataclass(slots=True)
class BrowserSessionManager:
    """Bounded session table shared by browser tools."""

    backend: SessionBackend
    profile_dir: Path
    max_sessions: int = 1
    session_ttl_seconds: float = 300.0
    max_steps_per_session: int = 20
    max_interactive_elements: int = 150
    _sessions: dict[str, _SessionRecord] = field(default_factory=dict)
    _lock: threading.RLock = field(default_factory=threading.RLock)

    def open(self, url: str, *, policy: BrowserPolicy) -> SessionSnapshot:
        with self._lock:
            self.cleanup_expired()
            if len(self._sessions) >= self.max_sessions:
                raise BrowserPolicyError(
                    f"max_sessions={self.max_sessions} reached; "
                    "close an existing session first"
                )
            policy.assert_url_allowed(url)
            handle, snap = _unpack_open(
                self.backend.session_open(
                    url,
                    policy=policy,
                    profile_dir=self.profile_dir,
                )
            )
            session_id = snap.session_id or str(uuid.uuid4())
            record = _SessionRecord(
                session_id=session_id,
                handle=handle,
                backend=self.backend,
                policy=policy,
                created_at=time.monotonic(),
                last_used_at=time.monotonic(),
                steps_used=1,
                history=[snap.url],
            )
            snap = self._finalize(record, snap)
            if snap.status == "blocked_by_challenge":
                self.backend.session_close(handle)
                reason = snap.blocked_reason or "blocked_by_challenge"
                raise BrowserPolicyError(f"blocked_by_challenge: {reason}")
            self._sessions[session_id] = record
            return snap

    def snapshot(self, session_id: str) -> SessionSnapshot:
        with self._lock:
            record = self._require(session_id)
            snap = record.backend.session_snapshot(
                record.handle,
                policy=record.policy,
                session_id=record.session_id,
                steps_used=record.steps_used,
            )
            return self._finalize(record, snap, bump_step=False)

    def click(self, session_id: str, ref: int) -> SessionSnapshot:
        with self._lock:
            record = self._require(session_id, consume_step=True)
            element = self._resolve_element(record, ref)
            assert_element_allowed(element)
            if element.role == "link" and element.url:
                record.policy.assert_url_allowed(element.url)
            snap = record.backend.session_click(
                record.handle,
                element,
                policy=record.policy,
                session_id=record.session_id,
                steps_used=record.steps_used,
            )
            return self._after_navigation(record, snap)

    def type_text(
        self,
        session_id: str,
        ref: int,
        text: str,
        *,
        submit: bool = False,
    ) -> SessionSnapshot:
        with self._lock:
            record = self._require(session_id, consume_step=True)
            element = self._resolve_element(record, ref)
            assert_type_allowed(element, text)
            if len(text) > 200:
                raise BrowserPolicyError(
                    "Typed text exceeds 200 characters (search/filter only)"
                )
            snap = record.backend.session_type(
                record.handle,
                element,
                text,
                submit=submit,
                policy=record.policy,
                session_id=record.session_id,
                steps_used=record.steps_used,
            )
            return self._after_navigation(record, snap)

    def select(self, session_id: str, ref: int, value: str) -> SessionSnapshot:
        with self._lock:
            record = self._require(session_id, consume_step=True)
            element = self._resolve_element(record, ref)
            assert_select_allowed(element)
            snap = record.backend.session_select(
                record.handle,
                element,
                value,
                policy=record.policy,
                session_id=record.session_id,
                steps_used=record.steps_used,
            )
            return self._after_navigation(record, snap)

    def scroll(self, session_id: str, direction: str) -> SessionSnapshot:
        with self._lock:
            record = self._require(session_id, consume_step=True)
            direction_n = (direction or "").strip().lower()
            if direction_n not in {"up", "down", "top", "bottom"}:
                raise BrowserPolicyError(
                    "direction must be one of: up, down, top, bottom"
                )
            snap = record.backend.session_scroll(
                record.handle,
                direction_n,
                policy=record.policy,
                session_id=record.session_id,
                steps_used=record.steps_used,
            )
            return self._finalize(record, snap)

    def back(self, session_id: str) -> SessionSnapshot:
        with self._lock:
            record = self._require(session_id, consume_step=True)
            snap = record.backend.session_back(
                record.handle,
                policy=record.policy,
                session_id=record.session_id,
                steps_used=record.steps_used,
            )
            return self._after_navigation(record, snap)

    def screenshot(self, session_id: str, output_path: Path) -> SessionSnapshot:
        with self._lock:
            record = self._require(session_id, consume_step=True)
            snap = record.backend.session_screenshot(
                record.handle,
                policy=record.policy,
                session_id=record.session_id,
                steps_used=record.steps_used,
                output_path=output_path,
            )
            return self._finalize(record, snap)

    def close(self, session_id: str) -> None:
        with self._lock:
            record = self._sessions.pop(session_id, None)
            if record is None:
                return
            record.closed = True
            record.backend.session_close(record.handle)

    def close_all(self) -> int:
        with self._lock:
            ids = list(self._sessions)
            for session_id in ids:
                self.close(session_id)
            return len(ids)

    def cleanup_expired(self) -> int:
        with self._lock:
            now = time.monotonic()
            expired = [
                session_id
                for session_id, record in self._sessions.items()
                if now - record.last_used_at >= self.session_ttl_seconds
            ]
            for session_id in expired:
                self.close(session_id)
            return len(expired)

    def _require(
        self,
        session_id: str,
        *,
        consume_step: bool = False,
    ) -> _SessionRecord:
        self.cleanup_expired()
        record = self._sessions.get(session_id)
        if record is None or record.closed:
            raise BrowserPolicyError(f"Unknown or closed session: {session_id}")
        if consume_step and record.steps_used >= self.max_steps_per_session:
            self.close(session_id)
            raise BrowserPolicyError(
                f"max_steps_per_session={self.max_steps_per_session} exceeded"
            )
        if consume_step:
            record.steps_used += 1
        record.last_used_at = time.monotonic()
        return record

    @staticmethod
    def _resolve_element(
        record: _SessionRecord,
        ref: int,
    ) -> InteractiveElement:
        snap = record.backend.session_snapshot(
            record.handle,
            policy=record.policy,
            session_id=record.session_id,
            steps_used=record.steps_used,
        )
        for element in snap.elements:
            if element.ref == ref:
                return element
        raise BrowserPolicyError(f"No interactive element with ref={ref}")

    def _after_navigation(
        self,
        record: _SessionRecord,
        snap: SessionSnapshot,
    ) -> SessionSnapshot:
        # Domain policy on every resulting URL (redirects included).
        record.policy.assert_url_allowed(snap.url)
        if not record.history or record.history[-1] != snap.url:
            record.history.append(snap.url)
        finalized = self._finalize(record, snap)
        if finalized.status == "blocked_by_challenge":
            self.close(record.session_id)
            reason = finalized.blocked_reason or "blocked_by_challenge"
            raise BrowserPolicyError(f"blocked_by_challenge: {reason}")
        return finalized

    def _finalize(
        self,
        record: _SessionRecord,
        snap: SessionSnapshot,
        *,
        bump_step: bool = True,
    ) -> SessionSnapshot:
        _ = bump_step
        capped = list(snap.elements[: self.max_interactive_elements])
        text = snap.text
        max_bytes = int(record.policy.max_page_bytes)
        encoded = text.encode("utf-8")
        if len(encoded) > max_bytes:
            text = encoded[:max_bytes].decode("utf-8", errors="ignore")
        rebuilt = SessionSnapshot(
            session_id=record.session_id,
            url=snap.url,
            title=snap.title,
            text=text,
            elements=capped,
            status=snap.status,
            screenshot_path=snap.screenshot_path,
            blocked_reason=snap.blocked_reason,
            steps_used=record.steps_used,
        )
        return apply_challenge_status(rebuilt)


def _unpack_open(opened: Any) -> tuple[Any, SessionSnapshot]:
    if not isinstance(opened, tuple) or len(opened) != 2:
        raise BrowserPolicyError("session backend returned an invalid open result")
    handle, snap = opened
    if not isinstance(snap, SessionSnapshot):
        raise BrowserPolicyError("session backend returned an invalid snapshot")
    return handle, snap


_MANAGERS: dict[str, BrowserSessionManager] = {}
_MANAGERS_LOCK = threading.Lock()


def get_session_manager(
    *,
    root: Path,
    backend: SessionBackend,
    profile_dir: Path,
    max_sessions: int,
    session_ttl_seconds: float,
    max_steps_per_session: int,
    max_interactive_elements: int,
) -> BrowserSessionManager:
    key = str(root.resolve())
    backend_name = type(backend).__name__
    with _MANAGERS_LOCK:
        existing = _MANAGERS.get(key)
        if existing is not None:
            if type(existing.backend).__name__ == backend_name:
                return existing
            existing.close_all()
        manager = BrowserSessionManager(
            backend=backend,
            profile_dir=profile_dir,
            max_sessions=max_sessions,
            session_ttl_seconds=session_ttl_seconds,
            max_steps_per_session=max_steps_per_session,
            max_interactive_elements=max_interactive_elements,
        )
        _MANAGERS[key] = manager
        return manager


def close_all_session_managers() -> int:
    """Close every in-process session (browser stop / process teardown)."""

    with _MANAGERS_LOCK:
        managers = list(_MANAGERS.values())
        _MANAGERS.clear()
    closed = 0
    for manager in managers:
        closed += manager.close_all()
    return closed


def drop_session_manager(root: Path) -> None:
    key = str(root.resolve())
    with _MANAGERS_LOCK:
        manager = _MANAGERS.pop(key, None)
    if manager is not None:
        manager.close_all()
