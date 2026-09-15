"""Cloud credentials management for Jiro CLI.

Stores API key and user info in encrypted storage (~/.jiro/credentials.enc).
Migrates from legacy plaintext (~/.jiro/cloud.json) automatically.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Optional
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError


JIRO_CLOUD_BASE = os.environ.get("JIRO_CLOUD_BASE", "https://api.jiro.cloud")
JIRO_WEB_BASE = os.environ.get("JIRO_WEB_BASE", "https://searchjiro.vercel.app")


@dataclass
class CloudCredentials:
    api_key: str
    user_id: str
    email: str
    name: str
    plan: str = "FREE"
    role: str = "USER"
    credits_used: int = 0
    credits_included: int = 1000
    credits_remaining: int = 1000
    rate_limit_rpm: int = 5

    @property
    def credits_pct(self) -> float:
        if self.credits_included <= 0:
            return 0.0
        return min(100.0, (self.credits_used / self.credits_included) * 100)

    @property
    def is_pro(self) -> bool:
        return self.plan.upper() in ("PRO", "ENTERPRISE")

    @property
    def is_enterprise(self) -> bool:
        return self.plan.upper() == "ENTERPRISE"


def load_cloud_credentials() -> Optional[CloudCredentials]:
    """Load cloud credentials from encrypted storage."""
    from jiro.secure_store import load_credentials

    data = load_credentials()
    if not data:
        return None

    return CloudCredentials(
        api_key=data.get("api_key", ""),
        user_id=data.get("user_id", ""),
        email=data.get("email", ""),
        name=data.get("name", ""),
        plan=data.get("plan", "FREE"),
        role=data.get("role", "USER"),
        credits_used=data.get("credits_used", 0),
        credits_included=data.get("credits_included", 1000),
        credits_remaining=data.get("credits_remaining", 1000),
        rate_limit_rpm=data.get("rate_limit_rpm", 5),
    )


def save_cloud_credentials(creds: CloudCredentials) -> None:
    """Save cloud credentials to encrypted storage."""
    from jiro.secure_store import save_credentials

    data = {
        "api_key": creds.api_key,
        "user_id": creds.user_id,
        "email": creds.email,
        "name": creds.name,
        "plan": creds.plan,
        "role": creds.role,
        "credits_used": creds.credits_used,
        "credits_included": creds.credits_included,
        "credits_remaining": creds.credits_remaining,
        "rate_limit_rpm": creds.rate_limit_rpm,
    }
    save_credentials(data)


def clear_cloud_credentials() -> bool:
    """Remove cloud credentials. Returns True if credentials existed."""
    from jiro.secure_store import clear_credentials
    return clear_credentials()


def is_cloud_configured() -> bool:
    """Check if cloud credentials are configured."""
    from jiro.secure_store import is_configured
    return is_configured()


def fetch_credits_from_server(api_key: str) -> Optional[dict]:
    """Fetch current credit balance from the server.

    Returns:
        { plan, credits: { used, included, remaining }, rateLimit: { rpm } }
    """
    url = f"{JIRO_WEB_BASE}/api/auth/credits"
    req = Request(url)
    req.add_header("Authorization", f"Bearer {api_key}")

    try:
        with urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode())
    except (HTTPError, URLError, Exception):
        return None


def refresh_credits(creds: CloudCredentials) -> CloudCredentials:
    """Refresh credit balance from server and update local storage."""
    data = fetch_credits_from_server(creds.api_key)
    if data:
        credits = data.get("credits", {})
        rate = data.get("rateLimit", {})
        plan = data.get("plan", creds.plan).upper()

        creds.plan = plan
        creds.credits_used = credits.get("used", creds.credits_used)
        creds.credits_included = credits.get("included", creds.credits_included)
        creds.credits_remaining = credits.get("remaining", creds.credits_remaining)
        creds.rate_limit_rpm = rate.get("rpm", creds.rate_limit_rpm)

        # Update role from server
        user = data.get("user", {})
        if user.get("role"):
            creds.role = user["role"]

        save_cloud_credentials(creds)

    return creds
