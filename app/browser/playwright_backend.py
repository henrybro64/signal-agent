"""Optional Playwright session backend (#106).

Requires ``pip install -e '.[browser]'`` and ``playwright install chromium``.
"""

from __future__ import annotations

import shutil
import tempfile
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.browser.backend import PageSnapshot
from app.browser.models import InteractiveElement, SessionSnapshot
from app.browser.policy import BrowserPolicy, BrowserPolicyError
from app.browser.safety import detect_challenge

_MAX_ELEMENTS = 150
# Must stay in sync with the evaluate() collector so ref N is locator.nth(N-1).
ELEMENT_SELECTOR = (
    "a[href], button, input, select, textarea, "
    '[role="button"], [role="link"], [role="textbox"]'
)


@dataclass(slots=True)
class _PlaywrightHandle:
    playwright: Any
    context: Any
    page: Any
    profile_dir: Path


@dataclass(slots=True)
class PlaywrightBackend:
    """Persistent Chromium page driven by Playwright (optional extra)."""

    headless: bool = True
    profile_dir: Path | None = None
    _handles: list[_PlaywrightHandle] = field(default_factory=list)

    def open(self, url: str, *, policy: BrowserPolicy) -> PageSnapshot:
        """One-shot convenience wrapper used when ``backend: playwright``."""

        root = self.profile_dir or Path(
            tempfile.mkdtemp(prefix="signal-agent-oneshot-")
        )
        handle, snap = self.session_open(url, policy=policy, profile_dir=root)
        try:
            return PageSnapshot(url=snap.url, title=snap.title, text=snap.text)
        finally:
            self.session_close(handle)

    def screenshot(
        self,
        url: str,
        *,
        policy: BrowserPolicy,
        output_path: Path,
    ) -> PageSnapshot:
        root = self.profile_dir or Path(
            tempfile.mkdtemp(prefix="signal-agent-oneshot-")
        )
        handle, _snap = self.session_open(url, policy=policy, profile_dir=root)
        try:
            session = self.session_screenshot(
                handle,
                policy=policy,
                session_id="oneshot",
                steps_used=1,
                output_path=output_path,
            )
            return PageSnapshot(
                url=session.url,
                title=session.title,
                text=session.text,
                screenshot_path=session.screenshot_path,
            )
        finally:
            self.session_close(handle)

    def session_open(
        self,
        url: str,
        *,
        policy: BrowserPolicy,
        profile_dir: Path,
    ) -> tuple[_PlaywrightHandle, SessionSnapshot]:
        handle = self._launch(policy, profile_dir)
        try:
            self._goto(handle.page, url, policy=policy)
            self._handles.append(handle)
            session_id = str(uuid.uuid4())
            return handle, self._snapshot(handle, session_id=session_id, steps=1)
        except Exception:
            self.session_close(handle)
            raise

    def session_snapshot(
        self,
        handle: _PlaywrightHandle,
        *,
        policy: BrowserPolicy,
        session_id: str,
        steps_used: int,
    ) -> SessionSnapshot:
        _ = policy
        return self._snapshot(handle, session_id=session_id, steps=steps_used)

    def session_click(
        self,
        handle: _PlaywrightHandle,
        element: InteractiveElement,
        *,
        policy: BrowserPolicy,
        session_id: str,
        steps_used: int,
    ) -> SessionSnapshot:
        locator = self._locator(handle.page, element)
        timeout_ms = int(policy.navigation_timeout_seconds * 1000)
        self._run_playwright(
            lambda: locator.click(timeout=timeout_ms),
            action="click",
        )
        self._wait_settled(handle.page, timeout_ms)
        self._assert_page_domain(handle.page, policy)
        return self._snapshot(handle, session_id=session_id, steps=steps_used)

    def session_type(
        self,
        handle: _PlaywrightHandle,
        element: InteractiveElement,
        text: str,
        *,
        submit: bool,
        policy: BrowserPolicy,
        session_id: str,
        steps_used: int,
    ) -> SessionSnapshot:
        locator = self._locator(handle.page, element)
        timeout_ms = int(policy.navigation_timeout_seconds * 1000)
        self._run_playwright(
            lambda: locator.fill(text, timeout=timeout_ms),
            action="type",
        )
        if submit:
            self._run_playwright(lambda: locator.press("Enter"), action="submit")
            self._wait_settled(handle.page, timeout_ms)
        self._assert_page_domain(handle.page, policy)
        return self._snapshot(handle, session_id=session_id, steps=steps_used)

    def session_select(
        self,
        handle: _PlaywrightHandle,
        element: InteractiveElement,
        value: str,
        *,
        policy: BrowserPolicy,
        session_id: str,
        steps_used: int,
    ) -> SessionSnapshot:
        locator = self._locator(handle.page, element)
        self._run_playwright(
            lambda: locator.select_option(value),
            action="select",
        )
        self._assert_page_domain(handle.page, policy)
        return self._snapshot(handle, session_id=session_id, steps=steps_used)

    def session_scroll(
        self,
        handle: _PlaywrightHandle,
        direction: str,
        *,
        policy: BrowserPolicy,
        session_id: str,
        steps_used: int,
    ) -> SessionSnapshot:
        _ = policy
        script = {
            "up": "window.scrollBy(0, -Math.max(400, window.innerHeight * 0.8))",
            "down": "window.scrollBy(0, Math.max(400, window.innerHeight * 0.8))",
            "top": "window.scrollTo(0, 0)",
            "bottom": "window.scrollTo(0, document.body.scrollHeight)",
        }[direction]
        self._run_playwright(
            lambda: handle.page.evaluate(script),
            action="scroll",
        )
        return self._snapshot(handle, session_id=session_id, steps=steps_used)

    def session_back(
        self,
        handle: _PlaywrightHandle,
        *,
        policy: BrowserPolicy,
        session_id: str,
        steps_used: int,
    ) -> SessionSnapshot:
        self._run_playwright(
            lambda: handle.page.go_back(wait_until="domcontentloaded"),
            action="back",
        )
        self._assert_page_domain(handle.page, policy)
        return self._snapshot(handle, session_id=session_id, steps=steps_used)

    def session_screenshot(
        self,
        handle: _PlaywrightHandle,
        *,
        policy: BrowserPolicy,
        session_id: str,
        steps_used: int,
        output_path: Path,
    ) -> SessionSnapshot:
        _ = policy
        output_path.parent.mkdir(parents=True, exist_ok=True)
        self._run_playwright(
            lambda: handle.page.screenshot(path=str(output_path), full_page=False),
            action="screenshot",
        )
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

    def session_close(self, handle: _PlaywrightHandle) -> None:
        try:
            handle.context.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            handle.playwright.stop()
        except Exception:  # noqa: BLE001
            pass
        try:
            shutil.rmtree(handle.profile_dir, ignore_errors=True)
        except Exception:  # noqa: BLE001
            pass
        if handle in self._handles:
            self._handles.remove(handle)

    def _launch(self, policy: BrowserPolicy, profile_dir: Path) -> _PlaywrightHandle:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise BrowserPolicyError(
                "Playwright is not installed. Run: "
                "pip install -e '.[browser]' && playwright install chromium"
            ) from exc
        profile_dir.mkdir(parents=True, exist_ok=True)
        marker = profile_dir / ".signal-agent-browser"
        marker.write_text("signal-agent-isolated-profile\n", encoding="utf-8")
        session_dir = profile_dir / f"session-{uuid.uuid4().hex}"
        session_dir.mkdir(parents=True, exist_ok=True)
        playwright = sync_playwright().start()
        try:
            context = playwright.chromium.launch_persistent_context(
                str(session_dir),
                headless=self.headless,
                java_script_enabled=bool(policy.allow_javascript),
                accept_downloads=False,
            )
            page = context.pages[0] if context.pages else context.new_page()
            page.set_default_timeout(int(policy.navigation_timeout_seconds * 1000))
        except Exception:
            try:
                playwright.stop()
            except Exception:  # noqa: BLE001
                pass
            shutil.rmtree(session_dir, ignore_errors=True)
            raise
        handle = _PlaywrightHandle(
            playwright=playwright,
            context=context,
            page=page,
            profile_dir=session_dir,
        )
        self._install_routes(context, policy)

        def _close_popup(new_page: Any) -> None:  # noqa: ANN401
            if new_page is handle.page:
                return
            try:
                new_page.close()
            except Exception:  # noqa: BLE001
                pass

        context.on("page", _close_popup)
        return handle

    def _install_routes(self, context: Any, policy: BrowserPolicy) -> None:
        def _route_handler(route: Any) -> None:  # noqa: ANN401
            request = route.request
            is_document = request.resource_type == "document"
            try:
                policy.assert_url_allowed(request.url)
            except BrowserPolicyError:
                if not is_document and policy.allow_subresources:
                    route.continue_()
                    return
                route.abort()
                return
            route.continue_()

        context.route("**/*", _route_handler)

    def _goto(self, page: Any, url: str, *, policy: BrowserPolicy) -> None:
        policy.assert_url_allowed(url)
        response = self._run_playwright(
            lambda: page.goto(url, wait_until="domcontentloaded"),
            action="goto",
        )
        self._assert_page_domain(page, policy)
        if response is not None and getattr(response, "status", 0) >= 400:
            raise BrowserPolicyError(
                f"Navigation failed for {url!r}: HTTP {response.status}"
            )

    def _assert_page_domain(self, page: Any, policy: BrowserPolicy) -> None:
        policy.assert_url_allowed(page.url)

    def _locator(self, page: Any, element: InteractiveElement) -> Any:
        if element.ref < 1:
            raise BrowserPolicyError(f"Invalid element ref={element.ref}")
        return page.locator(ELEMENT_SELECTOR).nth(element.ref - 1)

    def _wait_settled(self, page: Any, timeout_ms: int) -> None:
        try:
            page.wait_for_load_state(
                "domcontentloaded",
                timeout=min(5_000, timeout_ms),
            )
        except Exception:
            # Non-navigating controls (buttons that only expand UI).
            pass

    def _run_playwright(self, fn: Any, *, action: str) -> Any:
        try:
            return fn()
        except BrowserPolicyError:
            raise
        except Exception as exc:
            message = str(exc)
            lowered = message.casefold()
            if "net::err_aborted" in lowered or "interrupted" in lowered:
                raise BrowserPolicyError("Navigation blocked by domain policy") from exc
            raise BrowserPolicyError(
                f"Browser {action} failed: {message}"[:200]
            ) from exc

    def _snapshot(
        self,
        handle: _PlaywrightHandle,
        *,
        session_id: str,
        steps: int,
    ) -> SessionSnapshot:
        page = handle.page
        title = page.title() or page.url
        text = page.inner_text("body") if page.locator("body").count() else ""
        html = page.content()
        if len(text.encode("utf-8")) > 2_000_000:
            text = text[:500_000]
        challenge = detect_challenge(title=title, text=text, html=html)
        elements = [] if challenge else self._collect_elements(page)
        return SessionSnapshot(
            session_id=session_id,
            url=page.url,
            title=title,
            text=text,
            elements=elements,
            status="blocked_by_challenge" if challenge else "ok",
            blocked_reason=challenge,
            steps_used=steps,
        )

    def _collect_elements(self, page: Any) -> list[InteractiveElement]:
        script = f"""
() => {{
  const sel = {ELEMENT_SELECTOR!r};
  const nodes = Array.from(document.querySelectorAll(sel));
  return nodes.slice(0, 150).map((el, index) => {{
    const tag = (el.tagName || '').toLowerCase();
    const type = (el.getAttribute('type') || '').toLowerCase();
    let role = (el.getAttribute('role') || '').toLowerCase();
    if (!role) {{
      if (tag === 'a') role = 'link';
      else if (tag === 'button') role = 'button';
      else if (tag === 'select') role = 'combobox';
      else if (tag === 'textarea') role = 'textarea';
      else if (tag === 'input') {{
        role = (type === 'submit' || type === 'button') ? 'button'
          : (type === 'search') ? 'searchbox' : 'textbox';
      }} else role = tag || 'generic';
    }}
    const label = (
      el.getAttribute('aria-label')
      || el.innerText
      || el.getAttribute('placeholder')
      || el.getAttribute('name')
      || el.getAttribute('value')
      || tag
    || '').trim().replace(/\\s+/g, ' ').slice(0, 80);
    return {{
      ref: index + 1,
      role,
      label: label || role,
      url: tag === 'a' ? el.href : null,
      input_type: tag === 'input' ? (type || 'text')
        : (tag === 'textarea' ? 'textarea' : null),
    }};
  }});
}}
"""
        raw = page.evaluate(script) or []
        elements: list[InteractiveElement] = []
        for item in raw[:_MAX_ELEMENTS]:
            elements.append(
                InteractiveElement(
                    ref=int(item.get("ref") or 0),
                    role=str(item.get("role") or "generic"),
                    label=str(item.get("label") or ""),
                    url=item.get("url"),
                    input_type=item.get("input_type"),
                )
            )
        return elements


def playwright_available() -> tuple[bool, str]:
    """Return whether Playwright and a Chromium binary can launch."""

    try:
        import playwright  # noqa: F401
    except ImportError:
        return False, "playwright package not installed (pip install -e '.[browser]')"
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as instance:
            browser = instance.chromium.launch(headless=True)
            browser.close()
        return True, "playwright chromium available"
    except Exception as exc:  # noqa: BLE001
        return False, f"playwright installed but chromium unavailable: {exc}"[:160]
