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
        "completed m6",
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


def test_agents_guide_lists_completed_m6_order() -> None:
    text = _guide_text()
    marker = "## Completed M6 map (#49–#57)"
    assert marker in text
    section = text.split(marker, 1)[1].split("## ", 1)[0]
    ordered = ("#56", "#54", "#50", "#53", "#49", "#55", "#51", "#52", "#57")
    positions = []
    for issue in ordered:
        assert issue in section, f"AGENTS.md M6 order missing {issue}"
        positions.append(section.index(issue))
    assert positions == sorted(positions)


def test_agents_guide_lists_m7_execution_order() -> None:
    text = _guide_text()
    marker = "## Next execution order (M7)"
    assert marker in text
    section = text.split(marker, 1)[1].split("## ", 1)[0]
    ordered = ("#61", "#62", "#63", "#64", "#65", "#66", "#67")
    positions = []
    for issue in ordered:
        assert issue in section, f"AGENTS.md M7 order missing {issue}"
        positions.append(section.index(issue))
    assert positions == sorted(positions)
    assert "docs/m7-agent-platform-roadmap.md" in text


def test_architecture_paths_documented_in_guide_exist() -> None:
    required_paths = [
        "main.py",
        "app/cli.py",
        "app/config.py",
        "config.yaml",
        ".env.example",
        "Dockerfile",
        "docker-compose.yml",
        "AGENTS.md",
        "README.md",
        "app/core/models.py",
        "app/core/config.py",
        "app/core/db.py",
        "app/core/migrations.py",
        "app/core/secrets.py",
        "app/core/time_utils.py",
        "app/core/tw_market_calendar.py",
        "app/data/tw_market_holidays.yaml",
        "app/core/logging.py",
        "app/core/error_handling.py",
        "app/sources/base.py",
        "app/sources/http_settings.py",
        "app/sources/dummy.py",
        "app/sources/job104.py",
        "app/sources/linkedin_email.py",
        "app/sources/indeed.py",
        "app/sources/glassdoor.py",
        "app/sources/twse.py",
        "app/sources/tpex.py",
        "app/tasks/base.py",
        "app/tasks/dummy.py",
        "app/tasks/fetch.py",
        "app/tasks/job_search.py",
        "app/tasks/tw_stock_movers.py",
        "app/tasks/stock_watchlist.py",
        "app/scorers/job_rule_scorer.py",
        "app/scorers/job_llm_scorer.py",
        "app/scorers/job_score_pipeline.py",
        "app/scorers/job_search.py",
        "app/profiles/henry_job_profile.md",
        "app/notifiers/telegram.py",
        "app/sql/migrations/001_initial.sql",
        "sql/schema.sql",
        "docs/ubuntu-vm-deployment.md",
        "docs/m7-agent-platform-roadmap.md",
        "deploy/ubuntu/crontab.example",
        "deploy/k3s/cronjob-job-search.yaml",
        ".github/workflows/ci.yml",
    ]
    missing = [path for path in required_paths if not (ROOT / path).exists()]
    assert not missing, f"AGENTS.md architecture paths missing on disk: {missing}"


def test_agents_guide_names_key_sources_and_tasks() -> None:
    text = _guide_text()
    for needle in (
        "indeed.py",
        "glassdoor.py",
        "http_settings.py",
        "fetch.py",
        "dummy.py",
        "fetch_from_sources",
        "job104.py",
        "linkedin_email.py",
        "twse.py",
        "tpex.py",
    ):
        assert needle in text, f"AGENTS.md missing layout mention: {needle}"


def test_agents_guide_mvp_map_has_file_level_direction() -> None:
    text = _guide_text()
    marker = "## Completed MVP map (#1–#25)"
    assert marker in text
    section = text.split(marker, 1)[1].split("## ", 1)[0]
    for needle in (
        "app/core/models.py",
        "app/sources/base.py",
        "app/tasks/job_search.py",
        "app/sources/indeed.py",
        "app/tasks/fetch.py",
        "app/core/error_handling.py",
    ):
        assert needle in section, f"MVP map missing file-level path: {needle}"


def test_agents_guide_does_not_document_removed_pipeline_module() -> None:
    text = _guide_text()
    # Historical issue #26 mentioned app/core/pipeline.py; it was never shipped.
    assert not (ROOT / "app/core/pipeline.py").exists()
    assert "There is **no** shared `app/core/pipeline.py`" in text
    assert "│   │   ├── pipeline.py" not in text
    assert "│   │   └── pipeline.py" not in text


def test_readme_points_agents_to_agents_md() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "AGENTS.md" in readme
