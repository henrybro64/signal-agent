"""Taiwan stock mover tool wrapper."""

from __future__ import annotations

from typing import Any

from app.core.tw_stock_utils import select_top_gainers, select_top_losers
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

    def __init__(self) -> None:
        # Instance copy so DeepSource/PYL-R0201 treats invoke as using self.
        self._tool_name = type(self).name

    def invoke(  # skipcq: PYL-R0201
        self,
        arguments: dict[str, Any],
        *,
        context: ToolContext,
    ) -> ToolResult:
        return _get_tw_stock_movers(self._tool_name, arguments, context=context)


def _get_tw_stock_movers(
    tool_name: str,
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
            f"{tool_name} requires config in ToolContext",
            code="missing_config",
        )

    failed: list[str] = []

    def on_error(source: Any, exc: BaseException) -> None:
        failed.append(f"{source.name}:{type(exc).__name__}")

    try:
        sources = build_tw_stock_mover_sources(config)
    except ValueError as exc:
        raise ToolError(str(exc), code="invalid_argument") from exc
    collected = fetch_from_sources(sources, on_error=on_error)
    # Rank by change_percent before applying the tool limit so agents see
    # actual movers rather than arbitrary category/member order.
    gainer_limit = max(1, (limit + 1) // 2)
    loser_limit = max(1, limit // 2)
    ranked = [
        *select_top_gainers(collected, limit=gainer_limit),
        *select_top_losers(collected, limit=loser_limit),
    ]
    payload = [signal_item_to_dict(item) for item in ranked[:limit]]
    return ToolResult(
        ok=True,
        data={"count": len(payload), "items": payload},
        metadata={"mode": "live", "failed_sources": failed},
    )
