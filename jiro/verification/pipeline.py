"""Verification pipeline integration.

Ties together claim registry, validators, contradiction detection,
and formal verification into a unified pipeline that can be invoked
at report finalization, memory promotion, and agent output validation.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

from jiro.log import get_logger
from jiro.verification.claims import (
    Assumption,
    Claim,
    Limitation,
    SupportLevel,
    VerificationDecision,
    VerificationStatus,
)
from jiro.verification.contradictions import (
    Contradiction,
    ContradictionDetector,
    CrossMemoryContradictionDetector,
)
from jiro.verification.formal import FormalVerificationEngine, FormalSolverStatus
from jiro.verification.validators import (
    BaseValidator,
    EvidenceStore,
    ValidationResult,
    ValidatorPipeline,
)

log = get_logger("jiro.verification.pipeline")


class PipelineMode(str, Enum):
    STRICT = "strict"         # All validators must pass
    STANDARD = "standard"     # Most validators must pass, contradictions flagged
    LENIENT = "lenient"       # Only critical validators must pass


class VerificationOutcome(str, Enum):
    VERIFIED = "verified"                    # Meets all criteria
    CONDITIONALLY_VERIFIED = "conditionally_verified"  # Verified with limitations
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"    # Not enough evidence
    CONTRADICTED = "contradicted"            # Evidence contradicts
    REJECTED = "rejected"                    # Explicitly rejected
    ERROR = "error"                          # Verification error


@dataclass(frozen=True)
class VerificationPolicy:
    """Policy governing verification behavior."""

    mode: PipelineMode = PipelineMode.STANDARD
    min_independent_sources: int = 2
    require_citation_resolution: bool = True
    require_evidence_relevance: bool = True
    require_temporal_consistency: bool = True
    enable_formal_verification: bool = False
    formal_timeout_seconds: float = 30.0
    stop_on_critical_contradiction: bool = True
    auto_reject_unsupported: bool = False

    # Support level thresholds
    weak_threshold: int = 1      # min supporting evidence for WEAK
    moderate_threshold: int = 2  # min independent sources for MODERATE
    strong_threshold: int = 3    # min independent sources for STRONG


DEFAULT_POLICY = VerificationPolicy()


@dataclass
class VerificationContext:
    """Context for a verification run."""

    policy: VerificationPolicy
    evidence_store: EvidenceStore
    contradiction_detector: ContradictionDetector
    formal_engine: Optional[FormalVerificationEngine] = None
    memory_facts: List[Dict[str, Any]] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class VerificationReport:
    """Complete report of a verification run."""

    claim_id: str
    outcome: VerificationOutcome
    final_status: VerificationStatus
    final_support_level: SupportLevel

    # Validator results
    validator_results: Dict[str, ValidationResult] = field(default_factory=dict)
    all_validators_passed: bool = False

    # Contradictions
    contradictions: List[Contradiction] = field(default_factory=list)
    unresolved_contradictions: List[Contradiction] = field(default_factory=list)

    # Formal verification
    formal_results: Dict[str, Any] = field(default_factory=dict)

    # Evidence summary
    supporting_evidence_count: int = 0
    contradicting_evidence_count: int = 0
    independent_domains_count: int = 0

    # Limitations and assumptions
    limitations: List[Limitation] = field(default_factory=list)
    assumptions: List[Assumption] = field(default_factory=list)

    # Decision trail
    decisions: List[VerificationDecision] = field(default_factory=list)

    # Metadata
    verified_at: float = field(default_factory=time.time)
    verified_by: str = "pipeline"
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "claim_id": self.claim_id,
            "outcome": self.outcome.value,
            "final_status": self.final_status.value,
            "final_support_level": self.final_support_level.value,
            "validator_results": {k: v.to_dict() for k, v in self.validator_results.items()},
            "all_validators_passed": self.all_validators_passed,
            "contradictions": [c.to_dict() for c in self.contradictions],
            "unresolved_contradictions": [c.to_dict() for c in self.unresolved_contradictions],
            "formal_results": self.formal_results,
            "supporting_evidence_count": self.supporting_evidence_count,
            "contradicting_evidence_count": self.contradicting_evidence_count,
            "independent_domains_count": self.independent_domains_count,
            "limitations": [lim.to_dict() for lim in self.limitations],
            "assumptions": [a.to_dict() for a in self.assumptions],
            "decisions": [d.to_dict() for d in self.decisions],
            "verified_at": self.verified_at,
            "verified_by": self.verified_by,
            "metadata": self.metadata,
        }


class VerificationPipeline:
    """Main verification pipeline orchestrator."""

    def __init__(
        self,
        policy: VerificationPolicy = DEFAULT_POLICY,
        validators: Optional[List[BaseValidator]] = None,
        formal_engine: Optional[FormalVerificationEngine] = None,
    ):
        self.policy = policy
        self.validator_pipeline = ValidatorPipeline(validators)
        self.contradiction_detector = ContradictionDetector()
        self.formal_engine = formal_engine
        self._verification_history: List[VerificationReport] = []

    async def verify_claim(
        self,
        claim: Claim,
        evidence_store: EvidenceStore,
        existing_claims: Optional[List[Claim]] = None,
        memory_facts: Optional[List[Dict[str, Any]]] = None,
    ) -> VerificationReport:
        """Run full verification on a single claim."""
        start_time = time.time()

        # Initialize context
        context = VerificationContext(
            policy=self.policy,
            evidence_store=evidence_store,
            contradiction_detector=self.contradiction_detector,
            formal_engine=self.formal_engine,
            memory_facts=memory_facts or [],
        )

        # Register claim for contradiction detection
        self.contradiction_detector.add_claim(claim)

        # Run validators
        validator_results = self.validator_pipeline.run(
            claim, evidence_store, stop_on_failure=self.policy.mode == PipelineMode.STRICT
        )
        all_passed = validator_results["all_passed"]

        # Detect contradictions
        contradictions = self.contradiction_detector.detect_contradictions(
            claim, existing_claims
        )

        # Check memory contradictions
        if memory_facts:
            cross_detector = CrossMemoryContradictionDetector(self.contradiction_detector)
            memory_contradictions = cross_detector.check_memory_claims(claim, memory_facts)
            contradictions.extend(memory_contradictions)

        # Separate resolved/unresolved
        unresolved = [c for c in contradictions if not c.resolved]

        # Check for critical contradictions
        critical_contradictions = [
            c for c in unresolved
            if c.severity.value in ("high", "critical")
        ]

        # Run formal verification if enabled
        formal_results = {}
        if self.policy.enable_formal_verification and self.formal_engine:
            formal_results = await self._run_formal_verification(claim, context)

        # Determine outcome
        outcome, final_status, final_support = self._determine_outcome(
            claim,
            all_passed,
            validator_results,
            unresolved,
            critical_contradictions,
            formal_results,
            context,
        )

        # Calculate evidence summary
        domains = set()
        for ev in claim.supporting_evidence:
            domains.add(ev.source_id)  # Would need source lookup for actual domain

        report = VerificationReport(
            claim_id=claim.claim_id,
            outcome=outcome,
            final_status=final_status,
            final_support_level=final_support,
            validator_results={
                k: ValidationResult(**v) if isinstance(v, dict) else v
                for k, v in validator_results["validator_results"].items()
            },
            all_validators_passed=all_passed,
            contradictions=contradictions,
            unresolved_contradictions=unresolved,
            formal_results=formal_results,
            supporting_evidence_count=len(claim.supporting_evidence),
            contradicting_evidence_count=len(claim.contradicting_evidence),
            independent_domains_count=len(domains),
            limitations=claim.limitations,
            assumptions=claim.assumptions,
            decisions=claim.verification_history,
            metadata={
                "verification_time_ms": (time.time() - start_time) * 1000,
                "policy_mode": self.policy.mode.value,
                "existing_claims_checked": len(existing_claims) if existing_claims else 0,
            },
        )

        # Record in history
        self._verification_history.append(report)

        return report

    async def _run_formal_verification(
        self,
        claim: Claim,
        context: VerificationContext,
    ) -> Dict[str, Any]:
        """Run formal verification if applicable."""
        results = {}

        if not self.formal_engine:
            return results

        # Try arithmetic verification
        arith_problem = self.formal_engine.verify_arithmetic_claim(claim)
        if arith_problem:
            result = await self.formal_engine.verify(arith_problem)
            results["arithmetic"] = result.to_dict()

        # Try temporal verification
        temp_problem = self.formal_engine.verify_temporal_claim(claim)
        if temp_problem:
            result = await self.formal_engine.verify(temp_problem)
            results["temporal"] = result.to_dict()

        return results

    def _determine_outcome(
        self,
        claim: Claim,
        all_validators_passed: bool,
        validator_results: Dict[str, Any],
        unresolved_contradictions: List[Contradiction],
        critical_contradictions: List[Contradiction],
        formal_results: Dict[str, Any],
        context: VerificationContext,
    ) -> tuple[VerificationOutcome, VerificationStatus, SupportLevel]:
        """Determine final verification outcome based on policy and results."""

        # Critical contradictions override everything
        if critical_contradictions and self.policy.stop_on_critical_contradiction:
            return (
                VerificationOutcome.CONTRADICTED,
                VerificationStatus.CONTRADICTED,
                SupportLevel.CONTRADICTED,
            )

        # Check formal verification failures
        for fr in formal_results.values():
            if fr.get("status") == FormalSolverStatus.UNSATISFIABLE.value:
                return (
                    VerificationOutcome.CONTRADICTED,
                    VerificationStatus.CONTRADICTED,
                    SupportLevel.CONTRADICTED,
                )

        # Count independent sources
        independent_sources = claim.independent_domains
        # Also check validator results
        indep_result = validator_results["validator_results"].get("independent_sources", {})
        if indep_result.get("passed"):
            metadata = indep_result.get("metadata", {})
            independent_sources = metadata.get("domains", [])

        source_count = len(set(independent_sources))

        # Determine support level
        if source_count >= self.policy.strong_threshold:
            support_level = SupportLevel.STRONG
        elif source_count >= self.policy.moderate_threshold:
            support_level = SupportLevel.MODERATE
        elif source_count >= self.policy.weak_threshold:
            support_level = SupportLevel.WEAK
        else:
            support_level = SupportLevel.INSUFFICIENT

        # Map to status and outcome
        if not all_validators_passed:
            if self.policy.mode == PipelineMode.STRICT:
                return (
                    VerificationOutcome.INSUFFICIENT_EVIDENCE,
                    VerificationStatus.INSUFFICIENT_EVIDENCE,
                    SupportLevel.INSUFFICIENT,
                )
            # In standard/lenient, we still assign based on support
            pass

        if support_level == SupportLevel.STRONG:
            return (
                VerificationOutcome.VERIFIED,
                VerificationStatus.VERIFIED,
                SupportLevel.STRONG,
            )
        elif support_level == SupportLevel.MODERATE:
            return (
                VerificationOutcome.CONDITIONALLY_VERIFIED,
                VerificationStatus.MODERATE_SUPPORT,
                SupportLevel.MODERATE,
            )
        elif support_level == SupportLevel.WEAK:
            return (
                VerificationOutcome.CONDITIONALLY_VERIFIED,
                VerificationStatus.WEAK_SUPPORT,
                SupportLevel.WEAK,
            )
        else:
            if self.policy.auto_reject_unsupported:
                return (
                    VerificationOutcome.REJECTED,
                    VerificationStatus.REJECTED,
                    SupportLevel.INSUFFICIENT,
                )
            return (
                VerificationOutcome.INSUFFICIENT_EVIDENCE,
                VerificationStatus.INSUFFICIENT_EVIDENCE,
                SupportLevel.INSUFFICIENT,
            )

    def get_verification_history(self) -> List[VerificationReport]:
        return list(self._verification_history)

    def get_claim_verifications(self, claim_id: str) -> List[VerificationReport]:
        return [r for r in self._verification_history if r.claim_id == claim_id]


class VerificationService:
    """High-level service for verification operations."""

    def __init__(self, pipeline: VerificationPipeline):
        self.pipeline = pipeline

    async def verify_and_update_claim(
        self,
        claim: Claim,
        evidence_store: EvidenceStore,
        existing_claims: Optional[List[Claim]] = None,
        memory_facts: Optional[List[Dict[str, Any]]] = None,
    ) -> Claim:
        """Verify a claim and return updated claim with verification decision."""
        report = await self.pipeline.verify_claim(
            claim, evidence_store, existing_claims, memory_facts
        )

        # Apply the verification decision to the claim
        if report.final_status != claim.status:
            # Update claim directly (transition_status creates decision internally)
            claim = claim.transition_status(
                new_status=report.final_status,
                support_level=report.final_support_level,
                reason=f"Pipeline verification: {report.outcome.value}",
                evidence_reviewed=[e.evidence_id for e in claim.supporting_evidence],
                assumptions_considered=[a.assumption_id for a in claim.assumptions],
                validator="pipeline",
            )

        return claim

    async def verify_report_claims(
        self,
        claims: List[Claim],
        evidence_store: EvidenceStore,
        memory_facts: Optional[List[Dict[str, Any]]] = None,
    ) -> List[VerificationReport]:
        """Verify all claims in a report."""
        reports = []
        for claim in claims:
            report = await self.pipeline.verify_claim(
                claim, evidence_store, claims, memory_facts
            )
            reports.append(report)
        return reports


__all__ = [
    "DEFAULT_POLICY",
    "PipelineMode",
    "VerificationOutcome",
    "VerificationPolicy",
    "VerificationContext",
    "VerificationReport",
    "VerificationPipeline",
    "VerificationService",
]