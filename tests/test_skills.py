"""Tests for built-in skill manifests (#89)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from app.cli import main
from app.setup import SetupOptions, run_setup
from app.skills import (
    SkillManifestError,
    disable_skill,
    enable_skill,
    enabled_skill_names,
    format_skill_info,
    get_skill,
    list_skills,
    load_builtin_manifests,
    parse_manifest,
    required_permissions_for_enabled_skills,
    tools_for_enabled_skills,
)
from app.skills.registry import skills_state_path
from app.tools import build_tool_registry


def _prepare(tmp_path: Path) -> Path:
    run_setup(
        root=tmp_path,
        options=SetupOptions(provider="fake", non_interactive=True, force=True),
    )
    return tmp_path / "config.yaml"


def test_builtin_manifests_validate_and_unique_tools() -> None:
    manifests = load_builtin_manifests()
    assert "job_search" in manifests
    assert "stock_monitoring" in manifests
    assert "browser" in manifests
    job = manifests["job_search"]
    assert "search_jobs" in job.tools
    assert "job_search" in job.tasks


def test_invalid_and_incompatible_manifests() -> None:
    with pytest.raises(SkillManifestError):
        parse_manifest(
            {
                "api_version": 1,
                "name": "",
                "version": "1",
                "description": "x",
            }
        )
    with pytest.raises(
        SkillManifestError,
        match="outside compatibility|incompatible",
    ):
        parse_manifest(
            {
                "api_version": 1,
                "name": "x",
                "version": "1",
                "description": "desc",
                "compatibility": {"min_api_version": 99, "max_api_version": 99},
            }
        )
    with pytest.raises(SkillManifestError, match="unknown required permission"):
        parse_manifest(
            {
                "api_version": 1,
                "name": "x",
                "version": "1",
                "description": "desc",
                "required_permissions": ["root"],
            }
        )


def test_enable_disable_changes_tool_registration(tmp_path: Path) -> None:
    cfg = _prepare(tmp_path)
    assert "job_search" in enabled_skill_names(root=tmp_path, config_path=cfg)
    tools = tools_for_enabled_skills(root=tmp_path, config_path=cfg)
    assert "search_jobs" in tools
    assert "get_tw_stock_movers" in tools
    assert "browser_navigate" not in tools

    # Preserve operator comments — enable/disable must not rewrite config.yaml.
    original = cfg.read_text(encoding="utf-8")
    comment_marker = "# comment-must-survive"
    cfg.write_text(comment_marker + "\n" + original, encoding="utf-8")

    disable_skill("job_search", root=tmp_path, config_path=cfg)
    assert comment_marker in cfg.read_text(encoding="utf-8")
    assert skills_state_path(tmp_path).is_file()
    tools = tools_for_enabled_skills(root=tmp_path, config_path=cfg)
    assert "search_jobs" not in tools
    registry = build_tool_registry(
        allowlist=["search_jobs", "query_recent_runs", "get_tw_stock_movers"],
        root=tmp_path,
        config_path=cfg,
    )
    assert "search_jobs" not in registry.allowlist
    assert "query_recent_runs" in registry.allowlist

    enable_skill("browser", root=tmp_path, config_path=cfg)
    assert comment_marker in cfg.read_text(encoding="utf-8")
    tools = tools_for_enabled_skills(root=tmp_path, config_path=cfg)
    assert "browser_navigate" in tools
    perms = required_permissions_for_enabled_skills(root=tmp_path, config_path=cfg)
    assert "execute" in perms
    # Allowlist additions from skill enable make browser tools callable.
    registry = build_tool_registry(
        allowlist=["query_recent_runs"],
        root=tmp_path,
        config_path=cfg,
    )
    assert "browser_navigate" in registry.allowlist


def test_skill_gating_respects_root_not_cwd(tmp_path: Path, monkeypatch) -> None:
    cfg = _prepare(tmp_path)
    disable_skill("job_search", root=tmp_path, config_path=cfg)
    monkeypatch.chdir(Path.cwd())  # keep cwd as repo
    from app.core.config import load_config

    config = load_config(cfg)
    registry = build_tool_registry(
        allowlist=list(config.tools.allowlist),
        root=tmp_path,
        config_path=cfg,
    )
    assert "search_jobs" not in registry.allowlist


def test_corrupt_skills_state_fails_closed(tmp_path: Path) -> None:
    cfg = _prepare(tmp_path)
    state = skills_state_path(tmp_path)
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text("{not-json", encoding="utf-8")
    with pytest.raises(SkillManifestError, match="corrupt skills-state"):
        enabled_skill_names(root=tmp_path, config_path=cfg)
    catalog_tools = __import__(
        "app.tools.registry", fromlist=["builtin_tools"]
    ).builtin_tools(
        root=tmp_path,
        config_path=cfg,
    )
    assert set(catalog_tools) == {"query_recent_runs"}


def test_config_enabled_browser_adds_allowlist(tmp_path: Path) -> None:
    cfg = _prepare(tmp_path)
    payload = yaml.safe_load(cfg.read_text(encoding="utf-8"))
    payload["skills"] = {"enabled": ["job_search", "stock_monitoring", "browser"]}
    cfg.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    registry = build_tool_registry(
        allowlist=["query_recent_runs"],
        root=tmp_path,
        config_path=cfg,
    )
    assert "browser_navigate" in registry.allowlist


def test_unknown_allowlist_tool_still_errors(tmp_path: Path) -> None:
    cfg = _prepare(tmp_path)
    from app.tools import ToolError

    with pytest.raises(ToolError, match="Unknown tools"):
        build_tool_registry(
            allowlist=["query_recent_runs", "not_a_real_tool"],
            root=tmp_path,
            config_path=cfg,
        )


def test_skills_failure_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.skills as skills_pkg
    import app.tools.registry as tr

    def boom(**_kwargs):
        raise RuntimeError("skills broken")

    monkeypatch.setattr(skills_pkg, "tools_for_enabled_skills", boom)
    catalog = tr.builtin_tools()
    assert set(catalog) == {"query_recent_runs"}


def test_dependency_failure_surfaces(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = _prepare(tmp_path)
    assert "browser" in load_builtin_manifests()
    from app.skills import registry as skills_registry

    def _missing_extras(manifest: object) -> tuple[str, ...]:
        name = getattr(manifest, "name", "")
        return ("nonexistent_extra",) if name == "browser" else ()

    monkeypatch.setattr(skills_registry, "missing_python_extras", _missing_extras)
    with pytest.raises(SkillManifestError, match="missing optional dependencies"):
        enable_skill("browser", root=tmp_path, config_path=cfg)


def test_skill_cli_and_info(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _prepare(tmp_path)
    assert main(["--root", str(tmp_path), "skills"]) == 0
    out = capsys.readouterr().out
    assert "job_search" in out
    assert "workflows=" in out
    assert main(["--root", str(tmp_path), "skill", "info", "job_search"]) == 0
    info_out = capsys.readouterr().out
    assert "search_jobs" in info_out
    assert main(["--root", str(tmp_path), "skill", "disable", "stock_monitoring"]) == 0
    assert main(["--root", str(tmp_path), "skill", "enable", "stock_monitoring"]) == 0
    info = get_skill("job_search", root=tmp_path, config_path=tmp_path / "config.yaml")
    assert "job_search" in format_skill_info(info)
    infos = list_skills(root=tmp_path, config_path=tmp_path / "config.yaml")
    assert any(item.enabled for item in infos)


def test_duplicate_tool_names_rejected(tmp_path: Path) -> None:
    from app.skills.manifest import SkillManifest
    from app.skills.registry import _assert_no_duplicate_tools

    with pytest.raises(SkillManifestError, match="duplicate tool"):
        _assert_no_duplicate_tools(
            {
                "a": SkillManifest(
                    api_version=1,
                    name="a",
                    version="1",
                    description="a",
                    tools=("search_jobs",),
                ),
                "b": SkillManifest(
                    api_version=1,
                    name="b",
                    version="1",
                    description="b",
                    tools=("search_jobs",),
                ),
            }
        )


def test_setup_config_roundtrip_keeps_skills(tmp_path: Path) -> None:
    cfg = _prepare(tmp_path)
    payload = yaml.safe_load(cfg.read_text(encoding="utf-8"))
    names = enabled_skill_names(root=tmp_path, config_path=cfg)
    assert "job_search" in names
    assert isinstance(payload, dict)
