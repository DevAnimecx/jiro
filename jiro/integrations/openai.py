"""OpenAI function-calling integration for Jiro."""

from __future__ import annotations

from typing import Any, Callable, List, Optional

from ..ai.tools import openai_tools as _openai_tools


def register_openai(search_fn: Callable[..., Any],
                    scrape_fn: Callable[..., Any],
                    ai_fn: Optional[Callable[..., Any]] = None,
                    include_ai: bool = True) -> List[dict[str, Any]]:
    return _openai_tools(include_ai=include_ai)


__all__ = ["register_openai"]
