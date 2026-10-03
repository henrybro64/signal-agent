"""Operator-facing runtime status (#82)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.core.config import load_config
from app.core.health import resolve_db_path, summarize_run_health
from app.core.secrets import (
    has_openai_api_key,
    has_telegram_credentials,
    load_env_settings,
)
from app.llm import supported_llm_providers
from app.tools import DEFAULT_TOOL_ALLOWLIST, build_tool_registry

from . import paths as runtime_paths
from .lifecycle import get_runtime_info


@dataclass(slots=True)
class StatusReport:
    runtime_running: bool
    runtime_pid: int | None
    runtime_state: str
    config_path: str
    config_ok: bool
    db_path: str
    db_ok: bool
    provider: str
    model: str
    openai_key_configured: bool
    telegram_configured: bool
    telegram_enabled: bool
    browser_ready: bool
    browser_detail: str
    mcp_available: bool
    langchain_available: bool
    tool_allowlist: list[str] = field(default_factory=list)
    health_overall: str = "UNKNOWN"
    worker_status: str = "unknown"
    last_tick_at: str | None = None
    last_tick_ok: bool | None = None
    details: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "runtime_running": self.runtime_running,
            "runtime_pid": self.runtime_pid,
            "runtime_state": self.runtime_state,
            "config_path": self.config_path,
            "config_ok": self.config_ok,
            "db_path": self.db_path,
            "db_ok": self.db_ok,
            "provider": self.provider,
            "model": self.model,
            "openai_key_configured": self.openai_key_configured,
            "telegram_configured": self.telegram_configured,
            "telegram_enabled": self.telegram_enabled,
            "browser_ready": self.browser_ready,
            "browser_detail": self.browser_detail,
            "mcp_available": self.mcp_available,
            "langchain_available": self.langchain_available,
            "tool_allowlist": list(self.tool_allowlist),
            "health_overall": self.health_overall,
            "worker_status": self.worker_status,
            "last_tick_at": self.last_tick_at,
            "last_tick_ok": self.last_tick_ok,
            "details": list(self.details),
        }


def collect_status(
    *,
    root: str | Path | None = None,
    config_path: str | Path | None = None,
) -> StatusReport:
    """Build a redaction-safe status snapshot."""

    from app.setup.browser import detect_browser

    from .lifecycle import read_runtime_state

    cfg_path = Path(config_path or runtime_paths.config_path(root))
    env = load_env_settings(runtime_paths.env_path(root))
    runtime = get_runtime_info(root)
    state = read_runtime_state(root)
    details: list[str] = []
    if state.get("worker"):
        worker_status = str(state.get("worker"))
    else:
        worker_status = "active" if runtime.running else "stopped"
    last_tick_at = str(state.get("last_tick_at") or "") or None
    last_tick_ok = state.get("last_tick_ok")
    if last_tick_ok is not None:
        last_tick_ok = bool(last_tick_ok)
    config_ok = False
    provider = "unknown"
    model = "unknown"
    telegram_enabled = False
    allowlist = list(DEFAULT_TOOL_ALLOWLIST)
    if cfg_path.is_file():
        try:
            config = load_config(cfg_path)
            config_ok = True
            provider = config.llm.provider
            model = config.llm.model
            telegram_enabled = config.notifiers.telegram.enabled
            allowlist = list(config.tools.allowlist or DEFAULT_TOOL_ALLOWLIST)
        except Exception as exc:  # noqa: BLE001
            details.append(f"config_error:{type(exc).__name__}")
    else:
        details.append("config_missing")

    db = resolve_db_path(runtime_paths.db_path(root))
    db_ok = db.is_file()
    health_overall = "UNKNOWN"
    if db_ok:
        try:
            health_overall = summarize_run_health(db_path=db).overall_status
        except Exception:  # noqa: BLE001
            health_overall = "UNKNOWN"

    browser = detect_browser()
    mcp_available = False
    langchain_available = False
    try:
        from app.integrations.mcp import mcp_available as _mcp

        mcp_available = _mcp()
    except Exception:  # noqa: BLE001
        mcp_available = False
    try:
        from app.integrations.langchain import langchain_available as _lc

        langchain_available = _lc()
    except Exception:  # noqa: BLE001
        langchain_available = False

    # Touch registry construction for readiness without invoking tools.
    try:
        build_tool_registry(allowlist=allowlist)
    except Exception as exc:  # noqa: BLE001
        details.append(f"tools_error:{type(exc).__name__}")

    _ = supported_llm_providers()

    return StatusReport(
        runtime_running=runtime.running,
        runtime_pid=runtime.pid,
        runtime_state=runtime.state,
        config_path=str(cfg_path),
        config_ok=config_ok,
        db_path=str(db),
        db_ok=db_ok,
        provider=provider,
        model=model,
        openai_key_configured=has_openai_api_key(env),
        telegram_configured=has_telegram_credentials(env),
        telegram_enabled=telegram_enabled,
        browser_ready=browser.ready,
        browser_detail=browser.detail,
        mcp_available=mcp_available,
        langchain_available=langchain_available,
        tool_allowlist=allowlist,
        health_overall=health_overall,
        worker_status=worker_status,
        last_tick_at=last_tick_at,
        last_tick_ok=last_tick_ok if isinstance(last_tick_ok, bool) else None,
        details=details,
    )


def format_status_report(report: StatusReport) -> str:
    tick = "n/a"
    if report.last_tick_at:
        tick_ok = (
            "ok"
            if report.last_tick_ok
            else "error"
            if report.last_tick_ok is False
            else "unknown"
        )
        tick = f"{report.last_tick_at} ({tick_ok})"
    lines = [
        f"Runtime: {'running' if report.runtime_running else 'stopped'}"
        + (f" (pid={report.runtime_pid})" if report.runtime_pid else ""),
        f"State: {report.runtime_state}",
        f"Worker: {report.worker_status}; last_tick={tick}",
        "Config: "
        f"{report.config_path} "
        f"({'ok' if report.config_ok else 'missing/invalid'})",
        f"Database: {report.db_path} ({'ok' if report.db_ok else 'missing'})",
        f"LLM: provider={report.provider} model={report.model} "
        f"api_key={'set' if report.openai_key_configured else 'missing'}",
        f"Telegram: enabled={report.telegram_enabled} "
        f"credentials={'set' if report.telegram_configured else 'missing'}",
        f"Browser: {'ready' if report.browser_ready else 'not ready'} "
        f"({report.browser_detail})",
        f"MCP extra: {'available' if report.mcp_available else 'not installed'}",
        f"LangChain extra: "
        f"{'available' if report.langchain_available else 'not installed'}",
        f"Tools allowlist: {', '.join(report.tool_allowlist) or '(none)'}",
        f"Task-run health (history): {report.health_overall}",
    ]
    if report.details:
        lines.append("Notes: " + "; ".join(report.details))
    return "\n".join(lines)
