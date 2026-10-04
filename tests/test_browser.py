"""Tests for optional browser service and tools (#87)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml

from app.browser import (
    BrowserPolicy,
    BrowserPolicyError,
    BrowserServiceError,
    browser_status,
    format_browser_status,
    run_browser_test,
    start_browser,
    stop_browser,
)
from app.browser.backend import FakeBrowserBackend, select_backend
from app.browser.fixtures import FIXTURE_URL
from app.cli import main
from app.setup import SetupOptions, run_setup
from app.tools import ToolContext, build_tool_registry
from app.tools.permissions import (
    PermissionClass,
    PermissionPolicyConfig,
    build_permission_policy,
)


def _prepare(tmp_path: Path, *, enable_browser: bool = True) -> Path:
    run_setup(
        root=tmp_path,
        options=SetupOptions(provider="fake", non_interactive=True, force=True),
    )
    os.environ["SIGNAL_AGENT_BROWSER_BACKEND"] = "fake"
    cfg_path = tmp_path / "config.yaml"
    if enable_browser:
        _set_browser_enabled(cfg_path, enabled=True)
    return cfg_path


def _set_browser_enabled(cfg_path: Path, *, enabled: bool) -> None:
    payload = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    browser = payload.setdefault("browser", {})
    browser["enabled"] = enabled
    browser.setdefault("headless", True)
    browser.setdefault(
        "allowed_domains",
        ["fixture.local", "example.com"],
    )
    browser.setdefault("allow_downloads", False)
    browser.setdefault("allow_javascript", False)
    cfg_path.write_text(
        yaml.safe_dump(payload, sort_keys=False),
        encoding="utf-8",
    )


def test_browser_lifecycle_and_fixture_test(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SIGNAL_AGENT_BROWSER_BACKEND", "fake")
    cfg = _prepare(tmp_path)
    status = start_browser(root=tmp_path, config_path=cfg)
    assert status.running
    assert status.enabled
    assert status.ready
    assert Path(status.profile_dir).is_dir()
    assert (Path(status.profile_dir) / ".signal-agent-browser").is_file()
    result = run_browser_test(root=tmp_path, config_path=cfg)
    assert result["ok"] is True
    assert "signal-agent" in (result.get("title") or "").lower() or result.get(
        "text_preview"
    )
    text = format_browser_status(browser_status(root=tmp_path, config_path=cfg))
    assert "Backend: fake" in text
    assert "Enabled: True" in text
    stop_browser(root=tmp_path)
    stopped = browser_status(root=tmp_path, config_path=cfg)
    assert stopped.running is False
    assert stopped.ready is False


def test_disabled_browser_refuses_start(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SIGNAL_AGENT_BROWSER_BACKEND", "fake")
    cfg = _prepare(tmp_path, enable_browser=False)
    with pytest.raises(BrowserServiceError, match="disabled"):
        start_browser(root=tmp_path, config_path=cfg)


def test_missing_chromium_does_not_silently_fake(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SIGNAL_AGENT_BROWSER_BACKEND", raising=False)
    cfg = _prepare(tmp_path)
    # Clear explicit fake from prepare helper env and config.
    os.environ.pop("SIGNAL_AGENT_BROWSER_BACKEND", None)
    payload = yaml.safe_load(cfg.read_text(encoding="utf-8"))
    payload["browser"].pop("backend", None)
    payload["browser"]["enabled"] = True
    cfg.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    monkeypatch.setattr(
        "app.browser.service.detect_browser",
        lambda: type(
            "D",
            (),
            {
                "ready": False,
                "executable": None,
                "detail": "no browser on PATH",
            },
        )(),
    )
    with pytest.raises(BrowserServiceError, match="dependencies missing"):
        start_browser(root=tmp_path, config_path=cfg)

    with pytest.raises(BrowserPolicyError, match="not available"):
        select_backend(
            executable=None,
            profile_dir=tmp_path / "profile",
            headless=True,
            force_fake=False,
        )


def test_redirect_hop_must_be_allowlisted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.browser.backend import resolve_allowed_url

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *args):  # noqa: ANN002
            return False

        def geturl(self) -> str:
            return "https://evil.example/"

    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda *args, **kwargs: _Resp(),
    )
    with pytest.raises(BrowserPolicyError, match="not in browser.allowed_domains"):
        resolve_allowed_url(
            "https://example.com/",
            policy=BrowserPolicy(allowed_domains=["example.com"]),
            network=True,
        )


def test_downloads_not_supported() -> None:
    backend = FakeBrowserBackend(fixtures={FIXTURE_URL: ("t", "ok")})
    with pytest.raises(BrowserPolicyError, match="not supported"):
        backend.open(
            FIXTURE_URL,
            policy=BrowserPolicy(
                allowed_domains=["fixture.local"],
                allow_downloads=True,
            ),
        )


def test_domain_policy_and_timeout_bounds() -> None:
    policy = BrowserPolicy(allowed_domains=["example.com"], max_page_bytes=20)
    with pytest.raises(BrowserPolicyError, match="not in browser.allowed_domains"):
        policy.assert_url_allowed("https://evil.example/path")
    with pytest.raises(BrowserPolicyError, match="denied"):
        BrowserPolicy(
            allowed_domains=["example.com"],
            denied_domains=["evil.example.com"],
        ).assert_url_allowed("https://evil.example.com/")
    backend = FakeBrowserBackend(
        fixtures={FIXTURE_URL: ("t", "x" * 100)},
    )
    with pytest.raises(BrowserPolicyError, match="max_page_bytes"):
        backend.open(
            FIXTURE_URL,
            policy=BrowserPolicy(allowed_domains=["fixture.local"], max_page_bytes=10),
        )


def test_browser_tools_honor_permission_policy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SIGNAL_AGENT_BROWSER_BACKEND", "fake")
    cfg_path = _prepare(tmp_path)
    from app.skills import enable_skill

    enable_skill("browser", root=tmp_path, config_path=cfg_path)
    registry = build_tool_registry(
        allowlist=["browser_navigate", "browser_read_page"],
        include_browser=True,
        enabled_skill_tools={
            "browser_navigate",
            "browser_read_page",
            "browser_screenshot",
        },
        root=tmp_path,
        config_path=cfg_path,
    )
    denied = registry.invoke(
        "browser_navigate",
        {"url": FIXTURE_URL},
        context=ToolContext(
            permission_policy=build_permission_policy(
                PermissionPolicyConfig(classes={"execute": "disabled"})
            )
        ),
    )
    assert denied.ok is False
    assert denied.metadata["code"] == "permission_denied"

    from app.core.config import load_config

    config = load_config(cfg_path)
    allowed = registry.invoke(
        "browser_read_page",
        {"url": FIXTURE_URL},
        context=ToolContext(
            config=config,
            db_path=tmp_path / "data" / "signals.sqlite3",
            root=tmp_path,
            config_path=cfg_path,
            permission_policy=build_permission_policy(
                PermissionPolicyConfig(classes={"execute": "allow"})
            ),
        ),
    )
    assert allowed.ok is True
    assert allowed.data["title"]
    assert registry.get("browser_navigate").permission == PermissionClass.EXECUTE

    # disabled config must refuse tools even with execute=allow
    _set_browser_enabled(cfg_path, enabled=False)
    config = load_config(cfg_path)
    blocked = registry.invoke(
        "browser_navigate",
        {"url": FIXTURE_URL},
        context=ToolContext(
            config=config,
            db_path=tmp_path / "data" / "signals.sqlite3",
            root=tmp_path,
            config_path=cfg_path,
            permission_policy=build_permission_policy(
                PermissionPolicyConfig(classes={"execute": "allow"})
            ),
        ),
    )
    assert blocked.ok is False
    assert "disabled" in (blocked.error or "").lower()


def test_browser_cli(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("SIGNAL_AGENT_BROWSER_BACKEND", "fake")
    _prepare(tmp_path)
    assert main(["--root", str(tmp_path), "browser", "status"]) == 0
    assert "Enabled:" in capsys.readouterr().out
    assert main(["--root", str(tmp_path), "browser", "start"]) == 0
    assert main(["--root", str(tmp_path), "browser", "test"]) == 0
    out = capsys.readouterr().out
    assert "browser test: ok" in out
    assert main(["--root", str(tmp_path), "browser", "stop"]) == 0
