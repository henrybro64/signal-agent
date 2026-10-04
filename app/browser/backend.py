"""Browser backends: fake fixture (CI) and Chromium headless (#87)."""

from __future__ import annotations

import os
import subprocess
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from urllib.parse import urljoin, urlparse

from app.browser.policy import BrowserPolicy, BrowserPolicyError

_MAX_REDIRECTS = 5


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
        final_url = resolve_allowed_url(url, policy=policy, network=False)
        title, text = self.fixtures.get(
            final_url,
            self.fixtures.get(url, ("Fixture", f"Fake page for {final_url}")),
        )
        if len(text.encode("utf-8")) > policy.max_page_bytes:
            raise BrowserPolicyError("Page exceeds max_page_bytes")
        return PageSnapshot(url=final_url, title=title, text=text)

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
        final_url = resolve_allowed_url(url, policy=policy, network=True)
        _assert_downloads_denied(policy)
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        cmd = self._base_cmd(policy) + ["--dump-dom", final_url]
        html = _run_chromium_bounded(
            cmd,
            timeout_seconds=policy.navigation_timeout_seconds,
            max_page_bytes=policy.max_page_bytes,
            action="navigation",
        )
        text = _html_to_text(html)
        title = _extract_title(html) or final_url
        return PageSnapshot(url=final_url, title=title, text=text)

    def screenshot(
        self,
        url: str,
        *,
        policy: BrowserPolicy,
        output_path: Path,
    ) -> PageSnapshot:
        final_url = resolve_allowed_url(url, policy=policy, network=True)
        _assert_downloads_denied(policy)
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        cmd = self._base_cmd(policy) + [
            f"--screenshot={output_path}",
            final_url,
        ]
        _run_chromium_bounded(
            cmd,
            timeout_seconds=policy.navigation_timeout_seconds,
            max_page_bytes=policy.max_page_bytes,
            action="screenshot",
            require_stdout=False,
        )
        if not output_path.is_file():
            raise BrowserPolicyError("Chromium screenshot failed: no output file")
        return PageSnapshot(
            url=final_url,
            title=final_url,
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


def resolve_allowed_url(
    url: str,
    *,
    policy: BrowserPolicy,
    network: bool = True,
) -> str:
    """Validate ``url`` and every redirect hop against browser policy."""

    policy.assert_url_allowed(url)
    _assert_downloads_denied(policy)
    if not network:
        return url
    host = (urlparse(url).hostname or "").lower()
    # Local fixture host and loopback self-tests skip outbound probes.
    if host in {"fixture.local", "127.0.0.1", "localhost"}:
        return url

    current = url
    for _ in range(_MAX_REDIRECTS):
        policy.assert_url_allowed(current)
        try:
            final = _http_probe(
                current,
                method="HEAD",
                timeout_seconds=policy.navigation_timeout_seconds,
            )
            policy.assert_url_allowed(final)
            return final
        except urllib.error.HTTPError as exc:
            if exc.code in {403, 405, 501}:
                return _probe_with_get(current, policy=policy)
            if 300 <= exc.code < 400:
                location = exc.headers.get("Location")
                if not location:
                    raise BrowserPolicyError(
                        f"Redirect from {current!r} missing Location header"
                    ) from exc
                current = urljoin(current, location)
                continue
            raise BrowserPolicyError(
                f"URL probe failed for {current!r}: HTTP {exc.code}"
            ) from exc
        except urllib.error.URLError:
            # Offline / blocked networks: original URL already validated.
            return current
    raise BrowserPolicyError(f"Too many redirects while resolving {url!r}")


def _probe_with_get(url: str, *, policy: BrowserPolicy) -> str:
    try:
        final = _http_probe(
            url,
            method="GET",
            timeout_seconds=policy.navigation_timeout_seconds,
            read_prefix=64,
        )
        policy.assert_url_allowed(final)
        return final
    except urllib.error.URLError:
        return url


def _assert_http_https_url(url: str) -> None:
    scheme = (urlparse(url).scheme or "").lower()
    if scheme not in {"http", "https"}:
        raise BrowserPolicyError(
            f"URL scheme {scheme!r} is not allowed for network probe (http/https only)"
        )


def _http_probe(
    url: str,
    *,
    method: str,
    timeout_seconds: float,
    read_prefix: int = 0,
) -> str:
    """Open an http(s) URL after an explicit scheme audit."""

    _assert_http_https_url(url)
    request = urllib.request.Request(
        url,
        method=method,
        headers={"User-Agent": "signal-agent-browser/1.0"},
    )
    with urllib.request.urlopen(  # noqa: S310 - scheme audited above
        request,
        timeout=min(5.0, timeout_seconds),
    ) as response:
        if read_prefix > 0:
            response.read(read_prefix)
        return response.geturl() or url


def _run_chromium_bounded(
    cmd: list[str],
    *,
    timeout_seconds: float,
    max_page_bytes: int,
    action: str,
    require_stdout: bool = True,
) -> str:
    """Run Chromium and stream stdout into a bounded buffer."""

    proc = _spawn_chromium(cmd, action=action)
    timed_out, stdout = _drain_chromium_stdout(
        proc,
        max_page_bytes=max_page_bytes,
        timeout_seconds=timeout_seconds,
    )
    stderr = _read_chromium_stderr(proc)
    if timed_out:
        raise BrowserPolicyError(
            f"Chromium {action} timed out after {timeout_seconds}s"
        )
    if proc.returncode != 0:
        err = (stderr or stdout or f"chromium {action} failed")[:200]
        raise BrowserPolicyError(f"Chromium {action} failed: {err}")
    if require_stdout and not stdout and action == "navigation":
        raise BrowserPolicyError("Chromium navigation returned empty document")
    return stdout


def _spawn_chromium(cmd: list[str], *, action: str) -> subprocess.Popen[str]:
    try:
        return subprocess.Popen(  # noqa: S603
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except OSError as exc:
        raise BrowserPolicyError(f"Chromium {action} failed: {exc}") from exc


def _drain_chromium_stdout(
    proc: subprocess.Popen[str],
    *,
    max_page_bytes: int,
    timeout_seconds: float,
) -> tuple[bool, str]:
    assert proc.stdout is not None
    chunks: list[str] = []
    total = 0
    timed_out = False
    try:
        while True:
            block = proc.stdout.read(8192)
            if not block:
                break
            total += len(block.encode("utf-8"))
            if total > max_page_bytes:
                proc.kill()
                raise BrowserPolicyError("Page exceeds max_page_bytes")
            chunks.append(block)
        try:
            proc.wait(timeout=max(0.1, timeout_seconds))
        except subprocess.TimeoutExpired:
            timed_out = True
            proc.kill()
            proc.wait(timeout=5)
    finally:
        if proc.poll() is None:
            proc.kill()
    return timed_out, "".join(chunks)


def _read_chromium_stderr(proc: subprocess.Popen[str]) -> str:
    if proc.stderr is None:
        return ""
    try:
        return proc.stderr.read() or ""
    except OSError:
        return ""


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
