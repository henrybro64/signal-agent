"""Taiwan stock mover tool wrapper."""

from __future__ import annotations

from typing import Any

from app.sources import build_tw_stock_mover_sources, fetch_from_sources

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
            "description": "Maximum mover rows to return.",
        }
    },
}


class GetTwStockMoversTool:
    """Fetch TWSE/TPEx mover signals via existing collectors."""

    name = "get_tw_stock_movers"
    description = (
        "Fetch Taiwan stock market movers from TWSE and TPEx collectors. "
        "Read-only; does not place trades."
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
        if context.fetch_movers is not None:
            items = context.fetch_movers(limit=limit, arguments=arguments)
            return ToolResult(
                ok=True,
                data={"count": len(items), "items": items[:limit]},
                metadata={"mode": "injected"},
            )

        config = context.config
        if config is None:
            raise ToolError(
                "get_tw_stock_movers requires config in ToolContext",
                code="missing_config",
            )

        failed: list[str] = []
        try:
            sources = build_tw_stock_mover_sources(config)
        except ValueError as exc:
            raise ToolError(str(exc), code="invalid_argument") from exc
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
            metadata={"mode": "live", "failed_sources": failed},
        )
