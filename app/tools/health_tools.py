"""Health / run-history tool wrappers."""

from __future__ import annotations

from typing import Any

from app.core.db import get_recent_signal_runs, get_recent_task_runs
from app.core.health import resolve_db_path

from .args import optional_int
from .models import ToolContext, ToolError, ToolResult
from .permissions import PermissionClass

_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "limit": {
            "type": "integer",
            "minimum": 1,
            "maximum": 100,
            "description": "Maximum recent run rows to return.",
        },
        "run_kind": {
            "type": "string",
            "enum": ["task", "signal"],
            "description": (
                "Which history table to read: task_runs (CLI --status style) "
                "or signal_runs (per-source collectors). Default: task."
            ),
        },
    },
}


class QueryRecentRunsTool:
    """Read recent task or source collection runs from SQLite (read-only)."""

    name = "query_recent_runs"
    permission = PermissionClass.READ
    description = (
        "Query recent run history from the local SQLite database "
        "(task_runs by default, or signal_runs). "
        "Read-only; does not execute arbitrary SQL."
    )
    input_schema = _INPUT_SCHEMA

    def __init__(self) -> None:
        # Instance copy so DeepSource/PYL-R0201 treats invoke as using self.
        self._tool_name = type(self).name

    def invoke(  # skipcq: PYL-R0201
        self,
        arguments: dict[str, Any],
        *,
        context: ToolContext,
    ) -> ToolResult:
        return _query_recent_runs(self._tool_name, arguments, context=context)


def _query_recent_runs(
    tool_name: str,
    arguments: dict[str, Any],
    *,
    context: ToolContext,
) -> ToolResult:
    limit = optional_int(arguments, "limit", default=20, minimum=1, maximum=100)
    run_kind = str(arguments.get("run_kind") or "task").strip().lower()
    if run_kind not in {"task", "signal"}:
        raise ToolError(
            f"{tool_name}: 'run_kind' must be 'task' or 'signal'",
            code="invalid_argument",
        )

    if context.query_runs is not None:
        rows = context.query_runs(limit=limit, run_kind=run_kind)
        return ToolResult(
            ok=True,
            data={"count": len(rows), "runs": rows[:limit], "run_kind": run_kind},
            metadata={"mode": "injected"},
        )

    db_path = resolve_db_path(context.db_path)
    if run_kind == "signal":
        runs = _serialize_signal_runs(
            get_recent_signal_runs(limit=limit, db_path=db_path)
        )
    else:
        runs = _serialize_task_runs(
            get_recent_task_runs(limit=limit, db_path=db_path)
        )
    return ToolResult(
        ok=True,
        data={"count": len(runs), "runs": runs, "run_kind": run_kind},
        metadata={"mode": "live"},
    )


def _serialize_signal_runs(records: Any) -> list[dict[str, Any]]:
    return [
        {
            "id": row.id,
            "source": row.source,
            "status": row.status,
            "started_at": row.started_at.isoformat(),
            "finished_at": (
                None if row.finished_at is None else row.finished_at.isoformat()
            ),
            "fetched_count": row.fetched_count,
            "inserted_count": row.inserted_count,
            "updated_count": row.updated_count,
            "error": row.error,
        }
        for row in records
    ]


def _serialize_task_runs(records: Any) -> list[dict[str, Any]]:
    return [
        {
            "id": row.id,
            "task_name": row.task_name,
            "status": row.status,
            "started_at": row.started_at.isoformat(),
            "finished_at": (
                None if row.finished_at is None else row.finished_at.isoformat()
            ),
            "fetched_count": row.fetched_count,
            "new_count": row.new_count,
            "skipped_count": row.skipped_count,
            "notified_count": row.notified_count,
            "error_message": row.error_message,
        }
        for row in records
    ]
