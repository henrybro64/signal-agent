"""Tests for interactive browser sessions (#106)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from app.browser.fake_session import FakeSessionBackend
from app.browser.fixtures import (
    CHALLENGE_URL,
    JOBS_PAGE_1,
    JOBS_PAGE_2,
    JOBS_PAGE_3,
)
from app.browser.policy import BrowserPolicy, BrowserPolicyError
from app.browser.safety import assert_element_action_allowed, detect_challenge
from app.browser.sessions import (
    BrowserSessionManager,
    close_all_session_managers,
    drop_session_manager,
)
from app.core.config import load_config
from app.setup import SetupOptions, run_setup, validate_local_setup
from app.tools import ToolContext, build_tool_registry
from app.tools.permissions import (
    PermissionClass,
    PermissionPolicyConfig,
    build_permission_policy,
)


def _policy(**kwargs: object) -> BrowserPolicy:
    defaults: dict[str, object] = {
        "allowed_domains": ["fixture.local", "example.com"],
        "navigation_timeout_seconds": 5.0,
        "max_page_bytes": 500_000,
    }
    defaults.update(kwargs)
    return BrowserPolicy(**defaults)  # type: ignore[arg-type]


def _manager(
    tmp_path: Path,
    *,
    max_sessions: int = 1,
    session_ttl_seconds: float = 300.0,
    max_steps_per_session: int = 20,
) -> BrowserSessionManager:
    drop_session_manager(tmp_path)
    return BrowserSessionManager(
        backend=FakeSessionBackend(),
        profile_dir=tmp_path / "profile",
        max_sessions=max_sessions,
        session_ttl_seconds=session_ttl_seconds,
        max_steps_per_session=max_steps_per_session,
    )


def test_session_paginate_three_pages(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    snap = manager.open(JOBS_PAGE_1, policy=_policy())
    assert snap.title == "Jobs page 1"
    assert any(el.label == "Next page" for el in snap.elements)

    snap = manager.click(snap.session_id, 1)
    assert snap.url == JOBS_PAGE_2
    assert "page 2" in snap.text

    snap = manager.click(snap.session_id, 1)
    assert snap.url == JOBS_PAGE_3
    assert "page 3" in snap.text
    assert "Staff Agent Engineer" in snap.text
    manager.close(snap.session_id)


def test_off_domain_link_refused(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    snap = manager.open(JOBS_PAGE_1, policy=_policy())
    with pytest.raises(BrowserPolicyError, match="not in browser.allowed_domains"):
        manager.click(snap.session_id, 6)
    manager.close(snap.session_id)


def test_password_and_apply_refused(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    snap = manager.open(JOBS_PAGE_1, policy=_policy())
    passwords = [el for el in snap.elements if el.input_type == "password"]
    apply_btns = [el for el in snap.elements if "Apply" in el.label]
    assert passwords
    assert apply_btns
    password = passwords[0]
    apply_btn = apply_btns[0]
    with pytest.raises(BrowserPolicyError, match="password"):
        assert_element_action_allowed(password, action="type")
    with pytest.raises(BrowserPolicyError, match="password"):
        manager.type_text(snap.session_id, password.ref, "secret")
    with pytest.raises(BrowserPolicyError, match="apply"):
        manager.click(snap.session_id, apply_btn.ref)
    messages = [el for el in snap.elements if "Message recruiter" in el.label]
    assert messages
    with pytest.raises(BrowserPolicyError, match="message recruiter"):
        manager.click(snap.session_id, messages[0].ref)
    manager.close(snap.session_id)


def test_challenge_page_blocked(tmp_path: Path) -> None:
    reason = detect_challenge(
        title="Just a moment...",
        text="Checking your browser before accessing fixture.local",
    )
    assert reason is not None
    assert detect_challenge(title="News", text="We recapture market share") is None
    manager = _manager(tmp_path)
    with pytest.raises(BrowserPolicyError, match="blocked_by_challenge"):
        manager.open(CHALLENGE_URL, policy=_policy())


def test_max_steps_and_ttl(tmp_path: Path) -> None:
    manager = _manager(tmp_path, max_steps_per_session=2, session_ttl_seconds=1.0)
    snap = manager.open(JOBS_PAGE_1, policy=_policy())
    session_id = snap.session_id
    manager.scroll(session_id, "down")
    with pytest.raises(BrowserPolicyError, match="max_steps"):
        manager.scroll(session_id, "down")

    manager = _manager(tmp_path, session_ttl_seconds=0.01)
    snap = manager.open(JOBS_PAGE_1, policy=_policy())
    session_id = snap.session_id
    import time

    time.sleep(0.02)
    assert manager.cleanup_expired() >= 1
    with pytest.raises(BrowserPolicyError, match="Unknown or closed"):
        manager.snapshot(session_id)


def test_max_sessions(tmp_path: Path) -> None:
    manager = _manager(tmp_path, max_sessions=1)
    snap = manager.open(JOBS_PAGE_1, policy=_policy())
    with pytest.raises(BrowserPolicyError, match="max_sessions"):
        manager.open(JOBS_PAGE_2, policy=_policy())
    manager.close(snap.session_id)


def test_type_search_and_back(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    snap = manager.open(JOBS_PAGE_1, policy=_policy())
    snap = manager.type_text(snap.session_id, 3, "python", submit=True)
    assert "q=python" in snap.url
    manager.close(snap.session_id)

    manager = _manager(tmp_path)
    snap = manager.open(JOBS_PAGE_1, policy=_policy())
    snap = manager.click(snap.session_id, 1)
    assert snap.url == JOBS_PAGE_2
    snap = manager.back(snap.session_id)
    assert snap.url == JOBS_PAGE_1
    manager.close(snap.session_id)


def test_session_tools_via_registry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SIGNAL_AGENT_BROWSER_BACKEND", "fake")
    run_setup(
        root=tmp_path,
        options=SetupOptions(provider="fake", non_interactive=True, force=True),
    )
    cfg_path = tmp_path / "config.yaml"
    payload = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    browser = payload.setdefault("browser", {})
    browser["enabled"] = True
    browser["allowed_domains"] = ["fixture.local", "example.com"]
    browser["max_sessions"] = 2
    browser["max_steps_per_session"] = 20
    cfg_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    from app.skills import enable_skill

    enable_skill("browser", root=tmp_path, config_path=cfg_path)
    drop_session_manager(tmp_path)
    close_all_session_managers()

    registry = build_tool_registry(
        allowlist=[
            "browser_open",
            "browser_click",
            "browser_snapshot",
            "browser_close",
            "browser_type",
        ],
        include_browser=True,
        enabled_skill_tools={
            "browser_open",
            "browser_click",
            "browser_snapshot",
            "browser_close",
            "browser_type",
            "browser_navigate",
            "browser_read_page",
            "browser_screenshot",
            "browser_select",
            "browser_scroll",
            "browser_back",
        },
        root=tmp_path,
        config_path=cfg_path,
    )
    assert registry.get("browser_open").permission == PermissionClass.EXECUTE
    config = load_config(cfg_path)
    ctx = ToolContext(
        config=config,
        db_path=tmp_path / "data" / "signals.sqlite3",
        root=tmp_path,
        config_path=cfg_path,
        permission_policy=build_permission_policy(
            PermissionPolicyConfig(
                classes={"execute": "allow"},
                max_side_effect_actions=20,
            )
        ),
    )
    opened = registry.invoke(
        "browser_open",
        {"url": JOBS_PAGE_1},
        context=ctx,
    )
    assert opened.ok is True
    session_id = opened.data["session_id"]
    assert opened.data["elements"]

    clicked = registry.invoke(
        "browser_click",
        {"session_id": session_id, "ref": 1},
        context=ctx,
    )
    assert clicked.ok is True
    assert clicked.data["url"] == JOBS_PAGE_2

    # Off-domain link is on page 1 (ref 6).
    registry.invoke("browser_close", {"session_id": session_id}, context=ctx)
    reopen = registry.invoke(
        "browser_open",
        {"url": JOBS_PAGE_1},
        context=ctx,
    )
    assert reopen.ok is True
    denied = registry.invoke(
        "browser_click",
        {"session_id": reopen.data["session_id"], "ref": 6},
        context=ctx,
    )
    assert denied.ok is False
    assert "allowed_domains" in (denied.error or "")

    closed = registry.invoke(
        "browser_close",
        {"session_id": reopen.data["session_id"]},
        context=ctx,
    )
    assert closed.ok is True
    close_all_session_managers()


def test_config_parses_session_limits(tmp_path: Path) -> None:
    run_setup(
        root=tmp_path,
        options=SetupOptions(provider="fake", non_interactive=True, force=True),
    )
    cfg = tmp_path / "config.yaml"
    payload = yaml.safe_load(cfg.read_text(encoding="utf-8")) or {}
    browser = payload.setdefault("browser", {})
    browser.update(
        {
            "enabled": True,
            "backend": "fake",
            "max_sessions": 2,
            "session_ttl_seconds": 60,
            "max_steps_per_session": 10,
            "max_interactive_elements": 50,
            "allowed_domains": ["fixture.local"],
        }
    )
    cfg.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    loaded = load_config(cfg)
    assert loaded.browser.max_sessions == 2
    assert loaded.browser.session_ttl_seconds == 60.0
    assert loaded.browser.max_steps_per_session == 10
    assert loaded.browser.max_interactive_elements == 50
    assert loaded.browser.backend == "fake"
    assert loaded.browser.allow_subresources is False


def test_snapshot_puts_elements_before_text_for_chat() -> None:
    from app.browser.models import InteractiveElement, SessionSnapshot

    snap = SessionSnapshot(
        session_id="sess-1",
        url="https://fixture.local/jobs",
        title="Jobs",
        text="Job results " + ("word " * 400),
        elements=[
            InteractiveElement(
                ref=1,
                role="link",
                label="Next page",
                url="https://fixture.local/jobs?page=2",
            )
        ],
    )
    data = snap.to_tool_data()
    assert list(data.keys())[0] == "formatted"
    formatted = str(data["formatted"])
    assert formatted.index("elements:") < formatted.index("\ntext:")
    payload = json.dumps({"ok": True, "data": data}, ensure_ascii=False)[:1200]
    assert "[1]" in payload
    assert "Next page" in payload


def test_allow_subresources_does_not_bypass_denylist() -> None:
    policy = BrowserPolicy(
        allowed_domains=["example.com"],
        denied_domains=["tracker.example"],
        allow_subresources=True,
    )
    policy.assert_url_not_denied("https://cdn.other/style.css")
    with pytest.raises(BrowserPolicyError, match="denied"):
        policy.assert_url_not_denied("https://tracker.example/pixel.js")
    with pytest.raises(BrowserPolicyError, match="scheme"):
        policy.assert_url_not_denied("file:///etc/passwd")
    with pytest.raises(BrowserPolicyError, match="allowed_domains"):
        policy.assert_url_allowlisted("https://cdn.other/style.css")


def test_browser_close_after_side_effect_budget(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SIGNAL_AGENT_BROWSER_BACKEND", "fake")
    run_setup(
        root=tmp_path,
        options=SetupOptions(provider="fake", non_interactive=True, force=True),
    )
    cfg_path = tmp_path / "config.yaml"
    payload = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    browser = payload.setdefault("browser", {})
    browser["enabled"] = True
    browser["allowed_domains"] = ["fixture.local"]
    cfg_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    from app.skills import enable_skill

    enable_skill("browser", root=tmp_path, config_path=cfg_path)
    drop_session_manager(tmp_path)
    close_all_session_managers()
    registry = build_tool_registry(
        allowlist=["browser_open", "browser_close"],
        include_browser=True,
        enabled_skill_tools={"browser_open", "browser_close"},
        root=tmp_path,
        config_path=cfg_path,
    )
    config = load_config(cfg_path)
    ctx = ToolContext(
        config=config,
        db_path=tmp_path / "data" / "signals.sqlite3",
        root=tmp_path,
        config_path=cfg_path,
        permission_policy=build_permission_policy(
            PermissionPolicyConfig(
                classes={"execute": "allow"},
                max_side_effect_actions=1,
            )
        ),
    )
    opened = registry.invoke("browser_open", {"url": JOBS_PAGE_1}, context=ctx)
    assert opened.ok is True
    closed = registry.invoke(
        "browser_close",
        {"session_id": opened.data["session_id"]},
        context=ctx,
    )
    assert closed.ok is True
    close_all_session_managers()


def test_select_screenshot_file_and_non_search_type(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    snap = manager.open(JOBS_PAGE_1, policy=_policy())
    combos = [el for el in snap.elements if el.role == "combobox"]
    notes = [el for el in snap.elements if "Cover letter" in el.label]
    files = [el for el in snap.elements if el.input_type == "file"]
    assert combos
    assert notes
    assert files
    selected = manager.select(snap.session_id, combos[0].ref, "Taipei")
    assert "selected=" in selected.text
    shot = tmp_path / "session.png"
    captured = manager.screenshot(snap.session_id, shot)
    assert shot.is_file()
    assert captured.screenshot_path == str(shot)
    with pytest.raises(BrowserPolicyError, match="search/filter"):
        manager.type_text(snap.session_id, notes[0].ref, "hello")
    with pytest.raises(BrowserPolicyError, match="File uploads"):
        manager.click(snap.session_id, files[0].ref)
    manager.close(snap.session_id)


def test_session_backend_chromium_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.browser.backend import select_backend, select_session_backend
    from app.browser.playwright_backend import PlaywrightBackend

    monkeypatch.delenv("SIGNAL_AGENT_BROWSER_BACKEND", raising=False)
    with pytest.raises(BrowserPolicyError, match="playwright"):
        select_session_backend(backend_name="chromium")
    with pytest.raises(BrowserPolicyError, match="playwright"):
        select_session_backend(backend_name=None)
    fake = select_session_backend(backend_name="fake")
    assert type(fake).__name__ == "FakeSessionBackend"
    backend = select_backend(
        executable=None,
        profile_dir=tmp_path / "unused",
        headless=True,
        backend_name="playwright",
    )
    assert isinstance(backend, PlaywrightBackend)


def test_playwright_locator_uses_nth_for_ref() -> None:
    from app.browser.models import InteractiveElement
    from app.browser.playwright_backend import ELEMENT_SELECTOR, nth_locator

    class _Locator:
        def __init__(self) -> None:
            self.nth_calls: list[int] = []

        def nth(self, index: int):
            self.nth_calls.append(index)
            return self

    class _Page:
        def __init__(self) -> None:
            self.selector: str | None = None
            self.locator_obj = _Locator()

        def locator(self, selector: str):
            self.selector = selector
            return self.locator_obj

    page = _Page()
    element = InteractiveElement(ref=3, role="button", label="Same")
    nth_locator(page, element)
    assert page.selector == ELEMENT_SELECTOR
    assert page.locator_obj.nth_calls == [2]


def test_doctor_warns_when_playwright_backend_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_setup(
        root=tmp_path,
        options=SetupOptions(provider="fake", non_interactive=True, force=True),
    )
    cfg = tmp_path / "config.yaml"
    payload = yaml.safe_load(cfg.read_text(encoding="utf-8")) or {}
    browser = payload.setdefault("browser", {})
    browser["backend"] = "playwright"
    cfg.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    monkeypatch.setattr(
        "app.browser.playwright_backend.playwright_available",
        lambda: (False, "playwright package not installed"),
    )
    report = validate_local_setup(root=tmp_path)
    issues = [issue for issue in report.issues if issue.code == "browser_playwright"]
    assert issues
    assert issues[0].severity == "warning"
    assert "not installed" in issues[0].message


def test_playwright_session_smoke_nth_and_select(tmp_path: Path) -> None:
    pytest.importorskip("playwright.sync_api")
    from app.browser.playwright_backend import PlaywrightBackend, playwright_available

    ok, detail = playwright_available()
    if not ok:
        pytest.skip(detail)

    html = (
        b"<!doctype html><title>nth-ref</title><body>"
        b"<button>Same</button><button>Same</button>"
        b'<select aria-label="Location filter">'
        b'<option value="tw">Taiwan</option>'
        b'<option value="us">US</option>'
        b"</select>"
        b'<input type="search" placeholder="Search jobs">'
        b"</body>"
    )
    server, url = _start_html_server(html)
    backend = PlaywrightBackend(headless=True, profile_dir=tmp_path / "pw-profile")
    policy = _policy(allowed_domains=["127.0.0.1"], allow_javascript=True)
    handle = None
    try:
        handle, snap = backend.session_open(
            url, policy=policy, profile_dir=tmp_path / "pw-profile"
        )
        buttons = [el for el in snap.elements if el.role == "button"]
        combos = [el for el in snap.elements if el.role in {"combobox", "select"}]
        assert len(buttons) >= 2
        assert buttons[0].ref != buttons[1].ref
        clicked = backend.session_click(
            handle,
            buttons[1],
            policy=policy,
            session_id=snap.session_id,
            steps_used=2,
        )
        assert clicked.status == "ok"
        assert combos
        selected = backend.session_select(
            handle,
            combos[0],
            "us",
            policy=policy,
            session_id=snap.session_id,
            steps_used=3,
        )
        assert selected.status == "ok"
    finally:
        if handle is not None:
            backend.session_close(handle)
        server.shutdown()


def _start_html_server(html: bytes):
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(html)))
            self.end_headers()
            self.wfile.write(html)

        def log_message(self, *_args: object) -> None:
            return None

    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[:2]
    return server, f"http://{host}:{port}/"
