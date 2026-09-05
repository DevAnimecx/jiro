"""Enterprise compliance: SOC2 controls, data residency, retention policies.

Provides:
- SOC2 control framework mapping
- Data residency configuration (region-based storage)
- Data retention policies with auto-purge
- Compliance posture dashboard
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set


@dataclass
class SOC2Control:
    control_id: str
    domain: str
    name: str
    description: str
    status: str = "untested"
    evidence: List[str] = field(default_factory=list)
    last_tested: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "control_id": self.control_id,
            "domain": self.domain,
            "name": self.name,
            "description": self.description,
            "status": self.status,
            "evidence": self.evidence,
            "last_tested": self.last_tested,
        }


SOC2_CONTROLS = [
    SOC2Control("CC6.1", "Access Control", "Logical Access Controls",
                "System access is restricted to authorized users",
                "implemented", ["RBAC system", "API key authentication", "OIDC/SSO"]),
    SOC2Control("CC6.2", "Access Control", "User Authentication",
                "Users are authenticated before access is granted",
                "implemented", ["JWT tokens", "API key hashing", "MFA support"]),
    SOC2Control("CC6.3", "Access Control", "Role-Based Access",
                "Access is granted based on job responsibilities",
                "implemented", ["RBAC roles: viewer/editor/admin/auditor"]),
    SOC2Control("CC7.1", "System Operations", "Vulnerability Management",
                "System vulnerabilities are identified and remediated",
                "implemented", ["Dependency scanning", "Security headers"]),
    SOC2Control("CC7.2", "System Operations", "Security Monitoring",
                "Security events are monitored and anomalies detected",
                "implemented", ["Audit chain", "Anomaly detection", "Metrics"]),
    SOC2Control("CC8.1", "Change Management", "Change Control",
                "Changes to the system are authorized and tested",
                "implemented", ["Git version control", "CI/CD pipeline"]),
    SOC2Control("CC9.1", "Risk Mitigation", "Encryption at Rest",
                "Sensitive data is encrypted at rest",
                "implemented", ["AES-256-GCM encryption", "SQLCipher support"]),
    SOC2Control("CC9.2", "Risk Mitigation", "Encryption in Transit",
                "Data is encrypted during transmission",
                "implemented", ["TLS 1.2+", "HTTPS enforcement"]),
    SOC2Control("A1.2", "Availability", "Backup and Recovery",
                "Data is backed up and can be recovered",
                "partial", ["SQLite backup", "PostgreSQL replication"]),
    SOC2Control("P1.1", "Privacy", "Data Minimization",
                "Only necessary personal data is collected",
                "implemented", ["Configurable logging", "Query redaction"]),
    SOC2Control("P1.2", "Privacy", "Right to Erasure",
                "Users can request deletion of their data",
                "implemented", ["GDPR DSAR endpoint", "Data purge"]),
    SOC2Control("P1.3", "Privacy", "Data Retention",
                "Data is retained only as long as necessary",
                "implemented", ["Configurable retention periods", "Auto-purge"]),
]


@dataclass
class DataResidencyConfig:
    region: str
    storage_backend: str
    encryption_key_region: str
    allowed_regions: List[str]
    data_classification: str = "confidential"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "region": self.region,
            "storage_backend": self.storage_backend,
            "encryption_key_region": self.encryption_key_region,
            "allowed_regions": self.allowed_regions,
            "data_classification": self.data_classification,
        }


@dataclass
class RetentionPolicy:
    data_type: str
    retention_days: int
    auto_purge: bool = True
    archive_before_purge: bool = False
    legal_hold: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "data_type": self.data_type,
            "retention_days": self.retention_days,
            "auto_purge": self.auto_purge,
            "archive_before_purge": self.archive_before_purge,
            "legal_hold": self.legal_hold,
        }


DEFAULT_RETENTION_POLICIES = [
    RetentionPolicy("search_history", 90, True),
    RetentionPolicy("usage_logs", 365, True),
    RetentionPolicy("audit_chain", 730, False, True),
    RetentionPolicy("sessions", 30, True),
    RetentionPolicy("api_keys", 0, False),
    RetentionPolicy("oidc_identities", 0, False),
]


class ComplianceManager:
    def __init__(self):
        self._controls: Dict[str, SOC2Control] = {
            c.control_id: c for c in SOC2_CONTROLS
        }
        self._residency: Optional[DataResidencyConfig] = None
        self._retention_policies: Dict[str, RetentionPolicy] = {
            p.data_type: p for p in DEFAULT_RETENTION_POLICIES
        }
        self._compliance_events: List[Dict[str, Any]] = []

    def get_controls(self, domain: Optional[str] = None) -> List[Dict[str, Any]]:
        controls = list(self._controls.values())
        if domain:
            controls = [c for c in controls if c.domain == domain]
        return [c.to_dict() for c in controls]

    def get_control(self, control_id: str) -> Optional[Dict[str, Any]]:
        control = self._controls.get(control_id)
        return control.to_dict() if control else None

    def update_control_status(self, control_id: str, status: str,
                              evidence: Optional[List[str]] = None) -> bool:
        control = self._controls.get(control_id)
        if not control:
            return False
        control.status = status
        control.last_tested = time.time()
        if evidence:
            control.evidence.extend(evidence)
        return True

    def get_compliance_posture(self) -> Dict[str, Any]:
        controls = list(self._controls.values())
        implemented = sum(1 for c in controls if c.status == "implemented")
        partial = sum(1 for c in controls if c.status == "partial")
        total = len(controls)
        return {
            "total_controls": total,
            "implemented": implemented,
            "partial": partial,
            "untested": total - implemented - partial,
            "compliance_score": round((implemented / max(total, 1)) * 100, 1),
            "domains": self._get_domain_summary(controls),
        }

    def _get_domain_summary(self, controls: List[SOC2Control]) -> Dict[str, Any]:
        domains: Dict[str, Dict[str, int]] = {}
        for c in controls:
            if c.domain not in domains:
                domains[c.domain] = {"implemented": 0, "partial": 0, "total": 0}
            domains[c.domain]["total"] += 1
            if c.status == "implemented":
                domains[c.domain]["implemented"] += 1
            elif c.status == "partial":
                domains[c.domain]["partial"] += 1
        return domains

    def set_data_residency(self, config: DataResidencyConfig) -> None:
        self._residency = config

    def get_data_residency(self) -> Optional[Dict[str, Any]]:
        return self._residency.to_dict() if self._residency else None

    def set_retention_policy(self, policy: RetentionPolicy) -> None:
        self._retention_policies[policy.data_type] = policy

    def get_retention_policies(self) -> List[Dict[str, Any]]:
        return [p.to_dict() for p in self._retention_policies.values()]

    def get_retention_policy(self, data_type: str) -> Optional[Dict[str, Any]]:
        policy = self._retention_policies.get(data_type)
        return policy.to_dict() if policy else None

    def record_compliance_event(self, event_type: str, details: Dict[str, Any]) -> None:
        self._compliance_events.append({
            "event_type": event_type,
            "timestamp": time.time(),
            "details": details,
        })

    def get_compliance_events(self, limit: int = 100) -> List[Dict[str, Any]]:
        return self._compliance_events[-limit:]


_enterprise_compliance: Optional[ComplianceManager] = None


def get_enterprise_compliance() -> ComplianceManager:
    global _enterprise_compliance
    if _enterprise_compliance is None:
        _enterprise_compliance = ComplianceManager()
    return _enterprise_compliance


def reset_enterprise_compliance():
    global _enterprise_compliance
    _enterprise_compliance = None
