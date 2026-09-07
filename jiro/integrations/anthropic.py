"""Anthropic tool-use integration for Jiro."""

from __future__ import annotations

from typing import Any, Callable, List, Optional

from ..ai.tools import anthropic_tools as _anthropic_tools


def register_anthropic(search_fn: Callable[..., Any],
                       scrape_fn: Callable[..., Any],
                       ai_fn: Optional[Callable[..., Any]] = None,
                       include_ai: bool = True) -> List[dict[str, Any]]:
    return _anthropic_tools(include_ai=include_ai)


__all__ = ["register_anthropic"]
