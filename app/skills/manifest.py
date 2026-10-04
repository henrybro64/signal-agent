"""Skill manifest models and validation (#89)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

SKILL_API_VERSION = 1


class SkillManifestError(ValueError):
    """Raised when a skill manifest is invalid or incompatible."""


@dataclass(slots=True, frozen=True)
class SkillManifest:
    api_version: int
    name: str
    version: str
    description: str
    tools: tuple[str, ...] = ()
    tasks: tuple[str, ...] = ()
    workflows: tuple[str, ...] = ()
    required_permissions: tuple[str, ...] = ()
    python_extras: tuple[str, ...] = ()
    config_schema: dict[str, Any] = field(default_factory=dict)
    min_api_version: int = 1
    max_api_version: int = SKILL_API_VERSION
    source_path: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "api_version": self.api_version,
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "tools": list(self.tools),
            "tasks": list(self.tasks),
            "workflows": list(self.workflows),
            "required_permissions": list(self.required_permissions),
            "python_extras": list(self.python_extras),
            "config_schema": dict(self.config_schema),
            "compatibility": {
                "min_api_version": self.min_api_version,
                "max_api_version": self.max_api_version,
            },
            "source_path": self.source_path,
        }


def parse_manifest(
    data: dict[str, Any],
    *,
    source_path: str | None = None,
) -> SkillManifest:
    if not isinstance(data, dict):
        raise SkillManifestError("manifest root must be a mapping")
    try:
        api_version = int(data.get("api_version"))
    except (TypeError, ValueError) as exc:
        raise SkillManifestError("api_version must be an integer") from exc
    name = str(data.get("name") or "").strip()
    version = str(data.get("version") or "").strip()
    description = str(data.get("description") or "").strip()
    if not name or not version or not description:
        raise SkillManifestError(
            "manifest requires non-empty name, version, and description"
        )
    compat = data.get("compatibility") or {}
    if not isinstance(compat, dict):
        raise SkillManifestError("compatibility must be a mapping")
    min_api = int(compat.get("min_api_version", 1))
    max_api = int(compat.get("max_api_version", SKILL_API_VERSION))
    if api_version < min_api or api_version > max_api:
        raise SkillManifestError(
            f"manifest api_version {api_version} outside compatibility "
            f"[{min_api}, {max_api}]"
        )
    if SKILL_API_VERSION < min_api or SKILL_API_VERSION > max_api:
        raise SkillManifestError(
            f"runtime skill API {SKILL_API_VERSION} incompatible with "
            f"manifest {name} requiring [{min_api}, {max_api}]"
        )
    tools = _string_tuple(data.get("tools"), field_name="tools")
    tasks = _string_tuple(data.get("tasks"), field_name="tasks")
    workflows = _string_tuple(data.get("workflows"), field_name="workflows")
    required_permissions = _string_tuple(
        data.get("required_permissions"),
        field_name="required_permissions",
    )
    for perm in required_permissions:
        if perm not in {"read", "notify", "write", "execute"}:
            raise SkillManifestError(
                f"unknown required permission {perm!r} in skill {name}"
            )
    python_extras = _string_tuple(
        data.get("python_extras"),
        field_name="python_extras",
    )
    schema = data.get("config_schema") or {}
    if not isinstance(schema, dict):
        raise SkillManifestError("config_schema must be a mapping")
    return SkillManifest(
        api_version=api_version,
        name=name,
        version=version,
        description=description,
        tools=tools,
        tasks=tasks,
        workflows=workflows,
        required_permissions=required_permissions,
        python_extras=python_extras,
        config_schema=dict(schema),
        min_api_version=min_api,
        max_api_version=max_api,
        source_path=source_path,
    )


def _string_tuple(raw: Any, *, field_name: str) -> tuple[str, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list) or not all(
        isinstance(item, str) and item.strip() for item in raw
    ):
        raise SkillManifestError(f"{field_name} must be a list of non-empty strings")
    return tuple(item.strip() for item in raw)
