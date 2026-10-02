"""Serialize SignalItem rows for tool responses."""

from __future__ import annotations

from typing import Any

from app.core.models import SignalItem

_MAX_CONTENT_CHARS = 500


def signal_item_to_dict(
    item: SignalItem,
    *,
    include_content: bool = True,
) -> dict[str, Any]:
    """Convert a SignalItem into a JSON-friendly dict (truncated content)."""

    content = item.content
    if (
        include_content
        and isinstance(content, str)
        and len(content) > _MAX_CONTENT_CHARS
    ):
        content = content[:_MAX_CONTENT_CHARS] + "…"
    return {
        "source": item.source,
        "item_type": item.item_type,
        "title": item.title,
        "observed_at": item.observed_at.isoformat(),
        "url": item.url,
        "external_id": item.external_id,
        "content": content if include_content else None,
        "metadata": dict(item.metadata),
    }
