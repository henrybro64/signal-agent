"""Install/uninstall/status for systemd --user units (#88)."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.runtime.lifecycle import get_runtime_info
from app.service.paths import xdg_agent_paths
from app.service.unit import UserUnitSpec, default_exec_start, render_user_unit

UNIT_NAME = "signal-agent.service"
INSTALL_META_NAME = "signal-agent.install.json"


class ServiceError(RuntimeError):
    """Raised for service install/status failures."""


@dataclass(slots=True, frozen=True)
class ServiceStatus:
    installed: bool
    unit_path: str
    active: str
    enabled: str
    runtime_running: bool
    runtime_pid: int | None
    detail: str
    conflict: str | None = None
    root: str | None = None
    config_path: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "installed": self.installed,
            "unit_path": self.unit_path,
            "active": self.active,
            "enabled": self.enabled,
            "runtime_running": self.runtime_running,
            "runtime_pid": self.runtime_pid,
            "detail": self.detail,
            "conflict": self.conflict,
            "root": self.root,
            "config_path": self.config_path,
        }


def render_user_unit_for_root(
    *,
    root: str | Path,
    config_path: str | Path,
    env_path: str | Path | None = None,
    python_executable: str | None = None,
    environment: dict[str, str] | None = None,
) -> str:
    python = python_executable or sys.executable
    exec_start = default_exec_start(
        python_executable=python,
        root=root,
        config_path=config_path,
    )
    return render_user_unit(
        UserUnitSpec(
            description="signal-agent local runtime",
            working_directory=str(Path(root).resolve()),
            exec_start=exec_start,
            environment_file=(str(Path(env_path).resolve()) if env_path else None),
            environment=dict(environment or {}),
        )
    )


def install_user_service(
    *,
    root: str | Path | None = None,
    config_path: str | Path | None = None,
    use_xdg: bool = False,
    unit_dir: str | Path | None = None,
    enable: bool = True,
    systemctl_bin: str | None = None,
) -> dict[str, Any]:
    """Write the user unit idempotently; optionally enable via systemctl."""

    base, cfg, env, environment = _resolve_install_layout(
        root=root,
        config_path=config_path,
        use_xdg=use_xdg,
    )
    runtime = get_runtime_info(base)
    conflict = None
    if runtime.running and not _pid_is_active_user_unit(
        runtime.pid,
        systemctl_bin=systemctl_bin,
    ):
        conflict = (
            f"Foreground/detached runtime already running (pid={runtime.pid}). "
            "Stop it with `signal-agent stop` before enabling the user service "
            "to avoid PID conflicts."
        )

    body = render_user_unit_for_root(
        root=base,
        config_path=cfg,
        env_path=env if Path(env).is_file() or use_xdg else None,
        environment=environment,
    )
    destination = Path(unit_dir or _default_user_unit_dir()) / UNIT_NAME
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(body, encoding="utf-8")
    _write_install_meta(
        destination.parent,
        {
            "root": str(Path(base).resolve()),
            "config_path": str(Path(cfg).resolve()),
            "use_xdg": use_xdg,
            "unit_path": str(destination),
        },
    )

    systemctl_ok, detail, refused_enable = _enable_unit_if_requested(
        destination=destination,
        enable=enable,
        conflict=conflict,
        runtime_pid=runtime.pid,
        systemctl_bin=systemctl_bin,
    )
    return {
        "unit_path": str(destination),
        "root": str(base),
        "config_path": str(cfg),
        "systemctl_enabled": systemctl_ok,
        "detail": detail,
        "conflict": conflict,
        "refused_enable": refused_enable,
    }


def uninstall_user_service(
    *,
    unit_dir: str | Path | None = None,
    systemctl_bin: str | None = None,
) -> dict[str, Any]:
    destination = Path(unit_dir or _default_user_unit_dir()) / UNIT_NAME
    meta_path = destination.parent / INSTALL_META_NAME
    systemctl = systemctl_bin or shutil.which("systemctl")
    if systemctl and destination.is_file():
        _run(
            [systemctl, "--user", "disable", "--now", UNIT_NAME],
            ignore_error=True,
        )
        _run([systemctl, "--user", "daemon-reload"], ignore_error=True)
    existed = destination.is_file()
    if existed:
        destination.unlink()
    if meta_path.is_file():
        meta_path.unlink()
    return {
        "unit_path": str(destination),
        "removed": existed,
        "detail": "removed" if existed else "already absent",
    }


def service_status(
    *,
    root: str | Path | None = None,
    unit_dir: str | Path | None = None,
    systemctl_bin: str | None = None,
) -> ServiceStatus:
    destination = Path(unit_dir or _default_user_unit_dir()) / UNIT_NAME
    installed = destination.is_file()
    active, enabled = _query_systemd_state(
        installed=installed,
        systemctl_bin=systemctl_bin,
    )

    from app.runtime import paths as runtime_paths

    meta = _read_install_meta(destination.parent)
    unit_root, unit_config = _parse_unit_root_config(destination)
    base = Path(meta.get("root") or unit_root or runtime_paths.project_root(root))
    cfg = str(meta.get("config_path") or unit_config or "") or None
    runtime = get_runtime_info(base)
    conflict = None
    if installed and runtime.running and active not in {"active", "activating"}:
        conflict = (
            "Detached runtime PID is running outside systemd; "
            "prefer one supervisor (service or `signal-agent start`)."
        )
    return ServiceStatus(
        installed=installed,
        unit_path=str(destination),
        active=active,
        enabled=enabled,
        runtime_running=runtime.running,
        runtime_pid=runtime.pid,
        detail="ok" if installed else "unit not installed",
        conflict=conflict,
        root=str(base),
        config_path=cfg,
    )


def format_service_status(status: ServiceStatus) -> str:
    lines = [
        f"Unit: {status.unit_path} ({'installed' if status.installed else 'missing'})",
        f"systemd active: {status.active}",
        f"systemd enabled: {status.enabled}",
        f"Root: {status.root or '(unknown)'}",
        f"Config: {status.config_path or '(unknown)'}",
        f"Runtime process: "
        f"{'running' if status.runtime_running else 'stopped'}"
        + (f" (pid={status.runtime_pid})" if status.runtime_pid else ""),
        f"Detail: {status.detail}",
    ]
    if status.conflict:
        lines.append(f"Conflict: {status.conflict}")
    return "\n".join(lines)


def _resolve_install_layout(
    *,
    root: str | Path | None,
    config_path: str | Path | None,
    use_xdg: bool,
) -> tuple[Path, Path, Path, dict[str, str]]:
    from app.runtime import paths as runtime_paths

    source_root = runtime_paths.project_root(root)
    environment: dict[str, str] = {}
    if not use_xdg:
        base = source_root
        cfg = Path(config_path or runtime_paths.config_path(base))
        env = runtime_paths.env_path(base)
        runtime_paths.ensure_local_dirs(base)
        if not cfg.is_file():
            raise ServiceError(
                f"Config not found at {cfg}. Run `signal-agent setup` first."
            )
        return base, cfg, env, environment

    paths = xdg_agent_paths()
    base = paths.data_dir
    cfg = paths.config_path
    env = paths.env_path
    source_cfg = (
        Path(config_path) if config_path else runtime_paths.config_path(source_root)
    )
    for directory in (
        paths.config_dir,
        paths.data_dir,
        paths.state_dir / "logs",
        paths.runtime_dir,
    ):
        directory.mkdir(parents=True, exist_ok=True)
    _seed_xdg_files(
        source_config=source_cfg,
        dest_config=cfg,
        dest_env=env,
        source_env=runtime_paths.env_path(source_root),
    )
    environment["SIGNAL_AGENT_LOG_PATH"] = str(paths.log_path)
    runtime_paths.ensure_local_dirs(base)
    return base, cfg, env, environment


def _enable_unit_if_requested(
    *,
    destination: Path,
    enable: bool,
    conflict: str | None,
    runtime_pid: int | None,
    systemctl_bin: str | None,
) -> tuple[bool, str, bool]:
    detail = f"wrote {destination}"
    if not enable:
        return False, detail, False
    if conflict:
        return (
            False,
            (
                f"wrote {destination}; refused to enable because a runtime "
                f"process is already running (pid={runtime_pid}). "
                "Stop it with `signal-agent stop`, then re-run "
                "`signal-agent service install`."
            ),
            True,
        )
    systemctl = systemctl_bin or shutil.which("systemctl")
    if not systemctl:
        return (
            False,
            (
                f"wrote {destination}; systemctl not found "
                "(unit file only; enable manually on Ubuntu)"
            ),
            False,
        )
    _run([systemctl, "--user", "daemon-reload"], ignore_error=True)
    code, out = _run(
        [systemctl, "--user", "enable", "--now", UNIT_NAME],
        ignore_error=True,
    )
    if code == 0:
        return True, f"installed and enabled {destination}", False
    return False, f"wrote {destination}; systemctl enable failed: {out}", False


def _query_systemd_state(
    *,
    installed: bool,
    systemctl_bin: str | None,
) -> tuple[str, str]:
    if not installed:
        return "inactive", "disabled"
    systemctl = systemctl_bin or shutil.which("systemctl")
    if not systemctl:
        return "unknown", "unknown"
    code, out = _run(
        [systemctl, "--user", "is-active", UNIT_NAME],
        ignore_error=True,
    )
    active = out.strip() or ("inactive" if code else "unknown")
    _code2, out2 = _run(
        [systemctl, "--user", "is-enabled", UNIT_NAME],
        ignore_error=True,
    )
    enabled = out2.strip() or "unknown"
    return active, enabled


def _seed_xdg_files(
    *,
    source_config: Path,
    dest_config: Path,
    dest_env: Path,
    source_env: Path,
) -> None:
    if not dest_config.is_file():
        if source_config.is_file():
            shutil.copy2(source_config, dest_config)
        else:
            raise ServiceError(
                f"XDG config missing at {dest_config} and no source config "
                f"at {source_config}. Run `signal-agent setup` in the project "
                "root, then retry `service install --xdg`."
            )
    if not dest_env.is_file() and source_env.is_file():
        shutil.copy2(source_env, dest_env)


def _write_install_meta(unit_dir: Path, payload: dict[str, Any]) -> None:
    path = unit_dir / INSTALL_META_NAME
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _read_install_meta(unit_dir: Path) -> dict[str, Any]:
    path = unit_dir / INSTALL_META_NAME
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _parse_unit_root_config(unit_path: Path) -> tuple[str | None, str | None]:
    if not unit_path.is_file():
        return None, None
    try:
        text = unit_path.read_text(encoding="utf-8")
    except OSError:
        return None, None
    root_match = re.search(r"--root\s+(\S+)", text)
    cfg_match = re.search(r"--config\s+(\S+)", text)
    return (
        root_match.group(1) if root_match else None,
        cfg_match.group(1) if cfg_match else None,
    )


def _default_user_unit_dir() -> Path:
    override = os.environ.get("SIGNAL_AGENT_SYSTEMD_USER_DIR")
    if override:
        return Path(override).expanduser()
    config_home = Path(
        os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config")
    ).expanduser()
    return config_home / "systemd" / "user"


def _pid_is_active_user_unit(
    pid: int | None,
    *,
    systemctl_bin: str | None,
) -> bool:
    """True when ``pid`` is the MainPID of the installed user unit."""

    if pid is None:
        return False
    systemctl = systemctl_bin or shutil.which("systemctl")
    if not systemctl:
        return False
    code, out = _run(
        [systemctl, "--user", "show", "-p", "MainPID", "--value", UNIT_NAME],
        ignore_error=True,
    )
    if code != 0:
        return False
    try:
        return int(out.strip() or "0") == pid
    except ValueError:
        return False


def _run(
    cmd: list[str],
    *,
    ignore_error: bool = False,
) -> tuple[int, str]:
    try:
        completed = subprocess.run(  # noqa: S603
            cmd,
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        if ignore_error:
            return 1, str(exc)
        raise ServiceError(str(exc)) from exc
    text = (completed.stdout or completed.stderr or "").strip()
    if completed.returncode != 0 and not ignore_error:
        raise ServiceError(text or f"command failed: {cmd}")
    return completed.returncode, text
