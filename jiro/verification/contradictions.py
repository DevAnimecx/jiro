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
