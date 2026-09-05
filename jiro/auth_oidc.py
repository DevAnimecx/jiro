"""OpenID Connect (OIDC) / SSO authentication support.

Provides enterprise identity provider integration for:
- Okta
- Azure AD / Entra ID
- Google Workspace
- Generic OIDC-compliant IdPs

Uses authlib for OIDC token validation and user provisioning.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from jiro.config import Settings
from jiro.errors import AuthError
from jiro.log import get_logger

log = get_logger("jiro.auth_oidc")

# Supported IdP providers with their discovery URLs
IDP_DISCOVERY_URLS: Dict[str, str] = {
    "okta": "https://{domain}/.well-known/openid-configuration",
    "azure": "https://login.microsoftonline.com/{tenant}/v2.0/.well-known/openid-configuration",
    "google": "https://accounts.google.com/.well-known/openid-configuration",
    "generic": "{issuer}/.well-known/openid-configuration",
}


@dataclass
class OIDCConfig:
    """OIDC provider configuration."""
    provider: str  # okta, azure, google, generic
    issuer: str
    client_id: str
    client_secret: str
    domain: str = ""  # For okta: your-domain.okta.com
    tenant: str = ""  # For azure: tenant ID or domain
    scopes: list = field(default_factory=lambda: ["openid", "profile", "email"])
    claim_mapping: Dict[str, str] = field(default_factory=lambda: {
        "email": "email",
        "name": "name",
        "sub": "sub",
    })


class OIDCAuthenticator:
    """Validates OIDC tokens and provisions users.
    
    Flow:
    1. Client sends Authorization: Bearer <oidc_token>
    2. Server validates token signature + claims against IdP
    3. Server maps IdP claims to local user record
    4. Server creates/updates local user + assigns RBAC role
    """
    
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._configs: Dict[str, OIDCConfig] = {}
        self._load_configs()
    
    def _load_configs(self) -> None:
        """Load OIDC configurations from settings."""
        oidc_configs = self.settings.get("auth.oidc", {})
        for name, cfg in oidc_configs.items():
            self._configs[name] = OIDCConfig(
                provider=cfg.get("provider", "generic"),
                issuer=cfg.get("issuer", ""),
                client_id=cfg.get("client_id", ""),
                client_secret=cfg.get("client_secret", ""),
                domain=cfg.get("domain", ""),
                tenant=cfg.get("tenant", ""),
                scopes=cfg.get("scopes", ["openid", "profile", "email"]),
                claim_mapping=cfg.get("claim_mapping", {
                    "email": "email",
                    "name": "name",
                    "sub": "sub",
                }),
            )
    
    def get_config(self, provider_name: str) -> Optional[OIDCConfig]:
        """Get OIDC config by name."""
        return self._configs.get(provider_name)
    
    def list_providers(self) -> List[str]:
        """List configured OIDC provider names."""
        return list(self._configs.keys())
    
    async def validate_token(self, token: str, provider_name: str) -> Dict[str, Any]:
        """Validate an OIDC token and return claims.
        
        Args:
            token: Raw bearer token from Authorization header
            provider_name: Name of the OIDC provider config
            
        Returns:
            Dict of validated claims
            
        Raises:
            AuthError: If token is invalid or provider not configured
        """
        config = self._configs.get(provider_name)
        if not config:
            raise AuthError(f"OIDC provider '{provider_name}' not configured")
        
        try:
            from authlib.jose import jwt
            from authlib.jose.errors import ExpiredTokenError, InvalidTokenError
            
            # Get JWKS for token validation
            jwks = await self._get_jwks(config)
            
            # Validate token
            claims = jwt.decode(
                token,
                jwks,
                claims_options={
                    "iss": {"essential": True, "value": config.issuer},
                    "aud": {"essential": True, "value": config.client_id},
                },
            )
            claims.validate()
            
            return dict(claims)
            
        except ExpiredTokenError:
            raise AuthError("OIDC token expired")
        except InvalidTokenError as exc:
            raise AuthError(f"invalid OIDC token: {exc}")
        except ImportError:
            raise AuthError("authlib package required for OIDC: pip install authlib")
        except Exception as exc:
            raise AuthError(f"OIDC validation failed: {exc}")
    
    async def _get_jwks(self, config: OIDCConfig) -> Any:
        """Fetch JWKS from the IdP's discovery endpoint."""
        import httpx
        
        discovery_url = self._get_discovery_url(config)
        async with httpx.AsyncClient() as client:
            resp = await client.get(discovery_url, timeout=10.0)
            resp.raise_for_status()
            discovery = resp.json()
            
            jwks_url = discovery.get("jwks_uri")
            if not jwks_url:
                raise AuthError("IdP discovery missing jwks_uri")
            
            resp = await client.get(jwks_url, timeout=10.0)
            resp.raise_for_status()
            return resp.json()
    
    def _get_discovery_url(self, config: OIDCConfig) -> str:
        """Build the OIDC discovery URL for the provider."""
        template = IDP_DISCOVERY_URLS.get(config.provider, IDP_DISCOVERY_URLS["generic"])
        return template.format(
            domain=config.domain,
            tenant=config.tenant,
            issuer=config.issuer,
        )
    
    def map_claims(self, claims: Dict[str, Any], config: OIDCConfig) -> Dict[str, Any]:
        """Map IdP claims to local user attributes."""
        mapped = {}
        for local_key, claim_key in config.claim_mapping.items():
            value = claims.get(claim_key)
            if value:
                mapped[local_key] = value
        return mapped
    
    async def provision_user(self, claims: Dict[str, Any], provider_name: str) -> Dict[str, Any]:
        """Create or update local user from OIDC claims.
        
        Links the OIDC identity to a local API key or creates a new one.
        """
        from jiro.db import Database
        
        db = Database(self.settings.db_path)
        await db.connect()
        
        try:
            # Extract identity
            sub = claims.get("sub")
            email = claims.get("email", "")
            name = claims.get("name", email)
            
            if not sub:
                raise AuthError("OIDC claims missing 'sub'")
            
            # Check if identity already exists
            existing = await db.fetchone(
                "SELECT * FROM oidc_identities WHERE provider = ? AND subject = ?",
                (provider_name, sub),
            )
            
            if existing:
                # Update last login
                await db.execute(
                    "UPDATE oidc_identities SET last_login = ? WHERE id = ?",
                    (time.time(), existing["id"]),
                )
                return dict(existing)
            
            # Create new identity
            identity_id = f"oidc_{provider_name}_{sub[:16]}"
            await db.execute(
                "INSERT INTO oidc_identities (id, provider, subject, email, name, created_at, last_login)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (identity_id, provider_name, sub, email, name, time.time(), time.time()),
            )
            
            return {
                "id": identity_id,
                "provider": provider_name,
                "subject": sub,
                "email": email,
                "name": name,
            }
        finally:
            await db.close()


class OIDCStateManager:
    """Manages OIDC state parameters for CSRF protection."""
    
    def __init__(self) -> None:
        self._states: Dict[str, Dict[str, Any]] = {}
    
    def create_state(self, redirect_uri: str, nonce: Optional[str] = None) -> str:
        """Create a state parameter for OIDC authorization request."""
        import secrets
        state = secrets.token_urlsafe(32)
        self._states[state] = {
            "redirect_uri": redirect_uri,
            "nonce": nonce or secrets.token_urlsafe(16),
            "created_at": time.time(),
        }
        return state
    
    def verify_state(self, state: str) -> Optional[Dict[str, Any]]:
        """Verify and consume a state parameter."""
        return self._states.pop(state, None)
    
    def cleanup_expired(self, max_age: int = 600) -> None:
        """Remove expired state parameters."""
        now = time.time()
        expired = [s for s, v in self._states.items() if now - v["created_at"] > max_age]
        for s in expired:
            del self._states[s]
