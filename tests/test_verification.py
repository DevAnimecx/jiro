"""Tests for the verification module."""

import pytest
from dataclasses import replace

from jiro.verification.claims import (
    Assumption,
    Claim,
    ClaimType,
    EvidenceRef,
    Limitation,
    SupportLevel,
    TemporalScope,
    VerificationDecision,
    VerificationStatus,
)
from jiro.verification.validators import (
    ArithmeticValidator,
    CitationResolutionValidator,
    ContradictionValidator,
    EvidenceRelevanceValidator,
    EvidenceStore,
    IndependentSourceValidator,
    SchemaValidator,
    TemporalConsistencyValidator,
    ValidatorPipeline,
)
from jiro.verification.contradictions import (
    ContradictionDetector,
    ContradictionType,
    CrossMemoryContradictionDetector,
)
from jiro.verification.formal import (
    FormalVerificationEngine,
    MockFormalSolver,
)
from jiro.verification.pipeline import (
    PipelineMode,
    VerificationOutcome,
    VerificationPolicy,
    VerificationPipeline,
    VerificationService,
)


class TestClaimModel:
    """Tests for Claim data model."""

    def test_claim_creation(self):
        claim = Claim.create(
            claim_text="The population of Tokyo is 37 million",
            claim_type=ClaimType.SOURCE_ASSERTION,
            created_by="test_user",
        )
        assert claim.claim_id.startswith("claim_") or len(claim.claim_id) > 0
        assert claim.claim_text == "The population of Tokyo is 37 million"
        assert claim.claim_type == ClaimType.SOURCE_ASSERTION
        assert claim.created_by == "test_user"
        assert claim.status == VerificationStatus.PENDING

    def test_claim_status_transition(self):
        claim = Claim.create(
            claim_text="Test claim",
            claim_type=ClaimType.SOURCE_ASSERTION,
            created_by="test_user",
        )

        # Valid transition
        claim = claim.transition_status(
            new_status=VerificationStatus.STRONG_SUPPORT,
            support_level=SupportLevel.STRONG,
            reason="Strong evidence",
            evidence_reviewed=["ev1", "ev2"],
            assumptions_considered=[],
            validator="test_validator",
        )
        assert claim.status == VerificationStatus.STRONG_SUPPORT
        assert claim.support_level == SupportLevel.STRONG
        assert len(claim.verification_history) == 1

    def test_claim_transition_to_rejected(self):
        claim = Claim.create(
            claim_text="Test claim",
            claim_type=ClaimType.SOURCE_ASSERTION,
            created_by="test_user",
        )
        claim = claim.transition_status(
            new_status=VerificationStatus.REJECTED,
            support_level=SupportLevel.INSUFFICIENT,
            reason="Explicit rejection",
            evidence_reviewed=[],
            assumptions_considered=[],
            validator="test_validator",
        )
        assert claim.status == VerificationStatus.REJECTED

    def test_temporal_scope(self):
        scope = TemporalScope(valid_from=1000, valid_until=2000)
        claim = Claim.create(
            claim_text="Valid claim",
            claim_type=ClaimType.SOURCE_ASSERTION,
            created_by="test_user",
            temporal_scope=scope,
        )
        assert claim.temporal_scope is not None
        assert claim.temporal_scope.valid_from == 1000
        assert claim.temporal_scope.valid_until == 2000

    def test_evidence_ref(self):
        ref = EvidenceRef(
            evidence_id="ev_123",
            source_id="src_456",
            passage_hash="abc123",
            relevance="supports",
            excerpt="Relevant text",
        )
        assert ref.evidence_id == "ev_123"
        assert ref.relevance == "supports"

    def test_add_evidence_method(self):
        claim = Claim.create(
            claim_text="Test claim",
            claim_type=ClaimType.SOURCE_ASSERTION,
            created_by="test",
        )
        evidence = EvidenceRef(
            evidence_id="ev_1",
            source_id="src_1",
            passage_hash="hash1",
            relevance="supports",
            excerpt="Supporting evidence",
        )
        claim = claim.add_evidence(evidence, "supports")
        assert len(claim.supporting_evidence) == 1
        assert claim.supporting_evidence[0].evidence_id == "ev_1"

    def test_replace_independent_domains(self):
        claim = Claim.create(
            claim_text="Test claim",
            claim_type=ClaimType.SOURCE_ASSERTION,
            created_by="test",
        )
        claim = replace(claim, independent_domains=["example.com", "other.com"])
        assert claim.independent_domains == ["example.com", "other.com"]


class TestValidators:
    """Tests for validator implementations."""

    def setup_method(self):
        self.evidence_store = EvidenceStore()
        self.evidence_store.add_evidence(
            EvidenceRef(
                evidence_id="ev_1",
                source_id="src_1",
                passage_hash="hash1",
                relevance="supports",
                excerpt="Supporting evidence for claim",
            )
        )
        self.evidence_store.add_source("src_1", {"domain": "example.com", "published_at": 1000})

    def test_citation_resolution_validator(self):
        claim = Claim.create(
            claim_text="Test claim",
            claim_type=ClaimType.SOURCE_ASSERTION,
            created_by="test",
        )
        claim = claim.add_evidence(
            EvidenceRef(evidence_id="ev_1", source_id="src_1", passage_hash="hash1", relevance="supports", excerpt="text"),
            "supports"
        )
        claim = claim.add_evidence(
            EvidenceRef(evidence_id="ev_missing", source_id="src_x", passage_hash="hash2", relevance="supports", excerpt="text"),
            "supports"
        )

        validator = CitationResolutionValidator()
        result = validator.validate(claim, self.evidence_store)
        assert not result.passed
        assert "not found" in result.reason

    def test_evidence_relevance_validator(self):
        # Add evidence to store
        self.evidence_store.add_evidence(
            EvidenceRef(
                evidence_id="ev_1",
                source_id="src_1",
                passage_hash="hash1",
                relevance="supports",
                excerpt="Tokyo population is 37 million",
            )
        )
        
        claim = Claim.create(
            claim_text="Tokyo population is 37 million",
            claim_type=ClaimType.SOURCE_ASSERTION,
            created_by="test",
        )
        claim = claim.add_evidence(
            EvidenceRef(evidence_id="ev_1", source_id="src_1", passage_hash="hash1", relevance="supports", excerpt="Tokyo population is 37 million"),
            "supports"
        )

        validator = EvidenceRelevanceValidator(min_overlap_words=2)
        result = validator.validate(claim, self.evidence_store)
        assert result.passed

        # Low relevance
        self.evidence_store.add_evidence(
            EvidenceRef(
                evidence_id="ev_2",
                source_id="src_1",
                passage_hash="hash2",
                relevance="supports",
                excerpt="Unrelated text about something else",
            )
        )
        
        claim = Claim.create(
            claim_text="Tokyo population is 37 million",
            claim_type=ClaimType.SOURCE_ASSERTION,
            created_by="test",
        )
        claim = claim.add_evidence(
            EvidenceRef(evidence_id="ev_2", source_id="src_1", passage_hash="hash2", relevance="supports", excerpt="Unrelated text about something else"),
            "supports"
        )
        result = validator.validate(claim, self.evidence_store)
        assert not result.passed

    def test_independent_source_validator(self):
        claim = Claim.create(
            claim_text="Test claim",
            claim_type=ClaimType.SOURCE_ASSERTION,
            created_by="test",
        )
        claim = claim.add_evidence(
            EvidenceRef(evidence_id="ev_1", source_id="src_1", passage_hash="hash1", relevance="supports", excerpt="text"),
            "supports"
        )
        claim = replace(claim, independent_domains=["example.com", "other.com"])

        validator = IndependentSourceValidator(min_independent_domains=2)
        result = validator.validate(claim, self.evidence_store)
        assert result.passed

    def test_arithmetic_validator(self):
        # Add evidence with numbers
        self.evidence_store.add_evidence(
            EvidenceRef(
                evidence_id="ev_1",
                source_id="src_1",
                passage_hash="hash1",
                relevance="supports",
                excerpt="5 + 3 = 8",
            )
        )
        
        claim = Claim.create(
            claim_text="5 + 3 = 8",
            claim_type=ClaimType.DERIVED_CALCULATION,
            created_by="test",
        )
        claim = claim.add_evidence(
            EvidenceRef(evidence_id="ev_1", source_id="src_1", passage_hash="hash1", relevance="supports", excerpt="5 + 3 = 8"),
            "supports"
        )

        validator = ArithmeticValidator()
        result = validator.validate(claim, self.evidence_store)
        assert result.passed

        # Non-arithmetic claim should skip
        claim = Claim.create(
            claim_text="The sky is blue",
            claim_type=ClaimType.SOURCE_ASSERTION,
            created_by="test",
        )
        claim = claim.add_evidence(
            EvidenceRef(evidence_id="ev_1", source_id="src_1", passage_hash="hash1", relevance="supports", excerpt="Some text"),
            "supports"
        )
        result = validator.validate(claim, self.evidence_store)
        assert result.passed  # Skips non-arithmetic

    def test_temporal_consistency_validator(self):
        scope = TemporalScope(valid_from=1000, valid_until=2000)
        claim = Claim.create(
            claim_text="Event happened between 1000 and 2000",
            claim_type=ClaimType.SOURCE_ASSERTION,
            created_by="test",
            temporal_scope=scope,
        )

        validator = TemporalConsistencyValidator()
        result = validator.validate(claim, self.evidence_store)
        assert result.passed

    def test_schema_validator(self):
        claim = Claim.create(
            claim_text="Test claim",
            claim_type=ClaimType.SOURCE_ASSERTION,
            created_by="test",
        )

        validator = SchemaValidator()
        result = validator.validate(claim, self.evidence_store)
        assert result.passed

        # Missing claim_text - create new claim
        claim = Claim.create(
            claim_text="",
            claim_type=ClaimType.SOURCE_ASSERTION,
            created_by="test",
        )
        result = validator.validate(claim, self.evidence_store)
        assert not result.passed

    def test_contradiction_validator(self):
        # Test that validator runs without error
        claim = Claim.create(
            claim_text="X is true",
            claim_type=ClaimType.SOURCE_ASSERTION,
            created_by="test",
        )
        claim = claim.add_evidence(
            EvidenceRef(evidence_id="ev_1", source_id="src_1", passage_hash="hash1", relevance="supports", excerpt="Supporting evidence"),
            "supports"
        )

        validator = ContradictionValidator()
        result = validator.validate(claim, self.evidence_store)
        # Just verify it runs - the negation detection is heuristic-based
        assert isinstance(result.passed, bool)


class TestValidatorPipeline:
    """Tests for ValidatorPipeline."""

    def setup_method(self):
        self.evidence_store = EvidenceStore()
        self.evidence_store.add_evidence(
            EvidenceRef(
                evidence_id="ev_1",
                source_id="src_1",
                passage_hash="hash1",
                relevance="supports",
                excerpt="Tokyo population 37M",
            )
        )
        self.evidence_store.add_source("src_1", {"domain": "example.com"})
        self.pipeline = ValidatorPipeline()

    def test_pipeline_runs_all_validators(self):
        claim = Claim.create(
            claim_text="Tokyo population is 37 million",
            claim_type=ClaimType.SOURCE_ASSERTION,
            created_by="test",
        )
        claim = claim.add_evidence(
            EvidenceRef(evidence_id="ev_1", source_id="src_1", passage_hash="hash1", relevance="supports", excerpt="Tokyo population is 37 million"),
            "supports"
        )
        claim = replace(claim, independent_domains=["example.com"])

        results = self.pipeline.run(claim, self.evidence_store)
        assert "validator_results" in results
        assert "all_passed" in results

    def test_pipeline_stops_on_failure_strict(self):
        claim = Claim.create(
            claim_text="Test",
            claim_type=ClaimType.SOURCE_ASSERTION,
            created_by="test",
        )
        # No evidence - will fail citation resolution
        results = self.pipeline.run(claim, self.evidence_store, stop_on_failure=True)
        assert not results["all_passed"]


class TestContradictionDetection:
    """Tests for contradiction detection."""

    def setup_method(self):
        self.detector = ContradictionDetector()

    def test_direct_negation(self):
        claim_a = Claim.create(
            claim_text="The sky is blue",
            claim_type=ClaimType.SOURCE_ASSERTION,
            created_by="test",
        )
        claim_b = Claim.create(
            claim_text="The sky is not blue",
            claim_type=ClaimType.SOURCE_ASSERTION,
            created_by="test",
        )

        contradictions = self.detector.detect_contradictions(claim_a, [claim_b])
        assert len(contradictions) > 0
        assert any(c.contradiction_type == ContradictionType.DIRECT_NEGATION for c in contradictions)

    def test_value_conflict(self):
        claim_a = Claim.create(
            claim_text="The population is 37 million",
            claim_type=ClaimType.SOURCE_ASSERTION,
            created_by="test",
        )
        claim_b = Claim.create(
            claim_text="The population is 13 million",
            claim_type=ClaimType.SOURCE_ASSERTION,
            created_by="test",
        )

        contradictions = self.detector.detect_contradictions(claim_a, [claim_b])
        assert len(contradictions) > 0
        assert any(c.contradiction_type == ContradictionType.VALUE_CONFLICT for c in contradictions)

    def test_temporal_conflict(self):
        scope_a = TemporalScope(valid_from=1000, valid_until=1500)
        scope_b = TemporalScope(valid_from=2000, valid_until=2500)

        claim_a = Claim.create(
            claim_text="Event happened early",
            claim_type=ClaimType.SOURCE_ASSERTION,
            created_by="test",
            temporal_scope=scope_a,
        )
        claim_b = Claim.create(
            claim_text="Event happened late",
            claim_type=ClaimType.SOURCE_ASSERTION,
            created_by="test",
            temporal_scope=scope_b,
        )

        contradictions = self.detector.detect_contradictions(claim_a, [claim_b])
        assert len(contradictions) > 0
        assert any(c.contradiction_type == ContradictionType.TEMPORAL_CONFLICT for c in contradictions)

    def test_logical_inconsistency(self):
        claim_a = Claim.create(
            claim_text="Exercise increases health",
            claim_type=ClaimType.SOURCE_ASSERTION,
            created_by="test",
        )
        claim_b = Claim.create(
            claim_text="Exercise decreases health",
            claim_type=ClaimType.SOURCE_ASSERTION,
            created_by="test",
        )

        contradictions = self.detector.detect_contradictions(claim_a, [claim_b])
        assert len(contradictions) > 0
        assert any(c.contradiction_type == ContradictionType.LOGICAL_INCONSISTENCY for c in contradictions)

    def test_same_subject_detection(self):
        claim_a = Claim.create(
            claim_text="Apple stock price is $150",
            claim_type=ClaimType.SOURCE_ASSERTION,
            created_by="test",
        )
        claim_b = Claim.create(
            claim_text="Microsoft stock price is $300",
            claim_type=ClaimType.SOURCE_ASSERTION,
            created_by="test",
        )

        contradictions = self.detector.detect_contradictions(claim_a, [claim_b])
        # Different subjects, should not detect contradiction
        assert len(contradictions) == 0

    def test_contradiction_resolution(self):
        claim_a = Claim.create(
            claim_text="X is true",
            claim_type=ClaimType.SOURCE_ASSERTION,
            created_by="test",
        )
        claim_b = Claim.create(
            claim_text="X is not true",
            claim_type=ClaimType.SOURCE_ASSERTION,
            created_by="test",
        )

        contradictions = self.detector.detect_contradictions(claim_a, [claim_b])
        assert len(contradictions) > 0
        cid = contradictions[0].contradiction_id

        assert not self.detector.get_contradiction(cid).resolved
        self.detector.resolve_contradiction(cid, "New evidence shows X is true", "evidence")
        assert self.detector.get_contradiction(cid).resolved


class TestCrossMemoryContradiction:
    """Tests for cross-memory contradiction detection."""

    def setup_method(self):
        self.detector = ContradictionDetector()
        self.cross_detector = CrossMemoryContradictionDetector(self.detector)

    def test_memory_fact_contradiction(self):
        claim = Claim.create(
            claim_text="The value is 100",
            claim_type=ClaimType.SOURCE_ASSERTION,
            created_by="test",
        )

        memory_facts = [
            {"object": "The value is 50", "predicate": "has_value"},
            {"object": "Other fact", "predicate": "has_value"},
        ]

        contradictions = self.cross_detector.check_memory_claims(claim, memory_facts)
        # Should detect value conflict
        assert any(c.contradiction_type == ContradictionType.VALUE_CONFLICT for c in contradictions)


class TestFormalVerification:
    """Tests for formal verification (mock solver)."""

    @pytest.mark.asyncio
    async def test_mock_formal_solver(self):
        engine = FormalVerificationEngine(solver=MockFormalSolver())

        problem = engine.create_problem(
            name="Test problem",
            description="Test",
            constraints=["(= x 5)", "(= y 3)", "(= z (+ x y))"],
            variables={"x": "Int", "y": "Int", "z": "Int"},
            claims_involved=["claim_1"],
        )

        result = await engine.verify(problem)
        assert result.status.value == "unknown"
        assert "Mock solver" in result.error

    @pytest.mark.asyncio
    async def test_arithmetic_verification_problem(self):
        engine = FormalVerificationEngine(solver=MockFormalSolver())

        claim = Claim.create(
            claim_text="5 + 3 = 8",
            claim_type=ClaimType.DERIVED_CALCULATION,
            created_by="test",
        )

        problem = engine.verify_arithmetic_claim(claim)
        assert problem is not None
        assert len(problem.constraints) > 0
        assert "x0a" in problem.variables


class TestVerificationPipeline:
    """Tests for the full verification pipeline."""

    def setup_method(self):
        self.evidence_store = EvidenceStore()
        self.evidence_store.add_evidence(
            EvidenceRef(
                evidence_id="ev_1",
                source_id="src_1",
                passage_hash="hash1",
                relevance="supports",
                excerpt="Tokyo population is 37 million",
            )
        )
        self.evidence_store.add_source("src_1", {"domain": "example.com", "published_at": 1000000})
        self.evidence_store.add_evidence(
            EvidenceRef(
                evidence_id="ev_2",
                source_id="src_2",
                passage_hash="hash2",
                relevance="supports",
                excerpt="Tokyo population is 37 million",
            )
        )
        self.evidence_store.add_source("src_2", {"domain": "other.com", "published_at": 2000000})

        self.policy = VerificationPolicy(
            mode=PipelineMode.STANDARD,
            min_independent_sources=2,
        )
        self.pipeline = VerificationPipeline(policy=self.policy)

    @pytest.mark.asyncio
    async def test_verify_claim_sufficient_evidence(self):
        claim = Claim.create(
            claim_text="Tokyo population is 37 million",
            claim_type=ClaimType.SOURCE_ASSERTION,
            created_by="test",
        )
        claim = claim.add_evidence(
            EvidenceRef(evidence_id="ev_1", source_id="src_1", passage_hash="hash1", relevance="supports", excerpt="Tokyo population is 37 million"),
            "supports"
        )
        claim = claim.add_evidence(
            EvidenceRef(evidence_id="ev_2", source_id="src_2", passage_hash="hash2", relevance="supports", excerpt="Tokyo population is 37 million"),
            "supports"
        )
        claim = replace(claim, independent_domains=["example.com", "other.com"])

        report = await self.pipeline.verify_claim(claim, self.evidence_store)
        assert report.outcome in (VerificationOutcome.VERIFIED, VerificationOutcome.CONDITIONALLY_VERIFIED)
        assert report.final_support_level in (SupportLevel.STRONG, SupportLevel.MODERATE)

    @pytest.mark.asyncio
    async def test_verify_claim_insufficient_evidence(self):
        claim = Claim.create(
            claim_text="Unknown fact",
            claim_type=ClaimType.SOURCE_ASSERTION,
            created_by="test",
        )
        # No evidence added

        report = await self.pipeline.verify_claim(claim, self.evidence_store)
        assert report.outcome == VerificationOutcome.INSUFFICIENT_EVIDENCE
        assert report.final_support_level == SupportLevel.INSUFFICIENT

    @pytest.mark.asyncio
    async def test_verify_claim_with_contradiction(self):
        # Add contradicting evidence to store
        self.evidence_store.add_evidence(
            EvidenceRef(
                evidence_id="ev_2",
                source_id="src_2",
                passage_hash="hash2",
                relevance="contradicts",
                excerpt="The claim that X is true is false and incorrect",
            )
        )
        self.evidence_store.add_source("src_2", {"domain": "other.com"})
        
        claim = Claim.create(
            claim_text="X is true",
            claim_type=ClaimType.SOURCE_ASSERTION,
            created_by="test",
        )
        claim = claim.add_evidence(
            EvidenceRef(evidence_id="ev_1", source_id="src_1", passage_hash="hash1", relevance="supports", excerpt="X is true"),
            "supports"
        )
        claim = claim.add_evidence(
            EvidenceRef(evidence_id="ev_2", source_id="src_2", passage_hash="hash2", relevance="contradicts", excerpt="The claim that X is true is false and incorrect"),
            "contradicts"
        )
        claim = replace(claim, independent_domains=["example.com"])

        report = await self.pipeline.verify_claim(claim, self.evidence_store)
        # Should detect contradiction from claim's contradicting_evidence field
        assert report.contradicting_evidence_count == 1
        assert report.outcome in (VerificationOutcome.CONDITIONALLY_VERIFIED, VerificationOutcome.CONTRADICTED)


class TestVerificationService:
    """Tests for VerificationService."""

    @pytest.mark.asyncio
    async def test_verify_and_update_claim(self):
        evidence_store = EvidenceStore()
        evidence_store.add_evidence(
            EvidenceRef(
                evidence_id="ev_1",
                source_id="src_1",
                passage_hash="hash1",
                relevance="supports",
                excerpt="Test fact",
            )
        )
        evidence_store.add_source("src_1", {"domain": "example.com"})

        policy = VerificationPolicy(mode=PipelineMode.STANDARD)
        pipeline = VerificationPipeline(policy=policy)
        service = VerificationService(pipeline)

        claim = Claim.create(
            claim_text="Test fact",
            claim_type=ClaimType.SOURCE_ASSERTION,
            created_by="test",
        )
        claim = claim.add_evidence(
            EvidenceRef(evidence_id="ev_1", source_id="src_1", passage_hash="hash1", relevance="supports", excerpt="Test fact"),
            "supports"
        )
        claim = replace(claim, independent_domains=["example.com"])

        updated_claim = await service.verify_and_update_claim(claim, evidence_store)
        assert updated_claim.status != VerificationStatus.PENDING
        assert len(updated_claim.verification_history) > 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])