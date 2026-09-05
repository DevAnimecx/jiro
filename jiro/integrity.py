"""Runtime integrity verification for Jiro.

Provides self-hash verification to detect tampering, modified files,
or unauthorized changes to the installed package.
"""

from __future__ import annotations

import hashlib
import importlib
import logging
import os
import sys
from pathlib import Path
from typing import Dict, List, Optional, Set

log = logging.getLogger("jiro.integrity")


class IntegrityError(Exception):
    """Raised when package integrity check fails."""
    pass


class IntegrityVerifier:
    """Verify package integrity at runtime.

    Computes and verifies hashes of critical source files
    to detect tampering or unauthorized modifications.
    """

    def __init__(self, package_path: Optional[Path] = None, manifest: Optional[Dict[str, str]] = None) -> None:
        self.package_path = package_path or Path(importlib.import_module("jiro").__file__).parent
        self.manifest = manifest or self._load_manifest()
        self._failures: List[str] = []

    def _load_manifest(self) -> Dict[str, str]:
        """Load integrity manifest (expected hashes)."""
        manifest_path = self.package_path / "integrity_manifest.json"
        if not manifest_path.exists():
            return {}
        try:
            import json
            return json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception as exc:
            log.error("failed to load integrity manifest: %s", exc)
            return {}

    def _compute_hash(self, file_path: Path) -> str:
        """Compute SHA-256 hash of a file."""
        h = hashlib.sha256()
        with open(file_path, "rb") as f:
            while chunk := f.read(8192):
                h.update(chunk)
        return h.hexdigest()[:16]  # first 16 chars for performance

    def verify(self) -> bool:
        """Verify all critical files in the manifest."""
        if not self.manifest:
            log.warning("no integrity manifest found, skipping verification")
            return True

        self._failures = []
        critical_files = self._get_critical_files()

        for rel_path in critical_files:
            file_path = self.package_path / rel_path
            if not file_path.exists():
                self._failures.append(f"missing: {rel_path}")
                continue

            actual_hash = self._compute_hash(file_path)
            expected_hash = self.manifest.get(rel_path)
            if expected_hash and actual_hash != expected_hash:
                self._failures.append(
                    f"tampered: {rel_path} (expected {expected_hash}, got {actual_hash})"
                )

        if self._failures:
            log.error("integrity check failed: %s", self._failures)
            return False

        log.info("integrity check passed")
        return True

    def _get_critical_files(self) -> List[str]:
        """Get list of critical files to verify."""
        return list(self.manifest.keys())

    def get_failures(self) -> List[str]:
        """Get list of integrity failures."""
        return self._failures

    def record_violation(self) -> None:
        """Record an integrity violation (e.g., to logging/remote)."""
        log.warning("integrity violation recorded: %s", self._failures)
        # Could also send to monitoring/logging service


# Module-level cache to avoid repeated verification
_verifier: Optional[IntegrityVerifier] = None
_verified: bool = False
_verification_failed: bool = False


def verify_package_integrity() -> bool:
    """Verify package integrity (cached result)."""
    global _verified, _verification_failed
    if _verified:
        return True
    if _verification_failed:
        return False

    global _verifier
    if _verifier is None:
        _verifier = IntegrityVerifier()

    result = _verifier.verify()
    if result:
        _verified = True
    else:
        _verification_failed = True
        _verifier.record_violation()
    return result


def ensure_integrity() -> None:
    """Raise IntegrityError if package integrity check fails."""
    if not verify_package_integrity():
        raise IntegrityError(
            "Package integrity verification failed. "
            "The Jiro package may have been tampered with or modified. "
            "Please reinstall from the official source: pip install --force-reinstall jiro-search"
        )
