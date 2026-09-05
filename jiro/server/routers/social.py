"""Social media scraping endpoints."""

from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from jiro.auth import AuthContext
from jiro.config import Settings
from jiro.log import get_logger
from jiro.scraping.social import (
    registry,
    router as social_router,
    parse_social_url,
    BaseSocialScraper,
    SocialPost,
    SocialProfile,
    SocialScrapeError,
    RateLimitError,
    AuthRequiredError,
    NotFoundError,
)
from jiro.scraping.client import ScrapingClient
from jiro.security import async_validate_target_url
from jiro.server.deps import (
    get_client,
    get_settings,
    record_usage,
    optional_auth_context,
)

log = get_logger("jiro.server.routers.social")

router = APIRouter(tags=["social"])


# ── Request/Response Models ──────────────────────────────────────────────────

class SocialScrapeRequest(BaseModel):
    url: str = Field(..., description="Social media URL to scrape")
    platform: Optional[str] = Field(None, description="Force specific platform (auto-detect if omitted)")
    action: Optional[str] = Field(None, description="Action type: post, profile, timeline, etc.")


class SocialScrapeResponse(BaseModel):
    platform: str
    type: str
    url: str
    data: Dict[str, Any]
    scraped_at: str
    credits_charged: int


class SocialBatchRequest(BaseModel):
    urls: List[str] = Field(..., min_length=1, max_length=10, description="List of social URLs to scrape")
    parallel: bool = True
    max_concurrent: int = 5


class SocialSearchRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=500)
    platform: str = Field(..., description="Platform to search: twitter, reddit, youtube, etc.")
    limit: int = Field(25, ge=1, le=100)
    extra_params: Dict[str, Any] = Field(default_factory=dict)


class SocialSearchEverywhereRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=500)
    limit: int = Field(10, ge=1, le=50)
    platforms: Optional[List[str]] = Field(None, description="Specific platforms (default: all supported)")


class SocialPlatformInfo(BaseModel):
    platform: str
    url_patterns: List[str]
    supported_actions: List[str]
    requires_auth: bool
    rate_limit_rpm: int


# ── Helpers ──────────────────────────────────────────────────────────────────

async def get_scraper(platform: str, client: ScrapingClient, settings: Settings) -> BaseSocialScraper:
    """Get scraper instance for platform."""
    try:
        return registry.get_instance(platform, client, settings)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


def normalize_post(post: SocialPost) -> Dict[str, Any]:
    """Convert SocialPost to dict."""
    return post.to_dict()


def normalize_profile(profile: SocialProfile) -> Dict[str, Any]:
    """Convert SocialProfile to dict."""
    return {
        "platform": profile.platform,
        "type": profile.type,
        "url": profile.url,
        "data": profile.data,
        "scraped_at": profile.scraped_at,
        "credits_charged": profile.credits_charged,
    }


# ── Endpoints ────────────────────────────────────────────────────────────────

@router.post("/social", response_model=SocialScrapeResponse, summary="Scrape any social media URL")
async def scrape_social(
    request: Request,
    body: SocialScrapeRequest,
    ctx: AuthContext = Depends(optional_auth_context),
    client: ScrapingClient = Depends(get_client),
    settings: Settings = Depends(get_settings),
) -> Dict[str, Any]:
    """
    Scrape any social media URL with automatic platform detection.
    
    Supports: Twitter/X, Threads, Instagram, TikTok, YouTube, Reddit, 
    LinkedIn, Facebook, Telegram, Pinterest, Hacker News, Bluesky
    """
    request.state.auth = ctx
    started = time.perf_counter()
    
    # Auto-detect platform if not specified
    platform = body.platform
    if not platform:
        parsed = parse_social_url(body.url)
        platform = parsed.get("platform")
        if not platform:
            raise HTTPException(status_code=400, detail="Could not detect platform from URL")

    # SECURITY: Validate user-provided URL against SSRF before scraping
    try:
        await async_validate_target_url(body.url)
    except Exception as e:
        raise HTTPException(status_code=400, detail="Invalid URL")

    # Get scraper
    try:
        scraper = await get_scraper(platform, client, settings)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Unsupported platform: {platform}")
    
    # Determine action
    action = body.action
    if not action:
        parsed = parse_social_url(body.url)
        action = parsed.get("action", "post")
    
    # Execute scrape
    try:
        if action in ("post", "video", "pin", "message", "reel", "story"):
            result = await scraper.scrape_post(body.url)
            response = normalize_post(result)
        elif action in ("profile", "channel", "user"):
            identifier = social_router.extract_identifier(platform, body.url) or body.url.split("/")[-1]
            result = await scraper.scrape_profile(identifier)
            response = normalize_profile(result)
        elif action == "timeline":
            identifier = social_router.extract_identifier(platform, body.url) or body.url.split("/")[-1]
            result = await scraper.scrape_timeline(identifier)
            response = {"platform": platform, "type": "timeline", "url": body.url, "data": [normalize_post(p) for p in result]}
        else:
            raise HTTPException(status_code=400, detail=f"Unsupported action: {action}")
    except RateLimitError as e:
        raise HTTPException(status_code=429, detail=f"Rate limited by {e.platform}")
    except AuthRequiredError as e:
        raise HTTPException(status_code=401, detail=f"Authentication required for {e.platform}")
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=f"Content not found on {e.platform}")
    except SocialScrapeError as e:
        # SECURITY: Log full error server-side, return generic message to client
        log.exception("Social scrape failed", extra={"platform": platform, "url": body.url, "error": str(e)})
        raise HTTPException(status_code=502, detail="Scraping failed")
    except Exception as e:
        # SECURITY: Log full exception server-side only, return generic message to client
        log.exception("Social scrape failed", extra={"platform": platform, "url": body.url})
        raise HTTPException(status_code=500, detail="Internal server error")
    
    latency_ms = (time.perf_counter() - started) * 1000
    await record_usage(request, endpoint="/social", status=200, latency_ms=latency_ms)
    
    response["latency_ms"] = round(latency_ms, 1)
    return response


@router.post("/social/batch", summary="Batch scrape multiple social URLs")
async def scrape_social_batch(
    request: Request,
    body: SocialBatchRequest,
    ctx: AuthContext = Depends(optional_auth_context),
    client: ScrapingClient = Depends(get_client),
    settings: Settings = Depends(get_settings),
) -> Dict[str, Any]:
    """
    Scrape multiple social media URLs in parallel.
    
    Limited to 10 URLs per batch.
    """
    request.state.auth = ctx
    started = time.perf_counter()
    
    results = []
    
    async def scrape_one(url: str) -> Dict[str, Any]:
        try:
            try:
                await async_validate_target_url(url)
            except Exception:
                return {"url": url, "error": "Invalid URL", "status": "failed"}
            parsed = parse_social_url(url)
            platform = parsed.get("platform")
            action = parsed.get("action", "post")
            
            if not platform:
                return {"url": url, "error": "Could not detect platform", "status": "failed"}
            
            scraper = await get_scraper(platform, client, settings)
            
            if action in ("post", "video", "pin", "message", "reel", "story"):
                result = await scraper.scrape_post(url)
                return {"url": url, "data": normalize_post(result), "platform": platform, "status": "success"}
            elif action in ("profile", "channel", "user"):
                identifier = social_router.extract_identifier(platform, url) or url.split("/")[-1]
                result = await scraper.scrape_profile(identifier)
                return {"url": url, "data": normalize_profile(result), "platform": platform, "status": "success"}
            else:
                return {"url": url, "error": f"Unsupported action: {action}", "status": "failed"}
        except RateLimitError as e:
            return {"url": url, "error": "Rate limited", "platform": e.platform, "status": "failed"}
        except AuthRequiredError as e:
            return {"url": url, "error": "Auth required", "platform": e.platform, "status": "failed"}
        except NotFoundError as e:
            return {"url": url, "error": "Not found", "platform": e.platform, "status": "failed"}
        except Exception as e:
            log.warning("batch scrape failed", extra={"url": url, "error": str(e)})
            return {"url": url, "error": "Scraping failed", "status": "failed"}
    
    if body.parallel:
        semaphore = asyncio.Semaphore(body.max_concurrent)
        
        async def limited_scrape(url: str) -> Dict[str, Any]:
            async with semaphore:
                return await scrape_one(url)
        
        tasks = [limited_scrape(url) for url in body.urls]
        results = await asyncio.gather(*tasks)
    else:
        for url in body.urls:
            results.append(await scrape_one(url))
    
    latency_ms = (time.perf_counter() - started) * 1000
    await record_usage(request, endpoint="/social/batch", status=200, latency_ms=latency_ms)
    
    return {
        "count": len(results),
        "results": results,
        "latency_ms": round(latency_ms, 1),
    }


@router.get("/social/platforms", summary="List supported social media platforms")
async def list_social_platforms(
    ctx: AuthContext = Depends(optional_auth_context),
) -> List[SocialPlatformInfo]:
    """List all supported social media platforms and their capabilities."""
    platforms = registry.list_platforms()
    return [SocialPlatformInfo(**p) for p in platforms]


@router.post("/social/search", summary="Search on a specific social platform")
async def social_search(
    request: Request,
    body: SocialSearchRequest,
    ctx: AuthContext = Depends(optional_auth_context),
    client: ScrapingClient = Depends(get_client),
    settings: Settings = Depends(get_settings),
) -> Dict[str, Any]:
    """Search for posts on a specific social platform."""
    request.state.auth = ctx
    started = time.perf_counter()
    
    try:
        scraper = await get_scraper(body.platform, client, settings)
        
        if not hasattr(scraper, 'search') or not callable(getattr(scraper, 'search')):
            raise HTTPException(status_code=400, detail=f"Platform {body.platform} does not support search")
        
        results = await scraper.search(body.query, limit=body.limit, **body.extra_params)
        
        response = {
            "platform": body.platform,
            "query": body.query,
            "results": [normalize_post(p) for p in results],
            "total": len(results),
        }
    except RateLimitError as e:
        raise HTTPException(status_code=429, detail=f"Rate limited by {e.platform}")
    except AuthRequiredError as e:
        raise HTTPException(status_code=401, detail=f"Authentication required for {e.platform}")
    except NotImplementedError as e:
        raise HTTPException(status_code=501, detail=str(e))
    except Exception as e:
        log.exception("Social search failed", extra={"platform": body.platform, "query": body.query})
        raise HTTPException(status_code=500, detail=f"Search failed: {str(e)}")
    
    latency_ms = (time.perf_counter() - started) * 1000
    await record_usage(request, endpoint="/social/search", status=200, latency_ms=latency_ms)
    
    response["latency_ms"] = round(latency_ms, 1)
    return response


@router.post("/social/search/everywhere", summary="Cross-platform social search")
async def social_search_everywhere(
    request: Request,
    body: SocialSearchEverywhereRequest,
    ctx: AuthContext = Depends(optional_auth_context),
    client: ScrapingClient = Depends(get_client),
    settings: Settings = Depends(get_settings),
) -> Dict[str, Any]:
    """
    Search across all supported social platforms simultaneously.
    
    Returns merged, deduplicated results ranked by engagement.
    """
    request.state.auth = ctx
    started = time.perf_counter()
    
    platforms = body.platforms or [p["platform"] for p in registry.list_platforms() if p["platform"] != "hackernews"]
    
    async def search_platform(platform: str) -> List[Dict[str, Any]]:
        try:
            scraper = await get_scraper(platform, client, settings)
            if not hasattr(scraper, 'search') or not callable(getattr(scraper, 'search')):
                return []
            results = await scraper.search(body.query, limit=body.limit)
            return [normalize_post(p) for p in results]
        except Exception as e:
            log.warning("Cross-platform search failed for platform", extra={"platform": platform, "error": str(e)})
            return []
    
    # Search all platforms in parallel
    tasks = [search_platform(p) for p in platforms]
    all_results = await asyncio.gather(*tasks)
    
    # Merge and deduplicate by URL
    seen_urls = set()
    merged = []
    for platform_results in all_results:
        for result in platform_results:
            url = result.get("url", "")
            if url and url not in seen_urls:
                seen_urls.add(url)
                merged.append(result)
    
    # Sort by engagement (total interactions)
    def engagement_score(r: Dict[str, Any]) -> int:
        eng = r.get("data", {}).get("engagement", {})
        return sum(v for v in eng.values() if isinstance(v, (int, float)))
    
    merged.sort(key=engagement_score, reverse=True)
    merged = merged[:body.limit]
    
    latency_ms = (time.perf_counter() - started) * 1000
    await record_usage(request, endpoint="/social/search/everywhere", status=200, latency_ms=latency_ms)
    
    return {
        "query": body.query,
        "platforms_searched": platforms,
        "results": merged,
        "total": len(merged),
        "latency_ms": round(latency_ms, 1),
    }