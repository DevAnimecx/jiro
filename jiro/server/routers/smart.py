"""Smart router endpoint - natural language intent → auto-route.

The killer feature of v0.2: one endpoint that figures out what the user wants
and routes accordingly.
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from jiro.auth import AuthContext
from jiro.config import Settings
from jiro.log import get_logger
from jiro.scraping.client import ScrapingClient
from jiro.search.intent import IntentType, classify_intent, IntentResult
from jiro.server.deps import (
    get_auth_context,
    get_client,
    get_settings,
    get_orchestrator,
    record_usage,
    optional_auth_context,
)
from jiro.search import MultiQuerySearcher, MultiQueryRequest

log = get_logger("jiro.server.routers.smart")

router = APIRouter(tags=["smart"])


# ── Request/Response Models ──────────────────────────────────────────────────

class SmartRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=500, description="Natural language query")
    max_results: int = Field(10, ge=1, le=50)
    include_social: bool = Field(False, description="Also search social platforms")
    depth: str = Field("basic", description="instant | fast | basic | advanced | deep")


class SmartResponse(BaseModel):
    intent: str
    confidence: float
    platform: Optional[str] = None
    action: Optional[str] = None
    data: Dict[str, Any] = Field(default_factory=dict)
    latency_ms: float
    credits_charged: int = 0


# ── Helpers ──────────────────────────────────────────────────────────────────

async def _execute_scrape(intent: IntentResult, client: ScrapingClient, settings: Settings) -> Dict[str, Any]:
    """Execute a scrape action based on intent."""
    from jiro.scraping.social import registry, parse_social_url
    from jiro.scraping.social.base import SocialScrapeError, RateLimitError, AuthRequiredError, NotFoundError
    
    platform = intent.platform or "auto"
    action = intent.action or "post"
    extracted = intent.extracted or {}
    
    url = extracted.get("url")
    username = extracted.get("username")
    
    if not url and not username:
        return {"error": "No URL or username provided"}
    
    try:
        # Get scraper
        if platform == "auto" and url:
            parsed = parse_social_url(url)
            platform = parsed.get("platform", "auto")
        
        if platform == "auto":
            return {"error": "Could not determine platform"}
        
        try:
            scraper = registry.get_instance(platform, client, settings)
        except ValueError:
            return {"error": f"Unsupported platform: {platform}"}
        
        # Execute based on action
        if action in ("post", "video", "pin", "message", "reel", "story") and url:
            result = await scraper.scrape_post(url)
            return result.to_dict()
        elif action in ("profile", "channel", "user") and username:
            result = await scraper.scrape_profile(username)
            return {
                "platform": result.platform,
                "type": result.type,
                "url": result.url,
                "data": result.data,
                "scraped_at": result.scraped_at,
                "credits_charged": result.credits_charged,
            }
        elif action == "timeline" and username:
            results = await scraper.scrape_timeline(username)
            return {"platform": platform, "type": "timeline", "data": [r.to_dict() for r in results]}
        else:
            return {"error": f"Unsupported action: {action}"}
            
    except RateLimitError as e:
        return {"error": f"Rate limited by {e.platform}", "code": "rate_limited"}
    except AuthRequiredError as e:
        return {"error": f"Authentication required for {e.platform}", "code": "auth_required"}
    except NotFoundError as e:
        return {"error": f"Content not found on {e.platform}", "code": "not_found"}
    except SocialScrapeError as e:
        # SECURITY: Log full error server-side, return generic message to client
        log.exception("Smart scrape failed", extra={"platform": platform, "action": action, "error": str(e)})
        return {"error": "Scraping failed", "code": "scrape_failed"}
    except Exception as e:
        # SECURITY: Log full exception server-side only, return generic message to client
        log.exception("Smart scrape failed", extra={"platform": platform, "action": action})
        return {"error": "Internal server error"}


async def _execute_search(
    intent: IntentResult,
    query: str,
    max_results: int,
    depth: str,
    include_social: bool,
    orchestrator: Any,
    client: ScrapingClient,
    settings: Settings
) -> Dict[str, Any]:
    """Execute a search based on intent."""
    from jiro.models import SearchRequest
    from jiro.search import MultiQuerySearcher, MultiQueryRequest
    
    platform = intent.platform
    
    # For research/answer intent, use AI search
    if intent.intent == IntentType.RESEARCH_ANSWER:
        from jiro.ai.agent import Agent
        from jiro.ai.llm import LLM
        
        llm = LLM(settings)
        agent = Agent(settings, orchestrator, 
                     lambda url: __import__('jiro.extract').extract.scrape_url(url, client, fmt="markdown", include_metadata=False),
                     llm)
        
        result = await agent.research(query, max_sources=min(max_results, 10))
        return {
            "type": "research_answer",
            "answer": result.get("answer", ""),
            "citations": result.get("citations", []),
            "sources_used": result.get("sources_used", []),
        }
    
    # For news search, use news engine
    if intent.intent == IntentType.NEWS_SEARCH:
        search_req = SearchRequest(
            q=query,
            engine=platform or "google",
            type="news",
            num=max_results,
            depth=depth,
        )
        result = await orchestrator.search(search_req)
        return {
            "type": "news_search",
            "query": query,
            "results": result.model_dump(),
        }
    
    # For trending, search social platforms
    if intent.intent == IntentType.TRENDING:
        if include_social:
            from jiro.scraping.social import registry
            results = {}
            social_platforms = ["twitter", "reddit", "youtube", "tiktok", "bluesky"]
            for p in social_platforms:
                try:
                    scraper = registry.get_instance(p, client, settings)
                    if hasattr(scraper, 'search'):
                        trending = await scraper.search("trending", limit=5)
                        results[p] = [r.to_dict() for r in trending]
                except Exception:
                    pass
            return {"type": "trending", "results": results}
        return {"type": "trending", "results": {}}
    
    # Default: web search
    search_req = SearchRequest(
        q=query,
        engine=platform or "auto",
        type="web",
        num=max_results,
        depth=depth,
    )
    result = await orchestrator.search(search_req)
    
    response = {
        "type": "search",
        "query": query,
        "results": result.model_dump(),
    }
    
    # Also search social if requested
    if include_social:
        from jiro.scraping.social import registry
        social_results = {}
        for p in ["twitter", "reddit", "youtube", "bluesky"]:
            try:
                scraper = registry.get_instance(p, client, settings)
                if hasattr(scraper, 'search'):
                    social = await scraper.search(query, limit=3)
                    social_results[p] = [r.to_dict() for r in social]
            except Exception:
                pass
        response["social_results"] = social_results
    
    return response


async def _execute_monitor_setup(intent: IntentResult, query: str) -> Dict[str, Any]:
    """Handle monitor/alert setup intent."""
    # This would integrate with the monitors system (Pro feature)
    return {
        "type": "monitor_setup",
        "message": "Monitor setup requires Pro subscription",
        "query": query,
        "suggestion": "Use /monitors endpoint with Pro API key",
    }


# ── Endpoint ──────────────────────────────────────────────────────────────────

@router.post("/v1/smart", response_model=SmartResponse, summary="Smart router - natural language → auto-action")
async def smart(
    request: Request,
    body: SmartRequest,
    ctx: AuthContext = Depends(optional_auth_context),
    client: ScrapingClient = Depends(get_client),
    settings: Settings = Depends(get_settings),
    orchestrator: Any = Depends(get_orchestrator),
) -> Dict[str, Any]:
    """
    Smart router: figures out what the user wants and executes it.
    
    Examples:
    - "latest AI news today" → news search
    - "@elonmusk latest tweets" → Twitter profile timeline
    - "What is the best Python web framework?" → AI research with answer
    - "https://example.com" → scrape URL
    - "reddit r/Python top posts" → Reddit subreddit
    - "monitor competitor pricing" → monitor setup (Pro)
    - "trending on twitter" → cross-platform trending
    """
    request.state.auth = ctx
    started = time.perf_counter()
    
    # Classify intent
    intent = classify_intent(body.query, settings)
    
    log.info("Smart router intent", extra={
        "query": body.query[:100],
        "intent": intent.intent.value,
        "confidence": intent.confidence,
        "platform": intent.platform,
    })
    
    # Execute based on intent
    data = {}
    credits = 0
    
    try:
        if intent.intent == IntentType.SCRAPE:
            data = await _execute_scrape(intent, client, settings)
            credits = 2
        elif intent.intent in (
            IntentType.SOCIAL_PROFILE, IntentType.SOCIAL_TWITTER, IntentType.SOCIAL_REDDIT,
            IntentType.SOCIAL_INSTAGRAM, IntentType.SOCIAL_TIKTOK, IntentType.SOCIAL_YOUTUBE,
            IntentType.SOCIAL_TELEGRAM, IntentType.SOCIAL_THREADS, IntentType.SOCIAL_BLUESKY,
            IntentType.SOCIAL_LINKEDIN, IntentType.SOCIAL_FACEBOOK, IntentType.SOCIAL_PINTEREST,
            IntentType.SOCIAL_HACKERNEWS
        ):
            data = await _execute_scrape(intent, client, settings)
            credits = 2
        elif intent.intent in (IntentType.RESEARCH_ANSWER, IntentType.NEWS_SEARCH, IntentType.TRENDING, IntentType.SEARCH):
            data = await _execute_search(
                intent, body.query, body.max_results, body.depth, body.include_social,
                orchestrator, client, settings
            )
            credits = 1 if intent.intent == IntentType.SEARCH else 3
        elif intent.intent == IntentType.MONITOR_SETUP:
            data = await _execute_monitor_setup(intent, body.query)
            credits = 0
        else:
            # Default to search
            data = await _execute_search(
                IntentResult(intent=IntentType.SEARCH, confidence=0.5),
                body.query, body.max_results, body.depth, body.include_social,
                orchestrator, client, settings
            )
            credits = 1
    except Exception as e:
        log.exception("Smart router execution failed", extra={"intent": intent.intent.value})
        data = {"error": f"Execution failed: {str(e)}"}
    
    latency_ms = (time.perf_counter() - started) * 1000
    await record_usage(request, endpoint="/v1/smart", status=200, latency_ms=latency_ms)
    
    return SmartResponse(
        intent=intent.intent.value,
        confidence=intent.confidence,
        platform=intent.platform,
        action=intent.action,
        data=data,
        latency_ms=round(latency_ms, 1),
        credits_charged=credits,
    )


# ── Test endpoint for debugging ──────────────────────────────────────────────

@router.post("/v1/smart/classify", summary="Classify intent only (debug)")
async def smart_classify(
    request: Request,
    body: SmartRequest,
    ctx: AuthContext = Depends(optional_auth_context),
    settings: Settings = Depends(get_settings),
) -> Dict[str, Any]:
    """Classify intent without executing (for debugging)."""
    request.state.auth = ctx
    intent = classify_intent(body.query, settings)
    
    return {
        "query": body.query,
        "intent": intent.intent.value,
        "confidence": intent.confidence,
        "platform": intent.platform,
        "action": intent.action,
        "extracted": intent.extracted,
    }