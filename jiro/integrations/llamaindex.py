"""LlamaIndex integration for Jiro."""

from __future__ import annotations

from typing import Any, Callable, List, Optional

from ..ai.tools import JiroTool, ToolSpec as _ToolSpec


def register_llamaindex(search_fn: Callable[..., Any],
                        scrape_fn: Callable[..., Any],
                        ai_fn: Optional[Callable[..., Any]] = None) -> _ToolSpec:
    return _ToolSpec(search_fn=search_fn, scrape_fn=scrape_fn, ai_fn=ai_fn)


__all__ = ["register_llamaindex", "ToolSpec"]
