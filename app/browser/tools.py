"""Bounded browser tools registered through the tool registry (#87/#106)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.browser.backend import (
    select_backend,
    select_session_backend,
    temp_screenshot_path,
)
from app.browser.policy import BrowserPolicyError
from app.browser.service import (
    BrowserServiceError,
    browser_profile_dir,
    browser_status,
    policy_from_config,
    start_browser,
)
from app.browser.sessions import drop_session_manager, get_session_manager
from app.tools.models import ToolContext, ToolResult
from app.tools.permissions import PermissionClass


class BrowserNavigateTool:
    name = "browser_navigate"
    description = (
        "Navigate the isolated signal-agent browser to an http(s) URL "
        "(one-shot; domain policy enforced; no arbitrary JavaScript)."
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
        "(one-shot; metadata + text only; no JS execution)."
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
    description = (
        "Capture a PNG screenshot. Pass url for a one-shot capture, or "
        "session_id for the current session viewport (#106)."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "One-shot URL to capture"},
            "session_id": {
                "type": "string",
                "description": "Existing session id (interactive browsing)",
            },
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
        session_id = str(arguments.get("session_id") or "").strip()
        url = str(arguments.get("url") or "").strip()
        if session_id and url:
            return ToolResult(
                ok=False,
                error="Pass either url or session_id, not both",
                metadata={"code": "invalid_argument"},
            )
        if session_id:
            return _session_call(
                context,
                lambda manager: manager.screenshot(
                    session_id,
                    temp_screenshot_path(
                        browser_profile_dir(_root_from_context(context))
                    ),
                ),
            )
        if not url:
            return ToolResult(
                ok=False,
                error="url or session_id is required",
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


class BrowserOpenTool:
    name = "browser_open"
    description = (
        "Start an interactive browser session at a URL and return "
        "session_id plus a snapshot with numbered interactive elements."
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
        _ = self._tool_name
        url = str(arguments.get("url") or "").strip()
        if not url:
            return ToolResult(
                ok=False,
                error="url is required",
                metadata={"code": "invalid_argument"},
            )

        def _open(manager: Any) -> Any:
            _cfg, policy, _status, _root = _session_context(context)
            return manager.open(url, policy=policy)

        return _session_call(context, _open)


class BrowserSnapshotTool:
    name = "browser_snapshot"
    description = "Re-read the current page for an open browser session."
    input_schema = {
        "type": "object",
        "required": ["session_id"],
        "properties": {"session_id": {"type": "string"}},
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
        session_id = str(arguments.get("session_id") or "").strip()
        if not session_id:
            return ToolResult(
                ok=False,
                error="session_id is required",
                metadata={"code": "invalid_argument"},
            )
        return _session_call(context, lambda manager: manager.snapshot(session_id))


class BrowserClickTool:
    name = "browser_click"
    description = "Click a link or button by ref from the latest session snapshot."
    input_schema = {
        "type": "object",
        "required": ["session_id", "ref"],
        "properties": {
            "session_id": {"type": "string"},
            "ref": {"type": "integer"},
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
        session_id = str(arguments.get("session_id") or "").strip()
        parsed = _parse_ref(arguments)
        if isinstance(parsed, ToolResult):
            return parsed
        if not session_id:
            return ToolResult(
                ok=False,
                error="session_id is required",
                metadata={"code": "invalid_argument"},
            )
        return _session_call(context, lambda manager: manager.click(session_id, parsed))


class BrowserTypeTool:
    name = "browser_type"
    description = (
        "Type into a search/filter field by ref (password and apply flows are refused)."
    )
    input_schema = {
        "type": "object",
        "required": ["session_id", "ref", "text"],
        "properties": {
            "session_id": {"type": "string"},
            "ref": {"type": "integer"},
            "text": {"type": "string"},
            "submit": {"type": "boolean", "default": False},
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
        session_id = str(arguments.get("session_id") or "").strip()
        text = str(arguments.get("text") or "")
        submit = bool(arguments.get("submit", False))
        parsed = _parse_ref(arguments)
        if isinstance(parsed, ToolResult):
            return parsed
        if not session_id:
            return ToolResult(
                ok=False,
                error="session_id is required",
                metadata={"code": "invalid_argument"},
            )
        return _session_call(
            context,
            lambda manager: manager.type_text(session_id, parsed, text, submit=submit),
        )


class BrowserSelectTool:
    name = "browser_select"
    description = "Choose a dropdown option by ref (filters only)."
    input_schema = {
        "type": "object",
        "required": ["session_id", "ref", "value"],
        "properties": {
            "session_id": {"type": "string"},
            "ref": {"type": "integer"},
            "value": {"type": "string"},
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
        session_id = str(arguments.get("session_id") or "").strip()
        value = str(arguments.get("value") or "")
        parsed = _parse_ref(arguments)
        if isinstance(parsed, ToolResult):
            return parsed
        if not session_id:
            return ToolResult(
                ok=False,
                error="session_id is required",
                metadata={"code": "invalid_argument"},
            )
        return _session_call(
            context, lambda manager: manager.select(session_id, parsed, value)
        )


class BrowserScrollTool:
    name = "browser_scroll"
    description = "Scroll the session page (up/down/top/bottom) for lazy loading."
    input_schema = {
        "type": "object",
        "required": ["session_id", "direction"],
        "properties": {
            "session_id": {"type": "string"},
            "direction": {
                "type": "string",
                "enum": ["up", "down", "top", "bottom"],
            },
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
        session_id = str(arguments.get("session_id") or "").strip()
        direction = str(arguments.get("direction") or "").strip()
        if not session_id:
            return ToolResult(
                ok=False,
                error="session_id is required",
                metadata={"code": "invalid_argument"},
            )
        return _session_call(
            context, lambda manager: manager.scroll(session_id, direction)
        )


class BrowserBackTool:
    name = "browser_back"
    description = "Go back one step in the browser session history."
    input_schema = {
        "type": "object",
        "required": ["session_id"],
        "properties": {"session_id": {"type": "string"}},
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
        session_id = str(arguments.get("session_id") or "").strip()
        if not session_id:
            return ToolResult(
                ok=False,
                error="session_id is required",
                metadata={"code": "invalid_argument"},
            )
        return _session_call(context, lambda manager: manager.back(session_id))


class BrowserCloseTool:
    name = "browser_close"
    description = "End an interactive browser session and release resources."
    input_schema = {
        "type": "object",
        "required": ["session_id"],
        "properties": {"session_id": {"type": "string"}},
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
        session_id = str(arguments.get("session_id") or "").strip()
        if not session_id:
            return ToolResult(
                ok=False,
                error="session_id is required",
                metadata={"code": "invalid_argument"},
            )

        def _close(manager: Any) -> dict[str, Any]:
            manager.close(session_id)
            return {"session_id": session_id, "closed": True}

        return _session_call(context, _close, raw=True)


def browser_tools() -> dict[str, Any]:
    tools = [
        BrowserNavigateTool(),
        BrowserReadPageTool(),
        BrowserScreenshotTool(),
        BrowserOpenTool(),
        BrowserSnapshotTool(),
        BrowserClickTool(),
        BrowserTypeTool(),
        BrowserSelectTool(),
        BrowserScrollTool(),
        BrowserBackTool(),
        BrowserCloseTool(),
    ]
    return {tool.name: tool for tool in tools}


def _parse_ref(arguments: dict[str, Any]) -> int | ToolResult:
    raw = arguments.get("ref")
    if raw is None:
        return ToolResult(
            ok=False,
            error="ref must be an integer",
            metadata={"code": "invalid_argument"},
        )
    try:
        return int(raw)
    except (TypeError, ValueError):
        return ToolResult(
            ok=False,
            error="ref must be an integer",
            metadata={"code": "invalid_argument"},
        )


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


def _session_call(
    context: ToolContext,
    action: Any,
    *,
    raw: bool = False,
) -> ToolResult:
    try:
        manager = _session_manager_for_context(context)
        result = action(manager)
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
    if raw:
        return ToolResult(ok=True, data=result)
    return ToolResult(ok=True, data=result.to_tool_data())


def _session_manager_for_context(context: ToolContext) -> Any:
    cfg, _policy, status, root = _session_context(context)
    backend = select_session_backend(
        headless=status.headless,
        force_fake=None,
        backend_name=status.backend,
        profile_dir=browser_profile_dir(root),
    )
    return get_session_manager(
        root=root,
        backend=backend,
        profile_dir=browser_profile_dir(root),
        max_sessions=int(cfg.get("max_sessions") or 1),
        session_ttl_seconds=float(cfg.get("session_ttl_seconds") or 300.0),
        max_steps_per_session=int(cfg.get("max_steps_per_session") or 20),
        max_interactive_elements=int(cfg.get("max_interactive_elements") or 150),
    )


def _session_context(
    context: ToolContext,
) -> tuple[dict[str, Any], Any, Any, Path]:
    root = _root_from_context(context)
    cfg_path = Path(context.config_path) if context.config_path is not None else None
    cfg = _browser_cfg_from_context(context)
    if not cfg.get("enabled"):
        raise BrowserServiceError("Browser is disabled (browser.enabled=false)")
    status = browser_status(root=root, config_path=cfg_path)
    if not status.running:
        start_browser(root=root, config_path=cfg_path)
        status = browser_status(root=root, config_path=cfg_path)
    policy = policy_from_config(cfg)
    return cfg, policy, status, root


def _root_from_context(context: ToolContext) -> Path:
    if context.root is not None:
        return Path(context.root)
    if context.db_path is not None:
        return context.db_path.parent.parent
    return Path(".")


def _browser_cfg_from_context(context: ToolContext) -> dict[str, Any]:
    if context.config is not None and hasattr(context.config, "browser"):
        browser = context.config.browser
        return {
            "enabled": bool(browser.enabled),
            "allowed_domains": list(browser.allowed_domains),
            "denied_domains": list(browser.denied_domains),
            "navigation_timeout_seconds": browser.navigation_timeout_seconds,
            "max_page_bytes": browser.max_page_bytes,
            "allow_downloads": browser.allow_downloads,
            "allow_javascript": browser.allow_javascript,
            "allow_subresources": browser.allow_subresources,
            "backend": browser.backend,
            "max_sessions": browser.max_sessions,
            "session_ttl_seconds": browser.session_ttl_seconds,
            "max_steps_per_session": browser.max_steps_per_session,
            "max_interactive_elements": browser.max_interactive_elements,
            "headless": browser.headless,
        }
    raise BrowserServiceError(
        "Browser config unavailable; cannot enforce browser.enabled"
    )


def _backend_for_context(context: ToolContext) -> tuple[Any, Any, Path]:
    root = _root_from_context(context)
    cfg_path = Path(context.config_path) if context.config_path is not None else None
    cfg = _browser_cfg_from_context(context)
    if not bool(cfg.get("enabled")):
        raise BrowserServiceError("Browser is disabled (browser.enabled=false)")

    status = browser_status(root=root, config_path=cfg_path)
    if not status.running:
        start_browser(root=root, config_path=cfg_path)
        status = browser_status(root=root, config_path=cfg_path)
    policy = policy_from_config(cfg)
    backend = select_backend(
        executable=status.executable,
        profile_dir=Path(status.profile_dir),
        headless=status.headless,
        force_fake=status.backend == "fake",
        backend_name=status.backend,
    )
    return backend, policy, root


# Re-export for tests that reset managers between cases.
__all__ = [
    "BrowserBackTool",
    "BrowserClickTool",
    "BrowserCloseTool",
    "BrowserNavigateTool",
    "BrowserOpenTool",
    "BrowserReadPageTool",
    "BrowserScreenshotTool",
    "BrowserScrollTool",
    "BrowserSelectTool",
    "BrowserSnapshotTool",
    "BrowserTypeTool",
    "browser_tools",
    "drop_session_manager",
]
