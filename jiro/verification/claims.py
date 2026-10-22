"""Structured claim model for verified reasoning.

Provides a consistent representation for significant claims with explicit
provenance, verification status, and evidence mapping.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any, Dict, List, Optional, Set

from jiro.log import get_logger
from jiro.runtime.validation import Field, TaskSchema

log = get_logger("jiro.verification.claims")


class ClaimType(str, Enum):
    """Type of claim based on its epistemic status."""

    DIRECTLY_OBSERVED = "directly_observed"       # Direct measurement/observation
    SOURCE_ASSERTION = "source_assertion"         # Asserted by a cited source
    DERIVED_CALCULATION = "derived_calculation"   # Computed from other claims
    INFERRED_EVIDENCE = "inferred_evidence"       # Inferred from evidence
    HYPOTHESIS = "hypothesis"                     # Tentative, under investigation
    UNRESOLVED = "unresolved"                     # Insufficient evidence either way
    CONTRADICTED = "contradicted"                 # Evidence conflicts


class VerificationStatus(str, Enum):
    """Verification status with documented transitions."""

    PENDING = "pending"                           # Not yet evaluated
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"  # Not enough evidence
    WEAK_SUPPORT = "weak_support"                 # Some evidence, low confidence
    MODERATE_SUPPORT = "moderate_support"         # Multiple sources, reasonable confidence
    STRONG_SUPPORT = "strong_support"             # Multiple independent sources, high confidence
    CONTRADICTED = "contradicted"                 # Evidence directly conflicts
    VERIFIED = "verified"                         # Meets verification criteria

    # Terminal states
    REJECTED = "rejected"                         # Explicitly rejected with reason
    SUPERSEDED = "superseded"                     # Replaced by newer claim


# Valid status transitions (from -> allowed to)
STATUS_TRANSITIONS: Dict[VerificationStatus, Set[VerificationStatus]] = {
    VerificationStatus.PENDING: {
        VerificationStatus.INSUFFICIENT_EVIDENCE,
        VerificationStatus.WEAK_SUPPORT,
        VerificationStatus.MODERATE_SUPPORT,
        VerificationStatus.STRONG_SUPPORT,
        VerificationStatus.CONTRADICTED,
        VerificationStatus.REJECTED,
    },
    VerificationStatus.INSUFFICIENT_EVIDENCE: {
        VerificationStatus.WEAK_SUPPORT,
        VerificationStatus.MODERATE_SUPPORT,
        VerificationStatus.STRONG_SUPPORT,
        VerificationStatus.CONTRADICTED,
        VerificationStatus.REJECTED,
    },
    VerificationStatus.WEAK_SUPPORT: {
        VerificationStatus.MODERATE_SUPPORT,
        VerificationStatus.STRONG_SUPPORT,
        VerificationStatus.CONTRADICTED,
        VerificationStatus.INSUFFICIENT_EVIDENCE,
        VerificationStatus.REJECTED,
    },
    VerificationStatus.MODERATE_SUPPORT: {
        VerificationStatus.STRONG_SUPPORT,
        VerificationStatus.CONTRADICTED,
        VerificationStatus.WEAK_SUPPORT,
        VerificationStatus.REJECTED,
    },
    VerificationStatus.STRONG_SUPPORT: {
        VerificationStatus.CONTRADICTED,
        VerificationStatus.MODERATE_SUPPORT,
        VerificationStatus.VERIFIED,
        VerificationStatus.REJECTED,
    },
    VerificationStatus.CONTRADICTED: {
        VerificationStatus.INSUFFICIENT_EVIDENCE,
        VerificationStatus.REJECTED,
        VerificationStatus.SUPERSEDED,
    },
    VerificationStatus.VERIFIED: {
        VerificationStatus.SUPERSEDED,
        VerificationStatus.REJECTED,  # If new evidence emerges
    },
    VerificationStatus.REJECTED: {
        VerificationStatus.SUPERSEDED,
    },
    VerificationStatus.SUPERSEDED: set(),  # Terminal
}


class SupportLevel(str, Enum):
    """Transparent support categories with documented criteria."""

    INSUFFICIENT = "insufficient"      # < 2 independent sources OR no primary evidence
    WEAK = "weak"                      # >= 1 primary source, limited corroboration
    MODERATE = "moderate"              # >= 2 independent sources, consistent
    STRONG = "strong"                  # >= 3 independent sources OR authoritative primary + corroboration
    CONTRADICTED = "contradicted"      # Direct conflicting evidence exists


@dataclass(frozen=True)
class EvidenceRef:
    """Reference to a piece of evidence with provenance."""

    evidence_id: str
    source_id: str
    passage_hash: str
    relevance: str  # supports, contradicts, contextual
    excerpt: str
    locator: Optional[str] = None  # e.g., "para 3", "table 2", "lines 45-50"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "evidence_id": self.evidence_id,
            "source_id": self.source_id,
            "passage_hash": self.passage_hash,
            "relevance": self.relevance,
            "excerpt": self.excerpt[:200] + ("..." if len(self.excerpt) > 200 else ""),
            "locator": self.locator,
        }


@dataclass(frozen=True)
class Assumption:
    """Explicit assumption underlying a claim."""

    assumption_id: str
    description: str
    justification: Optional[str] = None
    confidence: str = "medium"  # low, medium, high

    def to_dict(self) -> Dict[str, Any]:
        return {
            "assumption_id": self.assumption_id,
            "description": self.description,
            "justification": self.justification,
            "confidence": self.confidence,
        }


@dataclass(frozen=True)
class Limitation:
    """Known limitation of a claim or its verification."""

    limitation_id: str
    description: str
    severity: str = "minor"  # minor, moderate, major
    mitigation: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "limitation_id": self.limitation_id,
            "description": self.description,
            "severity": self.severity,
            "mitigation": self.mitigation,
        }


@dataclass(frozen=True)
class TemporalScope:
    """Temporal validity scope for a claim."""

    valid_from: Optional[float] = None  # Unix timestamp
    valid_until: Optional[float] = None
    timezone: str = "UTC"
    is_ongoing: bool = False

    def contains(self, timestamp: float) -> bool:
        if self.valid_from and timestamp < self.valid_from:
            return False
        if self.valid_until and timestamp > self.valid_until:
            return False
        return True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "valid_from": self.valid_from,
            "valid_until": self.valid_until,
            "timezone": self.timezone,
            "is_ongoing": self.is_ongoing,
        }


@dataclass(frozen=True)
class VerificationDecision:
    """Auditable record of a verification decision."""

    decision_id: str
    claim_id: str
    previous_status: VerificationStatus
    new_status: VerificationStatus
    support_level: SupportLevel
    reason: str
    evidence_reviewed: List[str]  # evidence_ids
    assumptions_considered: List[str]  # assumption_ids
    validator: str  # "deterministic" or "model:<model_name>"
    timestamp: float = field(default_factory=time.time)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "decision_id": self.decision_id,
            "claim_id": self.claim_id,
            "previous_status": self.previous_status.value,
            "new_status": self.new_status.value,
            "support_level": self.support_level.value,
            "reason": self.reason,
            "evidence_reviewed": self.evidence_reviewed,
            "assumptions_considered": self.assumptions_considered,
            "validator": self.validator,
            "timestamp": self.timestamp,
            "metadata": self.metadata,
        }


@dataclass(frozen=True)
class Claim:
    """A structured claim with full provenance and verification tracking."""

    claim_id: str
    claim_text: str
    claim_type: ClaimType
    status: VerificationStatus
    support_level: SupportLevel

    # Evidence and provenance
    supporting_evidence: List[EvidenceRef] = field(default_factory=list)
    contradicting_evidence: List[EvidenceRef] = field(default_factory=list)
    independent_domains: List[str] = field(default_factory=list)  # e.g., ["academic", "government", "news"]

    # Context
    related_question: Optional[str] = None
    subquestion_id: Optional[str] = None
    assumptions: List[Assumption] = field(default_factory=list)
    limitations: List[Limitation] = field(default_factory=list)
    temporal_scope: Optional[TemporalScope] = None

    # Inference tracking
    inference_method: Optional[str] = None  # "direct_extraction", "calculation", "synthesis", "model_inference"
    derived_from_claims: List[str] = field(default_factory=list)  # parent claim_ids

    # Verification history
    verification_history: List[VerificationDecision] = field(default_factory=list)

    # Metadata
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    version: int = 1
    created_by: str = "system"  # agent_id or "human"
    metadata: Dict[str, Any] = field(default_factory=dict)

    def can_transition_to(self, new_status: VerificationStatus) -> bool:
        """Check if status transition is valid."""
        return new_status in STATUS_TRANSITIONS.get(self.status, set())

    def transition_status(
        self,
        new_status: VerificationStatus,
        support_level: SupportLevel,
        reason: str,
        evidence_reviewed: List[str],
        assumptions_considered: List[str],
        validator: str,
    ) -> "Claim":
        """Create a new claim with updated status and recorded decision."""
        if not self.can_transition_to(new_status):
            raise ValueError(f"Invalid status transition: {self.status.value} -> {new_status.value}")

        decision = VerificationDecision(
            decision_id=hashlib.sha256(f"{self.claim_id}{time.time()}".encode()).hexdigest()[:16],
            claim_id=self.claim_id,
            previous_status=self.status,
            new_status=new_status,
            support_level=support_level,
            reason=reason,
            evidence_reviewed=evidence_reviewed,
            assumptions_considered=assumptions_considered,
            validator=validator,
        )

        return replace(
            self,
            status=new_status,
            support_level=support_level,
            verification_history=[*self.verification_history, decision],
            updated_at=time.time(),
            version=self.version + 1,
        )

    def add_evidence(self, evidence: EvidenceRef, relevance: str) -> "Claim":
        """Add evidence reference."""
        if relevance == "supports":
            supporting = [*self.supporting_evidence, evidence]
            contradicting = self.contradicting_evidence
        elif relevance == "contradicts":
            supporting = self.supporting_evidence
            contradicting = [*self.contradicting_evidence, evidence]
        else:
            supporting = self.supporting_evidence
            contradicting = self.contradicting_evidence

        return replace(
            self,
            supporting_evidence=supporting,
            contradicting_evidence=contradicting,
            updated_at=time.time(),
            version=self.version + 1,
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "claim_id": self.claim_id,
            "claim_text": self.claim_text,
            "claim_type": self.claim_type.value,
            "status": self.status.value,
            "support_level": self.support_level.value,
            "supporting_evidence": [e.to_dict() for e in self.supporting_evidence],
            "contradicting_evidence": [e.to_dict() for e in self.contradicting_evidence],
            "independent_domains": self.independent_domains,
            "related_question": self.related_question,
            "subquestion_id": self.subquestion_id,
            "assumptions": [a.to_dict() for a in self.assumptions],
            "limitations": [lim.to_dict() for lim in self.limitations],
            "temporal_scope": self.temporal_scope.to_dict() if self.temporal_scope else None,
            "inference_method": self.inference_method,
            "derived_from_claims": self.derived_from_claims,
            "verification_history": [d.to_dict() for d in self.verification_history],
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "version": self.version,
            "created_by": self.created_by,
            "metadata": self.metadata,
        }

    @classmethod
    def create(
        cls,
        claim_text: str,
        claim_type: ClaimType = ClaimType.SOURCE_ASSERTION,
        created_by: str = "system",
        related_question: Optional[str] = None,
        subquestion_id: Optional[str] = None,
        temporal_scope: Optional[TemporalScope] = None,
        inference_method: Optional[str] = None,
    ) -> "Claim":
        """Factory for creating new claims with generated IDs."""
        claim_id = hashlib.sha256(f"{claim_text}{time.time()}".encode()).hexdigest()[:16]
        return cls(
            claim_id=claim_id,
            claim_text=claim_text.strip(),
            claim_type=claim_type,
            status=VerificationStatus.PENDING,
            support_level=SupportLevel.INSUFFICIENT,
            created_by=created_by,
            related_question=related_question,
            subquestion_id=subquestion_id,
            temporal_scope=temporal_scope,
            inference_method=inference_method,
        )


# Schema for claim validation
CLAIM_SCHEMA = TaskSchema("claim", [
    Field("claim_id", "str", required=True),
    Field("claim_text", "str", required=True, max_length=5000),
    Field("claim_type", "str", required=True),
    Field("status", "str", required=True),
    Field("support_level", "str", required=True),
    Field("supporting_evidence", "list", required=False),
    Field("contradicting_evidence", "list", required=False),
    Field("independent_domains", "list", required=False),
    Field("related_question", "str", required=False),
    Field("subquestion_id", "str", required=False),
    Field("assumptions", "list", required=False),
    Field("limitations", "list", required=False),
    Field("temporal_scope", "dict", required=False),
    Field("inference_method", "str", required=False),
    Field("derived_from_claims", "list", required=False),
    Field("verification_history", "list", required=False),
    Field("created_at", "float", required=True),
    Field("updated_at", "float", required=True),
    Field("version", "int", required=True),
    Field("created_by", "str", required=True),
    Field("metadata", "dict", required=False),
])


__all__ = [
    "Assumption",
    "Claim",
    "ClaimType",
    "CLAIM_SCHEMA",
    "EvidenceRef",
    "Limitation",
    "SupportLevel",
    "TemporalScope",
    "VerificationDecision",
    "VerificationStatus",
    "STATUS_TRANSITIONS",
]