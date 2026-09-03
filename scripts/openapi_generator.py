"""OpenAPI 3.1 Specification Generator for Jiro v0.2.

Generates a complete OpenAPI 3.1 spec from the existing routers and models.
Also generates SDK generation scripts for Python and TypeScript.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional


def generate_openapi_spec() -> Dict[str, Any]:
    """Generate OpenAPI 3.1 specification for Jiro v0.2."""
    
    spec = {
        "openapi": "3.1.0",
        "info": {
            "title": "Jiro Search API",
            "version": "0.2.0",
            "description": "Local-first web search, scraping, and social media intelligence platform. "
                          "Supports 9+ search engines, 12 social platforms, hybrid search, structured extraction, "
                          "and smart intent routing.",
            "contact": {
                "name": "Jiro Team",
                "url": "https://github.com/DevAnimecx/jiro"
            },
            "license": {
                "name": "MIT",
                "url": "https://opensource.org/licenses/MIT"
            }
        },
        "servers": [
            {"url": "http://localhost:8000", "description": "Local development"},
            {"url": "https://api.jiro.dev", "description": "Production"}
        ],
        "tags": [
            {"name": "search", "description": "Web search across multiple engines"},
            {"name": "scrape", "description": "URL content extraction"},
            {"name": "social", "description": "Social media scraping and search"},
            {"name": "smart", "description": "Smart search with intent routing"},
            {"name": "structured", "description": "Structured data extraction"},
            {"name": "plugins", "description": "Plugin management"},
            {"name": "monitor", "description": "Server monitoring and health"}
        ],
        "paths": _generate_paths(),
        "components": _generate_components(),
        "security": [
            {"ApiKeyAuth": []},
            {"BearerAuth": []}
        ]
    }
    
    return spec


def _generate_paths() -> Dict[str, Any]:
    """Generate all API paths."""
    
    paths = {}
    
    # Search endpoints
    paths["/v1/search"] = {
        "post": {
            "tags": ["search"],
            "summary": "Search the web",
            "description": "Search across multiple engines with filtering, biasing, and hybrid ranking.",
            "operationId": "search",
            "requestBody": {
                "required": True,
                "content": {"application/json": {"schema": {"$ref": "#/components/schemas/SearchRequest"}}}
            },
            "responses": {
                "200": {"description": "Search results", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/SearchResponse"}}}},
                "400": {"description": "Invalid request", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Error"}}}},
                "429": {"description": "Rate limit exceeded"}
            }
        }
    }
    
    paths["/v1/search/multi"] = {
        "post": {
            "tags": ["search"],
            "summary": "Multi-query search",
            "description": "Execute multiple search queries in parallel.",
            "operationId": "searchMulti",
            "requestBody": {
                "required": True,
                "content": {"application/json": {"schema": {"$ref": "#/components/schemas/MultiQueryRequest"}}}
            },
            "responses": {
                "200": {"description": "Multi-query results", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/MultiQueryResponse"}}}}
            }
        }
    }
    
    # Scrape endpoints
    paths["/v1/scrape"] = {
        "post": {
            "tags": ["scrape"],
            "summary": "Scrape a URL",
            "description": "Extract content from a URL as markdown, text, or structured data.",
            "operationId": "scrape",
            "requestBody": {
                "required": True,
                "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ScrapeRequest"}}}
            },
            "responses": {
                "200": {"description": "Scraped content", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ScrapeResponse"}}}},
                "400": {"description": "Invalid URL"}
            }
        }
    }
    
    # Social endpoints
    paths["/v1/social"] = {
        "post": {
            "tags": ["social"],
            "summary": "Scrape social media URL",
            "description": "Scrape a social media post, profile, or thread from 12 supported platforms.",
            "operationId": "socialScrape",
            "requestBody": {
                "required": True,
                "content": {"application/json": {"schema": {"$ref": "#/components/schemas/SocialScrapeRequest"}}}
            },
            "responses": {
                "200": {"description": "Social content", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/SocialResponse"}}}},
                "400": {"description": "Unsupported platform"}
            }
        }
    }
    
    paths["/v1/social/batch"] = {
        "post": {
            "tags": ["social"],
            "summary": "Batch scrape social URLs",
            "description": "Scrape multiple social media URLs in parallel.",
            "operationId": "socialBatch",
            "requestBody": {
                "required": True,
                "content": {"application/json": {"schema": {"$ref": "#/components/schemas/SocialBatchRequest"}}}
            },
            "responses": {
                "200": {"description": "Batch results", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/SocialBatchResponse"}}}}
            }
        }
    }
    
    paths["/v1/social/platforms"] = {
        "get": {
            "tags": ["social"],
            "summary": "List supported social platforms",
            "operationId": "socialPlatforms",
            "responses": {
                "200": {"description": "Platform list", "content": {"application/json": {"schema": {"type": "array", "items": {"type": "string"}}}}}
            }
        }
    }
    
    paths["/v1/social/search"] = {
        "post": {
            "tags": ["social"],
            "summary": "Search social platforms",
            "description": "Search across multiple social platforms simultaneously.",
            "operationId": "socialSearch",
            "requestBody": {
                "required": True,
                "content": {"application/json": {"schema": {"$ref": "#/components/schemas/SocialSearchRequest"}}}
            },
            "responses": {
                "200": {"description": "Search results", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/SocialSearchResponse"}}}}
            }
        }
    }
    
    paths["/v1/social/search/everywhere"] = {
        "post": {
            "tags": ["social"],
            "summary": "Search all social platforms",
            "description": "Search across all 12 supported social platforms.",
            "operationId": "socialSearchEverywhere",
            "requestBody": {
                "required": True,
                "content": {"application/json": {"schema": {"$ref": "#/components/schemas/SocialSearchEverywhereRequest"}}}
            },
            "responses": {
                "200": {"description": "Search results", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/SocialSearchResponse"}}}}
            }
        }
    }
    
    # Smart endpoints
    paths["/v1/smart"] = {
        "post": {
            "tags": ["smart"],
            "summary": "Smart search with intent routing",
            "description": "Automatically detect search intent and route to the appropriate handler.",
            "operationId": "smartSearch",
            "requestBody": {
                "required": True,
                "content": {"application/json": {"schema": {"$ref": "#/components/schemas/SmartRequest"}}}
            },
            "responses": {
                "200": {"description": "Smart search result", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/SmartResponse"}}}}
            }
        }
    }
    
    paths["/v1/smart/classify"] = {
        "post": {
            "tags": ["smart"],
            "summary": "Classify search intent",
            "description": "Classify the intent of a search query without executing it.",
            "operationId": "smartClassify",
            "requestBody": {
                "required": True,
                "content": {"application/json": {"schema": {"$ref": "#/components/schemas/SmartClassifyRequest"}}}
            },
            "responses": {
                "200": {"description": "Intent classification", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/SmartClassifyResponse"}}}}
            }
        }
    }
    
    # Structured endpoints
    paths["/v1/structured/extract"] = {
        "post": {
            "tags": ["structured"],
            "summary": "Extract structured data",
            "description": "Extract structured data from search results using JSON schema.",
            "operationId": "structuredExtract",
            "requestBody": {
                "required": True,
                "content": {"application/json": {"schema": {"$ref": "#/components/schemas/StructuredExtractRequest"}}}
            },
            "responses": {
                "200": {"description": "Extracted data", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/StructuredExtractResponse"}}}}
            }
        }
    }
    
    # Plugin endpoints
    paths["/v1/plugins"] = {
        "get": {
            "tags": ["plugins"],
            "summary": "List plugins",
            "operationId": "listPlugins",
            "responses": {
                "200": {"description": "Plugin list", "content": {"application/json": {"schema": {"type": "array", "items": {"$ref": "#/components/schemas/PluginInfo"}}}}}
            }
        }
    }
    
    paths["/v1/plugins/{name}"] = {
        "get": {
            "tags": ["plugins"],
            "summary": "Get plugin info",
            "operationId": "getPlugin",
            "parameters": [{"name": "name", "in": "path", "required": True, "schema": {"type": "string"}}],
            "responses": {
                "200": {"description": "Plugin info", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/PluginInfo"}}}},
                "404": {"description": "Plugin not found"}
            }
        }
    }
    
    # Monitor endpoints
    paths["/v1/monitor/status"] = {
        "get": {
            "tags": ["monitor"],
            "summary": "Get server status",
            "operationId": "monitorStatus",
            "responses": {
                "200": {"description": "Server status", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ServerStatus"}}}}
            }
        }
    }
    
    paths["/v1/monitor/health"] = {
        "get": {
            "tags": ["monitor"],
            "summary": "Health check",
            "operationId": "monitorHealth",
            "responses": {
                "200": {"description": "Health status", "content": {"application/json": {"schema": {"type": "object", "properties": {"status": {"type": "string"}}}}}}
            }
        }
    }
    
    return paths


def _generate_components() -> Dict[str, Any]:
    """Generate component schemas."""
    
    schemas = {}
    
    # Search schemas
    schemas["SearchRequest"] = {
        "type": "object",
        "required": ["q"],
        "properties": {
            "q": {"type": "string", "description": "Search query"},
            "engine": {"type": "string", "enum": ["google", "bing", "brave", "duckduckgo", "youtube", "amazon", "ebay", "yandex", "baidu"], "default": "google"},
            "type": {"type": "string", "enum": ["web", "images", "news", "videos", "shopping", "places"], "default": "web"},
            "max_results": {"type": "integer", "minimum": 1, "maximum": 100, "default": 10},
            "language": {"type": "string", "default": "en"},
            "location": {"type": "string", "default": "us"},
            "time_range": {"type": "string", "enum": ["any", "day", "week", "month", "year"], "default": "any"},
            "safe_search": {"type": "boolean", "default": True},
            "depth": {"type": "integer", "minimum": 1, "maximum": 5, "description": "Crawl depth"},
            "include_domains": {"type": "array", "items": {"type": "string"}},
            "exclude_domains": {"type": "array", "items": {"type": "string"}},
            "bias_domains": {"type": "object", "additionalProperties": {"type": "number"}},
            "freshness": {"type": "string", "enum": ["any", "day", "week", "month", "year"]},
            "category": {"type": "string"},
            "hybrid": {"type": "boolean", "description": "Enable hybrid search"},
            "answer": {"type": "boolean", "description": "Generate synthesized answer"},
            "highlights": {"type": "boolean", "description": "Include highlights"},
        }
    }
    
    schemas["SearchResponse"] = {
        "type": "object",
        "properties": {
            "search_metadata": {"type": "object"},
            "organic_results": {"type": "array", "items": {"$ref": "#/components/schemas/OrganicResult"}},
            "answer": {"type": "string", "description": "Synthesized answer"},
            "highlights": {"type": "array", "items": {"type": "string"}},
            "multi_query": {"type": "object", "description": "Multi-query results"},
        }
    }
    
    schemas["OrganicResult"] = {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "link": {"type": "string", "format": "uri"},
            "snippet": {"type": "string"},
            "position": {"type": "integer"},
            "source": {"type": "string"},
            "relevance": {"type": "number", "description": "Relevance score 0-1"},
            "highlights": {"type": "array", "items": {"type": "string"}},
        }
    }
    
    schemas["MultiQueryRequest"] = {
        "type": "object",
        "required": ["q"],
        "properties": {
            "q": {"type": "string"},
            "num_variations": {"type": "integer", "minimum": 2, "maximum": 10, "default": 3},
            "engine": {"type": "string"},
            "max_results": {"type": "integer"},
        }
    }
    
    schemas["MultiQueryResponse"] = {
        "type": "object",
        "properties": {
            "queries": {"type": "array", "items": {"type": "string"}},
            "results": {"type": "array", "items": {"$ref": "#/components/schemas/SearchResponse"}},
            "merged": {"$ref": "#/components/schemas/SearchResponse"},
        }
    }
    
    # Scrape schemas
    schemas["ScrapeRequest"] = {
        "type": "object",
        "required": ["url"],
        "properties": {
            "url": {"type": "string", "format": "uri"},
            "format": {"type": "string", "enum": ["markdown", "text", "html", "json"], "default": "markdown"},
            "include_metadata": {"type": "boolean", "default": True},
        }
    }
    
    schemas["ScrapeResponse"] = {
        "type": "object",
        "properties": {
            "url": {"type": "string"},
            "title": {"type": "string"},
            "content": {"type": "string"},
            "metadata": {"type": "object"},
            "links": {"type": "array", "items": {"type": "string"}},
            "images": {"type": "array", "items": {"type": "string"}},
        }
    }
    
    # Social schemas
    schemas["SocialScrapeRequest"] = {
        "type": "object",
        "required": ["url"],
        "properties": {
            "url": {"type": "string", "format": "uri", "description": "Social media URL"},
        }
    }
    
    schemas["SocialResponse"] = {
        "type": "object",
        "properties": {
            "platform": {"type": "string"},
            "type": {"type": "string", "enum": ["post", "profile", "thread", "comment"]},
            "data": {"type": "object"},
            "scraped_at": {"type": "string", "format": "date-time"},
        }
    }
    
    schemas["SocialBatchRequest"] = {
        "type": "object",
        "required": ["urls"],
        "properties": {
            "urls": {"type": "array", "items": {"type": "string", "format": "uri"}, "maxItems": 50},
        }
    }
    
    schemas["SocialBatchResponse"] = {
        "type": "object",
        "properties": {
            "results": {"type": "array", "items": {"$ref": "#/components/schemas/SocialResponse"}},
            "total": {"type": "integer"},
            "failed": {"type": "integer"},
        }
    }
    
    schemas["SocialSearchRequest"] = {
        "type": "object",
        "required": ["query"],
        "properties": {
            "query": {"type": "string"},
            "platforms": {"type": "array", "items": {"type": "string"}},
            "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 25},
        }
    }
    
    schemas["SocialSearchEverywhereRequest"] = {
        "type": "object",
        "required": ["query"],
        "properties": {
            "query": {"type": "string"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 25},
        }
    }
    
    schemas["SocialSearchResponse"] = {
        "type": "object",
        "properties": {
            "results": {"type": "array", "items": {"$ref": "#/components/schemas/SocialResponse"}},
            "total": {"type": "integer"},
        }
    }
    
    # Smart schemas
    schemas["SmartRequest"] = {
        "type": "object",
        "required": ["query"],
        "properties": {
            "query": {"type": "string"},
            "type": {"type": "string", "enum": ["web", "images", "news", "videos", "shopping"], "default": "web"},
            "max_results": {"type": "integer", "default": 10},
            "schema": {"type": "object", "description": "JSON Schema for structured extraction"},
        }
    }
    
    schemas["SmartResponse"] = {
        "type": "object",
        "properties": {
            "intent": {"type": "string"},
            "target": {"type": "string"},
            "results": {"type": "object"},
        }
    }
    
    schemas["SmartClassifyRequest"] = {
        "type": "object",
        "required": ["query"],
        "properties": {
            "query": {"type": "string"},
        }
    }
    
    schemas["SmartClassifyResponse"] = {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "intent": {"type": "string"},
            "confidence": {"type": "number"},
            "target": {"type": "string"},
            "social_platform": {"type": "string"},
            "category": {"type": "string"},
            "reasoning": {"type": "string"},
        }
    }
    
    # Structured schemas
    schemas["StructuredExtractRequest"] = {
        "type": "object",
        "required": ["query", "schema"],
        "properties": {
            "query": {"type": "string"},
            "schema": {"type": "object", "description": "JSON Schema for desired output"},
            "engine": {"type": "string"},
            "max_results": {"type": "integer"},
        }
    }
    
    schemas["StructuredExtractResponse"] = {
        "type": "object",
        "properties": {
            "data": {"type": "array"},
            "schema": {"type": "object"},
            "extraction_method": {"type": "string"},
        }
    }
    
    # Plugin schemas
    schemas["PluginInfo"] = {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "type": {"type": "string"},
            "version": {"type": "string"},
            "author": {"type": "string"},
            "description": {"type": "string"},
            "homepage": {"type": "string"},
        }
    }
    
    # Monitor schemas
    schemas["ServerStatus"] = {
        "type": "object",
        "properties": {
            "version": {"type": "string"},
            "engines": {"type": "array", "items": {"$ref": "#/components/schemas/PluginInfo"}},
            "health": {"type": "object"},
            "cache_enabled": {"type": "boolean"},
            "plugins_enabled": {"type": "boolean"},
        }
    }
    
    # Error schema
    schemas["Error"] = {
        "type": "object",
        "properties": {
            "error": {"type": "string"},
            "message": {"type": "string"},
            "status_code": {"type": "integer"},
        }
    }
    
    return schemas


def save_openapi_spec(output_dir: str = ".") -> str:
    """Generate and save OpenAPI spec to file."""
    spec = generate_openapi_spec()
    output_path = Path(output_dir) / "openapi-3.1.json"
    output_path.write_text(json.dumps(spec, indent=2))
    return str(output_path)


def generate_sdk_scripts() -> Dict[str, str]:
    """Generate SDK generation scripts."""
    
    scripts = {}
    
    # Python SDK generation script
    scripts["generate_sdk_python.sh"] = """#!/bin/bash
# Generate Python SDK from OpenAPI spec
# Requires: openapi-generator-cli

echo "Generating Python SDK..."
openapi-generator-cli generate \\
    -i openapi-3.1.json \\
    -g python \\
    -o sdk/python \\
    --package-name jiro_client \\
    --additional-properties=packageVersion=0.2.0

echo "Python SDK generated at sdk/python/"
"""
    
    # TypeScript SDK generation script
    scripts["generate_sdk_typescript.sh"] = """#!/bin/bash
# Generate TypeScript SDK from OpenAPI spec
# Requires: openapi-generator-cli

echo "Generating TypeScript SDK..."
openapi-generator-cli generate \\
    -i openapi-3.1.json \\
    -g typescript-fetch \\
    -o sdk/typescript \\
    --additional-properties=npmName=@jiro/client,npmVersion=0.2.0

echo "TypeScript SDK generated at sdk/typescript/"
"""
    
    # Go SDK generation script
    scripts["generate_sdk_go.sh"] = """#!/bin/bash
# Generate Go SDK from OpenAPI spec
# Requires: openapi-generator-cli

echo "Generating Go SDK..."
openapi-generator-cli generate \\
    -i openapi-3.1.json \\
    -g go \\
    -o sdk/go \\
    --package-name jiro

echo "Go SDK generated at sdk/go/"
"""
    
    # Docker Compose for SDK generation
    scripts["docker-compose.sdk.yml"] = """version: '3.8'

services:
  sdk-generator:
    image: openapitools/openapi-generator-cli:latest
    volumes:
      - .:/workspace
    working_dir: /workspace
    command: >
      sh -c "
        openapi-generator-cli generate -i openapi-3.1.json -g python -o sdk/python --package-name jiro_client &&
        openapi-generator-cli generate -i openapi-3.1.json -g typescript-fetch -o sdk/typescript --additional-properties=npmName=@jiro/client &&
        openapi-generator-cli generate -i openapi-3.1.json -g go -o sdk/go --package-name jiro
      "
"""
    
    return scripts


def save_sdk_scripts(output_dir: str = ".") -> List[str]:
    """Generate and save SDK scripts."""
    scripts = generate_sdk_scripts()
    output_path = Path(output_dir)
    saved = []
    
    for filename, content in scripts.items():
        filepath = output_path / filename
        filepath.write_text(content)
        saved.append(str(filepath))
    
    return saved


if __name__ == "__main__":
    import sys
    
    output_dir = sys.argv[1] if len(sys.argv) > 1 else "."
    
    print(f"Generating OpenAPI 3.1 spec...")
    spec_path = save_openapi_spec(output_dir)
    print(f"  Saved to: {spec_path}")
    
    print(f"\nGenerating SDK scripts...")
    script_paths = save_sdk_scripts(output_dir)
    for path in script_paths:
        print(f"  Saved to: {path}")
    
    print(f"\nDone! Use the following to generate SDKs:")
    print(f"  pip install openapi-generator-cli")
    print(f"  openapi-generator-cli generate -i openapi-3.1.json -g python -o sdk/python")