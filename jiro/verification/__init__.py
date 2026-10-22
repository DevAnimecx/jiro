"""Verification module for Jiro v0.4.0.

Provides verified reasoning capabilities including:
- Structured claim representation with provenance
- Deterministic validators for evidence quality
- Contradiction detection across claims and memory
- Optional formal verification via SMT solvers
- Integrated verification pipeline
"""

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
    BaseValidator,
    CitationResolutionValidator,
    ContradictionValidator,
    EvidenceRelevanceValidator,
    EvidenceStore,
    IndependentSourceValidator,
    SchemaValidator,
    TemporalConsistencyValidator,
    ValidationResult,
    ValidatorPipeline,
)
from jiro.verification.contradictions import (
    ClaimPair,
    ConflictSeverity,
    Contradiction,
    ContradictionDetector,
    ContradictionType,
    CrossMemoryContradictionDetector,
)
from jiro.verification.formal import (
    FormalProblem,
    FormalResult,
    FormalSolver,
    FormalSolverStatus,
    FormalVerificationEngine,
    MockFormalSolver,
    Z3Solver,
)
from jiro.verification.pipeline import (
    DEFAULT_POLICY,
    PipelineMode,
    VerificationContext,
    VerificationOutcome,
    VerificationPolicy,
    VerificationPipeline,
    VerificationReport,
    VerificationService,
)

__all__ = [
    # Claims
    "Assumption",
    "Claim",
    "ClaimType",
    "EvidenceRef",
    "Limitation",
    "SupportLevel",
    "TemporalScope",
    "VerificationDecision",
    "VerificationStatus",
    # Validators
    "ArithmeticValidator",
    "BaseValidator",
    "CitationResolutionValidator",
    "ContradictionValidator",
    "EvidenceRelevanceValidator",
    "EvidenceStore",
    "IndependentSourceValidator",
    "SchemaValidator",
    "TemporalConsistencyValidator",
    "ValidationResult",
    "ValidatorPipeline",
    # Contradictions
    "ClaimPair",
    "ConflictSeverity",
    "Contradiction",
    "ContradictionDetector",
    "ContradictionType",
    "CrossMemoryContradictionDetector",
    # Formal
    "FormalProblem",
    "FormalResult",
    "FormalSolver",
    "FormalSolverStatus",
    "FormalVerificationEngine",
    "MockFormalSolver",
    "Z3Solver",
    # Pipeline
    "DEFAULT_POLICY",
    "PipelineMode",
    "VerificationContext",
    "VerificationOutcome",
    "VerificationPolicy",
    "VerificationPipeline",
    "VerificationReport",
    "VerificationService",
]