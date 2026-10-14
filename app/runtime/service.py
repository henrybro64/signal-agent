"""Long-running local runtime service (#82)."""

from __future__ import annotations

import argparse
import fcntl
import os
import signal
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from types import FrameType

from app.core.db import init_db
from app.core.logging import configure_logging, get_logger, log_event

from . import paths as runtime_paths
from .lifecycle import write_runtime_state
from .worker import run_worker_tick, tick_state_patch

_LOGGER = get_logger("runtime.service")


class _StopState:
    """Mutable stop flag shared with signal handlers (avoids ``global``)."""

    __slots__ = ("requested",)

    def __init__(self) -> None:
        self.requested = False


_STOP = _StopState()


def _request_stop(signum: int, _frame: FrameType | None) -> None:
    _STOP.requested = True
    log_event(
        _LOGGER,
        event="runtime_signal",
        message=f"received signal {signum}",
        signum=signum,
    )


def is_stop_requested() -> bool:
    """Return True when the runtime received SIGTERM/SIGINT (#98)."""

    return _STOP.requested


def acquire_runtime_lock(root: str | Path | None = None):
    """Acquire an exclusive non-blocking lock for this runtime instance."""

    runtime_paths.ensure_local_dirs(root)
    lock_path = runtime_paths.lock_path(root)
    handle = lock_path.open("a+", encoding="utf-8")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        handle.close()
        raise RuntimeError(
            "Runtime lock held by another process; duplicate start rejected"
        ) from exc
    handle.seek(0)
    handle.truncate()
    handle.write(f"{os.getpid()}\n")
    handle.flush()
    return handle


def run_service_forever(
    *,
    root: str | Path | None = None,
    config_path: str | None = None,
    heartbeat_seconds: float = 2.0,
    tick_every_heartbeats: int = 5,
) -> int:
    """Run until SIGTERM/SIGINT; persist final state.

    Each worker tick exercises config/DB/tool paths and swallows failures so
    individual task/tool errors cannot terminate the runtime.
    """

    _STOP.requested = False
    runtime_paths.ensure_local_dirs(root)
    log_file = runtime_paths.log_path(root)
    configure_logging(stream=sys.stderr, force=True)

    try:
        lock_handle = acquire_runtime_lock(root)
    except RuntimeError as exc:
        _append_log(log_file, f"runtime start rejected: {exc}")
        print(exc, file=sys.stderr)
        return 1

    db = runtime_paths.db_path(root)
    init_db(db)

    # Only one runtime holds the flock, so any schedule still marked running /
    # retrying at startup was orphaned by a prior SIGKILL (#98).
    from app.scheduler.store import ScheduleStore

    reclaimed = ScheduleStore(db).reclaim_orphaned_runs()
    if reclaimed:
        _append_log(
            log_file,
            f"reclaimed {reclaimed} orphaned schedule run(s) at startup",
        )
        log_event(
            _LOGGER,
            event="scheduler_reclaimed_orphaned",
            message="reclaimed orphaned schedule runs at runtime startup",
            reclaimed=reclaimed,
        )

    pid = os.getpid()
    runtime_paths.pid_path(root).write_text(f"{pid}\n", encoding="utf-8")
    started = _utc_now()
    cfg = config_path or str(runtime_paths.config_path(root))
    write_runtime_state(
        {
            "state": "running",
            "pid": pid,
            "started_at": started,
            "config_path": cfg,
            "stopped_at": None,
            "heartbeats": 0,
            "worker": "starting",
            "tick_failures_total": 0,
        },
        root=root,
    )
    _append_log(log_file, f"runtime started pid={pid} config={cfg}")

    signal.signal(signal.SIGTERM, _request_stop)
    signal.signal(signal.SIGINT, _request_stop)

    heartbeats = 0
    tick_failures_total = 0
    log_event(
        _LOGGER,
        event="runtime_started",
        message="local runtime started",
        pid=pid,
        config_path=cfg,
    )

    def _persist_tick(state_payload: dict) -> dict:
        nonlocal tick_failures_total
        tick = run_worker_tick(
            root=root,
            config_path=cfg,
            stop_requested=is_stop_requested,
        )
        state_payload.update(tick_state_patch(tick))
        if not tick.ok:
            tick_failures_total += 1
            state_payload["tick_failures_total"] = tick_failures_total
            _append_log(
                log_file,
                "worker tick failed: " + ",".join(tick.failures or [tick.detail]),
            )
        return state_payload

    # Immediate first tick so status/tests observe a live worker quickly.
    initial = {
        "state": "running",
        "pid": pid,
        "started_at": started,
        "config_path": cfg,
        "stopped_at": None,
        "heartbeats": 0,
        "worker": "active",
        "tick_failures_total": 0,
    }
    write_runtime_state(_persist_tick(initial), root=root)

    try:
        while not _STOP.requested:
            time.sleep(max(0.1, heartbeat_seconds))
            heartbeats += 1
            state_payload = {
                "state": "running",
                "pid": pid,
                "started_at": started,
                "config_path": cfg,
                "stopped_at": None,
                "heartbeats": heartbeats,
                "last_heartbeat_at": _utc_now(),
                "worker": "active",
                "tick_failures_total": tick_failures_total,
            }
            if heartbeats % max(1, tick_every_heartbeats) == 0:
                state_payload = _persist_tick(state_payload)
            if heartbeats % 15 == 0:
                write_runtime_state(state_payload, root=root)
    finally:
        stopped = _utc_now()
        write_runtime_state(
            {
                "state": "stopped",
                "pid": None,
                "started_at": started,
                "config_path": cfg,
                "stopped_at": stopped,
                "heartbeats": heartbeats,
                "worker": "stopped",
                "tick_failures_total": tick_failures_total,
            },
            root=root,
        )
        pid_file = runtime_paths.pid_path(root)
        if pid_file.is_file():
            try:
                pid_file.unlink()
            except OSError:
                pass
        try:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        try:
            lock_handle.close()
        except OSError:
            pass
        _append_log(
            log_file,
            f"runtime stopped pid={pid} heartbeats={heartbeats} "
            f"tick_failures={tick_failures_total}",
        )
        log_event(
            _LOGGER,
            event="runtime_stopped",
            message="local runtime stopped",
            pid=pid,
            heartbeats=heartbeats,
            tick_failures_total=tick_failures_total,
        )
    return 0


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _append_log(path: Path, line: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(f"{_utc_now()} {line}\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="signal-agent-runtime")
    parser.add_argument("--root", default=".")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--heartbeat-seconds", type=float, default=2.0)
    parser.add_argument("--tick-every-heartbeats", type=int, default=5)
    args = parser.parse_args(argv)
    return run_service_forever(
        root=args.root,
        config_path=args.config,
        heartbeat_seconds=args.heartbeat_seconds,
        tick_every_heartbeats=args.tick_every_heartbeats,
    )


if __name__ == "__main__":
    raise SystemExit(main())
