"""Local runtime paths and constants (#82)."""

from __future__ import annotations

import os
from pathlib import Path

DEFAULT_CONFIG_NAME = "config.yaml"
DEFAULT_ENV_NAME = ".env"
DEFAULT_DB_RELATIVE = Path("data") / "signals.sqlite3"
DEFAULT_LOG_RELATIVE = Path("logs") / "signal-agent.log"
DEFAULT_RUNTIME_DIR = Path("data") / "runtime"
PID_FILENAME = "signal-agent.pid"
LOCK_FILENAME = "signal-agent.lock"
STATE_FILENAME = "runtime-state.json"
CHAT_SESSIONS_DIRNAME = "chat-sessions"
CHAT_TRACES_DIRNAME = "chat-traces"


def project_root(cwd: str | Path | None = None) -> Path:
    """Return the working directory used for local agent files."""

    return Path(cwd or os.getcwd()).resolve()


def config_path(root: str | Path | None = None) -> Path:
    return project_root(root) / DEFAULT_CONFIG_NAME


def env_path(root: str | Path | None = None) -> Path:
    return project_root(root) / DEFAULT_ENV_NAME


def db_path(root: str | Path | None = None) -> Path:
    override = os.getenv("SIGNAL_AGENT_DB_PATH")
    if override:
        return Path(override).expanduser()
    return project_root(root) / DEFAULT_DB_RELATIVE


def log_path(root: str | Path | None = None) -> Path:
    override = os.getenv("SIGNAL_AGENT_LOG_PATH")
    if override:
        return Path(override).expanduser()
    return project_root(root) / DEFAULT_LOG_RELATIVE


def runtime_dir(root: str | Path | None = None) -> Path:
    override = os.getenv("SIGNAL_AGENT_RUNTIME_DIR")
    if override:
        return Path(override).expanduser()
    return project_root(root) / DEFAULT_RUNTIME_DIR


def pid_path(root: str | Path | None = None) -> Path:
    return runtime_dir(root) / PID_FILENAME


def lock_path(root: str | Path | None = None) -> Path:
    return runtime_dir(root) / LOCK_FILENAME


def state_path(root: str | Path | None = None) -> Path:
    return runtime_dir(root) / STATE_FILENAME


def chat_sessions_dir(root: str | Path | None = None) -> Path:
    return runtime_dir(root) / CHAT_SESSIONS_DIRNAME


def chat_traces_dir(root: str | Path | None = None) -> Path:
    return runtime_dir(root) / CHAT_TRACES_DIRNAME


def ensure_local_dirs(root: str | Path | None = None) -> dict[str, Path]:
    """Create required local directories; return created/existing paths."""

    base = project_root(root)
    paths = {
        "data": base / "data",
        "logs": base / "logs",
        "runtime": runtime_dir(base),
        "chat_sessions": chat_sessions_dir(base),
        "chat_traces": chat_traces_dir(base),
    }
    for path in paths.values():
        path.mkdir(parents=True, exist_ok=True)
    return paths