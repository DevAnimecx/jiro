"""Social media scraping package."""

from __future__ import annotations

import logging

log = logging.getLogger("jiro.scraping.social")

# Import scrapers with error isolation — one broken scraper should not
# prevent the others from being available.
import importlib

_SCRAPER_MODULES = [
    "reddit",
    "hackernews",
    "youtube",
    "bluesky",
    "twitter",
    "threads",
    "instagram",
    "tiktok",
    "linkedin",
    "facebook",
    "telegram",
    "pinterest",
]

for _mod_name in _SCRAPER_MODULES:
    try:
        importlib.import_module(f"jiro.scraping.social.{_mod_name}")
    except Exception as exc:
        log.warning("Failed to import social scraper '%s': %s", _mod_name, exc)

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