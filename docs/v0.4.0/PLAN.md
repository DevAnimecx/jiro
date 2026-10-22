# JIRO v0.4.0 — Engine Track Plan

**Status:** Proposed. Engine sub-plan. Supersedes the PDF's engine sections; `MASTER_PLAN.md` is authoritative for product scope.
**See also:** [MASTER_PLAN.md](MASTER_PLAN.md) for the full product scope (agent fabric, parameter layer, 2x/3x claims).
**Baseline:** `jirosearch 0.3.3`, 50 test modules, 9 built-in web engines, 6 plugin engines, 12 social platforms, 19 MCP tools.
**Scope:** search engine core, parametric output layer, delivery surfaces.
**Track split:** Track A covers the durable runtime, cognitive memory, research repository, and agent orchestration already recorded in `JIRO_V040_PHASE3_HANDOFF.md` and `JIRO_V040_PHASE4_HANDOFF.md`; its verification layer lives in `jiro/verification/`. This document is Track B, the three-phase engine plan. v0.4.0 ships when both tracks pass their gates.

---

## 0. Verdict on the source plan

The direction is right. Six claims need correction, and two would damage credibility if published.

| # | Source claim | Repository reality | v0.4.0 decision |
|---|---|---|---|
| 1 | "Inject a temporary LoRA / attention matrix into `model.layers.gated_cross_attention`" | JIRO has no model runtime. `pyproject.toml` ships FastAPI, httpx, numpy — no torch, no transformers, no GPU path. Remote providers (OpenAI, Anthropic, Gemini) expose no attention hooks. Ollama exposes no attention hooks either. | **Cut from core.** Replace with *parametric context packets*: bounded, typed, citable evidence with measured token counts, plus an optional embedding export for runtimes that accept prompt embeddings. No claim of weight injection. |
| 2 | "Sub-300ms concurrent streaming parameter loops"; "parallel scraping < 120ms"; "hard ceiling 250ms" | Remote SERP latency dominates and is not under JIRO's control. A single cold Google fetch is typically several hundred milliseconds before parsing. 250ms end-to-end across 9 engines is not achievable on real networks. | **Replace with tiered, measured SLOs** (§6). Cache-hit, warm fanout, and cold fanout get separate targets. Publish p50/p95/p99, never a single best-case number. |
| 3 | MCP protocol version `"2027.1.0"` | MCP versions are date strings. The repo negotiates `2025-03-26` and `2024-11-05` (`jiro/mcp.py:47`). There is no 2027.1.0. | **Keep date-versioned negotiation.** Optionally add the newest published revision that passes conformance. Never invent a version. |
| 4 | "9 engines" including `twitter` | 9 built-in *web* engines: google, bing, duckduckgo, brave, youtube, amazon, ebay, yandex, baidu. Six plugin engines. Social is a separate subsystem of 12 platforms, not a search engine. | **State the real inventory.** Graph nodes route to web engines, plugin engines, or social actions by explicit type. |
| 5 | `jirosearch/src/intelligence/parallel_engine.py` | Path does not exist. Some equivalent primitives do: `search/intent.py` (rule-based classifier), `search/multiquery.py` (parallel explicit queries), `search/hybrid.py` (RRF + rerank), `orchestration/graph.py` (agent DAG, different domain). | **Build `jiro/graph/`** and reuse the primitives listed in Appendix B. Do not create a second agent-DAG. |
| 6 | Guardrails via "numerical variance spike" detection | Variance checks catch broken tensors, not adversarial content. The real attack is indirect prompt injection in scraped text, plus cache poisoning and cost amplification. | **Rebuild the threat model** (§7). Keep tensor sanity checks, but label them reliability checks, not a security boundary. |

Two smaller claims also get dropped: "zero-copy CPU-GPU memory" (there is no GPU path in core) and "sub-millisecond execution envelope" (that is scheduler overhead, not end-to-end latency).

### One-sentence repositioning

> JIRO v0.4.0 turns a fan-out search proxy into a **budgeted query-graph runtime that returns compact, citable evidence packets** instead of raw page dumps.

That sentence is defensible, demonstrable, and still a real category shift from "nine scrapers in a trench coat."

---

## 1. Invariants and non-goals

These do not change across all three phases.

### Invariants

1. **Local-first and MIT.** No mandatory cloud dependency, no telemetry by default, no phone-home.
2. **No breaking changes.** `/v1/search`, `/v1/scrape`, `/v1/ai/*`, the 19 existing MCP tools, and SDK shapes keep working. New behavior is additive and flag-gated.
3. **Web content is data, never instruction.** Scraped text cannot grant capabilities, change budgets, or alter graph structure.
4. **Every derived token has provenance.** Each packet segment cites a source id or is explicitly marked `unsourced`.
5. **Budgets are hard.** Deadline, fanout, request count, byte count, and token count are enforced, not advisory.
6. **Honest attribution survives.** `engine_requested` / `engine_substituted` semantics from 0.3.3 remain intact through the graph layer.
7. **Parametric features are off by default** until Phase 2 gates pass. Kill switch at config, tenant, and request level.

### Non-goals

- Training, fine-tuning, or hosting a foundation model.
- Injecting weights into third-party APIs.
- Bypassing paywalls, logins, or CAPTCHAs at scale.
- A general workflow engine. The graph is for retrieval, extraction, and evidence assembly.
- Bundling or reselling residential proxies. Existing proxy configuration stays operator-supplied.
- Replacing Track A's agent orchestration. Different problem, different contract.

---

## 2. Target architecture

```
+-------------------------------------------------------------+
| L4  Surfaces        REST  /v1/graph/*   MCP  search_graph    |
|                     SDKs  Python/TS/Go   CLI  jiro graph     |
+-------------------------------------------------------------+
| L3  Evidence        SourceRecord -> EvidenceStore -> Packet  |
|                     taint, dedupe, rank, token budget        |
+-------------------------------------------------------------+
| L2  Graph runtime   Planner -> Validator -> Wave scheduler   |
|                     hedging, single-flight, deadline, cancel |
+-------------------------------------------------------------+
| L1  Primitives      intent, engines, scrape, hybrid, verify  |
|                     cache, robots, SSRF guard, rate limits   |
+-------------------------------------------------------------+
```

### 2.1 Core contracts

These are the only shapes phases share. Freeze them in Phase 1; extend only by adding optional fields.

```python
# jiro/graph/model.py
@dataclass(frozen=True)
class Budget:
    deadline_ms: int = 2500
    max_nodes: int = 12
    max_depth: int = 3
    max_fanout: int = 6
    max_requests: int = 24
    max_bytes: int = 8_000_000
    max_tokens: int = 0          # 0 = no packet budget at graph layer

@dataclass(frozen=True)
class QueryNode:
    id: str
    op: Literal["SEARCH", "FETCH", "EXTRACT", "MERGE", "VERIFY", "COMPRESS", "SYNTHESIZE"]
    inputs: tuple[str, ...]
    engine_policy: tuple[str, ...] = ("auto",)
    query_template: str = ""
    required: bool = True
    evidence: Literal["NONE", "SNIPPET", "PAGE", "MULTI_SOURCE"] = "SNIPPET"
    timeout_ms: int = 1200
    hedge_after_ms: int = 0      # 0 = no hedging

@dataclass(frozen=True)
class QueryGraph:
    schema: str                   # "jiro.graph/v1"
    graph_id: str
    intent: str
    nodes: tuple[QueryNode, ...]
    budget: Budget
    policy: dict                  # locale, freshness, safe_search, allow_domains
```

```python
# jiro/parametric/packet.py
@dataclass(frozen=True)
class SourceRecord:
    id: str
    url: str
    domain: str
    engine: str
    fetched_at: str
    http_status: int
    content_hash: str             # sha256 of normalized text
    trust: Literal["untrusted", "allowlisted"]

@dataclass(frozen=True)
class Segment:
    id: str
    kind: Literal["fact", "quote", "numeric", "entity", "timeline", "gap"]
    text: str
    source_ids: tuple[str, ...]
    confidence: float             # heuristic, documented; never presented as calibrated
    observed_at: str | None
    taint: tuple[str, ...]        # e.g. ("untrusted_web", "injection_suspected")

@dataclass(frozen=True)
class EvidencePacket:
    schema: str                   # "jiro.packet/v1"
    packet_id: str
    query: str
    tokenizer: str
    budget_tokens: int
    used_tokens: int
    segments: tuple[Segment, ...]
    sources: tuple[SourceRecord, ...]
    omitted: dict                 # {"segments": n, "reason": "budget"}
    provenance_digest: str
    embedding: dict | None        # experimental, see 4.3
```

### 2.2 Why a new graph module instead of extending `orchestration/graph.py`

`jiro/orchestration/graph.py` models **agent authority**: capabilities, delegation, approvals, node ownership. Its invariants are security invariants.

The query graph models **network work**: fetch order, fanout, hedging, deadlines, cache keys, byte budgets. Its invariants are cost and latency invariants.

Sharing a base class would force agent permissions into retrieval code and retrieval budgets into agent code. Share only pure validation helpers (cycle detection, topological sort) if duplication becomes real. Keep the contracts separate and say so in the ADR.

---

## 3. Phase 1 — Parallel Query Graph

**Goal:** one request can fan out into a validated, budgeted, observable graph, and return partial results deterministically.

**Shape:** Large. New subpackage, new endpoint, new benchmark harness, no new mandatory dependencies.

### 3.1 Workstream 1A — Baseline before code

You cannot claim a latency win without a "before". Build this first.

- `benchmarks/queries.jsonl` — 200 fixed queries across six classes: factual, news, comparison, entity, how-to, social signal.
- `benchmarks/run.py` — harness that records: commit SHA, config hash, cache state, per-engine latency, result count, token count, error class.
- `benchmarks/report.py` — emits p50/p95/p99, cache hit rate, upstream request count, and a machine-readable JSON artifact.
- A `make bench` (or `python -m benchmarks.run`) target that works offline against recorded fixtures.

**Deliverable:** a baseline report committed under `docs/v0.4.0/benchmarks/baseline.json`. No optimization work starts before this exists.

### 3.2 Workstream 1B — Graph model and planner

- `jiro/graph/model.py` — the contracts above, plus validation: no cycles, depth <= 3, nodes <= 12, fanout <= 6, every non-root node has inputs, every required evidence class is reachable.
- `jiro/graph/planner.py` — deterministic rules first:
  - reuse `jiro/search/intent.py` as the first signal (it already runs in under a millisecond);
  - six templates: `fact_lookup`, `news_check`, `comparison`, `entity_profile`, `howto`, `social_signal`;
  - optional LLM planner behind `graph.planner.llm_enabled` (default false), output validated against the same schema, capped at 12 nodes, rejected on any schema violation;
  - fallback on any planner failure: a single `SEARCH` node. A bad plan must never be worse than today's behavior.
- `jiro/graph/compiler.py` — template + slots -> `QueryGraph`, with stable node ids derived from content, not random UUIDs, so cache keys and tests are reproducible.

**Hard rule:** the planner never invents engine names. It selects from the registry, and unknown names fail validation with a 422 listing valid engines.

### 3.3 Workstream 1C — Executor

`jiro/graph/executor.py`:

- Kahn-wave scheduling; nodes in a wave run concurrently under a semaphore.
- Global deadline via `asyncio.timeout`; every node gets `min(node.timeout_ms, remaining)`.
- Cancellation propagates to children when a `REQUIRED` parent fails and to siblings when the completion policy is satisfied.
- Hedging: if `hedge_after_ms > 0`, start a second engine after the delay; first valid result wins, loser is cancelled. Default off.
- Single-flight: identical `(op, normalized_inputs, engine_policy, locale, safe_search, freshness)` collapses to one in-flight task.
- Budget accountant: increments requests, bytes, and elapsed; refuses to start a node that would exceed `max_requests` or `max_bytes`.
- Partial results: `REQUIRED` failure fails the graph with a structured error; `OPTIONAL` failure records a `gap` and continues.
- Deterministic output ordering: results sorted by `(wave, node_id, rank)` after merge, so the same run yields the same payload.

`jiro/graph/operators.py` adapts existing code; it does not reimplement engines:

| Operator | Backend |
|---|---|
| `SEARCH` | `SearchOrchestrator` (with existing fallback + honesty metadata) |
| `FETCH` | `ScrapingClient` + `RobotsManager` + `jiro.security` SSRF guard |
| `EXTRACT` | existing extract/readability path, normalized to `SourceRecord` |
| `MERGE` | `HybridSearcher` RRF + `RelevanceScorer` |
| `VERIFY` | `jiro/verification/pipeline.py`, optional |
| `COMPRESS` | Phase 2 only; returns input unchanged in Phase 1 |
| `SYNTHESIZE` | `jiro/ai/llm.py`, optional, flag-gated |

### 3.4 Workstream 1D — Surface and observability

- `POST /v1/graph/search` — body: query, budget overrides (clamped), `plan_only`, `include_sources`.
- `POST /v1/graph/plan` — dry-run planner; returns the graph without executing. This is the debugging tool and the contract test surface.
- Response envelope: `graph`, `results`, `sources`, `timings` (per node), `gaps`, `budget_used`, `engine_attribution`.
- `jiro/graph/metrics.py` — counters and histograms on the existing telemetry path: nodes started/completed/failed, hedge wins, cancellations, budget refusals, cache hits.
- Tracing: one span per graph, one child span per node, attributes for engine and cache state.

### 3.5 Phase 1 exit gates

All must pass, on the committed benchmark suite, with the artifact attached to the release PR.

| Gate | Test | Threshold |
|---|---|---|
| G1.1 Schema safety | `tests/test_graph_model.py` | cycles, depth > 3, fanout > 6, unknown engine all rejected with 422 |
| G1.2 Planner latency | `tests/test_graph_planner_bench.py` | p95 < 25 ms, rules-only, 200 queries |
| G1.3 Planner determinism | 100 repeated runs | byte-identical graph JSON |
| G1.4 Deadline | `tests/test_graph_executor.py` | 100% of runs return within deadline + 100 ms |
| G1.5 No orphan tasks | executor test using `asyncio.all_tasks()` | zero survivors after every cancellation path |
| G1.6 Cache-hit path | benchmark, warm cache | p95 < 150 ms |
| G1.7 Fanout path | benchmark, 4 engines, cold | p95 recorded and published; target < 2.0 s |
| G1.8 Upstream efficiency | repeated workload | >= 25% fewer upstream requests than 0.3.3 baseline |
| G1.9 Compatibility | full existing suite | all pass, unchanged |
| G1.10 Honest attribution | graph test | `engine_substituted` present on every fallback result |

If G1.7 misses badly, do not hide it. Publish the measured number and adjust the phase 2 scope; the graph still ships if G1.8 and G1.9 hold.

---

## 4. Phase 2 — Parametric Context Layer

**Goal:** convert graph output into a compact, cited, token-budgeted packet, with an optional embedding export that local runtimes can consume.

**Shape:** Large. New subpackage, optional `[parametric]` extra, no changes to existing response shapes.

### 4.1 What replaces DIaaP

The PDF's DIaaP wanted to turn web text into attention values. That requires owning the forward pass. JIRO does not.

Ship the part that is real and measurable:

1. **Packetization** — evidence-dense segments, each cited, under a hard token budget.
2. **Compression** — remove boilerplate and duplication before spending tokens, not after.
3. **Optional vector export** — segment embeddings for retrieval, clustering, or runtimes that accept prompt embeddings.
4. **No weight claims.**

This still delivers the promised outcome — "less context bloat, more knowledge per token" — without lying about the mechanism.

### 4.2 Compression pipeline

`jiro/parametric/extract.py`:
- normalize HTML -> text using the existing parser path;
- drop nav, cookie banners, related-links blocks, and repeated site chrome (selectolax + heuristics, with fixtures);
- split into sentences, preserving character offsets for verbatim quotes;
- classify: `numeric` (number + unit + date), `quote`, `entity`, `timeline`, `fact`;
- reject content shorter than a quality floor (e.g. < 40 chars after normalization) and record a `gap`.

`jiro/parametric/compress.py`:
- dedupe near-identical sentences with SimHash (64-bit, Hamming <= 3), keep the earliest/allowlisted source;
- score each candidate: `relevance * novelty * freshness * source_prior`;
- fill the budget with a greedy knapsack: highest score per token first, never split a quote;
- keep at least one segment per required evidence class, or emit an explicit `gap` segment;
- compute `used_tokens` with a real tokenizer when available (`tiktoken` in the optional extra), otherwise a calibrated estimator whose error is documented and tested against the real tokenizer on the fixture set.

`jiro/parametric/packet.py`:
- validate schema, enforce `used_tokens <= budget_tokens`, require `source_ids` on every non-`gap` segment;
- compute `provenance_digest = sha256(canonical_json(sources) + segments)`;
- stable serialization for reproducible tests.

### 4.3 Optional embedding export (experimental)

`jiro/parametric/embeddings.py`:
- reuse the existing `EmbeddingModel` (MiniLM-class, 384 dims) rather than adding a new model;
- output float16 little-endian, either `.npy` or base64 in the packet, with `model`, `dim`, `dtype`, `normalized` fields;
- document exactly what it is: **retrieval vectors, not attention injections**;
- provide `docs/v0.4.0/ADAPTER_SPEC.md` describing how a runtime that supports prompt embeddings (soft-prompt prefix) can consume the vectors, marked experimental, with a reference script for one runtime only;
- if that reference script cannot demonstrate a measurable benefit, the feature ships as `experimental` and stays out of the launch narrative.

### 4.4 Packet safety

`jiro/parametric/guard.py`:
- taint propagation: every segment from a non-allowlisted domain carries `untrusted_web`;
- injection heuristics tag `injection_suspected` (imperative instructions aimed at a model, hidden text, encoded payloads). Tag, do not silently rewrite. The caller decides;
- synthesis is opt-in and runs with no tool access; its system prompt delimits evidence and its output is a fixed schema. A hijacked synthesis call can produce bad text, not an action;
- quote integrity: verbatim segments must match the source span exactly; a mismatch is a hard error;
- tensor sanity (for embedding export only): finite values, expected shape, norm bounds, dtype check. Named `sanity`, not `security`;
- per-tenant packet isolation: packet ids are unguessable, storage is scoped exactly like memory (`owner_key_id` from auth context, never the payload).

### 4.5 Phase 2 exit gates

| Gate | Test | Threshold |
|---|---|---|
| G2.1 Token reduction | benchmark vs raw markdown | >= 55% fewer tokens at equal answer quality |
| G2.2 Quality non-inferiority | LLM judge + 50 human spot checks | lower 95% CI no worse than -2 points on a 1-10 rubric |
| G2.3 Citation coverage | packet validator | 100% of non-gap segments have >= 1 valid source id |
| G2.4 Budget enforcement | property test | 0 packets over budget across 10k generated cases |
| G2.5 Packet build latency | 20 KB / 200 KB fixtures | p95 < 150 ms / < 400 ms |
| G2.6 Determinism | 100 runs | identical packet bytes excluding `packet_id` and timestamps |
| G2.7 Injection suite | 40 adversarial pages + 20 human-reviewed synthesis cases | 0 critical: every payload flagged, no tool execution or budget change derived from page text, 0 untainted packets |
| G2.8 Quote integrity | fixture set | 100% verbatim match or hard error |
| G2.9 Kill switch | config + tenant + request | feature fully disabled at each level |

If G2.1 and G2.2 conflict, quality wins. A 40% reduction with no quality loss beats 60% with a measurable drop.

---

## 5. Phase 3 — Production Surface, Guardrails, Release

**Goal:** make the graph and packet usable from Cursor, Claude Desktop, and code, survive abuse and soak, and ship with reproducible evidence.

**Shape:** Medium-to-large. Mostly integration, hardening, docs, and release engineering.

### 5.1 MCP surface

- Add three tools, additive only:
  - `search_graph` — execute a budgeted graph, return results + packet summary;
  - `plan_search` — dry-run plan, no network;
  - `get_packet` — fetch a packet by id, scoped to the caller.
- Keep the existing 19 tools unchanged.
- Advertise structured output schemas; support progress notifications per node and cancellation.
- Transport: JSON-RPC over stdio and streamable HTTP. SSE is a streaming response mode, not the protocol. Do not describe JIRO as "JSON-RPC over SSE".
- Negotiate the newest published protocol revision that passes the conformance suite. Keep `2025-03-26` working. Do not invent a version string.
- Add `docs/v0.4.0/MCP.md` with copy-paste configs for Cursor, Claude Desktop, and OpenCode, matching the clients already detected by `jiro/mcp_setup.py`.

### 5.2 REST, SDK, CLI

- OpenAPI entries for `/v1/graph/*` and packet schemas; regenerate the Python/TS/Go SDKs with the existing scripts.
- CLI: `jiro graph plan "..."`, `jiro graph run "..." --budget 1200`, `jiro packet show <id>`.
- Error catalog: stable codes for plan invalid, budget exhausted, deadline exceeded, partial result, tainted packet.

### 5.3 Abuse and cost controls

Graph fanout is a cost amplifier. Treat it as one.

- Per-key limits: graphs/minute, nodes/graph, requests/graph, bytes/graph.
- Cost estimate returned in `plan_only` so a caller can see the amplification before running.
- Global circuit breaker on upstream error rate; per-engine breakers already exist and must be respected by graph nodes.
- Cache namespace isolation: no cross-tenant packet reuse for authenticated callers; document the anonymous namespace limitation exactly as memory does.
- Replay protection on packet ids.

### 5.4 Hardening

- Red-team the injection suite with obfuscated payloads: zero-width characters, HTML comments, alt text, JSON-LD, Markdown link titles.
- SSRF regression tests through `FETCH` nodes (private IP, DNS rebinding, redirect chains, metadata endpoints). Reuse `jiro/security.py`; do not add a second guard.
- 24-hour soak: steady 10 RPS mixed cached/fanout, memory growth < 5%, no fd leaks, no unbounded task growth.
- Load test: 50 RPS cache-hit, 10 RPS 4-engine fanout; publish p95/p99 and error rate.
- Dependency audit (`pip-audit`) and secret scan in CI, matching the existing workflow.

### 5.5 Release engineering

- Version bump to `0.4.0` in `pyproject.toml`, `jiro/__init__.py`, npm, SDKs, OpenAPI, dashboard banner.
- `CHANGELOG.md` entry with measured numbers and links to the benchmark artifact.
- `docs/v0.4.0/RELEASE.md`: what shipped, what is experimental, what was cut and why.
- Feature flags: `graph.enabled=true` for the new endpoint; `parametric.enabled=false` until the operator opts in; `parametric.embeddings=false` by default.
- Rollback: flags off restores 0.3.3 behavior without a downgrade; document the exact config.

### 5.6 Phase 3 exit gates

| Gate | Test | Threshold |
|---|---|---|
| G3.1 MCP conformance | `tests/test_mcp_graph.py` + conformance suite | all pass; old tools unchanged |
| G3.2 Injection red team | 100-case suite | 0 critical, >= 95% flagged |
| G3.3 SSRF | graph FETCH tests | 0 bypasses |
| G3.4 Soak | 24 h | memory growth < 5%, 0 unhandled exceptions |
| G3.5 Load | 50/10 RPS | p99 within published SLO, error rate < 1% |
| G3.6 Compatibility | full suite + golden API snapshots | 0 breaking diffs |
| G3.7 Docs | review checklist | quickstart works from a clean machine |
| G3.8 Rollback | flag-off test | identical to 0.3.3 contract |

---

## 6. SLOs and measurement protocol

The PDF's numbers are replaced with tiered targets. All are **targets to validate**, not marketing promises, until the Phase 1 artifact exists.

| Path | Metric | Target | Why it is achievable |
|---|---|---|---|
| Plan compile (rules) | p95 | <= 25 ms | local regex/trie work, no network |
| Cached single query | p95 | <= 120 ms | SQLite hit + serialization |
| Warm 4-engine fanout | p95 | <= 1.8 s | network-bound, parallel, some cache |
| Cold 9-engine fanout | p95 | <= 3.5 s | measured, not promised |
| Default hard deadline | wall clock | 2.5 s | enforced by executor |
| Packet build, 20 KB | p95 | <= 150 ms | CPU-only, numpy |
| Packet build, 200 KB | p95 | <= 400 ms | CPU-only, bounded |
| Token reduction | vs raw markdown | >= 55% | measured on the fixed suite |
| Upstream request reduction | repeated workload | >= 25% | single-flight + node cache |
| 5xx rate | rolling 1 h | < 1% | error budget |

**Protocol**

1. Fixed hardware profile recorded in the artifact (CPU, RAM, OS, Python).
2. Fixed query suite, fixed fixtures, fixed cache state per run: `cold`, `warm-query`, `warm-node`.
3. Report p50/p95/p99 and n. Never a single run.
4. Every published number links to a JSON artifact and a reproduction command.
5. Do not compare against competitor marketing numbers. Compare against JIRO 0.3.3 on the same machine.
6. Token counts use a named tokenizer. If the estimator is used, publish its error rate.

**What "sub-300ms" can honestly mean:** scheduler + cache + packet assembly overhead on a warm path. It cannot mean a cold live-web fanout. Say it that way or do not say it.

---

## 7. Security model

### 7.1 Threat table

| Threat | Vector | Control | Test |
|---|---|---|---|
| Indirect prompt injection | Page text tells the model to ignore instructions, exfiltrate, or call tools | Content enters as tainted data segments; injection heuristics tag; synthesis is opt-in, has no tool access, and is isolated by a fixed schema; no tool call is ever derived from page text | 100-case red team, G3.2 |
| SSRF | Graph `FETCH` reaches internal services | Existing `jiro/security.py` guard on every fetch, including redirects; no second path | G3.3 |
| Cache poisoning | Attacker seeds a shared cache key with false content | Per-tenant namespaces; content hash + provenance digest; TTL by class; allowlist separate from cache | cache tests |
| Cost amplification | Caller submits a 12-node, 6-fanout graph repeatedly | Per-key graph/node/request/byte limits; `plan_only` cost estimate; global breaker | load test |
| Provenance laundering | Derived segment loses its source | Packet validator requires source ids; quote spans verified; `unsourced` is explicit | G2.3, G2.8 |
| Cross-tenant packet read | Guessable id or unscoped store | Unguessable ids; owner from auth context, never payload; same rule as memory ADR 0002 | security test |
| Adversarial numerics | NaN/Inf/oversized vectors in embedding export | Finite check, shape check, norm bound, dtype check | unit test |
| Tool confusion via MCP | A tool description is spoofed by page content | Tool list is static; page content never enters tool metadata; server instructions are fixed | conformance test |

### 7.2 What is explicitly not a control

- "Variance spike" detection is not an injection defense. It catches broken math.
- A classifier is not a boundary. Tagging plus isolation plus no-instruction-execution is the boundary.
- Majority vote across engines is not verification. Contradictions stay visible.
- Model confidence is not calibration.

---

## 8. Compatibility and migration

- All new endpoints are additive. No existing route changes shape.
- New response fields go under `meta` or a new envelope, never at the top level of an existing response.
- MCP tool list only grows. Existing names, argument schemas, and result shapes are frozen.
- `MultiQuerySearcher` stays public. Internally, `/v1/search/multi` may be reimplemented on top of the graph in Phase 3, but only if golden tests prove identical output ordering and dedupe behavior.
- Python floor stays 3.11. New deps are optional extras: `[parametric]` for tokenizer/embedding export, `[perf]` for uvloop. Core install stays lean.
- `uvloop` is evaluated, not assumed. It is Linux/macOS only; Windows falls back to the default loop. Benchmark before recommending.

---

## 9. Risk register

| Risk | Likelihood | Impact | Trigger | Mitigation |
|---|---|---|---|---|
| DIaaP expectations exceed what packets deliver | High | High | Launch copy implies weight injection | Rename in all copy; publish §4.1; demo tokens saved, not "attention" |
| Graph latency worse than linear due to engine rate limits | Medium | High | G1.7 misses | Reduce default fanout to 3; hedge only on explicit request; cache-first |
| Injection bypasses heuristics | Medium | High | Red team finds a critical case | Quarantine suspicious packets; require allowlist for high-trust use; never auto-execute |
| Token estimator drift | Medium | Medium | Error > 5% on fixtures | Ship `tiktoken` in the extra; fail closed to estimator with disclosed error |
| Scope creep into a workflow engine | High | Medium | Requests for arbitrary user-defined nodes | Fixed operator set; custom nodes rejected; document the boundary |
| Existing tests destabilized | Medium | High | Any regression in 50 modules | Additive-only rule; golden snapshots; CI gate |
| Benchmark numbers not reproducible | Medium | High | Different hardware | Artifact includes hardware + config + commit; reproduction script |
| MCP spec drift | Low | Medium | New revision changes transport | Date-version negotiation; conformance suite; keep old revision |

---

## 10. Gate summary

| Phase | Name | Ships | Must pass before next phase |
|---|---|---|---|
| 1 | Parallel Query Graph | `jiro/graph/`, `/v1/graph/*`, benchmark baseline | G1.1-G1.10 |
| 2 | Parametric Context Layer | `jiro/parametric/`, packets, embedding export (experimental) | G2.1-G2.9 |
| 3 | Production Surface & Release | MCP tools, SDK/CLI, hardening, `0.4.0` | G3.1-G3.8 |

**Sequencing rule:** no Phase 2 code merges before the Phase 1 baseline artifact exists. No Phase 3 launch copy mentions a number that is not in the artifact.

**Calendar:** this plan deliberately does not assign weeks. The PDF's 10-week schedule is an ordering, not a commitment. Dates are set after the Phase 1 baseline, because the baseline determines whether the fanout targets are realistic.

---

## 11. Launch narrative

Keep the category shift, drop the impossible physics.

**Headline:** *Search that returns evidence, not essays.*

**Three claims, each with a number from the artifact:**

1. **Fewer upstream requests.** The graph reuses nodes and collapses duplicates; measured reduction vs 0.3.3 on the fixed suite.
2. **Fewer tokens, same answers.** Packets cut context by >= 55% with non-inferior quality on the published rubric.
3. **Every line is citable.** 100% of non-gap segments carry a source id and a provenance digest.

**What not to say:**
- "LoRA injection", "attention values", "weight updates", "zero-context-overhead model extensions".
- "Sub-300ms" without naming the path (warm cache vs cold fanout).
- "9 engines" without saying web engines, and without mentioning plugins and social separately.
- "2027-ready". Ship behavior, not a year.

**Demo script (5 minutes):**
1. `jiro graph plan "compare EU AI Act enforcement timelines"` — show the DAG, cost estimate, no network.
2. Run it. Show per-node timings, one engine failure degrading to a gap, and the final packet.
3. Show token count vs raw markdown for the same sources.
4. Click a citation back to the exact source span.
5. Turn the parametric flag off; show 0.3.3 behavior returns.

---

## 12. Kickoff — first five actions

1. Commit `benchmarks/queries.jsonl` and `benchmarks/run.py`; record the 0.3.3 baseline. Nothing else starts first.
2. Freeze `jiro/graph/model.py` and its validation tests. Add ADR 0012 for the graph contract once reviewed.
3. Implement the six rule templates and `plan_only`. Ship the dry-run endpoint before the executor.
4. Implement wave scheduling with deadline and cancellation on a fake operator set. Prove G1.4 and G1.5 before touching live engines.
5. Wire `SEARCH` and `FETCH` to the existing orchestrator and SSRF guard; run the full suite; publish the first real fanout number, even if it is slow.

Rule for the whole track: a gate that has not been run is not a gate that passed. Attach the artifact.

---

## Appendix A — Corrected MCP schema

Replaces the PDF's `"mcp_protocol_version": "2027.1.0"`.

```json
{
  "name": "search_graph",
  "description": "Execute a budgeted parallel search graph over JIRO's engines and return cited results plus a compact evidence packet. Use plan_search first to inspect cost.",
  "inputSchema": {
    "type": "object",
    "properties": {
      "query": { "type": "string", "description": "Natural-language goal or reasoning step." },
      "depth": { "type": "string", "enum": ["standard", "deep"], "default": "standard" },
      "max_tokens": { "type": "integer", "minimum": 128, "maximum": 8000, "default": 1200 },
      "deadline_ms": { "type": "integer", "minimum": 250, "maximum": 15000, "default": 2500 },
      "include_sources": { "type": "boolean", "default": true }
    },
    "required": ["query"],
    "additionalProperties": false
  },
  "outputSchema": {
    "type": "object",
    "properties": {
      "graph_id": { "type": "string" },
      "packet": { "$ref": "#/$defs/EvidencePacket" },
      "gaps": { "type": "array", "items": { "type": "string" } },
      "budget_used": { "type": "object" }
    }
  }
}
```

Rules:
- `additionalProperties: false` on inputs.
- Every numeric input has bounds.
- Output references a versioned schema, not an inline shape.
- No field named after a mechanism JIRO does not implement.

---

## Appendix B — Existing code reuse map

| Existing module | Use in v0.4.0 | Change |
|---|---|---|
| `jiro/search/intent.py` | Planner signal, first pass | Import only; no behavior change |
| `jiro/search/multiquery.py` | Reference for merge/dedupe/rerank | Keep public; optionally reimplement on graph in Phase 3 |
| `jiro/search/hybrid.py` | `MERGE` operator, RRF + rerank | Import only |
| `jiro/scraping/engines.py` | `SEARCH` operator, registry, fallback, health | Import only |
| `jiro/scraping/client.py` | `FETCH` transport | Import only |
| `jiro/security.py` | SSRF guard on every fetch | Reuse; add tests, no fork |
| `jiro/robots.py` | Robots compliance on fetch | Reuse |
| `jiro/cache.py` | Node-level cache, single-flight storage | Add key builder; do not change existing keys |
| `jiro/search/embeddings.py` | Embedding export | Reuse model path |
| `jiro/verification/pipeline.py` | `VERIFY` operator | Import only |
| `jiro/ai/llm.py` | Optional planner and synthesis | Import only, flag-gated |
| `jiro/orchestration/graph.py` | Validation patterns only | No inheritance; see §2.2 |
| `jiro/mcp.py` | Tool registration, negotiation | Additive tools |
| `jiro/telemetry.py`, `tracing.py` | Metrics and spans | Add graph instruments |

---

## Appendix C — File manifest

### Phase 1

```
jiro/graph/__init__.py
jiro/graph/model.py
jiro/graph/planner.py
jiro/graph/compiler.py
jiro/graph/executor.py
jiro/graph/operators.py
jiro/graph/cache.py
jiro/graph/metrics.py
jiro/server/routers/graph.py
tests/test_graph_model.py
tests/test_graph_planner.py
tests/test_graph_executor.py
tests/test_graph_api.py
benchmarks/queries.jsonl
benchmarks/run.py
benchmarks/report.py
docs/v0.4.0/benchmarks/baseline.json
```

### Phase 2

```
jiro/parametric/__init__.py
jiro/parametric/packet.py
jiro/parametric/extract.py
jiro/parametric/compress.py
jiro/parametric/embeddings.py
jiro/parametric/guard.py
tests/test_parametric_packet.py
tests/test_parametric_compress.py
tests/test_parametric_guard.py
tests/test_parametric_bench.py
docs/v0.4.0/PACKET.md
docs/v0.4.0/ADAPTER_SPEC.md
```

### Phase 3

```
jiro/mcp.py                      (additive tools)
jiro/mcp_http.py                 (transport, if needed)
jiro/server/routers/graph.py     (final shapes, limits)
tests/test_mcp_graph.py
tests/test_graph_security.py
tests/test_graph_soak.py
benchmarks/soak.py
benchmarks/load.py
docs/v0.4.0/MCP.md
docs/v0.4.0/RELEASE.md
docs/adr/0011-parametric-context-boundary.md
```

---

## Appendix D — Definition of done

v0.4.0 is done when all of the following are true:

1. `/v1/graph/search` runs a validated graph and returns partial results under deadline.
2. A packet cuts tokens by >= 55% with non-inferior quality on the published rubric.
3. Every non-gap segment cites a source; quote spans verify.
4. The injection red team has zero critical findings.
5. MCP exposes the new tools without changing the old ones.
6. Full compatibility suite passes; flag-off behavior matches 0.3.3.
7. Benchmark artifacts are committed and reproducible.
8. Launch copy contains no claim that the code cannot demonstrate.

