"""Browser backends: fake fixture (CI) and Chromium headless (#87)."""

from __future__ import annotations

import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from app.browser.policy import BrowserPolicy, BrowserPolicyError


@dataclass(slots=True, frozen=True)
class PageSnapshot:
    url: str
    title: str
    text: str
    status: str = "ok"
    screenshot_path: str | None = None


class BrowserBackend(Protocol):
    def open(self, url: str, *, policy: BrowserPolicy) -> PageSnapshot: ...

    def screenshot(
        self,
        url: str,
        *,
        policy: BrowserPolicy,
        output_path: Path,
    ) -> PageSnapshot: ...


@dataclass(slots=True)
class FakeBrowserBackend:
    """Local fixture backend — no network, for CI and offline tests."""

    fixtures: dict[str, tuple[str, str]]

    def open(self, url: str, *, policy: BrowserPolicy) -> PageSnapshot:
        policy.assert_url_allowed(url)
        _assert_downloads_denied(policy)
        title, text = self.fixtures.get(
            url,
            ("Fixture", f"Fake page for {url}"),
        )
        if len(text.encode("utf-8")) > policy.max_page_bytes:
            raise BrowserPolicyError("Page exceeds max_page_bytes")
        return PageSnapshot(url=url, title=title, text=text)

    def screenshot(
        self,
        url: str,
        *,
        policy: BrowserPolicy,
        output_path: Path,
    ) -> PageSnapshot:
        snap = self.open(url, policy=policy)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        # Minimal valid-ish PNG header bytes are not required; tests only
        # need a file artifact.
        output_path.write_bytes(b"\x89PNG\r\n\x1a\nfake-screenshot")
        return PageSnapshot(
            url=snap.url,
            title=snap.title,
            text=snap.text,
            screenshot_path=str(output_path),
        )


@dataclass(slots=True)
class ChromiumBackend:
    """Launch Chromium/Chrome headless against an isolated profile."""

    executable: str
    profile_dir: Path
    headless: bool = True

    def open(self, url: str, *, policy: BrowserPolicy) -> PageSnapshot:
        policy.assert_url_allowed(url)
        _assert_downloads_denied(policy)
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        cmd = self._base_cmd(policy) + ["--dump-dom", url]
        try:
            completed = subprocess.run(  # noqa: S603 - controlled local executable
                cmd,
                check=False,
                capture_output=True,
                text=True,
                timeout=policy.navigation_timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            raise BrowserPolicyError(
                f"Chromium navigation timed out after "
                f"{policy.navigation_timeout_seconds}s"
            ) from exc
        if completed.returncode != 0:
            err = (completed.stderr or completed.stdout or "chromium failed")[:200]
            raise BrowserPolicyError(f"Chromium navigation failed: {err}")
        html = completed.stdout or ""
        raw = html.encode("utf-8")
        if len(raw) > policy.max_page_bytes:
            raise BrowserPolicyError("Page exceeds max_page_bytes")
        text = _html_to_text(html)
        title = _extract_title(html) or url
        return PageSnapshot(url=url, title=title, text=text)

    def screenshot(
        self,
        url: str,
        *,
        policy: BrowserPolicy,
        output_path: Path,
    ) -> PageSnapshot:
        policy.assert_url_allowed(url)
        _assert_downloads_denied(policy)
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        cmd = self._base_cmd(policy) + [
            f"--screenshot={output_path}",
            url,
        ]
        try:
            completed = subprocess.run(  # noqa: S603
                cmd,
                check=False,
                capture_output=True,
                text=True,
                timeout=policy.navigation_timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            raise BrowserPolicyError(
                f"Chromium screenshot timed out after "
                f"{policy.navigation_timeout_seconds}s"
            ) from exc
        if completed.returncode != 0 or not output_path.is_file():
            err = (completed.stderr or completed.stdout or "screenshot failed")[:200]
            raise BrowserPolicyError(f"Chromium screenshot failed: {err}")
        # Avoid a second navigation; screenshot path is the artifact.
        return PageSnapshot(
            url=url,
            title=url,
            text="",
            screenshot_path=str(output_path),
        )

    def _base_cmd(self, policy: BrowserPolicy) -> list[str]:
        download_dir = self.profile_dir / "downloads-blocked"
        download_dir.mkdir(parents=True, exist_ok=True)
        # Best-effort download denial for headless Chromium: isolate downloads
        # under the profile and disable download UI / prompts.
        cmd = [
            self.executable,
            f"--user-data-dir={self.profile_dir}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-extensions",
            "--disable-sync",
            "--disable-background-networking",
            f"--download-directory={download_dir}",
            "--disable-features=DownloadBubble,DownloadBubbleV2",
            "--deny-permission-prompts",
        ]
        if self.headless:
            cmd.append("--headless=new")
        if not policy.allow_javascript:
            cmd.append("--disable-javascript")
        return cmd


def select_backend(
    *,
    executable: str | None,
    profile_dir: Path,
    headless: bool,
    force_fake: bool | None = None,
) -> BrowserBackend:
    """Choose fake or Chromium backend.

    Fake is used only when explicitly requested (``force_fake``,
    ``SIGNAL_AGENT_BROWSER_BACKEND=fake``, etc.). Missing Chromium does
    **not** silently fall back to fixtures.
    """

    env_flag = (os.getenv("SIGNAL_AGENT_BROWSER_BACKEND") or "").strip().lower()
    explicit_fake = force_fake is True or env_flag in {
        "fake",
        "fixture",
        "mock",
    }
    if explicit_fake:
        from app.browser.fixtures import default_fixtures

        return FakeBrowserBackend(fixtures=default_fixtures())
    if not executable:
        raise BrowserPolicyError(
            "Chromium/Chrome executable not available. Install a browser or "
            "set SIGNAL_AGENT_BROWSER_BACKEND=fake for offline fixtures."
        )
    return ChromiumBackend(
        executable=executable,
        profile_dir=profile_dir,
        headless=headless,
    )


def _assert_downloads_denied(policy: BrowserPolicy) -> None:
    if policy.allow_downloads:
        raise BrowserPolicyError(
            "browser.allow_downloads=true is not supported; downloads remain disabled"
        )


def _html_to_text(html: str) -> str:
    # Lightweight strip; avoid pulling in an HTML parser dependency.
    import re

    no_script = re.sub(
        r"(?is)<(script|style).*?>.*?</\1>",
        " ",
        html,
    )
    text = re.sub(r"(?s)<[^>]+>", " ", no_script)
    return " ".join(text.split())


def _extract_title(html: str) -> str | None:
    import re

    match = re.search(r"(?is)<title[^>]*>(.*?)</title>", html)
    if not match:
        return None
    return " ".join(match.group(1).split())


def temp_screenshot_path(root: Path) -> Path:
    directory = root / "screenshots"
    directory.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        prefix="browser-",
        suffix=".png",
        dir=directory,
        delete=False,
    )
    handle.close()
    return Path(handle.name)
