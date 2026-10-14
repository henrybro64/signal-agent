"""Background worker ticks for the long-running runtime (#82)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.core.logging import get_logger, log_event

_LOGGER = get_logger("runtime.worker")


@dataclass(slots=True)
class WorkerTickResult:
    ok: bool
    detail: str
    failures: list[str] = field(default_factory=list)


def run_worker_tick(
    *,
    root: str | Path | None = None,
    config_path: str | Path | None = None,
    stop_requested: Callable[[], bool] | None = None,
) -> WorkerTickResult:
    """Run one maintenance pulse; never raise to the service loop.

    Intentionally exercises fallible paths (config load, DB health, a
    read-only tool invoke) and records failures so a single bad tick cannot
    kill the runtime process.

    ``stop_requested`` must be the callback from the executing service
    instance. Importing ``app.runtime.service`` here would load a second
    module copy when the runtime is launched as ``python -m app.runtime.service``.
    """

    failures: list[str] = []
    try:
        from app.core.config import load_config
        from app.core.db import init_db
        from app.core.health import summarize_run_health
        from app.runtime import paths as runtime_paths
        from app.tools import ToolContext, build_tool_registry

        base = runtime_paths.project_root(root)
        cfg = Path(config_path or runtime_paths.config_path(base))
        db = runtime_paths.db_path(base)
        init_db(db)

        allowlist = ["query_recent_runs"]
        try:
            config = load_config(cfg)
            allowlist = [
                name for name in config.tools.allowlist if name == "query_recent_runs"
            ] or ["query_recent_runs"]
        except Exception as exc:  # noqa: BLE001
            failures.append(f"config:{type(exc).__name__}")
            config = None

        try:
            health = summarize_run_health(db_path=db)
            health_status = health.overall_status
        except Exception as exc:  # noqa: BLE001
            failures.append(f"health:{type(exc).__name__}")
            health_status = "UNKNOWN"

        try:
            registry = build_tool_registry(
                allowlist=allowlist,
                root=base,
                config_path=cfg,
            )
            context = ToolContext(
                config=config,
                db_path=db,
                telegram_enabled=False,
                query_runs=lambda **_: [],
                root=base,
                config_path=cfg,
            )
            result = registry.invoke(
                "query_recent_runs",
                {"limit": 1},
                context=context,
            )
            if not result.ok:
                failures.append(f"tool:{result.error or 'error'}")
        except Exception as exc:  # noqa: BLE001
            failures.append(f"tool:{type(exc).__name__}")

        schedule_detail = "schedules=0"
        try:
            from app.scheduler import run_schedule_tick

            tick = run_schedule_tick(
                root=base,
                config_path=cfg,
                db_path=db,
                stop_requested=stop_requested,
            )
            schedule_detail = (
                f"schedules_due={tick.due},started={tick.started},failed={tick.failed}"
            )
        except Exception as exc:  # noqa: BLE001
            failures.append(f"scheduler:{type(exc).__name__}")

        detail = f"health={health_status};{schedule_detail}"
        ok = not failures
        log_event(
            _LOGGER,
            event="runtime_worker_tick",
            message="runtime worker tick completed",
            ok=ok,
            detail=detail,
            failures=";".join(failures) if failures else None,
        )
        return WorkerTickResult(ok=ok, detail=detail, failures=failures)
    except Exception as exc:  # noqa: BLE001 - outer belt; never kill service
        log_event(
            _LOGGER,
            event="runtime_worker_tick_failed",
            message="runtime worker tick crashed",
            level=40,
            error=type(exc).__name__,
        )
        return WorkerTickResult(
            ok=False,
            detail="tick_crashed",
            failures=[type(exc).__name__],
        )


def utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def tick_state_patch(result: WorkerTickResult) -> dict[str, Any]:
    return {
        "last_tick_at": utc_now(),
        "last_tick_ok": result.ok,
        "last_tick_detail": result.detail,
        "last_tick_failures": list(result.failures),
        "worker": "active",
    }
