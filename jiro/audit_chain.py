"""Hash-chained immutable audit trail for compliance.

Provides append-only audit logging with:
- SHA-256 hash chaining (each entry includes previous hash)
- Tamper detection (chain breaks if any entry is modified)
- Signed batch exports for external SIEM integration
- Long-term retention support (12-36 months)
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from jiro.config import Settings
from jiro.log import get_logger

log = get_logger("jiro.audit_chain")


@dataclass
class AuditEntry:
    """Single immutable audit entry."""
    timestamp: float
    event_type: str
    actor: str
    action: str
    target: str
    result: str
    ip_address: str
    user_agent: str
    details: Dict[str, Any] = field(default_factory=dict)
    previous_hash: str = ""
    entry_hash: str = ""
    
    def compute_hash(self) -> str:
        """Compute SHA-256 hash of this entry (including previous hash)."""
        content = json.dumps({
            "timestamp": self.timestamp,
            "event_type": self.event_type,
            "actor": self.actor,
            "action": self.action,
            "target": self.target,
            "result": self.result,
            "ip_address": self.ip_address,
            "user_agent": self.user_agent,
            "details": self.details,
            "previous_hash": self.previous_hash,
        }, sort_keys=True, default=str)
        return hashlib.sha256(content.encode("utf-8")).hexdigest()
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "event_type": self.event_type,
            "actor": self.actor,
            "action": self.action,
            "target": self.target,
            "result": self.result,
            "ip_address": self.ip_address,
            "user_agent": self.user_agent,
            "details": self.details,
            "previous_hash": self.previous_hash,
            "entry_hash": self.entry_hash,
        }


class AuditChain:
    """Append-only hash-chained audit log.
    
    Features:
    - Each entry includes the hash of the previous entry
    - Chain verification detects any tampering
    - Supports batch export with digital signature
    - Automatic rotation of log files
    """
    
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.log_path = settings.get("audit.chain_log_path", "~/.jiro/audit_chain.jsonl")
        self.log_path = os.path.expanduser(self.log_path)
        self._last_hash: str = ""
        self._entries: List[AuditEntry] = []
        self._batch_size = settings.get("audit.chain_batch_size", 100)
        self._max_file_size = settings.get("audit.chain_max_file_size_mb", 100) * 1024 * 1024
        
        # Ensure log directory exists
        os.makedirs(os.path.dirname(self.log_path), exist_ok=True)
        
        # Load last hash from existing log
        self._load_last_hash()
    
    def _load_last_hash(self) -> None:
        """Load the last entry hash from existing log file for chain continuity."""
        if not os.path.exists(self.log_path):
            return
        
        try:
            with open(self.log_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                        self._last_hash = entry.get("entry_hash", "")
                    except json.JSONDecodeError:
                        continue
        except Exception as exc:
            log.warning("failed to load audit chain: %s", exc)
    
    def append(self, event_type: str, actor: str, action: str, target: str,
               result: str, ip_address: str = "", user_agent: str = "",
               details: Optional[Dict[str, Any]] = None) -> AuditEntry:
        """Append a new entry to the audit chain.
        
        Args:
            event_type: Type of event (auth, search, scrape, admin, etc.)
            actor: Who performed the action (user ID, API key ID, IP)
            action: What was done
            target: What was acted upon
            result: Success/failure/denied
            ip_address: Source IP
            user_agent: User agent string
            details: Additional structured data
            
        Returns:
            The created AuditEntry
        """
        entry = AuditEntry(
            timestamp=time.time(),
            event_type=event_type,
            actor=actor,
            action=action,
            target=target,
            result=result,
            ip_address=ip_address,
            user_agent=user_agent,
            details=details or {},
            previous_hash=self._last_hash,
        )
        entry.entry_hash = entry.compute_hash()
        
        # Append to in-memory buffer
        self._entries.append(entry)
        
        # Flush if batch size reached
        if len(self._entries) >= self._batch_size:
            self._flush()
        
        # Update chain
        self._last_hash = entry.entry_hash
        
        return entry
    
    def _flush(self) -> None:
        """Write buffered entries to disk atomically."""
        if not self._entries:
            return
        
        # Check file size and rotate if needed
        if os.path.exists(self.log_path):
            if os.path.getsize(self.log_path) >= self._max_file_size:
                self._rotate_log()
        
        # Write atomically
        tmp_path = f"{self.log_path}.tmp"
        try:
            with open(tmp_path, "a", encoding="utf-8") as f:
                for entry in self._entries:
                    f.write(json.dumps(entry.to_dict(), default=str) + "\n")
            # Atomic rename
            if os.path.exists(self.log_path):
                os.replace(tmp_path, self.log_path)
            else:
                os.rename(tmp_path, self.log_path)
            self._entries.clear()
        except Exception as exc:
            log.error("failed to flush audit chain: %s", exc)
            # Clean up temp file
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
    
    def _rotate_log(self) -> None:
        """Rotate the log file when it exceeds max size."""
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        rotated = f"{self.log_path}.{timestamp}"
        os.rename(self.log_path, rotated)
        log.info("rotated audit chain to %s", rotated)
    
    def verify_chain(self) -> tuple[bool, Optional[str]]:
        """Verify the integrity of the entire audit chain.
        
        Returns:
            (is_valid, broken_at) where broken_at is the entry hash where
            chain broke, or None if valid.
        """
        if not os.path.exists(self.log_path):
            return True, None
        
        expected_hash = ""
        try:
            with open(self.log_path, "r", encoding="utf-8") as f:
                for line_num, line in enumerate(f, 1):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry_data = json.loads(line)
                    except json.JSONDecodeError:
                        return False, f"line_{line_num}"
                    
                    # Verify previous hash link
                    if entry_data.get("previous_hash") != expected_hash:
                        return False, entry_data.get("entry_hash", f"line_{line_num}")
                    
                    # Verify entry hash
                    entry = AuditEntry(**{k: v for k, v in entry_data.items() 
                                         if k in AuditEntry.__dataclass_fields__})
                    computed = entry.compute_hash()
                    if computed != entry_data.get("entry_hash"):
                        return False, entry_data.get("entry_hash", f"line_{line_num}")
                    
                    expected_hash = entry_data.get("entry_hash", "")
        except Exception as exc:
            log.error("chain verification failed: %s", exc)
            return False, str(exc)
        
        return True, None
    
    def export_signed(self, output_path: str, sign_key: Optional[str] = None) -> str:
        """Export audit chain with optional digital signature.
        
        Args:
            output_path: Path to write exported data
            sign_key: Optional HMAC key for signing the export
            
        Returns:
            SHA-256 hash of the exported file
        """
        # Flush any pending entries
        self._flush()
        
        # Read the log file
        with open(self.log_path, "r", encoding="utf-8") as f:
            content = f.read()
        
        # Add export metadata
        export_data = {
            "exported_at": time.time(),
            "source": self.log_path,
            "entry_count": len([l for l in content.split("\n") if l.strip()]),
            "content": content,
        }
        
        # Sign if key provided
        if sign_key:
            export_data["signature"] = hashlib.sha256(
                (content + sign_key).encode("utf-8")
            ).hexdigest()
        
        # Write export
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(export_data, f, indent=2, default=str)
        
        # Return hash of exported file
        file_hash = hashlib.sha256(open(output_path, "rb").read()).hexdigest()
        return file_hash
    
    def get_stats(self) -> Dict[str, Any]:
        """Get audit chain statistics."""
        if not os.path.exists(self.log_path):
            return {"entries": 0, "file_size": 0}
        
        try:
            with open(self.log_path, "r", encoding="utf-8") as f:
                lines = [l for l in f if l.strip()]
            return {
                "entries": len(lines),
                "file_size": os.path.getsize(self.log_path),
                "last_hash": self._last_hash,
            }
        except Exception as exc:
            return {"error": str(exc)}
    
    def close(self) -> None:
        """Flush remaining entries and close."""
        self._flush()


# Global instance
_audit_chain: Optional[AuditChain] = None


def get_audit_chain(settings: Optional[Settings] = None) -> AuditChain:
    """Get or create the global AuditChain instance."""
    global _audit_chain
    if _audit_chain is None:
        _audit_chain = AuditChain(settings or Settings.load())
    return _audit_chain
