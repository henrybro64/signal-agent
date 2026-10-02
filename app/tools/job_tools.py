"""Job-search tool wrapper over existing sources."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from app.sources import (
    SOURCE_FACTORIES,
    build_job_search_sources,
    build_job_search_sources_for_profile,
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

    @staticmethod
    def invoke(
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
            collected_items, source_count = _fetch_named_sources(
                config, source_filter, failed=failed
            )
        else:
            collected_items, source_count = _fetch_configured_sources(
                config, context=context, failed=failed
            )

        if source_count > 0 and not collected_items and _all_sources_failed(
            failed, source_count=source_count
        ):
            return ToolResult(
                ok=False,
                error="all_job_sources_failed:" + ",".join(failed),
                data={"count": 0, "items": []},
                metadata={
                    "mode": "live",
                    "failed_sources": failed,
                    "source_count": source_count,
                },
            )

        payload = [signal_item_to_dict(item) for item in collected_items[:limit]]
        return ToolResult(
            ok=True,
            data={"count": len(payload), "items": payload},
            metadata={
                "mode": "live",
                "failed_sources": failed,
                "source_count": source_count,
            },
        )


def _fetch_named_sources(
    config: Any,
    source_filter: list[Any],
    *,
    failed: list[str],
) -> tuple[list[Any], int]:
    names = [str(name) for name in source_filter]
    for name in names:
        if name not in SOURCE_FACTORIES:
            failed.append(f"unsupported_job_source:{name}")
    job_names = [name for name in names if name in SOURCE_FACTORIES]

    def on_unknown(name: str) -> None:
        failed.append(f"unknown:{name}")

    def on_error(source: Any, exc: BaseException) -> None:
        failed.append(f"{source.name}:{type(exc).__name__}")

    sources = build_sources(config, job_names, on_unknown=on_unknown)
    batch = fetch_from_sources(sources, on_error=on_error)
    return batch, len(sources)


def _fetch_configured_sources(
    config: Any,
    *,
    context: ToolContext,
    failed: list[str],
) -> tuple[list[Any], int]:
    profiles = list(config.tasks.job_search.profiles)
    if profiles:
        return _fetch_profile_sources(config, profiles, context=context, failed=failed)

    def on_unknown(name: str) -> None:
        failed.append(f"unknown:{name}")

    def on_error(source: Any, exc: BaseException) -> None:
        failed.append(f"{source.name}:{type(exc).__name__}")

    sources = build_job_search_sources(config, on_unknown=on_unknown)
    batch = fetch_from_sources(sources, on_error=on_error)
    return batch, len(sources)


def _fetch_profile_sources(
    config: Any,
    profiles: list[Any],
    *,
    context: ToolContext,
    failed: list[str],
) -> tuple[list[Any], int]:
    collected_items: list[Any] = []
    source_count = 0
    seen_keys: set[str] = set()
    for profile in profiles:
        profile_name = str(profile.name)

        def on_unknown(
            name: str,
            *,
            _profile_name: str = profile_name,
        ) -> None:
            failed.append(f"{_profile_name}/{name}:unknown")

        def on_error(
            source: Any,
            exc: BaseException,
            *,
            _profile_name: str = profile_name,
        ) -> None:
            failed.append(f"{_profile_name}/{source.name}:{type(exc).__name__}")

        sources = build_job_search_sources_for_profile(
            config,
            profile,
            on_unknown=on_unknown,
        )
        source_count += len(sources)
        batch = fetch_from_sources(sources, on_error=on_error)
        if context.cancel_event is not None and context.cancel_event.is_set():
            raise ToolError(
                "search_jobs cancelled after tool timeout",
                code="cancelled",
            )
        for item in batch:
            key = _dedupe_key(item)
            if key in seen_keys:
                continue
            seen_keys.add(key)
            metadata = dict(item.metadata)
            metadata.setdefault("search_profile", profile.name)
            if profile.scorer_profile:
                metadata.setdefault("scorer_profile", profile.scorer_profile)
            collected_items.append(replace(item, metadata=metadata))
    return collected_items, source_count


def _dedupe_key(item: Any) -> str:
    if getattr(item, "url", None):
        return f"url:{item.url}"
    if getattr(item, "external_id", None):
        return f"ext:{item.source}:{item.external_id}"
    return f"title:{item.source}:{item.title}"


def _all_sources_failed(failed: list[str], *, source_count: int) -> bool:
    """Return True when failures account for every attempted source fetch."""

    if source_count <= 0 or not failed:
        return False
    live_failures = [
        entry
        for entry in failed
        if ":" in entry
        and not entry.startswith("unsupported_job_source:")
        and not entry.startswith("unknown:")
    ]
    return len(live_failures) >= source_count
