"""Built-in skill/plugin manifests (#89)."""

from .manifest import (
    SKILL_API_VERSION,
    SkillManifest,
    SkillManifestError,
    parse_manifest,
)
from .registry import (
    DEFAULT_ENABLED_SKILLS,
    SkillInfo,
    allowlist_additions_for_enabled_skills,
    disable_skill,
    enable_skill,
    enabled_skill_names,
    format_skill_info,
    format_skills,
    get_skill,
    list_skills,
    load_builtin_manifests,
    next_steps_for_skill,
    required_permissions_for_enabled_skills,
    tools_for_enabled_skills,
)

__all__ = [
    "DEFAULT_ENABLED_SKILLS",
    "SKILL_API_VERSION",
    "SkillInfo",
    "SkillManifest",
    "SkillManifestError",
    "allowlist_additions_for_enabled_skills",
    "disable_skill",
    "enable_skill",
    "enabled_skill_names",
    "format_skill_info",
    "format_skills",
    "get_skill",
    "list_skills",
    "load_builtin_manifests",
    "next_steps_for_skill",
    "parse_manifest",
    "required_permissions_for_enabled_skills",
    "tools_for_enabled_skills",
]
