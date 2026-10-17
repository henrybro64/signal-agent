"""Fake multi-page session backend for CI (#106)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from app.browser.backend import resolve_allowed_url
from app.browser.fixtures import FixturePage, interactive_fixture_pages
from app.browser.models import InteractiveElement, SessionSnapshot
from app.browser.policy import BrowserPolicy, BrowserPolicyError


@dataclass(slots=True)
class _FakeHandle:
    url: str
    history: list[str] = field(default_factory=list)
    typed: dict[int, str] = field(default_factory=dict)
    selected: dict[int, str] = field(default_factory=dict)
    scroll_y: int = 0


@dataclass(slots=True)
class FakeSessionBackend:
    """In-memory page graph with interactive elements (no Playwright)."""

    pages: dict[str, FixturePage] = field(default_factory=interactive_fixture_pages)

    def session_open(
        self,
        url: str,
        *,
        policy: BrowserPolicy,
        profile_dir: Path,
    ) -> tuple[_FakeHandle, SessionSnapshot]:
        _ = profile_dir
        final = resolve_allowed_url(url, policy=policy, network=False)
        handle = _FakeHandle(url=final, history=[final])
        return handle, self._snapshot(handle, session_id=str(uuid.uuid4()), steps=1)

    def session_snapshot(
        self,
        handle: _FakeHandle,
        *,
        policy: BrowserPolicy,
        session_id: str,
        steps_used: int,
    ) -> SessionSnapshot:
        _ = policy
        return self._snapshot(handle, session_id=session_id, steps=steps_used)

    def session_click(
        self,
        handle: _FakeHandle,
        element: InteractiveElement,
        *,
        policy: BrowserPolicy,
        session_id: str,
        steps_used: int,
    ) -> SessionSnapshot:
        if element.role == "link":
            if not element.url:
                raise BrowserPolicyError("Link element has no URL")
            policy.assert_url_allowed(element.url)
            handle.url = element.url
            handle.history.append(element.url)
        # Buttons like "Show more" stay on the same page.
        return self._snapshot(handle, session_id=session_id, steps=steps_used)

    def session_type(
        self,
        handle: _FakeHandle,
        element: InteractiveElement,
        text: str,
        *,
        submit: bool,
        policy: BrowserPolicy,
        session_id: str,
        steps_used: int,
    ) -> SessionSnapshot:
        _ = policy
        handle.typed[element.ref] = text
        if submit and "search" in (element.label or "").casefold():
            parsed = urlparse(handle.url)
            query = parse_qs(parsed.query)
            query["q"] = [text]
            new_query = urlencode({k: v[0] for k, v in query.items()})
            handle.url = urlunparse(parsed._replace(query=new_query))
            handle.history.append(handle.url)
        return self._snapshot(handle, session_id=session_id, steps=steps_used)

    def session_select(
        self,
        handle: _FakeHandle,
        element: InteractiveElement,
        value: str,
        *,
        policy: BrowserPolicy,
        session_id: str,
        steps_used: int,
    ) -> SessionSnapshot:
        _ = policy
        handle.selected[element.ref] = value
        return self._snapshot(handle, session_id=session_id, steps=steps_used)

    def session_scroll(
        self,
        handle: _FakeHandle,
        direction: str,
        *,
        policy: BrowserPolicy,
        session_id: str,
        steps_used: int,
    ) -> SessionSnapshot:
        _ = policy
        delta = {"up": -400, "down": 400, "top": -10_000, "bottom": 10_000}
        handle.scroll_y = max(0, handle.scroll_y + delta.get(direction, 0))
        return self._snapshot(handle, session_id=session_id, steps=steps_used)

    def session_back(
        self,
        handle: _FakeHandle,
        *,
        policy: BrowserPolicy,
        session_id: str,
        steps_used: int,
    ) -> SessionSnapshot:
        _ = policy
        if len(handle.history) < 2:
            raise BrowserPolicyError("No previous page in session history")
        handle.history.pop()
        handle.url = handle.history[-1]
        return self._snapshot(handle, session_id=session_id, steps=steps_used)

    def session_screenshot(
        self,
        handle: _FakeHandle,
        *,
        policy: BrowserPolicy,
        session_id: str,
        steps_used: int,
        output_path: Path,
    ) -> SessionSnapshot:
        _ = policy
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"\x89PNG\r\n\x1a\nfake-session-screenshot")
        snap = self._snapshot(handle, session_id=session_id, steps=steps_used)
        return SessionSnapshot(
            session_id=snap.session_id,
            url=snap.url,
            title=snap.title,
            text=snap.text,
            elements=list(snap.elements),
            status=snap.status,
            screenshot_path=str(output_path),
            blocked_reason=snap.blocked_reason,
            steps_used=snap.steps_used,
        )

    def session_close(self, handle: _FakeHandle) -> None:  # skipcq: PYL-R0201
        _ = self.pages
        handle.history.clear()
        handle.typed.clear()
        handle.selected.clear()

    def _snapshot(
        self,
        handle: _FakeHandle,
        *,
        session_id: str,
        steps: int,
    ) -> SessionSnapshot:
        page = self.pages.get(handle.url)
        if page is None:
            # Unknown URL inside fixture host: empty page.
            page = FixturePage(title=handle.url, text=f"Fake page for {handle.url}")
        text = page.text
        if handle.scroll_y:
            text = f"{text} [scrolled={handle.scroll_y}]"
        if handle.typed:
            text = f"{text} [typed={handle.typed}]"
        if handle.selected:
            text = f"{text} [selected={handle.selected}]"
        return SessionSnapshot(
            session_id=session_id,
            url=handle.url,
            title=page.title,
            text=text,
            elements=list(page.elements),
            steps_used=steps,
        )
