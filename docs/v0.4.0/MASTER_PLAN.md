# JIRO v0.4.0 — Master Plan: The Agent Information Layer

**Status:** Proposed. Supersedes the PDF plan and expands `docs/v0.4.0/PLAN.md`.
**Companion docs:** [PLAN.md](PLAN.md) (engine detail) · [CLAIMS.md](CLAIMS.md) (2x/3x definitions) · [AGENT_MATRIX.md](AGENT_MATRIX.md) (16 clients) · [INSTALL.md](INSTALL.md) (one command).
**Baseline:** `jirosearch 0.3.3` (9 web engines, 6 plugin engines, 12 social platforms, 19 MCP tools, SQLite-first, MIT).
**One line:** Turn JIRO from a search-and-scrape API into the information layer agents live on: one query graph that searches and scrapes together, one evidence compiler that turns the web into bindable parameters, and one agent fabric that connects 15+ clients with a single command.

**North star:** an agent should never have to read a web page to know what the web says.

---

## 0. The shift, in one page

| Dimension | JIRO 0.3.3 | JIRO 0.4.0 |
|---|---|---|
| Unit of work | One search or one scrape | One budgeted query graph |
| Search vs scrape | Separate endpoints | One graph, executed concurrently |
| Output | Raw markdown / JSON rows | Cited evidence packet + optional vectors |
| Web state | Fetched and forgotten | Versioned parameter set with TTL, decay, diff |
| Agent integration | MCP server, manual config per client | MCP server + 15+ client adapters + one command |
| Multi-agent | Isolated calls | Shared session, evidence ledger, handoff |
| Quality | Ranked results | Claims, support, contradictions, provenance |
| Performance story | Cached responses | Measured 2x on multi-source research |
| Efficiency story | Fewer API bills | Measured 3x tokens per verified citation |
| Trust story | "Local-first" | Local-first + reproducible claim artifacts |

### Three promises, each with a test

1. **2x faster on multi-source research.** Not on a single fetch. On the workload agents actually run: several sub-queries, several sources, one cited result. Baseline is JIRO 0.3.3 on the same machine, same network, same sources. Formula and artifact in `CLAIMS.md`.
2. **3x more token-efficient per verified citation.** Not "smaller output". Same answer quality, one third of the tokens needed to support the same number of verified citations. Formula and artifact in `CLAIMS.md`.
3. **15+ agents, one command, one shared evidence session.** Auto-detect, write config, verify handshake, roll back on failure. Median time to first successful tool call under two minutes on a clean machine.

**The rule that makes this credible:** a promise without a committed benchmark artifact is not published. If a gate misses, the number changes, not the benchmark.

### What "internet as a model parameter" means here

It does **not** mean writing weights or attention matrices. JIRO has no forward pass, and ADR 0011 records why that claim is out of scope. It means:

- The web is compiled into a **versioned parameter set**: facts, quotes, numbers, entities, embeddings, freshness, provenance.
- An agent **binds** that parameter set to a model call. Hosted models receive a token-budgeted packet. Local runtimes that support prompt embeddings can receive a vector prefix.
- Parameters **decay** by claim type and **diff** when the web changes, so an agent reads only what changed.
- Every bound parameter is traceable to a URL, a fetch time, and a content hash.

That is an external, addressable parameter layer. It is real, measurable, and useful. It is not a LoRA, and the plan never calls it one.

### Why this plan does not say "AGI"

AGI is not a testable shipping claim. "AGI-level search" is a mood, not a metric. This plan uses a capability ladder instead:

| Level | Capability | Status |
|---|---|---|
| R0 | Snippet search, single source | 0.3.3 |
| R1 | Answer with citations | 0.3.3 |
| R2 | Multi-hop research, contradiction surfacing | partial |
| R3 | Persistent auditable sessions shared across agents, freshness-aware parameters | **v0.4.0 target** |
| R4 | Self-directed monitoring, automatic re-planning on change | partial, post-0.4 |

The ambition is AGI-grade research capability. The shipping claim is R3, because R3 can be tested.

---

## 1. What ships

| Capability | Type | Phase | Why it matters |
|---|---|---|---|
| Query graph runtime | New | 1 | Search and scrape become one concurrent plan |
| Cost-based planner | New | 1 | Chooses plan by latency, cache, and engine health |
| Unified search+scrape operators | Upgrade | 1 | One deadline covers discovery and extraction |
| Structured scraping (schema-first) | Upgrade | 1 | Agents ask for fields, not pages |
| Benchmark + claims harness | New | 1 | Makes 2x/3x falsifiable |
| Evidence packet compiler | New | 2 | Token-bounded, cited, deterministic |
| Claim ledger + verification | New | 2 | Support, contradiction, and provenance per claim |
| Live Parameter Layer (LPL) | New | 2 | Web state as bindable, decaying, diffable parameters |
| Embedding export | Experimental | 2 | Vector binding for local runtimes |
| Watch / monitor | Upgrade | 2 | Standing queries refresh parameters and emit diffs |
| MCP server v2 | Upgrade | 3 | Tools, resources, prompts, progress, cancellation |
| Shared agent sessions | New | 3 | Planner, retriever, verifier, writer share one ledger |
| 15+ client auto-setup | New | 3 | One command, detected clients, verified handshake |
| `jiro doctor` | New | 3 | Diagnose install, config, MCP, search, scrape, packet |
| Claims registry + CI gate | New | 3 | No unverified public numbers |

### Non-goals

- Training or hosting a foundation model.
- Injecting weights into any model.
- A general workflow engine with arbitrary user-defined nodes.
- Bundling or reselling residential proxies.
- Guaranteed bypass of bot protection.
- Calling itself AGI.

### Operating principles

1. **Local-first.** SQLite and a local process by default. No mandatory cloud, no telemetry without opt-in.
2. **Additive only.** Existing endpoints, tools, and SDK shapes keep working.
3. **Web content is data, never instruction.** It cannot grant capability, change budget, or alter a plan.
4. **No hidden crawling.** Speculative prefetch is explicit, bounded, and off by default.
5. **Claims need artifacts.** A public number without a committed benchmark does not ship.
6. **Fail visible.** Gaps, contradictions, substitutions, and partial results are reported, not smoothed over.

---

## 2. Architecture

```
+--------------------------------------------------------------------------------+
| L6  Agent Fabric    15+ client adapters | shared sessions | one-command setup   |
|                     MCP stdio + streamable HTTP | handoff protocol | doctor      |
+--------------------------------------------------------------------------------+
| L5  Parameter Layer Live Parameter Sets: facts, vectors, TTL, decay, diff       |
|                     bind(packet|vectors) | snapshot | merge | watch            |
+--------------------------------------------------------------------------------+
| L4  Evidence         Source -> Claim -> Support -> Packet -> Ledger             |
|                     extraction, dedupe, verification, contradictions           |
+--------------------------------------------------------------------------------+
| L3  Graph Runtime    Planner -> Cost model -> Validator -> Wave scheduler       |
|                     search+scrape fusion, hedging, single-flight, deadline     |
+--------------------------------------------------------------------------------+
| L2  Sense            engines | plugins | social | browser | documents           |
|                     proxies, robots, SSRF guard, rate limits, health           |
+--------------------------------------------------------------------------------+
| L1  Storage          SQLite (default) | Postgres (optional) | cache | runtime   |
+--------------------------------------------------------------------------------+
```

### 2.1 Core contracts

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
    max_tokens: int = 1200

@dataclass(frozen=True)
class QueryNode:
    id: str
    op: Literal["SEARCH", "FETCH", "EXTRACT", "MERGE", "VERIFY", "COMPRESS", "SYNTHESIZE"]
    inputs: tuple[str, ...]
    engine_policy: tuple[str, ...] = ("auto",)
    query_template: str = ""
    schema_ref: str | None = None       # structured extraction contract
    required: bool = True
    evidence: Literal["NONE", "SNIPPET", "PAGE", "MULTI_SOURCE"] = "SNIPPET"
    timeout_ms: int = 1200
    hedge_after_ms: int = 0

@dataclass(frozen=True)
class QueryGraph:
    schema: str                          # "jiro.graph/v1"
    graph_id: str
    intent: str
    nodes: tuple[QueryNode, ...]
    budget: Budget
    policy: dict                         # locale, freshness, safe_search, domains
```

```python
# jiro/parametric/packet.py
@dataclass(frozen=True)
class Source:
    id: str
    url: str
    domain: str
    engine: str
    fetched_at: str
    http_status: int
    content_hash: str
    trust: Literal["untrusted", "allowlisted"]

@dataclass(frozen=True)
class Claim:
    id: str
    text: str
    kind: Literal["fact", "quote", "numeric", "entity", "timeline"]
    source_ids: tuple[str, ...]
    support: Literal["single", "corroborated", "contradicted", "unsupported"]
    observed_at: str | None
    confidence: float                   # heuristic, documented, not calibrated
    taint: tuple[str, ...]

@dataclass(frozen=True)
class EvidencePacket:
    schema: str                          # "jiro.packet/v1"
    packet_id: str
    query: str
    tokenizer: str
    budget_tokens: int
    used_tokens: int
    claims: tuple[Claim, ...]
    sources: tuple[Source, ...]
    contradictions: tuple[str, ...]      # claim id pairs, preserved
    omitted: dict
    provenance_digest: str
    embedding: dict | None
```

```python
# jiro/params/model.py
@dataclass(frozen=True)
class ParameterSet:
    id: str
    session_id: str
    version: int
    created_at: str
    claims: tuple[str, ...]              # claim ids
    sources: tuple[str, ...]
    ttl_policy: dict                     # claim kind -> half-life
    freshness: dict                      # claim id -> observed_at + score
    provenance_digest: str
```

### 2.2 Request lifecycle

1. **Plan.** Intent classifier (rules, <1 ms) selects a template. Cost model scores candidate plans against cache state, engine health, and budget.
2. **Validate.** Cycles, depth, fanout, unknown engines, schema refs, and token budget are rejected before any network call.
3. **Dispatch.** Kahn waves. `SEARCH` and `FETCH` nodes run concurrently. Hedged requests only when asked.
4. **Extract.** Pages become `Source` records. Structured nodes apply a JSON Schema. Failures become `gap` records, not silent omissions.
5. **Verify.** Claims are extracted, matched across sources, and marked single/corroborated/contradicted. Contradictions are preserved.
6. **Compile.** Claims are deduped, ranked, and packed under the token budget. Every claim keeps its source ids.
7. **Bind.** The packet is returned, stored, or bound into a `ParameterSet` with TTL and a version.
8. **Record.** Trace, cost, timings, engine attribution, and gaps are written to the session ledger.

### 2.3 Why the query graph is not the agent graph

`jiro/orchestration/graph.py` models **authority**: capabilities, delegation, approvals, node ownership. Its invariants are security invariants.

The query graph models **network work**: fetch order, fanout, hedging, deadlines, cache keys, byte budgets. Its invariants are cost and latency invariants.

They share pure validation helpers (cycle detection, topological sort) and nothing else. Merging them would put agent permissions into retrieval code and retrieval budgets into agent code.

### 2.4 Module map

```
jiro/graph/          model, planner, cost, compiler, executor, operators, cache, replay
jiro/parametric/     packet, extract, compress, guard, embeddings
jiro/params/         parameter sets, TTL/decay, diff, bind, snapshots
jiro/verification/   claims, contradictions, validators (existing, extended)
jiro/fabric/         client registry, setup writers, doctor, session protocol
jiro/server/routers/ graph.py, params.py, sessions.py
jiro/mcp.py          additive tools/resources/prompts
```

### 2.5 The five bets that make this more than a wrapper

1. **Cost-based planning for web research.** Most tools run a fixed pipeline. JIRO compiles several candidate plans and picks by measured latency, cache probability, engine health, and token yield. This is the database-planner idea applied to the web.
2. **Parameter diff instead of re-reading.** A watch does not resend the web. It sends what changed. Diff is the efficiency engine, and it is only possible because claims are first-class objects.
3. **One ledger, many agents.** Fifteen agents should not run fifteen isolated searches. They share a session, a budget, and an append-only evidence log, then hand off structured briefs.
4. **Claims, not pages.** The unit of output is a claim with support, contradiction, freshness, and a source span. Pages are an implementation detail.
5. **Verified setup, not a snippet.** Detection, merge, parse, handshake, real call, rollback. The install is part of the product, not a README afterthought.

---

## 3. Phase 1 — Substrate: the query graph and the 2x foundation

**Goal:** one request can search and scrape concurrently under a hard budget, with a plan that is chosen by cost rather than habit.

**Shape:** Large. New subpackage, new endpoints, benchmark harness, no new mandatory dependency.

### 3.1 Baseline first

- `benchmarks/queries.jsonl` — 200 fixed queries across factual, news, comparison, entity, how-to, social.
- `benchmarks/workloads/research_5q_10src.json` — the 2x workload: 5 sub-queries, 10 sources, one cited result.
- `benchmarks/run.py` — records commit, config hash, cache state, engine latency, result count, token count, error class.
- `benchmarks/report.py` — p50/p95/p99, cache hit rate, upstream requests, tokens, cost estimate.
- Baseline artifact committed before optimization: `docs/v0.4.0/benchmarks/baseline.json`.

No baseline, no performance work.

### 3.2 Planner and cost model

`jiro/graph/planner.py`:
- Reuse `jiro/search/intent.py` as the first signal.
- Six templates: `fact_lookup`, `news_check`, `comparison`, `entity_profile`, `howto`, `social_signal`.
- Optional LLM planner behind `graph.planner.llm_enabled` (default false). Output validated against the same schema, capped at 12 nodes, rejected on any violation.
- Any planner failure falls back to a single `SEARCH` node. A bad plan is never worse than 0.3.3.

`jiro/graph/cost.py`:
- Estimate each node: expected latency from EWMA, cache hit probability, request cost, token yield.
- Choose among 2-4 candidate plans per template.
- `plan_only` returns the chosen plan, the rejected plans, and the estimate. This is the debugging surface and the cost-transparency feature.

This is the database-planner idea applied to web research: same query, several executable plans, pick by measured cost.

### 3.3 Executor

`jiro/graph/executor.py`:
- Kahn-wave scheduling; per-wave concurrency under a semaphore.
- Global deadline via `asyncio.timeout`; node timeout is `min(node.timeout_ms, remaining)`.
- Cancellation propagates to children and siblings.
- Single-flight on identical node keys.
- Hedging only when `hedge_after_ms > 0`; first valid result wins, loser cancelled.
- Budget accountant refuses nodes that would exceed requests or bytes.
- `REQUIRED` failure fails the graph with a structured error; `OPTIONAL` failure records a gap.
- Deterministic ordering by `(wave, node_id, rank)`.

`jiro/graph/operators.py`:
- `SEARCH` -> existing `SearchOrchestrator`, fallback, health, honest attribution.
- `FETCH` -> `ScrapingClient` + `RobotsManager` + `jiro.security` SSRF guard.
- `EXTRACT` -> readability + optional JSON Schema via existing structured extraction; optional browser fallback for JS-heavy pages and optional document extraction for PDFs in the `[docs]` extra.
- `MERGE` -> `HybridSearcher` RRF + relevance.
- `VERIFY` -> `jiro/verification/pipeline.py`.
- `COMPRESS` -> Phase 2 stub; returns input unchanged in Phase 1.
- `SYNTHESIZE` -> `jiro/ai/llm.py`, opt-in, no tool access.
- Social actions route through `jiro/scraping/social/router.py` as a distinct node type, never disguised as a web engine.

### 3.4 Search + scrape fusion

This is the "at the same time" capability, implemented literally:

- A `SEARCH` node fans out to engines.
- Its results feed `FETCH` nodes in the same graph.
- Fetch and remaining searches run concurrently; the graph does not wait for all searches before starting fetches.
- Extraction schemas are attached to `FETCH` nodes, so a single deadline produces structured fields, not raw pages.
- If a fetch fails, the search snippet remains as lower-grade evidence with an explicit `snippet_only` marker.

### 3.5 Cache and speculation

- Four cache tiers: exact query, semantic query, source content, packet.
- Node key: hash of `(op, normalized_inputs, engine_policy, locale, safe_search, freshness)`.
- Content cache keyed by URL + content hash, with TTL by class.
- Speculative prefetch only for explicit agent follow-ups; default off. No hidden background crawling.
- Cache entries carry provenance; a cache hit never loses `engine_requested` / `engine_substituted`.

### 3.6 Surface

- `POST /v1/graph/search`
- `POST /v1/graph/plan` (dry run, cost estimate, no network)
- `GET /v1/graph/{id}` (status/results for durable runs)
- CLI: `jiro graph plan`, `jiro graph run`, `jiro graph inspect`
- Tracing: one span per graph, one per node; attributes for engine, cache, bytes, tokens.

### 3.7 Phase 1 gates

| Gate | Test | Threshold |
|---|---|---|
| G1.1 Schema safety | `tests/test_graph_model.py` | cycles, depth > 3, fanout > 6, unknown engine rejected 422 |
| G1.2 Planner latency | bench | p95 < 25 ms rules-only |
| G1.3 Determinism | 100 runs | byte-identical graph JSON |
| G1.4 Deadline | executor tests | 100% within deadline + 100 ms |
| G1.5 No orphan tasks | `asyncio.all_tasks()` | zero survivors |
| G1.6 Cache-hit path | bench | p95 < 150 ms |
| G1.7 Fanout path | bench, 4 engines cold | p95 published; target < 2.0 s |
| G1.8 Speed measurement | W1 vs 0.3.3 | ratio recorded; 2x claim published only if >= 2.0x p50 |
| G1.9 Upstream reduction | repeated workload | >= 25% fewer requests |
| G1.10 Compatibility | full suite | all pass |

**Kill criterion:** if G1.8 measures below 1.5x after the executor and cache work, the 2x headline is removed. The graph still ships on efficiency and agent capability. It is not relabeled.

---

## 4. Phase 2 — Intelligence: evidence, claims, and the parameter layer

**Goal:** turn graph output into a cited packet and a live parameter set that agents can bind, diff, and trust.

**Shape:** Large. New subpackages, optional `[parametric]` extra, no changes to existing response shapes.

### 4.1 Evidence compiler

`jiro/parametric/extract.py`:
- Normalize HTML -> text using the existing parser path.
- Drop nav, cookie banners, related links, repeated chrome; fixtures for each.
- Preserve character offsets for verbatim quotes.
- Classify claims: numeric, quote, entity, timeline, fact.
- Reject content below a quality floor; record a gap.

`jiro/parametric/compress.py`:
- SimHash dedupe (64-bit, Hamming <= 3).
- Score = relevance x novelty x freshness x source prior.
- Greedy knapsack under the token budget; never split a quote.
- Keep at least one claim per required evidence class or emit an explicit gap.
- Real tokenizer when available (`tiktoken` in the extra); calibrated estimator otherwise, with published error.

`jiro/parametric/packet.py`:
- Validate schema; enforce `used_tokens <= budget_tokens`.
- Require `source_ids` on every non-gap claim.
- Preserve contradictions as claim-id pairs.
- `provenance_digest = sha256(canonical sources + claims)`.
- Stable serialization for replay and tests.

### 4.2 Claim ledger and verification

- Every claim gets a stable id derived from normalized text + source ids.
- Support levels: `single`, `corroborated`, `contradicted`, `unsupported`.
- Cross-source matching uses numeric/date normalization plus embedding similarity.
- Contradictions are recorded, never auto-resolved.
- Verification pipeline runs deterministic validators first; LLM judge only as an optional second pass.
- The ledger is append-only per session; corrections are new versions, not edits.

### 4.3 Live Parameter Layer

`jiro/params/`:
- `build(session, claims, ttl_policy) -> ParameterSet`
- `bind(set_id, model_profile, budget) -> Binding` (packet for hosted models, vector prefix for local runtimes)
- `decay(set_id, now)` re-ranks claims by freshness half-life.
- `diff(v1, v2)` returns added, removed, changed claims, and changed sources.
- `snapshot(set_id)` freezes an immutable version for reproducibility.
- `merge(a, b)` unions evidence and preserves conflicts.

Freshness half-lives are configuration, not magic: news hours, prices an hour, docs weeks, stable reference months.

**Why diff matters:** an agent watching a topic does not need the whole web again. It needs the delta. Diff is the main lever for the 3x efficiency claim.

### 4.4 Embedding export (experimental)

- Reuse `EmbeddingModel` (MiniLM-class, 384 dims).
- Emit float16 little-endian, `.npy` or base64, with model, dim, dtype, normalized flag.
- Document it as retrieval vectors, not attention edits.
- `ADAPTER_SPEC.md` covers one runtime that accepts prompt embeddings. If it cannot show measurable benefit, it stays `experimental` and out of the launch narrative.

### 4.5 Watch / monitor

- A watch is a saved graph + parameter set + refresh policy.
- Reuses the existing scheduler and durable runtime.
- Emits a diff event when claims change; never a full re-dump.
- Per-watch budgets and quiet hours.
- CLI: `jiro watch add`, `jiro watch list`, `jiro watch diff`.

### 4.6 Phase 2 gates

| Gate | Test | Threshold |
|---|---|---|
| G2.1 Efficiency measurement | W2 vs raw markdown | ratio recorded; 3x claim published only if >= 3.0x tokens per verified citation |
| G2.2 Quality | LLM judge + 50 human checks | non-inferior, lower 95% CI >= -2 points |
| G2.3 Citation coverage | packet validator | 100% of non-gap claims have a source |
| G2.4 Budget | property test | 0 over-budget in 10k cases |
| G2.5 Packet latency | 20 KB / 200 KB | p95 < 150 ms / < 400 ms |
| G2.6 Determinism | 100 runs | identical bytes except ids/timestamps |
| G2.7 Injection | 40 pages + 20 human cases | 0 critical; all flagged; no tool/budget change |
| G2.8 Parameter diff | fixture change set | 100% of changed claims detected, 0 false removals |
| G2.9 Kill switch | config/tenant/request | fully disabled at each level |

**Kill criterion:** if G2.1 measures below 2.2x, the 3x headline is removed. Publish the actual ratio.

---

## 5. Phase 3 — Fabric: 15+ agents, one command, one shared brain

**Goal:** any agent can install JIRO, connect, and join a shared evidence session without reading a setup guide.

**Shape:** Medium-to-large. Integration, protocol, hardening, docs, release.

### 5.1 MCP server v2

Tools (additive):
- `search_graph` — run a budgeted graph.
- `plan_search` — dry-run with cost estimate.
- `research_start` / `research_status` / `research_resume` — durable sessions.
- `evidence_get` — packet or claim subset.
- `claim_verify` — verify a claim against the ledger.
- `params_build` / `params_bind` / `params_diff` — live parameter layer.
- `watch_create` / `watch_events` — standing queries.
- `session_handoff` — structured brief for another agent.

Resources:
- `jiro://session/{id}/ledger`
- `jiro://packet/{id}`
- `jiro://params/{id}/{version}`

Prompts:
- `deep_research`
- `verify_claim`
- `monitor_topic`

Transport:
- JSON-RPC over stdio for single clients.
- Streamable HTTP for shared sessions and multi-agent.
- Progress notifications per node; cancellation supported.
- Date-versioned protocol negotiation, never an invented version.

### 5.2 Shared sessions and handoff

`jiro/fabric/session.py`:
- `POST /v1/sessions` -> session id + scoped token.
- Session stores graph state, evidence ledger, parameter sets, budgets, and an append-only event log.
- Roles: `planner`, `retriever`, `verifier`, `writer`, `observer`. Tokens carry scopes.
- Handoff payload: goal, evidence ids, open questions, contradictions, budget left, next suggested node.
- Optimistic concurrency with idempotency keys; conflicting claims are both kept.
- Event stream over SSE for observers; replayable from sequence number.

This is what "15 agents deeply connect" means technically: they are not 15 isolated search calls. They share one ledger and one budget.

### 5.3 Client matrix and auto-setup

`jiro/fabric/clients.py` replaces the hardcoded list in `mcp_setup.py` with a registry:

- 16 supported clients: Claude Code, Claude Desktop, Codex CLI, Cursor, Windsurf, Cline, Roo Code, Kilo Code, Continue.dev, Zed, OpenCode, Gemini CLI, Goose, VS Code Copilot, OpenClaw, Hermes.
- Each entry declares: detection paths, config format, transport, write strategy, verify command, uninstall strategy.
- Support tiers: A = auto-write + verified handshake; B = auto-detect + guided; C = HTTP/SDK recipe.
- `docs/v0.4.0/AGENT_MATRIX.md` is the source of truth. A client is only "supported" after a clean-VM test.

### 5.4 One-command install

Primary:

```bash
npx @jiro/setup@0.4.0
```

Python-native equivalent:

```bash
uv tool install "jirosearch==0.4.0" && jiro setup --all --yes
```

Alternatives:

```bash
pipx install "jirosearch==0.4.0" && jiro setup --all
npx @jiro/setup@0.4.0
docker run --rm -p 8080:8080 ghcr.io/devanimecx/jiro:0.4.0
```

Windows PowerShell (pinned release URL + checksum):

```powershell
$u="https://github.com/DevAnimecx/jiro/releases/download/v0.4.0/install.ps1"
$h="https://github.com/DevAnimecx/jiro/releases/download/v0.4.0/install.ps1.sha256"
irm $u -OutFile install.ps1; irm $h -OutFile install.ps1.sha256
if ((Get-FileHash install.ps1 -Algorithm SHA256).Hash -ne (Get-Content install.ps1.sha256)) { throw "checksum mismatch" }
./install.ps1 -Version 0.4.0
```

What `jiro setup --all` does:

1. Detect installed clients.
2. Back up each config with a timestamp.
3. Write only the JIRO entry; never rewrite unrelated config.
4. Verify by launching the MCP server and completing a handshake + one search.
5. Print a per-client result table.
6. Offer `--uninstall` and `--dry-run`.
7. Never write secrets; use env references.

### 5.5 Doctor and rollback

- `jiro doctor` checks binary, config, MCP handshake, search, scrape, packet, parameter store, disk, and network.
- `jiro doctor --json` for CI and support.
- `jiro setup --uninstall --all` removes only JIRO entries and restores backups.
- Every setup writes a manifest so rollback is deterministic.

### 5.6 Phase 3 gates

| Gate | Test | Threshold |
|---|---|---|
| G3.1 MCP conformance | conformance suite | all pass; old 19 tools unchanged |
| G3.2 Client setup | 16 clients x 3 OS | 15+ tier-A pass, 0 broken configs |
| G3.3 Time to first call | clean VM | median <= 120 s |
| G3.4 Shared session | 3 agents, 1 session | no lost evidence, conflicts preserved |
| G3.5 Injection red team | 100 cases | 0 critical, >= 95% flagged |
| G3.6 SSRF | FETCH tests | 0 bypasses |
| G3.7 Soak | 24 h | memory growth < 5%, 0 unhandled |
| G3.8 Load | 50/10 RPS | p99 within SLO, error < 1% |
| G3.9 Rollback | uninstall test | config restored byte-identical |
| G3.10 Claims | registry check | every public number has an artifact |

---

## 6. The claims protocol

Full detail in `docs/v0.4.0/CLAIMS.md`. Summary:

### 6.1 Speed

- Workload W1: 5 sub-queries, 10 sources, one cited result.
- Baseline: JIRO 0.3.3 sequential endpoints on the same machine and network.
- Metric: wall-clock p50 to a complete cited result.
- Claim: `T_baseline / T_0.4.0 >= 2.0`.
- Report cold and warm separately. Never aggregate them into one flattering number.

### 6.2 Efficiency

- Workload W2: same sources, same required citations, answer quality non-inferior.
- Baseline: raw markdown from those sources.
- Metric: tokens per verified citation.
- Claim: `tokens_baseline / tokens_0.4.0 >= 3.0`.
- If quality drops, the claim is void regardless of the ratio.

### 6.3 Setup

- 16 clients, clean Windows/macOS/Linux VMs.
- Median time to first successful tool call <= 120 s.
- >= 15 clients pass tier A.
- Zero corrupted configs.

### 6.4 Enforcement

`benchmarks/claims.yaml` lists each claim, workload, baseline, target, and artifact path. A release test parses the artifact, recomputes the ratio, and fails if the claim is not met. The launch checklist cannot be completed without it.

**Prohibited:** "AGI", "2x faster than Google", "3x faster than Perplexity", "zero-latency", "infinite context", "weight injection", "LoRA", any number without an artifact.

---

## 7. Evaluation

### 7.1 Task suite

100 tasks across:
- multi-hop factual
- fresh news
- numeric/date comparison
- contradictory sources
- code/docs
- social signal
- entity profile
- how-to with citations
- structured extraction from a page
- watch/diff over a changed page

### 7.2 Metrics

- answer correctness (rubric 1-10)
- citation precision and recall
- contradiction recall
- freshness accuracy
- p50/p95 latency
- tokens per verified citation
- upstream requests
- cost estimate accuracy
- setup success rate

### 7.3 Method

- LLM judge with a fixed rubric and fixed model version.
- 50 human spot checks per release, blind to system.
- Artifacts committed with hardware, network class, commit SHA, config hash.
- A reproduction command in every artifact.

---

## 8. Security and trust

| Threat | Vector | Control |
|---|---|---|
| Indirect prompt injection | Page text instructs the model | Taint every segment; tag suspicious patterns; synthesis opt-in with no tools; fixed output schema |
| SSRF | FETCH reaches internal services | Existing `jiro/security.py` on every fetch, including redirects |
| Cache poisoning | Shared cache seeded with false content | Tenant namespaces; content hash + provenance; allowlist separate from cache |
| Cost amplification | 12-node, 6-fanout graphs in a loop | Per-key graph/node/request/byte limits; plan cost estimate; breaker |
| Cross-session leakage | Guessable session or packet id | Unguessable ids; owner from auth context; scoped tokens |
| Agent privilege escalation | Agent writes a claim that grants capability | Capabilities are enforced at the tool boundary, never in content |
| Install supply chain | Tampered installer or package | Pinned versions, checksums, signed release artifacts, no sudo, no telemetry |
| Tool poisoning | Web page alters tool metadata | Static tool registry; page content never enters tool definitions |
| Replay | Reused packet or session id | Idempotency keys; expiry; sequence numbers |

Explicit non-controls: variance checks, classifier confidence, majority vote, model self-report.

---

## 9. Compatibility and rollout

### 9.1 Compatibility

- All new endpoints additive.
- Existing 19 MCP tools unchanged.
- `jiro mcp setup` stays as an alias for the new `jiro setup`.
- `MultiQuerySearcher` stays public; `/v1/search/multi` may be reimplemented on the graph only with golden-test parity.
- Python 3.11+; core install stays lean.
- Optional extras: `[parametric]`, `[perf]`, `[docs]` (PDF/document extraction).
- SQLite default; Postgres optional; no cloud requirement.

### 9.2 Rollout

- **Alpha:** internal + 5 design partners. Graph and packet behind flags.
- **Beta:** 100 users, 15+ clients, telemetry opt-in, claims artifact v1.
- **GA:** all gates pass, claims published, docs and demo complete.

No calendar dates in this document. Sequence is fixed; dates are set after the Phase 1 baseline, because the baseline decides whether 2x is real.

### 9.3 Rollback

Flags off restores 0.3.3 behavior without a downgrade. `jiro setup --uninstall` restores client configs from backup. A release never requires a database migration that cannot be reversed.

---

## 10. Risks and kill criteria

| Risk | Trigger | Response |
|---|---|---|
| 2x misses | W1 ratio < 2.0 | Publish actual; drop headline; keep graph |
| 3x misses | W2 ratio < 3.0 | Publish actual; drop headline; keep packet |
| Client configs break | Any corrupted config in G3.2 | Block release; add backup/restore test |
| Injection bypass | Critical red-team case | Quarantine; block release until fixed |
| Latency regression on single query | p95 worse than 0.3.3 | Keep graph opt-in; do not change default path |
| Scope creep | Arbitrary node types requested | Fixed operator set; reject custom nodes |
| Packet quality drop | G2.2 fails | Reduce compression; quality wins |
| Install friction | Median > 120 s | Simplify to one client; ship guided mode |

---

## 11. Execution order

1. Commit the baseline harness and the 0.3.3 artifact.
2. Freeze the graph contract and validation tests.
3. Ship `plan_only` before the executor.
4. Prove deadline and cancellation on fake operators.
5. Wire real search and fetch; measure W1.
6. Ship packets and measure W2.
7. Ship parameter sets and diff; measure watch efficiency.
8. Ship shared sessions and MCP v2.
9. Ship 16-client setup and clean-VM tests.
10. Publish claims, docs, demo, and rollback proof.

**Definition of done:** a new user runs one command, connects their agent, asks a multi-source question, gets a cited packet, sees the token count, and can reproduce the 2x/3x artifact on their own machine.

---

## 12. Open decisions

These are deliberately unresolved until the baseline exists. Each has a gate, not an opinion.

| # | Decision | Options | Decided by |
|---|---|---|---|
| 1 | Is the 2x headline cold or warm? | Cold is honest and harder; warm is easier and weaker | G1.8 artifact |
| 2 | Is 3x measured on first query or watch diff? | First query is the real product; diff is upside | G2.1 artifact |
| 3 | 15 Tier A or 12 Tier A + 4 guided? | More auto-write risk vs weaker claim | G3.2 clean-VM matrix |
| 4 | Does embedding export ship in 0.4.0? | Experimental, optional, or cut | Phase 2 adapter demo |
| 5 | Are shared sessions local-only or Postgres-backed at GA? | SQLite is simpler; teams need concurrency | Beta feedback + soak |

The default for every open decision is the smaller, more honest claim.

---

## Appendix A — Surface summary

### REST

```
POST /v1/graph/plan
POST /v1/graph/search
GET  /v1/graph/{id}
POST /v1/sessions
GET  /v1/sessions/{id}
POST /v1/params/build
POST /v1/params/{id}/bind
GET  /v1/params/{id}/diff
POST /v1/watch
GET  /v1/watch/{id}/events
```

### CLI

```
jiro setup --all --yes
jiro doctor
jiro graph plan "..." 
jiro graph run "..." --tokens 1200
jiro params diff <id> --since <version>
jiro watch add "..." --every 6h
jiro bench run --workload research_5q_10src
```

### MCP tools

```
search_graph, plan_search, research_start, research_status, research_resume,
evidence_get, claim_verify, params_build, params_bind, params_diff,
watch_create, watch_events, session_handoff
```

### SDKs

Regenerate Python, TypeScript, and Go clients from the OpenAPI spec with the
existing scripts. New surfaces are additive; no existing method changes shape.
`jiro mcp setup` remains as an alias for `jiro setup` so 0.3.x instructions keep
working.

---

## Appendix B — Glossary

- **Query graph:** validated DAG of retrieval and extraction operators under one budget.
- **Evidence packet:** token-bounded, cited compilation of claims from sources.
- **Claim ledger:** append-only record of claims, support, contradictions, provenance.
- **Live Parameter Layer:** versioned, decaying, diffable set of claims and vectors bound to model calls.
- **Binding:** the act of formatting a parameter set for a specific model profile and budget.
- **Session:** shared, durable workspace where multiple agents read and write one ledger.
- **Tier A client:** auto-configured and handshake-verified on a clean machine.
- **Claim artifact:** committed JSON that proves a public performance number.

---

## Appendix C — Launch narrative

**Headline:** *The web, compiled for agents.*

**Three proof points:**
1. 2x faster on a 5-query, 10-source research task, measured against 0.3.3.
2. 3x fewer tokens per verified citation, at non-inferior quality.
3. One command, 15+ agents, one shared evidence session.

**Demo:**
1. `jiro setup --all` — detect clients, verify, print table.
2. In Claude Code: "Research X and give me a cited packet."
3. Show the graph, the timings, the gaps, the contradictions, the token count.
4. `jiro params diff` — show what changed since yesterday.
5. Open the benchmark artifact and reproduce the ratio.

**Do not say:** AGI, LoRA, weight injection, infinite context, "2x faster than Google", "3x faster than Perplexity".
