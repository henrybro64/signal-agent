"""Contract tests for the GitHub Actions CI workflow."""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
CI_WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
MAKEFILE = ROOT / "Makefile"
README = ROOT / "README.md"


def _load_ci_workflow() -> dict:
    assert CI_WORKFLOW.is_file(), "CI workflow must exist at .github/workflows/ci.yml"
    workflow = yaml.safe_load(CI_WORKFLOW.read_text(encoding="utf-8"))
    assert isinstance(workflow, dict)
    return workflow


def test_ci_workflow_triggers_on_main_push_and_pull_request() -> None:
    workflow = _load_ci_workflow()
    on = workflow["on"]

    assert "pull_request" in on
    assert on["push"]["branches"] == ["main"]


def test_ci_workflow_uses_python_312_on_ubuntu() -> None:
    workflow = _load_ci_workflow()
    job = workflow["jobs"]["check"]

    assert job["runs-on"] == "ubuntu-latest"
    setup_step = next(
        step
        for step in job["steps"]
        if step.get("uses", "").startswith("actions/setup-python")
    )
    assert setup_step["with"]["python-version"] == "3.12"


def test_ci_workflow_runs_pytest_ruff_and_package_smoke() -> None:
    workflow = _load_ci_workflow()
    run_commands = [
        step["run"]
        for step in workflow["jobs"]["check"]["steps"]
        if "run" in step
    ]

    assert any("pip install --upgrade pip==" in command for command in run_commands)
    assert any("make test" in command for command in run_commands)
    assert any("make lint" in command for command in run_commands)
    assert any("make package-smoke" in command for command in run_commands)
    make_commands = [command for command in run_commands if command.startswith("make ")]
    assert all("PYTHON=python" in command for command in make_commands)


def test_makefile_check_is_local_ci_equivalent() -> None:
    makefile = MAKEFILE.read_text(encoding="utf-8")

    assert "check: test lint package-smoke" in makefile
    assert "test:" in makefile
    assert "lint:" in makefile
    assert "package-smoke:" in makefile
    assert "signal-agent" in makefile
    assert "load_job_search_profile" in makefile
    assert "load_tw_market_holiday_dates" in makefile
    assert "is_tw_market_holiday" in makefile


def test_readme_documents_make_check_for_local_verification() -> None:
    readme = README.read_text(encoding="utf-8").lower()

    assert "make check" in readme
    assert "github actions" in readme or "ci" in readme


def test_sonar_project_properties_excludes_deploy_from_cpd() -> None:
    sonar_path = ROOT / "sonar-project.properties"
    assert sonar_path.is_file()
    text = sonar_path.read_text(encoding="utf-8")
    assert "sonar.cpd.exclusions=deploy/**" in text
