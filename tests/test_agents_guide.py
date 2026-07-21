"""Contract tests keeping AGENTS.md aligned with the repo layout and roadmap."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AGENTS = ROOT / "AGENTS.md"


def _guide_text() -> str:
    assert AGENTS.is_file(), "AGENTS.md must exist as the Codex implementation map"
    return AGENTS.read_text(encoding="utf-8")


def test_agents_guide_exists_and_is_non_empty() -> None:
    text = _guide_text()
    assert len(text) > 500


def test_agents_guide_documents_core_sections() -> None:
    text = _guide_text().lower()
    for needle in (
        "project intent",
        "high-level architecture",
        "coding rules",
        "pr expectations",
        "completed mvp",
        "next execution order",
        "deployment",
    ):
        assert needle in text, f"AGENTS.md missing section mention: {needle}"


def test_agents_guide_warns_against_reimplementing_mvp() -> None:
    text = _guide_text()
    lowered = text.lower()
    assert "treat #26" in lowered
    assert "reimplement" in lowered
    assert "#1–#25" in text or "#1-#25" in text


def test_agents_guide_lists_m6_execution_order() -> None:
    text = _guide_text()
    marker = "## Next execution order (M6)"
    assert marker in text
    section = text.split(marker, 1)[1].split("## ", 1)[0]
    ordered = ("#56", "#54", "#50", "#53", "#49", "#55", "#51", "#52", "#57")
    positions = []
    for issue in ordered:
        assert issue in section, f"AGENTS.md M6 order missing {issue}"
        positions.append(section.index(issue))
    assert positions == sorted(positions)


def test_architecture_paths_documented_in_guide_exist() -> None:
    required_paths = [
        "main.py",
        "app/cli.py",
        "config.yaml",
        ".env.example",
        "Dockerfile",
        "docker-compose.yml",
        "AGENTS.md",
        "README.md",
        "app/core/models.py",
        "app/core/config.py",
        "app/core/db.py",
        "app/core/secrets.py",
        "app/core/time_utils.py",
        "app/core/logging.py",
        "app/core/error_handling.py",
        "app/sources/base.py",
        "app/tasks/base.py",
        "app/tasks/job_search.py",
        "app/tasks/tw_stock_movers.py",
        "app/scorers/job_rule_scorer.py",
        "app/scorers/job_llm_scorer.py",
        "app/profiles/henry_job_profile.md",
        "app/notifiers/telegram.py",
        "sql/schema.sql",
        "deploy/k3s/cronjob-job-search.yaml",
        ".github/workflows/ci.yml",
    ]
    missing = [path for path in required_paths if not (ROOT / path).exists()]
    assert not missing, f"AGENTS.md architecture paths missing on disk: {missing}"


def test_agents_guide_does_not_document_removed_pipeline_module() -> None:
    text = _guide_text()
    # Historical issue #26 mentioned app/core/pipeline.py; it was never shipped.
    assert "app/core/pipeline.py" not in text
    assert not (ROOT / "app/core/pipeline.py").exists()


def test_readme_points_agents_to_agents_md() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "AGENTS.md" in readme
