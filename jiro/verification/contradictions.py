"""Contradiction and inconsistency detection across claims, evidence, and memory.

Detects potential contradictions and preserves them for review rather than
auto-resolving. Uses typed relations and explicit conflict rules.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

from jiro.log import get_logger
from jiro.verification.claims import Claim, ClaimType, VerificationStatus

log = get_logger("jiro.verification.contradictions")


class ContradictionType(str, Enum):
    """Types of contradictions that can be detected."""

    DIRECT_NEGATION = "direct_negation"           # Claim A says X, Claim B says not X
    VALUE_CONFLICT = "value_conflict"             # Different numeric values for same metric
    TEMPORAL_CONFLICT = "temporal_conflict"       # Conflicting time bounds
    SOURCE_CONFLICT = "source_conflict"           # Same source cited for opposite claims
    ASSESSMENT_CONFLICT = "assessment_conflict"   # Different verification assessments
    DOMAIN_CONFLICT = "domain_conflict"           # Different domains give different answers
    LOGICAL_INCONSISTENCY = "logical_inconsistency"  # Mutually exclusive claims


class ConflictSeverity(str, Enum):
    LOW = "low"           # Minor discrepancy, likely definitional
    MODERATE = "moderate" # Meaningful difference, needs investigation
    HIGH = "high"         # Direct contradiction, cannot both be true
    CRITICAL = "critical" # Fundamental inconsistency, blocks verification


@dataclass(frozen=True)
class Contradiction:
    """A detected contradiction between two or more claims."""

    contradiction_id: str
    contradiction_type: ContradictionType
    severity: ConflictSeverity
    claim_ids: List[str]
    description: str
    details: Dict[str, Any] = field(default_factory=dict)
    detected_at: float = field(default_factory=time.time)
    resolved: bool = False
    resolution: Optional[str] = None
    resolved_at: Optional[float] = None
    resolution_method: Optional[str] = None  # "evidence", "policy", "human", "superseded"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "contradiction_id": self.contradiction_id,
            "contradiction_type": self.contradiction_type.value,
            "severity": self.severity.value,
            "claim_ids": self.claim_ids,
            "description": self.description,
            "details": self.details,
            "detected_at": self.detected_at,
            "resolved": self.resolved,
            "resolution": self.resolution,
            "resolved_at": self.resolved_at,
            "resolution_method": self.resolution_method,
        }


@dataclass(frozen=True)
class ClaimPair:
    """A pair of claims to compare for contradictions."""

    claim_a: Claim
    claim_b: Claim

    def same_subject(self) -> bool:
        """Heuristic: check if claims are about the same subject."""
        # Simple word overlap check
        words_a = set(self.claim_a.claim_text.lower().split())
        words_b = set(self.claim_b.claim_text.lower().split())
        # Remove common stop words
        stop = {"the", "a", "an", "and", "or", "but", "in", "on", "at", "to", "for",
                "of", "with", "by", "from", "as", "is", "was", "are", "were", "be",
                "been", "being", "have", "has", "had", "do", "does", "did", "will",
                "would", "could", "should", "may", "might", "must", "can", "this",
                "that", "these", "those", "it", "its", "they", "them", "their"}
        words_a = {w for w in words_a if w not in stop and len(w) > 3}
        words_b = {w for w in words_b if w not in stop and len(w) > 3}

        if not words_a or not words_b:
            return False

        overlap = words_a & words_b
        return len(overlap) >= min(len(words_a), len(words_b)) * 0.3


class ContradictionDetector:
    """Detects contradictions across claims, evidence, and agent outputs."""

    def __init__(self):
        self._contradictions: Dict[str, Contradiction] = {}
        self._claim_index: Dict[str, Claim] = {}

    def add_claim(self, claim: Claim) -> None:
        """Add a claim to the index for contradiction detection."""
        self._claim_index[claim.claim_id] = claim

    def detect_contradictions(
        self,
        new_claim: Claim,
        existing_claims: Optional[List[Claim]] = None,
    ) -> List[Contradiction]:
        """Detect contradictions between new claim and existing claims."""
        claims_to_check = existing_claims or list(self._claim_index.values())
        contradictions = []

        for existing in claims_to_check:
            if existing.claim_id == new_claim.claim_id:
                continue

            # Only check claims about similar subjects
            pair = ClaimPair(new_claim, existing)
            if not pair.same_subject():
                continue

            # Check various contradiction types
            contradictions.extend(self._check_direct_negation(new_claim, existing))
            contradictions.extend(self._check_value_conflict(new_claim, existing))
            contradictions.extend(self._check_temporal_conflict(new_claim, existing))
            contradictions.extend(self._check_assessment_conflict(new_claim, existing))
            contradictions.extend(self._check_logical_inconsistency(new_claim, existing))

        # Store detected contradictions
        for c in contradictions:
            self._contradictions[c.contradiction_id] = c

        return contradictions

    def _check_direct_negation(self, claim_a: Claim, claim_b: Claim) -> List[Contradiction]:
        """Check for direct negation patterns."""
        contradictions = []

        text_a = claim_a.claim_text.lower().strip()
        text_b = claim_b.claim_text.lower().strip()

        # Pattern: "X is Y" vs "X is not Y" or "X is not Y" vs "X is Y"
        negation_indicators = ["not ", "no ", "never ", "false", "incorrect", "wrong", "untrue"]

        for indicator in negation_indicators:
            if indicator in text_a and indicator not in text_b:
                # Check if removing negation makes them similar
                a_without = text_a.replace(indicator, "").strip()
                if self._texts_similar(a_without, text_b):
                    contradictions.append(Contradiction(
                        contradiction_id=hashlib.sha256(f"{claim_a.claim_id}{claim_b.claim_id}neg".encode()).hexdigest()[:16],
                        contradiction_type=ContradictionType.DIRECT_NEGATION,
                        severity=ConflictSeverity.HIGH,
                        claim_ids=[claim_a.claim_id, claim_b.claim_id],
                        description=f"Direct negation: '{claim_a.claim_text[:80]}...' vs '{claim_b.claim_text[:80]}...'",
                        details={"indicator": indicator, "claim_a": text_a, "claim_b": text_b},
                    ))
            elif indicator in text_b and indicator not in text_a:
                b_without = text_b.replace(indicator, "").strip()
                if self._texts_similar(text_a, b_without):
                    contradictions.append(Contradiction(
                        contradiction_id=hashlib.sha256(f"{claim_a.claim_id}{claim_b.claim_id}neg".encode()).hexdigest()[:16],
                        contradiction_type=ContradictionType.DIRECT_NEGATION,
                        severity=ConflictSeverity.HIGH,
                        claim_ids=[claim_a.claim_id, claim_b.claim_id],
                        description=f"Direct negation: '{claim_a.claim_text[:80]}...' vs '{claim_b.claim_text[:80]}...'",
                        details={"indicator": indicator, "claim_a": text_a, "claim_b": text_b},
                    ))

        return contradictions

    def _check_value_conflict(self, claim_a: Claim, claim_b: Claim) -> List[Contradiction]:
        """Check for conflicting numeric values."""

        contradictions = []

        numbers_a = self._extract_numeric_assertions(claim_a.claim_text)
        numbers_b = self._extract_numeric_assertions(claim_b.claim_text)

        for num_a in numbers_a:
            for num_b in numbers_b:
                # Same metric (same unit/context), different values
                if num_a["unit"] == num_b["unit"] and num_a["context"] == num_b["context"]:
                    if abs(num_a["value"] - num_b["value"]) > 0.001:
                        severity = ConflictSeverity.HIGH
                        if num_a["unit"] in ("%", "percent") and abs(num_a["value"] - num_b["value"]) < 5:
                            severity = ConflictSeverity.MODERATE

                        contradictions.append(Contradiction(
                            contradiction_id=hashlib.sha256(f"{claim_a.claim_id}{claim_b.claim_id}val".encode()).hexdigest()[:16],
                            contradiction_type=ContradictionType.VALUE_CONFLICT,
                            severity=severity,
                            claim_ids=[claim_a.claim_id, claim_b.claim_id],
                            description=f"Conflicting values: {num_a['value']} {num_a['unit']} vs {num_b['value']} {num_b['unit']} for {num_a['context']}",
                            details={"value_a": num_a, "value_b": num_b},
                        ))

        return contradictions

    def _check_temporal_conflict(self, claim_a: Claim, claim_b: Claim) -> List[Contradiction]:
        """Check for conflicting temporal scopes."""
        contradictions = []

        if not claim_a.temporal_scope or not claim_b.temporal_scope:
            return contradictions

        # Check if temporal scopes are mutually exclusive
        scope_a = claim_a.temporal_scope
        scope_b = claim_b.temporal_scope

        # If A says "before X" and B says "after X"
        if scope_a.valid_until and scope_b.valid_from:
            if scope_a.valid_until < scope_b.valid_from:
                contradictions.append(Contradiction(
                    contradiction_id=hashlib.sha256(f"{claim_a.claim_id}{claim_b.claim_id}temp".encode()).hexdigest()[:16],
                    contradiction_type=ContradictionType.TEMPORAL_CONFLICT,
                    severity=ConflictSeverity.HIGH,
                    claim_ids=[claim_a.claim_id, claim_b.claim_id],
                    description="Temporal conflict: Claim A ends before Claim B begins",
                    details={"scope_a": scope_a.to_dict(), "scope_b": scope_b.to_dict()},
                ))

        if scope_b.valid_until and scope_a.valid_from:
            if scope_b.valid_until < scope_a.valid_from:
                contradictions.append(Contradiction(
                    contradiction_id=hashlib.sha256(f"{claim_a.claim_id}{claim_b.claim_id}temp".encode()).hexdigest()[:16],
                    contradiction_type=ContradictionType.TEMPORAL_CONFLICT,
                    severity=ConflictSeverity.HIGH,
                    claim_ids=[claim_a.claim_id, claim_b.claim_id],
                    description="Temporal conflict: Claim B ends before Claim A begins",
                    details={"scope_a": scope_a.to_dict(), "scope_b": scope_b.to_dict()},
                ))

        return contradictions

    def _check_assessment_conflict(self, claim_a: Claim, claim_b: Claim) -> List[Contradiction]:
        """Check for conflicting verification assessments on same/similar claims."""
        contradictions = []

        # If both claims have been assessed and have opposite assessments
        status_a = claim_a.status
        status_b = claim_b.status

        # Strong support vs contradicted is a conflict
        strong_statuses = {VerificationStatus.STRONG_SUPPORT, VerificationStatus.VERIFIED}
        contradicted_statuses = {VerificationStatus.CONTRADICTED, VerificationStatus.REJECTED}

        if status_a in strong_statuses and status_b in contradicted_statuses:
            contradictions.append(Contradiction(
                contradiction_id=hashlib.sha256(f"{claim_a.claim_id}{claim_b.claim_id}assess".encode()).hexdigest()[:16],
                contradiction_type=ContradictionType.ASSESSMENT_CONFLICT,
                severity=ConflictSeverity.HIGH,
                claim_ids=[claim_a.claim_id, claim_b.claim_id],
                description=f"Assessment conflict: Claim A {status_a.value}, Claim B {status_b.value}",
                details={"status_a": status_a.value, "status_b": status_b.value},
            ))

        return contradictions

    def _check_logical_inconsistency(self, claim_a: Claim, claim_b: Claim) -> List[Contradiction]:
        """Check for mutually exclusive claims (e.g., "A causes B" vs "A prevents B")."""
        contradictions = []

        causal_patterns = [
            ("causes", "prevents"),
            ("increases", "decreases"),
            ("improves", "worsens"),
            ("enables", "blocks"),
            ("supports", "undermines"),
        ]

        text_a = claim_a.claim_text.lower()
        text_b = claim_b.claim_text.lower()

        for pos, neg in causal_patterns:
            if pos in text_a and neg in text_b:
                contradictions.append(Contradiction(
                    contradiction_id=hashlib.sha256(f"{claim_a.claim_id}{claim_b.claim_id}logic".encode()).hexdigest()[:16],
                    contradiction_type=ContradictionType.LOGICAL_INCONSISTENCY,
                    severity=ConflictSeverity.MODERATE,
                    claim_ids=[claim_a.claim_id, claim_b.claim_id],
                    description=f"Logical inconsistency: '{pos}' vs '{neg}'",
                    details={"positive": pos, "negative": neg},
                ))
            elif neg in text_a and pos in text_b:
                contradictions.append(Contradiction(
                    contradiction_id=hashlib.sha256(f"{claim_a.claim_id}{claim_b.claim_id}logic".encode()).hexdigest()[:16],
                    contradiction_type=ContradictionType.LOGICAL_INCONSISTENCY,
                    severity=ConflictSeverity.MODERATE,
                    claim_ids=[claim_a.claim_id, claim_b.claim_id],
                    description=f"Logical inconsistency: '{neg}' vs '{pos}'",
                    details={"positive": pos, "negative": neg},
                ))

        return contradictions

    def _texts_similar(self, text_a: str, text_b: str, threshold: float = 0.7) -> bool:
        """Check if two texts are similar (Jaccard similarity)."""
        words_a = set(text_a.split())
        words_b = set(text_b.split())

        if not words_a or not words_b:
            return False

        intersection = words_a & words_b
        union = words_a | words_b

        return len(intersection) / len(union) >= threshold

    def _extract_numeric_assertions(self, text: str) -> List[Dict[str, Any]]:
        """Extract numeric assertions with context and units."""
        import re

        assertions = []

        # Patterns: "X is 5%", "5% of X", "X increased by 10%", etc.
        patterns = [
            (r"(\w+(?:\s+\w+){0,3})\s+(?:is|equals?|was|were)\s+(\d+(?:,\d{3})*(?:\.\d+)?)\s*(\%|percent|dollars?|USD|€|£)?", "value"),
            (r"(?:increased|decreased|rose|fell|grew|dropped)\s+by\s+(\d+(?:,\d{3})*(?:\.\d+)?)\s*(\%|percent)?", "change"),
            (r"(\d+(?:,\d{3})*(?:\.\d+)?)\s*(\%|percent)\s+of\s+(\w+(?:\s+\w+){0,3})", "percentage_of"),
        ]

        for pattern, ptype in patterns:
            for match in re.finditer(pattern, text, re.IGNORECASE):
                groups = match.groups()
                if ptype == "value" and len(groups) >= 3:
                    context, value_str, unit = groups[0], groups[1], groups[2] or ""
                    assertions.append({
                        "context": context.strip(),
                        "value": float(value_str.replace(",", "")),
                        "unit": unit.strip().lower() if unit else "",
                        "type": "value",
                    })
                elif ptype == "change" and len(groups) >= 2:
                    value_str, unit = groups[0], groups[1] or ""
                    assertions.append({
                        "context": "change",
                        "value": float(value_str.replace(",", "")),
                        "unit": unit.strip().lower() if unit else "",
                        "type": "change",
                    })

        return assertions

    def get_contradiction(self, contradiction_id: str) -> Optional[Contradiction]:
        return self._contradictions.get(contradiction_id)

    def get_all_contradictions(self) -> List[Contradiction]:
        return list(self._contradictions.values())

    def get_unresolved_contradictions(self) -> List[Contradiction]:
        return [c for c in self._contradictions.values() if not c.resolved]

    def resolve_contradiction(
        self,
        contradiction_id: str,
        resolution: str,
        method: str = "evidence",
    ) -> bool:
        """Mark a contradiction as resolved."""
        if contradiction_id not in self._contradictions:
            return False

        c = self._contradictions[contradiction_id]
        # Create new resolved version
        self._contradictions[contradiction_id] = Contradiction(
            contradiction_id=c.contradiction_id,
            contradiction_type=c.contradiction_type,
            severity=c.severity,
            claim_ids=c.claim_ids,
            description=c.description,
            details=c.details,
            detected_at=c.detected_at,
            resolved=True,
            resolution=resolution,
            resolved_at=time.time(),
            resolution_method=method,
        )
        return True


class CrossMemoryContradictionDetector:
    """Detects contradictions between claims and memory records."""

    def __init__(self, contradiction_detector: ContradictionDetector):
        self.detector = contradiction_detector

    def check_memory_claims(
        self,
        claim: Claim,
        memory_facts: List[Dict[str, Any]],  # From memory system
    ) -> List[Contradiction]:
        """Check claim against memory facts for contradictions."""
        contradictions = []

        for fact in memory_facts:
            fact_text = fact.get("object", "") or fact.get("predicate", "")
            if not fact_text:
                continue

            # Create a pseudo-claim from the fact
            from jiro.verification.claims import Claim as ClaimClass, VerificationStatus, SupportLevel
            fact_claim = ClaimClass.create(
                claim_text=fact_text,
                claim_type=ClaimType.SOURCE_ASSERTION,
                created_by="memory",
            )
            # Use proper transition path: PENDING -> STRONG_SUPPORT -> VERIFIED
            fact_claim = fact_claim.transition_status(
                new_status=VerificationStatus.STRONG_SUPPORT,
                support_level=SupportLevel.STRONG,
                reason="Memory fact treated as verified",
                evidence_reviewed=[],
                assumptions_considered=[],
                validator="memory",
            )
            fact_claim = fact_claim.transition_status(
                new_status=VerificationStatus.VERIFIED,
                support_level=SupportLevel.STRONG,
                reason="Memory fact treated as verified",
                evidence_reviewed=[],
                assumptions_considered=[],
                validator="memory",
            )

            # Check for contradictions
            new_contradictions = self.detector.detect_contradictions(claim, [fact_claim])
            contradictions.extend(new_contradictions)

        return contradictions


__all__ = [
    "ClaimPair",
    "ConflictSeverity",
    "Contradiction",
    "ContradictionDetector",
    "ContradictionType",
    "CrossMemoryContradictionDetector",
]