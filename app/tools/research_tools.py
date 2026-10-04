"""Allowlisted research using the configured project-owned search gateway."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from app.core.secrets import EnvSettings, resolve_provider_credentials
from app.search import SearchRequest, create_search_provider

from .models import ToolContext, ToolError, ToolResult
from .permissions import PermissionClass


class ResearchWebTool:
    name = "research_web"
    permission = PermissionClass.READ
    description = (
        "Research a query using the configured search provider (may incur API charges)."
    )
    input_schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {"query": {"type": "string", "minLength": 1, "maxLength": 8000}},
        "required": ["query"],
    }

    def invoke(self, arguments: dict[str, Any], *, context: ToolContext) -> ToolResult:
        if context.config is None:
            raise ToolError("Research requires application configuration")
        config = context.config
        env = resolve_provider_credentials(
            context.env or EnvSettings(), config.providers
        )
        provider = create_search_provider(
            provider=config.search.provider,
            api_key=env.perplexity_api_key,
            model=config.search.model,
            timeout_seconds=config.search.timeout_seconds,
        )
        response = provider.search(
            SearchRequest(
                query=arguments["query"],
                max_results=min(config.search.max_results, context.max_items),
                timeout_seconds=min(
                    config.search.timeout_seconds, context.timeout_seconds
                ),
            )
        )
        return ToolResult(
            ok=True,
            data={
                "answer": response.answer,
                "results": [asdict(item) for item in response.results],
            },
            metadata={"provider": response.provider},
        )
