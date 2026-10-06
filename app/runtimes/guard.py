"""Shared runtime request validation and project permission enforcement."""

from __future__ import annotations

import math

from app.tools.permissions import (
    PermissionClass,
    PermissionPolicy,
    build_permission_policy,
    get_active_permission_policy,
)

from .exceptions import RuntimePermissionError, RuntimeProviderError
from .models import RuntimeRequest

ALLOWED_ACTIONS = frozenset({"analyze", "summarize", "plan", "propose_patch"})


class _RuntimePermission:
    permission = PermissionClass.EXECUTE


def authorize(request: RuntimeRequest, policy: PermissionPolicy | None) -> None:
    if not request.goal.strip() or len(request.goal) > 32000:
        raise RuntimeProviderError("Runtime goal must contain 1–32000 characters")
    if not math.isfinite(request.timeout_seconds) or request.timeout_seconds <= 0:
        raise RuntimeProviderError("Runtime timeout must be finite and positive")
    if not request.allowed_actions or set(request.allowed_actions) - ALLOWED_ACTIONS:
        raise RuntimePermissionError("Runtime request contains unsupported actions")
    if request.working_directory is not None:
        raise RuntimePermissionError(
            "Repository access is disabled; supply selected code in the goal"
        )
    active = get_active_permission_policy() or policy or build_permission_policy()
    decision = active.decide(
        "runtime_execute",
        {"actions": list(request.allowed_actions)},
        tool=_RuntimePermission(),
        side_effect_summary=(
            "Submit supplied coding context for bounded analysis; no repository access"
        ),
    )
    if not decision.allowed:
        raise RuntimePermissionError(decision.reason)
    active.record_side_effect(PermissionClass.EXECUTE)
