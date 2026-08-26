"""Legal compliance and ToS management.

Provides engine-specific Terms of Service warnings, compliance checks,
and user acknowledgment tracking.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from jiro.config import Settings
from jiro.log import get_logger

log = get_logger("jiro.compliance")


@dataclass
class EngineTOS:
    """Terms of Service information for a search engine."""
    engine: str
    tos_url: str
    robots_url: str
    allowed_uses: List[str] = field(default_factory=list)
    prohibited_uses: List[str] = field(default_factory=list)
    rate_limit_guidance: str = ""
    attribution_required: bool = False
    commercial_use_allowed: bool = False
    requires_approval: bool = False
    last_updated: str = ""
    version: str = "1.0"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "engine": self.engine,
            "tos_url": self.tos_url,
            "robots_url": self.robots_url,
            "allowed_uses": self.allowed_uses,
            "prohibited_uses": self.prohibited_uses,
            "rate_limit_guidance": self.rate_limit_guidance,
            "attribution_required": self.attribution_required,
            "commercial_use_allowed": self.commercial_use_allowed,
            "requires_approval": self.requires_approval,
            "last_updated": self.last_updated,
            "version": self.version,
        }


# Engine-specific ToS information (maintained by project maintainers)
ENGINE_TOS_REGISTRY: Dict[str, EngineTOS] = {
    "google": EngineTOS(
        engine="google",
        tos_url="https://policies.google.com/terms",
        robots_url="https://www.google.com/robots.txt",
        allowed_uses=["personal research", "non-commercial analysis"],
        prohibited_uses=[
            "automated scraping at scale",
            "commercial use without permission",
            "bypassing CAPTCHA or bot detection",
            "storing or redistributing results",
            "creating derivative search services",
        ],
        rate_limit_guidance="Respect crawl-delay in robots.txt. Use reasonable delays between requests.",
        attribution_required=True,
        commercial_use_allowed=False,
        requires_approval=True,
        last_updated="2024-01-01",
        version="2024.1",
    ),
    "bing": EngineTOS(
        engine="bing",
        tos_url="https://www.microsoft.com/en-us/servicesagreement",
        robots_url="https://www.bing.com/robots.txt",
        allowed_uses=["personal use", "research", "educational"],
        prohibited_uses=[
            "commercial scraping",
            "competitive analysis of Microsoft services",
            "bypassing rate limits",
            "mass data collection",
        ],
        rate_limit_guidance="Respect robots.txt crawl-delay. Recommended: 1 request/second max.",
        attribution_required=True,
        commercial_use_allowed=False,
        requires_approval=True,
        last_updated="2024-01-01",
        version="2024.1",
    ),
    "duckduckgo": EngineTOS(
        engine="duckduckgo",
        tos_url="https://duckduckgo.com/tos",
        robots_url="https://duckduckgo.com/robots.txt",
        allowed_uses=["personal use", "non-commercial research"],
        prohibited_uses=[
            "commercial scraping",
            "automated high-volume queries",
            "bypassing rate limits",
        ],
        rate_limit_guidance="Be respectful. No official rate limits published.",
        attribution_required=True,
        commercial_use_allowed=False,
        requires_approval=False,
        last_updated="2024-01-01",
        version="2024.1",
    ),
    "brave": EngineTOS(
        engine="brave",
        tos_url="https://search.brave.com/terms",
        robots_url="https://search.brave.com/robots.txt",
        allowed_uses=["personal use", "research"],
        prohibited_uses=[
            "commercial use without license",
            "automated scraping",
            "competing search service",
        ],
        rate_limit_guidance="Respect robots.txt. Use API for commercial use.",
        attribution_required=True,
        commercial_use_allowed=False,
        requires_approval=True,
        last_updated="2024-01-01",
        version="2024.1",
    ),
    "youtube": EngineTOS(
        engine="youtube",
        tos_url="https://www.youtube.com/t/terms",
        robots_url="https://www.youtube.com/robots.txt",
        allowed_uses=["personal viewing", "embedding via official player"],
        prohibited_uses=[
            "scraping video metadata at scale",
            "downloading videos",
            "commercial use without YouTube API",
            "bypassing restrictions",
        ],
        rate_limit_guidance="Use YouTube Data API for programmatic access.",
        attribution_required=True,
        commercial_use_allowed=False,
        requires_approval=True,
        last_updated="2024-01-01",
        version="2024.1",
    ),
    "amazon": EngineTOS(
        engine="amazon",
        tos_url="https://www.amazon.com/gp/help/customer/display.html?nodeId=508088",
        robots_url="https://www.amazon.com/robots.txt",
        allowed_uses=["personal shopping"],
        prohibited_uses=[
            "any automated scraping",
            "price monitoring",
            "competitive analysis",
            "data mining",
            "commercial use",
        ],
        rate_limit_guidance="Strictly prohibited. Use Amazon Product Advertising API.",
        attribution_required=False,
        commercial_use_allowed=False,
        requires_approval=True,
        last_updated="2024-01-01",
        version="2024.1",
    ),
    "ebay": EngineTOS(
        engine="ebay",
        tos_url="https://www.ebay.com/help/policies/user-agreement/user-agreement?id=4237",
        robots_url="https://www.ebay.com/robots.txt",
        allowed_uses=["personal browsing"],
        prohibited_uses=[
            "automated scraping",
            "price comparison services",
            "data aggregation",
            "commercial use without API",
        ],
        rate_limit_guidance="Use eBay APIs for programmatic access.",
        attribution_required=False,
        commercial_use_allowed=False,
        requires_approval=True,
        last_updated="2024-01-01",
        version="2024.1",
    ),
    "yandex": EngineTOS(
        engine="yandex",
        tos_url="https://yandex.com/legal/termsofuse/",
        robots_url="https://yandex.com/robots.txt",
        allowed_uses=["personal use", "non-commercial research"],
        prohibited_uses=[
            "automated scraping",
            "commercial use",
            "bypassing protections",
        ],
        rate_limit_guidance="Respect robots.txt crawl-delay.",
        attribution_required=True,
        commercial_use_allowed=False,
        requires_approval=True,
        last_updated="2024-01-01",
        version="2024.1",
    ),
    "baidu": EngineTOS(
        engine="baidu",
        tos_url="https://www.baidu.com/duty/",
        robots_url="https://www.baidu.com/robots.txt",
        allowed_uses=["personal use"],
        prohibited_uses=[
            "automated scraping",
            "commercial use",
            "data collection",
            "bypassing Chinese internet regulations",
        ],
        rate_limit_guidance="Strict compliance with Chinese law required.",
        attribution_required=True,
        commercial_use_allowed=False,
        requires_approval=True,
        last_updated="2024-01-01",
        version="2024.1",
    ),
}


@dataclass
class UserAcknowledgment:
    """User acknowledgment of ToS for an engine."""
    user_id: str
    engine: str
    tos_version: str
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    acknowledged_at: float = field(default_factory=time.time)
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "engine": self.engine,
            "tos_version": self.tos_version,
            "acknowledged_at": self.acknowledged_at,
            "ip_address": self.ip_address,
            "user_agent": self.user_agent,
        }


class ComplianceManager:
    """Manages ToS compliance and user acknowledgments."""

    def __init__(self, settings: Settings, db: Any = None) -> None:
        self.settings = settings
        self.db = db
        self._acknowledgments: Dict[str, UserAcknowledgment] = {}

    def get_tos(self, engine: str) -> Optional[EngineTOS]:
        """Get ToS info for an engine."""
        return ENGINE_TOS_REGISTRY.get(engine)

    def get_all_tos(self) -> Dict[str, EngineTOS]:
        """Get all engine ToS information."""
        return ENGINE_TOS_REGISTRY.copy()

    def check_compliance(self, engine: str, use_case: str) -> Dict[str, Any]:
        """Check if a use case is compliant with engine ToS."""
        tos = self.get_tos(engine)
        if not tos:
            return {"compliant": True, "engine": engine, "warnings": []}

        warnings = []
        if use_case in tos.prohibited_uses:
            warnings.append(f"Use case '{use_case}' is explicitly prohibited by {engine} ToS")

        if "commercial" in use_case.lower() and not tos.commercial_use_allowed:
            warnings.append(f"Commercial use is not allowed by {engine} ToS")

        if tos.requires_approval:
            warnings.append(f"{engine} requires explicit approval for automated access")

        return {
            "compliant": len(warnings) == 0,
            "engine": engine,
            "warnings": warnings,
            "tos_version": tos.version,
            "tos_url": tos.tos_url,
        }

    def get_compliance_summary(self, engines: List[str]) -> Dict[str, Any]:
        """Get compliance summary for multiple engines."""
        summary = {}
        for engine in engines:
            tos = self.get_tos(engine)
            if tos:
                summary[engine] = {
                    "commercial_allowed": tos.commercial_use_allowed,
                    "attribution_required": tos.attribution_required,
                    "requires_approval": tos.requires_approval,
                    "prohibited_uses": tos.prohibited_uses,
                    "tos_url": tos.tos_url,
                }
        return summary

    async def acknowledge_tos(self, user_id: str, engine: str,
                              ip: Optional[str] = None,
                              user_agent: Optional[str] = None) -> UserAcknowledgment:
        """Record user acknowledgment of ToS."""
        tos = self.get_tos(engine)
        if not tos:
            raise ValueError(f"No ToS info for engine: {engine}")

        ack = UserAcknowledgment(
            user_id=user_id,
            engine=engine,
            tos_version=tos.version,
            ip_address=ip,
            user_agent=user_agent,
        )

        key = f"{user_id}:{engine}"
        self._acknowledgments[key] = ack

        if self.db:
            try:
                await self.db.tos_ack_create(ack.to_dict())
            except Exception as exc:
                log.warning("failed to persist ToS acknowledgment",
                           extra={"error": str(exc)})

        return ack

    def has_acknowledged(self, user_id: str, engine: str) -> bool:
        """Check if user has acknowledged ToS for an engine (in-memory)."""
        tos = self.get_tos(engine)
        if not tos:
            return True  # No ToS = no acknowledgment needed

        key = f"{user_id}:{engine}"
        ack = self._acknowledgments.get(key)
        if ack and ack.tos_version == tos.version:
            return True
        return False

    async def has_acknowledged_async(self, user_id: str, engine: str) -> bool:
        """Check acknowledgment, falling back to the persistent DB store."""
        if self.has_acknowledged(user_id, engine):
            return True
        if self.db is None:
            return False
        try:
            row = await self.db.tos_ack_get(user_id, engine)
        except Exception:
            return False
        if not row:
            return False
        tos = self.get_tos(engine)
        return tos is None or row.get("tos_version") == tos.version

    def get_acknowledgment(self, user_id: str, engine: str) -> Optional[UserAcknowledgment]:
        """Get user's ToS acknowledgment."""
        key = f"{user_id}:{engine}"
        return self._acknowledgments.get(key)

    def generate_compliance_report(self) -> Dict[str, Any]:
        """Generate a compliance report for all engines."""
        report: Dict[str, Any] = {
            "generated_at": time.time(),
            "engines": {},
            "summary": {
                "total_engines": len(ENGINE_TOS_REGISTRY),
                "commercial_allowed": 0,
                "requires_approval": 0,
                "attribution_required": 0,
            },
        }

        for engine, tos in ENGINE_TOS_REGISTRY.items():
            report["engines"][engine] = tos.to_dict()
            if tos.commercial_use_allowed:
                report["summary"]["commercial_allowed"] += 1
            if tos.requires_approval:
                report["summary"]["requires_approval"] += 1
            if tos.attribution_required:
                report["summary"]["attribution_required"] += 1

        return report

    def export_tos_markdown(self) -> str:
        """Export ToS information as Markdown for documentation."""
        lines = ["# Engine Terms of Service Compliance", "",
                 f"Generated: {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}", "",
                 "⚠️ **IMPORTANT**: This information is for reference only. ",
                 "Always review the current ToS at the provided URLs before use.",
                 "Jiro Search does not provide legal advice.",
                 ""]

        for engine, tos in sorted(ENGINE_TOS_REGISTRY.items()):
            lines.append(f"## {engine.capitalize()}")
            lines.append(f"- **ToS URL**: {tos.tos_url}")
            lines.append(f"- **robots.txt**: {tos.robots_url}")
            lines.append(f"- **Commercial Use**: {'✅ Allowed' if tos.commercial_use_allowed else '❌ Not Allowed'}")
            lines.append(f"- **Attribution Required**: {'Yes' if tos.attribution_required else 'No'}")
            lines.append(f"- **Requires Approval**: {'Yes' if tos.requires_approval else 'No'}")
            lines.append(f"- **Rate Limit Guidance**: {tos.rate_limit_guidance or 'Not specified'}")
            lines.append("")
            lines.append("**Allowed Uses:**")
            for use in tos.allowed_uses:
                lines.append(f"  - {use}")
            lines.append("")
            lines.append("**Prohibited Uses:**")
            for use in tos.prohibited_uses:
                lines.append(f"  - {use}")
            lines.append("")

        return "\n".join(lines)


# Compliance warnings shown to users
COMPLIANCE_WARNINGS = {
    "startup": (
        "⚠️  LEGAL NOTICE: Jiro Search scrapes public search engine results. "
        "Each engine has its own Terms of Service and robots.txt rules. "
        "YOU are responsible for complying with all applicable terms. "
        "See `/engines/compliance` for details."
    ),
    "google_warning": (
        "🔴 Google ToS prohibits automated scraping. "
        "Use residential proxies and respect rate limits. "
        "Commercial use requires explicit permission from Google."
    ),
    "amazon_warning": (
        "🔴 Amazon ToS strictly prohibits automated scraping. "
        "Use Amazon Product Advertising API instead. "
        "This engine is provided for research only."
    ),
    "commercial_warning": (
        "⚠️  Commercial use of scraped search results may violate ToS. "
        "Review each engine's terms before using in production."
    ),
}


def get_startup_warning() -> str:
    """Get the startup compliance warning."""
    return COMPLIANCE_WARNINGS["startup"]


def get_engine_warning(engine: str) -> Optional[str]:
    """Get engine-specific warning."""
    key = f"{engine}_warning"
    return COMPLIANCE_WARNINGS.get(key)