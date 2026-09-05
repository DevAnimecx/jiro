"""Tests for Phase 1 enterprise features: RBAC, encryption, audit chain, sessions."""

from __future__ import annotations

import json
import os
import tempfile
import time

import pytest

from jiro.config import Settings
from jiro.db import Database
from tests.integration_utils import TEST_CONFIG


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def settings():
    return Settings(raw=TEST_CONFIG.copy())


@pytest.fixture
async def db(settings):
    d = Database(settings.db_path)
    await d.connect()
    yield d
    await d.close()


# ===========================================================================
# RBAC Tests
# ===========================================================================

class TestRBACManager:
    @pytest.mark.asyncio
    async def test_grant_and_check_permission(self, db, settings):
        from jiro.rbac import RBACManager
        rbac = RBACManager(db, settings)

        await rbac.grant_permission("user1", "search:read")
        assert await rbac.has_permission("user1", "search:read") is True
        assert await rbac.has_permission("user1", "admin:write") is False

    @pytest.mark.asyncio
    async def test_revoke_permission(self, db, settings):
        from jiro.rbac import RBACManager
        rbac = RBACManager(db, settings)

        await rbac.grant_permission("user1", "search:read")
        await rbac.revoke_permission("user1", "search:read")
        assert await rbac.has_permission("user1", "search:read") is False

    @pytest.mark.asyncio
    async def test_require_permission_raises(self, db, settings):
        from jiro.rbac import RBACManager
        from jiro.errors import JiroPermissionError
        rbac = RBACManager(db, settings)

        with pytest.raises(JiroPermissionError):
            await rbac.require_permission("user1", "admin:write")

    @pytest.mark.asyncio
    async def test_set_role_permissions(self, db, settings):
        from jiro.rbac import RBACManager, ROLE_TEMPLATES
        rbac = RBACManager(db, settings)

        await rbac.set_role_permissions("user1", "viewer")
        perms = await rbac.get_permissions("user1")
        assert perms == ROLE_TEMPLATES["viewer"]

    @pytest.mark.asyncio
    async def test_set_role_replaces_existing(self, db, settings):
        from jiro.rbac import RBACManager, ROLE_TEMPLATES
        rbac = RBACManager(db, settings)

        await rbac.grant_permission("user1", "billing:write")
        await rbac.set_role_permissions("user1", "viewer")
        perms = await rbac.get_permissions("user1")
        assert perms == ROLE_TEMPLATES["viewer"]
        assert "billing:write" not in perms

    @pytest.mark.asyncio
    async def test_list_identities_with_permission(self, db, settings):
        from jiro.rbac import RBACManager
        rbac = RBACManager(db, settings)

        await rbac.grant_permission("user1", "admin:read")
        await rbac.grant_permission("user2", "admin:read")
        await rbac.grant_permission("user3", "search:read")

        admins = await rbac.list_identities_with_permission("admin:read")
        assert set(admins) == {"user1", "user2"}

    @pytest.mark.asyncio
    async def test_grant_duplicate_permission_idempotent(self, db, settings):
        from jiro.rbac import RBACManager
        rbac = RBACManager(db, settings)

        await rbac.grant_permission("user1", "search:read")
        await rbac.grant_permission("user1", "search:read")
        perms = await rbac.get_permissions("user1")
        assert perms == {"search:read"}

    @pytest.mark.asyncio
    async def test_cache_invalidation_on_grant(self, db, settings):
        from jiro.rbac import RBACManager
        rbac = RBACManager(db, settings)

        # Populate cache
        await rbac.get_permissions("user1")
        assert "user1" in rbac._cache

        # Grant should invalidate cache
        await rbac.grant_permission("user1", "search:read")
        assert "user1" not in rbac._cache

    def test_role_templates_cover_core_permissions(self):
        from jiro.rbac import ROLE_TEMPLATES, Permission
        # Admin has all permissions except billing (which is billing-only)
        admin = ROLE_TEMPLATES["admin"]
        assert Permission.SEARCH_READ in admin
        assert Permission.ADMIN_WRITE in admin
        assert Permission.AUDIT_READ in admin
        assert Permission.KEY_MANAGE in admin


# ===========================================================================
# Encryption Tests
# ===========================================================================

class TestEncryptionManager:
    def test_encrypt_decrypt_roundtrip(self, settings):
        from jiro.encryption import EncryptionManager
        enc = EncryptionManager(settings)

        original = "sensitive-api-key-12345"
        encrypted = enc.encrypt(original)
        assert encrypted != original
        decrypted = enc.decrypt(encrypted)
        assert decrypted == original

    def test_encrypt_empty_string(self, settings):
        from jiro.encryption import EncryptionManager
        enc = EncryptionManager(settings)

        assert enc.encrypt("") == ""
        assert enc.decrypt("") == ""

    def test_encrypt_produces_different_ciphertext(self, settings):
        from jiro.encryption import EncryptionManager
        enc = EncryptionManager(settings)

        # Same plaintext should produce different ciphertext (random nonce)
        c1 = enc.encrypt("hello")
        c2 = enc.encrypt("hello")
        assert c1 != c2
        # But both should decrypt to the same value
        assert enc.decrypt(c1) == "hello"
        assert enc.decrypt(c2) == "hello"

    def test_encrypt_dict_fields(self, settings):
        from jiro.encryption import EncryptionManager
        enc = EncryptionManager(settings)

        data = {"key": "secret123", "name": "test", "token": "abc"}
        encrypted = enc.encrypt_dict(data, ["key", "token"])
        assert encrypted["key"] != "secret123"
        assert encrypted["token"] != "abc"
        assert encrypted["name"] == "test"  # Not encrypted

    def test_decrypt_dict_fields(self, settings):
        from jiro.encryption import EncryptionManager
        enc = EncryptionManager(settings)

        data = {"key": "secret123", "name": "test"}
        encrypted = enc.encrypt_dict(data, ["key"])
        decrypted = enc.decrypt_dict(encrypted, ["key"])
        assert decrypted["key"] == "secret123"
        assert decrypted["name"] == "test"

    def test_decrypt_dict_handles_legacy_unencrypted(self, settings):
        from jiro.encryption import EncryptionManager
        enc = EncryptionManager(settings)

        data = {"key": "plain-text-legacy"}
        decrypted = enc.decrypt_dict(data, ["key"])
        # Should pass through gracefully
        assert decrypted["key"] == "plain-text-legacy"

    def test_is_enabled_false_by_default(self, settings):
        from jiro.encryption import EncryptionManager
        enc = EncryptionManager(settings)
        assert enc.is_enabled() is False

    def test_get_encrypted_fields_default(self, settings):
        from jiro.encryption import EncryptionManager
        enc = EncryptionManager(settings)
        fields = enc.get_encrypted_fields()
        assert "api_keys" in fields
        assert "usage" in fields

    def test_get_sqlcipher_key(self, settings):
        from jiro.encryption import EncryptionManager
        enc = EncryptionManager(settings)
        key = enc.get_sqlcipher_key()
        assert isinstance(key, str)
        assert len(key) == 64  # 256 bits = 64 hex chars

    def test_key_derivation_deterministic(self, settings):
        from jiro.encryption import EncryptionManager
        enc1 = EncryptionManager(settings)
        enc2 = EncryptionManager(settings)
        assert enc1._key == enc2._key


# ===========================================================================
# Audit Chain Tests
# ===========================================================================

class TestAuditChain:
    def test_append_entry(self, settings, tmp_path):
        from jiro.audit_chain import AuditChain
        settings.raw["audit"] = {"chain_log_path": str(tmp_path / "audit.jsonl")}
        chain = AuditChain(settings)

        entry = chain.append(
            event_type="auth",
            actor="user1",
            action="login",
            target="api-key-123",
            result="success",
            ip_address="127.0.0.1",
            user_agent="test",
        )
        assert entry.event_type == "auth"
        assert entry.actor == "user1"
        assert entry.entry_hash != ""

    def test_chain_hash_links(self, settings, tmp_path):
        from jiro.audit_chain import AuditChain
        settings.raw["audit"] = {"chain_log_path": str(tmp_path / "audit.jsonl")}
        chain = AuditChain(settings)

        e1 = chain.append("auth", "u1", "login", "key1", "success")
        e2 = chain.append("auth", "u1", "logout", "key1", "success")
        assert e2.previous_hash == e1.entry_hash

    def test_verify_chain_valid(self, settings, tmp_path):
        from jiro.audit_chain import AuditChain
        settings.raw["audit"] = {"chain_log_path": str(tmp_path / "audit.jsonl")}
        chain = AuditChain(settings)

        chain.append("auth", "u1", "login", "key1", "success")
        chain.append("auth", "u1", "logout", "key1", "success")
        chain._flush()

        valid, broken = chain.verify_chain()
        assert valid is True
        assert broken is None

    def test_verify_tampered_chain(self, settings, tmp_path):
        from jiro.audit_chain import AuditChain
        settings.raw["audit"] = {"chain_log_path": str(tmp_path / "audit.jsonl")}
        chain = AuditChain(settings)

        chain.append("auth", "u1", "login", "key1", "success")
        chain.append("auth", "u1", "logout", "key1", "success")
        chain._flush()

        # Tamper with the log file
        log_path = str(tmp_path / "audit.jsonl")
        with open(log_path, "r") as f:
            lines = f.readlines()
        # Modify the second entry
        entry = json.loads(lines[1])
        entry["action"] = "TAMPERED"
        lines[1] = json.dumps(entry) + "\n"
        with open(log_path, "w") as f:
            f.writelines(lines)

        valid, broken = chain.verify_chain()
        assert valid is False
        assert broken is not None

    def test_verify_empty_chain(self, settings, tmp_path):
        from jiro.audit_chain import AuditChain
        settings.raw["audit"] = {"chain_log_path": str(tmp_path / "audit.jsonl")}
        chain = AuditChain(settings)

        valid, broken = chain.verify_chain()
        assert valid is True

    def test_flush_on_batch_size(self, settings, tmp_path):
        from jiro.audit_chain import AuditChain
        settings.raw["audit"] = {
            "chain_log_path": str(tmp_path / "audit.jsonl"),
            "chain_batch_size": 3,
        }
        chain = AuditChain(settings)

        for i in range(3):
            chain.append("test", f"u{i}", "action", "target", "success")

        # Should have flushed after 3 entries
        assert len(chain._entries) == 0
        assert os.path.exists(str(tmp_path / "audit.jsonl"))

    def test_get_stats(self, settings, tmp_path):
        from jiro.audit_chain import AuditChain
        settings.raw["audit"] = {"chain_log_path": str(tmp_path / "audit.jsonl")}
        chain = AuditChain(settings)

        chain.append("test", "u1", "action", "target", "success")
        chain._flush()

        stats = chain.get_stats()
        assert stats["entries"] == 1
        assert stats["file_size"] > 0

    def test_export_signed(self, settings, tmp_path):
        from jiro.audit_chain import AuditChain
        settings.raw["audit"] = {"chain_log_path": str(tmp_path / "audit.jsonl")}
        chain = AuditChain(settings)

        chain.append("test", "u1", "action", "target", "success")
        export_path = str(tmp_path / "export.json")
        file_hash = chain.export_signed(export_path, sign_key="test-secret")

        assert os.path.exists(export_path)
        assert len(file_hash) == 64  # SHA-256

        with open(export_path) as f:
            data = json.load(f)
        assert "signature" in data
        assert data["entry_count"] == 1

    def test_rotation_on_large_file(self, settings, tmp_path):
        from jiro.audit_chain import AuditChain
        settings.raw["audit"] = {
            "chain_log_path": str(tmp_path / "audit.jsonl"),
            "chain_max_file_size_mb": 0,
        }
        chain = AuditChain(settings)

        # First entry creates the file
        chain.append("test", "u1", "action", "target", "success")
        chain._flush()
        # Write the file bigger than max size to trigger rotation
        log_path = str(tmp_path / "audit.jsonl")
        with open(log_path, "a") as f:
            f.write("x" * 1024)
        # Now append another entry - should trigger rotation
        chain.append("test", "u2", "action", "target", "success")
        chain._flush()
        rotated_files = [f for f in os.listdir(tmp_path) if f.startswith("audit.jsonl.")]
        assert len(rotated_files) > 0

    def test_close_flushes(self, settings, tmp_path):
        from jiro.audit_chain import AuditChain
        settings.raw["audit"] = {"chain_log_path": str(tmp_path / "audit.jsonl")}
        chain = AuditChain(settings)

        chain.append("test", "u1", "action", "target", "success")
        assert len(chain._entries) == 1
        chain.close()
        assert len(chain._entries) == 0


# ===========================================================================
# Session Manager Tests
# ===========================================================================

class TestSessionManager:
    @pytest.mark.asyncio
    async def test_create_and_validate_session(self, db, settings):
        from jiro.session import SessionManager
        sm = SessionManager(settings, db)

        await sm.create_session("key1", "jti-1", time.time() + 3600)
        assert await sm.is_session_valid("jti-1") is True

    @pytest.mark.asyncio
    async def test_revoke_session(self, db, settings):
        from jiro.session import SessionManager
        sm = SessionManager(settings, db)

        await sm.create_session("key1", "jti-1", time.time() + 3600)
        await sm.revoke_session("jti-1")
        assert await sm.is_session_valid("jti-1") is False

    @pytest.mark.asyncio
    async def test_revoke_all_sessions(self, db, settings):
        from jiro.session import SessionManager
        sm = SessionManager(settings, db)

        await sm.create_session("key1", "jti-1", time.time() + 3600)
        await sm.create_session("key1", "jti-2", time.time() + 3600)
        count = await sm.revoke_all_sessions("key1")
        assert count == 2
        assert await sm.is_session_valid("jti-1") is False
        assert await sm.is_session_valid("jti-2") is False

    @pytest.mark.asyncio
    async def test_get_active_sessions(self, db, settings):
        from jiro.session import SessionManager
        sm = SessionManager(settings, db)

        await sm.create_session("key1", "jti-1", time.time() + 3600)
        await sm.create_session("key1", "jti-2", time.time() + 3600)
        active = await sm.get_active_sessions("key1")
        assert len(active) == 2

    @pytest.mark.asyncio
    async def test_cleanup_expired(self, db, settings):
        from jiro.session import SessionManager
        sm = SessionManager(settings, db)

        # Create expired session
        await sm.create_session("key1", "jti-expired", time.time() - 100)
        # Create valid session
        await sm.create_session("key1", "jti-valid", time.time() + 3600)

        count = await sm.cleanup_expired()
        assert count >= 1
        assert await sm.is_session_valid("jti-expired") is False

    @pytest.mark.asyncio
    async def test_nonexistent_session_invalid(self, db, settings):
        from jiro.session import SessionManager
        sm = SessionManager(settings, db)

        assert await sm.is_session_valid("nonexistent-jti") is False


# ===========================================================================
# Config Enterprise Settings Tests
# ===========================================================================

class TestEnterpriseConfig:
    def test_oidc_config_defaults(self, settings):
        assert settings.get("auth.oidc.enabled", False) is False

    def test_rbac_config_defaults(self, settings):
        assert settings.get("auth.rbac.enabled", True) is True
        assert settings.get("auth.rbac.default_role", "editor") == "editor"

    def test_session_config_defaults(self, settings):
        assert settings.get("auth.session.enabled", True) is True

    def test_encryption_config_defaults(self, settings):
        assert settings.get("security.encryption_enabled", False) is False

    def test_compliance_config_defaults(self, settings):
        assert settings.get("compliance.dsar_enabled", True) is True
        assert settings.get("compliance.retention_days", 365) == 365


# ===========================================================================
# DB Schema Tests (new tables exist)
# ===========================================================================

class TestEnterpriseSchema:
    @pytest.mark.asyncio
    async def test_role_permissions_table_exists(self, db):
        tables = await db.fetchall(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
        table_names = {t["name"] for t in tables}
        assert "role_permissions" in table_names

    @pytest.mark.asyncio
    async def test_sessions_table_exists(self, db):
        tables = await db.fetchall(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
        table_names = {t["name"] for t in tables}
        assert "sessions" in table_names

    @pytest.mark.asyncio
    async def test_oidc_identities_table_exists(self, db):
        tables = await db.fetchall(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
        table_names = {t["name"] for t in tables}
        assert "oidc_identities" in table_names
