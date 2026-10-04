"""Discover built-in skills and manage enable/disable state (#89)."""

from __future__ import annotations

import importlib.util
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from app.runtime import paths as runtime_paths
from app.skills.manifest import (
    SKILL_API_VERSION,
    SkillManifest,
    SkillManifestError,
    parse_manifest,
)

# Built-ins enabled when config omits skills.enabled (backward compatible).
DEFAULT_ENABLED_SKILLS: tuple[str, ...] = ("job_search", "stock_monitoring")


@dataclass(slots=True)
class SkillInfo:
    manifest: SkillManifest
    enabled: bool
    missing_extras: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        payload = self.manifest.to_dict()
        payload["enabled"] = self.enabled
        payload["missing_extras"] = list(self.missing_extras)
        return payload


def builtin_manifests_dir() -> Path:
    return Path(__file__).resolve().parent / "builtin"


def skills_state_path(root: str | Path | None = None) -> Path:
    return runtime_paths.runtime_dir(root) / "skills-state.json"


def load_builtin_manifests() -> dict[str, SkillManifest]:
    """Load and validate packaged skill manifests (no arbitrary paths)."""

    directory = builtin_manifests_dir()
    manifests: dict[str, SkillManifest] = {}
    if not directory.is_dir():
        return manifests
    for path in sorted(directory.glob("*.yaml")):
        payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(payload, dict):
            raise SkillManifestError(f"{path}: root must be a mapping")
        manifest = parse_manifest(payload, source_path=str(path))
        if manifest.name in manifests:
            raise SkillManifestError(f"duplicate skill name {manifest.name!r}")
        manifests[manifest.name] = manifest
    _assert_no_duplicate_tools(manifests)
    return manifests


def list_skills(
    *,
    root: str | Path | None = None,
    config_path: str | Path | None = None,
) -> list[SkillInfo]:
    manifests = load_builtin_manifests()
    enabled = set(enabled_skill_names(root=root, config_path=config_path))
    infos: list[SkillInfo] = []
    for name in sorted(manifests):
        manifest = manifests[name]
        missing = missing_python_extras(manifest)
        infos.append(
            SkillInfo(
                manifest=manifest,
                enabled=name in enabled,
                missing_extras=missing,
            )
        )
    return infos


def get_skill(
    name: str,
    *,
    root: str | Path | None = None,
    config_path: str | Path | None = None,
) -> SkillInfo:
    manifests = load_builtin_manifests()
    if name not in manifests:
        raise KeyError(name)
    enabled = set(enabled_skill_names(root=root, config_path=config_path))
    manifest = manifests[name]
    return SkillInfo(
        manifest=manifest,
        enabled=name in enabled,
        missing_extras=missing_python_extras(manifest),
    )


def enabled_skill_names(
    *,
    root: str | Path | None = None,
    config_path: str | Path | None = None,
) -> list[str]:
    state = _load_skills_state(root=root, config_path=config_path)
    enabled = state.get("enabled")
    if enabled is None:
        return list(DEFAULT_ENABLED_SKILLS)
    return [str(item) for item in enabled]


def enable_skill(
    name: str,
    *,
    root: str | Path | None = None,
    config_path: str | Path | None = None,
) -> list[str]:
    manifests = load_builtin_manifests()
    if name not in manifests:
        raise KeyError(name)
    missing = missing_python_extras(manifests[name])
    if missing:
        raise SkillManifestError(
            f"skill {name!r} missing optional dependencies: " + ", ".join(missing)
        )
    enabled = enabled_skill_names(root=root, config_path=config_path)
    if name not in enabled:
        enabled.append(name)
    _save_enabled(
        enabled,
        root=root,
        config_path=config_path,
        allowlist_additions=_allowlist_additions_payload(
            enabled,
            manifests=manifests,
            previous=_load_skills_state(root=root, config_path=config_path),
        ),
    )
    return enabled


def disable_skill(
    name: str,
    *,
    root: str | Path | None = None,
    config_path: str | Path | None = None,
) -> list[str]:
    manifests = load_builtin_manifests()
    if name not in manifests:
        raise KeyError(name)
    enabled = [
        item
        for item in enabled_skill_names(root=root, config_path=config_path)
        if item != name
    ]
    _save_enabled(
        enabled,
        root=root,
        config_path=config_path,
        allowlist_additions=_allowlist_additions_payload(
            enabled,
            manifests=manifests,
            previous=_load_skills_state(root=root, config_path=config_path),
        ),
    )
    return enabled


def tools_for_enabled_skills(
    *,
    root: str | Path | None = None,
    config_path: str | Path | None = None,
) -> set[str]:
    """Return tool names contributed by currently enabled skills."""

    manifests = load_builtin_manifests()
    enabled = set(enabled_skill_names(root=root, config_path=config_path))
    names: set[str] = set()
    for skill_name in enabled:
        manifest = manifests.get(skill_name)
        if manifest is None:
            continue
        names.update(manifest.tools)
    return names


def allowlist_additions_for_enabled_skills(
    *,
    root: str | Path | None = None,
    config_path: str | Path | None = None,
) -> list[str]:
    """Tool names added to the effective allowlist when skills were enabled."""

    state = _load_skills_state(root=root, config_path=config_path)
    additions = state.get("allowlist_additions") or {}
    if not isinstance(additions, dict):
        return []
    enabled = set(enabled_skill_names(root=root, config_path=config_path))
    names: list[str] = []
    seen: set[str] = set()
    for skill_name in enabled:
        raw = additions.get(skill_name) or []
        if not isinstance(raw, list):
            continue
        for item in raw:
            tool = str(item).strip()
            if tool and tool not in seen:
                seen.add(tool)
                names.append(tool)
    return names


def required_permissions_for_enabled_skills(
    *,
    root: str | Path | None = None,
    config_path: str | Path | None = None,
) -> set[str]:
    manifests = load_builtin_manifests()
    enabled = set(enabled_skill_names(root=root, config_path=config_path))
    perms: set[str] = set()
    for skill_name in enabled:
        manifest = manifests.get(skill_name)
        if manifest is None:
            continue
        perms.update(manifest.required_permissions)
    return perms


def missing_python_extras(manifest: SkillManifest) -> tuple[str, ...]:
    missing: list[str] = []
    for extra in manifest.python_extras:
        # Map extras to importable modules when practical.
        module = {
            "langchain": "langchain_core",
            "mcp": "mcp",
        }.get(extra, extra.replace("-", "_"))
        if importlib.util.find_spec(module) is None:
            missing.append(extra)
    return tuple(missing)


def format_skills(infos: list[SkillInfo]) -> str:
    if not infos:
        return "(no skills)"
    lines = []
    for info in infos:
        state = "enabled" if info.enabled else "disabled"
        extras = (
            f" missing_extras={','.join(info.missing_extras)}"
            if info.missing_extras
            else ""
        )
        lines.append(
            f"{info.manifest.name}  v{info.manifest.version}  {state}  "
            f"tools={','.join(info.manifest.tools) or '-'}  "
            f"tasks={','.join(info.manifest.tasks) or '-'}  "
            f"workflows={','.join(info.manifest.workflows) or '-'}{extras}"
        )
    return "\n".join(lines)


def format_skill_info(info: SkillInfo) -> str:
    m = info.manifest
    lines = [
        f"name: {m.name}",
        f"version: {m.version}",
        f"enabled: {info.enabled}",
        f"api_version: {m.api_version} (runtime={SKILL_API_VERSION})",
        f"description: {m.description.strip()}",
        f"tools: {', '.join(m.tools) or '(none)'}",
        f"tasks: {', '.join(m.tasks) or '(none)'}",
        f"workflows: {', '.join(m.workflows) or '(none)'}",
        f"required_permissions: {', '.join(m.required_permissions) or '(none)'}",
        f"python_extras: {', '.join(m.python_extras) or '(none)'}",
    ]
    if info.missing_extras:
        lines.append("missing_extras: " + ", ".join(info.missing_extras))
    if m.source_path:
        lines.append(f"source: {m.source_path}")
    return "\n".join(lines)


def next_steps_for_skill(name: str) -> list[str]:
    """Operator follow-ups after enabling a skill."""

    if name == "browser":
        return [
            "Set browser.enabled: true in config.yaml",
            "browser_* tools are added to the effective allowlist via "
            "skills-state; ensure tools.permissions.classes.execute is "
            "ask/allow for interactive use",
            "Run: signal-agent browser start && signal-agent browser test",
        ]
    return []


def _assert_no_duplicate_tools(manifests: dict[str, SkillManifest]) -> None:
    # send_telegram_message may be shared; allow shared notify tools.
    shared_ok = {"send_telegram_message"}
    owners: dict[str, str] = {}
    for skill_name, manifest in manifests.items():
        for tool in manifest.tools:
            if tool in shared_ok:
                continue
            prior = owners.get(tool)
            if prior is not None:
                raise SkillManifestError(
                    f"duplicate tool {tool!r} in skills {prior!r} and {skill_name!r}"
                )
            owners[tool] = skill_name


def _allowlist_additions_payload(
    enabled: list[str],
    *,
    manifests: dict[str, SkillManifest],
    previous: dict[str, Any],
) -> dict[str, list[str]]:
    """Keep allowlist additions for enabled skills (esp. browser_*)."""

    prior = previous.get("allowlist_additions") or {}
    if not isinstance(prior, dict):
        prior = {}
    additions: dict[str, list[str]] = {}
    for skill_name in enabled:
        manifest = manifests.get(skill_name)
        if manifest is None:
            continue
        # Persist non-default tools so enablement makes them callable without
        # rewriting config.yaml tools.allowlist.
        default_tools = {
            "search_jobs",
            "get_tw_stock_movers",
            "query_recent_runs",
            "send_telegram_message",
        }
        tools = [t for t in manifest.tools if t not in default_tools]
        if not tools and skill_name in prior:
            # Preserve prior explicit additions if any.
            raw = prior.get(skill_name) or []
            if isinstance(raw, list) and raw:
                tools = [str(x) for x in raw]
        if tools:
            additions[skill_name] = list(tools)
    return additions


def _load_skills_state(
    *,
    root: str | Path | None,
    config_path: str | Path | None,
) -> dict[str, Any]:
    # Prefer runtime skills-state.json (CLI enable/disable) so we never need
    # to rewrite config.yaml comments. Fall back to config.yaml skills.enabled.
    state_path = skills_state_path(root)
    if state_path.is_file():
        try:
            raw = json.loads(state_path.read_text(encoding="utf-8"))
            if isinstance(raw, dict) and "enabled" in raw:
                return raw
        except (OSError, json.JSONDecodeError):
            pass
    cfg_path = Path(config_path or runtime_paths.config_path(root))
    if cfg_path.is_file():
        try:
            payload = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
            if isinstance(payload, dict) and isinstance(payload.get("skills"), dict):
                return dict(payload["skills"])
        except (OSError, yaml.YAMLError):
            pass
    return {}


def _save_enabled(
    enabled: list[str],
    *,
    root: str | Path | None,
    config_path: str | Path | None = None,
    allowlist_additions: dict[str, list[str]] | None = None,
) -> None:
    """Persist enablement to skills-state.json only (preserve config comments)."""

    # config_path is accepted for API symmetry with loaders; writes never
    # rewrite config.yaml (comments stay intact).
    _ = config_path
    runtime_paths.ensure_local_dirs(root)
    state_path = skills_state_path(root)
    payload: dict[str, Any] = {"enabled": list(enabled)}
    if allowlist_additions is not None:
        payload["allowlist_additions"] = {
            key: list(value) for key, value in allowlist_additions.items()
        }
    state_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
