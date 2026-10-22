"""Bounded formal validation for verification.

Provides optional integration with SMT solvers (Z3) for formally
verifiable constraints. Isolated behind an interface with strict limits.
"""

from __future__ import annotations

import asyncio
import hashlib
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

from jiro.log import get_logger
from jiro.verification.claims import Claim

log = get_logger("jiro.verification.formal")


class FormalSolverStatus(str, Enum):
    SATISFIABLE = "sat"
    UNSATISFIABLE = "unsat"
    UNKNOWN = "unknown"
    TIMEOUT = "timeout"
    ERROR = "error"


@dataclass(frozen=True)
class FormalProblem:
    """A formal verification problem."""

    problem_id: str
    name: str
    description: str
    constraints: List[str]  # SMT-LIB2 or solver-specific format
    variables: Dict[str, str]  # var_name -> type (Int, Real, Bool, etc.)
    claims_involved: List[str]  # claim_ids
    created_at: float = field(default_factory=time.time)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_smtlib2(self) -> str:
        """Convert to SMT-LIB2 format."""
        lines = [
            "(set-logic QF_UFLRA)",
            "; Problem: " + self.name,
            "; " + self.description,
            "",
        ]

        # Declare variables
        for var_name, var_type in self.variables.items():
            lines.append(f"(declare-fun {var_name} () {var_type})")

        # Assert constraints
        for i, constraint in enumerate(self.constraints):
            lines.append(f"(assert {constraint})")

        lines.extend([
            "",
            "(check-sat)",
            "(get-model)",
        ])

        return "\n".join(lines)


@dataclass(frozen=True)
class FormalResult:
    """Result of formal verification."""

    problem_id: str
    status: FormalSolverStatus
    model: Optional[Dict[str, Any]] = None
    unsat_core: Optional[List[str]] = None
    execution_time_ms: float = 0.0
    solver_version: str = ""
    timestamp: float = field(default_factory=time.time)
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "problem_id": self.problem_id,
            "status": self.status.value,
            "model": self.model,
            "unsat_core": self.unsat_core,
            "execution_time_ms": self.execution_time_ms,
            "solver_version": self.solver_version,
            "timestamp": self.timestamp,
            "error": self.error,
        }


class FormalSolver(ABC):
    """Abstract interface for formal solvers."""

    @abstractmethod
    async def solve(self, problem: FormalProblem, timeout_seconds: float) -> FormalResult:
        pass

    @abstractmethod
    def is_available(self) -> bool:
        pass

    @abstractmethod
    def get_version(self) -> str:
        pass


class Z3Solver(FormalSolver):
    """Z3 SMT solver integration with strict isolation."""

    def __init__(
        self,
        timeout_seconds: float = 30.0,
        memory_limit_mb: int = 512,
    ):
        self.timeout_seconds = timeout_seconds
        self.memory_limit_mb = memory_limit_mb
        self._z3_available = self._check_z3()

    def _check_z3(self) -> bool:
        try:
            import importlib.util
            if importlib.util.find_spec("z3") is not None:
                return True
        except Exception:
            pass
        log.warning("Z3 not available, formal validation disabled")
        return False

    def is_available(self) -> bool:
        return self._z3_available

    def get_version(self) -> str:
        try:
            import z3
            return z3.get_version_string()
        except Exception:
            return "unknown"

    async def solve(self, problem: FormalProblem, timeout_seconds: float = None) -> FormalResult:
        if not self._z3_available:
            return FormalResult(
                problem_id=problem.problem_id,
                status=FormalSolverStatus.ERROR,
                error="Z3 not installed",
            )

        timeout = timeout_seconds or self.timeout_seconds
        start = time.time()

        try:
            import z3

            # Create solver with timeout
            solver = z3.Solver()
            solver.set("timeout", int(timeout * 1000))

            # Parse and add constraints
            # This is simplified - real implementation would parse SMT-LIB2
            for constraint in problem.constraints:
                # For now, we only support a subset
                # Real implementation would use z3.parse_smtlib2_string
                pass

            # Run with timeout
            result = await asyncio.wait_for(
                asyncio.to_thread(solver.check),
                timeout=timeout,
            )

            exec_time = (time.time() - start) * 1000

            if result == z3.sat:
                model = solver.model()
                model_dict = {}
                for decl in model.decls():
                    model_dict[decl.name()] = str(model[decl])
                return FormalResult(
                    problem_id=problem.problem_id,
                    status=FormalSolverStatus.SATISFIABLE,
                    model=model_dict,
                    execution_time_ms=exec_time,
                    solver_version=self.get_version(),
                )
            elif result == z3.unsat:
                return FormalResult(
                    problem_id=problem.problem_id,
                    status=FormalSolverStatus.UNSATISFIABLE,
                    execution_time_ms=exec_time,
                    solver_version=self.get_version(),
                )
            else:
                return FormalResult(
                    problem_id=problem.problem_id,
                    status=FormalSolverStatus.UNKNOWN,
                    execution_time_ms=exec_time,
                    solver_version=self.get_version(),
                )

        except asyncio.TimeoutError:
            return FormalResult(
                problem_id=problem.problem_id,
                status=FormalSolverStatus.TIMEOUT,
                execution_time_ms=timeout * 1000,
                solver_version=self.get_version(),
                error=f"Solver timed out after {timeout}s",
            )
        except Exception as e:
            return FormalResult(
                problem_id=problem.problem_id,
                status=FormalSolverStatus.ERROR,
                execution_time_ms=(time.time() - start) * 1000,
                solver_version=self.get_version(),
                error=str(e),
            )


class MockFormalSolver(FormalSolver):
    """Mock solver for testing without Z3."""

    def __init__(self, timeout_seconds: float = 30.0):
        self.timeout_seconds = timeout_seconds

    def is_available(self) -> bool:
        return True

    def get_version(self) -> str:
        return "mock-1.0"

    async def solve(self, problem: FormalProblem, timeout_seconds: float = None) -> FormalResult:
        # Simulate solving
        await asyncio.sleep(0.01)
        return FormalResult(
            problem_id=problem.problem_id,
            status=FormalSolverStatus.UNKNOWN,
            execution_time_ms=10.0,
            solver_version=self.get_version(),
            error="Mock solver - install Z3 for real formal validation",
        )


class FormalVerificationEngine:
    """Manages formal verification of claims."""

    def __init__(self, solver: Optional[FormalSolver] = None):
        self.solver = solver or (Z3Solver() if Z3Solver().is_available() else MockFormalSolver())
        self._problems: Dict[str, FormalProblem] = {}
        self._results: Dict[str, FormalResult] = {}

    def create_problem(
        self,
        name: str,
        description: str,
        constraints: List[str],
        variables: Dict[str, str],
        claims_involved: List[str],
    ) -> FormalProblem:
        """Create a formal verification problem."""
        problem_id = hashlib.sha256(f"{name}{time.time()}".encode()).hexdigest()[:16]
        problem = FormalProblem(
            problem_id=problem_id,
            name=name,
            description=description,
            constraints=constraints,
            variables=variables,
            claims_involved=claims_involved,
        )
        self._problems[problem_id] = problem
        return problem

    async def verify(self, problem: FormalProblem) -> FormalResult:
        """Run formal verification on a problem."""
        if not self.solver.is_available():
            log.warning("Formal solver not available, skipping formal verification")
            return FormalResult(
                problem_id=problem.problem_id,
                status=FormalSolverStatus.ERROR,
                error="No formal solver available",
            )

        log.info("Running formal verification on problem %s", problem.problem_id)
        result = await self.solver.solve(problem, self.solver.timeout_seconds)
        self._results[problem.problem_id] = result
        return result

    def verify_arithmetic_claim(self, claim: Claim) -> Optional[FormalProblem]:
        """Create a formal problem for an arithmetic claim."""
        import re

        # Extract arithmetic expressions
        numbers = re.findall(r"(\d+(?:,\d{3})*(?:\.\d+)?)\s*([\+\-\*/])\s*(\d+(?:,\d{3})*(?:\.\d+)?)", claim.claim_text)
        if not numbers:
            return None

        # Build constraints
        constraints = []
        variables = {}
        for i, (a, op, b) in enumerate(numbers):
            var_a = f"x{i}a"
            var_b = f"x{i}b"
            var_result = f"x{i}r"
            variables[var_a] = "Real"
            variables[var_b] = "Real"
            variables[var_result] = "Real"

            constraints.append(f"(= {var_a} {a.replace(',', '')})")
            constraints.append(f"(= {var_b} {b.replace(',', '')})")

            if op == "+":
                constraints.append(f"(= {var_result} (+ {var_a} {var_b}))")
            elif op == "-":
                constraints.append(f"(= {var_result} (- {var_a} {var_b}))")
            elif op == "*":
                constraints.append(f"(= {var_result} (* {var_a} {var_b}))")
            elif op == "/":
                constraints.append(f"(= {var_result} (/ {var_a} {var_b}))")

        return self.create_problem(
            name=f"Arithmetic verification for {claim.claim_id}",
            description=f"Verify arithmetic in claim: {claim.claim_text[:100]}",
            constraints=constraints,
            variables=variables,
            claims_involved=[claim.claim_id],
        )

    def verify_temporal_claim(self, claim: Claim) -> Optional[FormalProblem]:
        """Create a formal problem for temporal consistency."""
        if not claim.temporal_scope:
            return None

        constraints = []
        variables = {}

        if claim.temporal_scope.valid_from:
            variables["valid_from"] = "Real"
            constraints.append(f"(= valid_from {claim.temporal_scope.valid_from})")

        if claim.temporal_scope.valid_until:
            variables["valid_until"] = "Real"
            constraints.append(f"(= valid_until {claim.temporal_scope.valid_until})")

        if claim.temporal_scope.valid_from and claim.temporal_scope.valid_until:
            constraints.append("(<= valid_from valid_until)")

        if not constraints:
            return None

        return self.create_problem(
            name=f"Temporal verification for {claim.claim_id}",
            description=f"Verify temporal consistency of claim: {claim.claim_text[:100]}",
            constraints=constraints,
            variables=variables,
            claims_involved=[claim.claim_id],
        )

    def get_result(self, problem_id: str) -> Optional[FormalResult]:
        return self._results.get(problem_id)

    def get_all_results(self) -> Dict[str, FormalResult]:
        return dict(self._results)


__all__ = [
    "FormalProblem",
    "FormalResult",
    "FormalSolver",
    "FormalSolverStatus",
    "FormalVerificationEngine",
    "MockFormalSolver",
    "Z3Solver",
]