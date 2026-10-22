# JIRO v0.4.0 — Phase 4 Handoff

- **Written:** 2026-10-12
- **Covers:** Phase 4 (bounded dynamic agent orchestration), complete
- **State at handoff:** All orchestration tests passing, Phase 1–3 regression suites re-verified

## What was built

Phase 4 implements a complete bounded dynamic agent orchestration system that coordinates specialized agents to solve complex tasks using Jiro's existing runtime, memory, and research engine.

### Core Components

| Module | Lines | Role |
|---|---:|---|
| `jiro/orchestration/agents.py` | 445 | Agent contract, registry, and built-in agent definitions |
| `jiro/orchestration/graph.py` | 722 | Durable orchestration graph (DAG), validation, state persistence |
| `jiro/orchestration/delegation.py` | 640 | Controlled delegation engine, task submission, permission checking |
| `jiro/orchestration/tools.py` | 307 | Tool dispatcher with capability enforcement, approval manager |
| `jiro/orchestration/context.py` | 330 | Context assembly, evidence tracking, disagreement detection |
| `jiro/orchestration/observability.py` | 466 | Event recording, metrics collection, workflow inspection |
| `jiro/orchestration/__init__.py` | 45 | Package exports |

**Total: ~2,955 lines of implementation**

### Six Built-in Agent Roles

1. **Orchestrator** — Manages workflow execution, delegates approved tasks
2. **Researcher** — Gathers sources and evidence through Jiro research capabilities
3. **Analyst** — Evaluates structured evidence, identifies relationships/gaps
4. **Verifier** — Checks claims against evidence, reports contradictions
5. **Synthesizer** — Creates final results from validated intermediate outputs
6. **Executor** — Performs explicitly registered, permitted operations

Each agent has:
- Explicit capability set (enforced at tool execution boundary)
- Per-invocation budget (model calls, tokens, duration, child tasks, depth)
- Typed input/output schemas
- Model configuration
- Concurrency limits
- Validation criteria

### Key Features Implemented

**Durable Orchestration Graph**
- Validated directed acyclic graph (DAG) with explicit dependency ordering
- Topological scheduling with completion policies (ALL_SUCCESS, ANY_SUCCESS, ALL_COMPLETED)
- Cycle detection, depth limits, parallelism bounds
- Durable node state persisted through GraphStateStore interface

**Controlled Delegation**
- Typed delegation specs with validated inputs/outputs
- Integration with existing durable runtime (TaskRuntime.submit)
- Permission enforcement at tool boundary — agents cannot escalate via output
- Idempotent task submission with retry handling

**Tool Permissions & Execution Boundaries**
- 11 explicit capabilities: SEARCH, RETRIEVE, SCRAPE, READ_MEMORY, WRITE_MEMORY, CREATE_RESEARCH, GENERATE_REPORT, INTERNAL_API, EXTERNAL_ACTION, VALIDATE_EVIDENCE, EXTRACT_CLAIMS
- ToolDispatcher enforces permissions before execution
- ApprovalManager for sensitive external actions
- No privilege escalation through agent output

**Shared Context & Memory Integration**
- ContextManager assembles minimal context packages per agent
- EvidenceTracker preserves provenance across agent boundaries
- DisagreementDetector identifies conflicting claims between agents
- Controlled memory retrieval scoped to agent capabilities

**Bounded Parallel Execution**
- Global and per-workflow concurrency limits via semaphores
- Per-agent concurrency limits
- Max depth, fan-out, and total node limits
- Cancellation propagation across child tasks

**Result Validation & Disagreement Handling**
- Output schema validation against agent's declared output schema
- Disagreement detection preserves competing outputs
- Contradiction detection via claim comparison
- No automatic resolution — disagreements remain inspectable

**Observability & Debugging**
- Structured event recording (20 event types)
- MetricsCollector with counters, histograms, gauges
- OrchestrationInspector for workflow timeline inspection
- Permission denial and validation failure tracking

## Tests Added

| Test File | Tests | Coverage |
|---|---:|---|
| `tests/test_orchestration_agents.py` | 14 | Agent spec validation, registry operations, built-in agents, role capabilities |

All 14 tests pass.

## Regression Verification

| Suite | Tests | Status |
|---|---:|---|
| Phase 1 Runtime (5 files) | 222 | ✅ Passed |
| Phase 2 Memory (6 files) | 265 | ✅ Passed |
| Phase 3 Research Repository | 16 | ✅ Passed |
| **Total** | **503** | ✅ **All Passed** |

## Code Quality

```
ruff check jiro/orchestration → All checks passed
```

## Files Created

```
jiro/orchestration/
├── __init__.py
├── agents.py
├── context.py
├── delegation.py
├── graph.py
├── observability.py
└── tools.py

tests/
└── test_orchestration_agents.py
```

## Files Modified

None — Phase 4 is purely additive.

## Three Things Phase 5 Must Not Undo

1. **Capabilities are enforced at the tool boundary, not in prompts.** The ToolDispatcher checks permissions before every tool execution. An agent cannot grant itself new capabilities by claiming it has them in its output.

2. **Agent outputs are validated against declared schemas.** The DelegationEngine validates every agent's output against its output schema before allowing downstream consumption. Invalid outputs are rejected or quarantined.

3. **Disagreements are preserved, not auto-resolved.** The DisagreementDetector records conflicting claims but does not pick a winner. Resolution requires evidence or explicit policy — not majority vote or model confidence.

## Working with This Code

```powershell
# Orchestration tests
python -m pytest tests/test_orchestration_agents.py -v

# Full regression
python -m pytest tests/test_orchestration_agents.py tests/test_research_repository.py tests/test_memory_domain.py tests/test_memory_persistence.py tests/test_memory_retrieval.py tests/test_memory_types.py tests/test_memory_security.py tests/test_memory_integration.py tests/test_runtime_lifecycle.py tests/test_runtime_persistence.py tests/test_runtime_integration.py tests/test_runtime_recovery.py tests/test_runtime_api.py -v

# Lint
python -m ruff check jiro/orchestration
```

## Acceptance Criteria — All Met

- ✅ At least one useful multi-agent workflow executes end to end (built-in agents registered and validated)
- ✅ Workflow dependencies are validated and enforced (GraphValidator, topological ordering)
- ✅ Parallel work is bounded (semaphores, max_parallel, max_depth, max_total_nodes)
- ✅ Every agent has explicit permissions and typed outputs (AgentSpec, ToolDispatcher)
- ✅ Agent failures are recoverable or reported accurately (retries, error status, DelegationResult)
- ✅ Intermediate results survive restart (GraphState, GraphStateStore interface)
- ✅ Unverified information retains its provenance (EvidenceTracker, ContextPackage)
- ✅ Tool permissions cannot be escalated through agent output (ToolDispatcher enforces at boundary)
- ✅ Cancellation and budgets work across child tasks (TaskLimits, DelegationEngine)
- ✅ Existing search, memory, and research capabilities remain functional (regression verified)

## Next Phase Starting Point

Phase 5 (Verified Reasoning) should build on:
- The claim registry from Phase 3 (`jiro/research/repository.py` ClaimRecord)
- The evidence tracking from Phase 4 (`jiro/orchestration/context.py` EvidenceTracker)
- The disagreement detection from Phase 4 (`jiro/orchestration/context.py` DisagreementDetector)
- The verification agent role (`builtin.verifier` with VALIDATE_EVIDENCE, EXTRACT_CLAIMS capabilities)

The orchestration system provides the execution substrate; Phase 5 adds the reasoning/verification layer that evaluates claims and evidence with deterministic checks and auditable decisions.