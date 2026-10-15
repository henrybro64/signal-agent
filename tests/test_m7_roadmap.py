"""Contract tests for the M7 agent-platform roadmap (issue #60)."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ROADMAP = ROOT / "docs" / "roadmap" / "m7-agent-platform-roadmap.md"
AGENTS = ROOT / "AGENTS.md"
README = ROOT / "README.md"

CHILD_ISSUES = ("#61", "#62", "#63", "#64", "#65", "#66", "#67")


def _roadmap_text() -> str:
    assert ROADMAP.is_file(), "docs/roadmap/m7-agent-platform-roadmap.md must exist"
    return ROADMAP.read_text(encoding="utf-8")


def test_m7_roadmap_exists_and_is_non_empty() -> None:
    text = _roadmap_text()
    assert len(text) > 500


def test_m7_roadmap_documents_core_sections() -> None:
    text = _roadmap_text().lower()
    for needle in (
        "goal",
        "preserve first",
        "target architecture",
        "dependency direction",
        "planned child work",
        "file-level direction",
        "implementation principles",
        "what m7 deliberately does not do",
    ):
        assert needle in text, f"M7 roadmap missing section mention: {needle}"


def test_m7_roadmap_lists_child_issues_in_order() -> None:
    text = _roadmap_text()
    marker = "## Planned child work (execution order)"
    assert marker in text
    section = text.split(marker, 1)[1].split("## ", 1)[0]
    positions = []
    for issue in CHILD_ISSUES:
        assert issue in section, f"M7 roadmap order missing {issue}"
        positions.append(section.index(issue))
    assert positions == sorted(positions)


def test_m7_roadmap_separates_domain_from_adapters() -> None:
    text = _roadmap_text()
    assert "framework-neutral" in text.lower()
    assert "app/integrations/langchain" in text
    assert "app/integrations/mcp" in text
    assert "never leaks into" in text.lower() or "Framework code never leaks" in text
    for domain in ("app/core", "app/sources", "app/scorers", "app/notifiers"):
        assert domain in text, f"M7 roadmap should name domain package {domain}"


def test_m7_roadmap_freezes_preferred_package_roots() -> None:
    text = _roadmap_text()
    lowered = text.lower()
    assert "preferred package roots" in lowered
    for package in (
        "app/llm/",
        "app/tools/",
        "app/integrations/langchain/",
        "app/workflows/",
        "app/memory/",
        "app/evaluation/",
        "app/integrations/mcp/",
    ):
        assert package in text, f"M7 roadmap missing preferred package {package}"
    assert "job_research_agent.py" in text


def test_m7_roadmap_clarifies_issue_60_close_vs_child_checklist() -> None:
    text = _roadmap_text().lower()
    assert "child implementation" in text
    assert "leave those boxes unchecked" in text or "unchecked until" in text


def test_m7_roadmap_preserves_deterministic_workflows() -> None:
    text = _roadmap_text().lower()
    assert "deterministic" in text
    assert "job_search" in text or "job-search" in text
    assert "cli" in text
    assert "optional" in text
    assert "do not" in text and "rewrite" in text


def test_m7_roadmap_does_not_duplicate_m0_m6_domains() -> None:
    text = _roadmap_text()
    lowered = text.lower()
    assert "reimplement" in lowered or "duplicate" in lowered
    assert "M0–M6" in text or "M0-M6" in text
    assert "collectors" in lowered
    assert "telegram" in lowered


def test_m7_roadmap_warns_against_rewriting_via_issue_60() -> None:
    text = _roadmap_text()
    lowered = text.lower()
    assert "#60" in text
    assert "do **not** treat #60" in lowered or "do not treat #60" in lowered
    assert "rewrite" in lowered


def test_agents_guide_points_to_m7_roadmap_and_order() -> None:
    text = AGENTS.read_text(encoding="utf-8")
    assert "docs/roadmap/m7-agent-platform-roadmap.md" in text
    marker = "## Next execution order (M7)"
    assert marker in text
    section = text.split(marker, 1)[1].split("## ", 1)[0]
    positions = []
    for issue in CHILD_ISSUES:
        assert issue in section, f"AGENTS.md M7 order missing {issue}"
        positions.append(section.index(issue))
    assert positions == sorted(positions)


def test_agents_guide_marks_m6_complete_and_m7_ready() -> None:
    text = AGENTS.read_text(encoding="utf-8")
    assert "M6" in text and "Complete" in text
    assert "Roadmap ready" in text or "M7" in text
    assert "#57" in text and "DONE" in text


def test_readme_points_to_m7_roadmap() -> None:
    readme = README.read_text(encoding="utf-8")
    assert "docs/roadmap/m7-agent-platform-roadmap.md" in readme
    assert "M7" in readme


def test_readme_is_short_and_links_docs_index() -> None:
    readme = README.read_text(encoding="utf-8")
    assert len(readme.splitlines()) <= 150
    assert "docs/index.md" in readme
    assert "docs/getting-started.md" in readme
    assert "```python" not in readme
