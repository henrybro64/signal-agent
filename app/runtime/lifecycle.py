"""Runtime lifecycle: start/stop/restart with PID lock (#82)."""

from __future__ import annotations

import fcntl
import json
import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import paths as runtime_paths

# Absolute path avoids PATH-dependent process launch (DeepSource PTC).
_PS_EXECUTABLE = "/bin/ps"


class RuntimeLifecycleError(RuntimeError):
    """Raised for lifecycle conflicts or invalid runtime state."""


@dataclass(slots=True, frozen=True)
class RuntimeProcessInfo:
    running: bool
    pid: int | None = None
    started_at: str | None = None
    state: str = "stopped"
    detail: str = ""


def read_runtime_state(root: str | Path | None = None) -> dict[str, Any]:
    path = runtime_paths.state_path(root)
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def write_runtime_state(
    payload: dict[str, Any],
    *,
    root: str | Path | None = None,
) -> Path:
    runtime_paths.ensure_local_dirs(root)
    path = runtime_paths.state_path(root)
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return path


def get_runtime_info(root: str | Path | None = None) -> RuntimeProcessInfo:
    pid_file = runtime_paths.pid_path(root)
    state = read_runtime_state(root)
    if not pid_file.is_file():
        return RuntimeProcessInfo(
            running=False,
            state=str(state.get("state") or "stopped"),
            detail="no pid file",
        )
    try:
        pid = int(pid_file.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return RuntimeProcessInfo(
            running=False,
            state="stopped",
            detail="invalid pid file",
        )
    if not _pid_alive(pid):
        return RuntimeProcessInfo(
            running=False,
            pid=pid,
            started_at=str(state.get("started_at") or "") or None,
            state="stopped",
            detail="stale pid file",
        )
    if not _pid_is_our_runtime(pid, root=root):
        return RuntimeProcessInfo(
            running=False,
            pid=pid,
            started_at=str(state.get("started_at") or "") or None,
            state="stopped",
            detail="stale pid file (ownership mismatch)",
        )
    return RuntimeProcessInfo(
        running=True,
        pid=pid,
        started_at=str(state.get("started_at") or "") or None,
        state=str(state.get("state") or "running"),
        detail="running",
    )


def start_runtime(
    *,
    root: str | Path | None = None,
    config_path: str | Path | None = None,
    detach: bool = True,
) -> RuntimeProcessInfo:
    """Start the long-running local runtime; reject duplicates."""

    current = get_runtime_info(root)
    if current.running:
        raise RuntimeLifecycleError(
            f"Runtime already running (pid={current.pid}). "
            "Use 'signal-agent status' or 'signal-agent stop'."
        )

    # Clear stale pid/lock artifacts before launching a new child.
    _cleanup_pid(root)

    if not detach:
        # Foreground mode is `python -m app.runtime.service` only. Importing
        # service from lifecycle would create a cyclic import (service →
        # lifecycle → service) that DeepSource flags as PYL-R0401.
        raise RuntimeLifecycleError(
            "In-process foreground runtime is not supported via start_runtime; "
            "run `python -m app.runtime.service` or use detach=True."
        )

    runtime_paths.ensure_local_dirs(root)
    cfg = str(config_path or runtime_paths.config_path(root))
    log_file = runtime_paths.log_path(root)
    log_file.parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        sys.executable,
        "-m",
        "app.runtime.service",
        "--root",
        str(runtime_paths.project_root(root)),
        "--config",
        cfg,
    ]
    # The service process owns ``log_file`` via RotatingFileHandler. Redirecting
    # parent stdout into that path would pin an unrotated fd across rotations
    # (#104), so discard inherited stdio and let the child write the log.
    proc = subprocess.Popen(  # noqa: S603 - controlled local launcher
        cmd,
        cwd=str(runtime_paths.project_root(root)),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )

    # Child owns the flock + pid file. Poll until the lock-backed runtime
    # reports alive, or fail if the child exits early.
    deadline = time.time() + 3.0
    while time.time() < deadline:
        info = get_runtime_info(root)
        if info.running:
            return info
        if proc.poll() is not None:
            break
        time.sleep(0.05)
    raise RuntimeLifecycleError(
        "Runtime failed to stay alive after start "
        f"(child_exit={proc.poll()})"
    )


def stop_runtime(
    *,
    root: str | Path | None = None,
    timeout_seconds: float = 20.0,
) -> RuntimeProcessInfo:
    """Stop the runtime if running; idempotent when already stopped."""

    info = get_runtime_info(root)
    if not info.running or info.pid is None:
        _cleanup_pid(root)
        state = read_runtime_state(root)
        state.update(
            {
                "state": "stopped",
                "worker": "stopped",
                "stopped_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            }
        )
        write_runtime_state(state, root=root)
        return RuntimeProcessInfo(
            running=False,
            state="stopped",
            detail="already stopped",
        )

    pid = info.pid
    # Re-verify ownership immediately before signaling.
    if not _pid_is_our_runtime(pid, root=root):
        _cleanup_pid(root)
        return RuntimeProcessInfo(
            running=False,
            state="stopped",
            detail="stale pid file (ownership mismatch)",
        )

    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        _cleanup_pid(root)
        return RuntimeProcessInfo(running=False, state="stopped", detail="process gone")

    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        if not _pid_alive(pid):
            break
        time.sleep(0.05)
    else:
        if _pid_is_our_runtime(pid, root=root):
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

    _cleanup_pid(root)
    state = read_runtime_state(root)
    state.update(
        {
            "state": "stopped",
            "pid": None,
            "worker": "stopped",
            "stopped_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        }
    )
    write_runtime_state(state, root=root)
    return RuntimeProcessInfo(running=False, pid=pid, state="stopped", detail="stopped")


def restart_runtime(
    *,
    root: str | Path | None = None,
    config_path: str | Path | None = None,
) -> RuntimeProcessInfo:
    stop_runtime(root=root)
    return start_runtime(root=root, config_path=config_path, detach=True)


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _pid_is_our_runtime(pid: int, *, root: str | Path | None = None) -> bool:
    """Return True only when ``pid`` is a verified signal-agent runtime.

    Requires the runtime flock to be held and the lock-file PID to match.
    When ``ps`` is available, also require the command line to contain
    ``app.runtime.service`` so a recycled PID is never signaled.
    """

    if not _pid_alive(pid):
        return False
    # Prefer lock ownership: if nobody holds the lock, the pid file is stale
    # even when some process reuses the numeric PID.
    if not _runtime_lock_held(root):
        return False
    lock_pid = _read_lock_pid(root)
    if lock_pid is None or lock_pid != pid:
        return False
    cmdline = _read_cmdline(pid)
    if cmdline is None:
        # ``ps`` unavailable (restricted environments): lock+pid match is enough.
        return True
    return "app.runtime.service" in cmdline


def _read_cmdline(pid: int) -> str | None:
    # Prefer /proc on Linux: avoids COLUMNS truncation from ``ps`` (#99).
    proc_cmdline = Path(f"/proc/{pid}/cmdline")
    if proc_cmdline.is_file():
        try:
            raw = proc_cmdline.read_bytes()
        except OSError:
            raw = b""
        if raw:
            text = raw.replace(b"\x00", b" ").decode("utf-8", errors="replace")
            return text.strip() or None

    return _read_ps_cmdline(pid)


def _read_ps_cmdline(pid: int) -> str | None:
    """Read a process command line via ``ps -ww``, ignoring COLUMNS (#99)."""

    if not _PS_EXECUTABLE or not Path(_PS_EXECUTABLE).is_file():
        return None
    env = os.environ.copy()
    # ``ps`` truncates to COLUMNS when that variable is set (pytest exports
    # COLUMNS=80). Drop it so ``-ww`` can return the full command line.
    env.pop("COLUMNS", None)
    try:
        result = subprocess.run(  # noqa: S603 - absolute ps path
            [_PS_EXECUTABLE, "-ww", "-p", str(pid), "-o", "command="],
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )
    except OSError:
        return None
    if result.returncode != 0:
        return None
    return (result.stdout or "").strip() or None


def _runtime_lock_held(root: str | Path | None = None) -> bool:
    lock_path = runtime_paths.lock_path(root)
    if not lock_path.is_file():
        return False
    try:
        handle = lock_path.open("a+", encoding="utf-8")
    except OSError:
        return False
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return True
    except OSError:
        return False
    else:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        return False
    finally:
        handle.close()


def _read_lock_pid(root: str | Path | None = None) -> int | None:
    lock_path = runtime_paths.lock_path(root)
    if not lock_path.is_file():
        return None
    try:
        text = lock_path.read_text(encoding="utf-8").strip()
        return int(text.splitlines()[0]) if text else None
    except (OSError, ValueError):
        return None


def _cleanup_pid(root: str | Path | None = None) -> None:
    path = runtime_paths.pid_path(root)
    if path.is_file():
        try:
            path.unlink()
        except OSError:
            pass
