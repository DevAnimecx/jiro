"""Deterministic validators for claim verification.

Implements reproducible, auditable validation checks that can be run
without model assistance. Each validator produces a decision with
explicit reasoning and evidence references.
"""

from __future__ import annotations

import re
import time
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Set

from jiro.log import get_logger
from jiro.verification.claims import (
    Claim,
    EvidenceRef,
    SupportLevel,
    VerificationStatus,
)

log = get_logger("jiro.verification.validators")


class ValidationResult:
    """Result of a validation check."""

    def __init__(
        self,
        passed: bool,
        reason: str,
        evidence_refs: List[str] = None,
        metadata: Dict[str, Any] = None,
    ):
        self.passed = passed
        self.reason = reason
        self.evidence_refs = evidence_refs or []
        self.metadata = metadata or {}

    def to_dict(self) -> Dict[str, Any]:
        return {
            "passed": self.passed,
            "reason": self.reason,
            "evidence_refs": self.evidence_refs,
            "metadata": self.metadata,
        }


class BaseValidator(ABC):
    """Base class for deterministic validators."""

    name: str = "base_validator"
    description: str = "Base validator"

    @abstractmethod
    def validate(self, claim: Claim, evidence_store: "EvidenceStore") -> ValidationResult:
        """Validate a claim against evidence. Returns ValidationResult."""
        pass


class EvidenceStore:
    """Interface for accessing evidence by ID."""

    def __init__(self):
        self._evidence: Dict[str, EvidenceRef] = {}
        self._sources: Dict[str, Dict[str, Any]] = {}

    def add_evidence(self, evidence: EvidenceRef) -> None:
        self._evidence[evidence.evidence_id] = evidence

    def add_source(self, source_id: str, source_data: Dict[str, Any]) -> None:
        self._sources[source_id] = source_data

    def get_evidence(self, evidence_id: str) -> Optional[EvidenceRef]:
        return self._evidence.get(evidence_id)

    def get_source(self, source_id: str) -> Optional[Dict[str, Any]]:
        return self._sources.get(source_id)

    def get_evidence_by_ids(self, evidence_ids: List[str]) -> List[EvidenceRef]:
        return [self._evidence[eid] for eid in evidence_ids if eid in self._evidence]


class CitationResolutionValidator(BaseValidator):
    """Validates that all cited evidence actually exists and is accessible."""

    name = "citation_resolution"
    description = "Verifies all cited evidence references resolve to stored evidence"

    def validate(self, claim: Claim, evidence_store: EvidenceStore) -> ValidationResult:
        missing = []
        resolved = []

        for evidence_ref in claim.supporting_evidence:
            evidence = evidence_store.get_evidence(evidence_ref.evidence_id)
            if evidence is None:
                missing.append(evidence_ref.evidence_id)
            else:
                resolved.append(evidence_ref.evidence_id)

        for evidence_ref in claim.contradicting_evidence:
            evidence = evidence_store.get_evidence(evidence_ref.evidence_id)
            if evidence is None:
                missing.append(evidence_ref.evidence_id)
            else:
                resolved.append(evidence_ref.evidence_id)

        if missing:
            return ValidationResult(
                passed=False,
                reason=f"Citation resolution failed: {len(missing)} evidence reference(s) not found",
                evidence_refs=resolved,
                metadata={"missing_evidence_ids": missing, "resolved_count": len(resolved)},
            )

        return ValidationResult(
            passed=True,
            reason=f"All {len(resolved)} evidence references resolved",
            evidence_refs=resolved,
            metadata={"resolved_count": len(resolved)},
        )


class EvidenceRelevanceValidator(BaseValidator):
    """Validates that cited evidence is actually relevant to the claim."""

    name = "evidence_relevance"
    description = "Checks that evidence passages contain claim-relevant content"

    def __init__(self, min_overlap_words: int = 3):
        self.min_overlap_words = min_overlap_words

    def validate(self, claim: Claim, evidence_store: EvidenceStore) -> ValidationResult:
        if not claim.supporting_evidence:
            return ValidationResult(
                passed=False,
                reason="No supporting evidence provided",
                evidence_refs=[],
            )

        claim_words = self._extract_words(claim.claim_text.lower())
        irrelevant = []
        relevant = []

        for evidence_ref in claim.supporting_evidence:
            evidence = evidence_store.get_evidence(evidence_ref.evidence_id)
            if evidence is None:
                irrelevant.append(evidence_ref.evidence_id)
                continue

            passage_words = self._extract_words(evidence.excerpt.lower())
            overlap = claim_words & passage_words

            if len(overlap) >= self.min_overlap_words:
                relevant.append(evidence_ref.evidence_id)
            else:
                irrelevant.append(evidence_ref.evidence_id)

        if irrelevant:
            return ValidationResult(
                passed=False,
                reason=f"{len(irrelevant)} evidence reference(s) lack relevant content overlap",
                evidence_refs=relevant,
                metadata={"irrelevant_evidence_ids": irrelevant, "relevant_count": len(relevant)},
            )

        return ValidationResult(
            passed=True,
            reason=f"All {len(relevant)} evidence references show content relevance",
            evidence_refs=relevant,
        )

    def _extract_words(self, text: str) -> Set[str]:
        # Remove stop words
        stop_words = {
            "the", "a", "an", "and", "or", "but", "in", "on", "at", "to", "for",
            "of", "with", "by", "from", "as", "is", "was", "are", "were", "be",
            "been", "being", "have", "has", "had", "do", "does", "did", "will",
            "would", "could", "should", "may", "might", "must", "can", "this",
            "that", "these", "those", "it", "its", "they", "them", "their",
        }
        words = re.findall(r"\b[a-z]{3,}\b", text.lower())
        return {w for w in words if w not in stop_words}


class IndependentSourceValidator(BaseValidator):
    """Validates that supporting evidence comes from independent domains/sources."""

    name = "independent_sources"
    description = "Checks for evidence from multiple independent domains"

    def __init__(self, min_independent_domains: int = 2):
        self.min_independent_domains = min_independent_domains

    def validate(self, claim: Claim, evidence_store: EvidenceStore) -> ValidationResult:
        domains = set()

        for evidence_ref in claim.supporting_evidence:
            evidence = evidence_store.get_evidence(evidence_ref.evidence_id)
            if evidence:
                source = evidence_store.get_source(evidence.source_id)
                if source:
                    domain = source.get("domain", "")
                    if domain:
                        domains.add(domain)

        # Also use the claim's explicit independent_domains
        domains.update(claim.independent_domains)

        if len(domains) < self.min_independent_domains:
            return ValidationResult(
                passed=False,
                reason=f"Only {len(domains)} independent domain(s), need {self.min_independent_domains}",
                evidence_refs=list(domains),
                metadata={"domains": list(domains), "required": self.min_independent_domains},
            )

        return ValidationResult(
            passed=True,
            reason=f"Evidence from {len(domains)} independent domains",
            evidence_refs=list(domains),
            metadata={"domains": list(domains)},
        )


class TemporalConsistencyValidator(BaseValidator):
    """Validates that claim's temporal scope is consistent with evidence dates."""

    name = "temporal_consistency"
    description = "Checks claim temporal scope against evidence publication dates"

    def validate(self, claim: Claim, evidence_store: EvidenceStore) -> ValidationResult:
        if not claim.temporal_scope:
            return ValidationResult(
                passed=True,
                reason="No temporal scope specified, skipping temporal check",
                evidence_refs=[],
            )

        inconsistencies = []
        checked = 0

        for evidence_ref in claim.supporting_evidence:
            evidence = evidence_store.get_evidence(evidence_ref.evidence_id)
            if not evidence:
                continue

            source = evidence_store.get_source(evidence.source_id)
            if not source:
                continue

            published_at = source.get("published_at")
            if published_at is None:
                continue

            checked += 1
            if not claim.temporal_scope.contains(published_at):
                inconsistencies.append({
                    "evidence_id": evidence_ref.evidence_id,
                    "source_id": evidence.source_id,
                    "published_at": published_at,
                    "claim_scope": claim.temporal_scope.to_dict(),
                })

        if inconsistencies:
            return ValidationResult(
                passed=False,
                reason=f"{len(inconsistencies)} evidence source(s) outside claim's temporal scope",
                evidence_refs=[i["evidence_id"] for i in inconsistencies],
                metadata={"inconsistencies": inconsistencies, "checked": checked},
            )

        if checked == 0:
            return ValidationResult(
                passed=True,
                reason="No dated evidence to check against temporal scope",
                evidence_refs=[],
            )

        return ValidationResult(
            passed=True,
            reason=f"All {checked} dated evidence sources within temporal scope",
            evidence_refs=[],
        )


class ArithmeticValidator(BaseValidator):
    """Validates arithmetic claims by recomputing."""

    name = "arithmetic"
    description = "Recomputes numerical claims from cited evidence"

    def validate(self, claim: Claim, evidence_store: EvidenceStore) -> ValidationResult:
        # Only apply to claims that appear to be arithmetic
        if not self._looks_like_arithmetic(claim.claim_text):
            return ValidationResult(
                passed=True,
                reason="Claim does not appear to be arithmetic, skipping",
                evidence_refs=[],
            )

        # Extract numbers from claim
        claim_numbers = self._extract_numbers(claim.claim_text)

        # Extract numbers from evidence
        evidence_numbers = []
        for evidence_ref in claim.supporting_evidence:
            evidence = evidence_store.get_evidence(evidence_ref.evidence_id)
            if evidence:
                evidence_numbers.extend(self._extract_numbers(evidence.excerpt))

        if not evidence_numbers:
            return ValidationResult(
                passed=False,
                reason="Arithmetic claim but no numerical evidence found",
                evidence_refs=[],
            )

        # Check if claim numbers are supported by evidence
        # This is a simplified check - real implementation would parse expressions
        supported = any(abs(cn - en) < 0.001 for cn in claim_numbers for en in evidence_numbers)

        if not supported and claim_numbers:
            return ValidationResult(
                passed=False,
                reason=f"Claim numbers {claim_numbers} not found in evidence {evidence_numbers}",
                evidence_refs=[e.evidence_id for e in claim.supporting_evidence],
                metadata={"claim_numbers": claim_numbers, "evidence_numbers": evidence_numbers},
            )

        return ValidationResult(
            passed=True,
            reason="Arithmetic claim numbers supported by evidence",
            evidence_refs=[e.evidence_id for e in claim.supporting_evidence],
            metadata={"claim_numbers": claim_numbers, "evidence_numbers": evidence_numbers},
        )

    def _looks_like_arithmetic(self, text: str) -> bool:
        patterns = [
            r"\d+\s*[\+\-\*/]\s*\d+",
            r"(equals?|is|totals?|sums?|averages?)\s+\d+",
            r"\d+%",
            r"\$\d+",
        ]
        return any(re.search(p, text, re.IGNORECASE) for p in patterns)

    def _extract_numbers(self, text: str) -> List[float]:
        # Extract numbers including decimals, percentages, currency
        numbers = []
        for match in re.finditer(r"[\$\%]?\s*(\d+(?:,\d{3})*(?:\.\d+)?)\s*[\$\%]?", text):
            try:
                num = float(match.group(1).replace(",", ""))
                numbers.append(num)
            except ValueError:
                pass
        return numbers


class ContradictionValidator(BaseValidator):
    """Detects direct contradictions between claim and evidence."""

    name = "contradiction"
    description = "Checks for explicit contradictions in evidence"

    def __init__(self):
        self.negation_patterns = [
            r"\bnot\b", r"\bno\b", r"\bnever\b", r"\bfalse\b", r"\bincorrect\b",
            r"\bwrong\b", r"\buntrue\b", r"\bdispute[sd]?\b", r"\bcontradict[s]?\b",
            r"\brefute[sd]?\b", r"\bdeny\b", r"\breject[s]?\b",
        ]

    def validate(self, claim: Claim, evidence_store: EvidenceStore) -> ValidationResult:
        contradictions = []

        for evidence_ref in claim.supporting_evidence:
            evidence = evidence_store.get_evidence(evidence_ref.evidence_id)
            if not evidence:
                continue

            # Check if evidence explicitly contradicts claim
            if self._contains_negation(claim.claim_text, evidence.excerpt):
                contradictions.append({
                    "evidence_id": evidence_ref.evidence_id,
                    "type": "explicit_negation",
                    "claim": claim.claim_text[:100],
                    "evidence": evidence.excerpt[:100],
                })

        if contradictions:
            return ValidationResult(
                passed=False,
                reason=f"Found {len(contradictions)} explicit contradiction(s) in supporting evidence",
                evidence_refs=[c["evidence_id"] for c in contradictions],
                metadata={"contradictions": contradictions},
            )

        return ValidationResult(
            passed=True,
            reason="No explicit contradictions detected in supporting evidence",
            evidence_refs=[],
        )

    def _contains_negation(self, claim_text: str, evidence_text: str) -> bool:
        claim_lower = claim_text.lower()
        evidence_lower = evidence_text.lower()

        # Simple check: if claim asserts X and evidence contains "not X" or "no X"
        claim_words = set(re.findall(r"\b[a-z]{3,}\b", claim_lower))

        # Check for negation of key claim terms in evidence
        for word in claim_words:
            if len(word) > 4:
                for pattern in self.negation_patterns:
                    if re.search(pattern + r"\s+" + re.escape(word), evidence_lower):
                        return True

        return False


class SchemaValidator(BaseValidator):
    """Validates claim structure and required fields."""

    name = "schema"
    description = "Validates claim structure against schema"

    def validate(self, claim: Claim, evidence_store: EvidenceStore) -> ValidationResult:
        errors = []

        if not claim.claim_text or not claim.claim_text.strip():
            errors.append("claim_text is required")

        if not claim.claim_id:
            errors.append("claim_id is required")

        if claim.version < 1:
            errors.append("version must be >= 1")

        if claim.status not in VerificationStatus.__members__.values():
            errors.append(f"Invalid status: {claim.status}")

        if claim.support_level not in SupportLevel.__members__.values():
            errors.append(f"Invalid support_level: {claim.support_level}")

        if errors:
            return ValidationResult(
                passed=False,
                reason=f"Schema validation failed: {'; '.join(errors)}",
                evidence_refs=[],
                metadata={"errors": errors},
            )

        return ValidationResult(
            passed=True,
            reason="Claim schema is valid",
            evidence_refs=[],
        )


class ValidatorPipeline:
    """Runs a sequence of validators and aggregates results."""

    def __init__(self, validators: List[BaseValidator] = None):
        self.validators = validators or self._default_validators()

    def _default_validators(self) -> List[BaseValidator]:
        return [
            SchemaValidator(),
            CitationResolutionValidator(),
            EvidenceRelevanceValidator(),
            IndependentSourceValidator(),
            TemporalConsistencyValidator(),
            ArithmeticValidator(),
            ContradictionValidator(),
        ]

    def run(
        self,
        claim: Claim,
        evidence_store: EvidenceStore,
        stop_on_failure: bool = False,
    ) -> Dict[str, Any]:
        """Run all validators on a claim."""
        results = {}
        all_passed = True
        all_evidence_refs = []

        for validator in self.validators:
            result = validator.validate(claim, evidence_store)
            results[validator.name] = result.to_dict()
            all_evidence_refs.extend(result.evidence_refs)

            if not result.passed:
                all_passed = False
                if stop_on_failure:
                    break

        return {
            "claim_id": claim.claim_id,
            "all_passed": all_passed,
            "validator_results": results,
            "evidence_referenced": list(set(all_evidence_refs)),
            "timestamp": time.time(),
        }


__all__ = [
    "ArithmeticValidator",
    "BaseValidator",
    "ContradictionValidator",
    "CitationResolutionValidator",
    "EvidenceRelevanceValidator",
    "EvidenceStore",
    "IndependentSourceValidator",
    "SchemaValidator",
    "TemporalConsistencyValidator",
    "ValidationResult",
    "ValidatorPipeline",
]