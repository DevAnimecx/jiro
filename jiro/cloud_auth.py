"""Cloud credentials management for Jiro CLI.

Stores API key and user info in encrypted storage (~/.jiro/credentials.enc).
Migrates from legacy plaintext (~/.jiro/cloud.json) automatically.
"""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Optional
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError


JIRO_CLOUD_BASE = os.environ.get("JIRO_CLOUD_BASE", "https://api.jiro.cloud")
JIRO_WEB_BASE = os.environ.get("JIRO_WEB_BASE", "https://searchjiro.vercel.app")

_AUTO_REFRESH_INTERVAL = 300  # 5 minutes
_last_refresh: float = 0.0
_refresh_lock = threading.Lock()


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
        "api_key": creds.api_key, "user_id": creds.user_id,
        "email": creds.email, "name": creds.name,
        "plan": creds.plan, "role": creds.role,
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
        { plan, credits: { used, included, remaining }, rateLimit: { rpm },
          user: { id, email, name, role } }
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
        user = data.get("user", {})
        plan = data.get("plan", creds.plan).upper()
        creds.plan = plan
        creds.credits_used = credits.get("used", creds.credits_used)
        creds.credits_included = credits.get("included", creds.credits_included)
        creds.credits_remaining = credits.get("remaining", creds.credits_remaining)
        creds.rate_limit_rpm = rate.get("rpm", creds.rate_limit_rpm)
        if user.get("role"):
            creds.role = user["role"]
        if user.get("email"):
            creds.email = user["email"]
        if user.get("name"):
            creds.name = user["name"]
        save_cloud_credentials(creds)
    return creds


def _auto_refresh_background() -> None:
    """Background thread: refresh account info from server."""
    global _last_refresh
    try:
        creds = load_cloud_credentials()
        if not creds or not creds.api_key:
            return
        data = fetch_credits_from_server(creds.api_key)
        if not data:
            return
        credits = data.get("credits", {})
        rate = data.get("rateLimit", {})
        user = data.get("user", {})
        plan = data.get("plan", creds.plan).upper()
        changed = False
        if plan != creds.plan:
            creds.plan = plan
            changed = True
        new_used = credits.get("used", creds.credits_used)
        new_included = credits.get("included", creds.credits_included)
        if new_used != creds.credits_used or new_included != creds.credits_included:
            creds.credits_used = new_used
            creds.credits_included = new_included
            creds.credits_remaining = credits.get("remaining", creds.credits_remaining)
            changed = True
        rpm = rate.get("rpm")
        if rpm and rpm != creds.rate_limit_rpm:
            creds.rate_limit_rpm = rpm
            changed = True
        if user.get("role") and user["role"] != creds.role:
            creds.role = user["role"]
            changed = True
        if user.get("email") and user["email"] != creds.email:
            creds.email = user["email"]
            changed = True
        if user.get("name") and user["name"] != creds.name:
            creds.name = user["name"]
            changed = True
        if changed:
            save_cloud_credentials(creds)
        _last_refresh = time.time()
    except Exception:
        pass


def auto_refresh_account(silent: bool = True) -> None:
    """Auto-refresh account info if stale. Non-blocking, runs in background.

    Args:
        silent: If True, runs in a daemon thread. If False, blocks.
    """
    global _last_refresh
    now = time.time()
    if now - _last_refresh < _AUTO_REFRESH_INTERVAL:
        return
    if silent:
        t = threading.Thread(target=_auto_refresh_background, daemon=True)
        t.start()
    else:
        _auto_refresh_background()
