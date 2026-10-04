"""Browser lifecycle: status/start/stop/test (#87)."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.browser.backend import select_backend
from app.browser.fixtures import FIXTURE_URL
from app.browser.policy import BrowserPolicy, BrowserPolicyError
from app.core.config import load_config
from app.runtime import paths as runtime_paths
from app.setup.browser import detect_browser


class BrowserServiceError(RuntimeError):
    """Raised for browser lifecycle failures."""


@dataclass(slots=True, frozen=True)
class BrowserStatus:
    running: bool
    ready: bool
    executable: str | None
    profile_dir: str
    headless: bool
    backend: str
    detail: str
    enabled: bool = False
    last_test_at: str | None = None
    last_test_ok: bool | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "running": self.running,
            "ready": self.ready,
            "executable": self.executable,
            "profile_dir": self.profile_dir,
            "headless": self.headless,
            "backend": self.backend,
            "detail": self.detail,
            "enabled": self.enabled,
            "last_test_at": self.last_test_at,
            "last_test_ok": self.last_test_ok,
        }


def browser_profile_dir(root: str | Path | None = None) -> Path:
    return runtime_paths.runtime_dir(root) / "browser-profile"


def browser_state_path(root: str | Path | None = None) -> Path:
    return runtime_paths.runtime_dir(root) / "browser-state.json"


def browser_status(
    *,
    root: str | Path | None = None,
    config_path: str | Path | None = None,
) -> BrowserStatus:
    detection = detect_browser()
    state = _read_state(root)
    profile = browser_profile_dir(root)
    cfg = _load_browser_config(root=root, config_path=config_path)
    enabled = bool(cfg.get("enabled", False))
    prefer_fake = _explicit_fake(cfg)
    backend_name = "fake" if prefer_fake else "chromium"
    running = bool(state.get("running"))
    # Ready means the service is running *and* its chosen backend can operate.
    if prefer_fake:
        backend_ok = True
    else:
        backend_ok = bool(detection.ready)
    ready = bool(enabled and running and backend_ok)
    detail = detection.detail
    if not enabled:
        detail = f"disabled in config; {detail}"
    elif not running:
        detail = f"service stopped; {detail}"
    elif not backend_ok:
        detail = (
            f"chromium unavailable; {detail}. "
            "Install Chromium/Chrome or set SIGNAL_AGENT_BROWSER_BACKEND=fake"
        )
    return BrowserStatus(
        running=running,
        ready=ready,
        executable=detection.executable,
        profile_dir=str(profile),
        headless=bool(cfg.get("headless", True)),
        backend=backend_name
        if running or prefer_fake
        else ("fake" if prefer_fake else "chromium"),
        detail=detail,
        enabled=enabled,
        last_test_at=str(state.get("last_test_at") or "") or None,
        last_test_ok=(
            None
            if state.get("last_test_ok") is None
            else bool(state.get("last_test_ok"))
        ),
    )


def start_browser(
    *,
    root: str | Path | None = None,
    config_path: str | Path | None = None,
    require_enabled: bool = True,
) -> BrowserStatus:
    """Mark the isolated browser profile ready (idempotent)."""

    runtime_paths.ensure_local_dirs(root)
    cfg = _load_browser_config(root=root, config_path=config_path)
    if require_enabled and not cfg.get("enabled", False):
        raise BrowserServiceError(
            "Browser is disabled (browser.enabled=false). "
            "Set browser.enabled: true in config.yaml before starting."
        )
    detection = detect_browser()
    prefer_fake = _explicit_fake(cfg)
    if not prefer_fake and not detection.ready:
        raise BrowserServiceError(
            "Browser dependencies missing: "
            f"{detection.detail}. Install Chromium/Chrome or set "
            "SIGNAL_AGENT_BROWSER_BACKEND=fake for offline fixtures."
        )
    profile = browser_profile_dir(root)
    profile.mkdir(parents=True, exist_ok=True)
    # Refuse to reuse a path that looks like a normal Chrome user profile.
    if (profile / "Default" / "Preferences").is_file() and not (
        profile / ".signal-agent-browser"
    ).is_file():
        raise BrowserServiceError(
            "Refusing to reuse an existing browser profile that was not "
            "created by signal-agent"
        )
    marker = profile / ".signal-agent-browser"
    marker.write_text("signal-agent-isolated-profile\n", encoding="utf-8")
    _write_state(
        {
            "running": True,
            "started_at": _now(),
            "backend": "fake" if prefer_fake else "chromium",
            "executable": detection.executable,
            "profile_dir": str(profile),
            "headless": bool(cfg.get("headless", True)),
        },
        root=root,
    )
    return browser_status(root=root, config_path=config_path)


def stop_browser(*, root: str | Path | None = None) -> BrowserStatus:
    state = _read_state(root)
    state.update({"running": False, "stopped_at": _now()})
    _write_state(state, root=root)
    return browser_status(root=root)


def run_browser_test(
    *,
    root: str | Path | None = None,
    config_path: str | Path | None = None,
    url: str | None = None,
) -> dict[str, Any]:
    """Open a fixture/test page through the bounded backend."""

    status = browser_status(root=root, config_path=config_path)
    if not status.running:
        # Auto-start for convenience in CLI test (still requires enabled).
        start_browser(root=root, config_path=config_path)
        status = browser_status(root=root, config_path=config_path)
    cfg = _load_browser_config(root=root, config_path=config_path)
    policy = policy_from_config(cfg)
    prefer_fake = status.backend == "fake" or _explicit_fake(cfg)
    local_server = None
    target = url
    if target is None and prefer_fake:
        if "fixture.local" not in policy.allowed_domains:
            policy.allowed_domains = [*policy.allowed_domains, "fixture.local"]
        target = FIXTURE_URL
    elif target is None:
        # Chromium cannot resolve fixture.local; serve a real loopback page.
        local_server, target = _start_local_fixture_server()
        if "127.0.0.1" not in policy.allowed_domains:
            policy.allowed_domains = [*policy.allowed_domains, "127.0.0.1"]
    try:
        backend = select_backend(
            executable=status.executable,
            profile_dir=Path(status.profile_dir),
            headless=status.headless,
            force_fake=prefer_fake,
        )
        snap = backend.open(target, policy=policy)
        ok = True
        error = None
    except (
        BrowserPolicyError,
        BrowserServiceError,
        OSError,  # includes TimeoutError on modern Python
        subprocess.TimeoutExpired,
    ) as exc:
        snap = None
        ok = False
        error = str(exc)[:200]
    finally:
        if local_server is not None:
            local_server.shutdown()
    state = _read_state(root)
    state.update({"last_test_at": _now(), "last_test_ok": ok})
    _write_state(state, root=root)
    return {
        "ok": ok,
        "url": target,
        "title": None if snap is None else snap.title,
        "text_preview": None if snap is None else snap.text[:160],
        "backend": status.backend,
        "error": error,
    }


def format_browser_status(status: BrowserStatus) -> str:
    lines = [
        f"Browser: {'running' if status.running else 'stopped'}",
        f"Enabled: {status.enabled}",
        f"Ready: {status.ready}",
        f"Backend: {status.backend}",
        f"Executable: {status.executable or '(none)'}",
        f"Profile: {status.profile_dir}",
        f"Headless: {status.headless}",
        f"Detail: {status.detail}",
    ]
    if status.last_test_at:
        lines.append(
            f"Last test: {status.last_test_at} "
            f"({'ok' if status.last_test_ok else 'failed'})"
        )
    return "\n".join(lines)


def policy_from_config(cfg: dict[str, Any]) -> BrowserPolicy:
    return BrowserPolicy(
        allowed_domains=[str(x) for x in cfg.get("allowed_domains") or []],
        denied_domains=[str(x) for x in cfg.get("denied_domains") or []],
        navigation_timeout_seconds=float(cfg.get("navigation_timeout_seconds") or 15.0),
        max_page_bytes=int(cfg.get("max_page_bytes") or 500_000),
        allow_downloads=bool(cfg.get("allow_downloads", False)),
        allow_javascript=bool(cfg.get("allow_javascript", False)),
    )


def _explicit_fake(cfg: dict[str, Any]) -> bool:
    """Return True only when the operator explicitly requested fixtures."""

    import os

    env = (os.getenv("SIGNAL_AGENT_BROWSER_BACKEND") or "").strip().lower()
    if env in {"fake", "fixture", "mock"}:
        return True
    return cfg.get("backend") in {"fake", "fixture", "mock"}


def _load_browser_config(
    *,
    root: str | Path | None,
    config_path: str | Path | None,
) -> dict[str, Any]:
    path = Path(config_path or runtime_paths.config_path(root))
    try:
        config = load_config(path)
        browser = getattr(config, "browser", None)
        if browser is None:
            return {"enabled": False, "headless": True}
        if hasattr(browser, "__dataclass_fields__"):
            return {
                "enabled": bool(browser.enabled),
                "headless": bool(browser.headless),
                "backend": browser.backend,
                "allowed_domains": list(browser.allowed_domains),
                "denied_domains": list(browser.denied_domains),
                "navigation_timeout_seconds": browser.navigation_timeout_seconds,
                "max_page_bytes": browser.max_page_bytes,
                "allow_downloads": browser.allow_downloads,
                "allow_javascript": browser.allow_javascript,
            }
    except Exception:  # noqa: BLE001
        pass
    return {"enabled": False, "headless": True}


def _read_state(root: str | Path | None) -> dict[str, Any]:
    path = browser_state_path(root)
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_state(payload: dict[str, Any], *, root: str | Path | None) -> None:
    runtime_paths.ensure_local_dirs(root)
    path = browser_state_path(root)
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _start_local_fixture_server() -> tuple[Any, str]:
    """Serve a tiny HTML page on 127.0.0.1 for Chromium self-tests."""

    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    html = (
        b"<!doctype html><title>signal-agent browser test</title>"
        b"<body>Signal-agent browser fixture page. No network required.</body>"
    )

    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(html)))
            self.end_headers()
            self.wfile.write(html)

        def do_HEAD(self) -> None:  # noqa: N802
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(html)))
            self.end_headers()

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[:2]
    return server, f"http://{host}:{port}/signal-agent-test"
