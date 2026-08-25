"""Plugin marketplace API endpoints."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from jiro.auth import AuthContext
from jiro.scraping.engines import registry
from jiro.server.deps import get_auth_context, require_scope

router = APIRouter(tags=["plugins"])


@router.get("/plugins", summary="List all registered engine plugins")
async def list_plugins(
    ctx: AuthContext = Depends(get_auth_context),
) -> Dict[str, Any]:
    """List all registered engine plugins with metadata."""
    engines = registry.names()
    metadata = registry.get_all_metadata()

    result = []
    for name in engines:
        meta = metadata.get(name, {})
        result.append({
            "name": name,
            "version": meta.get("version", "1.0"),
            "author": meta.get("author", ""),
            "description": meta.get("description", ""),
            "types": meta.get("types", ["web"]),
            "homepage": meta.get("homepage", ""),
            "license": meta.get("license", "MIT"),
            "min_jiro_version": meta.get("min_jiro_version", "0.1.0"),
            "config_schema": meta.get("config_schema", {}),
        })

    return {"plugins": result, "total": len(result)}


@router.get("/plugins/{name}", summary="Get plugin details")
async def get_plugin(
    name: str,
    ctx: AuthContext = Depends(get_auth_context),
) -> Dict[str, Any]:
    """Get detailed information about a specific plugin."""
    meta = registry.get_metadata(name)
    if not meta:
        raise HTTPException(status_code=404, detail=f"Plugin '{name}' not found")

    return meta


@router.post("/plugins/discover", summary="Discover and load plugins from directories")
async def discover_plugins(
    plugin_dirs: Optional[List[str]] = Query(None, description="Plugin directories to search"),
    ctx: AuthContext = Depends(require_scope("admin")),
) -> Dict[str, Any]:
    """Discover and load plugins from directories (admin only)."""
    loaded = registry.discover_plugins(plugin_dirs)
    return {"loaded": loaded, "count": len(loaded)}


@router.get("/plugins/{name}/validate", summary="Validate plugin configuration")
async def validate_plugin_config(
    name: str,
    config: Dict[str, Any] = {},
    ctx: AuthContext = Depends(get_auth_context),
) -> Dict[str, Any]:
    """Validate a plugin's configuration."""
    try:
        engine_cls = registry.get(name)
        # Create instance without client/settings for validation
        engine = engine_cls(None, None)  # type: ignore
        errors = engine.validate_config(config)

        return {
            "valid": len(errors) == 0,
            "engine": name,
            "errors": errors,
        }
    except Exception as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.get("/plugins/marketplace/featured", summary="Get featured plugins")
async def get_featured_plugins(
    ctx: AuthContext = Depends(get_auth_context),
) -> Dict[str, Any]:
    """Get featured/curated plugins from the marketplace."""
    # In a real implementation, this would fetch from a remote registry
    # For now, return built-in engines as "featured"
    featured = ["google", "bing", "duckduckgo", "brave", "youtube"]
    result = {}

    for name in featured:
        meta = registry.get_metadata(name)
        if meta:
            result[name] = {
                **meta,
                "featured": True,
                "category": "general" if name in ["google", "bing", "duckduckgo", "brave"] else "specialized",
            }

    return {"featured": result}


@router.get("/plugins/categories", summary="Get plugin categories")
async def get_plugin_categories(
    ctx: AuthContext = Depends(get_auth_context),
) -> Dict[str, Any]:
    """Get available plugin categories."""
    return {
        "categories": [
            {"id": "general", "name": "General Web Search", "description": "General purpose search engines"},
            {"id": "specialized", "name": "Specialized Search", "description": "Vertical-specific engines (video, shopping, etc.)"},
            {"id": "regional", "name": "Regional Search", "description": "Region-specific engines (Yandex, Baidu, etc.)"},
            {"id": "custom", "name": "Custom Plugins", "description": "Community-contributed engines"},
        ]
    }


@router.get("/plugins/stats", summary="Get plugin registry statistics")
async def get_plugin_stats(
    ctx: AuthContext = Depends(get_auth_context),
) -> Dict[str, Any]:
    """Get statistics about the plugin registry."""
    engines = registry.names()
    metadata = registry.get_all_metadata()

    by_type: Dict[str, int] = {}
    by_license: Dict[str, int] = {}
    community_count = 0

    for name in engines:
        meta = metadata.get(name, {})
        for t in meta.get("types", ["web"]):
            by_type[t] = by_type.get(t, 0) + 1
        license = meta.get("license", "MIT")
        by_license[license] = by_license.get(license, 0) + 1
        # Consider non-core engines as community
        if name not in ["google", "bing", "duckduckgo", "brave", "youtube", "amazon", "ebay", "yandex", "baidu"]:
            community_count += 1

    return {
        "total_engines": len(engines),
        "core_engines": len(engines) - community_count,
        "community_engines": community_count,
        "by_type": by_type,
        "by_license": by_license,
    }