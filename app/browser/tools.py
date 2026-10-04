"""Bounded browser tools registered through the tool registry (#87)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.browser.backend import select_backend, temp_screenshot_path
from app.browser.policy import BrowserPolicyError
from app.browser.service import (
    BrowserServiceError,
    browser_profile_dir,
    browser_status,
    policy_from_config,
    start_browser,
)
from app.tools.models import ToolContext, ToolResult
from app.tools.permissions import PermissionClass


class BrowserNavigateTool:
    name = "browser_navigate"
    description = (
        "Navigate the isolated signal-agent browser to an http(s) URL "
        "(domain policy enforced; no arbitrary JavaScript)."
    )
    input_schema = {
        "type": "object",
        "required": ["url"],
        "properties": {
            "url": {"type": "string", "description": "http(s) URL to open"},
        },
        "additionalProperties": False,
    }
    permission = PermissionClass.EXECUTE

    def __init__(self) -> None:
        # Instance copy so DeepSource/PYL-R0201 treats invoke as using self.
        self._tool_name = type(self).name

    def invoke(  # skipcq: PYL-R0201
        self,
        arguments: dict[str, Any],
        *,
        context: ToolContext,
    ) -> ToolResult:
        return _open_page(self._tool_name, arguments, context=context)


class BrowserReadPageTool:
    name = "browser_read_page"
    description = (
        "Read title/text from a URL via the isolated browser "
        "(metadata + text only; no JS execution)."
    )
    input_schema = {
        "type": "object",
        "required": ["url"],
        "properties": {
            "url": {"type": "string"},
        },
        "additionalProperties": False,
    }
    permission = PermissionClass.EXECUTE

    def __init__(self) -> None:
        self._tool_name = type(self).name

    def invoke(  # skipcq: PYL-R0201
        self,
        arguments: dict[str, Any],
        *,
        context: ToolContext,
    ) -> ToolResult:
        return _open_page(self._tool_name, arguments, context=context)


class BrowserScreenshotTool:
    name = "browser_screenshot"
    description = "Capture a screenshot of a URL via the isolated browser profile."
    input_schema = {
        "type": "object",
        "required": ["url"],
        "properties": {
            "url": {"type": "string"},
        },
        "additionalProperties": False,
    }
    permission = PermissionClass.EXECUTE

    def __init__(self) -> None:
        self._tool_name = type(self).name

    def invoke(  # skipcq: PYL-R0201
        self,
        arguments: dict[str, Any],
        *,
        context: ToolContext,
    ) -> ToolResult:
        _ = self._tool_name
        url = str(arguments.get("url") or "").strip()
        if not url:
            return ToolResult(
                ok=False,
                error="url is required",
                metadata={"code": "invalid_argument"},
            )
        try:
            backend, policy, root = _backend_for_context(context)
            out = temp_screenshot_path(browser_profile_dir(root))
            snap = backend.screenshot(url, policy=policy, output_path=out)
        except (BrowserPolicyError, BrowserServiceError) as exc:
            return ToolResult(
                ok=False,
                error=str(exc),
                metadata={"code": "browser_policy"},
            )
        except Exception as exc:  # noqa: BLE001
            return ToolResult(
                ok=False,
                error=f"{type(exc).__name__}: {exc}"[:200],
                metadata={"code": "browser_error"},
            )
        return ToolResult(
            ok=True,
            data={
                "url": snap.url,
                "title": snap.title,
                "screenshot_path": snap.screenshot_path,
            },
        )


def browser_tools() -> dict[str, Any]:
    tools = [
        BrowserNavigateTool(),
        BrowserReadPageTool(),
        BrowserScreenshotTool(),
    ]
    return {tool.name: tool for tool in tools}


def _open_page(
    _tool_name: str,
    arguments: dict[str, Any],
    *,
    context: ToolContext,
) -> ToolResult:
    url = str(arguments.get("url") or "").strip()
    if not url:
        return ToolResult(
            ok=False,
            error="url is required",
            metadata={"code": "invalid_argument"},
        )
    try:
        backend, policy, _root = _backend_for_context(context)
        snap = backend.open(url, policy=policy)
    except (BrowserPolicyError, BrowserServiceError) as exc:
        return ToolResult(
            ok=False,
            error=str(exc),
            metadata={"code": "browser_policy"},
        )
    except Exception as exc:  # noqa: BLE001
        return ToolResult(
            ok=False,
            error=f"{type(exc).__name__}: {exc}"[:200],
            metadata={"code": "browser_error"},
        )
    return ToolResult(
        ok=True,
        data={
            "url": snap.url,
            "title": snap.title,
            "text": snap.text[:4000],
            "text_length": len(snap.text),
        },
    )


def _backend_for_context(context: ToolContext) -> tuple[Any, Any, Path]:
    root = Path(".")
    if context.db_path is not None:
        # db lives under <root>/data/... — recover project root when possible.
        root = context.db_path.parent.parent
    cfg: dict[str, Any] = {}
    if context.config is not None and hasattr(context.config, "browser"):
        browser = context.config.browser
        if not bool(browser.enabled):
            raise BrowserServiceError("Browser is disabled (browser.enabled=false)")
        cfg = {
            "enabled": True,
            "allowed_domains": list(browser.allowed_domains),
            "denied_domains": list(browser.denied_domains),
            "navigation_timeout_seconds": browser.navigation_timeout_seconds,
            "max_page_bytes": browser.max_page_bytes,
            "allow_downloads": browser.allow_downloads,
            "allow_javascript": browser.allow_javascript,
            "backend": browser.backend,
        }
    else:
        # No config bag — refuse rather than silently browsing.
        raise BrowserServiceError(
            "Browser config unavailable; cannot enforce browser.enabled"
        )

    status = browser_status(root=root)
    if not status.running:
        start_browser(root=root)
        status = browser_status(root=root)
    policy = policy_from_config(cfg)
    backend = select_backend(
        executable=status.executable,
        profile_dir=Path(status.profile_dir),
        headless=status.headless,
        force_fake=status.backend == "fake",
    )
    return backend, policy, root
