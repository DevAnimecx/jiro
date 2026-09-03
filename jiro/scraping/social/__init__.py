"""Social media scraping package."""

from __future__ import annotations

# Import scrapers to register them
from jiro.scraping.social import (
    reddit,
    hackernews,
    youtube,
    bluesky,
    twitter,
    threads,
    instagram,
    tiktok,
    linkedin,
    facebook,
    telegram,
    pinterest,
)

from jiro.scraping.social.base import (
    BaseSocialScraper,
    SocialScraperRegistry,
    SocialPost,
    SocialProfile,
    SocialAuthor,
    SocialEngagement,
    SocialMedia,
    SocialScrapeError,
    RateLimitError,
    AuthRequiredError,
    NotFoundError,
    registry,
)

from jiro.scraping.social.router import (
    SocialRouter,
    router,
    auto_detect,
    detect_action,
    parse_social_url,
)

from jiro.scraping.social.normalizer import (
    SocialNormalizer,
    normalize_timestamp,
    normalize_number,
    normalize_url,
    clean_text,
    extract_hashtags,
    extract_mentions,
    build_post,
    build_profile,
    build_search,
)

__all__ = [
    # Base classes
    "BaseSocialScraper",
    "SocialScraperRegistry",
    "SocialPost",
    "SocialProfile",
    "SocialAuthor",
    "SocialEngagement",
    "SocialMedia",
    "SocialScrapeError",
    "RateLimitError",
    "AuthRequiredError",
    "NotFoundError",
    "registry",
    # Router
    "SocialRouter",
    "router",
    "auto_detect",
    "detect_action",
    "parse_social_url",
    # Normalizer
    "SocialNormalizer",
    "normalize_timestamp",
    "normalize_number",
    "normalize_url",
    "clean_text",
    "extract_hashtags",
    "extract_mentions",
    "build_post",
    "build_profile",
    "build_search",
]