"""Render systemd user unit files for the local runtime (#88)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


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

    env_lines = ""
    if spec.environment_file:
        # '-' prefix means optional; secrets stay in the env file.
        env_lines += f"EnvironmentFile=-{spec.environment_file}\n"
    for key, value in sorted(spec.environment.items()):
        # Values are paths we control; never put secrets here.
        env_lines += f"Environment={key}={value}\n"
    return f"""\
[Unit]
Description={spec.description}
After=default.target
StartLimitIntervalSec=60
StartLimitBurst={spec.start_limit_burst}

[Service]
Type=simple
WorkingDirectory={spec.working_directory}
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
    """Match ``signal-agent start`` child process (app.runtime.service)."""

    root_s = str(Path(root).resolve())
    cfg_s = str(Path(config_path).resolve())
    return (
        f"{python_executable} -m app.runtime.service --root {root_s} --config {cfg_s}"
    )
