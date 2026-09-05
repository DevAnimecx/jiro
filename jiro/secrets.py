"""Encrypted secrets vault for API keys, cookies, and tokens.

Provides:
- Fernet-based encryption for secrets at rest
- Machine-specific key derivation
- OS keyring integration (optional)
- In-memory caching with secure cleanup
"""

from __future__ import annotations

import hashlib
import logging
import os
import platform
import secrets
from pathlib import Path
from typing import Optional

try:
    from cryptography.fernet import Fernet
    _CRYPTO_AVAILABLE = True
except ImportError:
    _CRYPTO_AVAILABLE = False

from jiro.config import Settings

log = logging.getLogger("jiro.secrets")

_DEFAULT_VAULT_PATH = "~/.jiro/vault.enc"
_KEYRING_SERVICE = "jiro"


class SecretVault:
    """Encrypted storage for sensitive data.

    Uses Fernet (AES-128-CBC + HMAC) for encryption.
    Keys are derived from machine-specific identifiers.
    """

    def __init__(self, vault_path: Optional[str] = None, settings: Optional[Settings] = None) -> None:
        if not _CRYPTO_AVAILABLE:
            raise ImportError("cryptography package required: pip install cryptography")

        self.settings = settings or Settings.load()
        self.vault_path = Path(
            vault_path or self.settings.get("secrets.vault_path", _DEFAULT_VAULT_PATH)
        ).expanduser()
        self._key: bytes = self._derive_key()
        self._fernet = Fernet(self._key)
        self._cache: dict[str, str] = {}
        self._cache_ttl = 300  # 5 minutes

    def _derive_key(self) -> bytes:
        """Derive encryption key from machine-specific data.

        Uses a combination of:
        - Machine ID (platform-specific)
        - OS keyring (if available)
        - Configurable salt
        """
        machine_id = self._get_machine_id()

        # Try OS keyring first (most secure)
        try:
            import keyring
            stored_key = keyring.get_password(_KEYRING_SERVICE, "vault_key")
            if stored_key:
                return base64.urlsafe_b64encode(stored_key.encode())
        except Exception:
            pass

        # Fallback: derive from machine ID + salt
        salt = self.settings.get("secrets.salt", "jiro_v1").encode()
        key_material = f"{machine_id}:{salt.decode()}".encode()
        key = hashlib.pbkdf2_hmac('sha256', key_material, salt, 100000)
        return base64.urlsafe_b64encode(key)

    def _get_machine_id(self) -> str:
        """Get a unique machine identifier."""
        try:
            if platform.system() == "Windows":
                import subprocess
                result = subprocess.run(
                    ["wmic", "baseboard", "get", "serialnumber"],
                    capture_output=True, text=True, timeout=5
                )
                if result.returncode == 0:
                    lines = [l.strip() for l in result.stdout.strip().split("\n") if l.strip()]
                    if len(lines) > 1:
                        return lines[1]
                return platform.machine() + platform.processor()
            elif platform.system() == "Darwin":
                import subprocess
                result = subprocess.run(
                    ["ioreg", "-l"], capture_output=True, text=True, timeout=5
                )
                import re
                match = re.search(r'"IOPlatformSerialNumber" = "([^"]+)"', result.stdout)
                return match.group(1) if match else platform.machine()
            else:
                # Linux
                try:
                    with open("/etc/machine-id") as f:
                        return f.read().strip()
                except Exception:
                    return platform.machine()
        except Exception:
            return platform.machine() + platform.processor()

    def encrypt(self, service: str, plaintext: str) -> str:
        """Encrypt and store a secret.

        Args:
            service: Identifier for the secret (e.g., "openai_api_key")
            plaintext: The secret value to encrypt

        Returns:
            Encrypted ciphertext (base64-encoded)
        """
        ciphertext = self._fernet.encrypt(plaintext.encode()).decode()
        self._cache[service] = plaintext
        self._persist()
        return ciphertext

    def decrypt(self, service: str) -> Optional[str]:
        """Decrypt a secret.

        Args:
            service: Identifier for the secret

        Returns:
            Decrypted plaintext, or None if not found
        """
        # Check cache first
        if service in self._cache:
            return self._cache[service]

        # Load from vault
        vault = self._load_vault()
        if service not in vault:
            return None

        try:
            plaintext = self._fernet.decrypt(vault[service].encode()).decode()
            self._cache[service] = plaintext
            return plaintext
        except Exception as exc:
            log.error("failed to decrypt secret %s: %s", service, exc)
            return None

    def get(self, service: str, default: Optional[str] = None) -> Optional[str]:
        """Get a secret, returning default if not found."""
        return self.decrypt(service) or default

    def set(self, service: str, value: str) -> None:
        """Set a secret (encrypt and store)."""
        self.encrypt(service, value)

    def delete(self, service: str) -> None:
        """Delete a secret from the vault."""
        self._cache.pop(service, None)
        vault = self._load_vault()
        vault.pop(service, None)
        self._persist(vault)

    def _load_vault(self) -> dict[str, str]:
        """Load the encrypted vault from disk."""
        if not self.vault_path.exists():
            return {}
        try:
            data = self.vault_path.read_text(encoding="utf-8")
            return json.loads(data)
        except Exception as exc:
            log.error("failed to load vault: %s", exc)
            return {}

    def _persist(self, vault: Optional[dict[str, str]] = None) -> None:
        """Persist vault to disk."""
        if vault is None:
            vault = {}
        try:
            self.vault_path.parent.mkdir(parents=True, exist_ok=True)
            # Write to temp file then rename (atomic)
            tmp_path = self.vault_path.with_suffix(".tmp")
            tmp_path.write_text(json.dumps(vault), encoding="utf-8")
            tmp_path.replace(self.vault_path)
            # Set restrictive permissions
            if platform.system() != "Windows":
                os.chmod(self.vault_path, 0o600)
        except Exception as exc:
            log.error("failed to persist vault: %s", exc)

    def clear_cache(self) -> None:
        """Clear in-memory cache (secrets remain on disk)."""
        self._cache.clear()

    def rotate_key(self) -> None:
        """Rotate the encryption key (re-encrypt all secrets)."""
        old_fernet = self._fernet
        old_key = self._key
        self._key = self._derive_key()
        self._fernet = Fernet(self._key)

        # Re-encrypt all secrets
        vault = self._load_vault()
        new_vault = {}
        for service, ciphertext in vault.items():
            try:
                plaintext = old_fernet.decrypt(ciphertext.encode()).decode()
                new_vault[service] = self._fernet.encrypt(plaintext.encode()).decode()
            except Exception as exc:
                log.error("failed to rotate secret %s: %s", service, exc)
        self._persist(new_vault)
        self._cache.clear()


# Global vault instance
_vault: Optional[SecretVault] = None


def get_vault(settings: Optional[Settings] = None) -> SecretVault:
    """Get or create the global SecretVault instance."""
    global _vault
    if _vault is None:
        _vault = SecretVault(settings=settings)
    return _vault


def encrypt_secret(service: str, plaintext: str) -> str:
    """Encrypt and store a secret (convenience function)."""
    return get_vault().encrypt(service, plaintext)


def decrypt_secret(service: str) -> Optional[str]:
    """Decrypt a secret (convenience function)."""
    return get_vault().decrypt(service)


def get_secret(service: str, default: Optional[str] = None) -> Optional[str]:
    """Get a secret with default fallback (convenience function)."""
    return get_vault().get(service, default)


def set_secret(service: str, value: str) -> None:
    """Set a secret (convenience function)."""
    get_vault().set(service, value)


def delete_secret(service: str) -> None:
    """Delete a secret (convenience function)."""
    get_vault().delete(service)
