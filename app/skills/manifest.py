"""Skill manifest models and validation (#89)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

SKILL_API_VERSION = 1

_VALID_PERMISSIONS = frozenset({"read", "notify", "write", "execute"})


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
    api_version = _require_int(data.get("api_version"), field_name="api_version")
    name = str(data.get("name") or "").strip()
    version = str(data.get("version") or "").strip()
    description = str(data.get("description") or "").strip()
    if not name or not version or not description:
        raise SkillManifestError(
            "manifest requires non-empty name, version, and description"
        )
    min_api, max_api = _parse_compatibility(data.get("compatibility"))
    _assert_api_compatible(api_version, min_api, max_api, name=name)
    required_permissions = _string_tuple(
        data.get("required_permissions"),
        field_name="required_permissions",
    )
    _assert_known_permissions(required_permissions, name=name)
    schema = data.get("config_schema") or {}
    if not isinstance(schema, dict):
        raise SkillManifestError("config_schema must be a mapping")
    return SkillManifest(
        api_version=api_version,
        name=name,
        version=version,
        description=description,
        tools=_string_tuple(data.get("tools"), field_name="tools"),
        tasks=_string_tuple(data.get("tasks"), field_name="tasks"),
        workflows=_string_tuple(data.get("workflows"), field_name="workflows"),
        required_permissions=required_permissions,
        python_extras=_string_tuple(
            data.get("python_extras"),
            field_name="python_extras",
        ),
        config_schema=dict(schema),
        min_api_version=min_api,
        max_api_version=max_api,
        source_path=source_path,
    )


def _require_int(raw: Any, *, field_name: str) -> int:
    if raw is None:
        raise SkillManifestError(f"{field_name} must be an integer")
    try:
        return int(raw)
    except (TypeError, ValueError) as exc:
        raise SkillManifestError(f"{field_name} must be an integer") from exc


def _parse_compatibility(raw: Any) -> tuple[int, int]:
    compat = raw or {}
    if not isinstance(compat, dict):
        raise SkillManifestError("compatibility must be a mapping")
    min_api = _require_int(
        compat.get("min_api_version", 1),
        field_name="compatibility.min_api_version",
    )
    max_api = _require_int(
        compat.get("max_api_version", SKILL_API_VERSION),
        field_name="compatibility.max_api_version",
    )
    return min_api, max_api


def _assert_api_compatible(
    api_version: int,
    min_api: int,
    max_api: int,
    *,
    name: str,
) -> None:
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


def _assert_known_permissions(permissions: tuple[str, ...], *, name: str) -> None:
    for perm in permissions:
        if perm not in _VALID_PERMISSIONS:
            raise SkillManifestError(
                f"unknown required permission {perm!r} in skill {name}"
            )


def _string_tuple(raw: Any, *, field_name: str) -> tuple[str, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list) or not all(
        isinstance(item, str) and item.strip() for item in raw
    ):
        raise SkillManifestError(f"{field_name} must be a list of non-empty strings")
    return tuple(item.strip() for item in raw)
