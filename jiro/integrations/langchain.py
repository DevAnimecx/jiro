"""LangChain integration for Jiro."""

from __future__ import annotations

from typing import Any, Callable, List, Optional

from ..ai.tools import JiroTool, langchain_tools as _langchain_tools


def register_langchain(search_fn: Callable[..., Any],
                       scrape_fn: Callable[..., Any],
                       ai_fn: Optional[Callable[..., Any]] = None) -> List[JiroTool]:
    return _langchain_tools(search_fn=search_fn, scrape_fn=scrape_fn, ai_fn=ai_fn)


__all__ = ["register_langchain", "JiroTool"]
