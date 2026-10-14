"""Render systemd user unit files for the local runtime (#88)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

# Tokens safe to leave unquoted in ExecStart= / Environment=.
_SAFE_UNIT_TOKEN = re.compile(r"^[A-Za-z0-9_./:@+=-]+$")


@dataclass(slots=True, frozen=True)
class UserUnitSpec:
    description: str
    working_directory: str
    exec_start: str
    environment_file: str | None
    restart_sec: int = 5
    start_limit_burst: int = 5
    timeout_stop_sec: int = 20
    environment: dict[str, str] = field(default_factory=dict)


def render_user_unit(spec: UserUnitSpec) -> str:
    """Return a systemd user unit body (never embeds secrets)."""

    working = _systemd_path_value(
        spec.working_directory,
        field_name="WorkingDirectory",
    )
    env_lines = ""
    if spec.environment_file:
        # '-' prefix means optional; secrets stay in the env file.
        env_path = _systemd_path_value(
            spec.environment_file,
            field_name="EnvironmentFile",
        )
        env_lines += f"EnvironmentFile=-{env_path}\n"
    for key, value in sorted(spec.environment.items()):
        # Values are paths we control; never put secrets here.
        env_lines += f"Environment={key}={_systemd_exec_arg(value)}\n"
    return f"""\
[Unit]
Description={spec.description}
After=default.target
StartLimitIntervalSec=60
StartLimitBurst={spec.start_limit_burst}

[Service]
Type=simple
WorkingDirectory={working}
{env_lines}ExecStart={spec.exec_start}
Restart=on-failure
RestartSec={spec.restart_sec}
TimeoutStopSec={spec.timeout_stop_sec}
KillMode=mixed
KillSignal=SIGTERM
# Keep secrets out of the unit; load via EnvironmentFile / process env.
NoNewPrivileges=true

[Install]
WantedBy=default.target
"""


def default_exec_start(
    *,
    python_executable: str,
    root: str | Path,
    config_path: str | Path,
) -> str:
    """Match ``signal-agent start`` child process (app.runtime.service).

    Uses ``Path.absolute()`` (not ``resolve()``) for the interpreter so a
    ``.venv/bin/python`` symlink is preserved and the process stays inside
    the venv (#100).
    """

    python_s = _systemd_exec_arg(_absolute_keep_symlink(python_executable))
    root_s = _systemd_exec_arg(_absolute_keep_symlink(root))
    cfg_s = _systemd_exec_arg(_absolute_keep_symlink(config_path))
    return f"{python_s} -m app.runtime.service --root {root_s} --config {cfg_s}"


def _absolute_keep_symlink(path: str | Path) -> str:
    """Absolute path without following symlinks (venv interpreters)."""

    return str(Path(path).expanduser().absolute())


def _systemd_path_value(path: str, *, field_name: str) -> str:
    """Return an unquoted path safe for non-ExecStart unit directives.

    ``WorkingDirectory=`` / ``EnvironmentFile=`` do not strip shell quotes, so
    ``shlex.quote`` would embed literal quote characters (#100).
    """

    text = _absolute_keep_symlink(path) if path else path
    if (
        not text
        or any(ch.isspace() for ch in text)
        or any(ch in text for ch in "\"'\\")
    ):
        raise ValueError(
            f"{field_name} path must not contain whitespace or quotes: {path!r}"
        )
    return text


def _systemd_exec_arg(value: str) -> str:
    """Quote one ExecStart= token using systemd double-quote rules."""

    if _SAFE_UNIT_TOKEN.fullmatch(value):
        return value
    escaped = (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("$", "$$")
        .replace("%", "%%")
    )
    return f'"{escaped}"'
