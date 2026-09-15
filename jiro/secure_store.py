"""Secure credential storage for Jiro CLI.

Stores API key and user info encrypted on disk using machine-derived keys.
Falls back to filesystem permissions when encryption is unavailable.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import platform
import sys
from pathlib import Path
from typing import Optional


CREDENTIALS_PATH = Path("~/.jiro/credentials.enc").expanduser()
CLOUD_CONFIG_PATH = Path("~/.jiro/cloud.json").expanduser()  # Legacy fallback


def _machine_fingerprint() -> bytes:
    """Derive a machine-specific fingerprint for encryption key."""
    parts = [
        platform.node(),       # hostname
        os.getlogin() if hasattr(os, "getlogin") else "user",
        sys.platform,          # os type
        "jiro-secure-store-v1",
    ]
    return hashlib.sha256("|".join(parts).encode()).digest()


def _derive_key() -> bytes:
    """Derive a 32-byte encryption key from machine fingerprint."""
    fp = _machine_fingerprint()
    # Use PBKDF2-like derivation with multiple rounds
    key = fp
    for _ in range(10000):
        key = hashlib.sha256(key + fp).digest()
    return key


def _encrypt(data: str) -> bytes:
    """Encrypt data using AES-256-GCM if available, else base64."""
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        key = _derive_key()
        nonce = os.urandom(12)
        aesgcm = AESGCM(key)
        ciphertext = aesgcm.encrypt(nonce, data.encode(), None)
        # Prefix with nonce: nonce(12) + ciphertext
        return b"enc:" + nonce + ciphertext
    except ImportError:
        # Fallback: base64 encode (protection via filesystem permissions only)
        return b"b64:" + base64.b64encode(data.encode())


def _decrypt(raw: bytes) -> Optional[str]:
    """Decrypt data."""
    if not raw:
        return None

    prefix = raw[:4]

    if prefix == b"enc:":
        try:
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM
            key = _derive_key()
            nonce = raw[4:16]
            ciphertext = raw[16:]
            aesgcm = AESGCM(key)
            plaintext = aesgcm.decrypt(nonce, ciphertext, None)
            return plaintext.decode()
        except Exception:
            return None

    if prefix == b"b64:":
        try:
            return base64.b64decode(raw[4:]).decode()
        except Exception:
            return None

    # Unknown format — try raw JSON (legacy plaintext)
    try:
        return raw.decode()
    except Exception:
        return None


def _ensure_dir() -> None:
    """Ensure ~/.jiro directory exists."""
    CREDENTIALS_PATH.parent.mkdir(parents=True, exist_ok=True)


def _set_permissions(path: Path) -> None:
    """Set restrictive file permissions (Unix only)."""
    if sys.platform != "win32":
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass


def save_credentials(data: dict) -> None:
    """Save credentials to encrypted file."""
    _ensure_dir()
    plaintext = json.dumps(data, indent=2)
    encrypted = _encrypt(plaintext)
    CREDENTIALS_PATH.write_bytes(encrypted)
    _set_permissions(CREDENTIALS_PATH)

    # Remove legacy plaintext config if it exists
    if CLOUD_CONFIG_PATH.exists():
        try:
            CLOUD_CONFIG_PATH.unlink()
        except OSError:
            pass


def load_credentials() -> Optional[dict]:
    """Load credentials from encrypted file."""
    # Try encrypted file first
    if CREDENTIALS_PATH.exists():
        try:
            raw = CREDENTIALS_PATH.read_bytes()
            plaintext = _decrypt(raw)
            if plaintext:
                return json.loads(plaintext)
        except Exception:
            pass

    # Fallback: legacy plaintext config
    if CLOUD_CONFIG_PATH.exists():
        try:
            data = json.loads(CLOUD_CONFIG_PATH.read_text(encoding="utf-8"))
            # Migrate to encrypted storage
            save_credentials(data)
            return data
        except Exception:
            pass

    return None


def clear_credentials() -> bool:
    """Remove stored credentials. Returns True if credentials existed."""
    found = False
    if CREDENTIALS_PATH.exists():
        try:
            CREDENTIALS_PATH.unlink()
            found = True
        except OSError:
            pass
    if CLOUD_CONFIG_PATH.exists():
        try:
            CLOUD_CONFIG_PATH.unlink()
            found = True
        except OSError:
            pass
    return found


def is_configured() -> bool:
    """Check if credentials are configured."""
    data = load_credentials()
    return data is not None and bool(data.get("api_key"))
