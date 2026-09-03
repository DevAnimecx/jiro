"""Pydantic data models — the API contracts (PRD §8.3)."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator


# --------------------------------------------------------------------------
# Search
# --------------------------------------------------------------------------
class SearchRequest(BaseModel):
    q: str = Field(..., min_length=1, max_length=500, description="Search query")
    engine: str = Field("google", description="google | bing | duckduckgo | brave | youtube | amazon | ebay | yandex | baidu | auto")
    type: str = Field("web", description="web | images | news | videos | shopping | places")
    location: str = Field("us", description="Country/region code")
    language: str = Field("en", description="Language code")
    num: int = Field(10, ge=1, le=100, description="Number of results (1-100)")
    start: int = Field(0, ge=0, description="Pagination offset")
    safe: str = Field("off", description="off | medium | high")
    time_range: str = Field("any", description="any | day | week | month | year")
    device: str = Field("desktop", description="desktop | mobile")
    gl: str = Field("us", description="Google country parameter")
    hl: str = Field("en", description="Google language parameter")
    fresh: bool = Field(False, description="Force refresh (bypass cache)")
    
    # v0.2: Hybrid search parameters
    mode: str = Field("auto", description="auto | keyword | hybrid")
    depth: str = Field("basic", description="instant | fast | basic | advanced | deep")
    
    # v0.2: Domain filtering
    include_domains: Optional[List[str]] = Field(None, description="Only include results from these domains")
    exclude_domains: Optional[List[str]] = Field(None, description="Exclude results from these domains")
    bias_domains: Optional[Dict[str, float]] = Field(None, description="Soft-rank boost for domains")
    
    # v0.2: Date range filtering (absolute)
    start_date: Optional[str] = Field(None, description="Start date YYYY-MM-DD")
    end_date: Optional[str] = Field(None, description="End date YYYY-MM-DD")
    
    # v0.2: Category-specific search
    category: Optional[str] = Field(None, description="publication | financial_report | people | shopping | github | news | code | academic")
    
    # v0.2: Content highlights
    highlights: bool = Field(False, description="Extract token-efficient highlights")
    max_highlight_chars: int = Field(500, ge=100, le=2000, description="Max characters for highlights")
    
    # v0.2: Answer synthesis
    include_answer: Optional[str] = Field(None, description="none | extractive | advanced")
    output_schema: Optional[Dict[str, Any]] = Field(None, description="JSON Schema for structured output")

    @field_validator("num")
    @classmethod
    def clamp_num(cls, v: int) -> int:
        return max(1, min(v, 100))

    @field_validator("engine")
    @classmethod
    def engine_lower(cls, v: str) -> str:
        return v.strip().lower()

    @field_validator("mode")
    @classmethod
    def mode_lower(cls, v: str) -> str:
        return v.strip().lower()

    @field_validator("depth")
    @classmethod
    def depth_lower(cls, v: str) -> str:
        return v.strip().lower()

    @field_validator("include_answer")
    @classmethod
    def answer_lower(cls, v: Optional[str]) -> Optional[str]:
        return v.strip().lower() if v else None


class OrganicResult(BaseModel):
    position: int
    title: str = ""
    link: str = ""
    displayed_link: str = ""
    snippet: str = ""
    date: Optional[str] = None
    source: Optional[str] = None
    sitelinks: List[Dict[str, Any]] = Field(default_factory=list)
    rich_snippet: Dict[str, Any] = Field(default_factory=dict)
    thumbnail: Optional[str] = None
    dimensions: Optional[str] = None
    duration: Optional[str] = None
    source_type: Optional[str] = None
    price: Optional[str] = None
    merchant: Optional[str] = None
    rating: Optional[str] = None
    reviews: Optional[str] = None
    address: Optional[str] = None
    phone: Optional[str] = None
    # YouTube-specific
    channel: Optional[str] = None
    views: Optional[str] = None
    # Amazon/eBay-specific
    asin: Optional[str] = None
    condition: Optional[str] = None
    seller: Optional[str] = None
    shipping: Optional[str] = None
    prime: Optional[bool] = None
    # v0.2: Relevance scoring
    relevance: Optional[Dict[str, Any]] = Field(None, description="Relevance score with breakdown")
    # v0.2: Content highlights
    highlights: Optional[List[str]] = Field(None, description="Token-efficient content excerpts")


class SearchResponse(BaseModel):
    search_metadata: Dict[str, Any] = Field(default_factory=dict)
    search_information: Dict[str, Any] = Field(default_factory=dict)
    organic_results: List[OrganicResult] = Field(default_factory=list)
    ads: List[Dict[str, Any]] = Field(default_factory=list)
    knowledge_graph: Dict[str, Any] = Field(default_factory=dict)
    related_questions: List[Dict[str, Any]] = Field(default_factory=list)
    related_searches: List[Dict[str, Any]] = Field(default_factory=list)
    pagination: Dict[str, Any] = Field(default_factory=dict)
    raw: Dict[str, Any] = Field(default_factory=dict, exclude=True)
    # v0.2: Answer synthesis
    answer: Optional[Dict[str, Any]] = Field(None, description="Synthesized answer with citations")


# v0.2: Structured output extraction
class StructuredExtractRequest(BaseModel):
    url: Optional[str] = None
    text: Optional[str] = None
    html: Optional[str] = None
    schema: Dict[str, Any] = Field(..., description="JSON Schema for extraction")
    mode: str = Field("auto", description="auto | extractive | llm")


class StructuredExtractResponse(BaseModel):
    extracted: Dict[str, Any] = Field(default_factory=dict)
    provider: str = ""
    model: Optional[str] = None
    confidence: float = 0.0


# v0.2: Multi-query search
class MultiQuerySearchRequest(BaseModel):
    queries: List[str] = Field(..., min_length=1, max_length=10)
    merge: bool = True
    deduplicate: bool = True
    rerank: bool = True
    max_results: int = Field(20, ge=1, le=100)
    depth: str = Field("basic", description="instant | fast | basic | advanced | deep")
    engine: str = Field("auto")


# v0.2: Smart router request
class SmartRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=500, description="Natural language query")
    max_results: int = Field(10, ge=1, le=50)
    include_social: bool = False


# --------------------------------------------------------------------------
# Scrape
# --------------------------------------------------------------------------
class ScrapeRequest(BaseModel):
    url: str = Field(..., description="URL to scrape")
    format: str = Field("markdown", description="markdown | text | html | json")
    include_metadata: bool = True
    include_structured: bool = Field(False, description="Include Schema.org structured data extraction")
    extract_schema: Optional[Dict[str, Any]] = Field(
        None, description="Custom schema for LLM-assisted extraction"
    )
    recipe: Optional[Dict[str, Dict[str, str]]] = Field(
        None, description="Custom extraction recipe: {css| xpath| jsonpath: {field: rule}}"
    )

    @field_validator("format")
    @classmethod
    def format_valid(cls, v: str) -> str:
        v = v.strip().lower()
        if v not in ("markdown", "text", "html", "json"):
            raise ValueError("format must be one of: markdown, text, html, json")
        return v


class ScrapeResponse(BaseModel):
    url: str
    title: str = ""
    content: str = ""
    links: List[Dict[str, Any]] = Field(default_factory=list)
    images: List[Dict[str, Any]] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    status_code: int = 200
    time_taken: float = 0.0
    cached: bool = False
    extracted: Optional[Dict[str, Any]] = Field(
        default=None, description="LLM-assisted extraction result (if extract_schema given)"
    )
    recipe_result: Optional[Dict[str, Any]] = Field(
        default=None, description="Custom recipe extraction result (if recipe given)"
    )


class BatchScrapeItem(BaseModel):
    url: str
    format: str = "markdown"
    extract_schema: Optional[Dict[str, Any]] = None


# --------------------------------------------------------------------------
# AI endpoints
# --------------------------------------------------------------------------
class AISearchRequest(BaseModel):
    query: str = Field(..., min_length=1, description="Natural-language question")
    max_sources: int = Field(5, ge=1, le=20)
    return_answer: bool = True
    stream: bool = False
    llm_provider: Optional[str] = None
    llm_model: Optional[str] = None


class AISearchResponse(BaseModel):
    answer: str = ""
    citations: List[Dict[str, Any]] = Field(default_factory=list)
    search_results: List[Dict[str, Any]] = Field(default_factory=list)
    reasoning_steps: List[Dict[str, Any]] = Field(default_factory=list)
    sources_used: List[Dict[str, Any]] = Field(default_factory=list)
    provider: Optional[str] = None
    model: Optional[str] = None


class AgentRequest(BaseModel):
    goal: str = Field(..., min_length=1, description="Research goal (multi-step)")
    max_steps: int = Field(5, ge=1, le=20)
    max_sources: int = Field(8, ge=1, le=20)
    max_sources_per_step: int = Field(3, ge=1, le=10)
    refine: bool = Field(True, description="Iteratively refine queries after each step")
    return_answer: bool = True
    llm_provider: Optional[str] = None
    llm_model: Optional[str] = None


class JobCreate(BaseModel):
    type: str = Field(..., description="ai_search | batch_scrape | ai_agent")
    payload: Dict[str, Any] = Field(default_factory=dict)
    webhook_url: Optional[str] = Field(None, description="POST result here on completion")
    webhook_secret: Optional[str] = Field(None, description="Sent as X-Jiro-Webhook-Sig header")


class JobOut(BaseModel):
    id: str
    type: str
    status: str
    created_at: str
    completed_at: Optional[str] = None
    progress: str = ""
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    webhook_url: Optional[str] = None
    webhook_delivered: bool = False


class AIExtractRequest(BaseModel):
    url: Optional[str] = None
    text: Optional[str] = None
    schema_: Dict[str, str] = Field(
        default_factory=dict,
        description="Field name → type ('string' | 'number' | 'date' | 'text' | 'list')",
        alias="schema",
    )


# --------------------------------------------------------------------------
# Social endpoints (v0.2)
# --------------------------------------------------------------------------
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
    latency_ms: Optional[float] = None


class SocialBatchRequest(BaseModel):
    urls: List[str] = Field(..., min_length=1, max_length=10, description="List of social URLs to scrape")
    parallel: bool = True
    max_concurrent: int = Field(5, ge=1, le=10)


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


# --------------------------------------------------------------------------
# Auth / admin
# --------------------------------------------------------------------------
class ApiKeyCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    role: str = Field("user", description="admin | user")
    rate_limit_rpm: int = Field(0, ge=0, description="0 = instance default")
    scopes: List[str] = Field(default_factory=lambda: ["search", "scrape", "ai"])


class ApiKeyOut(BaseModel):
    id: str
    name: str
    key_prefix: str
    role: str
    scopes: List[str]
    rate_limit_rpm: int
    created_at: str
    revoked: bool
    last_used_at: Optional[str] = None


class ApiKeyCreated(BaseModel):
    id: str
    api_key: str  # full key shown once
    name: str
    role: str


class LoginRequest(BaseModel):
    api_key: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    scope: str
