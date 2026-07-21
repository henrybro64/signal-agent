"""Contract tests for the Ubuntu VM deployment runbook (issue #56)."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNBOOK = ROOT / "docs" / "ubuntu-vm-deployment.md"
UBUNTU_DEPLOY = ROOT / "deploy" / "ubuntu"


def test_ubuntu_vm_runbook_exists_and_covers_required_steps() -> None:
    assert RUNBOOK.is_file(), "docs/ubuntu-vm-deployment.md must exist"
    text = RUNBOOK.read_text(encoding="utf-8").lower()

    for needle in (
        "clone",
        ".env",
        "data/",
        "logs/",
        "docker compose build",
        "smoke",
        "cron",
        "inspect logs",
        "k3s is optional",
        "08:05",
        "18:05",
        "every 10",
    ):
        assert needle in text, f"runbook missing required guidance: {needle}"


def test_ubuntu_deploy_helpers_exist_and_are_executable() -> None:
    required = [
        UBUNTU_DEPLOY / "run-task.sh",
        UBUNTU_DEPLOY / "run-job-search.sh",
        UBUNTU_DEPLOY / "run-tw-stock-movers.sh",
        UBUNTU_DEPLOY / "crontab.example",
        UBUNTU_DEPLOY / "systemd" / "signal-agent-job-search.service",
        UBUNTU_DEPLOY / "systemd" / "signal-agent-job-search.timer",
        UBUNTU_DEPLOY / "systemd" / "signal-agent-tw-stock-movers.service",
        UBUNTU_DEPLOY / "systemd" / "signal-agent-tw-stock-movers.timer",
    ]
    missing = [str(path) for path in required if not path.is_file()]
    assert not missing, f"missing ubuntu deploy files: {missing}"

    for script in (
        UBUNTU_DEPLOY / "run-task.sh",
        UBUNTU_DEPLOY / "run-job-search.sh",
        UBUNTU_DEPLOY / "run-tw-stock-movers.sh",
    ):
        assert script.stat().st_mode & 0o111, f"{script} must be executable"
        contents = script.read_text(encoding="utf-8")
        assert contents.startswith("#!/usr/bin/env bash")
        assert "set -euo pipefail" in contents


def test_crontab_example_matches_intended_schedules() -> None:
    crontab = (UBUNTU_DEPLOY / "crontab.example").read_text(encoding="utf-8")
    assert "CRON_TZ=Asia/Taipei" in crontab
    assert "5 8,18 * * *" in crontab
    assert "*/10 * * * 1-5" in crontab
    assert "run-job-search.sh" in crontab
    assert "run-tw-stock-movers.sh" in crontab


def test_readme_points_to_ubuntu_vm_runbook() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "docs/ubuntu-vm-deployment.md" in readme
    assert "k3s is optional" in readme.lower() or "optional" in readme.lower()
