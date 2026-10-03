"""Execute due schedules with overlap/retry/permission rules (#85)."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.core.config import load_config
from app.core.logging import get_logger, log_event
from app.core.secrets import load_env_settings
from app.runtime import paths as runtime_paths
from app.tools.permissions import (
    build_permission_policy,
    permission_policy_config_from_tools,
    push_permission_policy,
    reset_permission_policy,
)
from app.tools.registry import builtin_tools

from .store import DEFAULT_STALE_RUNNING_SECONDS, Schedule, ScheduleStore

_LOGGER = get_logger("scheduler.service")


@dataclass(slots=True)
class ScheduleTickResult:
    due: int = 0
    started: int = 0
    skipped: int = 0
    succeeded: int = 0
    failed: int = 0
    details: list[str] | None = None


def run_schedule_tick(
    *,
    root: str | Path | None = None,
    config_path: str | Path | None = None,
    db_path: str | Path | None = None,
    now: datetime | None = None,
    task_runner: Any | None = None,
) -> ScheduleTickResult:
    """Run all due schedules once; never raise to the runtime loop."""

    base = runtime_paths.project_root(root)
    store = ScheduleStore(db_path or runtime_paths.db_path(base))
    result = ScheduleTickResult(details=[])
    moment = now or datetime.now(UTC)
    reclaimed = store.reclaim_stale_runs(
        older_than_seconds=DEFAULT_STALE_RUNNING_SECONDS,
        now=moment,
    )
    if reclaimed:
        log_event(
            _LOGGER,
            event="scheduler_reclaimed_stale",
            message="reclaimed stale schedule runs",
            reclaimed=reclaimed,
        )
    due = store.due(now=moment)
    result.due = len(due)
    runner = task_runner or _default_task_runner
    for schedule in due:
        outcome = _run_one(
            store,
            schedule,
            root=base,
            config_path=str(config_path or runtime_paths.config_path(base)),
            runner=runner,
        )
        if outcome == "started_ok":
            result.started += 1
            result.succeeded += 1
        elif outcome == "started_failed":
            result.started += 1
            result.failed += 1
        else:
            result.skipped += 1
        if result.details is not None:
            result.details.append(f"{schedule.id}:{outcome}")
    log_event(
        _LOGGER,
        event="scheduler_tick",
        message="scheduler tick completed",
        due=result.due,
        started=result.started,
        skipped=result.skipped,
        succeeded=result.succeeded,
        failed=result.failed,
    )
    return result


def run_schedule_now(
    schedule_id: str,
    *,
    root: str | Path | None = None,
    config_path: str | Path | None = None,
    db_path: str | Path | None = None,
    dry_run: bool | None = None,
    task_runner: Any | None = None,
) -> str:
    """Manually run one schedule; return final status."""

    base = runtime_paths.project_root(root)
    store = ScheduleStore(db_path or runtime_paths.db_path(base))
    schedule = store.get(schedule_id)
    if schedule is None:
        raise KeyError(schedule_id)
    return _run_one(
        store,
        schedule,
        root=base,
        config_path=str(config_path or runtime_paths.config_path(base)),
        runner=task_runner or _default_task_runner,
        force=True,
        dry_run_override=dry_run,
    )


def build_unattended_permission_policy(
    *,
    root: Path,
    config_path: str,
) -> Any:
    """Non-interactive policy for scheduled/runtime ticks (#84/#85)."""

    env = load_env_settings(runtime_paths.env_path(root))
    try:
        config = load_config(config_path)
        policy_cfg = permission_policy_config_from_tools(
            config.tools.permissions,
            audit_log_path=str(root / "logs" / "permissions-audit.jsonl"),
        )
    except Exception:  # noqa: BLE001 - scheduler must stay up if config is odd
        from app.tools.permissions import PermissionPolicyConfig

        policy_cfg = PermissionPolicyConfig(
            audit_log_path=str(root / "logs" / "permissions-audit.jsonl"),
        )
    # Unattended runs never ask; force ask→deny via interactive=False.
    # Notify stays disabled unless Telegram credentials exist *and* config
    # already allows notify (or tool override).
    if not env.telegram_bot_token:
        policy_cfg.classes["notify"] = "disabled"
    return build_permission_policy(
        policy_cfg,
        interactive=False,
        secrets=tuple(
            value
            for value in (
                env.openai_api_key,
                env.telegram_bot_token,
                env.telegram_chat_id,
            )
            if value
        ),
        root=root,
    )


def _run_one(
    store: ScheduleStore,
    schedule: Schedule,
    *,
    root: Path,
    config_path: str,
    runner: Any,
    force: bool = False,
    dry_run_override: bool | None = None,
) -> str:
    if not schedule.enabled and not force:
        return "skipped_disabled"
    if not schedule.allow_overlap and store.has_running(schedule.id):
        return "skipped_overlap"

    policy = build_unattended_permission_policy(root=root, config_path=config_path)
    denied = _permission_preflight(store, schedule, policy=policy)
    if denied is not None:
        return denied

    dry_run = schedule.dry_run if dry_run_override is None else dry_run_override
    policy_token = push_permission_policy(policy)
    try:
        return _execute_with_retries(
            store,
            schedule,
            config_path=config_path,
            runner=runner,
            dry_run=dry_run,
        )
    finally:
        reset_permission_policy(policy_token)


def _permission_preflight(
    store: ScheduleStore,
    schedule: Schedule,
    *,
    policy: Any,
) -> str | None:
    tools_catalog = builtin_tools()
    for tool_name in schedule.metadata.get("require_tools") or []:
        tool = tools_catalog.get(str(tool_name))
        decision = policy.decide(str(tool_name), {}, tool=tool)
        if decision.allowed:
            continue
        run_id = str(uuid.uuid4())
        store.mark_started(
            schedule.id,
            run_id=run_id,
            attempt=1,
            dry_run=bool(schedule.dry_run),
        )
        current = store.get(schedule.id) or schedule
        store.mark_finished(
            current,
            run_id=run_id,
            status="permission_denied",
            error=decision.reason[:300],
        )
        log_event(
            _LOGGER,
            event="schedule_permission_denied",
            message="schedule blocked by permission policy",
            level=30,
            schedule_id=schedule.id,
            run_id=run_id,
            tool_name=str(tool_name),
            reason=decision.reason,
        )
        return "skipped_permission"
    return None


def _execute_with_retries(
    store: ScheduleStore,
    schedule: Schedule,
    *,
    config_path: str,
    runner: Any,
    dry_run: bool,
) -> str:
    attempt = 1
    max_attempts = max(1, schedule.max_retries + 1)
    while attempt <= max_attempts:
        run_id = str(uuid.uuid4())
        store.mark_started(
            schedule.id,
            run_id=run_id,
            attempt=attempt,
            dry_run=dry_run,
        )
        log_event(
            _LOGGER,
            event="schedule_run_started",
            message="schedule run started",
            schedule_id=schedule.id,
            run_id=run_id,
            task_name=schedule.task_name,
            attempt=attempt,
            dry_run=dry_run,
        )
        try:
            if not dry_run:
                runner(schedule.task_name, config_path=config_path)
            current = store.get(schedule.id) or schedule
            store.mark_finished(current, run_id=run_id, status="success")
            log_event(
                _LOGGER,
                event="schedule_run_finished",
                message="schedule run finished",
                schedule_id=schedule.id,
                run_id=run_id,
                status="success",
            )
            return "started_ok"
        except Exception as exc:  # noqa: BLE001
            outcome = _finish_failed_attempt(
                store,
                schedule,
                run_id=run_id,
                attempt=attempt,
                exc=exc,
            )
            if outcome == "started_failed":
                return outcome
            time.sleep(max(0.0, schedule.backoff_seconds))
            attempt += 1
    return "started_failed"


def _finish_failed_attempt(
    store: ScheduleStore,
    schedule: Schedule,
    *,
    run_id: str,
    attempt: int,
    exc: BaseException,
) -> str:
    last_error = f"{type(exc).__name__}: {exc}"[:300]
    final_attempt = attempt > schedule.max_retries or not _is_transient(exc)
    current = store.get(schedule.id) or schedule
    store.mark_finished(
        current,
        run_id=run_id,
        status="failed",
        error=last_error,
        schedule_next=final_attempt,
    )
    log_event(
        _LOGGER,
        event="schedule_run_failed",
        message="schedule run failed",
        level=40,
        schedule_id=schedule.id,
        run_id=run_id,
        attempt=attempt,
        error=last_error,
    )
    return "started_failed" if final_attempt else "retry"


def _default_task_runner(task_name: str, *, config_path: str) -> None:
    from app.tasks import build_task

    build_task(task_name, config_path=config_path).run()


def _is_transient(exc: BaseException) -> bool:
    name = type(exc).__name__.lower()
    text = str(exc).lower()
    needles = ("timeout", "temporarily", "connection", "unavailable", "429")
    return any(n in name or n in text for n in needles)


def format_schedules(schedules: list[Schedule]) -> str:
    if not schedules:
        return "(no schedules)"
    lines = []
    for item in schedules:
        state = "enabled" if item.enabled else "disabled"
        cadence = item.cron or item.run_at or "?"
        lines.append(
            f"{item.id}  {item.name}  task={item.task_name}  {state}  "
            f"tz={item.timezone}  cadence={cadence}  "
            f"last={item.last_status or '-'}  next={item.next_run_at or '-'}"
        )
    return "\n".join(lines)
