"""Job-search tool wrapper over existing sources."""

from __future__ import annotations

from typing import Any

from app.sources import (
    SOURCE_FACTORIES,
    build_job_search_sources,
    build_sources,
    fetch_from_sources,
)

from .args import optional_int
from .models import ToolContext, ToolError, ToolResult
from .serialization import signal_item_to_dict

_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "limit": {
            "type": "integer",
            "minimum": 1,
            "maximum": 50,
            "description": "Maximum jobs to return after fetch.",
        },
        "sources": {
            "type": "array",
            "items": {"type": "string"},
            "description": (
                "Optional job-source name filter "
                "(104 / LinkedIn email / Indeed / Glassdoor / dummy); "
                "default uses config job_search sources."
            ),
        },
    },
}


class SearchJobsTool:
    """Fetch job signals through configured collectors (no scraping login)."""

    name = "search_jobs"
    description = (
        "Fetch recent job postings from configured sources "
        "(104, LinkedIn email alerts, optional web collectors). "
        "Does not apply to jobs or message recruiters."
    )
    input_schema = _INPUT_SCHEMA

    def invoke(
        self,
        arguments: dict[str, Any],
        *,
        context: ToolContext,
    ) -> ToolResult:
        limit = optional_int(
            arguments, "limit", default=context.max_items, minimum=1, maximum=50
        )
        if context.fetch_jobs is not None:
            items = context.fetch_jobs(limit=limit, arguments=arguments)
            return ToolResult(
                ok=True,
                data={"count": len(items), "items": items[:limit]},
                metadata={"mode": "injected"},
            )

        config = context.config
        if config is None:
            raise ToolError(
                "search_jobs requires config in ToolContext",
                code="missing_config",
            )

        source_filter = arguments.get("sources")
        failed: list[str] = []
        if isinstance(source_filter, list) and source_filter:
            names = [str(name) for name in source_filter]
            unsupported = [
                name for name in names if name not in SOURCE_FACTORIES
            ]
            for name in unsupported:
                failed.append(f"unsupported_job_source:{name}")
            job_names = [name for name in names if name in SOURCE_FACTORIES]
            sources = build_sources(
                config,
                job_names,
                on_unknown=lambda name: failed.append(f"unknown:{name}"),
            )
        else:
            sources = build_job_search_sources(
                config,
                on_unknown=lambda name: failed.append(f"unknown:{name}"),
            )

        collected = fetch_from_sources(
            sources,
            on_error=lambda source, exc: failed.append(
                f"{source.name}:{type(exc).__name__}"
            ),
        )
        payload = [signal_item_to_dict(item) for item in collected[:limit]]
        return ToolResult(
            ok=True,
            data={"count": len(payload), "items": payload},
            metadata={
                "mode": "live",
                "failed_sources": failed,
                "source_count": len(sources),
            },
        )
