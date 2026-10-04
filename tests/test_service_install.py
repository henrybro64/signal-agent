"""Tests for systemd user-service helpers (#88)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.cli import main
from app.service import (
    ServiceError,
    format_service_status,
    install_user_service,
    render_user_unit,
    service_status,
    uninstall_user_service,
    xdg_agent_paths,
)
from app.service.unit import UserUnitSpec, default_exec_start
from app.setup import SetupOptions, run_setup


def test_render_user_unit_has_restart_and_no_secrets() -> None:
    body = render_user_unit(
        UserUnitSpec(
            description="signal-agent local runtime",
            working_directory="/opt/signal-agent",
            exec_start=(
                "/usr/bin/python -m app.runtime.service "
                "--root /opt/signal-agent "
                "--config /opt/signal-agent/config.yaml"
            ),
            environment_file="/opt/signal-agent/.env",
            restart_sec=5,
            timeout_stop_sec=20,
            environment={"SIGNAL_AGENT_LOG_PATH": "/tmp/signal-agent.log"},
        )
    )
    assert "Restart=on-failure" in body
    assert "EnvironmentFile=-/opt/signal-agent/.env" in body
    assert "Environment=SIGNAL_AGENT_LOG_PATH=/tmp/signal-agent.log" in body
    assert "OPENAI_API_KEY" not in body
    assert "Type=simple" in body
    assert "KillSignal=SIGTERM" in body
    exec_start = default_exec_start(
        python_executable="/usr/bin/python3",
        root="/tmp/root with spaces",
        config_path="/tmp/root with spaces/config.yaml",
    )
    assert "-m app.runtime.service" in exec_start
    assert "root with spaces" in exec_start
    assert "--root '" in exec_start or '--root "' in exec_start


def test_install_uninstall_idempotent(tmp_path: Path) -> None:
    run_setup(
        root=tmp_path,
        options=SetupOptions(provider="fake", non_interactive=True, force=True),
    )
    unit_dir = tmp_path / "systemd-user"
    first = install_user_service(
        root=tmp_path,
        config_path=tmp_path / "config.yaml",
        unit_dir=unit_dir,
        enable=False,
    )
    assert Path(first["unit_path"]).is_file()
    body = Path(first["unit_path"]).read_text(encoding="utf-8")
    assert "app.runtime.service" in body
    second = install_user_service(
        root=tmp_path,
        config_path=tmp_path / "config.yaml",
        unit_dir=unit_dir,
        enable=False,
    )
    assert second["unit_path"] == first["unit_path"]
    status = service_status(root=tmp_path, unit_dir=unit_dir)
    assert status.installed
    assert status.root == str(tmp_path.resolve())
    text = format_service_status(status)
    assert "installed" in text
    removed = uninstall_user_service(unit_dir=unit_dir)
    assert removed["removed"] is True
    again = uninstall_user_service(unit_dir=unit_dir)
    assert again["removed"] is False


def test_active_systemd_unit_not_treated_as_conflict(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_setup(
        root=tmp_path,
        options=SetupOptions(provider="fake", non_interactive=True, force=True),
    )
    unit_dir = tmp_path / "systemd-user"
    fake_runtime = MagicMock(running=True, pid=4242, state="running")
    monkeypatch.setattr(
        "app.service.install.get_runtime_info",
        lambda _root: fake_runtime,
    )
    monkeypatch.setattr(
        "app.service.install._pid_is_active_user_unit",
        lambda pid, systemctl_bin=None: pid == 4242,
    )
    result = install_user_service(
        root=tmp_path,
        config_path=tmp_path / "config.yaml",
        unit_dir=unit_dir,
        enable=False,
    )
    assert result["conflict"] is None
    assert result["refused_enable"] is False


def test_conflict_refuses_enable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_setup(
        root=tmp_path,
        options=SetupOptions(provider="fake", non_interactive=True, force=True),
    )
    unit_dir = tmp_path / "systemd-user"
    fake_runtime = MagicMock(running=True, pid=4242, state="running")
    monkeypatch.setattr(
        "app.service.install.get_runtime_info",
        lambda _root: fake_runtime,
    )
    monkeypatch.setattr(
        "app.service.install._pid_is_active_user_unit",
        lambda pid, systemctl_bin=None: False,
    )
    enable_calls: list[list[str]] = []

    def fake_run(cmd, *, ignore_error=False):  # noqa: ANN001
        enable_calls.append(list(cmd))
        return 0, "ok"

    monkeypatch.setattr("app.service.install._run", fake_run)
    monkeypatch.setattr(
        "app.service.install.shutil.which",
        lambda _name: "/usr/bin/systemctl",
    )
    result = install_user_service(
        root=tmp_path,
        config_path=tmp_path / "config.yaml",
        unit_dir=unit_dir,
        enable=True,
    )
    assert result["refused_enable"] is True
    assert result["systemctl_enabled"] is False
    assert Path(result["unit_path"]).is_file()
    assert not any("enable" in " ".join(cmd) for cmd in enable_calls)


def test_xdg_seeds_config_and_status_uses_unit_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv(
        "SIGNAL_AGENT_SYSTEMD_USER_DIR",
        str(tmp_path / "systemd"),
    )
    paths = xdg_agent_paths(home=tmp_path / "home")
    assert paths.config_dir == tmp_path / "config" / "signal-agent"
    repo = tmp_path / "repo"
    run_setup(
        root=repo,
        options=SetupOptions(provider="fake", non_interactive=True, force=True),
    )
    result = install_user_service(
        root=repo,
        use_xdg=True,
        unit_dir=tmp_path / "systemd",
        enable=False,
    )
    assert Path(result["config_path"]).is_file()
    assert Path(result["config_path"]) == paths.config_path
    body = Path(result["unit_path"]).read_text(encoding="utf-8")
    assert str(paths.data_dir.resolve()) in body
    assert "SIGNAL_AGENT_LOG_PATH=" in body
    status = service_status(unit_dir=tmp_path / "systemd")
    assert status.root == str(paths.data_dir.resolve())
    assert status.config_path == str(paths.config_path.resolve())
    # Default unit dir honors XDG_CONFIG_HOME when no explicit override.
    monkeypatch.delenv("SIGNAL_AGENT_SYSTEMD_USER_DIR", raising=False)
    from app.service.install import _default_user_unit_dir

    assert _default_user_unit_dir() == tmp_path / "config" / "systemd" / "user"


def test_xdg_fails_without_source_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    empty = tmp_path / "empty-repo"
    empty.mkdir()
    with pytest.raises(ServiceError, match="XDG config missing"):
        install_user_service(
            root=empty,
            use_xdg=True,
            unit_dir=tmp_path / "systemd",
            enable=False,
        )


def test_xdg_paths_and_cli(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv(
        "SIGNAL_AGENT_SYSTEMD_USER_DIR",
        str(tmp_path / "systemd"),
    )
    run_setup(
        root=tmp_path / "repo",
        options=SetupOptions(provider="fake", non_interactive=True, force=True),
    )
    code = main(
        [
            "--root",
            str(tmp_path / "repo"),
            "service",
            "install",
            "--no-enable",
        ]
    )
    assert code == 0
    assert "wrote" in capsys.readouterr().out
    assert main(["--root", str(tmp_path / "repo"), "service", "status"]) == 0
