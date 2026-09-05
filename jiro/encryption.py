"""Encryption at rest utilities for Jiro.

Provides:
- AES-256-GCM encryption for sensitive database fields
- Key derivation from configurable secret
- Transparent encrypt/decrypt for DB operations
- Support for SQLite (application-level) and PostgreSQL (pgcrypto)
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
from typing import Optional

from jiro.config import Settings
from jiro.log import get_logger

log = get_logger("jiro.encryption")

# Encryption algorithms
ALGORITHM_AES_GCM = "aes-256-gcm"
ALGORITHM_SQLCIPHER = "sqlcipher"


class EncryptionError(Exception):
    """Raised when encryption/decryption fails."""
    pass


class EncryptionManager:
    """Manages encryption at rest for sensitive data.
    
    Supports:
    - AES-256-GCM for application-level encryption
    - SQLCipher for transparent SQLite encryption
    - pgcrypto for PostgreSQL column-level encryption
    """
    
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._key = self._derive_key()
        self._algorithm = settings.get("security.encryption_algorithm", ALGORITHM_AES_GCM)
    
    def _derive_key(self) -> bytes:
        """Derive a 256-bit encryption key from settings."""
        secret = self.settings.get("security.encryption_key", "")
        if not secret:
            # Use JWT secret as fallback (if available)
            secret = self.settings.jwt_secret or ""
        if not secret or len(secret) < 32:
            raise EncryptionError(
                "encryption key too short: set security.encryption_key to >= 32 bytes"
            )
        # Use PBKDF2 to derive a 256-bit key
        salt = b"jiro-encryption-salt-v1"
        return hashlib.pbkdf2_hmac("sha256", secret.encode("utf-8"), salt, 100000, 32)
    
    def encrypt(self, plaintext: str) -> str:
        """Encrypt a string using AES-256-GCM.
        
        Returns:
            Base64-encoded ciphertext with IV and tag prepended
        """
        if not plaintext:
            return ""
        
        try:
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM
            import os
            
            aesgcm = AESGCM(self._key)
            nonce = os.urandom(12)
            ciphertext = aesgcm.encrypt(nonce, plaintext.encode("utf-8"), None)
            
            # Prepend nonce to ciphertext
            result = nonce + ciphertext
            import base64
            return base64.b64encode(result).decode("utf-8")
        except ImportError:
            raise EncryptionError("cryptography package required: pip install cryptography")
        except Exception as exc:
            raise EncryptionError(f"encryption failed: {exc}")
    
    def decrypt(self, ciphertext_b64: str) -> str:
        """Decrypt a base64-encoded ciphertext.
        
        Returns:
            Decrypted plaintext string
        """
        if not ciphertext_b64:
            return ""
        
        try:
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM
            import base64
            
            raw = base64.b64decode(ciphertext_b64.encode("utf-8"))
            nonce = raw[:12]
            ciphertext = raw[12:]
            
            aesgcm = AESGCM(self._key)
            plaintext = aesgcm.decrypt(nonce, ciphertext, None)
            return plaintext.decode("utf-8")
        except ImportError:
            raise EncryptionError("cryptography package required: pip install cryptography")
        except Exception as exc:
            raise EncryptionError(f"decryption failed: {exc}")
    
    def encrypt_dict(self, data: Dict[str, Any], fields: list) -> Dict[str, Any]:
        """Encrypt specific fields in a dictionary.
        
        Args:
            data: Dictionary containing data to encrypt
            fields: List of field names to encrypt
            
        Returns:
            Dictionary with encrypted fields
        """
        result = data.copy()
        for field in fields:
            if field in result and result[field]:
                result[field] = self.encrypt(str(result[field]))
        return result
    
    def decrypt_dict(self, data: Dict[str, Any], fields: list) -> Dict[str, Any]:
        """Decrypt specific fields in a dictionary.
        
        Args:
            data: Dictionary containing encrypted data
            fields: List of field names to decrypt
            
        Returns:
            Dictionary with decrypted fields
        """
        result = data.copy()
        for field in fields:
            if field in result and result[field]:
                try:
                    result[field] = self.decrypt(str(result[field]))
                except EncryptionError:
                    # Field might not be encrypted (legacy data)
                    pass
        return result
    
    def get_sqlcipher_key(self) -> str:
        """Get the SQLCipher encryption key for database encryption.
        
        Returns:
            Hex-encoded 256-bit key for SQLCipher PRAGMA key
        """
        return self._key.hex()
    
    def is_enabled(self) -> bool:
        """Check if encryption at rest is enabled."""
        return self.settings.get("security.encryption_enabled", False)
    
    def get_encrypted_fields(self) -> Dict[str, list]:
        """Get mapping of tables to encrypted fields."""
        return self.settings.get("security.encrypted_fields", {
            "api_keys": ["key_hash"],
            "usage": ["query"],
            "oidc_identities": ["email"],
        })


# Global instance
_encryption: Optional[EncryptionManager] = None


def get_encryption(settings: Optional[Settings] = None) -> EncryptionManager:
    """Get or create the global EncryptionManager instance."""
    global _encryption
    if _encryption is None:
        _encryption = EncryptionManager(settings or Settings.load())
    return _encryption
